"""Shared deterministic helpers for execution-plan assembly."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

SCHEMA_VERSION = "openair-execution-plan/v1"
RESOURCE_PLACEHOLDER = {
    "model": "{{openair-resource:model}}",
    "dataset": "{{openair-resource:dataset}}",
}


def unresolved(target: str, reason: str) -> dict[str, str]:
    return {"target": target, "reason": reason}


def dedupe_unresolved(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[tuple[str, str]] = set()
    result: list[dict[str, str]] = []
    for row in rows:
        key = (row.get("target", ""), row.get("reason", ""))
        if key in seen:
            continue
        seen.add(key)
        result.append({"target": key[0], "reason": key[1]})
    return result


def documents_from_source(
    source_package: Mapping[str, Any],
) -> dict[str, Mapping[str, Any]] | None:
    rows = source_package.get("documents")
    if not isinstance(rows, list) or not rows:
        return None
    documents: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            return None
        document_id = row.get("document_id")
        if (
            not isinstance(document_id, str)
            or document_id in documents
            or not isinstance(row.get("content"), str)
        ):
            return None
        documents[document_id] = row
    return documents


def _find_exact_excerpt(content: str, excerpt: Any) -> tuple[int, int] | None:
    if not isinstance(excerpt, str) or not excerpt:
        return None
    first = content.find(excerpt)
    if first < 0 or content.find(excerpt, first + 1) >= 0:
        return None
    line_start = content.count("\n", 0, first) + 1
    return line_start, line_start + excerpt.count("\n")


def safe_relative(value: Any, *, allow_dot: bool = True) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip().replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or any(part == ".." for part in path.parts):
        return None
    result = str(path)
    if result == "." and not allow_dot:
        return None
    return result


def join_workspace(base: Any, child: str) -> str | None:
    base_relative = safe_relative(base)
    if base_relative is None:
        return None
    if child == ".":
        return base_relative
    joined = PurePosixPath(base_relative) / PurePosixPath(child)
    if any(part == ".." for part in joined.parts):
        return None
    return str(joined)


def plan_id(plan_payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        plan_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "execplan:" + hashlib.sha256(encoded).hexdigest()


@dataclass
class AssemblyContext:
    source_package: Mapping[str, Any]
    documents: Mapping[str, Mapping[str, Any]]
    unresolved: list[dict[str, str]] = field(default_factory=list)
    provenance: list[dict[str, Any]] = field(default_factory=list)

    def add_unresolved(self, target: str, reason: str) -> None:
        self.unresolved.append(unresolved(target, reason))

    def system_selection(self, target: str, reason: str) -> None:
        self.provenance.append(
            {
                "origin": "system_selection",
                "target": target,
                "reason": reason,
                "official_source_status": "unspecified",
            }
        )

    def basis_for_excerpt(
        self, *, target: str, document_id: Any, excerpt: Any
    ) -> dict[str, Any] | None:
        document = self.documents.get(str(document_id), {})
        lines = _find_exact_excerpt(str(document.get("content", "")), excerpt)
        if lines is None:
            self.add_unresolved(target, "official_source_excerpt_not_unique_or_missing")
            return None
        basis = {
            "origin": "official_source",
            "source_kind": self.source_package.get("source_kind"),
            "repository": self.source_package.get("repository"),
            "resolved_commit_sha": self.source_package.get("resolved_commit_sha"),
            "document_id": document_id,
            "path": document.get("path"),
            "sha256": document.get("sha256"),
            "locator": {
                "line_start": lines[0] if lines else None,
                "line_end": lines[1] if lines else None,
                "excerpt": excerpt,
            },
        }
        self.provenance.append({"target": target, **basis})
        return basis

    def source_value(self, fact: Any, *, target: str) -> Any:
        if not isinstance(fact, Mapping) or "value" not in fact:
            self.add_unresolved(target, "fact_value_key_missing")
            return None
        if (
            self.basis_for_excerpt(
                target=target,
                document_id=fact.get("source_document_id"),
                excerpt=fact.get("source_excerpt"),
            )
            is None
        ):
            return None
        return fact["value"]
