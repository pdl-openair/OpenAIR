"""One-shot workers for claimed and recoverable physical research dispatches."""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from functools import partial
from typing import Any, Mapping

from app.execution.research_dispatch_context import submission_request_from_authorized
from app.execution.research_dispatch_provider import (
    DispatchProviderKnownRejected,
    DispatchProviderPermanentPreflightError,
    DispatchProviderResultUnknown,
    DispatchProviderRetryablePreflightError,
    DispatchSubmissionCompleted,
    DispatchSubmissionRequest,
    PreparedDispatchProvider,
    ResearchDispatchProvider,
    require_safe_submission,
)
from app.execution.research_internal_loop_pending import DispatchProviderInternalExecutionPending
from app.modules.artifact_write import ArtifactStorageError
from app.modules.research_dispatch_service import (
    DispatchContinuationRetryableError,
    ResearchDispatchService,
)
from app.modules.research_orchestration_repository import (
    AuthorizedDispatch,
    DispatchAuthorizationError,
    DispatchFence,
)

_logger = logging.getLogger("openair.research_dispatch")


def _dispatch_result_payload(result: DispatchSubmissionCompleted) -> bytes:
    payload = {
        "title": result.title,
        "mimeType": result.mime_type,
        "content": result.content,
        "providerId": result.provider_id,
        "adapter": result.adapter,
        "usage": dict(result.usage) if result.usage is not None else None,
        "externalActionEvidence": [
            dict(item) for item in result.external_action_evidence
        ],
        "model": result.model,
        "resultProtocol": dict(result.result_protocol),
        "warnings": list(result.warnings),
    }
    return json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _dispatch_result_from_payload(payload: bytes) -> DispatchSubmissionCompleted:
    body = json.loads(payload.decode("utf-8"))
    if not isinstance(body, dict):
        raise ValueError("staged dispatch result must be an object")
    return DispatchSubmissionCompleted(
        title=body["title"],
        mime_type=body["mimeType"],
        content=body["content"],
        provider_id=body["providerId"],
        adapter=body["adapter"],
        usage=body.get("usage"),
        external_action_evidence=tuple(body.get("externalActionEvidence") or ()),
        model=body.get("model"),
        result_protocol=(
            body.get("resultProtocol")
            if isinstance(body.get("resultProtocol"), Mapping)
            else {}
        ),
        warnings=tuple(body.get("warnings") or ()),
    )


def _record_unknown_execution(
    authorized: AuthorizedDispatch,
    *,
    prepared: PreparedDispatchProvider | None = None,
    owner_id: str,
    workspace_id: str,
    service: ResearchDispatchService,
    error_code: str,
    remote_run_id: str | None = None,
) -> "DispatchWorkerResult":
    """Persist uncertainty for observation/reconciliation without replay."""
    return _record_external_outcome(
        authorized,
        prepared=prepared,
        owner_id=owner_id,
        workspace_id=workspace_id,
        service=service,
        outcome="unknown",
        terminal_status="submission_unknown",
        remote_run_id=remote_run_id,
        error_code=error_code,
    )


@dataclass(frozen=True)
class DispatchWorkerResult:
    status: str
    recorded: bool
    error_code: str | None = None


def _continuation_pending(
    authorized: AuthorizedDispatch,
    error: DispatchContinuationRetryableError,
) -> DispatchWorkerResult:
    """Retain a post-result continuation failure without replaying the provider."""
    error_code = str(error) or "execution_continuation_not_committed"
    _logger.warning(
        "research_dispatch continuation pending dispatchId=%s taskId=%s reason=%s",
        authorized.dispatch_id,
        authorized.task_id,
        error_code,
    )
    return DispatchWorkerResult(
        status="retryable_error",
        recorded=False,
        error_code=error_code,
    )


class _AuthorizedDispatchLeaseKeeper:
    """Keep the durable Dispatch authorization live while Provider I/O is in flight.

    Provider execution may legitimately outlive the initial short queue lease.  A
    lease expiry is therefore not execution-state evidence and must not allow a
    second worker to reconcile a still-observed request as abandoned.
    """

    def __init__(
        self,
        service: ResearchDispatchService,
        fence: AuthorizedDispatch,
        *,
        lease_seconds: int,
    ) -> None:
        self._renew = getattr(service, "renew_authorized", None)
        self._fence = fence
        self._lease_seconds = max(1, int(lease_seconds))
        self._renew_interval = max(1.0, min(10.0, self._lease_seconds / 3.0))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.lost = False

    def __enter__(self) -> "_AuthorizedDispatchLeaseKeeper":
        if callable(self._renew):
            # Renew immediately so preflight/authorize latency cannot expire the
            # lease before the first periodic tick.
            if not self._try_renew():
                return self
            self._thread = threading.Thread(
                target=self._run,
                name=f"dispatch-lease-{self._fence.dispatch_id}",
                daemon=True,
            )
            self._thread.start()
        return self

    def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def _try_renew(self) -> bool:
        try:
            renewed = bool(
                self._renew(
                    self._fence,
                    lease_seconds=self._lease_seconds,
                )
            )
        except Exception:
            _logger.exception(
                "research_dispatch lease renewal failed dispatchId=%s taskId=%s",
                self._fence.dispatch_id,
                self._fence.task_id,
            )
            self.lost = True
            return False
        if not renewed:
            _logger.warning(
                "research_dispatch lease lost dispatchId=%s taskId=%s",
                self._fence.dispatch_id,
                self._fence.task_id,
            )
            self.lost = True
            return False
        return True

    def _run(self) -> None:
        while not self._stop.wait(self._renew_interval):
            if not self._try_renew():
                return


def _observe_openair_provider_execution(
    authorized: AuthorizedDispatch,
):
    """Read durable OpenAIR Provider-attempt evidence for recovery.

    This is deliberately read-only.  Failure to observe is itself insufficient
    authority to replay or to claim a terminal Provider state.
    """
    try:
        from app.modules.provider_transport_evidence import ProviderHttpAttemptRepository

        return ProviderHttpAttemptRepository().observe_latest(
            parent_kind="capability_call",
            parent_id=authorized.external_attempt.capability_call_id,
            operation_identity=f"provider_submission:{authorized.dispatch_id}",
        )
    except Exception:
        _logger.exception(
            "research_dispatch provider observation unavailable dispatchId=%s taskId=%s",
            authorized.dispatch_id,
            authorized.task_id,
        )
        return None


def _latest_task_revision(
    service: ResearchDispatchService,
    *,
    task_id: str,
    owner_id: str,
    workspace_id: str,
) -> int | None:
    """Best-effort reload of task.revision for authorize retry after mismatch."""
    load = getattr(service, "load", None)
    if not callable(load):
        repository = getattr(service, "_repository", None)
        load = getattr(repository, "load", None) if repository is not None else None
    if not callable(load):
        return None
    task = load(task_id=task_id, owner_id=owner_id, workspace_id=workspace_id)
    if task is None:
        return None
    revision = getattr(task, "revision", None)
    if revision is None and isinstance(task, Mapping):
        revision = task.get("revision")
    try:
        return int(revision) if revision is not None else None
    except (TypeError, ValueError):
        return None


def process_dispatch_claim(
    fence: DispatchFence,
    *,
    owner_id: str,
    workspace_id: str,
    expected_task_revision: int,
    provider: ResearchDispatchProvider,
    service: ResearchDispatchService,
    lease_seconds: int = 30,
) -> DispatchWorkerResult:
    """Preflight, authorize, submit once, and persist the resulting external fact."""
    # Quota gate (S03-14-03): refuse to start a provider call when the owner has
    # no remaining quota. Mark the dispatch failed with a structured code instead
    # of consuming model resources.
    try:
        from app.modules.quota_service import QuotaExceeded, get_quota_service

        get_quota_service().check_and_gate(owner_id)
    except QuotaExceeded:
        recorded = service.fail_unsubmitted(
            fence,
            owner_id=owner_id,
            workspace_id=workspace_id,
            error_code="quota_exhausted",
        )
        return _log_dispatch_result(
            fence,
            _terminal_result("failed", recorded),
            error_code="quota_exhausted",
        )
    try:
        prepared = provider.preflight()
        require_safe_submission(prepared.capabilities)
        _prepared_provider_identity(prepared)
    except DispatchProviderPermanentPreflightError as exc:
        recorded = service.fail_unsubmitted(
            fence,
            owner_id=owner_id,
            workspace_id=workspace_id,
            error_code=exc.code,
        )
        return _log_dispatch_result(
            fence,
            _terminal_result("failed", recorded),
            error_code=exc.code,
        )
    except DispatchProviderRetryablePreflightError as exc:
        return _defer_claim(
            fence,
            owner_id=owner_id,
            workspace_id=workspace_id,
            service=service,
            error_code=exc.code,
        )
    except TimeoutError:
        return _defer_claim(
            fence,
            owner_id=owner_id,
            workspace_id=workspace_id,
            service=service,
            error_code="provider_preflight_timeout",
        )
    except OSError:
        return _defer_claim(
            fence,
            owner_id=owner_id,
            workspace_id=workspace_id,
            service=service,
            error_code="provider_preflight_io_error",
        )
    except Exception:
        return _defer_claim(
            fence,
            owner_id=owner_id,
            workspace_id=workspace_id,
            service=service,
            error_code="provider_preflight_internal_error",
        )

    # Wartime: continuation may bump task.revision between claim binding and
    # authorize. Retry once/few times with the latest revision in-process so
    # the user does not eat a full poll sleep as a node gap.
    revision = int(expected_task_revision)
    authorized: AuthorizedDispatch | None = None
    for attempt in range(3):
        try:
            authorized = service.authorize(
                fence,
                owner_id=owner_id,
                workspace_id=workspace_id,
                expected_task_revision=revision,
                capabilities=prepared.capabilities,
                lease_seconds=lease_seconds,
            )
            break
        except DispatchAuthorizationError as exc:
            if "task_revision_mismatch" not in str(exc) or attempt >= 2:
                raise
            latest = _latest_task_revision(
                service,
                task_id=fence.task_id,
                owner_id=owner_id,
                workspace_id=workspace_id,
            )
            if latest is None or latest == revision:
                raise
            revision = latest
    if authorized is None:
        return DispatchWorkerResult(status="stale_fence", recorded=False)
    return _submit_authorized(
        authorized,
        prepared=prepared,
        owner_id=owner_id,
        workspace_id=workspace_id,
        service=service,
        lease_seconds=lease_seconds,
    )


def process_authorized_recovery(
    authorized: AuthorizedDispatch,
    *,
    owner_id: str,
    workspace_id: str,
    provider: ResearchDispatchProvider | None,
    service: ResearchDispatchService,
    lease_seconds: int = 30,
) -> DispatchWorkerResult:
    """Recover without replaying a provider that only supports one physical attempt."""
    checkpoint_reader = getattr(service, "get_external_execution_checkpoint", None)
    checkpoint = None
    if authorized.submission_mode == "single_attempt_no_replay" and callable(
        checkpoint_reader
    ):
        try:
            checkpoint = checkpoint_reader(
                authorized,
                owner_id=owner_id,
                workspace_id=workspace_id,
            )
        except Exception:
            _logger.exception(
                "research_dispatch staged result validation failed dispatchId=%s",
                authorized.dispatch_id,
            )
            # Reading or decoding the local durable checkpoint is a local
            # recovery failure.  It does not make the already-started external
            # execution unknown and must not overwrite its external outcome.
            return DispatchWorkerResult(
                status="retryable_error",
                recorded=False,
                error_code="staged_external_result_invalid",
            )
    if checkpoint is not None:
        if checkpoint.phase == "canonical_completed":
            return DispatchWorkerResult(status="completed", recorded=True)
        if (
            checkpoint.phase == "response_received"
            and checkpoint.response_kind == "dispatch_completed"
            and checkpoint.response_payload is not None
        ):
            try:
                result = _dispatch_result_from_payload(checkpoint.response_payload)
                recorded = service.record_completed_result(
                    authorized,
                    owner_id=owner_id,
                    workspace_id=workspace_id,
                    result=result,
                )
            except DispatchContinuationRetryableError as exc:
                return _continuation_pending(authorized, exc)
            except Exception:
                _logger.exception(
                    "research_dispatch staged result finalization failed dispatchId=%s",
                    authorized.dispatch_id,
                )
                # response_received is the durable Provider-completed fact.
                # Keep this as a local retry and never downgrade or replay it.
                return DispatchWorkerResult(
                    status="retryable_error",
                    recorded=False,
                    error_code="staged_external_result_invalid",
                )
            return _terminal_result("completed", recorded)
        if checkpoint.phase == "submitted":
            return _record_unknown_execution(
                authorized,
                owner_id=owner_id,
                workspace_id=workspace_id,
                service=service,
                error_code="external_execution_result_unavailable",
            )
        if checkpoint.phase in {"prepared", "response_received"}:
            if provider is None:
                return DispatchWorkerResult(
                    status="retryable_error",
                    recorded=False,
                    error_code="dispatch_provider_unavailable",
                )
            try:
                prepared = provider.preflight()
                require_safe_submission(prepared.capabilities)
                _prepared_provider_identity(prepared)
            except Exception:
                return DispatchWorkerResult(
                    status="retryable_error",
                    recorded=False,
                    error_code="dispatch_provider_unavailable",
                )
            return _submit_authorized(
                authorized,
                prepared=prepared,
                owner_id=owner_id,
                workspace_id=workspace_id,
                service=service,
                lease_seconds=lease_seconds,
            )
    if (
        authorized.submission_mode == "single_attempt_no_replay"
        and authorized.external_action_started_at is not None
    ):
        try:
            recovered = service.recover_completed_result(
                authorized,
                owner_id=owner_id,
                workspace_id=workspace_id,
            )
        except DispatchContinuationRetryableError as exc:
            return _continuation_pending(authorized, exc)
        if recovered:
            return DispatchWorkerResult(status="completed", recorded=True)

        observation = _observe_openair_provider_execution(authorized)
        if observation is not None:
            execution_id = str(getattr(observation, "attempt_id", "") or "").strip() or None
            observed_state = str(getattr(observation, "state", "") or "").strip()
            transport_outcome = str(
                getattr(observation, "transport_outcome", "") or ""
            ).strip()
            if transport_outcome == "transport_not_submitted":
                return _record_external_outcome(
                    authorized,
                    owner_id=owner_id,
                    workspace_id=workspace_id,
                    service=service,
                    outcome="not_submitted",
                    terminal_status="failed",
                    error_code="provider_transport_not_submitted",
                )
            if observed_state == "failed":
                status = getattr(observation, "http_status", None)
                error_code = (
                    f"provider_http_{int(status)}"
                    if isinstance(status, int) and 100 <= status <= 599
                    else "provider_execution_known_failed"
                )
                return _record_external_outcome(
                    authorized,
                    owner_id=owner_id,
                    workspace_id=workspace_id,
                    service=service,
                    outcome="known_failed",
                    terminal_status="failed",
                    remote_run_id=execution_id,
                    error_code=error_code,
                )
            if observed_state == "completed":
                return _record_external_outcome(
                    authorized,
                    owner_id=owner_id,
                    workspace_id=workspace_id,
                    service=service,
                    outcome="completed",
                    terminal_status="submitted",
                    remote_run_id=execution_id,
                    error_code="external_execution_result_unavailable",
                )
            if observed_state == "running" or transport_outcome == "in_flight":
                return _record_external_outcome(
                    authorized,
                    owner_id=owner_id,
                    workspace_id=workspace_id,
                    service=service,
                    outcome="running",
                    terminal_status="submitted",
                    remote_run_id=execution_id,
                )
            if observed_state == "unknown":
                return _record_unknown_execution(
                    authorized,
                    owner_id=owner_id,
                    workspace_id=workspace_id,
                    service=service,
                    error_code="external_execution_result_unavailable",
                    remote_run_id=execution_id,
                )

        if authorized.result_storage_ref or provider is not None:
            return _record_unknown_execution(
                authorized,
                owner_id=owner_id,
                workspace_id=workspace_id,
                service=service,
                error_code="external_execution_result_unavailable",
            )
        return DispatchWorkerResult(
            status="retryable_error",
            recorded=False,
            error_code="providerless_execution_result_pending",
        )
    if provider is None:
        return _defer_authorized(
            authorized,
            owner_id=owner_id,
            workspace_id=workspace_id,
            service=service,
            error_code="dispatch_provider_unavailable",
        )
    try:
        prepared = provider.preflight()
        require_safe_submission(prepared.capabilities)
        _prepared_provider_identity(prepared)
    except DispatchProviderPermanentPreflightError as exc:
        if authorized.external_action_started_at is not None:
            return _defer_authorized(
                authorized,
                owner_id=owner_id,
                workspace_id=workspace_id,
                service=service,
                error_code=exc.code,
            )
        recorded = service.fail_authorized_unsubmitted(
            authorized,
            owner_id=owner_id,
            workspace_id=workspace_id,
            error_code=exc.code,
        )
        return _log_dispatch_result(
            authorized,
            _terminal_result("failed", recorded),
            error_code=exc.code,
        )
    except DispatchProviderRetryablePreflightError as exc:
        return _defer_authorized(
            authorized,
            owner_id=owner_id,
            workspace_id=workspace_id,
            service=service,
            error_code=exc.code,
        )
    except TimeoutError:
        return _defer_authorized(
            authorized,
            owner_id=owner_id,
            workspace_id=workspace_id,
            service=service,
            error_code="provider_preflight_timeout",
        )
    except OSError:
        return _defer_authorized(
            authorized,
            owner_id=owner_id,
            workspace_id=workspace_id,
            service=service,
            error_code="provider_preflight_io_error",
        )
    except Exception:
        return _defer_authorized(
            authorized,
            owner_id=owner_id,
            workspace_id=workspace_id,
            service=service,
            error_code="provider_preflight_internal_error",
        )

    if prepared.capabilities.submissionMode == "single_attempt_no_replay":
        if authorized.external_action_started_at is not None:
            return _record_unknown_execution(
                authorized,
                prepared=prepared,
                owner_id=owner_id,
                workspace_id=workspace_id,
                service=service,
                error_code="external_execution_result_unavailable",
            )
        authority = service.revalidate_recovery_submission(
            authorized,
            owner_id=owner_id,
            workspace_id=workspace_id,
        )
        if authority == "closed":
            return DispatchWorkerResult(status="failed", recorded=True)
        if authority != "ready":
            return DispatchWorkerResult(status="outcome_conflict", recorded=False)
        return _submit_authorized(
            authorized,
            prepared=prepared,
            owner_id=owner_id,
            workspace_id=workspace_id,
            service=service,
            lease_seconds=lease_seconds,
        )

    try:
        query = prepared.query_by_idempotency_key(authorized.idempotency_key)
        status = query.status
        if status not in {
            "accepted", "submitted", "running", "completed",
            "not_submitted", "known_rejected", "known_failed", "unknown",
        }:
            raise ValueError("invalid provider query result")
    except Exception:
        return _record_external_outcome(
            authorized,
            prepared=prepared,
            owner_id=owner_id,
            workspace_id=workspace_id,
            service=service,
            outcome="unknown",
            terminal_status="submission_unknown",
            error_code="provider_query_result_unknown",
        )

    if status in {"accepted", "submitted"}:
        return _record_external_outcome(
            authorized,
            prepared=prepared,
            owner_id=owner_id,
            workspace_id=workspace_id,
            service=service,
            outcome="accepted",
            terminal_status="submitted",
            remote_run_id=query.remote_run_id,
        )
    if status == "running":
        return _record_external_outcome(
            authorized,
            prepared=prepared,
            owner_id=owner_id,
            workspace_id=workspace_id,
            service=service,
            outcome="running",
            terminal_status="submitted",
            remote_run_id=query.remote_run_id,
        )
    if status == "completed":
        return _record_external_outcome(
            authorized,
            prepared=prepared,
            owner_id=owner_id,
            workspace_id=workspace_id,
            service=service,
            outcome="completed",
            terminal_status="submitted",
            remote_run_id=query.remote_run_id,
            error_code="external_execution_result_unavailable",
        )
    if status in {"known_rejected", "known_failed"}:
        return _record_external_outcome(
            authorized,
            prepared=prepared,
            owner_id=owner_id,
            workspace_id=workspace_id,
            service=service,
            outcome=("known_failed" if status == "known_failed" else "known_rejected"),
            terminal_status="failed",
            remote_run_id=query.remote_run_id,
            error_code=query.error_code,
        )
    if status == "unknown":
        return _record_external_outcome(
            authorized,
            prepared=prepared,
            owner_id=owner_id,
            workspace_id=workspace_id,
            service=service,
            outcome="unknown",
            terminal_status="submission_unknown",
            remote_run_id=query.remote_run_id,
            error_code=query.error_code,
        )
    if status == "not_submitted" and authorized.external_action_started_at is not None:
        return _record_external_outcome(
            authorized,
            prepared=prepared,
            owner_id=owner_id,
            workspace_id=workspace_id,
            service=service,
            outcome="not_submitted",
            terminal_status="failed",
            error_code="provider_query_not_submitted",
        )
    if authorized.external_action_started_at is not None:
        return _record_unknown_execution(
            authorized,
            prepared=prepared,
            owner_id=owner_id,
            workspace_id=workspace_id,
            service=service,
            error_code="external_execution_result_unavailable",
        )

    authority = service.revalidate_recovery_submission(
        authorized,
        owner_id=owner_id,
        workspace_id=workspace_id,
    )
    if authority == "closed":
        return DispatchWorkerResult(status="failed", recorded=True)
    if authority != "ready":
        return DispatchWorkerResult(status="outcome_conflict", recorded=False)
    return _submit_authorized(
        authorized,
        prepared=prepared,
        owner_id=owner_id,
        workspace_id=workspace_id,
        service=service,
        lease_seconds=lease_seconds,
    )


def _prepared_provider_identity(
    prepared: PreparedDispatchProvider,
) -> tuple[str, str]:
    provider_id = str(getattr(prepared, "provider_id", "") or "").strip()
    adapter = str(getattr(prepared, "adapter", "") or "").strip()
    if not provider_id or not adapter:
        raise ValueError("prepared provider identity is missing")
    return provider_id, adapter


def _submit_authorized(
    authorized: AuthorizedDispatch,
    *,
    prepared: PreparedDispatchProvider,
    owner_id: str,
    workspace_id: str,
    service: ResearchDispatchService,
    lease_seconds: int,
) -> DispatchWorkerResult:
    provider_id, adapter = _prepared_provider_identity(prepared)
    checkpoint = None
    prepare_checkpoint = getattr(service, "prepare_external_execution", None)
    if (
        authorized.submission_mode == "single_attempt_no_replay"
        and callable(prepare_checkpoint)
    ):
        request_identity = json.dumps(
            {
                "dispatchId": authorized.dispatch_id,
                "taskId": authorized.task_id,
                "executionDecisionId": authorized.execution_decision_id,
                "workUnitId": authorized.work_unit_id,
                "idempotencyKey": authorized.idempotency_key,
                "payload": authorized.work_unit_payload,
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        runtime = getattr(prepared, "runtime", None)
        runtime_kind = (
            str(runtime.get("runtimeKind") or "")
            if isinstance(runtime, Mapping)
            else ""
        )
        execution_kind = (
            runtime_kind
            if runtime_kind in {"external_adapter", "skill_script"}
            else "provider_http"
            if provider_id != "direct_skill_runtime" or runtime_kind == "llm_reasoning"
            else "direct_skill_runtime"
        )
        try:
            checkpoint = prepare_checkpoint(
                authorized,
                owner_id=owner_id,
                workspace_id=workspace_id,
                execution_kind=execution_kind,
                provider_id=provider_id,
                adapter=adapter,
                request_body=request_identity,
            )
        except Exception:
            _logger.exception(
                "research_dispatch checkpoint preparation failed dispatchId=%s",
                authorized.dispatch_id,
            )
            if authorized.external_action_started_at is not None:
                return _record_unknown_execution(
                    authorized,
                    prepared=prepared,
                    owner_id=owner_id,
                    workspace_id=workspace_id,
                    service=service,
                    error_code="external_execution_checkpoint_conflict",
                )
            recorded = service.fail_authorized_unsubmitted(
                authorized,
                owner_id=owner_id,
                workspace_id=workspace_id,
                error_code="external_execution_checkpoint_conflict",
            )
            return _terminal_result("failed", recorded)
    if authorized.external_action_started_at is None:
        started = service.mark_external_action_started(
            authorized,
            provider_id=provider_id,
            adapter=adapter,
            lease_seconds=lease_seconds,
        )
        if not started:
            return DispatchWorkerResult(status="outcome_conflict", recorded=False)
        if isinstance(started, AuthorizedDispatch):
            authorized = started
    request = submission_request_from_authorized(
        authorized,
        owner_id=owner_id,
        workspace_id=workspace_id,
    )
    payload = authorized.work_unit_payload if isinstance(authorized.work_unit_payload, Mapping) else {}
    executor_ref = payload.get("executorRef") if isinstance(payload.get("executorRef"), Mapping) else {}
    _logger.info(
        "research_dispatch submit_start dispatchId=%s taskId=%s workUnitId=%s "
        "capabilityCallId=%s agentId=%s executorType=%s substage=%s provider=%s",
        authorized.dispatch_id,
        authorized.task_id,
        authorized.work_unit_id,
        request.capability_call_id,
        executor_ref.get("executorId") or "",
        executor_ref.get("executorType") or "",
        payload.get("substageDefinitionId") or "",
        getattr(prepared, "provider_id", ""),
    )
    outcome_context = {
        "prepared": prepared,
        "owner_id": owner_id,
        "workspace_id": workspace_id,
        "service": service,
    }
    record_outcome = partial(_record_external_outcome, authorized, **outcome_context)
    lease_lost_during_submit = False
    try:
        with _AuthorizedDispatchLeaseKeeper(
            service, authorized, lease_seconds=lease_seconds
        ) as keeper:
            if keeper.lost:
                # Ownership already moved; do not start Provider I/O on a stale fence.
                return DispatchWorkerResult(
                    status="outcome_conflict",
                    recorded=False,
                    error_code="dispatch_lease_lost",
                )
            from app.modules.provider_transport_evidence import provider_execution_parent

            with provider_execution_parent(
                kind="capability_call",
                parent_id=request.capability_call_id,
                originating_authority="research_dispatch:provider_submit",
                only_if_absent=True,
            ):
                accepted = prepared.submit(request)
            lease_lost_during_submit = keeper.lost
    except DispatchProviderKnownRejected as exc:
        _logger.warning(
            "research_dispatch known_rejected dispatchId=%s taskId=%s workUnitId=%s "
            "capabilityCallId=%s error=%s",
            authorized.dispatch_id,
            authorized.task_id,
            authorized.work_unit_id,
            request.capability_call_id,
            exc.code,
        )
        _record_rejected_dispatch_usage(
            owner_id=owner_id,
            workspace_id=workspace_id,
            task_id=authorized.task_id,
            usage=getattr(exc, "usage", None),
            provider_id=prepared.provider_id if hasattr(prepared, "provider_id") else None,
        )
        return record_outcome(
            outcome="known_rejected",
            terminal_status="failed",
            error_code=exc.code,
            failure_detail=getattr(exc, "failure_detail", None),
        )
    except DispatchProviderInternalExecutionPending:
        return record_outcome(
            outcome="running",
            terminal_status="submitted",
        )
    except DispatchProviderResultUnknown as exc:
        _logger.warning(
            "research_dispatch result_unknown dispatchId=%s taskId=%s workUnitId=%s "
            "capabilityCallId=%s error=%s",
            authorized.dispatch_id,
            authorized.task_id,
            authorized.work_unit_id,
            request.capability_call_id,
            exc.code,
        )
        if authorized.submission_mode == "single_attempt_no_replay":
            return _record_unknown_execution(
                authorized,
                **outcome_context,
                error_code="external_execution_result_unavailable",
            )
        return record_outcome(
            outcome="unknown",
            terminal_status="submission_unknown",
            remote_run_id=getattr(exc, "remote_run_id", None), error_code=exc.code,
        )
    except Exception:
        # External ambiguity is a typed provider fact and must arrive as
        # DispatchProviderResultUnknown. Untyped exceptions are local adapter /
        # DTO / serialization failures; reporting them as external unknown would
        # discard a completed result and leave the user waiting without an action.
        _logger.exception(
            "research_dispatch local submit processing error dispatchId=%s taskId=%s workUnitId=%s "
            "capabilityCallId=%s",
            authorized.dispatch_id,
            authorized.task_id,
            authorized.work_unit_id,
            request.capability_call_id,
        )
        return record_outcome(
            outcome="known_rejected",
            terminal_status="failed",
            error_code="local_result_processing_failed",
            failure_detail={"failedStage": "dispatch_submit_local_processing"},
        )
    if lease_lost_during_submit:
        # Lost means renew failed (expiry or takeover). Still attempt the formal
        # owner+attempt write — record_* ignores lease_expires_at. Fence mismatch
        # becomes outcome_conflict without inventing a fresh unknown terminal.
        _logger.warning(
            "research_dispatch lease lost during provider submit dispatchId=%s "
            "taskId=%s; attempting ownership-fenced result write",
            authorized.dispatch_id,
            authorized.task_id,
        )
    if isinstance(accepted, DispatchSubmissionCompleted):
        stage_result = getattr(service, "stage_external_execution_result", None)
        if checkpoint is not None and callable(stage_result):
            try:
                stage_result(
                    authorized,
                    owner_id=owner_id,
                    workspace_id=workspace_id,
                    payload=_dispatch_result_payload(accepted),
                )
            except Exception:
                # The completed value is still in memory.  Do not discard it or
                # rewrite a local staging failure as an external result problem;
                # continue into the canonical result transaction.
                _logger.exception(
                    "research_dispatch result staging failed; continuing canonical write dispatchId=%s",
                    authorized.dispatch_id,
                )
        try:
            recorded = service.record_completed_result(
                authorized,
                owner_id=owner_id,
                workspace_id=workspace_id,
                result=accepted,
            )
        except ArtifactStorageError as exc:
            if checkpoint is None:
                return _record_external_outcome(
                    authorized,
                    prepared=prepared,
                    owner_id=owner_id,
                    workspace_id=workspace_id,
                    service=service,
                    outcome="completed",
                    terminal_status="submitted",
                    error_code=str(exc) or "artifact_storage_write_failed",
                )
            return DispatchWorkerResult(
                status="retryable_error",
                recorded=False,
                error_code=str(exc) or "artifact_storage_write_failed",
            )
        except DispatchContinuationRetryableError as exc:
            return _continuation_pending(authorized, exc)
        except Exception:
            _logger.exception(
                "research_dispatch local result persistence failed dispatchId=%s",
                authorized.dispatch_id,
            )
            return DispatchWorkerResult(
                status="retryable_error",
                recorded=False,
                error_code="local_result_persistence_failed",
            )
        _record_dispatch_usage(
            owner_id=owner_id,
            workspace_id=workspace_id,
            task_id=authorized.task_id,
            accepted=accepted,
        )
        return _terminal_result("completed", recorded)
    return _record_external_outcome(
        authorized,
        prepared=prepared,
        owner_id=owner_id,
        workspace_id=workspace_id,
        service=service,
        outcome="accepted",
        terminal_status="submitted",
        remote_run_id=accepted.remote_run_id,
    )


def _defer_claim(
    fence: DispatchFence,
    *,
    owner_id: str,
    workspace_id: str,
    service: ResearchDispatchService,
    error_code: str,
) -> DispatchWorkerResult:
    disposition = service.defer_unsubmitted(
        fence,
        owner_id=owner_id,
        workspace_id=workspace_id,
        error_code=error_code,
    )
    if disposition == "retry_scheduled":
        return DispatchWorkerResult(status="retry_scheduled", recorded=True)
    if disposition == "failed":
        return DispatchWorkerResult(status="failed", recorded=True)
    return DispatchWorkerResult(status="stale_fence", recorded=False)


def _defer_authorized(
    fence: AuthorizedDispatch,
    *,
    owner_id: str,
    workspace_id: str,
    service: ResearchDispatchService,
    error_code: str,
) -> DispatchWorkerResult:
    # Once the physical action-start fact exists, provider/preflight authority
    # loss is not a pre-submission retry.  Settle to an explicit reconcile state
    # while preserving external_action_started_at; never move back to pending.
    if fence.external_action_started_at is not None:
        return _record_unknown_execution(
            fence,
            owner_id=owner_id,
            workspace_id=workspace_id,
            service=service,
            error_code=error_code,
        )
    disposition = service.defer_authorized(
        fence,
        owner_id=owner_id,
        workspace_id=workspace_id,
        error_code=error_code,
    )
    if disposition == "retry_scheduled":
        return DispatchWorkerResult(status="retry_scheduled", recorded=True)
    if disposition == "failed":
        return DispatchWorkerResult(status="failed", recorded=True)
    if disposition == "submission_unknown":
        return DispatchWorkerResult(status="submission_unknown", recorded=True)
    return DispatchWorkerResult(status="stale_fence", recorded=False)


def _record_external_outcome(
    authorized: AuthorizedDispatch,
    *,
    prepared: PreparedDispatchProvider | None = None,
    owner_id: str,
    workspace_id: str,
    service: ResearchDispatchService,
    outcome: str,
    terminal_status: str,
    remote_run_id: str | None = None,
    error_code: str | None = None,
    failure_detail: Mapping[str, str] | None = None,
) -> DispatchWorkerResult:
    recorded = service.record_outcome(
        authorized,
        owner_id=owner_id,
        workspace_id=workspace_id,
        outcome=outcome,
        remote_run_id=remote_run_id,
        error_code=error_code,
        failure_detail=failure_detail,
        provider_id=(
            str(getattr(prepared, "provider_id", "") or "").strip()
            if prepared is not None
            else None
        ),
        adapter=(
            str(getattr(prepared, "adapter", "") or "").strip()
            if prepared is not None
            else None
        ),
    )
    return _log_dispatch_result(
        authorized,
        _terminal_result(terminal_status, recorded, error_code=error_code),
        error_code=error_code,
    )


def _terminal_result(
    status: str,
    recorded: bool,
    *,
    error_code: str | None = None,
) -> DispatchWorkerResult:
    if not recorded:
        return DispatchWorkerResult(status="outcome_conflict", recorded=False, error_code=error_code)
    return DispatchWorkerResult(status=status, recorded=True, error_code=error_code)


def _log_dispatch_result(
    fence_or_authorized: Any,
    result: DispatchWorkerResult,
    *,
    error_code: str | None = None,
) -> DispatchWorkerResult:
    code = error_code or result.error_code
    if result.status not in {"failed", "submission_unknown"}:
        return result if code is None or result.error_code == code else DispatchWorkerResult(
            status=result.status,
            recorded=result.recorded,
            error_code=code,
        )
    _logger.warning(
        "research_dispatch %s dispatchId=%s taskId=%s workUnitId=%s error=%s recorded=%s",
        result.status,
        getattr(fence_or_authorized, "dispatch_id", ""),
        getattr(fence_or_authorized, "task_id", ""),
        getattr(fence_or_authorized, "work_unit_id", ""),
        code or "",
        result.recorded,
    )
    if result.error_code == code:
        return result
    return DispatchWorkerResult(
        status=result.status,
        recorded=result.recorded,
        error_code=code,
    )


def _record_dispatch_usage(
    *,
    owner_id: str,
    workspace_id: str,
    task_id: str,
    accepted: DispatchSubmissionCompleted,
) -> None:
    """Best-effort metering for one completed dispatch via the single mechanism (U-04)."""
    usage = dict(accepted.usage) if isinstance(accepted.usage, Mapping) else None
    if usage is None:
        # Completed provider turn with no usage payload must not be free.
        usage = {
            "inputTokens": 0,
            "outputTokens": 1,
            "totalTokens": 1,
            "estimated": True,
            "raw": {"reason": "dispatch_usage_missing_floor"},
        }
        _logger.warning(
            "dispatch usage missing; applying 1-token floor owner=%s task=%s provider=%s",
            owner_id,
            task_id,
            accepted.provider_id,
        )
    from app.modules.token_ledger_repository import meter_call

    meter_call(
        user_id=owner_id,
        source="durable_dispatch",
        usage_wire=usage,
        workspace_id=workspace_id,
        task_id=task_id,
        provider=accepted.provider_id,
        model=accepted.model,
        key_source="platform",
        status="success",
    )


def _record_rejected_dispatch_usage(
    *,
    owner_id: str,
    workspace_id: str,
    task_id: str,
    usage: Mapping[str, Any] | None,
    provider_id: str | None,
) -> None:
    """Meter provider tokens that were spent before a known rejection (e.g. schema invalid)."""
    if not owner_id or not isinstance(usage, Mapping) or not usage:
        return
    from app.modules.token_ledger_repository import meter_call

    meter_call(
        user_id=owner_id,
        source="durable_dispatch",
        usage_wire=dict(usage),
        workspace_id=workspace_id,
        task_id=task_id,
        provider=provider_id,
        status="failed",
    )


__all__ = ["DispatchWorkerResult", "process_authorized_recovery", "process_dispatch_claim"]
