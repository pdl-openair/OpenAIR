"""Deterministic admission for the CS paper-reproduction Workflow."""

from __future__ import annotations

from app.modules.workflow_planning_admission import WorkflowPlanningAdmission


CS_REPRODUCTION_WORKFLOW_ID = "cs_paper_reproduction_workflow"
CS_REPRODUCTION_SKILL_PACK = "cs_reproduction"


def cs_reproduction_workflow_planning_admission() -> WorkflowPlanningAdmission:
    return WorkflowPlanningAdmission(
        workflow_id=CS_REPRODUCTION_WORKFLOW_ID,
        reason="cs_paper_reproduction_workflow_selected",
        user_facing_summary="正在按计算机论文复现流程准备研究方案。",
        suggested_title="计算机领域论文复现",
    )


__all__ = [
    "CS_REPRODUCTION_SKILL_PACK",
    "CS_REPRODUCTION_WORKFLOW_ID",
    "cs_reproduction_workflow_planning_admission",
]
