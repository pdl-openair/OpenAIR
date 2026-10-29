from __future__ import annotations

import importlib.util
import json
import sys
from copy import deepcopy
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("pvd_recommendation_finalizer", ROOT / "scripts/finalize.py")
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)
OUTPUT_VALIDATOR = Draft202012Validator(
    json.loads((ROOT / "output.schema.json").read_text(encoding="utf-8"))
)


def base(*, decision="release", gate="release", conditions=None, device_bounds=None):
    conditions = conditions or []
    context = {
        "recommendation_parameter_set": {
            "parameters": [
                {
                    "parameter_key": "power",
                    "display_name": "Power",
                    "value_type": "continuous",
                    "unit": "W",
                    "minimum": 50,
                    "maximum": 300,
                }
            ]
        }
    }
    if device_bounds is not None:
        context["device_hard_boundaries"] = device_bounds
    return {
        "current_context": context,
        "direction_analysis": {
            "factor_assessments": [
                {
                    "candidate_ref": "node6:factor_assessment:power",
                    "factor": "power",
                    "display_name": "Power",
                    "candidate_direction": "increase",
                }
            ]
        },
        "feasibility_validation": {
            "recommendation_gate": gate,
            "direction_assessments": [
                {
                    "candidate_ref": "node6:factor_assessment:power",
                    "factor": "power",
                    "candidate_direction": "increase",
                    "decision": decision,
                    "conditions": conditions,
                }
            ],
            "shared_conditions": conditions,
            "shared_risks": [],
            "important_unknowns": ["upstream unknown must not replace model text"],
        },
    }


def experiment(experiment_id="e1", priority=1, value=120, conditions=None):
    return {
        "experiment_id": experiment_id,
        "priority": priority,
        "parameters": [
            {
                "parameter_key": "power",
                "recommended_value": value,
                "confidence": "medium",
                "rationale": f"model rationale for {experiment_id}",
                "basis_refs": [],
            }
        ],
        "design_rationale": f"model design for {experiment_id}",
        "validation_goal": "validate",
        "failure_signal": "reverse",
        "feasibility_conditions": conditions or [],
        "recommendation_risks": [],
    }


def draft(*experiments, first=None, status="recommendation"):
    return {
        "decision_status": status,
        "experiments": list(experiments),
        "limited_resource_first_to_run_experiment_ids": first or [],
        "direction_change_signals": [],
        "next_step": "model next step",
        "important_unknowns": ["model unknown"],
        "portfolio_note": "bounded",
    }


def succeeded(inputs, value):
    result = MODULE.finalize(inputs, value)
    assert result.status == "succeeded", result.errors
    assert not [error for error in OUTPUT_VALIDATOR.iter_errors(result.output) if error.validator == "required"]
    return result.output


def test_zero_accepted_is_no_recommendation_and_not_applicable():
    output = succeeded(base(), draft(status="no_recommendation"))
    assert output["decision_status"] == "no_recommendation"
    assert output["first_run_ranking_status"] == "not_applicable"
    assert output["limited_resource_first_to_run_experiment_ids"] == []


def test_one_accepted_without_first_run_is_pending_not_ranked():
    output = succeeded(base(), draft(experiment()))
    assert output["decision_status"] == "recommendation"
    assert output["first_run_ranking_status"] == "pending"
    assert output["important_unknowns"] == ["model unknown"]
    assert output["next_step"] == "model next step"


def test_one_accepted_with_first_run_is_ranked():
    output = succeeded(base(), draft(experiment(), first=["e1"]))
    assert output["first_run_ranking_status"] == "ranked"
    assert output["limited_resource_first_to_run_experiment_ids"] == ["e1"]


















def test_inputs_and_draft_are_not_modified_in_place():
    inputs = base(
        device_bounds=[{"parameter_key": "power", "minimum": 80, "maximum": 200}]
    )
    value = draft(
        experiment("bad", 1, 60),
        experiment("good", 2, 120),
        first=["bad", "good"],
    )
    before_inputs = deepcopy(inputs)
    before_draft = deepcopy(value)
    output = succeeded(inputs, value)
    assert inputs == before_inputs
    assert value == before_draft
    assert output["limited_resource_first_to_run_experiment_ids"] == ["bad", "good"]
