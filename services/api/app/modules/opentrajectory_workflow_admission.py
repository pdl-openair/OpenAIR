"""Deterministic OpenTrajectory Workflow admission."""

from __future__ import annotations

from app.modules.workflow_planning_admission import WorkflowPlanningAdmission

OPENTRAJECTORY_WORKFLOW_ID = "opentrajectory_research_workflow"
OPENTRAJECTORY_SKILL_PACK = "opentrajectory"

def opentrajectory_workflow_planning_admission(raw_input: str) -> WorkflowPlanningAdmission:
    focus = _research_focus(raw_input)
    return WorkflowPlanningAdmission(
        workflow_id=OPENTRAJECTORY_WORKFLOW_ID,
        reason="opentrajectory_workflow_selected",
        user_facing_summary="正在按科研线索与假说流程准备研究方案。",
        suggested_title=focus[:22] or "科研线索与假说",
    )


def _research_focus(raw_goal: str) -> str:
    return (raw_goal or "").strip(" ，,。；;：:") or "当前研究目标"


__all__ = [
    "OPENTRAJECTORY_SKILL_PACK",
    "OPENTRAJECTORY_WORKFLOW_ID",
    "opentrajectory_workflow_planning_admission",
]
