"""Coverage for OpenAI-compatible thinking toggle and final-content extraction."""

from __future__ import annotations

import pytest

from llm_core.openai_compatible_client import (
    apply_openai_compatible_thinking,
    chat_completions_url,
    extract_final_chat_content,
    ProviderEndpoint,
    _provider_transport_timeout,
)


def test_shared_openai_transport_does_not_turn_soft_budget_into_read_deadline() -> None:
    timeout = _provider_transport_timeout(
        ProviderEndpoint(
            base_url="https://provider.invalid",
            api_key="secret",
            timeout_seconds=180.0,
        )
    )

    assert timeout.read is None
    assert timeout.connect == 5.0
    assert timeout.pool == 5.0
    assert timeout.write == 10.0


def test_chat_completions_url_openai_style() -> None:
    assert chat_completions_url("https://api.openai.com") == "https://api.openai.com/v1/chat/completions"
    assert chat_completions_url("https://opencode.ai/zen") == "https://opencode.ai/zen/v1/chat/completions"
    assert chat_completions_url("https://opencode.ai/zen/v1") == "https://opencode.ai/zen/v1/chat/completions"


def test_chat_completions_url_zhipu_paas_v4() -> None:
    assert (
        chat_completions_url("https://open.bigmodel.cn/api/paas/v4")
        == "https://open.bigmodel.cn/api/paas/v4/chat/completions"
    )


def test_apply_thinking_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAIR_RESEARCH_START_PLANNER_LLM_THINKING", "disabled")
    out = apply_openai_compatible_thinking({"model": "m"}, "OPENAIR_RESEARCH_START_PLANNER_LLM_THINKING")
    assert out["thinking"] == {"type": "disabled"}
    assert out["reasoning_effort"] == "none"


def test_apply_thinking_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAIR_RESEARCH_START_PLANNER_LLM_THINKING", "enabled")
    out = apply_openai_compatible_thinking(
        {"model": "m", "reasoning_effort": "high"},
        "OPENAIR_RESEARCH_START_PLANNER_LLM_THINKING",
    )
    assert out["thinking"] == {"type": "enabled"}
    assert "reasoning_effort" not in out


def test_apply_thinking_unset_leaves_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAIR_RESEARCH_START_PLANNER_LLM_THINKING", raising=False)
    payload = {"model": "m"}
    assert apply_openai_compatible_thinking(payload, "OPENAIR_RESEARCH_START_PLANNER_LLM_THINKING") == payload


def test_extract_final_content_prefers_message_content() -> None:
    content, choice = extract_final_chat_content(
        {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "content": '{"ok": true}',
                        "reasoning_content": "thinking prose",
                    },
                }
            ]
        }
    )
    assert content == '{"ok": true}'
    assert choice["finish_reason"] == "stop"


def test_extract_final_content_rejects_reasoning_only_truncation() -> None:
    with pytest.raises(ValueError, match="truncated while reasoning"):
        extract_final_chat_content(
            {
                "choices": [
                    {
                        "finish_reason": "length",
                        "message": {
                            "content": "",
                            "reasoning_content": "still thinking about the schema",
                        },
                    }
                ]
            }
        )


def test_extract_final_content_rejects_reasoning_without_content() -> None:
    with pytest.raises(ValueError, match="reasoning_content without final content"):
        extract_final_chat_content(
            {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": "",
                            "reasoning_content": "done thinking but forgot the answer",
                        },
                    }
                ]
            }
        )


def test_usage_wire_from_openai_body_estimates_when_missing() -> None:
    from llm_core.openai_compatible_client import usage_wire_from_openai_body

    wire = usage_wire_from_openai_body(
        {"choices": [{"message": {"content": "abcdefgh"}}]},  # 8 chars → 2
        request_text="abcd",  # 4 chars → 1
    )
    assert wire["estimated"] is True
    assert wire["inputTokens"] == 1
    assert wire["outputTokens"] == 2
    assert wire["totalTokens"] == 3


def test_usage_wire_from_openai_body_prefers_provider_usage() -> None:
    from llm_core.openai_compatible_client import usage_wire_from_openai_body

    wire = usage_wire_from_openai_body(
        {
            "choices": [{"message": {"content": "ignored"}}],
            "usage": {"prompt_tokens": 9, "completion_tokens": 4, "total_tokens": 13},
        }
    )
    assert wire["estimated"] is False
    assert wire["totalTokens"] == 13
