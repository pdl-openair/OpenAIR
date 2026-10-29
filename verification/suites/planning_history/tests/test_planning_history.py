"""One persisted transcript feeds both refresh and the existing event stream."""

import json
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path

from app.modules.collaboration_lifecycle import build_collaboration_lifecycle_events
from app.modules.research_planning_history import load_planning_thought_history
from app.modules.research_start_thinking_store import writer_for_request
from app.modules.task_events import project_research_activity_events
from app.modules.task_public_projection import project_public_task_read_only


class SessionCursor:
    def __init__(self):
        self.row = {
            "session_id": "session",
            "owner_id": "owner",
            "workspace_id": "workspace",
            "state": "running",
            "thinking_transcript": [],
        }

    def execute(self, sql, params):
        if "SET thinking_transcript" in sql:
            self.row["thinking_transcript"] = deepcopy(params[0].obj)
        if "JOIN openair_tasks" in sql:
            assert "t.owner_id = s.owner_id" in sql
            assert "t.workspace_id = s.workspace_id" in sql
            assert params == ("task",)

    def fetchone(self):
        return self.row

    def fetchall(self):
        return [self.row]

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class SessionConnection:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor


def test_thoughts_survive_retry_confirmation_refresh_and_stream_replay():
    cursor = SessionCursor()

    @contextmanager
    def connect():
        yield SessionConnection(cursor)

    first = writer_for_request(connect, "request-1", "research_planner", 1)
    first.add_delta("先统一比较条件。")
    initial = load_planning_thought_history(cursor, "task")
    first.add_delta("然后核查证据。")
    first.seal()
    second = writer_for_request(connect, "request-2", "research_planner", 1)
    second.add_delta("这次补充数据来源。")
    second.seal()
    cursor.row["state"] = "completed"
    refreshed = load_planning_thought_history(cursor, "task")
    assert [row["summary"] for row in refreshed] == [
        "先统一比较条件。然后核查证据。",
        "这次补充数据来源。",
    ]
    assert refreshed[0]["eventId"] == initial[0]["eventId"]
    assert refreshed[0]["occurredAt"] == initial[0]["occurredAt"]
    lifecycle = build_collaboration_lifecycle_events(
        {
            "taskId": "task",
            "revision": 10,
            "runId": "execution",
            "planningThoughtHistory": refreshed,
        }
    )
    events = project_research_activity_events(lifecycle)
    assert [row["summary"] for row in events] == [row["summary"] for row in refreshed]
    assert [row["payload"]["activityKey"] for row in events] == [
        row["activityKey"] for row in refreshed
    ]
    assert (
        build_collaboration_lifecycle_events(
            {
                "taskId": "task",
                "revision": 10,
                "runId": "execution",
                "planningThoughtHistory": refreshed,
            }
        )
        == lifecycle
    )


def test_all_public_thoughts_are_recovered_without_invented_legacy_times():
    cursor = SessionCursor()
    cursor.row["thinking_transcript"] = [
        {"key": str(index), "state": "formed", "text": f"第{index}次证据核查。"}
        for index in range(15)
    ] + [{"key": "private", "state": "formed", "text": "planner schema prompt"}]
    rows = load_planning_thought_history(cursor, "task")
    assert len(rows) == 15
    assert all(row["occurredAt"] is None for row in rows)


def test_public_task_refresh_combines_thoughts_with_plan_and_execution_history():
    fixture = (
        Path(__file__).resolve().parents[4]
        / "packages/shared/test-fixtures/research-orchestration-v1.2-valid.json"
    )
    canonical = json.loads(fixture.read_text(encoding="utf-8"))["researchTask"]
    cursor = SessionCursor()
    cursor.row["thinking_transcript"] = [
        {
            "key": "request:1",
            "state": "formed",
            "text": "先核查比较条件。",
            "occurredAt": "2026-07-28T00:00:00Z",
        }
    ]
    source = {
        **canonical,
        "ownerId": "owner",
        "workspaceId": "workspace",
        "createdBy": "owner",
        "title": "研究",
        "currentStage": "solution_design",
        "stages": [],
        "planningThoughtHistory": load_planning_thought_history(cursor, "task"),
    }
    public = project_public_task_read_only(source).model_dump(mode="json", by_alias=True)
    history = public["progressProjection"]["researchProcessHistory"]
    assert any(row["summary"] == "先核查比较条件。" for row in history)
    assert any(row["phase"] == "confirmed" for row in history)
    assert "planningThoughtHistory" not in public
    assert source["planningThoughtHistory"][0]["summary"] == "先核查比较条件。"
