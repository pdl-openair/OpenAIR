"""Safe, human-facing collaboration projection for one task."""

import logging

from fastapi import APIRouter, Depends, HTTPException

from app.auth import RequestIdentity, ensure_task_in_scope, resolve_request_identity
from app.config import settings
from app.models.schemas import ExecutionGateDecisionIn, ExecutionGateDecisionOut
from app.modules.collaboration_lifecycle import project_research_task_lifecycle
from app.modules.collaboration_repository import CollaborationRepository
from app.modules.artifact_repository import ArtifactRepository
from app.modules.canonical_collaboration import (
    CanonicalCollaborationAuthorityMismatch,
    project_canonical_collaboration,
)
from app.modules.durable_execution import DurableExecutionError
from app.modules.research_orchestration_repository import (
    ResearchOrchestrationRepository,
    ResearchOrchestrationPersistenceError,
    ResearchTaskNotFoundError,
)
from app.modules.task_repository import TaskRepository
from app.modules.workbench_user_actions import project_workbench_user_actions


_log = logging.getLogger(__name__)

router = APIRouter(prefix="/tasks", tags=["collaboration"])


def _visible_artifact_ids_for_collaboration(
    *, task_id: str, owner_id: str, workspace_id: str
) -> frozenset[str]:
    """Read Artifact membership from the public catalog authority, fail closed."""

    try:
        repository = ArtifactRepository()
        rows, total = repository.list_by_scope(
            owner_id,
            workspace_id,
            source_task_id=task_id,
            page=1,
            page_size=200,
        )
        page = 2
        while len(rows) < total:
            next_rows, _ = repository.list_by_scope(
                owner_id,
                workspace_id,
                source_task_id=task_id,
                page=page,
                page_size=200,
            )
            if not next_rows:
                break
            rows.extend(next_rows)
            page += 1
    except Exception:
        _log.warning(
            "visible Artifact projection unavailable for collaboration task %s",
            task_id,
            exc_info=True,
        )
        return frozenset()
    return frozenset(
        artifact_id
        for row in rows
        if isinstance(row, dict)
        if (artifact_id := str(row.get("id") or row.get("artifactId") or "").strip())
    )


@router.get("/{task_id}/collaboration/canonical")
def get_canonical_task_collaboration(
    task_id: str,
    identity: RequestIdentity = Depends(resolve_request_identity),
) -> dict:
    if settings.persistence_backend != "postgres" or not settings.research_orchestration_v1:
        raise HTTPException(status_code=503, detail="canonical_collaboration_requires_research_orchestration")
    try:
        task = ResearchOrchestrationRepository().load(
            task_id=task_id,
            owner_id=identity["ownerId"],
            workspace_id=identity["workspaceId"],
        )
    except ResearchTaskNotFoundError as exc:
        raise HTTPException(status_code=404, detail="task not found") from exc
    except ResearchOrchestrationPersistenceError:
        # Task hasn't reached the v1.2 execution aggregate yet (pre-planning,
        # legacy, or cloned shape — e.g. missing executionDecisions). Canonical
        # collaboration requires the orchestration aggregate; surface a graceful
        # 503 so the workbench shows planning/no-spin instead of a 500 crash.
        raise HTTPException(
            status_code=503,
            detail="canonical_collaboration_requires_research_orchestration",
        )
    control_payload = ensure_task_in_scope(TaskRepository().get_task(task_id), identity)
    if control_payload.get("revision") != task.revision:
        raise HTTPException(status_code=409, detail="durable_collaboration_revision_mismatch")
    action_authority = project_workbench_user_actions(
        task_payload={
            **task.model_dump(mode="json", by_alias=True),
            "stopReceipt": control_payload.get("stopReceipt"),
        },
        owner_id=identity["ownerId"],
        workspace_id=identity["workspaceId"],
        actor_id=identity["createdBy"],
    )
    try:
        return project_canonical_collaboration(
            task,
            action_authority=action_authority,
            visible_artifact_ids=_visible_artifact_ids_for_collaboration(
                task_id=task.task_id,
                owner_id=identity["ownerId"],
                workspace_id=identity["workspaceId"],
            ),
        )
    except CanonicalCollaborationAuthorityMismatch as exc:
        # Permission and control facts must belong to the same canonical revision.
        # A changed revision is refreshed, never re-stamped into authorization.
        raise HTTPException(status_code=409, detail="durable_collaboration_revision_mismatch") from exc


@router.get("/{task_id}/user-actions")
def get_workbench_user_actions(
    task_id: str,
    identity: RequestIdentity = Depends(resolve_request_identity),
) -> dict:
    if settings.persistence_backend != "postgres":
        raise HTTPException(status_code=503, detail="durable_execution_requires_postgres")
    task = ensure_task_in_scope(TaskRepository().get_task(task_id), identity)
    return project_workbench_user_actions(
        task_payload=task,
        owner_id=identity["ownerId"],
        workspace_id=identity["workspaceId"],
        actor_id=identity["createdBy"],
    )


@router.get("/{task_id}/collaboration")
def get_task_collaboration(
    task_id: str,
    identity: RequestIdentity = Depends(resolve_request_identity),
) -> dict:
    if settings.persistence_backend != "postgres":
        raise HTTPException(status_code=503, detail="durable_execution_requires_postgres")
    try:
        if settings.research_orchestration_v1:
            task_repository = TaskRepository()
            orchestration_repository = ResearchOrchestrationRepository()
            is_research_v12_task = False
            projection: dict | None = None
            for _attempt in range(3):
                current_task = ensure_task_in_scope(task_repository.get_task(task_id), identity)
                # This is the same authority condition used by task SSE.  A v1.2
                # aggregate must never be mixed with the legacy run projection.
                is_research_v12_task = (
                    isinstance(current_task.get("researchDesignVersions"), list)
                    and isinstance(current_task.get("registrySnapshotRef"), dict)
                    and isinstance(current_task.get("executionPolicyVersion"), str)
                )
                if not is_research_v12_task:
                    break
                aggregate = orchestration_repository.load(
                    task_id=task_id,
                    owner_id=identity["ownerId"],
                    workspace_id=identity["workspaceId"],
                )
                projection = project_research_task_lifecycle(aggregate)
                if projection.get("revision") == current_task.get("revision"):
                    return projection
            if is_research_v12_task and projection is not None:
                # Revision drift persisted across retries — a write is in flight.
                # The lifecycle projection is a derived read model (already
                # recomputed each attempt); serve best-available and let the
                # client/SSE self-correct rather than hard-failing (a 409 here
                # surfaces as "进度暂不可确认" in the workbench).
                _log.warning(
                    "collaboration revision drift for task %s; serving best-available projection",
                    task_id,
                )
                return projection
        projection = CollaborationRepository().list_projection(
            task_id=task_id,
            owner_id=identity["ownerId"],
            workspace_id=identity["workspaceId"],
        )
    except ResearchTaskNotFoundError as exc:
        raise HTTPException(status_code=404, detail="task not found") from exc
    except DurableExecutionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return projection


@router.post(
    "/{task_id}/collaboration/decisions/{gate_id}",
    response_model=ExecutionGateDecisionOut,
)
def decide_task_collaboration_gate(
    task_id: str,
    gate_id: str,
    body: ExecutionGateDecisionIn,
    identity: RequestIdentity = Depends(resolve_request_identity),
) -> ExecutionGateDecisionOut:
    if settings.persistence_backend != "postgres":
        raise HTTPException(status_code=503, detail="durable_execution_requires_postgres")
    try:
        repository = CollaborationRepository()
        gate_kind = repository.execution_gate_kind(
            task_id=task_id, gate_id=gate_id, owner_id=identity["ownerId"], workspace_id=identity["workspaceId"],
        )
        decision = (
            repository.decide_execution_policy_gate(
                task_id=task_id, gate_id=gate_id, owner_id=identity["ownerId"],
                workspace_id=identity["workspaceId"], decided_by=identity["createdBy"],
                action=body.action, note=body.note,
            )
            if gate_kind == "execution_policy"
            else repository.decide_artifact_review_gate(
                task_id=task_id, gate_id=gate_id, owner_id=identity["ownerId"],
                workspace_id=identity["workspaceId"], decided_by=identity["createdBy"],
                action=body.action, note=body.note,
            )
        )
    except DurableExecutionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return ExecutionGateDecisionOut.model_validate(decision)
