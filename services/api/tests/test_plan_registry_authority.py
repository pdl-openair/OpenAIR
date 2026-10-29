from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from app.execution.durable_orchestrator_bridge import FrozenPlanError, precompile_creation_plan
from app.modules.agent_orchestrator import ensure_orchestrator_decision
from app.modules.agent_registry import load_agent_registry


from app.modules.research_start_planner import (
    _planner_agent_catalog,
    _deterministic_research_start_plan,
    build_creation_plan_stages,
)

CURRENT_USER_GOAL = (
    "我最近在做一个在线编程教学系统，想用大语言模型给学生的代码提供即时反馈，但又担心模型会直接泄露完整答案，"
    "或者对不同基础的学生给出不公平的建议。这个方向能做成一个怎样的计算机科学研究问题？"
)


def _current_goal_creation_plan() -> dict:
    planned = _deterministic_research_start_plan(
        raw_input=CURRENT_USER_GOAL,
        planning_selection={"reason": "question_refinement_start_draft"},
    )
    plan = {
        "goalSummary": CURRENT_USER_GOAL,
        "selectedCapabilities": [capability.to_dict() for capability in planned.selectedCapabilities],
        "stages": build_creation_plan_stages(planned, pack="sdr"),
    }
    return ensure_orchestrator_decision(
        plan,
        user_goal=CURRENT_USER_GOAL,
        task_status="draft",
    )


def test_planner_catalog_contains_only_production_specialists() -> None:
    registry = load_agent_registry()

    assert {row["id"] for row in _planner_agent_catalog()} == registry.ids() - {"general_research_agent"}
    assert "general_research_agent" not in {row["id"] for row in _planner_agent_catalog()}
    assert "research_progress_planning" not in registry.callable_ids()


def test_current_user_goal_precompiles_registered_specialist_before_confirmation() -> None:
    registry = load_agent_registry()
    plan = _current_goal_creation_plan()

    selected = plan["selectedCapabilities"]
    units = precompile_creation_plan(creation_plan=plan, goal_summary=CURRENT_USER_GOAL)

    assert selected[0]["id"] == "research_question_refinement"
    assert all(registry.is_callable(row["id"]) for row in selected if row["kind"] == "agent")
    assert units
    assert all(registry.is_callable(unit["agentId"]) for unit in units)
    assert all(unit["agentId"] != "research_progress_planning" for unit in units)


def test_planner_only_unknown_agent_is_rejected_before_a_run_exists() -> None:
    plan = deepcopy(_current_goal_creation_plan())
    plan["orchestratorDecision"]["primaryAgent"] = "planner_only_unknown"

    with pytest.raises(FrozenPlanError, match="unregistered_agent:primaryAgent:planner_only_unknown"):
        precompile_creation_plan(creation_plan=plan, goal_summary=CURRENT_USER_GOAL)


def test_instance_resolver_does_not_read_legacy_selection_authorities() -> None:
    resolver_path = Path(__file__).parents[1] / "app" / "modules" / "research_orchestration_resolver.py"
    assert resolver_path.exists(), "instance-level Resolver boundary is absent"
    source = resolver_path.read_text(encoding="utf-8")
    for forbidden in (
        "dynamicSkills[0]",
        "selectedCapabilities",
        "orchestratorDecision",
        "primaryAgent",
        "supportingAgents",
        "PLANNER_AGENT_CATALOG",
    ):
        assert forbidden not in source
