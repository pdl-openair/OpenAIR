"""Isolated PostgreSQL proof for A1 external-action budget migration/bootstrap.

Requires IB01_TEST_DATABASE_URL (disposable test/qa PostgreSQL). Never connects to
DATABASE_URL / OPENAIR_TEST_DATABASE_URL / production / development.
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
from app.modules.postgres_migrations import MIGRATIONS_DIR, apply_postgres_migrations
from app.modules.task_repository import TaskRepository


UP_VERSION = "20260802_03_p01_external_action_budget.sql"
DOWN_PATH = Path(__file__).resolve().parents[2] / "migrations" / "20260802_03_p01_external_action_budget_down.sql"
UP_PATH = Path(__file__).resolve().parents[2] / "migrations" / UP_VERSION

_FORBIDDEN_DSN_ENVS = (
    "DATABASE_URL",
    "OPENAIR_TEST_DATABASE_URL",
    "POSTGRES_DSN",
    "OPENAIR_DATABASE_URL",
)


def _configure_isolated_postgres() -> str:
    for name in _FORBIDDEN_DSN_ENVS:
        if os.environ.get(name):
            pytest.fail(f"A1 migration tests refuse to read {name}; use IB01_TEST_DATABASE_URL only")
    dsn = os.environ.get("IB01_TEST_DATABASE_URL", "").strip()
    if not dsn:
        pytest.fail("IB01_TEST_DATABASE_URL is required; A1 migration tests refuse mocks")
    parsed = urlparse(dsn)
    database = parsed.path.lstrip("/").lower()
    if parsed.scheme not in {"postgres", "postgresql"} or not database or not any(
        tag in database for tag in ("test", "qa")
    ):
        pytest.fail("IB01_TEST_DATABASE_URL must identify an isolated PostgreSQL test or qa database")
    if any(tag in database for tag in ("prod", "production", "development", "dev")):
        pytest.fail("A1 migration tests refuse development and production databases")
    settings.postgres_host = parsed.hostname or "localhost"
    settings.postgres_port = parsed.port or 5432
    settings.postgres_user = parsed.username or "postgres"
    settings.postgres_password = parsed.password or ""
    settings.postgres_database = database
    settings.persistence_backend = "postgres"
    return dsn


def _query(sql: str, params: tuple = ()) -> list[dict]:
    with psycopg.connect(os.environ["IB01_TEST_DATABASE_URL"], row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return list(cur.fetchall())


def _reset_public_schema(dsn: str) -> None:
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")


def _ledger_versions() -> set[str]:
    rows = _query("SELECT version FROM openair_schema_migrations")
    return {row["version"] for row in rows}


def _assert_budget_schema_present() -> None:
    columns = {
        row["column_name"]: row
        for row in _query(
            """SELECT column_name, data_type, is_nullable, column_default
               FROM information_schema.columns
              WHERE table_schema = 'public'
                AND table_name = 'openair_execution_runs'
                AND column_name IN ('max_external_actions', 'external_actions_reserved')"""
        )
    }
    assert set(columns) == {"max_external_actions", "external_actions_reserved"}
    assert columns["max_external_actions"]["data_type"] == "integer"
    assert columns["max_external_actions"]["is_nullable"] == "YES"
    assert columns["external_actions_reserved"]["data_type"] == "integer"
    assert columns["external_actions_reserved"]["is_nullable"] == "NO"

    constraints = {
        row["conname"]
        for row in _query(
            """SELECT c.conname
               FROM pg_constraint c
               JOIN pg_class t ON t.oid = c.conrelid
              WHERE t.relname = 'openair_execution_runs'
                AND c.conname = 'openair_execution_runs_external_actions_reserved_lte_max'"""
        )
    }
    assert constraints == {"openair_execution_runs_external_actions_reserved_lte_max"}

    assert _query(
        "SELECT to_regclass('public.openair_execution_external_action_reservations') AS name"
    )[0]["name"] == "openair_execution_external_action_reservations"

    indexes = {
        row["indexname"]
        for row in _query(
            """SELECT indexname FROM pg_indexes
              WHERE schemaname = 'public'
                AND indexname IN (
                    'uq_openair_external_action_initial_reserved',
                    'idx_openair_external_action_reservations_run'
                )"""
        )
    }
    assert indexes == {
        "uq_openair_external_action_initial_reserved",
        "idx_openair_external_action_reservations_run",
    }


def _assert_budget_schema_absent() -> None:
    columns = _query(
        """SELECT column_name FROM information_schema.columns
          WHERE table_schema = 'public'
            AND table_name = 'openair_execution_runs'
            AND column_name IN ('max_external_actions', 'external_actions_reserved')"""
    )
    assert columns == []
    assert _query(
        "SELECT to_regclass('public.openair_execution_external_action_reservations') AS name"
    )[0]["name"] is None
    assert UP_VERSION not in _ledger_versions()


def test_refuses_unset_ib01_test_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("IB01_TEST_DATABASE_URL", raising=False)
    with pytest.raises(pytest.fail.Exception, match="IB01_TEST_DATABASE_URL is required"):
        _configure_isolated_postgres()


def test_refuses_non_test_qa_ib01_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IB01_TEST_DATABASE_URL", "postgresql://u:p@localhost:5432/openair_prod")
    with pytest.raises(pytest.fail.Exception, match="test or qa"):
        _configure_isolated_postgres()


def test_refuses_other_database_url_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IB01_TEST_DATABASE_URL", "postgresql://u:p@localhost:5432/openair_test")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost:5432/should_not_read")
    with pytest.raises(pytest.fail.Exception, match="refuse to read DATABASE_URL"):
        _configure_isolated_postgres()


def test_runner_never_auto_applies_down_files() -> None:
    """Source evidence: down SQL is excluded from apply_postgres_migrations scan."""
    from app.modules import postgres_migrations as mod

    source = Path(mod.__file__).read_text(encoding="utf-8")
    assert 'endswith("_down.sql")' in source
    assert "never" in source.lower() or "Down migrations" in source
    scanned = sorted(path.name for path in MIGRATIONS_DIR.glob("*.sql") if not path.name.endswith("_down.sql"))
    assert UP_VERSION in scanned
    assert DOWN_PATH.name not in scanned
    down_sql = DOWN_PATH.read_text(encoding="utf-8")
    up_sql = UP_PATH.read_text(encoding="utf-8")
    assert "DELETE FROM openair_schema_migrations" in down_sql
    assert UP_VERSION in down_sql
    assert "preflight" in up_sql
    assert "refuse auto-dedupe" in up_sql
    assert "uq_openair_external_action_initial_reserved" in up_sql
    assert "ADD COLUMN IF NOT EXISTS" not in up_sql
    assert "CREATE TABLE IF NOT EXISTS" not in up_sql
    assert "CREATE INDEX IF NOT EXISTS" not in up_sql
    assert "CREATE UNIQUE INDEX IF NOT EXISTS" not in up_sql
    assert "DROP CONSTRAINT IF EXISTS" not in up_sql
    assert UP_VERSION in down_sql
    assert "20260802_03" in UP_VERSION


def test_p01_external_action_budget_migration_fresh_idempotent_down_reup() -> None:
    dsn = _configure_isolated_postgres()
    _reset_public_schema(dsn)

    # Fresh apply via production bootstrap path.
    TaskRepository().bootstrap()
    ArtifactRepository().bootstrap()
    ledger_before = set()
    try:
        ledger_before = _ledger_versions()
    except psycopg.errors.UndefinedTable:
        ledger_before = set()

    DurableExecutionRepository().bootstrap(True)
    ledger_after = _ledger_versions()
    assert UP_VERSION not in ledger_before
    assert UP_VERSION in ledger_after
    _assert_budget_schema_present()

    # Preflight on empty reservation table: no conflicts.
    conflicts = _query(
        """SELECT call_id, COUNT(*) AS n
             FROM openair_execution_external_action_reservations
            WHERE attempt_kind = 'initial' AND outcome = 'reserved'
            GROUP BY call_id
           HAVING COUNT(*) > 1"""
    )
    assert conflicts == []

    # Repeat bootstrap / apply is idempotent via ledger (not IF NOT EXISTS).
    DurableExecutionRepository().bootstrap(True)
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            apply_postgres_migrations(cur)
        conn.commit()
    assert _ledger_versions() == ledger_after
    _assert_budget_schema_present()

    # Manual down (runner does not execute *_down.sql).
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(DOWN_PATH.read_text(encoding="utf-8"))
    _assert_budget_schema_absent()

    # Re-up via bootstrap after down.
    DurableExecutionRepository().bootstrap(True)
    assert UP_VERSION in _ledger_versions()
    _assert_budget_schema_present()


def test_bootstrap_false_skips_migration_scan_on_fresh_schema() -> None:
    dsn = _configure_isolated_postgres()
    _reset_public_schema(dsn)
    TaskRepository().bootstrap()
    ArtifactRepository().bootstrap()

    DurableExecutionRepository().bootstrap(False)

    with pytest.raises(psycopg.errors.UndefinedTable):
        _ledger_versions()
    columns = _query(
        """SELECT column_name FROM information_schema.columns
          WHERE table_schema = 'public'
            AND table_name = 'openair_execution_runs'
            AND column_name IN ('max_external_actions', 'external_actions_reserved')"""
    )
    assert columns == []
