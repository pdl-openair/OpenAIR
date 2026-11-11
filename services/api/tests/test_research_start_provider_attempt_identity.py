from __future__ import annotations

from typing import Any

import app.modules.provider_transport_evidence as evidence_module
import httpx
import pytest
from app.execution.research_start_provider_adapter import (
    PreparedPlannerAttempt,
    execute_prepared_attempt,
)


class _Attempt:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {}

    def mark_io_started(self) -> None:  # pragma: no cover - recorder stub
        return None


class _Repository:
    def __init__(self) -> None:
        self.identities: list[str] = []
        self.attempt_numbers: list[int] = []

    def begin(self, **kwargs: Any) -> _Attempt:
        self.identities.append(str(kwargs["operation_identity"]))
        self.attempt_numbers.append(int(kwargs["attempt_number"]))
        return _Attempt()

    def mark_io_started(self, _attempt: _Attempt) -> None:
        return None

    def finish(self, *_args: Any, **_kwargs: Any) -> None:
        return None


class _Transport:
    def post(self, *_args: Any, **_kwargs: Any) -> httpx.Response:
        request = httpx.Request("POST", "https://example.invalid/v1/chat/completions")
        return httpx.Response(
            200,
            json={
                "id": "resp_test",
                "choices": [{"message": {"content": "{}"}}],
                "usage": {},
            },
            headers={"x-request-id": "req_test"},
            request=request,
        )


def _attempt(ordinal: int) -> PreparedPlannerAttempt[dict[str, Any]]:
    return PreparedPlannerAttempt(
        phase="research_planner",
        ordinal=ordinal,
        provider="huiyan_chat_completions",
        model="deepseek-v4.1-flash",
        endpoint="https://example.invalid/v1/chat/completions",
        payload={"model": "deepseek-v4.1-flash", "messages": []},
        response_format=None,
        timeout_seconds=5.0,
        decode=lambda body: dict(body),
    )


def test_retry_ordinals_get_distinct_operation_identities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_PROVIDER_API_KEY", "test-key")
    repository = _Repository()
    monkeypatch.setattr(evidence_module, "ProviderHttpAttemptRepository", lambda: repository)
    monkeypatch.setattr(
        evidence_module,
        "current_provider_execution_parent",
        lambda: object(),
    )

    execute_prepared_attempt(_attempt(1), transport=_Transport())
    execute_prepared_attempt(_attempt(2), transport=_Transport())

    assert repository.identities == [
        "research_start:research_planner:1",
        "research_start:research_planner:2",
    ]
    assert repository.attempt_numbers == [1, 2]


def test_one_ordinal_keeps_a_single_operation_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A replayed logical attempt stays inside its own identity scope."""
    monkeypatch.setenv("LLM_PROVIDER_API_KEY", "test-key")
    repository = _Repository()
    monkeypatch.setattr(evidence_module, "ProviderHttpAttemptRepository", lambda: repository)
    monkeypatch.setattr(
        evidence_module,
        "current_provider_execution_parent",
        lambda: object(),
    )

    execute_prepared_attempt(_attempt(1), transport=_Transport())
    execute_prepared_attempt(_attempt(1), transport=_Transport())

    assert repository.identities == ["research_start:research_planner:1"] * 2


@pytest.mark.parametrize(
    "runner_name",
    ["_run_chat_completions_planner", "_run_openai_responses_planner"],
)
def test_live_planner_fallback_chain_advances_operation_identity(
    monkeypatch: pytest.MonkeyPatch,
    runner_name: str,
) -> None:
    """In-run retries and the repair pass must never reuse a terminal identity."""
    import app.modules.research_start_planner as planner

    observed: list[str] = []

    def fake_execute(**kwargs: Any) -> dict[str, Any]:
        observed.append(str(kwargs["operation_identity"]))
        raise httpx.ConnectError("provider unavailable")

    monkeypatch.setattr(planner, "execute_observed_openai_compatible_json", fake_execute)
    monkeypatch.setattr(
        planner,
        "apply_provider_schema_transport",
        lambda _provider, *, payload, **_kwargs: dict(payload),
    )
    monkeypatch.setattr(
        planner,
        "_build_responses_payload",
        lambda **kwargs: {"model": kwargs["model"], "userPrompt": kwargs["user_prompt"]},
    )
    monkeypatch.setattr(planner, "_planner_attempts", lambda: 1)
    monkeypatch.setattr(planner, "_planner_chat_model_fallback_chain", lambda: ["model-a"])
    monkeypatch.setattr(planner, "_openai_model_fallback_chain", lambda _model: ["model-a"])
    monkeypatch.setenv("LLM_PROVIDER_API_KEY", "test-key")
    monkeypatch.setenv("LLM_PROVIDER_BASE_URL", "https://example.invalid")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAIR_RESEARCH_START_PLANNER_LLM_MODEL", "model-a")

    runner = getattr(planner, runner_name)
    ordinals = planner._planner_submission_ordinals()
    for _attempt_number in range(2):
        with pytest.raises(planner.ResearchStartPlannerUnavailable):
            runner(
                raw_input="plan a study",
                planning_selection=None,
                source_page="home",
                selected_hint=None,
                submission_ordinals=ordinals,
            )

    assert observed == [
        "research_start:research_planner:1",
        "research_start:research_planner:2",
    ]
