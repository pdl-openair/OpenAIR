from __future__ import annotations

from dataclasses import replace
from copy import deepcopy
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
import json
from pathlib import Path

import pytest

from tests.support.authorized_dispatch import authorized_dispatch

from app.execution import research_dispatch_provider as dispatch_provider_module
from app.execution import research_dispatch_worker as dispatch_worker_module

from app.execution.research_dispatch_provider import (
    DispatchProviderKnownRejected,
    DispatchProviderResultUnknown,
    DispatchSubmissionAccepted,
    DispatchSubmissionCompleted,
)
from app.execution.research_dispatch_worker import process_dispatch_claim
from app.modules.artifact_write import ArtifactStorageError
from app.modules.provider_registry import ProviderSubmissionSafety
from app.modules.research_orchestration_repository import (
    AuthorizedDispatch,
    DispatchAuthorizationError,
    DispatchContinuationFence,
    DispatchFence,
    ResearchOrchestrationPersistenceError,
    ResearchOrchestrationRepository,
)
from app.modules.research_orchestration_repository import dispatch_capability_call_id
from app.modules.continuation_responsibility import ContinuationResponsibilityProof
from app.modules.research_dispatch_service import ResearchDispatchService
from app.models.research_orchestration import ResearchTaskV12


SAFE = ProviderSubmissionSafety(True, True, True, False)
NO_REPLAY = ProviderSubmissionSafety(
    False, False, False, False, "single_attempt_no_replay"
)
FENCE = DispatchFence("dispatch-1", "task-1", "decision-1", "work-unit-1", "stable-key", "worker-1", 7)


@pytest.fixture(autouse=True)
def _no_real_provider_observation(monkeypatch):
    # Unit tests never touch PostgreSQL. Specific recovery tests override this
    # with a durable observation fact.
    monkeypatch.setattr(
        dispatch_worker_module,
        "_observe_openair_provider_execution",
        lambda *_args, **_kwargs: None,
    )


class Prepared:
    provider_id = "openai_primary"
    adapter = "huiyan_chat_completions"

    def __init__(self, outcome="accepted", *, capabilities=SAFE, on_submit=None) -> None:
        self.capabilities = capabilities
        self.outcome = outcome
        self.on_submit = on_submit
        self.calls = []

    def submit(self, request):
        self.calls.append(request)
        if self.on_submit:
            self.on_submit()
        if self.outcome == "rejected":
            raise DispatchProviderKnownRejected("provider_http_400")
        if self.outcome == "rejected_with_diagnostics":
            raise DispatchProviderKnownRejected(
                "provider_response_contract_invalid",
                failure_detail={
                    "failedStage": "runtime_guard_failed",
                    "firstMismatch": "P07C-R03",
                },
            )
        if self.outcome == "unknown":
            raise DispatchProviderResultUnknown(
                "provider_http_500", remote_run_id="pat-openair-unknown-1"
            )
        if self.outcome == "timeout":
            raise TimeoutError("secret provider body")
        if self.outcome == "response_loss":
            raise ConnectionError("response lost after provider acceptance")
        if self.outcome == "no_receipt":
            return DispatchSubmissionAccepted("")
        if self.outcome == "completed":
            return DispatchSubmissionCompleted(
                title="Result",
                mime_type="text/plain",
                content='{"result":"ok"}',
                provider_id="openai_primary",
                adapter="huiyan_chat_completions",
            )
        return DispatchSubmissionAccepted("remote-receipt-1")


class Provider:
    def __init__(self, prepared) -> None:
        self.prepared = prepared
        self.preflight_calls = 0

    def preflight(self):
        self.preflight_calls += 1
        return self.prepared


class Service:
    def __init__(
        self,
        *,
        stale=False,
        record_result=True,
        fail_result=True,
        revalidation_result="ready",
        completed_result_error=None,
        recovered_result_error=None,
        action_start_result=True,
        retry_disposition="retry_scheduled",
        renew_result=True,
        mismatch_then_revision: int | None = None,
        task_revision: int = 7,
    ) -> None:
        self.stale = stale
        self.authorize_calls = 0
        self.outcomes = []
        self.stopped = False
        self.preflight_failures = []
        self.record_result = record_result
        self.fail_result = fail_result
        self.revalidations = []
        self.revalidation_result = revalidation_result
        self.action_starts = []
        self.action_start_identities = []
        self.completed_results = []
        self.completed_result_error = completed_result_error
        self.recovered_result = False
        self.recovered_result_error = recovered_result_error
        self.action_start_result = action_start_result
        self.retry_disposition = retry_disposition
        self.deferred = []
        self.authorized_failures = []
        self.renewals = []
        self.renew_result = renew_result
        self.mismatch_then_revision = mismatch_then_revision
        self.task_revision = task_revision
        self.authorize_revisions: list[int] = []

    def load(self, **_scope):
        return SimpleNamespace(revision=self.task_revision)

    def authorize(self, fence, **kwargs):
        self.authorize_calls += 1
        revision = int(kwargs["expected_task_revision"])
        self.authorize_revisions.append(revision)
        if self.stale:
            return None
        if (
            self.mismatch_then_revision is not None
            and revision != self.mismatch_then_revision
        ):
            raise DispatchAuthorizationError("task_revision_mismatch")
        return authorized_dispatch(
            **fence.__dict__, authorization_task_revision=revision,
            work_unit_payload={"instruction": "frozen"},
        )

    def record_outcome(self, fence, **kwargs):
        self.outcomes.append((fence, kwargs, self.stopped))
        return self.record_result

    def fail_unsubmitted(self, fence, **kwargs):
        self.preflight_failures.append((fence, kwargs))
        return self.fail_result

    def defer_unsubmitted(self, fence, **kwargs):
        self.deferred.append((fence, kwargs))
        return self.retry_disposition

    def defer_authorized(self, fence, **kwargs):
        self.deferred.append((fence, kwargs))
        return self.retry_disposition

    def fail_authorized_unsubmitted(self, fence, **kwargs):
        self.authorized_failures.append((fence, kwargs))
        return self.fail_result

    def revalidate_recovery_submission(self, fence, **kwargs):
        self.revalidations.append((fence, kwargs))
        return self.revalidation_result

    def mark_external_action_started(self, fence, *, provider_id, adapter, lease_seconds=30):
        self.action_starts.append(fence)
        self.action_start_identities.append((provider_id, adapter, lease_seconds))
        return self.action_start_result

    def record_completed_result(self, fence, **kwargs):
        self.completed_results.append((fence, kwargs))
        if self.completed_result_error is not None:
            raise self.completed_result_error
        return self.record_result

    def recover_completed_result(self, fence, **kwargs):
        if self.recovered_result_error is not None:
            raise self.recovered_result_error
        return self.recovered_result

    def renew_authorized(self, fence, *, lease_seconds):
        self.renewals.append((fence, lease_seconds))
        return self.renew_result

    def resolve_external_attempt_identity(self, fence, *, owner_id, workspace_id):
        _ = owner_id, workspace_id
        return dispatch_capability_call_id(fence), 1


class CheckpointService(Service):
    def __init__(self, checkpoint):
        super().__init__()
        self.checkpoint = checkpoint

    def get_external_execution_checkpoint(self, _fence, **kwargs):
        _ = kwargs
        return self.checkpoint

    def prepare_external_execution(self, _fence, **_kwargs):
        return self.checkpoint

    def stage_external_execution_result(self, _fence, *, payload, **kwargs):
        _ = kwargs
        self.checkpoint = SimpleNamespace(
            **{
                **self.checkpoint.__dict__,
                "phase": "response_received",
                "response_kind": "dispatch_completed",
                "response_payload": payload,
            }
        )
        return self.checkpoint


class CheckpointReadFailureService(Service):
    def get_external_execution_checkpoint(self, _fence, **kwargs):
        _ = kwargs
        raise RuntimeError("local checkpoint read failed")


class CheckpointStageFailureService(CheckpointService):
    def stage_external_execution_result(self, _fence, *, payload, **kwargs):
        _ = kwargs
        raise RuntimeError("local checkpoint stage failed")


def _run(prepared, service=None):
    service = service or Service()
    result = process_dispatch_claim(
        FENCE,
        owner_id="owner-1",
        workspace_id="workspace-1",
        expected_task_revision=4,
        provider=Provider(prepared),
        service=service,
    )
    return result, service


def _authorized_no_replay(*, action_started: bool = True) -> AuthorizedDispatch:
    return authorized_dispatch(
        **FENCE.__dict__,
        authorization_task_revision=4,
        work_unit_payload={"instruction": "frozen"},
        submission_mode="single_attempt_no_replay",
        external_action_started_at=(
            datetime.fromisoformat("2026-07-31T00:00:00+00:00")
            if action_started
            else None
        ),
    )


def test_checkpoint_read_failure_is_local_retry_not_external_unknown() -> None:
    service = CheckpointReadFailureService()

    result = dispatch_worker_module.process_authorized_recovery(
        _authorized_no_replay(),
        owner_id="owner-1",
        workspace_id="workspace-1",
        provider=Provider(Prepared(capabilities=NO_REPLAY)),
        service=service,
    )

    assert result.status == "retryable_error"
    assert result.error_code == "staged_external_result_invalid"
    assert service.outcomes == []


def test_result_staging_failure_still_commits_completed_result() -> None:
    checkpoint = SimpleNamespace(
        phase="prepared",
        response_kind=None,
        response_payload=None,
    )
    service = CheckpointStageFailureService(checkpoint)
    prepared = Prepared(outcome="completed", capabilities=NO_REPLAY)

    result = dispatch_worker_module.process_authorized_recovery(
        _authorized_no_replay(action_started=False),
        owner_id="owner-1",
        workspace_id="workspace-1",
        provider=Provider(prepared),
        service=service,
    )

    assert result.status == "completed"
    assert result.recorded is True
    assert len(prepared.calls) == 1
    assert len(service.completed_results) == 1
    assert service.outcomes == []


def test_result_staging_serialization_failure_still_commits_completed_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkpoint = SimpleNamespace(
        phase="prepared",
        response_kind=None,
        response_payload=None,
    )
    service = CheckpointService(checkpoint)
    prepared = Prepared(outcome="completed", capabilities=NO_REPLAY)
    monkeypatch.setattr(
        dispatch_worker_module,
        "_dispatch_result_payload",
        lambda _result: (_ for _ in ()).throw(
            TypeError("local result serialization failed")
        ),
    )

    result = dispatch_worker_module.process_authorized_recovery(
        _authorized_no_replay(action_started=False),
        owner_id="owner-1",
        workspace_id="workspace-1",
        provider=Provider(prepared),
        service=service,
    )

    assert result.status == "completed"
    assert result.recorded is True
    assert len(prepared.calls) == 1
    assert len(service.completed_results) == 1
    assert service.outcomes == []


def test_staged_completed_result_recovers_without_provider_replay() -> None:
    completed = DispatchSubmissionCompleted(
        title="Recovered",
        mime_type="application/json",
        content='{"real":true}',
        provider_id="direct_skill_runtime",
        adapter="durable_skill_runtime",
        result_protocol={
            "status": "degraded",
            "accepted_items": [{"path": "queries", "count": 7}],
            "quarantined_items": [{"path": "queries[7]", "reason": "invalid_query"}],
            "repaired_items": [],
            "warnings": ["one query was skipped"],
            "coverage": {"accepted_query_count": 7},
        },
    )
    checkpoint = SimpleNamespace(
        phase="response_received",
        response_kind="dispatch_completed",
        response_payload=dispatch_worker_module._dispatch_result_payload(completed),
    )
    service = CheckpointService(checkpoint)
    prepared = Prepared(capabilities=NO_REPLAY)

    result = dispatch_worker_module.process_authorized_recovery(
        _authorized_no_replay(),
        owner_id="owner-1",
        workspace_id="workspace-1",
        provider=Provider(prepared),
        service=service,
    )

    assert result.status == "completed"
    assert prepared.calls == []
    assert service.completed_results[0][1]["result"].content == '{"real":true}'
    assert service.completed_results[0][1]["result"].result_protocol["status"] == "degraded"
    assert service.completed_results[0][1]["result"].result_protocol["coverage"] == {
        "accepted_query_count": 7
    }


def test_staged_completed_result_local_failure_never_overwrites_or_replays_provider() -> None:
    completed = DispatchSubmissionCompleted(
        title="Recovered",
        mime_type="application/json",
        content='{"real":true}',
        provider_id="direct_skill_runtime",
        adapter="durable_skill_runtime",
    )
    checkpoint = SimpleNamespace(
        phase="response_received",
        response_kind="dispatch_completed",
        response_payload=dispatch_worker_module._dispatch_result_payload(completed),
    )
    service = CheckpointService(checkpoint)
    service.completed_result_error = RuntimeError("local_acceptance_failed")
    prepared = Prepared(capabilities=NO_REPLAY)

    result = dispatch_worker_module.process_authorized_recovery(
        _authorized_no_replay(),
        owner_id="owner-1",
        workspace_id="workspace-1",
        provider=Provider(prepared),
        service=service,
    )

    assert result.status == "retryable_error"
    assert result.error_code == "staged_external_result_invalid"
    assert prepared.calls == []
    assert len(service.completed_results) == 1
    assert service.outcomes == []


def test_prepared_checkpoint_establishes_action_start_before_submitting_once() -> None:
    prepared_checkpoint = SimpleNamespace(
        phase="prepared", response_kind=None, response_payload=None
    )
    service = CheckpointService(prepared_checkpoint)
    prepared = Prepared(outcome="completed", capabilities=NO_REPLAY)
    recovered = dispatch_worker_module.process_authorized_recovery(
        _authorized_no_replay(action_started=False),
        owner_id="owner-1",
        workspace_id="workspace-1",
        provider=Provider(prepared),
        service=service,
    )
    assert recovered.status == "completed"
    assert len(service.action_starts) == 1
    assert len(prepared.calls) == 1


def test_prepared_checkpoint_with_action_start_reuses_boundary_and_submits_once() -> None:
    service = CheckpointService(
        SimpleNamespace(phase="prepared", response_kind=None, response_payload=None)
    )
    prepared = Prepared(outcome="completed", capabilities=NO_REPLAY)

    recovered = dispatch_worker_module.process_authorized_recovery(
        _authorized_no_replay(),
        owner_id="owner-1",
        workspace_id="workspace-1",
        provider=Provider(prepared),
        service=service,
    )

    assert recovered.status == "completed"
    assert service.action_starts == []
    assert len(prepared.calls) == 1


def test_submitted_checkpoint_without_result_stays_unknown_without_replay() -> None:
    ambiguous = CheckpointService(
        SimpleNamespace(phase="submitted", response_kind=None, response_payload=None)
    )
    recovered = dispatch_worker_module.process_authorized_recovery(
        _authorized_no_replay(),
        owner_id="owner-1",
        workspace_id="workspace-1",
        provider=Provider(Prepared(capabilities=NO_REPLAY)),
        service=ambiguous,
    )
    assert recovered.status == "submission_unknown"
    assert ambiguous.outcomes[0][1]["outcome"] == "unknown"
    assert ambiguous.outcomes[0][1]["error_code"] == "external_execution_result_unavailable"


def test_capability_failure_authorizes_and_transports_zero_times() -> None:
    prepared = Prepared(capabilities=replace(SAFE, acceptsIdempotencyKey=False))
    service = Service()

    result, _service = _run(prepared, service)

    assert result.status == "failed"
    assert service.authorize_calls == 0
    assert prepared.calls == []
    assert service.preflight_failures[0][1]["error_code"] == "provider_idempotency_unsupported"


def test_stale_authorize_fence_never_calls_provider_or_records_outcome() -> None:
    prepared = Prepared()
    result, service = _run(prepared, Service(stale=True))

    assert result.status == "stale_fence"
    assert prepared.calls == []
    assert service.outcomes == []


def test_authorize_retries_once_on_task_revision_mismatch() -> None:
    prepared = Prepared(outcome="completed")
    service = Service(mismatch_then_revision=8, task_revision=8)

    result, service = _run(prepared, service)

    assert result.status == "completed"
    assert service.authorize_calls == 2
    assert service.authorize_revisions == [4, 8]
    assert len(prepared.calls) == 1


def test_stop_winning_after_authorization_never_calls_provider_submit() -> None:
    prepared = Prepared()

    result, service = _run(prepared, Service(action_start_result=False))

    assert result.status == "outcome_conflict"
    assert service.action_starts
    assert prepared.calls == []
    assert service.outcomes == []


@pytest.mark.parametrize(
    ("provider_outcome", "worker_status", "recorded_outcome", "code"),
    (
        ("accepted", "submitted", "accepted", None),
        ("rejected", "failed", "known_rejected", "provider_http_400"),
        ("unknown", "submission_unknown", "unknown", "provider_http_500"),
        ("timeout", "failed", "known_rejected", "local_result_processing_failed"),
        ("response_loss", "failed", "known_rejected", "local_result_processing_failed"),
        ("no_receipt", "failed", "known_rejected", "local_result_processing_failed"),
    ),
)
def test_worker_submits_once_with_stable_key_and_records_structured_outcome(
    provider_outcome, worker_status, recorded_outcome, code
) -> None:
    prepared = Prepared(provider_outcome)

    result, service = _run(prepared)

    assert result.status == worker_status
    assert len(prepared.calls) == 1
    assert service.action_starts
    assert prepared.calls[0].idempotency_key == FENCE.idempotency_key
    assert prepared.calls[0].capability_call_id == dispatch_capability_call_id(service.action_starts[0])
    assert prepared.calls[0].attempt_number == 1
    assert len(service.outcomes) == 1
    assert service.outcomes[0][1]["outcome"] == recorded_outcome
    assert service.outcomes[0][1].get("error_code") == code


def test_physical_provider_submission_remains_one_across_continuation_retries() -> None:
    prepared = Prepared()
    result, _service = _run(prepared)
    assert result.status == "submitted"
    assert len(prepared.calls) == 1

    fences = [
        DispatchContinuationFence(
            dispatch_id=FENCE.dispatch_id,
            task_id=FENCE.task_id,
            execution_decision_id=FENCE.execution_decision_id,
            work_unit_id=FENCE.work_unit_id,
            owner_id="owner-1",
            workspace_id="workspace-1",
            lease_owner=f"continuation-{attempt}",
            attempt_count=attempt,
        )
        for attempt in (1, 2)
    ]

    class ContinuationRepository:
        def claim_dispatch_continuation(self, **_kwargs):
            return fences.pop(0) if fences else None

        def renew_dispatch_continuation(self, _fence, *, lease_seconds):
            return lease_seconds > 0

        def defer_dispatch_continuation(self, _fence, **_kwargs):
            return True

    continuation = ResearchDispatchService(ContinuationRepository())
    continuation.process_continuation = (  # type: ignore[method-assign]
        lambda fence: ContinuationResponsibilityProof.no_proof(
            source_execution_decision_id=fence.execution_decision_id,
            reason_code="dispatch_continuation_not_completed",
        )
    )
    assert continuation.process_next_dispatch_continuation(
        lease_owner="continuation-1", lease_seconds=30
    ) is True
    assert continuation.process_next_dispatch_continuation(
        lease_owner="continuation-2", lease_seconds=30
    ) is True
    assert len(prepared.calls) == 1


def test_worker_persists_openair_execution_identity_for_unknown_result() -> None:
    result, service = _run(Prepared("unknown"))

    assert result.status == "submission_unknown"
    assert service.outcomes[0][1]["remote_run_id"] == "pat-openair-unknown-1"


def test_stop_after_provider_request_does_not_discard_accepted_truth() -> None:
    service = Service()
    prepared = Prepared(on_submit=lambda: setattr(service, "stopped", True))

    result, service = _run(prepared, service)

    assert result.status == "submitted"
    assert service.outcomes[0][2] is True
    assert service.outcomes[0][1]["remote_run_id"] == "remote-receipt-1"


def test_worker_persists_safe_known_rejection_diagnostics() -> None:
    result, service = _run(Prepared("rejected_with_diagnostics"))

    assert result.status == "failed"
    assert service.outcomes[0][1]["failure_detail"] == {
        "failedStage": "runtime_guard_failed",
        "firstMismatch": "P07C-R03",
    }


def test_synchronous_completed_result_uses_durable_result_persistence() -> None:
    prepared = Prepared("completed")

    result, service = _run(prepared)

    assert result.status == "completed"
    assert result.recorded is True
    assert len(prepared.calls) == 1
    assert len(service.completed_results) == 1
    assert service.outcomes == []


def test_completed_provider_result_with_local_persistence_failure_is_retryable_not_unknown() -> None:
    prepared = Prepared("completed")
    service = Service(completed_result_error=RuntimeError("local persistence bug"))

    result, service = _run(prepared, service)

    assert result.status == "retryable_error"
    assert result.recorded is False
    assert result.error_code == "local_result_persistence_failed"
    assert len(prepared.calls) == 1
    assert len(service.completed_results) == 1
    assert service.outcomes == []


def test_completed_provider_result_with_storage_failure_preserves_completed_execution() -> None:
    prepared = Prepared("completed")
    service = Service(
        completed_result_error=ArtifactStorageError("artifact_storage_write_failed")
    )

    result, service = _run(prepared, service)

    assert result.status == "submitted"
    assert result.recorded is True
    assert len(prepared.calls) == 1
    assert len(service.completed_results) == 1
    assert service.outcomes[0][1]["outcome"] == "completed"
    assert service.outcomes[0][1]["error_code"] == "artifact_storage_write_failed"


@pytest.mark.parametrize("error", (TimeoutError("timeout"), OSError("io"), RuntimeError("bug")))
def test_transient_or_unexpected_preflight_failure_is_not_terminalized(error: Exception) -> None:
    class RaisingProvider:
        def preflight(self):
            raise error

    service = Service()
    result = process_dispatch_claim(
        FENCE,
        owner_id="owner-1",
        workspace_id="workspace-1",
        expected_task_revision=4,
        provider=RaisingProvider(),
        service=service,
    )

    assert result.status == "retry_scheduled"
    assert result.recorded is True
    assert service.preflight_failures == []
    assert len(service.deferred) == 1
    assert service.deferred[0][1]["error_code"] in {
        "provider_preflight_timeout",
        "provider_preflight_io_error",
        "provider_preflight_internal_error",
    }


@pytest.mark.parametrize("provider_outcome", ("accepted", "rejected", "unknown"))
def test_outcome_persistence_false_reports_conflict(provider_outcome: str) -> None:
    prepared = Prepared(provider_outcome)

    result, service = _run(prepared, Service(record_result=False))

    assert result.status == "outcome_conflict"
    assert result.recorded is False
    assert len(prepared.calls) == 1
    assert len(service.outcomes) == 1


def test_permanent_preflight_persistence_false_reports_conflict() -> None:
    prepared = Prepared(capabilities=replace(SAFE, acceptsIdempotencyKey=False))

    result, service = _run(prepared, Service(fail_result=False))

    assert result.status == "outcome_conflict"
    assert result.recorded is False
    assert prepared.calls == []
    assert len(service.preflight_failures) == 1


class QueryPrepared(Prepared):
    def __init__(self, query_result=None, *, query_error=None) -> None:
        super().__init__()
        self.query_result = query_result
        self.query_error = query_error
        self.query_keys = []

    def query_by_idempotency_key(self, idempotency_key):
        self.query_keys.append(idempotency_key)
        if self.query_error is not None:
            raise self.query_error
        return self.query_result


def _query_result(status: str, *, remote_run_id: str | None = None, error_code: str | None = None):
    result_type = getattr(dispatch_provider_module, "DispatchSubmissionQueryResult", None)
    assert result_type is not None, "authorized recovery requires structured query results"
    return result_type(status=status, remote_run_id=remote_run_id, error_code=error_code)


def _run_recovery(prepared: QueryPrepared, service: Service | None = None):
    process = getattr(dispatch_worker_module, "process_authorized_recovery", None)
    assert callable(process), "authorized recovery worker path is required"
    authorized = authorized_dispatch(
        **FENCE.__dict__,
        authorization_task_revision=4,
        work_unit_payload={"instruction": "frozen"},
    )
    service = service or Service()
    result = process(
        authorized,
        owner_id="owner-1",
        workspace_id="workspace-1",
        provider=Provider(prepared),
        service=service,
    )
    return result, service


def test_single_attempt_recovery_after_transport_never_queries_or_replays() -> None:
    prepared = QueryPrepared(_query_result("not_submitted"))
    authorized = authorized_dispatch(
        **FENCE.__dict__,
        authorization_task_revision=4,
        work_unit_payload={"instruction": "frozen"},
        submission_mode="single_attempt_no_replay",
        external_action_started_at=datetime.fromisoformat("2026-07-31T00:00:00+00:00"),
    )
    service = Service()

    result = dispatch_worker_module.process_authorized_recovery(
        authorized,
        owner_id="owner-1",
        workspace_id="workspace-1",
        provider=Provider(prepared),
        service=service,
    )

    assert result.status == "submission_unknown"
    assert prepared.query_keys == []
    assert prepared.calls == []
    assert service.action_starts == []
    assert service.outcomes[0][1]["outcome"] == "unknown"
    assert service.outcomes[0][1]["error_code"] == "external_execution_result_unavailable"


def test_providerless_recovery_defers_while_execution_still_pending() -> None:
    authorized = authorized_dispatch(
        **FENCE.__dict__,
        authorization_task_revision=4,
        work_unit_payload={"instruction": "frozen"},
        submission_mode="single_attempt_no_replay",
        external_action_started_at=datetime.fromisoformat("2026-07-31T00:00:00+00:00"),
    )
    service = Service()

    result = dispatch_worker_module.process_authorized_recovery(
        authorized,
        owner_id="owner-1",
        workspace_id="workspace-1",
        provider=None,
        service=service,
    )

    assert result.status == "retryable_error"
    assert result.recorded is False
    assert result.error_code == "providerless_execution_result_pending"
    assert service.outcomes == []


@pytest.mark.parametrize(
    ("observed_state", "transport_outcome", "http_status", "expected_status", "expected_outcome", "expected_error"),
    (
        ("failed", "transport_not_submitted", None, "failed", "not_submitted", "provider_transport_not_submitted"),
        ("failed", "provider_http_error", 503, "failed", "known_failed", "provider_http_503"),
        ("unknown", "transport_uncertain", None, "submission_unknown", "unknown", "external_execution_result_unavailable"),
        ("running", "in_flight", None, "submitted", "running", None),
        ("completed", "response_received", 200, "submitted", "completed", "external_execution_result_unavailable"),
    ),
)
def test_single_attempt_recovery_reconciles_durable_provider_observation(
    monkeypatch, observed_state, transport_outcome, http_status, expected_status, expected_outcome, expected_error
) -> None:
    authorized = authorized_dispatch(
        **FENCE.__dict__,
        authorization_task_revision=4,
        work_unit_payload={"instruction": "frozen"},
        submission_mode="single_attempt_no_replay",
        external_action_started_at=datetime.fromisoformat("2026-07-31T00:00:00+00:00"),
    )
    observation = SimpleNamespace(
        attempt_id="pat-observed-1",
        state=observed_state,
        http_status=http_status,
        transport_outcome=transport_outcome,
    )
    monkeypatch.setattr(
        dispatch_worker_module,
        "_observe_openair_provider_execution",
        lambda *_args, **_kwargs: observation,
    )
    service = Service()

    result = dispatch_worker_module.process_authorized_recovery(
        authorized,
        owner_id="owner-1",
        workspace_id="workspace-1",
        provider=None,
        service=service,
    )

    assert result.status == expected_status
    assert service.outcomes[0][1]["outcome"] == expected_outcome
    assert service.outcomes[0][1]["error_code"] == expected_error
    if transport_outcome == "transport_not_submitted":
        assert service.outcomes[0][1].get("remote_run_id") is None
    elif observed_state == "failed":
        assert service.outcomes[0][1]["remote_run_id"] == "pat-observed-1"
    else:
        assert service.outcomes[0][1]["remote_run_id"] == "pat-observed-1"


def test_single_attempt_recovery_keeps_in_flight_running_after_owner_loss_without_replay(
    monkeypatch,
) -> None:
    authorized = authorized_dispatch(
        **FENCE.__dict__,
        authorization_task_revision=4,
        work_unit_payload={"instruction": "frozen"},
        submission_mode="single_attempt_no_replay",
        external_action_started_at=datetime.fromisoformat("2026-07-31T00:00:00+00:00"),
    )
    observation = SimpleNamespace(
        attempt_id="pat-inflight-1",
        state="unknown",
        http_status=None,
        transport_outcome="in_flight",
    )
    monkeypatch.setattr(
        dispatch_worker_module,
        "_observe_openair_provider_execution",
        lambda *_args, **_kwargs: observation,
    )
    service = Service()

    result = dispatch_worker_module.process_authorized_recovery(
        authorized,
        owner_id="owner-1",
        workspace_id="workspace-1",
        provider=None,
        service=service,
    )

    assert result.status == "submitted"
    assert result.recorded is True
    assert result.error_code is None
    assert service.outcomes[0][1]["outcome"] == "running"
    assert service.outcomes[0][1]["remote_run_id"] == "pat-inflight-1"
    assert service.action_starts == []


def test_lease_keeper_renews_immediately_on_enter() -> None:
    service = Service()
    authorized = authorized_dispatch(
        **FENCE.__dict__,
        authorization_task_revision=4,
        work_unit_payload={"instruction": "frozen"},
        submission_mode="single_attempt_no_replay",
    )

    with dispatch_worker_module._AuthorizedDispatchLeaseKeeper(
        service, authorized, lease_seconds=30
    ) as keeper:
        assert keeper.lost is False
        assert len(service.renewals) >= 1
        assert service.renewals[0] == (authorized, 30)


def test_lease_keeper_lost_before_submit_skips_provider_call() -> None:
    prepared = Prepared()
    service = Service(renew_result=False)

    result, service = _run(prepared, service)

    assert result.status == "outcome_conflict"
    assert result.error_code == "dispatch_lease_lost"
    assert prepared.calls == []
    assert service.action_starts
    assert service.outcomes == []
    assert service.completed_results == []


def test_worker_passes_lease_seconds_through_authorize_and_action_start() -> None:
    prepared = Prepared()
    service = Service()

    result = process_dispatch_claim(
        FENCE,
        owner_id="owner-1",
        workspace_id="workspace-1",
        expected_task_revision=4,
        provider=Provider(prepared),
        service=service,
        lease_seconds=45,
    )

    assert result.status == "submitted"
    assert service.action_start_identities[0] == ("openai_primary", "huiyan_chat_completions", 45)
    assert service.renewals
    assert all(seconds == 45 for _fence, seconds in service.renewals)


def test_long_provider_submit_keeps_lease_via_keeper_renewals() -> None:
    """Known-bad: Provider I/O outlives the original short lease window."""
    import time

    renew_times: list[float] = []
    service = Service()
    original_renew = service.renew_authorized

    def tracking_renew(fence, *, lease_seconds):
        renew_times.append(time.monotonic())
        return original_renew(fence, lease_seconds=lease_seconds)

    service.renew_authorized = tracking_renew  # type: ignore[method-assign]

    prepared = Prepared(on_submit=lambda: time.sleep(1.2))
    result = process_dispatch_claim(
        FENCE,
        owner_id="owner-1",
        workspace_id="workspace-1",
        expected_task_revision=4,
        provider=Provider(prepared),
        service=service,
        lease_seconds=3,
    )

    assert result.status == "submitted"
    assert len(prepared.calls) == 1
    assert len(renew_times) >= 2  # immediate enter + at least one periodic
    assert service.outcomes[0][1]["outcome"] == "accepted"


def test_takeover_boundary_late_completed_write_becomes_outcome_conflict() -> None:
    """Ownership already taken: late completed must not invent a new unknown."""
    service = Service(record_result=False)
    prepared = Prepared("completed")

    result, service = _run(prepared, service)

    assert result.status == "outcome_conflict"
    assert result.recorded is False
    assert len(prepared.calls) == 1
    assert service.completed_results
    assert service.outcomes == []


def test_single_attempt_recovery_uses_persisted_result_without_provider_call() -> None:
    prepared = QueryPrepared(_query_result("not_submitted"))
    authorized = authorized_dispatch(
        **FENCE.__dict__,
        authorization_task_revision=4,
        work_unit_payload={"instruction": "frozen"},
        submission_mode="single_attempt_no_replay",
        external_action_started_at=datetime.fromisoformat("2026-07-31T00:00:00+00:00"),
    )
    service = Service()
    service.recovered_result = True

    result = dispatch_worker_module.process_authorized_recovery(
        authorized,
        owner_id="owner-1",
        workspace_id="workspace-1",
        provider=Provider(prepared),
        service=service,
    )

    assert result.status == "completed"
    assert result.recorded is True
    assert prepared.query_keys == []
    assert prepared.calls == []
    assert service.outcomes == []


def test_crash_after_authorize_queries_not_submitted_then_rechecks_and_submits_same_key_once() -> None:
    prepared = QueryPrepared(_query_result("not_submitted"))

    result, service = _run_recovery(prepared)

    assert result.status == "submitted"
    assert prepared.query_keys == [FENCE.idempotency_key]
    assert len(service.revalidations) == 1
    assert [request.idempotency_key for request in prepared.calls] == [FENCE.idempotency_key]


def test_durable_recovery_honors_provider_query_proving_not_submitted_after_local_action_start() -> None:
    prepared = QueryPrepared(_query_result("not_submitted"))
    authorized = authorized_dispatch(
        **FENCE.__dict__,
        authorization_task_revision=4,
        work_unit_payload={"instruction": "frozen"},
        external_action_started_at=datetime.fromisoformat("2026-07-31T00:00:00+00:00"),
    )
    service = Service()

    result = dispatch_worker_module.process_authorized_recovery(
        authorized,
        owner_id="owner-1",
        workspace_id="workspace-1",
        provider=Provider(prepared),
        service=service,
    )

    assert result.status == "failed"
    assert prepared.query_keys == [FENCE.idempotency_key]
    assert prepared.calls == []
    assert service.action_starts == []
    assert service.outcomes[0][1]["outcome"] == "not_submitted"
    assert service.outcomes[0][1]["error_code"] == "provider_query_not_submitted"


def test_started_recovery_permanent_preflight_failure_stays_unknown() -> None:
    class PermanentlyUnavailableProvider:
        def preflight(self):
            from app.execution.research_dispatch_provider import (
                DispatchProviderPermanentPreflightError,
            )

            raise DispatchProviderPermanentPreflightError("provider_authority_missing")

    authorized = authorized_dispatch(
        **FENCE.__dict__,
        authorization_task_revision=4,
        work_unit_payload={"instruction": "frozen"},
        external_action_started_at=datetime.fromisoformat("2026-07-31T00:00:00+00:00"),
    )
    service = Service()

    result = dispatch_worker_module.process_authorized_recovery(
        authorized,
        owner_id="owner-1",
        workspace_id="workspace-1",
        provider=PermanentlyUnavailableProvider(),
        service=service,
    )

    assert result.status == "submission_unknown"
    assert result.recorded is True
    assert service.deferred == []
    assert service.authorized_failures == []
    assert service.outcomes[0][1]["outcome"] == "unknown"
    assert service.outcomes[0][1]["error_code"] == "provider_authority_missing"


def test_crash_after_provider_acceptance_queries_accepted_and_never_resubmits() -> None:
    prepared = QueryPrepared(_query_result("accepted", remote_run_id="remote-existing"))

    result, service = _run_recovery(prepared)

    assert result.status == "submitted"
    assert prepared.query_keys == [FENCE.idempotency_key]
    assert prepared.calls == []
    assert service.outcomes[0][1]["remote_run_id"] == "remote-existing"


def test_physical_provider_submission_remains_one_across_worker_restart_recovery() -> None:
    prepared = QueryPrepared(
        _query_result("accepted", remote_run_id="remote-receipt-1")
    )
    initial, _initial_service = _run(prepared)
    assert initial.status == "submitted"
    assert len(prepared.calls) == 1

    recovered, recovered_service = _run_recovery(prepared)
    assert recovered.status == "submitted"
    assert prepared.query_keys == [FENCE.idempotency_key]
    assert len(prepared.calls) == 1
    assert recovered_service.outcomes[0][1]["remote_run_id"] == "remote-receipt-1"


def test_physical_provider_submission_remains_one_across_stop_late_truth_and_resume() -> None:
    prepared = QueryPrepared(
        _query_result("accepted", remote_run_id="remote-receipt-1")
    )
    stopped_service = Service()
    prepared.on_submit = lambda: setattr(stopped_service, "stopped", True)

    late_truth, stopped_service = _run(prepared, stopped_service)
    assert late_truth.status == "submitted"
    assert stopped_service.outcomes[0][2] is True
    assert len(prepared.calls) == 1

    resumed, resumed_service = _run_recovery(prepared, Service())
    assert resumed.status == "submitted"
    assert prepared.query_keys == [FENCE.idempotency_key]
    assert len(prepared.calls) == 1
    assert resumed_service.outcomes[0][1]["remote_run_id"] == "remote-receipt-1"


@pytest.mark.parametrize(
    "prepared",
    (
        QueryPrepared(query_error=TimeoutError("query timeout")),
        QueryPrepared(query_error=ConnectionError("query connection lost")),
    ),
)
def test_recovery_query_exception_records_unknown_and_never_submits(prepared: QueryPrepared) -> None:
    result, service = _run_recovery(prepared)

    assert result.status == "submission_unknown"
    assert prepared.calls == []
    assert service.outcomes[0][1]["outcome"] == "unknown"


@pytest.mark.parametrize(
    ("query_status", "error_code", "worker_status", "recorded_outcome"),
    (
        ("known_rejected", "provider_query_rejected", "failed", "known_rejected"),
        ("unknown", "provider_query_unknown", "submission_unknown", "unknown"),
    ),
)
def test_recovery_query_terminal_fact_never_submits(
    query_status: str,
    error_code: str,
    worker_status: str,
    recorded_outcome: str,
) -> None:
    prepared = QueryPrepared(_query_result(query_status, error_code=error_code))

    result, service = _run_recovery(prepared)

    assert result.status == worker_status
    assert prepared.calls == []
    assert service.outcomes[0][1]["outcome"] == recorded_outcome
    assert service.outcomes[0][1]["error_code"] == error_code


@pytest.mark.parametrize(
    "query_result",
    (
        lambda: _query_result("accepted", remote_run_id="existing"),
        lambda: _query_result("known_rejected", error_code="query_rejected"),
        lambda: _query_result("unknown", error_code="query_unknown"),
    ),
)
def test_recovery_query_fact_persistence_conflict_never_reports_terminal(query_result) -> None:
    prepared = QueryPrepared(query_result())

    result, _service = _run_recovery(prepared, Service(record_result=False))

    assert result.status == "outcome_conflict"
    assert result.recorded is False
    assert prepared.calls == []


@pytest.mark.parametrize(("authority", "expected_status"), (("closed", "failed"), ("stale", "outcome_conflict")))
def test_recovery_not_submitted_requires_current_authority_before_transport(
    authority: str,
    expected_status: str,
) -> None:
    prepared = QueryPrepared(_query_result("not_submitted"))

    result, service = _run_recovery(prepared, Service(revalidation_result=authority))

    assert result.status == expected_status
    assert result.recorded is (authority == "closed")
    assert prepared.calls == []
    assert len(service.revalidations) == 1


_CONTRACT_FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "packages"
    / "shared"
    / "test-fixtures"
    / "research-orchestration-v1.2-valid.json"
)


def _binding_task(*, agent: bool) -> ResearchTaskV12:
    payload = json.loads(_CONTRACT_FIXTURE.read_text(encoding="utf-8"))["researchTask"]
    decision = payload["executionDecisions"][0]
    assignment = decision["assignments"][0]
    work_unit = payload["workUnits"][0]
    payload["substageRuntimeStates"][0]["status"] = "queued"
    work_unit.update({"status": "queued", "finishedAt": None})
    if agent:
        agent_ref = {"agentId": "agent-cold-start", "definitionVersion": 1}
        actor_ref = {
            "actorType": "agent",
            "actorId": "agent-cold-start",
            "definitionVersion": 1,
        }
        executor_ref = {
            "executorType": "agent",
            "executorId": "agent-cold-start",
            "definitionVersion": 1,
        }
        decision.update({"strategy": "specialist_agent"})
        decision["candidateEvaluations"][0].update(
            {
                "candidateRef": agent_ref,
                "supportedRole": "primary",
                "sourceBindingRefs": [
                    {"bindingId": "binding-agent-cold-start", "bindingVersion": 1}
                ],
            }
        )
        assignment.update(
            {
                "sourceBindingRefs": [
                    {"bindingId": "binding-agent-cold-start", "bindingVersion": 1}
                ],
                "responsibleActorRef": actor_ref,
                "executorRef": executor_ref,
            }
        )
        work_unit.update(
            {
                "responsibleActorRef": actor_ref,
                "executorRef": executor_ref,
            }
        )
        decision["runtimeReadiness"][0].update(
            {
                "capabilityRef": agent_ref,
                "runtimeBindingId": "registry:binding-agent-cold-start:1",
            }
        )
    return ResearchTaskV12.model_validate(payload)


class _BindingRepository:
    def __init__(self, task: ResearchTaskV12, snapshot: dict | None = None) -> None:
        self.task = task
        self.snapshot = snapshot or {}

    def load(self, **_scope):
        return self.task

    def load_registry_snapshot(self, _content_hash):
        return deepcopy(self.snapshot)


def _dispatch_candidate(task: ResearchTaskV12) -> dict:
    decision = task.execution_decisions[0]
    work_unit = task.work_units[0]
    return {
        "dispatch_id": "dispatch-cold-start",
        "task_id": task.task_id,
        "execution_decision_id": decision.execution_decision_id,
        "work_unit_id": work_unit.work_unit_id,
        "idempotency_key": work_unit.idempotency_key,
        "status": "pending",
        "owner_id": "owner",
        "workspace_id": "workspace",
        "revision": task.revision,
    }


def test_candidate_query_selects_durable_result_ref_for_submission_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from worker import entrypoint

    observed: list[str] = []

    class Result:
        def fetchall(self):
            return []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, query, _params=None):
            observed.append(" ".join(str(query).split()))
            return Result()

    monkeypatch.setattr(entrypoint.psycopg, "connect", lambda *_args, **_kwargs: Connection())
    resolver = entrypoint.PostgresDispatchProviderBindingResolver(
        repository=_BindingRepository(_binding_task(agent=True)),
        now=lambda: datetime.fromisoformat("2026-07-28T00:05:00+00:00"),
    )

    assert resolver._candidate_rows() == []
    assert len(observed) == 1
    sql = observed[0]
    assert "d.result_storage_ref" in sql
    assert "d.status = 'submission_unknown'" in sql
    assert "d.result_storage_ref IS NOT NULL" in sql


def test_repository_claims_stored_submission_unknown_without_provider_replay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.modules import research_orchestration_repository as repository_module

    task = _binding_task(agent=True)
    payload = task.model_dump(mode="json", by_alias=True)
    payload["workUnits"][0].update({
        "status": "external_result_unknown",
        "terminalReasonCode": "producer_artifact_type_ambiguous",
    })
    task = ResearchTaskV12.model_validate(payload)
    decision = task.execution_decisions[0]
    work = task.work_units[0]
    started_at = datetime.fromisoformat("2026-07-28T00:03:00+00:00")
    candidate = {
        "dispatch_id": "dispatch-stored-unknown",
        "task_id": task.task_id,
        "execution_decision_id": decision.execution_decision_id,
        "work_unit_id": work.work_unit_id,
        "idempotency_key": work.idempotency_key,
        "observed_status": "submission_unknown",
        "observed_authorization_task_revision": 4,
        "submission_mode": "single_attempt_no_replay",
        "external_action_started_at": started_at,
        "result_storage_type": "external",
        "result_storage_scope": "minio+http://minio:9000/openair-artifacts",
        "result_storage_ref": "durable/research-dispatch/dispatch-stored-unknown/result.json",
        "owner_id": "owner",
        "workspace_id": "workspace",
        "observed_lease_owner": None,
        "observed_attempt_count": 2,
    }
    locked_row = {
        "dispatch_id": candidate["dispatch_id"],
        "task_id": candidate["task_id"],
        "execution_decision_id": candidate["execution_decision_id"],
        "work_unit_id": candidate["work_unit_id"],
        "idempotency_key": candidate["idempotency_key"],
        "status": "submission_unknown",
        "lease_live": False,
        "lease_owner": None,
        "attempt_count": 2,
        "authorization_task_revision": 4,
        "submission_mode": "single_attempt_no_replay",
        "external_action_started_at": started_at,
        "result_storage_type": candidate["result_storage_type"],
        "result_storage_scope": candidate["result_storage_scope"],
        "result_storage_ref": candidate["result_storage_ref"],
    }
    updated_row = {
        "dispatch_id": candidate["dispatch_id"],
        "task_id": candidate["task_id"],
        "execution_decision_id": candidate["execution_decision_id"],
        "work_unit_id": candidate["work_unit_id"],
        "idempotency_key": candidate["idempotency_key"],
        "lease_owner": "stored-result-recovery",
        "attempt_count": 3,
        "authorization_task_revision": 4,
        "submission_mode": "single_attempt_no_replay",
        "external_action_started_at": started_at,
        "result_storage_type": candidate["result_storage_type"],
        "result_storage_scope": candidate["result_storage_scope"],
        "result_storage_ref": candidate["result_storage_ref"],
    }

    class Cursor:
        def __init__(self) -> None:
            self.mode = ""
            self.rowcount = 0

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, query, _params=None):
            sql = " ".join(str(query).split())
            self.rowcount = 0
            if "d.status AS observed_status" in sql:
                self.mode = "candidates"
            elif sql.startswith("SELECT *, lease_expires_at > clock_timestamp() AS lease_live"):
                self.mode = "locked"
            elif sql.startswith("UPDATE openair_dispatch SET status = 'submission_authorized'"):
                self.mode = "updated"
                self.rowcount = 1
            else:
                self.mode = "other"
            return self

        def fetchall(self):
            return [candidate] if self.mode == "candidates" else []

        def fetchone(self):
            if self.mode == "locked":
                return locked_row
            if self.mode == "updated":
                return updated_row
            return None

    cursor = Cursor()

    class Connection:
        def cursor(self):
            return cursor

    class Uow:
        @contextmanager
        def transaction(self):
            yield Connection()

    repository = ResearchOrchestrationRepository.__new__(ResearchOrchestrationRepository)
    repository._uow = Uow()
    repository._settings = type("Settings", (), {"execution_namespace": "workspace-test"})()
    repository._require_write_enabled = lambda: None
    repository._require_postgres = lambda: None
    repository._load_locked_task = lambda *_args, **_kwargs: (
        task,
        task.model_dump(mode="json", by_alias=True),
    )
    monkeypatch.setattr(
        repository_module.ControllerCommandRepository,
        "restore_canonical_execution_context",
        staticmethod(lambda _cursor, **kwargs: kwargs["work_unit_payload"]),
    )

    recovered = repository.claim_authorized_recovery(
        lease_owner="stored-result-recovery",
        lease_seconds=60,
        dispatch_id=candidate["dispatch_id"],
        task_id=task.task_id,
        owner_id="owner",
        workspace_id="workspace",
        execution_namespace="workspace-test",
    )

    assert len(recovered) == 1
    fence = recovered[0]
    assert fence.result_recovery_from_unknown is True
    assert fence.result_storage_ref == candidate["result_storage_ref"]
    assert fence.attempt_count == 3
    assert fence.work_unit_payload["workUnitId"] == work.work_unit_id


def test_binding_resolver_accepts_stored_submission_unknown_only_for_providerless_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from worker import entrypoint

    task = _binding_task(agent=True)
    payload = task.model_dump(mode="json", by_alias=True)
    payload["workUnits"][0].update({
        "status": "external_result_unknown",
        "terminalReasonCode": "producer_artifact_type_ambiguous",
    })
    payload["capabilityCalls"][0].update({
        "status": "external_result_unknown",
        "finishedAt": "2026-07-28T00:04:00Z",
    })
    task = ResearchTaskV12.model_validate(payload)
    repository = _BindingRepository(task)
    row = {
        **_dispatch_candidate(task),
        "status": "submission_unknown",
        "submission_mode": "single_attempt_no_replay",
        "external_action_started_at": datetime.fromisoformat("2026-07-28T00:03:00+00:00"),
        "result_storage_ref": "durable/research-dispatch/dispatch-cold-start/result.json",
        "attempt_count": 2,
        "next_attempt_at": None,
        "updated_at": datetime.fromisoformat("2026-07-28T00:04:00+00:00"),
    }
    resolver = entrypoint.PostgresDispatchProviderBindingResolver(
        repository=repository,
        now=lambda: datetime.fromisoformat("2026-07-28T00:05:00+00:00"),
    )
    resolver._candidate_rows = lambda: [row]
    monkeypatch.setattr(entrypoint.settings, "research_orchestration_v1", True)
    monkeypatch.setattr(entrypoint.settings, "persistence_backend", "postgres")

    binding = resolver.resolve()

    assert binding is not None
    assert binding.dispatch_id == row["dispatch_id"]
    assert binding.providerless_recovery is True
    assert binding.provider_id is None
    # The providerless branch returns before loading Registry/provider runtime:
    # recovery consumes only the already persisted durable result.
    assert repository.snapshot == {}


def test_binding_resolver_recovers_stored_result_after_decision_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A closed decision must not erase a result already tied to its WorkUnit."""
    from worker import entrypoint

    task = _binding_task(agent=True)
    payload = task.model_dump(mode="json", by_alias=True)
    payload["executionDecisions"][0].update({
        "status": "closed",
        "outcome": "succeeded",
        "closedAt": "2026-07-28T00:04:00Z",
    })
    payload["workUnits"][0]["status"] = "completed"
    payload["capabilityCalls"][0].update({
        "status": "succeeded",
        "finishedAt": "2026-07-28T00:04:00Z",
    })
    task = ResearchTaskV12.model_validate(payload)
    repository = _BindingRepository(task)
    row = {
        **_dispatch_candidate(task),
        "status": "submission_authorized",
        "submission_mode": "single_attempt_no_replay",
        "external_action_started_at": datetime.fromisoformat("2026-07-28T00:03:00+00:00"),
        "result_storage_ref": "durable/research-dispatch/dispatch-closed/result.json",
        "attempt_count": 2,
        "next_attempt_at": None,
        "updated_at": datetime.fromisoformat("2026-07-28T00:04:00+00:00"),
    }
    resolver = entrypoint.PostgresDispatchProviderBindingResolver(
        repository=repository,
        now=lambda: datetime.fromisoformat("2026-07-28T00:05:00+00:00"),
    )
    resolver._candidate_rows = lambda: [row]
    monkeypatch.setattr(entrypoint.settings, "research_orchestration_v1", True)
    monkeypatch.setattr(entrypoint.settings, "persistence_backend", "postgres")

    binding = resolver.resolve()

    assert binding is not None
    assert binding.providerless_recovery is True
    assert binding.provider_id is None
    assert repository.snapshot == {}


def test_production_binding_resolver_is_cold_start_durable_for_exact_agent_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from worker import entrypoint

    task = _binding_task(agent=True)
    snapshot = {
        "definitions": {
            "agents": [
                {
                    "agentId": "agent-cold-start",
                    "definitionVersion": 1,
                    "enabled": True,
                    "defaultProvider": "provider-safe",
                    "allowedProviders": ["provider-safe"],
                }
            ]
        },
        "bindings": {
            "agents": [
                {
                    "bindingId": "binding-agent-cold-start",
                    "bindingVersion": 1,
                    "agentId": "agent-cold-start",
                    "agentDefinitionVersion": 1,
                    "enabled": True,
                }
            ]
        },
    }
    repository = _BindingRepository(task, snapshot)
    monkeypatch.setattr(entrypoint.settings, "research_orchestration_v1", True)
    monkeypatch.setattr(entrypoint.settings, "persistence_backend", "postgres")
    now = lambda: datetime.fromisoformat("2026-07-28T00:04:00+00:00")

    first = entrypoint.PostgresDispatchProviderBindingResolver(repository=repository, now=now)
    first._candidate_rows = lambda: [_dispatch_candidate(task)]
    second = entrypoint.PostgresDispatchProviderBindingResolver(repository=repository, now=now)
    second._candidate_rows = lambda: [_dispatch_candidate(task)]

    first_binding = first.resolve()
    second_binding = second.resolve()

    assert first_binding is not None
    assert second_binding == first_binding
    assert first_binding.provider_id == "provider-safe"
    assert first_binding.expected_task_revision == task.revision


def test_production_binding_resolver_revalidates_live_agent_after_frozen_readiness_expiry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from worker import entrypoint

    task = _binding_task(agent=True)
    snapshot = {
        "definitions": {
            "agents": [
                {
                    "agentId": "agent-cold-start",
                    "definitionVersion": 1,
                    "enabled": True,
                    "defaultProvider": "provider-safe",
                    "allowedProviders": ["provider-safe"],
                }
            ]
        },
        "bindings": {
            "agents": [
                {
                    "bindingId": "binding-agent-cold-start",
                    "bindingVersion": 1,
                    "agentId": "agent-cold-start",
                    "agentDefinitionVersion": 1,
                    "enabled": True,
                }
            ]
        },
    }
    resolver = entrypoint.PostgresDispatchProviderBindingResolver(
        repository=_BindingRepository(task, snapshot),
        now=lambda: datetime.fromisoformat("2026-07-28T00:06:00+00:00"),
    )
    resolver._candidate_rows = lambda: [_dispatch_candidate(task)]
    monkeypatch.setattr(entrypoint.settings, "research_orchestration_v1", True)
    monkeypatch.setattr(entrypoint.settings, "persistence_backend", "postgres")

    binding = resolver.resolve()

    assert binding is not None
    assert binding.provider_id == "provider-safe"


def test_production_binding_resolver_recovers_started_single_attempt_after_readiness_expiry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from worker import entrypoint

    task = _binding_task(agent=True)

    class RecoveryRepository(_BindingRepository):
        def load_registry_snapshot(self, _content_hash):
            raise AssertionError("providerless recovery must not require a registry snapshot")

    candidate = {
        **_dispatch_candidate(task),
        "status": "submission_authorized",
        "submission_mode": "single_attempt_no_replay",
        "external_action_started_at": datetime.fromisoformat("2026-07-28T00:04:30+00:00"),
    }
    resolver = entrypoint.PostgresDispatchProviderBindingResolver(
        repository=RecoveryRepository(task),
        now=lambda: datetime.fromisoformat("2026-07-28T00:06:00+00:00"),
    )
    resolver._candidate_rows = lambda: [candidate]
    monkeypatch.setattr(entrypoint.settings, "research_orchestration_v1", True)
    monkeypatch.setattr(entrypoint.settings, "persistence_backend", "postgres")

    binding = resolver.resolve()

    assert binding is not None
    assert binding.provider_id is None
    assert binding.providerless_recovery is True
    assert binding.dispatch_id == candidate["dispatch_id"]


def test_production_binding_resolver_fails_closed_for_skill_without_provider_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from worker import entrypoint

    task = _binding_task(agent=False)
    resolver = entrypoint.PostgresDispatchProviderBindingResolver(
        repository=_BindingRepository(task),
        now=lambda: datetime.fromisoformat("2026-07-28T00:04:00+00:00"),
    )
    resolver._candidate_rows = lambda: [_dispatch_candidate(task)]
    monkeypatch.setattr(entrypoint.settings, "research_orchestration_v1", True)
    monkeypatch.setattr(entrypoint.settings, "persistence_backend", "postgres")

    assert resolver.resolve() is None


def test_resolver_quarantines_32_poison_rows_before_valid_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from worker import entrypoint

    task = _binding_task(agent=True)
    snapshot = {
        "definitions": {
            "agents": [
                {
                    "agentId": "agent-cold-start",
                    "definitionVersion": 1,
                    "enabled": True,
                    "defaultProvider": "provider-safe",
                    "allowedProviders": ["provider-safe"],
                }
            ]
        },
        "bindings": {
            "agents": [
                {
                    "bindingId": "binding-agent-cold-start",
                    "bindingVersion": 1,
                    "agentId": "agent-cold-start",
                    "agentDefinitionVersion": 1,
                    "enabled": True,
                }
            ]
        },
    }

    class Repository(_BindingRepository):
        def __init__(self):
            super().__init__(task, snapshot)
            self.failures: list[dict] = []

        def record_dispatch_resolution_failure(self, **kwargs):
            self.failures.append(kwargs)
            return "retry_scheduled"

    repository = Repository()
    valid = {**_dispatch_candidate(task), "attempt_count": 0}
    poison = [
        {
            **valid,
            "dispatch_id": f"dispatch-poison-{index}",
            "revision": task.revision + 1,
        }
        for index in range(32)
    ]
    resolver = entrypoint.PostgresDispatchProviderBindingResolver(
        repository=repository,
        now=lambda: datetime.fromisoformat("2026-07-28T00:04:00+00:00"),
    )
    resolver._candidate_rows = lambda: [*poison, valid]
    monkeypatch.setattr(entrypoint.settings, "research_orchestration_v1", True)
    monkeypatch.setattr(entrypoint.settings, "persistence_backend", "postgres")

    binding = resolver.resolve()

    assert binding is not None
    assert binding.dispatch_id == valid["dispatch_id"]
    assert len(repository.failures) == 32
    assert all(row["error_code"] == "dispatch_task_revision_changed" for row in repository.failures)
    assert all(row["retryable"] is True for row in repository.failures)


def _missing_work_unit_resolution_repository(
    task: ResearchTaskV12,
    dispatch_row: dict,
) -> tuple[ResearchOrchestrationRepository, object, list[dict]]:
    class Cursor:
        def __init__(self) -> None:
            self.fetches = [dispatch_row]
            self.rowcount = 0
            self.executed: list[tuple[str, object]] = []

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, query, params=None):
            sql = str(query)
            self.executed.append((sql, params))
            if "UPDATE openair_dispatch" in sql:
                self.rowcount = 1
                self.fetches.append({"dispatch_id": dispatch_row["dispatch_id"]})
            return self

        def fetchone(self):
            return self.fetches.pop(0) if self.fetches else None

    cursor = Cursor()

    class Connection:
        def cursor(self):
            return cursor

    class Uow:
        @contextmanager
        def transaction(self):
            yield Connection()

    task_writes: list[dict] = []
    repository = ResearchOrchestrationRepository.__new__(ResearchOrchestrationRepository)
    repository._uow = Uow()
    repository._require_write_enabled = lambda: None
    repository._require_postgres = lambda: None
    repository._require_expected_revision = lambda _revision: None
    repository._load_locked_task = lambda *_args, **_kwargs: (
        task,
        task.model_dump(mode="json", by_alias=True),
    )
    repository._write_locked_task = lambda *_args, **kwargs: task_writes.append(
        kwargs["payload_patch"]
    )
    return repository, cursor, task_writes


def test_missing_work_unit_before_action_start_is_durably_quarantined() -> None:
    task = _binding_task(agent=True)
    decision = task.execution_decisions[0]
    work_unit = task.work_units[0]
    dispatch_row = {
        "dispatch_id": "dispatch-missing-pre-start",
        "task_id": task.task_id,
        "execution_decision_id": decision.execution_decision_id,
        "work_unit_id": "work-unit-missing-pre-start",
        "idempotency_key": work_unit.idempotency_key,
        "status": "pending",
        "external_action_started_at": None,
        "attempt_count": 0,
        "authorization_task_revision": None,
        "lease_owner": None,
        "lease_expires_at": None,
        "lease_expired": False,
    }
    repository, cursor, task_writes = _missing_work_unit_resolution_repository(
        task, dispatch_row
    )

    result = repository.record_dispatch_resolution_failure(
        dispatch_id=dispatch_row["dispatch_id"],
        task_id=task.task_id,
        execution_decision_id=decision.execution_decision_id,
        work_unit_id=dispatch_row["work_unit_id"],
        idempotency_key=work_unit.idempotency_key,
        owner_id="owner",
        workspace_id="workspace",
        expected_task_revision=task.revision,
        error_code="dispatch_contract_mismatch",
        retryable=False,
        max_attempts=2,
        retry_delay_seconds=1,
    )

    assert result == "failed"
    update_sql = next(sql for sql, _ in cursor.executed if "UPDATE openair_dispatch" in sql)
    assert "SET status = 'failed'" in update_sql
    blocker = task_writes[0]["executionIntegrityBlockers"][0]
    assert blocker["reasonCode"] == "dispatch_work_unit_missing"
    assert blocker["status"] == "blocked_recoverable"
    assert blocker["allowedActions"] == ["rebuild_execution_assignment"]


def test_missing_work_unit_poison_does_not_block_next_valid_resolver_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from worker import entrypoint

    task = _binding_task(agent=True)
    snapshot = {
        "definitions": {
            "agents": [
                {
                    "agentId": "agent-cold-start",
                    "definitionVersion": 1,
                    "enabled": True,
                    "defaultProvider": "provider-safe",
                    "allowedProviders": ["provider-safe"],
                }
            ]
        },
        "bindings": {
            "agents": [
                {
                    "bindingId": "binding-agent-cold-start",
                    "bindingVersion": 1,
                    "agentId": "agent-cold-start",
                    "agentDefinitionVersion": 1,
                    "enabled": True,
                }
            ]
        },
    }

    class Repository(_BindingRepository):
        def __init__(self):
            super().__init__(task, snapshot)
            self.failures: list[dict] = []

        def record_dispatch_resolution_failure(self, **kwargs):
            self.failures.append(kwargs)
            return "submission_unknown"

    repository = Repository()
    valid = {**_dispatch_candidate(task), "attempt_count": 0}
    poison = {
        **valid,
        "dispatch_id": "dispatch-poison-missing-work-unit",
        "work_unit_id": "work-unit-missing-from-task",
        "status": "submission_authorized",
        "external_action_started_at": datetime.now(timezone.utc),
        "attempt_count": 1,
    }
    resolver = entrypoint.PostgresDispatchProviderBindingResolver(repository=repository)
    resolver._candidate_rows = lambda: [poison, valid]
    monkeypatch.setattr(entrypoint.settings, "research_orchestration_v1", True)
    monkeypatch.setattr(entrypoint.settings, "persistence_backend", "postgres")

    binding = resolver.resolve()

    assert binding is not None
    assert binding.dispatch_id == valid["dispatch_id"]
    assert len(repository.failures) == 1
    assert repository.failures[0]["error_code"] == "dispatch_contract_mismatch"


def test_missing_work_unit_settlement_database_failure_remains_process_fatal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from psycopg import OperationalError
    from worker import entrypoint

    task = _binding_task(agent=True)

    class Repository(_BindingRepository):
        def __init__(self):
            super().__init__(task)
            self.settlement_calls = 0

        def record_dispatch_resolution_failure(self, **_kwargs):
            self.settlement_calls += 1
            raise OperationalError("postgres authority unavailable")

    repository = Repository()
    poison = {
        **_dispatch_candidate(task),
        "work_unit_id": "work-unit-missing-from-task",
        "attempt_count": 0,
    }
    resolver = entrypoint.PostgresDispatchProviderBindingResolver(repository=repository)
    resolver._candidate_rows = lambda: [poison]
    monkeypatch.setattr(entrypoint.settings, "research_orchestration_v1", True)
    monkeypatch.setattr(entrypoint.settings, "persistence_backend", "postgres")

    with pytest.raises(OperationalError):
        resolver.resolve()
    assert repository.settlement_calls == 1


def test_missing_work_unit_settlement_is_not_invoked_twice_after_integrity_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from worker import entrypoint

    task = _binding_task(agent=True)

    class Repository(_BindingRepository):
        def __init__(self):
            super().__init__(task)
            self.settlement_calls = 0

        def record_dispatch_resolution_failure(self, **_kwargs):
            self.settlement_calls += 1
            raise ResearchOrchestrationPersistenceError("dispatch_work_unit_missing")

    repository = Repository()
    poison = {
        **_dispatch_candidate(task),
        "work_unit_id": "work-unit-missing-from-task",
        "attempt_count": 0,
    }
    resolver = entrypoint.PostgresDispatchProviderBindingResolver(repository=repository)
    resolver._candidate_rows = lambda: [poison]
    monkeypatch.setattr(entrypoint.settings, "research_orchestration_v1", True)
    monkeypatch.setattr(entrypoint.settings, "persistence_backend", "postgres")

    assert resolver.resolve() is None
    assert repository.settlement_calls == 1



def test_production_binding_resolver_accepts_v2_planning_execution_snapshot_lineage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from worker import entrypoint
    from app.modules.registry_snapshot_repository import registry_envelope_ref
    from app.modules.research_orchestration_resolver import (
        build_execution_registry_snapshot,
        build_planning_registry_snapshot,
    )

    task = _binding_task(agent=True)
    planning = build_planning_registry_snapshot().envelope
    definition = planning["definitions"]["substages"][0]
    execution = build_execution_registry_snapshot(
        planning,
        required_definition_refs=((
            definition["definitionId"],
            definition["definitionVersion"],
        ),),
    ).envelope

    planning_ref = registry_envelope_ref(planning)
    execution_ref = registry_envelope_ref(execution)
    payload = task.model_dump(mode="json", by_alias=True)
    payload["registrySnapshotRef"] = planning_ref
    payload["executionDecisions"][0]["registrySnapshotRef"] = execution_ref
    payload["workUnits"][0]["registrySnapshotRef"] = execution_ref
    task = ResearchTaskV12.model_validate(payload)

    repository = _BindingRepository(task, execution)
    monkeypatch.setattr(entrypoint.settings, "research_orchestration_v1", True)
    monkeypatch.setattr(entrypoint.settings, "persistence_backend", "postgres")
    now = lambda: datetime.fromisoformat("2026-09-17T13:40:00+00:00")
    row = {
        **_dispatch_candidate(task),
        "status": "submission_authorized",
        "submission_mode": "single_attempt_no_replay",
        "external_action_started_at": now(),
    }

    resolver = entrypoint.PostgresDispatchProviderBindingResolver(
        repository=repository,
        now=now,
    )
    resolver._candidate_rows = lambda: [row]

    binding = resolver.resolve()

    assert binding is not None
    assert binding.providerless_recovery is True
    assert binding.registry_snapshot_ref == execution_ref
    assert task.registry_snapshot_ref.model_dump(mode="json", by_alias=True) == planning_ref
    assert task.registry_snapshot_ref != task.execution_decisions[0].registry_snapshot_ref



def test_production_binding_resolver_v2_new_dispatch_validates_snapshot_lineage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from worker import entrypoint

    task = _binding_task(agent=True)
    payload = task.model_dump(mode="json", by_alias=True)
    planning_ref = {"schemaVersion": 2, "contentHash": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}
    execution_ref = {"schemaVersion": 2, "contentHash": "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}
    payload["registrySnapshotRef"] = planning_ref
    payload["executionDecisions"][0]["registrySnapshotRef"] = execution_ref
    payload["workUnits"][0]["registrySnapshotRef"] = execution_ref
    task = ResearchTaskV12.model_validate(payload)

    snapshot = {
        "scope": {"kind": "execution", "planningSnapshotRef": planning_ref},
        "definitions": {
            "agents": [
                {
                    "agentId": "agent-cold-start",
                    "definitionVersion": 1,
                    "enabled": True,
                    "defaultProvider": "provider-safe",
                    "allowedProviders": ["provider-safe"],
                }
            ]
        },
        "bindings": {
            "agents": [
                {
                    "bindingId": "binding-agent-cold-start",
                    "bindingVersion": 1,
                    "agentId": "agent-cold-start",
                    "agentDefinitionVersion": 1,
                    "enabled": True,
                }
            ]
        },
    }
    seen = {}

    def matches(planning, execution, envelope):
        seen["planning"] = planning
        seen["execution"] = execution
        seen["envelope"] = envelope
        return True

    monkeypatch.setattr(entrypoint, "execution_snapshot_matches_planning", matches)
    monkeypatch.setattr(entrypoint.settings, "research_orchestration_v1", True)
    monkeypatch.setattr(entrypoint.settings, "persistence_backend", "postgres")
    resolver = entrypoint.PostgresDispatchProviderBindingResolver(
        repository=_BindingRepository(task, snapshot),
        now=lambda: datetime.fromisoformat("2026-09-17T13:40:00+00:00"),
    )
    resolver._candidate_rows = lambda: [_dispatch_candidate(task)]

    binding = resolver.resolve()

    assert binding is not None
    assert binding.provider_id == "provider-safe"
    assert seen == {
        "planning": planning_ref,
        "execution": execution_ref,
        "envelope": snapshot,
    }


def test_production_binding_resolver_v2_providerless_recovery_does_not_reopen_registry_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from worker import entrypoint

    task = _binding_task(agent=True)
    payload = task.model_dump(mode="json", by_alias=True)
    planning_ref = {"schemaVersion": 2, "contentHash": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}
    execution_ref = {"schemaVersion": 2, "contentHash": "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}
    payload["registrySnapshotRef"] = planning_ref
    payload["executionDecisions"][0]["registrySnapshotRef"] = execution_ref
    payload["workUnits"][0]["registrySnapshotRef"] = execution_ref
    task = ResearchTaskV12.model_validate(payload)

    class RecoveryRepository(_BindingRepository):
        def load_registry_snapshot(self, _content_hash):
            raise AssertionError("providerless recovery must not reopen registry authority")

    row = {
        **_dispatch_candidate(task),
        "status": "submission_authorized",
        "submission_mode": "single_attempt_no_replay",
        "external_action_started_at": datetime.fromisoformat("2026-09-17T13:40:00+00:00"),
    }
    monkeypatch.setattr(entrypoint.settings, "research_orchestration_v1", True)
    monkeypatch.setattr(entrypoint.settings, "persistence_backend", "postgres")
    resolver = entrypoint.PostgresDispatchProviderBindingResolver(
        repository=RecoveryRepository(task),
        now=lambda: datetime.fromisoformat("2026-09-17T13:41:00+00:00"),
    )
    resolver._candidate_rows = lambda: [row]

    binding = resolver.resolve()

    assert binding is not None
    assert binding.provider_id is None
    assert binding.providerless_recovery is True
    assert binding.registry_snapshot_ref == execution_ref
