"""Plaza AgentSession routes — independent of ResearchDesign / research-start."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict

from app.auth import RequestIdentity, resolve_request_identity
from app.models.agent_session import (
    AgentSessionCreateIn,
    AgentSessionListOut,
    AgentSessionMessageCreateIn,
    AgentSessionOut,
    AgentSessionSummaryOut,
)
from app.modules.agent_session_service import (
    AgentSessionError,
    attachment_content_disposition,
    build_session_artifacts_zip,
    create_agent_session,
    get_agent_session,
    list_agent_sessions,
    read_session_artifact_bytes,
)
from app.modules.model_job_queue import (
    ModelJobConflict,
    ModelJobQueueRepository,
    ModelJobResourceNotFound,
)


router = APIRouter(prefix="/agent-sessions", tags=["agent-sessions"])


class AgentSessionTurnAcceptedOut(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    jobId: str
    sessionId: str
    state: str = "accepted"


def _map_error(exc: AgentSessionError) -> HTTPException:
    code = exc.code
    if code == "catalog_unavailable":
        return HTTPException(status_code=503, detail="resource_catalog_unavailable")
    if code in {"not_found", "artifact_not_found", "artifact_content_unavailable"}:
        return HTTPException(status_code=404, detail=code)
    if code in {"not_tryable", "unsupported_resource_type", "missing_capability", "agent_unavailable", "no_steps"}:
        return HTTPException(status_code=422, detail=code)
    if code in {"invalid_resource", "empty_message"}:
        return HTTPException(status_code=422, detail=code)
    return HTTPException(status_code=400, detail=code)


@router.post("", response_model=AgentSessionOut, status_code=status.HTTP_201_CREATED)
def create_session(
    body: AgentSessionCreateIn,
    identity: RequestIdentity = Depends(resolve_request_identity),
) -> AgentSessionOut:
    try:
        row = create_agent_session(
            resource_id=body.resourceId,
            owner_id=identity["ownerId"],
            workspace_id=identity["workspaceId"],
        )
    except AgentSessionError as exc:
        raise _map_error(exc) from exc
    return AgentSessionOut.model_validate(row)


@router.get("/history", response_model=AgentSessionListOut)
def list_sessions(
    identity: RequestIdentity = Depends(resolve_request_identity),
    limit: int = Query(default=50, ge=1, le=200),
) -> AgentSessionListOut:
    items = list_agent_sessions(
        owner_id=identity["ownerId"],
        workspace_id=identity["workspaceId"],
        limit=limit,
    )
    return AgentSessionListOut(
        items=[AgentSessionSummaryOut.model_validate(item) for item in items]
    )


@router.get("/{session_id}", response_model=AgentSessionOut)
def read_session(
    session_id: str,
    identity: RequestIdentity = Depends(resolve_request_identity),
) -> AgentSessionOut:
    row = get_agent_session(
        session_id=session_id,
        owner_id=identity["ownerId"],
        workspace_id=identity["workspaceId"],
    )
    if row is None:
        raise HTTPException(status_code=404, detail="agent_session_not_found")
    return AgentSessionOut.model_validate(row)


@router.post(
    "/{session_id}/messages",
    response_model=AgentSessionTurnAcceptedOut,
    status_code=status.HTTP_202_ACCEPTED,
)
def post_message(
    session_id: str,
    body: AgentSessionMessageCreateIn,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    identity: RequestIdentity = Depends(resolve_request_identity),
) -> AgentSessionTurnAcceptedOut:
    if not idempotency_key:
        raise HTTPException(status_code=428, detail="Idempotency-Key is required")
    try:
        ModelJobQueueRepository().enqueue(
            resource_kind="agent_session",
            resource_id=session_id,
            owner_id=identity["ownerId"],
            workspace_id=identity["workspaceId"],
            job_id=idempotency_key,
            job_kind="agent_session_turn",
            expected_revision=None,
            command_payload={"content": body.content},
        )
    except ModelJobResourceNotFound as exc:
        raise HTTPException(status_code=404, detail="agent_session_not_found") from exc
    except ModelJobConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return AgentSessionTurnAcceptedOut(jobId=idempotency_key, sessionId=session_id)


@router.get("/{session_id}/artifacts/download-zip")
def download_session_artifacts_zip(
    session_id: str,
    identity: RequestIdentity = Depends(resolve_request_identity),
) -> Response:
    from urllib.parse import quote

    try:
        blob, filename = build_session_artifacts_zip(
            session_id=session_id,
            owner_id=identity["ownerId"],
            workspace_id=identity["workspaceId"],
        )
    except AgentSessionError as exc:
        raise _map_error(exc) from exc
    ascii_name = f"agent_session_{session_id[-12:]}_artifacts.zip"
    return Response(
        content=blob,
        media_type="application/zip",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{ascii_name}"; '
                f"filename*=UTF-8''{quote(filename)}"
            )
        },
    )


@router.get("/{session_id}/artifacts/{artifact_id}/download")
def download_session_artifact(
    session_id: str,
    artifact_id: str,
    identity: RequestIdentity = Depends(resolve_request_identity),
) -> Response:
    try:
        blob, media_type, filename = read_session_artifact_bytes(
            session_id=session_id,
            owner_id=identity["ownerId"],
            workspace_id=identity["workspaceId"],
            artifact_id=artifact_id,
        )
    except AgentSessionError as exc:
        raise _map_error(exc) from exc
    return Response(
        content=blob,
        media_type=media_type,
        headers={
            "Content-Disposition": attachment_content_disposition(filename, artifact_id=artifact_id)
        },
    )
