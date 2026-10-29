from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import ValidationError
from jsonschema.validators import validator_for

ROOT = Path(__file__).resolve().parents[1]
INPUT_SCHEMA = json.loads((ROOT / "input.schema.json").read_text(encoding="utf-8"))
OUTPUT_SCHEMA = json.loads((ROOT / "output.schema.json").read_text(encoding="utf-8"))
INPUT_VALIDATOR = validator_for(INPUT_SCHEMA)(INPUT_SCHEMA)
OUTPUT_VALIDATOR = validator_for(OUTPUT_SCHEMA)(OUTPUT_SCHEMA)


def _formal_input() -> dict[str, object]:
    return {
        "user_query": "诊断 EXP-001",
        "experiment_ref": "EXP-001",
        "scientific_state_facts": {
            "experiment_history": [
                {"experiment_ref": "EXP-000", "planned_execution": None, "actual_execution": None, "measurements": [], "process_events": []},
                {"experiment_ref": "EXP-001", "planned_execution": {"provenance": {"source_ref": "PLAN-001"}}, "actual_execution": {"execution_ref": "EXEC-001"}, "measurements": [], "process_events": []},
            ]
        },
        "evidence_comparability": None,
        "factor_analysis": None,
        "recommendation": None,
        "current_project_context": None,
    }


def _output() -> dict[str, object]:
    return {
        "analysis_status": "partial",
        "experiment_ref": "EXP-001",
        "execution_analysis": {"status": "deviated", "deviations": [{"statement": "actual differs from plan", "basis_refs": ["EXEC-001", "PLAN-001"]}]},
        "measurement_analysis": {"status": "not_supplied", "limitations": [{"statement": "no measurement", "basis_refs": ["EXP-001"]}]},
        "result_change": {"state": "not_assessable", "basis_refs": ["EXP-001"], "comparison_refs": []},
        "findings": [{"epistemic_state": "observed", "statement": "execution deviation exists", "basis_refs": ["EXEC-001"], "uncertainty": None, "missing_evidence": []}],
        "anomalies": [],
        "confounders": [],
        "causal_boundary": {"causality_established": False, "statement": "one round does not establish causality", "basis_refs": ["EXP-001"]},
        "unresolved_questions": ["performance change requires measurement"],
        "upstream_context": {"authorized_evidence_refs": [], "factor_basis_refs": [], "recommendation_basis_refs": []},
        "downstream_readiness": {"status": "usable_with_caution", "reasons": ["execution diagnosis is usable; performance remains unknown"]},
        "summary": "Execution deviation is traceable; performance is not assessable without measurement."
    }


def test_formal_input_requires_explicit_experiment_ref_and_scientific_state_facts() -> None:
    INPUT_VALIDATOR.validate(_formal_input())
    payload = _formal_input(); payload.pop("experiment_ref")
    with pytest.raises(ValidationError): INPUT_VALIDATOR.validate(payload)
