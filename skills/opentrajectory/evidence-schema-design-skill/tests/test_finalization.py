from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any

from jsonschema import Draft7Validator


ROOT = Path(__file__).resolve().parents[1]


def _load_finalizer():
    path = ROOT / "scripts" / "finalize.py"
    spec = importlib.util.spec_from_file_location("test_evidence_schema_design_finalizer", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _input() -> dict[str, Any]:
    return {
        "task_id": "task-evidence-finalization",
        "intent_understanding": {
            "original_user_query": "  How does the process change the measured property?  ",
        },
    }


def _ready_draft() -> dict[str, Any]:
    return {
        "ready": [
            {
                "task_scope": "Preserve process, measurement, and comparison context.",
                "domain_hints": ["materials science"],
                "design_assumptions": ["Unreported values remain unknown."],
                "evidence_targets": [
                    {
                        "target_type": "experimental_condition",
                        "scientific_role": "condition",
                        "description": "Record the process condition with the associated result.",
                        "requiredness": "required",
                        "completion_criteria": "At least one source reports the condition and result together.",
                        "allowed_evidence_forms": ["methods", "table"],
                        "condition_dimensions": ["process condition"],
                        "comparison_requirements": ["same measurement definition"],
                        "provenance_requirements": ["source anchor"],
                    }
                ],
                "gap_taxonomy": ["missing_condition", "not_comparable"],
                "uncertainty_rules": [
                    {
                        "trigger": "The comparison condition is not reported.",
                        "handling": "Keep comparability unresolved.",
                    }
                ],
                "design_warnings": ["Current evidence may be incomplete."],
            }
        ],
        "blocked": [],
    }


def test_model_schema_excludes_code_owned_formal_fields() -> None:
    schema = json.loads((ROOT / "model_output.schema.json").read_text(encoding="utf-8"))
    serialized = json.dumps(schema, ensure_ascii=False)
    for forbidden in (
        '"schema_id"',
        '"schema_version"',
        '"research_question"',
        '"target_id"',
        '"rule_id"',
        '"optional_extensions"',
    ):
        assert forbidden not in serialized

    draft = _ready_draft()
    assert list(Draft7Validator(schema).iter_errors(draft)) == []


def test_ready_draft_finalizes_to_strict_formal_schema_without_rewriting_science() -> None:
    finalizer = _load_finalizer()
    draft = _ready_draft()
    result = finalizer.finalize(_input(), deepcopy(draft))

    assert result.status == "succeeded"
    output = result.output
    assert output is not None
    formal = json.loads((ROOT / "output.schema.json").read_text(encoding="utf-8"))
    assert list(Draft7Validator(formal).iter_errors(output)) == []

    evidence = output["evidence_schema"]
    assert evidence["schema_id"] == "task-evidence-finalization:evidence"
    assert evidence["schema_version"] == "0.1.0"
    assert evidence["task_context"]["research_question"] == _input()["intent_understanding"]["original_user_query"]
    assert "optional_extensions" not in evidence
    assert evidence["evidence_targets"][0]["target_id"] == "target_001"
    assert evidence["uncertainty_rules"][0]["rule_id"] == "u01"

    source = draft["ready"][0]
    target = evidence["evidence_targets"][0]
    assert {key: value for key, value in target.items() if key != "target_id"} == source["evidence_targets"][0]
    rule = evidence["uncertainty_rules"][0]
    assert {key: value for key, value in rule.items() if key != "rule_id"} == source["uncertainty_rules"][0]
    assert evidence["task_context"]["task_scope"] == source["task_scope"]
    assert evidence["task_context"]["domain_hints"] == source["domain_hints"]
    assert evidence["task_context"]["design_assumptions"] == source["design_assumptions"]
    assert evidence["gap_taxonomy"] == source["gap_taxonomy"]
    assert output["design_warnings"] == source["design_warnings"]


def test_blocked_draft_is_wrapped_without_semantic_substitution() -> None:
    finalizer = _load_finalizer()
    blocked = {
        "blocking_reason": "The formal inputs conflict on the research object.",
        "missing_minimum_information": ["one authoritative research object"],
        "missing_comparison_dimensions": [],
        "design_warnings": ["Resolve the authority conflict before evidence design."],
    }
    result = finalizer.finalize(_input(), {"ready": [], "blocked": [deepcopy(blocked)]})

    assert result.status == "succeeded"
    assert result.output == {"status": "blocked", **blocked}
    formal = json.loads((ROOT / "output.schema.json").read_text(encoding="utf-8"))
    assert list(Draft7Validator(formal).iter_errors(result.output)) == []


def test_missing_ready_payload_retains_existing_failure() -> None:
    result = _load_finalizer().finalize(_input(), {"ready": [], "blocked": []})

    assert result.status == "failed"
    assert result.output is None
    assert result.errors == ("evidence_schema_ready_payload_missing",)
