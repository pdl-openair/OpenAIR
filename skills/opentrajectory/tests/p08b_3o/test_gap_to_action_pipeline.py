from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
TEST_DIR = Path(__file__).resolve().parent
if str(TEST_DIR) not in sys.path:
    sys.path.insert(0, str(TEST_DIR))

from gap_to_action_pipeline import (  # noqa: E402
    build_planner_suite_from_research_lead_package,
    run_gap_to_action_pipeline,
    validate_pipeline_output,
)


FIXTURE = TEST_DIR / "fixture_research_lead_package.json"
EXPECTED = (
    REPO_ROOT
    / "artifacts/opentrajectory-p08b-3o-gap-to-action-pipeline-ae661d0/before_after_pipeline_output.json"
)


@pytest.fixture(scope="module")
def lead_package() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def pipeline_output(lead_package: dict) -> dict:
    return run_gap_to_action_pipeline(lead_package)


def _case(output: dict, section: str, name: str) -> dict:
    return next(
        row for row in output["planner_before_after"][section]["cases"] if row["case"] == name
    )


def test_real_package_object_maps_visible_gaps_to_targeted_actions(
    lead_package: dict,
    pipeline_output: dict,
) -> None:
    suite = build_planner_suite_from_research_lead_package(lead_package)
    assert suite["cases"]
    assert {
        row["gap_type"]
        for case in suite["cases"]
        for row in case["gaps"]
    } >= {
        "retrieval_gap",
        "source_lock_gap",
        "protocol_gap",
        "threshold_gap",
        "baseline_control_gap",
        "decision_rule_gap",
        "independent_validation_gap",
        "domain_evidence_contract_gap",
    }
    baseline_leads = pipeline_output["baseline_research_lead_package"]["research_leads"]
    treatment_leads = pipeline_output["treatment_research_lead_package"]["research_leads"]
    assert all("gap_to_action_targeted_actions" not in lead for lead in baseline_leads)
    assert sum(len(lead.get("gap_to_action_targeted_actions") or []) for lead in treatment_leads) == len(
        pipeline_output["planner_before_after"]["action_rows"]
    )


def test_same_type_status_different_slots_are_not_deduped(lead_package: dict) -> None:
    package = copy.deepcopy(lead_package)
    package["slot_coverage_table"].extend(
        [
            {
                "research_lead_id": "lead_paperbench",
                "slot_id": "artifact_alpha",
                "slot_label": "artifact alpha",
                "status": "missing",
                "current_status": "NOT_FOUND",
            },
            {
                "research_lead_id": "lead_paperbench",
                "slot_id": "artifact_beta",
                "slot_label": "artifact beta",
                "status": "missing",
                "current_status": "NOT_FOUND",
            },
        ]
    )
    package["research_leads"][0]["missing_decision_slots"].extend(["artifact_alpha", "artifact_beta"])
    package["research_leads"][0]["decision_evidence_contract"]["missing_critical_items"].extend(
        ["artifact_alpha", "artifact_beta"]
    )

    suite = build_planner_suite_from_research_lead_package(package)
    paperbench = next(case for case in suite["cases"] if case["case"] == "PaperBench bounded subcase")
    matched = [
        gap
        for gap in paperbench["gaps"]
        if gap["gap_type"] == "retrieval_gap" and gap["current_status"] == "NOT_FOUND"
    ]
    assert len(matched) == 2
    assert {gap["required_input"].split(":")[0] for gap in matched} == {"artifact alpha", "artifact beta"}


def test_exact_duplicate_slot_rows_are_still_deduped(lead_package: dict) -> None:
    package = copy.deepcopy(lead_package)
    duplicate_row = {
        "research_lead_id": "lead_paperbench",
        "slot_id": "artifact_duplicate",
        "slot_label": "artifact duplicate",
        "status": "missing",
        "current_status": "NOT_FOUND",
    }
    package["slot_coverage_table"].extend([copy.deepcopy(duplicate_row), copy.deepcopy(duplicate_row)])
    package["research_leads"][0]["missing_decision_slots"].append("artifact_duplicate")
    package["research_leads"][0]["decision_evidence_contract"]["missing_critical_items"].append(
        "artifact_duplicate"
    )

    suite = build_planner_suite_from_research_lead_package(package)
    paperbench = next(case for case in suite["cases"] if case["case"] == "PaperBench bounded subcase")
    matched = [
        gap
        for gap in paperbench["gaps"]
        if gap["gap_type"] == "retrieval_gap" and gap["current_status"] == "NOT_FOUND"
    ]
    assert len(matched) == 1
    assert matched[0]["required_input"].startswith("artifact duplicate:")


def test_unkeyed_slot_rows_only_match_declared_missing_slots(lead_package: dict) -> None:
    package = copy.deepcopy(lead_package)
    package["slot_coverage_table"].extend(
        [
            {
                "slot_id": "paper_task_artifact_source",
                "slot_label": "unkeyed paper task artifact source",
                "status": "missing",
                "current_status": "UNKEYED_MATCH_ONLY_PAPERBENCH",
            },
            {
                "slot_id": "orphan_source_anchor",
                "slot_label": "orphan source anchor",
                "status": "missing",
                "current_status": "UNKEYED_ORPHAN_SHOULD_IGNORE",
            },
        ]
    )

    suite = build_planner_suite_from_research_lead_package(package)
    cases = {case["case"]: case for case in suite["cases"]}
    matched_counts = {
        case_name: sum(
            1
            for gap in case["gaps"]
            if gap["current_status"] == "UNKEYED_MATCH_ONLY_PAPERBENCH"
        )
        for case_name, case in cases.items()
    }
    orphan_count = sum(
        1
        for case in cases.values()
        for gap in case["gaps"]
        if gap["current_status"] == "UNKEYED_ORPHAN_SHOULD_IGNORE"
    )
    assert matched_counts == {
        "PaperBench bounded subcase": 1,
        "ASSB interlaboratory reproducibility": 0,
        "BFE/F2DEM / Li||NCM811": 0,
    }
    assert orphan_count == 0


def test_treatment_does_not_mutate_status_or_post_action_status(pipeline_output: dict) -> None:
    baseline = pipeline_output["baseline_research_lead_package"]
    treatment = pipeline_output["treatment_research_lead_package"]
    assert baseline["decision_evidence_contract"] == treatment["decision_evidence_contract"]
    assert baseline["scientific_effect_validated"] == treatment["scientific_effect_validated"]
    assert baseline["evidence_packet"] == treatment["evidence_packet"]
    assert baseline["slot_coverage_table"] == treatment["slot_coverage_table"]
    for before, after in zip(baseline["research_leads"], treatment["research_leads"]):
        assert before["decision_level_ready"] == after["decision_level_ready"]
        assert before["status_fields"] == after["status_fields"]
        assert before["decision_evidence_contract"] == after["decision_evidence_contract"]
    for action in pipeline_output["planner_before_after"]["action_rows"]:
        assert action["post_action_status"] == action["current_status"]


def test_paperbench_remains_reproduction_workflow_only(pipeline_output: dict) -> None:
    paperbench = _case(pipeline_output, "treatment", "PaperBench bounded subcase")
    rendered = json.dumps(paperbench).lower()
    assert "reproduction" in rendered
    assert "scientific " + "validation" not in rendered


def test_assb_remains_reproducibility_grounded_validation_only(pipeline_output: dict) -> None:
    assb = _case(pipeline_output, "treatment", "ASSB interlaboratory reproducibility")
    rendered = json.dumps(assb).lower()
    assert "reproducibility" in rendered
    for forbidden in ("dis" + "covery", "break" + "through", "universal " + "rule"):
        assert forbidden not in rendered


def test_bfe_fixed_fields_remain_unchanged(pipeline_output: dict) -> None:
    required = {
        "role": "NEGATIVE_ONLY",
        "fast_charge_protocol": "PARTIAL",
        "explicit threshold": "PARTIAL",
        "executable decision rule": "PLANNED_TEMPLATE_ONLY / GAP",
        "reproduced_validation": "NOT_FOUND",
        "decision_level_ready": False,
        "scientific_effect_validated": False,
    }
    for section in ("baseline", "treatment"):
        bfe = _case(pipeline_output, section, "BFE/F2DEM / Li||NCM811")
        assert bfe["role"] == "NEGATIVE_ONLY"
        assert bfe["status_fields"] == required
    for section in ("baseline_research_lead_package", "treatment_research_lead_package"):
        bfe = next(
            lead
            for lead in pipeline_output[section]["research_leads"]
            if lead["case"] == "BFE/F2DEM / Li||NCM811"
        )
        assert bfe["status_fields"] == required


def test_evidence_producing_run_remains_false(pipeline_output: dict) -> None:
    assert pipeline_output["evidence_producing_run"] is False
    assert pipeline_output["planner_before_after"]["evidence_producing_run"] is False
    assert (
        pipeline_output["treatment_research_lead_package"]["gap_to_action_planning"][
            "evidence_producing_run"
        ]
        is False
    )
