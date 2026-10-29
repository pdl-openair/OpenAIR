"""Repository SQL exercised only against an explicitly opted-in disposable DB."""

import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from app.modules.research_start_display_context import ResearchStartDisplayRepository
from app.modules.task_repository import TASKS_TABLE_SQL, TaskRepository
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


@pytest.fixture
def db():
    if os.environ.get("OPENAIR_UI_ISOLATED_DB_TEST") != "1":
        pytest.skip("requires a disposable PostgreSQL on loopback:55439")

    # Fixed test-only endpoint: never use app settings, .env or a caller supplied DSN.
    def connect(schema="public"):
        return psycopg.connect(
            host="127.0.0.1",
            port=55439,
            dbname="postgres",
            user="postgres",
            password="isolated-test-password",
            connect_timeout=10,
            options=f"-c search_path={schema}",
            row_factory=dict_row,
        )

    schema = "ui_title_" + uuid4().hex
    with connect() as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))

    def factory():
        return connect(schema)

    with factory() as conn:
        conn.execute(TASKS_TABLE_SQL)
        conn.execute("ALTER TABLE openair_tasks ADD COLUMN revision INTEGER DEFAULT 0")
        conn.execute("""CREATE TABLE research_start_sessions (
            session_id TEXT PRIMARY KEY, owner_id TEXT, workspace_id TEXT,
            reserved_task_id TEXT UNIQUE);
            CREATE TABLE research_start_requests (request_id TEXT PRIMARY KEY,
            session_id TEXT, owner_id TEXT, workspace_id TEXT, created_at TIMESTAMPTZ,
            request_payload JSONB);
            CREATE TABLE openair_artifacts (source_task_id TEXT, owner_id TEXT,
            workspace_id TEXT, source_task_title TEXT, updated_at TIMESTAMPTZ)""")
        conn.execute(
            Path("migrations/20261009_01_research_start_display_context.sql").read_text(
                encoding="utf-8"
            )
        )
        conn.execute(
            "INSERT INTO research_start_sessions "
            "VALUES ('session', 'owner', 'workspace', 'task', NULL, NULL, NULL)"
        )
        conn.execute(
            "INSERT INTO research_start_requests VALUES (%s, %s, %s, %s, NOW(), %s)",
            (
                "request",
                "session",
                "owner",
                "workspace",
                Jsonb({"input": "@Workflow 原始输入\n完整第二行"}),
            ),
        )
    try:
        yield factory
    finally:
        with connect() as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def task(title="新研究"):
    return {
        "taskId": "task",
        "ownerId": "owner",
        "workspaceId": "workspace",
        "title": title,
        "status": "draft",
        "revision": 0,
    }


def rename_repository(factory):
    repository = TaskRepository()

    @contextmanager
    def connection():
        with factory() as conn:
            yield conn

    repository._connect = connection
    return repository


def test_scoped_input_read_and_concurrent_claim_once(db):
    repository = ResearchStartDisplayRepository(db)
    assert (
        repository.read("task", "owner", "workspace", "session")["originalInput"]
        == "@Workflow 原始输入\n完整第二行"
    )
    for scope in [
        ("task", "other", "workspace", "session"),
        ("task", "owner", "other", "session"),
        ("task", "owner", "workspace", "other"),
    ]:
        assert repository.read(*scope)["originalInput"] is None
    with ThreadPoolExecutor(max_workers=8) as pool:
        claims = list(
            pool.map(lambda _: repository.claim_title("session", "owner", "workspace"), range(8))
        )
    assert sum(value is not None for value in claims) == 1


@pytest.mark.parametrize("early", [True, False])
def test_title_before_or_after_task_creation_survives_reload(db, early):
    repository = ResearchStartDisplayRepository(db)
    repository.claim_title("session", "owner", "workspace")
    if early:
        repository.finish_title("session", "owner", "workspace", "研究短名")
    repository.insert_task(task(), automatic=True)
    with db() as conn:
        conn.execute(
            "INSERT INTO openair_artifacts VALUES ('task', 'owner', 'workspace', %s, NOW())",
            ("研究短名" if early else "新研究",),
        )
    if not early:
        repository.finish_title("session", "owner", "workspace", "研究短名")
    assert repository.read("task", "owner", "workspace")["displayTitle"] == "研究短名"
    with db() as conn:
        row = conn.execute("SELECT title, payload, revision FROM openair_tasks").fetchone()
    assert row["title"] == row["payload"]["title"] == "研究短名"
    assert row["revision"] == 0
    with db() as conn:
        assert (
            conn.execute("SELECT source_task_title FROM openair_artifacts").fetchone()[
                "source_task_title"
            ]
            == "研究短名"
        )


@pytest.mark.parametrize("new_name", ["用户命名", "新研究"])
def test_actual_user_rename_defeats_late_generated_title(db, new_name):
    repository = ResearchStartDisplayRepository(db)
    repository.insert_task(task(), automatic=True)
    repository.claim_title("session", "owner", "workspace")
    renamed = rename_repository(db).rename_task_scoped(
        task_id="task",
        owner_id="owner",
        workspace_id="workspace",
        title=new_name,
        expected_revision=0,
    )
    assert renamed["title"] == new_name
    repository.finish_title("session", "owner", "workspace", "迟到自动标题")
    assert repository.read("task", "owner", "workspace")["displayTitle"] == new_name
    assert not repository.read("task", "owner", "workspace")["titlePending"]


def test_explicit_existing_title_is_never_claimed(db):
    repository = ResearchStartDisplayRepository(db)
    repository.insert_task(task("已有名称"), automatic=False)
    assert repository.claim_title("session", "owner", "workspace") is None
    assert repository.read("task", "owner", "workspace")["displayTitle"] == "已有名称"


def test_failure_is_neutral_and_migration_absence_preserves_creation_and_rename(db):
    repository = ResearchStartDisplayRepository(db)
    repository.claim_title("session", "owner", "workspace")
    repository.finish_title("session", "owner", "workspace", None)
    repository.insert_task(task(), automatic=True)
    assert repository.read("task", "owner", "workspace")["displayTitle"] == "新研究"
    assert repository.claim_title("session", "owner", "workspace") is None
    with db() as conn:
        conn.execute("DELETE FROM openair_tasks")
        conn.execute(
            Path("migrations/20261009_01_research_start_display_context_down.sql").read_text(
                encoding="utf-8"
            )
        )
    repository.insert_task(task(), automatic=True)
    rename_repository(db).rename_task_scoped(
        task_id="task", owner_id="owner", workspace_id="workspace", title="仍可改名"
    )
    value = repository.read("task", "owner", "workspace")
    assert value["displayTitle"] == "仍可改名"
    assert value["originalInput"].endswith("完整第二行")


def test_concurrent_task_creation_and_completion_keep_title(db):
    from threading import Barrier

    repository = ResearchStartDisplayRepository(db)
    repository.claim_title("session", "owner", "workspace")
    ready = Barrier(2)

    def insert():
        ready.wait(timeout=5)
        repository.insert_task(task(), automatic=True)

    def finish():
        ready.wait(timeout=5)
        repository.finish_title("session", "owner", "workspace", "并发短名")

    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = [pool.submit(insert), pool.submit(finish)]
        for result in pending:
            result.result(timeout=10)
    assert repository.read("task", "owner", "workspace")["displayTitle"] == "并发短名"
