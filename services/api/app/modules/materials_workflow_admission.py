"""Workflow admission for Materials workflows.

Two admitted workflows share this module:

- ``materials_candidate_discovery_workflow`` — the bounded, executable
  discovery prefix ``MP constraint search -> formula crossover``.
- ``materials_full_discovery_workflow`` — the full-chain pipeline
  (literature review -> database search -> candidate generation ->
  synthesis assessment -> parameter recommendation -> DFT validation ->
  wet lab -> report generation). The deterministic design plans the fixed
  sub-chain database_search -> candidate_generation -> stability_verification
  -> report_generation.
"""

from __future__ import annotations

import re
from app.modules.workflow_planning_admission import WorkflowPlanningAdmission


MATERIALS_WORKFLOW_ID = "materials_candidate_discovery_workflow"
MATERIALS_SKILL_PACK = "materials"
MATERIALS_FULL_DISCOVERY_WORKFLOW_ID = "materials_full_discovery_workflow"

_MATERIAL_SIGNAL = re.compile(
    r"(?:band\s*gap|bandgap|ehull|formation\s*energy|oxide|perovskite|"
    r"semiconductor|crystal|materials?\s+(?:design|discovery)|"
    r"带隙|禁带|氧化物|钙钛矿|半导体|晶体|材料(?:设计|发现))",
    re.IGNORECASE,
)
_DISCOVERY_SIGNAL = re.compile(
    r"(?:find|search|discover|generate|evolve|candidate|mp\b|materials\s+project|"
    r"寻找|检索|筛选|发现|生成|演化|候选|材料项目)",
    re.IGNORECASE,
)


def is_materials_candidate_discovery_request(raw_input: str) -> bool:
    """Admit only requests that name both a materials constraint and discovery action."""
    value = raw_input or ""
    chinese_material = any(token in value for token in (
        "\u5e26\u9699", "\u7981\u5e26", "\u6c27\u5316\u7269", "\u9499\u949b\u77ff",
        "\u534a\u5bfc\u4f53", "\u6676\u4f53", "\u6750\u6599",
    ))
    chinese_discovery = any(token in value for token in (
        "\u5bfb\u627e", "\u641c\u7d22", "\u7b5b\u9009", "\u53d1\u73b0",
        "\u751f\u6210", "\u6f14\u5316", "\u5019\u9009",
    ))
    return bool(_MATERIAL_SIGNAL.search(value) and _DISCOVERY_SIGNAL.search(value)) or (
        chinese_material and chinese_discovery
    )


def materials_workflow_planning_admission(
    raw_input: str,
    workflow_id: str = MATERIALS_WORKFLOW_ID,
) -> WorkflowPlanningAdmission:
    if workflow_id not in {MATERIALS_WORKFLOW_ID, MATERIALS_FULL_DISCOVERY_WORKFLOW_ID}:
        raise ValueError(f"materials workflow is not registered: {workflow_id}")
    goal = (raw_input or "").strip() or (
        "发现满足给定约束的新晶体候选"
        if workflow_id == MATERIALS_WORKFLOW_ID
        else "按材料发现全流程开展研究：文献调研、候选设计、制备评估、参数推荐、DFT 验证与报告"
    )
    return WorkflowPlanningAdmission(
        workflow_id=workflow_id,
        reason=f"{workflow_id}_selected",
        user_facing_summary=(
            "正在按材料候选发现流程准备研究方案。"
            if workflow_id == MATERIALS_WORKFLOW_ID
            else "正在按材料发现全流程准备研究方案。"
        ),
        suggested_title=goal[:40],
    )
