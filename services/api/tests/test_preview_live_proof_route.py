from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.modules.artifact_content import load_artifact_content
from app.modules.task_store import store


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def isolated_store():
    original = store._tasks.copy()
    store._tasks.clear()
    try:
        yield
    finally:
        store._tasks.clear()
        store._tasks.update(original)


def _create_task(status: str) -> str:
    task = store.create_task(
        {
            "title": f"preview proof {status}",
            "goalSummary": "bounded preview proof route test",
            "domain": "computer_science",
            "currentStage": "problem_modeling",
            "status": status,
        }
    )
    return str(task["taskId"])


def test_legacy_preview_live_proof_route_is_not_available(client: TestClient) -> None:
    for status in ["draft", "not_started", "awaiting_confirmation", "completed", "failed", "cancelled"]:
        task_id = _create_task(status)
        response = client.post(f"/api/tasks/{task_id}/preview-live-proof")
        assert response.status_code == 404
        assert store.get_task(task_id)["artifacts"] == []


def test_legacy_preview_route_cannot_fabricate_running_task_artifacts(client: TestClient) -> None:
    task_id = _create_task("running")

    response = client.post(f"/api/tasks/{task_id}/preview-live-proof")

    assert response.status_code == 404
    task = store.get_task(task_id)
    assert task is not None
    assert task["artifacts"] == []
    assert task["capabilityCalls"] == []


def test_patch_capability_call_metadata_cannot_overwrite_protected_fields() -> None:
    task_id = _create_task("running")
    call = store.add_capability_call(
        task_id,
        skill_id="real_skill",
        status="running",
        runtime="claude_code",
    )
    assert call

    patched = store.patch_capability_call(
        task_id,
        call["id"],
        status="completed",
        metadata={
            "id": "call_hijacked",
            "status": "failed",
            "runtime": "replay",
            "stageKey": "report_generation",
            "runId": "run_allowed",
        },
    )

    assert patched
    assert patched["id"] == call["id"]
    assert patched["status"] == "completed"
    assert patched["runtime"] == "claude_code"
    assert patched["stageKey"] is None
    assert patched["runId"] == "run_allowed"


def test_inline_non_preview_artifact_reports_inline_source_mode() -> None:
    task_id = _create_task("running")
    call = store.add_capability_call(
        task_id,
        skill_id="real_skill",
        status="completed",
        runtime="claude_code",
    )
    assert call
    artifact = store.add_artifact(
        task_id,
        kind="stage_summary",
        title="inline non-preview",
        format="markdown",
        related_stage="problem_modeling",
        source_capability_call_id=call["id"],
        storage_type="inline",
        inline_content="# inline non-preview",
    )
    assert artifact

    task = store.get_task(task_id)
    assert task
    content, source_mode = load_artifact_content(task_id, artifact, task["capabilityCalls"])

    assert content == "# inline non-preview"
    assert source_mode == "inline"


def test_inline_preview_artifact_reports_inline_source_mode() -> None:
    task_id = _create_task("running")
    call = store.add_capability_call(
        task_id,
        skill_id="preview_live_proof",
        status="completed",
        runtime="preview_l2_lite",
    )
    assert call
    artifact = store.add_artifact(
        task_id,
        kind="stage_summary",
        title="inline preview",
        format="markdown",
        related_stage="problem_modeling",
        source_capability_call_id=call["id"],
        storage_type="inline",
        inline_content="# inline preview",
    )
    assert artifact

    content, source_mode = load_artifact_content(task_id, artifact, [call])

    assert content == "# inline preview"
    assert source_mode == "inline"
