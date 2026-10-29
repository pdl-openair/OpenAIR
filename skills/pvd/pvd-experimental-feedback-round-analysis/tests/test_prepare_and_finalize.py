from __future__ import annotations

from scripts.finalize import validate_final_result
from scripts.prepare_input import prepare_model_input


def _conclusion(target: int, use: str | None, *, support_level: str = "direct") -> dict:
    return {
        "conclusion_id": f"support_conclusion:target_{target:03d}",
        "judgment_target": f"target_{target:03d}",
        "evidence_record_refs": [f"REC-{target}"],
        "evidence_refs": [f"EVID-{target}"],
        "support_level": support_level,
        "allowed_uses": [] if use is None else [use],
        "conditions": [],
        "transfer_limits": [f"limit-{target}"],
        "conflicts": [],
        "important_unknowns": [],
        "rationale": f"rationale-{target}",
    }


def _formal_input() -> dict:
    return {
        "user_query": "diagnose EXP-1",
        "experiment_ref": "EXP-1",
        "scientific_state_facts": {
            "experiment_history": [
                {"experiment_ref": "EXP-0", "planned_execution": {"provenance": {"source_ref": "PLAN-0"}}, "actual_execution": {"execution_ref": "EXEC-0"}, "measurements": [], "process_events": []},
                {"experiment_ref": "EXP-1", "planned_execution": {"provenance": {"source_ref": "PLAN-1"}}, "actual_execution": {"execution_ref": "EXEC-1"}, "measurements": [{"measurement_ref": "MEAS-1"}], "process_events": []},
            ]
        },
        "evidence_comparability": {
            "analysis_status": "complete",
            "evidence_assessments": [],
            "support_conclusions": [
                _conclusion(1, "mechanism"),
                _conclusion(2, "trend_direction"),
                _conclusion(3, "magnitude_reference"),
                _conclusion(4, "direct_numeric"),
                _conclusion(5, None, support_level="background_only"),
            ],
            "conflicts": [],
            "important_unknowns": [],
            "summary": "formal Cap4 fixture",
        },
        "factor_analysis": {"artifact_ref": "FACTOR-1", "basis_refs": ["REC-FACTOR"]},
        "recommendation": {"artifact_ref": "RECMD-1", "basis_refs": ["FACTOR-1"]},
        "current_project_context": None,
    }


def _result(basis: str = "support_conclusion:target_001") -> dict:
    return {
        "experiment_ref": "EXP-1",
        "execution_analysis": {"deviations": [{"basis_refs": ["EXEC-1", "PLAN-1"]}]},
        "measurement_analysis": {"limitations": [{"basis_refs": ["MEAS-1"]}]},
        "result_change": {"basis_refs": ["MEAS-1"], "comparison_refs": []},
        "findings": [{"basis_refs": [basis]}],
        "anomalies": [], "confounders": [], "causal_boundary": {"basis_refs": ["EXP-1"]},
    }


def test_explicit_experiment_ref_is_selected_not_latest_or_first() -> None:
    model = prepare_model_input(_formal_input())
    assert model["selected_experiment"]["experiment_ref"] == "EXP-1"
    assert model["selected_experiment"]["actual_execution"]["execution_ref"] == "EXEC-1"




def test_current_cap4_shape_is_consumed_without_shadow_assessments() -> None:
    model = prepare_model_input(_formal_input())
    assert "evidence_analyses" not in model["evidence_comparability"]
    assert [row["conclusion_id"] for row in model["evidence_comparability"]["support_conclusions"]] == [
        f"support_conclusion:target_{i:03d}" for i in range(1, 6)
    ]


def test_mechanism_is_a_positive_diagnosis_authorization() -> None:
    formal = _formal_input(); model = prepare_model_input(formal)
    validate_final_result(formal, model, _result("support_conclusion:target_001"))










def test_support_level_never_derives_authorization() -> None:
    formal = _formal_input()
    row = formal["evidence_comparability"]["support_conclusions"][4]
    row["support_level"] = "direct"
    row["allowed_uses"] = []
    model = prepare_model_input(formal)


def test_basis_refs_accept_selected_facts_authorized_cap4_factor_and_recommendation() -> None:
    formal = _formal_input(); model = prepare_model_input(formal)
    validate_final_result(formal, model, _result())
    result = _result("FACTOR-1")
    validate_final_result(formal, model, result)
    result = _result("RECMD-1")
    validate_final_result(formal, model, result)






def test_factor_and_recommendation_context_remain_traceable() -> None:
    model = prepare_model_input(_formal_input())
    assert model["factor_analysis"]["artifact_ref"] == "FACTOR-1"
    assert model["recommendation"]["artifact_ref"] == "RECMD-1"
