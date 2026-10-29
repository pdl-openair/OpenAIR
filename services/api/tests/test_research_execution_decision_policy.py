"""ExecutionDecision deterministic policy and completion-authority tests."""

from __future__ import annotations
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from app.models.research_orchestration import (
    ExecutionDecision,
    ExecutionDecisionOutcome,
    MultiAgentRecipeDefinition,
    ResearchTaskV12,
    TaskExecutionMode,
)
from app.modules.research_execution_decision_service import (
    DecisionPolicyViolation,
    ExecutionPreflightBlocked,
    close_decision_from_durable_facts,
    build_production_resolution_facts,
    plan_execution_continuation,
    promote_dependency_ready_substages,
    reconcile_closed_successful_result_progress,
    reconcile_persisted_accepted_revision_progress,
    reconcile_task_lifecycle_from_runtime,
    resolve_execution_outcome,
    validate_execution_decision,
)
from app.modules import research_execution_decision_service as decision_service
from tests.support.research_execution import (
    multi_artifact_selection_fixture as _multi_artifact_selection_fixture,
)
from tests.support.research_execution_decision import (
    build_planned_continuation_task,
    freeze_prepared_selection_for_test as _freeze_prepared_selection_for_test,
    resolution_for_execution_decision as _resolution,
    structural_execution_decision_payload as _decision,
)
from tests.support.research_lifecycle import build_confirmed_research_task

FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "packages"
    / "shared"
    / "test-fixtures"
    / "research-orchestration-v1.2-valid.json"
)

def _fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_unknown_execution_result_reconciles_instead_of_replaying_or_terminalizing() -> None:
    resolution = resolve_execution_outcome("unknown")

    assert resolution.system_action == "reconcile"
    assert resolution.work_unit_status.value == "external_result_unknown"
    assert resolution.decision_outcome is None


def test_closed_successful_result_repairs_only_a_stale_recoverable_runtime() -> None:
    payload = deepcopy(_fixture()["researchTask"])
    payload["workUnits"][0].update(
        {
            "status": "completed",
            "terminalReasonCode": None,
            "finishedAt": "2026-09-21T00:00:00Z",
        }
    )
    payload["artifacts"][0]["artifactState"] = "usable"
    closed = close_decision_from_durable_facts(
        ResearchTaskV12.model_validate(payload),
        execution_decision_id=payload["executionDecisions"][0]["executionDecisionId"],
        closed_at=datetime.fromisoformat("2026-09-21T00:01:00+00:00"),
    )
    stale_payload = closed.model_dump(mode="json", by_alias=True)
    stale_payload["substageRuntimeStates"][0].update(
        {
            "status": "blocked_recoverable",
            "statusReasonCode": "external_result_unknown",
            "activeExecutionDecisionId": None,
        }
    )
    stale = ResearchTaskV12.model_validate(stale_payload)

    repaired = reconcile_closed_successful_result_progress(
        stale,
        completed_execution_decision_id=closed.execution_decisions[0].execution_decision_id,
        occurred_at=datetime.fromisoformat("2026-09-21T00:02:00+00:00"),
    )

    assert repaired.execution_decisions[0] == stale.execution_decisions[0]
    assert repaired.work_units == stale.work_units
    assert repaired.artifacts == stale.artifacts
    assert repaired.substage_runtime_states[0].status.value == "completed"
    assert repaired.substage_runtime_states[0].status_reason_code is None


def test_ot_v3_terminal_dependencies_promote_only_after_completed_upstream() -> None:
    task = build_planned_continuation_task(next_stage_key="insight_generation")
    payload = task.model_dump(mode="json", by_alias=True)
    design = payload["researchDesignVersions"][0]
    design["workflowProvenanceRef"] = {
        "workflowId": "opentrajectory_research_workflow",
        "workflowVersion": 3,
        "contentHash": f"sha256:{'d' * 64}",
    }
    current, unknowns = design["instances"]
    current["substageDefinitionId"] = (
        "opentrajectory:current_research_conclusion"
    )
    payload["workUnits"][0]["substageDefinitionId"] = current[
        "substageDefinitionId"
    ]
    unknowns["substageDefinitionId"] = (
        "opentrajectory:key_unknowns_candidate_explanations"
    )
    unknowns["dependencies"][0].update(
        {
            "conditionType": "instance_completed",
            "requiredArtifactTypes": [],
        }
    )
    recommendation = deepcopy(unknowns)
    recommendation.update(
        {
            "substageInstanceId": "sub_next_recommendation",
            "substageDefinitionId": (
                "opentrajectory:next_research_recommendation"
            ),
            "sequenceHint": 2,
        }
    )
    recommendation["acceptanceCriteria"][0]["criterionId"] = (
        "criterion_next_recommendation"
    )
    recommendation["dependencies"] = [
        {
            "dependencyId": "dependency_unknowns_completed",
            "upstreamInstanceRef": {
                "substageInstanceId": unknowns["substageInstanceId"],
                "instanceRevision": unknowns["instanceRevision"],
            },
            "conditionType": "instance_completed",
            "requiredArtifactTypes": [],
            "confirmationTargetRef": None,
        }
    ]
    design["instances"].append(recommendation)
    payload["substageRuntimeStates"].append(
        {
            "substageInstanceId": recommendation["substageInstanceId"],
            "instanceRevision": recommendation["instanceRevision"],
            "status": "planned",
            "activeExecutionDecisionId": None,
            "workUnitIds": [],
            "artifactRefs": [],
            "statusReasonCode": None,
            "updatedAt": "2026-07-28T00:00:03Z",
        }
    )
    completed_current = ResearchTaskV12.model_validate(payload)

    incomplete_payload = deepcopy(payload)
    incomplete_payload["substageRuntimeStates"][0].update(
        {
            "status": "blocked_recoverable",
            "statusReasonCode": "acceptance_criteria_unresolved",
        }
    )
    incomplete_current = ResearchTaskV12.model_validate(incomplete_payload)
    still_blocked = promote_dependency_ready_substages(
        incomplete_current,
        occurred_at=datetime.fromisoformat("2026-07-28T00:01:01+00:00"),
    )
    assert still_blocked.substage_runtime_states[1].status.value == "planned"

    unknowns_ready = promote_dependency_ready_substages(
        completed_current,
        occurred_at=datetime.fromisoformat("2026-07-28T00:01:02+00:00"),
    )
    assert unknowns_ready.substage_runtime_states[1].status.value == "ready"
    assert unknowns_ready.substage_runtime_states[2].status.value == "planned"

    completed_unknowns_payload = unknowns_ready.model_dump(mode="json", by_alias=True)
    completed_unknowns_payload["substageRuntimeStates"][1].update(
        {"status": "completed", "statusReasonCode": None}
    )
    completed_unknowns = ResearchTaskV12.model_validate(completed_unknowns_payload)
    recommendation_ready = promote_dependency_ready_substages(
        completed_unknowns,
        occurred_at=datetime.fromisoformat("2026-07-28T00:01:03+00:00"),
    )
    assert recommendation_ready.substage_runtime_states[2].status.value == "ready"


@pytest.mark.parametrize(
    "reason_code",
    (
        "provider_response_contract_invalid",
        "skill_input_invalid",
        "key_literature_grounded_candidates_missing",
        "key_literature_source_not_grounded",
        "future_internal_failure_code",
    ),
)
def test_blocked_work_unit_closes_recoverable_without_task_terminalization(reason_code: str) -> None:
    payload = deepcopy(_fixture()["researchTask"])
    payload["workUnits"][0].update(
        {
            "status": "blocked",
            "terminalReasonCode": reason_code,
            "finishedAt": "2026-08-14T09:30:00Z",
        }
    )
    payload["capabilityCalls"][0].update(
        {
            "status": "failed",
            "finishedAt": "2026-08-14T09:30:00Z",
            "outputArtifactRefs": [],
        }
    )
    task = ResearchTaskV12.model_validate(payload)

    closed = close_decision_from_durable_facts(
        task,
        execution_decision_id=task.execution_decisions[0].execution_decision_id,
        closed_at=datetime.fromisoformat("2026-08-14T09:30:00+00:00"),
    )
    reconciled = reconcile_task_lifecycle_from_runtime(
        closed,
        occurred_at=datetime.fromisoformat("2026-08-14T09:30:00+00:00"),
    )

    assert closed.execution_decisions[0].outcome.value == "blocked"
    assert closed.substage_runtime_states[0].status.value == "blocked_recoverable"
    assert reconciled.status.value == "running"
    assert reconciled.finished_at is None


def _as_general_fallback(payload: dict) -> dict:
    decision = deepcopy(payload)
    assignment = decision["assignments"][0]
    assignment.update(
        {
            "responsibleActorRef": {
                "actorType": "agent",
                "actorId": "general-research-agent",
                "definitionVersion": 1,
            },
            "executorRef": {
                "executorType": "agent",
                "executorId": "general-research-agent",
                "definitionVersion": 1,
            },
        }
    )
    decision.update(
        {
            "strategy": "general_research_agent",
            "fallbackInvoked": True,
            "fallbackReasonCode": "no_completion_coverage_preferred_candidate",
            "rationale": "Fallback requested despite the preferred executable Skill.",
        }
    )
    return decision


def test_root_workunit_inherits_only_usable_exact_upstream_dependency_artifacts() -> None:
    exact = SimpleNamespace(
        work_unit_id="work-exact",
        source_mode=decision_service.WorkUnitSourceMode.LIVE,
        status=decision_service.WorkUnitStatus.COMPLETED,
        research_design_version=3,
        substage_instance_id="upstream",
        substage_instance_revision=2,
    )
    stale_revision = SimpleNamespace(
        work_unit_id="work-stale",
        source_mode=decision_service.WorkUnitSourceMode.LIVE,
        status=decision_service.WorkUnitStatus.COMPLETED,
        research_design_version=3,
        substage_instance_id="upstream",
        substage_instance_revision=1,
    )
    old_design = SimpleNamespace(
        work_unit_id="work-old-design",
        source_mode=decision_service.WorkUnitSourceMode.LIVE,
        status=decision_service.WorkUnitStatus.COMPLETED,
        research_design_version=2,
        substage_instance_id="upstream",
        substage_instance_revision=2,
    )
    task = SimpleNamespace(
        work_units=[exact, stale_revision, old_design],
        artifacts=[
            SimpleNamespace(
                artifact_id="artifact-exact",
                artifact_state=decision_service.ArtifactState.USABLE,
                work_unit_id="work-exact",
            ),
            SimpleNamespace(
                artifact_id="artifact-needs-review",
                artifact_state=decision_service.ArtifactState.NEEDS_REVIEW,
                work_unit_id="work-exact",
            ),
            SimpleNamespace(
                artifact_id="artifact-stale",
                artifact_state=decision_service.ArtifactState.USABLE,
                work_unit_id="work-stale",
            ),
            SimpleNamespace(
                artifact_id="artifact-old-design",
                artifact_state=decision_service.ArtifactState.USABLE,
                work_unit_id="work-old-design",
            ),
        ],
    )
    instance = SimpleNamespace(
        dependencies=[
            SimpleNamespace(
                upstream_instance_ref=SimpleNamespace(
                    substage_instance_id="upstream", instance_revision=2
                )
            )
        ]
    )

    artifact_refs, work_unit_ids = decision_service._upstream_dependency_materials(
        task, instance, research_design_version=3
    )

    assert artifact_refs == ["artifact-exact"]
    assert work_unit_ids == ["work-exact"]


def test_declared_cross_substage_dependency_requires_upstream_materialization() -> None:
    instance = SimpleNamespace(dependencies=[object()])

    assert decision_service._should_materialize_upstream_dependencies(
        None,
        instance,
        required_skill_sequence=(),
    ) is True


def test_design_approval_is_not_forwarded_as_an_undeclared_human_gate_fact() -> None:
    task = ResearchTaskV12.model_validate(_fixture()["researchTask"])
    instance = task.research_design_versions[0].instances[0]

    assert decision_service._satisfied_human_gate_refs(task, instance) == set()


def test_fallback_is_rejected_when_completion_capable_executable_preferred_exists() -> None:
    payload = _as_general_fallback(_decision())

    decision = ExecutionDecision.model_validate(payload)
    with pytest.raises(DecisionPolicyViolation, match="fallback_completion_candidate_exists"):
        validate_execution_decision(
            decision,
            resolution=_resolution(payload),
            snapshot_envelope={"definitions": {"agents": []}},
        )


def test_general_agent_cannot_claim_professional_completion_when_specialist_is_temporarily_blocked() -> None:
    payload = _as_general_fallback(_decision())
    specialist = deepcopy(payload["candidateEvaluations"][0])
    specialist.update(
        {
            "candidateEvaluationId": "candidate_specialist_blocked",
            "candidateRef": {"agentId": "domain-specialist", "definitionVersion": 1},
            "sourceBindingRefs": [{"bindingId": "binding_specialist", "bindingVersion": 1}],
            "supportedRole": "primary",
            "rationale": "Professional specialist is eligible but temporarily unavailable.",
        }
    )
    specialist["preference"].update(
        {
            "executableNow": False,
            "executionBlockerCodes": ["provider_unavailable"],
            "supportsProfessionalJudgment": True,
        }
    )
    payload["candidateEvaluations"] = [specialist]
    payload["preferredCandidateEvaluationRefs"] = ["candidate_specialist_blocked"]
    criterion = payload["assignments"][0]["acceptanceCriteria"][0]
    criterion.update(
        {
            "judgmentRequirement": "domain_professional",
            "evaluatorType": "agent",
            "evaluatorRef": "domain-specialist",
        }
    )

    decision = ExecutionDecision.model_validate(payload)
    with pytest.raises(DecisionPolicyViolation, match="professional_fallback_requires_progress"):
        validate_execution_decision(
            decision,
            resolution=_resolution(payload),
            snapshot_envelope={
                "definitions": {
                    "agents": [
                        {
                            "agentId": "domain-specialist",
                            "definitionVersion": 1,
                            "enabled": True,
                            "agentKind": "specialist",
                        }
                    ]
                }
            },
        )


def test_multi_agent_decision_cannot_invent_or_omit_recipe_templates() -> None:
    payload = _decision()
    first = payload["assignments"][0]
    first.update({"assignmentId": "assignment_a", "recipeTemplateKey": "invented_a"})
    second = deepcopy(first)
    second.update({"assignmentId": "assignment_b", "recipeTemplateKey": "invented_b"})
    payload.update(
        {
            "strategy": "multi_agent",
            "multiAgentRecipeRef": {"recipeId": "registered_recipe", "recipeVersion": 1},
            "assignments": [first, second],
        }
    )
    payload["completionPolicy"].update(
        {"requiredAssignmentIds": ["assignment_a", "assignment_b"]}
    )
    payload["resultAssemblyPolicy"]["finalArtifactRequirements"][0][
        "sourceAssignmentIds"
    ] = ["assignment_a", "assignment_b"]

    recipe = MultiAgentRecipeDefinition.model_validate(
        {
            "recipeId": "registered_recipe",
            "recipeVersion": 1,
            "substageDefinitionId": "sdr:evidence_synthesis",
            "substageDefinitionVersion": 1,
            "applicabilityRuleRefs": ["rule:registered"],
            "assignmentTemplates": [
                {
                    "templateKey": "registered_primary",
                    "role": "primary",
                    "requiredSupportedRole": "primary",
                    "objectiveTemplate": "Primary {objective}",
                    "dependencyTemplateKeys": [],
                },
                {
                    "templateKey": "registered_support",
                    "role": "support",
                    "requiredSupportedRole": "support",
                    "objectiveTemplate": "Support {objective}",
                    "dependencyTemplateKeys": ["registered_primary"],
                },
            ],
            "completionPolicyTemplate": {
                "requiredTemplateKeys": ["registered_primary", "registered_support"],
                "successMode": "all_required",
                "reviewerTemplateKeys": [],
                "reviewRequirement": "none",
                "partialSuccessAllowed": False,
                "onReviewerReject": "new_revision_decision",
            },
            "resultAssemblyPolicyTemplate": {
                "mode": "primary_output",
                "assemblerTemplateKey": None,
                "finalArtifactRequirements": [
                    {
                        "artifactType": "evidence_summary",
                        "minimumCount": 1,
                        "sourceTemplateKeys": ["registered_primary"],
                    }
                ],
            },
            "enabled": True,
        }
    )
    decision = ExecutionDecision.model_validate(payload)
    with pytest.raises(DecisionPolicyViolation, match="recipe_template_one_to_one_violation"):
        validate_execution_decision(
            decision,
            resolution=_resolution(payload),
            snapshot_envelope={"definitions": {"agents": []}},
            recipe=recipe,
        )


def test_progress_decision_cannot_complete_substage() -> None:
    task = _fixture()["researchTask"]
    decision = task["executionDecisions"][0]
    decision.update(
        {
            "purpose": "progress",
            "completionPolicy": None,
            "progressPolicy": {
                "requiredAssignmentIds": ["assignment_contract_full"],
                "requiredArtifactTypes": ["evidence_summary"],
                "acceptedResultCodes": "progressed",
            },
            "status": "dispatched",
            "outcome": None,
            "closedAt": None,
        }
    )
    decision["assignments"][0]["completionCoverage"] = "progress_capable"
    task["workUnits"][0].update(
        {
            "status": "completed",
            "finishedAt": "2026-07-28T00:00:59Z",
        }
    )
    task["substageRuntimeStates"][0].update(
        {"status": "reviewing", "activeExecutionDecisionId": "decision_contract_full"}
    )

    closed = close_decision_from_durable_facts(
        ResearchTaskV12.model_validate(task),
        execution_decision_id="decision_contract_full",
        closed_at=datetime.fromisoformat("2026-07-28T00:01:00+00:00"),
    )

    assert closed.execution_decisions[0].outcome.value == "progressed"
    assert closed.substage_runtime_states[0].status.value == "blocked_recoverable"


def test_multi_artifact_completion_selects_and_freezes_typed_requirements() -> None:
    """Single primary Skill may formally own multiple expected Artifact types."""
    from app.models.research_orchestration import RuntimeReadinessSnapshot

    task, resolution, snapshot, facts, readiness = _multi_artifact_selection_fixture(
        completion_coverage="completion_capable"
    )
    snapshot = deepcopy(snapshot)
    snapshot["definitions"]["agents"] = [
        {
            "agentId": "result_reliability_audit",
            "definitionVersion": 1,
            "enabled": True,
            "reviewContract": {
                "frozenArtifactContract": {
                    "artifactContract": "independent_result_review_v1"
                }
            },
        }
    ]
    readiness = {
        **readiness,
        "agent:result_reliability_audit:1": RuntimeReadinessSnapshot.model_validate(
            {
                "capabilityRef": {
                    "agentId": "result_reliability_audit",
                    "definitionVersion": 1,
                },
                "ready": True,
                "runtimeBindingId": "registry:review-contract:result_reliability_audit:1",
                "checkedAt": "2026-07-28T00:00:03Z",
                "expiresAt": "2026-07-28T00:05:03Z",
                "reason": None,
            }
        ),
    }

    prepared = decision_service.prepare_selection(
        task,
        instance_id="sub_contract_full",
        resolution=resolution,
        snapshot_envelope=snapshot,
        facts=facts,
        runtime_readiness=readiness,
        selection_request_id="selection-multi-artifact-completion",
        selection_command_hash="command-multi-artifact-completion",
        expected_task_revision=task.revision,
        owner_id="owner",
        workspace_id="workspace",
        decided_at=datetime.fromisoformat("2026-07-28T00:00:04+00:00"),
    )
    materialized = _freeze_prepared_selection_for_test(
        prepared,
        task,
        "skill:evidence_synthesis_skill:1",
    )

    assert materialized.decision.purpose.value == "completion"
    assert materialized.decision.result_assembly_policy.mode.value == "artifact_set"
    assert [
        row.artifact_type
        for row in materialized.decision.result_assembly_policy.final_artifact_requirements
    ] == ["evidence_summary", "comparison_matrix"]
    assert materialized.decision.assignments[0].expected_artifact_types == [
        "evidence_summary",
        "comparison_matrix",
    ]
    assert materialized.task.work_units[0].expected_artifact_types == [
        "evidence_summary",
        "comparison_matrix",
    ]


def test_multi_artifact_progress_candidate_remains_zero_output() -> None:
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
        selection_request_id="selection-multi-artifact-progress",
        selection_command_hash="command-multi-artifact-progress",
        expected_task_revision=task.revision,
        owner_id="owner",
        workspace_id="workspace",
        decided_at=datetime.fromisoformat("2026-07-28T00:00:04+00:00"),
    )

    materialized = _freeze_prepared_selection_for_test(
        prepared,
        task,
        "skill:evidence_synthesis_skill:1",
    )

    assert materialized.decision.purpose.value == "progress"
    assert materialized.decision.result_assembly_policy.final_artifact_requirements == []
    assert materialized.decision.assignments[0].expected_artifact_types == []
    assert materialized.task.work_units[-1].expected_artifact_types == []


def test_expected_artifact_checklist_does_not_override_satisfied_criteria() -> None:
    """A Design projection cannot create continuation work by itself."""
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
        selection_request_id="artifact-checklist-not-authority",
        selection_command_hash="artifact-checklist-not-authority",
        expected_task_revision=task.revision,
        owner_id="owner",
        workspace_id="workspace",
        decided_at=datetime.fromisoformat("2026-07-28T00:00:04+00:00"),
    )
    materialized = _freeze_prepared_selection_for_test(prepared, task, "skill:evidence_synthesis_skill:1")
    payload = materialized.task.model_dump(mode="json", by_alias=True)
    payload["workUnits"][0].update({"status": "completed", "finishedAt": "2026-07-28T00:00:59Z"})
    artifact = deepcopy(_fixture()["researchTask"]["artifacts"][0])
    artifact.update({
        "artifactId": "artifact_only_confirmed_criterion",
        "workUnitId": payload["workUnits"][0]["workUnitId"],
            "substageInstanceId": "sub_contract_full",
            "sourceCapabilityCallId": None,
            "lineageKind": "direct_work_unit_output",
            "artifactType": "evidence_summary",
        "artifactState": "usable",
    })
    payload["artifacts"] = [artifact]
    closed = close_decision_from_durable_facts(
        ResearchTaskV12.model_validate(payload),
        execution_decision_id=materialized.decision.execution_decision_id,
        closed_at=datetime.fromisoformat("2026-07-28T00:01:00+00:00"),
    )
    state = closed.scientific_states[-1].execution_state["substageCompletion"][0]
    assert state["completionCriteriaSatisfied"] is True
    assert closed.substage_runtime_states[0].status.value == "completed"
    assert plan_execution_continuation(
        closed,
        completed_execution_decision_id=materialized.decision.execution_decision_id,
        execution_mode=TaskExecutionMode.AUTOMATIC_RESEARCH,
    ).action == "none"


def test_production_resolution_facts_expose_latest_design_scientific_state() -> None:
    payload = build_confirmed_research_task().model_dump(mode="json", by_alias=True)
    payload["scientificStates"] = [{
        "scientificStateVersion": 1,
        "researchDesignVersion": 1,
        "scientificFacts": {"validatedArtifactRefs": []},
        "executionState": {
            "terminalSubstages": False,
            "substageCompletion": [{
                "substageInstanceId": "sub_contract_full",
                "instanceRevision": 1,
                "unresolvedCriterionIds": ["criterion_contract_full"],
            }],
        },
        "sourceRefs": [],
        "createdAt": "2026-08-19T00:00:00Z",
    }]
    task = ResearchTaskV12.model_validate(payload)
    facts, _ = build_production_resolution_facts(
        task,
        task.research_design_versions[0].instances[0],
        {
            "definitions": {"skills": [], "agents": []},
            "bindings": {"skills": [], "agents": []},
            "recipes": [],
            "staticPolicies": {"policyVersion": "registry-policy.v1"},
        },
    )
    assert facts.current_scientific_state is not None
    assert facts.current_scientific_state["scientificStateVersion"] == 1


def test_recovered_revision_progress_reenters_next_task_authority_without_rewriting_history() -> None:
    payload = deepcopy(_fixture()["researchTask"])
    payload["revision"] = 20
    criterion = payload["researchDesignVersions"][0]["instances"][0]["acceptanceCriteria"][0]
    criterion.update({
        "criterionKind": "professional_judgment",
        "judgmentRequirement": "domain_professional",
        "evaluatorType": "agent",
        "evaluatorRef": "result_reliability_audit",
    })
    assignment_criterion = payload["executionDecisions"][0]["assignments"][0][
        "acceptanceCriteria"
    ][0]
    assignment_criterion.update({
        "criterionKind": "professional_judgment",
        "judgmentRequirement": "domain_professional",
        "evaluatorType": "agent",
        "evaluatorRef": "result_reliability_audit",
    })

    old_decision = payload["executionDecisions"][0]
    old_decision.update({
        "status": "closed",
        "outcome": "blocked",
        "closedAt": "2026-09-19T14:00:00Z",
    })
    old_work = payload["workUnits"][0]
    old_work.update({
        "status": "cancelled",
        "terminalReasonCode": "external_result_unknown",
        "finishedAt": "2026-09-19T14:00:00Z",
    })
    payload["capabilityCalls"][0].update({
        "status": "failed",
        "outputArtifactRefs": [],
        "finishedAt": "2026-09-19T14:00:00Z",
    })
    payload["artifacts"] = []
    payload["agentInteractionRecords"] = []

    recovery_decision = deepcopy(old_decision)
    recovery_decision.update({
        "executionDecisionId": "decision_recovery",
        "selectionRequestId": "selection_recovery",
        "decisionSequence": 2,
        "supersedesExecutionDecisionId": old_decision["executionDecisionId"],
        "revisionReasonCode": "research_revision",
        "status": "dispatched",
        "outcome": None,
        "closedAt": None,
    })
    recovery_assignment = recovery_decision["assignments"][0]
    recovery_assignment["assignmentId"] = "assignment_recovery"
    recovery_assignment["expectedArtifactTypes"] = []
    recovery_decision["completionPolicy"]["requiredAssignmentIds"] = [
        "assignment_recovery"
    ]
    recovery_decision["completionPolicy"]["reviewerAssignmentIds"] = []
    recovery_decision["resultAssemblyPolicy"]["finalArtifactRequirements"] = []
    payload["executionDecisions"].append(recovery_decision)

    recovery_work = deepcopy(old_work)
    recovery_work.update({
        "workUnitId": "wu_recovery",
        "idempotencyKey": "workunit:decision_recovery:assignment_recovery",
        "executionDecisionId": "decision_recovery",
        "assignmentId": "assignment_recovery",
        "status": "completed",
        "terminalReasonCode": None,
        "finishedAt": "2026-09-19T14:19:00Z",
        "expectedArtifactTypes": [],
    })
    payload["workUnits"].append(recovery_work)

    recovery_call = deepcopy(payload["capabilityCalls"][0])
    recovery_call.update({
        "capabilityCallId": "call_recovery",
        "idempotencyKey": "call:wu_recovery:1",
        "workUnitId": "wu_recovery",
        "status": "succeeded",
        "outputArtifactRefs": ["artifact_recovery"],
        "startedAt": "2026-09-19T14:18:00Z",
        "finishedAt": "2026-09-19T14:19:00Z",
    })
    payload["capabilityCalls"].append(recovery_call)
    payload["artifacts"].append({
        "artifactId": "artifact_recovery",
        "workUnitId": "wu_recovery",
        "substageInstanceId": "sub_contract_full",
        "sourceCapabilityCallId": "call_recovery",
        "artifactType": "recovered_intermediate_inputs",
        "artifactState": "usable",
        "version": 1,
        "sourceRefs": ["material_contract"],
        "validatorResult": {"status": "passed"},
        "lineageKind": "capability_call_output",
        "sourceResultId": None,
    })
    payload["substageRuntimeStates"][0].update({
        "status": "running",
        "activeExecutionDecisionId": "decision_recovery",
        "workUnitIds": ["wu_contract_full", "wu_recovery"],
        "artifactRefs": ["artifact_recovery"],
        "statusReasonCode": None,
        "updatedAt": "2026-09-19T14:18:00Z",
    })

    closed = close_decision_from_durable_facts(
        ResearchTaskV12.model_validate(payload),
        execution_decision_id="decision_recovery",
        closed_at=datetime.fromisoformat("2026-09-19T14:20:00+00:00"),
        recoverable_terminal_outcome=ExecutionDecisionOutcome.BLOCKED,
    )
    state = closed.scientific_states[-1].execution_state["substageCompletion"][0]
    plan = plan_execution_continuation(
        closed,
        completed_execution_decision_id="decision_recovery",
        execution_mode=TaskExecutionMode.AUTOMATIC_RESEARCH,
    )

    assert closed.execution_decisions[0].outcome.value == "blocked"
    assert closed.work_units[0].status.value == "cancelled"
    assert closed.execution_decisions[-1].outcome.value == "blocked"
    assert closed.substage_runtime_states[0].status.value == "ready"
    assert state["unresolvedCriterionIds"] == ["criterion_contract_full"]
    assert state["unmetCriterionIds"] == []
    assert state["needsAction"] is True
    assert "work_unit:wu_contract_full:cancelled" not in state["gaps"]
    assert closed.scientific_states[-1].execution_state["validatedArtifactRefs"] == [
        "artifact_recovery"
    ]
    assert plan.action == "start"
    assert plan.next_instance_id is None
    assert closed.completion_decisions == []
    assert closed.final_results == []

    # Simulate the same durable recovery having been finalized by an older
    # binary: recovered evidence exists, while state/runtime still carry the
    # superseded cancelled attempt as current failure authority.
    legacy_payload = closed.model_dump(mode="json", by_alias=True)
    legacy_runtime = legacy_payload["substageRuntimeStates"][0]
    legacy_runtime.update({
        "status": "blocked_recoverable",
        "statusReasonCode": "external_result_unknown",
        "activeExecutionDecisionId": None,
    })
    legacy_state = legacy_payload["scientificStates"][-1]
    legacy_completion = legacy_state["executionState"]["substageCompletion"][0]
    legacy_completion["criteria"][0]["status"] = "unmet"
    legacy_completion.update({
        "completionCriteriaSatisfied": False,
        "unresolvedCriterionIds": [],
        "unmetCriterionIds": ["criterion_contract_full"],
        "gaps": [
            "criterion:criterion_contract_full",
            "runtime:sub_contract_full:blocked_recoverable",
            "work_unit:wu_contract_full:cancelled",
        ],
        "uncertainty": [],
        "needsAction": False,
        "runtimeStatus": "blocked_recoverable",
        "hasExecutionDegradation": True,
    })
    legacy = ResearchTaskV12.model_validate(legacy_payload)
    reconciled_existing = reconcile_persisted_accepted_revision_progress(
        legacy,
        completed_execution_decision_id="decision_recovery",
        occurred_at=datetime.fromisoformat("2026-09-20T00:30:00+00:00"),
    )
    reconciled_state = reconciled_existing.scientific_states[-1].execution_state[
        "substageCompletion"
    ][0]
    reconciled_plan = plan_execution_continuation(
        reconciled_existing,
        completed_execution_decision_id="decision_recovery",
        execution_mode=TaskExecutionMode.AUTOMATIC_RESEARCH,
    )

    assert reconciled_existing.scientific_states[-1].scientific_state_version == (
        legacy.scientific_states[-1].scientific_state_version + 1
    )
    assert reconciled_existing.substage_runtime_states[0].status.value == "ready"
    assert reconciled_state["unresolvedCriterionIds"] == ["criterion_contract_full"]
    assert reconciled_state["unmetCriterionIds"] == []
    assert reconciled_state["needsAction"] is True
    assert "work_unit:wu_contract_full:cancelled" not in reconciled_state["gaps"]
    assert reconciled_plan.action == "start"
    assert reconciled_plan.next_instance_id is None
    assert reconciled_existing.work_units[0].status.value == "cancelled"
    assert reconciled_existing.execution_decisions[0].outcome.value == "blocked"
    assert reconciled_existing.completion_decisions == []
    assert reconciled_existing.final_results == []


def test_unmet_criterion_after_failed_action_does_not_requeue_same_substage() -> None:
    payload = _fixture()["researchTask"]
    payload["workUnits"][0].update({
        "status": "blocked",
        "terminalReasonCode": "runtime_unavailable",
        "finishedAt": "2026-07-28T00:00:59Z",
    })
    payload["capabilityCalls"][0].update({"status": "failed", "finishedAt": "2026-07-28T00:00:59Z"})
    closed = close_decision_from_durable_facts(
        ResearchTaskV12.model_validate(payload),
        execution_decision_id="decision_contract_full",
        closed_at=datetime.fromisoformat("2026-07-28T00:01:00+00:00"),
    )
    state = closed.scientific_states[-1].execution_state["substageCompletion"][0]
    assert state["unmetCriterionIds"] == ["criterion_contract_full"]
    assert state["needsAction"] is False
    assert closed.substage_runtime_states[0].status.value == "blocked_recoverable"
    assert plan_execution_continuation(
        closed,
        completed_execution_decision_id="decision_contract_full",
        execution_mode=TaskExecutionMode.AUTOMATIC_RESEARCH,
    ).action == "none"
