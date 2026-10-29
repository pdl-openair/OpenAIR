"""Tests for the token usage ledger (S03-14-02), using the JSON dev fallback.

The Postgres backend shares the same interface; JSON exercises record/query/
summary semantics and the failure/retry/estimate rules without a live DB.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from llm_core.token_usage import to_wire_usage
from app.modules.token_ledger_repository import (
    JsonTokenLedgerRepository,
    TokenUsageRecord,
    record_call_usage,
)


@pytest.fixture()
def ledger(tmp_path: Path) -> JsonTokenLedgerRepository:
    repo = JsonTokenLedgerRepository(ledger_dir=tmp_path / "ledger")
    repo.bootstrap()
    return repo


def _rec(**overrides: object) -> TokenUsageRecord:  # type: ignore[valid-type]
    base: TokenUsageRecord = {  # type: ignore[typeddict-item]
        "user_id": "u_test",
        "source": "workbench_chat",
        "provider": "huiyan_chat_completions",
        "model": "gpt-4o-mini",
        "status": "success",
        "input_tokens": 10,
        "output_tokens": 20,
        "total_tokens": 30,
    }
    base.update(overrides)  # type: ignore[typeddict-item]
    return base


def test_record_and_list_usage(ledger: JsonTokenLedgerRepository) -> None:
    ledger.record_usage(_rec(total_tokens=30))
    ledger.record_usage(_rec(total_tokens=12, source="execute_skill"))
    rows, next_cursor = ledger.list_usage("u_test", limit=10)
    assert next_cursor is None
    assert len(rows) == 2
    # Newest first; both belong to the user.
    assert all(r["user_id"] == "u_test" for r in rows)
    assert {30, 12} == {r["total_tokens"] for r in rows}


def test_list_usage_isolation_between_users(ledger: JsonTokenLedgerRepository) -> None:
    ledger.record_usage(_rec(user_id="u_a", total_tokens=5))  # type: ignore[arg-type]
    ledger.record_usage(_rec(user_id="u_b", total_tokens=9))  # type: ignore[arg-type]
    rows_a, _ = ledger.list_usage("u_a")
    rows_b, _ = ledger.list_usage("u_b")
    assert [r["total_tokens"] for r in rows_a] == [5]
    assert [r["total_tokens"] for r in rows_b] == [9]


def test_list_usage_cursor_pagination(ledger: JsonTokenLedgerRepository) -> None:
    for i in range(5):
        ledger.record_usage(_rec(total_tokens=i))
    page1, next1 = ledger.list_usage("u_test", limit=2)
    assert len(page1) == 2
    assert next1 is not None
    page2, next2 = ledger.list_usage("u_test", limit=2, cursor=next1)
    assert len(page2) == 2
    # No overlap between pages.
    seen = {r["ledger_id"] for r in page1}
    assert not (seen & {r["ledger_id"] for r in page2})


def test_sum_usage(ledger: JsonTokenLedgerRepository) -> None:
    ledger.record_usage(_rec(total_tokens=30, status="success"))
    ledger.record_usage(_rec(total_tokens=7, status="failed"))  # failed but usage observed → counted in sum
    assert ledger.sum_usage("u_test") == 37


def test_sum_usage_time_range(ledger: JsonTokenLedgerRepository) -> None:
    ledger.record_usage(_rec(total_tokens=30))
    future = datetime.now(timezone.utc) + timedelta(days=2)
    assert ledger.sum_usage("u_test", from_ts=future) == 0


def test_summary_by_source(ledger: JsonTokenLedgerRepository) -> None:
    ledger.record_usage(_rec(total_tokens=30, input_tokens=10, output_tokens=20, source="workbench_chat"))
    ledger.record_usage(_rec(total_tokens=5, input_tokens=2, output_tokens=3, source="workbench_chat"))
    ledger.record_usage(_rec(total_tokens=8, input_tokens=3, output_tokens=5, source="execute_skill"))
    summary = ledger.summary_by_source("u_test")
    by_source = {row["source"]: row for row in summary}
    assert by_source["workbench_chat"]["calls"] == 2
    assert by_source["workbench_chat"]["total_tokens"] == 35
    assert by_source["execute_skill"]["total_tokens"] == 8


def test_summary_by_model_and_timeseries(ledger: JsonTokenLedgerRepository) -> None:
    from app.modules.token_ledger_repository import build_timeseries_bundle

    ledger.record_usage(_rec(total_tokens=30, model="gpt-4o-mini", provider="a"))
    ledger.record_usage(_rec(total_tokens=12, model="gpt-4o-mini", provider="a"))
    ledger.record_usage(_rec(total_tokens=8, model="claude-sonnet", provider="b", key_source="user"))
    models = ledger.summary_by_model("u_test")
    assert len(models) == 2
    by_model = {(m["model"], m["key_source"]): m for m in models}
    assert by_model[("gpt-4o-mini", "platform")]["total_tokens"] == 42
    assert by_model[("claude-sonnet", "user")]["calls"] == 1

    now = datetime.now(timezone.utc)
    since = now - timedelta(days=7)
    raw = ledger.timeseries("u_test", granularity="day", from_ts=since, to_ts=now)
    assert sum(r["total_tokens"] for r in raw) == 50
    bundle = build_timeseries_bundle(granularity="day", raw_points=raw, from_ts=since, to_ts=now)
    assert bundle["peakTokens"] >= 30
    assert bundle["peakPeriod"] is not None
    assert len(bundle["points"]) >= 7


def test_record_call_usage_success_with_no_usage_skips(ledger: JsonTokenLedgerRepository, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.modules.token_ledger_repository.get_token_ledger_repository", lambda: ledger
    )
    # Successful call with no observable usage (e.g. replay/mock) → nothing booked.
    result = record_call_usage(user_id="u_test", source="workbench_chat", usage_wire=None, status="success")
    assert result is None
    assert ledger.sum_usage("u_test") == 0


def test_record_call_usage_failed_with_no_usage_records_zero_row(ledger: JsonTokenLedgerRepository, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.modules.token_ledger_repository.get_token_ledger_repository", lambda: ledger
    )
    # Failed call before any usage → 0-token row for traceability, no deduction.
    record_call_usage(user_id="u_test", source="workbench_chat", usage_wire=None, status="failed")
    rows, _ = ledger.list_usage("u_test")
    assert len(rows) == 1
    assert rows[0]["status"] == "failed"
    assert rows[0]["total_tokens"] == 0


def test_record_call_usage_from_wire(ledger: JsonTokenLedgerRepository, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.modules.token_ledger_repository.get_token_ledger_repository", lambda: ledger
    )
    wire = to_wire_usage({"input_tokens": 15, "output_tokens": 25, "total_tokens": 40, "estimated": False, "raw": None})
    record_call_usage(
        user_id="u_test",
        source="durable_dispatch",
        usage_wire=wire,
        provider="huiyan_chat_completions",
        status="success",
    )
    assert ledger.sum_usage("u_test") == 40


def test_meter_call_settles_failed_call_with_real_usage(
    ledger: JsonTokenLedgerRepository,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from app.modules.json_user_repository import JsonUserRepository
    from app.modules.quota_service import QuotaService
    from app.modules.token_ledger_repository import meter_call

    users = JsonUserRepository(users_dir=tmp_path / "users", workspaces_dir=tmp_path / "workspaces")
    users.bootstrap()
    user = users.create_user(
        email="meter@x.test",
        username="meter_user",
        hashed_password="x",
        display_name="meter",
        is_active=True,
    )
    users.update_user_quota_fields(user["user_id"], quota_tier="trial", period_used=0, bonus_quota=0)
    quota = QuotaService(users)
    monkeypatch.setattr(
        "app.modules.token_ledger_repository.get_token_ledger_repository", lambda: ledger
    )
    monkeypatch.setattr("app.modules.quota_service.get_quota_service", lambda: quota)

    wire = to_wire_usage(
        {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15, "estimated": False, "raw": None}
    )
    meter_call(
        user_id=user["user_id"],
        source="durable_dispatch",
        usage_wire=wire,
        status="failed",
    )
    rows, _ = ledger.list_usage(user["user_id"])
    assert len(rows) == 1
    assert rows[0]["status"] == "failed"
    assert rows[0]["total_tokens"] == 15
    assert quota.get_quota_state(user["user_id"])["periodUsed"] == 15


def test_flush_observed_usage_meters_bucket(
    ledger: JsonTokenLedgerRepository,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from app.modules.json_user_repository import JsonUserRepository
    from app.modules.quota_service import QuotaService
    from app.modules.token_ledger_repository import flush_observed_usage

    users = JsonUserRepository(users_dir=tmp_path / "users", workspaces_dir=tmp_path / "workspaces")
    users.bootstrap()
    user = users.create_user(
        email="flush@x.test",
        username="flush_user",
        hashed_password="x",
        display_name="flush",
        is_active=True,
    )
    users.update_user_quota_fields(user["user_id"], quota_tier="trial", period_used=0, bonus_quota=0)
    quota = QuotaService(users)
    monkeypatch.setattr(
        "app.modules.token_ledger_repository.get_token_ledger_repository", lambda: ledger
    )
    monkeypatch.setattr("app.modules.quota_service.get_quota_service", lambda: quota)

    bucket = [
        {
            "provider": "huiyan_chat_completions",
            "model": "GLM-5.2",
            "usage": to_wire_usage(
                {"input_tokens": 3, "output_tokens": 4, "total_tokens": 7, "estimated": False, "raw": None}
            ),
        },
        {
            "provider": "huiyan_chat_completions",
            "model": "GLM-5.2",
            "usage": to_wire_usage(
                {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3, "estimated": False, "raw": None}
            ),
        },
    ]
    flush_observed_usage(
        user_id=user["user_id"],
        source="research_start",
        bucket=bucket,
        workspace_id="ws_1",
        task_id="task_1",
    )
    assert ledger.sum_usage(user["user_id"]) == 10
    assert quota.get_quota_state(user["user_id"])["periodUsed"] == 10
