"""Unified user quota — tier definitions, period windows, lazy reset (S03-14-01/02).

Provider-neutral: quota is measured in tokens (input + output) regardless of
which provider served the call. S03-14-03 adds check_and_gate / settle on top
of the state computation here.

Rule source: docs/task_specs/sprint03/S03-14-01_quota_rules.md
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, TypedDict

from app.config import settings
from app.modules.user_repository import UserRepositoryBackend

logger = logging.getLogger(__name__)

QuotaTier = Literal["trial", "free", "pro", "internal", "admin"]
PeriodType = Literal["permanent", "daily", "monthly", "unlimited"]

QUOTA_TIERS: tuple[str, ...] = ("trial", "free", "pro", "internal", "admin")
USER_ROLES: tuple[str, ...] = ("user", "admin")


class QuotaExceeded(Exception):
    """Raised when a user has no remaining quota (S03-14-03 gate).

    Carries the structured-error payload (S03-14-01 §6) so route/worker layers
    can render HTTP 402 / dispatch-failed without re-deriving it.
    """

    def __init__(self, *, tier: str, remaining: int, reset_at: str | None) -> None:
        self.tier = tier
        self.remaining = remaining
        self.reset_at = reset_at
        reset_hint = f"，将于 {reset_at} 重置" if reset_at else "（一次性额度已用尽）"
        super().__init__(f"额度不足：当前等级 {tier} 剩余 {remaining} tokens{reset_hint}")

# trial 从注册时刻起的永久窗口起点用一个足够早的哨兵，避免 None 特殊分支。
_PERMANENT_EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)


class TierDefinition(TypedDict):
    periodType: PeriodType
    limit: int | None  # None = unlimited (admin)


class QuotaState(TypedDict):
    tier: str
    periodType: PeriodType
    unlimited: bool
    periodLimit: int | None
    periodUsed: int
    bonusQuota: int
    remaining: int | None  # None = unlimited
    resetAt: str | None  # ISO-8601; None for permanent/unlimited


def tier_definition(tier: str) -> TierDefinition:
    """Resolve (period type, limit) for a quota tier from config."""
    if tier == "trial":
        return {"periodType": "permanent", "limit": settings.quota_trial_limit}
    if tier == "free":
        return {"periodType": "daily", "limit": settings.quota_free_limit}
    if tier == "pro":
        return {"periodType": "monthly", "limit": settings.quota_pro_limit}
    if tier == "internal":
        return {"periodType": "monthly", "limit": settings.quota_internal_limit}
    if tier == "admin":
        return {"periodType": "unlimited", "limit": None}
    raise ValueError(f"unknown quota tier: {tier!r}")


def _month_window(anchor: datetime) -> tuple[datetime, datetime]:
    start = anchor.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1)
    else:
        end = start.replace(month=start.month + 1)
    return start, end


def period_window(period_type: PeriodType, anchor: datetime) -> tuple[datetime, datetime | None]:
    """Return (window_start, window_end) containing ``anchor``; end=None means never resets."""
    anchor = anchor.astimezone(timezone.utc)
    if period_type == "daily":
        start = anchor.replace(hour=0, minute=0, second=0, microsecond=0)
        return start, start + timedelta(days=1)
    if period_type == "monthly":
        start, end = _month_window(anchor)
        return start, end
    if period_type == "permanent":
        return _PERMANENT_EPOCH, None
    return _PERMANENT_EPOCH, None  # unlimited


def _parse_ts(value: Any) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


class QuotaService:
    """Quota state computation + lazy period reset over a user repository."""

    def __init__(self, user_repo: UserRepositoryBackend) -> None:
        self._users = user_repo

    def ensure_period_fresh(self, user: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
        """Lazy reset: roll ``period_used`` when the current window has passed.

        No cron — the reset happens on the next quota touch (gate / me endpoint).
        permanent/unlimited tiers never reset.
        """
        now = now or datetime.now(timezone.utc)
        tier = str(user.get("quota_tier") or "trial")
        definition = tier_definition(tier)
        if definition["periodType"] in ("permanent", "unlimited"):
            if user.get("period_start") is None:
                # Stamp the window start once so resetAt math stays stable.
                updated = self._users.update_user_quota_fields(
                    user["user_id"], period_start=now.isoformat()
                )
                return updated or user
            return user

        period_start = _parse_ts(user.get("period_start"))
        if period_start is None:
            window_start, _ = period_window(definition["periodType"], now)
            updated = self._users.update_user_quota_fields(
                user["user_id"], period_start=window_start.isoformat()
            )
            return updated or user

        _, window_end = period_window(definition["periodType"], period_start)
        if window_end is not None and now >= window_end:
            window_start, _ = period_window(definition["periodType"], now)
            updated = self._users.update_user_quota_fields(
                user["user_id"],
                period_used=0,
                period_start=window_start.isoformat(),
            )
            return updated or user
        return user

    def get_quota_state(self, user_id: str, *, now: datetime | None = None) -> QuotaState:
        user = self._users.get_user_by_id(user_id)
        if not user:
            raise KeyError(f"user not found: {user_id}")
        user = self.ensure_period_fresh(user, now=now)
        return self.quota_state_for_user(user, now=now)

    def check_and_gate(self, user_id: str, *, now: datetime | None = None) -> QuotaState | None:
        """Pre-call gate (S03-14-03): raise QuotaExceeded when remaining <= 0.

        Triggers lazy reset first. admin tier always passes. Concurrency note:
        the gate is a read-check; under a concurrent burst several callers may
        pass simultaneously and the subsequent settle can drive the balance
        slightly negative — but every token is in the ledger (explainable), per
        S03-14-01 §5. The hard guarantee is that a user *already* at 0 never
        starts a model call.

        Fail-open: an unknown owner (e.g. the demo/legacy identity used in open
        dev mode ``OPENAIR_AUTH_REQUIRED=0``, which has no user record) returns
        None instead of raising — quota enforcement only applies to real
        registered users. Callers ignore the return value.
        """
        try:
            user = self._users.get_user_by_id(user_id)
            if not user:
                raise KeyError(f"user not found: {user_id}")
            if not user.get("is_active", True):
                raise QuotaExceeded(
                    tier=str(user.get("quota_tier") or "trial"),
                    remaining=0,
                    reset_at=None,
                )
            state = self.get_quota_state(user_id, now=now)
        except KeyError:
            logger.warning("quota gate: owner has no user record, failing open: %s", user_id)
            return None
        if state["unlimited"]:
            return state
        if (state["remaining"] or 0) <= 0:
            raise QuotaExceeded(
                tier=state["tier"],
                remaining=0,
                reset_at=state["resetAt"],
            )
        return state

    def settle(self, user_id: str, total_tokens: int) -> None:
        """Post-call deduction (S03-14-03): charge ``total_tokens`` atomically.

        Charges the period quota first, spilling any overflow to bonus quota.
        admin tier is never charged (but is still ledgered by the caller).
        Logs a warning if the balance goes negative (concurrent-window overshoot).
        """
        if total_tokens <= 0:
            return
        user = self._users.get_user_by_id(user_id)
        if not user:
            return
        tier = str(user.get("quota_tier") or "trial")
        definition = tier_definition(tier)
        if definition["periodType"] == "unlimited":
            return  # admin: ledger only, no deduction
        period_limit = definition["limit"]
        assert period_limit is not None
        try:
            self._users.apply_usage_settlement(user_id, total_tokens, period_limit)
        except Exception:  # noqa: BLE001
            logger.exception("quota settle failed user=%s tokens=%s", user_id, total_tokens)
            return
        after = self._users.get_user_by_id(user_id)
        if after is not None:
            period_remaining = max(0, period_limit - int(after.get("period_used") or 0))
            bonus_after = int(after.get("bonus_quota") or 0)
            if period_remaining == 0 and bonus_after < 0:
                logger.warning(
                    "quota balance negative after settle user=%s bonus=%s (concurrent overshoot; ledger explains it)",
                    user_id,
                    bonus_after,
                )

    def quota_state_for_user(self, user: dict[str, Any], *, now: datetime | None = None) -> QuotaState:
        """Compute the quota view for an already-fresh user dict (no extra writes)."""
        now = now or datetime.now(timezone.utc)
        tier = str(user.get("quota_tier") or "trial")
        definition = tier_definition(tier)
        period_type = definition["periodType"]
        period_limit = definition["limit"]
        period_used = int(user.get("period_used") or 0)
        bonus = int(user.get("bonus_quota") or 0)

        if period_type == "unlimited":
            return QuotaState(
                tier=tier,
                periodType=period_type,
                unlimited=True,
                periodLimit=None,
                periodUsed=period_used,
                bonusQuota=bonus,
                remaining=None,
                resetAt=None,
            )

        assert period_limit is not None
        period_remaining = max(0, period_limit - period_used)
        reset_at: str | None = None
        if period_type in ("daily", "monthly"):
            anchor = _parse_ts(user.get("period_start")) or now
            _, window_end = period_window(period_type, anchor)
            reset_at = window_end.isoformat() if window_end else None

        remaining = period_remaining + bonus
        if not user.get("is_active", True):
            remaining = 0

        return QuotaState(
            tier=tier,
            periodType=period_type,
            unlimited=False,
            periodLimit=period_limit,
            periodUsed=period_used,
            bonusQuota=bonus,
            remaining=remaining,
            resetAt=reset_at,
        )


_quota_service: QuotaService | None = None


def get_quota_service() -> QuotaService:
    from app.modules.user_repository import get_user_repository

    global _quota_service  # noqa: PLW0603
    if _quota_service is None:
        _quota_service = QuotaService(get_user_repository())
    return _quota_service
