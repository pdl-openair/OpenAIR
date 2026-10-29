"""Admin audit log query + heavy-queue status (S04-01)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from app.auth import RequestIdentity, require_admin
from app.models.governance_schemas import AuditLogOut, HeavyQueueStatus, audit_from_row
from app.modules.audit_repository import get_audit_repository, record_audit
from app.modules.heavy_task_queue import get_heavy_task_queue

router = APIRouter(tags=["audit"])


@router.get("/admin/audit-logs", response_model=list[AuditLogOut])
def list_audit_logs(
    _admin: RequestIdentity = Depends(require_admin),
    action: str | None = Query(default=None),
    actor_id: str | None = Query(default=None, alias="actorId"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[AuditLogOut]:
    rows = get_audit_repository().list_logs(action=action, actor_id=actor_id, limit=limit, offset=offset)
    return [audit_from_row(r) for r in rows]


@router.get("/admin/heavy-queue", response_model=HeavyQueueStatus)
def heavy_queue_status(_admin: RequestIdentity = Depends(require_admin)) -> HeavyQueueStatus:
    return HeavyQueueStatus(**get_heavy_task_queue().snapshot())


class ExceptionAuditMiddleware(BaseHTTPMiddleware):
    """Persist uncaught exceptions to the audit log, then re-raise for normal handlers."""

    async def dispatch(self, request: Request, call_next) -> Response:
        try:
            return await call_next(request)
        except Exception as exc:
            from fastapi import HTTPException as FastAPIHTTPException
            from fastapi.exceptions import RequestValidationError
            from starlette.exceptions import HTTPException as StarletteHTTPException

            from app.modules.quota_service import QuotaExceeded

            if isinstance(
                exc,
                (FastAPIHTTPException, StarletteHTTPException, RequestValidationError, QuotaExceeded),
            ):
                raise

            actor = request.headers.get("x-openair-owner-id")
            forwarded = request.headers.get("x-forwarded-for")
            ip = forwarded.split(",")[0].strip() if forwarded else (request.client.host if request.client else None)
            record_audit(
                "uncaught_exception",
                actor_id=actor,
                outcome="failure",
                detail={
                    "path": str(request.url.path),
                    "method": request.method,
                    "errorType": type(exc).__name__,
                    "message": str(exc)[:500],
                },
                ip=ip,
                user_agent=request.headers.get("user-agent"),
            )
            raise
