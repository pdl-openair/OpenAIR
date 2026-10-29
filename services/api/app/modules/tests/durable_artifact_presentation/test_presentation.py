"""Receiver proof: retain usable quality facts and reject invalid envelopes."""

import hashlib
from copy import deepcopy

import pytest
from app.models.result_protocol import artifact_quality_for_protocol, normalize_result_protocol
from app.modules.durable_execution import DurableExecutionError, _validated_skill_presentation


def _presentation(*, warnings=()):
    content = b"project: bounded research\n"
    protocol = normalize_result_protocol(None, warnings=warnings)
    metadata = {
        "runtime": "llm_reasoning",
        "skillId": "intent_understanding",
        "resultProtocol": protocol,
        "artifactQuality": artifact_quality_for_protocol(protocol),
    }
    if protocol["warnings"]:
        metadata["warnings"] = protocol["warnings"][:24]
    return {
        "title": "Research configuration",
        "mimeType": "text/yaml",
        "metadata": metadata,
        "contentSha256": hashlib.sha256(content).hexdigest(),
        "contentSize": len(content),
    }


@pytest.mark.parametrize(
    "warnings", [[], ["one item quarantined"], [f"issue {i}" for i in range(30)]]
)
def test_accepts_current_runtime_quality_without_loss(warnings):
    presentation = _presentation(warnings=warnings)
    before = deepcopy(presentation)
    accepted = _validated_skill_presentation(presentation)
    assert accepted == before
    assert presentation == before
    assert accepted["metadata"]["resultProtocol"]["warnings"] == warnings
    assert accepted["metadata"]["artifactQuality"] == ("degraded" if warnings else "standard")


@pytest.mark.parametrize(
    "runtime,mime",
    [
        ("python_adapter", "application/json"),
        ("reasoning_adapter", "application/json"),
        ("deterministic", "application/json"),
        ("external_adapter", "application/json"),
        ("huiyan_durable_skill", "text/plain"),
        ("llm_reasoning", "text/markdown"),
        ("llm_reasoning", "text/yaml"),
    ],
)
def test_existing_adapter_envelopes_remain_accepted(runtime, mime):
    presentation = _presentation()
    presentation.update(mimeType=mime, metadata={"runtime": runtime, "skillId": "current_skill"})
    assert _validated_skill_presentation(presentation) == presentation


@pytest.mark.parametrize(
    "field,value",
    [
        ("unexpected", "anything"),
        ("runtime", "unknown"),
        ("runtime", []),
        ("skillId", " "),
        ("skillId", 12),
        ("artifactQuality", "degraded"),
        ("warnings", ["invented warning"]),
        ("warnings", "not-a-list"),
        ("resultProtocol", None),
        ("resultProtocol", []),
        ("resultProtocol", {"status": "ok"}),
    ],
)
def test_rejects_invalid_or_inconsistent_metadata(field, value):
    presentation = _presentation()
    presentation["metadata"][field] = value
    _assert_rejected(presentation)


@pytest.mark.parametrize(
    "field,value",
    [
        ("version", "result-protocol.v2"),
        ("status", "fatal"),
        ("status", "no_usable_result"),
        ("status", "unknown"),
        ("warnings", "not-a-list"),
        ("warnings", [None]),
        ("accepted_items", [{"path": "x", "count": True}]),
        ("accepted_items", [{"path": "x", "count": -1}]),
        ("quarantined_items", [{"path": "x"}]),
        ("repaired_items", [{"path": "x"}]),
        ("coverage", []),
        ("unexpected", "anything"),
    ],
)
def test_rejects_malformed_noncanonical_or_unusable_protocol(field, value):
    presentation = _presentation()
    presentation["metadata"]["resultProtocol"][field] = value
    _assert_rejected(presentation)


@pytest.mark.parametrize("field", ["resultProtocol", "artifactQuality"])
def test_rejects_partial_quality_metadata(field):
    presentation = _presentation()
    del presentation["metadata"][field]
    _assert_rejected(presentation)


@pytest.mark.parametrize(
    "field,value",
    [
        ("title", ""),
        ("title", "x" * 241),
        ("title", "unsafe\nname"),
        ("mimeType", "application/json"),
        ("mimeType", "text/html"),
        ("metadata", []),
        ("contentSha256", "not-a-sha256"),
        ("contentSize", 0),
        ("contentSize", -1),
        ("contentSize", True),
        ("contentSize", "12"),
    ],
)
def test_rejects_invalid_presentation_envelope(field, value):
    presentation = _presentation()
    presentation[field] = value
    _assert_rejected(presentation)


def _assert_rejected(presentation):
    with pytest.raises(DurableExecutionError) as error:
        _validated_skill_presentation(presentation)
    assert error.value.status_code == 422
    assert error.value.detail == "invalid_skill_artifact_presentation"


def test_preserves_all_degraded_protocol_facts():
    presentation = _presentation()
    protocol = normalize_result_protocol(
        {
            "accepted_items": [{"path": "queries", "count": 7}],
            "quarantined_items": [{"path": "queries[7]", "reason": "invalid query"}],
            "repaired_items": [{"path": "sources[3]", "action": "normalized identity"}],
            "coverage": {"accepted_query_count": 7, "source_ids": ["s1", "s2"]},
            "warnings": ["one query skipped"],
        }
    )
    presentation["metadata"].update(
        resultProtocol=protocol, artifactQuality="degraded", warnings=protocol["warnings"]
    )
    assert _validated_skill_presentation(presentation) == presentation


@pytest.mark.parametrize("status", ["fatal", "no_usable_result"])
def test_rejects_canonical_unusable_quality_even_when_projection_matches(status):
    presentation = _presentation()
    protocol = normalize_result_protocol({"status": status})
    presentation["metadata"].update(
        resultProtocol=protocol, artifactQuality=artifact_quality_for_protocol(protocol)
    )
    _assert_rejected(presentation)
