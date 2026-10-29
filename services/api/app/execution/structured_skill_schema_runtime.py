"""File-backed JSON-Schema validation for shared structured Skill runtime.

A JSON-Schema file is a document with a location.  Relative ``$ref`` values are
therefore resolved from the URI of the document that contains them, including
references nested in another referenced schema.  This module preserves that
standard document semantics without rewriting, copying, or inlining sibling
schemas.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit
from urllib.request import url2pathname

from jsonschema import validators
from jsonschema.protocols import Validator
from referencing import Registry, Resource
from referencing.exceptions import NoSuchResource
from referencing.jsonschema import DRAFT202012, specification_with


class StructuredSkillSchemaError(RuntimeError):
    """Raised when a file-backed schema document cannot be loaded safely."""


def find_openair_repository_root(start: Path) -> Path:
    """Return the unique ancestor containing the formal ``schemas/pvd`` tree.

    Repository discovery is fail-closed: fixed parent indexes, fallback roots,
    and ambiguous nested repositories are intentionally not accepted.
    """
    resolved_start = start.resolve()
    directory = resolved_start if resolved_start.is_dir() else resolved_start.parent
    matches = [
        candidate
        for candidate in (directory, *directory.parents)
        if (candidate / "schemas" / "pvd").is_dir()
    ]
    if len(matches) != 1:
        raise StructuredSkillSchemaError(
            "cannot resolve unique OpenAIR repository root containing schemas/pvd; "
            f"found {len(matches)} candidates from {start}"
        )
    return matches[0]


@dataclass(frozen=True)
class FileBackedSchema:
    """One loaded schema plus a validator whose base is that schema file URI."""

    path: Path
    schema: dict[str, Any]
    validator: Validator

    def validate(self, instance: Any) -> None:
        for error in self.validator.iter_errors(instance):
            if error.validator == "required":
                raise error


def _read_schema_object(path: Path) -> dict[str, Any]:
    try:
        resolved = path.resolve(strict=True)
    except FileNotFoundError as exc:
        raise StructuredSkillSchemaError(f"schema file not found: {path}") from exc
    if not resolved.is_file():
        raise StructuredSkillSchemaError(f"schema path is not a file: {resolved}")
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except UnicodeDecodeError as exc:
        raise StructuredSkillSchemaError(f"schema file is not UTF-8: {resolved}") from exc
    except json.JSONDecodeError as exc:
        raise StructuredSkillSchemaError(f"schema file is not valid JSON: {resolved}") from exc
    if not isinstance(payload, dict):
        raise StructuredSkillSchemaError(f"schema document must be a JSON object: {resolved}")
    validator_cls = validators.validator_for(payload)
    validator_cls.check_schema(payload)
    return payload


def _file_uri_to_path(uri: str) -> Path:
    parsed = urlsplit(uri)
    if parsed.scheme != "file" or parsed.netloc:
        raise NoSuchResource(ref=uri)
    return Path(url2pathname(unquote(parsed.path)))


def load_file_backed_schema(schema_path: Path, *, allowed_root: Path | None = None) -> FileBackedSchema:
    """Load a schema file and preserve its own URI as the relative-ref base.

    ``allowed_root`` is an optional containment boundary for every retrieved
    schema document.  It does not change ref resolution; it only prevents a
    relative ref from escaping the caller-approved repository boundary.
    """
    try:
        root_path = schema_path.resolve(strict=True)
    except FileNotFoundError as exc:
        raise StructuredSkillSchemaError(f"schema file not found: {schema_path}") from exc
    root_schema = _read_schema_object(root_path)
    validator_cls = validators.validator_for(root_schema)
    root_dialect = root_schema.get("$schema")
    root_specification = specification_with(
        str(root_dialect), default=DRAFT202012
    ) if root_dialect else DRAFT202012

    boundary = allowed_root.resolve(strict=True) if allowed_root is not None else None
    if boundary is not None:
        try:
            root_path.relative_to(boundary)
        except ValueError as exc:
            raise StructuredSkillSchemaError(
                f"schema file escapes allowed root: {root_path}"
            ) from exc

    def retrieve(uri: str) -> Resource[dict[str, Any]]:
        path = _file_uri_to_path(uri).resolve()
        if boundary is not None:
            try:
                path.relative_to(boundary)
            except ValueError as exc:
                raise NoSuchResource(ref=uri) from exc
        try:
            schema = _read_schema_object(path)
        except StructuredSkillSchemaError as exc:
            raise NoSuchResource(ref=uri) from exc
        dialect = schema.get("$schema")
        specification = (
            specification_with(str(dialect), default=root_specification)
            if dialect
            else root_specification
        )
        return Resource(contents=schema, specification=specification)

    root_uri = root_path.as_uri()
    registry: Registry[dict[str, Any]] = Registry(retrieve=retrieve).with_resource(
        root_uri,
        Resource(contents=root_schema, specification=root_specification),
    )
    # Validate through a reference to the root resource.  This binds the root
    # schema to its file URI without injecting a synthetic $id into the source
    # document and lets each subsequently retrieved resource retain its own URI.
    validator = validator_cls({"$ref": root_uri}, registry=registry)
    return FileBackedSchema(path=root_path, schema=root_schema, validator=validator)


def validate_instance_against_schema_file(
    instance: Any,
    schema_path: Path,
    *,
    allowed_root: Path | None = None,
) -> dict[str, Any]:
    """Validate *instance* against one file-backed JSON-Schema document."""
    document = load_file_backed_schema(schema_path, allowed_root=allowed_root)
    document.validate(instance)
    return document.schema
