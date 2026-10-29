"""Isolated PostgreSQL proof for the P0-1 Recovery authority migrations.

Requires IB01_TEST_DATABASE_URL pointing at a disposable test/qa PostgreSQL.
The suite refuses fallback DSNs, SQLite, development, and production databases.
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse

import psycopg
import pytest
from psycopg.rows import dict_row

from app.config import settings
from app.modules.artifact_repository import ArtifactRepository
from app.modules.durable_execution import DurableExecutionRepository
from app.modules.task_repository import TaskRepository


MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"
UP_VERSIONS = (
    "20260802_04_p01_task_recovery.sql",
    "20260802_05_p01_scope_integrity_and_execution_projection.sql",
    "20260802_06_p01_recovery_message_preflight.sql",
    "20260802_07_p01_recovery_requested_mutex.sql",
)
DOWN_FILES = tuple(
    MIGRATIONS_DIR / version.replace(".sql", "_down.sql")
    for version in reversed(UP_VERSIONS)
)
HEX_A = "a" * 64
HEX_B = "b" * 64
HEX_C = "c" * 64

_FORBIDDEN_DSN_ENVS = (
    "DATABASE_URL",
    "OPENAIR_TEST_DATABASE_URL",
    "POSTGRES_DSN",
    "OPENAIR_DATABASE_URL",
)


def _configure_isolated_postgres() -> str:
    for name in _FORBIDDEN_DSN_ENVS:
        if os.environ.get(name):
            pytest.fail(f"Recovery migration tests refuse to read {name}; use IB01_TEST_DATABASE_URL only")
    dsn = os.environ.get("IB01_TEST_DATABASE_URL", "").strip()
    if not dsn:
        pytest.fail("IB01_TEST_DATABASE_URL is required; Recovery migration tests refuse mocks")
    parsed = urlparse(dsn)
    database = parsed.path.lstrip("/").lower()
    if parsed.scheme not in {"postgres", "postgresql"} or not database or not any(
        tag in database for tag in ("test", "qa")
    ):
        pytest.fail("IB01_TEST_DATABASE_URL must identify an isolated PostgreSQL test or qa database")
    if any(tag in database for tag in ("prod", "production", "development", "dev")):
        pytest.fail("Recovery migration tests refuse development and production databases")
    settings.postgres_host = parsed.hostname or "localhost"
    settings.postgres_port = parsed.port or 5432
    settings.postgres_user = parsed.username or "postgres"
    settings.postgres_password = parsed.password or ""
    settings.postgres_database = database
    settings.persistence_backend = "postgres"
    return dsn


def _reset_public_schema(dsn: str) -> None:
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")


def _rows(dsn: str, sql: str, params: tuple = ()) -> list[dict]:
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        return list(conn.execute(sql, params).fetchall())


def _insert_task(conn: psycopg.Connection, task_id: str, owner_id: str, workspace_id: str, revision: int) -> None:
    conn.execute(
        """INSERT INTO openair_tasks(
               task_id, owner_id, workspace_id, status, current_stage, title,
               created_at, updated_at, payload, revision
           ) VALUES (%s, %s, %s, 'failed', 'execution_validation', %s,
                     clock_timestamp(), clock_timestamp(), '{}'::jsonb, %s)""",
        (task_id, owner_id, workspace_id, task_id, revision),
    )


def _insert_run(
    conn: psycopg.Connection,
    run_id: str,
    task_id: str,
    owner_id: str,
    workspace_id: str,
    revision: int,
    run_number: int,
    run_kind: str,
    state: str,
) -> None:
    conn.execute(
        """INSERT INTO openair_execution_runs(
               run_id, task_id, owner_id, workspace_id, task_revision,
               run_number, run_kind, idempotency_key, request_fingerprint,
               input_snapshot, source_mode, capability_call_id, state
           ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s,
                     '{}'::jsonb, 'live', %s, %s)""",
        (
            run_id,
            task_id,
            owner_id,
            workspace_id,
            revision,
            run_number,
            run_kind,
            f"idem-{run_id}",
            HEX_A,
            f"call-{run_id}",
            state,
        ),
    )


def _insert_request(
    conn: psycopg.Connection,
    request_id: str,
    task_id: str,
    source_run_id: str,
    owner_id: str,
    workspace_id: str,
    revision: int,
    *,
    kind: str = "retry",
) -> None:
    conn.execute(
        """INSERT INTO openair_task_recovery_requests(
               request_id, task_id, source_run_id, recovery_kind, state,
               expected_task_revision, owner_id, workspace_id, created_by,
               idempotency_key, payload_fingerprint, plan_fingerprint
           ) VALUES (%s, %s, %s, %s, 'requested', %s, %s, %s, %s, %s, %s, %s)""",
        (
            request_id,
            task_id,
            source_run_id,
            kind,
            revision,
            owner_id,
            workspace_id,
            owner_id,
            f"idem-{request_id}",
            HEX_B,
            HEX_C,
        ),
    )


def _insert_message_and_link(
    conn: psycopg.Connection,
    request_id: str,
    message_id: str,
    task_id: str,
    owner_id: str,
    workspace_id: str,
) -> None:
    conn.execute(
        """INSERT INTO openair_recovery_messages(
               message_id, task_id, owner_id, workspace_id, created_by,
               message_kind, content, payload_fingerprint
           ) VALUES (%s, %s, %s, %s, %s, 'recovery_instruction', %s, %s)""",
        (message_id, task_id, owner_id, workspace_id, owner_id, f"recover {task_id}", HEX_A),
    )
    conn.execute(
        """INSERT INTO openair_recovery_request_messages(
               request_id, message_id, task_id, owner_id, workspace_id, ordinal
           ) VALUES (%s, %s, %s, %s, %s, 1)""",
        (request_id, message_id, task_id, owner_id, workspace_id),
    )


def _confirm_request(conn: psycopg.Connection, request_id: str, successor_run_id: str) -> None:
    conn.execute(
        """UPDATE openair_task_recovery_requests
              SET state = 'confirmed', successor_run_id = %s,
                  confirmed_at = clock_timestamp(), updated_at = clock_timestamp()
            WHERE request_id = %s""",
        (successor_run_id, request_id),
    )


def _insert_projection(
    conn: psycopg.Connection,
    projection_id: str,
    request_id: str,
    task_id: str,
    source_run_id: str,
    successor_run_id: str,
    owner_id: str,
    workspace_id: str,
    revision: int,
    *,
    kind: str = "retry",
    source_sequence: int = 1,
) -> None:
    conn.execute(
        """INSERT INTO openair_recovery_execution_projections(
               projection_id, request_id, task_id, source_run_id,
               successor_run_id, expected_task_revision, owner_id,
               workspace_id, recovery_kind, source_lease_epoch_fence,
               source_next_event_sequence_fence
           ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 0, %s)""",
        (
            projection_id,
            request_id,
            task_id,
            source_run_id,
            successor_run_id,
            revision,
            owner_id,
            workspace_id,
            kind,
            source_sequence,
        ),
    )


def test_recovery_authority_migrations_postgres_apply_constraints_rollback_reup() -> None:
    dsn = _configure_isolated_postgres()
    _reset_public_schema(dsn)

    TaskRepository().bootstrap()
    ArtifactRepository().bootstrap()
    DurableExecutionRepository().bootstrap(True)

    versions = {row["version"] for row in _rows(dsn, "SELECT version FROM openair_schema_migrations")}
    assert set(UP_VERSIONS) <= versions
    assert _rows(
        dsn,
        """SELECT to_regclass('public.openair_task_recovery_requests') AS requests,
                  to_regclass('public.openair_recovery_messages') AS messages,
                  to_regclass('public.openair_recovery_execution_projections') AS projections""",
    ) == [
        {
            "requests": "openair_task_recovery_requests",
            "messages": "openair_recovery_messages",
            "projections": "openair_recovery_execution_projections",
        }
    ]

    with psycopg.connect(dsn) as conn:
        _insert_task(conn, "task-a", "user-a", "workspace-a", 7)
        _insert_task(conn, "task-b", "user-b", "workspace-b", 3)
        _insert_task(conn, "task-c", "user-a", "workspace-a", 5)
        _insert_task(conn, "task-mutex", "user-a", "workspace-a", 3)
        _insert_task(conn, "task-rollback", "user-a", "workspace-a", 10)
        _insert_task(conn, "task-stale", "user-a", "workspace-a", 6)
        _insert_task(conn, "task-running", "user-a", "workspace-a", 2)
        # Durable failure projection advances Task revision. Recovery must use
        # current Task revision while retaining the source Run's old revision.
        _insert_run(conn, "run-a-source", "task-a", "user-a", "workspace-a", 6, 1, "initial_start", "failed")
        _insert_run(conn, "run-c-source", "task-c", "user-a", "workspace-a", 4, 1, "initial_start", "failed")
        _insert_run(conn, "run-mutex-source", "task-mutex", "user-a", "workspace-a", 2, 1, "initial_start", "failed")
        _insert_run(conn, "run-rollback-source", "task-rollback", "user-a", "workspace-a", 9, 1, "initial_start", "failed")
        _insert_run(conn, "run-stale-source", "task-stale", "user-a", "workspace-a", 5, 1, "initial_start", "failed")
        _insert_run(conn, "run-running-source", "task-running", "user-a", "workspace-a", 1, 1, "initial_start", "queued")
        conn.execute(
            """INSERT INTO openair_execution_run_events(run_id, sequence, type, payload)
               VALUES ('run-a-source', 1, 'failed', '{"result":"preserved"}'::jsonb)"""
        )

        conn.execute(
            "UPDATE openair_execution_runs SET next_event_sequence = 2 WHERE run_id = 'run-a-source'"
        )
        conn.execute(
            """INSERT INTO openair_artifacts(
                   artifact_id, owner_id, workspace_id, created_by, kind, title,
                   metadata, source_task_id, source_execution_run_id, source_mode
               ) VALUES ('artifact-existing', 'user-a', 'workspace-a', 'user-a',
                         'report', 'Existing result', '{"preserved":true}'::jsonb,
                         'task-a', 'run-a-source', 'live')"""
        )

    assert _rows(
        dsn,
        """SELECT t.revision AS current_task_revision,
                  r.task_revision AS source_run_revision,
                  r.state AS source_state
             FROM openair_tasks t
             JOIN openair_execution_runs r ON r.run_id = 'run-a-source'
            WHERE t.task_id = 'task-a'""",
    ) == [{"current_task_revision": 7, "source_run_revision": 6, "source_state": "failed"}]

    # Legal same-scope request/message/successor/projection.
    with psycopg.connect(dsn) as conn:
        _insert_request(conn, "recovery-a", "task-a", "run-a-source", "user-a", "workspace-a", 7)
        _insert_message_and_link(conn, "recovery-a", "message-a", "task-a", "user-a", "workspace-a")
        _insert_run(conn, "run-a-successor", "task-a", "user-a", "workspace-a", 7, 2, "retry", "queued")
        _confirm_request(conn, "recovery-a", "run-a-successor")
        _insert_projection(
            conn,
            "projection-a",
            "recovery-a",
            "task-a",
            "run-a-source",
            "run-a-successor",
            "user-a",
            "workspace-a",
            7,
            source_sequence=2,
        )

    assert _rows(
        dsn,
        """SELECT r.state, r.successor_run_id, p.inheritance_mode
             FROM openair_task_recovery_requests r
             JOIN openair_recovery_execution_projections p USING (request_id)
            WHERE r.request_id = 'recovery-a'""",
    ) == [
        {
            "state": "confirmed",
            "successor_run_id": "run-a-successor",
            "inheritance_mode": "reference_existing_facts",
        }
    ]
    assert _rows(
        dsn,
        """SELECT request_id, payload_fingerprint
             FROM openair_task_recovery_requests
            WHERE created_by = 'user-a'
              AND task_id = 'task-a'
              AND idempotency_key = 'idem-recovery-a'""",
    ) == [{"request_id": "recovery-a", "payload_fingerprint": HEX_B}]
    with pytest.raises(psycopg.errors.RaiseException, match="revision is stale"):
        with psycopg.connect(dsn) as conn:
            _insert_request(
                conn,
                "recovery-a-source-revision-stale",
                "task-a",
                "run-a-source",
                "user-a",
                "workspace-a",
                6,
            )
    with pytest.raises(psycopg.errors.RaiseException, match="cannot be deleted"):
        with psycopg.connect(dsn) as conn:
            conn.execute("DELETE FROM openair_task_recovery_requests WHERE request_id = 'recovery-a'")
    with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
        with psycopg.connect(dsn) as conn:
            conn.execute(
                """UPDATE openair_recovery_execution_projections
                      SET source_next_event_sequence_fence = 2
                    WHERE projection_id = 'projection-a'"""
            )

    # A request cannot be inserted directly as confirmed because no linked
    # preflight message can exist before the request row.
    with pytest.raises(psycopg.errors.RaiseException, match="preflight message"):
        with psycopg.connect(dsn) as conn:
            _insert_run(conn, "run-c-uncommitted", "task-c", "user-a", "workspace-a", 5, 2, "retry", "queued")
            conn.execute(
                """INSERT INTO openair_task_recovery_requests(
                       request_id, task_id, source_run_id, recovery_kind, state,
                       expected_task_revision, owner_id, workspace_id, created_by,
                       idempotency_key, payload_fingerprint, plan_fingerprint,
                       successor_run_id, confirmed_at
                   ) VALUES ('recovery-direct-confirm', 'task-c', 'run-c-source',
                             'retry', 'confirmed', 5, 'user-a', 'workspace-a',
                             'user-a', 'idem-direct-confirm', %s, %s,
                             'run-c-uncommitted', clock_timestamp())""",
                (HEX_B, HEX_C),
            )
    assert _rows(dsn, "SELECT run_id FROM openair_execution_runs WHERE run_id = 'run-c-uncommitted'") == []

    # Cross-Task source and cross-scope request facts are rejected by composite FKs/CHECKs.
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        with psycopg.connect(dsn) as conn:
            _insert_request(conn, "recovery-cross-task", "task-c", "run-a-source", "user-a", "workspace-a", 5)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        with psycopg.connect(dsn) as conn:
            _insert_request(conn, "recovery-cross-user", "task-a", "run-a-source", "user-b", "workspace-a", 7)
    with pytest.raises(psycopg.errors.RaiseException, match="revision is stale"):
        with psycopg.connect(dsn) as conn:
            _insert_request(
                conn,
                "recovery-stale",
                "task-stale",
                "run-stale-source",
                "user-a",
                "workspace-a",
                5,
            )
    with pytest.raises(psycopg.errors.RaiseException, match="not in a recoverable failure state"):
        with psycopg.connect(dsn) as conn:
            _insert_request(
                conn,
                "recovery-running",
                "task-running",
                "run-running-source",
                "user-a",
                "workspace-a",
                2,
            )

    # A message from another Task cannot be attached to this request, even in the same scope.
    with psycopg.connect(dsn) as conn:
        _insert_request(conn, "recovery-c", "task-c", "run-c-source", "user-a", "workspace-a", 5)
        conn.execute(
            """INSERT INTO openair_recovery_messages(
                   message_id, task_id, owner_id, workspace_id, created_by,
                   message_kind, content, payload_fingerprint
               ) VALUES ('message-wrong-task', 'task-a', 'user-a', 'workspace-a', 'user-a',
                         'failure_context', 'wrong task', %s)""",
            (HEX_A,),
        )
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        with psycopg.connect(dsn) as conn:
            conn.execute(
                """INSERT INTO openair_recovery_request_messages(
                       request_id, message_id, task_id, owner_id, workspace_id, ordinal
                   ) VALUES ('recovery-c', 'message-wrong-task', 'task-c',
                             'user-a', 'workspace-a', 1)"""
            )

    # retry and replan contend on one requested mutex for the same source/revision.
    with psycopg.connect(dsn) as conn:
        _insert_request(
            conn,
            "recovery-mutex-retry",
            "task-mutex",
            "run-mutex-source",
            "user-a",
            "workspace-a",
            3,
            kind="retry",
        )
    with pytest.raises(psycopg.errors.UniqueViolation):
        with psycopg.connect(dsn) as conn:
            _insert_request(
                conn,
                "recovery-mutex-replan",
                "task-mutex",
                "run-mutex-source",
                "user-a",
                "workspace-a",
                3,
                kind="replan",
            )

    # A second bound successor for the same source/revision is rejected. The
    # attempted successor Run is in the same transaction and rolls back too.
    with psycopg.connect(dsn) as conn:
        _insert_request(conn, "recovery-a-second", "task-a", "run-a-source", "user-a", "workspace-a", 7)
        _insert_message_and_link(conn, "recovery-a-second", "message-a-second", "task-a", "user-a", "workspace-a")
    with pytest.raises(psycopg.errors.UniqueViolation):
        with psycopg.connect(dsn) as conn:
            _insert_run(conn, "run-a-successor-2", "task-a", "user-a", "workspace-a", 7, 3, "retry", "queued")
            _confirm_request(conn, "recovery-a-second", "run-a-successor-2")
    assert _rows(dsn, "SELECT run_id FROM openair_execution_runs WHERE run_id = 'run-a-successor-2'") == []

    # Failure after every Recovery fact was staged leaves no partial rows.
    with pytest.raises(RuntimeError, match="force transaction rollback"):
        with psycopg.connect(dsn) as conn:
            _insert_request(
                conn,
                "recovery-rollback",
                "task-rollback",
                "run-rollback-source",
                "user-a",
                "workspace-a",
                10,
            )
            _insert_message_and_link(
                conn,
                "recovery-rollback",
                "message-rollback",
                "task-rollback",
                "user-a",
                "workspace-a",
            )
            _insert_run(
                conn,
                "run-rollback-successor",
                "task-rollback",
                "user-a",
                "workspace-a",
                10,
                2,
                "retry",
                "queued",
            )
            _confirm_request(conn, "recovery-rollback", "run-rollback-successor")
            _insert_projection(
                conn,
                "projection-rollback",
                "recovery-rollback",
                "task-rollback",
                "run-rollback-source",
                "run-rollback-successor",
                "user-a",
                "workspace-a",
                10,
            )
            raise RuntimeError("force transaction rollback")

    assert _rows(dsn, "SELECT request_id FROM openair_task_recovery_requests WHERE request_id = 'recovery-rollback'") == []
    assert _rows(dsn, "SELECT projection_id FROM openair_recovery_execution_projections WHERE projection_id = 'projection-rollback'") == []
    assert _rows(dsn, "SELECT run_id FROM openair_execution_runs WHERE run_id = 'run-rollback-successor'") == []
    assert _rows(
        dsn,
        "SELECT payload FROM openair_execution_run_events WHERE run_id = 'run-a-source' AND sequence = 1",
    ) == [{"payload": {"result": "preserved"}}]
    assert _rows(
        dsn,
        "SELECT metadata FROM openair_artifacts WHERE artifact_id = 'artifact-existing'",
    ) == [{"metadata": {"preserved": True}}]

    # Manual down follows the runner convention; reverse order removes only
    # Recovery objects and ledger rows. Re-applying through bootstrap succeeds.
    with psycopg.connect(dsn, autocommit=True) as conn:
        for down_path in DOWN_FILES:
            conn.execute(down_path.read_text(encoding="utf-8"))

    assert not ({row["version"] for row in _rows(dsn, "SELECT version FROM openair_schema_migrations")} & set(UP_VERSIONS))
    assert _rows(
        dsn,
        """SELECT to_regclass('public.openair_task_recovery_requests') AS requests,
                  to_regclass('public.openair_recovery_messages') AS messages,
                  to_regclass('public.openair_recovery_execution_projections') AS projections""",
    ) == [{"requests": None, "messages": None, "projections": None}]

    DurableExecutionRepository().bootstrap(True)
    versions = {row["version"] for row in _rows(dsn, "SELECT version FROM openair_schema_migrations")}
    assert set(UP_VERSIONS) <= versions
    assert _rows(
        dsn,
        "SELECT metadata FROM openair_artifacts WHERE artifact_id = 'artifact-existing'",
    ) == [{"metadata": {"preserved": True}}]


def test_recovery_migration_manifest_is_unique_and_ordered() -> None:
    official = sorted(path.name for path in MIGRATIONS_DIR.glob("20260802_0[4-7]_p01_*.sql"))
    assert official == sorted((*UP_VERSIONS, *(path.name for path in DOWN_FILES)))
    for version in UP_VERSIONS:
        assert "external_action_budget" not in (MIGRATIONS_DIR / version).read_text(encoding="utf-8")
