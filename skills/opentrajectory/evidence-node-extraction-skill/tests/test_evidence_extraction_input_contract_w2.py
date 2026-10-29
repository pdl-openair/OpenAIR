from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

from jsonschema import Draft7Validator

SKILL = Path(__file__).resolve().parents[1]
DEEP = SKILL.parent / "deepxiv-progressive-reading-skill"


def _load(name: str):
    return json.loads((SKILL / name).read_text(encoding="utf-8"))


def _finalizer():
    path = SKILL / "scripts" / "finalize.py"
    spec = importlib.util.spec_from_file_location("evidence_input_contract_finalize", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_task_id_trim_nonempty_is_formal_schema_constraint():
    schema = _load("input.schema.json")
    Draft7Validator.check_schema(schema)
    task_id_schema = schema["properties"]["task_id"]
    assert not list(Draft7Validator(task_id_schema).iter_errors("task-1"))
    assert list(Draft7Validator(task_id_schema).iter_errors("   "))


def test_upstream_packet_schema_exposes_runtime_relationship_invariants():
    deep_schema = json.loads((DEEP / "output.schema.json").read_text(encoding="utf-8"))
    section = deep_schema["definitions"]["section"]
    anchor = deep_schema["definitions"]["source_anchor"]
    assert "SHA-256" in section["$comment"]
    assert "cross-object" in anchor["$comment"]
    for field in ("section_name", "content", "source_type", "provider", "source_locator"):
        assert section["properties"][field]["pattern"] == r"\S"
    for field in ("anchor_id", "candidate_id", "section_name", "source_type", "provider", "source_locator"):
        assert anchor["properties"][field]["pattern"] == r"\S"


def test_schema_valid_example_is_not_rejected_by_hidden_same_semantic_rule():
    request = json.loads((SKILL / "examples" / "request.json").read_text(encoding="utf-8"))
    draft = json.loads((SKILL / "examples" / "model_semantic_output.json").read_text(encoding="utf-8"))
    result = _finalizer().finalize(request, draft)
    assert result.status in {"succeeded", "partial", "degraded"}
    assert result.output is not None
