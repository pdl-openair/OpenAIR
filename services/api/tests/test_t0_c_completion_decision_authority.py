from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

from app.models.research_orchestration import ResearchTaskV12
from app.modules.research_execution_decision_service import (
    close_and_reconcile_decision_from_durable_facts,
    close_decision_from_durable_facts,
)
from app.modules.research_finalization import finalize_from_validated_facts
from tests.support.research_lifecycle import load_structural_research_task_payload


NOW = datetime(2026, 9, 14, 4, 30, tzinfo=timezone.utc)


def _base_payload() -> dict:
    payload = load_structural_research_task_payload()
    payload.update(
        {
            "status": "running",
            "finishedAt": None,
            "finalResultAuthority": None,
            "scientificStates": [],
            "completionDecisions": [],
            "finalResults": [],
        }
    )
    return payload


def _mark_durable_success(payload: dict) -> None:
    payload["workUnits"][0].update(
        {
            "status": "completed",
            "terminalReasonCode": None,
            "finishedAt": NOW.isoformat(),
        }
    )
    payload["artifacts"][0]["artifactState"] = "usable"


def _closed_success_task() -> ResearchTaskV12:
    payload = _base_payload()
    _mark_durable_success(payload)
    task = ResearchTaskV12.model_validate(payload)
    return close_decision_from_durable_facts(
        task,
        execution_decision_id=task.execution_decisions[0].execution_decision_id,
        closed_at=NOW,
    )


def _current_completion(task: ResearchTaskV12):
    design = task.research_design_versions[0]
    state = max(
        (
            row
            for row in task.scientific_states
            if row.research_design_version == design.version
        ),
        key=lambda row: row.scientific_state_version,
    )
    return next(
        row
        for row in task.completion_decisions
        if row.completion_authority_version == "project_completion_v1"
        and row.scientific_state_version == state.scientific_state_version
        and row.research_design_ref is not None
        and row.research_design_ref.research_design_id == design.research_design_id
        and row.research_design_ref.version == design.version
    )


def test_satisfied_project_requirements_gate_final_result_and_task_completion() -> None:
    payload = _base_payload()
    _mark_durable_success(payload)
    task = ResearchTaskV12.model_validate(payload)

    completed = close_and_reconcile_decision_from_durable_facts(
        task,
        execution_decision_id=task.execution_decisions[0].execution_decision_id,
        closed_at=NOW,
    )

    decision = _current_completion(completed)
    assert decision.can_complete is True
    assert all(row.status == "satisfied" for row in decision.requirement_assessments)
    assert completed.status.value == "completed"
    assert completed.final_result_authority is not None
    assert completed.final_results[-1].completion_decision_id == decision.completion_decision_id


def test_legal_degraded_completion_preserves_gap_uncertainty_and_limitation() -> None:
    closed = _closed_success_task()
    payload = closed.model_dump(mode="json", by_alias=True)
    runtime = payload["substageRuntimeStates"][0]
    runtime.update(
        {
            "status": "blocked_recoverable",
            "statusReasonCode": "t0c_degraded_runtime",
        }
    )
    task = ResearchTaskV12.model_validate(payload)
    finalized = finalize_from_validated_facts(
        task,
        task.research_design_versions[0],
        occurred_at=NOW,
    )

    decision = _current_completion(finalized)
    assert decision.can_complete is True
    assert decision.disposition.value == "degraded"
    assert all(row.status == "satisfied" for row in decision.requirement_assessments)
    assert any(gap.endswith(":blocked_recoverable") for gap in decision.gaps)
    assert any(value.startswith("execution_degradation:") for value in decision.limitations)
    assert isinstance(decision.uncertainty, list)
    assert finalized.final_result_authority is not None

def test_completion_decision_is_bound_to_latest_scientific_state() -> None:
    closed = _closed_success_task()
    design = closed.research_design_versions[0]
    first = finalize_from_validated_facts(closed, design, occurred_at=NOW)
    first_decision = _current_completion(first)

    changed = first.model_dump(mode="json", by_alias=True)
    extra = deepcopy(changed["artifacts"][0])
    extra.update(
        {
            "artifactId": "artifact_t0c_new_durable_source",
            "sourceCapabilityCallId": None,
            "lineageKind": "direct_work_unit_output",
        }
    )
    changed["artifacts"].append(extra)
    second = finalize_from_validated_facts(
        ResearchTaskV12.model_validate(changed),
        design,
        occurred_at=NOW.replace(minute=31),
    )
    current = _current_completion(second)

    assert current.scientific_state_version > first_decision.scientific_state_version
    assert current.completion_decision_id != first_decision.completion_decision_id
    assert current.research_design_ref == second.active_research_design_ref
    assert second.final_result_authority is not None
    assert second.final_results[-1].completion_decision_id == current.completion_decision_id


def test_direct_finalization_cannot_bypass_completion_decision() -> None:
    task = ResearchTaskV12.model_validate(_base_payload())
    finalized = finalize_from_validated_facts(
        task,
        task.research_design_versions[0],
        occurred_at=NOW,
    )

    assert finalized.status.value == "running"
    assert finalized.finished_at is None
    assert finalized.completion_decisions == []
    assert finalized.final_result_authority is None
    assert finalized.final_results == []
