from __future__ import annotations

import os
from contextlib import contextmanager
from types import SimpleNamespace
from pathlib import Path
from uuid import uuid4

import pytest

from app.models.research_external_result import ExplicitRetryCommand
from app.modules.research_orchestration_repository import (
    ExternalRecoveryConflictError,
    ResearchOrchestrationRepository,
)


MIGRATIONS = Path(__file__).parents[2] / "migrations"
T04_UP = MIGRATIONS / "20260728_01_research_orchestration_v1_2.sql"
T06_UP = MIGRATIONS / "20260730_04_external_execution_evidence.sql"
T06_DOWN = MIGRATIONS / "20260730_04_external_execution_evidence_down.sql"
PLAINTEXT_EVIDENCE_UP = MIGRATIONS / "20261008_03_plaintext_external_evidence.sql"


@pytest.fixture()
def postgres_t06():
    psycopg = pytest.importorskip("psycopg")
    from psycopg import sql
    from psycopg.rows import dict_row

    dsn = os.environ.get("OPENAIR_T06_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("OPENAIR_T06_TEST_DATABASE_URL is not configured")
    schema = "t06_" + uuid4().hex
    connection = psycopg.connect(dsn, autocommit=True, row_factory=dict_row)
    with connection.cursor() as cursor:
        cursor.execute(sql.SQL("CREATE SCHEMA {}" ).format(sql.Identifier(schema)))
        cursor.execute(sql.SQL("SET search_path TO {}" ).format(sql.Identifier(schema)))
        cursor.execute(
            """CREATE TABLE openair_schema_migrations (
                   version TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"""
        )
        cursor.execute(
            """CREATE TABLE openair_tasks (
                   task_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, workspace_id TEXT NOT NULL,
                   status TEXT NOT NULL, revision BIGINT NOT NULL, payload JSONB NOT NULL,
                   created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"""
        )
        cursor.execute(T04_UP.read_text(encoding="utf-8"))
        cursor.execute(T06_UP.read_text(encoding="utf-8"))
        cursor.execute(PLAINTEXT_EVIDENCE_UP.read_text(encoding="utf-8"))
        cursor.execute(
            "INSERT INTO openair_schema_migrations(version) VALUES (%s), (%s), (%s)",
            (T04_UP.name, T06_UP.name, PLAINTEXT_EVIDENCE_UP.name),
        )
    try:
        yield connection, schema
    finally:
        with connection.cursor() as cursor:
            cursor.execute("SET search_path TO public")
            cursor.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
        connection.close()


def _seed_lineage(connection) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """INSERT INTO openair_tasks(task_id, owner_id, workspace_id, status, revision, payload)
               VALUES ('task-1', 'owner-1', 'workspace-1', 'awaiting_confirmation', 9, '{"revision":9}')"""
        )
        cursor.execute(
            """INSERT INTO openair_dispatch(
                   dispatch_id, task_id, execution_decision_id, work_unit_id,
                   idempotency_key, status, attempt_count, authorization_task_revision)
               VALUES ('dispatch-1', 'task-1', 'decision-1', 'work-1',
                       'provider-key-1', 'submission_unknown', 1, 8)"""
        )


def _retry_command(*, command_request_id: str, owner_id: str, workspace_id: str, fingerprint: str = "a" * 64):
    return ExplicitRetryCommand(
        task_id="task-request-scope",
        work_unit_id="work-request-scope",
        dispatch_id="dispatch-request-scope",
        execution_decision_id="decision-request-scope",
        prior_capability_call_id="call-request-scope",
        prior_attempt_number=1,
        command_request_id=command_request_id,
        canonical_payload_fingerprint=fingerprint,
        expected_task_revision=9,
        owner_id=owner_id,
        workspace_id=workspace_id,
        actor_id="actor-request-scope",
        reason_code="transient_transport_failure",
    )


def _retry_repository(connection):
    @contextmanager
    def connect():
        yield connection

    return ResearchOrchestrationRepository(
        connection_factory=connect,
        runtime_settings=SimpleNamespace(
            research_orchestration_v1=True,
            persistence_backend="postgres",
        ),
    )


def _insert_retry_ledger(
    connection,
    *,
    command_request_id: str,
    owner_id: str,
    workspace_id: str,
    fingerprint: str = "a" * 64,
) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """INSERT INTO openair_external_retry_attempt (
                   command_request_id, canonical_payload_fingerprint, task_id,
                   execution_decision_id, work_unit_id, dispatch_id,
                   prior_capability_call_id, capability_call_id, attempt_number,
                   call_idempotency_key, provider_idempotency_key, owner_id,
                   workspace_id, actor_id, reason_code, expected_task_revision,
                   committed_task_revision)
               VALUES (%s, %s, 'task-1', 'decision-1', 'work-1', 'dispatch-1',
                       'call-1', %s, 2, %s, 'provider-key-1', %s, %s,
                       'actor-foreign-scope', 'transient_transport_failure', 9, 10)""",
            (
                command_request_id,
                fingerprint,
                f"call-foreign-{command_request_id}",
                f"call-key-foreign-{command_request_id}",
                owner_id,
                workspace_id,
            ),
        )


@pytest.mark.parametrize(
    ("foreign_owner_id", "foreign_workspace_id"),
    (("owner-foreign", "workspace-1"), ("owner-1", "workspace-foreign")),
    ids=("owner-mismatch", "workspace-mismatch"),
)
def test_retry_replay_scope_race_rejects_foreign_ledger_before_scoped_load_or_write(
    postgres_t06,
    foreign_owner_id: str,
    foreign_workspace_id: str,
) -> None:
    connection, _schema = postgres_t06
    _seed_lineage(connection)
    repository = _retry_repository(connection)
    command = _retry_command(
        command_request_id=f"retry-race-{foreign_owner_id}-{foreign_workspace_id}",
        owner_id="owner-1",
        workspace_id="workspace-1",
    )

    assert repository.get_retry_result(command) is None
    _insert_retry_ledger(
        connection,
        command_request_id=command.command_request_id,
        owner_id=foreign_owner_id,
        workspace_id=foreign_workspace_id,
    )

    with pytest.raises(ExternalRecoveryConflictError, match="^retry_command_payload_conflict$"):
        repository.create_retry_attempt(command)

    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) AS count FROM openair_tasks WHERE task_id = %s", (command.task_id,))
        assert cursor.fetchone()["count"] == 0
        cursor.execute("SELECT count(*) AS count FROM openair_dispatch")
        assert cursor.fetchone()["count"] == 1
        cursor.execute("SELECT count(*) AS count FROM openair_external_retry_attempt")
        assert cursor.fetchone()["count"] == 1


def test_retry_replay_same_scope_preserves_idempotency_and_fingerprint_conflict(postgres_t06) -> None:
    connection, _schema = postgres_t06
    _seed_lineage(connection)
    repository = _retry_repository(connection)
    command = _retry_command(
        command_request_id="retry-replay-same-scope",
        owner_id="owner-1",
        workspace_id="workspace-1",
    )
    _insert_retry_ledger(
        connection,
        command_request_id=command.command_request_id,
        owner_id=command.owner_id,
        workspace_id=command.workspace_id,
    )

    result = repository.get_retry_result(command)
    assert result == {
        "status": "created",
        "capabilityCallId": "call-foreign-retry-replay-same-scope",
        "attemptNumber": 2,
        "taskRevision": 10,
        "replayed": True,
    }
    assert repository.create_retry_attempt(command) == result
    with pytest.raises(ExternalRecoveryConflictError, match="^retry_command_payload_conflict$"):
        repository.get_retry_result(
            _retry_command(
                command_request_id=command.command_request_id,
                owner_id=command.owner_id,
                workspace_id=command.workspace_id,
                fingerprint="b" * 64,
            )
        )


def test_up_catalog_has_protected_store_reconciliation_retry_and_directive(postgres_t06) -> None:
    connection, _schema = postgres_t06
    with connection.cursor() as cursor:
        cursor.execute(
            """SELECT table_name FROM information_schema.tables
                WHERE table_schema = current_schema() AND table_name LIKE 'openair_external_%'"""
        )
        tables = {row["table_name"] for row in cursor.fetchall()}
    assert {
        "openair_external_execution",
        "openair_external_evidence",
        "openair_external_evidence_access_audit",
        "openair_external_reconciliation",
        "openair_external_reconciliation_evidence",
        "openair_external_retry_attempt",
    } <= tables


def test_plaintext_shape_and_retention_constraints(postgres_t06) -> None:
    connection, _schema = postgres_t06
    _seed_lineage(connection)
    base = (
        "evidence-1", "task-1", "decision-1", "work-1", "call-1", "dispatch-1", 1,
        "owner-1", "workspace-1", "fake-provider", "fake-adapter", "rejected_response",
        "provider_known_rejection", "content_filter", "[]", "a" * 64, 12, False, False,
        b"raw-response", "b" * 64, "c" * 64,
    )
    with connection.cursor() as cursor:
        cursor.execute(
            """INSERT INTO openair_external_evidence(
                   evidence_id, task_id, execution_decision_id, work_unit_id, capability_call_id,
                   dispatch_id, attempt_number, owner_id, workspace_id, provider_id, adapter,
                   evidence_kind, failure_class, normalized_finish_reason, diagnostics,
                   content_sha256, byte_length, truncated, hash_incomplete, raw_body,
                   canonical_metadata_fingerprint, canonical_payload_fingerprint,
                   created_at, expires_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb,
                       %s, %s, %s, %s, %s, %s, %s, NOW(), NOW() + INTERVAL '7 days')""",
            base,
        )
        with pytest.raises(Exception):
            cursor.execute(
                """INSERT INTO openair_external_evidence
                       SELECT 'evidence-2', task_id, execution_decision_id, work_unit_id,
                              capability_call_id, dispatch_id, attempt_number, owner_id, workspace_id,
                              provider_id, adapter, evidence_kind, failure_class, normalized_finish_reason,
                              diagnostics, content_sha256, byte_length, truncated, hash_incomplete,
                              NULL, canonical_metadata_fingerprint, canonical_payload_fingerprint,
                              reconciliation_evidence_refs, created_at, expires_at, NULL
                         FROM openair_external_evidence WHERE evidence_id = 'evidence-1'"""
            )
        connection.rollback()


def test_down_refuses_nonempty_audit_facts(postgres_t06) -> None:
    connection, _schema = postgres_t06
    _seed_lineage(connection)
    with connection.cursor() as cursor:
        cursor.execute(
            """INSERT INTO openair_external_execution(
                   dispatch_id, task_id, execution_decision_id, work_unit_id, capability_call_id,
                   attempt_number, owner_id, workspace_id, provider_id, adapter,
                   provider_idempotency_key, external_state, failure_code,
                   canonical_payload_fingerprint)
               VALUES ('dispatch-1', 'task-1', 'decision-1', 'work-1', 'call-1', 1,
                       'owner-1', 'workspace-1', 'fake-provider', 'fake-adapter',
                       'provider-key-1', 'unknown', 'transport_unknown', %s)""",
            ("d" * 64,),
        )
        with pytest.raises(Exception, match="non-empty external execution evidence"):
            cursor.execute(T06_DOWN.read_text(encoding="utf-8"))
        connection.rollback()


def test_empty_down_removes_tables_and_exact_ledger_version(postgres_t06) -> None:
    connection, _schema = postgres_t06
    with connection.cursor() as cursor:
        cursor.execute(T06_DOWN.read_text(encoding="utf-8"))
        cursor.execute(
            "SELECT 1 FROM openair_schema_migrations WHERE version = %s",
            (T06_UP.name,),
        )
        assert cursor.fetchone() is None
