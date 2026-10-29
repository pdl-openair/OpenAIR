"""Public API for OpenTrajectory research-lead gap-to-action planning."""
from __future__ import annotations

from typing import Any

from .adapter import run_gap_to_action_adapter
from .planner import plan_gap_action, plan_gap_to_actions, validate_planner_output
from .pipeline import (
    AuthenticGapNormalizer,
    build_planner_suite_from_research_lead_package,
    run_gap_to_action_pipeline,
    validate_pipeline_output,
)


def build_research_lead_package(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Forward to the sibling ResearchLeadPackage builder.

    Kept lazy so importing ``gap_to_action`` does not pull evaluator/runtime
    dependencies that are unrelated to deterministic action planning.
    """

    from skills.opentrajectory.research_lead_package.builder import (
        build_research_lead_package as _build_research_lead_package,
    )

    return _build_research_lead_package(*args, **kwargs)


__all__ = [
    "AuthenticGapNormalizer",
    "build_planner_suite_from_research_lead_package",
    "build_research_lead_package",
    "plan_gap_action",
    "plan_gap_to_actions",
    "run_gap_to_action_adapter",
    "run_gap_to_action_pipeline",
    "validate_pipeline_output",
    "validate_planner_output",
]
