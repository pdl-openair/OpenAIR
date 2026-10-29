"""Deterministic ResearchDesign generator for the PVD workflow.

PVD is a fixed pipeline (8 substages S1..S8). When a user selects the PVD
workflow, the design is produced deterministically — no LLM planner call —
so every PVD task covers the full chain and can surface latent bugs.

The generator reuses the production assembly/compiler unchanged:
``build_adaptive_workflow_semantics`` + ``compile_research_design_proposal``.
It supplies a deterministic ``WorkflowProjectSemanticBriefV4`` that selects
both conditional responsibilities (S4 evidence applicability, S5 experimental
feedback) as ``planned_work``. Core responsibilities (S6/S7/S8) are auto-
planned by the assembly; supporting work (S1/S2/S3) is pulled in by S4.

Post-processing (``_post_process_pvd_proposal``) applies two PVD-specific
adjustments that the generic assembly cannot express:

1. **Drop the S5→S6 dependency edge.** Workflow v3 declares both S4→S6 and
   S5→S6 as ``optional_enhancement``; the assembly wires both into S6's
   dependencies. But S5 stays dormant until the experiment-feedback API
   wakes it (its binding declares a caller-designated ``experiment_ref``;
   generic auto-start is blocked by the invocation-context guard). If S6
   depended on S5, the chain would stall at ``dependency_not_satisfied``.
   We keep S4→S6 (evidence feeds candidate analysis) and remove S5→S6.

2. **Move S5 to the end of the instance order.** ``execution_instances_for_design``
   returns ``design.instances`` in proposal order; ``_legal_next_task_candidates``
   iterates READY instances in that order. With S5 last, the generic scheduler
   runs S1→S2→S3→S4→S6→S7→S8 first; only when all of those complete does S5
   become the sole READY candidate → the invocation-context guard fires →
   S5 honestly lands in ``needs_input`` (awaiting experiment feedback).
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
    workflow_responsibility_choice,
)

PVD_WORKFLOW_ID = "pvd_process_optimization_workflow"

# Definition ids for the two conditional responsibilities and the downstream
# whose dependency edge must be trimmed.
_S5_DEFINITION_ID = "pvd:experimental_feedback_round_analysis"
_S6_DEFINITION_ID = "pvd:process_factor_candidate_experiment_analysis"

# Scientific contexts that select each conditional responsibility as planned
# work (non-empty contexts → assembly marks the responsibility ``planned_work``).
_S4_CONTEXT = (
    "评估外部文献证据对当前 PVD 工艺优化的适用范围、迁移限制与可比性，"
    "为候选实验方向分析提供证据支撑"
)
_S5_CONTEXT = (
    "在真实实验反馈提交后，分析计划与实际执行的偏差、测量结果与混杂因素，"
    "为下一轮候选方向重入提供诊断依据"
)


def _pvd_responsibility_choices(
    workflow_planning_context: Mapping[str, Any],
) -> list[tuple[str, Mapping[str, Any], str]]:
    """Return ``(responsibility_choice, node_row, requirement)`` sorted by displayOrder.

    Mirrors ``_adaptive_responsibility_descriptions`` position numbering so the
    ``responsibility_choice`` tokens match what ``build_adaptive_workflow_semantics``
    expects.
    """
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
    return [
        (workflow_responsibility_choice(position), row, requirement)
        for position, (row, requirement) in enumerate(rows, start=1)
    ]


def build_pvd_deterministic_brief(
    *,
    raw_input: str,
    workflow_planning_context: Mapping[str, Any],
) -> WorkflowProjectSemanticBriefV4:
    """Construct the deterministic semantic brief that selects the full PVD chain.

    Both conditional responsibilities (S4 evidence applicability, S5 experimental
    feedback) receive a non-empty ``work_item_scientific_contexts`` delta so the
    assembly materializes them as ``planned_work``. Core responsibilities
    (S6/S7/S8) need no delta — the assembly auto-plans them.
    """
    raw_goal = str(raw_input or "").strip()
    choices = _pvd_responsibility_choices(workflow_planning_context)
    deltas: list[WorkflowResponsibilityDeltaV4] = []
    for choice, row, requirement in choices:
        if requirement != "conditional":
            continue
        node_key = str(row.get("nodeKey") or "").strip()
        context = (
            _S4_CONTEXT
            if node_key == "evidence_applicability_comparability_analysis"
            else _S5_CONTEXT
        )
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


def _post_process_pvd_proposal(proposal: dict[str, Any]) -> dict[str, Any]:
    """Apply PVD-specific adjustments to a compiled proposal dict.

    1. Remove the S5→S6 dependency edge from S6's dependencies.
    2. Move the S5 instance to the end of the instances list so the generic
       scheduler runs the full chain before touching S5.
    """
    import copy

    result = copy.deepcopy(proposal)
    instances: list[dict[str, Any]] = list(result.get("instances") or [])

    # Identify S5 and S6 by definition id.
    s5_instance_key: str | None = None
    for instance in instances:
        if str(instance.get("definitionId") or "") == _S5_DEFINITION_ID:
            s5_instance_key = str(instance.get("instanceKey") or "")
            break

    if s5_instance_key is not None:
        # 1. Trim S5→S6 dependency edge.
        for instance in instances:
            if str(instance.get("definitionId") or "") == _S6_DEFINITION_ID:
                deps: list[dict[str, Any]] = instance.get("dependencies") or []
                instance["dependencies"] = [
                    dep
                    for dep in deps
                    if str(dep.get("upstreamInstanceKey") or "") != s5_instance_key
                ]
                break

        # 2. Reorder: S5 last.
        s5_instances = [
            row
            for row in instances
            if str(row.get("definitionId") or "") == _S5_DEFINITION_ID
        ]
        others = [
            row
            for row in instances
            if str(row.get("definitionId") or "") != _S5_DEFINITION_ID
        ]
        result["instances"] = others + s5_instances

    return result


def compile_pvd_full_chain_proposal(
    *,
    raw_input: str,
    definition_catalog: Sequence[Mapping[str, Any]],
    workflow_planning_context: Mapping[str, Any],
    planning_sources: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Produce the full-chain PVD ResearchDesign proposal dict (no LLM call).

    Reuses ``build_adaptive_workflow_semantics`` + ``compile_research_design_proposal``
    unchanged, then applies PVD-specific post-processing.
    """
    brief = build_pvd_deterministic_brief(
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
    return _post_process_pvd_proposal(proposal.model_dump(mode="json", by_alias=True))


def build_pvd_deterministic_provider_result(
    *,
    raw_input: str,
    definition_catalog: Sequence[Mapping[str, Any]],
    workflow_planning_context: Mapping[str, Any],
    planning_sources: Sequence[Mapping[str, Any]] = (),
) -> PlannerProviderResult:
    """Assemble the ``PlannerProviderResult`` for a deterministic PVD design.

    The ``provider_call_ref`` is a stable synthetic identifier (no HTTP call
    was made); ``planning_source_usages`` is empty because no external planning
    sources are consumed.
    """
    proposal = compile_pvd_full_chain_proposal(
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
        provider_call_ref=f"pvd-deterministic:{digest}",
        planning_source_usages=(),
    )


@dataclass(frozen=True)
class PreparedDeterministicPvdPlannerAttempt:
    """A planner attempt that produces a deterministic PVD design with no HTTP.

    ``execute`` returns the pre-computed ``PlannerProviderResult`` directly,
    bypassing ``execute_prepared_attempt`` (no provider HTTP, no physical
    attempt evidence). The worker runtime still fences the attempt via
    ``ResearchStartProviderPhase`` and persists the result.
    """

    ordinal: int
    _result: PlannerProviderResult

    @property
    def phase(self) -> str:
        return "research_planner"

    @property
    def provider(self) -> str:
        return "deterministic_pvd"

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
            "mode": "deterministic_pvd",
        }

    def execute(self, *, transport: Any = None) -> PlannerProviderResult:
        return self._result


def prepare_deterministic_pvd_planner_attempt(
    *,
    raw_input: str,
    ordinal: int,
    definition_catalog: Sequence[Mapping[str, Any]],
    workflow_planning_context: Mapping[str, Any],
    planning_sources: Sequence[Mapping[str, Any]] = (),
) -> PreparedDeterministicPvdPlannerAttempt:
    """Build a deterministic PVD planner attempt for the given frozen catalog."""
    result = build_pvd_deterministic_provider_result(
        raw_input=raw_input,
        definition_catalog=definition_catalog,
        workflow_planning_context=workflow_planning_context,
        planning_sources=planning_sources,
    )
    return PreparedDeterministicPvdPlannerAttempt(ordinal=ordinal, _result=result)


__all__ = [
    "PVD_WORKFLOW_ID",
    "PreparedDeterministicPvdPlannerAttempt",
    "build_pvd_deterministic_brief",
    "build_pvd_deterministic_provider_result",
    "compile_pvd_full_chain_proposal",
    "prepare_deterministic_pvd_planner_attempt",
]
