"""Shared thin transport for OpenAI-compatible LLM HTTP calls (U-02).

Centralizes the duplicated ``POST + content extraction + usage normalization``
across the HTTP-family call sites (B/D/E/G/H + A's transport layer). Thin by
design — it only sends the request and returns ``{content, usage_wire, raw_body}``:

- frozen-contract validation (B) stays in B's upper adapter layer
- domain pipelines (A: runtime guard / P08A / sanitizer) stay in A
- retry / model-fallback loops stay with each caller (they catch httpx errors
  as before; this module lets ``httpx.HTTPStatusError`` / ``RequestError``
  propagate unchanged so callers' retry classification still works)
- ledger capture / settle (U-04) remains at call sites, but usage extraction here
  always returns billable wire usage (provider usage or character-based estimate)
  so new invoke_* callers cannot silently observe ``None`` usage on HTTP success

Provider-neutral: takes endpoint creds + a caller-built payload, so HUIYAN and
any future OpenAI-compatible provider share one path.

Cycle avoidance: the pure content extractors live HERE (not in
``llm_structured_runner``), so this module depends only on ``token_usage``.
``llm_structured_runner`` re-imports them for back-comat with G/H's existing
``from llm_structured_runner import _extract_chat_content`` imports.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

import httpx

from llm_core.token_usage import billable_usage_from_openai_body, to_wire_usage


@dataclass(frozen=True)
class ProviderEndpoint:
    """Connection + identity for one OpenAI-compatible provider."""

    base_url: str
    api_key: str = field(default="", repr=False)
    timeout_seconds: float = 300.0
    read_timeout_seconds: float | None = None
    allow_proxy_fallback: bool = True


@dataclass(frozen=True)
class InvokeResult:
    """One OpenAI-compatible call result (chat-completions or responses)."""

    content: str
    usage_wire: dict[str, Any] | None  # camelCase; estimated when provider omits usage
    raw_body: dict[str, Any]  # full response body for caller-side error classification / contract checks


def chat_completions_url(base_url: str) -> str:
    """Resolve the chat-completions URL for an OpenAI-compatible provider base.

    - ``…/v1`` or bare host → ``…/v1/chat/completions`` (OpenAI / OpenCode / Huiyan)
    - ``…/paas/v4`` or ``…/v4`` → ``…/chat/completions`` (Zhipu BigModel)
    - already ending in ``/chat/completions`` is returned unchanged

    Do **not** point ``LLM_PROVIDER_BASE_URL`` at Anthropic-compatible hosts
    (e.g. ``…/api/anthropic``); those belong to ``ANTHROPIC_BASE_URL``.
    """
    base = str(base_url or "").strip().rstrip("/")
    if not base:
        raise ValueError("provider base URL is empty")
    if base.endswith("/chat/completions"):
        return base
    if base.endswith("/v1"):
        return f"{base}/chat/completions"
    # Zhipu OpenAI-compatible API uses /api/paas/v4/chat/completions (no /v1).
    if base.endswith("/v4") or "/paas/v4" in base:
        return f"{base}/chat/completions"
    return f"{base}/v1/chat/completions"


def openai_compatible_headers(api_key: str) -> dict[str, str]:
    """Shared request headers for OpenAI-compatible chat/completions calls."""
    h = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        # Stable identity; avoid Python-urllib defaults that some edges ban (CF 1010).
        "User-Agent": "OpenAIR/1.0 (+llm_core)",
    }
    if api_key:
        h["Authorization"] = f"Bearer {api_key}"
    return h


def _headers(api_key: str) -> dict[str, str]:
    return openai_compatible_headers(api_key)


def _request_text_for_estimate(payload: Mapping[str, Any] | None) -> str:
    """Cheap request-size hint for missing-usage estimation (S03-14-01 §5)."""
    if not isinstance(payload, Mapping):
        return ""
    messages = payload.get("messages")
    if isinstance(messages, list):
        parts: list[str] = []
        for message in messages:
            if not isinstance(message, Mapping):
                continue
            content = message.get("content")
            if isinstance(content, str):
                parts.append(content)
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, Mapping) and isinstance(part.get("text"), str):
                        parts.append(part["text"])
        if parts:
            return "\n".join(parts)
    input_text = payload.get("input")
    if isinstance(input_text, str):
        return input_text
    return ""


def usage_wire_from_openai_body(
    body: Any,
    *,
    request_text: str = "",
    response_text: str = "",
) -> dict[str, Any]:
    """Normalize provider usage, estimating when the gateway omits ``usage``.

    Shared entry for invoke_* and any post_* caller that needs billable wire
    usage without re-implementing the estimate rule (U-04 leakage guard).
    """
    usage = billable_usage_from_openai_body(
        body if isinstance(body, dict) else {},
        request_text=request_text,
        response_text=response_text,
    )
    wire = to_wire_usage(usage)
    assert wire is not None
    return wire


def _usage_from_body(
    body: Any,
    *,
    request_text: str = "",
    response_text: str = "",
) -> dict[str, Any]:
    return usage_wire_from_openai_body(
        body,
        request_text=request_text,
        response_text=response_text,
    )


# ---------------------------------------------------------------------------
# Pure content extractors (moved here from llm_structured_runner to break the
# import cycle; llm_structured_runner re-exports them for back-comat).
# ---------------------------------------------------------------------------


def apply_openai_compatible_thinking(
    payload: dict[str, Any],
    env_name: str,
) -> dict[str, Any]:
    """Optionally set OpenAI-compatible thinking controls from an env toggle.

    DeepSeek documents ``{"thinking": {"type": "enabled|disabled"}}``. Some
    gateways (observed: OpenCode Zen + deepseek-v4-flash-free) still emit
    ``reasoning_content`` unless ``reasoning_effort`` is also set to ``none``.
    Empty / unset env leaves provider default.
    """
    raw = (os.environ.get(env_name) or "").strip().lower()
    if not raw:
        return payload
    if raw in {"disabled", "off", "0", "false", "none"}:
        return {
            **payload,
            "thinking": {"type": "disabled"},
            "reasoning_effort": "none",
        }
    if raw in {"enabled", "on", "1", "true"}:
        out = {**payload, "thinking": {"type": "enabled"}}
        # Do not force a high effort; leave provider/model defaults for enable.
        out.pop("reasoning_effort", None)
        return out
    return payload


def _extract_chat_content(result: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Extract ``(content, choice)`` from a chat-completions response body.

    Prefer final ``message.content``. Fall back to ``reasoning_content`` only for
    non-structured callers that historically accepted either field; structured
    JSON callers must use :func:`extract_final_chat_content` instead.
    """
    choices = result.get("choices")
    if isinstance(choices, list) and choices:
        choice = choices[0] if isinstance(choices[0], dict) else {}
        message = choice.get("message", {}) if isinstance(choice, dict) else {}
        content = message.get("content") or message.get("reasoning_content") or ""
        if content:
            return str(content), choice

    if isinstance(result.get("output_text"), str):
        return str(result.get("output_text") or ""), {"finish_reason": None}
    if isinstance(result.get("text"), str):
        return str(result.get("text") or ""), {"finish_reason": None}
    if isinstance(result.get("data"), dict) and isinstance(result["data"].get("content"), str):
        return str(result["data"].get("content") or ""), {"finish_reason": None}

    raise RuntimeError(
        "Unexpected API response structure: missing choices/content; "
        f"top_level_keys={list(result.keys())[:20]}; raw_preview={str(result)[:500]}"
    )


def extract_final_chat_content(result: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    """Extract final assistant ``content`` for structured JSON contracts.

    Never treats ``reasoning_content`` as the answer. Raises ``ValueError`` with a
    diagnostic when the model truncated while thinking or returned reasoning only.
    """
    choices = result.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("empty LLM response: missing choices")
    choice = choices[0] if isinstance(choices[0], dict) else {}
    message = choice.get("message", {}) if isinstance(choice, dict) else {}
    if not isinstance(message, Mapping):
        message = {}
    content = message.get("content")
    if isinstance(content, str) and content.strip():
        return content, choice

    finish_reason = choice.get("finish_reason")
    reasoning = message.get("reasoning_content")
    has_reasoning = isinstance(reasoning, str) and bool(reasoning.strip())
    if finish_reason == "length" and has_reasoning:
        raise ValueError(
            "LLM truncated while reasoning; final content is empty "
            "(increase max_tokens or set thinking=disabled for this model)"
        )
    if has_reasoning:
        raise ValueError(
            "LLM returned reasoning_content without final content "
            "(enable enough output budget or disable thinking for structured JSON)"
        )
    if isinstance(content, str):
        raise ValueError("empty LLM response content")
    raise ValueError("empty LLM response: missing message.content")


def _extract_responses_output_text(result: dict[str, Any]) -> str:
    """Extract output text from an OpenAI Responses API body."""
    direct = result.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct

    text_parts: list[str] = []
    refusals: list[str] = []
    output = result.get("output")
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "refusal":
                refusals.append(str(item.get("refusal") or item.get("text") or "model refused the request"))
            content_items = item.get("content")
            if not isinstance(content_items, list):
                continue
            for content_item in content_items:
                if not isinstance(content_item, dict):
                    continue
                if content_item.get("type") == "output_text" and isinstance(content_item.get("text"), str):
                    text_parts.append(content_item["text"])
                elif content_item.get("type") == "refusal":
                    refusals.append(str(content_item.get("refusal") or content_item.get("text") or "model refused the request"))

    if refusals:
        raise RuntimeError(f"OpenAI Responses API refusal: {'; '.join(refusals)}")
    if not text_parts:
        raise RuntimeError(
            "Unexpected OpenAI Responses API response structure: missing output_text; "
            f"top_level_keys={list(result.keys())[:20]}"
        )
    return "".join(text_parts)


# ---------------------------------------------------------------------------
# Transport — two layers.
#
# post_*  : thinnest. POST → raise_for_status → json. Returns the raw body dict.
#           httpx errors (HTTPStatusError / RequestError) propagate unchanged so
#           callers' retry/fallback classification (reads exc.response.status_code)
#           keeps working. Use when the caller has its own body parsing
#           (A's structured pipeline, E's artifact parser, B's frozen contract).
#
# invoke_*: convenience. post_* + content extraction + usage normalization →
#           InvokeResult. Use when the caller wants the message text + usage
#           (G/H planner, D huiyan-skill).
# ---------------------------------------------------------------------------


def _provider_transport_timeout(endpoint: ProviderEndpoint) -> httpx.Timeout:
    """Bound connect/pool/write stages while leaving Provider response observation open."""
    guard = max(float(endpoint.timeout_seconds), 0.1)
    return httpx.Timeout(
        connect=min(5.0, guard),
        pool=min(5.0, guard),
        write=min(10.0, guard),
        read=endpoint.read_timeout_seconds,
    )


def post_chat_completions(
    endpoint: ProviderEndpoint,
    payload: dict[str, Any],
    *,
    extra_headers: Mapping[str, str] | None = None,
    before_send: Callable[[], None] | None = None,
    response_observer: Callable[[httpx.Response], None] | None = None,
) -> dict[str, Any]:
    """POST chat/completions (sync). Returns the raw response body.

    Optional transport hooks are intentionally generic. They let a higher layer
    durably record the physical-send boundary and the raw HTTP response before
    JSON parsing without teaching llm_core about OpenAIR persistence.
    """
    url = chat_completions_url(endpoint.base_url)
    headers = _headers(endpoint.api_key)
    if extra_headers:
        headers.update(dict(extra_headers))
    timeout = _provider_transport_timeout(endpoint)

    def _send(*, trust_env: bool) -> dict[str, Any]:
        with httpx.Client(timeout=timeout, trust_env=trust_env) as client:
            if before_send is not None:
                before_send()
            response = client.post(url, headers=headers, json=payload)
            if response_observer is not None:
                response_observer(response)
            response.raise_for_status()
            return response.json()

    try:
        # Prefer direct provider reachability; env proxies are a fallback only.
        return _send(trust_env=False)
    except (httpx.ConnectTimeout, httpx.ConnectError, httpx.ProxyError):
        if not endpoint.allow_proxy_fallback:
            raise
        return _send(trust_env=True)


async def apost_chat_completions(endpoint: ProviderEndpoint, payload: dict[str, Any]) -> dict[str, Any]:
    """POST chat/completions (async, for callers in an event loop). Returns raw body."""
    url = chat_completions_url(endpoint.base_url)
    timeout = _provider_transport_timeout(endpoint)
    headers = _headers(endpoint.api_key)

    async def _send(*, trust_env: bool) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=timeout, trust_env=trust_env) as client:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            return response.json()

    try:
        return await _send(trust_env=False)
    except (httpx.ConnectTimeout, httpx.ConnectError, httpx.ProxyError):
        return await _send(trust_env=True)

def post_responses(
    endpoint: ProviderEndpoint,
    payload: dict[str, Any],
    *,
    extra_headers: Mapping[str, str] | None = None,
    before_send: Callable[[], None] | None = None,
    response_observer: Callable[[httpx.Response], None] | None = None,
) -> dict[str, Any]:
    """POST /v1/responses (sync). Returns the raw response body.

    ``response_observer`` runs before HTTP status and JSON interpretation so a
    caller can make Provider completion/failure permanent independently of later
    local processing.
    """
    base = str(endpoint.base_url or "").strip().rstrip("/")
    if base.endswith("/v1"):
        url = f"{base}/responses"
    else:
        url = f"{base}/v1/responses"
    headers = _headers(endpoint.api_key)
    if extra_headers:
        headers.update(dict(extra_headers))
    with httpx.Client(timeout=_provider_transport_timeout(endpoint)) as client:
        if before_send is not None:
            before_send()
        response = client.post(url, headers=headers, json=payload)
        if response_observer is not None:
            response_observer(response)
        response.raise_for_status()
        return response.json()


def invoke_chat_completions(endpoint: ProviderEndpoint, payload: dict[str, Any]) -> InvokeResult:
    """POST /v1/chat/completions (sync). Returns extracted content + usage + raw body."""
    body = post_chat_completions(endpoint, payload)
    content, _ = _extract_chat_content(body)
    return InvokeResult(
        content=content,
        usage_wire=_usage_from_body(
            body,
            request_text=_request_text_for_estimate(payload),
            response_text=content,
        ),
        raw_body=body,
    )


async def ainvoke_chat_completions(endpoint: ProviderEndpoint, payload: dict[str, Any]) -> InvokeResult:
    """POST /v1/chat/completions (async). Returns extracted content + usage + raw body."""
    body = await apost_chat_completions(endpoint, payload)
    content, _ = _extract_chat_content(body)
    return InvokeResult(
        content=content,
        usage_wire=_usage_from_body(
            body,
            request_text=_request_text_for_estimate(payload),
            response_text=content,
        ),
        raw_body=body,
    )


def invoke_responses(endpoint: ProviderEndpoint, payload: dict[str, Any]) -> InvokeResult:
    """POST /v1/responses (sync). Returns extracted output_text + usage + raw body."""
    body = post_responses(endpoint, payload)
    content = _extract_responses_output_text(body)
    return InvokeResult(
        content=content,
        usage_wire=_usage_from_body(
            body,
            request_text=_request_text_for_estimate(payload),
            response_text=content,
        ),
        raw_body=body,
    )
