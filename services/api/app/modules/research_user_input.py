"""Code-owned, version-bound user input; Planner summaries are never source text."""
from __future__ import annotations

from typing import Literal

from app.models.research_orchestration import (
    PlanningInputSnapshot, PlanningInputSource, ResearchDesignRef,
    ResearchDesignVersion, ResearchTaskV12,
)


def design_user_input_snapshot(
    task: ResearchTaskV12, design: ResearchDesignVersion,
) -> PlanningInputSnapshot | None:
    if design.task_id != task.task_id or design.coverage_report is None:
        return None
    attempts = [row for row in task.planning_attempts
                if row.task_id == task.task_id
                and row.planning_attempt_id == design.coverage_report.planning_attempt_id]
    return attempts[0].user_input_snapshot if len(attempts) == 1 else None


def freeze_planning_user_input(
    *, task: ResearchTaskV12, content: str, source_ref: str,
    source_kind: Literal["user_request", "user_revision", "plan_revision"],
    base_design_ref: ResearchDesignRef | None = None,
) -> PlanningInputSnapshot:
    sources: list[PlanningInputSource] = []
    if source_kind != "user_request":
        base = next((row for row in task.research_design_versions
                     if base_design_ref is not None
                     and row.research_design_id == base_design_ref.research_design_id
                     and row.version == base_design_ref.version), None)
        inherited = None if base is None else design_user_input_snapshot(task, base)
        if inherited is None:
            raise ValueError("planning_user_input_source_missing")
        sources = [row.model_copy(deep=True) for row in inherited.sources]
    elif base_design_ref is not None:
        raise ValueError("planning_user_input_root_has_base")
    sources.append(PlanningInputSource(
        sourceKind=source_kind, sourceRef=source_ref, content=content,
    ))
    return PlanningInputSnapshot(sources=sources, baseResearchDesignRef=base_design_ref)


def canonical_user_query(
    task: ResearchTaskV12, design: ResearchDesignVersion,
) -> str | None:
    snapshot = design_user_input_snapshot(task, design)
    if snapshot is None:
        return None
    # A model-visible source view contains only verbatim user content, in source
    # order. Platform identifiers and Planner summaries are provenance/context,
    # never user-supplied scientific facts. Avoid JSON escaping source identities.
    return "\n\n".join(row.content for row in snapshot.sources)
