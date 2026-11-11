"""Anthropic Messages HTTP transport.

Mirrors ``openai_compatible_client``: thin by design — it only sends the
request and returns ``{content, usage_wire, raw_body}``. Callers keep their
own retry/error classification (``httpx.HTTPStatusError`` / ``RequestError``
propagate unchanged) and contract validation.

Wire contract (Anthropic Messages API):

- endpoint ``POST {base}/v1/messages``
- auth header ``x-api-key`` plus required ``anthropic-version``
- system prompt is a top-level ``system`` string, not a message role
- ``max_tokens`` is required
- response ``content`` is a list of blocks (``text`` / ``tool_use``)
- usage reports ``input_tokens`` / ``output_tokens`` (already normalized by
  ``token_usage.normalize_openai_usage``)
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

import httpx

from llm_core.openai_compatible_client import (
    InvokeResult,
    ProviderEndpoint,
    _provider_transport_timeout,
    _request_text_for_estimate,
    _usage_from_body,
)

ANTHROPIC_API_VERSION = "2023-06-01"


def messages_url(base_url: str) -> str:
    """Resolve the Messages endpoint for an Anthropic-compatible base URL."""
    base = str(base_url or "").strip().rstrip("/")
    if not base:
        raise ValueError("provider base URL is empty")
    if base.endswith("/messages"):
        return base
    if base.endswith("/v1"):
        return f"{base}/messages"
    return f"{base}/v1/messages"


def anthropic_headers(api_key: str, *, api_version: str = ANTHROPIC_API_VERSION) -> dict[str, str]:
    """Shared request headers for Anthropic Messages calls."""
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "anthropic-version": api_version,
        "User-Agent": "OpenAIR/1.0 (+llm_core)",
    }
    if api_key:
        headers["x-api-key"] = api_key
    return headers


def extract_messages_text(result: dict[str, Any]) -> str:
    """Extract assistant text from a Messages response body.

    ``stop_reason == "refusal"`` is a provider refusal, not a parse failure.
    """
    content = result.get("content")
    text_parts: list[str] = []
    if isinstance(content, list):
        for block in content:
            if isinstance(block, Mapping) and block.get("type") == "text":
                text = block.get("text")
                if isinstance(text, str):
                    text_parts.append(text)
    if str(result.get("stop_reason") or "") == "refusal" or any(
        isinstance(block, Mapping) and block.get("type") == "refusal"
        for block in content if isinstance(content, list)
    ):
        raise RuntimeError("Anthropic Messages API refusal: provider declined to answer")
    if not text_parts:
        raise RuntimeError(
            "Unexpected Anthropic Messages API response structure: missing text content; "
            f"top_level_keys={list(result.keys())[:20]}"
        )
    return "".join(text_parts)


def extract_tool_use_input(result: dict[str, Any], tool_name: str) -> dict[str, Any]:
    """Return the ``input`` payload of the named ``tool_use`` block."""
    content = result.get("content")
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, Mapping) or block.get("type") != "tool_use":
                continue
            if block.get("name") == tool_name:
                tool_input = block.get("input")
                if isinstance(tool_input, Mapping):
                    return dict(tool_input)
                raise RuntimeError(
                    "Anthropic Messages API tool_use block has no mapping input; "
                    f"tool={tool_name}"
                )
    raise RuntimeError(
        f"Anthropic Messages API response is missing tool_use block: tool={tool_name}"
    )


def post_messages(
    endpoint: ProviderEndpoint,
    payload: dict[str, Any],
    *,
    extra_headers: Mapping[str, str] | None = None,
    before_send: Callable[[], None] | None = None,
    response_observer: Callable[[httpx.Response], None] | None = None,
) -> dict[str, Any]:
    """POST /v1/messages (sync). Returns the raw response body."""
    url = messages_url(endpoint.base_url)
    headers = anthropic_headers(endpoint.api_key)
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


def invoke_messages(endpoint: ProviderEndpoint, payload: dict[str, Any]) -> InvokeResult:
    """POST /v1/messages (sync). Returns extracted text + usage + raw body."""
    body = post_messages(endpoint, payload)
    content = extract_messages_text(body)
    return InvokeResult(
        content=content,
        usage_wire=_usage_from_body(
            body,
            request_text=_request_text_for_estimate(payload),
            response_text=content,
        ),
        raw_body=body,
    )
