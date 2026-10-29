"""Control regressions for durable method failure and formal stop.

Production outcome/release/closure functions; repository IO is in memory.
This supplements test_execution_method_preflight_outcomes.py and uses its
unchanged PVD reading failure policy and declared generic cascade fixture.
"""
from __future__ import annotations

import pytest

from app.modules.research_dispatch_service import ResearchDispatchService
from app.modules.research_orchestration_repository import (
    DispatchFence,
    ResearchOrchestrationRepository,
)
from app.modules.tests.research_execution_method_runtime_control import test_execution_method_preflight_outcomes as fixture


def _failed_reading_callback():
    coordinator, repository = fixture._service(fixture._build_case())
    dispatched = fixture._release(coordinator, repository)
    reading = dispatched.work_units[-1]
    assert reading.execution_method_role_id == "progressive_reading"
    assert reading.execution_method_role_control["failurePolicy"] == "fail_closed"
    dispatch = repository.dispatches[-1]
    fence = DispatchFence(
        dispatch_id=dispatch.dispatch_id,
        task_id=dispatch.task_id,
        execution_decision_id=dispatch.execution_decision_id,
        work_unit_id=dispatch.work_unit_id,
        idempotency_key=dispatch.idempotency_key,
        lease_owner="test-known-failure",
        attempt_count=1,
    )
    failed = ResearchOrchestrationRepository._task_with_outcome(
        dispatched, fence, "known_failed", "provider_known_failed",
        coordinator._now(), external_action_started=False,
    )
    return coordinator, repository, dispatched, failed


def test_real_known_failure_callback_settles_fail_closed_reading():
    coordinator, repository, dispatched, failed = _failed_reading_callback()
    assert failed.work_units[-1].status.value == "blocked"
    assert failed.work_units[-1].terminal_reason_code == "provider_known_failed"
    assert failed.execution_decisions[-1].status.value == "closed"
    assert failed.execution_decisions[-1].outcome.value == "blocked"
    assert failed.substage_runtime_states[0].status.value == "blocked_recoverable"
    assert failed.substage_runtime_states[0].status_reason_code == "provider_known_failed"
    assert failed.artifacts == dispatched.artifacts
    assert failed.work_units[:-1] == dispatched.work_units[:-1]
    # Preserve actual WorkUnits and physical failure; no preflight record may
    # claim the dispatched reading and no new work is released after failure.
    assert failed.execution_decisions[-1].assignment_preflight_outcomes == (
        dispatched.execution_decisions[-1].assignment_preflight_outcomes
    )
    repository.current = failed
    save_count = repository.save_calls
    assert fixture._release(coordinator, repository) is failed
    assert repository.save_calls == save_count
    assert len(repository.dispatches) == 1


def test_durable_legacy_fail_closed_state_settles_once_without_dispatch():
    coordinator, repository = fixture._service(fixture._build_case())
    dispatched = fixture._release(coordinator, repository)
    # A known failure already committed by the old callback, before method
    # settlement. This is the durable input the worker scan must recover.
    failed = fixture.method_fixture._set_work_status(
        dispatched, "progressive_reading", "blocked",
    )
    repository.current = failed
    row = {
        "task_id": failed.task_id,
        "execution_decision_id": failed.execution_decisions[-1].execution_decision_id,
        "owner_id": "owner", "workspace_id": "workspace",
        "revision": failed.revision,
    }
    repository.list_pending_method_continuations = lambda **_kwargs: [row]
    service = ResearchDispatchService(repository, decision_coordinator=coordinator)
    save_count = repository.save_calls
    assert service.resume_pending_method_continuation() is True
    assert repository.current.revision == failed.revision + 1
    assert repository.current.execution_decisions[-1].status.value == "closed"
    assert repository.current.execution_decisions[-1].outcome.value == "blocked"
    assert repository.current.substage_runtime_states[0].status.value == "blocked_recoverable"
    assert repository.current.work_units == failed.work_units
    assert repository.current.artifacts == failed.artifacts
    assert repository.save_calls == save_count + 1
    assert len(repository.dispatches) == 1
    # A stale observed revision cannot settle again or submit any action.
    assert service.resume_pending_method_continuation() is False
    assert repository.save_calls == save_count + 1
    assert len(repository.dispatches) == 1


@pytest.mark.parametrize("has_final_artifact", [False, True])
def test_declared_stop_still_uses_formal_final_artifact_contract(has_final_artifact):
    coordinator, repository = fixture._service(fixture._build_case(cascade=True))
    dispatched = fixture._release(coordinator, repository)
    completed = fixture.method_fixture._set_work_status(
        dispatched, "evidence_closure", "completed",
    )
    if has_final_artifact:
        completed = fixture.method_fixture._append_role_artifact(
            completed, role_id="evidence_closure", iteration=1,
            artifact_id="artifact-formal-stop-closure",
        )
    decision = completed.execution_decisions[-1]
    method = repository.snapshot["definitions"]["substages"][0]["executionMethod"]
    assert fixture.decisions._execution_method_stop_triggered(
        completed, decision=decision, method=method,
    )
    assert fixture.decisions.prepare_ready_assignments(
        completed, execution_decision_id=decision.execution_decision_id,
        snapshot=repository.snapshot,
    ) is None
    repository.current = completed
    save_count = repository.save_calls
    settled = fixture._release(coordinator, repository)
    assert settled.execution_decisions[-1].status.value == "closed"
    assert settled.execution_decisions[-1].outcome.value == (
        "succeeded" if has_final_artifact else "blocked"
    )
    if not has_final_artifact:
        assert settled.substage_runtime_states[0].status_reason_code == (
            "final_artifact_requirement_unsatisfied"
        )
    assert settled.work_units == completed.work_units
    assert settled.artifacts == completed.artifacts
    assert repository.save_calls == save_count + 1
    assert len(repository.dispatches) == 1
    assert fixture._release(coordinator, repository) is settled
    assert repository.save_calls == save_count + 1
