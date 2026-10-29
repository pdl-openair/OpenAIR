"""Read-only browser projections for durable execution runs."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth import RequestIdentity, resolve_request_identity
from app.config import settings
from app.modules.durable_execution import DurableExecutionRepository, serialize_run

router = APIRouter(prefix="/execution-runs", tags=["execution-runs"])


@router.get("/task/{task_id}")
def get_current_task_execution_run(
    task_id: str,
    identity: RequestIdentity = Depends(resolve_request_identity),
) -> dict:
    if settings.persistence_backend != "postgres":
        raise HTTPException(status_code=503, detail="durable_execution_requires_postgres")
    run = DurableExecutionRepository().get_latest_run_for_task(
        task_id,
        identity["ownerId"],
        identity["workspaceId"],
    )
    return {"run": serialize_run(run) if run else None}


@router.get("/{run_id}")
def get_execution_run(run_id: str, identity: RequestIdentity = Depends(resolve_request_identity)) -> dict:
    if settings.persistence_backend != "postgres":
        raise HTTPException(status_code=503, detail="durable_execution_requires_postgres")
    run = DurableExecutionRepository().get_run(run_id, identity["ownerId"], identity["workspaceId"])
    if not run:
        raise HTTPException(status_code=404, detail="execution run not found")
    return serialize_run(run)


@router.get("/{run_id}/events")
def get_execution_events(
    run_id: str,
    after_sequence: int = Query(0, alias="afterSequence", ge=0),
    identity: RequestIdentity = Depends(resolve_request_identity),
) -> dict:
    if settings.persistence_backend != "postgres":
        raise HTTPException(status_code=503, detail="durable_execution_requires_postgres")
    events = DurableExecutionRepository().list_events(run_id, identity["ownerId"], identity["workspaceId"], after_sequence)
    return {"runId": run_id, "events": [
        {"sequence": row["sequence"], "type": row["type"], "payload": row["payload"], "occurredAt": row["occurred_at"].isoformat().replace("+00:00", "Z")}
        for row in events
    ]}


@router.get("/{run_id}/call")
def get_execution_call(run_id: str, identity: RequestIdentity = Depends(resolve_request_identity)) -> dict:
    """Read-only durable call projection; it never consults TaskStore."""
    if settings.persistence_backend != "postgres":
        raise HTTPException(status_code=503, detail="durable_execution_requires_postgres")
    call = DurableExecutionRepository().get_call(run_id, identity["ownerId"], identity["workspaceId"])
    if not call:
        raise HTTPException(status_code=404, detail="execution capability call not found")
    return {
        "callId": call["call_id"], "runId": call["run_id"], "taskId": call["task_id"],
        "state": call["state"], "sourceMode": call["source_mode"],
        "createdAt": call["created_at"].isoformat(), "startedAt": call["started_at"].isoformat() if call["started_at"] else None,
        "endedAt": call["ended_at"].isoformat() if call["ended_at"] else None,
    }


@router.get("/{run_id}/artifacts")
def list_execution_artifacts(run_id: str, identity: RequestIdentity = Depends(resolve_request_identity)) -> dict:
    """Read-only durable artifact projection for restart and GET audits."""
    if settings.persistence_backend != "postgres":
        raise HTTPException(status_code=503, detail="durable_execution_requires_postgres")
    artifacts = DurableExecutionRepository().list_artifacts(run_id, identity["ownerId"], identity["workspaceId"])
    return {"runId": run_id, "artifacts": [
        {
            "artifactId": row["artifact_id"], "runId": row["source_execution_run_id"],
            "callId": row["source_capability_call_id"], "sourceMode": row["source_mode"],
            "metadata": row["metadata"], "storageType": row["storage_type"],
            "storageRef": row["storage_ref"],
            "contentSize": row["content_size"], "contentSha256": row["content_sha256"],
        }
        for row in artifacts
    ]}
