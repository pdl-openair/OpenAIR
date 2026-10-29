"""Ledger key_source + user-facing usage redaction."""

from __future__ import annotations

from app.models.account_schemas import usage_entry_from_row
from app.modules.token_ledger_repository import TokenUsageRecord, _normalize


def test_normalize_defaults_key_source_platform() -> None:
    entry = _normalize(
        TokenUsageRecord(  # type: ignore[typeddict-item]
            user_id="u1",
            source="workbench_chat",
            input_tokens=1,
            output_tokens=2,
        )
    )
    assert entry["key_source"] == "platform"
    assert entry["total_tokens"] == 3


def test_normalize_accepts_user_key_source() -> None:
    entry = _normalize(
        TokenUsageRecord(  # type: ignore[typeddict-item]
            user_id="u1",
            source="workbench_chat",
            key_source="user",
            model="gpt-test",
            input_tokens=1,
            output_tokens=0,
        )
    )
    assert entry["key_source"] == "user"
    assert entry["model"] == "gpt-test"


def test_user_usage_redacts_platform_provider_model() -> None:
    row = {
        "ledger_id": "led_1",
        "user_id": "u1",
        "source": "durable_dispatch",
        "provider": "huiyan",
        "model": "terra",
        "key_source": "platform",
        "status": "success",
        "attempt": 1,
        "input_tokens": 10,
        "output_tokens": 20,
        "total_tokens": 30,
        "estimated": False,
        "created_at": "2026-08-12T00:00:00+00:00",
    }
    redacted = usage_entry_from_row(row, redact_platform_provider_model=True)
    assert redacted.keySource == "platform"
    assert redacted.provider is None
    assert redacted.model is None
    assert redacted.totalTokens == 30

    full = usage_entry_from_row(row, redact_platform_provider_model=False)
    assert full.provider == "huiyan"
    assert full.model == "terra"


def test_user_usage_keeps_byok_model() -> None:
    row = {
        "ledger_id": "led_2",
        "user_id": "u1",
        "source": "workbench_chat",
        "provider": "openai",
        "model": "gpt-4o",
        "key_source": "user",
        "status": "success",
        "attempt": 1,
        "input_tokens": 1,
        "output_tokens": 1,
        "total_tokens": 2,
        "estimated": False,
        "created_at": "2026-08-12T00:00:00+00:00",
    }
    entry = usage_entry_from_row(row, redact_platform_provider_model=True)
    assert entry.keySource == "user"
    assert entry.model == "gpt-4o"
    assert entry.provider == "openai"
