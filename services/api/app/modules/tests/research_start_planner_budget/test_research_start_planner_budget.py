from __future__ import annotations

import math

import pytest

from app.modules.research_start_planner_budget import (
    DEFAULT_PLANNER_TASK_BUDGET_SECONDS,
    PLANNER_TASK_BUDGET_ENV,
    research_start_planner_task_budget_seconds,
)


def test_planner_task_budget_has_one_provider_independent_default() -> None:
    assert research_start_planner_task_budget_seconds({}) == DEFAULT_PLANNER_TASK_BUDGET_SECONDS == 90.0


@pytest.mark.parametrize("configured", ["1", "120", "240", "3600", "17.5"])
def test_valid_deployment_budget_is_authoritative_without_hidden_cap(configured: str) -> None:
    assert research_start_planner_task_budget_seconds({PLANNER_TASK_BUDGET_ENV: configured}) == float(configured)


@pytest.mark.parametrize("configured", ["", "0", "-1", "nan", "inf", "not-a-number"])
def test_invalid_budget_falls_back_to_stable_capability_default(configured: str) -> None:
    value = research_start_planner_task_budget_seconds({PLANNER_TASK_BUDGET_ENV: configured})
    assert math.isfinite(value)
    assert value == DEFAULT_PLANNER_TASK_BUDGET_SECONDS
