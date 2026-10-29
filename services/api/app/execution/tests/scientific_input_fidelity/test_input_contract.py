from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest
from app.execution.durable_skill_runtime import _validate_structured_json
from app.execution.skill_contract_policy import apply_semantic_contract
from jsonschema import Draft202012Validator
from referencing import Registry, Resource


def schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["finding", "judgment"],
        "properties": {
            "finding": {"type": "string", "minLength": 1},
            "judgment": {"enum": ["release", "defer"], "default": "release"},
            "optional": {"type": "string", "default": "invented"},
            "path": {"type": "string", "format": "path", "minLength": 1},
        },
    }


@pytest.mark.parametrize("judgment", ["release", "defer", "unsupported", None])
def test_canonical_input_and_judgments_are_preserved(judgment: Any) -> None:
    original = {"finding": "已有证据", "judgment": judgment, "extra": {"text": "保留正文"}}
    before = deepcopy(original)
    warnings: list[str] = []
    received = _validate_structured_json(
        original, schema(), "input", role="input", input_warnings=warnings
    )
    assert original == before
    assert received == before
    assert "optional" not in received
    assert not warnings
    received["extra"]["text"] = "changed projection"
    assert original == before


def test_missing_scientific_content_stays_missing_without_fail_all() -> None:
    value = {"finding": "已有可信结果"}
    received, warnings = apply_semantic_contract(value, schema(), role="input")
    assert received == value
    assert "input_contract_gap:$:required" in warnings


def test_empty_local_content_does_not_erase_other_valid_content() -> None:
    value = {"finding": "", "judgment": "defer", "extra": "另一项有效结果"}
    received, warnings = apply_semantic_contract(value, schema(), role="input")
    assert received == value
    assert not warnings






@pytest.mark.parametrize("value", [None, "supported", {"text": "evidence"}])
def test_valid_union_and_null_remain_valid(value: Any) -> None:
    contract = {"anyOf": [{"type": "object"}, {"type": "string"}, {"type": "null"}]}
    received, warnings = apply_semantic_contract(value, contract, role="input")
    assert received == value
    assert not warnings


@pytest.mark.parametrize("judgment", ["unsupported", "DeFer"])
def test_formal_output_preserves_judgment_and_reports_gap(judgment: str) -> None:
    value = {"finding": "valid", "judgment": judgment}
    warnings = []
    assert _validate_structured_json(value, schema(), "output", input_warnings=warnings) == value
    assert not warnings


def test_unique_input_representation_does_not_rewrite_canonical_value() -> None:
    value = {"finding": "valid", "judgment": " DEFER "}
    warnings: list[str] = []
    received = _validate_structured_json(
        value, schema(), "input", role="input", input_warnings=warnings
    )
    assert received["judgment"] == " DEFER "
    assert value["judgment"] == " DEFER "
    assert not warnings


def test_unknown_extra_is_not_dropped_from_output_to_force_success() -> None:
    value = {"finding": "valid", "judgment": "defer", "extra": "fact"}
    warnings = []
    assert _validate_structured_json(value, schema(), "output", input_warnings=warnings) == value
    assert not warnings




def test_numeric_normalization_cannot_round_a_scientific_value_or_enum() -> None:
    contract = {"type": "object", "properties": {"value": {"type": "integer"}}}
    original = {"value": "9007199254740993"}
    received, _warnings = apply_semantic_contract(original, contract, role="input")
    assert received["value"] == "9007199254740993"
    enum_contract = {"type": "object", "properties": {"value": {"enum": [9007199254740992]}}}
    received, warnings = apply_semantic_contract(original, enum_contract, role="input")
    assert received == original
    assert not warnings


def test_decimal_that_cannot_round_trip_is_preserved_as_a_gap() -> None:
    value = "0.12345678901234567890123456789"
    received, warnings = apply_semantic_contract(
        {"value": value},
        {"type": "object", "properties": {"value": {"type": "number"}}},
        role="input",
    )
    assert received == {"value": value}
    assert not warnings


def test_custom_validator_keeps_its_reference_resolution() -> None:
    referred = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {"judgment": {"enum": ["valid"]}},
    }
    contract = {"$ref": "urn:scientific-input"}
    registry = Registry().with_resource("urn:scientific-input", Resource.from_contents(referred))
    validator = Draft202012Validator(contract, registry=registry)
    received, warnings = apply_semantic_contract(
        {"judgment": "unsupported"},
        contract,
        role="input",
        schema_validator=validator.validate,
    )
    assert received == {"judgment": "unsupported"}
    assert not warnings




def test_unselected_union_alternative_does_not_add_a_required_execution_field() -> None:
    contract = {
        "anyOf": [
            {
                "type": "object",
                "required": ["finding"],
                "properties": {"finding": {"type": "string", "minLength": 1}},
            },
            {
                "type": "object",
                "required": ["path"],
                "properties": {"path": {"type": "string", "format": "path"}},
            },
        ]
    }
    value = {"finding": "", "notes": "另一部分有效科研内容"}
    received, warnings = apply_semantic_contract(value, contract, role="input")
    assert received == value
    assert not warnings
