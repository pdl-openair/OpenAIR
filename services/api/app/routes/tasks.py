import asyncio
import logging
from copy import deepcopy
from typing import Annotated, Any, Literal, Mapping
from uuid import uuid4

import psycopg
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from app.auth import (
    RequestIdentity,
    ensure_artifact_in_scope,
    ensure_task_in_scope,
    resolve_media_identity,
    resolve_request_identity,
)
from app.execution.background_execution import cancel_task_background_executions
from app.models.schemas import (
    AgentResearchDesignRevisionIn,
    ArtifactContentOut,
    CatalogArtifactItem,
    CatalogArtifactsSummary,
    CatalogArtifactOut,
    CloneTaskIn,
    CreateTaskIn,
    PatchTaskExecutionModeIn,
    PatchTaskStatusIn,
    RenameTaskIn,
    StageResultIn,
    StageCatalogOut,
    TaskListOut,
    TaskTitleOptionOut,
    TaskTitleOptionsOut,
    TaskExecutionPolicyOut,
    TaskOut,
    UpdateTaskPlanIn,
)
from app.modules.task_scope_repository import read_task_scope
from app.modules.artifact_content import (
    build_file_tree,
    has_valid_immutable_content_identity,
    load_artifact_binary,
    load_artifact_content,
    load_artifact_sibling_bytes,
    resolve_artifact_path,
    resolve_artifact_sibling,
    resolve_artifact_tree_root,
)
from app.modules.task_event_stream import (
    encode_research_sse_event as _encode_research_sse_event,
    open_task_event_stream,
    read_task_research_stream_state as _read_task_research_stream_state,
    utc_now as _utc_now,
)
from app.modules.task_events import (
    TaskResearchEventRepository,
    project_research_activity_events,
    publish_task_event,
)
from app.modules.agent_orchestrator import ensure_orchestrator_decision, transition_orchestrator_state
from app.modules.artifact_repository import ArtifactRepository
from app.modules.historical_research_record_projection import historical_record_display_projection
from app.modules.artifact_catalog import is_artifact_user_visible, reuse_artifact
from app.modules.research_start_planner import (
    _deterministic_research_start_plan,
    execute_planner_attempt,
    prepare_planner_attempt,
)
from app.modules.research_design_service import (
    ConfirmationIdentityConflictError,
    ResearchDesignConfirmationError,
    ResearchDesignRevisionSaveUnavailable,
    ResearchDesignRevisionUserError,
    ResearchDesignService,
    ResearchDesignServiceError,
    classify_research_design_revision_failure,
)
from app.modules.research_orchestration_repository import (
    DispatchIdentityConflictError,
    ExternalRecoveryConflictError,
    ExternalRecoveryStateError,
    PersistenceBackendUnsupportedError,
    ResearchOrchestrationRepository,
    ResearchTaskNotFoundError,
    SelectionRequestPayloadConflictError,
    TaskRevisionConflictError,
)
from app.modules.research_dispatch_service import ResearchDispatchService
from app.modules.research_execution_decision_service import (
    DecisionPolicyViolation,
    ExecutionPreflightBlocked,
    RuntimeReadinessInvalid,
    SelectionRequestConflictError,
)
from app.modules.research_orchestration_resolver import (
    ResolutionRejectedError,
)
from app.models.research_external_result import (
    ExplicitRetryCommand,
)
from app.models.research_execution_start_status import (
    ResearchExecutionStartRequestStatusOut,
    ResearchExecutionStartStatusOut,
)
from app.modules.research_execution_start_status import (
    ResearchExecutionStartStatusReader,
    ResearchExecutionStartTaskNotFound,
)
from app.modules.model_job_queue import (
    ModelJobContinueDenied,
    ModelJobConflict,
    ModelJobQueueRepository,
    ModelJobResourceNotFound,
    ModelJobStartDenied,
)
from app.modules.research_external_recovery_service import (
    ExternalRecoveryConflict,
    ExternalRecoveryNotAllowed,
    ExternalRecoveryService,
    production_external_recovery_service,
)
from app.modules.research_start_task import create_research_start_draft_task
from app.modules.production_definition_source import ProductionDefinitionSource
from app.modules.task_store import build_stage_catalog, normalize_task_result_source, store
from app.modules.task_public_projection import (
    _filter_public_artifact_surface,
    _public_timestamp,
    project_public_task as _project_public_task,
    project_public_task_read_only as _project_public_task_read_only,
)
from app.modules.token_ledger_repository import get_token_ledger_repository
from app.config import settings
from app.modules.durable_execution import DurableExecutionError, DurableExecutionRepository, serialize_run
from app.execution.durable_orchestrator_bridge import FrozenPlanError, precompile_creation_plan
from app.modules.collaboration_repository import CollaborationRepository
from app.modules.collaboration_lifecycle import (
    build_collaboration_lifecycle_events,
)
from app.modules.capability_resolver import CapabilityResolutionError, resolve_creation_plan_capabilities
from app.modules.task_repository import TaskPlanCapabilityError, TaskPlanValidationError, TaskRepository
from app.modules.task_message_repository import TaskMessageRepository
from app.modules.workbench_user_actions import project_workbench_user_actions
from app.models.research_orchestration import (
    ProjectCompletionRequirement,
    ResearchTaskV12,
    WorkflowProvenanceRef,
)
from app.models.research_orchestration import WorkflowResponsibilityCoverage
from app.modules.research_progress_projection import (
    project_task_progress,
)

_log = logging.getLogger(__name__)


# Canonical fields that are reusable when starting a new Project from an existing one.
# User confirmation and execution facts are deliberately excluded: a clone is a new
# Project and must confirm its own ResearchDesign before execution.
_V12_FORK_REUSABLE_FIELDS = (
    "planningAttempts",
    "registrySnapshotRef",
    "executionPolicyVersion",
    "executionMode",
    "pendingExecutionMode",
)

_V12_FORK_RESET_FIELDS = {
    "activeResearchDesignRef": None,
    "confirmations": [],
    "reentryExecutionInstances": [],
    "substageRuntimeStates": [],
    "executionDecisions": [],
    "workUnits": [],
    "capabilityCalls": [],
    "agentInteractionRecords": [],
    "artifacts": [],
    "scientificStates": [],
    "completionDecisions": [],
    "finalResults": [],
    "finalResultAuthority": None,
    "progressProjection": None,
    "startedAt": None,
    "finishedAt": None,
}

def _rewrite_owned_task_ids(node: Any, new_task_id: str) -> None:
    """Recursively rewrite every ``taskId`` value to ``new_task_id``.

    Safe within the v1.2 contract subtree because the aggregate invariant
    (``all aggregate child taskId values must match ResearchTaskV12.taskId``)
    binds every child taskId to the root; cross-task references live outside the
    contract (catalog ``sourceTaskId``, artifact relations) and use other keys.
    """
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "taskId" and isinstance(value, str):
                node[key] = new_task_id
            else:
                _rewrite_owned_task_ids(value, new_task_id)
    elif isinstance(node, list):
        for item in node:
            _rewrite_owned_task_ids(item, new_task_id)


def _source_design_for_clone(source: dict) -> dict | None:
    versions = source.get("researchDesignVersions")
    if not isinstance(versions, list) or not versions:
        return None

    active_ref = source.get("activeResearchDesignRef")
    if isinstance(active_ref, dict):
        active_id = active_ref.get("researchDesignId")
        active_version = active_ref.get("version")
        for design in versions:
            if (
                isinstance(design, dict)
                and design.get("researchDesignId") == active_id
                and design.get("version") == active_version
            ):
                return design

    candidates = [design for design in versions if isinstance(design, dict)]
    if not candidates:
        return None
    return max(candidates, key=lambda design: int(design.get("version") or 0))


def _draft_design_for_clone(source: dict, *, new_task_id: str) -> dict | None:
    source_design = _source_design_for_clone(source)
    if source_design is None:
        return None

    draft = deepcopy(source_design)
    _rewrite_owned_task_ids(draft, new_task_id)
    draft["version"] = 1
    draft["supersedesVersion"] = None
    draft["status"] = "draft"
    draft["confirmedAt"] = None
    draft["confirmationRef"] = None

    coverage_report = draft.get("coverageReport")
    if isinstance(coverage_report, dict):
        design_ref = coverage_report.get("designRef")
        if isinstance(design_ref, dict):
            design_ref["version"] = 1
    return draft


def _fork_v12_contract_into(source: dict, target: dict) -> bool:
    """Copy a canonical ResearchDesign into a new Project as an unconfirmed draft.

    A clone may reuse the source's plan and planning provenance, but it cannot reuse
    the source Project's user confirmation or execution history. The new Project must
    explicitly confirm its copied Design before any WorkUnit can be created.
    """

    new_task_id = str(target.get("taskId") or "").strip()
    if not new_task_id:
        return False
    draft = _draft_design_for_clone(source, new_task_id=new_task_id)
    if draft is None:
        return False

    target["researchDesignVersions"] = [draft]
    for field_name in _V12_FORK_REUSABLE_FIELDS:
        if field_name not in source:
            continue
        value = deepcopy(source[field_name])
        _rewrite_owned_task_ids(value, new_task_id)
        target[field_name] = value
    for field_name, fresh in _V12_FORK_RESET_FIELDS.items():
        target[field_name] = deepcopy(fresh)

    target["revision"] = 0
    target["status"] = "awaiting_confirmation"
    target["creationStatus"] = "awaiting_confirmation"

    try:
        from app.models.research_orchestration import ResearchTaskV12
        from app.modules.research_orchestration_repository import _extract_contract_payload
        from app.modules.research_progress_projection import project_task_progress

        aggregate = ResearchTaskV12.model_validate(_extract_contract_payload(target))
        projected = project_task_progress(aggregate, revision=0)
        target["progressProjection"] = projected.progress_projection
    except Exception:
        _log.warning(
            "fork progress projection failed for %s",
            target.get("taskId"),
            exc_info=True,
        )
    return True


router = APIRouter(prefix="/tasks", tags=["tasks"])
_draft_request_index: dict[tuple[str, str, str], str] = {}
_TASK_EVENT_HEARTBEAT_SECONDS = 8
_DURABLE_EVENT_REFRESH_SECONDS = 0.25


def _visible_artifact_ids_for_task(task: Mapping[str, Any]) -> frozenset[str]:
    """Resolve retained-result membership from the existing catalog authority."""
    return frozenset(_visible_artifact_catalog_for_task(task))


def _visible_artifact_catalog_for_task(
    task: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    """Catalog rows (title/format/relatedSkillId/…) behind the visible id set.

    The canonical Task payload keeps Artifact rows lean (identity + validator
    facts); render-facing fields live only in the openair_artifacts catalog.
    Public projection merges them back so the workbench can route formats
    (markdown/cif/png) and show authored titles.
    """

    if settings.persistence_backend != "postgres":
        return {
            artifact_id: row
            for row in task.get("artifacts") or []
            if isinstance(row, dict) and is_artifact_user_visible(row)
            if (artifact_id := str(row.get("artifactId") or row.get("id") or "").strip())
        }

    try:
        repository = ArtifactRepository()
        rows, total = repository.list_by_scope(
            str(task.get("ownerId") or ""),
            str(task.get("workspaceId") or ""),
            source_task_id=str(task.get("taskId") or ""),
            page=1,
            page_size=200,
        )
        page = 2
        while len(rows) < total:
            next_rows, _ = repository.list_by_scope(
                str(task.get("ownerId") or ""),
                str(task.get("workspaceId") or ""),
                source_task_id=str(task.get("taskId") or ""),
                page=page,
                page_size=200,
            )
            if not next_rows:
                break
            rows.extend(next_rows)
            page += 1
    except Exception:
        _log.warning(
            "visible artifact projection unavailable for task %s",
            task.get("taskId"),
            exc_info=True,
        )
        return {}
    return {
        artifact_id: row
        for row in rows
        if isinstance(row, dict)
        if (artifact_id := str(row.get("id") or row.get("artifactId") or "").strip())
    }


def project_public_task(task: dict):
    """Normalize result provenance at the final public projection boundary."""
    normalized = normalize_task_result_source(task)
    catalog = _visible_artifact_catalog_for_task(normalized)
    return _project_public_task(
        normalized,
        visible_artifact_ids=frozenset(catalog),
        catalog_artifacts=catalog,
    )


def project_public_task_read_only(task: dict):
    """Normalize one immutable GET snapshot without entering a command path."""
    normalized = normalize_task_result_source(task)
    catalog = _visible_artifact_catalog_for_task(normalized)
    return _project_public_task_read_only(
        normalized,
        visible_artifact_ids=frozenset(catalog),
        catalog_artifacts=catalog,
    )


def _read_task_scoped_artifact(
    task_id: str,
    artifact_id: str,
    identity: RequestIdentity,
) -> tuple[dict, dict]:
    """Read Task and Artifact from the same authoritative backend."""
    if settings.persistence_backend == "postgres":
        task = ensure_task_in_scope(read_task_scope(task_id), identity)
        repository = ArtifactRepository()
        artifact = repository.get_by_id(artifact_id)
        ensure_artifact_in_scope(artifact, identity)
        if artifact is None or artifact.get("sourceTaskId") != task_id:
            raise HTTPException(status_code=404, detail="artifact not found")
        reference = repository.get_storage_reference(
            artifact_id=artifact_id, reference_kind="current"
        )
        if reference is None:
            # Durable dispatch artifacts can carry storage without an auxiliary reference row.
            storage_type = str(artifact.get("storageType") or "")
            has_canonical_storage = (
                storage_type in ("local", "object")
                and bool(str(artifact.get("storageRef") or artifact.get("path") or "").strip())
                and bool(str(artifact.get("contentSha256") or "").strip())
            ) or (
                storage_type == "object"
                and has_valid_immutable_content_identity(artifact)
            )
            if not has_canonical_storage:
                artifact = {**artifact, "_canonicalStorageReferenceMissing": True}
        else:
            artifact = dict(artifact)
            storage_type = str(reference.get("storage_type") or "")
            artifact["storageType"] = storage_type
            artifact["contentSha256"] = reference.get("content_sha256")
            artifact["contentSize"] = reference.get("content_size")
            storage_ref = str(reference.get("storage_ref") or "")
            if storage_type == "external":
                artifact["externalKey"] = storage_ref
                artifact.pop("path", None)
            elif storage_type == "local":
                artifact["path"] = storage_ref
                artifact.pop("externalKey", None)
            elif storage_type == "inline" and str(artifact.get("contentSha256") or "").lower() != storage_ref.lower():
                artifact["_canonicalStorageReferenceMissing"] = True
        return task, artifact

    task = ensure_task_in_scope(store.get_task(task_id), identity)
    artifact = next((a for a in task.get("artifacts") or [] if a.get("id") == artifact_id), None)
    if not artifact:
        raise HTTPException(status_code=404, detail="artifact not found")
    return task, artifact


def _cleanup_task_objects(task_id: str) -> None:
    """Best-effort object cleanup after the authoritative delete has committed."""
    if settings.artifact_storage != "minio":
        return
    try:
        from app.modules.minio_client import list_objects, remove_object

        objects = list_objects(f"{task_id}/")
    except Exception:
        _log.warning(
            "task object cleanup listing failed taskId=%s",
            task_id,
            exc_info=True,
        )
        return
    for obj in objects:
        object_key = obj.get("key")
        if not isinstance(object_key, str) or not object_key:
            continue
        try:
            remove_object(object_key)
        except Exception:
            _log.warning(
                "task object cleanup failed taskId=%s objectKey=%s",
                task_id,
                object_key,
                exc_info=True,
            )
class ResearchStartConfirmationIn(BaseModel):
    researchGoalConfirmed: bool = False
    existingMaterialsConfirmed: bool = False
    missingMaterialsDecision: str | None = None
    nonCitableBoundariesAccepted: bool = False
    nextStepLimitationsConfirmed: bool = False
    acceptedLimitations: list[str] = Field(default_factory=list)
    notes: str | None = None


class ResearchDesignConfirmationIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    designId: str = Field(min_length=1)
    version: int = Field(ge=1)
    confirmationId: str = Field(min_length=1)
    decision: str = Field(pattern=r"^(approved|rejected|request_revision)$")
    expectedTaskRevision: int = Field(ge=0)


class ResearchDesignReadOut(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    designId: str
    version: int
    status: str
    goal: str
    projectCompletionRequirements: list[ProjectCompletionRequirement]
    workflowProvenanceRef: WorkflowProvenanceRef | None
    workflowResponsibilityCoverage: list[WorkflowResponsibilityCoverage] = Field(default_factory=list)
    coverage: dict | None
    revision: int


class ResearchExecutionStartIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    expectedTaskRevision: int = Field(ge=0)
    selectionRequestId: str = Field(min_length=1)
    productAction: Literal["initial_start", "continue_research"] = "initial_start"
    substageInstanceId: str | None = Field(default=None, min_length=1)


class ResearchExecutionStopIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    expectedTaskRevision: int = Field(ge=0)


class ResearchExecutionResumeIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    expectedTaskRevision: int = Field(ge=0)


class StopTaskReceiptOut(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    receiptId: str
    taskId: str
    status: str
    revision: int = Field(ge=0)
    suppressed: bool = True


class ResumeTaskReceiptOut(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    receiptId: str
    taskId: str
    status: str
    revision: int = Field(ge=0)
    resumed: bool = True
    resumedDispatchCount: int = Field(ge=0)
    continuationStarted: bool = False
    continuationAccepted: bool = False
    selectionRequestId: str | None = None


class ModelJobAcceptedOut(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    jobId: str
    resourceId: str
    jobKind: str
    state: str = "accepted"


class TaskTokenUsageOut(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    totalTokens: int = Field(ge=0)


def _research_design_service() -> ResearchDesignService:
    # T03 owns the production DefinitionSource wiring. Read/confirm consume only
    # already-validated persisted Design facts and therefore need no source.
    return ResearchDesignService(
        repository=ResearchOrchestrationRepository(),
        definition_source=None,
    )


def _research_execution_start_status_reader() -> ResearchExecutionStartStatusReader:
    return ResearchExecutionStartStatusReader()


def _policy_allows_confirmed_design_initial_continuation(policy: Mapping[str, object]) -> bool:
    """Apply the initial-start portion of the persisted execution policy.

    This helper is called only after ``ResearchDesignService.confirm`` has
    persisted the matching approved confirmation.  Stage confirmation gates
    progression at a stage exit, while manual step gates every progression.
    Unknown policies fail closed.
    """
    return str(policy.get("currentMode") or "") in {
        "automatic_research",
        "stage_confirmation",
    }


def _continue_confirmed_design_automatic_research(
    *,
    task: ResearchTaskV12,
    design_id: str,
    version: int,
    owner_id: str,
    workspace_id: str,
    expected_task_revision: int,
) -> None:
    """Durably enqueue initial selection after authoritative confirmation."""
    active = task.active_research_design_ref
    if (
        active is None
        or active.research_design_id != design_id
        or active.version != version
    ):
        return
    policy = CollaborationRepository().get_task_execution_policy(
        task_id=task.task_id,
        owner_id=owner_id,
        workspace_id=workspace_id,
    )
    if not _policy_allows_confirmed_design_initial_continuation(policy):
        return
    selection_request_id = f"research-design:auto-start:{design_id}:v{version}"
    ModelJobQueueRepository().enqueue(
        resource_kind="task",
        resource_id=task.task_id,
        owner_id=owner_id,
        workspace_id=workspace_id,
        job_id=selection_request_id,
        job_kind="execution_selection",
        expected_revision=expected_task_revision,
        command_payload={"requestKind": "confirmed_design"},
    )


def _canonical_execution_in_flight(task) -> bool:
    return any(
        str(getattr(decision.status, "value", decision.status)) in {"selected", "dispatched"}
        for decision in task.execution_decisions
    )


def _map_research_execution_error(exc: Exception) -> HTTPException:
    if isinstance(exc, ResearchTaskNotFoundError):
        return HTTPException(status_code=404, detail="research task not found")
    if isinstance(
        exc,
        (
            TaskRevisionConflictError,
            ModelJobConflict,
            SelectionRequestPayloadConflictError,
            SelectionRequestConflictError,
            DispatchIdentityConflictError,
        ),
    ):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, ModelJobResourceNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, ExecutionPreflightBlocked):
        status_code = 428 if exc.code in {
            "required_input_missing",
            "authorization_required",
            "budget_expansion_required",
        } else 409
        return HTTPException(status_code=status_code, detail={"code": exc.code, "facts": exc.facts})
    if isinstance(exc, (DecisionPolicyViolation, RuntimeReadinessInvalid)):
        return HTTPException(status_code=409, detail={"code": exc.code})
    if isinstance(exc, ResolutionRejectedError):
        return HTTPException(status_code=409, detail={"code": exc.code})
    if isinstance(exc, PersistenceBackendUnsupportedError):
        return HTTPException(status_code=503, detail=str(exc))
    return HTTPException(status_code=500, detail="research execution selection failed")


def _require_research_design_api() -> None:
    if not settings.research_orchestration_v1:
        raise HTTPException(status_code=404, detail="research design API is disabled")
    if settings.persistence_backend != "postgres":
        raise HTTPException(
            status_code=409,
            detail="research design API requires PostgreSQL",
        )


def _external_recovery_service() -> ExternalRecoveryService:
    return production_external_recovery_service()


def _require_external_recovery_api() -> None:
    if not settings.research_orchestration_v1:
        raise HTTPException(status_code=404, detail="external recovery API is disabled")
    if settings.persistence_backend != "postgres":
        raise HTTPException(status_code=409, detail="external recovery API requires PostgreSQL")


def _map_external_recovery_error(exc: Exception) -> HTTPException:
    if isinstance(exc, ResearchTaskNotFoundError):
        return HTTPException(status_code=404, detail="external recovery fact not found")
    if isinstance(
        exc,
        (
            ExternalRecoveryConflict,
            ExternalRecoveryConflictError,
            ExternalRecoveryNotAllowed,
            ExternalRecoveryStateError,
            TaskRevisionConflictError,
        ),
    ):
        code = str(exc) or type(exc).__name__
        _log.warning("external recovery command rejected: %s", code)
        return HTTPException(
            status_code=409,
            detail={
                "code": code,
                "userMessage": "当前执行状态已经变化，本次操作未执行。请刷新后再试。",
            },
        )
    if isinstance(exc, PersistenceBackendUnsupportedError):
        return HTTPException(status_code=409, detail="external recovery persistence unavailable")
    return HTTPException(status_code=500, detail="external recovery operation failed")


def _map_research_design_error(exc: Exception) -> HTTPException:
    if isinstance(exc, ResearchTaskNotFoundError):
        return HTTPException(status_code=404, detail="research design task not found")
    if isinstance(
        exc,
        (
            ConfirmationIdentityConflictError,
            ResearchDesignConfirmationError,
            TaskRevisionConflictError,
        ),
    ):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, PersistenceBackendUnsupportedError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, ResearchDesignServiceError):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=500, detail="research design operation failed")


def _research_start_artifact(task: dict) -> dict | None:
    for artifact in task.get("artifacts") or []:
        if artifact.get("kind") == "research_start_draft" or artifact.get("artifactType") == "research_start_draft":
            return artifact
    return None


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _legacy_research_start_structure(task: dict, artifact: dict, *, plan: dict | None = None) -> dict:
    creation_plan = plan if isinstance(plan, dict) else task.get("creationPlan") or {}
    goal = str(
        creation_plan.get("goalSummary")
        or task.get("goalSummary")
        or artifact.get("summary")
        or task.get("title")
        or "Research start"
    ).strip()
    missing_materials = _string_list(artifact.get("missingInputs")) or _string_list(creation_plan.get("clarifications"))
    boundaries = _string_list(artifact.get("nonCitableBoundaries")) or [
        "No external papers, data, or code have been reviewed; this draft is not a citable research conclusion."
    ]
    return {
        "research_goal": goal,
        "comparison_targets": [],
        "existing_materials": [f"User-provided research request: {goal}"],
        "missing_materials": missing_materials,
        "non_citable_boundaries": boundaries,
        "recommended_next_actions": ["Review and confirm the research start before proceeding."],
        "risks": ["This legacy draft was migrated without verified external research materials."],
        "user_confirmation_status": {
            "status": "unconfirmed",
            "confirmed_items": [],
            "missing_materials_decision": None,
            "limitations_accepted": False,
            "confirmed_at": None,
        },
        "accepted_limitations": [],
        "source_or_run_refs": [{"artifact_id": artifact.get("id"), "source": "legacy_draft_migration"}],
    }


def _repair_research_start_artifact(task: dict, artifact: dict, *, plan: dict | None = None) -> dict:
    if isinstance(artifact.get("structuredResearchStart"), dict):
        return artifact
    structured = _legacy_research_start_structure(task, artifact, plan=plan)
    missing_materials = _string_list(structured.get("missing_materials"))
    boundaries = _string_list(structured.get("non_citable_boundaries"))
    return {
        **artifact,
        "artifactState": "needs_input" if missing_materials else "needs_review",
        "structuredResearchStart": structured,
        "researchStartConfirmation": structured["user_confirmation_status"],
        "missingInputs": missing_materials,
        "nonCitableBoundaries": boundaries,
        "downstreamInput": {
            **(artifact.get("downstreamInput") if isinstance(artifact.get("downstreamInput"), dict) else {}),
            "status": "blocked_by_confirmation",
            "inherits": [],
            "missingMaterials": missing_materials,
            "nonCitableBoundaries": boundaries,
        },
    }


def _research_start_confirmation_status(artifact: dict | None) -> str:
    if not artifact:
        return "missing"
    confirmation = artifact.get("researchStartConfirmation")
    if isinstance(confirmation, dict):
        return str(confirmation.get("status") or "")
    structured = artifact.get("structuredResearchStart")
    if isinstance(structured, dict):
        status = structured.get("user_confirmation_status")
        if isinstance(status, dict):
            return str(status.get("status") or "")
    return ""


def _confirmed_research_start_statuses() -> set[str]:
    return {"confirmed_with_limitations", "confirmed_complete"}


def _validate_research_start_confirmation(task: dict, body: ResearchStartConfirmationIn | None) -> dict:
    artifact = _research_start_artifact(task)
    if not artifact:
        raise HTTPException(status_code=409, detail="research start draft artifact is required before confirmation")
    artifact = _repair_research_start_artifact(task, artifact)
    structured = artifact["structuredResearchStart"]
    missing_materials = [str(item) for item in structured.get("missing_materials") or [] if str(item).strip()]
    if body is None:
        raise HTTPException(status_code=409, detail="structured research start confirmation is required")
    if not body.researchGoalConfirmed:
        raise HTTPException(status_code=409, detail="research goal must be confirmed before starting")
    if not body.existingMaterialsConfirmed:
        raise HTTPException(status_code=409, detail="existing materials must be confirmed before starting")
    if not body.nonCitableBoundariesAccepted:
        raise HTTPException(status_code=409, detail="non-citable boundaries must be accepted or the task must be revised")
    if not body.nextStepLimitationsConfirmed:
        raise HTTPException(status_code=409, detail="next-step limitations must be confirmed before starting")

    decision = body.missingMaterialsDecision
    if missing_materials and decision not in {"accept_limitations", "supplement", "modify_goal", "pause"}:
        raise HTTPException(status_code=409, detail="missing materials must be handled before starting")
    if missing_materials and decision != "accept_limitations":
        status_by_decision = {
            "supplement": "blocked_for_supplement",
            "modify_goal": "revision_requested",
            "pause": "paused",
        }
        raise HTTPException(
            status_code=409,
            detail=f"research start confirmation blocked: {status_by_decision.get(decision, 'missing_materials')}",
        )

    accepted_limitations = body.acceptedLimitations or list(structured.get("non_citable_boundaries") or [])
    confirmed_items = [
        "research_goal",
        "existing_materials",
        "missing_materials",
        "non_citable_boundaries",
        "recommended_next_actions",
    ]
    confirmed_at = _utc_now()
    confirmation = {
        "status": "confirmed_with_limitations" if missing_materials else "confirmed_complete",
        "confirmed_items": confirmed_items,
        "missing_materials_decision": decision or "not_missing",
        "limitations_accepted": True,
        "confirmed_at": confirmed_at,
        "accepted_limitations": accepted_limitations,
    }
    structured = {
        **structured,
        "user_confirmation_status": confirmation,
        "accepted_limitations": accepted_limitations,
    }
    downstream_input = {
        "status": "usable_with_limitations" if missing_materials else "usable",
        "inherits": [
            "research_goal",
            "comparison_targets",
            "existing_materials",
            "missing_materials",
            "non_citable_boundaries",
            "recommended_next_actions",
            "risks",
            "source_or_run_refs",
        ],
        "missingMaterials": missing_materials,
        "nonCitableBoundaries": list(structured.get("non_citable_boundaries") or []),
        "requiresVerification": bool(missing_materials or structured.get("non_citable_boundaries")),
    }
    updated_artifact = {
        **artifact,
        "artifactState": "usable",
        "structuredResearchStart": structured,
        "researchStartConfirmation": confirmation,
        "acceptedLimitations": accepted_limitations,
        "downstreamInput": downstream_input,
        "humanGate": {
            "canConfirmPass": False,
            "canUseDownstream": True,
            "requiresHumanReview": False,
            "requiresScopeExpansionConfirmation": False,
            "allowedActions": ["use_downstream"],
            "primaryAction": "use_downstream",
            "blockedReason": None,
        },
    }
    return updated_artifact


def _ensure_research_start_confirmed_for_advance(task: dict) -> None:
    artifact = _research_start_artifact(task)
    if not artifact:
        return
    status = _research_start_confirmation_status(artifact)
    if status not in _confirmed_research_start_statuses():
        raise HTTPException(status_code=409, detail="research start structured confirmation is required before advancing")


def _has_reviewable_execution_output(task: dict, substage_key: str) -> bool:
    current_stage = task.get("currentStage")
    current_detail = next(
        (detail for detail in task.get("stageDetails") or [] if detail.get("stageKey") == current_stage),
        None,
    )
    substage_instance_ids = {
        row.get("subStageInstanceId")
        for row in (current_detail or {}).get("subStages") or []
        if row.get("subStage") == substage_key and row.get("subStageInstanceId")
    }
    completed_calls = [
        call
        for call in task.get("capabilityCalls") or []
        if call.get("status") == "completed"
        and call.get("runMode") == "live"
        and call.get("sourceMode") == "live"
        and call.get("runtime") != "workbench_control"
        and (call.get("stageKey") in {None, current_stage})
        and (not substage_instance_ids or call.get("subStageInstanceId") in substage_instance_ids)
    ]
    if not completed_calls:
        return False

    completed_call_ids = {call.get("id") for call in completed_calls if call.get("id")}
    referenced_artifact_ids = {
        artifact_id
        for call in completed_calls
        for artifact_id in [*(call.get("artifactRefs") or []), *(call.get("outputArtifactRefs") or [])]
    }
    return any(
        artifact.get("kind") != "research_start_draft"
        and artifact.get("artifactType") != "research_start_draft"
        and (
            artifact.get("sourceCapabilityCallId") in completed_call_ids
            or artifact.get("id") in referenced_artifact_ids
        )
        for artifact in task.get("artifacts") or []
    )


ACTIVE_SUBSTAGE_RECOVERY_DETAIL = (
    "No active substage is available for progression. Refresh the task state, "
    "return to the current node, or resolve pending review/input before switching "
    "progression mode or entering the next stage."
)


def _current_running_substage_key(task: dict, *, include_legacy_fallback: bool = False) -> str | None:
    current_stage = task.get("currentStage")
    current_detail = next(
        (detail for detail in task.get("stageDetails") or [] if detail.get("stageKey") == current_stage),
        None,
    )
    if current_detail:
        for ss in current_detail.get("subStages") or []:
            if ss.get("status") == "running" and ss.get("subStage"):
                return ss.get("subStage")

    if include_legacy_fallback:
        for detail in task.get("stageDetails") or []:
            for ss in detail.get("subStages") or []:
                if ss.get("status") == "running" and ss.get("subStage"):
                    return ss.get("subStage")
    return None


def _current_completed_substage_for_approved_resume(task: dict) -> str | None:
    current_stage = task.get("currentStage")
    current_detail = next(
        (detail for detail in task.get("stageDetails") or [] if detail.get("stageKey") == current_stage),
        None,
    )
    if not current_detail:
        return None

    completed_keys = {
        ss.get("subStage")
        for ss in current_detail.get("subStages") or []
        if ss.get("status") == "completed" and ss.get("subStage")
    }
    if not completed_keys:
        return None

    ordered_keys = [
        entry.get("key")
        for row in task.get("stageCatalog") or []
        if row.get("stageKey") == current_stage
        for entry in row.get("allowedSubStages") or []
    ]
    for key in reversed(ordered_keys):
        if key in completed_keys:
            return key
    return next(iter(completed_keys), None)


def _has_pending_stage_gate(task: dict) -> bool:
    pending_gate = task.get("stageGateResumePending")
    if not isinstance(pending_gate, dict):
        return False
    return (
        pending_gate.get("stageKey") == task.get("currentStage")
        and pending_gate.get("substageKey") == _current_completed_substage_for_approved_resume(task)
        and bool(pending_gate.get("nextStageKey"))
    )


def _record_research_start_decision(task: dict, body: ResearchStartConfirmationIn) -> dict:
    decision = body.missingMaterialsDecision
    statuses = {
        "supplement": "blocked_for_supplement",
        "modify_goal": "revision_requested",
        "pause": "paused",
    }
    if decision not in statuses:
        raise HTTPException(status_code=409, detail="a non-start research decision is required")

    artifact = _research_start_artifact(task)
    if not artifact:
        raise HTTPException(status_code=409, detail="research start draft artifact is required before recording a decision")
    artifact = _repair_research_start_artifact(task, artifact)
    structured = artifact["structuredResearchStart"]
    confirmation = {
        "status": statuses[decision],
        "confirmed_items": [],
        "missing_materials_decision": decision,
        "limitations_accepted": False,
        "confirmed_at": _utc_now(),
        "accepted_limitations": [],
        "notes": body.notes,
    }
    return {
        **artifact,
        "artifactState": "needs_input",
        "structuredResearchStart": {**structured, "user_confirmation_status": confirmation},
        "researchStartConfirmation": confirmation,
        "downstreamInput": {
            "status": "blocked_by_user_decision",
            "inherits": [],
            "missingMaterials": list(structured.get("missing_materials") or []),
            "nonCitableBoundaries": list(structured.get("non_citable_boundaries") or []),
        },
    }


def _repair_terminal_running_substages(task_id: str, task: dict) -> dict:
    calls_by_id = {call.get("id"): call for call in task.get("capabilityCalls") or [] if call.get("id")}
    terminal_statuses = {"completed", "failed"}
    changed = False
    for detail in task.get("stageDetails") or []:
        for substage in detail.get("subStages") or []:
            if substage.get("status") != "running":
                continue
            refs = [ref for ref in substage.get("capabilityCallRefs") or [] if ref]
            if not refs:
                continue
            ref_calls = [calls_by_id.get(ref) for ref in refs]
            if not ref_calls or any(call is None or call.get("status") not in terminal_statuses for call in ref_calls):
                continue
            next_status = "failed" if any(call.get("status") == "failed" for call in ref_calls if call) else "completed"
            summary = substage.get("summary")
            if not summary:
                latest = ref_calls[-1] if ref_calls else None
                summary = latest.get("resultSummary") if latest else None
            store.patch_substage_instance(
                task_id,
                substage.get("subStageInstanceId"),
                status=next_status,
                summary=summary,
                capability_call_refs=refs,
                finished_at=_utc_now(),
            )
            changed = True
    if task.get("status") == "running" and not any(
        call.get("status") == "running" for call in task.get("capabilityCalls") or []
    ):
        blocked_statuses = {"needs_input", "blocked_recoverable", "insufficient_evidence"}
        has_user_actionable_terminal = any(
            substage.get("status") in blocked_statuses
            for detail in task.get("stageDetails") or []
            for substage in detail.get("subStages") or []
        )
        if has_user_actionable_terminal:
            store.patch_task(task_id, {"status": "awaiting_confirmation"})
            changed = True
    return store.get_task(task_id) or task if changed else task


def _degraded_task_projection(task: Mapping[str, object]) -> TaskOut:
    """Keep the durable row visible when its rich projection is corrupt."""
    task_id = str(task.get("taskId") or "unknown-task")
    stage = str(task.get("currentStage") or "problem_modeling")
    if stage not in {"problem_modeling", "solution_design", "execution_validation", "insight_generation"}:
        stage = "problem_modeling"
    status = str(task.get("status") or "not_started")
    if status not in {"draft", "not_started", "running", "awaiting_confirmation", "completed", "failed", "cancelled"}:
        status = "not_started"
    title = str(task.get("title") or "").strip() or f"任务 {task_id[:8]}"
    owner_id = str(task.get("ownerId") or "unknown-owner")
    workspace_id = str(task.get("workspaceId") or "unknown-workspace")
    return TaskOut.model_validate({
        "taskId": task_id,
        "ownerId": owner_id,
        "workspaceId": workspace_id,
        "createdBy": str(task.get("createdBy") or owner_id),
        "title": title,
        "revision": max(int(task.get("revision") or 0), 0),
        "currentStage": stage,
        "status": status,
        "resultSource": "none",
        "stages": ["problem_modeling", "solution_design", "execution_validation", "insight_generation"],
        "createdAt": _public_timestamp(task.get("createdAt")),
        "updatedAt": _public_timestamp(task.get("updatedAt")),
        "progressProjection": {
            "projectionError": "task_projection_unavailable",
            "retryable": True,
        },
    })


# List default page size. Clients may pass limit=1..200; omit limit → this default
# (no longer returns the entire scope unbounded).
DEFAULT_TASK_LIST_LIMIT = 20


@router.get("", response_model=TaskListOut)
@router.get("/", response_model=TaskListOut, include_in_schema=False)
def list_tasks(
    limit: Annotated[int | None, Query(ge=1, le=200)] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    status: Annotated[list[str] | None, Query()] = None,
    identity: RequestIdentity = Depends(resolve_request_identity),
) -> TaskListOut:
    from app.modules.task_public_projection import project_public_task_list_item

    effective_limit = DEFAULT_TASK_LIST_LIMIT if limit is None else limit
    selected_statuses = [value for value in (status or []) if value in {
        "draft", "not_started", "running", "awaiting_confirmation", "completed", "failed", "cancelled",
    }]
    if len(selected_statuses) != len(status or []):
        raise HTTPException(status_code=422, detail="invalid task status filter")
    if settings.persistence_backend == "postgres":
        # Owner/workspace filter pushed into SQL (idx_openair_tasks_scope).
        # Slim SELECT extracts list-card fields only — never the full JSONB payload.
        repo = TaskRepository()
        if selected_statuses:
            total = repo.count_tasks_by_scope(
                identity["ownerId"], identity["workspaceId"], statuses=selected_statuses,
            )
            source = repo.list_tasks_by_scope_slim(
                identity["ownerId"], identity["workspaceId"], limit=effective_limit, offset=offset, statuses=selected_statuses,
            )
        else:
            total = repo.count_tasks_by_scope(identity["ownerId"], identity["workspaceId"])
            source = repo.list_tasks_by_scope_slim(
                identity["ownerId"], identity["workspaceId"], limit=effective_limit, offset=offset,
            )
        count_statuses = getattr(repo, "count_task_statuses_by_scope", None)
        status_totals = count_statuses(identity["ownerId"], identity["workspaceId"]) if count_statuses else {}
    else:
        all_tasks = store.list_tasks()
        filtered = [
            t
            for t in all_tasks
            if t.get("ownerId") == identity["ownerId"]
            and t.get("workspaceId") == identity["workspaceId"]
        ]
        status_totals = {}
        for item in filtered:
            item_status = str(item.get("status") or "not_started")
            status_totals[item_status] = status_totals.get(item_status, 0) + 1
        if selected_statuses:
            filtered = [item for item in filtered if item.get("status") in selected_statuses]
        total = len(filtered)
        source = filtered[offset : offset + effective_limit]
        catalog_summaries = None  # JSON backend: derive from payload mirror below
    if settings.persistence_backend == "postgres":
        # Catalog is the artifact source of truth — batch-fetch a visible
        # artifact summary (count + top-N items) per task so the board shows
        # cloned/reused tasks whose artifacts live in the catalog, not the
        # (deprecated) payload mirror.
        catalog_summaries = ArtifactRepository().summary_visible_by_tasks(
            identity["ownerId"],
            identity["workspaceId"],
            [t.get("taskId") for t in source if t.get("taskId")],
        )
    tasks: list = []
    for t in source:
        try:
            out = project_public_task_list_item(t)
        except Exception:
            # Existence comes from PostgreSQL, not from projection success.
            _log.exception("degraded public task list projection taskId=%s", t.get("taskId"))
            out = _degraded_task_projection(t)
        summary = (
            catalog_summaries.get(t.get("taskId"))
            if catalog_summaries is not None
            else None
        )
        if summary is not None:
            out.catalogArtifacts = CatalogArtifactsSummary(
                count=summary.get("count", 0),
                items=[CatalogArtifactItem(**item) for item in summary.get("items", [])],
            )
        else:
            visible_ids = _visible_artifact_ids_for_task(t)
            arts = [
                row for row in t.get("artifacts") or []
                if isinstance(row, dict)
                and str(row.get("artifactId") or row.get("id") or "").strip() in visible_ids
            ]
            out.catalogArtifacts = CatalogArtifactsSummary(
                count=len(arts),
                items=[
                    CatalogArtifactItem(artifactId=a.get("artifactId") or a.get("id") or "", title=a.get("title"))
                    for a in arts[:4]
                ],
            )
        tasks.append(out)
    return TaskListOut(tasks=tasks, total=total, limit=effective_limit, offset=offset, statusTotals=status_totals)


@router.get("/options", response_model=TaskTitleOptionsOut)
def list_task_title_options(
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    identity: RequestIdentity = Depends(resolve_request_identity),
) -> TaskTitleOptionsOut:
    """Title-only picker rows for artifact library filters/upload/reuse.

    Reads table columns only (no JSONB payload). Registered before ``/{task_id}``.
    """
    if settings.persistence_backend == "postgres":
        repo = TaskRepository()
        total = repo.count_tasks_by_scope(identity["ownerId"], identity["workspaceId"])
        rows = repo.list_task_title_options(
            identity["ownerId"], identity["workspaceId"], limit=limit, offset=offset,
        )
        return TaskTitleOptionsOut(
            tasks=[TaskTitleOptionOut.model_validate(row) for row in rows],
            total=total,
            limit=limit,
            offset=offset,
        )
    all_tasks = store.list_tasks()
    filtered = [
        t
        for t in all_tasks
        if t.get("ownerId") == identity["ownerId"]
        and t.get("workspaceId") == identity["workspaceId"]
    ]
    total = len(filtered)
    page = filtered[offset : offset + limit]
    return TaskTitleOptionsOut(
        tasks=[
            TaskTitleOptionOut(
                taskId=str(t.get("taskId") or ""),
                title=str(t.get("title") or "").strip() or "未命名项目",
            )
            for t in page
            if t.get("taskId")
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/meta/packs")
def list_skill_packs() -> list[dict]:
    """Return all skill packs from schemas/packs.yaml for the create-task picker.

    Public (no identity dep) — pack metadata is not user-scoped. Must be
    registered before /{task_id} so FastAPI doesn't capture "meta" as a
    task_id path parameter.
    """
    from app.modules import pack_config

    return [
        {
            "key": p.get("key"),
            "label": p.get("label"),
            "description": p.get("description"),
            "when_to_use": p.get("when_to_use"),
        }
        for p in pack_config.list_available_packs()
    ]


@router.get("/meta/packs/{pack}/stage-catalog", response_model=list[StageCatalogOut])
def get_pack_stage_catalog(pack: str) -> list[StageCatalogOut]:
    """Return the full legal stage/substage catalog for a skill pack."""
    return [StageCatalogOut.model_validate(row) for row in build_stage_catalog(pack=pack)]


def _merge_catalog_artifacts_into_public(
    task: Mapping[str, Any], public: TaskOut, identity: RequestIdentity
) -> None:
    """Surface the task's catalog artifacts in the public ``artifacts`` list.

    The v1.2 contract ``artifacts`` list is the spec-side view (work-unit outputs).
    A freshly cloned task resets it to empty, so without this merge its reused
    catalog artifacts (the content source of truth) would never reach the workbench,
    which builds ``model.artifacts`` from this field. Contract artifacts and catalog
    rows share the same id space, so dedup by id: existing entries win and catalog
    rows only fill the gaps (a no-op for normal tasks whose ids already overlap).
    """
    if settings.persistence_backend != "postgres":
        return
    existing_ids = {getattr(row, "id", None) for row in public.artifacts}
    existing_ids.discard(None)
    try:
        catalog_rows, _total = ArtifactRepository().list_by_scope(
            identity["ownerId"],
            identity["workspaceId"],
            source_task_id=task["taskId"],
            page=1,
            page_size=200,
        )
    except Exception:
        _log.warning(
            "catalog artifact merge unavailable for task %s",
            task.get("taskId"),
            exc_info=True,
        )
        return
    additions: list[CatalogArtifactOut] = []
    for row in catalog_rows:
        row_id = row.get("id") if isinstance(row, dict) else None
        if not row_id or row_id in existing_ids:
            continue
        try:
            additions.append(CatalogArtifactOut.model_validate(row))
        except Exception:
            _log.debug("skipping unvalidatable catalog artifact %s", row_id)
            continue
        existing_ids.add(row_id)
    if additions:
        combined = [*public.artifacts, *additions]
        surface = {"artifacts": [row.model_dump(mode="json", by_alias=True) for row in combined]}
        # Catalog rows are authorized by list_by_scope. Their public links must
        # close over the merged surface too, including references between rows.
        _filter_public_artifact_surface(
            surface, visible_artifact_ids=existing_ids,
            source_artifact_ids={
                row.get("artifactId") or row.get("id")
                for row in task.get("artifacts") or [] if isinstance(row, dict)
            },
        )
        public.artifacts = [
            type(row).model_validate(payload)
            for row, payload in zip(combined, surface["artifacts"])
        ]


@router.get("/{task_id}", response_model=TaskOut)
def get_task(task_id: str, identity: RequestIdentity = Depends(resolve_request_identity)) -> TaskOut:
    # GET is a snapshot read.  It must never repair a substage, refresh a
    # lease, append an event, or schedule work.
    task = (
        TaskRepository().get_task_for_public_read(task_id)
        if settings.persistence_backend == "postgres"
        else store.get_task(task_id)
    )
    task = ensure_task_in_scope(task, identity)
    public = project_public_task_read_only(task)
    _merge_catalog_artifacts_into_public(task, public, identity)
    return public


@router.get("/{task_id}/token-usage", response_model=TaskTokenUsageOut)
def get_task_token_usage(
    task_id: str,
    identity: RequestIdentity = Depends(resolve_request_identity),
) -> TaskTokenUsageOut:
    task = TaskRepository().get_task(task_id) if settings.persistence_backend == "postgres" else store.get_task(task_id)
    ensure_task_in_scope(task, identity)
    try:
        total_tokens = get_token_ledger_repository().sum_task_usage(
            identity["ownerId"],
            task_id,
            workspace_id=identity["workspaceId"],
        )
    except Exception as exc:
        _log.warning("task token usage unavailable taskId=%s", task_id, exc_info=True)
        raise HTTPException(status_code=503, detail="task usage unavailable") from exc
    return TaskTokenUsageOut(totalTokens=total_tokens)


@router.get("/{task_id}/events")
async def stream_task_events(
    task_id: str,
    identity: RequestIdentity = Depends(resolve_request_identity),
    after_event_id: Annotated[str | None, Query(alias="afterEventId")] = None,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    return open_task_event_stream(
        task_id=task_id,
        identity=identity,
        after_event_id=after_event_id,
        last_event_id=last_event_id,
        task_repository_cls=TaskRepository,
        collaboration_repository_cls=CollaborationRepository,
        orchestration_repository_cls=ResearchOrchestrationRepository,
        task_message_repository_cls=TaskMessageRepository,
        heartbeat_seconds=_TASK_EVENT_HEARTBEAT_SECONDS,
        refresh_seconds=_DURABLE_EVENT_REFRESH_SECONDS,
    )


@router.get("/{task_id}/research-events")
async def stream_task_research_events(
    task_id: str,
    identity: RequestIdentity = Depends(resolve_request_identity),
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    """Replay the persistent user-safe research activity projection.

    LISTEN is established before the first replay query so an insert between
    replay and wait cannot be lost. Notifications contain no event body.
    """
    if settings.persistence_backend != "postgres" or not settings.research_orchestration_v1:
        raise HTTPException(status_code=409, detail="persistent research stream requires PostgreSQL orchestration")

    _log.info(
        "sse_connect endpoint_group=sse_connect task_id=%s reconnect=%s",
        task_id,
        bool(last_event_id),
    )

    try:
        task, collaboration = _read_task_research_stream_state(task_id, identity)
    except DurableExecutionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    repository = TaskResearchEventRepository()
    listener = repository.subscribe(
        owner_id=identity["ownerId"],
        workspace_id=identity["workspaceId"],
        task_id=task_id,
    )

    def source_identity(projection: dict) -> str:
        # Canonical v1.2 runId is the executionDecisionId lifecycle identity.
        return str(projection.get("runId") or "") or f"task:{task_id}:pre-run"

    def materialize(projection: dict, current_source_identity: str) -> None:
        lifecycle = build_collaboration_lifecycle_events(projection)
        repository.append_many(
            owner_id=identity["ownerId"],
            workspace_id=identity["workspaceId"],
            task_id=task_id,
            source_identity=current_source_identity,
            events=project_research_activity_events(lifecycle),
        )

    def reset_event(reason: str) -> dict[str, object]:
        return {
            "id": f"stream-reset:{task_id}:{uuid4().hex}",
            "type": "stream_reset",
            "taskId": task_id,
            "emittedAt": _utc_now(),
            "payload": {
                "reason": reason,
                "invalidateScopes": [
                    "task", "collaboration", "artifact", "review", "recovery", "controller_command",
                ],
            },
        }

    async def event_iter():
        sequence = 0
        current_source_identity = source_identity(collaboration)
        try:
            # LISTEN already exists. Materialize then replay, closing the classic
            # LISTEN/first-replay race without treating NOTIFY as durable data.
            await asyncio.to_thread(materialize, collaboration, current_source_identity)
            cursor = await asyncio.to_thread(
                repository.cursor,
                owner_id=identity["ownerId"],
                workspace_id=identity["workspaceId"],
                task_id=task_id,
                source_identity=current_source_identity,
                event_id=last_event_id,
            )
            sequence = cursor.sequence
            if cursor.expired:
                yield _encode_research_sse_event(reset_event("cursor_expired_or_source_changed"))
                sequence = 0

            while True:
                rows = await asyncio.to_thread(
                    repository.list_after,
                    owner_id=identity["ownerId"],
                    workspace_id=identity["workspaceId"],
                    task_id=task_id,
                    source_identity=current_source_identity,
                    sequence=sequence,
                )
                for event in rows:
                    sequence = max(sequence, int(event.get("sequence") or 0))
                    yield _encode_research_sse_event(event)
                if len(rows) >= 200:
                    continue

                notified = await asyncio.to_thread(listener.wait, _TASK_EVENT_HEARTBEAT_SECONDS)
                try:
                    refreshed_task, refreshed_collaboration = await asyncio.to_thread(
                        _read_task_research_stream_state, task_id, identity,
                    )
                except DurableExecutionError:
                    _log.exception("research stream authoritative refresh failed for task %s", task_id)
                    return
                refreshed_source_identity = source_identity(refreshed_collaboration)
                await asyncio.to_thread(
                    materialize, refreshed_collaboration, refreshed_source_identity,
                )
                if refreshed_source_identity != current_source_identity:
                    current_source_identity = refreshed_source_identity
                    sequence = 0
                    yield _encode_research_sse_event(reset_event("source_identity_changed"))
                    continue
                if not notified:
                    heartbeat = {
                        "type": "heartbeat",
                        "taskId": task_id,
                        "emittedAt": _utc_now(),
                        "payload": {"transient": True},
                    }
                    yield ": heartbeat\n" + _encode_research_sse_event(heartbeat)
        except asyncio.CancelledError:
            raise
        finally:
            listener.close()
            _log.info("sse_disconnect endpoint_group=sse_connect task_id=%s", task_id)

    return StreamingResponse(
        event_iter(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.patch("/{task_id}", response_model=TaskOut)
def rename_task(
    task_id: str,
    body: RenameTaskIn,
    identity: RequestIdentity = Depends(resolve_request_identity),
    expected_revision: int | None = Header(default=None, alias="If-Match-Revision"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> TaskOut:
    if settings.persistence_backend == "postgres":
        try:
            updated = TaskRepository().rename_task_scoped(
                task_id=task_id,
                owner_id=identity["ownerId"],
                workspace_id=identity["workspaceId"],
                title=body.title,
                expected_revision=expected_revision,
                idempotency_key=idempotency_key,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="task not found") from exc
        except RuntimeError as exc:
            code = str(exc)
            raise HTTPException(status_code=409, detail=code) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return project_public_task(updated)
    ensure_task_in_scope(store.get_task(task_id), identity)
    updated = store.rename_task(task_id, body.title)
    if not updated:
        # Missing and out-of-scope PostgreSQL rows share one anti-enumeration response.
        raise HTTPException(status_code=404, detail="task not found")
    return project_public_task(updated)


@router.post("", response_model=TaskOut, status_code=201)
@router.post("/", response_model=TaskOut, status_code=201, include_in_schema=False)
def create_task(body: CreateTaskIn, identity: RequestIdentity = Depends(resolve_request_identity)) -> TaskOut:
    payload = body.model_dump(exclude_none=True)
    payload.setdefault("ownerId", identity["ownerId"])
    payload.setdefault("workspaceId", identity["workspaceId"])
    payload.setdefault("createdBy", identity["createdBy"])
    task = store.create_task(payload, persist=settings.persistence_backend != "postgres")
    if settings.persistence_backend == "postgres":
        # API route is the PostgreSQL writer; TaskStore only constructed the
        # legacy compatibility payload used by this creation endpoint.
        try:
            TaskRepository().insert_task(task)
            CollaborationRepository().initialize_task_execution_policy(
                task_id=task["taskId"], owner_id=identity["ownerId"], workspace_id=identity["workspaceId"],
                changed_by=identity["createdBy"],
            )
        except Exception as exc:
            raise HTTPException(status_code=409, detail="task_id_conflict") from exc
    return project_public_task(task)


@router.post("/{task_id}/clone", response_model=TaskOut, status_code=201)
def clone_task(
    task_id: str,
    body: CloneTaskIn,
    identity: RequestIdentity = Depends(resolve_request_identity),
) -> TaskOut:
    """Clone a task into a fresh project seeded from the source spec.

    Reuses the source's research plan/goal/pack/stages via the standard task
    creation path (no execution state carried over). With
    ``includeArtifacts=True`` (default) every source artifact is reused into
    the new task — content-addressed, zero-copy; ``includeArtifacts=False``
    yields a spec-only template.
    """
    source = (
        TaskRepository().get_task(task_id)
        if settings.persistence_backend == "postgres"
        else store.get_task(task_id)
    )
    source = ensure_task_in_scope(source, identity)
    return project_public_task(_clone_source_into(source, identity, body))


def _clone_source_into(
    source: dict, identity: RequestIdentity, body: CloneTaskIn
) -> dict:
    """Build a fresh task seeded from ``source`` into ``identity``'s scope.

    No scope check on the source — callers (clone_task) must scope-check first,
    or (community fork) must have already verified the source is published.
    Returns the persisted new task dict. Shared so community fork reuses the
    exact same v1.2-deep-copy + content-addressed-artifact mechanics as clone.
    """
    payload = {
        "title": (body.title or f"{source.get('title') or 'research'}（副本）").strip(),
        "goalSummary": source.get("goalSummary"),
        "domain": source.get("domain") or "research_task",
        "skillPack": source.get("skillPack"),
        "creationPlan": source.get("creationPlan"),
        "stages": source.get("stages"),
        "currentStage": "problem_modeling",
        "status": "not_started",
        "executionMode": source.get("executionMode") or "stage_confirmation",
    }
    payload.setdefault("ownerId", identity["ownerId"])
    payload.setdefault("workspaceId", identity["workspaceId"])
    payload.setdefault("createdBy", identity["createdBy"])
    if settings.persistence_backend == "postgres":
        # store.create_task is fenced off in postgres mode; build the payload
        # with the pure builder, then insert via the postgres-native repo.
        new_task = store.build_task_payload(payload)
        # If the source is a v1.2 research task, fork its canonical contract
        # (design + registry snapshot + policy) so the clone loads as
        # ResearchTaskV12 rather than a pre-v1.2 legacy task. build_task_payload
        # constructs a fixed schema, so the v1.2 aliases must be injected after.
        _fork_v12_contract_into(source, new_task)
        try:
            TaskRepository().insert_task(new_task)
            CollaborationRepository().initialize_task_execution_policy(
                task_id=new_task["taskId"],
                owner_id=identity["ownerId"],
                workspace_id=identity["workspaceId"],
                changed_by=identity["createdBy"],
            )
        except Exception as exc:
            raise HTTPException(status_code=409, detail="task_id_conflict") from exc
    else:
        new_task = store.create_task(payload)
        if _fork_v12_contract_into(source, new_task):
            # create_task already persisted the base task; re-persist the forked
            # v1.2 aliases (build_task_payload/create_task drop unknown keys).
            new_task = store.patch_task(new_task["taskId"], new_task) or new_task
    if body.includeArtifacts:
        _clone_task_artifacts(source, new_task["taskId"], identity)
    refreshed = (
        TaskRepository().get_task(new_task["taskId"])
        if settings.persistence_backend == "postgres"
        else store.get_task(new_task["taskId"])
    ) or new_task
    return refreshed


def _clone_task_artifacts(
    source_task: dict, target_task_id: str, identity: RequestIdentity
) -> None:
    """Reuse every source artifact into the target task (content-addressed, best-effort).

    Lists artifacts under the SOURCE task's owner/workspace (so cross-workspace
    forks find the source's artifacts), then reuses each into the caller's target
    via ``identity`` (which assigns the new artifact to the caller's scope).
    """
    source_owner = source_task.get("ownerId") or identity["ownerId"]
    source_workspace = source_task.get("workspaceId") or identity["workspaceId"]
    if settings.persistence_backend == "postgres":
        repo = ArtifactRepository()
        page = 1
        page_size = 100
        while True:
            rows, total = repo.list_by_scope(
                source_owner,
                source_workspace,
                source_task_id=source_task["taskId"],
                page=page,
                page_size=page_size,
            )
            for artifact in rows:
                try:
                    reuse_artifact(artifact, source_task, target_task_id, identity=identity)
                except Exception:
                    _log.exception("clone: failed to reuse artifact %s", artifact.get("id"))
            if page * page_size >= total:
                break
            page += 1
    else:
        for artifact in source_task.get("artifacts") or []:
            try:
                reuse_artifact(artifact, source_task, target_task_id, identity=identity)
            except Exception:
                _log.exception("clone: failed to reuse artifact %s", artifact.get("id"))


@router.post("/draft", response_model=TaskOut, status_code=201)
def create_draft_task(body: CreateTaskIn, identity: RequestIdentity = Depends(resolve_request_identity)) -> TaskOut:
    payload = body.model_dump(exclude_none=True)
    client_request_id = payload.get("clientRequestId")
    if client_request_id:
        index_key = (identity["ownerId"], identity["workspaceId"], str(client_request_id))
        existing_task_id = _draft_request_index.get(index_key)
        existing_task = store.get_task(existing_task_id) if existing_task_id else None
        if existing_task:
            return project_public_task(existing_task)
        for task in store.list_tasks():
            if (
                task.get("ownerId") == identity["ownerId"]
                and task.get("workspaceId") == identity["workspaceId"]
                and task.get("draftClientRequestId") == client_request_id
            ):
                _draft_request_index[index_key] = task["taskId"]
                return project_public_task(task)
    task = create_research_start_draft_task(
        goal_summary=payload.get("goalSummary"),
        title=payload.get("title"),
        skill_pack=payload.get("skillPack"),
        planner_plan_override=_deterministic_research_start_plan(
            raw_input=payload.get("goalSummary") or payload.get("title") or "待补充研究问题",
        ),
        identity=identity,
    )
    if client_request_id:
        task = store.patch_task(
            task["taskId"],
            {"draftClientRequestId": client_request_id},
        ) or task
        _draft_request_index[(identity["ownerId"], identity["workspaceId"], str(client_request_id))] = task["taskId"]
    return project_public_task(task)


def _must_be_draft(task: dict) -> None:
    if task.get("status") != "draft":
        raise HTTPException(status_code=409, detail="task is not in draft status")


def _stage_substages_with_status(
    task: dict,
    stage_key: str,
    *,
    from_statuses: set[str],
    to_status: str,
) -> list[dict] | None:
    detail = next(
        (d for d in task.get("stageDetails") or [] if d.get("stageKey") == stage_key),
        None,
    )
    if not detail:
        return None
    substages = [dict(s) for s in detail.get("subStages") or []]
    changed = False
    for substage in substages:
        if substage.get("status") in from_statuses:
            substage["status"] = to_status
            if to_status == "running":
                substage["finishedAt"] = None
                substage["startedAt"] = substage.get("startedAt") or _utc_now()
            elif to_status in {"completed", "failed"}:
                substage["finishedAt"] = substage.get("finishedAt") or _utc_now()
            changed = True
    return substages if changed else None


def _activate_first_substage(task: dict, stage_key: str) -> list[dict] | None:
    detail = next(
        (d for d in task.get("stageDetails") or [] if d.get("stageKey") == stage_key),
        None,
    )
    if not detail:
        return None
    substages = [dict(s) for s in detail.get("subStages") or []]
    if any(substage.get("status") == "running" for substage in substages):
        return None
    for substage in substages:
        if substage.get("status") == "not_started":
            substage["status"] = "running"
            substage["startedAt"] = substage.get("startedAt") or _utc_now()
            substage["finishedAt"] = None
            return substages
    return None


def _finish_running_capability_calls(task_id: str, task: dict, *, reason: str) -> None:
    for call in task.get("capabilityCalls") or []:
        if call.get("status") != "running":
            continue
        store.patch_capability_call(
            task_id,
            call["id"],
            status="failed",
            result_summary=reason,
            ended_at=_utc_now(),
            artifact_refs=call.get("artifactRefs") or [],
            output_artifact_refs=call.get("outputArtifactRefs") or [],
            substage_instance_id=call.get("subStageInstanceId"),
            stage_key=call.get("stageKey") or task.get("currentStage"),
        )


def _record_execution_control(task_id: str, *, action: str, stage_key: str, summary: str) -> None:
    store.add_capability_call(
        task_id,
        skill_id=f"workbench_{action}",
        status="completed",
        runtime="workbench_control",
        result_summary=summary,
        started_at=_utc_now(),
        ended_at=_utc_now(),
        stage_key=stage_key,
    )


@router.get("/{task_id}/research-design", response_model=ResearchDesignReadOut)
def get_research_design(
    task_id: str,
    identity: RequestIdentity = Depends(resolve_request_identity),
) -> ResearchDesignReadOut:
    _require_research_design_api()
    try:
        result = _research_design_service().read_design(
            task_id=task_id,
            owner_id=identity["ownerId"],
            workspace_id=identity["workspaceId"],
        )
    except Exception as exc:
        raise _map_research_design_error(exc) from exc
    return ResearchDesignReadOut.model_validate(result)


@router.post("/{task_id}/research-design/confirm-plan", response_model=ResearchDesignReadOut)
def confirm_research_design(
    task_id: str,
    body: ResearchDesignConfirmationIn,
    identity: RequestIdentity = Depends(resolve_request_identity),
) -> ResearchDesignReadOut:
    _require_research_design_api()
    service = _research_design_service()
    try:
        confirmed_task = service.confirm(
            task_id=task_id,
            owner_id=identity["ownerId"],
            workspace_id=identity["workspaceId"],
            actor_id=identity["createdBy"],
            design_id=body.designId,
            version=body.version,
            confirmation_id=body.confirmationId,
            decision=body.decision,
            expected_task_revision=body.expectedTaskRevision,
        )
    except Exception as exc:
        raise _map_research_design_error(exc) from exc

    if body.decision == "approved":
        try:
            _continue_confirmed_design_automatic_research(
                task=confirmed_task,
                design_id=body.designId,
                version=body.version,
                owner_id=identity["ownerId"],
                workspace_id=identity["workspaceId"],
                expected_task_revision=body.expectedTaskRevision + 1,
            )
        except Exception as exc:
            # The ResearchDesign confirmation is already durable. Automatic
            # continuation is downstream system work and must not rewrite the
            # successful confirmation as a failed user action.
            if isinstance(exc, DurableExecutionError):
                status_code, detail = exc.status_code, exc.detail
            else:
                mapped = _map_research_execution_error(exc)
                status_code, detail = mapped.status_code, mapped.detail
            _log.error(
                "confirmed research design automatic continuation failed "
                "taskId=%s designId=%s version=%s statusCode=%s detail=%r",
                task_id,
                body.designId,
                body.version,
                status_code,
                detail,
                exc_info=True,
            )

    try:
        result = service.read_design(
            task_id=task_id,
            owner_id=identity["ownerId"],
            workspace_id=identity["workspaceId"],
        )
    except Exception as exc:
        raise _map_research_design_error(exc) from exc
    return ResearchDesignReadOut.model_validate(result)


@router.post("/{task_id}/confirm-plan", response_model=TaskOut)
def confirm_plan(
    task_id: str,
    body: ResearchStartConfirmationIn | None = None,
    identity: RequestIdentity = Depends(resolve_request_identity),
) -> TaskOut:
    task = ensure_task_in_scope(store.get_task(task_id), identity)
    if "researchDesignVersions" in task:
        raise HTTPException(
            status_code=409,
            detail="canonical research design must use /research-design/confirm-plan",
        )
    _must_be_draft(task)
    if task.get("creationStatus") != "awaiting_confirmation":
        raise HTTPException(status_code=409, detail="creation status must be awaiting_confirmation")
    confirmed_artifact = _validate_research_start_confirmation(task, body)
    artifacts = [
        confirmed_artifact if artifact.get("id") == confirmed_artifact.get("id") else artifact
        for artifact in task.get("artifacts") or []
    ]
    try:
        resolved_plan = resolve_creation_plan_capabilities(
            task.get("creationPlan") or {},
            skill_pack=str(task.get("skillPack") or "sdr"),
        )
        resolved_plan = ensure_orchestrator_decision(
            resolved_plan,
            user_goal=str(task.get("goalSummary") or ""),
            task_status="draft",
        )
        plan = transition_orchestrator_state(resolved_plan, task_status="not_started")
        precompile_creation_plan(
            creation_plan=plan or {},
            goal_summary=str(task.get("goalSummary") or ""),
        )
    except (CapabilityResolutionError, FrozenPlanError) as exc:
        raise HTTPException(
            status_code=409,
            detail="required_capability_unavailable：当前草案缺少可执行的专业智能体或技能，请先调整草案。",
        ) from exc
    updates = {
        "status": "not_started",
        "creationStatus": "confirmed",
        "researchStartConfirmation": confirmed_artifact.get("researchStartConfirmation"),
        "researchStartInput": confirmed_artifact.get("structuredResearchStart"),
        "downstreamInput": confirmed_artifact.get("downstreamInput"),
        "artifacts": artifacts,
        **({"creationPlan": plan} if plan is not None else {}),
    }
    if settings.persistence_backend == "postgres":
        confirmed_task = deepcopy(task)
        confirmed_task.update(updates)
        updated = TaskRepository().confirm_research_start_plan(
            task=confirmed_task,
            owner_id=identity["ownerId"],
            workspace_id=identity["workspaceId"],
        )
        ArtifactRepository().upsert_from_dict(task_id, confirmed_artifact, updated.get("title"))
        return project_public_task(updated)
    updated = store.patch_task(
        task_id,
        updates,
    )
    # If the creation plan contains workflow substages, initialize them
    plan = updated.get("creationPlan") or {}
    if isinstance(plan.get("stages"), list) and plan["stages"] and isinstance(plan["stages"][0].get("substages"), list):
        updated = store.initialize_from_workflow(task_id, plan) or updated
    return project_public_task(updated)


@router.post("/{task_id}/research-start-decision", response_model=TaskOut)
def record_research_start_decision(
    task_id: str,
    body: ResearchStartConfirmationIn,
    identity: RequestIdentity = Depends(resolve_request_identity),
) -> TaskOut:
    task = ensure_task_in_scope(store.get_task(task_id), identity)
    _must_be_draft(task)
    artifact = _record_research_start_decision(task, body)
    artifacts = [artifact if row.get("id") == artifact.get("id") else row for row in task.get("artifacts") or []]
    updated = store.patch_task(
        task_id,
        {
            "creationStatus": "revision_requested" if body.missingMaterialsDecision == "modify_goal" else "awaiting_confirmation",
            "researchStartConfirmation": artifact["researchStartConfirmation"],
            "downstreamInput": artifact["downstreamInput"],
            "artifacts": artifacts,
        },
    )
    return project_public_task(updated)


def _research_design_revision_spec(body: UpdateTaskPlanIn) -> dict[str, Any]:
    steps: list[dict[str, str]] = []
    for stage in body.stages:
        for substage in stage.substages:
            local_key = (
                f"existing_instance__{substage.instanceId}"
                if substage.instanceId
                else f"user:{stage.stageKey}:{substage.substage}"
            ).strip()
            steps.append({
                "localKey": local_key,
                "stageKey": str(stage.stageKey),
                "objective": substage.action.strip(),
            })
    return {"goal": body.goalSummary.strip(), "steps": steps}


def _revise_canonical_research_design(
    *,
    task_id: str,
    body: UpdateTaskPlanIn,
    identity: RequestIdentity,
    request_id: str | None = None,
) -> dict[str, Any]:
    repository = ResearchOrchestrationRepository()
    aggregate = repository.load(
        task_id=task_id,
        owner_id=identity["ownerId"],
        workspace_id=identity["workspaceId"],
    )
    source = ProductionDefinitionSource(
        snapshot_ref=aggregate.registry_snapshot_ref.model_dump(mode="json", by_alias=True),
    )
    workflow_admission = repository.load_planning_workflow_admission(
        task_id=task_id,
        owner_id=identity["ownerId"],
        workspace_id=identity["workspaceId"],
    )
    catalog = source.planner_catalog_for_research_design(workflow_admission)
    latest_design = max(aggregate.research_design_versions, key=lambda row: row.version)
    known_instance_ids = {row.substage_instance_id for row in latest_design.instances}
    requested_instance_ids = {
        substage.instanceId
        for stage in body.stages
        for substage in stage.substages
        if substage.instanceId
    }
    if not requested_instance_ids.issubset(known_instance_ids):
        raise ResearchDesignServiceError("研究方案包含已失效的研究步骤，请重新加载后再修改。")
    revision_spec = _research_design_revision_spec(body)
    revision_spec.update({
        "constraints": list(latest_design.constraints),
        "evidenceBoundaries": list(latest_design.understanding.evidence_boundaries),
        "requestedExternalActions": list(latest_design.external_action_boundary.allowed_action_kinds),
        "materialSummary": latest_design.understanding.material_summary,
    })
    raw_input = "\n".join([revision_spec["goal"], *[row["objective"] for row in revision_spec["steps"]]])
    service = ResearchDesignService(repository=repository, definition_source=source)

    def provider(_idempotency_key: str):
        try:
            prepared = prepare_planner_attempt(
                {"input": raw_input, "rawInput": raw_input, "sourcePage": "workbench"},
                None,
                1,
                definition_catalog=catalog,
                semantic_revision=revision_spec,
            )
            return execute_planner_attempt(prepared)
        except Exception as exc:
            raise classify_research_design_revision_failure(exc) from exc

    try:
        service.revise(
            task_id=task_id,
            owner_id=identity["ownerId"],
            workspace_id=identity["workspaceId"],
            request_id=request_id or f"user-revision:{uuid4().hex}",
            expected_task_revision=body.expectedRevision,
            provider=provider,
            user_input=body.model_dump_json(by_alias=True),
            user_input_kind="plan_revision",
        )
    except TaskRevisionConflictError:
        raise
    except (ResearchDesignRevisionUserError, ResearchDesignRevisionSaveUnavailable):
        raise
    except Exception as exc:
        raise classify_research_design_revision_failure(exc) from exc
    updated = TaskRepository().get_task(task_id)
    if updated is None:
        raise ResearchTaskNotFoundError(task_id)
    return updated


@router.post(
    "/{task_id}/research-design/agent-revision",
    response_model=ModelJobAcceptedOut,
    status_code=202,
)
def agent_revise_research_design(
    task_id: str,
    body: AgentResearchDesignRevisionIn,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    identity: RequestIdentity = Depends(resolve_request_identity),
) -> ModelJobAcceptedOut:
    # In PostgreSQL mode this command is entirely canonical: use the durable
    # projection for its preconditions as well as the canonical revision write.
    # TaskStore is only the JSON-backend compatibility read model.
    task = ensure_task_in_scope(
        TaskRepository().get_task(task_id)
        if settings.persistence_backend == "postgres"
        else store.get_task(task_id),
        identity,
    )
    _must_be_draft(task)
    if task.get("creationStatus") not in {"awaiting_confirmation", "revision_requested"}:
        raise HTTPException(status_code=409, detail="creation plan is not editable")
    if not settings.research_orchestration_v1 or not task.get("researchDesignVersions"):
        raise HTTPException(status_code=409, detail="canonical research design is required")
    if not idempotency_key:
        raise HTTPException(status_code=428, detail="Idempotency-Key is required")
    try:
        ModelJobQueueRepository().enqueue(
            resource_kind="task",
            resource_id=task_id,
            owner_id=identity["ownerId"],
            workspace_id=identity["workspaceId"],
            job_id=idempotency_key,
            job_kind="research_design_agent_revision",
            expected_revision=body.expectedRevision,
            command_payload={"instruction": body.instruction},
        )
    except ModelJobResourceNotFound as exc:
        raise HTTPException(status_code=404, detail="research task not found") from exc
    except ModelJobConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return ModelJobAcceptedOut(
        jobId=idempotency_key,
        resourceId=task_id,
        jobKind="research_design_agent_revision",
    )

@router.post("/{task_id}/update-plan", response_model=TaskOut | ModelJobAcceptedOut)
def update_plan(
    task_id: str,
    body: UpdateTaskPlanIn,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    identity: RequestIdentity = Depends(resolve_request_identity),
) -> TaskOut | ModelJobAcceptedOut:
    task = ensure_task_in_scope(store.get_task(task_id), identity)
    _must_be_draft(task)
    if task.get("creationStatus") not in {"awaiting_confirmation", "revision_requested"}:
        raise HTTPException(status_code=409, detail="creation plan is not editable")
    editable = body.model_dump(mode="json")
    try:
        if settings.research_orchestration_v1 and task.get("researchDesignVersions"):
            if not idempotency_key:
                raise HTTPException(status_code=428, detail="Idempotency-Key is required")
            ModelJobQueueRepository().enqueue(
                resource_kind="task",
                resource_id=task_id,
                owner_id=identity["ownerId"],
                workspace_id=identity["workspaceId"],
                job_id=idempotency_key,
                job_kind="research_design_plan_revision",
                expected_revision=body.expectedRevision,
                command_payload={"plan": body.model_dump(mode="json")},
            )
            return JSONResponse(
                status_code=202,
                content=ModelJobAcceptedOut(
                    jobId=idempotency_key,
                    resourceId=task_id,
                    jobKind="research_design_plan_revision",
                ).model_dump(mode="json"),
            )
        elif settings.persistence_backend == "postgres":
            updated = TaskRepository().update_task_plan(
                task=task,
                editable=editable,
                owner_id=identity["ownerId"],
                workspace_id=identity["workspaceId"],
            )
        else:
            updated = store.update_task_plan(task_id, editable)
    except ResearchDesignServiceError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ModelJobResourceNotFound as exc:
        raise HTTPException(status_code=404, detail="research task not found") from exc
    except ModelJobConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        if str(exc) == "task_revision_conflict":
            raise HTTPException(status_code=409, detail="task_revision_conflict") from exc
        raise
    except TaskPlanCapabilityError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except TaskPlanValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if updated is None:
        raise HTTPException(status_code=404, detail="task not found")
    return project_public_task(updated)


@router.post("/{task_id}/revise-plan", response_model=TaskOut)
def revise_plan(
    task_id: str,
    identity: RequestIdentity = Depends(resolve_request_identity),
    expected_revision: int | None = Header(default=None, alias="If-Match-Revision"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> TaskOut:
    task = ensure_task_in_scope(
        TaskRepository().get_task(task_id) if settings.persistence_backend == "postgres" else store.get_task(task_id),
        identity,
    )
    _must_be_draft(task)
    artifacts = [
        _repair_research_start_artifact(task, artifact)
        if artifact.get("kind") == "research_start_draft" or artifact.get("artifactType") == "research_start_draft"
        else artifact
        for artifact in task.get("artifacts") or []
    ]
    if settings.persistence_backend == "postgres":
        try:
            updated = TaskRepository().request_draft_revision(
                task_id=task_id,
                owner_id=identity["ownerId"],
                workspace_id=identity["workspaceId"],
                artifacts=artifacts if artifacts != task.get("artifacts") else None,
                expected_revision=expected_revision,
                idempotency_key=idempotency_key,
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return project_public_task(updated)
    updates = {"creationStatus": "revision_requested"}
    if artifacts != task.get("artifacts"):
        updates["artifacts"] = artifacts
    updated = store.patch_task(task_id, updates)
    return project_public_task(updated)


@router.post("/{task_id}/cancel-draft", response_model=TaskOut)
def cancel_draft(
    task_id: str,
    identity: RequestIdentity = Depends(resolve_request_identity),
    expected_revision: int | None = Header(default=None, alias="If-Match-Revision"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> TaskOut:
    task = ensure_task_in_scope(
        TaskRepository().get_task(task_id) if settings.persistence_backend == "postgres" else store.get_task(task_id),
        identity,
    )
    _must_be_draft(task)
    if settings.persistence_backend == "postgres":
        try:
            updated = TaskRepository().cancel_draft_scoped(
                task_id=task_id,
                owner_id=identity["ownerId"],
                workspace_id=identity["workspaceId"],
                expected_revision=expected_revision,
                idempotency_key=idempotency_key,
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return project_public_task(updated)
    updated = store.patch_task(task_id, {"status": "cancelled", "creationStatus": "cancelled"})
    return project_public_task(updated)


@router.delete("/{task_id}", status_code=204)
def delete_task(
    task_id: str,
    identity: RequestIdentity = Depends(resolve_request_identity),
    expected_revision: int | None = Header(default=None, alias="If-Match-Revision"),
) -> None:
    """Hide a PostgreSQL task via the canonical tombstone contract.

    Executed-task evidence is intentionally retained: recovery, dispatch, lineage,
    and external-evidence rows are RESTRICT-linked/append-only historical facts and
    must not be hard-deleted as part of the public Task delete operation.
    """
    if settings.persistence_backend == "postgres":
        try:
            deleted = TaskRepository().soft_delete_task(
                task_id=task_id,
                owner_id=identity["ownerId"],
                workspace_id=identity["workspaceId"],
                expected_revision=expected_revision,
            )
        except RuntimeError as exc:
            if str(exc) == "task_revision_conflict":
                raise HTTPException(status_code=409, detail="task_revision_conflict") from exc
            raise
        if not deleted:
            raise HTTPException(status_code=404, detail="task not found")
        return None

    ensure_task_in_scope(store.get_task(task_id), identity)
    _cleanup_task_objects(task_id)
    deleted = store.delete_task(task_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="task not found")


@router.get(
    "/{task_id}/execution-start-status",
    response_model=ResearchExecutionStartStatusOut,
    response_model_exclude_none=True,
)
def read_current_execution_start_status(
    task_id: str,
    identity: RequestIdentity = Depends(resolve_request_identity),
) -> ResearchExecutionStartStatusOut:
    """Read current Task-revision start semantics without client-local request identity."""
    if not settings.research_orchestration_v1:
        raise HTTPException(status_code=404, detail="research execution status API is disabled")
    if settings.persistence_backend != "postgres":
        raise HTTPException(status_code=503, detail="research_execution_requires_postgres")
    try:
        return _research_execution_start_status_reader().read_current(
            task_id=task_id,
            owner_id=identity["ownerId"],
            workspace_id=identity["workspaceId"],
        )
    except ResearchExecutionStartTaskNotFound as exc:
        raise HTTPException(status_code=404, detail="research task not found") from exc


@router.get(
    "/{task_id}/execution-selections/{selection_request_id}",
    response_model=ResearchExecutionStartRequestStatusOut,
    response_model_exclude_none=True,
)
def read_execution_selection_status(
    task_id: str,
    selection_request_id: str,
    identity: RequestIdentity = Depends(resolve_request_identity),
) -> ResearchExecutionStartRequestStatusOut:
    """Recover one Start request receipt without changing current Task authority."""
    if not settings.research_orchestration_v1:
        raise HTTPException(status_code=404, detail="research execution status API is disabled")
    if settings.persistence_backend != "postgres":
        raise HTTPException(status_code=503, detail="research_execution_requires_postgres")
    try:
        return _research_execution_start_status_reader().read(
            task_id=task_id,
            owner_id=identity["ownerId"],
            workspace_id=identity["workspaceId"],
            selection_request_id=selection_request_id,
        )
    except ResearchExecutionStartTaskNotFound as exc:
        raise HTTPException(status_code=404, detail="research task not found") from exc


@router.post("/{task_id}/start", status_code=202)
def start_task(
    task_id: str,
    body: dict | None = None,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    identity: RequestIdentity = Depends(resolve_request_identity),
) -> Response:
    """Persist a first-execution intent; this endpoint never runs a skill inline."""
    if settings.research_orchestration_v1:
        if settings.persistence_backend != "postgres":
            raise HTTPException(status_code=503, detail="research_execution_requires_postgres")
        try:
            command = ResearchExecutionStartIn.model_validate(body or {})
        except Exception as exc:
            raise HTTPException(
                status_code=428,
                detail="flag-on start requires expectedTaskRevision and selectionRequestId",
            ) from exc
        if not idempotency_key or idempotency_key != command.selectionRequestId:
            raise HTTPException(
                status_code=409,
                detail="Idempotency-Key must equal selectionRequestId",
            )
        command_payload = {
            "requestKind": (
                "explicit_start"
                if command.productAction == "initial_start"
                else "continue_research"
            )
        }
        queue = ModelJobQueueRepository()
        replay_probe = getattr(queue, "is_exact_replay", None)

        def is_exact_replay() -> bool:
            if not callable(replay_probe):
                return False
            try:
                return bool(replay_probe(
                    resource_kind="task",
                    resource_id=task_id,
                    owner_id=identity["ownerId"],
                    workspace_id=identity["workspaceId"],
                    job_id=command.selectionRequestId,
                    job_kind="execution_selection",
                    expected_revision=command.expectedTaskRevision,
                    command_payload=command_payload,
                ))
            except ModelJobResourceNotFound as exc:
                raise HTTPException(
                    status_code=404, detail="research task not found"
                ) from exc

        exact_replay = (
            command.productAction == "continue_research" and is_exact_replay()
        )
        if command.productAction == "continue_research" and not exact_replay:
            current_task = ensure_task_in_scope(
                TaskRepository().get_task(task_id), identity
            )
            if current_task.get("revision") != command.expectedTaskRevision:
                raise HTTPException(status_code=409, detail="task_revision_conflict")
            action_state = project_workbench_user_actions(
                task_payload=current_task,
                owner_id=identity["ownerId"],
                workspace_id=identity["workspaceId"],
                actor_id=identity["createdBy"],
            )
            continuation_denied = (
                action_state.get("revision") != command.expectedTaskRevision
                or action_state.get("executionStopped") is True
                or "continue_research" not in action_state.get("allowedActions", [])
            )
            if continuation_denied:
                if not is_exact_replay():
                    raise HTTPException(
                        status_code=409,
                        detail={"code": "continue_research_not_allowed"},
                    )
        try:
            if command.substageInstanceId is not None:
                raise DecisionPolicyViolation(
                    "api_cannot_preselect_next_research_task"
                )
            queued = queue.enqueue(
                resource_kind="task",
                resource_id=task_id,
                owner_id=identity["ownerId"],
                workspace_id=identity["workspaceId"],
                job_id=command.selectionRequestId,
                job_kind="execution_selection",
                expected_revision=command.expectedTaskRevision,
                command_payload=command_payload,
            )
        except ModelJobResourceNotFound as exc:
            raise HTTPException(status_code=404, detail="research task not found") from exc
        except ModelJobContinueDenied as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "continue_research_not_allowed"},
            ) from exc
        except ModelJobStartDenied as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "execution_start_not_allowed",
                    "startStatus": exc.start_status.model_dump(
                        mode="json", exclude_none=True, by_alias=True
                    ),
                },
            ) from exc
        except ModelJobConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:
            raise _map_research_execution_error(exc) from exc
        return JSONResponse(
            status_code=202,
            content={
                "taskId": task_id,
                "selectionRequestId": command.selectionRequestId,
                "state": "accepted" if queued["state"] == "queued" else queued["state"],
                "taskRevision": command.expectedTaskRevision,
            },
            headers={
                "Location": (
                    f"/api/tasks/{task_id}/execution-selections/"
                    f"{command.selectionRequestId}"
                ),
            },
        )
    if settings.persistence_backend != "postgres":
        raise HTTPException(status_code=503, detail="durable_execution_requires_postgres")
    try:
        run, replayed = DurableExecutionRepository().start_initial(
            task_id=task_id,
            owner_id=identity["ownerId"],
            workspace_id=identity["workspaceId"],
            idempotency_key=idempotency_key or "",
            source_mode=settings.skill_exec_mode,
        )
    except DurableExecutionError as exc:
        DurableExecutionRepository().record_start_attempt(
            task_id=task_id, owner_id=identity["ownerId"], workspace_id=identity["workspaceId"],
            idempotency_key=idempotency_key or "", outcome=f"rejected_{exc.status_code}", canonical_run_id=exc.run_id,
        )
        headers = {"Location": f"/api/execution-runs/{exc.run_id}"} if exc.run_id else None
        raise HTTPException(status_code=exc.status_code, detail=exc.detail, headers=headers) from exc
    body = {"run": serialize_run(run), "replayed": replayed}
    headers = {"Location": f"/api/execution-runs/{run['run_id']}"}
    if replayed:
        headers["Idempotent-Replay"] = "true"
    return JSONResponse(status_code=202, content=body, headers=headers)


@router.post("/{task_id}/stop-run", response_model=TaskOut | StopTaskReceiptOut)
def stop_task_run(
    task_id: str,
    body: dict | None = None,
    identity: RequestIdentity = Depends(resolve_request_identity),
) -> TaskOut | StopTaskReceiptOut:
    if settings.persistence_backend == "postgres":
        expected_revision: int | None = None
        if settings.research_orchestration_v1:
            try:
                command = ResearchExecutionStopIn.model_validate(body or {})
            except Exception as exc:
                raise HTTPException(
                    status_code=428,
                    detail="flag-on stop requires expectedTaskRevision",
                ) from exc
            expected_revision = command.expectedTaskRevision
        elif body and "expectedTaskRevision" in body:
            try:
                expected_revision = ResearchExecutionStopIn.model_validate(body).expectedTaskRevision
            except Exception as exc:
                raise HTTPException(status_code=422, detail="invalid expectedTaskRevision") from exc
        try:
            receipt = ResearchDispatchService().suppress_with_receipt(
                task_id=task_id,
                owner_id=identity["ownerId"],
                workspace_id=identity["workspaceId"],
                expected_task_revision=expected_revision,
                reason_code="user_stop_requested",
            )
        except Exception as exc:
            raise _map_research_execution_error(exc) from exc
        return StopTaskReceiptOut(
            receiptId=str(receipt.get("receiptId") or f"stop:{receipt['taskId']}:{receipt['revision']}"),
            taskId=str(receipt["taskId"]),
            status=str(receipt.get("taskStatus") or receipt["status"]),
            revision=int(receipt["revision"]),
            suppressed=bool(receipt.get("suppressed", True)),
        )

    task = ensure_task_in_scope(store.get_task(task_id), identity)
    if task.get("status") != "running":
        if task.get("status") in {"awaiting_confirmation", "failed", "cancelled"}:
            return project_public_task(task)
        raise HTTPException(status_code=409, detail="task must be running to stop the current run")
    cancel_task_background_executions(task_id)
    stage_key = task.get("currentStage") or (task.get("stages") or ["problem_modeling"])[0]
    _finish_running_capability_calls(
        task_id,
        task,
        reason="Stopped by user from workbench execution controls.",
    )
    stopped_substages = _stage_substages_with_status(
        task,
        stage_key,
        from_statuses={"running"},
        to_status="failed",
    )
    store.save_stage_result(
        task_id,
        stage_key,
        {
            "status": "failed",
            "lockedReason": "用户已停止本轮执行。",
            "risks": "本轮执行已停止；已生成的产物会保留，但不能直接当作最终结论。",
            "nextSteps": "先复核已保留产物和失败影响；确认方向后可重试当前阶段或补充材料。",
            "primaryAction": "复核已保留产物后重试当前阶段。",
            "canReview": True,
            "canLock": True,
            **({"subStages": stopped_substages} if stopped_substages is not None else {}),
        },
    )
    updated = store.patch_task(task_id, {"status": "awaiting_confirmation"}) or task
    return project_public_task(updated)


@router.post("/{task_id}/resume-run", response_model=ResumeTaskReceiptOut)
def resume_task_run(
    task_id: str,
    body: dict | None = None,
    identity: RequestIdentity = Depends(resolve_request_identity),
) -> ResumeTaskReceiptOut:
    """Resume the same canonical Task after a durable user stop."""
    if not settings.research_orchestration_v1:
        raise HTTPException(status_code=404, detail="research execution resume API is disabled")
    if settings.persistence_backend != "postgres":
        raise HTTPException(status_code=503, detail="research_execution_requires_postgres")
    try:
        command = ResearchExecutionResumeIn.model_validate(body or {})
    except Exception as exc:
        raise HTTPException(
            status_code=428,
            detail="flag-on resume requires expectedTaskRevision",
        ) from exc
    try:
        receipt = ResearchDispatchService().resume_with_receipt(
            task_id=task_id,
            owner_id=identity["ownerId"],
            workspace_id=identity["workspaceId"],
            expected_task_revision=command.expectedTaskRevision,
        )
        continuation_started = False
        continuation_accepted = False
        selection_request_id: str | None = None
        revision = int(receipt["revision"])
        if int(receipt.get("resumedDispatchCount") or 0) == 0:
            repository = ResearchOrchestrationRepository()
            task = repository.load(
                task_id=task_id,
                owner_id=identity["ownerId"],
                workspace_id=identity["workspaceId"],
            )
            if not _canonical_execution_in_flight(task):
                selection_request_id = f"{receipt['receiptId']}:continue"
                ModelJobQueueRepository().enqueue(
                    resource_kind="task",
                    resource_id=task_id,
                    owner_id=identity["ownerId"],
                    workspace_id=identity["workspaceId"],
                    job_id=selection_request_id,
                    job_kind="execution_selection",
                    expected_revision=revision,
                    command_payload={"requestKind": "resume"},
                )
                continuation_accepted = True
    except Exception as exc:
        raise _map_research_execution_error(exc) from exc
    return ResumeTaskReceiptOut(
        receiptId=str(receipt["receiptId"]),
        taskId=str(receipt["taskId"]),
        status=str(receipt.get("taskStatus") or receipt["status"]),
        revision=revision,
        resumed=True,
        resumedDispatchCount=int(receipt.get("resumedDispatchCount") or 0),
        continuationStarted=continuation_started,
        continuationAccepted=continuation_accepted,
        selectionRequestId=selection_request_id,
    )


@router.post(
    "/{task_id}/work-units/{work_unit_id}/external-result/retry",
    response_model=dict,
)
def retry_external_result(
    task_id: str,
    work_unit_id: str,
    body: ExplicitRetryCommand,
    identity: RequestIdentity = Depends(resolve_request_identity),
) -> dict:
    _require_external_recovery_api()
    if (
        body.task_id != task_id
        or body.work_unit_id != work_unit_id
        or body.owner_id != identity["ownerId"]
        or body.workspace_id != identity["workspaceId"]
        or body.actor_id != identity["createdBy"]
    ):
        raise HTTPException(status_code=404, detail="external recovery fact not found")
    try:
        return dict(_external_recovery_service().retry(body))
    except Exception as exc:
        raise _map_external_recovery_error(exc) from None


@router.post("/{task_id}/retry", response_model=TaskOut)
def retry_task_run(task_id: str, identity: RequestIdentity = Depends(resolve_request_identity)) -> TaskOut:
    if settings.research_orchestration_v1:
        raise HTTPException(status_code=409, detail="use the WorkUnit-scoped explicit retry command")
    task = ensure_task_in_scope(store.get_task(task_id), identity)
    if task.get("status") not in {"failed", "awaiting_confirmation", "not_started"}:
        raise HTTPException(status_code=409, detail="task must be failed, awaiting confirmation, or not started to retry")
    stage_key = task.get("currentStage") or (task.get("stages") or ["problem_modeling"])[0]
    retry_substages = _stage_substages_with_status(
        task,
        stage_key,
        from_statuses={"failed", "not_started"},
        to_status="running",
    )
    updated = store.save_stage_result(
        task_id,
        stage_key,
        {
            "status": "running",
            "lockedReason": "",
            "risks": "正在重试；请等待本轮结果，必要时可停止本轮。",
            "nextSteps": "等待当前阶段重试完成；失败时复核失败影响后再决定是否换策略。",
            "primaryAction": "等待当前阶段重试完成。",
            "canReview": True,
            "canLock": False,
            **({"subStages": retry_substages} if retry_substages is not None else {}),
        },
    ) or task
    _record_execution_control(
        task_id,
        action="retry_requested",
        stage_key=stage_key,
        summary="Task retry requested from workbench execution controls.",
    )
    updated = store.patch_task(task_id, {"status": "running"}) or updated
    return project_public_task(updated)


@router.post("/{task_id}/stages/{stage_key}/retry", response_model=TaskOut)
def retry_stage_run(
    task_id: str,
    stage_key: str,
    identity: RequestIdentity = Depends(resolve_request_identity),
) -> TaskOut:
    if settings.research_orchestration_v1:
        raise HTTPException(status_code=409, detail="use the WorkUnit-scoped explicit retry command")
    task = ensure_task_in_scope(store.get_task(task_id), identity)
    if stage_key not in (task.get("stages") or []):
        raise HTTPException(status_code=404, detail="stage not found")
    if task.get("status") not in {"failed", "awaiting_confirmation", "not_started"}:
        raise HTTPException(status_code=409, detail="task status does not allow stage retry")
    retry_substages = _stage_substages_with_status(
        task,
        stage_key,
        from_statuses={"failed", "not_started"},
        to_status="running",
    )
    updated = store.save_stage_result(
        task_id,
        stage_key,
        {
            "status": "running",
            "lockedReason": "",
            "risks": "当前阶段正在局部重试；只更新本阶段相关产物和状态。",
            "nextSteps": "等待本阶段重试完成；完成后复核新增产物再推进。",
            "primaryAction": "复核本阶段重试结果。",
            "canReview": True,
            "canLock": False,
            **({"subStages": retry_substages} if retry_substages is not None else {}),
        },
    ) or task
    _record_execution_control(
        task_id,
        action="stage_retry_requested",
        stage_key=stage_key,
        summary="Stage retry requested from workbench execution controls.",
    )
    updated = store.patch_task(task_id, {"status": "running", "currentStage": stage_key}) or updated
    return project_public_task(updated)


@router.patch("/{task_id}/status", response_model=TaskOut)
def patch_task_status(task_id: str, body: PatchTaskStatusIn, identity: RequestIdentity = Depends(resolve_request_identity)) -> TaskOut:
    task = ensure_task_in_scope(store.get_task(task_id), identity)
    current = task.get("status")
    nxt = body.status

    # Terminal states cannot be changed
    _TERMINAL = {"completed", "failed", "cancelled"}
    if current in _TERMINAL:
        raise HTTPException(status_code=409, detail=f"{current} tasks cannot change status")

    # draft must use confirm-plan / cancel-draft endpoints
    if current == "draft" and nxt != "cancelled":
        raise HTTPException(status_code=409, detail="draft tasks must use confirm-plan or cancel-draft")

    # Initial execution must go through /start; resuming after a review gate may
    # return an execution task from awaiting_confirmation to running.
    if nxt == "running" and current not in {"running", "awaiting_confirmation"}:
        raise HTTPException(status_code=400, detail="use /tasks/{task_id}/start to enter running")

    patched = store.patch_status(task_id, nxt)
    if current == "awaiting_confirmation" and nxt == "running":
        patched = store.patch_task(
            task_id,
            {"stageGateResumeApproved": _has_pending_stage_gate(task)},
        ) or patched
    return project_public_task(patched)


@router.get("/{task_id}/execution-policy", response_model=TaskExecutionPolicyOut)
def get_task_execution_policy(
    task_id: str,
    identity: RequestIdentity = Depends(resolve_request_identity),
) -> TaskExecutionPolicyOut:
    if settings.persistence_backend != "postgres":
        raise HTTPException(status_code=503, detail="durable_execution_requires_postgres")
    try:
        policy = CollaborationRepository().get_task_execution_policy(
            task_id=task_id, owner_id=identity["ownerId"], workspace_id=identity["workspaceId"],
        )
    except DurableExecutionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return TaskExecutionPolicyOut.model_validate(policy)


@router.patch("/{task_id}/execution-policy", response_model=TaskExecutionPolicyOut)
def patch_task_execution_policy(
    task_id: str,
    body: PatchTaskExecutionModeIn,
    identity: RequestIdentity = Depends(resolve_request_identity),
) -> TaskExecutionPolicyOut:
    if settings.persistence_backend != "postgres":
        raise HTTPException(status_code=503, detail="durable_execution_requires_postgres")
    try:
        policy = CollaborationRepository().set_task_execution_policy(
            task_id=task_id, owner_id=identity["ownerId"], workspace_id=identity["workspaceId"],
            mode=body.executionMode, changed_by=identity["createdBy"],
        )
    except DurableExecutionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return TaskExecutionPolicyOut.model_validate(policy)


@router.patch("/{task_id}/execution-mode", response_model=TaskOut)
def patch_task_execution_mode(
    task_id: str,
    body: PatchTaskExecutionModeIn,
    identity: RequestIdentity = Depends(resolve_request_identity),
) -> TaskOut:
    if settings.persistence_backend == "postgres":
        try:
            CollaborationRepository().set_task_execution_policy(
                task_id=task_id, owner_id=identity["ownerId"], workspace_id=identity["workspaceId"],
                mode=body.executionMode, changed_by=identity["createdBy"],
            )
        except DurableExecutionError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        task = ensure_task_in_scope(TaskRepository().get_task(task_id), identity)
        return project_public_task(task)
    task = ensure_task_in_scope(store.get_task(task_id), identity)
    completed_substage = _current_completed_substage_for_approved_resume(task)
    can_continue_completed_node_automatically = (
        body.executionMode == "automatic_research"
        and bool(completed_substage)
        and _has_reviewable_execution_output(task, completed_substage)
    )
    if (
        task.get("status") == "running"
        and not _current_running_substage_key(task)
        and not can_continue_completed_node_automatically
    ):
        raise HTTPException(status_code=409, detail=ACTIVE_SUBSTAGE_RECOVERY_DETAIL)
    patched = store.patch_task(task_id, {"executionMode": body.executionMode})
    if not patched:
        raise HTTPException(status_code=404, detail="task not found")
    return project_public_task(patched)


@router.get("/{task_id}/artifacts/{artifact_id}/content", response_model=ArtifactContentOut)
def get_artifact_content(task_id: str, artifact_id: str, identity: RequestIdentity = Depends(resolve_request_identity)) -> ArtifactContentOut:
    task, artifact = _read_task_scoped_artifact(task_id, artifact_id, identity)
    if artifact.get("_canonicalStorageReferenceMissing"):
        raise HTTPException(status_code=404, detail={"code": "artifact_content_unavailable", "artifactId": artifact_id})
    try:
        content, source_mode = load_artifact_content(
            task_id,
            artifact,
            task.get("capabilityCalls") or [],
            allow_replay_fallback=settings.persistence_backend != "postgres",
        )
    except (FileNotFoundError, NotImplementedError, PermissionError, ValueError) as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "artifact_content_unavailable", "artifactId": artifact_id},
        ) from exc
    def load_source(reference: str) -> str:
        source_task, source = _read_task_scoped_artifact(task_id, reference, identity)
        return load_artifact_content(
            task_id, source, source_task.get("capabilityCalls") or [],
            allow_replay_fallback=settings.persistence_backend != "postgres",
        )[0]

    display_projection = None
    try:
        display_projection = historical_record_display_projection(
            task, artifact, content, load_source=load_source,
        )
    except (OSError, ValueError, RuntimeError, KeyError, HTTPException):
        logging.getLogger(__name__).exception(
            "historical_record_display_projection_unavailable artifact_id=%s", artifact_id,
        )
    return ArtifactContentOut(
        artifactId=artifact_id,
        path=artifact.get("path") or "",
        format=artifact.get("format"),
        sourceMode=source_mode,
        content=content,
        displayProjection=display_projection,
        evidenceRefs=artifact.get("evidenceRefs") or [],
        linkedMaterialIds=artifact.get("linkedMaterialIds") or [],
    )


@router.get("/{task_id}/artifacts/{artifact_id}/raw")
def get_artifact_raw(task_id: str, artifact_id: str, identity: RequestIdentity = Depends(resolve_media_identity)):
    task, artifact = _read_task_scoped_artifact(task_id, artifact_id, identity)
    if artifact.get("_canonicalStorageReferenceMissing"):
        raise HTTPException(status_code=404, detail={"code": "artifact_content_unavailable", "artifactId": artifact_id})
    fmt = (artifact.get("format") or "").lower()
    media_types = {"pdf": "application/pdf", "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "svg": "image/svg+xml"}
    media_type = media_types.get(fmt, "application/octet-stream")
    if settings.persistence_backend == "postgres":
        try:
            blob, _source_mode = load_artifact_binary(
                task_id, artifact, allow_replay_fallback=False
            )
        except (FileNotFoundError, PermissionError, ValueError) as exc:
            raise HTTPException(
                status_code=404,
                detail={"code": "artifact_content_unavailable", "artifactId": artifact_id},
            ) from exc
        return Response(content=blob, media_type=media_type)
    try:
        path = resolve_artifact_path(
            task_id, artifact, task.get("capabilityCalls") or [], allow_replay_fallback=True
        )
    except (FileNotFoundError, PermissionError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="artifact content unavailable") from exc
    if not path:
        raise HTTPException(status_code=404, detail="artifact content unavailable")
    return FileResponse(path, media_type=media_type)


@router.get("/{task_id}/workunits/{work_unit_id}/progress")
def get_work_unit_progress(task_id: str, work_unit_id: str, identity: RequestIdentity = Depends(resolve_request_identity)):
    """Live read of one work unit's execution progress file (transient projection).

    Durable skill scripts append timestamped milestone lines to
    ``<OPENAIR_WORKSPACE_ROOT>/<task_id>/<work_unit_id>/progress.md`` while the
    substage runs; the workbench polls this route to render the 执行进展
    panel.  This is deliberately NOT an artifact — the durable record stays
    the Result/Artifact set; progress is a live view of shared workspace
    state.  Missing file yields ``{"exists": false}`` so callers can hide the
    panel without error handling.
    """
    import os
    from datetime import datetime, timezone
    from pathlib import Path

    from app.execution.declared_skill_files import declared_file_segment

    if settings.persistence_backend == "postgres":
        ensure_task_in_scope(read_task_scope(task_id), identity)
    ws_root = str(os.environ.get("OPENAIR_WORKSPACE_ROOT") or "").strip()
    if not ws_root:
        raise HTTPException(
            status_code=404,
            detail={"code": "work_unit_progress_unavailable", "taskId": task_id},
        )
    path = (
        Path(ws_root)
        / declared_file_segment(task_id)
        / declared_file_segment(work_unit_id)
        / "progress.md"
    )
    if not path.is_file():
        return {"exists": False, "content": None, "updatedAt": None}
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
        updated_at = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
    except OSError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "work_unit_progress_unavailable", "taskId": task_id},
        ) from exc
    if not content.strip():
        return {"exists": False, "content": None, "updatedAt": None}
    return {"exists": True, "content": content, "updatedAt": updated_at}


@router.get("/{task_id}/artifacts/{artifact_id}/tree")
def get_artifact_tree(task_id: str, artifact_id: str, identity: RequestIdentity = Depends(resolve_request_identity)):
    _task, artifact = _read_task_scoped_artifact(task_id, artifact_id, identity)
    if artifact.get("_canonicalStorageReferenceMissing"):
        raise HTTPException(status_code=404, detail={"code": "artifact_content_unavailable", "artifactId": artifact_id})
    try:
        root = resolve_artifact_tree_root(
            task_id,
            artifact,
            allow_replay_fallback=settings.persistence_backend != "postgres",
        )
    except (FileNotFoundError, PermissionError, ValueError) as exc:
        raise HTTPException(status_code=404, detail={"code": "artifact_content_unavailable", "artifactId": artifact_id}) from exc
    tree = build_file_tree(root)
    if not tree:
        raise HTTPException(status_code=404, detail={"code": "artifact_content_unavailable", "artifactId": artifact_id})
    return tree


@router.get("/{task_id}/artifacts/{artifact_id}/files/{filename:path}")
def get_artifact_sibling_file(task_id: str, artifact_id: str, filename: str, identity: RequestIdentity = Depends(resolve_request_identity)):
    """Serve a durable sibling file without consulting a TaskStore snapshot."""
    _task, artifact = _read_task_scoped_artifact(task_id, artifact_id, identity)
    if artifact.get("_canonicalStorageReferenceMissing"):
        raise HTTPException(status_code=404, detail={"code": "artifact_content_unavailable", "artifactId": artifact_id})
    media_types = {
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "gif": "image/gif",
        "svg": "image/svg+xml",
        "webp": "image/webp",
    }
    if settings.persistence_backend == "postgres":
        try:
            blob, sibling = load_artifact_sibling_bytes(
                task_id, artifact, filename, allow_legacy_fallback=False
            )
        except (FileNotFoundError, PermissionError, ValueError) as exc:
            raise HTTPException(
                status_code=404,
                detail={"code": "artifact_content_unavailable", "artifactId": artifact_id},
            ) from exc
        ext = sibling.suffix.lstrip(".").lower()
        return Response(content=blob, media_type=media_types.get(ext, "application/octet-stream"))
    try:
        sibling = resolve_artifact_sibling(
            task_id, artifact, filename, allow_legacy_fallback=True
        )
    except (FileNotFoundError, PermissionError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="artifact file unavailable") from exc
    ext = sibling.suffix.lstrip(".").lower()
    return FileResponse(sibling, media_type=media_types.get(ext, "application/octet-stream"))


@router.post("/{task_id}/stages/{stage_key}/result", response_model=TaskOut)
def save_stage_result(task_id: str, stage_key: str, body: StageResultIn, identity: RequestIdentity = Depends(resolve_request_identity)) -> TaskOut:
    task = ensure_task_in_scope(store.get_task(task_id), identity)
    if task.get("status") == "draft":
        raise HTTPException(status_code=409, detail="draft task cannot write stage results")
    updated = store.save_stage_result(task_id, stage_key, body.model_dump(exclude_none=True))
    if not updated:
        raise HTTPException(status_code=404, detail="task or stage not found")
    return project_public_task(updated)


@router.post("/{task_id}/advance-substage")
def advance_substage(
    task_id: str,
    substage_key: str | None = None,
    identity: RequestIdentity = Depends(resolve_request_identity),
) -> dict:
    """Complete current substage and advance to the next one in workflow order."""
    task = ensure_task_in_scope(store.get_task(task_id), identity)
    if task.get("status") != "running":
        raise HTTPException(status_code=409, detail="task must be running to advance workflow")

    resume_approved = bool(task.get("stageGateResumeApproved")) and _has_pending_stage_gate(task)
    current_substage_key = substage_key
    if not current_substage_key:
        current_substage_key = _current_running_substage_key(task, include_legacy_fallback=True)
        if not current_substage_key and resume_approved:
            current_substage_key = _current_completed_substage_for_approved_resume(task)

    if not current_substage_key:
        raise HTTPException(status_code=409, detail=ACTIVE_SUBSTAGE_RECOVERY_DETAIL)

    _ensure_research_start_confirmed_for_advance(task)
    if not _has_reviewable_execution_output(task, current_substage_key):
        raise HTTPException(
            status_code=409,
            detail=(
                "current substage has no completed real capability call with a reviewable output; "
                "run the current skill and review its artifact before advancing"
            ),
        )

    result = store.advance_substage(
        task_id,
        current_substage_key,
        respect_stage_gate=not resume_approved,
    )
    if not result:
        raise HTTPException(status_code=404, detail="advance failed")
    if resume_approved:
        store.patch_task(
            task_id,
            {"stageGateResumeApproved": False, "stageGateResumePending": None},
        )
    return result


@router.get("/{task_id}/substage-skills")
def get_substage_skills(
    task_id: str,
    substage_key: str = Query(..., alias="substage_key"),
    identity: RequestIdentity = Depends(resolve_request_identity),
) -> dict:
    """Return L2 skill for the given substage. L3 skills are internal and not exposed to users."""
    task = ensure_task_in_scope(store.get_task(task_id), identity)
    pack = task.get("skillPack", "sdr")
    from app.execution.agent_service import _load_l2_skill_meta
    from app.modules import pack_config

    l2_meta = _load_l2_skill_meta(pack, substage_key)
    l2_name = l2_meta.get("claude_name") or substage_key.replace("_", "-")

    catalog_label = next(
        (
            entry.get("label")
            for row in task.get("stageCatalog") or []
            for entry in row.get("allowedSubStages") or []
            if entry.get("key") == substage_key and entry.get("label")
        ),
        None,
    )
    label = catalog_label or pack_config.get_substage_label(substage_key, pack=pack)
    return {
        "substageKey": substage_key,
        "skills": [l2_name],
        "labels": {l2_name: label},
    }
