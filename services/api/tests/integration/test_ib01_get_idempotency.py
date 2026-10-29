"""A4b regression aid: repository read projections must write no domain facts.

The A4b PowerShell evidence harness supplies the cross-process HTTP proof; this
test keeps the PostgreSQL read-only invariant local and deterministic.
"""
from __future__ import annotations

import os
from urllib.parse import urlparse

import psycopg
import pytest

from app.config import settings
from app.modules.artifact_repository import ArtifactRepository
from app.modules.durable_execution import DurableExecutionRepository
from app.modules.task_repository import TaskRepository


@pytest.fixture()
def seeded_run() -> tuple[DurableExecutionRepository, dict]:
    dsn = os.environ.get("IB01_TEST_DATABASE_URL", "")
    if not dsn:
        pytest.skip("requires an isolated IB01_TEST_DATABASE_URL")
    parsed = urlparse(dsn)
    settings.postgres_host = parsed.hostname or "localhost"; settings.postgres_port = parsed.port or 5432
    settings.postgres_user = parsed.username or "postgres"; settings.postgres_password = parsed.password or ""
    settings.postgres_database = parsed.path.lstrip("/"); settings.persistence_backend = "postgres"
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE"); conn.execute("CREATE SCHEMA public")
    # Durable migration creates its own tables but extends the ordinary artifact
    # tables; bootstrap those base tables first for an empty disposable schema.
    TaskRepository().bootstrap(); ArtifactRepository().bootstrap(); repo = DurableExecutionRepository(); repo.bootstrap()
    TaskRepository().insert_task({"taskId": "a4b-task", "ownerId": "owner", "workspaceId": "workspace", "createdBy": "owner", "title": "A4b", "currentStage": "problem_modeling", "status": "not_started", "creationStatus": "confirmed", "stages": ["problem_modeling"], "stageDetails": [], "artifacts": [], "capabilityCalls": []})
    run, _ = repo.start_initial(task_id="a4b-task", owner_id="owner", workspace_id="workspace", idempotency_key="a4b-key", source_mode="replay")
    return repo, run


def _snapshot() -> dict[str, list[dict]]:
    dsn = os.environ["IB01_TEST_DATABASE_URL"]
    tables = ["openair_tasks", "openair_execution_runs", "openair_execution_run_events", "openair_execution_capability_calls", "openair_artifacts", "openair_artifact_uploads", "openair_execution_artifact_associations", "openair_execution_idempotency", "openair_execution_callback_rejections", "openair_execution_start_attempts"]
    with psycopg.connect(dsn, row_factory=psycopg.rows.dict_row) as conn:
        return {table: conn.execute(f"SELECT to_jsonb(t) AS row FROM {table} t ORDER BY 1").fetchall() for table in tables}


def test_durable_get_projections_are_strictly_read_only(seeded_run: tuple[DurableExecutionRepository, dict]) -> None:
    repo, run = seeded_run; before = _snapshot()
    for _ in range(3):
        assert repo.get_run(run["run_id"], "owner", "workspace")
        assert repo.list_events(run["run_id"], "owner", "workspace", 0)
        assert repo.get_call(run["run_id"], "owner", "workspace")
        assert repo.list_artifacts(run["run_id"], "owner", "workspace") == []
    assert _snapshot() == before
