"""The explicit Workflow selection frozen before ResearchDesign planning."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WorkflowPlanningAdmission:
    """A user-selected Workflow, not an inferred route or a research result."""

    workflow_id: str
    reason: str
    user_facing_summary: str
    suggested_title: str | None


__all__ = ["WorkflowPlanningAdmission"]
