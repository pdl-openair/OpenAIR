"""Execution Method runtime-control component contract."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime

import pytest

from app.models.research_orchestration import (
    AcceptanceCriterion,
    ResearchTaskV12,
    RuntimeReadinessSnapshot,
)
from app.modules import research_execution_decision_service as decision_service
from app.modules.research_execution_decision_service import (
    DecisionPolicyViolation,
    close_decision_from_durable_facts,
    freeze_ready_assignments,
    prepare_ready_assignments,
)
from app.modules.research_orchestration_resolver import ResolutionFacts, ResolutionResult
from tests.support.research_execution_decision import freeze_prepared_selection_for_test
from tests.support.research_lifecycle import build_confirmed_research_task


METHOD_ID = "pvd_literature_evidence_closure"


def test_completion_roles_receive_only_their_declared_final_artifact_contract() -> None:
    criteria = [
        AcceptanceCriterion.model_validate(
            {
                "criterionId": f"criterion_{artifact_type}",
                "description": f"Validate {artifact_type}.",
                "criterionKind": "artifact_quality",
                "coverageTargetIds": [],
                "requiredArtifactType": artifact_type,
                "judgmentRequirement": "domain_professional",
                "evaluatorType": "agent",
                "evaluatorRef": "cs-professional-contract",
            }
        )
        for artifact_type in (
            "reproduction_execution_record",
            "actual_metric_records",
        )
    ]
    role = {
        "roleId": "actual_metric_records_closure",
        "qualificationContract": {
            "outputArtifactTypes": ["actual_metric_records"]
        },
    }

    outputs, role_criteria = decision_service._execution_method_role_result_contract(
        role,
        is_completion=True,
        expected_artifact_types=[
            "reproduction_execution_record",
            "actual_metric_records",
        ],
        acceptance_criteria=criteria,
    )

    assert outputs == ["actual_metric_records"]
    assert [row.required_artifact_type for row in role_criteria] == [
        "actual_metric_records"
    ]
    assert decision_service._execution_method_role_result_contract(
        role,
        is_completion=False,
        expected_artifact_types=outputs,
        acceptance_criteria=criteria,
    ) == (["actual_metric_records"], [])


def _pvd_407b_method() -> dict:
    # Method authority contains roles/control only: no concrete Skill/Agent/Model identity.
    return {
        "methodId": METHOD_ID,
        "methodVersion": 1,
        "semantics": "role_graph_v1",
        "roles": [
            {
                "roleId": "candidate_acquisition",
                "requirement": "required",
                "dependsOn": [],
                "capabilityKinds": ["skill", "agent"],
                "releasePolicy": {"kind": "always"},
                "failurePolicy": "fail_closed",
                "parallelism": "single",
            },
            {
                "roleId": "scientific_relevance_screening",
                "requirement": "optional",
                "dependsOn": ["candidate_acquisition"],
                "capabilityKinds": ["skill", "agent"],
                "releasePolicy": {"kind": "always"},
                "failurePolicy": "continue_degraded",
                "parallelism": "single",
            },
            {
                "roleId": "progressive_reading",
                "requirement": "required",
                "dependsOn": [
                    "candidate_acquisition",
                    "scientific_relevance_screening",
                ],
                "capabilityKinds": ["skill", "agent"],
                "releasePolicy": {"kind": "always"},
                "failurePolicy": "fail_closed",
                "parallelism": "single",
            },
            {
                "roleId": "evidence_extraction",
                "requirement": "required",
                "dependsOn": ["progressive_reading"],
                "capabilityKinds": ["skill", "agent"],
                "releasePolicy": {"kind": "always"},
                "failurePolicy": "isolate_item",
                "parallelism": "per_item",
            },
            {
                "roleId": "research_record_closure",
                "requirement": "required",
                "dependsOn": ["evidence_extraction"],
                "capabilityKinds": ["skill", "agent"],
                "releasePolicy": {"kind": "always"},
                "failurePolicy": "continue_degraded",
                "parallelism": "single",
            },
            {
                "roleId": "coverage_assessment",
                "requirement": "required",
                "dependsOn": ["research_record_closure"],
                "capabilityKinds": ["skill", "agent"],
                "releasePolicy": {"kind": "always"},
                "failurePolicy": "fail_closed",
                "parallelism": "single",
            },
            {
                "roleId": "directed_gap_expansion",
                "requirement": "conditional",
                "dependsOn": ["coverage_assessment"],
                "capabilityKinds": ["skill", "agent"],
                "releasePolicy": {
                    "kind": "scientific_state_equals",
                    "path": ["scientificFacts", "evidenceCoverage", "status"],
                    "equals": "gap",
                },
                "failurePolicy": "continue_degraded",
                "parallelism": "single",
            },
            {
                "roleId": "evidence_closure",
                "requirement": "required",
                "dependsOn": ["coverage_assessment"],
                "capabilityKinds": ["skill", "agent"],
                "releasePolicy": {
                    "kind": "scientific_state_equals",
                    "path": ["scientificFacts", "evidenceCoverage", "status"],
                    "equals": "satisfied",
                },
                "failurePolicy": "fail_closed",
                "parallelism": "single",
            },
        ],
        "completionRoleIds": ["evidence_closure"],
        "stopConditions": [
            {
                "conditionId": "coverage_satisfied",
                "afterRoleId": "evidence_closure",
                "source": {
                    "kind": "scientific_state_equals",
                    "path": ["scientificFacts", "evidenceCoverage", "status"],
                    "equals": "satisfied",
                },
                "action": "complete_method",
            }
        ],
        "reentryTransitions": [
            {
                "transitionId": "coverage_gap_next_wave",
                "afterRoleId": "directed_gap_expansion",
                "restartRoleId": "progressive_reading",
                "repeatRoleIds": [
                    "progressive_reading",
                    "evidence_extraction",
                    "research_record_closure",
                    "coverage_assessment",
                    "directed_gap_expansion",
                    "evidence_closure",
                ],
                "source": {
                    "kind": "scientific_state_equals",
                    "path": ["scientificFacts", "evidenceCoverage", "status"],
                    "equals": "gap",
                },
                "action": "start_next_iteration",
            }
        ],
        "qualityConstraints": [
            "scientific_relevance_is_professional_judgment",
            "screening_unavailable_follows_formal_degradation",
            "progressive_reading_uses_small_waves",
            "stop_only_on_formal_coverage_satisfied",
            "expand_only_on_explicit_formal_gap",
            "evidence_extraction_parallel_per_source",
            "single_source_failure_isolated",
            "preserve_valid_partial_results",
        ],
    }


def _candidate(skill_id: str, *, role_id: str, active: int = 0, executable: bool = True) -> dict:
    return {
        "candidateEvaluationId": f"candidate_{skill_id}",
        "candidateRef": {"skillId": skill_id, "definitionVersion": 1},
        "sourceBindingRefs": [
            {
                "bindingId": f"bind:pvd:literature_evidence:skill:{skill_id}",
                "bindingVersion": 1,
            }
        ],
        "supportedRole": "tool",
        "eligible": True,
        "rejectionCodes": [],
        "preference": {
            "semanticFit": "exact",
            "semanticFitEvidence": {
                "objectiveMatch": "exact",
                "inputContractMatch": "exact",
                "outputContractMatch": "exact",
                "professionalJudgmentFit": "required_supported",
                "positiveRuleRefs": ["registry-policy.v1:definition-binding"],
                "negativeRuleRefs": [],
                "adapterRefs": [],
                "policyVersion": "registry-policy.v1",
                "rationale": f"Eligible for {role_id}.",
            },
            "completionCoverage": "completion_capable",
            "executableNow": executable,
            "executionBlockerCodes": [] if executable else ["runtime_unavailable"],
            "riskClass": "low",
            "estimatedCostAmount": 0.0,
            "permissionBreadth": "narrow",
            "supportsProfessionalJudgment": True,
            "activeWorkUnitCount": active,
            "lastAssignedAt": None,
        },
        "rationale": f"Candidate for {role_id}.",
        "executionMethodRoleEvaluations": [
            {
                "roleRef": f"{METHOD_ID}:{role_id}",
                "eligible": True,
                "rejectionCodes": [],
                "executableNow": executable,
                "executionBlockerCodes": [] if executable else ["runtime_unavailable"],
                "semanticFitEvidence": {
                    "objectiveMatch": "compatible",
                    "inputContractMatch": "exact",
                    "outputContractMatch": "exact",
                    "professionalJudgmentFit": "not_required",
                    "positiveRuleRefs": ["registry-policy.v1:execution-method-role"],
                    "negativeRuleRefs": [],
                    "adapterRefs": [],
                    "policyVersion": "registry-policy.v1",
                    "rationale": f"Role-qualified for {role_id}.",
                },
            }
        ],
    }


def _fixture(
    *,
    screening_a_executable: bool = True,
    expected_artifact_types: list[str] | None = None,
):
    method = _pvd_407b_method()
    task_payload = build_confirmed_research_task().model_dump(mode="json", by_alias=True)
    instance = task_payload["researchDesignVersions"][0]["instances"][0]
    instance.update(
        {
            "substageDefinitionId": "pvd:literature_evidence_acceptance_sample",
            "substageDefinitionVersion": 1,
            "stageKey": "execution_validation",
            "objective": "建立可追溯的 PVD 文献 Evidence 与 ResearchRecord。",
            "expectedArtifactTypes": (
                ["pvd_literature_evidence_packet"]
                if expected_artifact_types is None
                else list(expected_artifact_types)
            ),
        }
    )
    task = ResearchTaskV12.model_validate(task_payload)

    role_skill = {
        "candidate_acquisition": "sample_source_acquisition",
        "progressive_reading": "sample_progressive_reading",
        "evidence_extraction": "sample_evidence_extraction",
        "research_record_closure": "sample_record_closure",
        "coverage_assessment": "sample_coverage_assessment",
        "directed_gap_expansion": "sample_gap_expansion",
        "evidence_closure": "sample_evidence_closure",
    }
    candidates = [
        _candidate(skill_id, role_id=role_id)
        for role_id, skill_id in role_skill.items()
    ]
    candidates.extend(
        [
            _candidate(
                "sample_relevance_screen_a",
                role_id="scientific_relevance_screening",
                active=0,
                executable=screening_a_executable,
            ),
            _candidate(
                "sample_relevance_screen_b",
                role_id="scientific_relevance_screening",
                active=2,
            ),
        ]
    )
    resolution = ResolutionResult(
        designRef=task.active_research_design_ref,
        instanceRef={
            "substageInstanceId": instance["substageInstanceId"],
            "instanceRevision": instance["instanceRevision"],
        },
        registrySnapshotRef=task.registry_snapshot_ref,
        candidateEvaluations=candidates,
        preferredCandidateEvaluationRefs=[],
    )
    definitions = []
    bindings = []
    readiness: dict[str, RuntimeReadinessSnapshot] = {}
    for row in candidates:
        skill_id = row["candidateRef"]["skillId"]
        role_id = next(
            role["roleId"]
            for role in method["roles"]
            if f"{METHOD_ID}:{role['roleId']}" in {
                f"{METHOD_ID}:{candidate_role}"
                for candidate_role in (
                    ["scientific_relevance_screening"]
                    if skill_id.startswith("sample_relevance_screen_")
                    else [next(key for key, value in role_skill.items() if value == skill_id)]
                )
            }
        )
        definitions.append(
            {
                "skillId": skill_id,
                "definitionVersion": 1,
                "status": "ready",
                "enabled": True,
                "executionMethodRoles": [f"{METHOD_ID}:{role_id}"],
                "sourceContract": {},
            }
        )
        bindings.append(
            {
                "bindingId": f"bind:pvd:literature_evidence:skill:{skill_id}",
                "bindingVersion": 1,
                "definitionId": "pvd:literature_evidence_acceptance_sample",
                "definitionVersion": 1,
                "skillId": skill_id,
                "skillDefinitionVersion": 1,
                "enabled": True,
            }
        )
        ready = row["preference"]["executableNow"]
        readiness[f"skill:{skill_id}:1"] = RuntimeReadinessSnapshot.model_validate(
            {
                "capabilityRef": {"skillId": skill_id, "definitionVersion": 1},
                "ready": ready,
                "runtimeBindingId": f"python_adapter:{skill_id}" if ready else None,
                "checkedAt": "2026-07-28T00:00:03Z",
                "expiresAt": "2026-07-28T01:00:03Z",
                "reason": None if ready else "runtime unavailable",
            }
        )
    snapshot = {
        "definitions": {
            "substages": [
                {
                    "definitionId": "pvd:literature_evidence_acceptance_sample",
                    "definitionVersion": 1,
                    "inputContract": {"requiredInputRefs": {"status": "known", "values": []}},
                    "executionMethod": deepcopy(method),
                }
            ],
            "skills": definitions,
            "agents": [],
        },
        "bindings": {"skills": bindings, "agents": [], "agentSkills": []},
    }
    facts = ResolutionFacts(
        runtimeReadiness={},
        providerReadiness={},
        authorizedCandidateRefs={
            f"skill:{row['candidateRef']['skillId']}:1" for row in candidates
        },
        availableInputRefs=set(instance["inputRefs"]),
        applicableRuleRefs=set(),
        satisfiedHumanGateRefs=set(),
        riskAllowed=True,
        budgetAvailable=True,
        policyVersion="registry-policy.v1",
        activeWorkUnitCounts={},
        lastAssignedAt={},
    )
    prepared = decision_service.prepare_selection(
        task,
        instance_id=instance["substageInstanceId"],
        resolution=resolution,
        snapshot_envelope=snapshot,
        facts=facts,
        runtime_readiness=readiness,
        selection_request_id="selection-pvd-407b-method",
        selection_command_hash="command-pvd-407b-method",
        expected_task_revision=task.revision,
        owner_id="owner",
        workspace_id="workspace",
        decided_at=datetime.fromisoformat("2026-07-28T00:00:04+00:00"),
    )
    return task, prepared, snapshot, method


def _set_work_status(
    task: ResearchTaskV12, role_id: str, status: str, *, iteration: int = 1
) -> ResearchTaskV12:
    payload = task.model_dump(mode="json", by_alias=True)
    decision = payload["executionDecisions"][-1]
    assignment = next(
        row
        for row in decision["assignments"]
        if row["executionMethodRoleId"] == role_id
        and (row.get("executionMethodIteration") or 1) == iteration
    )
    work = next(
        row
        for row in payload["workUnits"]
        if row["assignmentId"] == assignment["assignmentId"]
    )
    work["status"] = status
    work["finishedAt"] = "2026-07-28T00:10:00Z" if status not in {"queued", "running", "reviewing"} else None
    return ResearchTaskV12.model_validate(payload)


def _set_coverage(task: ResearchTaskV12, status: str) -> ResearchTaskV12:
    payload = task.model_dump(mode="json", by_alias=True)
    payload["scientificStates"][-1]["scientificFacts"] = {
        "evidenceCoverage": {"status": status}
    }
    return ResearchTaskV12.model_validate(payload)


def _release(task: ResearchTaskV12, snapshot: dict):
    decision_id = task.execution_decisions[-1].execution_decision_id
    preparation = prepare_ready_assignments(
        task,
        execution_decision_id=decision_id,
        snapshot=snapshot,
    )
    if preparation is None:
        return task, ()
    release = freeze_ready_assignments(
        preparation,
        released_at=datetime.fromisoformat("2026-07-28T00:11:00+00:00"),
    )
    return release.task, release.work_units


def test_pvd_407b_same_method_can_replace_concrete_screening_skill() -> None:
    _task_a, prepared_a, _snapshot_a, method_a = _fixture(screening_a_executable=True)
    _task_b, prepared_b, _snapshot_b, method_b = _fixture(screening_a_executable=False)

    selected_a = dict(prepared_a.execution_method_role_candidates)["scientific_relevance_screening"]
    selected_b = dict(prepared_b.execution_method_role_candidates)["scientific_relevance_screening"]

    assert method_a == method_b
    assert selected_a == "skill:sample_relevance_screen_a:1"
    assert selected_b == "skill:sample_relevance_screen_b:1"


def test_pvd_407b_workunit_freezes_method_role_actual_skill_and_binding_after_selection() -> None:
    task, prepared, _snapshot, method = _fixture()
    materialized = freeze_prepared_selection_for_test(
        prepared, task, prepared.final_tie_candidate_ids[0]
    )

    assert materialized.decision.strategy.value == "execution_method"
    assert materialized.decision.execution_method_ref == {
        "methodId": method["methodId"],
        "methodVersion": method["methodVersion"],
    }
    assert len(materialized.work_units) == 1
    work = materialized.work_units[0]
    assert work.execution_method_role_id == "candidate_acquisition"
    assert work.execution_method_ref == materialized.decision.execution_method_ref
    assert work.executor_ref.executor_id == "sample_source_acquisition"
    assert work.resolved_parameters is None
    assignment = next(
        row for row in materialized.decision.assignments if row.assignment_id == work.assignment_id
    )
    assert assignment.source_binding_refs[0].binding_id.endswith(":sample_source_acquisition")


def test_pvd_407b_frozen_workunit_does_not_change_when_registry_candidates_change() -> None:
    task, prepared, snapshot, _method = _fixture()
    materialized = freeze_prepared_selection_for_test(
        prepared, task, prepared.final_tie_candidate_ids[0]
    )
    work_before = materialized.work_units[0].model_dump(mode="json", by_alias=True)
    decision_before = materialized.decision.model_dump(mode="json", by_alias=True)

    source_definition = next(
        row
        for row in snapshot["definitions"]["skills"]
        if row["skillId"] == "sample_source_acquisition"
    )
    source_definition["executionMethodRoles"] = []
    snapshot["definitions"]["skills"].append(
        {
            **deepcopy(source_definition),
            "skillId": "replacement_source_acquisition",
            "executionMethodRoles": [f"{METHOD_ID}:candidate_acquisition"],
        }
    )

    assert materialized.work_units[0].model_dump(mode="json", by_alias=True) == work_before
    assert materialized.decision.model_dump(mode="json", by_alias=True) == decision_before
    assert materialized.work_units[0].executor_ref.executor_id == "sample_source_acquisition"


def test_pvd_407b_method_control_uses_degraded_screening_and_formal_coverage_state() -> None:
    task, prepared, snapshot, _method = _fixture()
    materialized = freeze_prepared_selection_for_test(
        prepared, task, prepared.final_tie_candidate_ids[0]
    )
    current = _set_work_status(materialized.task, "candidate_acquisition", "completed")

    current, released = _release(current, snapshot)
    assert [row.execution_method_role_id for row in released] == [
        "scientific_relevance_screening"
    ]
    current = _set_work_status(current, "scientific_relevance_screening", "failed")

    current, released = _release(current, snapshot)
    assert [row.execution_method_role_id for row in released] == ["progressive_reading"]
    assert released[0].execution_method_role_control["failurePolicy"] == "fail_closed"

    current = _set_work_status(current, "progressive_reading", "completed")
    current, released = _release(current, snapshot)
    assert [row.execution_method_role_id for row in released] == ["evidence_extraction"]
    assert released[0].execution_method_role_control["parallelism"] == "per_item"
    assert released[0].execution_method_role_control["failurePolicy"] == "isolate_item"

    current = _set_work_status(current, "evidence_extraction", "completed")
    current, released = _release(current, snapshot)
    assert [row.execution_method_role_id for row in released] == ["research_record_closure"]
    current = _set_work_status(current, "research_record_closure", "completed")
    current, released = _release(current, snapshot)
    assert [row.execution_method_role_id for row in released] == ["coverage_assessment"]
    current = _set_work_status(current, "coverage_assessment", "completed")

    gap_task = _set_coverage(current, "gap")
    _gap_task, gap_released = _release(gap_task, snapshot)
    assert [row.execution_method_role_id for row in gap_released] == ["directed_gap_expansion"]

    satisfied_task = _set_coverage(current, "satisfied")
    satisfied_task, satisfied_released = _release(satisfied_task, snapshot)
    assert [row.execution_method_role_id for row in satisfied_released] == ["evidence_closure"]
    satisfied_task = _set_work_status(satisfied_task, "evidence_closure", "completed")
    stopped_task, after_stop = _release(satisfied_task, snapshot)
    assert stopped_task == satisfied_task
    assert after_stop == ()


def _role_iteration_pairs(rows) -> list[tuple[str | None, int]]:
    return [
        (row.execution_method_role_id, row.execution_method_iteration or 1)
        for row in rows
    ]


def _complete_first_round_to_coverage(
    task: ResearchTaskV12, snapshot: dict
) -> ResearchTaskV12:
    current = _set_work_status(task, "candidate_acquisition", "completed")
    current, released = _release(current, snapshot)
    assert _role_iteration_pairs(released) == [("scientific_relevance_screening", 1)]
    current = _set_work_status(current, "scientific_relevance_screening", "failed")
    current, released = _release(current, snapshot)
    assert _role_iteration_pairs(released) == [("progressive_reading", 1)]
    current = _set_work_status(current, "progressive_reading", "completed")
    current, released = _release(current, snapshot)
    assert _role_iteration_pairs(released) == [("evidence_extraction", 1)]
    current = _set_work_status(current, "evidence_extraction", "completed")
    current, released = _release(current, snapshot)
    assert _role_iteration_pairs(released) == [("research_record_closure", 1)]
    current = _set_work_status(current, "research_record_closure", "completed")
    current, released = _release(current, snapshot)
    assert _role_iteration_pairs(released) == [("coverage_assessment", 1)]
    return _set_work_status(current, "coverage_assessment", "completed")


def _complete_iteration_from_reading_to_coverage(
    task: ResearchTaskV12, snapshot: dict, *, iteration: int
) -> ResearchTaskV12:
    current = _set_work_status(
        task, "progressive_reading", "completed", iteration=iteration
    )
    current, released = _release(current, snapshot)
    assert _role_iteration_pairs(released) == [("evidence_extraction", iteration)]
    current = _set_work_status(
        current, "evidence_extraction", "completed", iteration=iteration
    )
    current, released = _release(current, snapshot)
    assert _role_iteration_pairs(released) == [("research_record_closure", iteration)]
    current = _set_work_status(
        current, "research_record_closure", "completed", iteration=iteration
    )
    current, released = _release(current, snapshot)
    assert _role_iteration_pairs(released) == [("coverage_assessment", iteration)]
    return _set_work_status(
        current, "coverage_assessment", "completed", iteration=iteration
    )


def test_execution_method_gap_reenters_second_iteration_and_stops_when_satisfied() -> None:
    task, prepared, snapshot, _method = _fixture()
    materialized = freeze_prepared_selection_for_test(
        prepared, task, prepared.final_tie_candidate_ids[0]
    )
    current = _complete_first_round_to_coverage(materialized.task, snapshot)
    current = _set_coverage(current, "gap")
    current, released = _release(current, snapshot)
    assert _role_iteration_pairs(released) == [("directed_gap_expansion", 1)]
    current = _set_work_status(current, "directed_gap_expansion", "completed")

    decision_id = current.execution_decisions[-1].execution_decision_id
    with pytest.raises(DecisionPolicyViolation) as exc:
        close_decision_from_durable_facts(
            current,
            execution_decision_id=decision_id,
            closed_at=datetime.fromisoformat("2026-07-28T00:12:00+00:00"),
        )
    assert exc.value.code == "execution_method_continuation_pending"

    round_one = {
        row.work_unit_id: row.model_dump(mode="json", by_alias=True)
        for row in current.work_units
    }
    current, released = _release(current, snapshot)
    assert _role_iteration_pairs(released) == [("progressive_reading", 2)]
    second_reading = released[0]
    assert second_reading.dependency_work_unit_ids
    for work_id, frozen in round_one.items():
        assert next(
            row.model_dump(mode="json", by_alias=True)
            for row in current.work_units
            if row.work_unit_id == work_id
        ) == frozen

    current = _complete_iteration_from_reading_to_coverage(
        current, snapshot, iteration=2
    )
    current = _set_coverage(current, "satisfied")
    current, released = _release(current, snapshot)
    assert _role_iteration_pairs(released) == [("evidence_closure", 2)]
    current = _set_work_status(
        current, "evidence_closure", "completed", iteration=2
    )
    stopped, after_stop = _release(current, snapshot)
    assert stopped == current
    assert after_stop == ()

    decision = stopped.execution_decisions[-1]
    repeated = [
        row
        for row in decision.assignments
        if row.execution_method_role_id == "progressive_reading"
    ]
    assert [row.execution_method_iteration or 1 for row in repeated] == [1, 2]
    assert len({row.assignment_id for row in repeated}) == 2
    repeated_work = [
        row
        for row in stopped.work_units
        if row.execution_method_role_id == "progressive_reading"
    ]
    assert [row.execution_method_iteration or 1 for row in repeated_work] == [1, 2]
    assert len({row.work_unit_id for row in repeated_work}) == 2


def test_execution_method_conditional_required_completion_uses_latest_iteration_only() -> None:
    task, prepared, snapshot, _method = _fixture(expected_artifact_types=[])
    materialized = freeze_prepared_selection_for_test(
        prepared, task, prepared.final_tie_candidate_ids[0]
    )
    current = _complete_first_round_to_coverage(materialized.task, snapshot)
    current = _set_coverage(current, "gap")
    current, released = _release(current, snapshot)
    assert _role_iteration_pairs(released) == [("directed_gap_expansion", 1)]
    current = _set_work_status(current, "directed_gap_expansion", "completed")
    current, released = _release(current, snapshot)
    assert _role_iteration_pairs(released) == [("progressive_reading", 2)]
    current = _complete_iteration_from_reading_to_coverage(
        current, snapshot, iteration=2
    )
    current = _set_coverage(current, "satisfied")
    current, released = _release(current, snapshot)
    assert _role_iteration_pairs(released) == [("evidence_closure", 2)]
    current = _set_work_status(
        current, "evidence_closure", "completed", iteration=2
    )

    closed = close_decision_from_durable_facts(
        current,
        execution_decision_id=current.execution_decisions[-1].execution_decision_id,
        closed_at=datetime.fromisoformat("2026-07-28T00:20:00+00:00"),
    )
    decision = closed.execution_decisions[-1]
    assert decision.status.value == "closed"
    assert decision.outcome.value == "succeeded"
    assert not any(
        row.execution_method_role_id == "evidence_closure"
        and (row.execution_method_iteration or 1) == 1
        and row.assignment_id in {work.assignment_id for work in closed.work_units}
        for row in decision.assignments
    )


def test_execution_method_reentry_is_unbounded_by_fixed_two_round_special_case() -> None:
    task, prepared, snapshot, _method = _fixture()
    materialized = freeze_prepared_selection_for_test(
        prepared, task, prepared.final_tie_candidate_ids[0]
    )
    current = _complete_first_round_to_coverage(materialized.task, snapshot)

    for iteration in (1, 2):
        current = _set_coverage(current, "gap")
        current, released = _release(current, snapshot)
        assert _role_iteration_pairs(released) == [("directed_gap_expansion", iteration)]
        current = _set_work_status(
            current, "directed_gap_expansion", "completed", iteration=iteration
        )
        current, released = _release(current, snapshot)
        assert _role_iteration_pairs(released) == [("progressive_reading", iteration + 1)]
        current = _complete_iteration_from_reading_to_coverage(
            current, snapshot, iteration=iteration + 1
        )

    current = _set_coverage(current, "satisfied")
    current, released = _release(current, snapshot)
    assert _role_iteration_pairs(released) == [("evidence_closure", 3)]
    current = _set_work_status(
        current, "evidence_closure", "completed", iteration=3
    )
    stopped, after_stop = _release(current, snapshot)
    assert stopped == current
    assert after_stop == ()

    reading_iterations = [
        row.execution_method_iteration or 1
        for row in stopped.execution_decisions[-1].assignments
        if row.execution_method_role_id == "progressive_reading"
    ]
    assert reading_iterations == [1, 2, 3]


def _bind_evidence_extraction_to_progressive_reading_artifact(snapshot: dict) -> None:
    producer_binding_id = "bind:pvd:literature_evidence:skill:sample_progressive_reading"
    target = next(
        row
        for row in snapshot["bindings"]["skills"]
        if row["skillId"] == "sample_evidence_extraction"
    )
    target["inputSources"] = {
        "contractVersion": 2,
        "fields": [
            {
                "targetField": "reading_packet",
                "sourcePresence": "required",
                "source": {
                    "kind": "artifact",
                    "bindingId": producer_binding_id,
                    "bindingVersion": 1,
                    "field": "$",
                },
            }
        ],
    }


def _append_role_artifact(
    task: ResearchTaskV12,
    *,
    role_id: str,
    iteration: int,
    artifact_id: str,
) -> ResearchTaskV12:
    payload = task.model_dump(mode="json", by_alias=True)
    decision = payload["executionDecisions"][-1]
    assignment = next(
        row
        for row in decision["assignments"]
        if row.get("executionMethodRoleId") == role_id
        and (row.get("executionMethodIteration") or 1) == iteration
    )
    work = next(
        row
        for row in payload["workUnits"]
        if row["assignmentId"] == assignment["assignmentId"]
    )
    artifact_type = (
        work["expectedArtifactTypes"][0]
        if work.get("expectedArtifactTypes")
        else "pvd_iteration_reading_packet"
    )
    payload["artifacts"].append(
        {
            "artifactId": artifact_id,
            "workUnitId": work["workUnitId"],
            "substageInstanceId": work["substageInstanceId"],
            "sourceCapabilityCallId": None,
            "artifactType": artifact_type,
            "artifactState": "usable",
            "version": 1,
            "sourceRefs": [],
            "validatorResult": {
                "status": "passed",
                "contentSha256": (str(iteration) * 64)[:64],
            },
            "lineageKind": "direct_work_unit_output",
            "sourceResultId": None,
        }
    )
    return ResearchTaskV12.model_validate(payload)


def _release_first_progressive_reading(
    task: ResearchTaskV12, snapshot: dict
) -> ResearchTaskV12:
    current = _set_work_status(task, "candidate_acquisition", "completed")
    current, released = _release(current, snapshot)
    assert _role_iteration_pairs(released) == [("scientific_relevance_screening", 1)]
    current = _set_work_status(current, "scientific_relevance_screening", "failed")
    current, released = _release(current, snapshot)
    assert _role_iteration_pairs(released) == [("progressive_reading", 1)]
    return current


def _complete_iteration_with_reading_artifact(
    task: ResearchTaskV12,
    snapshot: dict,
    *,
    iteration: int,
    artifact_id: str,
) -> tuple[ResearchTaskV12, tuple[str, ...]]:
    current = _set_work_status(
        task, "progressive_reading", "completed", iteration=iteration
    )
    current = _append_role_artifact(
        current,
        role_id="progressive_reading",
        iteration=iteration,
        artifact_id=artifact_id,
    )
    current, released = _release(current, snapshot)
    assert _role_iteration_pairs(released) == [("evidence_extraction", iteration)]
    evidence_work = released[0]

    current = _set_work_status(
        current, "evidence_extraction", "completed", iteration=iteration
    )
    current, released = _release(current, snapshot)
    assert _role_iteration_pairs(released) == [("research_record_closure", iteration)]
    current = _set_work_status(
        current, "research_record_closure", "completed", iteration=iteration
    )
    current, released = _release(current, snapshot)
    assert _role_iteration_pairs(released) == [("coverage_assessment", iteration)]
    current = _set_work_status(
        current, "coverage_assessment", "completed", iteration=iteration
    )
    return current, tuple(evidence_work.input_artifact_refs)


def _release_next_progressive_reading(
    task: ResearchTaskV12, snapshot: dict, *, iteration: int
) -> ResearchTaskV12:
    current = _set_coverage(task, "gap")
    current, released = _release(current, snapshot)
    assert _role_iteration_pairs(released) == [("directed_gap_expansion", iteration)]
    current = _set_work_status(
        current, "directed_gap_expansion", "completed", iteration=iteration
    )
    current, released = _release(current, snapshot)
    assert _role_iteration_pairs(released) == [("progressive_reading", iteration + 1)]
    return current


def test_execution_method_binding_reads_only_current_iteration_artifact_lineage() -> None:
    task, prepared, snapshot, _method = _fixture()
    _bind_evidence_extraction_to_progressive_reading_artifact(snapshot)
    materialized = freeze_prepared_selection_for_test(
        prepared, task, prepared.final_tie_candidate_ids[0]
    )
    current = _release_first_progressive_reading(materialized.task, snapshot)

    current, first_inputs = _complete_iteration_with_reading_artifact(
        current, snapshot, iteration=1, artifact_id="artifact-reading-i1"
    )
    assert first_inputs == ("artifact-reading-i1",)
    first_artifact = next(
        row.model_dump(mode="json", by_alias=True)
        for row in current.artifacts
        if row.artifact_id == "artifact-reading-i1"
    )

    current = _release_next_progressive_reading(current, snapshot, iteration=1)
    current, second_inputs = _complete_iteration_with_reading_artifact(
        current, snapshot, iteration=2, artifact_id="artifact-reading-i2"
    )
    assert second_inputs == ("artifact-reading-i2",)
    assert "artifact-reading-i1" not in second_inputs
    assert next(
        row.model_dump(mode="json", by_alias=True)
        for row in current.artifacts
        if row.artifact_id == "artifact-reading-i1"
    ) == first_artifact

    current = _release_next_progressive_reading(current, snapshot, iteration=2)
    current, third_inputs = _complete_iteration_with_reading_artifact(
        current, snapshot, iteration=3, artifact_id="artifact-reading-i3"
    )
    assert third_inputs == ("artifact-reading-i3",)
    assert {row.artifact_id for row in current.artifacts} >= {
        "artifact-reading-i1",
        "artifact-reading-i2",
        "artifact-reading-i3",
    }

    current = _set_coverage(current, "satisfied")
    current, released = _release(current, snapshot)
    assert _role_iteration_pairs(released) == [("evidence_closure", 3)]


def test_execution_method_binding_keeps_current_lineage_multiplicity_for_cardinality_contract() -> None:
    task, prepared, snapshot, _method = _fixture()
    _bind_evidence_extraction_to_progressive_reading_artifact(snapshot)
    materialized = freeze_prepared_selection_for_test(
        prepared, task, prepared.final_tie_candidate_ids[0]
    )
    current = _release_first_progressive_reading(materialized.task, snapshot)
    current = _set_work_status(current, "progressive_reading", "completed")
    current = _append_role_artifact(
        current,
        role_id="progressive_reading",
        iteration=1,
        artifact_id="artifact-reading-i1-a",
    )
    current = _append_role_artifact(
        current,
        role_id="progressive_reading",
        iteration=1,
        artifact_id="artifact-reading-i1-b",
    )
    decision = current.execution_decisions[-1]
    consumer = next(
        row
        for row in decision.assignments
        if row.execution_method_role_id == "evidence_extraction"
        and (row.execution_method_iteration or 1) == 1
    )
    refs = decision_service._binding_declared_artifact_refs(
        current,
        snapshot=snapshot,
        assignment=consumer,
        research_design_version=decision.research_design_version,
    )
    assert refs == ["artifact-reading-i1-a", "artifact-reading-i1-b"]
