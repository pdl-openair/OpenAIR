from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from app.execution.opentrajectory_structured_runtime_guard import (
    RuntimeGuardRejected,
    apply_opentrajectory_runtime_guard,
)


def _problem_framing_output() -> dict:
    return {
        "intent_understanding": {
            "original_user_query": "compare alpha and beta",
            "research_object": "alpha and beta",
            "target_metrics": ["performance"],
            "boundary_conditions": [],
            "comparison_baseline": "alpha versus beta",
            "output_intent": "comparison",
            "requested_source_scope": None,
            "ambiguities": [],
        },
        "constraints_criteria": {
            "interpreted_constraints": [{
                "constraint_name": "comparison_baseline",
                "original_phrase": "compare alpha and beta",
                "constraint_type": "comparison",
                "interpreted_value": "alpha versus beta",
                "candidate_values": [],
                "semantic_state": "confirmed",
                "domain_rationale": "The user explicitly requested the comparison.",
                "downstream_search_terms": ["alpha", "beta"],
                "evidence_basis": "compare alpha and beta",
            }],
            "missing_information": [],
            "research_risks": [],
        },
    }


def _phase2_output() -> dict:
    return {
        "interpreted_constraints": [{
            "constraint_name": "charge_rate",
            "original_phrase": "exactly 2C",
            "constraint_type": "condition_threshold",
            "interpreted_value": "2C",
            "candidate_values": [],
            "domain_rationale": "The source constraint is preserved.",
            "confidence": 0.45,
            "needs_user_confirmation": True,
            "downstream_search_terms": ["2C charge rate"],
            "evidence_basis": "user input",
            "risk_flags": [],
        }],
        "quality_report": {
            "confidence": 0.45,
            "coverage_level": "partial",
            "risk_flags": [],
            "missing_inputs": [],
            "needs_manual_review": True,
        },
    }


def _phase3_output() -> dict:
    return {
        "search_matrix": [{
            "search_intent": "core_topic",
            "query_terms": ["alpha"],
            "must_include_terms": ["2C"],
            "optional_terms": [],
            "exclude_terms": ["candidate exclusion"],
            "source_constraints": ["charge_rate"],
        }],
        "expanded_query_variants": [{
            "query": '"alpha" AND "2C"',
            "variant_type": "strict",
            "search_intent": "core_topic",
            "source_constraints": ["charge_rate"],
            "priority": 1,
        }, {
            "query": '"comparison"',
            "variant_type": "fallback",
            "search_intent": "core_topic",
            "source_constraints": [],
            "priority": 3,
        }],
        "quality_report": {
            "search_matrix_count": 0,
            "expanded_query_variants_count": 0,
            "fallback_used": True,
        },
        "assembled_facets": {"core_entities": ["alpha"]},
    }


def test_problem_framing_current_contract_passes_without_degradation() -> None:
    expected = _problem_framing_output()

    output, diagnostics = apply_opentrajectory_runtime_guard(
        skill_id="problem-framing-generation",
        input_payload={"user_query": "compare alpha and beta"},
        output_payload=deepcopy(expected),
    )

    assert output == expected
    assert diagnostics["status"] == "passed"
    assert diagnostics["downstream_execution_allowed"] is True
    assert diagnostics["warnings"] == []


@pytest.mark.parametrize(
    "legacy_value",
    [
        ["filter: rate >= 2C"],
        ["exclude everything"],
        ["must_include secret"],
        ["随便一段中文"],
        [{"action": "exclude", "value": "anything"}],
    ],
)
def test_problem_framing_deprecated_field_is_content_independent(
    legacy_value: object,
) -> None:
    expected = _problem_framing_output()
    payload = deepcopy(expected)
    payload["constraints_criteria"]["interpreted_constraints"][0][
        "downstream_filter_rules"
    ] = legacy_value

    output, diagnostics = apply_opentrajectory_runtime_guard(
        skill_id="problem-framing-generation",
        input_payload={"user_query": "compare alpha and beta"},
        output_payload=payload,
    )

    assert output == expected
    assert diagnostics["status"] == "degraded"
    assert diagnostics["downstream_execution_allowed"] is True
    assert diagnostics["recoverable_failures"] == ["PF-R04"]
    check = next(row for row in diagnostics["checks"] if row["check_id"] == "PF-R04")
    assert check["evidence"] == {
        "ignored_paths": [
            "constraints_criteria.interpreted_constraints[0].downstream_filter_rules"
        ]
    }


def test_problem_framing_preserves_exact_user_constraint_without_filter_rules() -> None:
    user_query = "compare alpha at exactly 2C"
    payload = _problem_framing_output()
    payload["intent_understanding"].update({
        "original_user_query": user_query,
        "research_object": "alpha at exactly 2C",
        "boundary_conditions": ["exactly 2C"],
        "comparison_baseline": None,
    })
    constraint = payload["constraints_criteria"]["interpreted_constraints"][0]
    constraint.update({
        "constraint_name": "charge_rate",
        "original_phrase": "exactly 2C",
        "constraint_type": "condition_threshold",
        "interpreted_value": "2C",
        "candidate_values": [],
        "semantic_state": "ambiguous",
        "domain_rationale": "The model left the semantic state unresolved.",
        "downstream_search_terms": ["2C charge rate"],
        "evidence_basis": "exactly 2C",
    })

    output, diagnostics = apply_opentrajectory_runtime_guard(
        skill_id="problem-framing-generation",
        input_payload={"user_query": user_query},
        output_payload=deepcopy(payload),
    )

    assert output == payload
    assert output["intent_understanding"]["original_user_query"] == user_query
    assert output["constraints_criteria"]["interpreted_constraints"][0][
        "original_phrase"
    ] == "exactly 2C"
    assert diagnostics["status"] == "passed"
    assert diagnostics["downstream_execution_allowed"] is True
    assert "formal_gate_satisfied" not in diagnostics
    assert "needs_manual_review" not in diagnostics
    assert diagnostics["result_protocol"]["status"] == "degraded"


def test_managed_non_object_output_remains_fatal() -> None:
    with pytest.raises(RuntimeGuardRejected) as exc_info:
        apply_opentrajectory_runtime_guard(
            skill_id="problem-framing-generation",
            input_payload={"user_query": "compare alpha and beta"},
            output_payload=[],
        )

    assert "OT-R00" in exc_info.value.diagnostics["fatal_failures"]


def test_problem_framing_rejects_query_identity_drift() -> None:
    payload = _problem_framing_output()
    payload["intent_understanding"]["original_user_query"] = "different query"

    with pytest.raises(RuntimeGuardRejected) as exc_info:
        apply_opentrajectory_runtime_guard(
            skill_id="problem-framing-generation",
            input_payload={"user_query": "compare alpha and beta"},
            output_payload=payload,
        )

    assert "PF-R02" in exc_info.value.diagnostics["fatal_failures"]


def test_problem_framing_removes_model_routing_authority_without_blocking() -> None:
    payload = _problem_framing_output()
    payload["constraints_criteria"]["needs_manual_review"] = False

    output, diagnostics = apply_opentrajectory_runtime_guard(
        skill_id="problem-framing-generation",
        input_payload={"user_query": "compare alpha and beta"},
        output_payload=payload,
    )

    assert "needs_manual_review" not in output["constraints_criteria"]
    assert diagnostics["status"] == "degraded"
    assert diagnostics["downstream_execution_allowed"] is True
    assert "PF-R03" in diagnostics["recoverable_failures"]


def test_phase1_sanitizes_representation_without_inventing_semantics() -> None:
    output, diagnostics = apply_opentrajectory_runtime_guard(
        skill_id="structured-requirement-generation",
        input_payload={"user_query": "alpha beta"},
        output_payload={
            "original_user_query": " alpha   beta ",
            "research_object": "alpha",
            "ambiguities": [],
        },
    )

    assert output["original_user_query"] == "alpha beta"
    assert diagnostics["sanitizer_applied"] is True


def test_phase1_rejects_query_drift() -> None:
    with pytest.raises(RuntimeGuardRejected) as exc_info:
        apply_opentrajectory_runtime_guard(
            skill_id="structured-requirement-generation",
            input_payload={"user_query": "alpha beta"},
            output_payload={
                "original_user_query": "different",
                "research_object": "alpha",
                "ambiguities": [],
            },
        )

    assert "P1-R02" in exc_info.value.diagnostics["fatal_failures"]


@pytest.mark.parametrize(
    "legacy_value",
    [
        ["filter: rate >= 2C"],
        ["exclude everything"],
        ["must_include secret"],
        ["随便一段中文"],
        [{"action": "exclude", "value": "anything"}],
    ],
)
def test_phase2_deprecated_field_is_ignored_without_semantic_classification(
    legacy_value: object,
) -> None:
    expected = _phase2_output()
    payload = deepcopy(expected)
    payload["interpreted_constraints"][0]["downstream_filter_rules"] = legacy_value

    output, diagnostics = apply_opentrajectory_runtime_guard(
        skill_id="constraint-interpretation-skill",
        input_payload={"user_query": "compare alpha at exactly 2C"},
        output_payload=payload,
    )

    assert output == expected
    assert diagnostics["status"] == "degraded"
    assert diagnostics["downstream_execution_allowed"] is True
    assert diagnostics["recoverable_failures"] == ["P2-R03"]


def test_phase2_without_deprecated_field_preserves_semantics() -> None:
    expected = _phase2_output()

    output, diagnostics = apply_opentrajectory_runtime_guard(
        skill_id="constraint-interpretation-skill",
        input_payload={"user_query": "compare alpha at exactly 2C"},
        output_payload=deepcopy(expected),
    )

    assert output == expected
    assert diagnostics["status"] == "passed"
    assert diagnostics["downstream_execution_allowed"] is True


def test_phase3_semantic_text_cases_do_not_block() -> None:
    output, diagnostics = apply_opentrajectory_runtime_guard(
        skill_id="retrieval-matrix-generation",
        input_payload={
            "interpreted_constraints": [{
                "constraint_name": "charge_rate",
                "candidate_values": ["2C"],
                "semantic_state": "ambiguous",
            }],
            "comparison_objects": ["alpha", "beta"],
        },
        output_payload=_phase3_output(),
    )

    assert output["expanded_query_variants"][0]["query"] == '"alpha" AND "2C"'
    assert output["search_matrix"][0]["exclude_terms"] == ["candidate exclusion"]
    assert diagnostics["downstream_execution_allowed"] is True
    for check_id in ("P3-R04", "P3-R05", "P3-R06"):
        check = next(row for row in diagnostics["checks"] if row["check_id"] == check_id)
        assert check["status"] == "not_applicable"


def test_phase3_repairs_derived_counts_only() -> None:
    output, diagnostics = apply_opentrajectory_runtime_guard(
        skill_id="retrieval-matrix-generation",
        input_payload={},
        output_payload=_phase3_output(),
    )

    assert output["quality_report"]["search_matrix_count"] == 1
    assert output["quality_report"]["expanded_query_variants_count"] == 2
    assert diagnostics["downstream_execution_allowed"] is True


def test_phase3_quarantines_normalized_duplicate_without_blocking() -> None:
    payload = _phase3_output()
    payload["expanded_query_variants"][1]["query"] = '  "alpha"   AND   "2C"  '

    output, diagnostics = apply_opentrajectory_runtime_guard(
        skill_id="retrieval-matrix-generation",
        input_payload={"user_query": "compare alpha at 2C"},
        output_payload=payload,
    )

    assert len(output["expanded_query_variants"]) == 1
    assert diagnostics["status"] == "degraded"
    assert diagnostics["downstream_execution_allowed"] is True
    assert diagnostics["result_protocol"]["quarantined_items"]


def test_unmanaged_structured_skill_is_not_forced_through_phase_rules():
    output, diagnostics = apply_opentrajectory_runtime_guard(
        skill_id="judge-skill",
        input_payload={},
        output_payload={"verdict": "pass"},
    )
    assert output == {"verdict": "pass"}
    assert diagnostics["managed"] is False


def _p07c_request(*, with_schema: bool = False) -> dict:
    request = {
        "task_id": "task-p07c-guard",
        "research_context": {"research_object": "material comparison"},
        "paper_packets": [{
            "candidate_id": "candidate:001",
            "arxiv_id": "2500.00001",
            "material_status": "MATERIAL_READY",
            "sections": [{
                "section_name": "Results",
                "content": "The sample delivered a conductivity of 12 mS cm-1 under room temperature testing.",
                "content_hash": "abc123def456",
            }],
            "source_anchors": [{
                "anchor_id": "anchor:001",
                "candidate_id": "candidate:001",
                "arxiv_id": "2500.00001",
                "section_name": "Results",
                "content_hash": "abc123def456",
            }],
        }],
    }
    if with_schema:
        request["evidence_schema"] = {
            "schema_id": "schema:pytest:001",
            "evidence_targets": [
                {"target_id": "target_001", "requiredness": "required"},
                {"target_id": "target_002", "requiredness": "preferred"},
            ],
        }
    return request


def _p07c_draft_item(*, matched_target_ids: list[str] | None = None, target_match_status: str | None = None) -> dict:
    item = {
        "source_anchor_id": "anchor:001",
        "evidence_type": "quantitative_result",
        "claim": "The sample delivered a conductivity of 12 mS cm-1.",
        "evidence_quote": "The sample delivered a conductivity of 12 mS cm-1 under room temperature testing.",
        "entities": [],
        "conditions": [],
        "metric": {"name": "conductivity", "value_text": "12", "unit": "mS cm-1"},
        "mechanism": None,
        "confidence": 0.9,
        "evidence_id": "",
        "candidate_id": "",
        "arxiv_id": "",
        "section_name": "",
        "content_hash": "",
    }
    if matched_target_ids is not None:
        item["matched_target_ids"] = matched_target_ids
        if target_match_status is not None:
            item["target_match_status"] = target_match_status
    return item


def test_p07c_without_evidence_schema_preserves_original_guard_behavior():
    output, diagnostics = apply_opentrajectory_runtime_guard(
        skill_id="evidence-node-extraction-skill",
        input_payload=_p07c_request(),
        output_payload={
            "evidence_items": [_p07c_draft_item()],
            "summary": {},
            "audit": {},
            "extraction_warnings": [],
        },
    )
    assert diagnostics["status"] == "passed"
    assert "schema_bridge_enabled" not in diagnostics
    assert output["evidence_items"][0]["evidence_id"].startswith("ev:")


def test_p07c_quarantines_packet_without_material_ready():
    request = _p07c_request()
    request["paper_packets"][0].pop("material_status")
    output, diagnostics = apply_opentrajectory_runtime_guard(
        skill_id="evidence-node-extraction-skill",
        input_payload=request,
        output_payload={"evidence_items": [], "summary": {}, "audit": {}, "extraction_warnings": []},
    )
    assert output["evidence_items"] == []
    assert diagnostics["status"] == "degraded"
    assert diagnostics["downstream_execution_allowed"] is True
    assert "P07C-R00-MATERIAL" in diagnostics["recoverable_failures"]


def test_p07c_with_evidence_schema_accepts_valid_binding():
    output, diagnostics = apply_opentrajectory_runtime_guard(
        skill_id="evidence-node-extraction-skill",
        input_payload=_p07c_request(with_schema=True),
        output_payload={
            "schema_id": "schema:pytest:001",
            "evidence_items": [_p07c_draft_item(matched_target_ids=["target_001"], target_match_status="matched")],
            "summary": {},
            "audit": {},
            "extraction_warnings": [],
        },
    )
    assert diagnostics["status"] == "passed"
    assert diagnostics["schema_bridge_enabled"] is True
    assert diagnostics["schema_id"] == "schema:pytest:001"
    assert diagnostics["matched_target_ids"] == ["target_001"]
    assert output["schema_id"] == "schema:pytest:001"


def test_p07c_schema_id_mismatch_is_degraded_not_task_fatal():
    output, diagnostics = apply_opentrajectory_runtime_guard(
        skill_id="evidence-node-extraction-skill",
        input_payload=_p07c_request(with_schema=True),
        output_payload={
            "schema_id": "schema:pytest:wrong",
            "evidence_items": [_p07c_draft_item(matched_target_ids=[], target_match_status=None)],
            "summary": {},
            "audit": {},
            "extraction_warnings": [],
        },
    )
    assert output["evidence_items"]
    assert diagnostics["status"] == "degraded"
    assert diagnostics["downstream_execution_allowed"] is True
    assert "P07C-R04" in diagnostics["recoverable_failures"]


def test_p07c_quarantines_unknown_matched_target_ids():
    output, diagnostics = apply_opentrajectory_runtime_guard(
        skill_id="evidence-node-extraction-skill",
        input_payload=_p07c_request(with_schema=True),
        output_payload={
            "schema_id": "schema:pytest:001",
            "evidence_items": [_p07c_draft_item(matched_target_ids=["target_999"], target_match_status="matched")],
            "summary": {},
            "audit": {},
            "extraction_warnings": [],
        },
    )
    assert output["evidence_items"] == []
    assert diagnostics["status"] == "degraded"
    assert diagnostics["downstream_execution_allowed"] is True


def test_p07c_allows_empty_matched_target_ids_with_schema():
    output, diagnostics = apply_opentrajectory_runtime_guard(
        skill_id="evidence-node-extraction-skill",
        input_payload=_p07c_request(with_schema=True),
        output_payload={
            "schema_id": "schema:pytest:001",
            "evidence_items": [_p07c_draft_item(matched_target_ids=[])],
            "summary": {},
            "audit": {},
            "extraction_warnings": [],
        },
    )
    assert diagnostics["status"] == "passed"
    assert output["evidence_items"][0]["matched_target_ids"] == []


def test_p07c_reports_missing_required_targets_without_rejecting():
    _output, diagnostics = apply_opentrajectory_runtime_guard(
        skill_id="evidence-node-extraction-skill",
        input_payload=_p07c_request(with_schema=True),
        output_payload={
            "schema_id": "schema:pytest:001",
            "evidence_items": [_p07c_draft_item(matched_target_ids=[], target_match_status=None)],
            "summary": {},
            "audit": {},
            "extraction_warnings": [],
        },
    )
    assert diagnostics["status"] == "passed"
    assert diagnostics["missing_required_target_ids"] == ["target_001"]
    assert "P07C-W01" in diagnostics["warnings"]


def test_p07c_quarantines_invalid_target_match_status():
    output, diagnostics = apply_opentrajectory_runtime_guard(
        skill_id="evidence-node-extraction-skill",
        input_payload=_p07c_request(with_schema=True),
        output_payload={
            "schema_id": "schema:pytest:001",
            "evidence_items": [_p07c_draft_item(matched_target_ids=["target_001"], target_match_status="bogus")],
            "summary": {},
            "audit": {},
            "extraction_warnings": [],
        },
    )
    assert output["evidence_items"] == []
    assert diagnostics["status"] == "degraded"
    assert diagnostics["downstream_execution_allowed"] is True


def test_p07c_evidence_schema_without_id_is_degraded_not_task_fatal():
    request = _p07c_request()
    request["evidence_schema"] = {"evidence_targets": [{"target_id": "target_001"}]}
    output, diagnostics = apply_opentrajectory_runtime_guard(
        skill_id="evidence-node-extraction-skill",
        input_payload=request,
        output_payload={
            "evidence_items": [_p07c_draft_item(matched_target_ids=[])],
            "summary": {},
            "audit": {},
            "extraction_warnings": [],
        },
    )
    assert output["evidence_items"]
    assert diagnostics["status"] == "degraded"
    assert diagnostics["downstream_execution_allowed"] is True
    assert "P07C-R04" in diagnostics["recoverable_failures"]


def test_only_explicit_fatal_allowlist_can_reject_entire_output() -> None:
    from app.execution import opentrajectory_structured_runtime_guard as guard

    assert guard._FATAL_CHECK_IDS == {"OT-R00", "P1-R02", "PF-R02"}
    source = Path(guard.__file__).read_text(encoding="utf-8")
    assert source.count("raise RuntimeGuardRejected") == 1


def test_phase2_language_and_punctuation_do_not_create_hard_gate() -> None:
    payload = _phase2_output()
    payload["interpreted_constraints"][0]["downstream_search_terms"] = [
        "快充？",
        "fast charging",
        "快充？",
    ]

    output, diagnostics = apply_opentrajectory_runtime_guard(
        skill_id="constraint-interpretation-skill",
        input_payload={"user_query": "研究快充"},
        output_payload=payload,
    )

    assert output["interpreted_constraints"][0]["downstream_search_terms"] == [
        "快充？",
        "fast charging",
    ]
    assert diagnostics["downstream_execution_allowed"] is True


def test_phase3_quarantines_one_bad_query_and_keeps_good_queries() -> None:
    payload = _phase3_output()
    payload["expanded_query_variants"].append({
        "query": '("broken" AND',
        "variant_type": "broad",
        "search_intent": "core_topic",
        "source_constraints": [],
        "priority": 2,
    })

    output, diagnostics = apply_opentrajectory_runtime_guard(
        skill_id="retrieval-matrix-generation",
        input_payload={"user_query": "compare alpha and beta"},
        output_payload=payload,
    )

    assert len(output["expanded_query_variants"]) == 2
    assert diagnostics["status"] == "degraded"
    assert diagnostics["downstream_execution_allowed"] is True


def test_phase3_uses_canonical_query_when_all_generated_queries_are_bad() -> None:
    payload = _phase3_output()
    payload["expanded_query_variants"] = [{
        "query": '("broken" AND',
        "variant_type": "strict",
        "search_intent": "core_topic",
        "source_constraints": [],
        "priority": 1,
    }]

    output, diagnostics = apply_opentrajectory_runtime_guard(
        skill_id="retrieval-matrix-generation",
        input_payload={"user_query": "原始用户问题"},
        output_payload=payload,
    )

    assert output["expanded_query_variants"][0]["query"] == "原始用户问题"
    assert diagnostics["fallback_used"] is True
    assert diagnostics["downstream_execution_allowed"] is True


@pytest.mark.parametrize("skill_id,payload,check", [
    ("structured-requirement-generation", {"research_object": "alpha"}, "P1-R02"),
    (
        "problem-framing-generation",
        {"intent_understanding": {"research_object": "alpha"}, "constraints_criteria": {}},
        "PF-R02",
    ),
])
def test_missing_query_echo_does_not_reject_remaining_research(skill_id, payload, check):
    output, diagnostics = apply_opentrajectory_runtime_guard(
        skill_id=skill_id, input_payload={"user_query": "alpha"}, output_payload=payload,
    )
    assert output == payload
    assert diagnostics["downstream_execution_allowed"] is True
    assert check in diagnostics["recoverable_failures"]
    assert not diagnostics["fatal_failures"]
