"""Line C round-5 real PostgreSQL / MinIO gates.

The database URL must name an explicitly disposable test/qa database:
OPENAIR_LINE_C_ROUND5_POSTGRES_DSN.
MinIO cases additionally require OPENAIR_ARTIFACT_STORAGE=minio and credentials.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import pytest

psycopg = pytest.importorskip("psycopg")
pytest.importorskip("minio")
from psycopg.rows import dict_row

from app.config import settings
from app.modules import minio_client, postgres_migrations
from app.modules.artifact_cleanup import ArtifactCleanupService
from app.modules.artifact_repository import ArtifactRepository
from app.modules.artifact_write import artifact_write_service
from app.modules.durable_execution import DurableExecutionRepository
from app.routes import artifacts as artifacts_route

DSN_ENV = "OPENAIR_LINE_C_ROUND5_POSTGRES_DSN"
MIGRATION_NAMES = (
    "20260719_00_artifact_catalog_base.sql",
    "20260807_01_artifact_cleanup_outbox.sql",
    "20260807_03_artifact_cleanup_consumer.sql",
    "20260807_04_artifact_storage_identity_fence.sql",
    "20260807_05_user_delete_receipts.sql",
    "20260807_06_artifact_storage_references.sql",
    "20260807_07_artifact_storage_identity_final.sql",
)


def _dsn() -> str:
    dsn = os.environ.get(DSN_ENV, "").strip()
    if not dsn:
        pytest.skip(f"{DSN_ENV} is not configured")
    database = urlparse(dsn).path.lstrip("/").lower()
    if "test" not in database and "qa" not in database:
        pytest.fail(f"{DSN_ENV} must identify an isolated test or qa database")
    return dsn


def _reset_storage_schema(dsn: str) -> None:
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("DROP TABLE IF EXISTS openair_artifact_storage_runtime CASCADE")
        conn.execute("DROP TABLE IF EXISTS openair_artifact_storage_references CASCADE")
        conn.execute("DROP TABLE IF EXISTS openair_artifact_cleanup_outbox CASCADE")
        conn.execute("DROP TABLE IF EXISTS openair_user_delete_receipts CASCADE")
        conn.execute("DROP TABLE IF EXISTS openair_artifacts CASCADE")
        conn.execute("DROP TABLE IF EXISTS openair_schema_migrations CASCADE")
        for function in (
            "openair_sync_artifact_storage_references()",
            "openair_enforce_historical_version_immutability()",
            "openair_enforce_historical_storage_reference_immutability()",
            "openair_assign_artifact_storage_scope()",
            "openair_fence_artifact_storage_reference()",
            "openair_fence_artifact_storage_registration()",
            "openair_retire_artifact_storage_references()",
            "openair_artifact_storage_lock_key(text,text,text)",
        ):
            conn.execute(f"DROP FUNCTION IF EXISTS {function} CASCADE")


@pytest.fixture()
def storage_migrations(tmp_path, monkeypatch):
    dsn = _dsn()
    _reset_storage_schema(dsn)
    source = Path(__file__).resolve().parents[2] / "migrations"
    migration_dir = tmp_path / "migrations"
    migration_dir.mkdir()
    for name in MIGRATION_NAMES:
        shutil.copy2(source / name, migration_dir / name)
    monkeypatch.setattr(postgres_migrations, "MIGRATIONS_DIR", migration_dir)
    yield dsn, migration_dir
    _reset_storage_schema(dsn)


class PgArtifactRepository(ArtifactRepository):
    def __init__(self, dsn: str): self._dsn = dsn
    @contextmanager
    def _connect(self):
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            yield conn
            conn.commit()


class PgDurableRepository(DurableExecutionRepository):
    def __init__(self, dsn: str): self._dsn = dsn
    @contextmanager
    def _connect(self):
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            yield conn
            conn.commit()


def _apply(dsn: str) -> None:
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            postgres_migrations.apply_postgres_migrations(cur)
        conn.commit()


def test_real_postgres_first_migration_then_second_bootstrap_is_stable(storage_migrations):
    dsn, _ = storage_migrations
    _apply(dsn)
    repo = PgArtifactRepository(dsn)
    repo.bootstrap()
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        before = conn.execute("SELECT count(*) AS n FROM openair_artifact_storage_references").fetchone()["n"]
        versions = {row["version"] for row in conn.execute("SELECT version FROM openair_schema_migrations").fetchall()}
    assert "20260807_07_artifact_storage_identity_final.sql" in versions

    # Runner and repository may both be invoked on the second API start; ledger skip +
    # read-only bootstrap means no backfill body is executed again.
    _apply(dsn)
    repo.bootstrap()
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        after = conn.execute("SELECT count(*) AS n FROM openair_artifact_storage_references").fetchone()["n"]
    assert after == before


def test_real_postgres_auto_apply_false_cannot_create_artifact_schema(storage_migrations):
    dsn, _ = storage_migrations
    PgDurableRepository(dsn).bootstrap(False)
    with pytest.raises(RuntimeError, match="artifact_schema_migration_required"):
        PgArtifactRepository(dsn).bootstrap()
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        assert conn.execute("SELECT to_regclass('public.openair_artifact_storage_references') AS r").fetchone()["r"] is None


def test_real_postgres_round5_migration_failure_rolls_back_schema_and_ledger(storage_migrations, monkeypatch, tmp_path):
    dsn, migration_dir = storage_migrations
    # First install through v4/06 only.
    round5 = migration_dir / "20260807_07_artifact_storage_identity_final.sql"
    held = round5.read_text(encoding="utf-8")
    round5.unlink()
    _apply(dsn)

    failing = tmp_path / "failing"
    failing.mkdir()
    (failing / round5.name).write_text(held + "\nSELECT 1 / 0;\n", encoding="utf-8")
    monkeypatch.setattr(postgres_migrations, "MIGRATIONS_DIR", failing)
    with pytest.raises(Exception):
        _apply(dsn)

    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        assert conn.execute("SELECT to_regclass('public.openair_artifact_storage_runtime') AS r").fetchone()["r"] is None
        assert conn.execute("SELECT count(*) AS n FROM openair_schema_migrations WHERE version = %s", (round5.name,)).fetchone()["n"] == 0
        # Transaction rollback restored the v4 fence trigger dropped near the start of 07.
        assert conn.execute("SELECT count(*) AS n FROM pg_trigger WHERE tgname = 'trg_openair_fence_artifact_storage_reference' AND NOT tgisinternal").fetchone()["n"] == 1


def _artifact_row(artifact_id: str, *, metadata: dict) -> tuple:
    body = b"current-inline"
    digest = hashlib.sha256(body).hexdigest()
    return (
        artifact_id, "owner", "workspace", "system", "execution_output", artifact_id,
        "inline", digest, digest, len(body), "task-round5", json.dumps(metadata),
    )


def _insert_artifact(conn, artifact_id: str, metadata: dict) -> None:
    conn.execute(
        """INSERT INTO openair_artifacts (
               artifact_id, owner_id, workspace_id, created_by, kind, title,
               storage_type, storage_ref, content_sha256, content_size,
               source_task_id, metadata, created_at, updated_at
           ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,NOW(),NOW())""",
        _artifact_row(artifact_id, metadata=metadata),
    )


def _historical(version_id: str, *, digest: str, size: int, scope: str, key: str) -> dict:
    return {
        "versionId": version_id,
        "versionNumber": 1,
        "createdAt": "2026-08-07T00:00:00+00:00",
        "createdBy": "system",
        "source": "initial",
        "storageType": "external",
        "storageScope": scope,
        "externalKey": key,
        "contentSha256": digest,
        "contentSize": size,
    }


def test_real_postgres_historical_version_is_immutable_and_rolls_back(storage_migrations):
    dsn, _ = storage_migrations
    _apply(dsn)
    scope = "minio+http://minio:9000/bucket-a"
    version = _historical("v1", digest="a" * 64, size=1, scope=scope, key="same/key.bin")
    with psycopg.connect(dsn) as conn:
        _insert_artifact(conn, "artifact-immutable", {"versions": [version]})
        conn.commit()

    changed = dict(version, contentSha256="b" * 64)
    with psycopg.connect(dsn) as conn:
        with pytest.raises(Exception, match="artifact_historical_version_immutable"):
            conn.execute(
                "UPDATE openair_artifacts SET metadata = %s::jsonb WHERE artifact_id = 'artifact-immutable'",
                (json.dumps({"versions": [changed]}),),
            )
            conn.commit()
        conn.rollback()

    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        row = conn.execute("SELECT metadata FROM openair_artifacts WHERE artifact_id='artifact-immutable'").fetchone()
        ref = conn.execute("SELECT storage_scope, storage_ref, content_sha256, content_size FROM openair_artifact_storage_references WHERE artifact_id='artifact-immutable' AND reference_kind='historical' AND version_id='v1'").fetchone()
        cleanup = conn.execute("SELECT count(*) AS n FROM openair_artifact_cleanup_outbox WHERE artifact_id='artifact-immutable'").fetchone()["n"]
    assert row["metadata"]["versions"][0]["contentSha256"] == "a" * 64
    assert ref == {"storage_scope": scope, "storage_ref": "same/key.bin", "content_sha256": "a" * 64, "content_size": 1}
    assert cleanup == 0


def test_real_postgres_concurrent_same_version_writers_leave_one_canonical_value(storage_migrations):
    dsn, _ = storage_migrations
    _apply(dsn)
    with psycopg.connect(dsn) as conn:
        _insert_artifact(conn, "artifact-race", {"versions": []})
        conn.commit()

    scope = "minio+http://minio:9000/bucket-a"
    versions = [
        _historical("race-v1", digest="a" * 64, size=1, scope=scope, key="race-a.bin"),
        _historical("race-v1", digest="b" * 64, size=2, scope=scope, key="race-b.bin"),
    ]
    barrier = threading.Barrier(2)
    outcomes: list[str] = []

    def writer(version):
        try:
            with psycopg.connect(dsn) as conn:
                barrier.wait(timeout=5)
                conn.execute(
                    "UPDATE openair_artifacts SET metadata = %s::jsonb WHERE artifact_id='artifact-race'",
                    (json.dumps({"versions": [version]}),),
                )
                conn.commit()
            outcomes.append("committed")
        except Exception:
            outcomes.append("rejected")

    threads = [threading.Thread(target=writer, args=(v,), daemon=True) for v in versions]
    for t in threads: t.start()
    for t in threads: t.join(timeout=10)
    assert sorted(outcomes) == ["committed", "rejected"]
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        refs = conn.execute("SELECT storage_ref, content_sha256, content_size FROM openair_artifact_storage_references WHERE artifact_id='artifact-race' AND version_id='race-v1'").fetchall()
    assert len(refs) == 1
    assert refs[0]["content_sha256"] in {"a" * 64, "b" * 64}


def test_real_postgres_canonical_version_truth_appends_and_reloads(storage_migrations, monkeypatch):
    dsn, _ = storage_migrations
    _apply(dsn)
    repo = PgArtifactRepository(dsn)
    task_id = f"task-version-{uuid4().hex[:10]}"
    artifact_id = f"artifact-version-{uuid4().hex[:10]}"
    initial_content = "# V1\ncanonical"
    initial_bytes = initial_content.encode("utf-8")
    initial_hash = hashlib.sha256(initial_bytes).hexdigest()
    artifact = {
        "id": artifact_id,
        "taskId": task_id,
        "sourceTaskId": task_id,
        "ownerId": "owner",
        "workspaceId": "workspace",
        "createdBy": "system",
        "kind": "execution_output",
        "title": "Canonical markdown",
        "format": "markdown",
        "createdAt": "2026-08-12T00:00:00+00:00",
        "storageType": "local",
        "storageScope": task_id,
        "path": f"execution/{artifact_id}/initial.md",
        "contentSha256": initial_hash,
        "contentSize": len(initial_bytes),
        "artifactState": "usable",
        "reviewedAt": "2026-08-12T00:01:00+00:00",
        "reviewedBy": "reviewer",
        "reviewSource": "user",
        "reviewNote": "approved v1",
    }
    with psycopg.connect(dsn) as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS openair_tasks (
                   task_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, workspace_id TEXT NOT NULL,
                   status TEXT NOT NULL, current_stage TEXT NOT NULL, title TEXT NOT NULL,
                   domain TEXT NULL, created_at TIMESTAMPTZ NULL, updated_at TIMESTAMPTZ NULL,
                   payload JSONB NOT NULL, revision BIGINT NOT NULL DEFAULT 0
               )"""
        )
        conn.execute("ALTER TABLE openair_tasks ADD COLUMN IF NOT EXISTS revision BIGINT NOT NULL DEFAULT 0")
        conn.execute(
            """INSERT INTO openair_tasks (
                   task_id, owner_id, workspace_id, status, current_stage, title, payload, revision,
                   created_at, updated_at
               ) VALUES (%s,'owner','workspace','completed','insight_generation','Version task',%s::jsonb,0,NOW(),NOW())""",
            (task_id, json.dumps({"taskId": task_id, "status": "completed", "revision": 0, "artifacts": [artifact]})),
        )
        conn.commit()

    repo.upsert_from_dict(task_id, artifact, "Version task")
    initial = repo.get_by_id(artifact_id)
    assert initial is not None
    assert initial["currentVersionId"] == f"v_initial_{artifact_id}"
    assert len(initial["versions"]) == 1
    assert initial["versions"][0]["source"] == "initial"

    v2_id = f"v_{uuid4().hex[:12]}"
    v2_ref = f"artifact-versions/{artifact_id}/{v2_id}.md"
    v2_bytes = b"# V2\nedited"
    v2_hash = hashlib.sha256(v2_bytes).hexdigest()
    v2 = repo.append_content_version(
        artifact_id=artifact_id,
        owner_id="owner",
        workspace_id="workspace",
        created_by="human",
        version_id=v2_id,
        source="user_edit",
        storage_type="local",
        storage_ref=v2_ref,
        storage_scope=task_id,
        content_sha256=v2_hash,
        content_size=len(v2_bytes),
        feedback="edit",
        expected_revision=0,
    )
    assert [v["versionNumber"] for v in v2["versions"]] == [1, 2]
    assert v2["currentVersionId"] == v2_id
    assert v2["artifactState"] == "needs_review"

    v3_id = f"v_{uuid4().hex[:12]}"
    v3_ref = f"artifact-versions/{artifact_id}/{v3_id}.md"
    v3 = repo.append_content_version(
        artifact_id=artifact_id,
        owner_id="owner",
        workspace_id="workspace",
        created_by="human",
        version_id=v3_id,
        source="restore",
        storage_type="local",
        storage_ref=v3_ref,
        storage_scope=task_id,
        content_sha256=initial_hash,
        content_size=len(initial_bytes),
        feedback="restore v1",
        restored_from_version_id=f"v_initial_{artifact_id}",
        expected_revision=1,
    )
    assert [v["source"] for v in v3["versions"]] == ["initial", "user_edit", "restore"]
    assert v3["versions"][0]["contentSha256"] == initial_hash
    assert v3["versions"][1]["contentSha256"] == v2_hash
    assert v3["versions"][2]["restoredFromVersionId"] == f"v_initial_{artifact_id}"
    assert v3["currentVersionId"] == v3_id

    reloaded = repo.get_by_id(artifact_id)
    assert reloaded is not None
    assert reloaded["currentVersionId"] == v3_id
    assert reloaded["path"].endswith(f"/{v3_id}.md")
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        task_row = conn.execute("SELECT revision, payload FROM openair_tasks WHERE task_id=%s", (task_id,)).fetchone()
        row = conn.execute("SELECT metadata FROM openair_artifacts WHERE artifact_id=%s", (artifact_id,)).fetchone()
        refs = conn.execute(
            """SELECT reference_kind, version_id, storage_ref, content_sha256
                 FROM openair_artifact_storage_references WHERE artifact_id=%s
                 ORDER BY reference_kind, version_id""",
            (artifact_id,),
        ).fetchall()
    assert task_row["revision"] == 2
    assert task_row["payload"]["revision"] == 2
    assert row["metadata"]["reviewedAt"] == "2026-08-12T00:01:00+00:00"
    assert row["metadata"]["reviewedBy"] == "reviewer"
    assert row["metadata"]["reviewSource"] == "user"
    assert row["metadata"]["reviewNote"] == "approved v1"
    historical = {r["version_id"]: r for r in refs if r["reference_kind"] == "historical"}
    assert historical[f"v_initial_{artifact_id}"]["content_sha256"] == initial_hash
    assert historical[v2_id]["content_sha256"] == v2_hash
    assert historical[v3_id]["content_sha256"] == initial_hash

    # Reload through the production route/content authority: current resolves V3,
    # while V1/V2 remain independently readable and immutable.
    monkeypatch.setattr(settings, "persistence_backend", "postgres")
    monkeypatch.setattr(artifacts_route, "ArtifactRepository", lambda: repo)
    blobs = {
        f"execution/{artifact_id}/initial.md": initial_bytes,
        v2_ref: v2_bytes,
        v3_ref: initial_bytes,
    }
    monkeypatch.setattr(
        artifact_write_service,
        "read_durable_bytes",
        lambda _task_id, _storage_type, storage_ref, _storage_scope=None: blobs.get(storage_ref),
    )
    identity = {"ownerId": "owner", "workspaceId": "workspace", "createdBy": "human"}
    current_content = artifacts_route.get_artifact_content(artifact_id, identity=identity)
    v1_content = artifacts_route.get_artifact_version_content(
        artifact_id, f"v_initial_{artifact_id}", identity=identity
    )
    v2_content = artifacts_route.get_artifact_version_content(artifact_id, v2_id, identity=identity)
    assert current_content.content == initial_content
    assert v1_content.content == initial_content
    assert v2_content.content == v2_bytes.decode("utf-8")

    # Replacing the current local identity creates cleanup intent, but historical
    # ownership must win the shared storage fence so V1/V2 bytes are never deleted.
    claims = repo.claim_storage_cleanup(batch_size=10, lease_owner="version-test", lease_seconds=30)
    assert len(claims) == 2
    deleted = []
    outcomes = [
        repo.execute_storage_cleanup_claim(
            cleanup_id=row["cleanup_id"],
            lease_owner="version-test",
            delete_callback=lambda *args: deleted.append(args),
        )
        for row in claims
    ]
    assert outcomes == ["preserved", "preserved"]
    assert deleted == []

    with pytest.raises(RuntimeError, match="task_revision_conflict"):
        repo.append_content_version(
            artifact_id=artifact_id,
            owner_id="owner",
            workspace_id="workspace",
            created_by="human",
            version_id=f"v_{uuid4().hex[:12]}",
            source="user_edit",
            storage_type="local",
            storage_ref=f"artifact-versions/{artifact_id}/stale.md",
            storage_scope=task_id,
            content_sha256="a" * 64,
            content_size=1,
            expected_revision=1,
        )


def test_real_postgres_user_edit_becomes_research_task_current_authority(storage_migrations):
    dsn, _ = storage_migrations
    _apply(dsn)
    repo = PgArtifactRepository(dsn)
    task_id = f"task-revision-authority-{uuid4().hex[:10]}"
    artifact_id = f"artifact-revision-authority-{uuid4().hex[:10]}"
    initial_bytes = b"# V1\nproducer truth"
    initial_hash = hashlib.sha256(initial_bytes).hexdigest()
    v2_bytes = b"# V2\nuser canonical truth"
    v2_hash = hashlib.sha256(v2_bytes).hexdigest()
    validator = {
        "status": "passed",
        "artifactContract": "direct_skill:literature_review:v1",
        "artifactSchemaDigest": "a" * 64,
        "contentSha256": initial_hash,
    }
    research_artifact = {
        "artifactId": artifact_id,
        "workUnitId": "work-producer",
        "substageInstanceId": "sub-producer",
        "sourceCapabilityCallId": "call-producer",
        "artifactType": "evidence_summary",
        "artifactState": "usable",
        "version": 1,
        "sourceRefs": [],
        "validatorResult": validator,
        "lineageKind": "capability_call_output",
    }
    catalog_artifact = {
        "id": artifact_id,
        "taskId": task_id,
        "sourceTaskId": task_id,
        "ownerId": "owner",
        "workspaceId": "workspace",
        "createdBy": "system",
        "kind": "execution_output",
        "title": "Editable research artifact",
        "format": "markdown",
        "mimeType": "text/markdown",
        "createdAt": "2026-08-12T00:00:00+00:00",
        "storageType": "local",
        "storageScope": task_id,
        "path": f"execution/{artifact_id}/initial.md",
        "contentSha256": initial_hash,
        "contentSize": len(initial_bytes),
        "artifactState": "usable",
        "artifactType": "evidence_summary",
        "validatorResult": validator,
        "reviewedAt": "2026-08-12T00:05:00+00:00",
        "reviewedBy": "review-agent",
        "reviewSource": "review_agent",
        "reviewNote": "v1 approved",
        "workUnitId": "work-producer",
        "sourceCapabilityCallId": "call-producer",
    }
    with psycopg.connect(dsn) as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS openair_tasks (
                   task_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, workspace_id TEXT NOT NULL,
                   status TEXT NOT NULL, current_stage TEXT NOT NULL, title TEXT NOT NULL,
                   domain TEXT NULL, created_at TIMESTAMPTZ NULL, updated_at TIMESTAMPTZ NULL,
                   payload JSONB NOT NULL, revision BIGINT NOT NULL DEFAULT 0
               )"""
        )
        conn.execute("ALTER TABLE openair_tasks ADD COLUMN IF NOT EXISTS revision BIGINT NOT NULL DEFAULT 0")
        payload = {
            "taskId": task_id,
            "status": "running",
            "revision": 0,
            "artifacts": [research_artifact],
        }
        conn.execute(
            """INSERT INTO openair_tasks (
                   task_id, owner_id, workspace_id, status, current_stage, title, payload, revision,
                   created_at, updated_at
               ) VALUES (%s,'owner','workspace','running','solution_design','Revision authority',%s::jsonb,0,NOW(),NOW())""",
            (task_id, json.dumps(payload)),
        )
        conn.commit()

    repo.upsert_from_dict(task_id, catalog_artifact, "Revision authority")
    v2_id = f"v_{uuid4().hex[:12]}"
    repo.append_content_version(
        artifact_id=artifact_id,
        owner_id="owner",
        workspace_id="workspace",
        created_by="human",
        version_id=v2_id,
        source="user_edit",
        storage_type="local",
        storage_ref=f"artifact-versions/{artifact_id}/{v2_id}.md",
        storage_scope=task_id,
        content_sha256=v2_hash,
        content_size=len(v2_bytes),
        feedback="correct the interpretation",
        expected_revision=0,
    )

    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        task_after_edit = conn.execute(
            "SELECT revision, payload FROM openair_tasks WHERE task_id=%s", (task_id,)
        ).fetchone()
        row_after_edit = conn.execute(
            "SELECT content_sha256, metadata FROM openair_artifacts WHERE artifact_id=%s", (artifact_id,)
        ).fetchone()
    projected_edit = task_after_edit["payload"]["artifacts"][0]
    assert task_after_edit["revision"] == 1
    assert projected_edit["artifactId"] == artifact_id
    assert projected_edit["artifactState"] == "needs_review"
    assert projected_edit["version"] == 2
    assert projected_edit["validatorResult"]["status"] == "revision_pending_review"
    assert projected_edit["validatorResult"]["contentSha256"] == v2_hash
    assert row_after_edit["content_sha256"] == v2_hash
    assert row_after_edit["metadata"]["validatorResult"] == projected_edit["validatorResult"]
    assert row_after_edit["metadata"].get("reviewSource") is None
    assert row_after_edit["metadata"].get("reviewedAt") is None
    assert row_after_edit["metadata"]["humanGate"]["canUseDownstream"] is False

    repo.review_artifact(
        artifact_id=artifact_id,
        owner_id="owner",
        workspace_id="workspace",
        decision="approved",
        reviewer="human",
        note="approved edited version",
        source="user",
        expected_revision=1,
    )

    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        task_after_review = conn.execute(
            "SELECT revision, payload FROM openair_tasks WHERE task_id=%s", (task_id,)
        ).fetchone()
        row_after_review = conn.execute(
            "SELECT content_sha256, metadata FROM openair_artifacts WHERE artifact_id=%s", (artifact_id,)
        ).fetchone()
    projected_review = task_after_review["payload"]["artifacts"][0]
    assert task_after_review["revision"] == 2
    assert projected_review["artifactState"] == "usable"
    assert projected_review["version"] == 2
    assert projected_review["validatorResult"]["status"] == "revision_human_approved"
    assert projected_review["validatorResult"]["contentSha256"] == v2_hash
    assert projected_review["validatorResult"]["revisionSource"] == "user_edit"
    assert row_after_review["content_sha256"] == v2_hash
    assert row_after_review["metadata"]["validatorResult"] == projected_review["validatorResult"]
    assert row_after_review["metadata"]["reviewSource"] == "user"
    assert row_after_review["metadata"]["reviewedAt"]
    assert row_after_review["metadata"]["humanGate"]["canUseDownstream"] is True


def test_real_postgres_object_v1_edit_restore_stays_append_only(storage_migrations, monkeypatch):
    import sys
    from types import ModuleType, SimpleNamespace

    dsn, _ = storage_migrations
    _apply(dsn)
    repo = PgArtifactRepository(dsn)
    task_id = f"task-object-version-{uuid4().hex[:10]}"
    artifact_id = f"artifact-object-version-{uuid4().hex[:10]}"
    v1_bytes = b"# Object V1\nshared canonical body"
    v1_hash = hashlib.sha256(v1_bytes).hexdigest()
    artifact = {
        "id": artifact_id,
        "taskId": task_id,
        "sourceTaskId": task_id,
        "ownerId": "owner",
        "workspaceId": "workspace",
        "createdBy": "system",
        "kind": "imported_artifact",
        "origin": "imported",
        "title": "Reused markdown",
        "format": "markdown",
        "createdAt": "2026-08-12T00:00:00+00:00",
        "storageType": "object",
        "contentSha256": v1_hash,
        "contentSize": len(v1_bytes),
        "artifactState": "usable",
    }
    with psycopg.connect(dsn) as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS openair_tasks (
                   task_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, workspace_id TEXT NOT NULL,
                   status TEXT NOT NULL, current_stage TEXT NOT NULL, title TEXT NOT NULL,
                   domain TEXT NULL, created_at TIMESTAMPTZ NULL, updated_at TIMESTAMPTZ NULL,
                   payload JSONB NOT NULL, revision BIGINT NOT NULL DEFAULT 0
               )"""
        )
        conn.execute("ALTER TABLE openair_tasks ADD COLUMN IF NOT EXISTS revision BIGINT NOT NULL DEFAULT 0")
        conn.execute(
            """INSERT INTO openair_tasks (
                   task_id, owner_id, workspace_id, status, current_stage, title, payload, revision,
                   created_at, updated_at
               ) VALUES (%s,'owner','workspace','completed','insight_generation','Object version task',%s::jsonb,0,NOW(),NOW())""",
            (task_id, json.dumps({"taskId": task_id, "status": "completed", "revision": 0, "artifacts": [artifact]})),
        )
        conn.commit()

    repo.upsert_from_dict(task_id, artifact, "Object version task")
    initial = repo.get_by_id(artifact_id)
    assert initial is not None
    v1_id = f"v_initial_{artifact_id}"
    assert initial["currentVersionId"] == v1_id
    assert len(initial["versions"]) == 1
    assert initial["versions"][0]["storageType"] == "object"
    assert initial["versions"][0]["contentSha256"] == v1_hash

    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        object_refs = conn.execute(
            "SELECT * FROM openair_artifact_storage_references WHERE artifact_id=%s",
            (artifact_id,),
        ).fetchall()
    assert object_refs == []

    fake_object_store = ModuleType("app.modules.object_store")
    fake_object_store.get_content = lambda digest: v1_bytes if digest == v1_hash else (_ for _ in ()).throw(FileNotFoundError())
    monkeypatch.setitem(sys.modules, "app.modules.object_store", fake_object_store)
    import app.modules as app_modules
    monkeypatch.setattr(app_modules, "object_store", fake_object_store, raising=False)

    monkeypatch.setattr(settings, "persistence_backend", "postgres")
    monkeypatch.setattr(artifacts_route, "ArtifactRepository", lambda: repo)
    blobs: dict[str, bytes] = {}

    def store_version_bytes(*, task_id, artifact_id, version_id, data):
        ref = f"artifact-versions/{artifact_id}/{version_id}.md"
        blobs[ref] = bytes(data)
        return SimpleNamespace(storage_type="local", storage_ref=ref, storage_scope=task_id)

    monkeypatch.setattr(artifacts_route, "_store_postgres_version_bytes", store_version_bytes)
    monkeypatch.setattr(
        artifact_write_service,
        "read_durable_bytes",
        lambda _task_id, _storage_type, storage_ref, _storage_scope=None: blobs.get(storage_ref),
    )
    identity = {"ownerId": "owner", "workspaceId": "workspace", "createdBy": "human"}

    historical_v1 = artifacts_route.get_artifact_version_content(
        artifact_id, v1_id, identity=identity
    )
    assert historical_v1.content == v1_bytes.decode("utf-8")
    assert historical_v1.sourceMode == "object"

    v2_body = "# Object V2\nuser edit"
    edited = artifacts_route.edit_artifact_content(
        artifact_id,
        artifacts_route.EditArtifactContentIn(content=v2_body, changeReason="edit object"),
        identity=identity,
        expected_revision=0,
    )
    v2_id = edited.currentVersionId
    assert v2_id and v2_id != v1_id

    restored = artifacts_route.restore_artifact_version(
        artifact_id,
        v1_id,
        identity=identity,
        expected_revision=1,
    )
    v3_id = restored.currentVersionId
    assert v3_id and v3_id not in {v1_id, v2_id}

    reloaded = repo.get_by_id(artifact_id)
    assert reloaded is not None
    assert reloaded["currentVersionId"] == v3_id
    assert [v["source"] for v in reloaded["versions"]] == ["initial", "user_edit", "restore"]
    assert [v["versionId"] for v in reloaded["versions"]] == [v1_id, v2_id, v3_id]
    assert reloaded["versions"][0]["contentSha256"] == v1_hash
    assert reloaded["versions"][2]["contentSha256"] == v1_hash
    assert reloaded["versions"][2]["restoredFromVersionId"] == v1_id

    current = artifacts_route.get_artifact_content(artifact_id, identity=identity)
    assert bytes(current.body) == v1_bytes

    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        task_row = conn.execute("SELECT revision FROM openair_tasks WHERE task_id=%s", (task_id,)).fetchone()
        refs = conn.execute(
            """SELECT reference_kind, version_id, storage_type, storage_ref
                 FROM openair_artifact_storage_references
                WHERE artifact_id=%s ORDER BY reference_kind, version_id""",
            (artifact_id,),
        ).fetchall()
        cleanup = conn.execute(
            "SELECT storage_type, storage_ref FROM openair_artifact_cleanup_outbox WHERE artifact_id=%s",
            (artifact_id,),
        ).fetchall()
    assert task_row["revision"] == 2
    assert all(row["storage_type"] != "object" for row in refs)
    assert all(row["version_id"] != v1_id for row in refs if row["reference_kind"] == "historical")
    assert all(row["storage_type"] != "object" for row in cleanup)


def _minio_ready() -> None:
    if settings.artifact_storage != "minio": pytest.skip("OPENAIR_ARTIFACT_STORAGE=minio required")
    if not settings.minio_access_key or not settings.minio_secret_key: pytest.skip("MinIO credentials required")


def test_real_minio_two_buckets_same_key_are_read_from_persisted_scope(storage_migrations, monkeypatch):
    _minio_ready()
    dsn, _ = storage_migrations
    _apply(dsn)
    client = minio_client.get_minio_client()
    bucket_a = f"linec-a-{uuid4().hex[:10]}"
    bucket_b = f"linec-b-{uuid4().hex[:10]}"
    key = "same/object.bin"
    for bucket in (bucket_a, bucket_b): client.make_bucket(bucket)
    try:
        from io import BytesIO
        client.put_object(bucket_a, key, BytesIO(b"A"), 1)
        client.put_object(bucket_b, key, BytesIO(b"B"), 1)
        endpoint = minio_client._normalize_minio_endpoint(settings.minio_endpoint)
        scheme = "minio+https" if settings.minio_secure else "minio+http"
        scope_a = f"{scheme}://{endpoint}/{bucket_a}"
        monkeypatch.setattr(settings, "minio_bucket", bucket_b)
        assert artifact_write_service.read_durable_bytes("task", "external", key, storage_scope=scope_a) == b"A"
    finally:
        for bucket in (bucket_a, bucket_b):
            try: client.remove_object(bucket, key)
            except Exception: pass
            try: client.remove_bucket(bucket)
            except Exception: pass


def test_real_minio_cleanup_deletes_only_persisted_bucket(storage_migrations, monkeypatch):
    _minio_ready()
    dsn, _ = storage_migrations
    _apply(dsn)
    client = minio_client.get_minio_client()
    bucket_a = f"linec-a-{uuid4().hex[:10]}"
    bucket_b = f"linec-b-{uuid4().hex[:10]}"
    key = "same/cleanup.bin"
    for bucket in (bucket_a, bucket_b): client.make_bucket(bucket)
    try:
        from io import BytesIO
        client.put_object(bucket_a, key, BytesIO(b"A"), 1)
        client.put_object(bucket_b, key, BytesIO(b"B"), 1)
        endpoint = minio_client._normalize_minio_endpoint(settings.minio_endpoint)
        scheme = "minio+https" if settings.minio_secure else "minio+http"
        scope_a = f"{scheme}://{endpoint}/{bucket_a}"
        repo = PgArtifactRepository(dsn)
        repo.enqueue_storage_cleanup(task_id="task", artifact_id="orphan", storage_type="external", storage_scope=scope_a, storage_ref=key, reason_code="test")
        monkeypatch.setattr(settings, "minio_bucket", bucket_b)
        result = ArtifactCleanupService(repo).reconcile_pending(batch_size=1)
        assert result.completed == 1
        assert client.stat_object(bucket_b, key).size == 1
        with pytest.raises(Exception): client.stat_object(bucket_a, key)
    finally:
        for bucket in (bucket_a, bucket_b):
            try: client.remove_object(bucket, key)
            except Exception: pass
            try: client.remove_bucket(bucket)
            except Exception: pass
