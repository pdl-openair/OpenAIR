from fastapi.testclient import TestClient

from app.main import app
from app.modules.task_store import store


OWNER = "owner_task_rename"
WORKSPACE = "ws_task_rename"


def _headers() -> dict[str, str]:
    return {"X-OpenAIR-Owner-Id": OWNER, "X-OpenAIR-Workspace-Id": WORKSPACE}


def _create_task(title: str = "Original title") -> str:
    task = store.create_task(
        {
            "title": title,
            "goalSummary": "rename route regression",
            "ownerId": OWNER,
            "workspaceId": WORKSPACE,
            "createdBy": OWNER,
        }
    )
    return task["taskId"]


def test_rename_task_trims_title() -> None:
    task_id = _create_task()
    response = TestClient(app).patch(
        f"/api/tasks/{task_id}",
        json={"title": "  Updated title  "},
        headers=_headers(),
    )

    assert response.status_code == 200, response.text
    assert response.json()["title"] == "Updated title"
    assert store.get_task(task_id)["title"] == "Updated title"


def test_rename_task_rejects_whitespace_only_title() -> None:
    task_id = _create_task()
    response = TestClient(app).patch(
        f"/api/tasks/{task_id}",
        json={"title": "   "},
        headers=_headers(),
    )

    assert response.status_code == 422
    assert store.get_task(task_id)["title"] == "Original title"
