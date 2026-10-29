"""Shared Provider Schema transport for structured LLM output.

Scientific callers own the minimal Provider Schema for the judgment they ask the
model to make. This module owns provider-specific structure transport only. It
never changes scientific meaning and never owns Formal Schema validation.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
import re
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from app.execution.provider_schema_bounded_lowering import (
    BoundedSchemaLoweringError,
    lower_bounded_schema,
)
from app.execution.provider_schema_projection import schema_for_provider_prompt
from app.execution.provider_schema_lossless_lowering import (
    NOT_LOWERABLE,
    lower_provider_schema_losslessly,
)


PROVIDER_SCHEMA_UNSUPPORTED = "PROVIDER_SCHEMA_UNSUPPORTED"


class ProviderSchemaCapabilityError(ValueError):
    """The selected provider cannot natively enforce the requested Schema."""

    code = PROVIDER_SCHEMA_UNSUPPORTED
    safe_error_code = PROVIDER_SCHEMA_UNSUPPORTED


_OPENAI_RESPONSES_STRICT_CAPABILITIES = {
    "$defs": "supported",
    "$ref": "supported",
    "additionalProperties": "supported",
    "anyOf": "supported",
    "const": "supported",
    "definitions": "supported",
    "enum": "supported",
    "items": "supported",
    "maxItems": "supported",
    "maxLength": "supported",
    "maximum": "supported",
    "minItems": "supported",
    "minLength": "supported",
    "minimum": "supported",
    "pattern": "unsupported",
    "required": "supported",
    "type": "supported",
    "allOf": "unsupported",
    "contains": "unsupported",
    "if": "unsupported",
    "then": "unsupported",
    "else": "unsupported",
    "not": "unsupported",
    "oneOf": "unsupported",
    "uniqueItems": "unsupported",
}
_LOCAL_REFERENCE = re.compile(r"^#/(\$defs|definitions)/([^/]+)$")
_SCHEMA_MAP_KEYWORDS = frozenset({
    "$defs",
    "definitions",
    "dependentSchemas",
    "patternProperties",
    "properties",
})
_SCHEMA_ARRAY_KEYWORDS = frozenset({"allOf", "anyOf", "oneOf", "prefixItems"})
_SCHEMA_VALUE_KEYWORDS = frozenset({
    "additionalItems",
    "additionalProperties",
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
_JSON_TYPES = frozenset({
    "array",
    "boolean",
    "integer",
    "null",
    "number",
    "object",
    "string",
})
_UNSUPPORTED_RESOURCE_SCOPE_KEYWORDS = frozenset({
    "$anchor",
    "$dynamicAnchor",
    "$dynamicRef",
    "$recursiveAnchor",
    "$recursiveRef",
})


def structured_response_format(
    provider: str,
    *,
    schema_name: str,
    enabled: bool = True,
) -> dict[str, Any] | None:
    """Return the provider-native response envelope without changing Schema meaning."""
    if not enabled:
        return None
    normalized = str(provider or "").strip().lower()
    if normalized == "huiyan_chat_completions":
        return {"type": "json_object"}
    if normalized == "openai_responses":
        return {"type": "json_schema", "name": schema_name}
    raise ValueError(f"unsupported structured-output provider: {provider}")


def _project_provider_schema(provider_schema: Mapping[str, Any]) -> dict[str, Any]:
    projected = schema_for_provider_prompt(provider_schema)
    if not isinstance(projected, Mapping):
        raise ValueError("Provider Schema must be a JSON object")
    return deepcopy(dict(projected))


def _const_matches_declared_type(value: Any, declared_type: str) -> bool:
    """Return whether a JSON const has the declared JSON Schema type."""
    if declared_type == "null":
        return value is None
    if declared_type == "boolean":
        return isinstance(value, bool)
    if declared_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if declared_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if declared_type == "string":
        return isinstance(value, str)
    if declared_type == "array":
        return isinstance(value, list)
    if declared_type == "object":
        return isinstance(value, Mapping)
    return False


def _decode_json_pointer_token(token: str) -> str | None:
    decoded: list[str] = []
    index = 0
    while index < len(token):
        character = token[index]
        if character != "~":
            decoded.append(character)
            index += 1
            continue
        if index + 1 >= len(token) or token[index + 1] not in {"0", "1"}:
            return None
        decoded.append("~" if token[index + 1] == "0" else "/")
        index += 2
    return "".join(decoded)


def _assert_projection_preserves_resource_scope(
    provider_schema: Mapping[str, Any],
) -> None:
    """Reject resource scopes that the shared metadata projection cannot preserve."""
    if not isinstance(provider_schema, Mapping):
        raise ValueError("Provider Schema must be a JSON object")

    def visit_schema(value: Any, *, at_root: bool, path: str) -> None:
        if isinstance(value, bool):
            return
        if not isinstance(value, Mapping):
            return
        if "$id" in value and not at_root:
            raise ProviderSchemaCapabilityError(
                f"{PROVIDER_SCHEMA_UNSUPPORTED}: nested $id changes resource scope at {path}"
            )
        for keyword in _UNSUPPORTED_RESOURCE_SCOPE_KEYWORDS:
            if keyword in value:
                raise ProviderSchemaCapabilityError(
                    f"{PROVIDER_SCHEMA_UNSUPPORTED}: {keyword} is unsupported at {path}"
                )

        for keyword, child in value.items():
            if keyword in _SCHEMA_MAP_KEYWORDS and isinstance(child, Mapping):
                for name, subschema in child.items():
                    visit_schema(
                        subschema,
                        at_root=False,
                        path=f"{path}/{keyword}/{name}",
                    )
            elif keyword == "dependencies" and isinstance(child, Mapping):
                for name, subschema in child.items():
                    if isinstance(subschema, (Mapping, bool)):
                        visit_schema(
                            subschema,
                            at_root=False,
                            path=f"{path}/{keyword}/{name}",
                        )
            elif keyword in _SCHEMA_ARRAY_KEYWORDS and _is_schema_sequence(child):
                for index, subschema in enumerate(child):
                    visit_schema(
                        subschema,
                        at_root=False,
                        path=f"{path}/{keyword}/{index}",
                    )
            elif keyword in _SCHEMA_VALUE_KEYWORDS:
                if keyword == "items" and _is_schema_sequence(child):
                    for index, subschema in enumerate(child):
                        visit_schema(
                            subschema,
                            at_root=False,
                            path=f"{path}/{keyword}/{index}",
                        )
                elif isinstance(child, (Mapping, bool)):
                    visit_schema(
                        child,
                        at_root=False,
                        path=f"{path}/{keyword}",
                    )

    visit_schema(provider_schema, at_root=True, path="#")
    _assert_local_reference_closure(provider_schema)


def _assert_local_reference_closure(provider_schema: Mapping[str, Any]) -> None:
    """Accept only closed, acyclic references to root definition resources."""
    targets: dict[str, Any] = {}
    for keyword in ("$defs", "definitions"):
        definitions = provider_schema.get(keyword)
        if definitions is None:
            continue
        if not isinstance(definitions, Mapping):
            raise ProviderSchemaCapabilityError(
                f"{PROVIDER_SCHEMA_UNSUPPORTED}: root {keyword} must be an object"
            )
        for name, definition in definitions.items():
            if not isinstance(name, str):
                raise ProviderSchemaCapabilityError(
                    f"{PROVIDER_SCHEMA_UNSUPPORTED}: {keyword} names must be strings"
                )
            escaped = name.replace("~", "~0").replace("/", "~1")
            targets[f"#/{keyword}/{escaped}"] = definition

    edges: dict[str, set[str]] = {pointer: set() for pointer in targets}

    def visit_schema(value: Any, *, owner: str | None) -> None:
        if isinstance(value, bool):
            return
        if not isinstance(value, Mapping):
            return
        if "$ref" in value:
            reference = value["$ref"]
            if not isinstance(reference, str):
                raise ProviderSchemaCapabilityError(
                    f"{PROVIDER_SCHEMA_UNSUPPORTED}: $ref must be a string"
                )
            match = _LOCAL_REFERENCE.fullmatch(reference)
            decoded = _decode_json_pointer_token(match.group(2)) if match else None
            if match is None or decoded is None:
                raise ProviderSchemaCapabilityError(
                    f"{PROVIDER_SCHEMA_UNSUPPORTED}: unsupported reference {reference!r}"
                )
            canonical_token = decoded.replace("~", "~0").replace("/", "~1")
            target = f"#/{match.group(1)}/{canonical_token}"
            if target not in targets:
                raise ProviderSchemaCapabilityError(
                    f"{PROVIDER_SCHEMA_UNSUPPORTED}: dangling reference {reference!r}"
                )
            if owner is not None:
                edges[owner].add(target)

        for keyword, child in value.items():
            if keyword in _SCHEMA_MAP_KEYWORDS and isinstance(child, Mapping):
                for name, subschema in child.items():
                    child_owner = owner
                    if keyword in {"$defs", "definitions"} and owner is None:
                        escaped = str(name).replace("~", "~0").replace("/", "~1")
                        child_owner = f"#/{keyword}/{escaped}"
                    visit_schema(subschema, owner=child_owner)
            elif keyword == "dependencies" and isinstance(child, Mapping):
                for subschema in child.values():
                    if isinstance(subschema, (Mapping, bool)):
                        visit_schema(subschema, owner=owner)
            elif keyword in _SCHEMA_ARRAY_KEYWORDS and _is_schema_sequence(child):
                for subschema in child:
                    visit_schema(subschema, owner=owner)
            elif keyword in _SCHEMA_VALUE_KEYWORDS:
                if keyword == "items" and _is_schema_sequence(child):
                    for subschema in child:
                        visit_schema(subschema, owner=owner)
                elif isinstance(child, (Mapping, bool)):
                    visit_schema(child, owner=owner)

    visit_schema(provider_schema, owner=None)

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit_target(pointer: str) -> None:
        if pointer in visiting:
            raise ProviderSchemaCapabilityError(
                f"{PROVIDER_SCHEMA_UNSUPPORTED}: recursive reference graph at {pointer}"
            )
        if pointer in visited:
            return
        visiting.add(pointer)
        for dependency in edges[pointer]:
            visit_target(dependency)
        visiting.remove(pointer)
        visited.add(pointer)

    for pointer in edges:
        visit_target(pointer)


def _is_schema_sequence(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))


def _assert_draft_2020_12_schema(provider_schema: Mapping[str, Any]) -> None:
    try:
        Draft202012Validator.check_schema(dict(provider_schema))
    except SchemaError as exc:
        raise ProviderSchemaCapabilityError(
            f"{PROVIDER_SCHEMA_UNSUPPORTED}: invalid Draft 2020-12 Provider Schema"
        ) from exc


def _schema_has_object_semantics(schema: Mapping[str, Any]) -> bool:
    declared_type = schema.get("type")
    if declared_type == "object":
        return True
    if isinstance(declared_type, list) and "object" in declared_type:
        return True
    return any(
        keyword in schema
        for keyword in ("properties", "required", "additionalProperties")
    )


def _declared_types_include_object(schema: Mapping[str, Any]) -> bool:
    declared_type = schema.get("type")
    if declared_type == "object":
        return True
    return isinstance(declared_type, list) and "object" in declared_type


def _ensure_object_properties(provider_schema: Mapping[str, Any]) -> dict[str, Any]:
    """Huiyan/OpenAI reject object nodes without an explicit properties map.

    Walk the executable schema and give every object-typed (or object-semantic)
    node a properties object. Empty ``{}`` is valid and prevents upstream 400
    ``properties is required for object schemas`` after lowering/admission.
    This is a transport integrity repair, not a scientific-schema weaken.
    """

    def visit(value: Any) -> Any:
        if isinstance(value, bool):
            return value
        if isinstance(value, list):
            return [visit(item) for item in value]
        if not isinstance(value, Mapping):
            return value
        node: dict[str, Any] = {}
        for key, child in value.items():
            keyword = str(key)
            if keyword in {"properties", "$defs", "definitions", "dependentSchemas"} and isinstance(
                child, Mapping
            ):
                node[keyword] = {
                    str(name): visit(sub) for name, sub in child.items()
                }
            elif keyword in {"anyOf", "oneOf", "allOf", "prefixItems"} and isinstance(
                child, list
            ):
                node[keyword] = [visit(item) for item in child]
            elif keyword in {"items", "not", "if", "then", "else", "contains"}:
                node[keyword] = visit(child)
            else:
                node[keyword] = visit(child) if isinstance(child, (Mapping, list)) else child
        if _declared_types_include_object(node) or _schema_has_object_semantics(node):
            if not isinstance(node.get("properties"), Mapping):
                node["properties"] = {}
            if "type" not in node and _schema_has_object_semantics(node):
                node["type"] = "object"
        return node

    ensured = visit(deepcopy(dict(provider_schema)))
    if not isinstance(ensured, Mapping):
        raise ProviderSchemaCapabilityError(
            f"{PROVIDER_SCHEMA_UNSUPPORTED}: Provider Schema must be a JSON object"
        )
    return dict(ensured)


def _assert_object_integrity(provider_schema: Mapping[str, Any]) -> None:
    """Recursively reject object constraints that refer to undeclared fields."""

    def visit_schema(value: Any, *, path: str) -> None:
        if isinstance(value, bool):
            return
        if not isinstance(value, Mapping):
            return

        if _schema_has_object_semantics(value) and "required" in value:
            required = value.get("required")
            properties = value.get("properties")
            if not isinstance(required, list) or any(
                not isinstance(name, str) for name in required
            ):
                raise ProviderSchemaCapabilityError(
                    f"{PROVIDER_SCHEMA_UNSUPPORTED}: invalid required at {path}"
                )
            if required and not isinstance(properties, Mapping):
                raise ProviderSchemaCapabilityError(
                    f"{PROVIDER_SCHEMA_UNSUPPORTED}: required has no properties at {path}"
                )
            missing = [
                name
                for name in required
                if not isinstance(properties, Mapping) or name not in properties
            ]
            if missing:
                raise ProviderSchemaCapabilityError(
                    f"{PROVIDER_SCHEMA_UNSUPPORTED}: required fields are absent from "
                    f"properties at {path}: {missing}"
                )

        for keyword, child in value.items():
            if keyword in _SCHEMA_MAP_KEYWORDS and isinstance(child, Mapping):
                for name, subschema in child.items():
                    visit_schema(subschema, path=f"{path}/{keyword}/{name}")
            elif keyword == "dependencies" and isinstance(child, Mapping):
                for name, subschema in child.items():
                    if isinstance(subschema, (Mapping, bool)):
                        visit_schema(subschema, path=f"{path}/{keyword}/{name}")
            elif keyword in _SCHEMA_ARRAY_KEYWORDS and _is_schema_sequence(child):
                for index, subschema in enumerate(child):
                    visit_schema(subschema, path=f"{path}/{keyword}/{index}")
            elif keyword in _SCHEMA_VALUE_KEYWORDS:
                if keyword == "items" and _is_schema_sequence(child):
                    for index, subschema in enumerate(child):
                        visit_schema(subschema, path=f"{path}/{keyword}/{index}")
                elif isinstance(child, (Mapping, bool)):
                    visit_schema(child, path=f"{path}/{keyword}")

    visit_schema(provider_schema, path="#")


def _assert_native_strict_compatible(
    provider_schema: Mapping[str, Any],
    *,
    provider_label: str,
    capabilities: Mapping[str, str],
    require_closed_objects: bool,
    path: str = "$",
) -> None:
    """Allow only assertions proven for the selected Provider strict subset."""
    if require_closed_objects and _schema_has_object_semantics(provider_schema):
        declared_type = provider_schema.get("type")
        declared_types = (
            [declared_type]
            if isinstance(declared_type, str)
            else declared_type
        )
        properties = provider_schema.get("properties")
        required = provider_schema.get("required")
        if (
            not isinstance(declared_types, list)
            or "object" not in declared_types
            or not isinstance(properties, Mapping)
            or not isinstance(required, list)
            or set(required) != set(properties)
            or provider_schema.get("additionalProperties") is not False
        ):
            raise ProviderSchemaCapabilityError(
                f"{provider_label} requires every object at {path} to declare "
                "type=object, properties, the same fields in required, and "
                "additionalProperties=false"
            )
    for raw_keyword, value in provider_schema.items():
        keyword = str(raw_keyword)
        keyword_path = f"{path}.{keyword}"
        if keyword in {"properties", "$defs", "definitions"}:
            if keyword in {"$defs", "definitions"} and path != "$":
                raise ProviderSchemaCapabilityError(
                    f"{provider_label} supports {keyword} only at the document root"
                )
            if not isinstance(value, Mapping):
                raise ProviderSchemaCapabilityError(
                    f"{provider_label} requires an object at {keyword_path}"
                )
            for property_name, property_schema in value.items():
                property_path = f"{keyword_path}.{property_name}"
                if not isinstance(property_schema, Mapping):
                    raise ProviderSchemaCapabilityError(
                        f"{provider_label} requires an object at {property_path}"
                    )
                _assert_native_strict_compatible(
                    property_schema,
                    provider_label=provider_label,
                    capabilities=capabilities,
                    require_closed_objects=require_closed_objects,
                    path=property_path,
                )
            continue
        if keyword == "items":
            if not isinstance(value, Mapping):
                raise ProviderSchemaCapabilityError(
                    f"{provider_label} requires object-form items at {keyword_path}"
                )
            _assert_native_strict_compatible(
                value,
                provider_label=provider_label,
                capabilities=capabilities,
                require_closed_objects=require_closed_objects,
                path=keyword_path,
            )
            continue
        capability = capabilities.get(keyword, "not_proven")
        if capability == "unsupported":
            raise ProviderSchemaCapabilityError(
                f"{provider_label} does not support {keyword_path}"
            )
        if capability != "supported":
            raise ProviderSchemaCapabilityError(
                f"{provider_label} capability is not proven for {keyword_path}"
            )
        if keyword == "additionalProperties" and value is not False:
            raise ProviderSchemaCapabilityError(
                f"{provider_label} capability is proven only for "
                f"additionalProperties=false at {keyword_path}"
            )
        if keyword == "anyOf":
            if not isinstance(value, list) or not value:
                raise ProviderSchemaCapabilityError(
                    f"{provider_label} requires a non-empty array at {keyword_path}"
                )
            for branch_index, branch_schema in enumerate(value):
                branch_path = f"{keyword_path}[{branch_index}]"
                if not isinstance(branch_schema, Mapping):
                    raise ProviderSchemaCapabilityError(
                        f"{provider_label} requires an object at {branch_path}"
                    )
                _assert_native_strict_compatible(
                    branch_schema,
                    provider_label=provider_label,
                    capabilities=capabilities,
                    require_closed_objects=require_closed_objects,
                    path=branch_path,
                )
        if keyword == "const":
            declared_type = provider_schema.get("type")
            if not isinstance(declared_type, str):
                raise ProviderSchemaCapabilityError(
                    f"{provider_label} capability is proven only for typed const at "
                    f"{keyword_path}"
                )
            if not _const_matches_declared_type(value, declared_type):
                raise ProviderSchemaCapabilityError(
                    f"{provider_label} const JSON type does not match "
                    f"type={declared_type} at {keyword_path}"
                )
        if keyword == "pattern":
            if provider_schema.get("type") != "string" or not isinstance(value, str):
                raise ProviderSchemaCapabilityError(
                    f"{provider_label} capability is proven only for string pattern at "
                    f"{keyword_path}"
                )
        if keyword == "type":
            declared_types = [value] if isinstance(value, str) else value
            if (
                not isinstance(declared_types, list)
                or not declared_types
                or any(item not in _JSON_TYPES for item in declared_types)
                or len(set(declared_types)) != len(declared_types)
            ):
                raise ProviderSchemaCapabilityError(
                    f"{provider_label} requires a legal JSON type union at {keyword_path}"
                )


def _assert_openai_responses_strict_compatible(
    provider_schema: Mapping[str, Any],
) -> None:
    _assert_native_strict_compatible(
        provider_schema,
        provider_label="OpenAI Responses strict",
        capabilities=_OPENAI_RESPONSES_STRICT_CAPABILITIES,
        require_closed_objects=True,
    )


def _apply_huiyan_schema_transport(
    payload: dict[str, Any],
    *,
    response_format: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if not isinstance(response_format, Mapping) or response_format.get("type") != "json_object":
        raise ValueError("Huiyan structured output requires json_object response mode")
    payload["response_format"] = {"type": "json_object"}
    return payload


def _apply_responses_schema_transport(
    payload: dict[str, Any],
    *,
    provider_schema: Mapping[str, Any],
    response_format: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if not isinstance(response_format, Mapping) or response_format.get("type") != "json_schema":
        raise ValueError("OpenAI Responses structured output requires json_schema response mode")
    schema_name = str(response_format.get("name") or "").strip()
    if not schema_name:
        raise ValueError("OpenAI Responses structured output requires schema name")
    text = payload.get("text")
    if text is None:
        text = {}
        payload["text"] = text
    if not isinstance(text, dict):
        raise ValueError("OpenAI Responses text envelope must be an object")
    text["format"] = {
        "type": "json_schema",
        "name": schema_name,
        "strict": True,
        "schema": deepcopy(dict(provider_schema)),
    }
    return payload


def _prepare_strict_schema(
    provider: str,
    provider_schema: Mapping[str, Any],
) -> dict[str, Any]:
    lowered = lower_provider_schema_losslessly(provider_schema)
    if lowered is NOT_LOWERABLE:
        raise ProviderSchemaCapabilityError(
            f"{PROVIDER_SCHEMA_UNSUPPORTED}: Provider Schema contains a construct "
            "that has no approved semantics-preserving lowering"
        )
    try:
        bounded = lower_bounded_schema(lowered)
    except BoundedSchemaLoweringError as exc:
        raise ProviderSchemaCapabilityError(
            f"{PROVIDER_SCHEMA_UNSUPPORTED}: {exc}"
        ) from exc
    # OpenAI strict object shapes require an explicit properties map.
    executable = _ensure_object_properties(bounded)
    _assert_final_executable_schema(provider, executable)
    return executable


def _assert_final_executable_schema(
    provider: str,
    provider_schema: Mapping[str, Any],
) -> None:
    """Admit the exact schema that will be serialized in the Provider payload."""
    _assert_draft_2020_12_schema(provider_schema)
    _assert_object_integrity(provider_schema)
    _assert_local_reference_closure(provider_schema)
    if provider_schema.get("type") != "object":
        raise ProviderSchemaCapabilityError(
            f"{PROVIDER_SCHEMA_UNSUPPORTED}: strict structured output requires "
            "root type=object"
        )
    if provider == "openai_responses":
        _assert_openai_responses_strict_compatible(provider_schema)
    else:  # pragma: no cover - guarded by the public dispatcher
        raise ValueError(f"unsupported structured-output provider: {provider}")


def _schema_from_final_payload(
    provider: str,
    payload: Mapping[str, Any],
) -> Mapping[str, Any]:
    if provider == "openai_responses":
        text = payload.get("text")
        format_value = text.get("format") if isinstance(text, Mapping) else None
        schema = format_value.get("schema") if isinstance(format_value, Mapping) else None
    else:
        raise ValueError(f"unsupported structured-output provider: {provider}")
    if not isinstance(schema, Mapping):
        raise ProviderSchemaCapabilityError(
            f"{PROVIDER_SCHEMA_UNSUPPORTED}: final Provider payload has no executable schema"
        )
    return schema


def apply_provider_schema_transport(
    provider: str,
    *,
    payload: Mapping[str, Any],
    response_format: Mapping[str, Any] | None,
    provider_schema: Mapping[str, Any],
) -> dict[str, Any]:
    """Apply Provider Schema through the shared provider-specific transport layer.

    Huiyan's stable contract is JSON object mode; its request never carries the
    Formal Schema. OpenAI Responses keeps its native strict-schema transport.
    """
    normalized_provider = str(provider or "").strip().lower()
    if normalized_provider not in {"huiyan_chat_completions", "openai_responses"}:
        raise ValueError(f"unsupported structured-output provider: {provider}")
    request_payload = deepcopy(dict(payload))
    if normalized_provider == "huiyan_chat_completions":
        return _apply_huiyan_schema_transport(
            request_payload,
            response_format=response_format,
        )
    _assert_projection_preserves_resource_scope(provider_schema)
    projected = _project_provider_schema(provider_schema)
    final_schema = _prepare_strict_schema(normalized_provider, projected)
    admitted = _apply_responses_schema_transport(
        request_payload,
        provider_schema=final_schema,
        response_format=response_format,
    )
    _assert_final_executable_schema(
        normalized_provider,
        _schema_from_final_payload(normalized_provider, admitted),
    )
    return admitted


__all__ = [
    "apply_provider_schema_transport",
    "PROVIDER_SCHEMA_UNSUPPORTED",
    "ProviderSchemaCapabilityError",
    "structured_response_format",
]
