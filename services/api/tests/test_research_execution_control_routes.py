import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app


public_client = TestClient(app)


def test_flag_on_task_start_persists_intent_without_running_model_inline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.routes import tasks as tasks_route

    legacy_calls: list[dict] = []

    class LegacyRepository:
        def start_initial(self, **kwargs):
            legacy_calls.append(kwargs)
            return {"run_id": "legacy_run_must_not_exist"}, False

    queued: list[dict] = []

    class Queue:
        def enqueue(self, **kwargs):
            queued.append(kwargs)
            return {"state": "queued"}

    monkeypatch.setattr(tasks_route.settings, "research_orchestration_v1", True)
    monkeypatch.setattr(tasks_route.settings, "persistence_backend", "postgres")
    monkeypatch.setattr(tasks_route, "DurableExecutionRepository", LegacyRepository)
    monkeypatch.setattr(tasks_route, "serialize_run", lambda run: run)
    monkeypatch.setattr(tasks_route, "ModelJobQueueRepository", Queue)

    response = public_client.post(
        "/api/tasks/task_t05_start/start",
        headers={"Idempotency-Key": "selection_request_t05"},
        json={
            "expectedTaskRevision": 4,
            "selectionRequestId": "selection_request_t05",
            "productAction": "initial_start",
        },
    )

    assert response.status_code == 202
    assert legacy_calls == []
    assert queued == [{
        "resource_kind": "task",
        "resource_id": "task_t05_start",
        "owner_id": "user_demo_owner",
        "workspace_id": "ws_demo_default",
        "job_id": "selection_request_t05",
        "job_kind": "execution_selection",
        "expected_revision": 4,
        "command_payload": {"requestKind": "explicit_start"},
    }]
    assert response.json()["state"] == "accepted"

    rejected = public_client.post(
        "/api/tasks/task_t05_start/start",
        headers={"Idempotency-Key": "selection_request_preselected"},
        json={
            "expectedTaskRevision": 4,
            "selectionRequestId": "selection_request_preselected",
            "productAction": "initial_start",
            "substageInstanceId": "substage_t05",
        },
    )
    assert rejected.status_code == 409
    assert rejected.json()["detail"]["code"] == "api_cannot_preselect_next_research_task"
    assert len(queued) == 1


def test_continue_research_rechecks_current_server_fact_before_enqueue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.routes import tasks as tasks_route

    queued: list[dict] = []

    class TaskRepo:
        def get_task(self, task_id):
            return {
                "taskId": task_id,
                "ownerId": "user_demo_owner",
                "workspaceId": "ws_demo_default",
                "createdBy": "user_demo_owner",
                "status": "awaiting_confirmation",
                "revision": 9,
            }

    class Queue:
        def enqueue(self, **kwargs):
            queued.append(kwargs)
            return {"state": "queued"}

    monkeypatch.setattr(tasks_route.settings, "research_orchestration_v1", True)
    monkeypatch.setattr(tasks_route.settings, "persistence_backend", "postgres")
    monkeypatch.setattr(tasks_route, "TaskRepository", TaskRepo)
    monkeypatch.setattr(tasks_route, "ModelJobQueueRepository", Queue)
    monkeypatch.setattr(
        tasks_route,
        "project_workbench_user_actions",
        lambda **_kwargs: {
            "revision": 9,
            "executionStopped": False,
            "allowedActions": ["continue_research"],
        },
    )

    response = public_client.post(
        "/api/tasks/task_continue/start",
        headers={"Idempotency-Key": "selection_continue_9"},
        json={
            "expectedTaskRevision": 9,
            "selectionRequestId": "selection_continue_9",
            "productAction": "continue_research",
        },
    )

    assert response.status_code == 202
    assert queued == [{
        "resource_kind": "task",
        "resource_id": "task_continue",
        "owner_id": "user_demo_owner",
        "workspace_id": "ws_demo_default",
        "job_id": "selection_continue_9",
        "job_kind": "execution_selection",
        "expected_revision": 9,
        "command_payload": {"requestKind": "continue_research"},
    }]


def test_continue_research_exact_replay_does_not_reacquire_frontend_permission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.routes import tasks as tasks_route

    class TaskRepo:
        def get_task(self, _task_id):
            pytest.fail("exact replay must not re-evaluate changed current facts")

    class Queue:
        def is_exact_replay(self, **_kwargs):
            return True

        def enqueue(self, **_kwargs):
            return {"state": "succeeded"}

    monkeypatch.setattr(tasks_route.settings, "research_orchestration_v1", True)
    monkeypatch.setattr(tasks_route.settings, "persistence_backend", "postgres")
    monkeypatch.setattr(tasks_route, "TaskRepository", TaskRepo)
    monkeypatch.setattr(tasks_route, "ModelJobQueueRepository", Queue)

    response = public_client.post(
        "/api/tasks/task_continue/start",
        headers={"Idempotency-Key": "selection_continue_9"},
        json={
            "expectedTaskRevision": 9,
            "selectionRequestId": "selection_continue_9",
            "productAction": "continue_research",
        },
    )

    assert response.status_code == 202
    assert response.json()["state"] == "succeeded"


@pytest.mark.parametrize(
    ("execution_stopped", "allowed_actions"),
    [
        (False, []),
        (True, ["continue_research"]),
    ],
)
def test_continue_research_rejects_forged_or_stopped_request(
    monkeypatch: pytest.MonkeyPatch,
    execution_stopped: bool,
    allowed_actions: list[str],
) -> None:
    from app.routes import tasks as tasks_route

    class TaskRepo:
        def get_task(self, task_id):
            return {
                "taskId": task_id,
                "ownerId": "user_demo_owner",
                "workspaceId": "ws_demo_default",
                "createdBy": "user_demo_owner",
                "status": "awaiting_confirmation",
                "revision": 9,
            }

    class Queue:
        def enqueue(self, **_kwargs):
            pytest.fail("denied continuation must not enqueue")

    monkeypatch.setattr(tasks_route.settings, "research_orchestration_v1", True)
    monkeypatch.setattr(tasks_route.settings, "persistence_backend", "postgres")
    monkeypatch.setattr(tasks_route, "TaskRepository", TaskRepo)
    monkeypatch.setattr(tasks_route, "ModelJobQueueRepository", Queue)
    monkeypatch.setattr(
        tasks_route,
        "project_workbench_user_actions",
        lambda **_kwargs: {
            "revision": 9,
            "executionStopped": execution_stopped,
            "allowedActions": allowed_actions,
        },
    )

    response = public_client.post(
        "/api/tasks/task_continue/start",
        headers={"Idempotency-Key": "selection_continue_9"},
        json={
            "expectedTaskRevision": 9,
            "selectionRequestId": "selection_continue_9",
            "productAction": "continue_research",
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"] == {"code": "continue_research_not_allowed"}


def test_flag_on_start_queue_is_idempotent_by_selection_command(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.models.research_orchestration import ResearchTaskV12
    from app.modules.research_execution_decision_service import (
        BlockedSelectionReplay,
        ResearchExecutionDecisionService,
        SelectionReplayEvidence,
    )
    from app.modules.research_orchestration_repository import SelectionRequestPayloadConflictError
    from app.modules.research_orchestration_resolver import ResolutionFacts
    from app.models.research_execution_start_status import ResearchExecutionStartStatusOut
    from app.modules.model_job_queue import ModelJobConflict, ModelJobStartDenied
    from app.routes import tasks as tasks_route

    fixture_path = (
        Path(__file__).resolve().parents[3]
        / "packages"
        / "shared"
        / "test-fixtures"
        / "research-orchestration-v1.2-valid.json"
    )
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))["researchTask"]
    payload["executionDecisions"] = []
    payload["workUnits"] = []
    payload["capabilityCalls"] = []
    payload["artifacts"] = []
    payload["agentInteractionRecords"] = []
    payload["substageRuntimeStates"][0].update(
        {
            "status": "ready",
            "activeExecutionDecisionId": None,
            "workUnitIds": [],
            "artifactRefs": [],
        }
    )
    payload["scientificStates"] = [
        {
            "scientificStateVersion": 1,
            "researchDesignVersion": 1,
            "scientificFacts": {},
            "executionState": {},
            "sourceRefs": [],
            "createdAt": "2026-07-28T00:00:00Z",
        }
    ]
    task = ResearchTaskV12.model_validate(payload)
    instance_id = task.research_design_versions[0].instances[0].substage_instance_id

    class Repository:
        def __init__(self) -> None:
            self.current_task = task
            self.blocked: dict[str, tuple[str, str, str, dict]] = {}
            self.load_calls = 0
            self.mutations = 0

        def lookup_selection_replay(self, **kwargs):
            evidence = self.blocked.get(kwargs["selection_request_id"])
            if evidence is None:
                return None
            command_hash, _instance_id, reason_code, facts = evidence
            if command_hash != kwargs["selection_command_hash"]:
                raise SelectionRequestPayloadConflictError("selection_request_command_conflict")
            return BlockedSelectionReplay(reason_code=reason_code, facts=facts)

        def lookup_selection_replay_evidence(self, **kwargs):
            evidence = self.blocked.get(kwargs["selection_request_id"])
            if evidence is None:
                return None
            command_hash, blocked_instance_id, reason_code, facts = evidence
            return SelectionReplayEvidence(
                instance_id=blocked_instance_id,
                selection_command_hash=command_hash,
                replay=BlockedSelectionReplay(reason_code=reason_code, facts=facts),
            )

        def load(self, **_kwargs):
            self.load_calls += 1
            return self.current_task

        def load_registry_snapshot(self, _content_hash):
            return {
                "definitions": {
                    "substages": [
                        {
                            "definitionId": "sdr:evidence_synthesis",
                            "definitionVersion": 1,
                            "inputContract": {
                                "requiredInputRefs": {
                                    "status": "known",
                                    "values": ["material_missing"],
                                }
                            },
                        }
                    ]
                }
            }

        def record_needs_input(self, **kwargs):
            self.mutations += 1
            updated = self.current_task.model_dump(mode="json", by_alias=True)
            updated["revision"] += 1
            updated["substageRuntimeStates"][0].update(
                {
                    "status": "needs_input",
                    "statusReasonCode": kwargs["reason_code"],
                }
            )
            self.current_task = ResearchTaskV12.model_validate(updated)
            durable_facts = {
                "taskRevision": self.current_task.revision,
                **dict(kwargs["blocker_facts"]),
            }
            self.blocked[kwargs["selection_request_id"]] = (
                kwargs["selection_command_hash"],
                kwargs["substage_instance_id"],
                kwargs["reason_code"],
                durable_facts,
            )
            return self.current_task

    facts = ResolutionFacts(
        runtimeReadiness={},
        providerReadiness={},
        authorizedCandidateRefs=set(),
        availableInputRefs=set(),
        applicableRuleRefs=set(),
        satisfiedHumanGateRefs=set(),
        riskAllowed=True,
        budgetAvailable=True,
        policyVersion="registry-policy.v1",
        activeWorkUnitCounts={},
        lastAssignedAt={},
    )
    repository = Repository()
    service = ResearchExecutionDecisionService(
        repository=repository,
        resolver=object(),
        facts_source=lambda *_args: (facts, {}),
    )

    monkeypatch.setattr(tasks_route.settings, "research_orchestration_v1", True)
    monkeypatch.setattr(tasks_route.settings, "persistence_backend", "postgres")
    jobs: dict[str, tuple[int, dict]] = {}

    class Queue:
        def enqueue(self, **kwargs):
            key = kwargs["job_id"]
            signature = (kwargs["expected_revision"], kwargs["command_payload"])
            if key in jobs and jobs[key] != signature:
                raise ModelJobConflict("model_job_command_conflict")
            if key not in jobs and jobs:
                raise ModelJobStartDenied(
                    ResearchExecutionStartStatusOut(
                        taskId=kwargs["resource_id"],
                        state="accepted",
                        taskRevision=kwargs["expected_revision"],
                        startAllowed=False,
                        responsibility="NONE",
                        retryable=False,
                    )
                )
            jobs[key] = signature
            return {"state": "queued"}

    monkeypatch.setattr(tasks_route, "ModelJobQueueRepository", Queue)

    request_a = {
        "expectedTaskRevision": task.revision,
        "selectionRequestId": "selection-blocked-a",
        "productAction": "initial_start",
    }
    headers_a = {"Idempotency-Key": "selection-blocked-a"}

    first = public_client.post(
        f"/api/tasks/{task.task_id}/start",
        headers=headers_a,
        json=request_a,
    )
    assert first.status_code == 202
    first_body = first.json()

    replay = public_client.post(
        f"/api/tasks/{task.task_id}/start",
        headers=headers_a,
        json=request_a,
    )
    assert replay.status_code == 202
    assert replay.json() == first_body
    assert len(jobs) == 1

    changed_revision = dict(request_a)
    changed_revision["expectedTaskRevision"] = task.revision + 1
    conflict_revision = public_client.post(
        f"/api/tasks/{task.task_id}/start",
        headers=headers_a,
        json=changed_revision,
    )
    assert conflict_revision.status_code == 409
    assert conflict_revision.json()["detail"] == "model_job_command_conflict"

    request_b = dict(request_a)
    request_b["selectionRequestId"] = "selection-brand-new-b"
    independent = public_client.post(
        f"/api/tasks/{task.task_id}/start",
        headers={"Idempotency-Key": "selection-brand-new-b"},
        json=request_b,
    )
    assert independent.status_code == 409
    assert independent.json()["detail"]["code"] == "execution_start_not_allowed"
    assert independent.json()["detail"]["startStatus"]["startAllowed"] is False
    assert len(jobs) == 1


def test_flag_on_stop_requires_revision_and_calls_t04_suppress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.routes import tasks as tasks_route

    suppress_calls: list[dict] = []
    legacy_calls: list[dict] = []

    class DispatchService:
        def suppress_with_receipt(self, **kwargs):
            suppress_calls.append(kwargs)
            return {
                "taskId": "task_t05_stop",
                "status": "cancelled",
                "revision": 10,
                "suppressed": True,
            }

    class TaskRepo:
        def get_task(self, task_id):
            return {
                "taskId": task_id,
                "ownerId": "user_demo_owner",
                "workspaceId": "ws_demo_default",
                "status": "cancelled",
                "revision": 10,
            }

    class LegacyRepository:
        def stop_current(self, *args, **kwargs):
            legacy_calls.append({"args": args, "kwargs": kwargs})

    monkeypatch.setattr(tasks_route.settings, "research_orchestration_v1", True)
    monkeypatch.setattr(tasks_route.settings, "persistence_backend", "postgres")
    monkeypatch.setattr(tasks_route, "ResearchDispatchService", DispatchService)
    monkeypatch.setattr(tasks_route, "TaskRepository", TaskRepo)
    monkeypatch.setattr(tasks_route, "DurableExecutionRepository", LegacyRepository)

    response = public_client.post(
        "/api/tasks/task_t05_stop/stop-run",
        json={"expectedTaskRevision": 9},
    )

    assert response.status_code == 200
    assert legacy_calls == []
    assert suppress_calls == [
        {
            "task_id": "task_t05_stop",
            "owner_id": "user_demo_owner",
            "workspace_id": "ws_demo_default",
            "expected_task_revision": 9,
            "reason_code": "user_stop_requested",
        }
    ]
    assert response.json()["revision"] == 10


def test_replayed_stop_returns_committed_canonical_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.routes import tasks as tasks_route

    class DispatchService:
        def suppress_with_receipt(self, **kwargs):
            return {
                "taskId": "task_t05_stop",
                "status": "cancelled",
                "revision": 10,
                "suppressed": True,
            }

    class TaskRepo:
        def get_task(self, task_id):
            return {
                "taskId": task_id,
                "ownerId": "user_demo_owner",
                "workspaceId": "ws_demo_default",
                "status": "cancelled",
                "revision": 10,
            }

    monkeypatch.setattr(tasks_route.settings, "research_orchestration_v1", True)
    monkeypatch.setattr(tasks_route.settings, "persistence_backend", "postgres")
    monkeypatch.setattr(tasks_route, "ResearchDispatchService", DispatchService)
    monkeypatch.setattr(tasks_route, "TaskRepository", TaskRepo)

    response = public_client.post(
        "/api/tasks/task_t05_stop/stop-run",
        json={"expectedTaskRevision": 9},
    )

    assert response.status_code == 200
    assert response.json() == {
        "receiptId": "stop:task_t05_stop:10",
        "taskId": "task_t05_stop",
        "status": "cancelled",
        "revision": 10,
        "suppressed": True,
    }


def test_canonical_start_status_route_is_scoped_and_read_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.models.research_execution_start_status import ResearchExecutionStartRequestStatusOut
    from app.routes import tasks as tasks_route

    calls: list[dict] = []

    class Reader:
        def read(self, **kwargs):
            calls.append(kwargs)
            return ResearchExecutionStartRequestStatusOut(
                taskId=kwargs["task_id"],
                selectionRequestId=kwargs["selection_request_id"],
                state="committed",
                executionDecisionId="decision-start-status",
                taskRevision=12,
            )

    monkeypatch.setattr(tasks_route.settings, "research_orchestration_v1", True)
    monkeypatch.setattr(tasks_route.settings, "persistence_backend", "postgres")
    monkeypatch.setattr(tasks_route, "_research_execution_start_status_reader", Reader)

    response = public_client.get(
        "/api/tasks/task-start-status/execution-selections/request-start-status",
        headers={
            "X-OpenAIR-Owner-Id": "owner-start-status",
            "X-OpenAIR-Workspace-Id": "workspace-start-status",
            "X-OpenAIR-Created-By": "owner-start-status",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "taskId": "task-start-status",
        "selectionRequestId": "request-start-status",
        "state": "committed",
        "taskRevision": 12,
        "executionDecisionId": "decision-start-status",
    }
    assert calls == [
        {
            "task_id": "task-start-status",
            "owner_id": "owner-start-status",
            "workspace_id": "workspace-start-status",
            "selection_request_id": "request-start-status",
        }
    ]


def test_current_start_status_route_is_task_discoverable_without_selection_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.models.research_execution_start_status import ResearchExecutionStartStatusOut
    from app.routes import tasks as tasks_route

    calls: list[dict] = []

    class Reader:
        def read_current(self, **kwargs):
            calls.append(kwargs)
            return ResearchExecutionStartStatusOut(
                taskId=kwargs["task_id"],
                state="blocked",
                taskRevision=12,
                startAllowed=False,
                responsibility="SYSTEM",
                retryable=False,
                reasonSummary="系统尚不能安全启动该研究。",
                blockerRevision=12,
                blockingReasonCode="future_integrity_reason",
            )

    monkeypatch.setattr(tasks_route.settings, "research_orchestration_v1", True)
    monkeypatch.setattr(tasks_route.settings, "persistence_backend", "postgres")
    monkeypatch.setattr(tasks_route, "_research_execution_start_status_reader", Reader)

    response = public_client.get(
        "/api/tasks/task-start-status/execution-start-status",
        headers={
            "X-OpenAIR-Owner-Id": "owner-start-status",
            "X-OpenAIR-Workspace-Id": "workspace-start-status",
            "X-OpenAIR-Created-By": "owner-start-status",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "taskId": "task-start-status",
        "state": "blocked",
        "taskRevision": 12,
        "startAllowed": False,
        "responsibility": "SYSTEM",
        "retryable": False,
        "reasonSummary": "系统尚不能安全启动该研究。",
        "blockerRevision": 12,
        "blockingReasonCode": "future_integrity_reason",
    }
    assert calls == [
        {
            "task_id": "task-start-status",
            "owner_id": "owner-start-status",
            "workspace_id": "workspace-start-status",
        }
    ]


def test_get_unavailable_and_post_start_return_the_same_canonical_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.models.research_execution_start_status import ResearchExecutionStartStatusOut
    from app.modules.model_job_queue import ModelJobStartDenied
    from app.routes import tasks as tasks_route

    status = ResearchExecutionStartStatusOut(
        taskId="task-start-status",
        state="blocked",
        taskRevision=12,
        startAllowed=False,
        responsibility="SYSTEM",
        retryable=False,
        reasonSummary="系统尚不能安全启动该研究。",
        blockerRevision=12,
        blockingReasonCode="future_integrity_reason",
    )

    class Reader:
        def read_current(self, **_kwargs):
            return status

    class Queue:
        def enqueue(self, **_kwargs):
            raise ModelJobStartDenied(status)

    monkeypatch.setattr(tasks_route.settings, "research_orchestration_v1", True)
    monkeypatch.setattr(tasks_route.settings, "persistence_backend", "postgres")
    monkeypatch.setattr(tasks_route, "_research_execution_start_status_reader", Reader)
    monkeypatch.setattr(tasks_route, "ModelJobQueueRepository", Queue)

    available = public_client.get(
        "/api/tasks/task-start-status/execution-start-status"
    )
    started = public_client.post(
        "/api/tasks/task-start-status/start",
        headers={"Idempotency-Key": "request-start-status"},
        json={
            "expectedTaskRevision": 12,
            "selectionRequestId": "request-start-status",
        },
    )

    assert available.status_code == 200
    assert started.status_code == 409
    assert started.json()["detail"]["startStatus"] == available.json()
    assert started.json()["detail"]["startStatus"]["blockingReasonCode"] == (
        "future_integrity_reason"
    )


def test_canonical_start_status_route_preserves_scoped_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.modules.research_execution_start_status import ResearchExecutionStartTaskNotFound
    from app.routes import tasks as tasks_route

    class Reader:
        def read(self, **_kwargs):
            raise ResearchExecutionStartTaskNotFound("research_task_not_found")

    monkeypatch.setattr(tasks_route.settings, "research_orchestration_v1", True)
    monkeypatch.setattr(tasks_route.settings, "persistence_backend", "postgres")
    monkeypatch.setattr(tasks_route, "_research_execution_start_status_reader", Reader)

    response = public_client.get(
        "/api/tasks/task-hidden/execution-selections/request-hidden",
        headers={
            "X-OpenAIR-Owner-Id": "other-owner",
            "X-OpenAIR-Workspace-Id": "other-workspace",
            "X-OpenAIR-Created-By": "other-owner",
        },
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "research task not found"}
