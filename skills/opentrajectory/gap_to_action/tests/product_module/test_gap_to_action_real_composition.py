from __future__ import annotations

import copy

import pytest

from skills.opentrajectory.gap_to_action import (
    build_research_lead_package,
    run_gap_to_action_pipeline,
)
from skills.opentrajectory.gap_to_action.pipeline import (
    UNSUPPORTED_UPSTREAM_GAP_TYPES,
    UPSTREAM_GAP_TYPE_TO_CANONICAL,
)
from skills.opentrajectory.gap_to_action.planner import TRACE_ACTION_FIELDS
from skills.opentrajectory.research_lead_package.domain_profiles.cs import CSDomainProfile
from skills.opentrajectory.research_lead_package.schema import REPRODUCIBILITY_KEYS


def _complete_repro() -> dict[str, str]:
    return {key: f"present:{key}" for key in REPRODUCIBILITY_KEYS}


def _high_confidence_source_mapping() -> dict:
    return {
        "source_type": "manual_fixture",
        "source_paths": [],
        "field_mapping_notes": [],
        "unmapped_source_fields": [],
        "mapping_confidence": "high",
    }


def _build_real_package(**proposal_overrides) -> dict:
    proposal = {
        "evidence_refs": [{"evidence_id": "e-1"}],
        "reproducibility_protocol": _complete_repro(),
        "measured_results": [{"metric": "m", "value": 1}],
    }
    proposal.update(proposal_overrides)
    return build_research_lead_package(
        proposal=proposal,
        domain_profile=CSDomainProfile(),
        task_domain="computer_science",
        task_subdomain="gap_to_action_test",
        evaluation_mode="traceable_package",
        source_mapping=_high_confidence_source_mapping(),
    )


def _strip_planning_annotations(package: dict) -> dict:
    stripped = copy.deepcopy(package)
    stripped.pop("gap_to_action_planning", None)
    stripped.pop("gap_to_action_targeted_actions", None)
    return stripped


def test_real_builder_output_composes_directly_into_pipeline() -> None:
    package = build_research_lead_package(
        proposal={},
        domain_profile=CSDomainProfile(),
        task_domain="computer_science",
        task_subdomain="offline_policy_evaluation",
    )
    upstream_types = [gap["type"] for gap in package["proposal"]["gap_records"]]
    assert upstream_types == [
        "missing_evidence_refs",
        "missing_measured_results",
        "missing_reproducibility_protocol",
        "source_mapping_low_confidence",
        "offline_proposal_traceability_limited",
    ]

    output = run_gap_to_action_pipeline(package)
    action_types = [row["gap_type"] for row in output["planner_before_after"]["action_rows"]]
    assert action_types == ["retrieval_gap", "protocol_gap"]
    assert {row["upstream_gap_type"] for row in output["unsupported_upstream_gaps"]} == {
        "missing_measured_results",
        "source_mapping_low_confidence",
        "offline_proposal_traceability_limited",
    }


def test_real_builder_single_mappable_gap_does_not_require_full_taxonomy() -> None:
    package = _build_real_package(evidence_refs=[])
    assert [gap["type"] for gap in package["proposal"]["gap_records"]] == [
        "missing_evidence_refs"
    ]

    output = run_gap_to_action_pipeline(package)
    assert [row["gap_type"] for row in output["planner_before_after"]["action_rows"]] == [
        "retrieval_gap"
    ]
    assert output["unsupported_upstream_gaps"] == []


def test_real_builder_package_is_deeply_immutable_except_planning_annotations() -> None:
    package = _build_real_package(evidence_refs=[])
    before = copy.deepcopy(package)

    output = run_gap_to_action_pipeline(package)

    assert package == before
    assert output["baseline_research_lead_package"] == before
    treatment = output["treatment_research_lead_package"]
    assert _strip_planning_annotations(treatment) == before
    assert treatment["proposal"] == before["proposal"]
    assert treatment["source_mapping"] == before["source_mapping"]
    assert treatment["domain_normalized"] == before["domain_normalized"]


def test_real_builder_trace_is_propagated_and_absent_trace_is_not_invented() -> None:
    trace_values = {
        "source_gap_pointer": "upstream-pointer",
        "source_gap_id": "gap-42",
        "source_signal": "explicit-signal",
        "source_value": {"kind": "missing"},
        "source_slot_id": "slot-7",
        "trace_id": "trace-9",
        "source_pointer": "source-ptr",
        "evidence_pointer": "evidence-ptr",
    }
    package = _build_real_package(
        gap_records=[
            {
                "type": "retrieval_gap",
                "severity": "high",
                "message": "Explicit canonical gap from upstream product object.",
                "fields": ["proposal.evidence_refs"],
                "current_status": "NOT_FOUND",
                **trace_values,
            }
        ]
    )
    output = run_gap_to_action_pipeline(package)
    action = output["planner_before_after"]["action_rows"][0]
    for field, value in trace_values.items():
        assert action[field] == value
    assert action["current_status"] == "NOT_FOUND"
    assert action["post_action_status"] == "NOT_FOUND"

    no_trace_package = _build_real_package(evidence_refs=[])
    no_trace_output = run_gap_to_action_pipeline(no_trace_package)
    no_trace_action = no_trace_output["planner_before_after"]["action_rows"][0]
    for field in TRACE_ACTION_FIELDS:
        assert field not in no_trace_action


def test_real_upstream_vocabulary_is_exhaustively_explicit() -> None:
    assert UPSTREAM_GAP_TYPE_TO_CANONICAL == {
        "missing_evidence_refs": "retrieval_gap",
        "missing_reproducibility_protocol": "protocol_gap",
    }
    assert set(UNSUPPORTED_UPSTREAM_GAP_TYPES) == {
        "missing_measured_results",
        "source_mapping_low_confidence",
        "unmapped_source_fields",
        "offline_proposal_traceability_limited",
    }
