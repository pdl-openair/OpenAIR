from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
REPO = ROOT.parents[2]


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    import sys
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _packet() -> dict:
    full_content = "The AlScN film used 500 W target power and 20 sccm nitrogen flow. " * 180
    content_hash = hashlib.sha256(full_content.encode("utf-8")).hexdigest()
    return {
        "candidate_id": "paper-1",
        "title": "PVD paper",
        "doi": "10.test/1",
        "arxiv_id": "",
        "sections": [{
            "section_name": "Methods",
            "content": full_content,
            "content_hash": content_hash,
        }],
        "source_anchors": [{
            "anchor_id": "anchor-1",
            "candidate_id": "paper-1",
            "arxiv_id": "",
            "section_name": "Methods",
            "content_hash": content_hash,
        }],
        "finalizer_only": "must-stay-canonical",
    }


def test_407b_projection_is_bounded_while_canonical_packet_remains_full() -> None:
    closure = _load_module(
        REPO / "skills/pvd/pvd-literature-evidence-acquisition-closure/adapter/literature_evidence_closure.py",
        "t04_projection_contract_closure",
    )
    packet = _packet()
    projected = closure._model_paper_packet(packet)
    assert set(projected) == {"candidate_id", "title", "doi", "arxiv_id", "anchored_sections"}
    assert projected["anchored_sections"][0]["source_anchor_id"] == "anchor-1"
    assert sum(len(row["content"]) for row in projected["anchored_sections"]) <= 8000

    canonical = closure._evidence_skill_packet(packet)
    assert canonical["sections"] == packet["sections"]
    assert canonical["source_anchors"] == packet["source_anchors"]
    assert canonical["finalizer_only"] == "must-stay-canonical"
    assert canonical["anchored_sections"] == projected["anchored_sections"]


def test_registered_provider_projection_hides_full_source_from_model() -> None:
    projection = _load_module(
        REPO / "services/api/app/execution/provider_visible_skill_input.py",
        "t04_projection_contract_runtime",
    )
    metadata = yaml.safe_load((ROOT / "skill.yaml").read_text(encoding="utf-8"))
    packet = _packet()
    packet["anchored_sections"] = [{
        "source_anchor_id": "anchor-1",
        "section_name": "Methods",
        "content": packet["sections"][0]["content"][:8000],
    }]
    parameters = {
        "task_id": "task-1",
        "paper_packets": [packet],
        "evidence_schema": {"evidence_targets": []},
    }
    provider = projection.project_provider_parameters(metadata, parameters)
    visible = provider["paper_packets"][0]
    assert set(visible) == {"candidate_id", "title", "doi", "arxiv_id", "anchored_sections"}
    assert "sections" not in visible
    assert "source_anchors" not in visible
    assert parameters["paper_packets"][0]["finalizer_only"] == "must-stay-canonical"


def test_full_canonical_source_finalizes_model_draft_from_bounded_view() -> None:
    finalizer = _load_module(ROOT / "scripts/finalize.py", "t04_projection_contract_finalizer")
    packet = _packet()
    packet["anchored_sections"] = [{
        "source_anchor_id": "anchor-1",
        "section_name": "Methods",
        "content": packet["sections"][0]["content"][:8000],
    }]
    draft = {
        "evidence_items": [{
            "source_anchor_id": "anchor-1",
            "evidence_type": "experimental_condition",
            "assertion_mode": "direct_observation",
            "claim": "The target power was 500 W.",
            "evidence_quote": "The AlScN film used 500 W target power",
            "conditions": [{"name": "power", "value_text": "500", "unit": "W"}],
            "metric": None,
            "mechanism": None,
            "comparison": None,
            "qualifiers": [],
            "confidence": 0.95,
            "matched_target_ids": ["target_001"],
            "target_match_status": "matched",
        }],
    }
    result = finalizer.finalize(
        {
            "task_id": "task-1",
            "paper_packets": [packet],
            "evidence_schema": {
                "evidence_targets": [{"target_id": "target_001", "requiredness": "required"}],
            },
        },
        draft,
    )
    assert result.status == "succeeded"
    assert result.output is not None
    row = result.output["evidence_items"][0]
    assert row["candidate_id"] == "paper-1"
    assert row["content_hash"] == packet["sections"][0]["content_hash"]
