import json
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from app.auth import RequestIdentity, resolve_request_identity
from app.models.schemas import TaskExecutionMode
from app.modules.intent_session_store import intent_session_store
from app.modules.research_start_repository import (
    CatalogSessionConflictError,
    InvalidIntentError,
    RequestConflictError,
    ResearchStartAdmissionFullError,
    ResearchStartRepository,
    SessionBusyError,
    SessionNotFoundError,
    TerminalStateError,
)
from app.modules.resource_catalog import ResourceCatalogUnavailableError, get_resource_for_research_start


router = APIRouter(prefix="/research-start", tags=["research-start"])
resource_catalog_router = APIRouter(prefix="/resources", tags=["resources"])
research_start_repository = ResearchStartRepository()

SourcePage = Literal["home", "board", "starter_plaza", "sample"]
KeyLiteratureStatus = Literal["ready", "partial", "needs_clarification", "no_reliable_candidates"]


class PlanningSourceRefIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["research_package"]
    id: str = Field(min_length=1)
    version: str = Field(min_length=1)


class ResearchStartSubmitIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input: str = Field(min_length=1, max_length=4000)
    sourcePage: SourcePage = "home"
    selectedHint: str | None = None
    presetQuery: str | None = None
    clientRequestId: str = Field(min_length=1, max_length=160)
    sessionId: str | None = Field(default=None, pattern=r"^intent_[0-9a-f]{12}$")
    requestedWorkflow: Literal[
        "pvd_process_optimization_workflow",
        "opentrajectory_research_workflow",
        "materials_candidate_discovery_workflow",
        "materials_full_discovery_workflow",
        "cs_paper_reproduction_workflow",
    ] | None = None
    executionMode: TaskExecutionMode = "stage_confirmation"
    createClarificationDraft: bool = False
    planningSources: list[PlanningSourceRefIn] = Field(default_factory=list)


class KeyLiteraturePositioningIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=4000)
    topK: int = Field(default=6, ge=1, le=12)


class KeyLiteratureQuerySummaryOut(BaseModel):
    originalQuestion: str
    normalizedQuery: str
    topK: int
    retrievalResultCount: int
    dedupedResultCount: int
    relevantCandidateCount: int


class KeyLiteratureSearchStrategyOut(BaseModel):
    normalizedQuery: str
    queryVariants: list[str] = Field(default_factory=list)
    coreTerms: list[str] = Field(default_factory=list)
    queryPhrases: list[str] = Field(default_factory=list)
    sourcePriority: list[str] = Field(default_factory=list)
    topK: int | None = None
    boundary: str


class KeyLiteratureReadingOut(BaseModel):
    rank: int
    sourceRef: str
    title: str
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    venue: str | None = None
    url: str | None = None
    doi: str | None = None
    arxivId: str | None = None
    sourceName: str
    sourceMode: str
    sourceModeLabel: str
    role: str
    whyRead: str
    matchedSignals: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    confidence: float
    provenance: dict


class KeyLiteratureCoverageOut(BaseModel):
    coveredAspects: list[str] = Field(default_factory=list)
    missingAspects: list[str] = Field(default_factory=list)


class KeyLiteratureSourceAttemptOut(BaseModel):
    sourceName: str
    query: str
    status: str
    retrievedAt: str
    resultCount: int
    error: str | None = None


class KeyLiteraturePositioningOut(BaseModel):
    status: KeyLiteratureStatus
    querySummary: KeyLiteratureQuerySummaryOut
    searchStrategy: KeyLiteratureSearchStrategyOut
    keyReadings: list[KeyLiteratureReadingOut] = Field(default_factory=list)
    candidateReadings: list[KeyLiteratureReadingOut] = Field(default_factory=list)
    coverage: KeyLiteratureCoverageOut
    sourceAttempts: list[KeyLiteratureSourceAttemptOut] = Field(default_factory=list)
    nextQuestions: list[str] = Field(default_factory=list)
    userFacingSummary: str


class ResearchStartAcceptedOut(BaseModel):
    sessionId: str
    requestId: str
    status: Literal["queued", "leased", "succeeded", "failed", "unknown"]
    reservedTaskId: str
    reservedArtifactId: str


class DurableResearchStartResultOut(BaseModel):
    outcome: str
    sessionId: str
    taskId: str | None = None


class ResearchStartPlanningKeyResultsOut(BaseModel):
    planningMode: Literal["lightweight", "partial", "sdr"] | None = None
    substageCount: int = Field(ge=0)
    researchQuestions: list[str] = Field(default_factory=list)
    objectives: list[str] = Field(default_factory=list)
    targetOutputs: list[str] = Field(default_factory=list)
    expectedArtifactTypes: list[str] = Field(default_factory=list)
    planningRationale: str | None = None


class ResearchStartThoughtOut(BaseModel):
    key: str
    state: Literal["forming", "formed"]
    text: str


class ResearchStartPlanningProgressOut(BaseModel):
    code: Literal[
        "intent_received",
        "still_queued",
        "queued_stale",
        "worker_unavailable",
        "worker_assigned",
        "organizing_intent",
        "planning_design",
        "compiling_design",
        "designing_research",
        "repairing_design",
        "design_validated",
        "design_ready",
        "failed",
        "external_result_unknown",
    ]
    message: str
    elapsedSeconds: int = Field(ge=0)
    startedAt: str | None = None
    updatedAt: str | None = None
    attemptOrdinal: int | None = Field(default=None, ge=1)
    repairRound: int = Field(default=0, ge=0)
    keyResults: ResearchStartPlanningKeyResultsOut | None = None
    thoughts: list[ResearchStartThoughtOut] = Field(default_factory=list)


class ResearchStartSessionStatusOut(BaseModel):
    sessionId: str
    status: Literal["running", "completed", "failed", "external_result_unknown"]
    currentPhase: Literal["understanding", "organizing", "planning"]
    reservedTaskId: str | None = None
    planningProgress: ResearchStartPlanningProgressOut | None = None
    result: DurableResearchStartResultOut | None = None
    userMessage: str | None = None
    recoveryReason: Literal[
        "still_queued",
        "queued_stale",
        "worker_unavailable",
        "external_result_unknown",
    ] | None = None
    retryAfter: int | None = Field(default=None, ge=1)
    automaticRetryAllowed: bool | None = None
    catalogIntentId: str | None = None
    selectedResourceId: str | None = None
    selectedResourceVersion: str | None = None
    selectedCapabilityId: str | None = None


class ResearchStartDisplayContextOut(BaseModel):
    taskId: str
    sessionId: str | None = None
    originalInput: str | None = None
    displayTitle: str | None = None
    titlePending: bool = False


@router.get("/tasks/{task_id}/display-context", response_model=ResearchStartDisplayContextOut)
def get_research_start_display_context(
    task_id: str,
    identity: Annotated[RequestIdentity, Depends(resolve_request_identity)],
    sessionId: str | None = None,
) -> ResearchStartDisplayContextOut:
    from app.modules.research_start_display_context import ResearchStartDisplayRepository

    context = ResearchStartDisplayRepository().read(
        task_id, identity["ownerId"], identity["workspaceId"], sessionId
    )
    if sessionId is not None and context["sessionId"] is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="startup context unavailable"
        )
    return ResearchStartDisplayContextOut.model_validate(context)


class ResearchStartIntentPromoteOut(BaseModel):
    sessionId: str
    selectedResourceId: str
    selectedResourceName: str
    selectedResourceKind: Literal["skill", "agent", "workflow"]


@resource_catalog_router.post(
    "/research-start-intents/{catalog_intent_id}/promote",
    response_model=ResearchStartIntentPromoteOut,
)
def promote_resource_research_start_intent(
    catalog_intent_id: str,
    identity: RequestIdentity = Depends(resolve_request_identity),
) -> ResearchStartIntentPromoteOut:
    catalog_intent = intent_session_store.get_scoped(
        session_id=catalog_intent_id,
        owner_id=identity["ownerId"],
        workspace_id=identity["workspaceId"],
    )
    if catalog_intent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="catalog intent not found")

    selected_resource_id = catalog_intent.get("selectedResourceId")
    selected_resource_version = catalog_intent.get("selectedResourceVersion")
    selected_capability_id = catalog_intent.get("selectedCapabilityId")
    if not all(
        isinstance(value, str) and value
        for value in (selected_resource_id, selected_resource_version, selected_capability_id)
    ):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="catalog intent resource facts are invalid")

    try:
        resource = get_resource_for_research_start(
            selected_resource_id,
            selected_resource_version,
            selected_capability_id,
        )
    except ResourceCatalogUnavailableError as exc:
        raise HTTPException(status_code=503, detail="resource_catalog_unavailable") from exc
    if resource is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="catalog intent resource snapshot conflict")
    if (
        str(resource["releaseVersion"]) != selected_resource_version
        or resource["capabilityId"] != selected_capability_id
    ):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="catalog intent resource snapshot conflict")

    try:
        session = research_start_repository.reserve_catalog_session(
            identity["ownerId"],
            identity["workspaceId"],
            catalog_intent_id,
            selected_resource_id,
            selected_resource_version,
            selected_capability_id,
        )
    except CatalogSessionConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.code) from exc

    kind_value = resource.get("kind") or resource.get("resourceType")
    if hasattr(kind_value, "value"):
        kind_value = kind_value.value
    if kind_value not in ("skill", "agent", "workflow"):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="catalog intent resource facts are invalid")
    resource_name = str(resource.get("name") or "").strip()
    if not resource_name:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="catalog intent resource facts are invalid")

    return ResearchStartIntentPromoteOut(
        sessionId=session["sessionId"],
        selectedResourceId=str(selected_resource_id),
        selectedResourceName=resource_name,
        selectedResourceKind=kind_value,
    )


@router.post("/key-literature-positioning", response_model=KeyLiteraturePositioningOut)
def key_literature_positioning(
    body: KeyLiteraturePositioningIn,
    identity: RequestIdentity = Depends(resolve_request_identity),
) -> KeyLiteraturePositioningOut:
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail={
            "code": "synchronous_model_route_retired",
            "use": "/api/research-start/submit",
        },
    )


def _reserve_public_research_start(
    body: ResearchStartSubmitIn,
    identity: RequestIdentity,
) -> ResearchStartAcceptedOut:
    try:
        reservation = research_start_repository.reserve(
            identity["ownerId"],
            identity["workspaceId"],
            body.clientRequestId,
            {
                "input": body.input,
                "sourcePage": body.sourcePage,
                "selectedHint": body.selectedHint,
                "presetQuery": body.presetQuery,
                "requestedWorkflow": body.requestedWorkflow,
                "sessionId": body.sessionId,
                "executionMode": body.executionMode,
                "createClarificationDraft": body.createClarificationDraft,
                "planningSources": [row.model_dump(mode="json") for row in body.planningSources],
            },
            session_id=body.sessionId,
            created_by=identity["createdBy"],
        )
    except InvalidIntentError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=exc.default_message) from exc
    except SessionNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=exc.default_message) from exc
    except ResearchStartAdmissionFullError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "code": exc.code,
                "message": str(exc),
                "retryAfter": exc.retry_after,
                "position": exc.position,
                "scope": exc.scope,
            },
            headers={"Retry-After": str(exc.retry_after)},
        ) from exc
    except (RequestConflictError, SessionBusyError, TerminalStateError) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.default_message) from exc
    # Reservation owns only the durable Research Start identity. The worker is
    # the sole planning authority and creates ResearchDesign Task state.
    return ResearchStartAcceptedOut.model_validate(reservation)


@router.post("/submit", response_model=ResearchStartAcceptedOut, status_code=status.HTTP_202_ACCEPTED)
def submit_research_start(
    body: ResearchStartSubmitIn,
    background_tasks: BackgroundTasks,
    identity: RequestIdentity = Depends(resolve_request_identity),
) -> ResearchStartAcceptedOut:
    from app.modules.research_start_title import generate_session_title

    accepted = _reserve_public_research_start(body, identity)
    background_tasks.add_task(
        generate_session_title, accepted.sessionId, identity["ownerId"], identity["workspaceId"]
    )
    return accepted


@router.get(
    "/sessions/{session_id}",
    response_model=ResearchStartSessionStatusOut,
)
def get_research_start_session(
    session_id: str,
    identity: RequestIdentity = Depends(resolve_request_identity),
) -> ResearchStartSessionStatusOut:
    try:
        return ResearchStartSessionStatusOut.model_validate(
            research_start_repository.get_scoped_session(
                identity["ownerId"], identity["workspaceId"], session_id
            )
        )
    except SessionNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="intent session not found") from exc


@router.post(
    "/sessions/{session_id}/retry",
    response_model=ResearchStartAcceptedOut,
    status_code=status.HTTP_202_ACCEPTED,
)
def retry_research_start_session(
    session_id: str,
    identity: RequestIdentity = Depends(resolve_request_identity),
) -> ResearchStartAcceptedOut:
    """Resume a recoverable Research Start session without creating a new Task/session."""
    try:
        return ResearchStartAcceptedOut.model_validate(
            research_start_repository.retry_external_result_unknown(
                identity["ownerId"], identity["workspaceId"], session_id
            )
        )
    except SessionNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=exc.default_message) from exc
    except ResearchStartAdmissionFullError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "code": exc.code,
                "message": str(exc),
                "retryAfter": exc.retry_after,
                "position": exc.position,
                "scope": exc.scope,
            },
            headers={"Retry-After": str(exc.retry_after)},
        ) from exc
    except TerminalStateError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="research start is not waiting for a recoverable retry",
        ) from exc


def _mark_research_start_failed(
    *,
    body: ResearchStartSubmitIn,
    identity: RequestIdentity,
) -> None:
    session = intent_session_store.get_by_request(
        owner_id=identity["ownerId"],
        workspace_id=identity["workspaceId"],
        client_request_id=body.clientRequestId,
    )
    if session and not isinstance(session.get("result"), dict):
        intent_session_store.fail(
            session["sessionId"],
            user_message="启动分析未完成。请返回研究起点检查输入后重试。",
        )


@router.post("/submit/stream")
async def submit_research_start_stream(
    body: ResearchStartSubmitIn,
    background_tasks: BackgroundTasks,
    identity: RequestIdentity = Depends(resolve_request_identity),
) -> StreamingResponse:
    from app.modules.research_start_title import generate_session_title

    accepted = _reserve_public_research_start(body, identity)
    background_tasks.add_task(
        generate_session_title, accepted.sessionId, identity["ownerId"], identity["workspaceId"]
    )

    async def event_iter():
        yield json.dumps({"type": "accepted", **accepted.model_dump(mode="json")}) + "\n"

    return StreamingResponse(
        event_iter(),
        media_type="application/x-ndjson",
        status_code=status.HTTP_202_ACCEPTED,
        background=background_tasks,
    )
