"""协议适配层（app.execution.provider_protocol）契约测试。

三种协议（openai_chat_completions、openai_responses、anthropic_messages）的
端点、认证头、请求体、响应提取、结构化输出封装都只经这一层。期望值来自各
协议公开 wire 契约，不从实现反推。
"""

from __future__ import annotations

import pytest
from app.execution.provider_protocol import (
    ProviderProtocolError,
    apply_protocol_structured_output,
    apply_protocol_structured_output_with_prompt_fallback,
    billable_protocol_usage,
    build_protocol_chat_payload,
    extract_protocol_structured_text,
    extract_protocol_text,
    normalize_protocol,
    protocol_chat_prompts,
    protocol_endpoint,
    protocol_finish_reason,
    protocol_headers,
    protocol_response_format,
    protocol_supports_stream,
    reconcile_provider_protocol,
)

CHAT = "openai_chat_completions"
RESPONSES = "openai_responses"
ANTHROPIC = "anthropic_messages"

_MESSAGES = [
    {"role": "system", "content": "只输出 JSON"},
    {"role": "user", "content": "生成方案"},
]


@pytest.mark.parametrize(
    ("provider", "expected"),
    [
        ("huiyan_chat_completions", CHAT),
        ("openai_chat_completions", CHAT),
        ("openai_responses", RESPONSES),
        ("anthropic_messages", ANTHROPIC),
    ],
)
def test_normalize_protocol_accepts_adapter_names_and_aliases(provider: str, expected: str) -> None:
    assert normalize_protocol(provider) == expected


def test_normalize_protocol_rejects_unknown_provider() -> None:
    with pytest.raises(ProviderProtocolError, match="Unsupported provider protocol"):
        normalize_protocol("openai_files")


@pytest.mark.parametrize(
    ("protocol", "base", "expected_path"),
    [
        (CHAT, "https://gw.example.com", "/v1/chat/completions"),
        (RESPONSES, "https://gw.example.com", "/v1/responses"),
        (ANTHROPIC, "https://gw.example.com", "/v1/messages"),
    ],
)
def test_protocol_endpoint_appends_native_paths(
    protocol: str, base: str, expected_path: str,
) -> None:
    assert protocol_endpoint(protocol, base) == f"{base}{expected_path}"


def test_protocol_endpoint_handles_v1_bases_for_all_protocols() -> None:
    assert protocol_endpoint(CHAT, "https://gw.example.com/v1") == "https://gw.example.com/v1/chat/completions"
    assert protocol_endpoint(RESPONSES, "https://gw.example.com/v1") == "https://gw.example.com/v1/responses"
    assert protocol_endpoint(ANTHROPIC, "https://gw.example.com/v1") == "https://gw.example.com/v1/messages"


def test_protocol_headers_select_bearer_for_openai_family() -> None:
    headers = protocol_headers(CHAT, "sk-test")
    assert headers["Authorization"] == "Bearer sk-test"
    assert headers["Content-Type"] == "application/json"
    assert protocol_headers(RESPONSES, "sk-test")["Authorization"] == "Bearer sk-test"


def test_protocol_headers_use_api_key_header_for_anthropic() -> None:
    headers = protocol_headers(ANTHROPIC, "sk-ant-test")
    assert headers["x-api-key"] == "sk-ant-test"
    assert headers["anthropic-version"] == "2023-06-01"
    assert not any(name.lower() == "authorization" for name in headers)


def test_streaming_is_chat_only() -> None:
    assert protocol_supports_stream(CHAT) is True
    assert protocol_supports_stream(RESPONSES) is False
    assert protocol_supports_stream(ANTHROPIC) is False


def test_chat_payload_keeps_messages_and_max_tokens() -> None:
    payload = build_protocol_chat_payload(
        CHAT,
        model="m1",
        messages=list(_MESSAGES),
        max_tokens=800,
        temperature=0.3,
    )
    assert payload["model"] == "m1"
    assert payload["messages"] == _MESSAGES
    assert payload["max_tokens"] == 800
    assert payload["temperature"] == 0.3
    assert "input" not in payload
    assert "system" not in payload


def test_responses_payload_moves_messages_to_input_and_caps_output() -> None:
    payload = build_protocol_chat_payload(
        RESPONSES,
        model="m1",
        messages=list(_MESSAGES),
        max_tokens=700,
    )
    assert payload["model"] == "m1"
    assert payload["input"] == _MESSAGES
    assert payload["max_output_tokens"] == 700
    assert payload["store"] is False
    assert "messages" not in payload
    assert "max_tokens" not in payload


def test_responses_payload_prefers_reasoning_over_temperature() -> None:
    payload = build_protocol_chat_payload(
        RESPONSES,
        model="gpt-5.6",
        messages=list(_MESSAGES),
        max_tokens=700,
        reasoning_effort="minimal",
        temperature=0.1,
    )
    assert payload["reasoning"] == {"effort": "minimal"}
    assert "temperature" not in payload


def test_anthropic_payload_lifts_system_and_requires_max_tokens() -> None:
    payload = build_protocol_chat_payload(
        ANTHROPIC,
        model="claude-sonnet-4-5",
        messages=[
            {"role": "system", "content": "只输出 JSON"},
            {"role": "user", "content": "生成方案"},
        ],
        max_tokens=1024,
    )
    assert payload["model"] == "claude-sonnet-4-5"
    assert payload["system"] == "只输出 JSON"
    assert payload["messages"] == [{"role": "user", "content": "生成方案"}]
    assert payload["max_tokens"] == 1024

    with pytest.raises(ProviderProtocolError, match="max_tokens"):
        build_protocol_chat_payload(
            ANTHROPIC,
            model="claude-sonnet-4-5",
            messages=[{"role": "user", "content": "u"}],
        )


def test_anthropic_payload_keeps_assistant_turns_for_correction() -> None:
    payload = build_protocol_chat_payload(
        ANTHROPIC,
        model="m",
        messages=[
            {"role": "system", "content": "s"},
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "上一轮答案"},
            {"role": "user", "content": "纠正它"},
        ],
        max_tokens=64,
    )
    assert [turn["role"] for turn in payload["messages"]] == ["user", "assistant", "user"]


def test_chat_prompts_round_trip_for_all_protocols() -> None:
    for protocol in (CHAT, RESPONSES, ANTHROPIC):
        payload = build_protocol_chat_payload(
            protocol,
            model="m",
            messages=list(_MESSAGES),
            max_tokens=128,
        )
        system_prompt, user_prompt = protocol_chat_prompts(protocol, payload)
        assert system_prompt == "只输出 JSON"
        assert user_prompt == "生成方案"


def _closed_schema() -> dict:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": ["ok"],
        "properties": {"ok": {"type": "boolean"}},
    }


def test_structured_output_envelope_matches_protocol() -> None:
    assert protocol_response_format(CHAT, schema_name="ignored") == {"type": "json_object"}
    assert protocol_response_format(RESPONSES, schema_name="emit_v1") == {
        "type": "json_schema",
        "name": "emit_v1",
    }
    assert protocol_response_format(ANTHROPIC, schema_name="emit_v1") == {
        "type": "tool_use",
        "name": "emit_v1",
    }
    assert protocol_response_format(CHAT, schema_name="x", enabled=False) is None


def test_apply_structured_output_keeps_chat_json_mode_with_alias() -> None:
    payload = build_protocol_chat_payload(
        "huiyan_chat_completions",
        model="m",
        messages=list(_MESSAGES),
        max_tokens=64,
    )
    adapted = apply_protocol_structured_output(
        CHAT,
        payload=payload,
        provider_schema=_closed_schema(),
        schema_name="emit_v1",
    )
    assert adapted["response_format"] == {"type": "json_object"}
    assert adapted["messages"] == payload["messages"]


def test_apply_structured_output_disabled_returns_payload_unchanged() -> None:
    payload = build_protocol_chat_payload(
        ANTHROPIC, model="m", messages=list(_MESSAGES), max_tokens=64
    )
    adapted = apply_protocol_structured_output(
        ANTHROPIC,
        payload=payload,
        provider_schema=_closed_schema(),
        schema_name="emit_v1",
        enabled=False,
    )
    assert adapted == payload
    assert "tools" not in adapted


def test_apply_structured_output_anthropic_forces_tool() -> None:
    payload = build_protocol_chat_payload(
        ANTHROPIC, model="m", messages=list(_MESSAGES), max_tokens=64
    )
    adapted = apply_protocol_structured_output(
        ANTHROPIC,
        payload=payload,
        provider_schema=_closed_schema(),
        schema_name="emit_v1",
    )
    assert adapted["tool_choice"] == {"type": "tool", "name": "emit_v1"}
    assert adapted["tools"][0]["name"] == "emit_v1"
    assert adapted["tools"][0]["input_schema"]["properties"] == {"ok": {"type": "boolean"}}


def test_extract_text_for_each_protocol() -> None:
    chat_body = {"choices": [{"message": {"content": "{\"ok\": true}"}, "finish_reason": "stop"}]}
    responses_body = {"output": [{"content": [{"type": "output_text", "text": "{\"ok\": true}"}]}]}
    anthropic_body = {
        "stop_reason": "end_turn",
        "content": [{"type": "text", "text": '{"ok": true}'}],
    }
    assert extract_protocol_text(CHAT, chat_body) == '{"ok": true}'
    assert extract_protocol_text(RESPONSES, responses_body) == '{"ok": true}'
    assert extract_protocol_text(ANTHROPIC, anthropic_body) == '{"ok": true}'


def test_extract_structured_text_reads_anthropic_tool_input() -> None:
    body = {
        "stop_reason": "tool_use",
        "content": [
            {"type": "tool_use", "id": "t1", "name": "emit_v1", "input": {"ok": True}},
        ],
    }
    assert (
        extract_protocol_structured_text(ANTHROPIC, body, schema_name="emit_v1")
        == '{"ok": true}'
    )


def test_extract_structured_text_falls_back_to_plain_text() -> None:
    chat_body = {"choices": [{"message": {"content": "{\"ok\": true}"}}]}
    assert extract_protocol_structured_text(CHAT, chat_body, schema_name="x") == '{"ok": true}'


def test_finish_reason_per_protocol() -> None:
    assert protocol_finish_reason(CHAT, {"choices": [{"finish_reason": "stop"}]}) == "stop"
    assert protocol_finish_reason(RESPONSES, {"status": "completed"}) == "completed"
    assert protocol_finish_reason(ANTHROPIC, {"stop_reason": "end_turn"}) == "end_turn"
    assert protocol_finish_reason(CHAT, {}) == ""


def test_billable_usage_reads_anthropic_usage() -> None:
    usage = billable_protocol_usage(
        ANTHROPIC,
        {"usage": {"input_tokens": 7, "output_tokens": 3}},
        request_text="请求",
        response_text="回答",
    )
    assert usage is not None
    assert usage["input_tokens"] == 7
    assert usage["output_tokens"] == 3
    assert usage["total_tokens"] == 10
    assert usage["estimated"] is False


def test_billable_usage_estimates_when_provider_omits_usage() -> None:
    usage = billable_protocol_usage(
        ANTHROPIC,
        {"stop_reason": "end_turn", "content": [{"type": "text", "text": "abcd"}]},
        request_text="1234",
        response_text="abcd",
    )
    assert usage is not None
    assert usage["estimated"] is True
    assert usage["input_tokens"] == 1
    assert usage["output_tokens"] == 1


def test_structured_output_falls_back_to_json_object_for_all_optional_schema() -> None:
    all_optional_schema = {"type": "object", "properties": {"goal": {"type": "string"}}}

    adapted, mode = apply_protocol_structured_output_with_prompt_fallback(
        RESPONSES,
        payload={"model": "m", "input": [], "max_output_tokens": 8},
        provider_schema=all_optional_schema,
        schema_name="brief_v1",
    )

    assert mode == "json_object_prompt"
    assert adapted["text"]["format"] == {"type": "json_object"}


def test_structured_output_falls_back_to_prompt_only_for_anthropic() -> None:
    all_optional_schema = {"type": "object", "properties": {"goal": {"type": "string"}}}

    adapted, mode = apply_protocol_structured_output_with_prompt_fallback(
        ANTHROPIC,
        payload={"model": "m", "messages": [], "max_tokens": 8},
        provider_schema=all_optional_schema,
        schema_name="brief_v1",
    )

    assert mode == "prompt_only"
    assert "tools" not in adapted
    assert "tool_choice" not in adapted


def test_structured_output_fallback_keeps_native_envelope_when_admitted() -> None:
    adapted, mode = apply_protocol_structured_output_with_prompt_fallback(
        RESPONSES,
        payload={"model": "m", "input": [], "max_output_tokens": 8},
        provider_schema=_closed_schema(),
        schema_name="emit_v1",
    )

    assert mode == "native"
    assert adapted["text"]["format"]["type"] == "json_schema"


def test_chat_extraction_rejects_reasoning_only_body() -> None:
    reasoning_only = {
        "choices": [
            {
                "message": {"content": "", "reasoning_content": "让我想想"},
                "finish_reason": "length",
            }
        ]
    }
    with pytest.raises(ValueError):
        extract_protocol_text(CHAT, reasoning_only)


# ---------------------------------------------------------------------------
# Explicit selector vs. registry adapter reconciliation.
#
# The registry supplies the credentials; an explicit protocol selector that
# names a different protocol would send one protocol's body to another
# gateway's key, so it must fail closed before any Provider I/O.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("selector", "registry_adapter"),
    [
        ("openai_chat_completions", "huiyan_chat_completions"),
        ("huiyan_chat_completions", "openai_chat_completions"),
        ("anthropic_messages", "anthropic_messages"),
        ("openai_responses", "openai_responses"),
    ],
)
def test_reconcile_accepts_selector_that_matches_registry_protocol(
    selector: str, registry_adapter: str
) -> None:
    assert (
        reconcile_provider_protocol(
            selector, registry_adapter, selector="OPENTRAJECTORY_LLM_PROVIDER"
        )
        == normalize_protocol(registry_adapter)
    )


@pytest.mark.parametrize(
    ("selector", "registry_adapter", "selected_name", "registry_name"),
    [
        ("anthropic_messages", "huiyan_chat_completions", ANTHROPIC, CHAT),
        ("openai_responses", "huiyan_chat_completions", RESPONSES, CHAT),
        ("openai_chat_completions", "anthropic_messages", CHAT, ANTHROPIC),
    ],
)
def test_reconcile_rejects_selector_conflicting_with_registry_adapter(
    selector: str, registry_adapter: str, selected_name: str, registry_name: str
) -> None:
    with pytest.raises(ProviderProtocolError) as raised:
        reconcile_provider_protocol(
            selector, registry_adapter, selector="OPENTRAJECTORY_LLM_PROVIDER"
        )

    message = str(raised.value)
    assert "OPENTRAJECTORY_LLM_PROVIDER=" in message
    assert selected_name in message
    assert registry_name in message
    assert registry_adapter in message


def test_reconcile_uses_registry_adapter_without_a_selector() -> None:
    assert (
        reconcile_provider_protocol(
            "", "anthropic_messages", selector="OPENTRAJECTORY_LLM_PROVIDER"
        )
        == ANTHROPIC
    )


def test_reconcile_honors_legacy_selector_when_registry_has_no_adapter() -> None:
    assert (
        reconcile_provider_protocol(
            "openai_responses",
            "",
            selector="OPENTRAJECTORY_LLM_PROVIDER",
            default_provider="huiyan_chat_completions",
        )
        == RESPONSES
    )


def test_reconcile_defaults_to_chat_completions_without_selector_or_adapter() -> None:
    assert (
        reconcile_provider_protocol(
            "",
            "",
            selector="OPENTRAJECTORY_LLM_PROVIDER",
            default_provider="huiyan_chat_completions",
        )
        == CHAT
    )


def test_reconcile_rejects_unknown_selector() -> None:
    with pytest.raises(ProviderProtocolError, match="Unsupported provider protocol"):
        reconcile_provider_protocol(
            "openai_files", "", selector="OPENTRAJECTORY_LLM_PROVIDER"
        )
