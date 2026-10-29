"""Explicit two-phase reconciliation and proven-safe retry boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from app.execution.research_dispatch_provider import (
    DispatchSubmissionQueryResult,
    ExternalResultQueryProvider,
)
from app.models.research_external_result import (
    ExplicitRetryCommand,
    ExternalExecutionState,
    ExternalQueryObservation,
    FrozenExternalQuery,
    ReconcileExternalResultCommand,
    ReconcileResolution,
)


class ExternalRecoveryError(RuntimeError):
    pass


class ExternalRecoveryConflict(ExternalRecoveryError):
    pass


class ExternalRecoveryNotAllowed(ExternalRecoveryError):
    pass


class ExternalRecoveryRepository(Protocol):
    def prepare_reconcile(self, command: ReconcileExternalResultCommand) -> FrozenExternalQuery: ...

    def commit_reconcile(
        self,
        command: ReconcileExternalResultCommand,
        frozen: FrozenExternalQuery,
        observation: ExternalQueryObservation,
    ) -> Mapping[str, Any]: ...

    def prepare_retry(self, command: ExplicitRetryCommand) -> FrozenExternalQuery: ...

    def create_retry_attempt(self, command: ExplicitRetryCommand) -> Mapping[str, Any]: ...


class RecoveryMaterializationPort(Protocol):
    def materialize_recovery(self, directive: Mapping[str, Any]) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class ExternalRecoveryCapabilities:
    """Server-owned recovery actions that are actually executable in this process."""

    query_supported: bool
    abandon_supported: bool


class ExternalRecoveryService:
    def __init__(
        self,
        *,
        repository: ExternalRecoveryRepository,
        provider: ExternalResultQueryProvider | None,
        recovery_materialization_port: RecoveryMaterializationPort | None = None,
    ) -> None:
        self._repository = repository
        self._provider = provider
        self._materializer = recovery_materialization_port
        self._retry_results: dict[str, tuple[str, Mapping[str, Any]]] = {}

    def capabilities(self) -> ExternalRecoveryCapabilities:
        return _capabilities_for_provider(self._provider)

    def reconcile(self, command: ReconcileExternalResultCommand) -> Mapping[str, Any]:
        if command.resolution is not ReconcileResolution.QUERY_PROVIDER:
            raise ExternalRecoveryNotAllowed("system_provider_observation_required")
        replay_reader = getattr(self._repository, "get_reconcile_result", None)
        if replay_reader is not None:
            replay = replay_reader(command)
            if replay is not None:
                return replay
        if self._provider is None:
            raise ExternalRecoveryNotAllowed("provider_external_query_unsupported")
        prepared = self._provider.preflight_query()
        if not bool(getattr(prepared, "query_capable", False)):
            raise ExternalRecoveryNotAllowed("provider_external_query_unsupported")

        # Phase one transaction is wholly owned by the repository and returns
        # only immutable facts. It must have released all locks before I/O.
        frozen = self._repository.prepare_reconcile(command)
        self._validate_frozen_command(command, frozen)
        try:
            result = prepared.query_external_result(
                idempotency_key=frozen.provider_idempotency_key,
                remote_run_id=frozen.remote_run_id,
            )
            observation = _observation(result)
        except Exception:
            observation = ExternalQueryObservation(
                external_state=ExternalExecutionState.UNKNOWN,
                error_code="provider_query_result_unknown",
            )

        # Phase two re-locks Task -> Dispatch, compares every frozen fact and
        # atomically persists aggregate, reconciliation, evidence and directive.
        durable = self._repository.commit_reconcile(command, frozen, observation)
        if durable.get("status") == "directive_pending" and self._materializer is not None:
            return self._materializer.materialize_recovery(durable)
        return durable

    def retry(self, command: ExplicitRetryCommand) -> Mapping[str, Any]:
        prior = self._retry_results.get(command.command_request_id)
        if prior is not None:
            fingerprint, result = prior
            if fingerprint != command.canonical_payload_fingerprint:
                raise ExternalRecoveryConflict("command_payload_conflict")
            return result
        replay_reader = getattr(self._repository, "get_retry_result", None)
        if replay_reader is not None:
            replay = replay_reader(command)
            if replay is not None:
                self._retry_results[command.command_request_id] = (
                    command.canonical_payload_fingerprint,
                    replay,
                )
                return replay
        frozen = self._repository.prepare_retry(command)
        self._validate_retry_frozen(command, frozen)
        safe_known_response_retry = (
            frozen.external_state is ExternalExecutionState.KNOWN_FAILED
            and frozen.remote_run_id is None
        )
        safe_reasoning_recovery = (
            frozen.external_state is ExternalExecutionState.UNKNOWN
            and frozen.proven_no_external_action_retry
            and frozen.remote_run_id is None
        )
        if (
            frozen.remote_run_id is not None
            or (
                frozen.external_state is not ExternalExecutionState.NEVER_SUBMITTED
                and not safe_known_response_retry
                and not safe_reasoning_recovery
            )
        ):
            raise ExternalRecoveryNotAllowed("retry_requires_proven_safe_outcome")
        # Production repository revalidates the exact persisted failure code under
        # lock before creating the next attempt. The command reason alone is never
        # sufficient authority for a known-failed replay.
        result = self._repository.create_retry_attempt(command)
        self._retry_results[command.command_request_id] = (command.canonical_payload_fingerprint, result)
        return result

    @staticmethod
    def _validate_frozen_command(command: ReconcileExternalResultCommand, frozen: FrozenExternalQuery) -> None:
        expected = (
            command.task_id,
            command.work_unit_id,
            command.dispatch_id,
            command.execution_decision_id,
            command.capability_call_id,
            command.attempt_number,
            command.expected_task_revision,
            command.owner_id,
            command.workspace_id,
        )
        actual = (
            frozen.task_id,
            frozen.work_unit_id,
            frozen.dispatch_id,
            frozen.execution_decision_id,
            frozen.capability_call_id,
            frozen.attempt_number,
            frozen.expected_task_revision,
            frozen.owner_id,
            frozen.workspace_id,
        )
        if expected != actual:
            raise ExternalRecoveryConflict("reconciliation_frozen_fact_conflict")
        if frozen.external_state not in {
            ExternalExecutionState.UNKNOWN,
            ExternalExecutionState.SUBMITTED,
            ExternalExecutionState.RUNNING,
        }:
            raise ExternalRecoveryNotAllowed("reconciliation_requires_observable_state")

    @staticmethod
    def _validate_retry_frozen(command: ExplicitRetryCommand, frozen: FrozenExternalQuery) -> None:
        if (
            command.task_id != frozen.task_id
            or command.work_unit_id != frozen.work_unit_id
            or command.dispatch_id != frozen.dispatch_id
            or command.execution_decision_id != frozen.execution_decision_id
            or command.prior_capability_call_id != frozen.capability_call_id
            or command.prior_attempt_number != frozen.attempt_number
            or command.owner_id != frozen.owner_id
            or command.workspace_id != frozen.workspace_id
            or command.expected_task_revision != frozen.expected_task_revision
        ):
            raise ExternalRecoveryConflict("retry_frozen_fact_conflict")


def _observation(result: DispatchSubmissionQueryResult) -> ExternalQueryObservation:
    state = {
        "accepted": ExternalExecutionState.SUBMITTED,
        "submitted": ExternalExecutionState.SUBMITTED,
        "running": ExternalExecutionState.RUNNING,
        "completed": ExternalExecutionState.COMPLETED,
        "known_rejected": ExternalExecutionState.KNOWN_FAILED,
        "known_failed": ExternalExecutionState.KNOWN_FAILED,
        "not_submitted": ExternalExecutionState.NEVER_SUBMITTED,
        "unknown": ExternalExecutionState.UNKNOWN,
    }[result.status]
    return ExternalQueryObservation(
        external_state=state,
        remote_run_id=result.remote_run_id,
        error_code=result.error_code,
        result_evidence_refs=result.result_evidence_refs,
    )


def _production_external_query_provider() -> ExternalResultQueryProvider | None:
    """Return the production read-only query adapter, when one truly exists.

    The current demo execution paths are HUIYAN and direct-Skill.  Both are
    single-attempt/no-replay and expose no provider query contract, so recovery
    must not advertise query_provider.  Keep this as the single wiring point for
    a future capability-checked read-only adapter.
    """
    return None


def _capabilities_for_provider(
    provider: ExternalResultQueryProvider | None,
) -> ExternalRecoveryCapabilities:
    query_supported = False
    if provider is not None:
        try:
            prepared = provider.preflight_query()
            query_supported = bool(getattr(prepared, "query_capable", False))
        except Exception:
            # Capability discovery is read-only and fail-closed. A broken or
            # unconfigured query adapter must never create a clickable action.
            query_supported = False
    return ExternalRecoveryCapabilities(
        query_supported=query_supported,
        abandon_supported=False,
    )


def production_external_recovery_capabilities() -> ExternalRecoveryCapabilities:
    # No repository is constructed for projection; capability discovery must be
    # side-effect free and safe to call from the Workbench GET path.
    return _capabilities_for_provider(_production_external_query_provider())


def production_external_recovery_service(
    repository: ExternalRecoveryRepository | None = None,
) -> ExternalRecoveryService:
    # Lazy import avoids coupling the pure recovery boundary to PostgreSQL at
    # module import time while keeping route execution and action projection on
    # one production capability source.
    from app.modules.research_orchestration_repository import ResearchOrchestrationRepository

    return ExternalRecoveryService(
        repository=repository or ResearchOrchestrationRepository(),
        provider=_production_external_query_provider(),
        recovery_materialization_port=None,
    )


__all__ = [
    "ExternalRecoveryConflict",
    "ExternalRecoveryError",
    "ExternalRecoveryNotAllowed",
    "ExternalRecoveryCapabilities",
    "ExternalRecoveryRepository",
    "ExternalRecoveryService",
    "RecoveryMaterializationPort",
    "production_external_recovery_capabilities",
    "production_external_recovery_service",
]
