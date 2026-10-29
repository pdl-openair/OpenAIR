from __future__ import annotations

import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
for import_root in (ROOT / "services" / "api", ROOT / "packages" / "llm_core"):
    value = str(import_root)
    if value not in sys.path:
        sys.path.insert(0, value)

from app.execution.durable_skill_runtime import _validate_structured_json


NODE4_OUTPUT_SCHEMA = (
    ROOT
    / "skills"
    / "opentrajectory"
    / "evidence-schema-design-skill"
    / "output.schema.json"
)


def _node4_schema() -> dict:
    return json.loads(NODE4_OUTPUT_SCHEMA.read_text(encoding="utf-8"))






def test_structured_durable_output_accepts_valid_blocked_node4_result() -> None:
    value = {
        "status": "blocked",
        "blocking_reason": "The minimum comparison basis is not uniquely defined.",
        "missing_minimum_information": ["comparable evaluation protocol"],
        "missing_comparison_dimensions": [],
        "design_warnings": [],
    }
    assert _validate_structured_json(
        dict(value),
        _node4_schema(),
        "Node4 output",
        role="output",
    ) == value
