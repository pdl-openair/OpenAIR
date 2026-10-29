from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest
from jsonschema import Draft7Validator

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import runtime_guard as guard
from scripts.identity import build_input_fingerprint
from scripts.validation import ContractRejected


def load_json(relative_path: str):
    return json.loads((ROOT / relative_path).read_text(encoding="utf-8"))


INPUT_SCHEMA = load_json("input.schema.json")
DRAFT_SCHEMA = load_json("draft.schema.json")
OUTPUT_SCHEMA = load_json("output.schema.json")
REQUEST = load_json("examples/request.json")
DRAFT = load_json("examples/response.draft.json")
EXPECTED = load_json("examples/response.json")


def validate(schema, payload) -> None:
    Draft7Validator(schema).validate(payload)


def second_hypothesis():
    row = copy.deepcopy(DRAFT["hypotheses"][0])
    row["title"] = "Coordination-dependent interphase changes predict higher Coulombic efficiency"
    row["statement"] = (
        "Within the stated lithium-metal boundary, a stronger LiF-rich interphase signature "
        "will be associated with higher Coulombic efficiency under matched conditions."
    )
    row["independent_variable"] = {
        "name": "LiF-rich interphase signature",
        "manipulation_or_contrast": "Higher versus lower predefined LiF-rich interphase signature across matched conditions.",
    }
    row["dependent_variable"] = {
        "name": "Coulombic efficiency",
        "measurement_or_observation": "Coulombic efficiency assessed using the same predefined metric across the compared conditions.",
    }
    row["expected_relation"] = {
        "direction": "positive_association",
        "detail": "A stronger LiF-rich interphase signature is expected to be associated with higher Coulombic efficiency.",
    }
    row["falsifiable_prediction"] = (
        "Conditions with a stronger predefined LiF-rich interphase signature will show reproducibly higher "
        "Coulombic efficiency than matched conditions with a weaker signature."
    )
    row["falsification_condition"] = (
        "The hypothesis is falsified within the stated boundary if a stronger predefined LiF-rich interphase "
        "signature is not associated with reproducibly higher Coulombic efficiency under matched conditions."
    )
    return row


def test_fingerprint_tracks_semantic_input_but_not_task_id():
    baseline = build_input_fingerprint(REQUEST)

    changed = copy.deepcopy(REQUEST)
    changed["research_context"]["research_object"] += " with a changed scientific scope"
    assert build_input_fingerprint(changed) != baseline

    changed = copy.deepcopy(REQUEST)
    changed["research_lead_packet"]["research_leads"][0]["research_question"] += "?"
    assert build_input_fingerprint(changed) != baseline

    changed = copy.deepcopy(REQUEST)
    changed["generation_config"]["focus_questions"].append("Examine an additional semantic focus.")
    assert build_input_fingerprint(changed) != baseline

    changed = copy.deepcopy(REQUEST)
    changed["task_id"] = "different-invocation-id"
    assert build_input_fingerprint(changed) == baseline


def test_required_hypothesis_structure_cannot_be_missing():
    required_paths = [
        ("independent_variable", "name"),
        ("dependent_variable", "measurement_or_observation"),
        ("expected_relation", "direction"),
    ]
    for parent, child in required_paths:
        draft = copy.deepcopy(DRAFT)
        del draft["hypotheses"][0][parent][child]
        with pytest.raises(ContractRejected, match="draft schema violation"):
            guard.finalize(REQUEST, draft)

    for field in ("boundary_conditions", "falsifiable_prediction", "falsification_condition"):
        draft = copy.deepcopy(DRAFT)
        if isinstance(draft["hypotheses"][0][field], list):
            draft["hypotheses"][0][field] = []
        else:
            draft["hypotheses"][0][field] = ""
        result = guard.finalize(REQUEST, draft)
        assert result["hypotheses"][0][field] == draft["hypotheses"][0][field]


def test_fingerprint_is_stable_for_unordered_semantic_collections():
    reordered = copy.deepcopy(REQUEST)
    reordered["research_context"]["target_metrics"].reverse()
    reordered["research_context"]["boundary_conditions"].reverse()
    reordered["mechanism_packet"]["mechanism_edges"].reverse()
    for edge in reordered["mechanism_packet"]["mechanism_edges"]:
        edge["evidence_ids"].reverse()
    lead = reordered["research_lead_packet"]["research_leads"][0]
    lead["mechanism_basis_edge_ids"].reverse()
    lead["provenance_evidence_ids"].reverse()
    lead["boundary_conditions"].reverse()
    reordered["generation_config"]["focus_questions"].reverse()
    assert build_input_fingerprint(reordered) == build_input_fingerprint(REQUEST)


def test_skill_registration_declares_single_formal_responsibility():
    import yaml

    registration = yaml.safe_load((ROOT / "skill.yaml").read_text(encoding="utf-8"))
    assert registration["skill_id"] == "ot_falsifiable_hypothesis_generation"
    assert registration["runtime"] == "llm_structured"
    assert registration["stages"] == ["insight_generation"]
    assert registration["substages"] == ["hypothesis_update"]
    assert registration["skill_type"] == "atomic"


def test_fingerprint_uses_effective_generation_config_defaults():
    omitted = copy.deepcopy(REQUEST)
    omitted.pop("generation_config")

    explicit_default = copy.deepcopy(omitted)
    explicit_default["generation_config"] = {"max_hypotheses": 8}

    explicit_empty_focus = copy.deepcopy(omitted)
    explicit_empty_focus["generation_config"] = {
        "max_hypotheses": 8,
        "focus_questions": [],
    }

    expected = build_input_fingerprint(omitted)
    assert build_input_fingerprint(explicit_default) == expected
    assert build_input_fingerprint(explicit_empty_focus) == expected


def test_fingerprint_ignores_local_runtime_metadata_only():
    baseline = build_input_fingerprint(REQUEST)

    changed = copy.deepcopy(REQUEST)
    changed["mechanism_packet"]["mechanism_edges"][0]["local_edge_key"] = "edge-runtime-only"
    changed["mechanism_packet"]["mechanism_chains"][0]["local_chain_key"] = "chain-runtime-only"
    changed["findings_packet"]["findings"][0]["local_finding_key"] = "finding-runtime-only"
    changed["research_lead_packet"]["research_leads"][0]["local_lead_key"] = "lead-runtime-only"
    assert build_input_fingerprint(changed) == baseline

    semantic_change = copy.deepcopy(REQUEST)
    semantic_change["mechanism_packet"]["mechanism_edges"][0]["relation"]["description"] += (
        " with a changed scientific relation"
    )
    assert build_input_fingerprint(semantic_change) != baseline

    chain_order_change = copy.deepcopy(REQUEST)
    chain_order_change["mechanism_packet"]["mechanism_chains"][0]["edge_ids"].reverse()
    assert build_input_fingerprint(chain_order_change) != baseline


def test_scientific_content_is_retained_without_membership_or_semantic_rejection():
    request = copy.deepcopy(REQUEST)
    draft = copy.deepcopy(DRAFT)
    draft["hypotheses"][0]["semantic_hypothesis_judgment"] = "原始模型判断"
    draft["hypotheses"][0]["provenance_evidence_ids"] = ["模型关联记录"]
    result = guard.finalize(request, draft)
    assert result["hypotheses"][0]["semantic_hypothesis_judgment"] == "原始模型判断"
    assert result["hypotheses"][0]["provenance_evidence_ids"] == ["模型关联记录"]


def test_output_candidates_are_preserved_even_when_generation_limit_is_zero():
    request = copy.deepcopy(REQUEST)
    request["generation_config"] = {"max_hypotheses": 0}
    draft = copy.deepcopy(DRAFT)
    draft["hypotheses"] = [draft["hypotheses"][0], copy.deepcopy(draft["hypotheses"][0])]
    result = guard.finalize(request, draft)
    assert len(result["hypotheses"]) == 2
