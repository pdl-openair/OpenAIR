"""E1 task messages tests: migration schema, repository seq/idempotency/pagination,
and the paginated GET /agent/messages route.

The repository is exercised against an in-memory fake cursor that models
``openair_task_messages`` (no live postgres needed). The route is exercised via
TestClient with the repository mocked.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.modules import task_message_repository as tmr_module
from app.modules.task_message_repository import TaskMessageRepository, _row_to_message
from app.routes import agent as agent_route

MIGRATION = (
    Path(__file__).resolve().parent.parent / "migrations" / "20260811_02_task_messages.sql"
).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# migration schema
# ---------------------------------------------------------------------------


def test_migration_defines_table_unique_constraints_indexes_and_scope_trigger() -> None:
    """The E1 migration must create the table with the two UNIQUE constraints
    (per-task seq monotonic, per-task message_uid idempotency), the cursor +
    scope indexes, and the owner/workspace scope trigger."""
    sql = " ".join(MIGRATION.split())
    assert "CREATE TABLE IF NOT EXISTS openair_task_messages" in sql
    assert "seq BIGINT NOT NULL" in sql
    assert "message_uid TEXT NOT NULL" in sql
    assert "role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system'))" in sql
    assert "UNIQUE (task_id, seq)" in sql
    assert "UNIQUE (task_id, message_uid)" in sql
    assert "idx_task_messages_cursor" in sql and "task_id, seq DESC" in sql
    assert "idx_task_messages_scope" in sql
    assert "openair_validate_task_message_scope" in sql
    assert "BEFORE INSERT ON openair_task_messages" in sql


# ---------------------------------------------------------------------------
# in-memory fake cursor modeling openair_task_messages
# ---------------------------------------------------------------------------


class _FakeCursor:
    """Models openair_task_messages in memory for repo unit tests."""

    def __init__(self) -> None:
        self.rows: list[dict] = []
        self._fetch: list[dict] = []
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql: str, params: tuple | list | None = None) -> None:
        s = " ".join(sql.split())
        p = tuple(params or ())
        self._fetch = []
        self.rowcount = 0
        if "FROM openair_tasks" in s and "FOR UPDATE" in s:
            self._fetch = [{}]
            return
        if "COALESCE(MAX(seq), 0) + 1 AS next_seq" in s:
            tid = p[0]
            mx = max((r["seq"] for r in self.rows if r["task_id"] == tid), default=0)
            self._fetch = [{"next_seq": mx + 1}]
            return
        if "MAX(seq) AS latest_seq" in s:
            tid, oid, wid = p[0], p[1], p[2]
            mx = max(
                (r["seq"] for r in self.rows
                 if r["task_id"] == tid and r["owner_id"] == oid and r["workspace_id"] == wid),
                default=None,
            )
            self._fetch = [{"latest_seq": mx}]
            return
        if s.startswith("INSERT INTO openair_task_messages"):
            (tid, oid, wid, seq, uid, role, text, runtime, mtype, call_id, meta) = p
            existing = next((r for r in self.rows if r["task_id"] == tid and r["message_uid"] == uid), None)
            if existing is None:
                self.rows.append({
                    "message_id": len(self.rows) + 1, "task_id": tid, "owner_id": oid,
                    "workspace_id": wid, "seq": seq, "message_uid": uid, "role": role,
                    "content_text": text, "runtime": runtime, "message_type": mtype,
                    "capability_call_id": call_id, "metadata": json.loads(meta),
                    "created_at": datetime(2026, 8, 11, tzinfo=timezone.utc),
                })
                self.rowcount = 1
            return
        if "SELECT * FROM openair_task_messages" in s:
            if "AND message_uid = " in s:
                tid, uid = p[0], p[1]
                self._fetch = [r for r in self.rows if r["task_id"] == tid and r["message_uid"] == uid]
                return
            tid, oid, wid = p[0], p[1], p[2]
            matched = [
                r for r in self.rows
                if r["task_id"] == tid and r["owner_id"] == oid and r["workspace_id"] == wid
            ]
            if "seq > " in s:
                after = p[3]
                self._fetch = sorted((r for r in matched if r["seq"] > after), key=lambda r: r["seq"])
                return
            if "seq < " in s:
                before = p[3]
                limit = p[4]
                self._fetch = sorted((r for r in matched if r["seq"] < before), key=lambda r: -r["seq"])[:limit]
                return
            # newest-first, limit = last param
            limit = p[3]
            self._fetch = sorted(matched, key=lambda r: -r["seq"])[:limit]
            return

    def fetchone(self):
        return self._fetch[0] if self._fetch else None

    def fetchall(self):
        return list(self._fetch)


@pytest.fixture()
def fake_repo(monkeypatch: pytest.MonkeyPatch) -> tuple[TaskMessageRepository, _FakeCursor]:
    """A TaskMessageRepository whose every _connect yields the same fake cursor
    (so reads see the writes performed via append on the shared cursor model)."""
    cursor = _FakeCursor()

    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def cursor(self):
            return cursor

        def commit(self):
            pass

        def rollback(self):
            pass

        def close(self):
            pass

    repo = TaskMessageRepository()
    monkeypatch.setattr(repo, "_connect", lambda: _Conn())
    return repo, cursor


# ---------------------------------------------------------------------------
# repository: append seq + idempotency
# ---------------------------------------------------------------------------


def test_append_allocates_monotonic_seq(fake_repo) -> None:
    repo, cursor = fake_repo
    cur = _FakeCursor()
    cur.rows = cursor.rows  # share the in-memory model across appends
    a = repo.append(cur, task_id="t1", owner_id="o1", workspace_id="w1",
                    role="user", text="hello", message_uid="m1")
    b = repo.append(cur, task_id="t1", owner_id="o1", workspace_id="w1",
                    role="assistant", text="hi", message_uid="m2")
    assert a["seq"] == 1 and b["seq"] == 2


def test_append_is_idempotent_on_message_uid_replay(fake_repo) -> None:
    """ON CONFLICT (task_id, message_uid) DO NOTHING: replaying the same uid
    returns the original row (same seq), without allocating a new seq."""
    repo, cursor = fake_repo
    cur = _FakeCursor()
    cur.rows = cursor.rows  # share the model so append sees prior writes
    first = repo.append(cur, task_id="t1", owner_id="o1", workspace_id="w1",
                        role="user", text="hello", message_uid="m1")
    # a second message so seq advances
    repo.append(cur, task_id="t1", owner_id="o1", workspace_id="w1",
                role="assistant", text="hi", message_uid="m2")
    # replay m1
    replay = repo.append(cur, task_id="t1", owner_id="o1", workspace_id="w1",
                         role="user", text="hello", message_uid="m1")
    assert replay["seq"] == first["seq"] == 1
    assert len([r for r in cur.rows if r["task_id"] == "t1"]) == 2  # no duplicate


def test_append_rejects_invalid_role_and_empty_text() -> None:
    repo = TaskMessageRepository()
    with pytest.raises(ValueError):
        repo.append(_FakeCursor(), task_id="t1", owner_id="o1", workspace_id="w1",
                    role="bot", text="x", message_uid="m1")
    with pytest.raises(ValueError):
        repo.append(_FakeCursor(), task_id="t1", owner_id="o1", workspace_id="w1",
                    role="user", text="", message_uid="m1")


# ---------------------------------------------------------------------------
# repository: cursor pagination
# ---------------------------------------------------------------------------


def test_list_messages_cursor_pagination_and_latest_seq(fake_repo) -> None:
    repo, cursor = fake_repo
    cur = _FakeCursor()
    cur.rows = cursor.rows
    for i in range(5):
        repo.append(cur, task_id="t1", owner_id="o1", workspace_id="w1",
                    role="user" if i % 2 == 0 else "assistant", text=f"msg{i}", message_uid=f"m{i}")

    page1, has_more1, latest1 = repo.list_messages(task_id="t1", owner_id="o1", workspace_id="w1", limit=2)
    assert [m["seq"] for m in page1] == [5, 4]
    assert has_more1 is True
    assert latest1 == 5
    assert page1[-1]["seq"] == 4  # nextBeforeSeq anchor

    page2, has_more2, _ = repo.list_messages(task_id="t1", owner_id="o1", workspace_id="w1",
                                             before_seq=page1[-1]["seq"], limit=2)
    assert [m["seq"] for m in page2] == [3, 2]
    assert has_more2 is True

    page3, has_more3, _ = repo.list_messages(task_id="t1", owner_id="o1", workspace_id="w1",
                                             before_seq=page2[-1]["seq"], limit=2)
    assert [m["seq"] for m in page3] == [1]
    assert has_more3 is False


def test_messages_after_returns_increment_oldest_first(fake_repo) -> None:
    repo, cursor = fake_repo
    cur = _FakeCursor()
    cur.rows = cursor.rows
    for i in range(3):
        repo.append(cur, task_id="t1", owner_id="o1", workspace_id="w1",
                    role="assistant", text=f"m{i}", message_uid=f"u{i}")
    new = repo.messages_after(task_id="t1", owner_id="o1", workspace_id="w1", after_seq=1)
    assert [m["seq"] for m in new] == [2, 3]


def test_row_to_message_carries_seq_and_metadata_extras() -> None:
    row = {
        "message_id": 9, "task_id": "t1", "owner_id": "o1", "workspace_id": "w1",
        "seq": 7, "message_uid": "m_dispatch_abc", "role": "assistant",
        "content_text": "summary", "runtime": None, "message_type": "research_dispatch_result",
        "capability_call_id": "call_1",
        "metadata": {"workUnitId": "wu_1", "artifactRefs": ["art_1"], "authorLabel": "exec"},
        "created_at": datetime(2026, 8, 11, tzinfo=timezone.utc),
    }
    msg = _row_to_message(row)
    assert msg["id"] == "m_dispatch_abc"
    assert msg["seq"] == 7
    assert msg["text"] == "summary"
    assert msg["messageType"] == "research_dispatch_result"
    assert msg["workUnitId"] == "wu_1"
    assert msg["artifactRefs"] == ["art_1"]
    assert msg["authorLabel"] == "exec"


# ---------------------------------------------------------------------------
# GET /agent/messages route (postgres cursor pagination)
# ---------------------------------------------------------------------------


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def test_get_agent_messages_paginates_from_table(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Postgres branch reads the table with cursor pagination; the response
    carries hasMore + nextBeforeSeq."""
    page = [
        {"id": "m3", "seq": 3, "role": "assistant", "text": "three", "at": "2026-08-11T00:00:03Z",
         "messageType": "chat"},
        {"id": "m2", "seq": 2, "role": "user", "text": "two", "at": "2026-08-11T00:00:02Z",
         "messageType": "chat"},
    ]

    class _Repo:
        def list_messages(self, **kwargs):
            return list(page), True, 3

    monkeypatch.setattr(settings, "persistence_backend", "postgres")
    monkeypatch.setattr(agent_route, "TaskMessageRepository", lambda: _Repo())

    class _TaskRepo:
        def get_task(self, task_id):
            return {"taskId": task_id, "ownerId": "o1", "workspaceId": "w1",
                    "createdBy": "o1", "agentSessionId": "sess"}

    monkeypatch.setattr(agent_route, "TaskRepository", _TaskRepo)

    response = client.get(
        "/api/tasks/t1/agent/messages?limit=2",
        headers={"X-OpenAIR-Owner-Id": "o1", "X-OpenAIR-Workspace-Id": "w1", "X-OpenAIR-Created-By": "o1"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert [m["id"] for m in body["messages"]] == ["m3", "m2"]
    assert body["hasMore"] is True
    assert body["nextBeforeSeq"] == 2  # oldest in this page → next page anchor


def test_get_agent_messages_before_cursor_excludes_anchor(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}

    class _Repo:
        def list_messages(self, **kwargs):
            captured.update(kwargs)
            return [], False, None

    monkeypatch.setattr(settings, "persistence_backend", "postgres")
    monkeypatch.setattr(agent_route, "TaskMessageRepository", lambda: _Repo())

    class _TaskRepo:
        def get_task(self, task_id):
            return {"taskId": task_id, "ownerId": "o1", "workspaceId": "w1", "createdBy": "o1"}

    monkeypatch.setattr(agent_route, "TaskRepository", _TaskRepo)

    response = client.get(
        "/api/tasks/t1/agent/messages?before=10&limit=25",
        headers={"X-OpenAIR-Owner-Id": "o1", "X-OpenAIR-Workspace-Id": "w1", "X-OpenAIR-Created-By": "o1"},
    )
    assert response.status_code == 200, response.text
    assert captured["before_seq"] == 10
    assert captured["limit"] == 25
