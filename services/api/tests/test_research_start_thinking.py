"""The collaboration transcript grows in place and does not replace earlier thoughts."""

from __future__ import annotations

import json

from app.modules.research_start_projection import _planning_progress
from app.modules.research_start_thinking import (
    consume_chat_stream,
    project_public_thoughts,
    public_thinking_text,
)


def test_public_thinking_drops_internal_sentences_and_keeps_research_language() -> None:
    text = public_thinking_text(
        "先看作者公开的监督设定。schema 要在编译器里核对。评价口径需要统一。",
    )
    assert text == "先看作者公开的监督设定。评价口径需要统一。"
    assert public_thinking_text("我在看比较口径") == "我在看比较口径"


def test_stream_deltas_become_reasoning_and_leave_the_answer_in_content() -> None:
    seen: list[str] = []
    raw = consume_chat_stream(
        [
            'data: {"choices":[{"delta":{"reasoning_content":"先看设定。"}}]}',
            'data: {"choices":[{"delta":{"content":"{\\"ok\\":true}"}}]}',
            "data: [DONE]",
        ],
        seen.append,
    )
    body = json.loads(raw)
    assert seen == ["先看设定。"]
    assert body["choices"][0]["message"]["content"] == '{"ok":true}'
    assert body["choices"][0]["message"]["reasoning_content"] == "先看设定。"


def test_projection_omits_an_empty_transcript_and_publishes_a_real_one() -> None:
    quiet = _planning_progress(
        {
            "state": "running",
            "request_state": "queued",
            "request_age_seconds": 1,
        }
    )
    assert "thoughts" not in quiet
    loud = _planning_progress(
        {
            "state": "running",
            "attempt_phase": "research_planner",
            "attempt_state": "started",
            "attempt_ordinal": 1,
            "thinking_transcript": [
                {"key": "research_planner:1", "state": "forming", "text": "先看作者设定。"},
                {"key": "noise", "state": "formed", "text": "planner schema prompt"},
            ],
        }
    )
    assert loud["thoughts"] == [
        {"key": "research_planner:1", "state": "forming", "text": "先看作者设定。"},
    ]
    assert project_public_thoughts(loud["thoughts"])[0]["text"] == "先看作者设定。"
