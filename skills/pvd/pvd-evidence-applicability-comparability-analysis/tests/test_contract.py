from __future__ import annotations

import json
from pathlib import Path

from jsonschema.validators import validator_for

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_SCHEMA = json.loads((ROOT / "output.schema.json").read_text(encoding="utf-8"))
VALIDATOR = validator_for(OUTPUT_SCHEMA)(OUTPUT_SCHEMA)


def _output() -> dict[str, object]:
    return {
        "analysis_status": "complete",
        "evidence_assessments": [
            {
                "record_id": "record:1",
                "source_id": "source:1",
                "evidence_refs": ["evidence:1"],
                "key_matches": ["同类沉积方法"],
                "important_differences": ["基底不同"],
                "measurement_compatibility": "partially_compatible",
                "transfer_limits": ["仅支持趋势判断"],
                "uncertainty": ["靶基距未报告"],
            }
        ],
        "support_conclusions": [
            {
                "conclusion_id": "support_conclusion:target_001",
                "judgment_target": "target_001",
                "evidence_record_refs": ["record:1"],
                "evidence_refs": ["evidence:1"],
                "support_level": "directional",
                "allowed_uses": ["trend_direction"],
                "conditions": ["不直接迁移具体数值"],
                "transfer_limits": ["不直接迁移具体数值"],
                "conflicts": [],
                "important_unknowns": ["靶基距未知"],
                "rationale": "体系存在关键差异",
            }
        ],
        "conflicts": [],
        "important_unknowns": ["靶基距未知"],
        "summary": "该记录可支持方向判断，不支持具体参数照搬。",
    }


def test_comparability_contract_keeps_explicit_downstream_use_authorization() -> None:
    payload = _output()
    VALIDATOR.validate(payload)
    assert payload["support_conclusions"][0]["allowed_uses"] == ["trend_direction"]
