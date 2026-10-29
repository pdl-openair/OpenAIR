"""Opaque readable material is context, never a successfully extracted field."""

import json
from collections.abc import Mapping
from typing import Any

from app.modules.binding_source_path import (
    BindingSourcePathError,
    binding_source_path_steps,
    resolve_binding_source_value,
)


class UnparsedScientificSource(dict[str, Any]):
    """Transient marker; the frozen JSON input preserves its explicit status."""

    def for_field(self, source_path: str) -> dict[str, Any]:
        return {**self, "requestedField": source_path}


def parse_scientific_source(source: Mapping[str, Any], identity: Any) -> dict[str, Any]:
    from app.execution.research_input_contract_gate import _failure

    content = source.get("content")
    mime = source.get("mimeType")
    if mime not in {"application/json", "text/plain", "text/markdown"} or not isinstance(
        content, bytes
    ):
        raise _failure(identity, "input_composition_source_artifact_invalid")
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _failure(identity, "input_composition_source_artifact_invalid") from exc
    if mime == "application/json":
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            return parsed
    material = {"mimeType": mime, "content": text}
    for key in ("artifactId", "contentSha256"):
        if key in source:
            material[key] = source[key]
    return UnparsedScientificSource(
        sourceStatus="unparsed",
        requestedField="$",
        sourceMaterials=[material],
    )


def resolve_scientific_source_value(document: Mapping[str, Any], source_path: str) -> Any:
    """Use the existing path grammar; opaque nodes never expose fake fields."""
    current: Any = document
    for step in binding_source_path_steps(source_path, document.keys()):
        if isinstance(current, UnparsedScientificSource):
            return current.for_field(source_path)
        if not isinstance(current, Mapping):
            raise BindingSourcePathError("binding_source_path_non_object", source_path, step)
        try:
            current = resolve_binding_source_value(current, step)
        except BindingSourcePathError as exc:
            raise BindingSourcePathError(exc.code, source_path, exc.segment) from exc
    return (
        current.for_field(source_path) if isinstance(current, UnparsedScientificSource) else current
    )
