from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path

import pytest
from jsonschema import Draft7Validator

ROOT = Path(__file__).resolve().parents[1]


def load_json(relative_path: str):
    return json.loads((ROOT / relative_path).read_text(encoding="utf-8"))


def load_guard():
    path = ROOT / "scripts" / "runtime_guard.py"
    spec = importlib.util.spec_from_file_location("candidate_runtime_guard", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.path.insert(0, str(ROOT))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(ROOT))
    return module


GUARD = load_guard()
INPUT_SCHEMA = load_json("input.schema.json")
DRAFT_SCHEMA = load_json("draft.schema.json")
OUTPUT_SCHEMA = load_json("output.schema.json")
REQUEST = load_json("examples/request.json")
DRAFT = load_json("examples/response.draft.json")
EXPECTED = load_json("examples/response.json")


def validate(schema, payload) -> None:
    Draft7Validator(schema).validate(payload)


def empty_request():
    request = copy.deepcopy(REQUEST)
    request["mechanism_packet"]["mechanism_edges"] = []
    request["mechanism_packet"]["mechanism_chains"] = []
    return request


def gap_edge(edge_id: str = "me:aaaaaaaaaaaaaaaaaaaa", evidence_id: str = "ev:gap-only"):
    edge = copy.deepcopy(REQUEST["mechanism_packet"]["mechanism_edges"][0])
    edge["edge_id"] = edge_id
    edge["edge_status"] = "gap"
    edge["evidence_ids"] = [evidence_id]
    edge["relation"]["predicate"] = "reveals_gap"
    edge["relation"]["description"] = "evidence is missing for the proposed relationship"
    return edge


def many_findings(count: int):
    findings = []
    for index in range(count):
        finding = copy.deepcopy(DRAFT["findings"][0])
        finding["statement"] = f"Supported finding variant {index}."
        findings.append(finding)
    return {"findings": findings, "generation_warnings": []}


def two_basis_request():
    request = copy.deepcopy(REQUEST)
    request["mechanism_packet"]["mechanism_edges"] = copy.deepcopy(
        request["mechanism_packet"]["mechanism_edges"][:2]
    )
    request["mechanism_packet"]["mechanism_edges"][0]["evidence_ids"] = ["ev:support-a"]
    request["mechanism_packet"]["mechanism_edges"][1]["evidence_ids"] = ["ev:support-b"]
    request["mechanism_packet"]["mechanism_chains"] = []
    return request


def test_present_mechanism_packet_still_requires_its_declared_schema():
    request = copy.deepcopy(REQUEST)
    request["mechanism_packet"].pop("mechanism_edges")

    with pytest.raises(GUARD.ContractRejected, match="input schema violation"):
        GUARD.finalize(request, {"findings": [], "generation_warnings": []})


def test_complete_basis_provenance_plus_gap_evidence_is_accepted():
    request = two_basis_request()
    gap = gap_edge()
    request["mechanism_packet"]["mechanism_edges"].append(gap)
    finding = copy.deepcopy(DRAFT["findings"][0])
    finding["basis_mechanism_edge_ids"] = [
        edge["edge_id"] for edge in request["mechanism_packet"]["mechanism_edges"][:2]
    ]
    finding["gap_mechanism_edge_ids"] = [gap["edge_id"]]
    finding["provenance_evidence_ids"] = ["ev:support-a", "ev:support-b", "ev:gap-only"]

    result = GUARD.finalize(request, {"findings": [finding], "generation_warnings": []})
    assert result["summary"]["degraded"] is False
    assert result["findings"][0]["provenance_evidence_ids"] == [
        "ev:gap-only",
        "ev:support-a",
        "ev:support-b",
    ]


def test_finalize_does_not_mutate_inputs():
    request = copy.deepcopy(REQUEST)
    draft = copy.deepcopy(DRAFT)
    request_before = copy.deepcopy(request)
    draft_before = copy.deepcopy(draft)
    GUARD.finalize(request, draft)
    assert request == request_before
    assert draft == draft_before


def test_scientific_content_is_retained_without_membership_or_semantic_rejection():
    request = copy.deepcopy(REQUEST)
    draft = copy.deepcopy(DRAFT)
    draft["findings"][0]["epistemic_status"] = "原始模型判断"
    draft["findings"][0]["provenance_evidence_ids"] = ["模型关联记录"]
    result = GUARD.finalize(request, draft)
    assert result["findings"][0]["epistemic_status"] == "原始模型判断"
    assert result["findings"][0]["provenance_evidence_ids"] == ["模型关联记录"]


def test_output_candidates_are_preserved_even_when_generation_limit_is_zero():
    request = copy.deepcopy(REQUEST)
    request["generation_config"] = {"max_findings": 0}
    draft = copy.deepcopy(DRAFT)
    draft["findings"] = [draft["findings"][0], copy.deepcopy(draft["findings"][0])]
    result = GUARD.finalize(request, draft)
    assert len(result["findings"]) == 2
