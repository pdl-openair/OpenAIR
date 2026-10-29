from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import yaml

from app.execution.provider_visible_skill_input import (
    SCIENTIFIC_MINIMAL_V1,
    build_scientific_minimal_user_payload,
    project_provider_parameters,
    resolve_provider_message_contract,
)


ROOT = Path(__file__).resolve().parents[6]
OT = ROOT / "skills" / "opentrajectory"


def _yaml(path: Path) -> dict:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _skill(skill_dir: str) -> tuple[dict, str]:
    root = OT / skill_dir
    return _yaml(root / "skill.yaml"), (root / "SKILL.md").read_text(encoding="utf-8")


def _problem_views(query: str, *, domain: str) -> dict:
    if domain == "materials":
        return {
            "intent_understanding": {
                "original_user_query": query,
                "research_object": "锂金属电池高电压快充条件下的醚类电解液设计",
                "target_metrics": ["库伦效率", "循环寿命", "界面稳定性"],
                "boundary_conditions": ["4.5V 以上", "2C 以上"],
                "ambiguities": ["具体锂盐未知", "测试温度未知"],
            },
            "constraints_criteria": {
                "interpreted_constraints": [
                    {
                        "constraint_name": "voltage",
                        "original_phrase": "4.5V 以上",
                        "interpreted_value": "4.5V 以上",
                        "semantic_state": "confirmed",
                    },
                    {
                        "constraint_name": "temperature",
                        "original_phrase": "未提供测试温度",
                        "interpreted_value": None,
                        "semantic_state": "unknown",
                    },
                ],
                "missing_information": [{
                    "information": "测试温度",
                    "why_needed": "温度会影响性能结果的可比性。",
                    "affected_constraint_names": ["temperature"],
                }],
                "research_risks": [],
            },
        }
    return {
        "intent_understanding": {
            "original_user_query": query,
            "research_object": "面向长任务软件开发的 AI Agent",
            "target_metrics": ["任务成功率", "稳定性"],
            "boundary_conditions": ["benchmark 或可复现实验优先"],
            "ambiguities": ["具体 benchmark 未指定"],
        },
        "constraints_criteria": {
            "interpreted_constraints": [
                {
                    "constraint_name": "reproducibility",
                    "original_phrase": "最好有 benchmark 或可复现实验",
                    "interpreted_value": "偏好 benchmark 或可复现实验",
                    "semantic_state": "confirmed",
                }
            ],
            "missing_information": [{
                "information": "具体 benchmark",
                "why_needed": "明确 benchmark 才能冻结比较边界。",
                "affected_constraint_names": ["reproducibility"],
            }],
            "research_risks": [],
        },
    }


def test_t02_three_front_chain_skills_explicitly_opt_into_scientific_minimal_view() -> None:
    for skill_dir in (
        "problem-framing-generation",
        "retrieval-matrix-generation",
        "evidence-schema-design-skill",
    ):
        metadata, _ = _skill(skill_dir)
        assert resolve_provider_message_contract(metadata) == SCIENTIFIC_MINIMAL_V1


def test_scientific_minimal_payload_contains_only_instructions_and_science() -> None:
    metadata, instructions = _skill("problem-framing-generation")
    scientific_input = {"user_query": "倍率未知，不要假设；正极材料体系不限。"}

    payload = build_scientific_minimal_user_payload(
        instructions=instructions,
        scientific_input=project_provider_parameters(metadata, scientific_input),
    )

    assert set(payload) == {"instructions", "scientificInput"}
    assert payload["scientificInput"] == scientific_input
    assert not payload["instructions"].startswith("---")
    assert "# 研究问题建模生成" in payload["instructions"]
    serialized = json.dumps(payload, ensure_ascii=False)
    for platform_field in (
        "skillMetadata",
        "runtimeContract",
        "frozenGoal",
        "frozenConstraints",
        "controllerInputs",
        "responseRequirement",
    ):
        assert platform_field not in serialized


def test_materials_and_cs_user_queries_reach_problem_framing_exactly() -> None:
    metadata, instructions = _skill("problem-framing-generation")
    cases = _json(
        OT / "problem-framing-generation" / "tests" / "t01_contract_cases.json"
    )["cases"]
    by_id = {row["case_id"]: row for row in cases}

    for case_id in ("materials_explicit_constraints", "cs_long_task_agent"):
        query = by_id[case_id]["user_query"]
        canonical = {"user_query": query}
        payload = build_scientific_minimal_user_payload(
            instructions=instructions,
            scientific_input=project_provider_parameters(metadata, canonical),
        )
        assert payload["scientificInput"] == canonical


def test_retrieval_formal_problem_views_keep_constraints_unknowns_and_conflicts_verbatim() -> None:
    metadata, instructions = _skill("retrieval-matrix-generation")
    canonical = {
        "intent_understanding": {
            "original_user_query": "筛选测试温度不超过 25°C；同时要求至少 60°C。倍率未知。",
            "ambiguities": ["倍率未知"],
        },
        "constraints_criteria": {
            "interpreted_constraints": [
                {"original_phrase": "不超过 25°C", "semantic_state": "conflicting"},
                {"original_phrase": "至少 60°C", "semantic_state": "conflicting"},
                {"original_phrase": "倍率未知", "semantic_state": "unknown"},
            ],
            "missing_information": [{"information": "倍率", "why_needed": "倍率影响检索边界。", "affected_constraint_names": []}],
            "research_risks": [{"risk": "温度条件冲突", "downstream_impact": "无法形成一致过滤边界。", "affected_constraint_names": []}],
        },
    }
    before = deepcopy(canonical)

    payload = build_scientific_minimal_user_payload(
        instructions=instructions,
        scientific_input=project_provider_parameters(metadata, canonical),
    )

    assert canonical == before
    assert payload["scientificInput"] == before


def test_evidence_schema_hides_execution_identity_and_expanded_queries_only_from_model_view() -> None:
    metadata, instructions = _skill("evidence-schema-design-skill")
    retrieval = _json(OT / "retrieval-matrix-generation" / "examples" / "response.json")
    query = "高电压和快充条件下如何设计锂金属电池电解液？"
    canonical = {
        "task_id": "task-materials-001",
        **_problem_views(query, domain="materials"),
        "retrieval_strategy_design": retrieval,
        "design_config": {"max_targets": 12},
    }
    before = deepcopy(canonical)

    visible = project_provider_parameters(metadata, canonical)
    payload = build_scientific_minimal_user_payload(
        instructions=instructions,
        scientific_input=visible,
    )

    assert canonical == before
    assert "task_id" not in visible
    assert visible["design_config"] == before["design_config"]
    assert visible["intent_understanding"] == before["intent_understanding"]
    assert visible["constraints_criteria"] == before["constraints_criteria"]
    assert visible["retrieval_strategy_design"] == {
        key: before["retrieval_strategy_design"][key]
        for key in ("search_matrix", "quality_report", "assembled_facets")
    }
    assert "expanded_query_variants" not in visible["retrieval_strategy_design"]
    assert before["task_id"] == "task-materials-001"
    assert before["retrieval_strategy_design"]["expanded_query_variants"]
    assert payload["scientificInput"] == visible


def test_evidence_schema_projection_is_domain_neutral_for_cs() -> None:
    metadata, _ = _skill("evidence-schema-design-skill")
    retrieval = _json(OT / "retrieval-matrix-generation" / "examples" / "response.json")
    query = "面向长任务软件开发的 AI Agent 中，记忆、工具调用和反思如何影响成功率与稳定性？"
    canonical = {
        "task_id": "task-cs-001",
        **_problem_views(query, domain="cs"),
        "retrieval_strategy_design": retrieval,
    }
    visible = project_provider_parameters(metadata, canonical)

    assert visible["intent_understanding"]["original_user_query"] == query
    assert visible["constraints_criteria"] == canonical["constraints_criteria"]
    assert set(visible["retrieval_strategy_design"]) == {
        "search_matrix",
        "quality_report",
        "assembled_facets",
    }


def test_t02_scientific_minimal_payload_is_smaller_than_legacy_envelope_for_materials_and_cs() -> None:
    cases = _json(
        OT / "problem-framing-generation" / "tests" / "t01_contract_cases.json"
    )["cases"]
    by_id = {row["case_id"]: row for row in cases}

    for case_id in ("materials_explicit_constraints", "cs_long_task_agent"):
        metadata, instructions = _skill("problem-framing-generation")
        parameters = {"user_query": by_id[case_id]["user_query"]}
        minimal = build_scientific_minimal_user_payload(
            instructions=instructions,
            scientific_input=project_provider_parameters(metadata, parameters),
        )
        legacy = {
            "selectedSkill": {
                "skillId": "problem_framing_generation",
                "skillMetadata": metadata,
                "instructions": instructions,
            },
            "frozenGoal": parameters["user_query"],
            "frozenConstraints": [],
            "controllerInputs": [],
            "selectedParameters": parameters,
            "runtimeContract": {
                "kind": "llm_reasoning",
                "requiresExternalAction": False,
                "adapterId": "huiyan_chat_completions",
                "evidenceContractVersion": "none",
            },
            "responseRequirement": "Return one valid json object only.",
            "outputContract": {"resultKind": "problem_framing"},
        }
        minimal_bytes = len(
            json.dumps(minimal, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        )
        legacy_bytes = len(
            json.dumps(legacy, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        )
        assert minimal_bytes < legacy_bytes
