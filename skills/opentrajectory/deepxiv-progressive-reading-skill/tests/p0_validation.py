from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from jsonschema import ValidationError, validate

SKILL_DIR = Path(__file__).resolve().parents[1]
ADAPTER = SKILL_DIR / "adapter" / "progressive_reading_adapter.py"


def load_adapter():
    spec = importlib.util.spec_from_file_location("p07b_progressive_reading_adapter", ADAPTER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def schema(name: str):
    return json.loads((SKILL_DIR / name).read_text(encoding="utf-8"))


def evidence_schema():
    return {
        "schema_id": "p07b-neutral",
        "schema_version": "0.1.0",
        "task_context": {
            "research_question": "What does the selected source report?",
            "task_scope": "identity and material validation",
            "domain_hints": [],
            "design_assumptions": [],
        },
        "evidence_targets": [{
            "target_id": "target_001",
            "target_type": "other",
            "scientific_role": "other",
            "description": "target information",
            "requiredness": "optional",
            "completion_criteria": "Locate explicit source content when present.",
            "allowed_evidence_forms": ["text"],
            "condition_dimensions": [],
            "comparison_requirements": [],
            "provenance_requirements": ["source anchor"],
        }],
        "gap_taxonomy": [],
        "uncertainty_rules": [],
    }


def canonical(candidate: dict, **config):
    payload = {
        "topk_selection": {"selected_for_p03": [candidate]},
        "evidence_schema": evidence_schema(),
    }
    if config:
        payload["reading_config"] = config
    return payload


class Reader:
    def __init__(self, observed: str, *, supplement: bool = False):
        self.observed = observed
        self.supplement = supplement

    def brief(self, _requested):
        return {"title": f"Paper {self.observed}", "arxiv_id": self.observed}

    def head(self, _requested):
        return {
            "title": f"Paper {self.observed}",
            "arxiv_id": self.observed,
            "sections": {"Results": {}},
        }

    def section(self, _requested, name):
        if self.supplement:
            return {
                "section_name": "Supplementary Information",
                "arxiv_id": self.observed,
                "material_type": "supplementary_material",
                "content": "Supplementary Information\n" + "supporting data " * 100,
            }
        return {
            "section_name": name,
            "arxiv_id": self.observed,
            "content": "Controlled methods and measured results. " * 60,
        }

    def raw(self, _requested):
        raise AssertionError("raw must remain opt-in")


def run():
    module = load_adapter()
    input_schema = schema("input.schema.json")
    output_schema = schema("output.schema.json")

    requested = "2401.12345v2"
    candidate = {"candidate_id": "paper-1", "arxiv_id": requested, "title": f"Paper {requested}"}

    success = module.progressive_read_candidates(canonical(candidate), reader=Reader(requested))
    validate(success, output_schema)
    packet = success["paper_packets"][0]
    assert success["status"] == "success"
    assert packet["material_status"] == "MATERIAL_READY"
    assert packet["requested_identity"]["arxiv_id"] == requested
    assert packet["provider_observed_identity"]["arxiv_id"] == requested
    assert packet["resolved_identity"]["arxiv_id"] == requested

    mismatch = module.progressive_read_candidates(canonical(candidate), reader=Reader("2401.12345v1"))
    validate(mismatch, output_schema)
    assert mismatch["status"] == "failed"
    assert mismatch["paper_packets"] == []
    assert mismatch["material_attempts"][0]["diagnostics"]["identity_basis"] == "arxiv_version_conflict"

    supplement = module.progressive_read_candidates(canonical(candidate), reader=Reader(requested, supplement=True))
    validate(supplement, output_schema)
    assert supplement["status"] == "failed"
    assert supplement["paper_packets"] == []
    assert supplement["material_attempts"][0]["reason"] == "material_type_not_admissible:supplementary_material"

    validate(canonical(candidate), input_schema)
    for legacy in ({"candidates": [candidate]}, {"selected_candidates": [candidate]}):
        try:
            validate(legacy, input_schema)
        except ValidationError:
            pass
        else:
            raise AssertionError("legacy input unexpectedly validates")

    many = [
        {"candidate_id": f"p{i}", "arxiv_id": f"2401.1234{i}", "title": f"Paper 2401.1234{i}"}
        for i in range(3)
    ]
    limited = module.progressive_read_candidates(
        {"topk_selection": {"selected_for_p03": many}, "evidence_schema": evidence_schema(), "reading_config": {"max_papers": 2}},
        reader=Reader("2401.12340"),
    )
    validate(limited, output_schema)
    assert limited["summary"]["input_candidate_count"] == 3
    assert limited["summary"]["attempted_candidate_count"] == 2
    assert limited["summary"]["truncated_candidate_count"] == 1

    validate(json.loads((SKILL_DIR / "examples" / "request.json").read_text()), input_schema)
    validate(json.loads((SKILL_DIR / "examples" / "response.json").read_text()), output_schema)

    print("P07B_OFFLINE_VALIDATION=PASS")
    print("P07B_PRODUCTION_IDENTITY_INTEGRITY=PASS")
    print("P07B_MATERIAL_TYPE_INTEGRITY=PASS")
    print("P07B_CANONICAL_INPUT_ONLY=PASS")
    print("P07B_RESOURCE_ACCOUNTING=PASS")


if __name__ == "__main__":
    run()
