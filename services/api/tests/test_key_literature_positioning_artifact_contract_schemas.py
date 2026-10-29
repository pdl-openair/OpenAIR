from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError


REPO_ROOT = Path(__file__).resolve().parents[3]
SCHEMA_REL = "schemas/agents/artifacts/key_literature_positioning_artifact_v1.schema.json"
SCHEMA_PATH = REPO_ROOT / SCHEMA_REL
REGISTRY_PATH = REPO_ROOT / "schemas" / "agents" / "agent-registry.yaml"

SEVEN_BODY_FIELDS = (
    "search_boundary",
    "keyword_families",
    "source_scope",
    "candidate_literature",
    "reading_priority",
    "coverage_limitations",
    "handoff",
)


def _load_schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _validator() -> Draft202012Validator:
    schema = _load_schema()
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _valid_strategy_only() -> dict[str, Any]:
    return {
        "search_boundary": {
            "authorization_status": "strategy_only",
            "retrieval_outcome": "not_attempted",
            "target_question": "How is SEI interface stability evaluated for lithium metal batteries?",
            "included_scope": "SEI stability metrics and review entry points for Li-metal anodes.",
            "excluded_scope": "Do not claim a completed literature review or verified mechanism conclusions.",
            "constraints": ["prefer recent reviews and benchmarks"],
        },
        "keyword_families": [
            {
                "family_id": "fam_sei",
                "label": "SEI stability",
                "keywords": ["SEI", "lithium metal", "interface stability"],
            }
        ],
        "source_scope": {
            "source_kinds": ["peer_reviewed", "preprint"],
            "languages": ["en", "zh"],
            "collections": [],
        },
        "candidate_literature": [],
        "reading_priority": [],
        "coverage_limitations": [
            {
                "limitation_id": "lim_strategy_only",
                "limitation_kind": "coverage",
                "statement": "No retrieval was authorized; this body is a search strategy only.",
            }
        ],
        "handoff": {
            "handoff_status": "ready_for_next_consumer",
            "next_consumer": "controller_planning",
            "candidate_ids": [],
            "instruction": "Confirm the search boundary before authorizing retrieval; do not invent candidate papers.",
            "requires_user_confirmation_before_external_execution": True,
        },
    }


def _valid_with_candidates() -> dict[str, Any]:
    return {
        "search_boundary": {
            "authorization_status": "retrieval_authorized",
            "retrieval_outcome": "attempted",
            "target_question": "Which benchmarks evaluate hallucination mitigation for retrieval-augmented generation?",
            "included_scope": "RAG hallucination evaluation benchmarks and mitigation comparisons.",
            "excluded_scope": "Do not treat candidates as a completed systematic review.",
            "constraints": ["arXiv and peer-reviewed venues"],
        },
        "keyword_families": [
            {
                "family_id": "fam_rag",
                "label": "RAG hallucination",
                "keywords": ["retrieval augmented generation", "hallucination", "benchmark"],
            }
        ],
        "source_scope": {
            "source_kinds": ["peer_reviewed", "preprint", "dataset_or_benchmark"],
            "languages": ["en"],
            "collections": ["arxiv"],
        },
        "candidate_literature": [
            {
                "candidate_id": "cand_alpha",
                "source_identifier": "arXiv:2401.00001",
                "title": "Example RAG hallucination benchmark survey",
                "screening_status": "screened_in",
                "screening_rationale": "Matches the authorized search boundary as an entry review candidate.",
                "role_hint": "entry_review",
            }
        ],
        "reading_priority": [
            {
                "rank": 1,
                "candidate_id": "cand_alpha",
                "reason": "Highest-priority entry point for the authorized RAG hallucination scope.",
            }
        ],
        "coverage_limitations": [
            {
                "limitation_id": "lim_access",
                "limitation_kind": "access",
                "statement": "Only metadata-level screening was retained; full-text claims are out of scope.",
            }
        ],
        "handoff": {
            "handoff_status": "ready_for_next_consumer",
            "next_consumer": "paper_evidence_extraction",
            "candidate_ids": ["cand_alpha"],
            "instruction": "Extract claim-evidence rows only from selected readable materials with preserved source identifiers.",
            "requires_user_confirmation_before_external_execution": True,
        },
    }


def test_registry_references_resolvable_artifact_schema_not_required_fields() -> None:
    registry = yaml.safe_load(REGISTRY_PATH.read_text(encoding="utf-8"))
    agent = next(row for row in registry["agents"] if row["agentId"] == "key_literature_positioning")
    output = agent["outputContract"]

    assert output["artifactContract"] == "key_literature_positioning_artifact_v1"
    assert output["artifactSchema"] == SCHEMA_REL
    assert "requiredFields" not in output
    assert (REPO_ROOT / output["artifactSchema"]).is_file()


def test_schema_is_valid_json_with_seven_required_closed_fields() -> None:
    schema = _load_schema()
    assert schema["$id"].endswith("/key_literature_positioning_artifact_v1.schema.json")
    assert schema["contractId"] == "key_literature_positioning_artifact_v1"
    assert schema["semanticVersion"] == "1.0.0"
    assert schema["additionalProperties"] is False
    assert list(schema["required"]) == list(SEVEN_BODY_FIELDS)
    assert set(schema["properties"]) == set(SEVEN_BODY_FIELDS)

    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    assert validator.is_valid(_valid_strategy_only())
    assert validator.is_valid(_valid_with_candidates())


def test_valid_strategy_only_passes() -> None:
    _validator().validate(_valid_strategy_only())


def test_valid_with_candidates_passes() -> None:
    _validator().validate(_valid_with_candidates())


def test_strategy_only_rejects_invented_candidates() -> None:
    body = _valid_strategy_only()
    body["candidate_literature"] = _valid_with_candidates()["candidate_literature"]
    body["reading_priority"] = _valid_with_candidates()["reading_priority"]
    with pytest.raises(ValidationError):
        _validator().validate(body)


def test_unknown_retrieval_rejects_candidates_without_uncertainty_limit() -> None:
    body = _valid_strategy_only()
    body["search_boundary"]["authorization_status"] = "retrieval_authorized"
    body["search_boundary"]["retrieval_outcome"] = "unknown"
    body["coverage_limitations"] = [
        {
            "limitation_id": "lim_cov",
            "limitation_kind": "coverage",
            "statement": "Coverage is incomplete but uncertainty is not declared.",
        }
    ]
    with pytest.raises(ValidationError):
        _validator().validate(body)


def test_extra_field_is_rejected() -> None:
    body = _valid_strategy_only()
    body["unexpected_top_level"] = "not allowed"
    with pytest.raises(ValidationError):
        _validator().validate(body)

    nested = _valid_with_candidates()
    nested["handoff"]["extra_note"] = "not allowed"
    with pytest.raises(ValidationError):
        _validator().validate(nested)


def test_wrong_nested_type_is_rejected() -> None:
    body = _valid_with_candidates()
    body["keyword_families"] = "not-an-array"
    with pytest.raises(ValidationError):
        _validator().validate(body)


def test_schema_document_is_parseable_without_schema_error() -> None:
    try:
        Draft202012Validator.check_schema(_load_schema())
    except SchemaError as exc:  # pragma: no cover - failure path
        pytest.fail(f"schema is not a valid Draft 2020-12 document: {exc}")
