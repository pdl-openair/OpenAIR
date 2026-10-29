"""PostgreSQL evidence for stale Research Start queue reconciliation.

The fixture accepts only an explicitly named disposable test/QA database and
creates one isolated schema per test.  Application database settings are never
read, so these tests cannot address the Demo database accidentally.
"""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest

DSN_ENV = "OPENAIR_TEST_POSTGRES_DSN"
MIGRATIONS = (
    "20260725_02_durable_research_start.sql",
    "20260805_01_research_start_catalog_promote.sql",
    "20260830_02_research_start_runtime_source_fence.sql",
    "20260802_04_c1_provider_http_attempts.sql",
    "20260805_03_provider_http_attempts_multi.sql",
    "20260920_90_research_start_attempt_response_evidence.sql",
    "20260928_01_physical_execution_truth.sql",
    "20261008_02_research_start_response_body.sql",
)
MIGRATIONS_DIR = Path(__file__).parents[2] / "migrations"


def _intent(text: str) -> dict[str, object]:
    return {
        "input": text,
        "sourcePage": "home",
        "selectedHint": None,
        "presetQuery": None,
        "sessionId": None,
        "createClarificationDraft": False,
    }


@pytest.fixture()
def isolated_postgres(monkeypatch: pytest.MonkeyPatch):
    psycopg = pytest.importorskip("psycopg")
    from psycopg import sql
    from psycopg.rows import dict_row

    dsn = os.environ.get(DSN_ENV, "").strip()
    if not dsn:
        pytest.skip(f"{DSN_ENV} is required; refusing to connect to an unspecified database")
    admin = psycopg.connect(dsn, row_factory=dict_row)
    schema = f"stale_queue_{uuid4().hex}"
    try:
        with admin.cursor() as cur:
            cur.execute("SELECT current_database() AS database_name")
            database_name = cur.fetchone()["database_name"]
            database_name_parts = set(database_name.lower().replace("-", "_").split("_"))
            if database_name_parts.isdisjoint({"test", "qa"}):
                pytest.fail(f"refusing stale-queue test against non-test database: {database_name}")
            cur.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
            cur.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
            for migration in MIGRATIONS:
                cur.execute((MIGRATIONS_DIR / migration).read_text(encoding="utf-8"))

            from app.modules.task_repository import TASKS_TABLE_SQL

            cur.execute(TASKS_TABLE_SQL)
            cur.execute(
                "ALTER TABLE openair_tasks "
                "ADD COLUMN IF NOT EXISTS revision BIGINT NOT NULL DEFAULT 0"
            )
        admin.commit()

        def connect():
            conn = psycopg.connect(dsn, row_factory=dict_row)
            with conn.cursor() as cur:
                cur.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
            conn.commit()
            return conn

        from app.modules import research_start_repository as repository_module
        from app.modules.research_start_repository import ResearchStartRepository

        monkeypatch.setattr(repository_module.settings, "heavy_task_queue_enabled", False)
        monkeypatch.setattr(repository_module.settings, "heavy_task_global_limit", 100)
        monkeypatch.setattr(repository_module.settings, "heavy_task_per_user_limit", 100)
        yield ResearchStartRepository(connection_factory=connect), connect, repository_module
    finally:
        admin.rollback()
        with admin.cursor() as cur:
            cur.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))
        admin.commit()
        admin.close()


def _old_source(current: str) -> str:
    candidate = "0" * 64
    return candidate if candidate != current else "1" * 64


def _make_stale(connect, request_ids: list[str], old_source: str) -> None:
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE research_start_requests SET runtime_source_version = %s "
                "WHERE request_id = ANY(%s)",
                (old_source, request_ids),
            )
            assert cur.rowcount == len(request_ids)
        conn.commit()
    finally:
        conn.close()


def _request_rows(connect, request_ids: list[str]) -> list[dict]:
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT request_id, session_id, state, lease_owner, runtime_source_version "
                "FROM research_start_requests WHERE request_id = ANY(%s) ORDER BY request_id",
                (request_ids,),
            )
            return list(cur.fetchall())
    finally:
        conn.close()


def test_stale_queue_two_ghosts_are_terminalized_before_new_admission(isolated_postgres) -> None:
    repository, connect, module = isolated_postgres
    owner = f"owner-{uuid4().hex}"
    workspace = "workspace-a"
    ghosts = [
        repository.reserve(owner, workspace, f"ghost-{index}", _intent(f"ghost {index}"))
        for index in range(2)
    ]
    _make_stale(
        connect,
        [row["requestId"] for row in ghosts],
        _old_source(module.settings.runtime_source_version),
    )

    module.settings.heavy_task_queue_enabled = True
    module.settings.heavy_task_per_user_limit = 2
    admitted = repository.reserve(owner, workspace, "fresh", _intent("fresh request"))

    assert admitted["outcome"] == "reserved"
    assert admitted["status"] == "queued"
    rows = _request_rows(connect, [row["requestId"] for row in ghosts] + [admitted["requestId"]])
    assert [row["state"] for row in rows].count("failed") == 2
    assert [row["state"] for row in rows].count("queued") == 1
    for ghost in ghosts:
        public = repository.get_scoped_session(owner, workspace, ghost["sessionId"])
        assert public["status"] == "failed"
        assert public["planningProgress"]["code"] == "failed"
        assert public["userMessage"]


def test_current_runtime_queue_still_enforces_heavy_limit(isolated_postgres) -> None:
    repository, _connect, module = isolated_postgres
    from app.modules.research_start_repository import ResearchStartAdmissionFullError

    module.settings.heavy_task_queue_enabled = True
    module.settings.heavy_task_per_user_limit = 2
    owner = f"owner-{uuid4().hex}"
    workspace = "workspace-live"
    live = [
        repository.reserve(owner, workspace, f"live-{index}", _intent(f"live {index}"))
        for index in range(2)
    ]

    with pytest.raises(ResearchStartAdmissionFullError) as raised:
        repository.reserve(owner, workspace, "live-third", _intent("must be rejected"))

    assert raised.value.code == "heavy_queue_full"
    assert raised.value.scope == "owner_workspace"
    assert {
        row["state"]
        for row in _request_rows(connect=_connect, request_ids=[r["requestId"] for r in live])
    } == {"queued"}


def test_reserved_task_and_leased_request_are_not_stale_reconciliation_targets(
    isolated_postgres,
) -> None:
    repository, connect, module = isolated_postgres
    owner = f"owner-{uuid4().hex}"
    leased = repository.reserve(owner, "workspace-lease", "protected-lease", _intent("lease"))
    claim = repository.claim("worker-protected")
    assert claim is not None and claim["requestId"] == leased["requestId"]
    protected_task = repository.reserve(owner, "workspace-task", "protected-task", _intent("task"))

    # The queued request has a real reserved Task; the other request remains
    # leased.  Neither is eligible for stale reconciliation.
    conn = connect()
    old_source = _old_source(module.settings.runtime_source_version)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE research_start_requests
                      SET state = 'queued', lease_owner = NULL, lease_expires_at = NULL,
                          runtime_source_version = %s
                    WHERE request_id = %s""",
                (old_source, protected_task["requestId"]),
            )
            cur.execute(
                """INSERT INTO openair_tasks(
                       task_id, owner_id, workspace_id, status, current_stage, title, payload
                   ) VALUES (
                       %s, %s, %s, 'draft', 'problem_modeling', 'reserved owner', %s::jsonb
                   )""",
                (
                    protected_task["reservedTaskId"],
                    owner,
                    "workspace-task",
                    json.dumps({"taskId": protected_task["reservedTaskId"], "status": "draft"}),
                ),
            )
            cur.execute(
                """UPDATE research_start_requests
                      SET runtime_source_version = %s
                    WHERE request_id = %s""",
                (old_source, leased["requestId"]),
            )
        conn.commit()
    finally:
        conn.close()

    module.settings.heavy_task_queue_enabled = True
    repository.reserve(owner, "workspace-trigger", "trigger", _intent("trigger reconciliation"))

    rows = {
        row["request_id"]: row
        for row in _request_rows(connect, [protected_task["requestId"], leased["requestId"]])
    }
    assert rows[protected_task["requestId"]]["state"] == "queued"
    assert rows[leased["requestId"]]["state"] == "leased"
    assert rows[leased["requestId"]]["lease_owner"] == "worker-protected"


def test_conflict_truth_and_concurrent_submits_do_not_over_admit(isolated_postgres) -> None:
    repository, connect, module = isolated_postgres
    from app.modules.research_start_repository import (
        ResearchStartAdmissionFullError,
        ResearchStartRepository,
    )

    owner = f"owner-{uuid4().hex}"
    workspace = "workspace-conflict"
    stale = [
        repository.reserve(owner, workspace, f"stale-{index}", _intent(f"stale {index}"))
        for index in range(2)
    ]
    live = repository.reserve(owner, workspace, "live", _intent("live"))
    _make_stale(
        connect,
        [row["requestId"] for row in stale],
        _old_source(module.settings.runtime_source_version),
    )
    module.settings.heavy_task_queue_enabled = True
    module.settings.heavy_task_per_user_limit = 2

    def submit(index: int):
        candidate = ResearchStartRepository(connection_factory=connect)
        try:
            return candidate.reserve(
                owner,
                workspace,
                f"concurrent-{index}",
                _intent(f"concurrent {index}"),
            )
        except ResearchStartAdmissionFullError as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(submit, range(2)))

    assert sum(isinstance(row, dict) for row in outcomes) == 1
    assert sum(isinstance(row, ResearchStartAdmissionFullError) for row in outcomes) == 1
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT state, count(*) AS count
                     FROM research_start_requests
                    WHERE owner_id = %s AND workspace_id = %s
                    GROUP BY state""",
                (owner, workspace),
            )
            counts = {row["state"]: row["count"] for row in cur.fetchall()}
        assert counts == {"failed": 2, "queued": 2}
    finally:
        conn.close()
    assert _request_rows(connect, [live["requestId"]])[0]["state"] == "queued"


def test_restart_reconciliation_self_heals_unowned_historical_row(isolated_postgres) -> None:
    repository, connect, module = isolated_postgres
    from app.modules.research_start_repository import ResearchStartRepository

    owner = f"owner-{uuid4().hex}"
    workspace = "workspace-restart"
    stale = repository.reserve(owner, workspace, "stale-before-restart", _intent("stale"))
    _make_stale(
        connect,
        [stale["requestId"]],
        _old_source(module.settings.runtime_source_version),
    )

    # A fresh repository instance models API/worker process restart. The next
    # formal admission owns reconciliation; no manual cleanup is required.
    restarted = ResearchStartRepository(connection_factory=connect)
    module.settings.heavy_task_queue_enabled = True
    module.settings.heavy_task_per_user_limit = 1
    admitted = restarted.reserve(owner, workspace, "after-restart", _intent("new"))

    rows = {
        row["request_id"]: row
        for row in _request_rows(connect, [stale["requestId"], admitted["requestId"]])
    }
    assert rows[stale["requestId"]]["state"] == "failed"
    assert rows[admitted["requestId"]]["state"] == "queued"
    public = restarted.get_scoped_session(owner, workspace, stale["sessionId"])
    assert public["status"] == "failed"


def test_uncertain_provider_fact_remains_queued_and_counts_against_admission(
    isolated_postgres,
) -> None:
    repository, connect, module = isolated_postgres
    from app.modules.research_start_repository import (
        ResearchStartAdmissionFullError,
        ResearchStartRepository,
    )

    owner = f"owner-{uuid4().hex}"
    workspace = "workspace-provider-owner"
    reserved = repository.reserve(owner, workspace, "uncertain", _intent("uncertain"))
    claim = repository.claim("worker-old")
    assert claim is not None
    attempt = repository.begin_attempt(
        claim["requestId"],
        "worker-old",
        claim["leaseEpoch"],
        "research_planner",
        1,
    )

    conn = connect()
    try:
        with conn.cursor() as cur:
            # Close the logical attempt so provider submission evidence is the
            # only durable-owner predicate protecting this queued request.
            cur.execute(
                """UPDATE research_start_attempts
                      SET state = 'failed', result_payload = %s::jsonb,
                          finished_at = clock_timestamp()
                    WHERE attempt_id = %s""",
                (json.dumps({"failureCode": "provider_transport_uncertain"}), attempt["attemptId"]),
            )
            cur.execute(
                """UPDATE research_start_requests
                      SET state = 'queued', lease_owner = NULL, lease_expires_at = NULL,
                          runtime_source_version = %s
                    WHERE request_id = %s""",
                (_old_source(module.settings.runtime_source_version), reserved["requestId"]),
            )
            cur.execute(
                """INSERT INTO openair_provider_http_attempts(
                       attempt_id, capability_call_id, attempt_number, request_hash,
                       provider_endpoint_identity, transport_outcome, evidence_kind,
                       parent_kind, parent_id, operation_identity,
                       logical_attempt_ordinal, execution_state,
                       originating_authority, io_started, submission_proven
                   ) VALUES (
                       %s, NULL, 1, %s, %s, 'transport_uncertain', 'provider_submission',
                       'research_start', %s, 'research_start:research_planner',
                       1, 'unknown', 'research_start:research_planner', TRUE, NULL
                   )""",
                (
                    f"pha_{uuid4().hex}",
                    "a" * 64,
                    "test:sha256:" + "b" * 64,
                    reserved["requestId"],
                ),
            )
        conn.commit()
    finally:
        conn.close()

    restarted = ResearchStartRepository(connection_factory=connect)
    module.settings.heavy_task_queue_enabled = True
    module.settings.heavy_task_per_user_limit = 1
    with pytest.raises(ResearchStartAdmissionFullError) as raised:
        restarted.reserve(owner, workspace, "must-stay-full", _intent("new"))

    assert raised.value.scope == "owner_workspace"
    assert _request_rows(connect, [reserved["requestId"]])[0]["state"] == "queued"
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT state FROM research_start_attempts WHERE attempt_id = %s",
                (attempt["attemptId"],),
            )
            assert cur.fetchone()["state"] == "failed"
    finally:
        conn.close()
    public = restarted.get_scoped_session(owner, workspace, reserved["sessionId"])
    assert public["status"] == "running"
