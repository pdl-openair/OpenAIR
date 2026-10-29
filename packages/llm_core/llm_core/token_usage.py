"""Provider-neutral token usage extraction (S03-14-02).

Every LLM call site funnels its provider-specific ``usage`` payload through
these normalizers so the ledger sees one shape regardless of provider:

    {"input_tokens", "output_tokens", "total_tokens", "estimated", "raw"}

Python-internal dicts use snake_case; payloads crossing a process boundary
(worker callback, SSE events) use camelCase via ``to_wire_usage``.
"""

from __future__ import annotations

import contextvars
from contextlib import contextmanager
from typing import Any, Iterator

NormalizedUsage = dict[str, Any]


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def normalize_openai_usage(usage: Any) -> NormalizedUsage | None:
    """OpenAI-compatible usage (chat completions OR responses API).

    chat completions: ``prompt_tokens`` / ``completion_tokens``
    responses API:    ``input_tokens`` / ``output_tokens``
    """
    if not isinstance(usage, dict) or not usage:
        return None
    input_tokens = _int(usage.get("prompt_tokens") if usage.get("prompt_tokens") is not None else usage.get("input_tokens"))
    output_tokens = _int(
        usage.get("completion_tokens") if usage.get("completion_tokens") is not None else usage.get("output_tokens")
    )
    total = _int(usage.get("total_tokens")) or (input_tokens + output_tokens)
    if not (input_tokens or output_tokens or total):
        return None
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total,
        "estimated": False,
        "raw": usage,
    }


def normalize_claude_sdk_usage(usage: Any) -> NormalizedUsage | None:
    """Claude Agent SDK ``ResultMessage.usage`` (dict or attribute object).

    Deduction basis is ``input_tokens + output_tokens``; cache fields are
    preserved in ``raw`` but not counted (S03-14-01 §5).
    """
    if usage is None:
        return None
    if isinstance(usage, dict):
        data = usage
    else:
        data = {
            key: getattr(usage, key, None)
            for key in (
                "input_tokens",
                "output_tokens",
                "cache_creation_input_tokens",
                "cache_read_input_tokens",
            )
            if getattr(usage, key, None) is not None
        }
    input_tokens = _int(data.get("input_tokens"))
    output_tokens = _int(data.get("output_tokens"))
    if not (input_tokens or output_tokens):
        return None
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "estimated": False,
        "raw": data,
    }


def estimate_usage_from_text(text: str) -> NormalizedUsage:
    """Fallback for interrupted streams with no provider usage (S03-14-01 §5)."""
    output_tokens = max(1, -(-len(text) // 4)) if text else 0
    return {
        "input_tokens": 0,
        "output_tokens": output_tokens,
        "total_tokens": output_tokens,
        "estimated": True,
        "raw": None,
    }


def _ceil_chars_to_tokens(text: str) -> int:
    return max(0, -(-len(text) // 4)) if text else 0


def _rough_chat_completion_text(body: Any) -> str:
    """Best-effort extract assistant text from an OpenAI-compatible chat body."""
    if not isinstance(body, dict):
        return ""
    choices = body.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            message = first.get("message")
            if isinstance(message, dict):
                content = message.get("content")
                if isinstance(content, str):
                    return content
                if isinstance(content, list):
                    parts = [
                        str(part.get("text") or "")
                        for part in content
                        if isinstance(part, dict)
                    ]
                    return "".join(parts)
            text = first.get("text")
            if isinstance(text, str):
                return text
    # Responses API style
    output_text = body.get("output_text")
    if isinstance(output_text, str):
        return output_text
    return ""


def billable_usage_from_openai_body(
    body: Any,
    *,
    request_text: str = "",
    response_text: str = "",
) -> NormalizedUsage:
    """Return provider usage, or a conservative estimate when usage is missing.

    A paid HTTP success must never become a free call just because the gateway
    omitted ``usage`` (public-beta leakage guard; S03-14-01 §5 estimation rule).
    """
    usage = normalize_openai_usage(body.get("usage") if isinstance(body, dict) else None)
    if usage is not None:
        return usage

    response = response_text or _rough_chat_completion_text(body)
    input_tokens = _ceil_chars_to_tokens(request_text)
    output_tokens = _ceil_chars_to_tokens(response)
    if output_tokens <= 0 and input_tokens <= 0:
        # Floor: we observed a completed response body; charge at least 1 token.
        output_tokens = 1
    elif output_tokens <= 0:
        output_tokens = 1
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "estimated": True,
        "raw": {"reason": "provider_usage_missing_estimated"},
    }


def to_wire_usage(usage: NormalizedUsage | None) -> dict[str, Any] | None:
    """camelCase payload for process boundaries (worker callback / SSE events)."""
    if not usage:
        return None
    return {
        "inputTokens": _int(usage.get("input_tokens")),
        "outputTokens": _int(usage.get("output_tokens")),
        "totalTokens": _int(usage.get("total_tokens")) or _int(usage.get("input_tokens")) + _int(usage.get("output_tokens")),
        "estimated": bool(usage.get("estimated") or False),
        "raw": usage.get("raw"),
    }


def from_wire_usage(payload: Any) -> NormalizedUsage | None:
    """Inverse of ``to_wire_usage`` (API side of the worker callback)."""
    if not isinstance(payload, dict):
        return None
    input_tokens = _int(payload.get("inputTokens"))
    output_tokens = _int(payload.get("outputTokens"))
    total = _int(payload.get("totalTokens")) or (input_tokens + output_tokens)
    if not (input_tokens or output_tokens or total):
        return None
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total,
        "estimated": bool(payload.get("estimated") or False),
        "raw": payload.get("raw"),
    }


# ---------------------------------------------------------------------------
# Request-scoped usage collector (for call sites whose result cannot carry
# usage without breaking a persisted contract — e.g. research-start planner,
# whose planningAudit is a persisted string-only mapping).
#
# A route opens a scope around the LLM call, the call site observes usage into
# the scope, and the route flushes the collected entries to the ledger. When no
# scope is active (CLI / batch paths), observations are silently skipped.
# ---------------------------------------------------------------------------

_usage_collector: contextvars.ContextVar[list[dict[str, Any]] | None] = contextvars.ContextVar(
    "openair_usage_collector", default=None
)


@contextmanager
def usage_collection_scope() -> Iterator[list[dict[str, Any]]]:
    bucket: list[dict[str, Any]] = []
    token = _usage_collector.set(bucket)
    try:
        yield bucket
    finally:
        _usage_collector.reset(token)


def begin_usage_collection() -> tuple[list[dict[str, Any]], contextvars.Token[list[dict[str, Any]] | None]]:
    """Begin collecting observed usage without a ``with`` block.

    Returns ``(bucket, token)``; pass ``token`` to :func:`end_usage_collection`.
    Use this when the collection must span a region that is awkward to indent
    under a contextmanager (e.g. a long function body with an existing finally).
    """
    bucket: list[dict[str, Any]] = []
    token = _usage_collector.set(bucket)
    return bucket, token


def end_usage_collection(token: contextvars.Token[list[dict[str, Any]] | None]) -> None:
    _usage_collector.reset(token)


def record_observed_usage(
    *,
    provider: str | None,
    model: str | None,
    body: Any,
    request_text: str = "",
    response_text: str = "",
) -> None:
    """Observe a provider response's usage into the active scope (if any).

    When the provider omits ``usage``, falls back to a character-based estimate
    so the eventual flush still settles tokens (no silent free calls).
    """
    bucket = _usage_collector.get()
    if bucket is None:
        return
    usage = billable_usage_from_openai_body(
        body,
        request_text=request_text,
        response_text=response_text,
    )
    if usage:
        bucket.append(
            {
                "provider": provider,
                "model": model,
                "usage": to_wire_usage(usage),
            }
        )
