"""List projection omits heavy collections while keeping board fields."""

from __future__ import annotations

from app.modules.task_public_projection import project_public_task_list_item


def test_list_item_projection_strips_heavy_fields() -> None:
    task = {
        "taskId": "task_list_slim",
        "ownerId": "owner_1",
        "workspaceId": "ws_1",
        "createdBy": "owner_1",
        "title": "Slim list task",
        "goalSummary": "Keep goal for board cards",
        "domain": "cs",
        "skillPack": "cs_research",
        "currentStage": "execution_validation",
        "status": "running",
        "creationStatus": "confirmed",
        "resultSource": "live",
        "executionMode": "stage_confirmation",
        "revision": 3,
        "createdAt": "2026-08-14T00:00:00Z",
        "updatedAt": "2026-08-14T01:00:00Z",
        "researchDesignVersions": [{"researchDesignId": "rd_1", "version": 1}],
        "stageCatalog": [{"stageKey": "problem_modeling"}],
        "stageDetails": [{"stageKey": "execution_validation", "summary": "heavy"}],
        "creationPlan": {"steps": [{"id": "s1"}]},
        "capabilityCalls": [{"id": "call_1"}],
        "workUnits": [{"workUnitId": "wu_1"}],
        "artifacts": [{"id": "art_1", "title": "should not appear"}],
        "progressProjection": {"revision": 3},
    }
    out = project_public_task_list_item(task)
    assert out.taskId == "task_list_slim"
    assert out.title == "Slim list task"
    assert out.goalSummary == "Keep goal for board cards"
    assert out.skillPack == "cs_research"
    assert out.status == "running"
    assert out.creationStatus == "confirmed"
    assert out.progressProjection == {"revision": 3}
    assert out.researchDesignVersions == []
    assert out.stageCatalog == []
    assert out.stageDetails == []
    assert out.creationPlan is None
    assert out.capabilityCalls == []
    assert out.workUnits == []
    assert out.artifacts == []
