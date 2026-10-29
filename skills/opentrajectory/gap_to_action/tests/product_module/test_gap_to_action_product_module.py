from __future__ import annotations

import copy

import pytest

from skills.opentrajectory.gap_to_action import plan_gap_to_actions, run_gap_to_action_pipeline
from skills.opentrajectory.gap_to_action.adapter import run_gap_to_action_adapter
from skills.opentrajectory.gap_to_action.pipeline import validate_pipeline_output


def _single_gap_suite() -> dict:
    return {
        "suite_id": "single-gap-product-test",
        "cases": [
            {
                "case": "Synthetic lead",
                "role": "RESEARCH_LEAD",
                "boundary": "preserve supplied evidence/status",
                "status_fields": {},
                "gaps": [
                    {
                        "gap_type": "protocol_gap",
                        "current_status": "PARTIAL",
                        "required_input": "missing protocol variables",
                        "expected_output": "protocol completion request",
                        "stop_condition": "stop after missing variables are listed",
                        "hard_fail": "do not claim execution",
                        "source_gap_pointer": "$.slot_coverage_table[0]",
                        "source_gap_id": "gap-1",
                    }
                ],
            }
        ],
    }


def _legacy_research_lead_package() -> dict:
    return {
        "package_id": "pkg-product-test",
        "decision_evidence_contract": {"state": "missing"},
        "scientific_effect_validated": False,
        "evidence_packet": {"evidence_items": []},
        "slot_coverage_table": [
            {
                "research_lead_id": "lead-1",
                "slot_id": "validation_protocol",
                "slot_label": "validation protocol",
                "status": "partial",
                "current_status": "PARTIAL",
                "source_gap_pointer": "$.slot_coverage_table[0]",
                "source_gap_id": "gap-1",
            }
        ],
        "research_leads": [
            {
                "research_lead_id": "lead-1",
                "case": "Synthetic lead",
                "role": "RESEARCH_LEAD",
                "status_fields": {},
                "decision_evidence_contract": {
                    "missing_critical_items": ["validation_protocol"]
                },
                "decision_level_ready": False,
                "missing_decision_slots": ["validation_protocol"],
            }
        ],
    }


def test_product_planner_accepts_only_gap_types_present_and_keeps_trace() -> None:
    output = plan_gap_to_actions(_single_gap_suite())
    assert len(output["action_rows"]) == 1
    action = output["action_rows"][0]
    assert action["post_action_status"] == "PARTIAL"
    assert action["source_gap_pointer"] == "$.slot_coverage_table[0]"
    assert action["source_gap_id"] == "gap-1"


def test_legacy_shape_pipeline_does_not_mutate_input_evidence_or_status() -> None:
    package = _legacy_research_lead_package()
    before = copy.deepcopy(package)
    output = run_gap_to_action_pipeline(package)

    assert package == before
    assert output["baseline_research_lead_package"] == before
    treatment = output["treatment_research_lead_package"]
    assert treatment["decision_evidence_contract"] == before["decision_evidence_contract"]
    assert treatment["evidence_packet"] == before["evidence_packet"]
    assert treatment["slot_coverage_table"] == before["slot_coverage_table"]
    assert treatment["research_leads"][0]["decision_level_ready"] is False
    assert treatment["research_leads"][0]["gap_to_action_targeted_actions"]


def test_adapter_is_importable_without_workbench_wiring() -> None:
    output = run_gap_to_action_adapter(
        {"research_lead_package": _legacy_research_lead_package()}
    )
    assert output["evidence_producing_run"] is False


def test_authentic_package_requires_explicit_track_f_normalizer_hook() -> None:
    package = {"contract_type": "authentic_research_lead_package", "package_id": "auth-1"}
    with pytest.raises(ValueError, match="explicit gap normalizer"):
        run_gap_to_action_pipeline(package)


def test_package_public_api_exports_adapter():
    import skills.opentrajectory.gap_to_action as product

    assert product.run_gap_to_action_adapter is run_gap_to_action_adapter
