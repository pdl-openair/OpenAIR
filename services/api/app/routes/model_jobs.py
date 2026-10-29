"""Read-only recovery surface for durable model jobs."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict

from app.auth import RequestIdentity, resolve_request_identity
from app.modules.model_job_queue import (
    ModelJobQueueRepository,
    ModelJobResourceNotFound,
)


router = APIRouter(prefix="/model-jobs", tags=["model-jobs"])


class ModelJobStatusOut(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    jobId: str
    jobKind: str
    resourceKind: Literal["task", "agent_session"]
    resourceId: str
    state: Literal["accepted", "running", "succeeded", "blocked", "failed"]
    result: dict[str, Any] | None = None
    errorCode: str | None = None


@router.get(
    "/{resource_kind}/{resource_id}/{job_id}",
    response_model=ModelJobStatusOut,
    response_model_exclude_none=True,
)
def read_model_job(
    resource_kind: Literal["task", "agent_session"],
    resource_id: str,
    job_id: str,
    identity: RequestIdentity = Depends(resolve_request_identity),
) -> ModelJobStatusOut:
    try:
        row = ModelJobQueueRepository().read(
            resource_kind=resource_kind,
            resource_id=resource_id,
            owner_id=identity["ownerId"],
            workspace_id=identity["workspaceId"],
            job_id=job_id,
        )
    except ModelJobResourceNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="model_job_not_found")
    state = {"queued": "accepted", "leased": "running"}.get(
        str(row["state"]), str(row["state"])
    )
    return ModelJobStatusOut(
        jobId=str(row["job_id"]),
        jobKind=str(row["job_kind"]),
        resourceKind=resource_kind,
        resourceId=resource_id,
        state=state,
        result=(dict(row["result_payload"]) if isinstance(row.get("result_payload"), dict) else None),
        errorCode=(str(row["last_error_code"]) if row.get("last_error_code") else None),
    )
