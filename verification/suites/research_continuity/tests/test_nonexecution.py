"""Real completion functions with constructed non-execution boundaries."""

from copy import deepcopy

import pytest
from app.models.research_orchestration import ResearchTaskV12
from app.modules.research_execution_decision_service import reconcile_task_lifecycle_from_runtime
from app.modules.research_finalization import record_scientific_state_from_durable_facts
from app.modules.research_round_end import derive_round_end
from test_round_end import NOW, settled_scope
from tests.support.research_execution_decision import append_downstream_instance


def unexecuted_scope(reason="policy_prohibited"):
    payload, _, _ = settled_scope()
    payload.update(
        status="running",
        finishedAt=None,
        finalResultAuthority=None,
        completionDecisions=[],
        finalResults=[],
    )
    append_downstream_instance(payload)
    runtime = next(
        row
        for row in payload["substageRuntimeStates"]
        if row["substageInstanceId"] == "sub_downstream"
    )
    runtime.update(status="skipped", statusReasonCode=reason, activeExecutionDecisionId=None)
    return ResearchTaskV12.model_validate(payload)


def test_formal_round_ends_with_exact_nonexecution_proof_without_fake_work():
    task = unexecuted_scope()
    finished = reconcile_task_lifecycle_from_runtime(task, occurred_at=NOW)
    assert finished.status.value == "completed"
    decision = finished.completion_decisions[-1]
    assert decision.can_complete is False
    assert decision.round_end.non_execution_refs[0].model_dump(by_alias=True) == {
        "substageInstanceId": "sub_downstream",
        "instanceRevision": 1,
        "reasonCode": "policy_prohibited",
    }
    assert finished.work_units == task.work_units
    assert finished.capability_calls == task.capability_calls
    assert finished.execution_decisions == task.execution_decisions
    assert (
        finished.final_result_authority.final_result_id
        == finished.final_results[-1].final_result_id
    )
    assert reconcile_task_lifecycle_from_runtime(finished, occurred_at=NOW) == finished


@pytest.mark.parametrize(
    "field,value",
    [
        ("reasonCode", "unknown_reason"),
        ("reasonCode", "dependency_pending"),
        ("substageInstanceId", "another"),
        ("instanceRevision", 2),
    ],
)
def test_forged_nonexecution_proof_fails_final_integrity(field, value):
    finished = reconcile_task_lifecycle_from_runtime(unexecuted_scope(), occurred_at=NOW)
    payload = finished.model_dump(mode="json", by_alias=True)
    payload["completionDecisions"][-1]["roundEnd"]["nonExecutionRefs"][0][field] = value
    with pytest.raises(ValueError, match="non-execution|confirmed scope"):
        ResearchTaskV12.model_validate(payload)


@pytest.mark.parametrize(
    "reason",
    ["authorization_required", "adapter_implementation_missing", "external_result_unknown"],
)
def test_unknown_or_unapproved_boundary_does_not_settle_scope(reason):
    task = unexecuted_scope(reason)
    finished = reconcile_task_lifecycle_from_runtime(task, occurred_at=NOW)
    assert finished.status.value == "running"
    assert not finished.completion_decisions


def test_current_runtime_and_snapshot_reason_must_match():
    task = unexecuted_scope()
    design = task.research_design_versions[0]
    task, state = record_scientific_state_from_durable_facts(task, design, occurred_at=NOW)
    payload = task.model_dump(mode="json", by_alias=True)
    altered = deepcopy(state)
    row = next(
        row
        for row in altered["executionState"]["substageCompletion"]
        if row["substageInstanceId"] == "sub_downstream"
    )
    row["runtimeStatusReasonCode"] = "dependency_pending"
    payload["scientificStates"][-1] = altered
    assert derive_round_end(payload, design, altered) is None


@pytest.mark.parametrize("status", ["running", "reviewing", "external_result_unknown"])
def test_nonexecution_ref_cannot_cover_other_active_or_unknown_work(status):
    task = unexecuted_scope()
    payload = task.model_dump(mode="json", by_alias=True)
    payload["workUnits"][0]["status"] = status
    if status != "external_result_unknown":
        payload["workUnits"][0]["finishedAt"] = None
        decision = payload["executionDecisions"][0]
        decision.update(status="dispatched", outcome=None, closedAt=None)
        payload["substageRuntimeStates"][0].update(
            status="running",
            activeExecutionDecisionId=decision["executionDecisionId"],
        )
    unsettled = ResearchTaskV12.model_validate(payload)
    finished = reconcile_task_lifecycle_from_runtime(unsettled, occurred_at=NOW)
    assert finished.status.value == "running"
    assert not finished.completion_decisions


def test_duplicate_nonexecution_ref_does_not_count_as_double_scope_coverage():
    finished = reconcile_task_lifecycle_from_runtime(unexecuted_scope(), occurred_at=NOW)
    payload = finished.model_dump(mode="json", by_alias=True)
    refs = payload["completionDecisions"][-1]["roundEnd"]["nonExecutionRefs"]
    refs.append(deepcopy(refs[0]))
    with pytest.raises(ValueError, match="non-execution refs must uniquely"):
        ResearchTaskV12.model_validate(payload)


def test_actual_public_get_reads_new_receipt_as_unmet_goal(monkeypatch):
    import test_public_delivery as public_get

    task = unexecuted_scope()
    monkeypatch.setattr(
        public_get,
        "settled_scope",
        lambda: (
            task.model_dump(mode="json", by_alias=True),
            None,
            None,
        ),
    )
    public_get.test_get_separates_round_end_from_goal_and_preserves_private_receipts(
        monkeypatch,
        False,
    )
