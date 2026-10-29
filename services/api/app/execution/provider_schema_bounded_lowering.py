"""Bounded, semantics-preserving Provider Schema expansions.

The lowerers in this module operate only on structural JSON-Schema facts.  A
schema is returned only when the requested expansion can be proved complete;
unsupported shapes and branch-budget overruns fail closed.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
import math
from typing import Any


OPTIONAL_FIELDS_PER_OBJECT = 5
PER_OBJECT_FINAL_LEAF_LIMIT = 32
GLOBAL_SCHEMA_FINAL_LEAF_LIMIT = 256

# Backward-compatible names for callers of the R1 candidate.
GENERATED_BRANCHES_PER_OBJECT = PER_OBJECT_FINAL_LEAF_LIMIT
GENERATED_BRANCHES_PER_SCHEMA = GLOBAL_SCHEMA_FINAL_LEAF_LIMIT


class BoundedSchemaLoweringError(ValueError):
    """A requested bounded lowering could not be proved semantics-preserving."""

    def __init__(self, code: str, path: tuple[str | int, ...]) -> None:
        self.code = code
        self.path = path
        pointer = "#" + "".join(f"/{_escape_pointer_part(part)}" for part in path)
        super().__init__(f"{code} at {pointer}")


_SCHEMA_MAP_KEYS = frozenset({"$defs", "definitions", "dependentSchemas"})
_SCHEMA_MIXED_MAP_KEYS = frozenset({"dependencies"})
_SCHEMA_ARRAY_KEYS = frozenset({"allOf", "anyOf", "oneOf", "prefixItems"})
_SCHEMA_SINGLE_KEYS = frozenset({
    "additionalProperties",
    "additionalItems",
    "contains",
    "contentSchema",
    "else",
    "if",
    "items",
    "not",
    "propertyNames",
    "then",
    "unevaluatedItems",
    "unevaluatedProperties",
})


def lower_closed_optional_presence(schema: Mapping[str, Any]) -> dict[str, Any]:
    """Expand every structurally closed optional-property object in ``schema``."""
    return _BoundedLowerer(optional_presence=True, finite_discriminator=False).lower(schema)


def lower_exhaustive_finite_discriminator(schema: Mapping[str, Any]) -> dict[str, Any]:
    """Expand exhaustive finite ``if``/``then`` discriminators into ``anyOf``."""
    return _BoundedLowerer(optional_presence=False, finite_discriminator=True).lower(schema)


def lower_bounded_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    """Apply both proven lowerings with one schema-wide final-leaf budget."""
    return _BoundedLowerer(optional_presence=True, finite_discriminator=True).lower(schema)


class _BoundedLowerer:
    def __init__(self, *, optional_presence: bool, finite_discriminator: bool) -> None:
        self._optional_presence = optional_presence
        self._finite_discriminator = finite_discriminator

    def lower(self, schema: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(schema, Mapping):
            raise TypeError("Provider Schema must be a JSON object")
        lowered, _ = self._visit(dict(schema), ())
        if not isinstance(lowered, dict):
            raise AssertionError("object schema traversal returned a non-object")
        return lowered

    def _visit(self, value: Any, path: tuple[str | int, ...]) -> tuple[Any, int]:
        if isinstance(value, bool) or not isinstance(value, Mapping):
            return deepcopy(value), 0

        node: dict[str, Any] = deepcopy(dict(value))
        node, expanded = self._expand_current_object(node, path)

        if expanded:
            lowered_branches: list[Any] = []
            final_leaves = 0
            for index, branch in enumerate(node["anyOf"]):
                lowered_branch, descendant_leaves = self._visit(
                    branch,
                    (*path, "anyOf", index),
                )
                lowered_branches.append(lowered_branch)
                final_leaves += descendant_leaves or 1
                _require_global_leaf_limit(final_leaves, path)
            node["anyOf"] = lowered_branches

            # Root definition resources must remain at their document location so
            # local JSON Pointers keep the same identity.  They are traversed once
            # (rather than copied into every generated branch) and consume the same
            # schema-wide leaf budget as the expanded document body.
            for keyword in ("$defs", "definitions"):
                definitions = node.get(keyword)
                if not isinstance(definitions, Mapping):
                    continue
                lowered_definitions: dict[Any, Any] = {}
                for name, definition in definitions.items():
                    lowered_definition, definition_leaves = self._visit(
                        definition,
                        (*path, keyword, str(name)),
                    )
                    lowered_definitions[name] = lowered_definition
                    final_leaves += definition_leaves
                    _require_global_leaf_limit(final_leaves, path)
                node[keyword] = lowered_definitions
            return node, final_leaves

        final_leaves = 0
        for key in tuple(node):
            child = node[key]
            child_path = (*path, key)
            if key in {"properties", "patternProperties"} | _SCHEMA_MAP_KEYS:
                if isinstance(child, Mapping):
                    lowered_children: dict[Any, Any] = {}
                    for child_key, child_schema in child.items():
                        lowered_child, child_leaves = self._visit(
                            child_schema,
                            (*child_path, str(child_key)),
                        )
                        lowered_children[child_key] = lowered_child
                        final_leaves += child_leaves
                        _require_global_leaf_limit(final_leaves, path)
                    node[key] = lowered_children
            elif key in _SCHEMA_MIXED_MAP_KEYS:
                if isinstance(child, Mapping):
                    lowered_children = {}
                    for child_key, child_schema in child.items():
                        if isinstance(child_schema, (Mapping, bool)):
                            lowered_child, child_leaves = self._visit(
                                child_schema,
                                (*child_path, str(child_key)),
                            )
                            final_leaves += child_leaves
                            _require_global_leaf_limit(final_leaves, path)
                        else:
                            lowered_child = deepcopy(child_schema)
                        lowered_children[child_key] = lowered_child
                    node[key] = lowered_children
            elif key in _SCHEMA_ARRAY_KEYS:
                if _is_schema_sequence(child):
                    lowered_children = []
                    for index, child_schema in enumerate(child):
                        lowered_child, child_leaves = self._visit(
                            child_schema,
                            (*child_path, index),
                        )
                        lowered_children.append(lowered_child)
                        final_leaves += child_leaves
                        _require_global_leaf_limit(final_leaves, path)
                    node[key] = lowered_children
            elif key in _SCHEMA_SINGLE_KEYS:
                if key == "items" and _is_schema_sequence(child):
                    lowered_children = []
                    for index, child_schema in enumerate(child):
                        lowered_child, child_leaves = self._visit(
                            child_schema,
                            (*child_path, index),
                        )
                        lowered_children.append(lowered_child)
                        final_leaves += child_leaves
                        _require_global_leaf_limit(final_leaves, path)
                    node[key] = lowered_children
                elif isinstance(child, (Mapping, bool)):
                    lowered_child, child_leaves = self._visit(child, child_path)
                    node[key] = lowered_child
                    final_leaves += child_leaves
                    _require_global_leaf_limit(final_leaves, path)
        return node, final_leaves

    def _expand_current_object(
        self,
        node: dict[str, Any],
        path: tuple[str | int, ...],
    ) -> tuple[dict[str, Any], bool]:
        # A local reference such as ``#/$defs/Foo`` is resolved from the schema
        # document root.  Root expansion therefore has to leave definition
        # resources at the root instead of cloning them into each branch.
        root_definitions = {
            keyword: node.pop(keyword)
            for keyword in ("$defs", "definitions")
            if path == () and keyword in node
        }
        discriminator_expanded = self._finite_discriminator and _has_conditional_all_of(node)
        if discriminator_expanded:
            discriminator_branches = _finite_discriminator_branches(node, path)
        else:
            discriminator_branches = [node]

        final_branches: list[dict[str, Any]] = []
        optional_expanded = False
        for branch in discriminator_branches:
            optional_branches = (
                _optional_presence_branches(branch, path)
                if self._optional_presence and branch.get("type") == "object"
                else None
            )
            if optional_branches is None:
                final_branches.append(branch)
            else:
                optional_expanded = True
                final_branches.extend(optional_branches)
            if len(final_branches) > PER_OBJECT_FINAL_LEAF_LIMIT:
                raise BoundedSchemaLoweringError("generated_branches_per_object_limit", path)

        if not discriminator_expanded and not optional_expanded:
            node.update(root_definitions)
            return node, False
        expanded_node: dict[str, Any] = {"type": "object"}
        expanded_node.update(root_definitions)
        expanded_node["anyOf"] = final_branches
        return expanded_node, True


def _optional_presence_branches(
    node: dict[str, Any],
    path: tuple[str | int, ...],
) -> list[dict[str, Any]] | None:
    if "properties" not in node and "required" not in node:
        return None
    properties, required = _require_explicit_object_shape(node, path, "optional_presence")
    optional = [name for name in properties if name not in required]
    if not optional:
        return None
    _require_closed_object(node, path, "optional_presence")
    if len(optional) > OPTIONAL_FIELDS_PER_OBJECT:
        raise BoundedSchemaLoweringError("optional_fields_per_object_limit", path)

    branch_count = 1 << len(optional)
    branches: list[dict[str, Any]] = []
    for mask in range(branch_count):
        present_optional = [
            name for index, name in enumerate(optional) if mask & (1 << index)
        ]
        present = set(required) | set(present_optional)
        branch = deepcopy(node)
        branch["properties"] = {
            name: deepcopy(property_schema)
            for name, property_schema in properties.items()
            if name in present
        }
        branch["required"] = [*required, *present_optional]
        branches.append(branch)
    return branches


def _finite_discriminator_branches(
    node: dict[str, Any],
    path: tuple[str | int, ...],
) -> list[dict[str, Any]]:
    properties, required = _require_explicit_object_shape(node, path, "discriminator")
    _require_closed_object(node, path, "discriminator")
    raw_clauses = node.get("allOf")
    if not _is_schema_sequence(raw_clauses) or not raw_clauses:
        raise BoundedSchemaLoweringError("discriminator_requires_conditional_all_of", path)

    parsed: list[tuple[str, Any, Mapping[str, Any]]] = []
    for index, raw_clause in enumerate(raw_clauses):
        clause_path = (*path, "allOf", index)
        parsed.extend(_parse_discriminator_clause(raw_clause, clause_path))

    discriminator = parsed[0][0]
    if any(field != discriminator for field, _, _ in parsed):
        raise BoundedSchemaLoweringError("discriminator_is_not_unique", path)
    if discriminator not in required:
        raise BoundedSchemaLoweringError("discriminator_is_not_required", path)

    discriminator_schema = properties.get(discriminator)
    if not isinstance(discriminator_schema, Mapping):
        raise BoundedSchemaLoweringError("discriminator_schema_is_not_explicit", (*path, "properties", discriminator))
    discriminator_type = discriminator_schema.get("type")
    if discriminator_type not in {"string", "integer", "number", "boolean"}:
        raise BoundedSchemaLoweringError("discriminator_type_is_not_explicit", (*path, "properties", discriminator))
    domain = discriminator_schema.get("enum")
    if not isinstance(domain, list) or not domain:
        raise BoundedSchemaLoweringError("discriminator_domain_is_not_finite", (*path, "properties", discriminator))
    if any(not _matches_json_type(value, discriminator_type) for value in domain):
        raise BoundedSchemaLoweringError(
            "discriminator_enum_type_mismatch",
            (*path, "properties", discriminator, "enum"),
        )
    if _has_json_duplicates(domain):
        raise BoundedSchemaLoweringError(
            "discriminator_domain_is_not_unique",
            (*path, "properties", discriminator, "enum"),
        )

    clause_by_domain_index: dict[int, Mapping[str, Any]] = {}
    for _, value, effect in parsed:
        domain_index = _json_value_index(domain, value)
        if domain_index is None:
            raise BoundedSchemaLoweringError("discriminator_condition_outside_domain", path)
        if domain_index in clause_by_domain_index:
            raise BoundedSchemaLoweringError("discriminator_branches_are_not_mutually_exclusive", path)
        clause_by_domain_index[domain_index] = effect
    if len(clause_by_domain_index) != len(domain):
        raise BoundedSchemaLoweringError("discriminator_domain_is_not_exhaustive", path)
    if len(domain) > PER_OBJECT_FINAL_LEAF_LIMIT:
        raise BoundedSchemaLoweringError("generated_branches_per_object_limit", path)

    base = {key: deepcopy(value) for key, value in node.items() if key != "allOf"}
    branches: list[dict[str, Any]] = []
    for domain_index, value in enumerate(domain):
        branch = deepcopy(base)
        branch_properties = dict(branch["properties"])
        branch_properties[discriminator] = _intersect_schema(
            branch_properties[discriminator],
            {"const": deepcopy(value)},
            (*path, "properties", discriminator),
        )
        effect = clause_by_domain_index[domain_index]
        effect_properties, effect_required = _parse_static_effect(
            effect,
            (*path, "allOf", domain_index, "then"),
            properties,
        )
        for property_name, effect_schema in effect_properties.items():
            branch_properties[property_name] = _intersect_schema(
                branch_properties[property_name],
                effect_schema,
                (*path, "properties", property_name),
            )
        branch["properties"] = branch_properties
        branch["required"] = _ordered_union(required, effect_required)
        branches.append(branch)
    return branches


def _parse_discriminator_clause(
    raw_clause: Any,
    path: tuple[str | int, ...],
) -> list[tuple[str, Any, Mapping[str, Any]]]:
    if not isinstance(raw_clause, Mapping) or set(raw_clause) != {"if", "then"}:
        raise BoundedSchemaLoweringError("discriminator_clause_is_not_static_if_then", path)
    condition = raw_clause["if"]
    if not isinstance(condition, Mapping) or not set(condition).issubset({"properties", "required"}):
        raise BoundedSchemaLoweringError("discriminator_condition_is_not_static", (*path, "if"))
    condition_properties = condition.get("properties")
    if not isinstance(condition_properties, Mapping) or len(condition_properties) != 1:
        raise BoundedSchemaLoweringError("discriminator_condition_is_not_single_field", (*path, "if", "properties"))
    discriminator, condition_schema = next(iter(condition_properties.items()))
    if not isinstance(discriminator, str) or not isinstance(condition_schema, Mapping):
        raise BoundedSchemaLoweringError("discriminator_condition_is_not_const", (*path, "if", "properties"))
    condition_keys = set(condition_schema)
    if condition_keys not in ({"const"}, {"const", "type"}, {"enum"}, {"enum", "type"}):
        raise BoundedSchemaLoweringError("discriminator_condition_is_not_const", (*path, "if", "properties"))
    condition_type = condition_schema.get("type")
    if "const" in condition_schema:
        values: list[Any] = [condition_schema["const"]]
    else:
        raw_values = condition_schema.get("enum")
        if not isinstance(raw_values, list) or not raw_values:
            raise BoundedSchemaLoweringError(
                "discriminator_condition_enum_is_empty",
                (*path, "if", "properties", discriminator, "enum"),
            )
        if _has_json_duplicates(raw_values):
            raise BoundedSchemaLoweringError(
                "discriminator_condition_enum_is_not_unique",
                (*path, "if", "properties", discriminator, "enum"),
            )
        values = list(raw_values)
    if condition_type is not None and (
        not isinstance(condition_type, str)
        or any(not _matches_json_type(value, condition_type) for value in values)
    ):
        raise BoundedSchemaLoweringError(
            "discriminator_condition_const_type_mismatch",
            (*path, "if", "properties", discriminator),
        )
    condition_required = condition.get("required")
    if condition_required is not None and condition_required != [discriminator]:
        raise BoundedSchemaLoweringError("discriminator_condition_presence_is_ambiguous", (*path, "if", "required"))
    effect = raw_clause["then"]
    if not isinstance(effect, Mapping):
        raise BoundedSchemaLoweringError("discriminator_effect_is_not_static", (*path, "then"))
    return [
        (discriminator, deepcopy(value), effect)
        for value in values
    ]


def _parse_static_effect(
    effect: Mapping[str, Any],
    path: tuple[str | int, ...],
    base_properties: Mapping[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    if not set(effect).issubset({"properties", "required"}):
        raise BoundedSchemaLoweringError("discriminator_effect_is_not_static", path)
    effect_properties = effect.get("properties", {})
    if not isinstance(effect_properties, Mapping) or any(not isinstance(name, str) for name in effect_properties):
        raise BoundedSchemaLoweringError("discriminator_effect_properties_are_invalid", (*path, "properties"))
    if any(name not in base_properties for name in effect_properties):
        raise BoundedSchemaLoweringError("discriminator_effect_adds_unknown_property", (*path, "properties"))
    effect_required = _validated_required(effect.get("required", []), (*path, "required"))
    if any(name not in base_properties for name in effect_required):
        raise BoundedSchemaLoweringError("discriminator_effect_requires_unknown_property", (*path, "required"))
    return dict(effect_properties), effect_required


def _intersect_schema(base: Any, effect: Any, path: tuple[str | int, ...]) -> Any:
    if base is False or effect is False:
        return False
    if base is True:
        return deepcopy(effect)
    if effect is True:
        return deepcopy(base)
    if not isinstance(base, Mapping) or not isinstance(effect, Mapping):
        raise BoundedSchemaLoweringError("discriminator_effect_is_not_static", path)

    merged = deepcopy(dict(base))
    for key, effect_value in effect.items():
        if key not in merged:
            merged[key] = deepcopy(effect_value)
            continue
        base_value = merged[key]
        if _json_equal(base_value, effect_value):
            continue
        if key == "properties" and isinstance(base_value, Mapping) and isinstance(effect_value, Mapping):
            properties = deepcopy(dict(base_value))
            for property_name, property_effect in effect_value.items():
                properties[property_name] = (
                    _intersect_schema(
                        properties[property_name],
                        property_effect,
                        (*path, "properties", str(property_name)),
                    )
                    if property_name in properties
                    else deepcopy(property_effect)
                )
            merged[key] = properties
        elif key == "required":
            merged[key] = _ordered_union(
                _validated_required(base_value, (*path, "required")),
                _validated_required(effect_value, (*path, "required")),
            )
        elif key == "type":
            if _expanded_types(base_value) is None or _expanded_types(effect_value) is None:
                raise BoundedSchemaLoweringError(
                    "discriminator_effect_intersection_not_proven",
                    (*path, key),
                )
            intersection = _intersect_types(base_value, effect_value)
            if intersection is None:
                return False
            merged[key] = intersection
        elif key == "enum" and isinstance(base_value, list) and isinstance(effect_value, list):
            intersection = [value for value in base_value if _json_value_index(effect_value, value) is not None]
            if not intersection:
                return False
            merged[key] = intersection
        elif key == "const":
            return False
        elif key in {"minimum", "exclusiveMinimum", "minLength", "minItems", "minProperties"}:
            if not _both_finite_numbers(base_value, effect_value):
                raise BoundedSchemaLoweringError("discriminator_effect_intersection_not_proven", (*path, key))
            merged[key] = max(base_value, effect_value)
        elif key in {"maximum", "exclusiveMaximum", "maxLength", "maxItems", "maxProperties"}:
            if not _both_finite_numbers(base_value, effect_value):
                raise BoundedSchemaLoweringError("discriminator_effect_intersection_not_proven", (*path, key))
            merged[key] = min(base_value, effect_value)
        elif key == "additionalProperties" and isinstance(base_value, bool) and isinstance(effect_value, bool):
            merged[key] = base_value and effect_value
        else:
            raise BoundedSchemaLoweringError("discriminator_effect_intersection_not_proven", (*path, key))
    return merged


def _require_explicit_object_shape(
    node: Mapping[str, Any],
    path: tuple[str | int, ...],
    prefix: str,
) -> tuple[dict[str, Any], list[str]]:
    if node.get("type") != "object":
        raise BoundedSchemaLoweringError(f"{prefix}_requires_object_type", path)
    properties = node.get("properties")
    if not isinstance(properties, Mapping) or any(not isinstance(name, str) for name in properties):
        raise BoundedSchemaLoweringError(f"{prefix}_requires_explicit_properties", (*path, "properties"))
    if "required" not in node:
        raise BoundedSchemaLoweringError(f"{prefix}_requires_explicit_required", (*path, "required"))
    required = _validated_required(node["required"], (*path, "required"))
    if any(name not in properties for name in required):
        raise BoundedSchemaLoweringError(f"{prefix}_required_outside_properties", (*path, "required"))
    return dict(properties), required


def _require_closed_object(node: Mapping[str, Any], path: tuple[str | int, ...], prefix: str) -> None:
    if node.get("additionalProperties") is not False:
        raise BoundedSchemaLoweringError(f"{prefix}_requires_closed_object", (*path, "additionalProperties"))
    patterns = node.get("patternProperties", {})
    if not isinstance(patterns, Mapping) or patterns:
        raise BoundedSchemaLoweringError(f"{prefix}_has_open_dynamic_fields", (*path, "patternProperties"))


def _validated_required(value: Any, path: tuple[str | int, ...]) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(name, str) for name in value):
        raise BoundedSchemaLoweringError("invalid_required", path)
    if len(set(value)) != len(value):
        raise BoundedSchemaLoweringError("duplicate_required", path)
    return list(value)


def _ordered_union(left: Sequence[str], right: Sequence[str]) -> list[str]:
    result = list(left)
    result.extend(value for value in right if value not in result)
    return result


def _require_global_leaf_limit(count: int, path: tuple[str | int, ...]) -> None:
    if count > GLOBAL_SCHEMA_FINAL_LEAF_LIMIT:
        raise BoundedSchemaLoweringError("generated_branches_per_schema_limit", path)


def _has_conditional_all_of(node: Mapping[str, Any]) -> bool:
    clauses = node.get("allOf")
    return _is_schema_sequence(clauses) and any(
        isinstance(clause, Mapping) and bool(set(clause) & {"if", "then", "else"})
        for clause in clauses
    )


def _is_schema_sequence(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))


def _json_equal(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left == right
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        return set(left) == set(right) and all(
            _json_equal(left[key], right[key]) for key in left
        )
    if _is_schema_sequence(left) and _is_schema_sequence(right):
        return len(left) == len(right) and all(
            _json_equal(left_value, right_value)
            for left_value, right_value in zip(left, right, strict=True)
        )
    return type(left) is type(right) and left == right


def _json_value_index(values: Sequence[Any], target: Any) -> int | None:
    return next((index for index, value in enumerate(values) if _json_equal(value, target)), None)


def _has_json_duplicates(values: Sequence[Any]) -> bool:
    return any(_json_value_index(values[:index], value) is not None for index, value in enumerate(values))


def _matches_json_type(value: Any, expected: str) -> bool:
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    return False


def _intersect_types(left: Any, right: Any) -> str | list[str] | None:
    left_types = _expanded_types(left)
    right_types = _expanded_types(right)
    if left_types is None or right_types is None:
        return None
    common = left_types & right_types
    if not common:
        return None
    result: list[str] = []
    if {"integer", "non_integer_number"}.issubset(common):
        result.append("number")
        common -= {"integer", "non_integer_number"}
    order = ("null", "boolean", "object", "array", "string", "integer", "non_integer_number")
    result.extend("number" if item == "non_integer_number" else item for item in order if item in common)
    return result[0] if len(result) == 1 else result


def _expanded_types(value: Any) -> set[str] | None:
    raw = [value] if isinstance(value, str) else value
    if not isinstance(raw, list) or not raw or any(not isinstance(item, str) for item in raw):
        return None
    allowed = {"null", "boolean", "object", "array", "string", "integer", "number"}
    if any(item not in allowed for item in raw):
        return None
    expanded: set[str] = set()
    for item in raw:
        expanded.update({"integer", "non_integer_number"} if item == "number" else {item})
    return expanded


def _both_finite_numbers(left: Any, right: Any) -> bool:
    return (
        isinstance(left, (int, float))
        and not isinstance(left, bool)
        and math.isfinite(left)
        and isinstance(right, (int, float))
        and not isinstance(right, bool)
        and math.isfinite(right)
    )


def _escape_pointer_part(part: str | int) -> str:
    return str(part).replace("~", "~0").replace("/", "~1")


__all__ = [
    "BoundedSchemaLoweringError",
    "GENERATED_BRANCHES_PER_OBJECT",
    "GENERATED_BRANCHES_PER_SCHEMA",
    "GLOBAL_SCHEMA_FINAL_LEAF_LIMIT",
    "OPTIONAL_FIELDS_PER_OBJECT",
    "PER_OBJECT_FINAL_LEAF_LIMIT",
    "lower_bounded_schema",
    "lower_closed_optional_presence",
    "lower_exhaustive_finite_discriminator",
]
