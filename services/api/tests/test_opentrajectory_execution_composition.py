"""OpenTrajectory frozen-composition execution-decision tests."""

from __future__ import annotations
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import pytest
import yaml
from jsonschema import Draft7Validator
from app.execution.opentrajectory_structured_runtime_guard import (
    apply_opentrajectory_runtime_guard,
)
from app.execution.skill_finalization_runtime import (
    build_frozen_finalization_contract,
    finalize_and_validate_registered_output,
)
from app.models.research_orchestration import (
    AssignmentRole,
    ExecutionStrategy,
    ResearchTaskV12,
    RuntimeReadinessSnapshot,
)
from app.modules.research_execution_decision_service import (
    ExecutionPreflightBlocked,
    ResearchExecutionDecisionService,
)
from app.modules import research_execution_decision_service as decision_service
from app.modules.skill_binding_source import load_skill_binding_source_rows
from app.modules.research_orchestration_resolver import ResolutionFacts, ResolutionResult
from tests.support.research_execution import (
    NoWorkflowLineageRepository as _NoWorkflowLineageRepository,
    multi_artifact_selection_fixture as _multi_artifact_selection_fixture,
)
from tests.support.research_execution_decision import (
    freeze_prepared_selection_for_test as _freeze_prepared_selection_for_test,
    structural_execution_decision_payload as _decision,
)
from tests.support.research_lifecycle import build_confirmed_research_task


REPO_ROOT = Path(__file__).resolve().parents[3]


def _ot_source_acquisition_task() -> ResearchTaskV12:
    payload = build_confirmed_research_task().model_dump(mode="json", by_alias=True)
    instance = payload["researchDesignVersions"][0]["instances"][0]
    instance.update(
        {
            "substageDefinitionId": "opentrajectory:source_acquisition",
            "substageDefinitionVersion": 1,
            "stageKey": "execution_validation",
            "expectedArtifactTypes": ["source_acquisition_result"],
        }
    )
    return ResearchTaskV12.model_validate(payload)


def _ot_candidate(
    base: dict,
    *,
    skill_id: str | None = None,
    active_work_units: int = 0,
) -> dict:
    row = deepcopy(base)
    if skill_id is None:
        row.update(
            {
                "candidateEvaluationId": "candidate_general",
                "candidateRef": {
                    "agentId": "general-research-agent",
                    "definitionVersion": 1,
                },
                "sourceBindingRefs": [
                    {
                        "bindingId": "bind:opentrajectory:source_acquisition:agent:general-research-agent:primary",
                        "bindingVersion": 1,
                    }
                ],
                "supportedRole": "primary",
                "eligible": True,
                "rejectionCodes": [],
                "rationale": "Executable generic fallback that must never replace OT composition.",
            }
        )
    else:
        row.update(
            {
                "candidateEvaluationId": f"candidate_{skill_id}",
                "candidateRef": {"skillId": skill_id, "definitionVersion": 1},
                "sourceBindingRefs": [
                    {
                        "bindingId": f"bind:opentrajectory:source_acquisition:skill:{skill_id}",
                        "bindingVersion": 1,
                    }
                ],
                "supportedRole": "tool",
                "eligible": True,
                "rejectionCodes": [],
                "rationale": f"Canonical OT Skill {skill_id}.",
            }
        )
    row["preference"].update(
        {
            "completionCoverage": "completion_capable",
            "executableNow": True,
            "executionBlockerCodes": [],
            "riskClass": "low",
            "activeWorkUnitCount": active_work_units,
        }
    )
    return row


def _ot_snapshot(skill_ids: tuple[str, ...]) -> dict:
    return {
        "definitions": {
            "substages": [
                {
                    "definitionId": "opentrajectory:source_acquisition",
                    "definitionVersion": 1,
                    "inputContract": {
                        "requiredInputRefs": {"status": "known", "values": []}
                    },
                    "executionComposition": {
                        "workflowId": "opentrajectory_research_workflow",
                        "skillPack": "opentrajectory",
                        "compositionVersion": 1,
                        "semantics": "ordered_required",
                        "sourceAuthority": "canonical-test",
                        "requiredSkillRefs": [
                            {"skillId": skill_id, "definitionVersion": 1}
                            for skill_id in skill_ids
                        ],
                    },
                }
            ],
            "skills": [
                {
                    "skillId": skill_id,
                    "definitionVersion": 1,
                    "status": "ready",
                    "enabled": True,
                    "sourceContract": {
                        "structured_output": {
                            "contract_version": 1,
                            "guard_version": "p06c-v1",
                        },
                        "review_policy": {
                            "contract_version": 1,
                            "mode": "schema_validated_only",
                        },
                        "frozenSchemas": {
                            "input": {
                                "sha256": "a" * 64,
                                "schema": {"type": "object"},
                            },
                            "output": {
                                "sha256": "b" * 64,
                                "schema": {"type": "object"},
                            },
                        },
                    },
                }
                for skill_id in skill_ids
            ],
            "agents": [
                {
                    "agentId": "general-research-agent",
                    "definitionVersion": 1,
                    "enabled": True,
                    "agentKind": "general_research",
                },
                {
                    "agentId": "result_reliability_audit",
                    "definitionVersion": 1,
                    "enabled": True,
                    "agentKind": "specialist",
                    "reviewContract": {
                        "frozenArtifactContract": {
                            "artifactContract": "independent_result_review_v1"
                        }
                    },
                },
            ],
        },
        "bindings": {
            "skills": [
                {
                    "bindingId": f"bind:opentrajectory:source_acquisition:skill:{skill_id}",
                    "bindingVersion": 1,
                    "skillId": skill_id,
                    "skillDefinitionVersion": 1,
                    "enabled": True,
                }
                for skill_id in skill_ids
            ],
            "agents": [
                {
                    "bindingId": "bind:opentrajectory:source_acquisition:agent:general-research-agent:primary",
                    "bindingVersion": 1,
                    "enabled": True,
                }
            ],
            "agentSkills": [],
        },
    }


def _ot_readiness(skill_ids: tuple[str, ...]) -> dict[str, RuntimeReadinessSnapshot]:
    checked = "2026-07-28T00:00:03Z"
    expires = "2026-07-28T01:00:03Z"
    readiness = {
        f"skill:{skill_id}:1": RuntimeReadinessSnapshot.model_validate(
            {
                "capabilityRef": {"skillId": skill_id, "definitionVersion": 1},
                "ready": True,
                "runtimeBindingId": f"python_adapter:{skill_id}",
                "checkedAt": checked,
                "expiresAt": expires,
                "reason": None,
            }
        )
        for skill_id in skill_ids
    }
    readiness["agent:result_reliability_audit:1"] = RuntimeReadinessSnapshot.model_validate(
        {
            "capabilityRef": {
                "agentId": "result_reliability_audit",
                "definitionVersion": 1,
            },
            "ready": True,
            "runtimeBindingId": "registry:review-contract:result_reliability_audit:1",
            "checkedAt": checked,
            "expiresAt": expires,
            "reason": None,
        }
    )
    return readiness


def _ot_composition_selection_fixture(
    *,
    missing_skill: str | None = None,
    reverse: bool = False,
):
    task = _ot_source_acquisition_task()
    instance = task.research_design_versions[0].instances[0]
    skill_ids = ("opentrajectory_source_acquisition",)
    base = deepcopy(_decision()["candidateEvaluations"][0])
    evaluations = [
        _ot_candidate(base, skill_id=skill_id, active_work_units=20 - index)
        for index, skill_id in enumerate(skill_ids)
        if skill_id != missing_skill
    ]
    evaluations.append(_ot_candidate(base))
    if reverse:
        evaluations.reverse()
    resolution = ResolutionResult(
        designRef=task.active_research_design_ref,
        instanceRef={
            "substageInstanceId": instance.substage_instance_id,
            "instanceRevision": instance.instance_revision,
        },
        registrySnapshotRef=task.registry_snapshot_ref,
        candidateEvaluations=evaluations,
        preferredCandidateEvaluationRefs=[],
    )
    facts = ResolutionFacts(
        runtimeReadiness={},
        providerReadiness={},
        authorizedCandidateRefs={
            *{f"skill:{skill_id}:1" for skill_id in skill_ids},
            "agent:general-research-agent:1",
        },
        availableInputRefs=set(instance.input_refs),
        applicableRuleRefs=set(),
        satisfiedHumanGateRefs=set(),
        riskAllowed=True,
        budgetAvailable=True,
        policyVersion="registry-policy.v1",
        activeWorkUnitCounts={},
        lastAssignedAt={},
    )
    return task, resolution, _ot_snapshot(skill_ids), facts, _ot_readiness(skill_ids), list(skill_ids)


def _prepare_ot_composition(*, missing_skill: str | None = None, reverse: bool = False, lineage=None):
    task, resolution, snapshot, facts, readiness, skill_ids = _ot_composition_selection_fixture(
        missing_skill=missing_skill, reverse=reverse
    )
    prepared = decision_service.prepare_selection(
        task,
        instance_id=task.research_design_versions[0].instances[0].substage_instance_id,
        resolution=resolution,
        snapshot_envelope=snapshot,
        facts=facts,
        runtime_readiness=readiness,
        selection_request_id="selection-ot-composition",
        selection_command_hash="command-ot-composition",
        expected_task_revision=task.revision,
        owner_id="owner",
        workspace_id="workspace",
        decided_at=datetime.fromisoformat("2026-07-28T00:00:04+00:00"),
        workflow_lineage=(
            lineage
            if lineage is not None
            else {
                "workflowId": "opentrajectory_research_workflow",
                "skillPack": "opentrajectory",
                "researchDesignId": task.active_research_design_ref.research_design_id,
                "researchDesignVersion": task.active_research_design_ref.version,
            }
        ),
    )
    return task, prepared, skill_ids


def test_ot_source_acquisition_materializes_one_work_unit() -> None:
    task, prepared, skill_ids = _prepare_ot_composition()
    materialized = _freeze_prepared_selection_for_test(
        prepared,
        task,
        prepared.final_tie_candidate_ids[0],
    )
    primaries = [
        row
        for row in materialized.decision.assignments
        if row.role.value == "primary"
    ]

    assert len(task.scientific_states) == 1
    assert materialized.decision.strategy.value == "direct_skill"
    assert materialized.decision.fallback_invoked is False
    assert [row.executor_ref.executor_id for row in primaries] == skill_ids
    assert primaries[0].dependency_assignment_ids == []
    assert [row.role.value for row in materialized.decision.assignments] == ["primary"]
    assert len(materialized.work_units) == 1
    assert materialized.work_units[0].executor_ref.executor_id == skill_ids[0]
    assert len(materialized.dispatches) == 1
    assert (
        materialized.decision.completion_policy.review_requirement.value
        == "none"
    )


def test_ot_single_structured_skill_resolver_authority_precedes_assignment_authority() -> None:
    task, resolution, snapshot, facts, readiness, skill_ids = _ot_composition_selection_fixture()
    skill_id = skill_ids[0]
    resolution_payload = resolution.model_dump(mode="json", by_alias=True)
    resolution_payload["candidateEvaluations"] = [
        row
        for row in resolution_payload["candidateEvaluations"]
        if row["candidateRef"].get("skillId") == skill_id
    ]
    resolution_payload["candidateEvaluations"][0]["preference"]["semanticFitEvidence"][
        "positiveRuleRefs"
    ] = [
        "registry-policy.v1:definition-binding",
        "registry-policy.v1:executor-capability",
    ]
    single_resolution = ResolutionResult.model_validate(resolution_payload)
    composition = snapshot["definitions"]["substages"][0]["executionComposition"]
    composition["requiredSkillRefs"] = [{"skillId": skill_id, "definitionVersion": 1}]
    snapshot["definitions"]["skills"] = [
        {
            "skillId": skill_id,
            "definitionVersion": 1,
            "sourceContract": {
                "structured_output": {"contract_version": 1, "guard_version": "p06c-v1"},
                "review_policy": {
                    "contract_version": 1,
                    "mode": "schema_validated_only",
                },
                "frozenSchemas": {
                    "input": {"sha256": "a" * 64, "schema": {"type": "object"}},
                    "output": {"sha256": "b" * 64, "schema": {"type": "object"}},
                },
            },
        }
    ]
    prepared = decision_service.prepare_selection(
        task,
        instance_id=task.research_design_versions[0].instances[0].substage_instance_id,
        resolution=single_resolution,
        snapshot_envelope=snapshot,
        facts=facts,
        runtime_readiness=readiness,
        selection_request_id="selection-ot-structured-no-review",
        selection_command_hash="command-ot-structured-no-review",
        expected_task_revision=task.revision,
        owner_id="owner",
        workspace_id="workspace",
        decided_at=datetime.fromisoformat("2026-07-28T00:00:04+00:00"),
        workflow_lineage={
            "workflowId": "opentrajectory_research_workflow",
            "skillPack": "opentrajectory",
            "researchDesignId": task.active_research_design_ref.research_design_id,
            "researchDesignVersion": task.active_research_design_ref.version,
        },
    )

    materialized = _freeze_prepared_selection_for_test(prepared, task, prepared.final_tie_candidate_ids[0])

    selected = single_resolution.candidate_evaluations[0]
    assert selected.preference.completion_coverage.value == "completion_capable"
    assert any(
        ref.endswith(":executor-capability")
        for ref in selected.preference.semantic_fit_evidence.positive_rule_refs
    )
    assert materialized.decision.strategy.value == "direct_skill"
    assert materialized.decision.purpose.value == "completion"
    assert [row.role.value for row in materialized.decision.assignments] == ["primary"]
    assert materialized.decision.assignments[0].expected_artifact_types == list(
        prepared.instance.expected_artifact_types
    )
    assert materialized.task.work_units[-1].expected_artifact_types == list(
        prepared.instance.expected_artifact_types
    )
    assert materialized.decision.completion_policy is not None
    assert materialized.decision.completion_policy.reviewer_assignment_ids == []
    assert materialized.decision.completion_policy.review_requirement.value == "none"
    assert (
        materialized.decision.assignments[0].completion_coverage
        == selected.preference.completion_coverage
    )


def test_ot_composition_authority_ignores_generic_semantic_scoring() -> None:
    task, resolution, snapshot, facts, readiness, _skill_ids = _ot_composition_selection_fixture()
    payload = resolution.model_dump(mode="json", by_alias=True)
    first = next(
        row
        for row in payload["candidateEvaluations"]
        if row["candidateRef"].get("skillId") == "opentrajectory_source_acquisition"
    )
    first.update(
        {
            "eligible": False,
            "rejectionCodes": ["semantic_fit_insufficient"],
        }
    )
    first["preference"].update(
        {
            "completionCoverage": "progress_capable",
            "executableNow": False,
            "executionBlockerCodes": ["authorization_denied"],
        }
    )
    rescored = ResolutionResult.model_validate(payload)

    prepared = decision_service.prepare_selection(
        task,
        instance_id=task.research_design_versions[0].instances[0].substage_instance_id,
        resolution=rescored,
        snapshot_envelope=snapshot,
        facts=facts,
        runtime_readiness=readiness,
        selection_request_id="selection-ot-semantic-scoring",
        selection_command_hash="command-ot-semantic-scoring",
        expected_task_revision=task.revision,
        owner_id="owner",
        workspace_id="workspace",
        decided_at=datetime.fromisoformat("2026-07-28T00:00:04+00:00"),
        workflow_lineage={
            "workflowId": "opentrajectory_research_workflow",
            "skillPack": "opentrajectory",
            "researchDesignId": task.active_research_design_ref.research_design_id,
            "researchDesignVersion": task.active_research_design_ref.version,
        },
    )
    materialized = _freeze_prepared_selection_for_test(prepared, task, prepared.final_tie_candidate_ids[0])

    assert materialized.decision.strategy == ExecutionStrategy.DIRECT_SKILL
    assert [
        row.executor_ref.executor_id
        for row in materialized.decision.assignments
        if row.role == AssignmentRole.PRIMARY
    ] == ["opentrajectory_source_acquisition"]


def test_ot_composition_missing_required_skill_fails_closed_instead_of_agent_fallback() -> None:
    task, resolution, snapshot, facts, readiness, _skill_ids = _ot_composition_selection_fixture(
        missing_skill="opentrajectory_source_acquisition"
    )
    with pytest.raises(ExecutionPreflightBlocked) as blocked:
        decision_service.prepare_selection(
            task,
            instance_id=task.research_design_versions[0].instances[0].substage_instance_id,
            resolution=resolution,
            snapshot_envelope=snapshot,
            facts=facts,
            runtime_readiness=readiness,
            selection_request_id="selection-ot-missing-skill",
            selection_command_hash="command-ot-missing-skill",
            expected_task_revision=task.revision,
            owner_id="owner",
            workspace_id="workspace",
            decided_at=datetime.fromisoformat("2026-07-28T00:00:04+00:00"),
            workflow_lineage={
                "workflowId": "opentrajectory_research_workflow",
                "skillPack": "opentrajectory",
                "researchDesignId": task.active_research_design_ref.research_design_id,
                "researchDesignVersion": task.active_research_design_ref.version,
            },
        )

    assert blocked.value.code == "canonical_skill_composition_unavailable"


def test_ot_canonical_lineage_without_frozen_composition_fails_closed() -> None:
    task, resolution, snapshot, facts, readiness, _skill_ids = _ot_composition_selection_fixture()
    frozen_definition = snapshot["definitions"]["substages"][0]
    frozen_definition["pack"] = "opentrajectory"
    frozen_definition.pop("executionComposition")
    assert any(
        row.eligible
        and getattr(row.candidate_ref, "agent_id", None) == "general-research-agent"
        for row in resolution.candidate_evaluations
    )

    class Repository(_NoWorkflowLineageRepository):
        def __init__(self) -> None:
            self.needs_input_calls: list[dict] = []

        def lookup_selection_replay(self, **_kwargs):
            return None

        def load(self, **_kwargs):
            return task

        def load_registry_snapshot(self, _content_hash):
            return snapshot

        def load_execution_workflow_lineage(self, **_kwargs):
            return {
                "workflowId": "opentrajectory_research_workflow",
                "skillPack": "opentrajectory",
                "researchDesignId": task.active_research_design_ref.research_design_id,
                "researchDesignVersion": task.active_research_design_ref.version,
                "planningAttemptId": "planning-attempt-ot-historical",
            }

        def record_needs_input(self, **kwargs):
            self.needs_input_calls.append(kwargs)
            raise AssertionError(
                f"system preflight blocker must not enter needs_input: {kwargs['reason_code']}"
            )

        def commit_prepared_selection(self, *_args, **_kwargs):
            raise AssertionError("historical OT snapshot must block before materialization")

    class Resolver:
        def resolve(self, **_kwargs):
            return resolution

    repository = Repository()
    service = ResearchExecutionDecisionService(
        repository=repository,
        resolver=Resolver(),
        facts_source=lambda *_args: (facts, readiness),
        now=lambda: datetime.fromisoformat("2026-07-28T00:00:04+00:00"),
    )

    with pytest.raises(ExecutionPreflightBlocked) as blocked:
        service.start(
            task_id=task.task_id,
            instance_id=task.research_design_versions[0].instances[0].substage_instance_id,
            owner_id="owner",
            workspace_id="workspace",
            selection_request_id="selection-ot-historical-no-composition",
            expected_task_revision=task.revision,
        )

    assert blocked.value.code == "canonical_skill_composition_not_frozen"
    assert repository.needs_input_calls == []
    assert task.execution_decisions == []
    assert task.work_units == []


def test_ot_composition_requires_persisted_workflow_lineage_match() -> None:
    task, resolution, snapshot, facts, readiness, _skill_ids = _ot_composition_selection_fixture()
    with pytest.raises(ExecutionPreflightBlocked) as blocked:
        decision_service.prepare_selection(
            task,
            instance_id=task.research_design_versions[0].instances[0].substage_instance_id,
            resolution=resolution,
            snapshot_envelope=snapshot,
            facts=facts,
            runtime_readiness=readiness,
            selection_request_id="selection-ot-lineage-mismatch",
            selection_command_hash="command-ot-lineage-mismatch",
            expected_task_revision=task.revision,
            owner_id="owner",
            workspace_id="workspace",
            decided_at=datetime.fromisoformat("2026-07-28T00:00:04+00:00"),
            workflow_lineage={
                "workflowId": "opentrajectory_research_workflow",
                "skillPack": "opentrajectory",
                "researchDesignId": task.active_research_design_ref.research_design_id,
                "researchDesignVersion": task.active_research_design_ref.version + 1,
            },
        )

    assert blocked.value.code == "canonical_workflow_lineage_mismatch"


def test_non_ot_selection_preserves_existing_single_candidate_behavior(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task, resolution, snapshot, facts, readiness = _multi_artifact_selection_fixture(
        completion_coverage="progress_capable"
    )
    prepared = decision_service.prepare_selection(
        task,
        instance_id="sub_contract_full",
        resolution=resolution,
        snapshot_envelope=snapshot,
        facts=facts,
        runtime_readiness=readiness,
        selection_request_id="selection-non-ot-regression",
        selection_command_hash="command-non-ot-regression",
        expected_task_revision=task.revision,
        owner_id="owner",
        workspace_id="workspace",
        decided_at=datetime.fromisoformat("2026-07-28T00:00:04+00:00"),
        workflow_lineage=None,
    )

    assert prepared.required_skill_sequence == ()
    assert prepared.composition_identity is None
    assert prepared.final_tie_candidate_ids == ("skill:evidence_synthesis_skill:1",)

    monkeypatch.setattr(
        decision_service,
        "_upstream_dependency_materials",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("non-OT selection must not inherit canonical OT dependency artifacts")
        ),
    )
    materialized = _freeze_prepared_selection_for_test(prepared, task, prepared.final_tie_candidate_ids[0])
    assert materialized.decision.strategy == ExecutionStrategy.DIRECT_SKILL


def test_problem_framing_artifact_reaches_retrieval_and_evidence_design_without_filter_rules() -> None:
    user_query = "compare alpha at exactly 2C"
    provider_output = {
        "intent_understanding": {
            "original_user_query": user_query,
            "research_object": "alpha",
            "target_metrics": ["performance"],
            "boundary_conditions": ["exactly 2C"],
            "comparison_baseline": None,
            "output_intent": "comparison",
            "requested_source_scope": None,
            "ambiguities": [],
        },
        "constraints_criteria": {
            "interpreted_constraints": [{
                "constraint_name": "charge_rate",
                "original_phrase": "exactly 2C",
                "constraint_type": "condition_threshold",
                "interpreted_value": "2C",
                "candidate_values": [],
                "semantic_state": "ambiguous",
                "domain_rationale": "The explicit user condition is preserved.",
                "downstream_search_terms": ["2C charge rate"],
                "downstream_filter_rules": ["exclude everything"],
                "evidence_basis": "exactly 2C",
            }],
            "missing_information": [],
            "research_risks": [],
        },
    }
    problem_artifact, diagnostics = apply_opentrajectory_runtime_guard(
        skill_id="problem-framing-generation",
        input_payload={"user_query": user_query},
        output_payload=provider_output,
    )
    assert diagnostics["downstream_execution_allowed"] is True
    assert "downstream_filter_rules" not in problem_artifact[
        "constraints_criteria"
    ]["interpreted_constraints"][0]

    binding = next(
        row
        for row in load_skill_binding_source_rows()
        if row["definitionId"] == "opentrajectory:retrieval_strategy_design"
        and row["skillId"] == "retrieval_matrix_generation"
    )
    retrieval_input = {
        entry["targetField"]: deepcopy(
            problem_artifact[entry["source"]["field"]]
        )
        for entry in binding["inputSources"]["fields"]
    }
    input_schema = json.loads(
        (
            REPO_ROOT
            / "skills/opentrajectory/retrieval-matrix-generation/input.schema.json"
        ).read_text(encoding="utf-8")
    )
    assert list(Draft7Validator(input_schema).iter_errors(retrieval_input)) == []
    constraint = retrieval_input["constraints_criteria"]["interpreted_constraints"][0]
    assert retrieval_input["intent_understanding"]["original_user_query"] == user_query
    assert constraint["original_phrase"] == "exactly 2C"
    assert constraint["interpreted_value"] == "2C"

    intents = (
        "core_topic",
        "constraint_focused",
        "mechanism_focused",
        "method_or_strategy",
        "metric_or_performance",
        "adjacent_but_relevant",
        "noise_exclusion",
    )
    retrieval_draft = {
        "strategies": [{
            "search_intent": intent,
            "query_terms": [f"alpha concept {index}"],
            "must_include_terms": ["2C charge rate"] if index == 0 else [],
            "optional_terms": [],
            "exclude_terms": [],
            "expected_paper_type": None,
            "target_database": None,
            "source_constraints": ["charge_rate"],
            "rationale": "Uses the formal charge-rate constraint.",
            "priority": 1 if index < 2 else 2 if index < 5 else 3,
        } for index, intent in enumerate(intents)],
        "expanded_query_variants": [{
            "query": f'"alpha" AND "2C charge rate" AND "variant {index}"',
            "variant_type": "strict" if index == 0 else "broad",
            "search_intent": intents[index % len(intents)],
            "source_constraints": ["charge_rate"],
            "rationale": "Preserves the explicit condition in retrieval strategy.",
            "priority": 1 if index < 3 else 2 if index < 6 else 3,
        } for index in range(8)],
        "facets": {
            "core_entities": ["alpha"],
            "research_object_en": ["alpha"],
            "mechanisms": [],
            "target_metrics_en": ["performance"],
            "task_context": ["2C charge rate"],
            "must_have_terms": ["2C charge rate"],
            "nice_to_have_terms": [],
            "negative_contexts": [],
            "evaluation_terms": ["performance"],
        },
    }
    skill_root = REPO_ROOT / "skills/opentrajectory/retrieval-matrix-generation"
    declaration = yaml.safe_load((skill_root / "skill.yaml").read_text(encoding="utf-8"))[
        "finalization"
    ]
    frozen = build_frozen_finalization_contract(
        "skills/opentrajectory/retrieval-matrix-generation",
        declaration,
        repo_root=REPO_ROOT,
    )
    final_schema = json.loads((skill_root / "output.schema.json").read_text(encoding="utf-8"))
    finalized = finalize_and_validate_registered_output(
        repo_root=REPO_ROOT,
        skill_path="skills/opentrajectory/retrieval-matrix-generation",
        frozen_contract=frozen,
        input_payload=retrieval_input,
        draft_payload=retrieval_draft,
        final_schema=final_schema,
    )

    assert finalized.status == "succeeded"
    assert finalized.output["expanded_query_variants"][0]["query"] == (
        '"alpha" AND "2C charge rate" AND "variant 0"'
    )
    assert finalized.output["quality_report"]["constraints_utilized"] == [
        "charge_rate"
    ]

    evidence_binding = next(
        row
        for row in load_skill_binding_source_rows()
        if row["bindingId"]
        == "bind:opentrajectory:evidence_schema_design:skill:evidence_schema_design"
    )
    upstream = {
        "bind:opentrajectory:problem_framing:skill:problem_framing_generation": problem_artifact,
        "bind:opentrajectory:retrieval_strategy_design:skill:retrieval_matrix_generation": finalized.output,
    }
    evidence_input = {"task_id": "task-filter-authority-migration"}
    for entry in evidence_binding["inputSources"]["fields"]:
        source = entry["source"]
        artifact = upstream[source["bindingId"]]
        evidence_input[entry["targetField"]] = deepcopy(
            artifact if source["field"] == "$" else artifact[source["field"]]
        )

    evidence_schema = json.loads(
        (
            REPO_ROOT
            / "skills/opentrajectory/evidence-schema-design-skill/input.schema.json"
        ).read_text(encoding="utf-8")
    )
    assert list(Draft7Validator(evidence_schema).iter_errors(evidence_input)) == []
    evidence_constraint = evidence_input["constraints_criteria"][
        "interpreted_constraints"
    ][0]
    assert evidence_constraint["original_phrase"] == "exactly 2C"
    assert evidence_constraint["interpreted_value"] == "2C"
    assert "downstream_filter_rules" not in evidence_constraint
    assert evidence_input["retrieval_strategy_design"] == finalized.output
