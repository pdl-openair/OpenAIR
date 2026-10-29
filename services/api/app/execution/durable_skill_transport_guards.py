"""Keep transport observation guards and internal-call pre-I/O authority."""

from collections.abc import Mapping
from typing import Any

from httpx import Timeout as HttpxTimeout


def mark_skill_provider_io_started(
    request: Mapping[str, Any], repository: Any, attempt: Any
) -> None:
    authorization = request.get("_internalProviderIoAuthorization")
    if authorization is None:
        repository.mark_io_started(attempt)
        return
    # This private execution context is injected by code after canonical reservation.
    # It is never part of model-visible parameters or frozen user inputs.
    if not callable(authorization):
        raise ValueError("internal_provider_io_authority_invalid")
    authorization(attempt)


def _llm_task_deadline(execution_policy: Mapping[str, Any] | None) -> float | None:
    """Return no hard deadline: taskBudgetSeconds is a soft observation budget.

    Kept as a compatibility shim for the existing structured-call plumbing.  The
    formal runtime contract explicitly forbids deriving Provider termination from
    the task budget.
    """
    return None


def _llm_http_timeout(
    *,
    runtime_config: Any,
    execution_policy: Mapping[str, Any] | None,
    deadline: float | None,
) -> Any:
    """Build transport guards without turning them into execution termination.

    The current HUIYAN chat-completions Provider exposes no durable remote job/query
    identity.  For that capability, the OpenAIR durable worker is the observation
    owner, so the response-read channel must remain open while the Provider may
    still be running.  Connect/pool/write remain bounded; ``read=None`` prevents a
    local fixed-second read guard (90/120/180...) from manufacturing an
    ``externalResultUnknown`` for a still-running model.  ``deadline`` is accepted
    only for backwards-compatible call signatures and is intentionally ignored.
    """
    if isinstance(execution_policy, Mapping):
        transport_guard = float(execution_policy["readTimeoutSeconds"])
    else:
        transport_guard = float(getattr(runtime_config, "timeoutSeconds", 300.0) or 300.0)
    return HttpxTimeout(
        connect=min(5.0, transport_guard),
        pool=min(5.0, transport_guard),
        write=min(10.0, transport_guard),
        read=None,
    )
