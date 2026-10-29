from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft7Validator


ROOT = Path(__file__).resolve().parents[1]


def _current_output() -> dict:
    return {
        "intent_understanding": {
            "original_user_query": "compare alpha at exactly 2C",
            "research_object": "alpha",
            "target_metrics": [],
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
    }


def test_current_problem_framing_contract_has_no_free_text_filter_field() -> None:
    schema = json.loads((ROOT / "output.schema.json").read_text(encoding="utf-8"))
    constraint = schema["properties"]["constraints_criteria"]["properties"][
        "interpreted_constraints"
    ]["items"]

    assert "downstream_filter_rules" not in constraint["required"]
    assert "downstream_filter_rules" not in constraint["properties"]
    assert list(Draft7Validator(schema).iter_errors(_current_output())) == []


def test_skill_does_not_request_a_replacement_free_text_filter_field() -> None:
    instructions = (ROOT / "SKILL.md").read_text(encoding="utf-8")

    assert "downstream_filter_rules" not in instructions
    assert "must-have / optional / exclusion" in instructions
