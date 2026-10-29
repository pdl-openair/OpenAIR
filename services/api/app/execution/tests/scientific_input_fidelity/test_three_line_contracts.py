from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest
from app.execution.durable_skill_runtime import _validate_structured_json

ROOT = Path(__file__).resolve().parents[6]


@pytest.mark.parametrize(
    ("skill_path", "value", "has_gap"),
    [
        (
            "opentrajectory/trajectory-planning-eval",
            {
                "user_goal": "回答风格偏差",
                "candidates": [{"id": "observed", "description": "已有科研材料"}],
                "constraints": ["不虚构测量"],
                "context": {"unknown": None, "measured_count": 0, "causal": False},
            },
            False,
        ),
        (
            "cs_research/gap-analysis",
            {
                "source_materials": [
                    {
                        "material_id": "observed",
                        "material_type": "unrecognized",
                        "content": "已有证据正文",
                    }
                ],
                "gap_focus_types": ["reproducibility_gap"],
                "extra_context": {"count": 0, "comparable": False},
            },
            False,
        ),
        (
            "cs_research/actual-metric-records-closure",
            {
                "reproduction_experiment_design": {},
                "execution_readiness_record": {},
                "frozen_execution_input": {},
                "raw_actual_metrics": {"value": 0, "successful": False},
                "unrecognized_context": "保留但不冒充实测记录",
            },
            False,
        ),
    ],
)
def test_actual_neighbor_contracts_retain_scientific_content(skill_path, value, has_gap) -> None:
    contract = json.loads(
        (ROOT / "skills" / skill_path / "input.schema.json").read_text(encoding="utf-8")
    )
    before = deepcopy(value)
    warnings: list[str] = []
    received = _validate_structured_json(
        value, contract, "neighbor input", role="input", input_warnings=warnings
    )
    assert value == received == before
    assert bool(warnings) == has_gap
