"""Ops-only routes (guarded by OPENAIR_MIGRATION_TOKEN)."""

from __future__ import annotations

import logging
import os
from secrets import compare_digest
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import ValidationError

from app.models.schemas import TaskOut
from app.modules.task_store import store

router = APIRouter(prefix="/admin", tags=["admin"])
_log = logging.getLogger(__name__)


def _migration_import_enabled() -> bool:
    return (os.environ.get("OPENAIR_MIGRATION_IMPORT_ENABLED") or "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _require_migration_token(x_openair_migration_token: str | None) -> None:
    if not _migration_import_enabled():
        raise HTTPException(status_code=503, detail="migration import is disabled")
    expected = (os.environ.get("OPENAIR_MIGRATION_TOKEN") or "").strip()
    if not expected:
        raise HTTPException(status_code=503, detail="migration import is not configured")
    provided = (x_openair_migration_token or "").strip()
    if not provided or not compare_digest(provided, expected):
        raise HTTPException(status_code=403, detail="invalid migration token")


@router.post("/tasks/import", response_model=TaskOut)
def import_task(
    body: dict[str, Any],
    x_openair_migration_token: str | None = Header(default=None),
    overwrite: bool = Query(default=False),
) -> TaskOut:
    """Import a full persisted task JSON (e.g. data/tasks/<task_id>.json)."""
    _require_migration_token(x_openair_migration_token)
    try:
        task = store.import_persisted_task(body, overwrite=overwrite)
    except ValueError as exc:
        message = str(exc)
        if message.startswith("task already exists:"):
            raise HTTPException(status_code=409, detail=message) from exc
        raise HTTPException(status_code=400, detail=message) from exc
    try:
        validated = TaskOut.model_validate(task)
    except ValidationError as exc:
        raise HTTPException(status_code=500, detail="imported task failed response validation") from exc
    _log.info("imported persisted task %s overwrite=%s", validated.taskId, overwrite)
    return validated
