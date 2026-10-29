from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema.exceptions import _WrappedReferencingError

from app.execution.structured_skill_schema_runtime import (
    StructuredSkillSchemaError,
    find_openair_repository_root,
    load_file_backed_schema,
    validate_instance_against_schema_file,
)

DRAFT7 = "http://json-schema.org/draft-07/schema#"


def _write(path: Path, value: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "schemas" / "pvd").mkdir(parents=True)
    return root


def test_same_directory_relative_ref_passes(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    skill = root / "skills" / "x"
    _write(skill / "defs.json", {"$schema": DRAFT7, "definitions": {"v": {"type": "string"}}})
    schema = _write(
        skill / "input.schema.json",
        {"$schema": DRAFT7, "type": "object", "properties": {"v": {"$ref": "defs.json#/definitions/v"}}},
    )
    validate_instance_against_schema_file({"v": "ok"}, schema, allowed_root=root)


def test_parent_sibling_relative_ref_and_fragment_pass(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    upstream = root / "skills" / "upstream"
    consumer = root / "skills" / "consumer"
    _write(
        upstream / "output.schema.json",
        {"$schema": DRAFT7, "definitions": {"packet": {"type": "object", "required": ["id"], "properties": {"id": {"type": "string"}}}}},
    )
    schema = _write(
        consumer / "input.schema.json",
        {"$schema": DRAFT7, "type": "object", "required": ["packet"], "properties": {"packet": {"$ref": "../upstream/output.schema.json#/definitions/packet"}}},
    )
    validate_instance_against_schema_file({"packet": {"id": "p1"}}, schema, allowed_root=root)


def test_nested_referenced_schema_rebases_relative_ref_to_its_own_uri(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    a = root / "skills" / "a"
    b = root / "skills" / "b"
    c = root / "skills" / "c"
    _write(c / "defs.json", {"$schema": DRAFT7, "definitions": {"v": {"type": "integer"}}})
    _write(b / "output.schema.json", {"$schema": DRAFT7, "definitions": {"value": {"$ref": "../c/defs.json#/definitions/v"}}})
    schema = _write(a / "input.schema.json", {"$schema": DRAFT7, "$ref": "../b/output.schema.json#/definitions/value"})
    validate_instance_against_schema_file(7, schema, allowed_root=root)
    validate_instance_against_schema_file("7", schema, allowed_root=root)


def test_local_fragment_ref_passes(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    schema = _write(
        root / "skills" / "x" / "input.schema.json",
        {"$schema": DRAFT7, "definitions": {"v": {"type": "string"}}, "$ref": "#/definitions/v"},
    )
    validate_instance_against_schema_file("ok", schema, allowed_root=root)


def test_missing_root_schema_fails_closed(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    with pytest.raises(StructuredSkillSchemaError):
        load_file_backed_schema(root / "skills" / "missing" / "input.schema.json", allowed_root=root)


def test_missing_referenced_schema_fails_closed(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    schema = _write(root / "skills" / "x" / "input.schema.json", {"$schema": DRAFT7, "$ref": "missing.json"})
    with pytest.raises(_WrappedReferencingError):
        validate_instance_against_schema_file({}, schema, allowed_root=root)


def test_invalid_referenced_schema_fails_closed(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    skill = root / "skills" / "x"
    _write(skill / "bad.json", {"$schema": DRAFT7, "type": 42})
    schema = _write(skill / "input.schema.json", {"$schema": DRAFT7, "$ref": "bad.json"})
    with pytest.raises(_WrappedReferencingError):
        validate_instance_against_schema_file({}, schema, allowed_root=root)


def test_reference_escape_outside_repository_is_rejected(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    outside = tmp_path / "outside.json"
    _write(outside, {"$schema": DRAFT7, "type": "object"})
    schema = _write(root / "skills" / "x" / "input.schema.json", {"$schema": DRAFT7, "$ref": "../../../outside.json"})
    with pytest.raises(_WrappedReferencingError):
        validate_instance_against_schema_file({}, schema, allowed_root=root)


def test_repository_root_discovery_requires_exactly_one_pvd_anchor(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    file_path = root / "skills" / "x" / "adapter.py"
    file_path.parent.mkdir(parents=True)
    file_path.write_text("# test", encoding="utf-8")
    assert find_openair_repository_root(file_path) == root

    none = tmp_path / "none" / "x.py"
    none.parent.mkdir()
    none.write_text("# test", encoding="utf-8")
    with pytest.raises(StructuredSkillSchemaError, match="found 0 candidates"):
        find_openair_repository_root(none)

    nested = root / "nested"
    (nested / "schemas" / "pvd").mkdir(parents=True)
    nested_file = nested / "skills" / "x.py"
    nested_file.parent.mkdir(parents=True)
    nested_file.write_text("# test", encoding="utf-8")
    with pytest.raises(StructuredSkillSchemaError, match="found 2 candidates"):
        find_openair_repository_root(nested_file)
