"""ResearchStart Planner task-budget authority.

The Planner owns the runtime budget for one planning judgment. Provider and
HTTP layers consume this value; they must not silently replace it with a
shorter provider-specific cap. Transport stages may still keep their own short
connect/pool/write guards while response reading uses the Planner budget.
"""

from __future__ import annotations

import math
import os
from collections.abc import Mapping


PLANNER_TASK_BUDGET_ENV = "OPENAIR_RESEARCH_START_PLANNER_LLM_TIMEOUT_SECONDS"
DEFAULT_PLANNER_TASK_BUDGET_SECONDS = 90.0

PLANNER_MAX_TOKENS_ENV = "OPENAIR_RESEARCH_START_PLANNER_LLM_MAX_TOKENS"
DEFAULT_PLANNER_MAX_TOKENS = 1800
MINIMUM_PLANNER_MAX_TOKENS = 600


def research_start_planner_max_tokens(
    environ: Mapping[str, str] | None = None,
) -> int:
    """Return the deployment-authoritative output token budget for one judgment.

    Reasoning models may spend a large share of this budget on hidden thinking
    before emitting the final JSON. A deployment that selects a high reasoning
    effort must therefore be able to raise the budget without a hidden platform
    ceiling; only a small floor protects against unusable zero-token requests.

    Missing, unparseable and non-positive values fall back to the stable
    capability default (the same rule as the task-budget authority above). A
    positive value below the usable floor is raised to that floor; every other
    positive value is returned unchanged.
    """

    values = os.environ if environ is None else environ
    raw = values.get(PLANNER_MAX_TOKENS_ENV)
    if raw is None or not str(raw).strip():
        return DEFAULT_PLANNER_MAX_TOKENS
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_PLANNER_MAX_TOKENS
    if value <= 0:
        return DEFAULT_PLANNER_MAX_TOKENS
    return max(MINIMUM_PLANNER_MAX_TOKENS, value)


def research_start_planner_task_budget_seconds(
    environ: Mapping[str, str] | None = None,
) -> float:
    """Return the formal runtime budget for one Planner model judgment.

    Deployment may override the capability budget through the existing Planner
    configuration variable. A valid positive finite value is authoritative and
    is returned unchanged: this layer deliberately does not add a hidden lower
    or upper cap. Invalid values fall back to the stable capability default.
    """

    values = os.environ if environ is None else environ
    raw = values.get(PLANNER_TASK_BUDGET_ENV)
    if raw is None or not str(raw).strip():
        return DEFAULT_PLANNER_TASK_BUDGET_SECONDS
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_PLANNER_TASK_BUDGET_SECONDS
    if not math.isfinite(value) or value <= 0:
        return DEFAULT_PLANNER_TASK_BUDGET_SECONDS
    return value
