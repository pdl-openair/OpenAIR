"""Unit tests for the provider-neutral token usage normalizer (S03-14-02)."""

from __future__ import annotations

from llm_core.token_usage import (
    billable_usage_from_openai_body,
    estimate_usage_from_text,
    from_wire_usage,
    normalize_claude_sdk_usage,
    normalize_openai_usage,
    to_wire_usage,
)


def test_normalize_openai_chat_completions_usage() -> None:
    result = normalize_openai_usage({"prompt_tokens": 12, "completion_tokens": 30, "total_tokens": 42})
    assert result == {
        "input_tokens": 12,
        "output_tokens": 30,
        "total_tokens": 42,
        "estimated": False,
        "raw": {"prompt_tokens": 12, "completion_tokens": 30, "total_tokens": 42},
    }


def test_normalize_openai_responses_api_usage() -> None:
    result = normalize_openai_usage({"input_tokens": 7, "output_tokens": 11})
    assert result is not None
    assert result["input_tokens"] == 7
    assert result["output_tokens"] == 11
    assert result["total_tokens"] == 18


def test_normalize_openai_usage_none_when_empty() -> None:
    assert normalize_openai_usage(None) is None
    assert normalize_openai_usage({}) is None
    assert normalize_openai_usage({"prompt_tokens": 0, "completion_tokens": 0}) is None


def test_normalize_claude_sdk_dict_usage() -> None:
    usage = {
        "input_tokens": 100,
        "output_tokens": 50,
        "cache_creation_input_tokens": 20,
        "cache_read_input_tokens": 5,
    }
    result = normalize_claude_sdk_usage(usage)
    assert result is not None
    # Cache fields preserved in raw but NOT counted toward deduction (S03-14-01 §5).
    assert result["input_tokens"] == 100
    assert result["output_tokens"] == 50
    assert result["total_tokens"] == 150
    assert result["raw"]["cache_creation_input_tokens"] == 20


def test_normalize_claude_sdk_attribute_object() -> None:
    class _Usage:
        input_tokens = 8
        output_tokens = 4

    result = normalize_claude_sdk_usage(_Usage())
    assert result is not None
    assert result["total_tokens"] == 12


def test_estimate_usage_from_text_ceil_division() -> None:
    est = estimate_usage_from_text("a" * 9)  # 9 chars → ceil(9/4) = 3
    assert est["output_tokens"] == 3
    assert est["estimated"] is True
    assert est["input_tokens"] == 0
    assert estimate_usage_from_text("")["output_tokens"] == 0


def test_wire_round_trip() -> None:
    normalized = normalize_openai_usage({"prompt_tokens": 5, "completion_tokens": 9})
    wire = to_wire_usage(normalized)
    assert wire == {
        "inputTokens": 5,
        "outputTokens": 9,
        "totalTokens": 14,
        "estimated": False,
        "raw": {"prompt_tokens": 5, "completion_tokens": 9},
    }
    back = from_wire_usage(wire)
    assert back is not None
    assert back["input_tokens"] == 5
    assert back["output_tokens"] == 9
    assert back["total_tokens"] == 14


def test_to_wire_usage_none() -> None:
    assert to_wire_usage(None) is None
    assert from_wire_usage(None) is None
    assert from_wire_usage({"inputTokens": 0, "outputTokens": 0}) is None


def test_billable_usage_prefers_provider_usage() -> None:
    body = {
        "choices": [{"message": {"content": "hello world"}}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 5, "total_tokens": 8},
    }
    usage = billable_usage_from_openai_body(body, request_text="ignored")
    assert usage["estimated"] is False
    assert usage["total_tokens"] == 8


def test_billable_usage_estimates_when_usage_missing() -> None:
    content = "x" * 8  # ceil(8/4) = 2
    body = {"choices": [{"message": {"content": content}}]}
    usage = billable_usage_from_openai_body(body, request_text="abcd")  # ceil(4/4) = 1
    assert usage["estimated"] is True
    assert usage["input_tokens"] == 1
    assert usage["output_tokens"] == 2
    assert usage["total_tokens"] == 3
    assert usage["raw"] == {"reason": "provider_usage_missing_estimated"}


def test_billable_usage_floor_when_empty_body() -> None:
    usage = billable_usage_from_openai_body({}, request_text="")
    assert usage["estimated"] is True
    assert usage["total_tokens"] == 1
