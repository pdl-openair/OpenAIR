import types

import pytest
from fastapi.testclient import TestClient

from app.main import app


public_client = TestClient(app)


def test_legacy_confirm_plan_rejects_canonical_research_design(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.routes import tasks as tasks_route

    monkeypatch.setattr(
        tasks_route.store,
        "get_task",
        lambda _task_id: {
            "taskId": "task_canonical",
            "ownerId": "user_demo_owner",
            "workspaceId": "ws_demo_default",
            "researchDesignVersions": [],
        },
    )

    response = public_client.post("/api/tasks/task_canonical/confirm-plan", json={})

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "canonical research design must use /research-design/confirm-plan"
    )


def _research_design_read_payload(
    *,
    design_id: str,
    version: int,
    status: str,
    revision: int,
    coverage_status: str,
    workflow_provenance: dict[str, object] | None = None,
) -> dict[str, object]:
    """Return the complete public ResearchDesign confirmation contract."""

    goal = "Complete the confirmed research objective."
    return {
        "designId": design_id,
        "version": version,
        "status": status,
        "goal": goal,
        "projectCompletionRequirements": [
            {
                "completionRequirementId": f"completion_{design_id}_0",
                "targetOutputRef": "understanding.targetOutputs[0]",
                "description": goal,
            }
        ],
        "workflowProvenanceRef": workflow_provenance,
        "workflowResponsibilityCoverage": [],
        "coverage": {"status": coverage_status},
        "revision": revision,
    }


def test_v12_design_api_requires_explicit_ref_revision_and_idempotency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.routes import tasks as tasks_route

    calls: list[dict] = []

    class FakeService:
        def read_design(self, **_kwargs):
            return _research_design_read_payload(
                design_id="design_api",
                version=2,
                status="confirmed",
                revision=9,
                coverage_status="passed_structural",
            )

        def confirm(self, **kwargs):
            calls.append(kwargs)
            return types.SimpleNamespace(active_research_design_ref=None)

    fake = FakeService()
    monkeypatch.setattr(tasks_route.settings, "research_orchestration_v1", True)
    monkeypatch.setattr(tasks_route.settings, "persistence_backend", "postgres")
    monkeypatch.setattr(tasks_route, "_research_design_service", lambda: fake)

    missing = public_client.post(
        "/api/tasks/task_api/research-design/confirm-plan",
        json={"designId": "design_api", "version": 2},
    )
    assert missing.status_code == 422

    response = public_client.post(
        "/api/tasks/task_api/research-design/confirm-plan",
        json={
            "designId": "design_api",
            "version": 2,
            "confirmationId": "confirmation_api",
            "decision": "approved",
            "expectedTaskRevision": 8,
        },
    )
    assert response.status_code == 200
    assert response.json() == _research_design_read_payload(
        design_id="design_api",
        version=2,
        status="confirmed",
        revision=9,
        coverage_status="passed_structural",
    )
    assert calls[0]["design_id"] == "design_api"
    assert calls[0]["confirmation_id"] == "confirmation_api"
    assert calls[0]["expected_task_revision"] == 8


def _confirmed_ot_task_for_confirmation_continuity():
    ready_instance = types.SimpleNamespace(
        substage_instance_id="instance_ot_1",
        instance_revision=1,
        substage_definition_id="opentrajectory:intent_understanding",
        sequence_hint=0,
    )
    later_instance = types.SimpleNamespace(
        substage_instance_id="instance_ot_2",
        instance_revision=1,
        substage_definition_id="opentrajectory:constraints_criteria",
        sequence_hint=1,
    )
    design = types.SimpleNamespace(
        research_design_id="design_ot",
        version=1,
        status=types.SimpleNamespace(value="confirmed"),
        understanding=types.SimpleNamespace(research_type="opentrajectory"),
        coverage_report=types.SimpleNamespace(planning_attempt_id="planning_ot"),
        instances=[ready_instance, later_instance],
    )
    return types.SimpleNamespace(
        task_id="task_ot",
        revision=9,
        active_research_design_ref=types.SimpleNamespace(
            research_design_id="design_ot",
            version=1,
        ),
        research_design_versions=[design],
        planning_attempts=[types.SimpleNamespace(
            planning_attempt_id="planning_ot",
            request_id="rsr_ot",
            status=types.SimpleNamespace(value="completed"),
            planner_provider_call_ref="provider:planner:ot",
            planner_call_idempotency_key="planner:rsr_ot",
        )],
        substage_runtime_states=[
            types.SimpleNamespace(
                substage_instance_id="instance_ot_1",
                instance_revision=1,
                status=types.SimpleNamespace(value="ready"),
            ),
            types.SimpleNamespace(
                substage_instance_id="instance_ot_2",
                instance_revision=1,
                status=types.SimpleNamespace(value="planned"),
            ),
        ],
        execution_decisions=[],
    )


@pytest.mark.parametrize(
    ("mode", "starts_execution"),
    [
        ("automatic_research", True),
        ("stage_confirmation", True),
        ("manual_step", False),
    ],
)
def test_v12_generic_confirmation_applies_initial_start_policy(
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    starts_execution: bool,
) -> None:
    from app.routes import tasks as tasks_route

    task = _confirmed_ot_task_for_confirmation_continuity()
    task.research_design_versions[0].understanding.research_type = "sdr"
    task.research_design_versions[0].instances[0].substage_definition_id = "sdr:step_one"
    queued: list[dict[str, object]] = []

    class FakeService:
        def confirm(self, **_kwargs):
            return task

        def read_design(self, **_kwargs):
            return _research_design_read_payload(
                design_id="design_ot",
                version=1,
                status="confirmed",
                revision=task.revision,
                coverage_status="passed_semantic",
            )

    class PolicyRepository:
        def get_task_execution_policy(self, **_kwargs):
            return {"currentMode": mode}

    class Queue:
        def enqueue(self, **kwargs):
            queued.append(kwargs)
            return {"state": "queued"}

    monkeypatch.setattr(tasks_route.settings, "research_orchestration_v1", True)
    monkeypatch.setattr(tasks_route.settings, "persistence_backend", "postgres")
    monkeypatch.setattr(tasks_route, "_research_design_service", lambda: FakeService())
    monkeypatch.setattr(tasks_route, "CollaborationRepository", PolicyRepository)
    monkeypatch.setattr(tasks_route, "ModelJobQueueRepository", Queue)

    body = {
        "designId": "design_ot",
        "version": 1,
        "confirmationId": "confirmation_generic",
        "decision": "approved",
        "expectedTaskRevision": 8,
    }
    response = public_client.post(
        "/api/tasks/task_ot/research-design/confirm-plan",
        json=body,
    )

    assert response.status_code == 200
    expected_start = {
        "resource_kind": "task",
        "resource_id": "task_ot",
        "owner_id": "user_demo_owner",
        "workspace_id": "ws_demo_default",
        "job_id": "research-design:auto-start:design_ot:v1",
        "job_kind": "execution_selection",
        "expected_revision": 9,
        "command_payload": {"requestKind": "confirmed_design"},
    }
    assert queued == ([expected_start] if starts_execution else [])

    retry = public_client.post("/api/tasks/task_ot/research-design/confirm-plan", json=body)
    assert retry.status_code == 200
    # The route delegates replay ownership to ExecutionDecision; the stable
    # selection_request_id makes the second request an idempotent service replay.
    assert queued == ([expected_start, expected_start] if starts_execution else [])


@pytest.mark.parametrize("failure_kind", ["generic", "durable"])
def test_v12_confirmed_design_continuation_failure_does_not_rewrite_confirmation_result(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    failure_kind: str,
) -> None:
    from app.routes import tasks as tasks_route

    task = _confirmed_ot_task_for_confirmation_continuity()
    confirm_calls: list[dict[str, object]] = []

    class FakeService:
        def confirm(self, **kwargs):
            confirm_calls.append(kwargs)
            return task

        def read_design(self, **_kwargs):
            return _research_design_read_payload(
                design_id="design_ot",
                version=1,
                status="confirmed",
                revision=task.revision,
                coverage_status="passed_semantic",
            )

    def fail_continuation(**_kwargs):
        if failure_kind == "durable":
            raise tasks_route.DurableExecutionError(503, "durable selection unavailable")
        raise RuntimeError("selection exploded")

    monkeypatch.setattr(tasks_route.settings, "research_orchestration_v1", True)
    monkeypatch.setattr(tasks_route.settings, "persistence_backend", "postgres")
    monkeypatch.setattr(tasks_route, "_research_design_service", lambda: FakeService())
    monkeypatch.setattr(
        tasks_route,
        "_continue_confirmed_design_automatic_research",
        fail_continuation,
    )
    caplog.set_level(40, logger=tasks_route.__name__)

    response = public_client.post(
        "/api/tasks/task_ot/research-design/confirm-plan",
        json={
            "designId": "design_ot",
            "version": 1,
            "confirmationId": "confirmation_ot_failure_boundary",
            "decision": "approved",
            "expectedTaskRevision": 8,
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "confirmed"
    assert len(confirm_calls) == 1
    assert "confirmed research design automatic continuation failed" in caplog.text
    assert "taskId=task_ot" in caplog.text
    assert "designId=design_ot" in caplog.text


def test_v12_research_design_confirmation_failure_still_fails_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.routes import tasks as tasks_route

    continuation_calls: list[dict[str, object]] = []

    class FakeService:
        def confirm(self, **_kwargs):
            raise tasks_route.ResearchDesignConfirmationError("confirmation rejected")

        def read_design(self, **_kwargs):
            pytest.fail("failed confirmation must not read a confirmed design")

    monkeypatch.setattr(tasks_route.settings, "research_orchestration_v1", True)
    monkeypatch.setattr(tasks_route.settings, "persistence_backend", "postgres")
    monkeypatch.setattr(tasks_route, "_research_design_service", lambda: FakeService())
    monkeypatch.setattr(
        tasks_route,
        "_continue_confirmed_design_automatic_research",
        lambda **kwargs: continuation_calls.append(kwargs),
    )

    response = public_client.post(
        "/api/tasks/task_ot/research-design/confirm-plan",
        json={
            "designId": "design_ot",
            "version": 1,
            "confirmationId": "confirmation_ot_rejected",
            "decision": "approved",
            "expectedTaskRevision": 8,
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "confirmation rejected"
    assert continuation_calls == []


def test_v12_stage_confirmation_draft_does_not_start_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.routes import tasks as tasks_route

    starts: list[dict[str, object]] = []
    task = _confirmed_ot_task_for_confirmation_continuity()

    class FakeService:
        def confirm(self, **_kwargs):
            return task

        def read_design(self, **_kwargs):
            return _research_design_read_payload(
                design_id="design_draft",
                version=1,
                status="draft",
                revision=8,
                coverage_status="passed_semantic",
            )

    monkeypatch.setattr(tasks_route.settings, "research_orchestration_v1", True)
    monkeypatch.setattr(tasks_route.settings, "persistence_backend", "postgres")
    monkeypatch.setattr(tasks_route, "_research_design_service", lambda: FakeService())
    monkeypatch.setattr(
        tasks_route,
        "_continue_confirmed_design_automatic_research",
        lambda **kwargs: starts.append(kwargs),
    )

    response = public_client.post(
        "/api/tasks/task_ot/research-design/confirm-plan",
        json={
            "designId": "design_draft",
            "version": 1,
            "confirmationId": "confirmation_draft",
            "decision": "request_revision",
            "expectedTaskRevision": 8,
        },
    )

    assert response.status_code == 200
    assert starts == []


def test_v12_ot_confirmation_never_starts_a_different_active_design(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.routes import tasks as tasks_route

    task = _confirmed_ot_task_for_confirmation_continuity()
    task.active_research_design_ref = types.SimpleNamespace(
        research_design_id="design_other",
        version=2,
    )

    class FakeService:
        def confirm(self, **_kwargs):
            # Models an idempotent replay of an older confirmation after a later
            # Design has become active.
            return task

        def read_design(self, **_kwargs):
            return _research_design_read_payload(
                design_id="design_other",
                version=2,
                status="confirmed",
                revision=task.revision,
                coverage_status="passed_semantic",
            )

    class NeverPolicyRepository:
        def get_task_execution_policy(self, **_kwargs):
            pytest.fail("a different active Design must fail closed before policy/start")

    monkeypatch.setattr(tasks_route.settings, "research_orchestration_v1", True)
    monkeypatch.setattr(tasks_route.settings, "persistence_backend", "postgres")
    monkeypatch.setattr(tasks_route, "_research_design_service", lambda: FakeService())
    monkeypatch.setattr(tasks_route, "CollaborationRepository", NeverPolicyRepository)

    response = public_client.post(
        "/api/tasks/task_ot/research-design/confirm-plan",
        json={
            "designId": "design_ot",
            "version": 1,
            "confirmationId": "confirmation_ot",
            "decision": "approved",
            "expectedTaskRevision": 8,
        },
    )

    assert response.status_code == 200

def test_v12_confirm_plan_projects_workflow_responsibility_coverage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.routes import tasks as tasks_route

    responsibility_coverage = [
        {
            "responsibilityKey": "reproduction_baseline_criteria",
            "definitionId": "openair:cs_reproduction_baseline_criteria",
            "requirement": "core",
            "coverageMode": "planned_work",
            "instanceRefs": [
                {
                    "substageInstanceId": "instance_cs_1",
                    "instanceRevision": 1,
                }
            ],
            "reusedResultRefs": [],
            "rationale": "Required for the active reproduction design.",
        }
    ]

    class FakeService:
        def confirm(self, **_kwargs):
            return types.SimpleNamespace(active_research_design_ref=None)

        def read_design(self, **_kwargs):
            payload = _research_design_read_payload(
                design_id="design_cs",
                version=1,
                status="confirmed",
                revision=9,
                coverage_status="passed_semantic",
            )
            payload["workflowResponsibilityCoverage"] = responsibility_coverage
            return payload

    monkeypatch.setattr(tasks_route.settings, "research_orchestration_v1", True)
    monkeypatch.setattr(tasks_route.settings, "persistence_backend", "postgres")
    monkeypatch.setattr(tasks_route, "_research_design_service", lambda: FakeService())
    monkeypatch.setattr(
        tasks_route,
        "_continue_confirmed_design_automatic_research",
        lambda **_kwargs: None,
    )

    response = public_client.post(
        "/api/tasks/task_cs/research-design/confirm-plan",
        json={
            "designId": "design_cs",
            "version": 1,
            "confirmationId": "confirmation_cs_coverage",
            "decision": "approved",
            "expectedTaskRevision": 8,
        },
    )

    assert response.status_code == 200
    assert response.json()["workflowResponsibilityCoverage"] == responsibility_coverage
