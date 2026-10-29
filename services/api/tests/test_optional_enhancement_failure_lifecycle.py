"""Shared optional-enhancement failure isolation for CS, OT, and PVD."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from types import SimpleNamespace

import pytest
import yaml

from app.models.research_orchestration import (
    ExecutionDecisionOutcome,
    ExecutionDecisionStatus,
    ResearchTaskV12,
    SubstageRuntimeStatus,
    TaskExecutionMode,
    TaskStatus,
)
from app.modules.research_execution_decision_service import plan_execution_continuation
from tests.support.research_execution_decision import (
    build_ready_continuation_task as _task_ready_for_continuation,
)


@dataclass(frozen=True)
class OptionalEnhancementCase:
    case_id: str
    workflow_file: str
    origin_definition_id: str
    origin_definition_version: int
    downstream_definition_id: str
    downstream_definition_version: int


CASES = (
    OptionalEnhancementCase(
        case_id="cs_deviation_diagnosis",
        workflow_file="cs_paper_reproduction_workflow_v7.yaml",
        origin_definition_id="cs_reproduction:deviation_diagnosis_validation",
        origin_definition_version=4,
        downstream_definition_id=(
            "cs_reproduction:reproduction_conclusion_recommendation"
        ),
        downstream_definition_version=4,
    ),
    OptionalEnhancementCase(
        case_id="ot_mechanism_explanation",
        workflow_file="opentrajectory_research_workflow_v4.yaml",
        origin_definition_id="opentrajectory:mechanism_explanation",
        origin_definition_version=1,
        downstream_definition_id="opentrajectory:current_research_conclusion",
        downstream_definition_version=1,
    ),
    OptionalEnhancementCase(
        case_id="pvd_evidence_applicability",
        workflow_file="pvd_process_optimization_workflow_v3.yaml",
        origin_definition_id=(
            "pvd:evidence_applicability_comparability_analysis"
        ),
        origin_definition_version=1,
        downstream_definition_id=(
            "pvd:process_factor_candidate_experiment_analysis"
        ),
        downstream_definition_version=1,
    ),
    OptionalEnhancementCase(
        case_id="pvd_experimental_feedback",
        workflow_file="pvd_process_optimization_workflow_v3.yaml",
        origin_definition_id="pvd:experimental_feedback_round_analysis",
        origin_definition_version=1,
        downstream_definition_id=(
            "pvd:process_factor_candidate_experiment_analysis"
        ),
        downstream_definition_version=1,
    ),
)


def _configure_case(
    payload: dict,
    case: OptionalEnhancementCase,
    *,
    downstream_condition_type: str = "instance_completed",
) -> None:
    design = payload["researchDesignVersions"][0]
    origin = next(
        row
        for row in design["instances"]
        if row["substageInstanceId"] == "sub_downstream"
    )
    origin.update(
        {
            "substageDefinitionId": case.origin_definition_id,
            "substageDefinitionVersion": case.origin_definition_version,
            "objective": f"Attempt optional enhancement: {case.case_id}.",
            "expectedArtifactTypes": ["optional_enhancement"],
            "sequenceHint": 20,
        }
    )
    origin["acceptanceCriteria"][0].update(
        {
            "criterionId": f"criterion:{case.case_id}",
            "description": "Produce the optional enhancement when available.",
            "requiredArtifactType": "optional_enhancement",
        }
    )

    downstream = deepcopy(origin)
    downstream.update(
        {
            "substageInstanceId": "sub_optional_consumer",
            "substageDefinitionId": case.downstream_definition_id,
            "substageDefinitionVersion": case.downstream_definition_version,
            "objective": "Produce the trustworthy core result with an explicit gap.",
            "expectedArtifactTypes": ["core_result"],
            "sequenceHint": 30,
        }
    )
    downstream["acceptanceCriteria"][0].update(
        {
            "criterionId": f"criterion:{case.case_id}:consumer",
            "description": "Produce the core result without inventing the missing enhancement.",
            "requiredArtifactType": "core_result",
        }
    )
    downstream["dependencies"] = [
        {
            "dependencyId": f"dependency:{case.case_id}:optional",
            "upstreamInstanceRef": {
                "substageInstanceId": "sub_downstream",
                "instanceRevision": 1,
            },
            "conditionType": downstream_condition_type,
            "requiredArtifactTypes": (
                ["optional_enhancement"]
                if downstream_condition_type == "artifact_usable"
                else []
            ),
            "confirmationTargetRef": None,
        }
    ]
    design["instances"].append(downstream)

    origin_runtime = next(
        row
        for row in payload["substageRuntimeStates"]
        if row["substageInstanceId"] == "sub_downstream"
    )
    origin_runtime.update(
        {
            "status": "queued",
            "activeExecutionDecisionId": "decision_optional_enhancement",
            "workUnitIds": ["work_optional_enhancement"],
            "artifactRefs": [],
            "statusReasonCode": None,
        }
    )
    payload["substageRuntimeStates"].append(
        {
            "substageInstanceId": "sub_optional_consumer",
            "instanceRevision": 1,
            "status": "planned",
            "activeExecutionDecisionId": None,
            "workUnitIds": [],
            "artifactRefs": [],
            "statusReasonCode": None,
            "updatedAt": "2026-10-05T00:01:00Z",
        }
    )

    decision = deepcopy(payload["executionDecisions"][0])
    assignment = deepcopy(decision["assignments"][0])
    assignment.update(
        {
            "assignmentId": "assignment_optional_enhancement",
            "objective": origin["objective"],
            "expectedArtifactTypes": ["optional_enhancement"],
            "acceptanceCriteria": deepcopy(origin["acceptanceCriteria"]),
            "dependencyAssignmentIds": [],
        }
    )
    decision.update(
        {
            "executionDecisionId": "decision_optional_enhancement",
            "selectionRequestId": "selection_optional_enhancement",
            "decisionSequence": 1,
            "substageInstanceId": "sub_downstream",
            "substageInstanceRevision": 1,
            "purpose": "completion",
            "assignments": [assignment],
            "completionPolicy": {
                "requiredAssignmentIds": ["assignment_optional_enhancement"],
                "successMode": "all_required",
                "reviewerAssignmentIds": [],
                "reviewRequirement": "none",
                "partialSuccessAllowed": False,
                "onReviewerReject": "new_revision_decision",
            },
            "progressPolicy": None,
            "resultAssemblyPolicy": {
                "mode": "primary_output",
                "assemblerAssignmentId": None,
                "finalArtifactRequirements": [
                    {
                        "artifactType": "optional_enhancement",
                        "minimumCount": 1,
                        "sourceAssignmentIds": ["assignment_optional_enhancement"],
                    }
                ],
            },
            "status": "dispatched",
            "outcome": None,
            "closedAt": None,
        }
    )
    payload["executionDecisions"].append(decision)

    work = deepcopy(payload["workUnits"][0])
    work.update(
        {
            "workUnitId": "work_optional_enhancement",
            "idempotencyKey": (
                "workunit:decision_optional_enhancement:"
                "assignment_optional_enhancement"
            ),
            "executionDecisionId": "decision_optional_enhancement",
            "assignmentId": "assignment_optional_enhancement",
            "substageInstanceId": "sub_downstream",
            "substageInstanceRevision": 1,
            "substageDefinitionId": case.origin_definition_id,
            "substageDefinitionVersion": case.origin_definition_version,
            "inputArtifactRefs": [],
            "inputArtifactSnapshots": [],
            "dependencyWorkUnitIds": [],
            "expectedArtifactTypes": ["optional_enhancement"],
            "acceptanceCriteria": deepcopy(origin["acceptanceCriteria"]),
            "status": "queued",
            "terminalReasonCode": None,
            "finishedAt": None,
        }
    )
    payload["workUnits"].append(work)


def _workflow_context(
    task: ResearchTaskV12,
    case: OptionalEnhancementCase,
) -> tuple[dict, dict]:
    from app.modules.repo_paths import repo_root

    path = repo_root() / "schemas" / "workflows" / case.workflow_file
    definition = yaml.safe_load(path.read_text(encoding="utf-8"))
    authority = {
        "workflowId": definition["workflowId"],
        "workflowVersion": definition["workflowVersion"],
        "workflowContentHash": "sha256:" + "f" * 64,
        "skillPack": definition["skillPack"],
        "researchDesignId": task.active_research_design_ref.research_design_id,
        "researchDesignVersion": task.active_research_design_ref.version,
        "planningAttemptId": "planning-r0b-test",
    }
    raw_payload = {
        "skillPack": definition["skillPack"],
        "workflowAdmission": {
            "workflowId": definition["workflowId"],
            "workflowVersion": definition["workflowVersion"],
            "workflowContentHash": authority["workflowContentHash"],
            "skillPack": definition["skillPack"],
            "routeType": "workflow_planning",
            "workflowDefinitionSnapshot": {"definition": definition},
        },
    }
    return raw_payload, authority


def _fixture(
    case: OptionalEnhancementCase,
    *,
    downstream_condition_type: str = "instance_completed",
):
    from app.modules import research_orchestration_repository as repository

    task = _task_ready_for_continuation(next_stage_key="insight_generation")
    payload = task.model_dump(mode="json", by_alias=True)
    _configure_case(
        payload,
        case,
        downstream_condition_type=downstream_condition_type,
    )
    task = ResearchTaskV12.model_validate(payload)
    raw_payload, authority = _workflow_context(task, case)
    fence = repository.DispatchFence(
        dispatch_id="dispatch_optional_enhancement",
        task_id=task.task_id,
        execution_decision_id="decision_optional_enhancement",
        work_unit_id="work_optional_enhancement",
        idempotency_key=(
            "workunit:decision_optional_enhancement:"
            "assignment_optional_enhancement"
        ),
        lease_owner="test",
        attempt_count=1,
    )
    return task, fence, raw_payload, authority


def _repository_harness(monkeypatch):
    from app.modules import research_orchestration_repository as repository

    monkeypatch.setattr(
        repository.ResearchOrchestrationRepository,
        "load_registry_snapshot",
        lambda _self, _content_hash: None,
    )
    return object.__new__(repository.ResearchOrchestrationRepository)

@pytest.mark.parametrize("case", CASES, ids=lambda case: case.case_id)
def test_known_optional_enhancement_failure_releases_trustworthy_consumer(
    monkeypatch,
    case: OptionalEnhancementCase,
) -> None:
    from app.modules import research_orchestration_repository as repository

    task, fence, raw_payload, authority = _fixture(case)
    original_artifacts = task.artifacts
    monkeypatch.setattr(
        repository,
        "validate_canonical_execution_lineage",
        lambda **_kwargs: SimpleNamespace(as_lineage_facts=lambda: authority),
    )

    updated = _repository_harness(monkeypatch)._task_with_outcome(
        task,
        fence,
        "known_rejected",
        "opaque_known_terminal_failure",
        datetime.fromisoformat("2026-10-05T00:02:00+00:00"),
        external_action_started=False,
        raw_payload=raw_payload,
    )

    origin = next(
        row
        for row in updated.substage_runtime_states
        if row.substage_instance_id == "sub_downstream"
    )
    consumer = next(
        row
        for row in updated.substage_runtime_states
        if row.substage_instance_id == "sub_optional_consumer"
    )
    decision = next(
        row
        for row in updated.execution_decisions
        if row.execution_decision_id == "decision_optional_enhancement"
    )
    assert decision.status is ExecutionDecisionStatus.CLOSED
    assert decision.outcome is ExecutionDecisionOutcome.BLOCKED
    assert origin.status is SubstageRuntimeStatus.SKIPPED
    assert origin.status_reason_code == "optional_enhancement_unavailable"
    assert consumer.status is SubstageRuntimeStatus.READY
    assert updated.status is TaskStatus.RUNNING
    assert updated.artifacts == original_artifacts

    plan = plan_execution_continuation(
        updated,
        completed_execution_decision_id="decision_optional_enhancement",
        execution_mode=TaskExecutionMode.AUTOMATIC_RESEARCH,
    )
    assert plan.action == "start"
    assert plan.next_instance_id is None


def test_scientific_dependency_failure_is_not_degraded(monkeypatch) -> None:
    from app.modules import research_orchestration_repository as repository

    case = CASES[0]
    task, fence, raw_payload, authority = _fixture(case)
    dependency = next(
        row
        for row in raw_payload["workflowAdmission"]["workflowDefinitionSnapshot"][
            "definition"
        ]["dependencies"]
        if row["upstreamNodeKey"] == "deviation_diagnosis_validation"
        and row["downstreamNodeKey"] == "reproduction_conclusion_recommendation"
    )
    dependency["dependencyType"] = "scientific_dependency"
    monkeypatch.setattr(
        repository,
        "validate_canonical_execution_lineage",
        lambda **_kwargs: SimpleNamespace(as_lineage_facts=lambda: authority),
    )

    updated = _repository_harness(monkeypatch)._task_with_outcome(
        task,
        fence,
        "known_rejected",
        "opaque_known_terminal_failure",
        datetime.fromisoformat("2026-10-05T00:02:00+00:00"),
        external_action_started=False,
        raw_payload=raw_payload,
    )

    origin = next(
        row
        for row in updated.substage_runtime_states
        if row.substage_instance_id == "sub_downstream"
    )
    consumer = next(
        row
        for row in updated.substage_runtime_states
        if row.substage_instance_id == "sub_optional_consumer"
    )
    assert origin.status is SubstageRuntimeStatus.BLOCKED_RECOVERABLE
    assert consumer.status is SubstageRuntimeStatus.PLANNED
    assert plan_execution_continuation(
        updated,
        completed_execution_decision_id="decision_optional_enhancement",
        execution_mode=TaskExecutionMode.AUTOMATIC_RESEARCH,
    ).action == "none"


def test_artifact_dependency_is_not_released_by_optional_skip(monkeypatch) -> None:
    from app.modules import research_orchestration_repository as repository

    case = CASES[1]
    task, fence, raw_payload, authority = _fixture(
        case,
        downstream_condition_type="artifact_usable",
    )
    monkeypatch.setattr(
        repository,
        "validate_canonical_execution_lineage",
        lambda **_kwargs: SimpleNamespace(as_lineage_facts=lambda: authority),
    )

    updated = _repository_harness(monkeypatch)._task_with_outcome(
        task,
        fence,
        "known_rejected",
        "opaque_known_terminal_failure",
        datetime.fromisoformat("2026-10-05T00:02:00+00:00"),
        external_action_started=False,
        raw_payload=raw_payload,
    )

    origin = next(
        row
        for row in updated.substage_runtime_states
        if row.substage_instance_id == "sub_downstream"
    )
    consumer = next(
        row
        for row in updated.substage_runtime_states
        if row.substage_instance_id == "sub_optional_consumer"
    )
    assert origin.status is SubstageRuntimeStatus.BLOCKED_RECOVERABLE
    assert consumer.status is SubstageRuntimeStatus.PLANNED


def test_external_result_unknown_is_not_projected_as_optional_skip(monkeypatch) -> None:
    from app.modules import research_orchestration_repository as repository

    case = CASES[2]
    task, fence, raw_payload, authority = _fixture(case)
    monkeypatch.setattr(
        repository,
        "validate_canonical_execution_lineage",
        lambda **_kwargs: SimpleNamespace(as_lineage_facts=lambda: authority),
    )

    updated = _repository_harness(monkeypatch)._task_with_outcome(
        task,
        fence,
        "unknown",
        "opaque_unknown_external_result",
        datetime.fromisoformat("2026-10-05T00:02:00+00:00"),
        external_action_started=False,
        raw_payload=raw_payload,
    )

    origin = next(
        row
        for row in updated.substage_runtime_states
        if row.substage_instance_id == "sub_downstream"
    )
    assert origin.status is not SubstageRuntimeStatus.SKIPPED
    assert origin.status_reason_code != "optional_enhancement_unavailable"
