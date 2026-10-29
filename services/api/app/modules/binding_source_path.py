"""Restricted, shared source-path semantics for Artifact Binding values."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class BindingSourcePathError(ValueError):
    """A stable diagnostic for an invalid or unreadable Binding source path."""

    code: str
    source_path: str
    segment: str | None = None

    def __str__(self) -> str:
        return self.code


def binding_source_path_steps(
    source_path: str,
    top_level_fields: Iterable[str],
) -> tuple[str, ...]:
    """Return the literal-or-dotted traversal used by Artifact Bindings.

    ``$`` selects the complete source document. A valid non-root path first
    selects a matching literal top-level field, preserving existing dotted-key
    documents. It otherwise traverses non-empty dot-separated object keys.
    """
    if not isinstance(source_path, str) or not source_path:
        raise BindingSourcePathError("binding_source_path_invalid", str(source_path))
    if source_path == "$":
        return ()

    steps = tuple(source_path.split("."))
    if any(not step for step in steps):
        raise BindingSourcePathError("binding_source_path_invalid", source_path)
    if source_path in set(top_level_fields):
        return (source_path,)
    return steps


def resolve_binding_source_value(
    document: Mapping[str, Any],
    source_path: str,
) -> Any:
    """Resolve one Artifact document with the formal Binding path semantics."""
    steps = binding_source_path_steps(source_path, document.keys())
    current: Any = document
    for step in steps:
        if not isinstance(current, Mapping):
            raise BindingSourcePathError(
                "binding_source_path_non_object",
                source_path,
                step,
            )
        if step not in current:
            raise BindingSourcePathError(
                "binding_source_path_missing",
                source_path,
                step,
            )
        current = current[step]
    return current


__all__ = [
    "BindingSourcePathError",
    "binding_source_path_steps",
    "resolve_binding_source_value",
]
