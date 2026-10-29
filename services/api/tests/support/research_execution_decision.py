"""Focused builders shared by execution-decision policy and lifecycle tests."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any

from app.models.research_orchestration import ResearchTaskV12
from app.modules import research_execution_decision_service as decision_service
from app.modules.research_execution_decision_service import PreparedSelection
from app.modules.research_orchestration_resolver import ResolutionResult

from tests.support.research_lifecycle import load_structural_research_task_payload


def freeze_prepared_selection_for_test(
    prepared: PreparedSelection,
    task: ResearchTaskV12,
    selected_candidate_identity: str,
):
    """Freeze one prepared selection through the production execution path."""

    preparation = decision_service.prepare_selection_execution(
        prepared,
        task,
        selected_candidate_identity,
    )
    return decision_service.freeze_selection_execution(
        prepared,
        task,
        preparation,
    )


def structural_execution_decision_payload() -> dict:
    """Return a fresh structural execution-decision payload."""

    return deepcopy(load_structural_research_task_payload()["executionDecisions"][0])


def resolution_for_execution_decision(decision: dict) -> ResolutionResult:
    """Build the minimal resolver result matching a structural decision."""

    return ResolutionResult(
        designRef={"researchDesignId": "rd_contract_full", "version": 1},
        instanceRef={"substageInstanceId": "sub_contract_full", "instanceRevision": 1},
        registrySnapshotRef=decision["registrySnapshotRef"],
        candidateEvaluations=decision["candidateEvaluations"],
        preferredCandidateEvaluationRefs=[],
    )


def append_downstream_instance(
    payload: dict[str, Any],
    *,
    upstream_revision: int = 1,
    condition_type: str = "instance_completed",
) -> None:
    """Append the minimal downstream instance used by continuation tests."""

    design = payload["researchDesignVersions"][-1]
    upstream = next(
        row
        for row in design["instances"]
        if row["substageInstanceId"] == "sub_contract_full"
        and row["instanceRevision"] == upstream_revision
    )
    downstream = deepcopy(upstream)
    downstream.update(
        {
            "substageInstanceId": "sub_downstream",
            "substageDefinitionId": "sdr:downstream",
            "objective": "Use the current upstream result.",
            "inputRefs": [],
            "expectedArtifactTypes": ["downstream_summary"],
            "sequenceHint": 1,
        }
    )
    downstream["acceptanceCriteria"][0].update(
        {
            "criterionId": "criterion_downstream",
            "description": "The downstream output is complete.",
            "requiredArtifactType": "downstream_summary",
        }
    )
    downstream["dependencies"] = [
        {
            "dependencyId": "dependency_upstream",
            "upstreamInstanceRef": {
                "substageInstanceId": "sub_contract_full",
                "instanceRevision": upstream_revision,
            },
            "conditionType": condition_type,
            "requiredArtifactTypes": (
                ["evidence_summary"] if condition_type == "artifact_usable" else []
            ),
            "confirmationTargetRef": None,
        }
    ]
    design["instances"].append(downstream)
    payload["substageRuntimeStates"].append(
        {
            "substageInstanceId": "sub_downstream",
            "instanceRevision": upstream_revision,
            "status": "planned",
            "activeExecutionDecisionId": None,
            "workUnitIds": [],
            "artifactRefs": [],
            "statusReasonCode": None,
            "updatedAt": "2026-07-28T00:00:03Z",
        }
    )


def build_planned_continuation_task(*, next_stage_key: str) -> ResearchTaskV12:
    """Build the post-execution structural state used by continuation policy tests."""

    payload = load_structural_research_task_payload()
    append_downstream_instance(
        payload,
        upstream_revision=1,
        condition_type="artifact_usable",
    )
    downstream = next(
        row
        for row in payload["researchDesignVersions"][0]["instances"]
        if row["substageInstanceId"] == "sub_downstream"
    )
    downstream["stageKey"] = next_stage_key
    payload["executionDecisions"][0].update(
        {
            "status": "closed",
            "outcome": "succeeded",
            "closedAt": "2026-07-28T00:01:00Z",
        }
    )
    payload["workUnits"][0].update(
        {
            "status": "completed",
            "finishedAt": "2026-07-28T00:00:59Z",
        }
    )
    payload["artifacts"][0]["artifactState"] = "usable"
    payload["substageRuntimeStates"][0].update(
        {
            "status": "completed",
            "activeExecutionDecisionId": None,
            "updatedAt": "2026-07-28T00:01:00Z",
        }
    )
    return ResearchTaskV12.model_validate(payload)


def build_ready_continuation_task(*, next_stage_key: str) -> ResearchTaskV12:
    """Advance the planned continuation fixture through production reconciliation."""

    return decision_service.reconcile_task_lifecycle_from_runtime(
        build_planned_continuation_task(next_stage_key=next_stage_key),
        occurred_at=datetime.fromisoformat("2026-07-28T00:01:00+00:00"),
    )
