"""Fail-closed, semantics-preserving Provider Schema lowerings.

This module is deliberately independent from Provider transport.  It only
implements transformations whose instance-validation equivalence is locally
provable.  A schema containing a targeted construct outside those proofs is
returned as ``NOT_LOWERABLE`` without exposing a partially lowered result.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import math
from typing import Any, Final


class _NotLowerable:
    __slots__ = ()

    def __repr__(self) -> str:
        return "NOT_LOWERABLE"


NOT_LOWERABLE: Final = _NotLowerable()


class _RefuseLowering(Exception):
    pass


_JSON_TYPES = frozenset({"array", "boolean", "integer", "null", "number", "object", "string"})
_SCHEMA_MAP_KEYWORDS = frozenset({
    "$defs",
    "definitions",
    "dependentSchemas",
    "patternProperties",
    "properties",
})
_SCHEMA_ARRAY_KEYWORDS = frozenset({"allOf", "anyOf", "prefixItems"})
_SCHEMA_VALUE_KEYWORDS = frozenset({
    "additionalItems",
    "additionalProperties",
    "contains",
    "contentSchema",
    "else",
    "if",
    "propertyNames",
    "then",
    "unevaluatedItems",
    "unevaluatedProperties",
})


def lower_provider_schema_losslessly(schema: Any) -> Any:
    """Return an equivalent lowered copy, or ``NOT_LOWERABLE``.

    The input is never mutated. Refusal is atomic: an unsupported ``const`` or
    ``oneOf``, or any ``not`` anywhere in the schema, refuses the complete result.
    """
    try:
        return _lower_schema(schema)
    except _RefuseLowering:
        return NOT_LOWERABLE


def _lower_schema(schema: Any) -> Any:
    if isinstance(schema, bool):
        return schema
    if not isinstance(schema, Mapping) or any(not isinstance(key, str) for key in schema):
        raise _RefuseLowering

    additions: dict[str, Any] = {}
    removed: set[str] = set()

    if "const" in schema and "type" not in schema:
        inferred_type = _unique_const_type(schema["const"])
        if inferred_type is None:
            raise _RefuseLowering
        additions["type"] = inferred_type

    if "oneOf" in schema:
        if "anyOf" in schema:
            raise _RefuseLowering
        branches = schema["oneOf"]
        if not isinstance(branches, list) or not branches:
            raise _RefuseLowering
        if any(not isinstance(branch, Mapping) for branch in branches):
            raise _RefuseLowering
        for index, left in enumerate(branches):
            for right in branches[index + 1 :]:
                if not _schemas_proven_disjoint(left, right):
                    raise _RefuseLowering
        additions["anyOf"] = [_lower_schema(branch) for branch in branches]
        removed.add("oneOf")

    if "not" in schema:
        raise _RefuseLowering

    lowered: dict[str, Any] = {}
    for key, value in schema.items():
        if key in removed:
            continue
        if key in _SCHEMA_MAP_KEYWORDS:
            lowered[key] = _lower_schema_map(value)
        elif key in _SCHEMA_ARRAY_KEYWORDS:
            lowered[key] = _lower_schema_array(value)
        elif key in _SCHEMA_VALUE_KEYWORDS:
            lowered[key] = _lower_schema(value)
        elif key == "items":
            lowered[key] = _lower_items(value)
        elif key == "dependencies":
            lowered[key] = _lower_dependencies(value)
        else:
            lowered[key] = deepcopy(value)
    lowered.update(additions)
    return lowered


def _lower_schema_map(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise _RefuseLowering
    return {key: _lower_schema(subschema) for key, subschema in value.items()}


def _lower_schema_array(value: Any) -> list[Any]:
    if not isinstance(value, list):
        raise _RefuseLowering
    return [_lower_schema(subschema) for subschema in value]


def _lower_items(value: Any) -> Any:
    if isinstance(value, list):
        return [_lower_schema(subschema) for subschema in value]
    return _lower_schema(value)


def _lower_dependencies(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise _RefuseLowering
    lowered: dict[str, Any] = {}
    for key, dependency in value.items():
        if isinstance(dependency, list):
            lowered[key] = deepcopy(dependency)
        else:
            lowered[key] = _lower_schema(dependency)
    return lowered


def _unique_const_type(value: Any) -> str | None:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array" if _is_json_value(value) else None
    if isinstance(value, Mapping):
        return "object" if _is_json_value(value) else None
    if isinstance(value, int):
        # JSON Schema's integer instances are also numbers, so no unique type
        # can be recovered from an untyped integral constant.
        return None
    if isinstance(value, float):
        if not math.isfinite(value) or value.is_integer():
            return None
        return "number"
    return None


def _is_json_value(value: Any) -> bool:
    if value is None or isinstance(value, (bool, str)):
        return True
    if isinstance(value, int):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, list):
        return all(_is_json_value(item) for item in value)
    if isinstance(value, Mapping):
        return all(
            isinstance(key, str) and _is_json_value(item)
            for key, item in value.items()
        )
    return False


def _schemas_proven_disjoint(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    left_types = _explicit_types(left)
    right_types = _explicit_types(right)
    if left_types is not None and right_types is not None:
        if all(_types_disjoint(a, b) for a in left_types for b in right_types):
            return True

    left_discriminators = _typed_discriminators(left)
    right_discriminators = _typed_discriminators(right)
    for property_name in left_discriminators.keys() & right_discriminators.keys():
        if not _json_scalar_equal(
            left_discriminators[property_name],
            right_discriminators[property_name],
        ):
            return True
    return False


def _explicit_types(schema: Mapping[str, Any]) -> frozenset[str] | None:
    declared = schema.get("type")
    if isinstance(declared, str):
        return frozenset({declared}) if declared in _JSON_TYPES else None
    if isinstance(declared, list) and declared:
        if any(not isinstance(item, str) or item not in _JSON_TYPES for item in declared):
            return None
        return frozenset(declared)
    return None


def _types_disjoint(left: str, right: str) -> bool:
    if left == right:
        return False
    return {left, right} != {"integer", "number"}


def _typed_discriminators(schema: Mapping[str, Any]) -> dict[str, Any]:
    if schema.get("type") != "object":
        return {}
    required = schema.get("required")
    properties = schema.get("properties")
    if not isinstance(required, list) or not isinstance(properties, Mapping):
        return {}

    discriminators: dict[str, Any] = {}
    for property_name in required:
        if not isinstance(property_name, str):
            continue
        property_schema = properties.get(property_name)
        if not isinstance(property_schema, Mapping):
            continue
        declared_type = property_schema.get("type")
        if not isinstance(declared_type, str) or "const" not in property_schema:
            continue
        const = property_schema["const"]
        if _scalar_matches_type(const, declared_type):
            discriminators[property_name] = const
    return discriminators


def _scalar_matches_type(value: Any, declared_type: str) -> bool:
    if declared_type == "null":
        return value is None
    if declared_type == "boolean":
        return isinstance(value, bool)
    if declared_type == "string":
        return isinstance(value, str)
    if declared_type == "integer":
        return (
            isinstance(value, int)
            and not isinstance(value, bool)
        ) or (
            isinstance(value, float)
            and math.isfinite(value)
            and value.is_integer()
        )
    if declared_type == "number":
        return (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and (not isinstance(value, float) or math.isfinite(value))
        )
    return False


def _json_scalar_equal(left: Any, right: Any) -> bool:
    left_is_number = isinstance(left, (int, float)) and not isinstance(left, bool)
    right_is_number = isinstance(right, (int, float)) and not isinstance(right, bool)
    if left_is_number or right_is_number:
        return left_is_number and right_is_number and left == right
    return type(left) is type(right) and left == right


__all__ = ["lower_provider_schema_losslessly", "NOT_LOWERABLE"]
