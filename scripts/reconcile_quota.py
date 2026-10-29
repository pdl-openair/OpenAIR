#!/usr/bin/env python3
"""Minimal quota reconciliation diagnostic (S03-14-03 §6.3).

Reports, per user: tier, period_used, bonus_quota, lifetime ledger token sum,
and flags accounts whose bonus quota has gone negative (concurrent overshoot)
or whose period_used exceeds the tier limit by more than one in-flight turn.

This is a diagnostic, not a precise invariant: period counters reset on a
daily/monthly cadence while the ledger is lifetime-append-only, so the two
columns are not expected to match — the script surfaces *implausible* states
(negative bonus, runaway period overshoot) for operator review.

Usage (from services/api/):
    OPENAIR_PERSISTENCE_BACKEND=postgres .venv/Scripts/python ../../scripts/reconcile_quota.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow running from services/api/ or repo root.
_API = Path(__file__).resolve().parents[1] / "services" / "api"
if _API.is_dir():
    sys.path.insert(0, str(_API))


def main() -> int:
    from app.config import settings  # noqa: F401  — triggers .env load
    from app.modules.quota_service import tier_definition
    from app.modules.token_ledger_repository import get_token_ledger_repository
    from app.modules.user_repository import get_user_repository

    users = get_user_repository()
    ledger = get_token_ledger_repository()
    all_users = users.list_users(limit=10000)
    warnings = 0
    print(f"{'user_id':<24} {'tier':<10} {'period_used':>12} {'limit':>12} {'bonus':>10} {'ledger_sum':>12}  flags")
    for u in all_users:
        tier = str(u.get("quota_tier") or "trial")
        period_used = int(u.get("period_used") or 0)
        bonus = int(u.get("bonus_quota") or 0)
        limit_def = tier_definition(tier)["limit"]
        ledger_sum = ledger.sum_usage(u["user_id"])
        flags = []
        if bonus < 0:
            flags.append("NEGATIVE_BONUS")
            warnings += 1
        if limit_def is not None and period_used > limit_def:
            flags.append("PERIOD_OVER_LIMIT")
            warnings += 1
        limit_str = str(limit_def) if limit_def is not None else "unlimited"
        print(
            f"{u['user_id']:<24} {tier:<10} {period_used:>12} {limit_str:>12} {bonus:>10} {ledger_sum:>12}  {','.join(flags)}"
        )
    print(f"\n{warnings} warning(s)")
    return 1 if warnings else 0


if __name__ == "__main__":
    raise SystemExit(main())
