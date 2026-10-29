from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

from jsonschema import Draft7Validator

SKILL = Path(__file__).resolve().parents[1]


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _input_schema():
    return json.loads((SKILL / "input.schema.json").read_text(encoding="utf-8"))


def _valid_payload():
    return {
        "topk_selection": {
            "selected_for_p03": [{
                "candidate_id": "candidate-1",
                "title": "Paper",
            }]
        },
        "evidence_schema": {
            "schema_id": "schema-1",
            "schema_version": "0.1.0",
            "task_context": {
                "research_question": "What changes?",
                "task_scope": "current evidence",
                "domain_hints": [],
                "design_assumptions": [],
            },
            "evidence_targets": [{
                "target_id": "target_001",
                "target_type": "performance_metric",
                "scientific_role": "metric",
                "description": "reported metric",
                "requiredness": "required",
                "completion_criteria": "reported value",
                "allowed_evidence_forms": [],
                "condition_dimensions": [],
                "comparison_requirements": [],
                "provenance_requirements": [],
            }],
            "gap_taxonomy": [],
            "uncertainty_rules": [],
        },
    }


def test_trim_nonempty_rules_are_formal_schema_constraints():
    schema = _input_schema()
    validator = Draft7Validator(schema)
    payload = _valid_payload()
    assert not list(validator.iter_errors(payload))

    bad_candidate = _valid_payload()
    bad_candidate["topk_selection"]["selected_for_p03"][0]["candidate_id"] = " \t "
    assert list(validator.iter_errors(bad_candidate))

    bad_description = _valid_payload()
    bad_description["evidence_schema"]["evidence_targets"][0]["description"] = "   "
    assert list(validator.iter_errors(bad_description))

    bad_priority = _valid_payload()
    bad_priority["reading_config"] = {"section_priority": ["   "]}
    assert list(validator.iter_errors(bad_priority))


def test_schema_valid_payload_passes_existing_runtime_identity_admission():
    payload = _valid_payload()
    adapter = _load_module(
        "ot_progressive_reading_input_contract_adapter",
        SKILL / "adapter" / "progressive_reading_adapter.py",
    )
    candidates = adapter._selected_candidates(payload)
    assert candidates[0]["candidate_id"] == "candidate-1"
    evidence = adapter._evidence_schema(payload)
    assert evidence["schema_id"] == "schema-1"
