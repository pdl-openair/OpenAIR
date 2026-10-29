"""Admin user lifecycle: list / disable / restore (S04-01)."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.auth import RequestIdentity, require_admin
from app.models.governance_schemas import AdminSetActiveRequest, AdminUserRow
from app.modules.audit_repository import record_audit
from app.modules.user_repository import UserRepositoryBackend, get_user_repository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin-users"])


def _user_repo() -> UserRepositoryBackend:
    return get_user_repository()


def _row(user: dict) -> AdminUserRow:
    return AdminUserRow(
        userId=user["user_id"],
        email=user["email"],
        username=user["username"],
        displayName=user.get("display_name"),
        role=str(user.get("role") or "user"),
        isActive=bool(user.get("is_active", True)),
        quotaTier=str(user.get("quota_tier") or "trial"),
        createdAt=str(user.get("created_at") or "") or None,
    )


@router.get("/accounts", response_model=list[AdminUserRow])
def list_accounts(
    _admin: RequestIdentity = Depends(require_admin),
    repo: UserRepositoryBackend = Depends(_user_repo),
    query: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[AdminUserRow]:
    """Account lifecycle view (complements /admin/users quota list)."""
    return [_row(u) for u in repo.list_users(query=query, limit=limit, offset=offset)]


@router.post("/accounts/{user_id}/active", response_model=AdminUserRow)
def set_account_active(
    user_id: str,
    body: AdminSetActiveRequest,
    request: Request,
    identity: RequestIdentity = Depends(require_admin),
    repo: UserRepositoryBackend = Depends(_user_repo),
) -> AdminUserRow:
    """Disable or restore a user account."""
    target = repo.get_user_by_id(user_id)
    if not target:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="user not found")

    # Prevent self-lockout
    if user_id == identity["ownerId"] and not body.isActive:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="cannot disable your own account")

    updated = repo.set_is_active(user_id, body.isActive)
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="user not found")

    action = "user_enable" if body.isActive else "user_disable"
    forwarded = request.headers.get("x-forwarded-for")
    ip = forwarded.split(",")[0].strip() if forwarded else (request.client.host if request.client else None)
    record_audit(
        action,
        actor_id=identity["ownerId"],
        target_id=user_id,
        outcome="success",
        detail={"reason": body.reason, "isActive": body.isActive},
        ip=ip,
        user_agent=request.headers.get("user-agent"),
    )
    logger.info("%s user=%s by=%s", action, user_id, identity["ownerId"])
    return _row(updated)
