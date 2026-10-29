"""Focused execution continuation, reconciliation, and semantic-gap lifecycle tests."""

from __future__ import annotations
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from app.models.research_orchestration import (
    ExecutionDecisionOutcome,
    ExecutionDecisionStatus,
    ResearchTaskV12,
    ResearchDesignStatus,
    ReviewerRejectAction,
    TaskExecutionMode,
    TaskStatus,
    SubstageRuntimeStatus,
)
from app.modules.research_execution_decision_service import (
    close_and_reconcile_decision_from_durable_facts,
    materialize_execution_continuation_pause,
    plan_execution_continuation,
    promote_dependency_ready_substages,
    reconcile_task_lifecycle_from_runtime,
)
from app.modules import research_execution_decision_service as decision_service
from tests.support.research_execution_decision import (
    append_downstream_instance as _append_downstream_instance,
    build_planned_continuation_task as _task_planned_for_continuation,
    build_ready_continuation_task as _task_ready_for_continuation,
)

FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "packages"
    / "shared"
    / "test-fixtures"
    / "research-orchestration-v1.2-valid.json"
)

def _fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _task_after_reviewer_rejection_for_continuation(
    *,
    on_reviewer_reject: str,
):
    reviewer_assignment_id = "assignment_contract_full_reviewer"
    return SimpleNamespace(
        active_research_design_ref=SimpleNamespace(research_design_id="design", version=1),
        status=TaskStatus.RUNNING,
        execution_decisions=[
            SimpleNamespace(
                execution_decision_id="decision_contract_full",
                status=ExecutionDecisionStatus.CLOSED,
                purpose=decision_service.ExecutionPurpose.COMPLETION,
                outcome=ExecutionDecisionOutcome.BLOCKED,
                substage_instance_id="sub_contract_full",
                substage_instance_revision=1,
                completion_policy=SimpleNamespace(
                    reviewer_assignment_ids=[reviewer_assignment_id],
                    on_reviewer_reject=ReviewerRejectAction(on_reviewer_reject),
                ),
            )
        ],
        substage_runtime_states=[
            SimpleNamespace(
                substage_instance_id="sub_contract_full",
                instance_revision=1,
                status=SubstageRuntimeStatus.READY,
            )
        ],
        agent_interaction_records=[
            SimpleNamespace(
                execution_decision_id="decision_contract_full",
                source_assignment_id=reviewer_assignment_id,
                verdict=SimpleNamespace(
                    decision=SimpleNamespace(value="insufficient_evidence"),
                    rationale_code="review_insufficient_materials",
                ),
            )
        ],
    )


@pytest.mark.parametrize(
    ("mode", "next_stage_key", "expected_action", "expected_reason"),
    [
        (TaskExecutionMode.AUTOMATIC_RESEARCH, "execution_validation", "start", None),
        (TaskExecutionMode.STAGE_CONFIRMATION, "solution_design", "start", None),
        (
            TaskExecutionMode.STAGE_CONFIRMATION,
            "execution_validation",
            "pause",
            "stage_confirmation_required",
        ),
        (
            TaskExecutionMode.MANUAL_STEP,
            "solution_design",
            "pause",
            "manual_step_confirmation_required",
        ),
    ],
)
def test_execution_mode_controls_next_safe_boundary(
    mode: TaskExecutionMode,
    next_stage_key: str,
    expected_action: str,
    expected_reason: str | None,
) -> None:
    task = _task_ready_for_continuation(next_stage_key=next_stage_key)

    plan = plan_execution_continuation(
        task,
        completed_execution_decision_id="decision_contract_full",
        execution_mode=mode,
    )

    assert plan.action == expected_action
    assert plan.next_instance_id is None
    assert plan.reason_code == expected_reason


@pytest.mark.parametrize(
    ("mode", "next_stage_key", "expected_action", "expected_reason"),
    [
        (TaskExecutionMode.AUTOMATIC_RESEARCH, "execution_validation", "start", None),
        (TaskExecutionMode.STAGE_CONFIRMATION, "solution_design", "start", None),
        (TaskExecutionMode.STAGE_CONFIRMATION, "execution_validation", "pause", "stage_confirmation_required"),
        (TaskExecutionMode.MANUAL_STEP, "solution_design", "pause", "manual_step_confirmation_required"),
    ],
)
def test_completed_upstream_promotes_planned_downstream_before_continuation_planning(
    mode: TaskExecutionMode,
    next_stage_key: str,
    expected_action: str,
    expected_reason: str | None,
) -> None:
    task = _task_planned_for_continuation(next_stage_key=next_stage_key)
    downstream_before = next(
        row for row in task.substage_runtime_states
        if row.substage_instance_id == "sub_downstream"
    )
    assert downstream_before.status.value == "planned"

    reconciled = reconcile_task_lifecycle_from_runtime(
        task,
        occurred_at=datetime.fromisoformat("2026-07-28T00:01:00+00:00"),
    )
    downstream_after = next(
        row for row in reconciled.substage_runtime_states
        if row.substage_instance_id == "sub_downstream"
    )
    assert downstream_after.status.value == "ready"

    plan = plan_execution_continuation(
        reconciled,
        completed_execution_decision_id="decision_contract_full",
        execution_mode=mode,
    )
    assert plan.action == expected_action
    assert plan.next_instance_id is None
    assert plan.reason_code == expected_reason


def _cs_readiness_continuation_task(
    status: str | None,
    *,
    include_scientific_state: bool = True,
    relation: str | None = "verified",
) -> ResearchTaskV12:
    task = _task_planned_for_continuation(next_stage_key="execution_validation")
    payload = task.model_dump(mode="json", by_alias=True)
    design = payload["researchDesignVersions"][0]
    downstream = next(
        row for row in design["instances"]
        if row["substageInstanceId"] == "sub_downstream"
    )
    downstream["substageDefinitionId"] = (
        "cs_reproduction:real_execution_result_acquisition"
    )
    if include_scientific_state:
        payload["scientificStates"].append(
            {
                "scientificStateVersion": 1,
                "researchDesignVersion": design["version"],
                "scientificFacts": {
                    **(
                        {"execution_readiness_status": status}
                        if status is not None
                        else {}
                    ),
                    **(
                        {"execution_plan_resource_relation_status": relation}
                        if relation is not None
                        else {}
                    ),
                    "execution_readiness_blocking_gaps": [
                        {"code": "repository_revision_unresolved"}
                    ],
                    "execution_readiness_unknowns": ["GPU runtime availability"],
                },
                "executionState": {},
                "sourceRefs": ["artifact:execution-readiness"],
                "createdAt": "2026-09-25T00:00:59Z",
            }
        )
    return ResearchTaskV12.model_validate(payload)


def test_cs_not_ready_remains_scientific_and_releases_preparation() -> None:
    reconciled = reconcile_task_lifecycle_from_runtime(
        _cs_readiness_continuation_task("not_ready"),
        occurred_at=datetime.fromisoformat("2026-09-25T00:01:00+00:00"),
    )
    downstream = next(
        row for row in reconciled.substage_runtime_states
        if row.substage_instance_id == "sub_downstream"
    )

    assert downstream.status is SubstageRuntimeStatus.READY
    assert downstream.status_reason_code is None
    assert reconciled.scientific_states[-1].scientific_facts[
        "execution_readiness_blocking_gaps"
    ] == [{"code": "repository_revision_unresolved"}]
    assert plan_execution_continuation(
        reconciled,
        completed_execution_decision_id="decision_contract_full",
        execution_mode=TaskExecutionMode.AUTOMATIC_RESEARCH,
    ).action == "start"


def test_cs_ready_releases_preparation_normally() -> None:
    reconciled = reconcile_task_lifecycle_from_runtime(
        _cs_readiness_continuation_task("ready"),
        occurred_at=datetime.fromisoformat("2026-09-25T00:01:00+00:00"),
    )
    downstream = next(
        row for row in reconciled.substage_runtime_states
        if row.substage_instance_id == "sub_downstream"
    )

    assert downstream.status is SubstageRuntimeStatus.READY
    assert downstream.status_reason_code is None
    assert plan_execution_continuation(
        reconciled,
        completed_execution_decision_id="decision_contract_full",
        execution_mode=TaskExecutionMode.AUTOMATIC_RESEARCH,
    ).action == "start"


def test_cs_preparation_does_not_require_scientific_relation() -> None:
    reconciled = reconcile_task_lifecycle_from_runtime(
        _cs_readiness_continuation_task("ready", relation=None),
        occurred_at=datetime.fromisoformat("2026-09-25T00:01:00+00:00"),
    )
    downstream = next(
        row for row in reconciled.substage_runtime_states
        if row.substage_instance_id == "sub_downstream"
    )
    assert downstream.status is SubstageRuntimeStatus.READY
    assert downstream.status_reason_code is None


def test_cs_preparation_does_not_require_readiness_fact() -> None:
    reconciled = reconcile_task_lifecycle_from_runtime(
        _cs_readiness_continuation_task(None),
        occurred_at=datetime.fromisoformat("2026-09-25T00:01:00+00:00"),
    )
    downstream = next(
        row for row in reconciled.substage_runtime_states
        if row.substage_instance_id == "sub_downstream"
    )

    assert downstream.status is SubstageRuntimeStatus.READY
    assert downstream.status_reason_code is None


def test_cs_dependency_promotion_does_not_require_scientific_state() -> None:
    reconciled = reconcile_task_lifecycle_from_runtime(
        _cs_readiness_continuation_task(None, include_scientific_state=False),
        occurred_at=datetime.fromisoformat("2026-09-25T00:01:00+00:00"),
    )
    downstream = next(
        row for row in reconciled.substage_runtime_states
        if row.substage_instance_id == "sub_downstream"
    )

    assert downstream.status is SubstageRuntimeStatus.READY
    assert downstream.status_reason_code is None


def test_cs_preparation_promotion_with_gap_is_idempotent() -> None:
    first = reconcile_task_lifecycle_from_runtime(
        _cs_readiness_continuation_task("not_ready"),
        occurred_at=datetime.fromisoformat("2026-09-25T00:01:00+00:00"),
    )
    second = reconcile_task_lifecycle_from_runtime(
        first,
        occurred_at=datetime.fromisoformat("2026-09-25T00:02:00+00:00"),
    )

    assert second == first


@pytest.mark.parametrize(
    ("mode", "next_stage_key", "expected_action", "expected_reason"),
    [
        (TaskExecutionMode.AUTOMATIC_RESEARCH, "solution_design", "start", None),
        (TaskExecutionMode.STAGE_CONFIRMATION, "solution_design", "start", None),
        (
            TaskExecutionMode.STAGE_CONFIRMATION,
            "execution_validation",
            "pause",
            "stage_confirmation_required",
        ),
        (
            TaskExecutionMode.MANUAL_STEP,
            "solution_design",
            "pause",
            "manual_step_confirmation_required",
        ),
    ],
)
def test_progressed_completed_origin_continues_other_ready_research(
    mode: TaskExecutionMode,
    next_stage_key: str,
    expected_action: str,
    expected_reason: str | None,
) -> None:
    task = _task_ready_for_continuation(next_stage_key=next_stage_key)
    payload = task.model_dump(mode="json", by_alias=True)
    payload["executionDecisions"][0]["outcome"] = "progressed"
    origin = next(
        row
        for row in payload["substageRuntimeStates"]
        if row["substageInstanceId"] == "sub_contract_full"
    )
    assert origin["status"] == "completed"
    assert any(
        row["substageInstanceId"] != "sub_contract_full"
        and row["status"] == "ready"
        for row in payload["substageRuntimeStates"]
    )

    plan = plan_execution_continuation(
        ResearchTaskV12.model_validate(payload),
        completed_execution_decision_id="decision_contract_full",
        execution_mode=mode,
    )

    assert plan.action == expected_action
    assert plan.next_instance_id is None
    assert plan.reason_code == expected_reason


@pytest.mark.parametrize("outcome", ["partial", "progressed"])
def test_partial_origin_gap_does_not_stop_other_ready_automatic_work(outcome) -> None:
    task = _task_ready_for_continuation(next_stage_key="solution_design")
    payload = task.model_dump(mode="json", by_alias=True)
    payload["executionDecisions"][0]["outcome"] = outcome
    origin = next(row for row in payload["substageRuntimeStates"] if row["substageInstanceId"] == "sub_contract_full")
    origin.update(status="blocked_recoverable", statusReasonCode="progress_capability_exhausted_without_completion_contract")
    task = ResearchTaskV12.model_validate(payload)
    before = task.model_dump(mode="json", by_alias=True)
    plan = plan_execution_continuation(task, completed_execution_decision_id="decision_contract_full", execution_mode=TaskExecutionMode.AUTOMATIC_RESEARCH)
    assert plan.action == "start"
    assert plan.next_instance_id is None
    assert task.model_dump(mode="json", by_alias=True) == before
    assert plan_execution_continuation(task, completed_execution_decision_id="decision_contract_full", execution_mode=TaskExecutionMode.MANUAL_STEP).action == "none"


def test_progressed_completed_origin_without_ready_work_returns_none() -> None:
    task = _task_ready_for_continuation(next_stage_key="solution_design")
    payload = task.model_dump(mode="json", by_alias=True)
    payload["executionDecisions"][0]["outcome"] = "progressed"
    for runtime in payload["substageRuntimeStates"]:
        runtime["status"] = "completed"
        runtime["activeExecutionDecisionId"] = None

    plan = plan_execution_continuation(
        ResearchTaskV12.model_validate(payload),
        completed_execution_decision_id="decision_contract_full",
        execution_mode=TaskExecutionMode.AUTOMATIC_RESEARCH,
    )

    assert plan.action == "none"


def test_progressed_completed_origin_does_not_release_planned_dependency() -> None:
    task = _task_planned_for_continuation(next_stage_key="solution_design")
    payload = task.model_dump(mode="json", by_alias=True)
    payload["executionDecisions"][0]["outcome"] = "progressed"

    plan = plan_execution_continuation(
        ResearchTaskV12.model_validate(payload),
        completed_execution_decision_id="decision_contract_full",
        execution_mode=TaskExecutionMode.AUTOMATIC_RESEARCH,
    )

    assert plan.action == "none"


def test_progressed_completed_origin_does_not_continue_cancelled_task() -> None:
    task = _task_ready_for_continuation(next_stage_key="solution_design")
    payload = task.model_dump(mode="json", by_alias=True)
    payload["executionDecisions"][0]["outcome"] = "progressed"
    payload["status"] = "cancelled"
    payload["finishedAt"] = "2026-07-28T00:02:00Z"

    plan = plan_execution_continuation(
        ResearchTaskV12.model_validate(payload),
        completed_execution_decision_id="decision_contract_full",
        execution_mode=TaskExecutionMode.AUTOMATIC_RESEARCH,
    )

    assert plan.action == "none"


def test_failed_decision_reenters_semantic_next_task_selection_when_other_work_is_ready() -> None:
    task = _task_ready_for_continuation(next_stage_key="solution_design")
    payload = task.model_dump(mode="json", by_alias=True)
    payload["executionDecisions"][0]["outcome"] = "failed"
    origin = next(
        row
        for row in payload["substageRuntimeStates"]
        if row["substageInstanceId"] == "sub_contract_full"
    )
    origin["status"] = "failed"
    origin["activeExecutionDecisionId"] = None
    origin["statusReasonCode"] = "execution_failed"

    plan = plan_execution_continuation(
        ResearchTaskV12.model_validate(payload),
        completed_execution_decision_id="decision_contract_full",
        execution_mode=TaskExecutionMode.AUTOMATIC_RESEARCH,
    )

    assert plan.action == "start"
    # The continuation planner does not preselect a replacement research task.
    # The dispatch layer routes this to start_next(), whose authority is the
    # confirmed ResearchDesign + current ScientificState.
    assert plan.next_instance_id is None


def test_reviewer_reject_policy_restarts_only_the_same_research_instance() -> None:
    task = _task_after_reviewer_rejection_for_continuation(
        on_reviewer_reject="new_revision_decision"
    )

    plan = plan_execution_continuation(
        task,
        completed_execution_decision_id="decision_contract_full",
        execution_mode=TaskExecutionMode.AUTOMATIC_RESEARCH,
    )

    assert plan.action == "start"
    assert plan.next_instance_id == "sub_contract_full"
    assert plan.reason_code is None


def test_reviewer_human_gate_policy_pauses_instead_of_auto_restarting() -> None:
    task = _task_after_reviewer_rejection_for_continuation(
        on_reviewer_reject="human_gate"
    )

    plan = plan_execution_continuation(
        task,
        completed_execution_decision_id="decision_contract_full",
        execution_mode=TaskExecutionMode.AUTOMATIC_RESEARCH,
    )

    assert plan.action == "pause"
    assert plan.next_instance_id == "sub_contract_full"
    assert plan.reason_code == "reviewer_human_gate_required"


def test_semantic_gap_recovery_continues_from_latest_design_not_superseded_instance() -> None:
    """A v1 blocked Decision canonically starts the v2 recovery instance."""
    task = SimpleNamespace(
        active_research_design_ref=SimpleNamespace(research_design_id="design", version=2),
        status=TaskStatus.RUNNING,
        execution_decisions=[SimpleNamespace(
            execution_decision_id="decision-v1",
            status=ExecutionDecisionStatus.CLOSED,
            outcome=ExecutionDecisionOutcome.BLOCKED,
            substage_instance_id="blocked",
            substage_instance_revision=1,
        )],
        research_design_versions=[SimpleNamespace(
            research_design_id="design",
            version=2,
            status=ResearchDesignStatus.CONFIRMED,
            instances=[
                # v2 deliberately supersedes the original v1 instance revision.
                SimpleNamespace(substage_instance_id="blocked", instance_revision=2, sequence_hint=2),
                SimpleNamespace(substage_instance_id="recovery-v2", instance_revision=1, sequence_hint=1),
            ],
        )],
        substage_runtime_states=[
            SimpleNamespace(
                substage_instance_id="blocked",
                instance_revision=1,
                status=SubstageRuntimeStatus.SKIPPED,
                status_reason_code="semantic_material_insufficient",
            ),
            SimpleNamespace(
                substage_instance_id="blocked",
                instance_revision=2,
                status=SubstageRuntimeStatus.PLANNED,
                status_reason_code=None,
            ),
            SimpleNamespace(
                substage_instance_id="recovery-v2",
                instance_revision=1,
                status=SubstageRuntimeStatus.READY,
                status_reason_code=None,
            ),
        ],
    )

    plan = plan_execution_continuation(
        task,
        completed_execution_decision_id="decision-v1",
        execution_mode=TaskExecutionMode.AUTOMATIC_RESEARCH,
    )

    assert plan.action == "start"
    assert plan.next_instance_id is None


def test_reconcile_promotes_dependency_ready_substage_after_upstream_completion() -> None:
    payload = _fixture()["researchTask"]
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
    _append_downstream_instance(payload)

    reconciled = reconcile_task_lifecycle_from_runtime(
        ResearchTaskV12.model_validate(payload),
        occurred_at=datetime.fromisoformat("2026-07-28T00:01:00+00:00"),
    )

    downstream = next(
        row for row in reconciled.substage_runtime_states
        if row.substage_instance_id == "sub_downstream"
    )
    assert downstream.status.value == "ready"
    assert reconciled.status.value == "running"


def test_stale_artifact_from_superseded_instance_revision_cannot_unlock_downstream() -> None:
    payload = _fixture()["researchTask"]
    version_one = payload["researchDesignVersions"][0]
    version_one["status"] = "superseded"
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

    version_two = deepcopy(version_one)
    version_two.update(
        {
            "version": 2,
            "supersedesVersion": 1,
            "status": "confirmed",
            "confirmedAt": "2026-07-28T00:02:00Z",
            "confirmationRef": "confirmation_design_v2",
        }
    )
    version_two["coverageReport"].update(
        {
            "coverageReportId": "coverage_v2",
            "designRef": {"researchDesignId": "rd_contract_full", "version": 2},
            "evaluatedAt": "2026-07-28T00:01:59Z",
        }
    )
    current_upstream = deepcopy(version_two["instances"][0])
    current_upstream["instanceRevision"] = 2
    current_upstream["acceptanceCriteria"][0]["criterionId"] = "criterion_contract_v2"
    version_two["instances"] = [current_upstream]
    version_two["coverageReport"]["claims"][0]["instanceRefs"] = [
        {"substageInstanceId": "sub_contract_full", "instanceRevision": 2}
    ]
    version_two["coverageReport"]["claims"][0]["acceptanceCriterionRefs"] = [
        "criterion_contract_v2"
    ]
    payload["researchDesignVersions"].append(version_two)
    payload["activeResearchDesignRef"] = {
        "researchDesignId": "rd_contract_full",
        "version": 2,
    }
    payload["confirmations"].append(
        {
            "confirmationId": "confirmation_design_v2",
            "targetType": "research_design",
            "targetId": "rd_contract_full",
            "targetVersion": 2,
            "decision": "approved",
            "actorId": "user_contract",
            "createdAt": "2026-07-28T00:02:00Z",
        }
    )
    payload["substageRuntimeStates"].append(
        {
            "substageInstanceId": "sub_contract_full",
            "instanceRevision": 2,
            "status": "completed",
            "activeExecutionDecisionId": None,
            "workUnitIds": [],
            "artifactRefs": [],
            "statusReasonCode": None,
            "updatedAt": "2026-07-28T00:02:30Z",
        }
    )
    _append_downstream_instance(
        payload,
        upstream_revision=2,
        condition_type="artifact_usable",
    )

    promoted = promote_dependency_ready_substages(
        ResearchTaskV12.model_validate(payload),
        occurred_at=datetime.fromisoformat("2026-07-28T00:03:00+00:00"),
    )

    downstream = next(
        row for row in promoted.substage_runtime_states
        if row.substage_instance_id == "sub_downstream"
    )
    assert downstream.status.value == "planned"


def test_reconcile_completes_from_verified_durable_facts_without_report_generation() -> None:
    payload = _fixture()["researchTask"]
    payload["workUnits"][0].update(
        {
            "status": "completed",
            "finishedAt": "2026-07-28T00:00:59Z",
        }
    )
    payload["artifacts"][0]["artifactState"] = "usable"

    closed = close_and_reconcile_decision_from_durable_facts(
        ResearchTaskV12.model_validate(payload),
        execution_decision_id="decision_contract_full",
        closed_at=datetime.fromisoformat("2026-07-28T00:01:00+00:00"),
    )

    assert closed.execution_decisions[0].status.value == "closed"
    assert closed.substage_runtime_states[0].status.value == "completed"
    assert closed.status.value == "completed"
    assert closed.finished_at is not None
    assert closed.final_result_authority is not None
    assert closed.final_result_authority.mode == "artifact_set"
    assert closed.final_result_authority.result_artifact_refs == ["artifact_contract_full"]
    assert closed.final_results[-1].validation_outcome is not None
    assert closed.final_results[-1].validation_outcome.status == "passed"


def test_reconcile_completes_with_persisted_canonical_report_result() -> None:
    payload = _fixture()["researchTask"]
    instance = payload["researchDesignVersions"][0]["instances"][0]
    instance.update({
        "substageDefinitionId": "sdr:report_generation",
        "stageKey": "insight_generation",
        "expectedArtifactTypes": ["research_report"],
    })
    instance["acceptanceCriteria"][0]["requiredArtifactType"] = "research_report"
    decision = payload["executionDecisions"][0]
    decision["assignments"][0]["expectedArtifactTypes"] = ["research_report"]
    decision["assignments"][0]["acceptanceCriteria"][0]["requiredArtifactType"] = "research_report"
    decision["resultAssemblyPolicy"]["finalArtifactRequirements"][0]["artifactType"] = "research_report"
    work = payload["workUnits"][0]
    work.update({
        "substageDefinitionId": "sdr:report_generation",
        "expectedArtifactTypes": ["research_report"],
        "status": "completed",
        "finishedAt": "2026-07-28T00:00:59Z",
    })
    work["acceptanceCriteria"][0]["requiredArtifactType"] = "research_report"
    payload["artifacts"][0].update({"artifactType": "research_report", "artifactState": "usable"})

    closed = close_and_reconcile_decision_from_durable_facts(
        ResearchTaskV12.model_validate(payload),
        execution_decision_id="decision_contract_full",
        closed_at=datetime.fromisoformat("2026-07-28T00:01:00+00:00"),
    )

    assert closed.status.value == "completed"
    assert closed.finished_at is not None
    assert closed.final_result_authority is not None
    assert closed.final_result_authority.mode == "artifact_set"
    assert closed.final_result_authority.result_artifact_refs == ["artifact_contract_full"]




def test_generic_failed_runtime_remains_local_and_keeps_task_running() -> None:
    task = _task_ready_for_continuation(next_stage_key="execution_validation")
    payload = task.model_dump(mode="json", by_alias=True)
    runtime = next(row for row in payload["substageRuntimeStates"] if row["substageInstanceId"] == "sub_downstream")
    runtime.update(
        {
            "status": "failed",
            "activeExecutionDecisionId": None,
            "statusReasonCode": "generic_required_failure",
        }
    )
    reconciled = reconcile_task_lifecycle_from_runtime(
        ResearchTaskV12.model_validate(payload),
        occurred_at=datetime.fromisoformat("2026-07-28T00:02:00+00:00"),
    )
    assert reconciled.status is TaskStatus.RUNNING
    assert reconciled.finished_at is None


@pytest.mark.parametrize(
    ("mode", "reason"),
    [
        (TaskExecutionMode.STAGE_CONFIRMATION, "stage_confirmation_required"),
        (TaskExecutionMode.MANUAL_STEP, "manual_step_confirmation_required"),
    ],
)
def test_execution_mode_pause_materializes_without_preselecting_next_instance(mode, reason):
    task = _task_ready_for_continuation(next_stage_key="execution_validation").model_copy(
        update={"execution_mode": mode}
    )
    plan = plan_execution_continuation(
        task,
        completed_execution_decision_id="decision_contract_full",
        execution_mode=mode,
    )
    assert plan.action == "pause"
    assert plan.next_instance_id is None

    paused = materialize_execution_continuation_pause(
        task,
        reason_code=plan.reason_code,
        next_instance_id=plan.next_instance_id,
        occurred_at=datetime.fromisoformat("2026-07-28T00:02:00+00:00"),
    )

    assert paused.status is TaskStatus.RUNNING
    ready = [row for row in paused.substage_runtime_states if row.status is SubstageRuntimeStatus.READY]
    assert ready
    assert all(row.status_reason_code == reason for row in ready)
    reconciled = reconcile_task_lifecycle_from_runtime(
        paused,
        occurred_at=datetime.fromisoformat("2026-07-28T00:02:00+00:00"),
    )
    assert reconciled.status is TaskStatus.AWAITING_CONFIRMATION


def test_stop_and_resume_control_generations_are_reduced_from_runtime_truth():
    task = _task_ready_for_continuation(next_stage_key="execution_validation")
    occurred_at = datetime.fromisoformat("2026-07-28T00:02:00+00:00")

    stopped = reconcile_task_lifecycle_from_runtime(
        task,
        occurred_at=occurred_at,
        lifecycle_control_fact={"controlAction": "stop", "controlGeneration": 7},
    )
    assert stopped.status is TaskStatus.CANCELLED
    assert stopped.substage_runtime_states == task.substage_runtime_states

    resumed = reconcile_task_lifecycle_from_runtime(
        stopped,
        occurred_at=occurred_at,
        lifecycle_control_fact={
            "controlAction": "resume",
            "controlGeneration": 8,
            "supersedesControlGeneration": 7,
        },
    )
    assert resumed.status is TaskStatus.RUNNING
    assert resumed.substage_runtime_states == task.substage_runtime_states


@pytest.mark.parametrize("terminal_status", [TaskStatus.COMPLETED, TaskStatus.FAILED])
def test_resume_control_cannot_reopen_terminal_task_truth(terminal_status):
    task = _task_ready_for_continuation(next_stage_key="execution_validation").model_copy(
        update={"status": terminal_status}
    )
    resumed = reconcile_task_lifecycle_from_runtime(
        task,
        occurred_at=datetime.fromisoformat("2026-07-28T00:02:00+00:00"),
        lifecycle_control_fact={
            "controlAction": "resume",
            "controlGeneration": 2,
            "supersedesControlGeneration": 1,
        },
    )
    assert resumed is task


def test_only_explicit_user_blocker_reduces_to_confirmation_gate():
    task = _task_ready_for_continuation(next_stage_key="execution_validation")
    payload = task.model_dump(mode="json", by_alias=True)
    runtime = next(
        row for row in payload["substageRuntimeStates"]
        if row["substageInstanceId"] == "sub_downstream"
    )
    runtime.update({"status": "needs_input", "statusReasonCode": "required_input_missing"})

    gated = reconcile_task_lifecycle_from_runtime(
        ResearchTaskV12.model_validate(payload),
        occurred_at=datetime.fromisoformat("2026-07-28T00:02:00+00:00"),
    )
    assert gated.status is TaskStatus.AWAITING_CONFIRMATION


def test_system_owned_continuation_blocker_stays_on_recovery_path():
    task = _task_ready_for_continuation(next_stage_key="execution_validation")
    payload = task.model_dump(mode="json", by_alias=True)
    runtime = next(
        row for row in payload["substageRuntimeStates"]
        if row["substageInstanceId"] == "sub_downstream"
    )
    runtime.update({
        "status": "blocked_recoverable",
        "statusReasonCode": "dispatch_continuation_internal_error_exhausted",
    })

    recovering = reconcile_task_lifecycle_from_runtime(
        ResearchTaskV12.model_validate(payload),
        occurred_at=datetime.fromisoformat("2026-07-28T00:02:00+00:00"),
    )
    assert recovering.status is TaskStatus.RUNNING


def test_reviewer_human_gate_is_a_durable_user_gate_fact():
    task = _task_ready_for_continuation(next_stage_key="solution_design")
    gated = materialize_execution_continuation_pause(
        task,
        reason_code="reviewer_human_gate_required",
        next_instance_id="sub_downstream",
        occurred_at=datetime.fromisoformat("2026-07-28T00:02:00+00:00"),
    )
    runtime = next(
        row for row in gated.substage_runtime_states
        if row.substage_instance_id == "sub_downstream"
    )
    assert runtime.status_reason_code == "reviewer_human_gate_required"
    reduced = reconcile_task_lifecycle_from_runtime(
        gated,
        occurred_at=datetime.fromisoformat("2026-07-28T00:02:00+00:00"),
    )
    assert reduced.status is TaskStatus.AWAITING_CONFIRMATION
