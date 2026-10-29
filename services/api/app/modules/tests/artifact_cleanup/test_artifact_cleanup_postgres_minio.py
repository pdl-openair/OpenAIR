"""Real PostgreSQL + MinIO gate for Artifact cleanup crash recovery.

Requires explicitly disposable infrastructure:
- OPENAIR_ARTIFACT_CLEANUP_TEST_DATABASE_URL points to a test/qa PostgreSQL DB.
- OPENAIR_ARTIFACT_STORAGE=minio and OPENAIR_MINIO_* point to a disposable bucket.
"""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import pytest

import minio  # noqa: F401 -- required real object-storage client
import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row

from app.config import settings
from app.modules.artifact_cleanup import ArtifactCleanupService
from app.modules.artifact_repository import (
    ARTIFACTS_TABLE_SQL,
    ARTIFACT_CLEANUP_OUTBOX_SQL,
    ARTIFACT_STORAGE_REFERENCES_SQL,
    ArtifactRepository,
)
from app.modules import minio_client

ROUND5_MIGRATION = (Path(__file__).resolve().parents[4] / "migrations" / "20260807_07_artifact_storage_identity_final.sql").read_text(encoding="utf-8")
REFERENCE_MIGRATION = (Path(__file__).resolve().parents[4] / "migrations" / "20260807_06_artifact_storage_references.sql").read_text(encoding="utf-8")
FENCE_MIGRATION = (Path(__file__).resolve().parents[4] / "migrations" / "20260807_04_artifact_storage_identity_fence.sql").read_text(encoding="utf-8")

DSN_ENV = "OPENAIR_ARTIFACT_CLEANUP_TEST_DATABASE_URL"


def _dsn() -> str:
    dsn = os.environ.get(DSN_ENV, "").strip()
    if not dsn:
        pytest.fail(f"{DSN_ENV} is required for real PostgreSQL evidence")
    database = urlparse(dsn).path.lstrip("/").lower()
    if "test" not in database and "qa" not in database:
        pytest.fail(f"{DSN_ENV} must identify an isolated test or qa database")
    if settings.artifact_storage != "minio":
        pytest.fail("OPENAIR_ARTIFACT_STORAGE=minio is required for real object-storage evidence")
    if not settings.minio_access_key or not settings.minio_secret_key:
        pytest.fail("OPENAIR_MINIO_ACCESS_KEY/SECRET_KEY are required")
    return dsn


class CleanupArtifactRepository(ArtifactRepository):
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    @contextmanager
    def _connect(self):
        with psycopg.connect(self._dsn, row_factory=dict_row) as connection:
            yield connection
            connection.commit()


@pytest.fixture()
def infrastructure():
    base_dsn = _dsn()
    schema = f"artifact_cleanup_{uuid4().hex}"
    minio_client._client = None
    minio_client.ensure_minio_bucket()
    with psycopg.connect(base_dsn, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    dsn = make_conninfo(base_dsn, options=f"-csearch_path={schema}")
    try:
        with psycopg.connect(dsn, autocommit=True, row_factory=dict_row) as connection:
            connection.execute(ARTIFACTS_TABLE_SQL)
            connection.execute(ARTIFACT_CLEANUP_OUTBOX_SQL)
            connection.execute(FENCE_MIGRATION)
            connection.execute(ARTIFACT_STORAGE_REFERENCES_SQL)
            connection.execute(REFERENCE_MIGRATION)
            connection.execute(ROUND5_MIGRATION)
            connection.execute(
                "INSERT INTO openair_artifact_storage_runtime(runtime_key, storage_scope) VALUES ('minio_current', %s) "
                "ON CONFLICT (runtime_key) DO UPDATE SET storage_scope = EXCLUDED.storage_scope",
                (minio_client.current_minio_storage_scope(),),
            )
            assert connection.execute("SELECT current_schema() AS name").fetchone()["name"] == schema
        yield dsn
    finally:
        with psycopg.connect(base_dsn, autocommit=True) as connection:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def _upload(object_key: str, content: bytes = b"cleanup-evidence") -> None:
    minio_client.upload_bytes(
        "task-cleanup",
        object_key.rsplit("/", 1)[-1],
        content,
        object_key=object_key,
    )
    assert minio_client.stat_object(object_key) is not None


def test_real_postgres_minio_expired_claim_recovers_and_deletes_orphan(infrastructure) -> None:
    dsn = infrastructure
    repository = CleanupArtifactRepository(dsn)
    storage_scope = minio_client.current_minio_storage_scope()
    object_key = f"task-cleanup/uploads/{uuid4().hex}/orphan.bin"
    _upload(object_key)
    try:
        repository.enqueue_storage_cleanup(
            task_id="task-cleanup",
            artifact_id="artifact-orphan",
            storage_type="external",
            storage_scope=storage_scope,
            storage_ref=object_key,
            reason_code="catalog_commit_failed",
        )
        claimed = repository.claim_storage_cleanup(
            lease_owner="crashed-process",
            lease_seconds=60,
            batch_size=1,
        )
        assert len(claimed) == 1
        cleanup_id = claimed[0]["cleanup_id"]
        assert ArtifactCleanupService(repository).reconcile_pending(batch_size=1).claimed == 0

        with psycopg.connect(dsn, autocommit=True, row_factory=dict_row) as connection:
            expired = connection.execute(
                "UPDATE openair_artifact_cleanup_outbox "
                "SET lease_expires_at = '-infinity'::timestamptz "
                "WHERE cleanup_id = %s AND state = 'claimed' AND lease_owner = 'crashed-process' "
                "RETURNING state, lease_owner, lease_expires_at::text AS lease_expires_at",
                (cleanup_id,),
            ).fetchone()
        assert expired is not None
        assert expired["state"] == "claimed"
        assert expired["lease_owner"] == "crashed-process"
        assert expired["lease_expires_at"] == "-infinity"

        result = ArtifactCleanupService(repository).reconcile_pending(batch_size=1)
        assert result.claimed == 1
        assert result.completed == 1
        assert minio_client.stat_object(object_key) is None
        with psycopg.connect(dsn, row_factory=dict_row) as connection:
            row = connection.execute(
                "SELECT state, attempt_count, terminal_at "
                "FROM openair_artifact_cleanup_outbox WHERE storage_scope = %s AND storage_ref = %s",
                (storage_scope, object_key),
            ).fetchone()
        assert row["state"] == "completed"
        assert row["attempt_count"] == 2
        assert row["terminal_at"] is not None
    finally:
        minio_client.remove_object(object_key)


def test_real_postgres_minio_historical_reference_preserves_shared_object_until_last_owner(
    infrastructure,
) -> None:
    dsn = infrastructure
    repository = CleanupArtifactRepository(dsn)
    storage_scope = minio_client.current_minio_storage_scope()
    body = b"shared historical evidence"
    digest = hashlib.sha256(body).hexdigest()
    object_key = f"task-cleanup/uploads/{uuid4().hex}/shared-history.bin"
    _upload(object_key, body)
    placeholder = b"current inline catalog body"
    placeholder_digest = hashlib.sha256(placeholder).hexdigest()
    version_id = f"version-{uuid4().hex}"
    historical_metadata = {
        "versions": [
            {
                "versionId": version_id,
                "versionNumber": 1,
                "createdAt": "2026-08-07T00:00:00+00:00",
                "createdBy": "system",
                "source": "initial",
                "storageType": "external",
                "storageScope": storage_scope,
                "externalKey": object_key,
                "contentSha256": digest,
                "contentSize": len(body),
            }
        ]
    }
    try:
        with psycopg.connect(dsn, row_factory=dict_row) as connection:
            connection.execute(
                """INSERT INTO openair_artifacts (
                       artifact_id, owner_id, workspace_id, created_by, kind, title,
                       storage_type, storage_scope, storage_ref, content_sha256, content_size,
                       source_task_id, metadata, created_at, updated_at
                   ) VALUES (
                       'artifact-current-owner', 'owner', 'workspace', 'system',
                       'execution_output', 'Current owner', 'external', %s, %s, %s, %s,
                       'task-cleanup', '{}'::jsonb, NOW(), NOW()
                   )""",
                (storage_scope, object_key, digest, len(body)),
            )
            connection.execute(
                """INSERT INTO openair_artifacts (
                       artifact_id, owner_id, workspace_id, created_by, kind, title,
                       storage_type, storage_ref, content_sha256, content_size,
                       source_task_id, metadata, created_at, updated_at
                   ) VALUES (
                       'artifact-historical-owner', 'owner', 'workspace', 'system',
                       'execution_output', 'Historical owner', 'inline', %s, %s, %s,
                       'task-cleanup', %s::jsonb, NOW(), NOW()
                   )""",
                (
                    placeholder.decode(),
                    placeholder_digest,
                    len(placeholder),
                    json.dumps(historical_metadata),
                ),
            )
            connection.commit()

        with psycopg.connect(dsn, row_factory=dict_row) as connection:
            rows = connection.execute(
                """SELECT artifact_id, reference_kind, version_id
                     FROM openair_artifact_storage_references
                    WHERE storage_type = 'external' AND storage_scope = %s
                      AND storage_ref = %s
                    ORDER BY artifact_id, reference_kind, version_id""",
                (storage_scope, object_key),
            ).fetchall()
        assert [(r["artifact_id"], r["reference_kind"], r["version_id"]) for r in rows] == [
            ("artifact-current-owner", "current", ""),
            ("artifact-historical-owner", "historical", version_id),
        ]

        with psycopg.connect(dsn) as connection:
            connection.execute(
                "DELETE FROM openair_artifacts WHERE artifact_id = 'artifact-current-owner'"
            )
            connection.commit()

        preserved = ArtifactCleanupService(repository).reconcile_pending(batch_size=8)
        assert preserved.completed == 1
        with psycopg.connect(dsn, row_factory=dict_row) as connection:
            cleanup_row = connection.execute(
                "SELECT state FROM openair_artifact_cleanup_outbox "
                "WHERE storage_type = 'external' AND storage_scope = %s AND storage_ref = %s",
                (storage_scope, object_key),
            ).fetchone()
        assert cleanup_row["state"] == "preserved"
        assert minio_client.stat_object(object_key) is not None

        with psycopg.connect(dsn) as connection:
            connection.execute(
                "DELETE FROM openair_artifacts WHERE artifact_id = 'artifact-historical-owner'"
            )
            connection.commit()

        deleted = ArtifactCleanupService(repository).reconcile_pending(batch_size=8)
        assert deleted.completed == 1
        assert minio_client.stat_object(object_key) is None
    finally:
        minio_client.remove_object(object_key)


def test_real_postgres_historical_writer_rollback_leaves_no_public_version_or_reference(
    infrastructure,
) -> None:
    dsn = infrastructure
    body = b"current inline body"
    digest = hashlib.sha256(body).hexdigest()
    with psycopg.connect(dsn) as connection:
        connection.execute(
            """INSERT INTO openair_artifacts (
                   artifact_id, owner_id, workspace_id, created_by, kind, title,
                   storage_type, storage_ref, content_sha256, content_size,
                   source_task_id, metadata, created_at, updated_at
               ) VALUES (
                   'artifact-writer-rollback', 'owner', 'workspace', 'system',
                   'execution_output', 'Writer rollback', 'inline', %s, %s, %s,
                   'task-cleanup', '{}'::jsonb, NOW(), NOW()
               )""",
            (body.decode(), digest, len(body)),
        )
        connection.commit()

    duplicate_version_id = f"duplicate-{uuid4().hex}"
    metadata = {
        "versions": [
            {
                "versionId": duplicate_version_id,
                "storageType": "external",
                "storageScope": minio_client.current_minio_storage_scope(),
                "externalKey": f"history/{uuid4().hex}/one.bin",
                "contentSha256": "a" * 64,
                "contentSize": 1,
            },
            {
                "versionId": duplicate_version_id,
                "storageType": "external",
                "storageScope": minio_client.current_minio_storage_scope(),
                "externalKey": f"history/{uuid4().hex}/two.bin",
                "contentSha256": "b" * 64,
                "contentSize": 1,
            },
        ]
    }
    with pytest.raises(psycopg.Error):
        with psycopg.connect(dsn) as connection:
            connection.execute(
                "UPDATE openair_artifacts SET metadata = %s::jsonb "
                "WHERE artifact_id = 'artifact-writer-rollback'",
                (json.dumps(metadata),),
            )
            connection.commit()

    with psycopg.connect(dsn, row_factory=dict_row) as connection:
        artifact = connection.execute(
            "SELECT metadata FROM openair_artifacts "
            "WHERE artifact_id = 'artifact-writer-rollback'"
        ).fetchone()
        references = connection.execute(
            "SELECT reference_kind, version_id FROM openair_artifact_storage_references "
            "WHERE artifact_id = 'artifact-writer-rollback' ORDER BY reference_kind, version_id"
        ).fetchall()
    assert artifact["metadata"] == {}
    assert [(row["reference_kind"], row["version_id"]) for row in references] == [
        ("current", "")
    ]


def test_real_postgres_reference_backfill_failure_rolls_back_whole_migration(
    infrastructure,
) -> None:
    dsn = infrastructure
    migration_path = (
        Path(__file__).resolve().parents[4]
        / "migrations"
        / "20260807_06_artifact_storage_references.sql"
    )
    migration = migration_path.read_text(encoding="utf-8")

    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute(
            "DROP TRIGGER IF EXISTS trg_openair_retire_artifact_storage_references "
            "ON openair_artifacts"
        )
        connection.execute(
            "DROP TRIGGER IF EXISTS trg_openair_sync_artifact_storage_references "
            "ON openair_artifacts"
        )
        connection.execute(
            "DROP TRIGGER IF EXISTS trg_openair_fence_artifact_storage_reference "
            "ON openair_artifact_storage_references"
        )
        connection.execute("DROP FUNCTION IF EXISTS openair_retire_artifact_storage_references()")
        connection.execute("DROP FUNCTION IF EXISTS openair_sync_artifact_storage_references()")
        connection.execute("DROP FUNCTION IF EXISTS openair_fence_artifact_storage_reference()")
        connection.execute("DROP TABLE openair_artifact_storage_references")
        malformed = {
            "versions": [
                {
                    "versionId": "version-overflow",
                    "storageType": "external",
                    "storageScope": minio_client.current_minio_storage_scope(),
                    "externalKey": "history/overflow.bin",
                    "contentSha256": "c" * 64,
                    "contentSize": int("9" * 80),
                }
            ]
        }
        connection.execute(
            """INSERT INTO openair_artifacts (
                   artifact_id, owner_id, workspace_id, created_by, kind, title,
                   source_task_id, metadata, created_at, updated_at
               ) VALUES (
                   'artifact-backfill-overflow', 'owner', 'workspace', 'system',
                   'execution_output', 'Backfill overflow', 'task-cleanup',
                   %s::jsonb, NOW(), NOW()
               )""",
            (json.dumps(malformed),),
        )

    with pytest.raises(psycopg.Error):
        with psycopg.connect(dsn) as connection:
            connection.execute(migration)
            connection.commit()

    with psycopg.connect(dsn, row_factory=dict_row) as connection:
        table = connection.execute(
            "SELECT to_regclass('openair_artifact_storage_references') AS table_name"
        ).fetchone()
        artifact = connection.execute(
            "SELECT artifact_id FROM openair_artifacts "
            "WHERE artifact_id = 'artifact-backfill-overflow'"
        ).fetchone()
    assert table["table_name"] is None
    assert artifact["artifact_id"] == "artifact-backfill-overflow"
