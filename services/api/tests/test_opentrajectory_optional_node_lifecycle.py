"""OpenTrajectory optional-node failure isolation and continuation tests."""

from __future__ import annotations
from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace
import pytest
import yaml
from app.models.research_orchestration import (
    ExecutionDecisionOutcome,
    ExecutionDecisionStatus,
    ResearchTaskV12,
    TaskExecutionMode,
    TaskStatus,
    SubstageRuntimeStatus,
)
from app.modules.research_execution_decision_service import (
    plan_execution_continuation,
    reconcile_task_lifecycle_from_runtime,
)
from tests.support.research_execution_decision import (
    build_ready_continuation_task as _task_ready_for_continuation,
)


def _configure_ot_instances(payload: dict) -> dict:
    design = payload["researchDesignVersions"][0]
    evidence = next(
        row for row in design["instances"]
        if row["substageInstanceId"] == "sub_contract_full"
    )
    mechanism = next(
        row for row in design["instances"]
        if row["substageInstanceId"] == "sub_downstream"
    )
    evidence.update(
        {
            "substageDefinitionId": "opentrajectory:evidence_extraction",
            "sequenceHint": 80,
        }
    )
    mechanism.update(
        {
            "substageDefinitionId": "opentrajectory:mechanism_explanation",
            "objective": "Optionally synthesize mechanism context from ResearchRecords.",
            "expectedArtifactTypes": ["mechanism_packet"],
            "sequenceHint": 90,
        }
    )
    mechanism["acceptanceCriteria"][0].update(
        {
            "criterionId": "criterion_ot_mechanism",
            "description": "Produce the optional mechanism packet when available.",
            "requiredArtifactType": "mechanism_packet",
        }
    )
    mechanism["dependencies"] = [
        {
            "dependencyId": "dependency_ot_evidence_to_mechanism",
            "upstreamInstanceRef": {
                "substageInstanceId": "sub_contract_full",
                "instanceRevision": 1,
            },
            "conditionType": "instance_completed",
            "requiredArtifactTypes": [],
            "confirmationTargetRef": None,
        }
    ]

    final = deepcopy(mechanism)
    final.update(
        {
            "substageInstanceId": "sub_ot_final",
            "substageDefinitionId": "opentrajectory:findings_synthesis",
            "objective": "Produce the single final evidence synthesis from ResearchRecords.",
            "expectedArtifactTypes": ["final_synthesis"],
            "sequenceHint": 100,
        }
    )
    final["acceptanceCriteria"][0].update(
        {
            "criterionId": "criterion_ot_final",
            "description": "Produce the final synthesis.",
            "requiredArtifactType": "final_synthesis",
        }
    )
    final["dependencies"] = [
        {
            "dependencyId": "dependency_ot_evidence_to_final",
            "upstreamInstanceRef": {
                "substageInstanceId": "sub_contract_full",
                "instanceRevision": 1,
            },
            "conditionType": "instance_completed",
            "requiredArtifactTypes": [],
            "confirmationTargetRef": None,
        }
    ]
    design["instances"].append(final)
    return mechanism


def _configure_ot_runtime(payload: dict) -> None:
    mechanism_runtime = next(
        row for row in payload["substageRuntimeStates"]
        if row["substageInstanceId"] == "sub_downstream"
    )
    mechanism_runtime.update(
        {
            "status": "queued",
            "activeExecutionDecisionId": "decision_ot_node9",
            "workUnitIds": ["work_ot_node9"],
            "artifactRefs": [],
            "statusReasonCode": None,
        }
    )
    payload["substageRuntimeStates"].append(
        {
            "substageInstanceId": "sub_ot_final",
            "instanceRevision": 1,
            "status": "planned",
            "activeExecutionDecisionId": None,
            "workUnitIds": [],
            "artifactRefs": [],
            "statusReasonCode": None,
            "updatedAt": "2026-07-28T00:01:00Z",
        }
    )


def _append_ot_node9_decision(payload: dict, mechanism: dict) -> None:
    decision = deepcopy(payload["executionDecisions"][0])
    assignment = deepcopy(decision["assignments"][0])
    assignment.update(
        {
            "assignmentId": "assignment_ot_node9",
            "objective": "Optionally synthesize mechanism context.",
            "expectedArtifactTypes": ["mechanism_packet"],
            "acceptanceCriteria": deepcopy(mechanism["acceptanceCriteria"]),
            "dependencyAssignmentIds": [],
        }
    )
    decision.update(
        {
            "executionDecisionId": "decision_ot_node9",
            "selectionRequestId": "selection_ot_node9",
            "decisionSequence": 1,
            "substageInstanceId": "sub_downstream",
            "substageInstanceRevision": 1,
            "purpose": "completion",
            "assignments": [assignment],
            "completionPolicy": {
                "requiredAssignmentIds": ["assignment_ot_node9"],
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
                        "artifactType": "mechanism_packet",
                        "minimumCount": 1,
                        "sourceAssignmentIds": ["assignment_ot_node9"],
                    }
                ],
            },
            "status": "dispatched",
            "outcome": None,
            "closedAt": None,
        }
    )
    payload["executionDecisions"].append(decision)


def _append_ot_node9_work(payload: dict, mechanism: dict) -> None:
    work = deepcopy(payload["workUnits"][0])
    work.update(
        {
            "workUnitId": "work_ot_node9",
            "idempotencyKey": "workunit:decision_ot_node9:assignment_ot_node9",
            "executionDecisionId": "decision_ot_node9",
            "assignmentId": "assignment_ot_node9",
            "substageInstanceId": "sub_downstream",
            "substageInstanceRevision": 1,
            "substageDefinitionId": "opentrajectory:mechanism_explanation",
            "substageDefinitionVersion": 1,
            "inputArtifactRefs": [],
            "inputArtifactSnapshots": [],
            "dependencyWorkUnitIds": [],
            "expectedArtifactTypes": ["mechanism_packet"],
            "acceptanceCriteria": deepcopy(mechanism["acceptanceCriteria"]),
            "status": "queued",
            "terminalReasonCode": None,
            "finishedAt": None,
        }
    )
    payload["workUnits"].append(work)

    evidence_work = next(
        row for row in payload["workUnits"]
        if row["substageInstanceId"] == "sub_contract_full"
        and row["workUnitId"] != "work_ot_node9"
    )
    evidence_work.update(
        {
            "substageDefinitionId": "opentrajectory:evidence_extraction",
            "substageDefinitionVersion": 1,
        }
    )
    evidence_artifacts = [
        row for row in payload["artifacts"]
        if row["workUnitId"] == evidence_work["workUnitId"]
    ]
    if len(evidence_artifacts) != 1:
        raise AssertionError("fixture requires exactly one Node8 ResearchRecords artifact")
    evidence_artifacts[0]["artifactType"] = "research_records"


def _ot_workflow_context(task: ResearchTaskV12) -> tuple[dict, dict]:
    from app.modules.repo_paths import repo_root

    definition_path = repo_root() / "schemas" / "workflows" / "opentrajectory_research_workflow.yaml"
    definition = yaml.safe_load(definition_path.read_text(encoding="utf-8"))
    authority = {
        "workflowId": definition["workflowId"],
        "workflowVersion": definition["workflowVersion"],
        "workflowContentHash": "sha256:" + "f" * 64,
        "skillPack": definition["skillPack"],
        "researchDesignId": task.active_research_design_ref.research_design_id,
        "researchDesignVersion": task.active_research_design_ref.version,
        "planningAttemptId": "planning-test",
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


def _ot_node9_nonblocking_fixture():
    from app.modules import research_orchestration_repository as orchestration_repository

    task = _task_ready_for_continuation(next_stage_key="execution_validation")
    payload = task.model_dump(mode="json", by_alias=True)
    mechanism = _configure_ot_instances(payload)
    _configure_ot_runtime(payload)
    _append_ot_node9_decision(payload, mechanism)
    _append_ot_node9_work(payload, mechanism)
    task = ResearchTaskV12.model_validate(payload)
    raw_payload, authority = _ot_workflow_context(task)
    fence = orchestration_repository.DispatchFence(
        dispatch_id="dispatch_ot_node9",
        task_id=task.task_id,
        execution_decision_id="decision_ot_node9",
        work_unit_id="work_ot_node9",
        idempotency_key="workunit:decision_ot_node9:assignment_ot_node9",
        lease_owner="test",
        attempt_count=1,
    )
    return task, fence, raw_payload, authority


def test_ot_required_node_failure_remains_local_and_keeps_task_running() -> None:
    task, _fence, _raw_payload, _authority = _ot_node9_nonblocking_fixture()
    payload = task.model_dump(mode="json", by_alias=True)
    evidence_runtime = next(
        row for row in payload["substageRuntimeStates"]
        if row["substageInstanceId"] == "sub_contract_full"
    )
    evidence_runtime.update(
        {
            "status": "failed",
            "activeExecutionDecisionId": None,
            "statusReasonCode": "required_evidence_failure",
        }
    )
    reconciled = reconcile_task_lifecycle_from_runtime(
        ResearchTaskV12.model_validate(payload),
        occurred_at=datetime.fromisoformat("2026-07-28T00:02:00+00:00"),
    )
    assert reconciled.status is TaskStatus.RUNNING
    assert reconciled.finished_at is None
