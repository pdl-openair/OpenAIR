from __future__ import annotations

from typing import Any

import pytest
from app.execution.skill_contract_policy import apply_semantic_contract


def _output_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["finding", "confidence", "priority"],
        "properties": {
            "finding": {"type": "string", "minLength": 1},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "priority": {
                "type": "string",
                "enum": ["HIGH", "LOW"],
                "default": "LOW",
            },
        },
    }


def test_numeric_string_is_preserved_with_output_gap() -> None:
    normalized, _warnings = apply_semantic_contract(
        {"finding": "Supported result", "confidence": "0.7", "priority": "HIGH"},
        _output_schema(),
        role="output",
    )

    assert normalized == {
        "finding": "Supported result",
        "confidence": "0.7",
        "priority": "HIGH",
    }


def test_enum_spelling_is_preserved_with_output_gap() -> None:
    normalized, _warnings = apply_semantic_contract(
        {"finding": "Supported result", "confidence": 0.7, "priority": "high"},
        _output_schema(),
        role="output",
    )

    assert normalized["priority"] == "high"


@pytest.mark.parametrize("patch", [
    {"priority": "UNKNOWN"},
    {"priority": None},
    {"confidence": "1.7"},
    {"confidence": "NaN"},
    {"unsupported_claim": "Retain the original claim"},
])
def test_local_output_gap_preserves_usable_content(patch) -> None:
    original = {"finding": "Supported result", "confidence": 0.7, "priority": "HIGH", **patch}
    received, warnings = apply_semantic_contract(original, _output_schema(), role="output")
    assert received["finding"] == "Supported result"
    assert set(received) == set(original)
    assert original == {
        "finding": "Supported result", "confidence": 0.7, "priority": "HIGH", **patch
    }
    assert any(w.startswith("output_contract_gap:") for w in warnings)
    if "priority" in patch:
        assert received["priority"] == patch["priority"]
    if "unsupported_claim" in patch:
        assert received["unsupported_claim"] == patch["unsupported_claim"]


def test_missing_output_field_is_preserved_as_gap_without_default() -> None:
    original = {"finding": "Supported result", "confidence": 0.7}
    received, warnings = apply_semantic_contract(original, _output_schema(), role="output")
    assert received == original
    assert "priority" not in received
    assert "output_contract_gap:$:required" in warnings


def test_adjacent_input_unknown_enum_tolerance_is_unchanged() -> None:
    normalized, warnings = apply_semantic_contract(
        {"priority": "UNKNOWN"},
        {
            "type": "object",
            "required": ["priority"],
            "properties": {
                "priority": {
                    "type": "string",
                    "enum": ["HIGH", "LOW"],
                    "default": "LOW",
                }
            },
        },
        role="input",
    )

    assert normalized == {"priority": "UNKNOWN"}
    assert "input_contract_gap:$.priority:enum" in warnings


def test_adjacent_input_ambiguous_enum_keeps_existing_first_match() -> None:
    normalized, warnings = apply_semantic_contract(
        {"priority": "High"},
        {
            "type": "object",
            "required": ["priority"],
            "properties": {
                "priority": {
                    "type": "string",
                    "enum": ["HIGH", "high"],
                }
            },
        },
        role="input",
    )

    assert normalized == {"priority": "High"}
    assert "input_contract_gap:$.priority:enum" in warnings
