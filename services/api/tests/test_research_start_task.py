import pytest

from app.config import settings
from app.models.research_orchestration import ResearchTaskV12
from app.modules import research_start_task
from app.modules.research_orchestration_repository import (
    ResearchOrchestrationPersistenceError,
    ResearchOrchestrationRepository,
)
from app.modules.research_design_service import PlannerProviderResult
from app.modules.research_start_planner import ResearchStartPlannerUnavailable


def test_flag_on_without_definition_source_uses_legacy_validated_draft_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Current contract: legacy/local submit has no DefinitionSource authority and
    # intentionally retains the validated draft path, so capability resolution is
    # part of that path rather than a forbidden call.
    calls: list[str] = []
    real_resolve = research_start_task.resolve_creation_plan_capabilities

    def recording_capability_resolution(plan: object, **kwargs: object) -> object:
        calls.append("capability")
        return real_resolve(plan, **kwargs)

    monkeypatch.setattr(settings, "research_orchestration_v1", True)
    monkeypatch.setattr(settings, "persistence_backend", "sqlite")
    monkeypatch.setattr(
        research_start_task,
        "resolve_creation_plan_capabilities",
        recording_capability_resolution,
    )

    task = research_start_task.create_research_start_draft_task(
        goal_summary="Create a bounded evidence summary design.",
        identity={
            "ownerId": "owner_test",
            "workspaceId": "workspace_test",
            "createdBy": "actor_test",
        },
    )

    assert calls == ["capability"]
    assert task["status"] == "draft"
    assert task["creationPlan"]


def test_flag_on_uses_single_design_service_creation_path(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    inserted: dict = {}

    class Source:
        def snapshot_ref(self):
            return {"schemaVersion": 1, "contentHash": "sha256:" + "c" * 64}

    class InsertRepository:
        def insert_task(self, task, *, automatic=False):
            inserted.update(task)
            calls.append("insert")

        def get_task(self, _task_id):
            return dict(inserted)

    class OrchestrationRepository:
        def load(self, **_kwargs):
            raise research_start_task.ResearchTaskNotFoundError("not found")

    class FakeDesignService:
        def __init__(self, *, repository, definition_source):
            assert definition_source is source
            calls.append("service")

        def plan(self, **kwargs):
            calls.append("plan")
            assert kwargs["provider"] is provider
            contract = {
                alias: inserted[alias]
                for alias in {
                    field.alias or name
                    for name, field in ResearchTaskV12.model_fields.items()
                }
            }
            return ResearchTaskV12.model_validate(contract)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("flag-on cannot resolve capability, Agent, or WorkUnit authority")

    source = Source()

    def provider(_key: str) -> PlannerProviderResult:
        raise AssertionError("the fake service owns provider invocation")

    monkeypatch.setattr(settings, "research_orchestration_v1", True)
    monkeypatch.setattr(settings, "persistence_backend", "postgres")
    monkeypatch.setattr(research_start_task, "TaskRepository", InsertRepository)
    monkeypatch.setattr(research_start_task, "ResearchStartDisplayRepository", InsertRepository)
    monkeypatch.setattr(research_start_task, "ResearchOrchestrationRepository", OrchestrationRepository)
    monkeypatch.setattr(research_start_task, "ResearchDesignService", FakeDesignService)
    monkeypatch.setattr(research_start_task, "resolve_creation_plan_capabilities", forbidden)
    monkeypatch.setattr(research_start_task, "ensure_orchestrator_decision", forbidden)
    task = research_start_task.create_research_start_draft_task(
        goal_summary="Produce a bounded evidence summary design.",
        identity={"ownerId": "owner", "workspaceId": "workspace", "createdBy": "actor"},
        definition_source=source,
        planner_provider=provider,
    )

    assert calls == ["insert", "service", "plan"]
    assert "creationPlan" not in task
    assert task["executionDecisions"] == []
    assert task["workUnits"] == []
    assert task["capabilityCalls"] == []
    assert task["artifacts"] == []


def test_ensure_research_design_task_persists_initial_canonical_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inserted: dict = {}

    class Source:
        def snapshot_ref(self):
            return {"schemaVersion": 1, "contentHash": "sha256:" + "d" * 64}

    class MissingRepository:
        def load(self, **_kwargs):
            raise research_start_task.ResearchTaskNotFoundError("not found")

    class InsertRepository:
        def insert_task(self, task, *, automatic=False):
            inserted.update(task)

    monkeypatch.setattr(settings, "research_orchestration_v1", True)
    monkeypatch.setattr(settings, "persistence_backend", "postgres")
    monkeypatch.setattr(research_start_task, "ResearchOrchestrationRepository", MissingRepository)
    monkeypatch.setattr(research_start_task, "TaskRepository", InsertRepository)
    monkeypatch.setattr(research_start_task, "ResearchStartDisplayRepository", InsertRepository)

    task = research_start_task.ensure_research_design_task(
        task_id="task_visible_before_planner",
        goal_summary="Define a bounded research question.",
        title=None,
        identity={"ownerId": "owner", "workspaceId": "workspace", "createdBy": "actor"},
        definition_source=Source(),
        skill_pack="opentrajectory",
        workflow_admission={
            "workflowId": "opentrajectory_research_workflow",
            "skillPack": "opentrajectory",
            "decisionReason": "explicit_opentrajectory_workflow_admission",
            "routeType": "workflow_planning",
        },
    )

    assert task.revision == 0
    assert task.progress_projection is not None
    assert task.progress_projection["revision"] == 0
    assert task.progress_projection["taskProgress"]["status"] == "draft"
    assert inserted["title"] == "新研究"
    assert inserted["taskId"] == "task_visible_before_planner"
    assert inserted["skillPack"] == "opentrajectory"
    assert inserted["workflowAdmission"] == {
        "workflowId": "opentrajectory_research_workflow",
        "skillPack": "opentrajectory",
        "decisionReason": "explicit_opentrajectory_workflow_admission",
        "routeType": "workflow_planning",
    }
    assert inserted["progressProjection"] == task.progress_projection


def test_ensure_research_design_task_attaches_llm_workflow_to_prereserved_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    existing = object()
    attached: dict[str, object] = {}

    class ExistingRepository:
        def load(self, **_kwargs):
            return existing

        def attach_pending_workflow_admission(self, **kwargs):
            attached.update(kwargs)
            return "attached-task"

    monkeypatch.setattr(research_start_task, "ResearchOrchestrationRepository", ExistingRepository)

    result = research_start_task.ensure_research_design_task(
        task_id="task_reserved_before_router",
        goal_summary="Compare two retrieval methods.",
        title=None,
        identity={"ownerId": "owner", "workspaceId": "workspace", "createdBy": "actor"},
        definition_source=object(),
        workflow_admission={
            "workflowId": "opentrajectory_research_workflow",
            "skillPack": "opentrajectory",
            "decisionReason": "llm_router",
            "routeType": "workflow_planning",
        },
    )

    assert result == "attached-task"
    assert attached["task_id"] == "task_reserved_before_router"
    assert attached["workflow_admission"] == {
        "workflowId": "opentrajectory_research_workflow",
        "skillPack": "opentrajectory",
        "decisionReason": "llm_router",
        "routeType": "workflow_planning",
    }


def _admission_repository_for_existing_payload(raw_payload: dict):
    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class Connection:
        def cursor(self):
            return Cursor()

    class Transaction:
        def __enter__(self):
            return Connection()

        def __exit__(self, *_args):
            return False

    class Uow:
        def transaction(self):
            return Transaction()

    repository = object.__new__(ResearchOrchestrationRepository)
    repository._uow = Uow()
    repository._require_write_enabled = lambda: None
    repository._require_postgres = lambda: None
    persisted_task = object()
    repository._load_locked_task = lambda *_args, **_kwargs: (persisted_task, raw_payload)
    return repository, persisted_task


def test_pending_workflow_admission_replay_preserves_persisted_reason_and_continues() -> None:
    persisted_admission = {
        "workflowId": "workflow-a",
        "skillPack": "pack-a",
        "routeType": "workflow_planning",
        "decisionReason": "reason-a",
    }
    raw_payload = {"skillPack": "pack-a", "workflowAdmission": persisted_admission}
    repository, persisted_task = _admission_repository_for_existing_payload(raw_payload)

    replay = repository.attach_pending_workflow_admission(
        task_id="task-a",
        owner_id="owner",
        workspace_id="workspace",
        workflow_admission={**persisted_admission, "decisionReason": "reason-b"},
    )

    assert replay is persisted_task
    assert raw_payload["workflowAdmission"] == persisted_admission


@pytest.mark.parametrize(
    "changed_field, changed_value",
    [
        ("workflowId", "workflow-b"),
        ("skillPack", "pack-b"),
        ("routeType", "direct_execution"),
    ],
)
def test_pending_workflow_admission_replay_rejects_canonical_identity_change(
    changed_field: str,
    changed_value: str,
) -> None:
    persisted_admission = {
        "workflowId": "workflow-a",
        "skillPack": "pack-a",
        "routeType": "workflow_planning",
        "decisionReason": "reason-a",
    }
    repository, _persisted_task = _admission_repository_for_existing_payload(
        {"skillPack": "pack-a", "workflowAdmission": persisted_admission}
    )

    with pytest.raises(
        ResearchOrchestrationPersistenceError,
        match="workflow_admission_lineage_conflict",
    ):
        repository.attach_pending_workflow_admission(
            task_id="task-a",
            owner_id="owner",
            workspace_id="workspace",
            workflow_admission={**persisted_admission, changed_field: changed_value},
        )
