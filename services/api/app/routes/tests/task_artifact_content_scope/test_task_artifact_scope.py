import hashlib
from contextlib import contextmanager

import pytest
from app.modules import task_scope_repository as scope
from app.routes import tasks
from fastapi import HTTPException


def test_scope_read_selects_only_row_authority(monkeypatch):
    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, sql, params):
            assert "payload" not in sql
            assert "status <> %s" in sql
            assert params == ("task_1", scope.TASK_DELETED_STATUS)

        def fetchone(self):
            return {"task_id": "task_1", "owner_id": "owner_1", "workspace_id": "workspace_1"}

    class Connection:
        def cursor(self):
            return Cursor()

    @contextmanager
    def connect(self):
        yield Connection()

    monkeypatch.setattr(scope.TaskRepository, "_connect", connect)
    assert scope.read_task_scope("task_1") == {
        "taskId": "task_1",
        "ownerId": "owner_1",
        "workspaceId": "workspace_1",
    }


@pytest.fixture
def durable_route(monkeypatch):
    body = "authoritative body"
    identity = {"ownerId": "owner_1", "workspaceId": "workspace_1"}
    task = {"taskId": "task_1", **identity}
    artifact = {
        "id": "artifact_1",
        "sourceTaskId": "task_1",
        **identity,
        "storageType": "inline",
        "inlineContent": body,
        "contentSha256": hashlib.sha256(body.encode()).hexdigest(),
        "contentSize": len(body.encode()),
        "format": "markdown",
    }
    monkeypatch.setattr(tasks.settings, "persistence_backend", "postgres")
    monkeypatch.setattr(
        tasks, "read_task_scope", lambda task_id: task if task_id == "task_1" else None
    )
    monkeypatch.setattr(
        tasks.TaskRepository, "get_task", lambda *args: pytest.fail("full task read")
    )
    monkeypatch.setattr(tasks.ArtifactRepository, "get_by_id", lambda *args: artifact)
    monkeypatch.setattr(
        tasks.ArtifactRepository,
        "get_storage_reference",
        lambda *args, **kwargs: {
            "storage_type": "inline",
            "storage_ref": artifact["contentSha256"],
            "content_sha256": artifact["contentSha256"],
            "content_size": artifact["contentSize"],
        },
    )
    return task, artifact, identity, body


def test_actual_content_read_uses_scope_without_full_task(durable_route):
    _task, artifact, identity, body = durable_route
    result = tasks.get_artifact_content("task_1", "artifact_1", identity)
    assert result.content == body
    assert result.sourceMode == "inline"
    artifact["contentSha256"] = "0" * 64
    with pytest.raises(HTTPException) as error:
        tasks.get_artifact_content("task_1", "artifact_1", identity)
    assert error.value.status_code == 404


@pytest.mark.parametrize("field", ["ownerId", "workspaceId"])
def test_task_scope_denies_different_identity(durable_route, field):
    task, _artifact, identity, _body = durable_route
    task[field] = "other"
    with pytest.raises(HTTPException) as error:
        tasks._read_task_scoped_artifact("task_1", "artifact_1", identity)
    assert error.value.status_code == 403


def test_missing_or_deleted_task_is_unavailable(durable_route, monkeypatch):
    _task, _artifact, identity, _body = durable_route
    monkeypatch.setattr(tasks, "read_task_scope", lambda task_id: None)
    with pytest.raises(HTTPException) as error:
        tasks._read_task_scoped_artifact("task_1", "artifact_1", identity)
    assert error.value.status_code == 404


@pytest.mark.parametrize("field", ["ownerId", "workspaceId", "sourceTaskId"])
def test_artifact_scope_and_cross_task_remain_protected(durable_route, field):
    _task, artifact, identity, _body = durable_route
    artifact[field] = "other"
    with pytest.raises(HTTPException) as error:
        tasks._read_task_scoped_artifact("task_1", "artifact_1", identity)
    assert error.value.status_code == (404 if field == "sourceTaskId" else 403)


def test_missing_storage_reference_remains_unavailable(durable_route, monkeypatch):
    _task, artifact, identity, _body = durable_route
    monkeypatch.setattr(
        tasks.ArtifactRepository, "get_storage_reference", lambda *args, **kwargs: None
    )
    artifact.pop("contentSha256")
    with pytest.raises(HTTPException) as error:
        tasks.get_artifact_content("task_1", "artifact_1", identity)
    assert error.value.status_code == 404
