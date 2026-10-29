from __future__ import annotations

from app.modules import research_start_repository
from app.modules.research_start_intent import (
    _canonical_intent,
    _durable_fingerprint,
    _request_fingerprint_matches,
    canonical_fingerprint,
)
from app.modules.research_start_projection import _planning_progress, _session_public


def test_intent_fingerprint_normalizes_equivalent_unicode_without_changing_facade() -> None:
    decomposed = {"input": "e\u0301lectrode", "sourcePage": "home"}
    composed = {"input": "électrode", "sourcePage": "home"}

    assert canonical_fingerprint(decomposed) == canonical_fingerprint(composed)
    assert research_start_repository.canonical_fingerprint(decomposed) == canonical_fingerprint(
        decomposed
    )


def test_durable_request_fingerprint_keeps_created_by_and_legacy_contracts_distinct() -> None:
    intent = _canonical_intent({"input": "Study material stability."})
    request = {
        "owner_id": "owner",
        "request_payload": {**intent, "_createdBy": "researcher"},
        "fingerprint": _durable_fingerprint(intent, "researcher"),
    }

    assert _request_fingerprint_matches(request, intent, "researcher") is True
    assert _request_fingerprint_matches(request, intent, "another-researcher") is False


def test_public_projection_refuses_to_claim_running_after_terminal_request() -> None:
    session = _session_public(
        {
            "session_id": "intent_0123456789ab",
            "state": "running",
            "request_state": "failed",
            "reserved_task_id": "task_1",
            "created_at": None,
            "updated_at": None,
        }
    )

    assert session["status"] == "failed"
    assert session["recoveryReason"] == "responsibility_gap"
    assert session["automaticRetryAllowed"] is False
    assert research_start_repository._session_public is _session_public


def test_planning_projection_preserves_the_retry_explanation() -> None:
    progress = _planning_progress(
        {
            "state": "running",
            "request_state": "leased",
            "attempt_phase": "research_planner",
            "attempt_state": "failed",
            "attempt_ordinal": 2,
        }
    )

    assert progress["code"] == "repairing_design"
    assert progress["repairRound"] == 1
