"""Validate the compact artifact envelope emitted by durable Skill adapters."""

from __future__ import annotations

import re
from typing import Any

from app.models.result_protocol import (
    artifact_quality_for_protocol,
    normalize_result_protocol,
    result_protocol_usable,
)

_JSON_RUNTIMES = frozenset(
    {"python_adapter", "reasoning_adapter", "deterministic", "external_adapter"}
)
_REASONING_RUNTIMES = frozenset({"huiyan_durable_skill", "llm_reasoning"})
_BASE_METADATA_KEYS = frozenset({"runtime", "skillId"})
_QUALITY_METADATA_KEYS = frozenset({"resultProtocol", "artifactQuality", "warnings"})


def _validated_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    extra_keys = metadata.keys() - _BASE_METADATA_KEYS
    if not extra_keys:
        return dict(metadata)
    if (
        extra_keys - _QUALITY_METADATA_KEYS
        or not {"resultProtocol", "artifactQuality"} <= extra_keys
        or not isinstance(metadata["resultProtocol"], dict)
    ):
        raise ValueError("invalid Skill result metadata")
    protocol = normalize_result_protocol(metadata["resultProtocol"])
    if (
        protocol != metadata["resultProtocol"]
        or not result_protocol_usable(protocol)
        or metadata["artifactQuality"] != artifact_quality_for_protocol(protocol)
        or ("warnings" in metadata and metadata["warnings"] != protocol["warnings"][:24])
    ):
        raise ValueError("inconsistent Skill result quality")
    return {**metadata, "resultProtocol": protocol}


def validate_skill_presentation(value: Any) -> dict[str, Any]:
    """Preserve valid producer quality facts; reject rather than repair input.

    Content checksum matching and invocation ownership remain the repository's
    responsibility. Adapters without quality metadata retain their envelope.
    """
    if not isinstance(value, dict):
        raise ValueError("Skill presentation must be an object")
    title = str(value.get("title") or "").strip()
    mime_type = str(value.get("mimeType") or "").strip().lower()
    metadata = value.get("metadata")
    digest = str(value.get("contentSha256") or "").strip().lower()
    size = value.get("contentSize")
    if (
        not title
        or len(title) > 240
        or any(ord(char) < 32 for char in title)
        or not isinstance(metadata, dict)
    ):
        raise ValueError("invalid Skill presentation envelope")
    runtime = metadata.get("runtime")
    if not isinstance(runtime, str) or runtime not in _JSON_RUNTIMES | _REASONING_RUNTIMES:
        raise ValueError("unsupported Skill runtime")
    expected_mime_types = (
        {"application/json"}
        if runtime in _JSON_RUNTIMES
        else {"text/plain", "text/markdown", "text/yaml"}
    )
    if (
        mime_type not in expected_mime_types
        or not isinstance(metadata.get("skillId"), str)
        or not metadata["skillId"].strip()
        or isinstance(size, bool)
        or not isinstance(size, int)
        or size < 1
        or not re.fullmatch(r"[0-9a-f]{64}", digest)
    ):
        raise ValueError("invalid Skill presentation fields")
    return {
        "title": title,
        "mimeType": mime_type,
        "metadata": _validated_metadata(metadata),
        "contentSha256": digest,
        "contentSize": size,
    }
