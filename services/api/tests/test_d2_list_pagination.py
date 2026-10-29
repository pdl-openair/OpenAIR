"""D2 list pagination tests: opt-in limit/offset + total count (TaskListOut)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.routes import tasks as task_routes

OWNER = "owner_d2"
WORKSPACE = "ws_d2"


def _headers() -> dict[str, str]:
    return {"X-OpenAIR-Owner-Id": OWNER, "X-OpenAIR-Workspace-Id": WORKSPACE, "X-OpenAIR-Created-By": OWNER}


def _minimal_task(task_id: str) -> dict:
    return {
        "taskId": task_id, "ownerId": OWNER, "workspaceId": WORKSPACE, "createdBy": OWNER,
        "title": task_id, "status": "not_started", "currentStage": "problem_modeling",
        "resultSource": "none", "executionMode": "stage_confirmation", "revision": 0,
        "stages": ["problem_modeling", "solution_design", "execution_validation", "insight_generation"],
        "stageDetails": [], "artifacts": [], "capabilityCalls": [],
    }


@pytest.fixture()
def _mocked_repo(monkeypatch: pytest.MonkeyPatch):
    """A TaskRepository fake holding 5 in-scope tasks, honoring limit/offset."""
    tasks = [_minimal_task(f"task_{i}") for i in range(5)]

    class _TaskRepo:
        def count_tasks_by_scope(self, owner_id: str, workspace_id: str) -> int:
            return len(tasks)

        def list_tasks_by_scope(self, owner_id: str, workspace_id: str, *, limit=None, offset=0):
            end = (offset + limit) if limit is not None else None
            return [dict(t) for t in tasks[offset:end]]

        def list_tasks_by_scope_slim(self, owner_id: str, workspace_id: str, *, limit=None, offset=0):
            return self.list_tasks_by_scope(owner_id, workspace_id, limit=limit, offset=offset)

        def list_task_title_options(self, owner_id: str, workspace_id: str, *, limit=50, offset=0):
            end = offset + limit
            return [
                {"taskId": t["taskId"], "title": t.get("title") or t["taskId"]}
                for t in tasks[offset:end]
            ]

    class _ArtifactRepo:
        def summary_visible_by_tasks(self, *args, **kwargs):
            return {}

    monkeypatch.setattr(settings, "persistence_backend", "postgres")
    monkeypatch.setattr(task_routes, "TaskRepository", _TaskRepo)
    monkeypatch.setattr(task_routes, "ArtifactRepository", _ArtifactRepo)
    return tasks


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def test_list_without_params_applies_default_limit(_mocked_repo, client: TestClient) -> None:
    response = client.get("/api/tasks", headers=_headers())
    assert response.status_code == 200, response.text
    body = response.json()
    assert [t["taskId"] for t in body["tasks"]] == [f"task_{i}" for i in range(5)]
    assert body["total"] == 5
    assert body["limit"] == 20
    assert body["offset"] == 0
    # Slim list projection: no heavy collections
    for row in body["tasks"]:
        assert row.get("researchDesignVersions") == []
        assert row.get("stageCatalog") == []
        assert row.get("creationPlan") is None
        assert row.get("capabilityCalls") == []


def test_list_paginates_with_limit_and_offset(_mocked_repo, client: TestClient) -> None:
    response = client.get("/api/tasks?limit=2&offset=1", headers=_headers())
    assert response.status_code == 200, response.text
    body = response.json()
    assert [t["taskId"] for t in body["tasks"]] == ["task_1", "task_2"]
    assert body["total"] == 5  # total is the full in-scope count, not the page size
    assert body["limit"] == 2
    assert body["offset"] == 1


def test_list_last_page_has_remaining_tasks(_mocked_repo, client: TestClient) -> None:
    response = client.get("/api/tasks?limit=2&offset=4", headers=_headers())
    assert response.status_code == 200, response.text
    body = response.json()
    assert [t["taskId"] for t in body["tasks"]] == ["task_4"]
    assert body["total"] == 5


def test_list_rejects_out_of_range_pagination(_mocked_repo, client: TestClient) -> None:
    assert client.get("/api/tasks?limit=0", headers=_headers()).status_code == 422
    assert client.get("/api/tasks?limit=201", headers=_headers()).status_code == 422
    assert client.get("/api/tasks?offset=-1", headers=_headers()).status_code == 422
