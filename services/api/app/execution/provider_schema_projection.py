"""Shared JSON-Schema projection for model-visible prompt content.

Formal and validation schemas remain unchanged. This module only removes
schema-document metadata that does not affect instance validation when a schema
is embedded in model-visible prompt text. Callers remain responsible for ensuring
that removed descriptions are not the sole source of scientific instructions.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


_SCHEMA_PROMPT_META_KEYS = frozenset({
    "$schema",
    "$id",
    "title",
    "description",
    "contractId",
    "semanticVersion",
})

# R0-A: keys inside these mappings are schema/data names, not schema metadata.
_SCHEMA_NAME_MAP_KEYS = frozenset({
    "$defs",
    "definitions",
    "dependencies",
    "dependentRequired",
    "dependentSchemas",
    "mapping",
    "patternProperties",
    "properties",
})

# These keyword values are instance data, not nested schema objects.
_SCHEMA_LITERAL_VALUE_KEYS = frozenset({
    "const",
    "default",
    "enum",
    "examples",
})


def schema_for_provider_prompt(
    value: Any,
    *,
    _preserve_schema_names: bool = False,
    _literal_value: bool = False,
) -> Any:
    """Remove schema metadata without deleting business names or literal data.

    JSON Schema mappings such as ``properties`` and ``$defs`` contain
    user-defined names, while keywords such as ``const`` and ``enum`` contain
    instance data.  Metadata filtering therefore applies only to schema-object
    keywords, never to those names or values.
    """
    if isinstance(value, dict):
        projected = {}
        for key, item in value.items():
            if (
                not _preserve_schema_names
                and not _literal_value
                and key in _SCHEMA_PROMPT_META_KEYS
            ):
                continue
            projected[key] = schema_for_provider_prompt(
                item,
                _preserve_schema_names=(
                    not _literal_value
                    and not _preserve_schema_names
                    and key in _SCHEMA_NAME_MAP_KEYS
                ),
                _literal_value=(
                    _literal_value
                    or (
                        not _preserve_schema_names
                        and key in _SCHEMA_LITERAL_VALUE_KEYS
                    )
                ),
            )
        return projected
    if isinstance(value, list):
        return [
            schema_for_provider_prompt(item, _literal_value=_literal_value)
            for item in value
        ]
    return value



__all__ = ["schema_for_provider_prompt"]
