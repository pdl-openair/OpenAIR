from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.execution.durable_skill_runtime import resolve_registered_durable_skill
from app.execution.external_source_identity import (
    validate_external_source_records_declaration,
    validate_result_external_sources,
)


ROOT = Path(__file__).resolve().parents[3]
OUTPUT_SCHEMA = json.loads(
    (
        ROOT
        / "skills"
        / "opentrajectory"
        / "deepxiv-progressive-reading-skill"
        / "output.schema.json"
    ).read_text(encoding="utf-8")
)
DECLARATION = [{
    "record_path": "/paper_packets/*",
    "identity_fields": [{
        "field_path": "/arxiv_id",
        "identity_type": "arxiv_identifier",
    }],
}]


def _packet(index: int, *, arxiv_id: str | None = None) -> dict[str, object]:
    return {
        "arxiv_id": arxiv_id if arxiv_id is not None else f"2409.{index:05d}",
        "content": "The paper says Unknown source is a common label in raw text.",
    }


def test_declaration_is_bound_to_the_formal_output_schema() -> None:
    assert validate_external_source_records_declaration(
        DECLARATION, output_schema=OUTPUT_SCHEMA
    ) == DECLARATION

    with pytest.raises(ValueError, match="external_source_path_not_in_output_schema"):
        validate_external_source_records_declaration(
            [{
                "record_path": "/paper_packets/*",
                "identity_fields": [{
                    "field_path": "/guessed_source",
                    "identity_type": "url_locator",
                }],
            }],
            output_schema=OUTPUT_SCHEMA,
        )


def test_deepxiv_definition_freezes_the_formal_declaration() -> None:
    skill = resolve_registered_durable_skill("deepxiv_progressive_reading")

    assert skill is not None
    assert skill["sourceContract"]["external_source_records"] == DECLARATION


def test_free_text_unknown_source_does_not_reject_a_valid_identity() -> None:
    content = json.dumps({"paper_packets": [_packet(1)]}, ensure_ascii=False)

    artifact_content, acceptance = validate_result_external_sources(
        content, DECLARATION
    )

    assert artifact_content == content
    assert acceptance == {
        "status": "validated",
        "acceptedCount": 1,
        "rejectedCount": 0,
        "records": [{
            "recordPath": "/paper_packets/0",
            "status": "accepted",
            "reasons": [],
        }],
    }


def test_one_invalid_identity_is_locally_rejected_from_twelve_records() -> None:
    packets = [_packet(index + 1) for index in range(12)]
    packets[7] = _packet(8, arxiv_id="Unknown source")
    result_content = json.dumps({
        "paper_packets": packets,
        "summary": {"producer_record_count": 12},
    }, ensure_ascii=False)

    artifact_content, acceptance = validate_result_external_sources(
        result_content, DECLARATION
    )

    assert len(json.loads(result_content)["paper_packets"]) == 12
    artifact = json.loads(artifact_content)
    assert len(artifact["paper_packets"]) == 11
    assert artifact["summary"] == {"producer_record_count": 12}
    assert acceptance["acceptedCount"] == 11
    assert acceptance["rejectedCount"] == 1
    assert acceptance["records"][7] == {
        "recordPath": "/paper_packets/7",
        "status": "rejected",
        "reasons": [{
            "fieldPath": "/arxiv_id",
            "identityType": "arxiv_identifier",
            "reasonCode": "source_identity_invalid",
        }],
    }


def test_missing_declaration_neither_guesses_nor_claims_validation() -> None:
    content = json.dumps({
        "paper_packets": [{"doi": "10.1000/looks-real"}],
        "text": "Unknown source",
    })

    artifact_content, acceptance = validate_result_external_sources(content, None)

    assert artifact_content == content
    assert acceptance == {
        "status": "not_declared",
        "acceptedCount": 0,
        "rejectedCount": 0,
        "records": [],
    }
