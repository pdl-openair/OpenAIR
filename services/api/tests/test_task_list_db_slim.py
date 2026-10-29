"""DB-side slim list: SQL avoids full payload; options endpoint is title-only."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.modules.task_repository import (
    SELECT_TASKS_BY_SCOPE_SLIM_SQL,
    SELECT_TASK_TITLE_OPTIONS_SQL,
    TaskRepository,
)
from app.routes import tasks as task_routes

OWNER = "owner_slim_db"
WORKSPACE = "ws_slim_db"


def test_slim_sql_does_not_select_full_payload_column() -> None:
    assert "payload," not in SELECT_TASKS_BY_SCOPE_SLIM_SQL.replace(" ", "")
    assert "payload->>" in SELECT_TASKS_BY_SCOPE_SLIM_SQL
    assert "payload->" in SELECT_TASKS_BY_SCOPE_SLIM_SQL
    assert "FROM openair_tasks" in SELECT_TASKS_BY_SCOPE_SLIM_SQL


def test_title_options_sql_reads_columns_only() -> None:
    assert "payload" not in SELECT_TASK_TITLE_OPTIONS_SQL
    assert "task_id" in SELECT_TASK_TITLE_OPTIONS_SQL
    assert "title" in SELECT_TASK_TITLE_OPTIONS_SQL


def test_project_slim_list_rows_maps_extracted_fields() -> None:
    rows = [
        {
            "task_id": "t1",
            "owner_id": OWNER,
            "workspace_id": WORKSPACE,
            "status": "running",
            "current_stage": "solution_design",
            "title": "Slim",
            "created_at": datetime(2026, 8, 14, 0, 0, 0, tzinfo=timezone.utc),
            "updated_at": datetime(2026, 8, 14, 1, 0, 0, tzinfo=timezone.utc),
            "revision": 2,
            "domain": "cs",
            "goal_summary": "goal",
            "skill_pack": "cs_research",
            "creation_status": "confirmed",
            "execution_mode": "automatic_research",
            "result_source": "live",
            "created_by": "actor",
            "progress_projection": {"revision": 2},
        }
    ]
    out = TaskRepository._project_slim_list_rows(object.__new__(TaskRepository), rows)
    assert out[0]["taskId"] == "t1"
    assert out[0]["goalSummary"] == "goal"
    assert out[0]["progressProjection"] == {"revision": 2}
    assert out[0]["createdAt"] == "2026-08-14T00:00:00Z"
    assert out[0]["updatedAt"] == "2026-08-14T01:00:00Z"
    assert "researchDesignVersions" not in out[0]
    assert "payload" not in out[0]


def test_list_item_projection_accepts_datetime_timestamps() -> None:
    from app.modules.task_public_projection import project_public_task_list_item

    out = project_public_task_list_item(
        {
            "taskId": "t_dt",
            "ownerId": OWNER,
            "workspaceId": WORKSPACE,
            "createdBy": OWNER,
            "title": "DT",
            "status": "running",
            "currentStage": "problem_modeling",
            "resultSource": "live",
            "executionMode": "stage_confirmation",
            "revision": 1,
            "createdAt": datetime(2026, 8, 14, 0, 0, 0, tzinfo=timezone.utc),
            "updatedAt": datetime(2026, 8, 14, 1, 0, 0, tzinfo=timezone.utc),
        }
    )
    assert out.createdAt == "2026-08-14T00:00:00Z"
    assert out.updatedAt == "2026-08-14T01:00:00Z"


def test_options_endpoint_returns_title_rows(monkeypatch) -> None:
    tasks = [
        {"taskId": "a", "title": "Alpha"},
        {"taskId": "b", "title": "Beta"},
    ]

    class _TaskRepo:
        def count_tasks_by_scope(self, owner_id: str, workspace_id: str) -> int:
            return len(tasks)

        def list_task_title_options(self, owner_id: str, workspace_id: str, *, limit=50, offset=0):
            return tasks[offset : offset + limit]

    monkeypatch.setattr(settings, "persistence_backend", "postgres")
    monkeypatch.setattr(task_routes, "TaskRepository", _TaskRepo)

    client = TestClient(app)
    response = client.get(
        "/api/tasks/options?limit=10",
        headers={
            "X-OpenAIR-Owner-Id": OWNER,
            "X-OpenAIR-Workspace-Id": WORKSPACE,
            "X-OpenAIR-Created-By": OWNER,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 2
    assert body["limit"] == 10
    assert [row["taskId"] for row in body["tasks"]] == ["a", "b"]
    assert body["tasks"][0]["title"] == "Alpha"
