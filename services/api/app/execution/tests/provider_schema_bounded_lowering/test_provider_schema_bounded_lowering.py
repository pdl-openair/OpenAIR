from __future__ import annotations

from copy import deepcopy
from itertools import product

import pytest
from jsonschema import Draft202012Validator

from app.execution.provider_schema_bounded_lowering import (
    BoundedSchemaLoweringError,
    GENERATED_BRANCHES_PER_OBJECT,
    GENERATED_BRANCHES_PER_SCHEMA,
    GLOBAL_SCHEMA_FINAL_LEAF_LIMIT,
    OPTIONAL_FIELDS_PER_OBJECT,
    PER_OBJECT_FINAL_LEAF_LIMIT,
    lower_bounded_schema,
    lower_closed_optional_presence,
    lower_exhaustive_finite_discriminator,
)


def _accepts(schema: dict, instance: object) -> bool:
    return Draft202012Validator(schema).is_valid(instance)


def _optional_schema(*, count: int = 2) -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["id"],
        "properties": {
            "id": {"type": "string"},
            **{f"optional_{index}": {"type": "integer"} for index in range(count)},
        },
    }


def _discriminator_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["kind"],
        "properties": {
            "kind": {"type": "string", "enum": ["measurement", "note"]},
            "value": {"type": ["number", "string"]},
            "unit": {"type": "string"},
            "text": {"type": "string"},
        },
        "allOf": [
            {
                "if": {"properties": {"kind": {"const": "measurement"}}},
                "then": {
                    "required": ["value", "unit"],
                    "properties": {"value": {"type": "number"}},
                },
            },
            {
                "if": {"properties": {"kind": {"const": "note"}}},
                "then": {"required": ["text"]},
            },
        ],
    }


def _combined_schema(*, discriminator_values: int, optional_fields: int) -> dict:
    values = [f"kind_{index}" for index in range(discriminator_values)]
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["kind"],
        "properties": {
            "kind": {"type": "string", "enum": values},
            **{f"optional_{index}": {"type": "integer"} for index in range(optional_fields)},
        },
        "allOf": [
            {
                "if": {"properties": {"kind": {"const": value}}},
                "then": {},
            }
            for value in values
        ],
    }


def test_optional_presence_expansion_preserves_every_small_object_decision() -> None:
    source = _optional_schema()
    frozen = deepcopy(source)

    lowered = lower_closed_optional_presence(source)

    assert source == frozen
    assert len(lowered["anyOf"]) == 4
    assert all(branch["additionalProperties"] is False for branch in lowered["anyOf"])
    values = [None, 7, "wrong"]
    instances = [{"id": "row"}, {"id": "row", "extra": 1}, {}, {"id": 4}]
    for left, right in product(values, repeat=2):
        candidate = {"id": "row"}
        if left is not None:
            candidate["optional_0"] = left
        if right is not None:
            candidate["optional_1"] = right
        instances.append(candidate)
    assert all(_accepts(source, value) == _accepts(lowered, value) for value in instances)
    assert _accepts(lowered, {"id": "row"})
    assert not _accepts(lowered, {"id": "row", "extra": 1})


def test_optional_presence_limits_and_unproven_objects_fail_closed() -> None:
    assert OPTIONAL_FIELDS_PER_OBJECT == 5
    assert GENERATED_BRANCHES_PER_OBJECT == 32
    assert GENERATED_BRANCHES_PER_SCHEMA == 256
    assert PER_OBJECT_FINAL_LEAF_LIMIT == 32
    assert GLOBAL_SCHEMA_FINAL_LEAF_LIMIT == 256

    at_object_limit = lower_closed_optional_presence(_optional_schema(count=5))
    assert len(at_object_limit["anyOf"]) == GENERATED_BRANCHES_PER_OBJECT

    at_schema_limit = lower_closed_optional_presence({
        "anyOf": [_optional_schema(count=5) for _ in range(8)],
    })
    assert sum(len(group["anyOf"]) for group in at_schema_limit["anyOf"]) == GENERATED_BRANCHES_PER_SCHEMA

    with pytest.raises(BoundedSchemaLoweringError, match="optional_fields_per_object_limit"):
        lower_closed_optional_presence(_optional_schema(count=6))

    too_many_across_schema = {
        "anyOf": [_optional_schema(count=5) for _ in range(9)],
    }
    with pytest.raises(BoundedSchemaLoweringError, match="generated_branches_per_schema_limit"):
        lower_closed_optional_presence(too_many_across_schema)

    open_object = _optional_schema()
    open_object["additionalProperties"] = True
    with pytest.raises(BoundedSchemaLoweringError, match="optional_presence_requires_closed_object"):
        lower_closed_optional_presence(open_object)

    schema_valued_map = _optional_schema()
    schema_valued_map["additionalProperties"] = {"type": "string"}
    with pytest.raises(BoundedSchemaLoweringError, match="optional_presence_requires_closed_object"):
        lower_closed_optional_presence(schema_valued_map)

    dynamic_object = _optional_schema()
    dynamic_object["patternProperties"] = {"^x-": {"type": "string"}}
    with pytest.raises(BoundedSchemaLoweringError, match="optional_presence_has_open_dynamic_fields"):
        lower_closed_optional_presence(dynamic_object)


def test_finite_discriminator_accepts_enum_conditioned_branches() -> None:
    source = _discriminator_schema()
    source["allOf"] = [
        {
            "if": {"properties": {"kind": {"enum": ["measurement", "note"]}}},
            "then": {},
        }
    ]
    # Not exhaustive as a single shared-effect enum covering the whole domain
    # still expands to one effect per domain value.
    lowered = lower_exhaustive_finite_discriminator(source)
    assert {branch["properties"]["kind"]["const"] for branch in lowered["anyOf"]} == {
        "measurement",
        "note",
    }


def test_finite_discriminator_expansion_preserves_branch_requirements() -> None:
    source = _discriminator_schema()
    frozen = deepcopy(source)

    lowered = lower_exhaustive_finite_discriminator(source)

    assert source == frozen
    assert len(lowered["anyOf"]) == 2
    assert all("allOf" not in branch for branch in lowered["anyOf"])
    instances = [
        {"kind": "measurement", "value": 2.5, "unit": "K"},
        {"kind": "measurement", "value": "2.5", "unit": "K"},
        {"kind": "measurement", "value": 2.5},
        {"kind": "note", "text": "observed"},
        {"kind": "note"},
        {"kind": "other", "text": "observed"},
        {"kind": "note", "text": "observed", "extra": True},
    ]
    assert all(_accepts(source, value) == _accepts(lowered, value) for value in instances)
    assert _accepts(lowered, instances[0])
    assert not _accepts(lowered, instances[2])
    assert _accepts(lowered, instances[3])
    assert not _accepts(lowered, instances[4])


def test_discriminator_intersection_keeps_json_boolean_and_number_distinct() -> None:
    source = _discriminator_schema()
    source["properties"]["value"]["enum"] = [1]
    source["allOf"][0]["then"]["properties"]["value"] = {"enum": [True]}

    lowered = lower_exhaustive_finite_discriminator(source)

    samples = [
        {"kind": "measurement", "value": 1, "unit": "K"},
        {"kind": "measurement", "value": True, "unit": "K"},
        {"kind": "note", "text": "observed", "value": 1},
    ]
    assert all(_accepts(source, value) == _accepts(lowered, value) for value in samples)
    measurement = next(
        branch
        for branch in lowered["anyOf"]
        if branch["properties"]["kind"]["const"] == "measurement"
    )
    assert measurement["properties"]["value"] is False


def test_discriminator_must_be_exhaustive_mutually_exclusive_and_closed() -> None:
    incomplete = _discriminator_schema()
    incomplete["allOf"].pop()
    with pytest.raises(BoundedSchemaLoweringError, match="discriminator_domain_is_not_exhaustive"):
        lower_exhaustive_finite_discriminator(incomplete)

    overlapping = _discriminator_schema()
    overlapping["allOf"][1]["if"]["properties"]["kind"]["const"] = "measurement"
    with pytest.raises(BoundedSchemaLoweringError, match="discriminator_branches_are_not_mutually_exclusive"):
        lower_exhaustive_finite_discriminator(overlapping)

    open_discriminator = _discriminator_schema()
    del open_discriminator["properties"]["kind"]["enum"]
    with pytest.raises(BoundedSchemaLoweringError, match="discriminator_domain_is_not_finite"):
        lower_exhaustive_finite_discriminator(open_discriminator)

    non_closed = _discriminator_schema()
    non_closed["additionalProperties"] = True
    with pytest.raises(BoundedSchemaLoweringError, match="discriminator_requires_closed_object"):
        lower_exhaustive_finite_discriminator(non_closed)

    dynamic = _discriminator_schema()
    dynamic["patternProperties"] = {"^x-": {"type": "string"}}
    with pytest.raises(BoundedSchemaLoweringError, match="discriminator_has_open_dynamic_fields"):
        lower_exhaustive_finite_discriminator(dynamic)

    unproven_effect = _discriminator_schema()
    unproven_effect["allOf"][0]["then"]["properties"]["value"] = {"pattern": "^[0-9]+$"}
    unproven_effect["properties"]["value"]["pattern"] = "^[1-9][0-9]*$"
    with pytest.raises(BoundedSchemaLoweringError, match="discriminator_effect_intersection_not_proven"):
        lower_exhaustive_finite_discriminator(unproven_effect)


def test_discriminator_per_object_limit_and_nested_256_boundary() -> None:
    too_many = _discriminator_schema()
    values = [f"v{index}" for index in range(GENERATED_BRANCHES_PER_OBJECT + 1)]
    too_many["properties"]["kind"]["enum"] = values
    too_many["allOf"] = [
        {
            "if": {"properties": {"kind": {"const": value}}},
            "then": {},
        }
        for value in values
    ]
    with pytest.raises(BoundedSchemaLoweringError, match="generated_branches_per_object_limit"):
        lower_exhaustive_finite_discriminator(too_many)

    combined = _discriminator_schema()
    combined["properties"]["payload"] = _optional_schema(count=4)
    combined["required"].extend(["value", "unit", "text", "payload"])
    at_limit = lower_bounded_schema({
        "anyOf": [deepcopy(combined) for _ in range(8)],
    })
    assert len(at_limit["anyOf"]) == 8


def test_combined_expansion_uses_final_per_object_leaf_count() -> None:
    one_by_32 = lower_bounded_schema(
        _combined_schema(discriminator_values=1, optional_fields=5)
    )
    assert len(one_by_32["anyOf"]) == 32

    two_by_16 = lower_bounded_schema(
        _combined_schema(discriminator_values=2, optional_fields=4)
    )
    assert len(two_by_16["anyOf"]) == 32

    reviewer_counterexample = _combined_schema(discriminator_values=2, optional_fields=5)
    frozen = deepcopy(reviewer_counterexample)
    with pytest.raises(BoundedSchemaLoweringError, match="generated_branches_per_object_limit"):
        lower_bounded_schema(reviewer_counterexample)
    assert reviewer_counterexample == frozen

    thirty_three = _combined_schema(discriminator_values=2, optional_fields=5)
    thirty_three["allOf"][0]["then"] = {
        "required": [f"optional_{index}" for index in range(5)],
    }
    with pytest.raises(BoundedSchemaLoweringError, match="generated_branches_per_object_limit"):
        lower_bounded_schema(thirty_three)

    two_by_17 = _combined_schema(discriminator_values=2, optional_fields=5)
    # The two discriminator paths yield 2 and 32 presence leaves: 34 total.
    two_by_17["allOf"][0]["then"] = {
        "required": [f"optional_{index}" for index in range(4)],
    }
    with pytest.raises(BoundedSchemaLoweringError, match="generated_branches_per_object_limit"):
        lower_bounded_schema(two_by_17)


def test_combined_expansion_preserves_discriminator_and_presence_decisions() -> None:
    source = _combined_schema(discriminator_values=2, optional_fields=2)
    frozen = deepcopy(source)

    lowered = lower_bounded_schema(source)

    instances = [{}, {"kind": "other"}, {"kind": "kind_0", "extra": 1}]
    for kind in ("kind_0", "kind_1"):
        for left, right in product((None, 7, "wrong"), repeat=2):
            candidate = {"kind": kind}
            if left is not None:
                candidate["optional_0"] = left
            if right is not None:
                candidate["optional_1"] = right
            instances.append(candidate)
    assert source == frozen
    assert all(_accepts(source, value) == _accepts(lowered, value) for value in instances)


def test_final_leaf_global_boundaries_and_nested_overflow() -> None:
    thirty_two_leaves = _combined_schema(discriminator_values=2, optional_fields=4)
    one_leaf = _combined_schema(discriminator_values=1, optional_fields=0)

    at_limit = lower_bounded_schema({
        "anyOf": [deepcopy(thirty_two_leaves) for _ in range(8)],
    })
    assert sum(len(group["anyOf"]) for group in at_limit["anyOf"]) == 256

    above_limit = {
        "anyOf": [deepcopy(thirty_two_leaves) for _ in range(8)] + [one_leaf],
    }
    with pytest.raises(BoundedSchemaLoweringError, match="generated_branches_per_schema_limit"):
        lower_bounded_schema(above_limit)

    nested = {
        "type": "object",
        "additionalProperties": False,
        "required": ["id"],
        "properties": {
            "id": {"type": "string"},
            "payload": _optional_schema(count=5),
            "optional_a": {"type": "integer"},
            "optional_b": {"type": "integer"},
            "optional_c": {"type": "integer"},
        },
    }
    frozen = deepcopy(nested)
    with pytest.raises(BoundedSchemaLoweringError, match="generated_branches_per_schema_limit"):
        lower_bounded_schema(nested)
    assert nested == frozen


def test_out_of_scope_combinators_are_not_flattened_or_removed() -> None:
    schema = {
        "oneOf": [{"type": "string"}, {"type": "integer"}],
        "not": {"const": "forbidden"},
        "contains": {"type": "integer"},
        "allOf": [{"minLength": 1}, {"maxLength": 4}],
    }
    assert lower_bounded_schema(schema) == schema
