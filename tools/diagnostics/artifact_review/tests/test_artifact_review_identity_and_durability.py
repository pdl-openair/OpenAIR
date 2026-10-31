from __future__ import annotations

import json
import os
import shutil
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from app.auth import resolve_request_identity
from app.config import settings
from app.modules import postgres_migrations
from app.modules.artifact_repository import ArtifactRepository
from app.modules.task_repository import TaskRepository
from app.routes import artifacts as artifacts_route


DSN_ENV = "OPENAIR_TEST_POSTGRES_DSN"
LEGACY_DSN_ENV = "OPENAIR_LINE_C_ROUND5_POSTGRES_DSN"
MIGRATION_NAMES = (
    "20260719_00_artifact_catalog_base.sql",
    "20260720_01_ib01_durable_execution.sql",
    "20260720_02_ib01_restart_idempotency_evidence.sql",
    "20260807_01_artifact_cleanup_outbox.sql",
    "20260807_03_artifact_cleanup_consumer.sql",
    "20260807_04_artifact_storage_identity_fence.sql",
    "20260807_05_user_delete_receipts.sql",
    "20260807_06_artifact_storage_references.sql",
    "20260807_07_artifact_storage_identity_final.sql",
)


class PgArtifactRepository(ArtifactRepository):
    def __init__(self, dsn: str):
        self._dsn = dsn

    @contextmanager
    def _connect(self):
        with psycopg.connect(self._dsn, row_factory=dict_row) as connection:
            yield connection
            connection.commit()


class PgTaskRepository(TaskRepository):
    def __init__(self, dsn: str):
        self._dsn = dsn

    @contextmanager
    def _connect(self):
        with psycopg.connect(self._dsn, row_factory=dict_row) as connection:
            yield connection
            connection.commit()


def _harness_postgres() -> tuple[str, str, str]:
    dsn = os.environ.get(DSN_ENV, "").strip()
    run_id = os.environ.get("OPENAIR_TEST_POSTGRES_RUN_ID", "").strip()
    container_id = os.environ.get("OPENAIR_TEST_POSTGRES_CONTAINER_ID", "").strip()
    image_id = os.environ.get("OPENAIR_TEST_POSTGRES_IMAGE_ID", "").strip()
    if not all((dsn, run_id, container_id, image_id)):
        pytest.fail(
            "NO_VALID_EVIDENCE: run this Suite through tools/testkit/postgres_harness.py",
            pytrace=False,
        )
    if os.environ.get(LEGACY_DSN_ENV):
        pytest.fail(f"NO_VALID_EVIDENCE: legacy {LEGACY_DSN_ENV} reached the F2 process")
    parsed = urlparse(dsn)
    database = parsed.path.lstrip("/")
    if parsed.scheme not in {"postgres", "postgresql"} or not database.startswith("openair_test_"):
        pytest.fail("NO_VALID_EVIDENCE: Harness DSN is not an isolated PostgreSQL test database")
    if database != os.environ.get("OPENAIR_TEST_POSTGRES_DATABASE"):
        pytest.fail("NO_VALID_EVIDENCE: PostgreSQL database identity does not match Harness evidence")
    return dsn, run_id, database


@pytest.fixture()
def review_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    dsn, run_id, database = _harness_postgres()
    with psycopg.connect(dsn, row_factory=dict_row) as connection:
        identity = connection.execute(
            "SELECT current_database() AS database, "
            "current_setting('server_version') AS server_version, "
            "to_regclass('public.openair_tasks') AS preexisting_tasks"
        ).fetchone()
    assert identity is not None
    assert identity["database"] == database
    assert identity["server_version"]
    assert identity["preexisting_tasks"] is None

    PgTaskRepository(dsn).bootstrap()
    repository_root = Path(__file__).resolve().parents[4]
    source = repository_root / "services" / "api" / "migrations"
    migration_dir = tmp_path / "migrations"
    migration_dir.mkdir()
    for name in MIGRATION_NAMES:
        shutil.copy2(source / name, migration_dir / name)
    monkeypatch.setattr(postgres_migrations, "MIGRATIONS_DIR", migration_dir)
    with psycopg.connect(dsn, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            postgres_migrations.apply_postgres_migrations(cursor)
        connection.commit()

    with psycopg.connect(dsn) as connection:
        connection.execute(
            "ALTER TABLE openair_tasks ADD COLUMN IF NOT EXISTS revision BIGINT NOT NULL DEFAULT 0"
        )
        connection.commit()
    PgArtifactRepository(dsn).bootstrap()
    monkeypatch.setattr(settings, "persistence_backend", "postgres")
    monkeypatch.setattr(artifacts_route, "ArtifactRepository", lambda: PgArtifactRepository(dsn))
    monkeypatch.setattr(artifacts_route, "TaskRepository", lambda: PgTaskRepository(dsn))

    app = FastAPI()
    app.include_router(artifacts_route.router)
    app.dependency_overrides[resolve_request_identity] = lambda: {
        "ownerId": "owner-review",
        "workspaceId": "workspace-review",
        "createdBy": "authenticated-user-a",
    }
    with TestClient(app) as client:
        yield client, dsn, run_id


def test_review_rejects_client_identity_and_durably_persists_server_identity(review_api) -> None:
    client, dsn, run_id = review_api
    task_id = f"task-review-{run_id[:8]}-{uuid4().hex[:8]}"
    artifact_id = f"artifact-review-{run_id[:8]}-{uuid4().hex[:8]}"
    artifact = {
        "id": artifact_id,
        "taskId": task_id,
        "sourceTaskId": task_id,
        "ownerId": "owner-review",
        "workspaceId": "workspace-review",
        "createdBy": "system",
        "kind": "research_note",
        "title": "Review durability",
        "format": "markdown",
        "createdAt": "2026-09-26T00:00:00+00:00",
        "storageType": "inline",
        "inlineContent": "# review me",
        "artifactState": "needs_review",
    }
    task = {
        "taskId": task_id,
        "ownerId": "owner-review",
        "workspaceId": "workspace-review",
        "status": "completed",
        "currentStage": "insight_generation",
        "title": "Review task",
        "revision": 0,
        "artifacts": [artifact],
    }
    with psycopg.connect(dsn) as connection:
        connection.execute(
            """INSERT INTO openair_tasks
               (task_id, owner_id, workspace_id, status, current_stage, title, payload,
                revision, created_at, updated_at)
               VALUES (%s, 'owner-review', 'workspace-review', 'completed',
                       'insight_generation', 'Review task', %s::jsonb, 0, NOW(), NOW())""",
            (task_id, json.dumps(task)),
        )
        connection.commit()
    PgArtifactRepository(dsn).upsert_from_dict(task_id, artifact, "Review task")

    rejected = client.post(
        f"/artifacts/{artifact_id}/review",
        json={
            "decision": "approved",
            "note": "forged",
            "reviewer": "attacker",
            "source": "web",
        },
    )
    assert rejected.status_code == 422
    unchanged = PgArtifactRepository(dsn).get_by_id(artifact_id)
    assert unchanged is not None
    assert unchanged["artifactState"] == "needs_review"
    assert unchanged.get("reviewedBy") is None
    assert unchanged.get("reviewSource") is None

    review_note = f"canonical approval from Harness run {run_id}"
    accepted = client.post(
        f"/artifacts/{artifact_id}/review",
        json={"decision": "approved", "note": review_note},
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["artifactState"] == "usable"

    # This connection is created after the request and bypasses both the TestClient
    # response and repository object, proving an independent durable read.
    with psycopg.connect(dsn, row_factory=dict_row) as fresh_connection:
        reread = fresh_connection.execute(
            "SELECT metadata FROM openair_artifacts WHERE artifact_id = %s",
            (artifact_id,),
        ).fetchone()
    assert reread is not None
    metadata = reread["metadata"]
    assert metadata["artifactState"] == "usable"
    assert metadata["reviewedBy"] == "authenticated-user-a"
    assert metadata["reviewSource"] == "user"
    assert metadata["reviewNote"] == review_note
