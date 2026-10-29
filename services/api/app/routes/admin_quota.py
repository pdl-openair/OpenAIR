"""Admin quota routes: user consumption views + adjustments + audit (S03-14-04).

All endpoints require role=admin via ``require_admin`` (a real logged-in-user
check, distinct from the ops-token ``/api/admin/tasks/import`` path) so every
adjustment records a real operator_id in the audit trail.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.auth import RequestIdentity, require_admin
from app.models.account_schemas import (
    AdminUserQuotaDetail,
    AdminUserQuotaRow,
    ModelUsageSummary,
    QuotaAdjustmentRecord,
    QuotaAdjustmentRequest,
    QuotaSummary,
    QuotaTier,
    QuotaTierCatalogEntry,
    SourceUsageSummary,
    TokenUsagePage,
    UsageTimeseriesBundle,
    adjustment_from_row,
    usage_entry_from_row,
)
from app.modules.quota_adjustment_repository import (
    QuotaAdjustmentBackend,
    QuotaAdjustment,
    get_quota_adjustment_repository,
)
from app.modules.quota_service import QUOTA_TIERS, QuotaService, get_quota_service, tier_definition
from app.modules.token_ledger_repository import (
    TokenLedgerBackend,
    build_timeseries_bundle,
    get_token_ledger_repository,
)
from app.modules.user_repository import UserRepositoryBackend, get_user_repository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin-quota"])


def _user_repo() -> UserRepositoryBackend:
    return get_user_repository()


def _ledger() -> TokenLedgerBackend:
    return get_token_ledger_repository()


def _adjustments() -> QuotaAdjustmentBackend:
    return get_quota_adjustment_repository()


def _row_for_user(user: dict, quota: QuotaService) -> AdminUserQuotaRow:
    state = quota.quota_state_for_user(quota.ensure_period_fresh(user))
    return AdminUserQuotaRow(
        userId=user["user_id"],
        email=user["email"],
        username=user["username"],
        displayName=user.get("display_name"),
        role=str(user.get("role") or "user"),
        isActive=bool(user.get("is_active", True)),
        quota=QuotaSummary(**state),
        createdAt=str(user.get("created_at") or "") or None,
    )


@router.get("/quota/tiers", response_model=list[QuotaTierCatalogEntry])
def list_quota_tiers(
    _admin: RequestIdentity = Depends(require_admin),
) -> list[QuotaTierCatalogEntry]:
    """Return the live tier catalog (period type + limit) from server config.

    Admin UI uses this so operators see concrete token budgets when changing
    a user's tier, instead of opaque labels alone.
    """
    items: list[QuotaTierCatalogEntry] = []
    for tier in QUOTA_TIERS:
        definition = tier_definition(tier)
        items.append(
            QuotaTierCatalogEntry(
                tier=tier,  # type: ignore[arg-type]
                periodType=definition["periodType"],  # type: ignore[arg-type]
                periodLimit=definition["limit"],
            )
        )
    return items


@router.get("/users", response_model=list[AdminUserQuotaRow])
def list_users(
    _admin: RequestIdentity = Depends(require_admin),
    repo: UserRepositoryBackend = Depends(_user_repo),
    quota: QuotaService = Depends(lambda: get_quota_service()),
    query: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[AdminUserQuotaRow]:
    users = repo.list_users(query=query, limit=limit, offset=offset)
    return [_row_for_user(u, quota) for u in users]


@router.get("/users/{user_id}/quota", response_model=AdminUserQuotaDetail)
def get_user_quota(
    user_id: str,
    _admin: RequestIdentity = Depends(require_admin),
    repo: UserRepositoryBackend = Depends(_user_repo),
    ledger: TokenLedgerBackend = Depends(_ledger),
    quota: QuotaService = Depends(lambda: get_quota_service()),
) -> AdminUserQuotaDetail:
    user = repo.get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="user not found")
    now = datetime.now(timezone.utc)
    since_30d = now - timedelta(days=30)
    since_12w = now - timedelta(weeks=12)
    since_12m = now - timedelta(days=365)

    by_source = [
        SourceUsageSummary(
            source=str(row.get("source") or "unknown"),
            calls=int(row.get("calls") or 0),
            inputTokens=int(row.get("input_tokens") or 0),
            outputTokens=int(row.get("output_tokens") or 0),
            totalTokens=int(row.get("total_tokens") or 0),
        )
        for row in ledger.summary_by_source(user_id, from_ts=since_30d)
    ]
    by_model = [
        ModelUsageSummary(
            model=row.get("model"),
            provider=row.get("provider"),
            keySource="user" if str(row.get("key_source") or "platform") == "user" else "platform",
            calls=int(row.get("calls") or 0),
            inputTokens=int(row.get("input_tokens") or 0),
            outputTokens=int(row.get("output_tokens") or 0),
            totalTokens=int(row.get("total_tokens") or 0),
        )
        for row in ledger.summary_by_model(user_id, from_ts=since_30d)
    ]
    recent_rows, _ = ledger.list_usage(user_id, from_ts=since_30d, limit=50)
    recent_usage = [usage_entry_from_row(r) for r in recent_rows]

    daily = UsageTimeseriesBundle(
        **build_timeseries_bundle(
            granularity="day",
            raw_points=ledger.timeseries(user_id, granularity="day", from_ts=since_30d, to_ts=now),
            from_ts=since_30d,
            to_ts=now,
        )
    )
    weekly = UsageTimeseriesBundle(
        **build_timeseries_bundle(
            granularity="week",
            raw_points=ledger.timeseries(user_id, granularity="week", from_ts=since_12w, to_ts=now),
            from_ts=since_12w,
            to_ts=now,
        )
    )
    monthly = UsageTimeseriesBundle(
        **build_timeseries_bundle(
            granularity="month",
            raw_points=ledger.timeseries(user_id, granularity="month", from_ts=since_12m, to_ts=now),
            from_ts=since_12m,
            to_ts=now,
        )
    )

    total_30d = sum(b.totalTokens for b in by_source)
    return AdminUserQuotaDetail(
        user=_row_for_user(user, quota),
        usageBySource=by_source,
        usageByModel=by_model,
        timeseriesDaily=daily,
        timeseriesWeekly=weekly,
        timeseriesMonthly=monthly,
        recentUsage=recent_usage,
        totalTokens30d=total_30d,
    )


@router.get("/users/{user_id}/quota/usage", response_model=TokenUsagePage)
def get_user_usage(
    user_id: str,
    _admin: RequestIdentity = Depends(require_admin),
    ledger: TokenLedgerBackend = Depends(_ledger),
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = Query(default=None),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> TokenUsagePage:
    def _ts(value: str | None, name: str) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"invalid {name}") from exc

    rows, next_cursor = ledger.list_usage(
        user_id,
        from_ts=_ts(from_, "from"),
        to_ts=_ts(to, "to"),
        limit=limit,
        cursor=cursor,
    )
    return TokenUsagePage(items=[usage_entry_from_row(r) for r in rows], nextCursor=next_cursor)


@router.get("/users/{user_id}/quota/adjustments", response_model=list[QuotaAdjustmentRecord])
def list_user_adjustments(
    user_id: str,
    _admin: RequestIdentity = Depends(require_admin),
    repo: UserRepositoryBackend = Depends(_user_repo),
    adjustments: QuotaAdjustmentBackend = Depends(_adjustments),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[QuotaAdjustmentRecord]:
    if not repo.get_user_by_id(user_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="user not found")
    rows = adjustments.list_for_user(user_id, limit=limit)
    return [adjustment_from_row(r) for r in rows]


@router.post("/users/{user_id}/quota/adjustments", response_model=QuotaAdjustmentRecord, status_code=status.HTTP_201_CREATED)
def create_adjustment(
    user_id: str,
    body: QuotaAdjustmentRequest,
    identity: RequestIdentity = Depends(require_admin),
    repo: UserRepositoryBackend = Depends(_user_repo),
    adjustments: QuotaAdjustmentBackend = Depends(_adjustments),
) -> QuotaAdjustmentRecord:
    target = repo.get_user_by_id(user_id)
    if not target:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="user not found")
    if not target.get("is_active", True):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="请先激活该账号，再分配额度",
        )
    operator_id = identity["ownerId"]

    if body.type == "bonus_quota":
        if not isinstance(body.delta, int) or body.delta <= 0:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="delta must be a positive integer")
        repo.increment_bonus_quota(user_id, body.delta)
        record = QuotaAdjustment(
            user_id=user_id,
            adjust_type="bonus_quota",
            delta=body.delta,
            old_tier=None,
            new_tier=None,
            reason=body.reason,
            operator_id=operator_id,
        )
    else:  # tier_change
        if not body.newTier or body.newTier not in QUOTA_TIERS:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="newTier is invalid")
        # Self-demotion guard: an admin must not drop their own tier below admin
        # and lock themselves out of the management surface (S03-14-04 §6.3).
        if operator_id == user_id and body.newTier != "admin":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="不能调低自己的等级；请由其他管理员操作",
            )
        old_tier = str(target.get("quota_tier") or "trial")
        repo.update_user_quota_fields(user_id, quota_tier=body.newTier)
        record = QuotaAdjustment(
            user_id=user_id,
            adjust_type="tier_change",
            delta=None,
            old_tier=old_tier,
            new_tier=body.newTier,
            reason=body.reason,
            operator_id=operator_id,
        )

    logger.info("quota adjustment user=%s type=%s by=%s reason=%s", user_id, body.type, operator_id, body.reason)
    row = adjustments.record(record)
    return adjustment_from_row(row)
