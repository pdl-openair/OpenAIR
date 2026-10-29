from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from jsonschema.validators import validator_for

ROOT = Path(__file__).resolve().parents[2]
INPUT_SCHEMA = json.loads((ROOT / "input.schema.json").read_text(encoding="utf-8"))
DRAFT_SCHEMA = json.loads((ROOT / "draft.schema.json").read_text(encoding="utf-8"))
OUTPUT_SCHEMA = json.loads((ROOT / "output.schema.json").read_text(encoding="utf-8"))

INPUT_VALIDATOR = validator_for(INPUT_SCHEMA)(INPUT_SCHEMA)
DRAFT_VALIDATOR = validator_for(DRAFT_SCHEMA)(DRAFT_SCHEMA)
OUTPUT_VALIDATOR = validator_for(OUTPUT_SCHEMA)(OUTPUT_SCHEMA)

_spec = importlib.util.spec_from_file_location(
    "pvd_candidate_direction_finalize",
    ROOT / "scripts" / "finalize.py",
)
assert _spec is not None and _spec.loader is not None
_finalize_module = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _finalize_module
_spec.loader.exec_module(_finalize_module)
finalize = _finalize_module.finalize


def _parameter(
    key: str,
    display_name: str,
    value_type: str = "continuous",
    unit: str | None = None,
    allowed_values: list[object] | None = None,
) -> dict[str, object]:
    return {
        "parameter_key": key,
        "display_name": display_name,
        "value_type": value_type,
        "unit": unit,
        "allowed_values": allowed_values or [],
        "rationale": "当前任务正式动态参数",
        "basis_refs": ["query:1"],
    }


def _context(parameters: list[dict[str, object]] | None = None) -> dict[str, object]:
    return {
        "structured_context": {
            "sources": [{"source_id": "query:1", "raw_text": "分析下一轮参数方向，不猜未知值。"}]
        },
        "recommendation_parameter_set": {
            "parameters": parameters
            or [
                _parameter("pulse_count", "脉冲次数", "integer", "次"),
                _parameter("target_type", "靶材类型", "categorical", None, ["A", "B"]),
                _parameter("bias_enabled", "是否开启偏压", "boolean"),
            ]
        },
        "run_preferences": {"initial_experiment_set_count": 2},
    }


def _assessment(
    factor: str,
    direction: str,
    *,
    role: str = "secondary_lever",
    uncertainty: str = "medium",
    evidence_refs: list[str] | None = None,
    diagnosis_refs: list[str] | None = None,
) -> dict[str, object]:
    return {
        "factor": factor,
        "candidate_direction": direction,
        "priority_role": role,
        "evidence_refs": evidence_refs or [],
        "diagnosis_refs": diagnosis_refs or [],
        "rationale": f"{factor} 的方向依据",
        "uncertainty": uncertainty,
    }


def _draft(parameters: list[dict[str, object]] | None = None) -> dict[str, object]:
    scope = parameters or list(_context()["recommendation_parameter_set"]["parameters"])
    assessments = []
    for index, parameter in enumerate(scope):
        direction = ["increase", "change", "uncertain", "hold", "decrease"][index % 5]
        role = "uncertain" if direction == "uncertain" else "secondary_lever"
        assessments.append(
            _assessment(
                str(parameter["parameter_key"]),
                direction,
                role=role,
                uncertainty="high" if direction == "uncertain" else "medium",
                evidence_refs=[f"evidence:{index}"] if direction != "uncertain" else [],
            )
        )
    return {
        "analysis_status": "complete",
        "factor_assessments": assessments,
        "recommended_next_experiment_focus": {
            "factor_refs": [str(scope[0]["parameter_key"])],
            "rationale": "优先验证信息价值最高的方向；不指定数值。",
        },
        "limitations": ["设备数值边界未知时保持未知"],
    }


def _run(
    draft: dict[str, object],
    *,
    parameters: list[dict[str, object]] | None = None,
    feedback_analysis: dict[str, object] | None = None,
):
    payload = {
        "current_context": _context(parameters),
        "evidence_applicability": {"records": [{"id": "evidence:0", "allowed_uses": ["direction"]}]},
        "feedback_analysis": feedback_analysis,
    }
    INPUT_VALIDATOR.validate(payload)
    DRAFT_VALIDATOR.validate(draft)
    result = finalize(payload, draft)
    if result.output is not None:
        OUTPUT_VALIDATOR.validate(result.output)
    return result


def test_dynamic_parameter_scope_is_covered_exactly_once() -> None:
    parameters = [
        _parameter("power", "功率", "continuous", "W"),
        _parameter("nitrogen_flow", "氮气流量", "continuous", "sccm"),
        _parameter("argon_flow", "氩气流量", "continuous", "sccm"),
        _parameter("deposition_time", "沉积时间", "continuous", "min"),
        _parameter("bias_enabled", "偏压开关", "boolean"),
    ]
    result = _run(_draft(parameters), parameters=parameters)
    assert result.status == "succeeded", result.errors
    assert result.output is not None
    assert [row["factor"] for row in result.output["factor_assessments"]] == [
        row["parameter_key"] for row in parameters
    ]
    assert result.output["audit"] == {
        "parameter_keys": [row["parameter_key"] for row in parameters],
        "scope_exact": True,
    }


def test_weak_evidence_can_remain_uncertain() -> None:
    draft = _draft()
    draft["factor_assessments"][2] = _assessment(
        "bias_enabled",
        "uncertain",
        role="uncertain",
        uncertainty="high",
        evidence_refs=[],
    )
    result = _run(draft)
    assert result.status == "succeeded"
    assert result.output is not None
    row = result.output["factor_assessments"][2]
    assert row["candidate_direction"] == "uncertain"
    assert row["uncertainty"] == "high"
    assert row["evidence_refs"] == []


def test_unknown_is_not_replaced_by_a_concrete_guess() -> None:
    draft = _draft()
    draft["factor_assessments"][0] = _assessment(
        "pulse_count",
        "uncertain",
        role="uncertain",
        uncertainty="high",
    )
    result = _run(draft)
    assert result.output is not None
    serialized = json.dumps(result.output, ensure_ascii=False)
    for forbidden in ("recommended_value", "setpoint", "exact_value", '"value"', '"recipe"', '"portfolio"'):
        assert forbidden not in serialized






def test_first_round_and_feedback_round_share_the_same_direction_contract() -> None:
    first = _run(_draft())
    feedback_draft = _draft()
    feedback_draft["factor_assessments"][0]["diagnosis_refs"] = ["diagnosis:run-1"]
    feedback = _run(feedback_draft, feedback_analysis={"diagnosis_id": "diagnosis:run-1"})

    assert first.output is not None and feedback.output is not None
    assert first.output["analysis_basis"]["history_mode"] == "R0"
    assert feedback.output["analysis_basis"]["history_mode"] == "feedback"
    assert set(first.output) == set(feedback.output)
    assert [set(row) for row in first.output["factor_assessments"]] == [
        set(row) for row in feedback.output["factor_assessments"]
    ]


def test_direction_basis_references_remain_traceable() -> None:
    draft = _draft()
    draft["factor_assessments"][0] = _assessment(
        "pulse_count",
        "increase",
        evidence_refs=["evidence:record-7"],
        diagnosis_refs=["diagnosis:run-2"],
    )
    result = _run(draft, feedback_analysis={"diagnosis_id": "diagnosis:run-2"})
    assert result.output is not None
    row = result.output["factor_assessments"][0]
    assert row["evidence_refs"] == ["evidence:record-7"]
    assert row["diagnosis_refs"] == ["diagnosis:run-2"]
