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
