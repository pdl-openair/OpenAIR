from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

from jsonschema import Draft202012Validator

SKILL = Path(__file__).resolve().parents[1]


def _load(name: str):
    return json.loads((SKILL / name).read_text(encoding="utf-8"))


def _finalizer():
    path = SKILL / "scripts" / "finalize.py"
    spec = importlib.util.spec_from_file_location("ot_screening_input_contract_finalize", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _valid_input():
    return {
        "task_id": "task-1",
        "user_query": "research question",
        "research_context": {"goal": "compare evidence"},
        "candidates": [{
            "candidate_id": "candidate-1",
            "title": "Paper",
            "provenance": [{"source": "test"}],
        }],
    }


def test_whitespace_identity_rejected_by_formal_schema_before_finalizer():
    schema = _load("input.schema.json")
    validator = Draft202012Validator(schema)
    payload = _valid_input()
    assert not list(validator.iter_errors(payload))
    payload["candidates"][0]["candidate_id"] = "   "
    assert list(validator.iter_errors(payload))


def test_schema_valid_identity_is_not_rejected_by_hidden_same_semantic_rule():
    f = _finalizer()
    payload = _valid_input()
    model = {
        "judgments": [{
            "candidate_id": "candidate-1",
            "decision": "exclude",
            "reading_priority": None,
            "evidence_roles": [],
            "relevant_target_ids": [],
            "reason": "Not needed for the current question.",
        }]
    }
    result = f.finalize(payload, model)
    assert result.status != "failed"
