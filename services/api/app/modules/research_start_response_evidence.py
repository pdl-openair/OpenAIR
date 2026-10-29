"""Scoped ResearchStart response evidence in its authoritative attempt record."""
from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any


def describe_response(
    raw: bytes, *, scope: Mapping[str, str], metadata: Mapping[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    """Describe raw bytes stored beside this metadata on the same attempt row."""
    if not isinstance(raw, bytes):
        raise TypeError("response evidence requires bytes")
    if set(scope) != {"ownerId", "workspaceId", "requestId", "attemptId"} or not all(scope.values()):
        raise ValueError("response evidence scope is incomplete")
    instant = now or datetime.now(timezone.utc)
    return {
        "schemaVersion": "research-start-response-evidence.v2",
        "scope": dict(scope),
        "metadata": {
            **dict(metadata),
            "responseSha256": hashlib.sha256(raw).hexdigest(),
            "responseBytes": len(raw),
            "truncated": False,
            "observedAt": instant.isoformat(),
        },
    }


def merge_attempt_result(previous: Any, result: Mapping[str, Any] | None) -> dict[str, Any]:
    """Terminalization cannot erase or replace a response already durably captured."""
    merged = dict(result or {})
    if isinstance(previous, Mapping) and isinstance(previous.get("responseEvidence"), Mapping):
        merged["responseEvidence"] = dict(previous["responseEvidence"])
    return merged
