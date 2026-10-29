from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError


REPO_ROOT = Path(__file__).resolve().parents[3]
SCHEMA_REL = "schemas/agents/reviews/independent_result_review_v1.schema.json"
SCHEMA_PATH = REPO_ROOT / SCHEMA_REL
REGISTRY_PATH = REPO_ROOT / "schemas" / "agents" / "agent-registry.yaml"

FOUR_BODY_FIELDS = ("verdict", "basis", "issues", "missingInputs")
ALLOWED_VERDICTS = (
    "usable",
    "needs_revision",
    "needs_input",
    "insufficient_materials",
    "human_decision_required",
    "external_result_unknown",
)


def _load_schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _validator() -> Draft202012Validator:
    schema = _load_schema()
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _valid_usable() -> dict[str, Any]:
    return {
        "verdict": "usable",
        "basis": "The specialist Artifact retains source locators and stays within the declared scope.",
        "issues": [],
        "missingInputs": [],
    }


def _valid_non_usable() -> dict[str, Any]:
    return {
        "verdict": "needs_revision",
        "basis": "The Artifact overclaims completion and omits required evidence links.",
        "issues": [
            {
                "code": "overclaim_completed_work",
                "severity": "high",
                "message": "Body implies retrieval completed without authorization evidence.",
            }
        ],
        "missingInputs": ["source_locator_table"],
    }


def test_registry_binds_review_schema_ref_not_research_artifact() -> None:
    registry = yaml.safe_load(REGISTRY_PATH.read_text(encoding="utf-8"))
    agent = next(row for row in registry["agents"] if row["agentId"] == "result_reliability_audit")
    review = agent["reviewContract"]
    output = agent["outputContract"]

    assert review["kind"] == "independent_result_review"
    assert review["contractId"] == "independent_result_review_v1"
    assert review["semanticVersion"] == "1.0.0"
    assert review["schemaRef"] == SCHEMA_REL
    assert list(review["allowedVerdicts"]) == list(ALLOWED_VERDICTS)
    assert list(review["requiredFields"]) == list(FOUR_BODY_FIELDS)
    assert (REPO_ROOT / review["schemaRef"]).is_file()

    # Research Artifact identity remains separate and must not be replaced.
    assert output["artifactContract"] == "result_reliability_audit_artifact_v1"
    assert "artifactSchema" not in review
    assert "artifactContract" not in review
    assert "artifactKind" not in review


def test_schema_is_valid_json_with_four_required_closed_fields() -> None:
    schema = _load_schema()
    assert schema["$id"].endswith("/independent_result_review_v1.schema.json")
    assert schema["contractId"] == "independent_result_review_v1"
    assert schema["semanticVersion"] == "1.0.0"
    assert schema["additionalProperties"] is False
    assert list(schema["required"]) == list(FOUR_BODY_FIELDS)
    assert set(schema["properties"]) == set(FOUR_BODY_FIELDS)
    assert schema["properties"]["verdict"]["$ref"] == "#/$defs/reviewVerdict"
    assert schema["$defs"]["reviewVerdict"]["enum"] == list(ALLOWED_VERDICTS)

    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    assert validator.is_valid(_valid_usable())
    assert validator.is_valid(_valid_non_usable())


def test_valid_usable_passes() -> None:
    _validator().validate(_valid_usable())


def test_valid_non_usable_passes() -> None:
    _validator().validate(_valid_non_usable())


def test_unknown_verdict_is_rejected() -> None:
    body = _valid_usable()
    body["verdict"] = "pass_with_caveats"
    with pytest.raises(ValidationError):
        _validator().validate(body)


def test_wrong_nested_type_is_rejected() -> None:
    body = _valid_non_usable()
    body["issues"] = "not-an-array"
    with pytest.raises(ValidationError):
        _validator().validate(body)

    nested = _valid_non_usable()
    nested["issues"] = [{"code": "x", "severity": "high", "message": "ok", "extra": True}]
    with pytest.raises(ValidationError):
        _validator().validate(nested)

    bad_issue_type = _valid_non_usable()
    bad_issue_type["issues"] = ["string-issue"]
    with pytest.raises(ValidationError):
        _validator().validate(bad_issue_type)


def test_extra_field_is_rejected() -> None:
    body = _valid_usable()
    body["unexpected_top_level"] = "not allowed"
    with pytest.raises(ValidationError):
        _validator().validate(body)

    nested = _valid_non_usable()
    nested["issues"][0]["hint"] = "not allowed"
    with pytest.raises(ValidationError):
        _validator().validate(nested)


def test_empty_required_strings_are_rejected() -> None:
    body = _valid_usable()
    body["basis"] = ""
    with pytest.raises(ValidationError):
        _validator().validate(body)

    missing = _valid_non_usable()
    missing["missingInputs"] = [""]
    with pytest.raises(ValidationError):
        _validator().validate(missing)


def test_schema_document_is_parseable_without_schema_error() -> None:
    try:
        Draft202012Validator.check_schema(_load_schema())
    except SchemaError as exc:  # pragma: no cover - failure path
        pytest.fail(f"schema is not a valid Draft 2020-12 document: {exc}")
