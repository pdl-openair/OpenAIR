"""Single protocol adapter layer for provider LLM calls.

All provider wire differences for the three supported protocols live here:

- ``openai_chat_completions`` (``huiyan_chat_completions`` is its compatibility
  alias): ``/v1/chat/completions``, ``messages``, ``Authorization: Bearer``.
- ``openai_responses``: ``/v1/responses``, ``input``, ``max_output_tokens``,
  ``text.format`` structured output.
- ``anthropic_messages``: ``/v1/messages``, ``x-api-key`` + ``anthropic-version``,
  top-level ``system``, required ``max_tokens``, ``tool_use`` structured output.

Call chains (research start planner, durable orchestration, LLM structured
skill runner) route endpoint/header/payload/extraction decisions through this
module instead of branching on protocol strings at each call site. Raw HTTP
stays in ``llm_core`` transports; schema admission stays in
``structured_output_provider_adapter``.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

from llm_core.anthropic_client import (
    anthropic_headers,
    extract_messages_text,
    extract_tool_use_input,
    messages_url,
)
from llm_core.openai_compatible_client import (
    _extract_responses_output_text,
    chat_completions_url,
    extract_final_chat_content,
    openai_compatible_headers,
    responses_url,
)
from llm_core.token_usage import NormalizedUsage, billable_usage_from_openai_body

from app.execution.structured_output_provider_adapter import (
    ProviderSchemaCapabilityError,
    apply_provider_schema_transport,
    structured_response_format,
)
from app.modules.provider_registry import adapter_protocol

PROTOCOL_OPENAI_CHAT_COMPLETIONS = "openai_chat_completions"
PROTOCOL_OPENAI_RESPONSES = "openai_responses"
PROTOCOL_ANTHROPIC_MESSAGES = "anthropic_messages"

_logger = logging.getLogger("openair.provider_protocol")


class ProviderProtocolError(ValueError):
    """Raised for an unknown provider protocol or a protocol contract violation."""


def normalize_protocol(provider: str) -> str:
    """Return the canonical protocol for an adapter or protocol name."""
    try:
        return adapter_protocol(str(provider or "").strip().lower())
    except ValueError as exc:
        raise ProviderProtocolError(f"Unsupported provider protocol: {provider}") from exc


def reconcile_provider_protocol(
    configured_provider: str,
    registry_adapter: str,
    *,
    selector: str,
    default_provider: str = PROTOCOL_OPENAI_CHAT_COMPLETIONS,
) -> str:
    """Return the one protocol the explicit selector and the registry agree on.

    The provider registry supplies the credentials, so the adapter it names is
    the wire authority: an explicit selector (``OPENTRAJECTORY_LLM_PROVIDER``,
    ``OPENAIR_RESEARCH_START_PLANNER_*_PROVIDER``) that names a different
    protocol would send one protocol's body to another gateway's API key. The
    selector is honored only when both agree; on the legacy fallback path
    (``registry_adapter`` empty, registry supplied no credentials) it is the
    sole authority. Adapter aliases compare as equal because they are
    normalized to the same protocol.
    """
    registered = normalize_protocol(registry_adapter) if str(registry_adapter or "").strip() else ""
    selected = str(configured_provider or "").strip().lower()
    if not selected:
        return registered or normalize_protocol(default_provider)
    protocol = normalize_protocol(selected)
    if registered and registered != protocol:
        raise ProviderProtocolError(
            f"{selector}={selected} selects the {protocol} protocol, but the provider "
            f"registry adapter {registry_adapter} supplies {registered} credentials; "
            "set the selector to the registry adapter (or change the adapter) so the "
            "wire protocol and credentials match"
        )
    return protocol


def protocol_endpoint(protocol: str, base_url: str) -> str:
    """Resolve the protocol-native endpoint for a provider base URL."""
    canonical = normalize_protocol(protocol)
    if canonical == PROTOCOL_OPENAI_RESPONSES:
        return responses_url(base_url)
    if canonical == PROTOCOL_ANTHROPIC_MESSAGES:
        return messages_url(base_url)
    return chat_completions_url(base_url)


def protocol_headers(
    protocol: str,
    api_key: str,
    *,
    extra: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Build protocol-native request headers (authentication included)."""
    canonical = normalize_protocol(protocol)
    headers = (
        anthropic_headers(api_key)
        if canonical == PROTOCOL_ANTHROPIC_MESSAGES
        else openai_compatible_headers(api_key)
    )
    if extra:
        headers.update(dict(extra))
    return headers


def protocol_supports_stream(protocol: str) -> bool:
    """Only chat-completions gateways expose the SSE thinking stream today."""
    return normalize_protocol(protocol) == PROTOCOL_OPENAI_CHAT_COMPLETIONS


def build_protocol_chat_payload(
    protocol: str,
    *,
    model: str,
    messages: Sequence[Mapping[str, Any]],
    max_tokens: int | None = None,
    temperature: float | None = None,
    reasoning_effort: str | None = None,
) -> dict[str, Any]:
    """Build a protocol-native chat request payload from role/content turns."""
    canonical = normalize_protocol(protocol)
    turns = [dict(turn) for turn in messages]
    if canonical == PROTOCOL_OPENAI_RESPONSES:
        payload: dict[str, Any] = {"model": model, "input": turns, "store": False}
        if max_tokens is not None:
            payload["max_output_tokens"] = max_tokens
        # ``reasoning_effort`` and ``temperature`` are mutually exclusive by
        # intent, not by accident: the Responses API answers 400 when
        # ``temperature`` accompanies ``reasoning.effort``, and a reasoning
        # model ignores sampling controls anyway. A caller-supplied effort
        # therefore wins and the temperature is dropped; callers must gate the
        # effort on ``_is_reasoning_model`` so non-reasoning models keep
        # taking the temperature path instead.
        if reasoning_effort is not None:
            payload["reasoning"] = {"effort": reasoning_effort}
        elif temperature is not None:
            payload["temperature"] = temperature
        return payload
    if canonical == PROTOCOL_ANTHROPIC_MESSAGES:
        if max_tokens is None or int(max_tokens) < 1:
            raise ProviderProtocolError(
                "anthropic_messages requires a positive max_tokens on every request"
            )
        system_parts = [
            str(turn.get("content") or "") for turn in turns if turn.get("role") == "system"
        ]
        conversation = [turn for turn in turns if turn.get("role") != "system"]
        payload = {
            "model": model,
            "max_tokens": int(max_tokens),
            "messages": conversation,
        }
        if system_parts:
            payload["system"] = "\n\n".join(part for part in system_parts if part)
        if temperature is not None:
            payload["temperature"] = temperature
        return payload
    payload = {"model": model, "messages": turns}
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    if temperature is not None:
        payload["temperature"] = temperature
    return payload


def protocol_chat_prompts(protocol: str, payload: Mapping[str, Any]) -> tuple[str, str]:
    """Extract the (system, user) prompts from a protocol-native payload."""
    canonical = normalize_protocol(protocol)
    if canonical == PROTOCOL_OPENAI_CHAT_COMPLETIONS:
        turns = payload.get("messages")
        system = (
            str(turns[0].get("content") or "") if isinstance(turns, list) and turns else ""
        )
        user = (
            str(turns[1].get("content") or "")
            if isinstance(turns, list) and len(turns) > 1
            else ""
        )
        return system, user
    if canonical == PROTOCOL_ANTHROPIC_MESSAGES:
        system = str(payload.get("system") or "")
        turns = payload.get("messages")
        user = str(turns[0].get("content") or "") if isinstance(turns, list) and turns else ""
        return system, user
    turns = payload.get("input")
    system = str(turns[0].get("content") or "") if isinstance(turns, list) and turns else ""
    user = str(turns[1].get("content") or "") if isinstance(turns, list) and len(turns) > 1 else ""
    return system, user


def protocol_response_format(
    protocol: str,
    *,
    schema_name: str,
    enabled: bool = True,
) -> dict[str, Any] | None:
    """Return the protocol-native structured-output envelope descriptor."""
    return structured_response_format(
        normalize_protocol(protocol),
        schema_name=schema_name,
        enabled=enabled,
    )


def apply_protocol_structured_output(
    protocol: str,
    *,
    payload: Mapping[str, Any],
    provider_schema: Mapping[str, Any],
    schema_name: str,
    enabled: bool = True,
) -> dict[str, Any]:
    """Apply the protocol-native structured-output envelope to a payload.

    With ``enabled=False`` the payload is returned unchanged (deep-copied):
    the caller explicitly opted out of structured output for this call.
    """
    canonical = normalize_protocol(protocol)
    if not enabled:
        return deepcopy(dict(payload))
    return apply_provider_schema_transport(
        canonical,
        payload=payload,
        response_format=structured_response_format(canonical, schema_name=schema_name),
        provider_schema=provider_schema,
    )


def apply_protocol_structured_output_with_prompt_fallback(
    protocol: str,
    *,
    payload: Mapping[str, Any],
    provider_schema: Mapping[str, Any],
    schema_name: str,
    enabled: bool = True,
) -> tuple[dict[str, Any], str]:
    """Apply the native envelope, degrading to prompt-only mode when the schema
    cannot be admitted.

    Modes: ``native`` (protocol envelope carries the schema),
    ``json_object_prompt`` (Responses text.format json_object; schema stays a
    local contract, matching chat-completions behavior), ``prompt_only``
    (Anthropic without a forced tool; the schema stays a local contract),
    ``disabled`` (caller opted out of structured output).
    """
    canonical = normalize_protocol(protocol)
    request_payload = deepcopy(dict(payload))
    if not enabled:
        return request_payload, "disabled"
    try:
        adapted = apply_protocol_structured_output(
            canonical,
            payload=request_payload,
            provider_schema=provider_schema,
            schema_name=schema_name,
        )
        return adapted, "native"
    except ProviderSchemaCapabilityError as exc:
        _logger.warning(
            "provider schema is not admissible natively; falling back to prompt-only "
            "structured output protocol=%s schema=%s reason=%s",
            canonical,
            schema_name,
            str(exc)[:200],
        )
    if canonical == PROTOCOL_OPENAI_RESPONSES:
        text = request_payload.get("text")
        if not isinstance(text, dict):
            text = {}
            request_payload["text"] = text
        text["format"] = {"type": "json_object"}
        return request_payload, "json_object_prompt"
    return request_payload, "prompt_only"


def extract_protocol_text(protocol: str, body: Mapping[str, Any]) -> str:
    """Extract the assistant text from a protocol-native response body.

    Chat-completions bodies use the strict extractor: ``reasoning_content`` is
    never accepted as the answer (structured callers relied on exactly
    ``choices[0].message.content`` before the protocol layer existed).
    """
    canonical = normalize_protocol(protocol)
    result = dict(body)
    if canonical == PROTOCOL_OPENAI_RESPONSES:
        return _extract_responses_output_text(result)
    if canonical == PROTOCOL_ANTHROPIC_MESSAGES:
        return extract_messages_text(result)
    return extract_final_chat_content(result)[0]


def protocol_finish_reason(protocol: str, body: Mapping[str, Any]) -> str:
    """Return the protocol-native completion marker ("" when absent)."""
    canonical = normalize_protocol(protocol)
    if canonical == PROTOCOL_OPENAI_CHAT_COMPLETIONS:
        choices = body.get("choices")
        choice = choices[0] if isinstance(choices, list) and choices else None
        value = choice.get("finish_reason") if isinstance(choice, Mapping) else None
    elif canonical == PROTOCOL_ANTHROPIC_MESSAGES:
        value = body.get("stop_reason")
    else:
        value = body.get("status")
    return str(value) if value else ""


def extract_protocol_structured_text(
    protocol: str,
    body: Mapping[str, Any],
    *,
    schema_name: str,
) -> str:
    """Extract the structured-output text (tool input for Anthropic).

    When an Anthropic model answers in text instead of calling the forced
    tool, the text blocks carry the attempted answer; use them so callers can
    run their correction loop on the real model output.
    """
    canonical = normalize_protocol(protocol)
    if canonical == PROTOCOL_ANTHROPIC_MESSAGES:
        try:
            tool_input = extract_tool_use_input(dict(body), schema_name)
        except RuntimeError:
            return extract_messages_text(dict(body))
        return json.dumps(tool_input, ensure_ascii=False)
    return extract_protocol_text(canonical, body)


def billable_protocol_usage(
    protocol: str,
    body: Mapping[str, Any],
    *,
    request_text: str = "",
    response_text: str = "",
) -> NormalizedUsage | None:
    """Extract billable usage from a protocol-native body.

    All three protocols report usage dicts that
    ``billable_usage_from_openai_body`` normalizes (chat-completions
    ``prompt_tokens``/``completion_tokens``, Responses/Anthropic
    ``input_tokens``/``output_tokens``); missing usage is estimated from text.
    """
    normalize_protocol(protocol)
    return billable_usage_from_openai_body(
        dict(body),
        request_text=request_text,
        response_text=response_text,
    )


__all__ = [
    "ProviderProtocolError",
    "PROTOCOL_ANTHROPIC_MESSAGES",
    "PROTOCOL_OPENAI_CHAT_COMPLETIONS",
    "PROTOCOL_OPENAI_RESPONSES",
    "apply_protocol_structured_output",
    "apply_protocol_structured_output_with_prompt_fallback",
    "billable_protocol_usage",
    "build_protocol_chat_payload",
    "extract_protocol_structured_text",
    "extract_protocol_text",
    "normalize_protocol",
    "protocol_chat_prompts",
    "protocol_endpoint",
    "protocol_finish_reason",
    "protocol_headers",
    "protocol_response_format",
    "protocol_supports_stream",
    "reconcile_provider_protocol",
]
