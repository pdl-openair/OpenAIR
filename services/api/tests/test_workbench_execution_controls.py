from fastapi.testclient import TestClient
import pytest

from app.config import settings
from app.main import app
from app.modules.task_store import store
from app.routes import tasks as tasks_route


client = TestClient(app)


@pytest.fixture(autouse=True)
def _use_json_store_for_legacy_workbench_controls(monkeypatch):
    # These controls exercise the in-process legacy workbench model.  Durable
    # PostgreSQL behavior is covered by the separate IB-01 integration suite.
    monkeypatch.setattr(settings, "persistence_backend", "json")


def _workflow_plan() -> dict:
    return {
        "goalSummary": "Review GraphRAG literature sources.",
        "domain": "research_task",
        "skillPack": "sdr",
        "clarifications": [],
        "stages": [
            {
                "stageKey": "problem_modeling",
                "description": "Collect and review sources.",
                "subStages": [],
                "substages": [{"substage": "source_acquisition", "skills": ["literature-search"]}],
            },
            {
                "stageKey": "insight_generation",
                "description": "Confirm next action.",
                "subStages": [],
                "substages": [{"substage": "next_step_confirmation", "skills": ["quality-audit"]}],
            },
        ],
        "readyToConfirm": True,
    }


def _create_running_task() -> dict:
    res = client.post(
        "/api/tasks",
        json={
            "title": "GraphRAG source review",
            "goalSummary": "Review GraphRAG literature sources.",
            "status": "running",
            "creationStatus": "confirmed",
            "currentStage": "problem_modeling",
            "creationPlan": _workflow_plan(),
        },
    )
    assert res.status_code == 201, res.text
    return res.json()


def _stage(body: dict, stage_key: str) -> dict:
    return next(stage for stage in body["stageDetails"] if stage["stageKey"] == stage_key)


def _first_substage_id(task: dict, stage_key: str) -> str:
    stage = _stage(task, stage_key)
    return stage["subStages"][0]["subStageInstanceId"]


def _add_running_call(task: dict, stage_key: str = "problem_modeling") -> dict:
    call = store.add_capability_call(
        task["taskId"],
        skill_id="literature-search",
        status="running",
        stage_key=stage_key,
        substage_instance_id=_first_substage_id(task, stage_key),
    )
    assert call is not None
    return call


def test_get_task_does_not_repair_running_substage_when_calls_are_terminal():
    task = _create_running_task()
    substage_id = _first_substage_id(task, "problem_modeling")
    store.patch_substage_instance(task["taskId"], substage_id, status="running")
    call = store.add_capability_call(
        task["taskId"],
        skill_id="literature-search",
        status="completed",
        result_summary="Completed current node.",
        ended_at="2026-07-11T10:00:00Z",
        stage_key="problem_modeling",
        substage_instance_id=substage_id,
    )
    assert call is not None

    res = client.get(f"/api/tasks/{task['taskId']}")

    assert res.status_code == 200, res.text
    body = res.json()
    substage = _stage(body, "problem_modeling")["subStages"][0]
    assert substage["status"] == "running"
    assert substage["finishedAt"] is None
    assert substage["summary"] is None

    persisted = store.get_task(task["taskId"])
    assert _stage(persisted, "problem_modeling")["subStages"][0]["status"] == "running"


def test_get_task_does_not_repair_running_substage_with_non_terminal_call():
    task = _create_running_task()
    substage_id = _first_substage_id(task, "problem_modeling")
    store.patch_substage_instance(task["taskId"], substage_id, status="running")
    call = store.add_capability_call(
        task["taskId"],
        skill_id="literature-search",
        status="not_started",
        result_summary="Queued current node.",
        stage_key="problem_modeling",
        substage_instance_id=substage_id,
    )
    assert call is not None

    res = client.get(f"/api/tasks/{task['taskId']}")

    assert res.status_code == 200, res.text
    body = res.json()
    substage = _stage(body, "problem_modeling")["subStages"][0]
    assert substage["status"] == "running"
    assert substage["finishedAt"] is None


def test_get_task_does_not_release_running_task_when_current_node_needs_input():
    task = _create_running_task()
    substage_id = _first_substage_id(task, "problem_modeling")
    store.patch_substage_instance(task["taskId"], substage_id, status="blocked_recoverable")
    call = store.add_capability_call(
        task["taskId"],
        skill_id="literature-search",
        status="blocked_recoverable",
        result_summary="Need the user to narrow the evidence scope.",
        stage_key="problem_modeling",
        substage_instance_id=substage_id,
    )
    assert call is not None

    res = client.get(f"/api/tasks/{task['taskId']}")

    assert res.status_code == 200, res.text
    assert res.json()["status"] == "running"
    assert store.get_task(task["taskId"])["status"] == "running"


def test_completed_live_output_in_automatic_research_advances_across_stage():
    task = _create_running_task()
    task_id = task["taskId"]
    substage_id = _first_substage_id(task, "problem_modeling")

    mode_switch = client.patch(
        f"/api/tasks/{task_id}/execution-mode",
        json={"executionMode": "automatic_research"},
    )
    assert mode_switch.status_code == 200, mode_switch.text
    assert mode_switch.json()["executionMode"] == "automatic_research"

    call = store.add_capability_call(
        task_id,
        skill_id="literature-search",
        status="completed",
        # Must use a declared live runtime; unknown provider strings no longer
        # infer live runMode/sourceMode under the result-source contract.
        runtime="claude_code",
        stage_key="problem_modeling",
        substage_instance_id=substage_id,
    )
    assert call is not None
    artifact = store.add_artifact(
        task_id,
        kind="stage_summary",
        title="Source acquisition output",
        related_stage="problem_modeling",
        related_skill_id="literature-search",
        substage_instance_id=substage_id,
        source_capability_call_id=call["id"],
        storage_type="inline",
        inline_content="# Reviewable output",
    )
    assert artifact is not None
    store.patch_capability_call(
        task_id,
        call["id"],
        status="completed",
        artifact_refs=[artifact["id"]],
        output_artifact_refs=[artifact["id"]],
    )

    advanced = client.post(f"/api/tasks/{task_id}/advance-substage?substage_key=source_acquisition")
    assert advanced.status_code == 200, advanced.text
    assert "requiresConfirmation" not in advanced.json()
    assert advanced.json()["stageKey"] == "insight_generation"
    assert advanced.json()["substageKey"] == "next_step_confirmation"
    running = store.get_task(task_id)
    assert running["status"] == "running"
    assert running["currentStage"] == "insight_generation"


def test_running_task_can_stop_and_preserve_review_next_step():
    task = _create_running_task()
    call = _add_running_call(task)

    res = client.post(f"/api/tasks/{task['taskId']}/stop-run")
    assert res.status_code == 200, res.text
    body = res.json()

    assert body["status"] == "awaiting_confirmation"
    stage = _stage(body, "problem_modeling")
    assert stage["status"] == "failed"
    assert stage["lockedReason"] == "用户已停止本轮执行。"
    assert "保留" in stage["risks"]
    assert "重试当前阶段" in stage["primaryAction"]
    assert all(sub["status"] != "running" for sub in stage["subStages"])
    stopped_call = next(c for c in body["capabilityCalls"] if c["id"] == call["id"])
    assert stopped_call["status"] == "failed"
    assert stopped_call["finishedAt"]
    assert "Stopped by user" in stopped_call["resultSummary"]


def _external_retry_body(task_id: str, work_unit_id: str) -> dict:
    return {
        "taskId": task_id,
        "workUnitId": work_unit_id,
        "dispatchId": "dispatch-1",
        "executionDecisionId": "decision-1",
        "priorCapabilityCallId": "call-1",
        "priorAttemptNumber": 1,
        "commandRequestId": "retry-command-1",
        "canonicalPayloadFingerprint": "c" * 64,
        "expectedTaskRevision": 9,
        "ownerId": "owner-route",
        "workspaceId": "workspace-route",
        "actorId": "actor-route",
        "reasonCode": "transport_never_started_transient",
    }


def test_external_recovery_service_uses_production_factory(monkeypatch):
    service = object()
    monkeypatch.setattr(tasks_route, "production_external_recovery_service", lambda: service)

    assert tasks_route._external_recovery_service() is service


def test_external_retry_route_is_absent_when_orchestration_flag_is_off(monkeypatch):
    calls = []

    class Service:
        def retry(self, command):
            calls.append(command)
            return {"status": "unexpected"}

    monkeypatch.setattr(settings, "research_orchestration_v1", False)
    monkeypatch.setattr(tasks_route, "_external_recovery_service", lambda: Service())
    response = client.post(
        "/api/tasks/task-1/work-units/work-1/external-result/retry",
        json=_external_retry_body("task-1", "work-1"),
        headers={
            "X-OpenAIR-Owner-Id": "owner-route",
            "X-OpenAIR-Workspace-Id": "workspace-route",
            "X-OpenAIR-Created-By": "actor-route",
        },
    )
    assert response.status_code == 404
    assert calls == []


def test_external_retry_route_uses_scoped_durable_command_when_flag_is_on(monkeypatch):
    seen = []

    class Service:
        def retry(self, command):
            seen.append(command)
            return {"status": "created", "capabilityCallId": "call-2", "attemptNumber": 2}

    monkeypatch.setattr(settings, "research_orchestration_v1", True)
    monkeypatch.setattr(settings, "persistence_backend", "postgres")
    monkeypatch.setattr(tasks_route, "_external_recovery_service", lambda: Service())
    response = client.post(
        "/api/tasks/task-1/work-units/work-1/external-result/retry",
        json=_external_retry_body("task-1", "work-1"),
        headers={
            "X-OpenAIR-Owner-Id": "owner-route",
            "X-OpenAIR-Workspace-Id": "workspace-route",
            "X-OpenAIR-Created-By": "actor-route",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"status": "created", "capabilityCallId": "call-2", "attemptNumber": 2}
    assert len(seen) == 1
    assert seen[0].work_unit_id == "work-1"


def test_external_retry_scope_mismatch_is_zero_service_calls(monkeypatch):
    calls = []

    class Service:
        def retry(self, command):
            calls.append(command)
            return {"status": "unexpected"}

    monkeypatch.setattr(settings, "research_orchestration_v1", True)
    monkeypatch.setattr(settings, "persistence_backend", "postgres")
    monkeypatch.setattr(tasks_route, "_external_recovery_service", lambda: Service())
    body = _external_retry_body("task-1", "work-1")
    body["ownerId"] = "wrong-owner"
    response = client.post(
        "/api/tasks/task-1/work-units/work-1/external-result/retry",
        json=body,
        headers={
            "X-OpenAIR-Owner-Id": "owner-route",
            "X-OpenAIR-Workspace-Id": "workspace-route",
            "X-OpenAIR-Created-By": "actor-route",
        },
    )
    assert response.status_code == 404
    assert calls == []


def test_near_match_external_retry_path_is_not_postgres_middleware_allowlisted(monkeypatch):
    monkeypatch.setattr(settings, "research_orchestration_v1", True)
    monkeypatch.setattr(settings, "persistence_backend", "postgres")
    response = client.post(
        "/api/tasks/task-1/work-units/work-1/external-result/retry-extra",
        json=_external_retry_body("task-1", "work-1"),
        headers={
            "X-OpenAIR-Owner-Id": "owner-route",
            "X-OpenAIR-Workspace-Id": "workspace-route",
            "X-OpenAIR-Created-By": "actor-route",
        },
    )
    assert response.status_code == 503
    assert response.json()["detail"] == "taskstore_mutation_forbidden_in_postgres"


def test_nested_delete_task_path_is_not_postgres_middleware_allowlisted(monkeypatch):
    monkeypatch.setattr(settings, "persistence_backend", "postgres")
    response = client.delete(
        "/api/tasks/task-1/artifacts",
        headers={
            "X-OpenAIR-Owner-Id": "owner-route",
            "X-OpenAIR-Workspace-Id": "workspace-route",
            "X-OpenAIR-Created-By": "actor-route",
        },
    )
    assert response.status_code == 503
    assert response.json()["detail"] == "taskstore_mutation_forbidden_in_postgres"


def test_flag_on_legacy_retry_cannot_bypass_durable_unknown_gate(monkeypatch):
    monkeypatch.setattr(settings, "research_orchestration_v1", True)
    response = client.post("/api/tasks/unknown-task/retry")
    assert response.status_code == 409
    assert response.json()["detail"] == "use the WorkUnit-scoped explicit retry command"
