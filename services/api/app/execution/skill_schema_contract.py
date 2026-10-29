"""Freeze Skill JSON Schemas into self-contained Registry authority.

This module resolves only JSON-Schema references. It does not interpret Skill
identities or scientific fields, and it never mutates source Skill artifacts.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from app.modules.repo_paths import repo_root


def _schema_pointer(document: Any, fragment: str) -> Any:
    if not fragment:
        return document
    if not fragment.startswith("/"):
        raise ValueError("skill_schema_ref_fragment_invalid")
    current = document
    for raw_part in fragment[1:].split("/"):
        part = unquote(raw_part).replace("~1", "/").replace("~0", "~")
        if isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError) as exc:
                raise ValueError("skill_schema_ref_fragment_invalid") from exc
        elif isinstance(current, dict) and part in current:
            current = current[part]
        else:
            raise ValueError("skill_schema_ref_fragment_invalid")
    return current


def _load_schema_document(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Skill schema must be an object: {path}")
    return value


def _inline_frozen_schema_refs(
    value: Any,
    *,
    document: dict[str, Any],
    document_path: Path,
    authority_root: Path,
    inline_local_refs: bool = False,
    resolving: frozenset[tuple[str, str]] = frozenset(),
) -> Any:
    """Resolve relative file references into one self-contained frozen schema."""
    if isinstance(value, list):
        return [
            _inline_frozen_schema_refs(
                item,
                document=document,
                document_path=document_path,
                authority_root=authority_root,
                inline_local_refs=inline_local_refs,
                resolving=resolving,
            )
            for item in value
        ]
    if not isinstance(value, dict):
        return value

    ref = value.get("$ref")
    if isinstance(ref, str):
        split = urlsplit(ref)
        is_local = not split.scheme and not split.netloc and not split.path
        is_relative_file = not split.scheme and not split.netloc and bool(split.path)
        if (is_local and inline_local_refs) or is_relative_file:
            target_document = document
            target_path = document_path
            if is_relative_file:
                target_path = (document_path.parent / split.path).resolve()
                root = authority_root.resolve()
                if target_path != root and root not in target_path.parents:
                    raise ValueError("skill_schema_ref_outside_authority_root")
                if not target_path.is_file():
                    raise ValueError("skill_schema_ref_unavailable")
                target_document = _load_schema_document(target_path)

            fragment = unquote(split.fragment or "")
            key = (str(target_path), fragment)
            if key in resolving:
                raise ValueError("skill_schema_ref_cycle")
            target = _schema_pointer(target_document, fragment)
            resolved_target = _inline_frozen_schema_refs(
                target,
                document=target_document,
                document_path=target_path,
                authority_root=authority_root,
                inline_local_refs=True,
                resolving=resolving | {key},
            )
            siblings = {key: item for key, item in value.items() if key != "$ref"}
            if not siblings:
                return resolved_target
            resolved_siblings = _inline_frozen_schema_refs(
                siblings,
                document=document,
                document_path=document_path,
                authority_root=authority_root,
                inline_local_refs=inline_local_refs,
                resolving=resolving,
            )
            return {"allOf": [resolved_target, resolved_siblings]}

    return {
        key: _inline_frozen_schema_refs(
            item,
            document=document,
            document_path=document_path,
            authority_root=authority_root,
            inline_local_refs=inline_local_refs,
            resolving=resolving,
        )
        for key, item in value.items()
    }


def build_frozen_skill_schema_contract(skill_path: str) -> dict[str, dict[str, Any]]:
    """Capture self-contained Skill input/output schemas for frozen authority."""
    authority_root = repo_root().resolve()
    skill_root = (authority_root / str(skill_path or "")).resolve()
    if skill_root == authority_root or authority_root not in skill_root.parents:
        raise ValueError("skill_schema_path_invalid")

    frozen: dict[str, dict[str, Any]] = {}
    for schema_name in ("input", "output"):
        path = skill_root / f"{schema_name}.schema.json"
        schema = _load_schema_document(path)
        frozen_schema = _inline_frozen_schema_refs(
            schema,
            document=schema,
            document_path=path.resolve(),
            authority_root=authority_root,
        )
        encoded = json.dumps(
            frozen_schema,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        frozen[schema_name] = {
            "sha256": hashlib.sha256(encoded).hexdigest(),
            "schema": frozen_schema,
        }
    return frozen
