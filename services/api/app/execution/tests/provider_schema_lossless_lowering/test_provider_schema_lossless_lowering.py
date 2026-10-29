from __future__ import annotations

from copy import deepcopy
from typing import Any

from jsonschema import Draft202012Validator

from app.execution.provider_schema_lossless_lowering import (
    NOT_LOWERABLE,
    lower_provider_schema_losslessly,
)


def _assert_equivalent_for(
    original: dict[str, Any],
    lowered: dict[str, Any],
    *,
    accepted: Any,
    rejected: Any,
) -> None:
    original_validator = Draft202012Validator(original)
    lowered_validator = Draft202012Validator(lowered)

    assert original_validator.is_valid(accepted)
    assert lowered_validator.is_valid(accepted)
    assert not original_validator.is_valid(rejected)
    assert not lowered_validator.is_valid(rejected)


def test_typed_const_preserves_positive_and_negative_validation() -> None:
    original = {"const": False}
    snapshot = deepcopy(original)

    lowered = lower_provider_schema_losslessly(original)

    assert lowered == {"const": False, "type": "boolean"}
    assert original == snapshot
    _assert_equivalent_for(original, lowered, accepted=False, rejected=True)


def test_typed_const_recurses_only_through_subschemas() -> None:
    original = {
        "type": "object",
        "properties": {"status": {"const": "ready"}},
        "default": {"oneOf": "instance data, not a subschema"},
    }

    lowered = lower_provider_schema_losslessly(original)

    assert lowered["properties"]["status"] == {"const": "ready", "type": "string"}
    assert lowered["default"] == original["default"]


def test_untyped_integral_const_fails_closed_instead_of_guessing_type() -> None:
    assert lower_provider_schema_losslessly({"const": 1}) is NOT_LOWERABLE


def test_disjoint_primitive_oneof_preserves_positive_and_negative_validation() -> None:
    original = {"oneOf": [{"type": "string"}, {"type": "boolean"}]}

    lowered = lower_provider_schema_losslessly(original)

    assert lowered == {"anyOf": [{"type": "string"}, {"type": "boolean"}]}
    _assert_equivalent_for(original, lowered, accepted="ready", rejected=3)


def test_mutually_exclusive_typed_discriminators_are_statically_disjoint() -> None:
    original = {
        "oneOf": [
            {
                "type": "object",
                "required": ["kind"],
                "properties": {"kind": {"type": "string", "const": "alpha"}},
            },
            {
                "type": "object",
                "required": ["kind"],
                "properties": {"kind": {"type": "string", "const": "beta"}},
            },
        ]
    }

    lowered = lower_provider_schema_losslessly(original)

    assert "oneOf" not in lowered
    assert lowered["anyOf"] == original["oneOf"]
    _assert_equivalent_for(
        original,
        lowered,
        accepted={"kind": "alpha"},
        rejected={"kind": "gamma"},
    )


def test_overlapping_oneof_fails_closed() -> None:
    overlapping = {"oneOf": [{"type": "number"}, {"type": "integer"}]}

    assert lower_provider_schema_losslessly(overlapping) is NOT_LOWERABLE


def test_unproven_discriminator_oneof_fails_closed() -> None:
    missing_required_discriminator = {
        "oneOf": [
            {
                "type": "object",
                "properties": {"kind": {"type": "string", "const": "alpha"}},
            },
            {
                "type": "object",
                "properties": {"kind": {"type": "string", "const": "beta"}},
            },
        ]
    }

    assert lower_provider_schema_losslessly(missing_required_discriminator) is NOT_LOWERABLE


def test_not_pattern_is_never_lowered_to_a_regex_complement() -> None:
    schema = {"type": "string", "not": {"pattern": "[aeiou]"}}

    assert lower_provider_schema_losslessly(schema) is NOT_LOWERABLE


def test_generic_not_fails_closed() -> None:
    assert (
        lower_provider_schema_losslessly({"type": "string", "not": {"type": "null"}})
        is NOT_LOWERABLE
    )


def test_complex_regex_negation_fails_closed() -> None:
    assert (
        lower_provider_schema_losslessly({"type": "string", "not": {"pattern": "[ab]+"}})
        is NOT_LOWERABLE
    )


def test_untyped_character_class_negation_fails_closed() -> None:
    assert lower_provider_schema_losslessly({"not": {"pattern": "[ab]"}}) is NOT_LOWERABLE


def test_refusal_is_atomic_when_unsupported_construct_is_nested() -> None:
    original = {
        "type": "object",
        "properties": {
            "valid": {"const": False},
            "unsupported": {"oneOf": [{"type": "number"}, {"type": "integer"}]},
        },
    }

    assert lower_provider_schema_losslessly(original) is NOT_LOWERABLE
