from __future__ import annotations

import importlib.util
import json
import sys
from copy import deepcopy
from pathlib import Path

import pytest
from app.execution.durable_skill_runtime import _validate_structured_json
from app.execution.skill_contract_policy import apply_semantic_contract

ROOT = Path(__file__).resolve().parents[6]
SKILLS = ROOT / "skills" / "pvd"


def source_input() -> dict:
    return {
        "current_context": {
            "recommendation_parameter_set": {
                "parameters": [
                    {
                        "parameter_key": "power",
                        "display_name": "Power",
                        "value_type": "continuous",
                        "unit": "W",
                    },
                ]
            }
        },
        "direction_analysis": {
            "factor_assessments": [
                {
                    "candidate_ref": "direction:power",
                    "factor": "power",
                    "display_name": "Power",
                    "candidate_direction": "uncertain",
                },
            ]
        },
    }


@pytest.mark.parametrize("direction", ["increase", "uncertain", "invalid_direction"])
def test_actual_cap6_input_preserves_direction(direction: str) -> None:
    contract = json.loads(
        (
            SKILLS / "pvd-parameter-experiment-plan-feasibility-validation/input.schema.json"
        ).read_text(encoding="utf-8")
    )
    value = source_input()
    value["direction_analysis"]["factor_assessments"][0]["candidate_direction"] = direction
    before = deepcopy(value)
    warnings: list[str] = []
    result = _validate_structured_json(
        value, contract, "cap6 input", role="input", input_warnings=warnings
    )
    assert result == before == value
    assert not warnings


@pytest.mark.parametrize(
    "gate", ["release", "conditional_release", "block", "defer", "invalid_gate"]
)
def test_actual_cap7_gate_reaches_consumer_without_default_release(gate: str) -> None:
    directory = SKILLS / "pvd-round-experiment-plan-parameter-recommendation"
    contract = json.loads((directory / "input.schema.json").read_text(encoding="utf-8"))
    value = source_input()
    value["feasibility_validation"] = {
        "recommendation_gate": gate,
        "direction_assessments": [
            {
                **value["direction_analysis"]["factor_assessments"][0],
                "decision": "defer",
            }
        ],
    }
    before = deepcopy(value)
    received, warnings = apply_semantic_contract(value, contract, role="input")
    assert received == value == before
    assert not warnings
    spec = importlib.util.spec_from_file_location(
        "safe_pvd_recommendation", directory / "scripts/finalize.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    result = module.finalize(received, {"decision_status": "no_recommendation", "experiments": []})
    assert "recommendation_gate_invalid" not in result.errors
