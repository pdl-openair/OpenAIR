"""Tests for postgres single-artifact reuse (content-store-backed) and the
``object`` storage type that backs it."""

from __future__ import annotations

from app.config import settings
from app.modules import artifact_catalog
from app.modules import object_store


def test_reuse_artifact_postgres_materializes_content_and_uses_native_repos(monkeypatch):
    """On postgres, reuse routes through object_store + native repos (not the
    forbidden store.add_artifact / store.record_artifact_relation)."""
    monkeypatch.setattr(settings, "persistence_backend", "postgres")

    captured: dict[str, object] = {}

    monkeypatch.setattr(object_store, "put_content", lambda data, **kw: "a" * 64)

    import app.modules.artifact_content as artifact_content

    monkeypatch.setattr(
        artifact_content,
        "load_artifact_binary",
        lambda *a, **k: (b"source bytes", "inline"),
    )

    import app.modules.task_repository as task_repository

    class FakeTaskRepo:
        def get_task(self, task_id):
            return {
                "taskId": task_id,
                "ownerId": "owner1",
                "workspaceId": "ws1",
                "createdBy": "owner1",
                "title": "Target",
            }

    monkeypatch.setattr(task_repository, "TaskRepository", FakeTaskRepo)

    class FakeArtRepo:
        def upsert_from_dict(self, task_id, artifact, task_title=None):
            captured["task_id"] = task_id
            captured["artifact"] = dict(artifact)
            captured["task_title"] = task_title

    monkeypatch.setattr(artifact_catalog, "ArtifactRepository", FakeArtRepo)

    import app.modules.artifact_relation_repository as relation_repository

    class FakeRelRepo:
        def upsert_relation(self, **kw):
            captured["relation"] = kw

    monkeypatch.setattr(relation_repository, "ArtifactRelationRepository", FakeRelRepo)

    source_artifact = {
        "id": "src1",
        "kind": "report",
        "title": "Src",
        "summary": "s",
        "format": "markdown",
        "storageType": "inline",
        "inlineContent": "x",
        "artifactState": "usable",
    }
    source_task = {"taskId": "srctask", "title": "Src Task"}
    identity = {"ownerId": "owner1", "workspaceId": "ws1", "createdBy": "owner1"}

    result = artifact_catalog.reuse_artifact(
        source_artifact, source_task, "target1", identity=identity
    )

    # produced artifact is content-addressed
    assert result["storageType"] == "object"
    assert result["contentSha256"] == "a" * 64
    assert result["contentSize"] == len(b"source bytes")
    assert result["origin"] == "imported"
    assert result["sourceTaskId"] == "target1"
    assert result["metadata"]["reusedFrom"]["artifactId"] == "src1"

    # catalog insert via native repo
    assert captured["task_id"] == "target1"
    assert captured["artifact"]["storageType"] == "object"
    assert captured["artifact"]["contentSha256"] == "a" * 64

    # reuses relation recorded via native repo
    assert captured["relation"]["relation_type"] == "reuses"
    assert captured["relation"]["source_artifact_id"] == result["id"]
    assert captured["relation"]["target_artifact_id"] == "src1"


def test_storage_ref_returns_sha256_for_object_type():
    from app.modules.artifact_repository import _storage_ref

    assert _storage_ref({"storageType": "object", "contentSha256": "b" * 64}) == "b" * 64
    # other types unchanged
    assert _storage_ref({"storageType": "inline", "inlineContent": "txt"}) == "txt"
    assert _storage_ref({"storageType": "external", "externalKey": "k"}) == "k"
    assert _storage_ref({"storageType": "local", "path": "p"}) == "p"


def test_resolver_and_loader_read_object_type_from_content_store(monkeypatch):
    from app.modules import artifact_content, artifact_resolver

    monkeypatch.setattr(object_store, "get_content", lambda sha: b"hello object")

    resolver = artifact_resolver.get_resolver()
    text, mode = resolver.resolve_content(
        "t1", {"storageType": "object", "contentSha256": "b" * 64}
    )
    assert (text, mode) == ("hello object", "object")

    blob, bmode = artifact_content.load_artifact_binary(
        "t1", {"storageType": "object", "contentSha256": "c" * 64}
    )
    assert (blob, bmode) == (b"hello object", "object")


def test_artifact_scoped_storage_reference_falls_back_for_object_type(monkeypatch):
    """A reused (object) artifact can never have a storage_reference row — the
    table's CHECK allows only inline/external/local, and the sync trigger skips
    object. The artifact-scoped content/download binder must fall back to the
    catalogued storage_type/content_sha256 instead of 404-ing, mirroring the
    task-scoped route, so reused artifacts render in the Artifact Library."""
    import pytest

    from app.routes import artifacts as artifacts_route

    class FakeArtRepo:
        def get_storage_reference(self, **kwargs):
            return None  # object type can never produce a row

    monkeypatch.setattr(artifacts_route, "ArtifactRepository", FakeArtRepo)

    artifact = {
        "id": "reused1",
        "storageType": "object",
        "contentSha256": "d" * 64,
        "contentSize": 12,
        "externalKey": "stale",
        "path": "stale",
    }
    bound = artifacts_route._artifact_with_current_storage_reference(artifact)

    assert bound["storageType"] == "object"
    assert bound["contentSha256"] == "d" * 64
    assert "externalKey" not in bound
    assert "path" not in bound


def test_artifact_scoped_storage_reference_still_fails_closed_without_row(monkeypatch):
    """Non-object types without a reference row keep failing closed (404) — the
    object fallback must not weaken integrity for inline/external/local."""
    import pytest

    from app.routes import artifacts as artifacts_route

    class FakeArtRepo:
        def get_storage_reference(self, **kwargs):
            return None

    monkeypatch.setattr(artifacts_route, "ArtifactRepository", FakeArtRepo)

    with pytest.raises(ValueError, match="artifact_storage_reference_missing"):
        artifacts_route._artifact_with_current_storage_reference(
            {"id": "x", "storageType": "inline", "inlineContent": "y"}
        )
