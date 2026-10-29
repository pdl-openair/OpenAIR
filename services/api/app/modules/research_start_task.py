"""Task creation helpers for the research-start mainline."""

from __future__ import annotations

import logging
from typing import Any, Mapping
from uuid import uuid4

from psycopg import errors

from app.auth import RequestIdentity
from app.config import settings
from app.models.research_orchestration import ResearchTaskV12
from app.modules.agent_orchestrator import ensure_orchestrator_decision
from app.modules.artifact_repository import ArtifactRepository
from app.modules.capability_resolver import CapabilityResolutionError, resolve_creation_plan_capabilities
from app.modules.research_design_service import (
    DefinitionSource,
    DefinitionSourceUnavailableError,
    PlannerProvider,
    ResearchDesignService,
    new_research_task,
)
from app.modules.research_orchestration_repository import (
    ResearchOrchestrationRepository,
    ResearchTaskNotFoundError,
)
from app.modules.research_progress_projection import project_task_progress
from app.modules.task_repository import TaskRepository
from app.modules.research_start_display_context import ResearchStartDisplayRepository
from app.modules.research_start_planner import (
    ResearchStartPlan,
    ResearchStartPlannerError,
    ResearchStartPlannerUnavailable,
    _deterministic_research_start_plan,
    hydrate_research_start_plan,
)
from app.modules.task_draft_builder import build_research_start_draft, derive_research_title
from app.modules.task_store import store

_log = logging.getLogger(__name__)


def _research_start_draft_artifact(
    *,
    task: dict[str, Any],
    draft: Any,
    artifact_id: str | None = None,
) -> dict[str, Any]:
    """Build the setup draft as part of the initial PostgreSQL task payload.

    PostgreSQL deliberately rejects TaskStore mutations.  A research-start
    draft therefore has to be complete before its task projection is inserted.
    """
    return {
        "id": artifact_id or f"artifact_{uuid4().hex[:12]}",
        "taskId": task["taskId"],
        "sourceTaskId": task["taskId"],
        "ownerId": task["ownerId"],
        "workspaceId": task["workspaceId"],
        "createdBy": task["createdBy"],
        "kind": "research_start_draft",
        "origin": "generated",
        "title": "研究起点草案",
        "summary": f"已根据输入识别：{'、'.join(draft.keywords[:4])}；请确认计划或补充材料。",
        "createdAt": task["createdAt"],
        "path": None,
        "format": "markdown",
        "relatedStage": "problem_modeling",
        "stage": "problem_modeling",
        "relatedSkillId": "research_start_draft",
        "subStageInstanceId": None,
        "substage": None,
        "sourceCapabilityCallId": None,
        "evidenceRefs": [],
        "artifactState": "needs_input" if draft.missing_info else "needs_review",
        "artifactType": "research_start_draft",
        "storageType": "inline",
        "inlineContent": draft.markdown,
        "structuredResearchStart": draft.structured,
        "researchStartConfirmation": draft.structured.get("user_confirmation_status"),
        "missingInputs": list(draft.missing_info),
        "nonCitableBoundaries": [draft.boundary],
        "downstreamInput": {
            "status": "blocked_by_confirmation",
            "inherits": [],
            "missingMaterials": list(draft.missing_info),
            "nonCitableBoundaries": [draft.boundary],
        },
    }


def _attach_research_start_draft(
    *,
    task: dict[str, Any],
    draft: Any,
) -> dict[str, Any]:
    """Complete the draft payload without using TaskStore mutation methods."""
    artifact = _research_start_draft_artifact(task=task, draft=draft)
    task["artifacts"] = [artifact]
    for detail in task.get("stageDetails") or []:
        if detail.get("stageKey") != "problem_modeling":
            continue
        detail.update(
            {
                "status": "not_started",
                "summary": f"已理解研究问题：{draft.goal}",
                "risks": "尚未读取外部论文、数据或代码；当前草案只能用于确认计划，不能作为最终科研结论。",
                "nextSteps": draft.next_action,
                "artifactRefs": [artifact["id"]],
            }
        )
        break
    return artifact


def build_research_start_draft_task_payload(
    *,
    task_id: str,
    artifact_id: str,
    owner_id: str,
    workspace_id: str,
    created_by: str,
    goal_summary: str | None,
    planner_plan_override: Any,
    title: str | None = None,
    skill_pack: str | None = None,
    planning_selection: object | None = None,
    source_page: str = "home",
    selected_hint: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Build the legacy/deterministic reserved task payload without persistence.

    Feature-flag routing is owned by the caller. Keeping this builder pure is
    required for persisted Planner-result replay and validation-only callers.
    """
    if not all(isinstance(value, str) and value for value in (task_id, artifact_id, owner_id, workspace_id, created_by)):
        raise ValueError("reserved task and artifact identity must be non-empty")
    if planner_plan_override is None:
        raise ValueError("planner_plan_override is required")
    if isinstance(planner_plan_override, ResearchStartPlan):
        planner_plan = planner_plan_override
    elif isinstance(planner_plan_override, Mapping):
        planner_plan = hydrate_research_start_plan(planner_plan_override)
    else:
        raise ResearchStartPlannerError(
            "planner_plan_override must be a ResearchStartPlan or serialized mapping"
        )
    draft = build_research_start_draft(
        goal_summary,
        title_input=title,
        skill_pack=skill_pack,
        planning_selection=planning_selection,
        source_page=source_page,
        selected_hint=selected_hint,
        planner_plan_override=planner_plan,
    )
    task = store.create_task(
        {
            "title": draft.title,
            "goalSummary": draft.goal,
            "domain": "research_task",
            "skillPack": draft.plan.get("skillPack") or skill_pack or "sdr",
            "currentStage": "problem_modeling",
            "status": "draft",
            "creationStatus": "awaiting_confirmation",
            "resultSource": "none",
            "stages": [
                "problem_modeling",
                "solution_design",
                "execution_validation",
                "insight_generation",
            ],
            "ownerId": owner_id,
            "workspaceId": workspace_id,
            "createdBy": created_by,
        },
        persist=False,
    )
    task["taskId"] = task_id
    task["creationPlan"] = draft.plan
    artifact = _research_start_draft_artifact(task=task, draft=draft, artifact_id=artifact_id)
    task["artifacts"] = [artifact]
    for detail in task.get("stageDetails") or []:
        if detail.get("stageKey") == "problem_modeling":
            detail["artifactRefs"] = [artifact_id]
            detail["status"] = "not_started"
            break
    return {"task": task, "artifact": artifact}


def create_research_start_draft_task(
    *,
    goal_summary: str | None,
    title: str | None = None,
    skill_pack: str | None = None,
    planning_selection: object | None = None,
    source_page: str = "home",
    selected_hint: str | None = None,
    planner_plan_override: Any = None,
    identity: RequestIdentity,
    definition_source: DefinitionSource | None = None,
    planner_provider: PlannerProvider | None = None,
) -> dict[str, Any]:
    # The durable worker supplies a trusted DefinitionSource when materializing
    # the canonical ResearchDesign. Legacy/local submit flows intentionally do
    # not have that authority and must retain the validated draft path; routing
    # them into the v1-only constructor makes clarification and agent-selected
    # drafts fail before materialization.
    if (
        settings.research_orchestration_v1
        and planner_plan_override is None
        and definition_source is not None
    ):
        return _create_research_design_task(
            goal_summary=goal_summary,
            title=title,
            identity=identity,
            definition_source=definition_source,
            planner_provider=planner_provider,
        )
    planning_reason = getattr(planning_selection, "reason", None)
    clarification_missing_info = (
        ["研究对象", "已有材料", "想得到的结果"]
        if planning_reason == "contract_confirmed_clarification_draft"
        else None
    )
    if planning_reason == "contract_confirmed_clarification_draft" and planner_plan_override is None:
        planner_plan_override = _deterministic_research_start_plan(
            raw_input=goal_summary or title or "待补充研究问题",
            planning_selection=planning_selection,
            selected_hint=selected_hint,
        )
    draft = build_research_start_draft(
        goal_summary,
        title_input=title,
        skill_pack=skill_pack,
        planning_selection=planning_selection,
        source_page=source_page,
        selected_hint=selected_hint,
        planner_plan_override=planner_plan_override,
        missing_info_override=clarification_missing_info,
    )
    resolved_pack = draft.plan.get("skillPack")
    if skill_pack and skill_pack != resolved_pack:
        _log.warning(
            "create_research_start_draft_task: unknown skillPack=%r fell back to %r; writing back",
            skill_pack,
            resolved_pack,
        )
    try:
        resolved_plan = resolve_creation_plan_capabilities(
            draft.plan,
            skill_pack=str(draft.plan.get("skillPack") or resolved_pack or "sdr"),
        )
        creation_plan = ensure_orchestrator_decision(
            resolved_plan,
            user_goal=draft.goal,
            selected_hint=selected_hint,
            task_status="draft",
        )
    except CapabilityResolutionError as exc:
        raise ResearchStartPlannerError(
            "当前草案缺少可执行的研究能力，请调整目标或补充可用能力后重试。"
        ) from exc

    task = store.create_task(
        {
            "title": draft.title,
            "goalSummary": draft.goal,
            "domain": "research_task",
            "skillPack": resolved_pack,
            "currentStage": "problem_modeling",
            "status": "draft",
            "creationStatus": "awaiting_confirmation",
            "resultSource": "none",
            "creationPlan": creation_plan,
            "ownerId": identity["ownerId"],
            "workspaceId": identity["workspaceId"],
            "createdBy": identity["createdBy"],
        },
        persist=settings.persistence_backend != "postgres",
    )
    if settings.persistence_backend == "postgres":
        artifact = _attach_research_start_draft(task=task, draft=draft)
        task_repository = TaskRepository()
        inserted = False
        try:
            task_repository.insert_task(task)
            inserted = True
            ArtifactRepository().upsert_from_dict(task["taskId"], artifact, task.get("title"))
        except Exception:
            # Do not leave a task that cannot enter the Workbench draft state.
            try:
                if inserted:
                    task_repository.delete_task(task["taskId"])
            except Exception:
                _log.exception("research-start draft compensation failed for %s", task["taskId"])
            raise
        return task
    task_id = task["taskId"]
    artifact = store.add_artifact(
        task_id,
        kind="research_start_draft",
        title="研究起点草案",
        summary=f"已根据输入识别：{'、'.join(draft.keywords[:4])}；请确认计划或补充材料。",
        format="markdown",
        related_stage="problem_modeling",
        related_skill_id="research_start_draft",
        storage_type="inline",
        inline_content=draft.markdown,
        extra_fields={
            "artifactState": "needs_input" if draft.missing_info else "needs_review",
            "structuredResearchStart": draft.structured,
            "researchStartConfirmation": draft.structured.get("user_confirmation_status"),
            "missingInputs": list(draft.missing_info),
            "nonCitableBoundaries": [draft.boundary],
            "downstreamInput": {
                "status": "blocked_by_confirmation",
                "inherits": [],
                "missingMaterials": list(draft.missing_info),
                "nonCitableBoundaries": [draft.boundary],
            },
        },
    )
    return store.save_stage_result(
        task_id,
        "problem_modeling",
        {
            "status": "not_started",
            "summary": f"已理解研究问题：{draft.goal}",
            "risks": "尚未读取外部论文、数据或代码；当前草案只能用于确认计划，不能作为最终科研结论。",
            "nextSteps": draft.next_action,
            "artifactRefs": [artifact["id"]] if artifact else [],
        },
    ) or store.get_task(task_id) or task


def _create_research_design_task(
    *,
    goal_summary: str | None,
    title: str | None,
    identity: RequestIdentity,
    definition_source: DefinitionSource | None,
    planner_provider: PlannerProvider | None,
) -> dict[str, Any]:
    """Create the sole flag-on Task row, then persist the Planner ledger via CAS."""
    if definition_source is None:
        raise ResearchStartPlannerUnavailable("trusted DefinitionSource is required")
    if settings.persistence_backend != "postgres":
        raise ResearchStartPlannerUnavailable(
            "ResearchDesign creation requires PostgreSQL; json/file backends fail closed"
        )
    if planner_provider is None:
        raise ResearchStartPlannerUnavailable("an explicit v1.2 Planner provider is required")

    aggregate = ensure_research_design_task(
        task_id=f"task_{uuid4().hex[:12]}",
        goal_summary=goal_summary,
        title=title,
        identity=identity,
        definition_source=definition_source,
    )
    service = ResearchDesignService(
        repository=ResearchOrchestrationRepository(),
        definition_source=definition_source,
    )
    planned = service.plan(
        task_id=aggregate.task_id,
        owner_id=identity["ownerId"],
        workspace_id=identity["workspaceId"],
        request_id=f"create:{aggregate.task_id}",
        expected_task_revision=aggregate.revision,
        provider=planner_provider,
        user_input=goal_summary,
    )
    task = TaskRepository().get_task(aggregate.task_id) or {}
    task.update(planned.model_dump(mode="json", by_alias=True))
    return task


def ensure_research_design_task(
    *,
    task_id: str,
    goal_summary: str | None,
    title: str | None,
    identity: RequestIdentity,
    definition_source: DefinitionSource,
    skill_pack: str | None = None,
    workflow_admission: Mapping[str, Any] | None = None,
) -> ResearchTaskV12:
    """Create the reserved v1.2 aggregate once, before any Planner transport."""
    orchestration_repository = ResearchOrchestrationRepository()
    try:
        existing = orchestration_repository.load(
            task_id=task_id,
            owner_id=identity["ownerId"],
            workspace_id=identity["workspaceId"],
        )
        if workflow_admission:
            return orchestration_repository.attach_pending_workflow_admission(
                task_id=task_id,
                owner_id=identity["ownerId"],
                workspace_id=identity["workspaceId"],
                workflow_admission=workflow_admission,
            )
        return existing
    except ResearchTaskNotFoundError:
        pass

    try:
        aggregate = new_research_task(
            task_id=task_id,
            registry_snapshot_ref=definition_source.snapshot_ref(),
        )
    except Exception as exc:
        raise DefinitionSourceUnavailableError("DefinitionSource snapshot is invalid") from exc

    goal = (goal_summary or title or "待确认研究任务").strip() or "待确认研究任务"
    generated_title = (
        title.strip()
        if isinstance(title, str) and title.strip()
        else "新研究"
    )
    task = store.create_task(
        {
            "title": generated_title,
            "goalSummary": goal,
            "domain": "research_task",
            **({"skillPack": skill_pack} if skill_pack else {}),
            "currentStage": "problem_modeling",
            "status": "draft",
            "creationStatus": "awaiting_confirmation",
            "resultSource": "none",
            "ownerId": identity["ownerId"],
            "workspaceId": identity["workspaceId"],
            "createdBy": identity["createdBy"],
        },
        persist=False,
    )
    task["taskId"] = task_id
    if workflow_admission:
        task["workflowAdmission"] = dict(workflow_admission)
    task.pop("creationPlan", None)
    projected = project_task_progress(aggregate, revision=aggregate.revision)
    task.update(projected.model_dump(mode="json", by_alias=True))
    try:
        ResearchStartDisplayRepository().insert_task(
            task, automatic=not bool(title and title.strip())
        )
        return projected
    except errors.UniqueViolation:
        # Concurrent replay of the same accepted request may race after the
        # reservation lock is released. The reserved identity is immutable;
        # load the winner rather than failing or creating another Task.
        return orchestration_repository.load(
            task_id=task_id,
            owner_id=identity["ownerId"],
            workspace_id=identity["workspaceId"],
        )
