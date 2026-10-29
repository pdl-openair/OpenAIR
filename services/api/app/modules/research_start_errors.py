"""Stable error types for the durable Research Start contract."""

from __future__ import annotations


class ResearchStartError(RuntimeError):
    """Base error for the durable Research Start contract."""

    code = "research_start_error"
    default_message = "The durable research-start operation could not be completed."

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.default_message)


class InvalidIntentError(ResearchStartError, ValueError):
    """The caller's intent cannot enter the durable contract."""

    code = "invalid_intent"
    default_message = "The research-start intent does not match the durable contract."


class RequestConflictError(ResearchStartError):
    code = "fingerprint_conflict"
    default_message = "The client request key is already reserved for different input."


class SessionNotFoundError(ResearchStartError):
    code = "session_not_found"
    default_message = "The research-start session was not found in this scope."


class RequestNotFoundError(ResearchStartError):
    code = "request_not_found"
    default_message = "The research-start request was not found."


class AttemptNotFoundError(ResearchStartError):
    code = "attempt_not_found"
    default_message = "The research-start attempt was not found."


class SessionBusyError(ResearchStartError):
    code = "session_busy"
    default_message = "The research-start session already has active work."


class TerminalStateError(ResearchStartError):
    code = "terminal_state"
    default_message = "The durable research-start state is terminal."


class LeaseLostError(ResearchStartError):
    code = "lease_lost"
    default_message = "The research-start lease is no longer valid."


class AttemptAlreadyStartedError(ResearchStartError):
    code = "attempt_already_started"
    default_message = "A physical external-call attempt is already recorded."


class TaskFinalizeRequiredError(ResearchStartError):
    code = "task_finalize_required"
    default_message = (
        "Research task results must be finalized with their reserved task and artifact."
    )


class FinalizeInvariantBrokenError(ResearchStartError):
    code = "finalize_invariant_broken"
    default_message = "The durable research-start finalize projection is inconsistent."


class FinalizeIdentityConflictError(ResearchStartError):
    code = "finalize_identity_conflict"
    default_message = "A reserved research-start identity is already occupied."


class CatalogSessionConflictError(ResearchStartError):
    code = "catalog_session_conflict"
    default_message = "The catalog intent is already bound to different durable resource facts."


class ResearchStartAdmissionFullError(ResearchStartError):
    code = "heavy_queue_full"
    default_message = "The research planning backlog is full. Please retry shortly."

    def __init__(
        self,
        message: str | None = None,
        *,
        retry_after: int = 5,
        position: int | None = None,
        scope: str = "owner_workspace",
    ) -> None:
        super().__init__(message)
        self.retry_after = max(1, int(retry_after))
        self.position = position
        self.scope = scope
