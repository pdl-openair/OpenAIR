from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from app.modules import artifact_content


class _MissingResolver:
    def resolve_content(self, task_id, artifact):
        raise FileNotFoundError("durable object missing")

    def resolve_binary_path(self, task_id, artifact):
        raise FileNotFoundError("durable object missing")


class _InlineResolver:
    def __init__(self, content: str):
        self.content = content

    def resolve_content(self, task_id, artifact):
        return self.content, "inline"


def test_durable_missing_bytes_never_fall_back_to_replay(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    replay = tmp_path / "historical.md"
    replay.write_text("historical replay body", encoding="utf-8")
    monkeypatch.setattr(artifact_content, "get_resolver", lambda: _MissingResolver())
    monkeypatch.setattr(artifact_content, "_resolve_replay_path", lambda *args: replay)
    artifact = {
        "id": "artifact_missing",
        "storageType": "local",
        "path": "workspace/missing.md",
        "relatedSkillId": "skill_replay",
    }

    with pytest.raises(FileNotFoundError, match="unavailable"):
        artifact_content.load_artifact_content(
            "task_1", artifact, allow_replay_fallback=False
        )
    assert artifact_content.resolve_artifact_path(
        "task_1", artifact, allow_replay_fallback=False
    ) is None


def test_durable_hash_mismatch_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(artifact_content, "get_resolver", lambda: _InlineResolver("actual bytes"))
    artifact = {
        "id": "artifact_hash_mismatch",
        "storageType": "inline",
        "inlineContent": "actual bytes",
        "contentSha256": hashlib.sha256(b"different bytes").hexdigest(),
        "contentSize": len(b"actual bytes"),
    }

    with pytest.raises(ValueError, match="artifact_content_hash_mismatch"):
        artifact_content.load_artifact_content(
            "task_1", artifact, allow_replay_fallback=False
        )


def test_durable_storage_reference_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(artifact_content, "get_resolver", lambda: _InlineResolver("body"))
    artifact = {"id": "artifact_no_ref", "storageType": "external"}

    with pytest.raises(ValueError, match="artifact_storage_reference_invalid"):
        artifact_content.load_artifact_content(
            "task_1", artifact, allow_replay_fallback=False
        )


def test_durable_matching_hash_returns_actual_resolver_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    body = "authoritative body"
    monkeypatch.setattr(artifact_content, "get_resolver", lambda: _InlineResolver(body))
    artifact = {
        "id": "artifact_ok",
        "storageType": "inline",
        "inlineContent": body,
        "contentSha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
        "contentSize": len(body.encode("utf-8")),
        "sourceMode": "live",
    }

    content, mode = artifact_content.load_artifact_content(
        "task_1", artifact, allow_replay_fallback=False
    )
    assert content == body
    assert mode == "inline"

class _PathResolver:
    def __init__(self, path: Path):
        self.path = path

    def resolve_binary_path(self, task_id, artifact):
        return self.path


def test_durable_binary_hash_mismatch_fails_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "artifact.bin"
    path.write_bytes(b"actual bytes")
    monkeypatch.setattr(artifact_content, "get_resolver", lambda: _PathResolver(path))
    artifact = {
        "id": "artifact_binary_hash_mismatch",
        "storageType": "local",
        "path": "durable/artifact.bin",
        "contentSha256": hashlib.sha256(b"different bytes").hexdigest(),
        "contentSize": len(b"actual bytes"),
    }

    with pytest.raises(ValueError, match="artifact_content_hash_mismatch"):
        artifact_content.resolve_artifact_path(
            "task_1", artifact, allow_replay_fallback=False
        )


def test_task_scoped_and_global_routes_share_postgres_catalog_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi.testclient import TestClient

    from app.config import settings
    from app.main import app
    from app.modules.artifact_repository import ArtifactRepository
    from app.modules.task_repository import TaskRepository
    from app.routes import artifacts as artifacts_route
    from app.routes import tasks as tasks_route

    owner = "owner-content"
    workspace = "workspace-content"
    headers = {
        "X-OpenAIR-Owner-Id": owner,
        "X-OpenAIR-Workspace-Id": workspace,
        "X-OpenAIR-Created-By": owner,
    }
    body = "authoritative catalog body"
    artifact = {
        "id": "artifact_catalog_body",
        "taskId": "task_catalog_body",
        "sourceTaskId": "task_catalog_body",
        "sourceTaskTitle": "Catalog task",
        "ownerId": owner,
        "workspaceId": workspace,
        "createdBy": owner,
        "kind": "execution_output",
        "title": "Catalog artifact",
        "createdAt": "2026-08-07T00:00:00+00:00",
        "storageType": "inline",
        "inlineContent": body,
        "contentSha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
        "contentSize": len(body.encode("utf-8")),
        "format": "markdown",
        "artifactState": "usable",
        "sourceMode": "live",
    }
    task = {
        "taskId": "task_catalog_body",
        "ownerId": owner,
        "workspaceId": workspace,
        "createdBy": owner,
        "title": "Catalog task",
        "capabilityCalls": [],
    }
    monkeypatch.setattr(settings, "persistence_backend", "postgres")
    monkeypatch.setattr(
        ArtifactRepository,
        "get_by_id",
        lambda self, artifact_id: artifact if artifact_id == artifact["id"] else None,
    )
    monkeypatch.setattr(
        ArtifactRepository,
        "get_storage_reference",
        lambda self, **kwargs: {
            "artifact_id": artifact["id"],
            "reference_kind": "current",
            "version_id": "",
            "source_task_id": task["taskId"],
            "storage_type": "inline",
            "storage_scope": artifact["id"],
            "storage_ref": artifact["contentSha256"],
            "content_sha256": artifact["contentSha256"],
            "content_size": artifact["contentSize"],
        },
    )
    monkeypatch.setattr(
        TaskRepository,
        "get_task",
        lambda self, task_id: task if task_id == task["taskId"] else None,
    )
    monkeypatch.setattr(
        tasks_route, "read_task_scope", lambda task_id: task if task_id == task["taskId"] else None
    )
    monkeypatch.setattr(
        tasks_route.store,
        "get_task",
        lambda _task_id: pytest.fail("task-scoped PostgreSQL content must not read TaskStore"),
    )
    monkeypatch.setattr(
        artifacts_route.store,
        "get_task",
        lambda _task_id: pytest.fail("global PostgreSQL content must not read TaskStore"),
    )

    client = TestClient(app)
    task_scoped = client.get(
        f"/api/tasks/{task['taskId']}/artifacts/{artifact['id']}/content",
        headers=headers,
    )
    global_scoped = client.get(f"/api/artifacts/{artifact['id']}/content", headers=headers)

    assert task_scoped.status_code == 200, task_scoped.text
    assert global_scoped.status_code == 200, global_scoped.text
    assert task_scoped.json()["content"] == body
    assert global_scoped.json()["content"] == body
    assert task_scoped.json()["sourceMode"] == "inline"
    assert global_scoped.json()["sourceMode"] == "inline"
