"""Pydantic schemas for quota & token ledger (Sprint-03).

Mirror of packages/shared/src/account.ts — keep the two in sync.
Rule source: docs/task_specs/sprint03/S03-14-01_quota_rules.md
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

QuotaTier = Literal["trial", "free", "pro", "internal", "admin"]
UserRole = Literal["user", "admin"]
QuotaPeriodType = Literal["permanent", "daily", "monthly", "unlimited"]
TokenUsageStatus = Literal["success", "failed", "retried"]
TokenUsageKeySource = Literal["platform", "user"]


class QuotaSummary(BaseModel):
    tier: QuotaTier
    periodType: QuotaPeriodType
    unlimited: bool
    periodLimit: int | None
    periodUsed: int
    bonusQuota: int
    remaining: int | None
    resetAt: str | None


class TokenUsageEntry(BaseModel):
    ledgerId: str
    userId: str
    workspaceId: str | None = None
    taskId: str | None = None
    callId: str | None = None
    source: str
    provider: str | None = None
    model: str | None = None
    keySource: TokenUsageKeySource = "platform"
    status: TokenUsageStatus
    attempt: int = 1
    inputTokens: int = 0
    outputTokens: int = 0
    totalTokens: int = 0
    estimated: bool = False
    createdAt: str


class TokenUsagePage(BaseModel):
    items: list[TokenUsageEntry]
    nextCursor: str | None = None


class QuotaExceededError(BaseModel):
    """HTTP 402 body when quota is exhausted (S03-14-01 §6)."""

    code: Literal["quota_exhausted"] = "quota_exhausted"
    message: str
    tier: QuotaTier
    remaining: int
    resetAt: str | None = None


# ---------------------------------------------------------------------------
# Admin (S03-14-04)
# ---------------------------------------------------------------------------


class QuotaAdjustmentRecord(BaseModel):
    adjustmentId: str
    userId: str
    adjustType: Literal["bonus_quota", "tier_change"]
    delta: int | None = None
    oldTier: QuotaTier | None = None
    newTier: QuotaTier | None = None
    reason: str
    operatorId: str
    createdAt: str


class QuotaAdjustmentRequest(BaseModel):
    type: Literal["bonus_quota", "tier_change"]
    delta: int | None = Field(default=None, description="required for bonus_quota; positive integer")
    newTier: QuotaTier | None = Field(default=None, description="required for tier_change")
    reason: str = Field(min_length=1, max_length=500)


class QuotaTierCatalogEntry(BaseModel):
    """Live tier → period/limit catalog for admin UI (from config/env)."""

    tier: QuotaTier
    periodType: QuotaPeriodType
    periodLimit: int | None = None


class AdminUserQuotaRow(BaseModel):
    userId: str
    email: str
    username: str
    displayName: str | None = None
    role: UserRole
    isActive: bool = True
    quota: QuotaSummary
    createdAt: str | None = None


class SourceUsageSummary(BaseModel):
    source: str
    calls: int
    inputTokens: int
    outputTokens: int
    totalTokens: int


class ModelUsageSummary(BaseModel):
    model: str | None = None
    provider: str | None = None
    keySource: TokenUsageKeySource = "platform"
    calls: int
    inputTokens: int
    outputTokens: int
    totalTokens: int


class UsageTimeseriesPoint(BaseModel):
    periodStart: str
    periodLabel: str
    calls: int
    totalTokens: int


class UsageTimeseriesBundle(BaseModel):
    granularity: Literal["day", "week", "month"]
    points: list[UsageTimeseriesPoint]
    peakTokens: int = 0
    peakPeriod: str | None = None
    troughTokens: int = 0
    troughPeriod: str | None = None
    avgTokens: float = 0.0


class AdminUserQuotaDetail(BaseModel):
    user: AdminUserQuotaRow
    usageBySource: list[SourceUsageSummary]
    usageByModel: list[ModelUsageSummary] = Field(default_factory=list)
    timeseriesDaily: UsageTimeseriesBundle | None = None
    timeseriesWeekly: UsageTimeseriesBundle | None = None
    timeseriesMonthly: UsageTimeseriesBundle | None = None
    recentUsage: list[TokenUsageEntry] = Field(default_factory=list)
    totalTokens30d: int


def usage_entry_from_row(
    row: dict[str, Any],
    *,
    redact_platform_provider_model: bool = False,
) -> TokenUsageEntry:
    """Map a ledger DB row (snake_case) to the API shape (camelCase).

    When ``redact_platform_provider_model`` is True (user-facing /me/usage),
    platform-key rows hide upstream provider/model (enterprise private).
    BYOK (``key_source=user``) keeps full model visibility.
    """
    created = row.get("created_at")
    key_source_raw = str(row.get("key_source") or "platform").strip().lower() or "platform"
    key_source: TokenUsageKeySource = "user" if key_source_raw == "user" else "platform"
    provider = row.get("provider")
    model = row.get("model")
    if redact_platform_provider_model and key_source == "platform":
        provider = None
        model = None
    return TokenUsageEntry(
        ledgerId=row["ledger_id"],
        userId=row["user_id"],
        workspaceId=row.get("workspace_id"),
        taskId=row.get("task_id"),
        callId=row.get("call_id"),
        source=row["source"],
        provider=provider,
        model=model,
        keySource=key_source,
        status=row["status"],
        attempt=int(row.get("attempt") or 1),
        inputTokens=int(row.get("input_tokens") or 0),
        outputTokens=int(row.get("output_tokens") or 0),
        totalTokens=int(row.get("total_tokens") or 0),
        estimated=bool(row.get("estimated") or False),
        createdAt=created.isoformat() if hasattr(created, "isoformat") else str(created or ""),
    )


def adjustment_from_row(row: dict[str, Any]) -> QuotaAdjustmentRecord:
    created = row.get("created_at")
    return QuotaAdjustmentRecord(
        adjustmentId=row["adjustment_id"],
        userId=row["user_id"],
        adjustType=row["adjust_type"],
        delta=row.get("delta"),
        oldTier=row.get("old_tier"),
        newTier=row.get("new_tier"),
        reason=row["reason"],
        operatorId=row["operator_id"],
        createdAt=created.isoformat() if hasattr(created, "isoformat") else str(created or ""),
    )
