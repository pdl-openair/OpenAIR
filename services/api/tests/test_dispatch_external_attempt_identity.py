from __future__ import annotations

from app.modules.research_dispatch_service import ResearchDispatchService
from app.modules.research_orchestration_repository import (
    AuthorizedDispatch,
    DispatchFence,
    ExternalAttemptIdentity,
    ResearchOrchestrationRepository,
    dispatch_capability_call_id,
)


def _fence() -> DispatchFence:
    return DispatchFence(
        dispatch_id="dispatch-retry-identity",
        task_id="task-retry-identity",
        execution_decision_id="decision-retry-identity",
        work_unit_id="work-retry-identity",
        idempotency_key="provider-key-retry-identity",
        lease_owner="worker-retry-identity",
        attempt_count=2,
    )


def _authorized_retry() -> AuthorizedDispatch:
    return AuthorizedDispatch(
        **_fence().__dict__,
        authorization_task_revision=3,
        work_unit_payload={},
        external_attempt=ExternalAttemptIdentity(
            capability_call_id="call-retry-75c16ceb86db41cd161c1ffe",
            attempt_number=2,
        ),
    )


def test_external_execution_values_use_authorized_attempt() -> None:
    fence = _authorized_retry()
    work_unit = type(
        "WorkUnit",
        (),
        {"model_dump": lambda self, **kwargs: {"workUnitId": fence.work_unit_id}},
    )()

    values = ResearchOrchestrationRepository._external_execution_values(
        fence=fence,
        work_unit=work_unit,
        owner_id="owner",
        workspace_id="workspace",
        provider_id="openai_primary",
        adapter="huiyan_chat_completions",
    )

    assert values["capability_call_id"] == fence.external_attempt.capability_call_id
    assert values["attempt_number"] == 2


class _ProjectionMustNotBeRead:
    def load(self, **_scope):
        raise AssertionError("Task projection must not resolve execution identity")


def test_checkpoint_uses_authorized_attempt_without_reading_task_projection(
    monkeypatch,
) -> None:
    fence = _authorized_retry()
    captured: dict[str, str] = {}

    class _CheckpointRepo:
        def prepare(self, **kwargs):
            captured["capability_call_id"] = kwargs["capability_call_id"]
            return object()

    monkeypatch.setattr(
        "app.modules.external_execution_checkpoint.ExternalExecutionCheckpointRepository",
        _CheckpointRepo,
    )
    service = ResearchDispatchService(_ProjectionMustNotBeRead())

    service.prepare_external_execution(
        fence,
        owner_id="owner",
        workspace_id="workspace",
        execution_kind="provider_http",
        provider_id="openai_primary",
        adapter="huiyan_chat_completions",
        request_body=b"{}",
    )

    assert captured["capability_call_id"] == fence.external_attempt.capability_call_id
    assert captured["capability_call_id"] != dispatch_capability_call_id(fence)
