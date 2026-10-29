from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema.validators import validator_for
from scripts.prepare_input import prepare_model_input

CAP8_ROOT = Path(__file__).resolve().parents[1]
CAP4_ROOT = CAP8_ROOT.parent / "pvd-evidence-applicability-comparability-analysis"
CAP4_SCHEMA = json.loads((CAP4_ROOT / "output.schema.json").read_text(encoding="utf-8"))
CAP4_VALIDATOR = validator_for(CAP4_SCHEMA)(CAP4_SCHEMA)


def _cap4_fixture(use: str | None) -> dict:
    return {
        "analysis_status": "complete",
        "evidence_assessments": [],
        "support_conclusions": [{
            "conclusion_id": "support_conclusion:target_001",
            "judgment_target": "target_001",
            "evidence_record_refs": ["record_001"],
            "evidence_refs": ["evidence_001"],
            "support_level": "direct",
            "allowed_uses": [] if use is None else [use],
            "conditions": [],
            "transfer_limits": [],
            "conflicts": [],
            "important_unknowns": [],
            "rationale": "fixture",
        }],
        "conflicts": [],
        "important_unknowns": [],
        "summary": "fixture",
    }


def _formal(cap4: dict) -> dict:
    return {
        "user_query": "diagnose",
        "experiment_ref": "EXP-1",
        "scientific_state_facts": {"experiment_history": [{
            "experiment_ref": "EXP-1", "planned_execution": None, "actual_execution": None,
            "measurements": [], "process_events": [],
        }]},
        "evidence_comparability": cap4,
        "factor_analysis": None,
        "recommendation": None,
        "current_project_context": None,
    }


@pytest.mark.parametrize("use", ["mechanism", "trend_direction", "magnitude_reference", "direct_numeric", None])
def test_fixture_uses_real_current_cap4_output_schema(use: str | None) -> None:
    cap4 = _cap4_fixture(use)
    CAP4_VALIDATOR.validate(cap4)
    model = prepare_model_input(_formal(cap4))
    assert model["evidence_comparability"]["support_conclusions"][0]["allowed_uses"] == ([] if use is None else [use])
