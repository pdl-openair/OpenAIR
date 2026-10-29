from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

from jsonschema import Draft7Validator


ROOT = Path(__file__).resolve().parents[1]


def _current_input() -> dict:
    retrieval = json.loads(
        (
            ROOT.parent
            / "retrieval-matrix-generation"
            / "examples"
            / "response.json"
        ).read_text(encoding="utf-8")
    )
    return {
        "task_id": "task-evidence-input-contract",
        "intent_understanding": {
            "original_user_query": "compare alpha at exactly 2C",
            "research_object": "alpha",
            "target_metrics": ["performance"],
            "boundary_conditions": ["exactly 2C"],
            "comparison_baseline": None,
            "output_intent": "comparison",
            "requested_source_scope": None,
            "ambiguities": [],
        },
        "constraints_criteria": {
            "interpreted_constraints": [{
                "constraint_name": "charge_rate",
                "original_phrase": "exactly 2C",
                "constraint_type": "condition_threshold",
                "interpreted_value": "2C",
                "candidate_values": [],
                "semantic_state": "confirmed",
                "domain_rationale": "The user supplied an exact rate.",
                "downstream_search_terms": ["2C charge rate"],
                "evidence_basis": "exactly 2C",
            }],
            "missing_information": [],
            "research_risks": [],
        },
        "retrieval_strategy_design": retrieval,
    }


def test_current_input_uses_formal_constraints_and_retrieval_strategy() -> None:
    schema = json.loads((ROOT / "input.schema.json").read_text(encoding="utf-8"))
    constraint = schema["definitions"]["constraints_criteria_artifact"]["properties"][
        "interpreted_constraints"
    ]["items"]

    assert "downstream_filter_rules" not in constraint["required"]
    assert "downstream_filter_rules" not in constraint["properties"]
    assert list(Draft7Validator(schema).iter_errors(_current_input())) == []


def test_removed_filter_field_cannot_reenter_evidence_design_contract() -> None:
    schema = json.loads((ROOT / "input.schema.json").read_text(encoding="utf-8"))
    legacy = deepcopy(_current_input())
    legacy["constraints_criteria"]["interpreted_constraints"][0][
        "downstream_filter_rules"
    ] = ["exclude everything"]

    assert list(Draft7Validator(schema).iter_errors(legacy))


def test_skill_migrates_filtering_authority_to_retrieval_strategy() -> None:
    instructions = (ROOT / "SKILL.md").read_text(encoding="utf-8")

    assert "free-text filter rules" in instructions
    assert "retrieval_strategy_design" in instructions
    assert "Do not reconstruct a removed filter-rule field" in instructions
