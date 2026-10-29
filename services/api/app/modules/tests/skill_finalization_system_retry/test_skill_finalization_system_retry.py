from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.execution.research_dispatch_provider import (
    SKILL_FINALIZATION_RUNTIME_FAILED,
    SKILL_FINALIZATION_SYSTEM_RETRY_REASON,
    STRUCTURED_OUTPUT_CORRECTION_EXHAUSTED,
    STRUCTURED_OUTPUT_EXPLICIT_RETRY_REASON,
)
from app.models.research_orchestration import (
    ExecutionDecisionOutcome,
    ExecutionDecisionStatus,
)
from app.models.research_external_result import ExplicitRetryCommand
from app.modules import workbench_user_actions as actions
from app.modules.research_dispatch_service import ResearchDispatchService
from app.modules.research_orchestration_repository import (
    AuthorizedDispatch,
    ExternalAttemptIdentity,
    ResearchOrchestrationRepository,
)
from tests.support.r0c_interruption import r0c_canonical


def _validation_row(**updates):
    row = {
        "execution_decision_id": "decision_1",
        "work_unit_id": "wu_1",
        "capability_call_id": "call_1",
        "attempt_number": 1,
        "owner_id": "owner_1",
        "workspace_id": "workspace_1",
        "provider_idempotency_key": "provider-key-1",
        "idempotency_key": "provider-key-1",
        "status": "failed",
        "external_state": "known_failed",
        "remote_run_id": None,
        "external_remote_run_id": None,
        "last_error_code": SKILL_FINALIZATION_RUNTIME_FAILED,
    }
    row.update(updates)
    return row


def _finalization_task(
    *,
    decision_status: str = "dispatched",
    decision_outcome: str | None = None,
):
    source = r0c_canonical(
        reason=SKILL_FINALIZATION_RUNTIME_FAILED,
        work_status="blocked",
        decision_status=decision_status,
        decision_outcome=decision_outcome,
    )
    decision = source.execution_decisions[0].model_copy(
        update={"execution_decision_id": "decision_1"}
    )
    if decision_status == "closed":
        decision = decision.model_copy(
            update={
                "status": ExecutionDecisionStatus.CLOSED,
                "outcome": ExecutionDecisionOutcome(decision_outcome or "blocked"),
                "closed_at": source.started_at,
            }
        )
    work = source.work_units[0].model_copy(
        update={
            "work_unit_id": "wu_1",
            "execution_decision_id": "decision_1",
            "idempotency_key": "provider-key-1",
            "status": "blocked",
            "terminal_reason_code": SKILL_FINALIZATION_RUNTIME_FAILED,
        }
    )
    call = source.capability_calls[0].model_copy(
        update={
            "capability_call_id": "call_1",
            "work_unit_id": "wu_1",
            "attempt_number": 1,
            "status": "failed",
        }
    )
    return source.model_copy(
        update={
            "execution_decisions": [decision],
            "work_units": [work],
            "capability_calls": [call],
        }
    )


@pytest.mark.parametrize(
    ("decision_status", "decision_outcome"),
    (
        ("dispatched", None),
        ("closed", "blocked"),
    ),
)
def test_explicit_retry_reason_allows_skill_finalization_known_failed(
    decision_status: str,
    decision_outcome: str | None,
) -> None:
    task = _finalization_task(
        decision_status=decision_status,
        decision_outcome=decision_outcome,
    )
    reason = actions.explicit_retry_reason(
        canonical=task,
        work_unit_id="wu_1",
        execution_decision_id="decision_1",
        prior_capability_call_id="call_1",
        prior_attempt_number=1,
        dispatch_status="failed",
        external_state="known_failed",
        dispatch_remote_run_id=None,
        external_remote_run_id=None,
        last_error_code=SKILL_FINALIZATION_RUNTIME_FAILED,
    )
    assert reason == SKILL_FINALIZATION_SYSTEM_RETRY_REASON
    command = ExplicitRetryCommand(
        task_id=task.task_id,
        work_unit_id="wu_1",
        execution_decision_id="decision_1",
        prior_capability_call_id="call_1",
        prior_attempt_number=1,
        owner_id="owner_1",
        workspace_id="workspace_1",
        reason_code=reason,
        dispatch_id="dispatch_1",
        command_request_id="retry-test-1",
        canonical_payload_fingerprint="a" * 64,
        expected_task_revision=0,
        actor_id="owner_1",
    )
    assert ResearchOrchestrationRepository._validate_retry_row(task, command, _validation_row()) is None


def _authorized_fence() -> AuthorizedDispatch:
    return AuthorizedDispatch(
        dispatch_id="dispatch_1",
        task_id="task_1",
        execution_decision_id="decision_1",
        work_unit_id="wu_1",
        idempotency_key="provider-key-1",
        lease_owner="worker-1",
        attempt_count=1,
        authorization_task_revision=8,
        work_unit_payload={},
        external_attempt=ExternalAttemptIdentity(
            capability_call_id="call_1",
            attempt_number=1,
        ),
    )


def test_record_outcome_auto_enqueues_system_explicit_retry() -> None:
    fence = _authorized_fence()
    repository = MagicMock()
    repository.record_dispatch_outcome.return_value = True
    command = SimpleNamespace(command_request_id="system-retry-1")
    repository.prepare_system_explicit_retry_command.return_value = command
    recovery = MagicMock()
    service = ResearchDispatchService(
        repository=repository,
        external_recovery_service=recovery,
    )

    recorded = service.record_outcome(
        fence,
        owner_id="owner_1",
        workspace_id="workspace_1",
        outcome="known_rejected",
        error_code=SKILL_FINALIZATION_RUNTIME_FAILED,
    )

    assert recorded is True
    repository.prepare_system_explicit_retry_command.assert_called_once_with(
        fence,
        owner_id="owner_1",
        workspace_id="workspace_1",
        error_code=SKILL_FINALIZATION_RUNTIME_FAILED,
    )
    recovery.retry.assert_called_once_with(command)


def test_record_outcome_skips_auto_retry_for_user_owned_errors() -> None:
    fence = _authorized_fence()
    repository = MagicMock()
    repository.record_dispatch_outcome.return_value = True
    recovery = MagicMock()
    service = ResearchDispatchService(
        repository=repository,
        external_recovery_service=recovery,
    )

    service.record_outcome(
        fence,
        owner_id="owner_1",
        workspace_id="workspace_1",
        outcome="known_rejected",
        error_code="skill_input_invalid",
    )

    repository.prepare_system_explicit_retry_command.assert_not_called()
    recovery.retry.assert_not_called()


def test_explicit_retry_reason_allows_structured_output_exhausted_after_closed_blocked() -> None:
    task = r0c_canonical(
        reason=STRUCTURED_OUTPUT_CORRECTION_EXHAUSTED,
        work_status="blocked",
        decision_status="closed",
        decision_outcome="blocked",
    )
    decision = task.execution_decisions[0].model_copy(
        update={
            "execution_decision_id": "decision_1",
            "status": ExecutionDecisionStatus.CLOSED,
            "outcome": ExecutionDecisionOutcome.BLOCKED,
            "closed_at": task.started_at,
        }
    )
    work = task.work_units[0].model_copy(
        update={
            "work_unit_id": "wu_1",
            "execution_decision_id": "decision_1",
            "idempotency_key": "provider-key-1",
            "status": "blocked",
            "terminal_reason_code": STRUCTURED_OUTPUT_CORRECTION_EXHAUSTED,
        }
    )
    call = task.capability_calls[0].model_copy(
        update={
            "capability_call_id": "call_1",
            "work_unit_id": "wu_1",
            "attempt_number": 1,
            "status": "failed",
        }
    )
    task = task.model_copy(
        update={
            "execution_decisions": [decision],
            "work_units": [work],
            "capability_calls": [call],
        }
    )
    reason = actions.explicit_retry_reason(
        canonical=task,
        work_unit_id="wu_1",
        execution_decision_id="decision_1",
        prior_capability_call_id="call_1",
        prior_attempt_number=1,
        dispatch_status="failed",
        external_state="known_failed",
        dispatch_remote_run_id=None,
        external_remote_run_id=None,
        last_error_code=STRUCTURED_OUTPUT_CORRECTION_EXHAUSTED,
    )
    assert reason == STRUCTURED_OUTPUT_EXPLICIT_RETRY_REASON
    command = ExplicitRetryCommand(
        task_id=task.task_id,
        work_unit_id="wu_1",
        execution_decision_id="decision_1",
        prior_capability_call_id="call_1",
        prior_attempt_number=1,
        owner_id="owner_1",
        workspace_id="workspace_1",
        reason_code=reason,
        dispatch_id="dispatch_1",
        command_request_id="retry-test-2",
        canonical_payload_fingerprint="a" * 64,
        expected_task_revision=0,
        actor_id="owner_1",
    )
    assert ResearchOrchestrationRepository._validate_retry_row(
        task,
        command,
        _validation_row(last_error_code=STRUCTURED_OUTPUT_CORRECTION_EXHAUSTED),
    ) is None


@pytest.mark.parametrize("attempt_number", [3, 4])
def test_explicit_retry_respects_dispatch_attempt_ceiling(monkeypatch: pytest.MonkeyPatch, attempt_number: int) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "research_dispatch_max_attempts", 3)
    task = _finalization_task()
    task.capability_calls[0] = task.capability_calls[0].model_copy(
        update={"attempt_number": attempt_number}
    )
    reason = actions.explicit_retry_reason(
        canonical=task,
        work_unit_id="wu_1",
        execution_decision_id="decision_1",
        prior_capability_call_id="call_1",
        prior_attempt_number=attempt_number,
        dispatch_status="failed",
        external_state="known_failed",
        dispatch_remote_run_id=None,
        external_remote_run_id=None,
        last_error_code=SKILL_FINALIZATION_RUNTIME_FAILED,
    )
    assert reason is None
