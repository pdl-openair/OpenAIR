from __future__ import annotations

import pytest

from evals.opentrajectory.harness.strategy_phase1_3.phase3_deterministic_checks import (
    check_p3_002,
    run_phase3_deterministic_checks,
)


def context(query: str, **fields):
    return {
        "execution_mode": "executed",
        "handling_class": "normal_execute",
        "baseline_eligible": True,
        "output_payload": {
            "expanded_query_variants": [{"query": query}],
            **fields,
        },
    }


@pytest.mark.parametrize("query", ["radon", "RNA", "氮化物", "α粒子", "AND"])
def test_nonempty_terms_are_only_assessed_for_structure(query):
    assert check_p3_002(context(query))["status"] == "passed"


@pytest.mark.parametrize("query", ["", "  "])
def test_missing_search_terms_are_rejected(query):
    assert check_p3_002(context(query))["status"] == "failed"


def test_structural_success_never_claims_scientific_quality_or_baseline():
    result = run_phase3_deterministic_checks(
        context(
            "electrode kinetics",
            comparison_objects=["silicon anode", "graphite anode"],
            pairwise_required=True,
            required_pairwise_objects=[["silicon anode", "graphite anode"]],
        )
    )
    assert result["overall_status"] == "passed"
    assert result["baseline_eligible"] is False
    assert result["assessment_scope"] == "structural_contract"
    assert result["scientific_quality_status"] == "not_evaluated"


@pytest.mark.parametrize("handling_class", ["conservative_generate", "manual_review_required"])
def test_manual_review_cannot_enter_baseline(handling_class):
    case = context("electrode kinetics")
    case["handling_class"] = handling_class
    result = run_phase3_deterministic_checks(case)
    assert result["overall_status"] == "manual_review_required"
    assert result["baseline_eligible"] is False


def test_fallback_cannot_claim_success():
    case = context("electrode kinetics", fallback_used=True, overall_status="passed")
    result = run_phase3_deterministic_checks(case)
    assert result["overall_status"] == "failed"
    assert result["baseline_eligible"] is False
