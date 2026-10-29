from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.auth import RequestIdentity, resolve_request_identity
from app.models.recovery import RecoveryRequestOut
from app.modules.recovery_repository import RecoveryError
from app.modules.recovery_service import RecoveryService


router = APIRouter(prefix="/tasks", tags=["recovery"])


class RecoveryErrorDetail(BaseModel):
    code: Literal[
        "recovery_not_found",
        "recovery_preflight_failed",
        "recovery_stale",
        "idempotency_payload_conflict",
        "recovery_mutex_conflict",
        "recovery_successor_exists",
        "recovery_terminal",
        "recovery_requires_postgres",
        "legacy_recovery_read_only",
    ]


class RecoveryErrorOut(BaseModel):
    detail: RecoveryErrorDetail


RECOVERY_ERROR_RESPONSES = {
    404: {"model": RecoveryErrorOut, "description": "Recovery task, request, or source Run is not visible."},
    409: {"model": RecoveryErrorOut, "description": "Recovery preflight, idempotency, mutex, or state transition conflict."},
    503: {"model": RecoveryErrorOut, "description": "Recovery requires the durable PostgreSQL authority."},
    410: {"model": RecoveryErrorOut, "description": "Generic recovery is retained as read-only history."},
}


def _raise(error: RecoveryError) -> None:
    raise HTTPException(status_code=error.status_code, detail={"code": error.code}) from error


@router.post(
    "/{task_id}/recovery-requests",
    responses=RECOVERY_ERROR_RESPONSES,
    deprecated=True,
)
def create_recovery_request(
    task_id: str,
    _identity: RequestIdentity = Depends(resolve_request_identity),
) -> None:
    del task_id
    raise HTTPException(status_code=410, detail={"code": "legacy_recovery_read_only"})


@router.get(
    "/{task_id}/recovery-requests",
    response_model=RecoveryRequestOut,
    responses=RECOVERY_ERROR_RESPONSES,
)
def discover_recovery_request(
    task_id: str,
    source_run_id: str = Query(alias="sourceRunId", min_length=1),
    expected_task_revision: int = Query(alias="expectedTaskRevision", ge=0),
    identity: RequestIdentity = Depends(resolve_request_identity),
) -> RecoveryRequestOut:
    try:
        result = RecoveryService().discover(
            task_id=task_id,
            source_run_id=source_run_id,
            expected_task_revision=expected_task_revision,
            owner_id=identity["ownerId"],
            workspace_id=identity["workspaceId"],
        )
    except RecoveryError as error:
        _raise(error)
    return RecoveryRequestOut.model_validate(result)


@router.post(
    "/{task_id}/recovery-requests/{recovery_request_id}/confirm",
    responses=RECOVERY_ERROR_RESPONSES,
    deprecated=True,
)
def confirm_recovery_request(
    task_id: str,
    recovery_request_id: str,
    _identity: RequestIdentity = Depends(resolve_request_identity),
) -> None:
    del task_id, recovery_request_id
    raise HTTPException(status_code=410, detail={"code": "legacy_recovery_read_only"})


@router.get(
    "/{task_id}/recovery-requests/{recovery_request_id}",
    response_model=RecoveryRequestOut,
    responses=RECOVERY_ERROR_RESPONSES,
)
def get_recovery_request(
    task_id: str,
    recovery_request_id: str,
    identity: RequestIdentity = Depends(resolve_request_identity),
) -> RecoveryRequestOut:
    try:
        result = RecoveryService().get(
            task_id=task_id, request_id=recovery_request_id, owner_id=identity["ownerId"], workspace_id=identity["workspaceId"],
        )
    except RecoveryError as error:
        _raise(error)
    return RecoveryRequestOut.model_validate(result)
