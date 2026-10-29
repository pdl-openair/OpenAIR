"""P0-B real PostgreSQL gate.

Set OPENAIR_P0B_TEST_DATABASE_URL to an explicitly disposable test/qa database.
No implicit DSN or developer database is ever used.
"""

from __future__ import annotations

import multiprocessing
import os
from pathlib import Path
from urllib.parse import urlparse

import pytest

DSN_ENV = "OPENAIR_P0B_TEST_DATABASE_URL"


def _dsn() -> str:
    dsn = os.environ.get(DSN_ENV, "").strip()
    if not dsn:
        pytest.skip(f"{DSN_ENV} is not configured")
    database = urlparse(dsn).path.lstrip("/").lower()
    if "test" not in database and "qa" not in database:
        pytest.fail(f"{DSN_ENV} must identify an isolated test or qa database")
    return dsn


def _worker_append(dsn: str, event: dict) -> None:
    from urllib.parse import urlparse

    from app.modules.task_events import TaskResearchEventRepository

    parsed = urlparse(dsn)
    conninfo = (
        f"host={parsed.hostname or '127.0.0.1'} port={parsed.port or 5432} "
        f"dbname={parsed.path.lstrip('/')} user={parsed.username or 'postgres'}"
    )
    repository = TaskResearchEventRepository(conninfo=conninfo, password=parsed.password or "")
    repository.append_many(
        owner_id="owner-a",
        workspace_id="workspace-a",
        task_id="task-a",
        source_identity="decision-a",
        events=[event],
    )


def _safe_event(source_event_id: str, headline: str = "当前步骤已经开始") -> dict:
    return {
        "sourceEventId": source_event_id,
        "eventType": "research_progress",
        "presentationLevel": "primary",
        "workUnitId": "work-a",
        "headline": headline,
        "summary": None,
        "invalidateScopes": ["collaboration"],
        "payload": {"phase": "started"},
        "occurredAt": "2026-08-06T00:00:00Z",
    }


@pytest.fixture()
def repository():
    psycopg = pytest.importorskip("psycopg")
    from app.modules.task_events import TaskResearchEventRepository

    dsn = _dsn()
    parsed = urlparse(dsn)
    migration = (Path(__file__).parents[2] / "migrations" / "20260806_01_task_research_stream.sql").read_text()
    down = (Path(__file__).parents[2] / "migrations" / "20260806_01_task_research_stream_down.sql").read_text()
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("DROP TABLE IF EXISTS openair_task_stream_events CASCADE")
        conn.execute("DROP TABLE IF EXISTS openair_execution_run_events CASCADE")
        conn.execute("DROP TABLE IF EXISTS openair_execution_runs CASCADE")
        conn.execute("DROP TABLE IF EXISTS openair_tasks CASCADE")
        conn.execute(
            """
            CREATE TABLE openair_tasks (
                task_id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT INTO openair_tasks(task_id, owner_id, workspace_id) VALUES "
            "('task-a','owner-a','workspace-a'),('task-b','owner-b','workspace-b')"
        )
        conn.execute(
            """
            CREATE TABLE openair_execution_runs (
                run_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL REFERENCES openair_tasks(task_id) ON DELETE CASCADE,
                owner_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE openair_execution_run_events (
                run_id TEXT NOT NULL REFERENCES openair_execution_runs(run_id) ON DELETE CASCADE,
                sequence BIGINT NOT NULL,
                type TEXT NOT NULL,
                payload JSONB NOT NULL DEFAULT '{}'::jsonb,
                occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY(run_id, sequence)
            )
            """
        )
        conn.execute(migration)
    conninfo = (
        f"host={parsed.hostname or '127.0.0.1'} port={parsed.port or 5432} "
        f"dbname={parsed.path.lstrip('/')} user={parsed.username or 'postgres'}"
    )
    repo = TaskResearchEventRepository(conninfo=conninfo, password=parsed.password or "")
    yield repo, dsn
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(down)
        conn.execute("DROP TABLE IF EXISTS openair_execution_run_events CASCADE")
        conn.execute("DROP TABLE IF EXISTS openair_execution_runs CASCADE")
        conn.execute("DROP TABLE IF EXISTS openair_tasks CASCADE")


def test_cross_process_notify_replay_idempotency_and_scope(repository) -> None:
    repo, dsn = repository
    listener = repo.subscribe(owner_id="owner-a", workspace_id="workspace-a", task_id="task-a")
    repo.append_many(
        owner_id="owner-b", workspace_id="workspace-b", task_id="task-b",
        source_identity="decision-b", events=[_safe_event("other-task")],
    )
    assert listener.wait(0.2) is False
    event = _safe_event("source-1")
    process = multiprocessing.Process(target=_worker_append, args=(dsn, event))
    process.start()
    process.join(timeout=10)
    assert process.exitcode == 0
    assert listener.wait(5.0) is True

    owner_rows = repo.list_after(
        owner_id="owner-a", workspace_id="workspace-a", task_id="task-a",
        source_identity="decision-a", sequence=0,
    )
    assert len(owner_rows) == 1
    assert owner_rows[0]["payload"]["headline"] == "当前步骤已经开始"
    assert repo.list_after(
        owner_id="owner-b", workspace_id="workspace-b", task_id="task-b",
        source_identity="decision-a", sequence=0,
    ) == []
    assert repo.append_many(
        owner_id="owner-a", workspace_id="workspace-a", task_id="task-a",
        source_identity="decision-a", events=[event],
    ) == 0
    with pytest.raises(Exception):
        repo.append_many(
            owner_id="owner-a", workspace_id="workspace-b", task_id="task-a",
            source_identity="decision-a", events=[_safe_event("scope-mismatch")],
        )
    listener.close()


def test_last_event_id_and_cursor_expiry_reset(repository) -> None:
    pytest.importorskip("psycopg")
    repo, dsn = repository
    repo.append_many(
        owner_id="owner-a", workspace_id="workspace-a", task_id="task-a",
        source_identity="decision-a", events=[_safe_event("source-1")],
    )
    first = repo.list_after(
        owner_id="owner-a", workspace_id="workspace-a", task_id="task-a",
        source_identity="decision-a", sequence=0,
    )[0]
    cursor = repo.cursor(
        owner_id="owner-a", workspace_id="workspace-a", task_id="task-a",
        source_identity="decision-a", event_id=first["id"],
    )
    repo.append_many(
        owner_id="owner-a", workspace_id="workspace-a", task_id="task-a",
        source_identity="decision-a", events=[_safe_event("source-2", "获得了有意义的新进展")],
    )
    replay = repo.list_after(
        owner_id="owner-a", workspace_id="workspace-a", task_id="task-a",
        source_identity="decision-a", sequence=cursor.sequence,
    )
    assert [row["payload"]["headline"] for row in replay] == ["获得了有意义的新进展"]

    import psycopg
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("UPDATE openair_task_stream_events SET expires_at=NOW()-INTERVAL '1 second' WHERE event_id=%s", (first["id"],))
    expired = repo.cursor(
        owner_id="owner-a", workspace_id="workspace-a", task_id="task-a",
        source_identity="decision-a", event_id=first["id"],
    )
    assert expired.expired is True
    assert expired.sequence == 0


def test_planning_event_id_survives_pre_run_to_execution_source(repository) -> None:
    repo, _dsn_value = repository
    planning = _safe_event("planning-source", "研究方案已形成")
    planning["eventType"] = "meaningful_progress"
    planning["workUnitId"] = None
    planning["payload"] = {
        "phase": "formed",
        "activityKey": "research-design:rd-1:v1",
    }
    assert repo.append_many(
        owner_id="owner-a", workspace_id="workspace-a", task_id="task-a",
        source_identity="task:task-a:pre-run", events=[planning],
    ) == 1
    pre_run = repo.list_after(
        owner_id="owner-a", workspace_id="workspace-a", task_id="task-a",
        source_identity="task:task-a:pre-run", sequence=0,
    )
    assert len(pre_run) == 1
    assert pre_run[0]["sourceIdentity"] == "task:task-a:planning"

    assert repo.append_many(
        owner_id="owner-a", workspace_id="workspace-a", task_id="task-a",
        source_identity="decision-a", events=[planning],
    ) == 0
    execution_replay = repo.list_after(
        owner_id="owner-a", workspace_id="workspace-a", task_id="task-a",
        source_identity="decision-a", sequence=0,
    )
    assert [row["id"] for row in execution_replay] == [pre_run[0]["id"]]
    cursor = repo.cursor(
        owner_id="owner-a", workspace_id="workspace-a", task_id="task-a",
        source_identity="decision-a", event_id=pre_run[0]["id"],
    )
    assert cursor.expired is False
    assert cursor.sequence == pre_run[0]["sequence"]

def test_source_identity_fences_successor_replay(repository) -> None:
    repo, _dsn_value = repository
    shared = _safe_event("shared-source")
    assert repo.append_many(
        owner_id="owner-a", workspace_id="workspace-a", task_id="task-a",
        source_identity="decision-a", events=[shared],
    ) == 1
    assert repo.append_many(
        owner_id="owner-a", workspace_id="workspace-a", task_id="task-a",
        source_identity="decision-b", events=[shared],
    ) == 1
    first = repo.list_after(
        owner_id="owner-a", workspace_id="workspace-a", task_id="task-a",
        source_identity="decision-a", sequence=0,
    )[0]
    assert len(repo.list_after(
        owner_id="owner-a", workspace_id="workspace-a", task_id="task-a",
        source_identity="decision-b", sequence=0,
    )) == 1
    successor_cursor = repo.cursor(
        owner_id="owner-a", workspace_id="workspace-a", task_id="task-a",
        source_identity="decision-b", event_id=first["id"],
    )
    assert successor_cursor.expired is True
    assert successor_cursor.sequence == 0


def test_listen_is_established_before_first_replay(repository) -> None:
    repo, _dsn_value = repository
    listener = repo.subscribe(owner_id="owner-a", workspace_id="workspace-a", task_id="task-a")
    repo.append_many(
        owner_id="owner-a", workspace_id="workspace-a", task_id="task-a",
        source_identity="decision-a", events=[_safe_event("race-source")],
    )
    replay = repo.list_after(
        owner_id="owner-a", workspace_id="workspace-a", task_id="task-a",
        source_identity="decision-a", sequence=0,
    )
    assert len(replay) == 1
    assert listener.wait(5.0) is True
    listener.close()
