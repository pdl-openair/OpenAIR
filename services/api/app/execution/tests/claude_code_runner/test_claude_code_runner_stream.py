from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace

import pytest

from app.execution import claude_code_runner


@pytest.mark.asyncio
async def test_stream_timeout_emits_terminal_event_after_producer_cancellation(monkeypatch: pytest.MonkeyPatch) -> None:
    async def silent_query(*_args, **_kwargs):
        await asyncio.Event().wait()
        yield None

    sdk = SimpleNamespace(
        AssistantMessage=type("AssistantMessage", (), {}),
        ResultMessage=type("ResultMessage", (), {}),
        StreamEvent=type("StreamEvent", (), {}),
        TaskProgressMessage=type("TaskProgressMessage", (), {}),
        query=silent_query,
    )
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", sdk)
    monkeypatch.setattr(claude_code_runner, "_build_options", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(claude_code_runner, "CLAUDE_INITIAL_STREAM_TIMEOUT_SECONDS", 0.03)
    monkeypatch.setattr(claude_code_runner, "CLAUDE_WAIT_PROGRESS_INTERVAL_SECONDS", 0.005)

    events = [
        event
        async for event in claude_code_runner.run_claude_code_chat_stream(
            {"taskId": "task_timeout"},
            "Run the selected Skill.",
        )
    ]

    # Terminal done event now carries a `usage` key (S03-14-02); on a producer
    # cancellation with no ResultMessage it is None (no usage observed).
    done = events[-1]
    assert done["type"] == "done"
    assert done["sessionId"] is None
    assert done["error"] == "timeout"
    assert done["usage"] is None
    assert events[-2]["type"] == "assistant_delta"
    assert any(event["type"] == "progress" for event in events)
