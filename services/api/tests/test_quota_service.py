"""Tests for QuotaService tier rules + lazy period reset (S03-14-01/02).

Uses JsonUserRepository so no live Postgres is required.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.config import settings
from app.modules.json_user_repository import JsonUserRepository
from app.modules.quota_service import (
    QuotaExceeded,
    QuotaService,
    period_window,
    tier_definition,
)


@pytest.fixture()
def service(tmp_path: Path) -> tuple[QuotaService, JsonUserRepository]:
    repo = JsonUserRepository(
        users_dir=tmp_path / "users",
        workspaces_dir=tmp_path / "workspaces",
    )
    repo.bootstrap()
    return QuotaService(repo), repo


def _make_user(repo: JsonUserRepository, *, tier: str = "trial", used: int = 0, bonus: int = 0, period_start=None):
    user = repo.create_user(
        email=f"{tier}@x.test",
        username=f"u_{tier}",
        hashed_password="x",
        display_name=tier,
        is_active=True,
    )
    return repo.update_user_quota_fields(
        user["user_id"],
        quota_tier=tier,
        period_used=used,
        bonus_quota=bonus,
        period_start=period_start,
    )


def test_tier_definitions() -> None:
    assert tier_definition("trial")["periodType"] == "permanent"
    assert tier_definition("trial")["limit"] == settings.quota_trial_limit
    assert tier_definition("free") == {"periodType": "daily", "limit": settings.quota_free_limit}
    assert tier_definition("pro")["periodType"] == "monthly"
    assert tier_definition("pro")["limit"] == settings.quota_pro_limit
    assert tier_definition("admin") == {"periodType": "unlimited", "limit": None}


def test_period_windows() -> None:
    anchor = datetime(2026, 7, 31, 13, 30, tzinfo=timezone.utc)
    d_start, d_end = period_window("daily", anchor)
    assert d_start == datetime(2026, 7, 31, tzinfo=timezone.utc)
    assert d_end == datetime(2026, 8, 1, tzinfo=timezone.utc)
    m_start, m_end = period_window("monthly", anchor)
    assert m_start == datetime(2026, 7, 1, tzinfo=timezone.utc)
    assert m_end == datetime(2026, 8, 1, tzinfo=timezone.utc)
    assert period_window("permanent", anchor)[1] is None


def test_trial_state(service: tuple[QuotaService, JsonUserRepository]) -> None:
    quota, repo = service
    user = _make_user(repo, tier="trial", used=30000)
    state = quota.get_quota_state(user["user_id"])
    assert state["tier"] == "trial"
    assert state["periodType"] == "permanent"
    assert state["unlimited"] is False
    assert state["periodLimit"] == settings.quota_trial_limit
    assert state["periodUsed"] == 30000
    assert state["remaining"] == settings.quota_trial_limit - 30000
    assert state["resetAt"] is None  # permanent never resets


def test_admin_tier_is_unlimited(service: tuple[QuotaService, JsonUserRepository]) -> None:
    quota, repo = service
    user = _make_user(repo, tier="admin", used=999999)
    state = quota.get_quota_state(user["user_id"])
    assert state["unlimited"] is True
    assert state["remaining"] is None
    assert state["periodLimit"] is None


def test_bonus_added_to_remaining(service: tuple[QuotaService, JsonUserRepository]) -> None:
    quota, repo = service
    free_limit = settings.quota_free_limit
    user = _make_user(repo, tier="free", used=free_limit, bonus=12000)  # period exhausted
    state = quota.get_quota_state(user["user_id"])
    # period_remaining = 0, plus bonus → remaining = bonus only
    assert state["remaining"] == 12000
    assert state["bonusQuota"] == 12000


def test_daily_lazy_reset(service: tuple[QuotaService, JsonUserRepository]) -> None:
    quota, repo = service
    free_limit = settings.quota_free_limit
    stale_start = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    user = _make_user(repo, tier="free", used=free_limit, period_start=stale_start)
    state = quota.get_quota_state(user["user_id"])
    # Window has passed → period_used reset to 0.
    assert state["periodUsed"] == 0
    assert state["remaining"] == free_limit
    refreshed = repo.get_user_by_id(user["user_id"])
    assert refreshed["period_used"] == 0


def test_daily_no_reset_within_window(service: tuple[QuotaService, JsonUserRepository]) -> None:
    quota, repo = service
    free_limit = settings.quota_free_limit
    fresh_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    user = _make_user(repo, tier="free", used=20000, period_start=fresh_start)
    state = quota.get_quota_state(user["user_id"])
    assert state["periodUsed"] == 20000  # not reset
    assert state["remaining"] == free_limit - 20000


def test_update_user_quota_fields_rejects_unknown_field(service: tuple[QuotaService, JsonUserRepository]) -> None:
    _, repo = service
    user = _make_user(repo)
    with pytest.raises(ValueError, match="not mutable"):
        repo.update_user_quota_fields(user["user_id"], email="hijack@x.test")  # type: ignore[arg-type]


def test_increment_bonus_quota_atomic(service: tuple[QuotaService, JsonUserRepository]) -> None:
    _, repo = service
    user = _make_user(repo, tier="pro", bonus=1000)
    updated = repo.increment_bonus_quota(user["user_id"], 5000)
    assert updated["bonus_quota"] == 6000


def test_check_and_gate_passes_with_remaining(service: tuple[QuotaService, JsonUserRepository]) -> None:
    quota, repo = service
    free_limit = settings.quota_free_limit
    user = _make_user(repo, tier="free", used=1000)
    state = quota.check_and_gate(user["user_id"])
    assert state["remaining"] == free_limit - 1000


def test_check_and_gate_raises_when_exhausted(service: tuple[QuotaService, JsonUserRepository]) -> None:
    quota, repo = service
    free_limit = settings.quota_free_limit
    user = _make_user(repo, tier="free", used=free_limit)  # period exhausted, no bonus
    with pytest.raises(QuotaExceeded) as exc_info:
        quota.check_and_gate(user["user_id"])
    assert exc_info.value.tier == "free"
    assert exc_info.value.remaining == 0


def test_check_and_gate_admin_never_gated(service: tuple[QuotaService, JsonUserRepository]) -> None:
    quota, repo = service
    user = _make_user(repo, tier="admin", used=10**9)
    state = quota.check_and_gate(user["user_id"])  # must not raise
    assert state["unlimited"] is True


def test_check_and_gate_bonus_keeps_user_alive(service: tuple[QuotaService, JsonUserRepository]) -> None:
    quota, repo = service
    free_limit = settings.quota_free_limit
    # Period exhausted but bonus covers → gate passes (remaining counts bonus).
    user = _make_user(repo, tier="free", used=free_limit, bonus=100)
    state = quota.check_and_gate(user["user_id"])
    assert state["remaining"] == 100


def test_settle_charges_period_first(service: tuple[QuotaService, JsonUserRepository]) -> None:
    quota, repo = service
    user = _make_user(repo, tier="free", used=10000, bonus=5000)
    quota.settle(user["user_id"], 3000)
    after = repo.get_user_by_id(user["user_id"])
    assert after["period_used"] == 13000
    assert after["bonus_quota"] == 5000  # untouched (period covered it)


def test_settle_spills_overflow_to_bonus(service: tuple[QuotaService, JsonUserRepository]) -> None:
    quota, repo = service
    free_limit = settings.quota_free_limit
    user = _make_user(repo, tier="free", used=free_limit - 2000, bonus=5000)  # 2000 period left
    quota.settle(user["user_id"], 5000)  # 2000 from period, 3000 from bonus
    after = repo.get_user_by_id(user["user_id"])
    assert after["period_used"] == free_limit  # capped at limit
    assert after["bonus_quota"] == 2000  # 5000 - 3000 spill


def test_settle_admin_is_noop(service: tuple[QuotaService, JsonUserRepository]) -> None:
    quota, repo = service
    user = _make_user(repo, tier="admin", used=0, bonus=0)
    quota.settle(user["user_id"], 99999)
    after = repo.get_user_by_id(user["user_id"])
    assert after["period_used"] == 0
    assert after["bonus_quota"] == 0


def test_settle_concurrent_overshoot_explained_negative(
    service: tuple[QuotaService, JsonUserRepository],
) -> None:
    quota, repo = service
    free_limit = settings.quota_free_limit
    # Period exhausted AND bonus small: settling more than available drives bonus
    # negative — allowed (explainable; every token is in the ledger).
    user = _make_user(repo, tier="free", used=free_limit, bonus=100)
    quota.settle(user["user_id"], 500)
    after = repo.get_user_by_id(user["user_id"])
    assert after["bonus_quota"] == -400


def test_inactive_user_has_zero_remaining_until_activated(
    service: tuple[QuotaService, JsonUserRepository],
) -> None:
    quota, repo = service
    user = repo.create_user(
        email="pending@x.test",
        username="pending_quota",
        hashed_password="x",
        display_name="pending",
        is_active=False,
    )
    state = quota.quota_state_for_user(user)
    assert state["remaining"] == 0
    with pytest.raises(QuotaExceeded):
        quota.check_and_gate(user["user_id"])

    repo.set_is_active(user["user_id"], True)
    after = quota.get_quota_state(user["user_id"])
    assert after["remaining"] == settings.quota_trial_limit
