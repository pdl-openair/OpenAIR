from __future__ import annotations

import math
from types import SimpleNamespace
from typing import Any

import pytest

from app.modules import research_design_agent_revision as revision
from app.modules import research_start_planner as planner
from app.modules.research_start_planner_budget import (
    DEFAULT_PLANNER_MAX_TOKENS,
    DEFAULT_PLANNER_TASK_BUDGET_SECONDS,
    MINIMUM_PLANNER_MAX_TOKENS,
    PLANNER_MAX_TOKENS_ENV,
    PLANNER_TASK_BUDGET_ENV,
    research_start_planner_max_tokens,
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


def test_planner_max_tokens_has_provider_independent_default() -> None:
    assert research_start_planner_max_tokens({}) == DEFAULT_PLANNER_MAX_TOKENS == 1800


@pytest.mark.parametrize(
    "configured",
    ["600", "5000", "8000", "16000", "32768", "65536", "131072"],
)
def test_valid_planner_max_tokens_is_authoritative_without_hidden_cap(
    configured: str,
) -> None:
    assert research_start_planner_max_tokens({PLANNER_MAX_TOKENS_ENV: configured}) == int(configured)


@pytest.mark.parametrize("configured", ["", "  ", "0", "-1", "not-a-number"])
def test_missing_or_non_positive_planner_max_tokens_uses_default(configured: str) -> None:
    assert (
        research_start_planner_max_tokens({PLANNER_MAX_TOKENS_ENV: configured})
        == DEFAULT_PLANNER_MAX_TOKENS
    )


@pytest.mark.parametrize("configured", ["1", "599"])
def test_positive_planner_max_tokens_below_floor_is_raised(configured: str) -> None:
    assert (
        research_start_planner_max_tokens({PLANNER_MAX_TOKENS_ENV: configured})
        == MINIMUM_PLANNER_MAX_TOKENS
    )


def test_truncated_planner_attempt_retries_with_the_same_deployment_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Issue #14: a retry after a length-truncated attempt keeps the configured budget."""

    configured = 12000
    monkeypatch.setattr(planner.settings, "research_orchestration_v1", False)
    monkeypatch.setenv("OPENAIR_RESEARCH_START_PLANNER_PROVIDER", "huiyan_chat_completions")
    monkeypatch.setenv("OPENAIR_RESEARCH_START_PLANNER_LLM_MODEL", "test-model")
    monkeypatch.setenv("LLM_PROVIDER_API_KEY", "test-key")
    monkeypatch.setenv("LLM_PROVIDER_BASE_URL", "https://provider.invalid")
    monkeypatch.setenv(PLANNER_MAX_TOKENS_ENV, str(configured))
    monkeypatch.setattr(planner, "_planner_chat_model", lambda: "test-model")
    monkeypatch.setattr(planner, "_planner_openai_model", lambda: "test-model")
    monkeypatch.setattr(planner, "_planner_agent_catalog", lambda: [])

    first = planner.prepare_planner_attempt(
        {"input": "compare reproducible research methods"},
        None,
        1,
    )
    retried = planner.prepare_planner_attempt(
        {"input": "compare reproducible research methods"},
        None,
        2,
        prior_failure={"finishReason": "length", "truncated": True},
    )

    assert first.payload["max_tokens"] == configured
    assert retried.payload["max_tokens"] == configured


@pytest.mark.parametrize(
    ("provider", "budget_field"),
    [
        ("huiyan_chat_completions", "max_tokens"),
        ("openai_responses", "max_output_tokens"),
    ],
)
def test_agent_revision_request_carries_the_deployment_budget(
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    budget_field: str,
) -> None:
    configured = 12000
    monkeypatch.setenv("OPENAIR_RESEARCH_START_PLANNER_PROVIDER", provider)
    monkeypatch.setenv("OPENAIR_RESEARCH_START_PLANNER_LLM_MODEL", "test-model")
    monkeypatch.setenv("LLM_PROVIDER_API_KEY", "test-key")
    monkeypatch.setenv("LLM_PROVIDER_BASE_URL", "https://provider.invalid")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_API_BASE_URL", "https://provider.invalid")
    monkeypatch.setenv(PLANNER_MAX_TOKENS_ENV, str(configured))
    monkeypatch.setattr(revision, "_agent_revision_provider", lambda: provider)

    captured: dict[str, Any] = {}

    def fake_execute(prepared: Any) -> tuple[object, str]:
        captured["payload"] = prepared.payload
        return object(), "provider-call-ref"

    monkeypatch.setattr(
        "app.execution.research_start_provider_adapter.execute_prepared_attempt",
        fake_execute,
    )
    latest_design = SimpleNamespace(
        goal="Goal",
        instances=[
            SimpleNamespace(
                substage_instance_id="sub_a",
                stage_key="problem_modeling",
                objective="Original objective",
            )
        ],
    )

    revision._invoke_targeted_model_patch("Make the objective reproducible.", latest_design)

    assert captured["payload"][budget_field] == configured
