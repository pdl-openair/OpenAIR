from __future__ import annotations

import json

import pytest

from app.execution.research_dispatch_provider import DispatchSubmissionCompleted
from app.execution.research_dispatch_worker import (
    _dispatch_result_from_payload,
    _dispatch_result_payload,
)
from app.models.result_protocol import (
    artifact_quality_for_protocol,
    merge_result_protocols,
    normalize_result_protocol,
)


def test_result_protocol_warnings_degrade_without_changing_usability() -> None:
    protocol = normalize_result_protocol(None, warnings=["intent_ambiguities"])

    assert protocol == {
        "version": "result-protocol.v1",
        "status": "degraded",
        "accepted_items": [],
        "quarantined_items": [],
        "repaired_items": [],
        "warnings": ["intent_ambiguities"],
        "coverage": {},
    }
    assert artifact_quality_for_protocol(protocol) == "degraded"


def test_result_protocol_merges_guard_and_finalizer_facts() -> None:
    protocol = merge_result_protocols(
        {
            "status": "degraded",
            "accepted_items": [{"path": "queries", "count": 7}],
            "quarantined_items": [{"path": "queries[7]", "reason": "invalid_query"}],
            "repaired_items": [],
            "warnings": ["one query was skipped"],
            "coverage": {"accepted_query_count": 7},
        },
        {
            "status": "ok",
            "accepted_items": [{"path": "sources", "count": 12}],
            "quarantined_items": [],
            "repaired_items": [{"path": "sources[3]", "action": "normalized_identity"}],
            "warnings": [],
            "coverage": {"accepted_source_count": 12},
        },
    )

    assert protocol["status"] == "degraded"
    assert protocol["coverage"] == {
        "accepted_query_count": 7,
        "accepted_source_count": 12,
    }
    assert len(protocol["accepted_items"]) == 2
    assert len(protocol["quarantined_items"]) == 1
    assert len(protocol["repaired_items"]) == 1


def test_result_protocol_rejects_malformed_public_fields() -> None:
    with pytest.raises(ValueError, match="warnings must be a list"):
        normalize_result_protocol({"status": "ok", "warnings": "not-a-list"})

    with pytest.raises(ValueError, match="accepted result items"):
        normalize_result_protocol(
            {"status": "ok", "accepted_items": [{"path": "queries"}]}
        )

    with pytest.raises(ValueError, match="version is unsupported"):
        normalize_result_protocol({"version": "result-protocol.v2", "status": "ok"})



def test_completed_submission_preserves_scientific_gap_warnings() -> None:
    completed = DispatchSubmissionCompleted(
        title="Result",
        mime_type="application/json",
        content='{"ok":true}',
        provider_id="direct_skill_runtime",
        adapter="durable_skill_runtime",

        warnings=("intent_ambiguities", "missing_information"),
    )

    assert completed.result_protocol["status"] == "degraded"
    assert completed.result_protocol["warnings"] == [
        "intent_ambiguities",
        "missing_information",
    ]


def test_completed_submission_rejects_nonusable_protocol() -> None:
    with pytest.raises(ValueError, match="usable result protocol"):
        DispatchSubmissionCompleted(
            title="Result",
            mime_type="application/json",
            content='{"ok":false}',
            provider_id="direct_skill_runtime",
            adapter="durable_skill_runtime",
            result_protocol={"status": "fatal"},
        )


def test_dispatch_staging_roundtrip_preserves_result_protocol() -> None:
    completed = DispatchSubmissionCompleted(
        title="Result",
        mime_type="application/json",
        content='{"ok":true}',
        provider_id="direct_skill_runtime",
        adapter="durable_skill_runtime",
        result_protocol={
            "status": "degraded",
            "accepted_items": [{"path": "queries", "count": 7}],
            "quarantined_items": [{"path": "queries[7]", "reason": "invalid_query"}],
            "repaired_items": [],
            "warnings": ["one query was skipped"],
            "coverage": {"accepted_query_count": 7},
        },
    )

    payload = _dispatch_result_payload(completed)
    staged = json.loads(payload.decode("utf-8"))
    recovered = _dispatch_result_from_payload(payload)

    assert "formalGateSatisfied" not in staged
    assert "manualReviewReasons" not in staged
    assert recovered.result_protocol == completed.result_protocol
    assert recovered.warnings == ("one query was skipped",)
