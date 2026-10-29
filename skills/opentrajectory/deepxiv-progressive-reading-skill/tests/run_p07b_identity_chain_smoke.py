from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load(name: str, relative_path: str):
    path = ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


scoring = load("scoring", "candidate-source-scoring/adapter/scoring_adapter.py")
tiering = load("tiering", "candidate-tiering/adapter/tiering_adapter.py")
topk = load("topk", "topk-source-selection/adapter/topk_adapter.py")
deepread = load("deepread", "deepxiv-progressive-reading-skill/adapter/progressive_reading_adapter.py")


class FakeReader:
    def brief(self, arxiv_id):
        return {"title": "Lithium metal fast charging electrolyte interface mechanism", "tldr": "Useful", "arxiv_id": arxiv_id}

    def head(self, arxiv_id):
        return {"title": "Lithium metal fast charging electrolyte interface mechanism", "arxiv_id": arxiv_id, "sections": {"Methods": {"token_count": 10}, "Results": {"token_count": 20}}}

    def section(self, arxiv_id, name):
        return {"section_name": name, "arxiv_id": arxiv_id, "content": (f"{name} section for {arxiv_id}. " * 40)}

    def raw(self, arxiv_id):
        raise AssertionError("raw must not be called")


def main():
    deduped = [{
        "candidate_id": "candidate:1",
        "dedupe_key": "arxiv:2409.05591",
        "title": "Lithium metal fast charging electrolyte interface mechanism",
        "abstract": "Lithium metal fast charging electrolyte interface mechanism cycling capacity",
        "source_name": "arxiv",
        "external_ids": {"arxiv_id": "2409.05591"},
        "arxiv_id": "2409.05591",
        "source_url": "https://arxiv.org/pdf/2409.05591.pdf",
        "landing_page_url": "https://arxiv.org/abs/2409.05591",
        "provenance": [{"source_name": "arxiv", "matched_query": "lithium metal fast charging"}],
        "citation_count": 10,
    }]
    facets = {
        "core_entities": ["lithium metal"],
        "research_object_en": ["electrolyte"],
        "mechanisms": ["interface mechanism"],
        "target_metrics_en": ["cycling capacity"],
        "task_context": ["fast charging"],
        "must_have_terms": ["electrolyte"],
        "negative_contexts": ["sodium ion"],
    }
    scored = scoring.score_candidates({"deduped_candidates": deduped, "facets": facets})
    assert scored["candidate_scores"][0]["arxiv_id"] == "2409.05591"
    tiered = tiering.tier_candidates({"candidate_scores": scored["candidate_scores"]})
    assert tiered["source_tiers"][0]["arxiv_id"] == "2409.05591"
    selected = topk.build_topk_selection({"source_tiers": tiered["source_tiers"], "topk_config": {"core_min": 1, "dynamic_target": 1, "dynamic_cap": 1}})
    top_row = selected["topk_selection"]["selected_for_p03"][0]
    assert top_row["arxiv_id"] == "2409.05591"
    assert top_row["external_ids"]["arxiv_id"] == "2409.05591"
    selected["evidence_schema"] = {
        "schema_id": "identity-chain",
        "schema_version": "0.1.0",
        "task_context": {
            "research_question": "What does the selected source report about cycling capacity?",
            "task_scope": "identity chain smoke",
            "domain_hints": [],
            "design_assumptions": []
        },
        "evidence_targets": [{
            "target_id": "target_001",
            "target_type": "performance_metric",
            "scientific_role": "metric",
            "description": "cycling capacity",
            "requiredness": "optional",
            "completion_criteria": "Locate explicit source content when present.",
            "allowed_evidence_forms": ["text"],
            "condition_dimensions": [],
            "comparison_requirements": [],
            "provenance_requirements": ["source anchor"]
        }],
        "gap_taxonomy": [],
        "uncertainty_rules": []
    }
    packets = deepread.progressive_read_candidates(selected, reader=FakeReader())
    assert packets["summary"]["paper_packet_count"] == 1
    assert packets["paper_packets"][0]["arxiv_id"] == "2409.05591"
    assert packets["summary"]["source_anchor_count"] >= 1
    assert packets["audit"]["raw_call_count"] == 0
    print("P07B_IDENTITY_CHAIN_SMOKE=PASS")
    print("CHAIN=scoring->tiering->topk->deepxiv_progressive_reading")
    print("ARXIV_ID_PRESERVED=2409.05591")
    print("RAW_DEFAULT_CALL_COUNT=0")


if __name__ == "__main__":
    main()
