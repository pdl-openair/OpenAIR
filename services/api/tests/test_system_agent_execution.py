from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.modules.task_store import store


client = TestClient(app)


@pytest.fixture(autouse=True)
def isolated_store():
    original = store._tasks.copy()
    store._tasks.clear()
    try:
        yield
    finally:
        store._tasks.clear()
        store._tasks.update(original)


def _create_task() -> str:
    task = store.create_task(
        {
            "title": "System agent execution",
            "goalSummary": "Run deterministic system agents",
            "domain": "research_task",
            "currentStage": "problem_modeling",
            "status": "running",
            "ownerId": settings.default_owner_id,
            "workspaceId": settings.default_workspace_id,
            "createdBy": settings.default_created_by,
        }
    )
    return str(task["taskId"])


def test_summary_system_agent_creates_source_bound_artifact() -> None:
    task_id = _create_task()

    res = client.post(
        f"/api/tasks/{task_id}/system-agents/run",
        json={
            "agentId": "openair_summary_agent",
            "sourceText": "Abstract: This study reports a bounded evidence workflow and explicitly lists uncertainty.",
        },
    )

    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "completed"
    assert body["runtime"] == "openair_system_agent"
    assert len(body["artifactIds"]) == 1

    task = store.get_task(task_id)
    assert task is not None
    artifact = task["artifacts"][0]
    assert artifact["kind"] == "agent_summary"
    assert artifact["storageType"] == "inline"
    assert "Source-bound summary" in artifact["inlineContent"]
    assert artifact["sourceCapabilityCallId"] == body["callId"]
    assert artifact["userFacingNarrative"]["detailCollapsedByDefault"] is True
    assert task["capabilityCalls"][0]["status"] == "completed"
    assert task["capabilityCalls"][0]["userFacingNarrative"]["agentId"] == "openair_summary_agent"
    assert task["capabilityCalls"][0]["userFacingNarrative"]["sourceType"] == "capability_call"
    assert task["agentMessages"][0]["userFacingNarrative"]["agentId"] == "openair_summary_agent"
    assert task["agentMessages"][0]["userFacingNarrative"]["sourceType"] == "assistant_message"


def test_summary_system_agent_preserves_source_ids_in_visible_summary() -> None:
    task_id = _create_task()

    res = client.post(
        f"/api/tasks/{task_id}/system-agents/run",
        json={
            "agentId": "openair_summary_agent",
            "sourceText": (
                "Key papers include DOI: 10.1038/nenergy.2016.141, "
                "arXiv: 2401.01234 and https://doi.org/10.1021/example."
            ),
        },
    )

    assert res.status_code == 200
    body = res.json()
    task = store.get_task(task_id)
    assert task is not None
    artifact = task["artifacts"][0]
    assert "Source IDs preserved" in artifact["inlineContent"]
    assert "10.1038/nenergy.2016.141" in artifact["inlineContent"]
    assert "2401.01234" in artifact["inlineContent"]
    assert "https://doi.org/10.1021/example" in artifact["inlineContent"]
    assert body["artifactIds"] == [artifact["id"]]


def test_review_system_agent_creates_report_and_updates_review_state_when_requested() -> None:
    task_id = _create_task()
    artifact = store.add_artifact(
        task_id,
        kind="evidence_table",
        title="Evidence table",
        summary="Rows E1 and E2 are traceable; row E3 is missing a locator.",
        storage_type="inline",
        inline_content="E1: linked\nE3: missing locator",
        extra_fields={"artifactState": "pending_review"},
    )
    assert artifact is not None

    res = client.post(
        f"/api/tasks/{task_id}/system-agents/run",
        json={
            "agentId": "openair_review_agent",
            "artifactId": artifact["id"],
            "criteria": "Traceability and missing locator check.",
            "reviewDecision": "needs_revision",
        },
    )

    assert res.status_code == 200
    body = res.json()
    assert body["reviewedArtifactId"] == artifact["id"]
    assert len(body["artifactIds"]) == 1

    task = store.get_task(task_id)
    assert task is not None
    reviewed = next(item for item in task["artifacts"] if item["id"] == artifact["id"])
    report = next(item for item in task["artifacts"] if item["id"] == body["artifactIds"][0])
    assert reviewed["artifactState"] == "rejected_or_needs_revision"
    assert reviewed["reviewSource"] == "review_agent"
    assert report["kind"] == "review_report"
    assert "Artifact review report" in report["inlineContent"]
    assert task["capabilityCalls"][0]["userFacingNarrative"]["agentId"] == "openair_review_agent"
    assert task["agentMessages"][0]["userFacingNarrative"]["agentId"] == "openair_review_agent"


def test_presentation_system_agent_creates_outline_without_inventing_results() -> None:
    task_id = _create_task()

    res = client.post(
        f"/api/tasks/{task_id}/system-agents/run",
        json={
            "agentId": "openair_presentation_guidance_agent",
            "sourceText": "Result table: validated evidence supports claim C1. Limitation: no raw logs for C2.",
            "audience": "lab meeting",
            "outputFormat": "PPT",
        },
    )

    assert res.status_code == 200
    body = res.json()
    task = store.get_task(task_id)
    assert task is not None
    artifact = task["artifacts"][0]
    assert artifact["kind"] == "presentation_outline"
    assert "Presentation guidance draft" in artifact["inlineContent"]
    assert "do not add unverified experiments" in artifact["inlineContent"]
    assert body["artifactIds"] == [artifact["id"]]
    assert task["capabilityCalls"][0]["userFacingNarrative"]["agentId"] == "openair_presentation_guidance_agent"
    assert task["agentMessages"][0]["userFacingNarrative"]["agentId"] == "openair_presentation_guidance_agent"


def test_system_agent_requires_source_material() -> None:
    task_id = _create_task()

    res = client.post(
        f"/api/tasks/{task_id}/system-agents/run",
        json={"agentId": "openair_summary_agent"},
    )

    assert res.status_code == 400
    assert "requires sourceText" in res.json()["detail"]
