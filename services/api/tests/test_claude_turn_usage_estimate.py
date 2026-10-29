"""Claude non-stream turn estimates usage when the SDK omits it."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from llm_core import claude_turn


class _Assistant:
    def __init__(self, text: str) -> None:
        self.content = [SimpleNamespace(text=text)]


@pytest.mark.asyncio
async def test_run_turn_estimates_usage_when_sdk_omits_it(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_query(*, prompt: str, options: object):
        yield _Assistant("abcdefgh")  # 8 chars → 2 tokens
        # No ResultMessage.usage

    monkeypatch.setattr(
        "claude_agent_sdk.AssistantMessage",
        _Assistant,
        raising=False,
    )
    monkeypatch.setattr(
        "claude_agent_sdk.TextBlock",
        SimpleNamespace,
        raising=False,
    )
    monkeypatch.setattr("claude_agent_sdk.ResultMessage", type("ResultMessage", (), {}), raising=False)
    monkeypatch.setattr("claude_agent_sdk.query", fake_query)

    # extract_text_from_message imports AssistantMessage/TextBlock inside the function;
    # patch the helper to return our text directly for a focused estimate test.
    monkeypatch.setattr(claude_turn, "extract_text_from_message", lambda message: "abcdefgh")

    result = await claude_turn.run_turn(
        options=SimpleNamespace(),
        prompt="hi",
        resume_session_id=None,
        timeout_seconds=5,
    )
    assert result.reply == "abcdefgh"
    assert result.usage is not None
    assert result.usage["estimated"] is True
    assert result.usage["output_tokens"] == 2


@pytest.mark.asyncio
async def test_run_turn_timeout_keeps_usage_none(monkeypatch: pytest.MonkeyPatch) -> None:
    async def slow_query(*, prompt: str, options: object):
        await asyncio.sleep(10)
        yield _Assistant("late")

    monkeypatch.setattr("claude_agent_sdk.query", slow_query)
    monkeypatch.setattr("claude_agent_sdk.ResultMessage", type("ResultMessage", (), {}), raising=False)

    result = await claude_turn.run_turn(
        options=SimpleNamespace(),
        prompt="hi",
        resume_session_id=None,
        timeout_seconds=0.01,
    )
    assert "超时" in result.reply
    assert result.usage is None
