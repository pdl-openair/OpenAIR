"""Actual decision closure with constructed scope, never a real CS run."""

from copy import deepcopy
from datetime import UTC, datetime

import pytest
from app.models.research_orchestration import ResearchTaskV12
from app.modules.research_execution_decision_service import (
    DecisionPolicyViolation,
    close_and_reconcile_decision_from_durable_facts,
    close_decision_from_durable_facts,
)
from tests.support.research_execution_decision import append_downstream_instance
from tests.support.research_lifecycle import load_structural_research_task_payload

NOW = datetime(2026, 10, 11, 0, 0, tzinfo=UTC)


def input_scope():
    payload = deepcopy(load_structural_research_task_payload())
    payload.update(
        status="running",
        finishedAt=None,
        finalResultAuthority=None,
        scientificStates=[],
        completionDecisions=[],
        finalResults=[],
    )
    payload["workUnits"][0].update(
        status="completed", terminalReasonCode=None, finishedAt=NOW.isoformat()
    )
    payload["artifacts"][0]["artifactState"] = "usable"
    criterion = payload["researchDesignVersions"][0]["instances"][0]["acceptanceCriteria"][0]
    criterion.update(
        judgmentRequirement="domain_professional",
        evaluatorType="agent",
        evaluatorRef="review-agent",
    )
    return payload


def test_actual_close_settles_delivery_without_satisfying_professional_goal():
    model = ResearchTaskV12.model_validate(input_scope())
    closed = close_decision_from_durable_facts(
        model,
        execution_decision_id=model.execution_decisions[0].execution_decision_id,
        closed_at=NOW,
    )
    assert closed.execution_decisions[0].outcome.value == "succeeded"
    assert closed.substage_runtime_states[0].status.value == "completed"
    row = closed.scientific_states[-1].execution_state["substageCompletion"][0]
    assert row["completionCriteriaSatisfied"] is False
    assert row["unresolvedCriterionIds"]
    assert closed.artifacts == model.artifacts
    assert closed.work_units == model.work_units


@pytest.mark.parametrize("status", ["queued", "running", "reviewing", "external_result_unknown"])
def test_actual_close_does_not_settle_active_or_unknown_required_work(status):
    payload = input_scope()
    payload["workUnits"][0]["status"] = status
    if status != "external_result_unknown":
        payload["workUnits"][0]["finishedAt"] = None
    model = ResearchTaskV12.model_validate(payload)
    with pytest.raises(DecisionPolicyViolation, match="required_assignment_not_terminal"):
        close_decision_from_durable_facts(
            model,
            execution_decision_id=model.execution_decisions[0].execution_decision_id,
            closed_at=NOW,
        )


def test_actual_close_still_requires_formal_artifact():
    payload = input_scope()
    payload["artifacts"][0]["artifactState"] = "needs_review"
    model = ResearchTaskV12.model_validate(payload)
    with pytest.raises(DecisionPolicyViolation, match="final_artifact_requirement_unsatisfied"):
        close_decision_from_durable_facts(
            model,
            execution_decision_id=model.execution_decisions[0].execution_decision_id,
            closed_at=NOW,
        )


def test_actual_close_and_t5_chain_end_round_with_false_goal_assessment():
    model = ResearchTaskV12.model_validate(input_scope())
    finished = close_and_reconcile_decision_from_durable_facts(
        model,
        execution_decision_id=model.execution_decisions[0].execution_decision_id,
        closed_at=NOW,
    )
    assert finished.substage_runtime_states[0].status.value == "completed"
    assert finished.status.value == "completed"
    decision = finished.completion_decisions[-1]
    assert decision.can_complete is False
    assert decision.round_end.reason == "confirmed_scope_settled"
    assert decision.requirement_assessments[0].status == "unresolved"
    assert finished.final_results[-1].completion_decision_id == decision.completion_decision_id


def test_real_dependency_promotion_uses_settled_delivery_without_goal_success():
    payload = input_scope()
    append_downstream_instance(payload)
    model = ResearchTaskV12.model_validate(payload)
    progressed = close_and_reconcile_decision_from_durable_facts(
        model,
        execution_decision_id=model.execution_decisions[0].execution_decision_id,
        closed_at=NOW,
    )
    by_id = {row.substage_instance_id: row for row in progressed.substage_runtime_states}
    assert by_id["sub_contract_full"].status.value == "completed"
    assert by_id["sub_downstream"].status.value == "ready"
    assert progressed.status.value == "running"
    assert not progressed.completion_decisions
    assert (
        progressed.scientific_states[-1].execution_state["substageCompletion"][0][
            "completionCriteriaSatisfied"
        ]
        is False
    )
