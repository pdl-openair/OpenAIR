from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.routes import artifacts as artifacts_route
from app.routes import tasks as tasks_route


OWNER = "owner-line-c"
WORKSPACE = "workspace-line-c"
ACTOR = "actor-line-c"
HEADERS = {
    "X-OpenAIR-Owner-Id": OWNER,
    "X-OpenAIR-Workspace-Id": WORKSPACE,
    "X-OpenAIR-Created-By": ACTOR,
}
client = TestClient(app)


def _task(*, status: str = "draft", revision: int = 3, title: str = "Task") -> dict:
    return {
        "taskId": "task_line_c",
        "ownerId": OWNER,
        "workspaceId": WORKSPACE,
        "createdBy": ACTOR,
        "title": title,
        "revision": revision,
        "status": status,
        "currentStage": "problem_modeling",
        "resultSource": "none",
        "creationStatus": "awaiting_confirmation" if status == "draft" else None,
        "stages": [
            "problem_modeling",
            "solution_design",
            "execution_validation",
            "insight_generation",
        ],
        "artifacts": [],
    }


@pytest.fixture(autouse=True)
def postgres_backend(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "persistence_backend", "postgres")


def test_postgres_rename_reaches_canonical_writer(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict] = []

    class Repository:
        def rename_task_scoped(self, **kwargs):
            calls.append(kwargs)
            return _task(revision=4, title=kwargs["title"].strip())

    monkeypatch.setattr(tasks_route, "TaskRepository", Repository)
    monkeypatch.setattr(
        tasks_route.store,
        "rename_task",
        lambda *args, **kwargs: pytest.fail("PostgreSQL rename must not use TaskStore"),
    )

    response = client.patch(
        "/api/tasks/task_line_c",
        json={"title": "  Renamed  "},
        headers={**HEADERS, "If-Match-Revision": "3", "Idempotency-Key": "rename-1"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["title"] == "Renamed"
    assert response.json()["revision"] == 4
    assert calls[0]["expected_revision"] == 3
    assert calls[0]["idempotency_key"] == "rename-1"


def test_postgres_revise_and_cancel_use_cas_writer(monkeypatch: pytest.MonkeyPatch) -> None:
    actions: list[str] = []

    class Repository:
        def get_task(self, task_id):
            return _task()

        def request_draft_revision(self, **kwargs):
            actions.append("revise")
            row = _task(revision=4)
            row["creationStatus"] = "revision_requested"
            return row

        def cancel_draft_scoped(self, **kwargs):
            actions.append("cancel")
            row = _task(status="cancelled", revision=4)
            row["creationStatus"] = "cancelled"
            return row

    monkeypatch.setattr(tasks_route, "TaskRepository", Repository)

    revise = client.post(
        "/api/tasks/task_line_c/revise-plan",
        headers={**HEADERS, "If-Match-Revision": "3", "Idempotency-Key": "revise-1"},
    )
    cancel = client.post(
        "/api/tasks/task_line_c/cancel-draft",
        headers={**HEADERS, "If-Match-Revision": "3", "Idempotency-Key": "cancel-1"},
    )

    assert revise.status_code == 200, revise.text
    assert cancel.status_code == 200, cancel.text
    assert revise.json()["creationStatus"] == "revision_requested"
    assert cancel.json()["status"] == "cancelled"
    assert actions == ["revise", "cancel"]


def test_task_delete_uses_tombstone_without_object_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    order: list[str] = []

    class Repository:
        def soft_delete_task(self, **kwargs):
            order.append("database")
            assert kwargs["expected_revision"] == 3
            return True

    monkeypatch.setattr(tasks_route, "TaskRepository", Repository)
    import app.modules.minio_client as minio_client
    monkeypatch.setattr(minio_client, "remove_object", lambda key: order.append(f"object:{key}"))

    response = client.delete(
        "/api/tasks/task_line_c",
        headers={**HEADERS, "If-Match-Revision": "3"},
    )

    assert response.status_code == 204, response.text
    assert order == ["database"]


def test_postgres_upload_registers_catalog_without_taskstore(monkeypatch: pytest.MonkeyPatch) -> None:
    task = _task(status="running")
    calls: list[dict] = []

    class TaskRepository:
        def get_task(self, task_id):
            return task

    class ArtifactRepository:
        def register_user_upload(self, **kwargs):
            calls.append(kwargs)
            return kwargs["artifact"]

    monkeypatch.setattr(artifacts_route, "TaskRepository", TaskRepository)
    monkeypatch.setattr(artifacts_route, "ArtifactRepository", ArtifactRepository)
    monkeypatch.setattr(
        artifacts_route.artifact_write_service,
        "store_uploaded_bytes",
        lambda *args, **kwargs: ("inline", "hello", None, None),
    )
    monkeypatch.setattr(
        artifacts_route.store,
        "add_artifact",
        lambda *args, **kwargs: pytest.fail("PostgreSQL upload must not use TaskStore"),
    )

    response = client.post(
        "/api/artifacts/upload",
        headers=HEADERS,
        files={"file": ("note.txt", b"hello", "text/plain")},
        data={
            "targetTaskId": "task_line_c",
            "expectedRevision": "3",
            "idempotencyKey": "upload-1",
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["sourceTaskId"] == "task_line_c"
    assert body["storageType"] == "inline"
    assert "inlineContent" not in body
    assert calls[0]["artifact"]["inlineContent"] == "hello"
    assert calls[0]["expected_revision"] == 3
    assert calls[0]["idempotency_key"] == "upload-1"


def test_upload_catalog_failure_compensates_local_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    task = _task(status="running")
    written = tmp_path / "upload.txt"
    written.write_text("hello", encoding="utf-8")

    class TaskRepository:
        def get_task(self, task_id):
            return task

    class ArtifactRepository:
        def register_user_upload(self, **kwargs):
            raise RuntimeError("task_revision_conflict")

    monkeypatch.setattr(artifacts_route, "TaskRepository", TaskRepository)
    monkeypatch.setattr(artifacts_route, "ArtifactRepository", ArtifactRepository)
    monkeypatch.setattr(
        artifacts_route.artifact_write_service,
        "store_uploaded_bytes",
        lambda *args, **kwargs: ("local", None, None, str(written)),
    )

    response = client.post(
        "/api/artifacts/upload",
        headers=HEADERS,
        files={"file": ("note.txt", b"hello", "text/plain")},
        data={"targetTaskId": "task_line_c", "expectedRevision": "2"},
    )

    assert response.status_code == 409, response.text
    assert not written.exists()


def test_artifact_delete_commits_catalog_before_blob_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    order: list[str] = []

    class ArtifactRepository:
        def delete_artifact_scoped(self, **kwargs):
            order.append("database")
            return {
                "id": "artifact_line_c",
                "storageType": "external",
                "externalKey": "task_line_c/artifact.bin",
            }

    monkeypatch.setattr(artifacts_route, "ArtifactRepository", ArtifactRepository)
    import app.modules.minio_client as minio_client
    monkeypatch.setattr(minio_client, "remove_object", lambda key: order.append(f"object:{key}"))

    response = client.delete(
        "/api/artifacts/artifact_line_c",
        headers={**HEADERS, "If-Match-Revision": "3"},
    )

    assert response.status_code == 200, response.text
    assert response.json() == {"deleted": True}
    assert order == ["database"]


def test_postgres_task_load_revalidates_persisted_live_label(monkeypatch: pytest.MonkeyPatch) -> None:
    from contextlib import contextmanager

    from app.modules.task_repository import TaskRepository

    row = {
        "task_id": "task_false_live",
        "owner_id": OWNER,
        "workspace_id": WORKSPACE,
        "status": "running",
        "current_stage": "problem_modeling",
        "title": "False live",
        "created_at": None,
        "updated_at": None,
        "revision": 4,
        "payload": {
            **_task(status="running", revision=4, title="False live"),
            "taskId": "task_false_live",
            "resultSource": "live",
            "artifacts": [{"id": "manual-artifact"}],
            "capabilityCalls": [],
        },
    }

    class Cursor:
        def execute(self, sql, params=None):
            self.sql = sql

        def fetchall(self):
            return [row]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    class Connection:
        def cursor(self):
            return Cursor()

    @contextmanager
    def connect():
        yield Connection()

    repository = TaskRepository()
    monkeypatch.setattr(repository, "_connect", connect)

    loaded = repository.load_all_tasks()

    assert loaded[0]["taskId"] == "task_false_live"
    assert loaded[0]["revision"] == 4
    assert loaded[0]["resultSource"] == "none"


def test_delete_route_leaves_cleanup_intent_to_repository_transaction(monkeypatch: pytest.MonkeyPatch) -> None:
    order: list[str] = []
    outbox: list[dict] = []

    class ArtifactRepository:
        def delete_artifact_scoped(self, **kwargs):
            order.append("database")
            return {
                "id": "artifact_line_c",
                "taskId": "task_line_c",
                "sourceTaskId": "task_line_c",
                "storageType": "external",
                "externalKey": "task_line_c/orphan.bin",
            }

        def enqueue_storage_cleanup(self, **kwargs):
            outbox.append(kwargs)
            order.append("outbox")

    monkeypatch.setattr(artifacts_route, "ArtifactRepository", ArtifactRepository)
    import app.modules.minio_client as minio_client
    monkeypatch.setattr(
        minio_client,
        "remove_object",
        lambda key: (_ for _ in ()).throw(RuntimeError("minio unavailable")),
    )

    response = client.delete(
        "/api/artifacts/artifact_line_c",
        headers={**HEADERS, "If-Match-Revision": "3"},
    )

    assert response.status_code == 200, response.text
    assert order == ["database"]
    assert outbox == []
