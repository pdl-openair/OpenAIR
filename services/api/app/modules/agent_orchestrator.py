"""Orchestrator MVP for entry-agent planning and execution-mode policy."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal
from uuid import uuid4

from app.modules.agent_registry import (
    VALID_EXECUTION_MODES,
    RegisteredAgent,
    load_agent_registry,
)


ExecutionMode = Literal[
    "route_only",
    "draft_only",
    "evidence_bound",
    "retrieval_bound",
    "full_execution",
    "critique_only",
]

UserInputPolicy = Literal["queue", "merge", "cancel", "wait"]
OrchestratorState = Literal["draft", "confirmed", "started", "awaiting_confirmation", "completed", "cancelled"]


SELECTED_HINT_TO_ENTRY_AGENT = {
    # literature_review is the durable Skill. The workbench entry must
    # remain an Agent so the orchestration decision has a callable owner.
    "agent_literature_research_v0_3": "key_literature_positioning",
    "skill_category_find_sources_v0_3": "key_literature_positioning",
    "skill_category_screen_priorities_v0_3": "key_literature_positioning",
    "agent_evidence_extraction_v0_3": "paper_evidence_extraction",
    "agent_gap_identification_v0_3": "research_gap_identification",
    "agent_validation_design_v0_3": "validation_design",
    "agent_result_audit_v0_3": "result_reliability_audit",
}

RESEARCH_TYPE_TO_PRIMARY_AGENT = {
    "evidence_extraction_start_draft": "paper_evidence_extraction",
    "evidence_organizing_start_draft": "paper_evidence_extraction",
    "gap_identification_start_draft": "research_gap_identification",
    "validation_design_start_draft": "validation_design",
    "result_audit_start_draft": "result_reliability_audit",
    "key_literature_start_draft": "key_literature_positioning",
    "research_planning_start_draft": "research_planning",
    "question_refinement_start_draft": "research_question_refinement",
    "mechanism_mapping_start_draft": "mechanism_dispute_mapping",
}

SUBSTAGE_TO_SPECIALIST_AGENT = {
    "evidence_extraction": "paper_evidence_extraction",
    "result_acceptance": "result_reliability_audit",
    "gap_identification": "research_gap_identification",
    "variable_definition": "validation_design",
    "baseline_selection": "validation_design",
    "ablation_design": "validation_design",
    "analysis_planning": "validation_design",
}

ENTRY_NATURAL_SUPPORT = {
    "key_literature_positioning": {"paper_evidence_extraction", "research_gap_identification"},
    "paper_evidence_extraction": {"research_gap_identification"},
    "research_gap_identification": {"paper_evidence_extraction"},
    "result_reliability_audit": {"paper_evidence_extraction"},
    "validation_design": set(),
}

DELIVERABLE_EXPANSIONS = {
    ("key_literature_positioning", "validation_design"): "literature map -> validation design",
    ("key_literature_positioning", "result_reliability_audit"): "literature map -> reliability audit",
    ("paper_evidence_extraction", "validation_design"): "evidence table -> validation design",
    ("paper_evidence_extraction", "result_reliability_audit"): "evidence table -> reliability verdict",
    ("research_gap_identification", "validation_design"): "gap list -> validation design",
}


@dataclass(frozen=True)
class SdrPlanStep:
    stage: str
    substages: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class OrchestratorDecision:
    decisionId: str
    userGoal: str
    selectedEntryAgent: str
    interpretedGoal: str
    primaryAgent: str
    supportingAgents: list[str]
    sdrPlan: list[SdrPlanStep]
    executionMode: ExecutionMode
    modeByAgent: dict[str, ExecutionMode]
    requiresUserConfirmation: bool
    scopeExpansion: bool
    reason: str
    userInputPolicy: UserInputPolicy = "queue"
    state: OrchestratorState = "draft"
    specialistGatewayRequired: bool = True

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["sdrPlan"] = [step.to_dict() for step in self.sdrPlan]
        return payload


def build_orchestrator_decision(
    *,
    user_goal: str | None,
    selected_entry_agent: str | None = None,
    selected_hint: str | None = None,
    creation_plan: dict[str, Any] | None = None,
    task_status: str = "draft",
) -> OrchestratorDecision:
    """Create the first-class orchestration decision object required by AgentArch-04."""

    goal = (user_goal or "").strip()
    plan = creation_plan or {}
    research_type = str(plan.get("researchType") or "").strip()
    primary = _primary_agent_from_plan(plan) or RESEARCH_TYPE_TO_PRIMARY_AGENT.get(research_type) or "research_planning"
    entry = _normalize_entry_agent(selected_entry_agent, selected_hint, primary)
    supporting = _supporting_agents(entry, primary, plan, goal)
    sdr_plan = _sdr_plan_from_creation_plan(plan) or _sdr_plan_from_agents([primary, *supporting])
    state = _state_from_task_status(task_status)
    mode = _mode_for_state(state)
    mode_by_agent = {
        agent_id: _mode_for_agent(agent_id, mode)
        for agent_id in [primary, *supporting]
        if _is_registered(agent_id)
    }
    scope_expansion = _scope_expansion(entry, primary, supporting)
    requires_confirmation = scope_expansion or (entry != primary and _is_registered(entry) and _is_registered(primary))
    reason = _decision_reason(entry, primary, supporting, scope_expansion)
    return OrchestratorDecision(
        decisionId=f"orch_{uuid4().hex[:12]}",
        userGoal=goal,
        selectedEntryAgent=entry,
        interpretedGoal=_interpreted_goal(goal, research_type, primary),
        primaryAgent=primary,
        supportingAgents=supporting,
        sdrPlan=sdr_plan,
        executionMode=mode,
        modeByAgent=mode_by_agent,
        requiresUserConfirmation=requires_confirmation,
        scopeExpansion=scope_expansion,
        reason=reason,
        state=state,
    )


def ensure_orchestrator_decision(
    plan: dict[str, Any],
    *,
    user_goal: str | None,
    selected_entry_agent: str | None = None,
    selected_hint: str | None = None,
    task_status: str = "draft",
) -> dict[str, Any]:
    updated = dict(plan)
    updated["orchestratorDecision"] = build_orchestrator_decision(
        user_goal=user_goal,
        selected_entry_agent=selected_entry_agent,
        selected_hint=selected_hint,
        creation_plan=updated,
        task_status=task_status,
    ).to_dict()
    return updated


def transition_orchestrator_state(plan: dict[str, Any] | None, *, task_status: str) -> dict[str, Any] | None:
    if not isinstance(plan, dict):
        return plan
    decision = plan.get("orchestratorDecision")
    if not isinstance(decision, dict):
        return plan
    updated_plan = dict(plan)
    updated_decision = dict(decision)
    state = _state_from_task_status(task_status)
    updated_decision["state"] = state
    updated_decision["executionMode"] = _mode_for_state(state)
    updated_plan["orchestratorDecision"] = updated_decision
    return updated_plan


def resolve_specialist_agent_for_call(
    *,
    skill_id: str | None = None,
    substage_key: str | None = None,
    task: dict[str, Any] | None = None,
) -> str | None:
    registry = load_agent_registry()
    for value in (skill_id, substage_key):
        if value and registry.is_callable(value):
            return registry.require(value).agentId
    if substage_key in SUBSTAGE_TO_SPECIALIST_AGENT:
        return SUBSTAGE_TO_SPECIALIST_AGENT[substage_key]
    plan = (task or {}).get("creationPlan") or {}
    if isinstance(plan, dict):
        for capability in plan.get("selectedCapabilities") or []:
            if not isinstance(capability, dict) or capability.get("kind") != "agent":
                continue
            agent_id = str(capability.get("id") or "")
            if registry.is_callable(agent_id):
                skill_ids = {str(item) for item in capability.get("skillIds") or []}
                if not substage_key or substage_key in skill_ids:
                    return registry.require(agent_id).agentId
    return None


def select_execution_mode(
    agent: RegisteredAgent,
    *,
    task_status: str,
    requested_mode: str | None = None,
) -> ExecutionMode:
    allowed = {str(mode) for mode in agent.budgetPolicy.get("allowedModes") or []}
    default_mode = str(agent.budgetPolicy.get("defaultMode") or "draft_only")
    if requested_mode:
        if requested_mode not in VALID_EXECUTION_MODES:
            raise ValueError(f"unsupported execution mode: {requested_mode}")
        if requested_mode not in allowed:
            raise ValueError(f"execution mode {requested_mode} is not allowed for agent {agent.agentId}")
        return requested_mode  # type: ignore[return-value]
    if task_status == "draft" and "draft_only" in allowed:
        return "draft_only"
    if task_status in {"not_started"} and "draft_only" in allowed:
        return "draft_only"
    if default_mode in VALID_EXECUTION_MODES:
        return default_mode  # type: ignore[return-value]
    return "draft_only"


def _normalize_entry_agent(selected_entry_agent: str | None, selected_hint: str | None, primary: str) -> str:
    registry = load_agent_registry()
    for value in (selected_entry_agent, selected_hint):
        if not value:
            continue
        mapped = SELECTED_HINT_TO_ENTRY_AGENT.get(value, value)
        if registry.is_callable(mapped):
            return registry.require(mapped).agentId
        return mapped
    return primary


def _primary_agent_from_plan(plan: dict[str, Any]) -> str | None:
    registry = load_agent_registry()
    for capability in plan.get("selectedCapabilities") or []:
        if not isinstance(capability, dict):
            continue
        if capability.get("resolutionType") == "skill_route":
            reviewer = str(capability.get("resolvedAgentId") or "")
            if registry.is_callable(reviewer):
                return registry.require(reviewer).agentId
            continue
        if capability.get("kind") != "agent":
            continue
        agent_id = str(capability.get("id") or "")
        if registry.is_callable(agent_id):
            return registry.require(agent_id).agentId
        if agent_id:
            return agent_id
    return None


def _supporting_agents(entry: str, primary: str, plan: dict[str, Any], goal: str) -> list[str]:
    if any(
        isinstance(capability, dict) and capability.get("resolutionType") == "skill_route"
        for capability in plan.get("selectedCapabilities") or []
    ):
        return []
    registry = load_agent_registry()
    candidates: list[str] = []
    for stage in plan.get("stages") or []:
        for row in stage.get("substages") or []:
            if not isinstance(row, dict):
                continue
            agent_id = SUBSTAGE_TO_SPECIALIST_AGENT.get(str(row.get("substage") or ""))
            if agent_id:
                candidates.append(agent_id)
    candidates.extend(ENTRY_NATURAL_SUPPORT.get(entry, set()))
    lower = goal.lower()
    if any(marker in lower for marker in ("validation", "experiment design", "baseline", "ablation")):
        candidates.append("validation_design")
    if any(marker in lower for marker in ("audit", "trust", "reliability", "reproducibility")):
        candidates.append("result_reliability_audit")
    out: list[str] = []
    for candidate in candidates:
        resolved = registry.require(candidate).agentId if registry.is_callable(candidate) else candidate
        if resolved != primary and resolved not in out:
            out.append(resolved)
    return out


def _sdr_plan_from_creation_plan(plan: dict[str, Any]) -> list[SdrPlanStep]:
    steps: list[SdrPlanStep] = []
    for stage in plan.get("stages") or []:
        if not isinstance(stage, dict):
            continue
        stage_key = str(stage.get("stageKey") or "").strip()
        substages = [
            str(row.get("substage") or "").strip()
            for row in stage.get("substages") or []
            if isinstance(row, dict) and str(row.get("substage") or "").strip()
        ]
        if not substages:
            substages = [str(item).strip() for item in stage.get("subStages") or [] if str(item).strip()]
        if stage_key and substages:
            steps.append(SdrPlanStep(stage=stage_key, substages=list(dict.fromkeys(substages))))
    return steps


def _sdr_plan_from_agents(agent_ids: list[str]) -> list[SdrPlanStep]:
    registry = load_agent_registry()
    by_stage: dict[str, list[str]] = {}
    for agent_id in agent_ids:
        if not registry.is_callable(agent_id):
            continue
        for mapping in registry.require(agent_id).sdr_mappings:
            stage = str(mapping.get("stage") or "")
            substage = str(mapping.get("substage") or "")
            if stage and substage:
                by_stage.setdefault(stage, [])
                if substage not in by_stage[stage]:
                    by_stage[stage].append(substage)
    return [SdrPlanStep(stage=stage, substages=substages) for stage, substages in by_stage.items()]


def _state_from_task_status(task_status: str) -> OrchestratorState:
    if task_status == "draft":
        return "draft"
    if task_status == "not_started":
        return "confirmed"
    if task_status == "running":
        return "started"
    if task_status == "awaiting_confirmation":
        return "awaiting_confirmation"
    if task_status == "completed":
        return "completed"
    if task_status == "cancelled":
        return "cancelled"
    return "draft"


def _mode_for_state(state: OrchestratorState) -> ExecutionMode:
    if state == "draft":
        return "draft_only"
    if state == "confirmed":
        return "route_only"
    if state == "awaiting_confirmation":
        return "critique_only"
    if state == "started":
        return "evidence_bound"
    return "route_only"


def _mode_for_agent(agent_id: str, fallback: ExecutionMode) -> ExecutionMode:
    registry = load_agent_registry()
    if not registry.is_callable(agent_id):
        return fallback
    agent = registry.require(agent_id)
    allowed = {str(mode) for mode in agent.budgetPolicy.get("allowedModes") or []}
    if fallback in allowed:
        return fallback
    default_mode = str(agent.budgetPolicy.get("defaultMode") or fallback)
    return default_mode if default_mode in VALID_EXECUTION_MODES else fallback  # type: ignore[return-value]


def _scope_expansion(entry: str, primary: str, supporting: list[str]) -> bool:
    targets = {primary, *supporting}
    return any((entry, target) in DELIVERABLE_EXPANSIONS for target in targets)


def _decision_reason(entry: str, primary: str, supporting: list[str], scope_expansion: bool) -> str:
    if scope_expansion:
        expanded = [target for target in [primary, *supporting] if (entry, target) in DELIVERABLE_EXPANSIONS]
        return f"Scope expansion requires confirmation: {entry} -> {', '.join(expanded)}."
    if supporting:
        return f"Primary entry {entry} can use internal supporting agents without changing the user-visible deliverable."
    return f"Primary entry {entry} maps directly to {primary}."


def _interpreted_goal(goal: str, research_type: str, primary: str) -> str:
    if research_type:
        return f"{research_type}: {goal}" if goal else research_type
    return f"{primary}: {goal}" if goal else primary


def _is_registered(agent_id: str) -> bool:
    return load_agent_registry().is_callable(agent_id)
