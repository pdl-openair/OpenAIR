from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

API_ROOT = Path(__file__).resolve().parents[3]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.main import app
from app.modules.task_store import store


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("OPENAIR_MIGRATION_TOKEN", "test-migration-token")
    monkeypatch.setenv("OPENAIR_MIGRATION_IMPORT_ENABLED", "true")
    return TestClient(app)


def _payload(task_id: str) -> dict:
    created = store.create_task({"title": "Admin import", "goalSummary": "goal"})
    task = store.get_task(created["taskId"])
    assert task is not None
    store._tasks.pop(created["taskId"], None)
    task["taskId"] = task_id
    return task


def test_admin_import_requires_token(client: TestClient) -> None:
    response = client.post("/api/admin/tasks/import", json={"taskId": "task_admin_1", "title": "x"})
    assert response.status_code == 403


def test_admin_import_disabled_returns_503(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAIR_MIGRATION_IMPORT_ENABLED", "false")
    response = client.post(
        "/api/admin/tasks/import",
        json=_payload("task_admin_disabled"),
        headers={"X-OpenAIR-Migration-Token": "test-migration-token"},
    )
    assert response.status_code == 503


def test_admin_import_success(client: TestClient) -> None:
    payload = _payload("task_admin_success")
    response = client.post(
        "/api/admin/tasks/import",
        json=payload,
        headers={"X-OpenAIR-Migration-Token": "test-migration-token"},
    )
    assert response.status_code == 200
    assert response.json()["taskId"] == "task_admin_success"


def test_admin_import_duplicate_returns_409(client: TestClient) -> None:
    payload = _payload("task_admin_conflict")
    headers = {"X-OpenAIR-Migration-Token": "test-migration-token"}
    assert client.post("/api/admin/tasks/import", json=payload, headers=headers).status_code == 200
    response = client.post("/api/admin/tasks/import", json=payload, headers=headers)
    assert response.status_code == 409
    assert "task already exists" in response.json()["detail"]


def test_admin_import_invalid_payload_returns_400(client: TestClient) -> None:
    response = client.post(
        "/api/admin/tasks/import",
        json={"title": "missing task id"},
        headers={"X-OpenAIR-Migration-Token": "test-migration-token"},
    )
    assert response.status_code == 400
    assert "missing or empty taskId" in response.json()["detail"]
