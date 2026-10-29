"""Deterministic PVD process-optimization Workflow admission."""

from __future__ import annotations

from app.modules.workflow_planning_admission import WorkflowPlanningAdmission


PVD_WORKFLOW_ID = "pvd_process_optimization_workflow"
PVD_SKILL_PACK = "pvd"

def pvd_workflow_planning_admission() -> WorkflowPlanningAdmission:
    return WorkflowPlanningAdmission(
        workflow_id=PVD_WORKFLOW_ID,
        reason="pvd_process_optimization_workflow_selected",
        user_facing_summary="正在按 PVD 工艺参数优化流程准备研究方案。",
        suggested_title="PVD 工艺参数优化",
    )


__all__ = [
    "PVD_SKILL_PACK",
    "PVD_WORKFLOW_ID",
    "pvd_workflow_planning_admission",
]
