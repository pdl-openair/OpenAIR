from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]


def _load_finalizer(skill_dir: str, module_name: str) -> Any:
    path = REPO_ROOT / "skills" / "pvd" / skill_dir / "scripts" / "finalize.py"
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.finalize


candidate_finalize = _load_finalizer(
    "pvd-process-factor-candidate-experiment-analysis",
    "pvd_v2_candidate_finalize",
)
feasibility_finalize = _load_finalizer(
    "pvd-parameter-experiment-plan-feasibility-validation",
    "pvd_v2_feasibility_finalize",
)
recommendation_finalize = _load_finalizer(
    "pvd-round-experiment-plan-parameter-recommendation",
    "pvd_v2_recommendation_finalize",
)


def _current_context() -> dict[str, object]:
    return {
        "structured_context": {
            "sources": [
                {
                    "source_id": "query:1",
                    "source_type": "query",
                    "raw_text": "研究脉冲次数、靶材类型与偏压是否开启。",
                }
            ]
        },
        "recommendation_parameter_set": {
            "parameters": [
                {
                    "parameter_key": "pulse_count",
                    "display_name": "脉冲次数",
                    "value_type": "integer",
                    "unit": "次",
                    "unit_identity": "count:cycle",
                    "unit_dimension": "count",
                    "allowed_values": [],
                    "rationale": "当前任务参数",
                    "basis_refs": ["query:1"],
                },
                {
                    "parameter_key": "target_type",
                    "display_name": "靶材类型",
                    "value_type": "categorical",
                    "unit": None,
                    "allowed_values": ["A", "B"],
                    "rationale": "当前任务参数",
                    "basis_refs": ["query:1"],
                },
                {
                    "parameter_key": "bias_enabled",
                    "display_name": "是否开启偏压",
                    "value_type": "boolean",
                    "unit": None,
                    "allowed_values": [],
                    "rationale": "当前任务参数",
                    "basis_refs": ["query:1"],
                },
            ]
        },
        "run_preferences": {
            "initial_experiment_set_count": 2,
            "source": "user_query",
            "requested_count": 2,
            "capped_to_maximum": False,
        },
    }


def test_core_chain_preserves_concrete_candidate_settings_until_final_recommendation() -> None:
    context = _current_context()
    candidate_result = candidate_finalize(
        {
            "current_context": context,
            "evidence_applicability": None,
            "feedback_analysis": None,
        },
        {
            "analysis_status": "complete",
            "directions": [
                {
                    "parameter_key": "pulse_count",
                    "direction": "increase",
                    "priority_role": "primary_lever",
                    "rationale": "验证脉冲次数影响",
                    "uncertainty": "上限未知",
                },
                {
                    "parameter_key": "target_type",
                    "direction": "change",
                    "priority_role": "secondary_lever",
                    "rationale": "比较两种靶材",
                    "uncertainty": "材料耦合仍需验证",
                },
                {
                    "parameter_key": "bias_enabled",
                    "direction": "explore",
                    "priority_role": "exploratory",
                    "rationale": "验证偏压作用",
                    "uncertainty": "设备边界未知",
                },
            ],
            "candidates": [
                {
                    "candidate_id": "candidate-1",
                    "priority": 1,
                    "experiment_intent": "基线附近探索",
                    "validation_goal": "判断主参数响应",
                    "rationale": "优先获得高价值信息",
                    "falsification_signal": "目标指标反向变化",
                    "parameters": [
                        {"parameter_key": "pulse_count", "value": 12},
                        {"parameter_key": "target_type", "value": "A"},
                        {"parameter_key": "bias_enabled", "value": True},
                    ],
                    "maintained_conditions": ["保持其他条件不变"],
                    "combination_constraints": [],
                },
                {
                    "candidate_id": "candidate-2",
                    "priority": 2,
                    "experiment_intent": "对照探索",
                    "validation_goal": "检查材料与偏压耦合",
                    "rationale": "形成可解释对照",
                    "falsification_signal": "两组结果无可解释差异",
                    "parameters": [
                        {"parameter_key": "pulse_count", "value": 16},
                        {"parameter_key": "target_type", "value": "B"},
                        {"parameter_key": "bias_enabled", "value": False},
                    ],
                    "maintained_conditions": ["保持其他条件不变"],
                    "combination_constraints": [],
                },
            ],
            "limitations": ["设备数值边界尚未提供"],
        },
    )
    assert candidate_result.status == "succeeded", candidate_result.errors
    assert candidate_result.output is not None

    candidates = candidate_result.output["candidates"]
    feasibility_result = feasibility_finalize(
        {
            "current_context": context,
            "candidate_analysis": candidate_result.output,
        },
        {
            "assessments": [
                {
                    "candidate_id": "candidate-1",
                    "decision": "release",
                    "rationale": "当前已知条件可执行",
                    "conditions": [],
                    "conflicts": [],
                    "risks": [],
                    "unknowns": [],
                },
                {
                    "candidate_id": "candidate-2",
                    "decision": "conditional_release",
                    "rationale": "确认设备状态后可执行",
                    "conditions": ["执行前确认设备状态"],
                    "conflicts": [],
                    "risks": ["设备状态可能变化"],
                    "unknowns": [],
                },
            ],
            "shared_conditions": ["统一测量口径"],
            "cross_candidate_constraints": ["同一基底批次"],
            "shared_risks": [],
            "important_unknowns": ["设备长期稳定性未知"],
        },
    )
    assert feasibility_result.status == "succeeded", feasibility_result.errors
    assert feasibility_result.output is not None
    assert feasibility_result.output["assessments"][0]["candidate"] == candidates[0]
    assert feasibility_result.output["assessments"][1]["candidate"] == candidates[1]

    recommendation_result = recommendation_finalize(
        {
            "current_context": context,
            "candidate_analysis": candidate_result.output,
            "feasibility_validation": feasibility_result.output,
        },
        {
            "selection_status": "conditional",
            "selected_experiments": [
                {
                    "candidate_id": "candidate-1",
                    "priority": 1,
                    "selection_rationale": "优先执行直接放行组",
                },
                {
                    "candidate_id": "candidate-2",
                    "priority": 2,
                    "selection_rationale": "满足前提后执行条件组",
                },
            ],
            "limited_resource_priority_candidate_ids": ["candidate-1"],
            "direction_change_signals": ["目标指标连续反向变化"],
            "next_step": "用真实执行与测量结果更新下一轮判断",
            "important_unknowns": ["测量重复性待确认"],
        },
    )
    assert recommendation_result.status == "succeeded", recommendation_result.errors
    assert recommendation_result.output is not None

    experiments = recommendation_result.output["experiments"]
    assert experiments[0]["candidate"] == candidates[0]
    assert experiments[1]["candidate"] == candidates[1]
    assert [row["parameter_key"] for row in experiments[0]["candidate"]["parameters"]] == [
        "pulse_count",
        "target_type",
        "bias_enabled",
    ]
    assert [row["value"] for row in experiments[0]["candidate"]["parameters"]] == [
        12,
        "A",
        True,
    ]
