from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from jsonschema import validate

SKILL_DIR = Path(__file__).resolve().parents[1]
ADAPTER = SKILL_DIR / "adapter" / "progressive_reading_adapter.py"

spec = importlib.util.spec_from_file_location("deepxiv_progressive_reading_live", ADAPTER)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

payload = {
    "topk_selection": {
        "selected_for_p03": [
            {
                "candidate_id": "live:2409.05591",
                "arxiv_id": "2409.05591",
                "title": "",
            }
        ]
    },
    "evidence_schema": {
        "schema_id": "live-progressive-reading",
        "schema_version": "0.1.0",
        "task_context": {
            "research_question": "What methods and results does the selected source report?",
            "task_scope": "live smoke",
            "domain_hints": [],
            "design_assumptions": []
        },
        "evidence_targets": [{
            "target_id": "target_001",
            "target_type": "other",
            "scientific_role": "other",
            "description": "reported results",
            "requiredness": "optional",
            "completion_criteria": "Locate explicit source content when present.",
            "allowed_evidence_forms": ["text"],
            "condition_dimensions": [],
            "comparison_requirements": [],
            "provenance_requirements": ["source anchor"]
        }],
        "gap_taxonomy": [],
        "uncertainty_rules": []
    },
    "reading_config": {
        "max_papers": 1,
        "max_sections_per_paper": 3,
        "allow_raw_fallback": False,
    },
}
output = module.progressive_read_candidates(payload)
schema = json.loads((SKILL_DIR / "output.schema.json").read_text(encoding="utf-8"))
validate(output, schema)
summary = output["summary"]
assert output["status"] in {"success", "degraded"}, output
assert summary["ready_packet_count"] >= 1, output
assert summary["section_count"] >= 1, output
assert summary["source_anchor_count"] >= 1, output
assert output["audit"]["raw_call_count"] == 0, output
packet = output["paper_packets"][0]
assert packet["provider_observed_identity"] is not None, output
assert packet["resolved_identity"] is not None, output
print("P07B_LIVE_SMOKE=PASS")
print(f"LIVE_READY_PACKET_COUNT={summary['ready_packet_count']}")
print(f"LIVE_SECTION_COUNT={summary['section_count']}")
print(f"LIVE_SOURCE_ANCHOR_COUNT={summary['source_anchor_count']}")
print(f"RAW_DEFAULT_CALL_COUNT={output['audit']['raw_call_count']}")
