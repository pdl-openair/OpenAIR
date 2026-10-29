"""Same-study plan regeneration keeps the original intent and drops prior attempts."""

from __future__ import annotations

import json
from typing import Any

from app.modules.research_start_plan_regeneration import regenerate_failed_research_plan
from app.modules.research_start_repository import ResearchStartRepository

TASK_ID = "task_" + "ab" * 16
SESSION_ID = "intent_abc123def456"
OWNER = "owner-1"
WORKSPACE = "workspace-1"


class _Cursor:
    def __init__(self, rows_for) -> None:
        self.rows_for = rows_for
        self.statements: list[tuple[str, Any]] = []
        self.rowcount = 0
        self._row: Any = None

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *_args: object) -> bool:
        return False

    def execute(self, sql: str, params: Any = None) -> None:
        text = " ".join(str(sql).split())
        self.statements.append((text, params))
        self.rowcount = 1
        self._row = self.rows_for(text, params)

    def fetchone(self) -> Any:
        return self._row


class _Connection:
    def __init__(self, cursor: _Cursor) -> None:
        self.cursor_obj = cursor
        self.committed = False

    def cursor(self) -> _Cursor:
        return self.cursor_obj

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        return None

    def close(self) -> None:
        return None


def _session(state: str) -> dict[str, str]:
    return {
        "session_id": SESSION_ID,
        "owner_id": OWNER,
        "workspace_id": WORKSPACE,
        "state": state,
        "reserved_task_id": TASK_ID,
    }


def _prior(intent: dict[str, Any]) -> dict[str, Any]:
    return {
        "request_id": "rsr_" + "cd" * 16,
        "session_id": SESSION_ID,
        "owner_id": OWNER,
        "workspace_id": WORKSPACE,
        "fingerprint": "f" * 64,
        "request_payload": intent,
        "state": "failed",
    }


def _regenerate(
    monkeypatch,
    *,
    state: str,
    intent: dict[str, Any],
    task_payload: dict[str, Any],
    authorized: bool = True,
):
    prior = _prior(intent)
    inserted = {"request_id": "rsr_" + "ef" * 16}

    def rows_for(sql: str, _params: Any) -> Any:
        if "FROM research_start_sessions" in sql:
            return _session(state)
        if "state IN ('queued', 'leased')" in sql:
            return None
        if "FROM openair_tasks" in sql:
            return {"payload": task_payload}
        if "ORDER BY created_at DESC" in sql:
            return prior
        if "INSERT INTO research_start_requests" in sql:
            return inserted
        return None

    cursor = _Cursor(rows_for)
    connection = _Connection(cursor)
    monkeypatch.setattr(
        "app.modules.research_start_plan_regeneration.apply_research_start_lifecycle_fact_locked",
        lambda *_args, **_kwargs: authorized,
    )
    monkeypatch.setattr(
        ResearchStartRepository,
        "_enforce_durable_admission",
        lambda self, cur, owner_id, workspace_id: None,
    )
    receipt = regenerate_failed_research_plan(
        owner_id=OWNER,
        workspace_id=WORKSPACE,
        task_id=TASK_ID,
        connection_factory=lambda: connection,
    )
    return receipt, cursor, connection


def _insert_params(cursor: _Cursor) -> Any:
    return next(
        params
        for statement, params in cursor.statements
        if "INSERT INTO research_start_requests" in statement
    )


def _sql(cursor: _Cursor) -> str:
    return "\n".join(statement for statement, _params in cursor.statements)


def test_workflow_intent_restarts_without_copying_attempts(monkeypatch) -> None:
    receipt, cursor, connection = _regenerate(
        monkeypatch,
        state="failed",
        intent={"input": "优化配方", "requestedWorkflow": "pvd_process_optimization_workflow"},
        task_payload={"status": "failed", "researchDesignVersions": []},
    )

    assert receipt["applied"] is True
    assert receipt["reason"] == "queued"
    assert receipt["sessionId"] == SESSION_ID
    assert connection.committed is True
    sql = _sql(cursor)
    assert "research_start_attempts" not in sql
    assert "thinking_transcript" not in sql
    stored_intent = json.loads(_insert_params(cursor)[-1])
    assert stored_intent["requestedWorkflow"] == "pvd_process_optimization_workflow"
    assert stored_intent["input"] == "优化配方"


def test_planner_intent_is_queued_unchanged(monkeypatch) -> None:
    receipt, cursor, _connection = _regenerate(
        monkeypatch,
        state="failed",
        intent={"input": "复现 SimCSE"},
        task_payload={"status": "failed"},
    )

    assert receipt["reason"] == "queued"
    assert json.loads(_insert_params(cursor)[-1]) == {"input": "复现 SimCSE"}
    assert "research_start_attempts" not in _sql(cursor)


def test_uncertain_submission_is_not_replayed(monkeypatch) -> None:
    receipt, cursor, _connection = _regenerate(
        monkeypatch,
        state="external_result_unknown",
        intent={"input": "复现 SimCSE"},
        task_payload={"status": "awaiting_confirmation"},
    )

    assert receipt == {
        "applied": False,
        "reason": "external_result_unknown",
        "sessionId": SESSION_ID,
    }
    assert "INSERT INTO research_start_requests" not in _sql(cursor)


def test_existing_design_blocks_regeneration(monkeypatch) -> None:
    receipt, cursor, _connection = _regenerate(
        monkeypatch,
        state="failed",
        intent={"input": "复现 SimCSE"},
        task_payload={"researchDesignVersions": [{"version": 1}]},
    )

    assert receipt["applied"] is False
    assert receipt["reason"] == "design_exists"
    assert "UPDATE research_start_sessions" not in _sql(cursor)


def test_rejected_task_lifecycle_does_not_reopen_the_session(monkeypatch) -> None:
    receipt, cursor, _connection = _regenerate(
        monkeypatch,
        state="failed",
        intent={"input": "复现 SimCSE"},
        task_payload={"status": "running"},
        authorized=False,
    )

    assert receipt["reason"] == "task_not_regenerable"
    assert "UPDATE research_start_sessions" not in _sql(cursor)
