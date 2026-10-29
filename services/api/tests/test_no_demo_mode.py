"""Convergence tests for the retired OPENAIR_NO_DEMO_DATA harness."""

from __future__ import annotations

import copy

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.modules.task_store import DEFAULT_OWNER_ID, DEFAULT_WORKSPACE_ID, TaskStore
from app.routes import tasks as task_routes


def test_public_task_api_isolates_scope_and_ignores_legacy_no_demo_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current_store = TaskStore()
    visible = current_store.create_task(
        {
            "title": "Current workspace task",
            "ownerId": DEFAULT_OWNER_ID,
            "workspaceId": DEFAULT_WORKSPACE_ID,
            "status": "not_started",
        }
    )
    other_workspace = current_store.create_task(
        {
            "title": "Same owner, other workspace task",
            "ownerId": DEFAULT_OWNER_ID,
            "workspaceId": "ws_other",
            "status": "not_started",
        }
    )
    other_owner = current_store.create_task(
        {
            "title": "Other owner, same workspace task",
            "ownerId": "user_other",
            "workspaceId": DEFAULT_WORKSPACE_ID,
            "status": "not_started",
        }
    )
    monkeypatch.setattr(task_routes, "store", current_store)
    monkeypatch.setattr(settings, "persistence_backend", "json")
    monkeypatch.setattr(settings, "auth_required", False)
    monkeypatch.delenv("OPENAIR_NO_DEMO_DATA", raising=False)
    headers = {
        "X-OpenAIR-Owner-Id": DEFAULT_OWNER_ID,
        "X-OpenAIR-Workspace-Id": DEFAULT_WORKSPACE_ID,
    }
    with TestClient(app) as client:
        baseline_list = client.get("/api/tasks", headers=headers)
        assert baseline_list.status_code == 200
        assert [task["taskId"] for task in baseline_list.json()["tasks"]] == [visible["taskId"]]
        assert client.get(f"/api/tasks/{visible['taskId']}", headers=headers).status_code == 200
        other_workspace_response = client.get(f"/api/tasks/{other_workspace['taskId']}", headers=headers)
        other_owner_response = client.get(f"/api/tasks/{other_owner['taskId']}", headers=headers)
        assert other_workspace_response.status_code == 403
        assert other_owner_response.status_code == 403
        assert other_workspace["title"] not in other_workspace_response.text
        assert other_owner["title"] not in other_owner_response.text
        assert client.get("/api/tasks/task_missing", headers=headers).status_code == 404

        monkeypatch.setenv("OPENAIR_NO_DEMO_DATA", "1")
        flagged_list = client.get("/api/tasks", headers=headers)
        flagged_task = client.get(f"/api/tasks/{visible['taskId']}", headers=headers)
        assert flagged_list.status_code == 200
        assert flagged_task.status_code == 200
        assert flagged_list.json() == baseline_list.json()
        assert flagged_task.json()["taskId"] == visible["taskId"]


def test_fixture_ownership_rebind_is_idempotent_for_task_and_children() -> None:
    current_store = TaskStore()
    task_id = "task_fixture_rebind_idempotent"
    current_store.register_fixture_seed_task_ids({task_id})
    current_store.seed_tasks(
        {
            task_id: {
                "taskId": task_id,
                "ownerId": DEFAULT_OWNER_ID,
                "workspaceId": DEFAULT_WORKSPACE_ID,
                "artifacts": [
                    {
                        "id": "artifact_fixture_rebind",
                        "ownerId": DEFAULT_OWNER_ID,
                        "workspaceId": DEFAULT_WORKSPACE_ID,
                    }
                ],
                "capabilityCalls": [
                    {
                        "id": "call_fixture_rebind",
                        "ownerId": DEFAULT_OWNER_ID,
                        "workspaceId": DEFAULT_WORKSPACE_ID,
                    }
                ],
            }
        }
    )

    assert current_store.rebind_legacy_fixture_ownership("user_dev", "ws_dev") == 1
    first = copy.deepcopy(current_store.get_task(task_id))
    assert current_store.rebind_legacy_fixture_ownership("user_dev", "ws_dev") == 1
    second = current_store.get_task(task_id)

    assert second == first
    assert second is not None
    assert (second["ownerId"], second["workspaceId"]) == ("user_dev", "ws_dev")
    assert (second["artifacts"][0]["ownerId"], second["artifacts"][0]["workspaceId"]) == ("user_dev", "ws_dev")
    assert (second["capabilityCalls"][0]["ownerId"], second["capabilityCalls"][0]["workspaceId"]) == ("user_dev", "ws_dev")


def test_task_store_cannot_become_a_postgres_writer() -> None:
    with pytest.raises(RuntimeError, match="TaskStore cannot be a PostgreSQL writer"):
        TaskStore().enable_repository(object())
