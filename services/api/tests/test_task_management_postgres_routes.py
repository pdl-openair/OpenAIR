from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import json
from typing import Any, Iterator

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.modules.durable_execution import DurableExecutionError, DurableExecutionRepository
from app.modules.task_repository import TaskRepository
from app.modules.task_store import store
from app.routes import tasks as task_routes


OWNER = "owner_task_management"
WORKSPACE = "ws_task_management"
OTHER_OWNER = "owner_task_management_other"


def _headers(owner: str = OWNER, workspace: str = WORKSPACE) -> dict[str, str]:
    return {
        "X-OpenAIR-Owner-Id": owner,
        "X-OpenAIR-Workspace-Id": workspace,
        "X-OpenAIR-Created-By": owner,
    }


def _task(*, task_id: str = "task_postgres_management", title: str = "Original title") -> dict[str, Any]:
    return {
        "taskId": task_id,
        "ownerId": OWNER,
        "workspaceId": WORKSPACE,
        "createdBy": OWNER,
        "title": title,
        "revision": 0,
        "currentStage": "problem_modeling",
        "status": "not_started",
        "resultSource": "none",
        "executionMode": "stage_confirmation",
        "stages": [
            "problem_modeling",
            "solution_design",
            "execution_validation",
            "insight_generation",
        ],
        "stageDetails": [],
        "artifacts": [],
        "capabilityCalls": [],
    }


def _repository_class(initial: dict[str, Any], events: list[str] | None = None):
    class Repository:
        task = deepcopy(initial)
        deleted = False

        def rename_task(
            self,
            *,
            task_id: str,
            new_title: str,
            owner_id: str,
            workspace_id: str,
        ) -> dict[str, Any] | None:
            cls = type(self)
            if (
                cls.deleted
                or task_id != cls.task["taskId"]
                or owner_id != cls.task["ownerId"]
                or workspace_id != cls.task["workspaceId"]
            ):
                return None
            cls.task["title"] = new_title.strip()
            cls.task["revision"] += 1
            return deepcopy(cls.task)

        def rename_task_scoped(
            self,
            *,
            task_id: str,
            owner_id: str,
            workspace_id: str,
            title: str,
            expected_revision: int | None = None,
            idempotency_key: str | None = None,
        ) -> dict[str, Any]:
            renamed = self.rename_task(
                task_id=task_id,
                new_title=title,
                owner_id=owner_id,
                workspace_id=workspace_id,
            )
            if renamed is None:
                raise KeyError("task not found")
            if expected_revision is not None and renamed["revision"] - 1 != expected_revision:
                raise RuntimeError("task_revision_conflict")
            return renamed

        def soft_delete_task(
            self,
            *,
            task_id: str,
            owner_id: str,
            workspace_id: str,
            expected_revision: int | None = None,
        ) -> bool:
            cls = type(self)
            if (
                cls.deleted
                or task_id != cls.task["taskId"]
                or owner_id != cls.task["ownerId"]
                or workspace_id != cls.task["workspaceId"]
            ):
                return False
            if expected_revision is not None and expected_revision != cls.task["revision"]:
                raise RuntimeError("task_revision_conflict")
            cls.deleted = True
            if events is not None:
                events.append("db_commit")
            return True

        def get_task(self, task_id: str) -> dict[str, Any] | None:
            cls = type(self)
            if cls.deleted or task_id != cls.task["taskId"]:
                return None
            return deepcopy(cls.task)

        def load_all_tasks(self) -> list[dict[str, Any]]:
            cls = type(self)
            return [] if cls.deleted else [deepcopy(cls.task)]

        def list_tasks_by_scope(
            self, owner_id: str, workspace_id: str, **_kwargs: Any
        ) -> list[dict[str, Any]]:
            cls = type(self)
            if cls.deleted:
                return []
            task = cls.task
            if task["ownerId"] != owner_id or task["workspaceId"] != workspace_id:
                return []
            return [deepcopy(task)]

        def count_tasks_by_scope(self, owner_id: str, workspace_id: str) -> int:
            return len(self.list_tasks_by_scope(owner_id, workspace_id))

    return Repository


def test_postgres_rename_scope_mismatch_is_same_404_as_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    repository = _repository_class(_task())
    monkeypatch.setattr(settings, "persistence_backend", "postgres")
    monkeypatch.setattr(task_routes, "TaskRepository", repository)
    client = TestClient(app)

    outside = client.patch(
        "/api/tasks/task_postgres_management",
        json={"title": "Should not apply"},
        headers=_headers(owner=OTHER_OWNER),
    )
    missing = client.patch(
        "/api/tasks/task_missing",
        json={"title": "Should not apply"},
        headers=_headers(owner=OTHER_OWNER),
    )

    assert outside.status_code == 404
    assert missing.status_code == 404
    assert outside.json() == missing.json() == {"detail": "task not found"}


def test_postgres_delete_scope_mismatch_is_same_404_as_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    repository = _repository_class(_task())
    monkeypatch.setattr(settings, "persistence_backend", "postgres")
    monkeypatch.setattr(settings, "artifact_storage", "local")
    monkeypatch.setattr(task_routes, "TaskRepository", repository)
    monkeypatch.setattr(task_routes, "cancel_task_background_executions", lambda _task_id: 0)
    client = TestClient(app)

    outside = client.delete(
        "/api/tasks/task_postgres_management",
        headers=_headers(owner=OTHER_OWNER),
    )
    missing = client.delete(
        "/api/tasks/task_missing",
        headers=_headers(owner=OTHER_OWNER),
    )

    assert outside.status_code == 404
    assert missing.status_code == 404
    assert outside.json() == missing.json() == {"detail": "task not found"}


def test_database_delete_failure_is_not_reported_as_a_revision_conflict(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    class Repository:
        def soft_delete_task(self, **_kwargs: Any) -> bool:
            calls.append("db_failure")
            raise RuntimeError("simulated database failure")

    monkeypatch.setattr(settings, "persistence_backend", "postgres")
    monkeypatch.setattr(settings, "artifact_storage", "minio")
    monkeypatch.setattr(task_routes, "TaskRepository", Repository)
    monkeypatch.setattr(task_routes, "_cleanup_task_objects", lambda _task_id: calls.append("cleanup"))
    client = TestClient(app, raise_server_exceptions=False)

    response = client.delete("/api/tasks/task_postgres_management", headers=_headers())

    assert response.status_code == 500
    assert "simulated database failure" not in response.text
    assert calls == ["db_failure"]


def test_json_delete_keeps_legacy_store_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "persistence_backend", "json")
    monkeypatch.setattr(settings, "artifact_storage", "local")
    task = store.create_task(
        {
            "title": "JSON task",
            "goalSummary": "json regression",
            "ownerId": OWNER,
            "workspaceId": WORKSPACE,
            "createdBy": OWNER,
        },
    )
    try:
        response = TestClient(app).delete(f"/api/tasks/{task['taskId']}", headers=_headers())
        assert response.status_code == 204, response.text
        assert store.get_task(task["taskId"]) is None
    finally:
        store._tasks.pop(task["taskId"], None)  # noqa: SLF001 - test cleanup


class _ScriptedCursor:
    def __init__(self, task: dict[str, Any]) -> None:
        self.task = deepcopy(task)
        self.executed: list[tuple[str, Any]] = []
        self.rowcount = 0
        self._next: dict[str, Any] | None = None

    def __enter__(self) -> "_ScriptedCursor":
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def execute(self, sql: str, params: Any = None) -> None:
        normalized = " ".join(sql.split())
        self.executed.append((normalized, params))
        self.rowcount = 0
        self._next = None
        if normalized.startswith("SELECT payload, revision FROM openair_tasks"):
            self._next = {"payload": deepcopy(self.task), "revision": self.task["revision"]}
        elif normalized.startswith(
            "SELECT payload, revision, status, current_stage, title, domain FROM openair_tasks"
        ):
            self._next = {
                "payload": deepcopy(self.task),
                "revision": self.task["revision"],
                "status": self.task.get("status"),
                "current_stage": self.task.get("currentStage"),
                "title": self.task.get("title"),
                "domain": self.task.get("domain"),
            }
        elif normalized.startswith("UPDATE openair_tasks SET title"):
            self._next = {"payload": json.loads(params[2]), "revision": params[1]}
            self.rowcount = 1
        elif (
            normalized.startswith("UPDATE openair_tasks SET status")
            and "current_stage = %s" in normalized
            and "title = %s" in normalized
            and "domain = %s" in normalized
        ):
            # Canonical scoped mutation writer (rename_task_scoped / _commit_user_task_mutation).
            payload = json.loads(params[5])
            self.task = payload
            self.rowcount = 1
        elif normalized.startswith("UPDATE openair_tasks SET status"):
            self.rowcount = 1
        elif normalized.startswith("UPDATE openair_artifacts SET source_task_title"):
            self.rowcount = 1

    def fetchone(self) -> dict[str, Any] | None:
        row = self._next
        self._next = None
        return row


class _ScriptedConnection:
    def __init__(self, cursor: _ScriptedCursor, events: list[str]) -> None:
        self._cursor = cursor
        self._events = events

    def cursor(self) -> _ScriptedCursor:
        return self._cursor


class _RepositoryHarness(TaskRepository):
    def __init__(self, cursor: _ScriptedCursor, events: list[str]) -> None:
        self._cursor = cursor
        self._events = events

    @contextmanager
    def _connect(self) -> Iterator[_ScriptedConnection]:
        try:
            yield _ScriptedConnection(self._cursor, self._events)
            self._events.append("commit")
        except Exception:
            self._events.append("rollback")
            raise


def test_repository_rename_is_scoped_and_revisioned() -> None:
    task = _task()
    cursor = _ScriptedCursor(task)
    events: list[str] = []
    repository = _RepositoryHarness(cursor, events)

    renamed = repository.rename_task(
        task_id=task["taskId"],
        new_title="  Persisted title  ",
        owner_id=OWNER,
        workspace_id=WORKSPACE,
    )

    assert renamed is not None
    assert renamed["title"] == "Persisted title"
    assert renamed["revision"] == task["revision"] + 1
    assert events == ["commit"]
    statements = [sql for sql, _params in cursor.executed]
    assert statements[0].startswith("SELECT pg_advisory_xact_lock")
    assert "owner_id = %s" in statements[1]
    assert "workspace_id = %s" in statements[1]
    assert any(sql.startswith("UPDATE openair_tasks SET title") for sql in statements)
    artifact_sql, artifact_params = next(
        (sql, params)
        for sql, params in cursor.executed
        if sql.startswith("UPDATE openair_artifacts SET source_task_title")
    )
    assert "source_task_id = %s" in artifact_sql
    assert artifact_params == ("Persisted title", task["taskId"], OWNER, WORKSPACE)
    assert statements[-1].startswith("UPDATE openair_artifacts SET source_task_title")


def test_repository_rename_task_scoped_syncs_artifact_source_task_title() -> None:
    """PostgreSQL rename route uses rename_task_scoped → must sync denormalized titles."""
    task = _task()
    cursor = _ScriptedCursor(task)
    events: list[str] = []
    repository = _RepositoryHarness(cursor, events)

    renamed = repository.rename_task_scoped(
        task_id=task["taskId"],
        owner_id=OWNER,
        workspace_id=WORKSPACE,
        title="  Scoped rename title  ",
        expected_revision=task["revision"],
    )

    assert renamed["title"] == "Scoped rename title"
    assert renamed["revision"] == task["revision"] + 1
    assert events == ["commit"]
    statements = [sql for sql, _params in cursor.executed]
    assert any(
        sql.startswith("UPDATE openair_tasks SET status") and "title = %s" in sql
        for sql in statements
    )
    artifact_sql, artifact_params = next(
        (sql, params)
        for sql, params in cursor.executed
        if sql.startswith("UPDATE openair_artifacts SET source_task_title")
    )
    assert "owner_id = %s" in artifact_sql
    assert "workspace_id = %s" in artifact_sql
    assert artifact_params == ("Scoped rename title", task["taskId"], OWNER, WORKSPACE)
    assert statements[-1].startswith("UPDATE openair_artifacts SET source_task_title")


def test_repository_soft_delete_is_one_scoped_transaction() -> None:
    task = _task()
    cursor = _ScriptedCursor(task)
    events: list[str] = []
    repository = _RepositoryHarness(cursor, events)

    deleted = repository.soft_delete_task(
        task_id=task["taskId"],
        owner_id=OWNER,
        workspace_id=WORKSPACE,
    )

    assert deleted is True
    assert events == ["commit"]
    statements = [sql for sql, _params in cursor.executed]
    assert statements[0].startswith("SELECT pg_advisory_xact_lock")
    assert "owner_id = %s" in statements[1]
    assert "workspace_id = %s" in statements[1]
    for table in (
        "openair_execution_gates",
        "openair_execution_capability_calls",
        "openair_execution_work_units",
        "openair_execution_runs",
        "openair_dispatch",
        "openair_controller_commands",
        "openair_artifacts",
        "openair_artifact_uploads",
    ):
        assert any(sql.startswith(f"UPDATE {table}") for sql in statements), table
    assert any(sql.startswith("UPDATE openair_artifacts SET storage_ref = NULL") for sql in statements)
    artifact_sql, artifact_params = next(
        (sql, params) for sql, params in cursor.executed
        if sql.startswith("UPDATE openair_artifacts SET storage_ref = NULL")
    )
    # jsonb_build_object values are "any"; without an explicit cast Postgres
    # raises IndeterminateDatatype for the deletedAt parameter.
    assert "deletedAt', %s::text" in artifact_sql
    assert artifact_params[0]  # ISO timestamp string
    assert statements[-1].startswith("UPDATE openair_tasks SET status")
    # Public deletion is a tombstone: RESTRICT-linked external evidence,
    # recovery, lineage, and dispatch history are never physically deleted.
    assert not any(sql.startswith("DELETE FROM") for sql in statements)


def test_repository_soft_delete_revision_conflict_rolls_back_before_fencing() -> None:
    task = _task()
    cursor = _ScriptedCursor(task)
    events: list[str] = []
    repository = _RepositoryHarness(cursor, events)

    with pytest.raises(RuntimeError, match="task_revision_conflict"):
        repository.soft_delete_task(
            task_id=task["taskId"],
            owner_id=OWNER,
            workspace_id=WORKSPACE,
            expected_revision=task["revision"] + 1,
        )

    assert events == ["rollback"]
    statements = [sql for sql, _params in cursor.executed]
    assert len(statements) == 2
    assert not any(sql.startswith("UPDATE ") for sql in statements)


def test_repository_soft_delete_mid_transaction_failure_rolls_back_tombstone() -> None:
    class FailingCursor(_ScriptedCursor):
        def execute(self, sql: str, params: Any = None) -> None:
            normalized = " ".join(sql.split())
            if normalized.startswith("UPDATE openair_artifacts SET storage_ref = NULL"):
                self.executed.append((normalized, params))
                raise RuntimeError("artifact cleanup projection failed")
            super().execute(sql, params)

    task = _task()
    cursor = FailingCursor(task)
    events: list[str] = []
    repository = _RepositoryHarness(cursor, events)

    with pytest.raises(RuntimeError, match="artifact cleanup projection failed"):
        repository.soft_delete_task(
            task_id=task["taskId"],
            owner_id=OWNER,
            workspace_id=WORKSPACE,
            expected_revision=task["revision"],
        )

    assert events == ["rollback"]
    statements = [sql for sql, _params in cursor.executed]
    assert any(sql.startswith("UPDATE openair_dispatch") for sql in statements)
    assert not any(sql.startswith("UPDATE openair_tasks SET status") for sql in statements)


class _ExecutionCursor:
    rowcount = 0

    def __init__(self) -> None:
        self.executed: list[tuple[str, Any]] = []

    def execute(self, sql: str, params: Any = None) -> None:
        self.executed.append((" ".join(sql.split()), params))


def test_deleted_task_latest_revision_cannot_be_resurrected() -> None:
    task = _task()
    task["status"] = "deleted"
    task["revision"] = 7
    task["taskId"] = "task_deleted_tombstone"
    task["task_id"] = task["taskId"]
    task["payload"] = {**task, "status": "deleted"}
    cursor = _ExecutionCursor()

    with pytest.raises(DurableExecutionError, match="task_revision_conflict") as error:
        DurableExecutionRepository()._project_task(cursor, task, status="completed")

    assert error.value.status_code == 409
    assert len(cursor.executed) == 1
    assert "status <> 'deleted'" in cursor.executed[0][0]
    assert task["status"] == "deleted"
