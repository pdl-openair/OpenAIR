"""User-side quota routes: quota summary + own token usage ledger (S03-14-02)."""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.auth import RequestIdentity, resolve_request_identity
from app.models.account_schemas import (
    QuotaSummary,
    TokenUsagePage,
    usage_entry_from_row,
)
from app.modules.quota_service import QuotaService, get_quota_service
from app.modules.token_ledger_repository import TokenLedgerBackend, get_token_ledger_repository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/quota", tags=["quota"])


def _quota_service() -> QuotaService:
    return get_quota_service()


def _ledger_repo() -> TokenLedgerBackend:
    return get_token_ledger_repository()


def _parse_ts(value: str | None, *, name: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"invalid {name} timestamp: {value!r} (expect ISO-8601)",
        ) from exc


@router.get("/me", response_model=QuotaSummary)
def get_my_quota(
    identity: RequestIdentity = Depends(resolve_request_identity),
    quota: QuotaService = Depends(_quota_service),
) -> QuotaSummary:
    """Quota state for the current user (triggers lazy period reset)."""
    try:
        state = quota.get_quota_state(identity["ownerId"])
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="user not found") from None
    return QuotaSummary(**state)


@router.get("/me/usage", response_model=TokenUsagePage)
def get_my_usage(
    identity: RequestIdentity = Depends(resolve_request_identity),
    ledger: TokenLedgerBackend = Depends(_ledger_repo),
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = Query(default=None),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> TokenUsagePage:
    """Own token usage ledger entries (time range + cursor pagination)."""
    rows, next_cursor = ledger.list_usage(
        identity["ownerId"],
        from_ts=_parse_ts(from_, name="from"),
        to_ts=_parse_ts(to, name="to"),
        limit=limit,
        cursor=cursor,
    )
    return TokenUsagePage(
        items=[usage_entry_from_row(row, redact_platform_provider_model=True) for row in rows],
        nextCursor=next_cursor,
    )
