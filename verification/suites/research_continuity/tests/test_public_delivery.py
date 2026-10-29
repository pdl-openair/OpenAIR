"""Actual public GET with read-only repositories; not a real demo acceptance."""

from copy import deepcopy

import pytest
from app.config import settings
from app.main import app
from app.models.research_orchestration import ResearchTaskV12
from app.modules.research_execution_decision_service import reconcile_task_lifecycle_from_runtime
from app.routes import tasks as tasks_route
from fastapi.testclient import TestClient
from test_round_end import NOW, settled_scope


@pytest.mark.parametrize("goal_achieved", [False, True])
def test_get_separates_round_end_from_goal_and_preserves_private_receipts(
    monkeypatch, goal_achieved
):
    task, _, _ = settled_scope()
    if goal_achieved:
        criterion = task["researchDesignVersions"][0]["instances"][0]["acceptanceCriteria"][0]
        criterion.update(
            judgmentRequirement="deterministic", evaluatorType="deterministic", evaluatorRef=None
        )
    finished = reconcile_task_lifecycle_from_runtime(
        ResearchTaskV12.model_validate(task), occurred_at=NOW
    )
    persisted = finished.model_dump(mode="json", by_alias=True)
    persisted.update(
        {
            "ownerId": "round-owner",
            "workspaceId": "round-workspace",
            "createdBy": "round-actor",
            "title": "Software round delivery control",
            "currentStage": "solution_design",
            "resultSource": "live",
            "stages": [
                "problem_modeling",
                "solution_design",
                "execution_validation",
                "insight_generation",
            ],
            "createdAt": NOW.isoformat(),
            "updatedAt": NOW.isoformat(),
        }
    )
    before = deepcopy(persisted)
    reads = []

    class Repository:
        def get_task_for_public_read(self, task_id):
            assert task_id == persisted["taskId"]
            reads.append(task_id)
            return persisted

    class Artifacts:
        def list_by_scope(self, *_args, **_kwargs):
            return [{"id": "artifact_contract_full"}], 1

        def summary_visible_by_tasks(self, *_args, **_kwargs):
            return {}

    monkeypatch.setattr(settings, "persistence_backend", "postgres")
    monkeypatch.setattr(tasks_route, "TaskRepository", Repository)
    monkeypatch.setattr(tasks_route, "ArtifactRepository", Artifacts)
    response = TestClient(app).get(
        f"/api/tasks/{persisted['taskId']}",
        headers={
            "X-OpenAIR-Owner-Id": persisted["ownerId"],
            "X-OpenAIR-Workspace-Id": persisted["workspaceId"],
            "X-OpenAIR-Created-By": persisted["createdBy"],
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "completed"
    assert body["completionDecision"]["goalAchieved"] is goal_achieved
    assert body["completionDecision"]["roundEndReason"] == (
        "goal_achieved" if goal_achieved else "confirmed_scope_settled"
    )
    assert (
        body["completionDecision"]["completionDecisionId"]
        == finished.completion_decisions[-1].completion_decision_id
    )
    assert body["finalResult"]["finalResultId"] == finished.final_results[-1].final_result_id
    assert "sourceDecisionRefs" not in response.text
    assert '"roundEnd":' not in response.text
    assert persisted == before and reads == [persisted["taskId"]]
