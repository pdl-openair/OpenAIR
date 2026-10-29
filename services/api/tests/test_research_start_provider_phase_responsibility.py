from __future__ import annotations

from typing import Any
from types import SimpleNamespace

import httpx
import pytest

from app.execution.research_start_provider_phase import ResearchStartProviderPhase
from app.modules.provider_transport_evidence import current_provider_execution_parent
from app.modules.research_start_repository import _session_public


class Repository:
    def __init__(self, *, permitted: bool = True, disposition: str = "response_observed") -> None:
        self.permitted = permitted
        self.disposition = disposition
        self.events: list[tuple[str, Any]] = []

    def begin_attempt(self, _request, _worker, _epoch, phase, ordinal):
        self.events.append(("begin", (phase, ordinal)))
        return {"attemptId": f"{phase}-{ordinal}", "externalCallPermitted": self.permitted}

    def finish_attempt(self, *_args, **kwargs):
        self.events.append(("finish", kwargs))

    def settle_provider_phase_failure(self, *_args, **kwargs):
        self.events.append(("settle", kwargs["failure_result"]))
        return self.disposition

    def mark_external_result_unknown(self, *_args):
        self.events.append(("unknown", _args[-1]))


def phase(repository: Repository, name: str) -> ResearchStartProviderPhase:
    return ResearchStartProviderPhase(
        repository=repository,
        request_id="request",
        worker_id="worker",
        lease_epoch=7,
        phase=name,
        ordinal=1,
        lease_lost=lambda: False,
    )


def test_planner_uses_one_physical_parent() -> None:
    name = "research_planner"
    repository = Repository()
    owner = phase(repository, name)
    assert owner.begin() is True
    physical_submissions = 0

    def execute():
        nonlocal physical_submissions
        physical_submissions += 1
        parent = current_provider_execution_parent()
        assert parent is not None
        assert parent.kind == "research_start"
        assert parent.parent_id == "request"
        assert parent.originating_authority == f"research_start:{name}"
        return {"ok": True}

    result = owner.execute(execute)
    owner.succeed(result)
    assert physical_submissions == 1
    assert [event[0] for event in repository.events] == ["begin", "finish"]


@pytest.mark.parametrize(
    ("disposition", "expected"),
    [
        ("not_submitted", "not_submitted"),
        ("response_observed", "response_observed"),
        ("external_result_unknown", "external_result_unknown"),
        ("task_handoff", "task_handoff"),
    ],
)
def test_failure_settlement_delegates_next_responsibility_to_repository(
    disposition: str, expected: str
) -> None:
    repository = Repository(disposition=disposition)
    owner = phase(repository, "research_planner")
    assert owner.begin() is True
    assert owner.fail({"failureCode": "planner_processing_failed"}) == expected
    assert [event[0] for event in repository.events] == ["begin", "settle"]


def test_transport_unknown_is_settled_without_second_execution() -> None:
    repository = Repository()
    owner = phase(repository, "research_planner")
    assert owner.begin() is True
    submissions = 0

    def execute():
        nonlocal submissions
        submissions += 1
        return None

    owner.execute(execute)
    owner.unknown({"failureCode": "external_result_unknown"})
    owner.unknown({"failureCode": "external_result_unknown"})
    assert submissions == 1
    assert [event[0] for event in repository.events] == ["begin", "unknown"]


def test_no_replay_guard_prevents_physical_execution() -> None:
    repository = Repository(permitted=False)
    owner = phase(repository, "research_planner")
    assert owner.begin() is False
    assert [event[0] for event in repository.events] == ["begin"]


def test_public_projection_rejects_false_running_without_responsibility() -> None:
    public = _session_public(
        {
            "session_id": "session",
            "state": "running",
            "reserved_task_id": None,
            "result_payload": None,
            "request_state": "unknown",
            "elapsed_seconds": 10,
        }
    )
    assert public["status"] == "failed"
    assert public["recoveryReason"] == "responsibility_gap"
    assert public["automaticRetryAllowed"] is False


def test_prepared_provider_adapter_uses_the_same_phase_operation_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.execution.research_start_provider_adapter import (
        PreparedPlannerAttempt,
        execute_prepared_attempt,
    )
    from app.modules import provider_transport_evidence as evidence_module
    from app.modules.provider_transport_evidence import provider_execution_parent

    observed: list[str] = []

    class EvidenceRepository:
        def __init__(self, *args, **kwargs):
            pass

        def begin(self, **kwargs):
            observed.append(kwargs["operation_identity"])
            return SimpleNamespace(
                attempt_id="physical",
                headers={},
                recovered_response_body=None,
                recovered_http_status=None,
                recovered_response_headers=None,
                parent_kind="research_start",
                capability_call_id="",
            )

        def mark_io_started(self, _attempt):
            pass

        def finish(self, *_args, **_kwargs):
            pass

    class Transport:
        def post(self, *_args, **_kwargs):
            return httpx.Response(
                200,
                request=httpx.Request("POST", "https://provider.test/v1/chat/completions"),
                json={"userOutcome": "research_task"},
            )

    monkeypatch.setenv("LLM_PROVIDER_API_KEY", "test-only")
    monkeypatch.setattr(evidence_module, "ProviderHttpAttemptRepository", EvidenceRepository)
    prepared = PreparedPlannerAttempt(
        phase="research_planner",
        ordinal=1,
        provider="huiyan_chat_completions",
        model="test",
        endpoint="https://provider.test/v1/chat/completions",
        payload={"model": "test", "messages": []},
        response_format=None,
        timeout_seconds=1,
        decode=lambda body: body,
    )
    with provider_execution_parent(
        kind="research_start",
        parent_id="request",
        originating_authority="research_start:research_planner",
    ):
        execute_prepared_attempt(prepared, transport=Transport())
    assert observed == ["research_start:research_planner"]
