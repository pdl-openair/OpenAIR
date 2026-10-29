"""Deterministic scalar field projection from Artifact JSON content.

This is intentionally domain-agnostic: it never writes ScientificState, never
registers a global runtime fact, and only preserves top-level scalar fields so
conditional binding evaluators can read Producer markers without loading the
durable Artifact body again.
"""

from __future__ import annotations

import json
from typing import Any, Mapping


ARTIFACT_FIELD_PROJECTION_KEY = "artifactFieldProjection"


def project_scalar_fields_from_content(content: Any) -> dict[str, Any] | None:
    """Project top-level scalar fields from JSON object Artifact content."""

    document = _parse_json_object(content)
    if document is None:
        return None
    return _normalize_projection(document)


def merge_artifact_field_projection(
    validator_result: Mapping[str, Any],
    content: Any,
) -> dict[str, Any]:
    """Return a copy of validatorResult with scalar Artifact fields attached."""

    merged = dict(validator_result)
    projected = project_scalar_fields_from_content(content)
    if projected is None:
        merged.pop(ARTIFACT_FIELD_PROJECTION_KEY, None)
        return merged
    merged[ARTIFACT_FIELD_PROJECTION_KEY] = projected
    return merged


def artifact_field_projection_from_validator(
    validator_result: Any,
) -> dict[str, Any] | None:
    if not isinstance(validator_result, Mapping):
        return None
    projected = validator_result.get(ARTIFACT_FIELD_PROJECTION_KEY)
    if not isinstance(projected, Mapping):
        return None
    return _normalize_projection(projected)


def _normalize_projection(projected: Mapping[str, Any]) -> dict[str, Any] | None:
    normalized: dict[str, Any] = {}
    for key, value in projected.items():
        if not isinstance(key, str) or not key:
            continue
        if isinstance(value, bool) or value is None or isinstance(value, str):
            normalized[key] = value
            continue
        if isinstance(value, list):
            normalized[key] = len(value)
            continue
        if isinstance(value, int) and not isinstance(value, bool):
            normalized[key] = value
            continue
        if (
            isinstance(value, float)
            and value == value
            and value not in {float("inf"), float("-inf")}
        ):
            normalized[key] = value
    return normalized or None


def _parse_json_object(content: Any) -> dict[str, Any] | None:
    if isinstance(content, Mapping):
        return dict(content)
    text: str | None = None
    if isinstance(content, bytes):
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            return None
    elif isinstance(content, str):
        text = content
    if text is None:
        return None
    stripped = text.strip()
    if not stripped:
        return None
    try:
        decoded = json.loads(stripped)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return dict(decoded) if isinstance(decoded, Mapping) else None


__all__ = [
    "ARTIFACT_FIELD_PROJECTION_KEY",
    "artifact_field_projection_from_validator",
    "merge_artifact_field_projection",
    "project_scalar_fields_from_content",
]
