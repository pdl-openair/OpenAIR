"""Deterministic ResearchDesign generator for the materials full-discovery workflow.

The full-discovery pipeline is a fixed five-step chain selected from the
workflow's nine nodes (workflow v3 demotes literature_review to optional):

    database_search -> candidate_generation -> stability_verification
    -> synthesis_execution -> report_generation

with the optional nodes (literature_review, synthesis_assessment,
parameter_recommendation, band_gap_calculation) omitted by default so the
chain runs end-to-end on deterministic channels only (HUIYAN reasoning + the
existing skill_script dispatchers). When a user selects the
materials_full_discovery_workflow, the design is produced deterministically —
no LLM planner call — so every task gets the same stable chain and repeated
test runs are comparable.

The generator reuses the production assembly/compiler unchanged
(``build_adaptive_workflow_semantics`` + ``compile_research_design_proposal``)
and supplies a deterministic ``WorkflowProjectSemanticBriefV4`` whose
responsibility deltas keep the five chain nodes planned while leaving the two
optional nodes dormant (no delta -> assembly skips them).
"""
from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.modules.research_design_compiler import compile_research_design_proposal
from app.modules.research_design_planner_assembly import (
    build_adaptive_workflow_semantics,
)
from app.modules.research_design_service import PlannerProviderResult
from app.modules.research_design_workflow_semantics import (
    WorkflowProjectSemanticBriefV4,
    WorkflowResponsibilityDeltaV4,
)

MATERIALS_FULL_DISCOVERY_WORKFLOW_ID = "materials_full_discovery_workflow"

# Optional workflow nodes that stay dormant in the deterministic chain.
# literature_review is optional since workflow v3; the deterministic chain
# starts at database_search (S2 -> S3 -> S7 -> S8 -> S9, where S8 dispatches
# the top candidates to the cloud experiment platform).
_DORMANT_OPTIONAL_NODES = frozenset(
    {
        "literature_review",
        "synthesis_assessment",
        "parameter_recommendation",
        "band_gap_calculation",
    }
)

# Scientific contexts that keep each required node planned. Required nodes are
# auto-planned by the assembly; a non-empty context makes the plan traceable to
# the user goal instead of relying on generic auto-planning.
_NODE_CONTEXTS = {
    "database_search": (
        "检索 Materials Project 满足约束的已有候选并导出种子 CIF，作为演化搜索的起点"
    ),
    "candidate_generation": (
        "基于种子 CIF 经 mattergen 生成初始种群并执行多目标进化搜索，输出 Pareto 前沿候选"
    ),
    "stability_verification": (
        "对进化结束综合排名第一的候选执行 VASP 热力学稳定性 DFT 计算，给出 ehull 与形成能"
    ),
    "synthesis_execution": (
        "取经 DFT 验证的少量候选，生成助熔剂法实验推荐方案并下发云实验平台"
        "（CrystalFlux）创建并启动试验任务"
    ),
    "report_generation": (
        "汇总全流程真实证据（目标、检索、候选、DFT）生成结构完整、数字有来源的实验报告"
    ),
}


def _responsibility_rows(
    workflow_planning_context: Mapping[str, Any],
) -> list[tuple[str, Mapping[str, Any], str]]:
    """Return ``(responsibility_choice, node_row, requirement)`` by displayOrder."""
    core = [
        row
        for row in (workflow_planning_context.get("coreResponsibilities") or [])
        if isinstance(row, Mapping)
    ]
    conditional = [
        row
        for row in (workflow_planning_context.get("conditionalResponsibilities") or [])
        if isinstance(row, Mapping)
    ]
    rows = sorted(
        [(row, "core") for row in core] + [(row, "conditional") for row in conditional],
        key=lambda pair: int(pair[0].get("displayOrder") or 0),
    )
    from app.modules.research_design_workflow_semantics import (
        workflow_responsibility_choice,
    )

    return [
        (workflow_responsibility_choice(position), row, requirement)
        for position, (row, requirement) in enumerate(rows, start=1)
    ]


def build_materials_deterministic_brief(
    *,
    raw_input: str,
    workflow_planning_context: Mapping[str, Any],
) -> WorkflowProjectSemanticBriefV4:
    """Construct the deterministic brief selecting the fixed four-node chain.

    Required chain nodes receive a non-empty scientific context (traceable
    planning); optional nodes receive nothing so the assembly leaves them out.
    """
    raw_goal = str(raw_input or "").strip()
    deltas: list[WorkflowResponsibilityDeltaV4] = []
    for choice, row, _requirement in _responsibility_rows(workflow_planning_context):
        node_key = str(row.get("nodeKey") or "").strip()
        if node_key in _DORMANT_OPTIONAL_NODES:
            continue
        context = _NODE_CONTEXTS.get(node_key)
        if context is None:
            # Unknown required node (future workflow extensions): fall back to
            # the user goal so the chain never silently drops a new node.
            context = raw_goal
        deltas.append(
            WorkflowResponsibilityDeltaV4(
                responsibility_choice=choice,
                work_item_scientific_contexts=[context],
            )
        )
    return WorkflowProjectSemanticBriefV4(
        research_goal=raw_goal,
        responsibility_deltas=deltas,
    )


def compile_materials_full_chain_proposal(
    *,
    raw_input: str,
    definition_catalog: Sequence[Mapping[str, Any]],
    workflow_planning_context: Mapping[str, Any],
    planning_sources: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Produce the fixed-chain materials ResearchDesign proposal dict (no LLM)."""
    brief = build_materials_deterministic_brief(
        raw_input=raw_input,
        workflow_planning_context=workflow_planning_context,
    )
    semantic = build_adaptive_workflow_semantics(
        raw_input=raw_input,
        definition_catalog=definition_catalog,
        workflow_planning_context=workflow_planning_context,
        brief=brief,
        planning_sources=planning_sources,
    )
    proposal = compile_research_design_proposal(
        semantic,
        definition_catalog=definition_catalog,
        raw_input=raw_input,
        workflow_planning_context=workflow_planning_context,
    )
    return proposal.model_dump(mode="json", by_alias=True)


def build_materials_deterministic_provider_result(
    *,
    raw_input: str,
    definition_catalog: Sequence[Mapping[str, Any]],
    workflow_planning_context: Mapping[str, Any],
    planning_sources: Sequence[Mapping[str, Any]] = (),
) -> PlannerProviderResult:
    """Assemble the ``PlannerProviderResult`` for a deterministic materials design."""
    proposal = compile_materials_full_chain_proposal(
        raw_input=raw_input,
        definition_catalog=definition_catalog,
        workflow_planning_context=workflow_planning_context,
        planning_sources=planning_sources,
    )
    content_hash = str(workflow_planning_context.get("workflowContentHash") or "")
    digest = hashlib.sha256(
        f"{raw_input}|{content_hash}".encode()
    ).hexdigest()[:12]
    return PlannerProviderResult(
        proposal=proposal,
        provider_call_ref=f"materials-deterministic:{digest}",
        planning_source_usages=(),
    )


@dataclass(frozen=True)
class PreparedDeterministicMaterialsPlannerAttempt:
    """A planner attempt producing a deterministic materials design with no HTTP."""

    ordinal: int
    _result: PlannerProviderResult

    @property
    def phase(self) -> str:
        return "research_planner"

    @property
    def provider(self) -> str:
        return "deterministic_materials"

    @property
    def model(self) -> str:
        return "deterministic"

    @property
    def endpoint(self) -> str:
        return ""

    @property
    def timeout_seconds(self) -> float:
        return 0.0

    @property
    def payload(self) -> dict[str, Any]:
        return {}

    @property
    def response_format(self) -> dict[str, Any] | None:
        return None

    @property
    def provider_schema(self) -> dict[str, Any] | None:
        return None

    @property
    def result(self) -> PlannerProviderResult:
        return self._result

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "ordinal": self.ordinal,
            "provider": self.provider,
            "model": self.model,
            "mode": "deterministic_materials",
        }

    def execute(self, *, transport: Any = None) -> PlannerProviderResult:
        return self._result


def prepare_deterministic_materials_planner_attempt(
    *,
    raw_input: str,
    ordinal: int,
    definition_catalog: Sequence[Mapping[str, Any]],
    workflow_planning_context: Mapping[str, Any],
    planning_sources: Sequence[Mapping[str, Any]] = (),
) -> PreparedDeterministicMaterialsPlannerAttempt:
    """Build a deterministic materials planner attempt for the frozen catalog."""
    result = build_materials_deterministic_provider_result(
        raw_input=raw_input,
        definition_catalog=definition_catalog,
        workflow_planning_context=workflow_planning_context,
        planning_sources=planning_sources,
    )
    return PreparedDeterministicMaterialsPlannerAttempt(ordinal=ordinal, _result=result)


__all__ = [
    "MATERIALS_FULL_DISCOVERY_WORKFLOW_ID",
    "PreparedDeterministicMaterialsPlannerAttempt",
    "build_materials_deterministic_brief",
    "build_materials_deterministic_provider_result",
    "compile_materials_full_chain_proposal",
    "prepare_deterministic_materials_planner_attempt",
]
