"""PostgreSQL proof for the T0-1 ResearchDesign contract migration.

Requires IB01_TEST_DATABASE_URL pointing at a disposable test/qa database.
The test refuses production/development DSNs and never reads DATABASE_URL.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import urlparse

import psycopg
import pytest
from psycopg.rows import dict_row


UP_VERSION = "20260909_01_research_design_authority.sql"
MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"
UP_PATH = MIGRATIONS_DIR / UP_VERSION
DOWN_PATH = MIGRATIONS_DIR / "20260909_01_research_design_authority_down.sql"

_FORBIDDEN_DSN_ENVS = (
    "DATABASE_URL",
    "OPENAIR_TEST_DATABASE_URL",
    "POSTGRES_DSN",
    "OPENAIR_DATABASE_URL",
)


def _isolated_dsn() -> str:
    for name in _FORBIDDEN_DSN_ENVS:
        if os.environ.get(name):
            pytest.fail(f"T0-1 migration test refuses to read {name}; use IB01_TEST_DATABASE_URL only")
    dsn = os.environ.get("IB01_TEST_DATABASE_URL", "").strip()
    if not dsn:
        pytest.fail("IB01_TEST_DATABASE_URL is required; T0-1 migration test refuses mocks")
    parsed = urlparse(dsn)
    database = parsed.path.lstrip("/").lower()
    if parsed.scheme not in {"postgres", "postgresql"} or not database or not any(
        marker in database for marker in ("test", "qa")
    ):
        pytest.fail("IB01_TEST_DATABASE_URL must identify an isolated PostgreSQL test or qa database")
    if any(marker in database for marker in ("prod", "production", "development", "dev")):
        pytest.fail("T0-1 migration test refuses development and production databases")
    return dsn


def _reset_schema(dsn: str) -> None:
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute("DROP SCHEMA public CASCADE")
        connection.execute("CREATE SCHEMA public")
        connection.execute(
            """CREATE TABLE openair_tasks (
                   task_id TEXT PRIMARY KEY,
                   payload JSONB NOT NULL
               )"""
        )
        connection.execute(
            """CREATE TABLE openair_schema_migrations (
                   version TEXT PRIMARY KEY,
                   applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
               )"""
        )


def _design(design_id: str, target_outputs: list[str]) -> dict:
    return {
        "researchDesignId": design_id,
        "understanding": {"targetOutputs": target_outputs},
    }


def _payload(*, design_id: str, workflow: dict | None = None) -> dict:
    payload = {"researchDesignVersions": [_design(design_id, ["final answer", "evidence summary"])]}
    if workflow is not None:
        payload["workflowAdmission"] = workflow
    return payload


def _row(dsn: str, task_id: str) -> dict:
    with psycopg.connect(dsn, row_factory=dict_row) as connection:
        return connection.execute(
            "SELECT payload FROM openair_tasks WHERE task_id = %s",
            (task_id,),
        ).fetchone()["payload"]


def test_t0_1_migration_source_is_one_time_and_strict() -> None:
    up_sql = UP_PATH.read_text(encoding="utf-8")
    down_sql = DOWN_PATH.read_text(encoding="utf-8")

    assert "research_design_completion_targets_not_migratable" in up_sql
    assert "research_design_workflow_provenance_not_migratable" in up_sql
    assert "research_design_confirmation_not_migratable" in up_sql
    assert "projectCompletionRequirements" in up_sql
    assert "workflowProvenanceRef" in up_sql
    assert "DELETE FROM openair_schema_migrations" in down_sql
    assert UP_VERSION in down_sql


def test_t0_1_migration_up_and_down_preserve_recoverable_truth() -> None:
    dsn = _isolated_dsn()
    _reset_schema(dsn)
    workflow = {
        "workflowId": "opentrajectory_research_workflow",
        "workflowVersion": 7,
        "workflowContentHash": "a" * 64,
    }
    with psycopg.connect(dsn) as connection:
        connection.execute(
            "INSERT INTO openair_tasks(task_id, payload) VALUES (%s, %s::jsonb), (%s, %s::jsonb)",
            (
                "generic",
                json.dumps(_payload(design_id="design_generic")),
                "workflow",
                json.dumps(_payload(design_id="design_workflow", workflow=workflow)),
            ),
        )
        connection.execute(UP_PATH.read_text(encoding="utf-8"))
        connection.execute(
            "INSERT INTO openair_schema_migrations(version) VALUES (%s)",
            (UP_VERSION,),
        )

    generic = _row(dsn, "generic")["researchDesignVersions"][0]
    workflow_design = _row(dsn, "workflow")["researchDesignVersions"][0]
    assert [row["description"] for row in generic["projectCompletionRequirements"]] == [
        "final answer",
        "evidence summary",
    ]
    assert generic["workflowProvenanceRef"] is None
    assert workflow_design["workflowProvenanceRef"] == {
        "workflowId": "opentrajectory_research_workflow",
        "workflowVersion": 7,
        "contentHash": f"sha256:{'a' * 64}",
    }

    with psycopg.connect(dsn) as connection:
        connection.execute(DOWN_PATH.read_text(encoding="utf-8"))

    rolled_back = _row(dsn, "workflow")["researchDesignVersions"][0]
    assert "projectCompletionRequirements" not in rolled_back
    assert "workflowProvenanceRef" not in rolled_back


def test_t0_1_migration_rejects_incomplete_workflow_provenance() -> None:
    dsn = _isolated_dsn()
    _reset_schema(dsn)
    incomplete = {
        "workflowId": "opentrajectory_research_workflow",
        "workflowVersion": 7,
    }
    with psycopg.connect(dsn) as connection:
        connection.execute(
            "INSERT INTO openair_tasks(task_id, payload) VALUES (%s, %s::jsonb)",
            ("bad_workflow", json.dumps(_payload(design_id="design_bad", workflow=incomplete))),
        )

    with psycopg.connect(dsn) as connection:
        with pytest.raises(psycopg.errors.RaiseException, match="workflow_provenance_not_migratable"):
            connection.execute(UP_PATH.read_text(encoding="utf-8"))
        connection.rollback()

    design = _row(dsn, "bad_workflow")["researchDesignVersions"][0]
    assert "projectCompletionRequirements" not in design
    assert "workflowProvenanceRef" not in design


def test_t0_1_migration_rejects_inherited_design_confirmation() -> None:
    dsn = _isolated_dsn()
    _reset_schema(dsn)
    payload = _payload(design_id="design_inherited")
    payload["researchDesignVersions"][0].update(
        {
            "version": 2,
            "status": "confirmed",
            "confirmationRef": "confirmation_v1",
        }
    )
    payload["confirmations"] = [
        {
            "confirmationId": "confirmation_v1",
            "targetType": "research_design",
            "targetId": "design_inherited",
            "targetVersion": 1,
            "decision": "approved",
        }
    ]
    with psycopg.connect(dsn) as connection:
        connection.execute(
            "INSERT INTO openair_tasks(task_id, payload) VALUES (%s, %s::jsonb)",
            ("bad_confirmation", json.dumps(payload)),
        )

    with psycopg.connect(dsn) as connection:
        with pytest.raises(psycopg.errors.RaiseException, match="confirmation_not_migratable"):
            connection.execute(UP_PATH.read_text(encoding="utf-8"))
        connection.rollback()

    design = _row(dsn, "bad_confirmation")["researchDesignVersions"][0]
    assert "projectCompletionRequirements" not in design
    assert "workflowProvenanceRef" not in design
