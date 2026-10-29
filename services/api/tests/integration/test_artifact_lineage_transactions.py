"""T09 durable live Artifact lineage migration checks."""

from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
import pytest


DSN_ENV = "OPENAIR_T09_POSTGRES_DSN"
MIGRATIONS = Path(__file__).resolve().parents[2] / "migrations"
UP = MIGRATIONS / "20260730_05_artifact_lineage.sql"
DOWN = MIGRATIONS / "20260730_05_artifact_lineage_down.sql"
FIXTURE = (
    Path(__file__).resolve().parents[4]
    / "packages"
    / "shared"
    / "test-fixtures"
    / "research-orchestration-v1.2-valid.json"
)


def _dsn() -> str:
    dsn = os.environ.get(DSN_ENV, "").strip()
    if not dsn:
        pytest.fail(f"{DSN_ENV} is required")
    return dsn


def _bootstrap_before_t09(connection: psycopg.Connection) -> None:
    from app.modules.artifact_repository import ARTIFACTS_TABLE_SQL
    from app.modules.task_repository import TASKS_TABLE_SQL

    connection.execute(TASKS_TABLE_SQL)
    connection.execute(ARTIFACTS_TABLE_SQL)
    connection.execute(
        """CREATE TABLE IF NOT EXISTS openair_schema_migrations (
               version TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"""
    )
    applied = {
        row["version"]
        for row in connection.execute("SELECT version FROM openair_schema_migrations").fetchall()
    }
    for path in sorted(MIGRATIONS.glob("*.sql")):
        if path.name.endswith("_down.sql") or path.name >= UP.name:
            continue
        if path.name in applied:
            continue
        connection.execute(path.read_text(encoding="utf-8"))
        connection.execute(
            "INSERT INTO openair_schema_migrations(version) VALUES (%s) ON CONFLICT DO NOTHING",
            (path.name,),
        )


def _task_payload(suffix: str) -> dict:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))["researchTask"]
    encoded = json.dumps(payload)
    for key in (
        "task_contract_full",
        "decision_contract_full",
        "wu_contract_full",
        "call_contract_full",
        "artifact_contract_full",
        "sub_contract_full",
    ):
        encoded = encoded.replace(key, f"{key}_{suffix}")
    return json.loads(encoded)


def _insert_task(connection: psycopg.Connection, payload: dict) -> None:
    connection.execute(
        """INSERT INTO openair_tasks (
               task_id, owner_id, workspace_id, status, current_stage, title,
               domain, created_at, updated_at, payload, revision
           ) VALUES (%s, 't09-owner', 't09-workspace', %s, 'solution_design', %s,
                     'research_task', NOW(), NOW(), %s::jsonb, %s)""",
        (
            payload["taskId"],
            payload["status"],
            payload["taskId"],
            json.dumps(payload),
            payload["revision"],
        ),
    )


@pytest.fixture(autouse=True)
def t09_schema() -> None:
    with psycopg.connect(_dsn(), autocommit=True, row_factory=dict_row) as connection:
        _bootstrap_before_t09(connection)
        yield
        connection.execute("DROP TABLE IF EXISTS openair_artifact_lineage_audit")
        connection.execute("DROP TABLE IF EXISTS openair_artifact_lineage")
        connection.execute("DROP TABLE IF EXISTS openair_capability_call_lineage")
        connection.execute("DROP TABLE IF EXISTS openair_work_unit_lineage")
        connection.execute("DELETE FROM openair_schema_migrations WHERE version = %s", (UP.name,))
        connection.execute("DELETE FROM openair_tasks WHERE owner_id = 't09-owner'")


def test_upgrade_backfill_is_idempotent() -> None:
    payload = _task_payload("upgrade")
    with psycopg.connect(_dsn(), autocommit=True, row_factory=dict_row) as connection:
        _insert_task(connection, payload)
        connection.execute(UP.read_text(encoding="utf-8"))
        connection.execute(UP.read_text(encoding="utf-8"))
        rows = connection.execute(
            """SELECT artifact_id, work_unit_id, execution_decision_id,
                      substage_instance_id, source_capability_call_id, migration_key
                 FROM openair_artifact_lineage"""
        ).fetchall()
        assert rows == [
            {
                "artifact_id": "artifact_contract_full_upgrade",
                "work_unit_id": "wu_contract_full_upgrade",
                "execution_decision_id": "decision_contract_full_upgrade",
                "substage_instance_id": "sub_contract_full_upgrade",
                "source_capability_call_id": "call_contract_full_upgrade",
                "migration_key": "20260730_05:artifact:task_contract_full_upgrade:artifact_contract_full_upgrade",
            }
        ]
        assert connection.execute(
            "SELECT count(*) AS count FROM openair_artifact_lineage_audit"
        ).fetchone()["count"] == 1


def test_lineage_constraints_reject_cross_workunit_call() -> None:
    with psycopg.connect(_dsn(), autocommit=True, row_factory=dict_row) as connection:
        connection.execute(UP.read_text(encoding="utf-8"))
        connection.execute(
            """INSERT INTO openair_tasks (
                   task_id, owner_id, workspace_id, status, current_stage, title,
                   domain, created_at, updated_at, payload, revision
               ) VALUES ('task-a', 't09-owner', 't09-workspace', 'not_started',
                         'solution_design', 'task-a', 'research_task', NOW(), NOW(), '{}'::jsonb, 0)"""
        )
        connection.execute(
            """INSERT INTO openair_work_unit_lineage (
                   task_id, work_unit_id, execution_decision_id, substage_instance_id,
                   source_mode, dispatchable, migration_key
               ) VALUES
                   ('task-a', 'work-a', 'decision-a', 'substage-a', 'live', TRUE, '20260730_05:work-a'),
                   ('task-a', 'work-b', 'decision-b', 'substage-b', 'live', TRUE, '20260730_05:work-b')"""
        )
        connection.execute(
            """INSERT INTO openair_capability_call_lineage (
                   task_id, capability_call_id, work_unit_id, substage_instance_id, migration_key
               ) VALUES ('task-a', 'call-b', 'work-b', 'substage-b', '20260730_05:call-b')"""
        )
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            connection.execute(
                """INSERT INTO openair_artifact_lineage (
                       task_id, artifact_id, work_unit_id, execution_decision_id,
                       substage_instance_id, source_capability_call_id, direct_work_unit_output,
                       migration_key
                   ) VALUES ('task-a', 'artifact-a', 'work-a', 'decision-a',
                             'substage-a', 'call-b', FALSE, '20260730_05:artifact-a')"""
            )


def test_down_refuses_nonempty_lineage_then_clean_reupgrade() -> None:
    payload = _task_payload("down")
    with psycopg.connect(_dsn(), autocommit=True, row_factory=dict_row) as connection:
        _insert_task(connection, payload)
        connection.execute(UP.read_text(encoding="utf-8"))
        with pytest.raises(psycopg.errors.RaiseException, match="non-empty"):
            connection.execute(DOWN.read_text(encoding="utf-8"))
        connection.rollback()
        connection.execute("DELETE FROM openair_artifact_lineage_audit")
        connection.execute("DELETE FROM openair_artifact_lineage")
        connection.execute("DELETE FROM openair_capability_call_lineage")
        connection.execute("DELETE FROM openair_work_unit_lineage")
        connection.execute(DOWN.read_text(encoding="utf-8"))
        connection.execute(UP.read_text(encoding="utf-8"))
        assert connection.execute(
            "SELECT to_regclass('openair_artifact_lineage') AS relation"
        ).fetchone()["relation"] == "openair_artifact_lineage"
