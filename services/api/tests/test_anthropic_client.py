"""Anthropic Messages 传输契约测试（llm_core.anthropic_client）。

期望值来自 Anthropic Messages API 公开契约，不从实现反推。
"""

from __future__ import annotations

import pytest
from llm_core import anthropic_client
from llm_core.anthropic_client import (
    anthropic_headers,
    extract_messages_text,
    extract_tool_use_input,
    invoke_messages,
    messages_url,
)
from llm_core.openai_compatible_client import ProviderEndpoint
from llm_core.token_usage import from_wire_usage


def test_messages_url_appends_v1_messages_for_bare_host() -> None:
    assert messages_url("https://api.anthropic.com") == "https://api.anthropic.com/v1/messages"


def test_messages_url_reuses_existing_v1_prefix() -> None:
    assert messages_url("https://gateway.example.com/v1") == "https://gateway.example.com/v1/messages"


def test_messages_url_keeps_endpoint_already_ending_in_messages() -> None:
    assert messages_url("https://gateway.example.com/v1/messages") == "https://gateway.example.com/v1/messages"


def test_messages_url_rejects_empty_base() -> None:
    with pytest.raises(ValueError, match="empty"):
        messages_url("   ")


def test_anthropic_headers_use_api_key_header_without_bearer() -> None:
    headers = anthropic_headers("sk-ant-test")
    assert headers["x-api-key"] == "sk-ant-test"
    assert headers["anthropic-version"] == "2023-06-01"
    assert headers["Content-Type"] == "application/json"
    assert not any(name.lower() == "authorization" for name in headers)


def test_anthropic_headers_allow_version_override() -> None:
    headers = anthropic_headers("sk-ant-test", api_version="2024-01-01")
    assert headers["anthropic-version"] == "2024-01-01"


def test_extract_messages_text_joins_text_blocks() -> None:
    body = {
        "id": "msg_1",
        "stop_reason": "end_turn",
        "content": [
            {"type": "text", "text": "第一段"},
            {"type": "text", "text": "第二段"},
        ],
    }
    assert extract_messages_text(body) == "第一段第二段"


def test_extract_messages_text_ignores_non_text_blocks() -> None:
    body = {
        "stop_reason": "tool_use",
        "content": [
            {"type": "text", "text": "答案"},
            {"type": "tool_use", "id": "t1", "name": "emit", "input": {"ok": True}},
        ],
    }
    assert extract_messages_text(body) == "答案"


def test_extract_messages_text_raises_for_refusal_stop_reason() -> None:
    body = {"stop_reason": "refusal", "content": [{"type": "text", "text": "不能回答"}]}
    with pytest.raises(RuntimeError, match="refusal"):
        extract_messages_text(body)


def test_extract_messages_text_raises_when_no_text_block() -> None:
    body = {
        "stop_reason": "end_turn",
        "content": [{"type": "tool_use", "id": "t1", "name": "emit", "input": {}}],
    }
    with pytest.raises(RuntimeError, match="missing text content"):
        extract_messages_text(body)


def test_extract_tool_use_input_returns_named_tool_payload() -> None:
    body = {
        "stop_reason": "tool_use",
        "content": [
            {"type": "text", "text": "使用工具"},
            {
                "type": "tool_use",
                "id": "t1",
                "name": "emit_result",
                "input": {"title": "t", "content": "{}"},
            },
        ],
    }
    assert extract_tool_use_input(body, "emit_result") == {"title": "t", "content": "{}"}


def test_extract_tool_use_input_raises_when_tool_missing() -> None:
    body = {"stop_reason": "end_turn", "content": [{"type": "text", "text": "直接回答"}]}
    with pytest.raises(RuntimeError, match="missing tool_use"):
        extract_tool_use_input(body, "emit_result")


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class _FakeClient:
    def __init__(self, payload: dict, calls: list[dict]) -> None:
        self._payload = payload
        self._calls = calls

    def post(self, url: str, *, headers: dict, json: dict) -> _FakeResponse:
        self._calls.append({"url": url, "headers": dict(headers), "payload": json})
        return _FakeResponse(self._payload)

    def __enter__(self) -> _FakeClient:
        return self

    def __exit__(self, *args: object) -> None:
        return None


def test_post_messages_sends_to_messages_endpoint_with_anthropic_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict] = []
    monkeypatch.setattr(
        anthropic_client.httpx,
        "Client",
        lambda timeout, trust_env=False: _FakeClient(
            {"stop_reason": "end_turn", "content": []}, calls
        ),
    )
    body = anthropic_client.post_messages(
        ProviderEndpoint(base_url="https://gw.example.com", api_key="sk-ant-test"),
        {"model": "claude-sonnet-4-5", "max_tokens": 16, "messages": []},
    )
    assert body == {"stop_reason": "end_turn", "content": []}
    assert len(calls) == 1
    assert calls[0]["url"] == "https://gw.example.com/v1/messages"
    assert calls[0]["headers"]["x-api-key"] == "sk-ant-test"
    assert calls[0]["headers"]["anthropic-version"] == "2023-06-01"


def test_invoke_messages_extracts_text_and_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "model": "claude-sonnet-4-5",
        "max_tokens": 64,
        "system": "只输出 JSON",
        "messages": [{"role": "user", "content": "你好"}],
    }
    response_body = {
        "stop_reason": "end_turn",
        "content": [{"type": "text", "text": '{"ok": true}'}],
        "usage": {"input_tokens": 7, "output_tokens": 3},
    }
    calls: list[dict] = []
    monkeypatch.setattr(
        anthropic_client.httpx,
        "Client",
        lambda timeout, trust_env=False: _FakeClient(response_body, calls),
    )
    endpoint = ProviderEndpoint(base_url="https://gw.example.com", api_key="k")
    result = invoke_messages(endpoint, payload)
    assert result.content == '{"ok": true}'
    usage = from_wire_usage(result.usage_wire)
    assert usage is not None
    assert usage["input_tokens"] == 7
    assert usage["output_tokens"] == 3
    assert usage["total_tokens"] == 10
    assert usage["estimated"] is False


def test_normalize_openai_usage_reads_anthropic_usage_shape() -> None:
    """Anthropic usage 直接复用既有归一（input_tokens/output_tokens），锁定不回归。"""
    from llm_core.token_usage import normalize_openai_usage

    usage = normalize_openai_usage({"input_tokens": 4, "output_tokens": 6})
    assert usage == {
        "input_tokens": 4,
        "output_tokens": 6,
        "total_tokens": 10,
        "estimated": False,
        "raw": {"input_tokens": 4, "output_tokens": 6},
    }
