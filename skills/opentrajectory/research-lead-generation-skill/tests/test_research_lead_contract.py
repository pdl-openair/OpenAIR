from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path

import pytest
from jsonschema import Draft7Validator

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def load_json(relative_path: str):
    return json.loads((ROOT / relative_path).read_text(encoding="utf-8"))


def load_guard():
    path = SCRIPTS / "runtime_guard.py"
    spec = importlib.util.spec_from_file_location("research_lead_runtime_guard", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


GUARD = load_guard()
INPUT_SCHEMA = load_json("input.schema.json")
DRAFT_SCHEMA = load_json("draft.schema.json")
OUTPUT_SCHEMA = load_json("output.schema.json")
REQUEST = load_json("examples/request.json")
DRAFT = load_json("examples/response.draft.json")


def validate(schema, payload):
    Draft7Validator(schema).validate(payload)


def make_distinct_leads(count: int) -> list[dict]:
    leads = []
    for index in range(count):
        lead = copy.deepcopy(DRAFT["research_leads"][0])
        lead["title"] = f"Transferability question {index + 1}"
        lead["research_question"] = f"Which unresolved transferability boundary should be examined for comparison set {index + 1}?"
        lead["investigation_target"] = f"Transferability boundary {index + 1}."
        leads.append(lead)
    return leads


def test_present_mechanism_packet_still_requires_its_declared_schema():
    request = copy.deepcopy(REQUEST)
    request["mechanism_packet"].pop("mechanism_edges")

    with pytest.raises(GUARD.ContractRejected, match="input schema violation"):
        GUARD.finalize(request, DRAFT)


def test_research_lead_id_is_task_independent_but_content_deterministic():
    first = GUARD.finalize(REQUEST, DRAFT)
    request_with_new_task = copy.deepcopy(REQUEST)
    request_with_new_task["task_id"] = "task-lmb-demo-2"
    second = GUARD.finalize(request_with_new_task, DRAFT)

    assert first["research_leads"][0]["research_lead_id"] == second["research_leads"][0]["research_lead_id"]

    changed = copy.deepcopy(DRAFT)
    changed["research_leads"][0]["title"] = "A different research direction"
    third = GUARD.finalize(REQUEST, changed)
    assert first["research_leads"][0]["research_lead_id"] != third["research_leads"][0]["research_lead_id"]


def test_input_fingerprint_is_deterministic_and_tracks_task_input():
    first = GUARD.finalize(REQUEST, DRAFT)
    second = GUARD.finalize(copy.deepcopy(REQUEST), copy.deepcopy(DRAFT))
    assert first["audit"]["input_fingerprint"] == second["audit"]["input_fingerprint"]

    changed = copy.deepcopy(REQUEST)
    changed["task_id"] = "different-task"
    third = GUARD.finalize(changed, DRAFT)
    assert first["audit"]["input_fingerprint"] != third["audit"]["input_fingerprint"]


def test_scientific_content_is_retained_without_membership_or_semantic_rejection():
    request = copy.deepcopy(REQUEST)
    draft = copy.deepcopy(DRAFT)
    draft["research_leads"][0]["semantic_boundary_judgment"] = "原始模型判断"
    draft["research_leads"][0]["provenance_evidence_ids"] = ["模型关联记录"]
    result = GUARD.finalize(request, draft)
    assert result["research_leads"][0]["semantic_boundary_judgment"] == "原始模型判断"
    assert result["research_leads"][0]["provenance_evidence_ids"] == ["模型关联记录"]


def test_output_candidates_are_preserved_even_when_generation_limit_is_zero():
    request = copy.deepcopy(REQUEST)
    request["generation_config"] = {"max_research_leads": 0}
    draft = copy.deepcopy(DRAFT)
    draft["research_leads"] = [draft["research_leads"][0], copy.deepcopy(draft["research_leads"][0])]
    result = GUARD.finalize(request, draft)
    assert len(result["research_leads"]) == 2
