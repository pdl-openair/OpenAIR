from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from app.execution import durable_skill_runtime


class _AttemptRepository:
    def begin(self, **_kwargs):
        return SimpleNamespace(headers={})

    def begin_sub_attempt(self, **_kwargs):
        return SimpleNamespace(headers={})

    def mark_io_started(self, *_args, **_kwargs) -> None:
        return None

    def finish(self, *_args, **_kwargs) -> None:
        return None


def _candidate(index: int) -> dict:
    return {
        "candidate_id": f"c{index:03d}",
        "title": f"Candidate {index}",
        "abstract": f"Abstract {index}",
    }


def _admissible_batch_schema(*, max_items: int = 65) -> dict:
    """Minimal closed schema that passes current Huiyan provider admission.

    Keep this helper intentionally small so batch partition / sibling /
    evidence-localization tests stay independent of the full OT screening draft.
    """
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["judgments"],
        "properties": {
            "judgments": {
                "type": "array",
                "maxItems": max_items,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "candidate_id",
                        "decision",
                        "reading_priority",
                        "reason",
                    ],
                    "properties": {
                        "candidate_id": {"type": "string", "minLength": 1},
                        "decision": {
                            "type": "string",
                            "enum": ["priority", "supporting", "defer", "exclude"],
                        },
                        "reason": {"type": "string", "minLength": 1},
                        "reading_priority": {
                            "type": ["integer", "null"],
                            "minimum": 1,
                            "maximum": 100,
                        },
                    },
                },
            }
        },
    }


def _base_payload(candidates: list[dict]) -> dict:
    schema = _admissible_batch_schema(max_items=max(65, len(candidates)))
    return {
        "model": "terra-model",
        "messages": [
            {"role": "system", "content": "screen candidates"},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "scientificInput": {
                            "user_query": "research question",
                            "research_context": {"research_object": "x"},
                            "candidates": candidates,
                        },
                        "outputSchema": schema,
                    },
                    ensure_ascii=False,
                ),
            },
        ],
    }


def _batch_policy() -> dict:
    return {
        "contract_version": 1,
        "input_array_field": "candidates",
        "output_array_field": "judgments",
        "identity_field": "candidate_id",
        "max_items_per_batch": 30,
        "max_parallel": 4,
        "partial_failure_mode": "preserve_successes",
    }


def _execution_policy() -> dict:
    return {
        "contractVersion": 1,
        "providerId": "openai_primary",
        "adapterId": "huiyan_chat_completions",
        "model": "terra-model",
        "modelProfile": "terra",
        "reasoningEffort": "none",
        "maxOutputTokens": 6000,
        "readTimeoutSeconds": 120.0,
        "taskBudgetSeconds": 145.0,
        "responseMode": "json_object",
        "loadReferences": False,
        "structuredOutputRecoveryAttempts": 0,
    }


def _response_for_request(content: bytes, *, status_code: int = 200):
    payload = json.loads(content)
    scientific = json.loads(payload["messages"][1]["content"])["scientificInput"]
    judgments = [
        {
            "candidate_id": row["candidate_id"],
            "decision": "priority",
            "reading_priority": 80,
            "reason": "Directly relevant to the research question.",
        }
        for row in scientific["candidates"]
    ]
    body = {
        "choices": [{"message": {"content": json.dumps({"judgments": judgments})}}],
        "model": "terra-model",
    }
    return SimpleNamespace(
        status_code=status_code,
        content=json.dumps(body).encode("utf-8"),
        headers={},
    )


def test_structured_batch_runtime_partitions_and_merges_without_scientific_inference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidates = [_candidate(index) for index in range(65)]
    requests: list[dict] = []
    timeouts: list[httpx.Timeout] = []

    class Client:
        def __init__(self, *, timeout) -> None:
            timeouts.append(timeout)

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def post(self, _url: str, *, headers: dict, content: bytes):
            requests.append(json.loads(content))
            return _response_for_request(content)

    monkeypatch.setattr(durable_skill_runtime, "ProviderHttpAttemptRepository", _AttemptRepository)
    monkeypatch.setattr(durable_skill_runtime.httpx, "Client", Client)
    monkeypatch.setattr(durable_skill_runtime, "_billable_usage_wire_from_body", lambda *_args, **_kwargs: None)

    result = durable_skill_runtime._execute_structured_batch_requests(
        request={
            "providerEvidence": {
                "capabilityCallId": "call:batch-test",
                "attemptNumber": 1,
            }
        },
        skill_id="opentrajectory_source_screening_selection",
        provider_parameters={"candidates": candidates},
        batch_policy=_batch_policy(),
        provider_message_contract="scientific_minimal_v1",
        base_payload=_base_payload(candidates),
        draft_schema=_admissible_batch_schema(),
        runtime_config=SimpleNamespace(
            adapter="huiyan_chat_completions",
            model="terra-model",
            timeoutSeconds=300.0,
        ),
        endpoint="https://provider.test/v1/chat/completions",
        base_headers={"Authorization": "Bearer test"},
        provider_id="openai_primary",
        execution_policy=_execution_policy(),
        task_deadline=None,
        max_output_chars=50_000,
    )

    assert result["successfulBatchCount"] == 3
    assert result["failedBatches"] == []
    assert len(result["draft"]["judgments"]) == 65
    sizes = [
        len(json.loads(row["messages"][1]["content"])["scientificInput"]["candidates"])
        for row in requests
    ]
    assert sorted(sizes) == [5, 30, 30]
    assert all(isinstance(timeout, httpx.Timeout) for timeout in timeouts)
    assert all(timeout.connect <= 5.0 for timeout in timeouts)
    assert all(timeout.write <= 10.0 for timeout in timeouts)
    assert all(timeout.read is None for timeout in timeouts)


def test_structured_batch_runtime_preserves_successful_siblings_when_one_batch_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidates = [_candidate(index) for index in range(65)]
    counter = {"value": 0}

    class Client:
        def __init__(self, *, timeout) -> None:
            return None

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def post(self, _url: str, *, headers: dict, content: bytes):
            index = counter["value"]
            counter["value"] += 1
            if index == 1:
                return SimpleNamespace(status_code=503, content=b"{}", headers={})
            return _response_for_request(content)

    monkeypatch.setattr(durable_skill_runtime, "ProviderHttpAttemptRepository", _AttemptRepository)
    monkeypatch.setattr(durable_skill_runtime.httpx, "Client", Client)
    monkeypatch.setattr(durable_skill_runtime, "_billable_usage_wire_from_body", lambda *_args, **_kwargs: None)

    result = durable_skill_runtime._execute_structured_batch_requests(
        request={
            "providerEvidence": {
                "capabilityCallId": "call:batch-partial",
                "attemptNumber": 1,
            }
        },
        skill_id="opentrajectory_source_screening_selection",
        provider_parameters={"candidates": candidates},
        batch_policy=_batch_policy(),
        provider_message_contract="scientific_minimal_v1",
        base_payload=_base_payload(candidates),
        draft_schema=_admissible_batch_schema(),
        runtime_config=SimpleNamespace(
            adapter="huiyan_chat_completions",
            model="terra-model",
            timeoutSeconds=300.0,
        ),
        endpoint="https://provider.test/v1/chat/completions",
        base_headers={"Authorization": "Bearer test"},
        provider_id="openai_primary",
        execution_policy=_execution_policy(),
        task_deadline=None,
        max_output_chars=50_000,
    )

    assert result["successfulBatchCount"] == 2
    assert len(result["failedBatches"]) == 1
    assert len(result["draft"]["judgments"]) in {35, 60}
    assert result["failedBatches"][0]["reason"] == "http_503"


def test_task_budget_is_soft_and_durable_observation_has_no_fixed_read_kill() -> None:
    deadline = durable_skill_runtime._llm_task_deadline(_execution_policy())
    timeout = durable_skill_runtime._llm_http_timeout(
        runtime_config=SimpleNamespace(timeoutSeconds=300.0),
        execution_policy=_execution_policy(),
        deadline=deadline,
    )
    assert deadline is None
    assert timeout.read is None
    assert timeout.connect == 5.0
    assert timeout.pool == 5.0
    assert timeout.write == 10.0


def test_structured_batch_runtime_localizes_sub_attempt_evidence_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidates = [_candidate(index) for index in range(35)]

    class AttemptRepository:
        def begin(self, **_kwargs):
            return SimpleNamespace(headers={})

        def begin_sub_attempt(self, **_kwargs):
            from app.modules.provider_transport_evidence import ProviderAttemptEvidenceError

            raise ProviderAttemptEvidenceError("provider_sub_attempt_evidence_persistence_failed")

        def mark_io_started(self, *_args, **_kwargs) -> None:
            return None

        def finish(self, *_args, **_kwargs) -> None:
            return None

    class Client:
        def __init__(self, *, timeout) -> None:
            return None

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def post(self, _url: str, *, headers: dict, content: bytes):
            return _response_for_request(content)

    monkeypatch.setattr(durable_skill_runtime, "ProviderHttpAttemptRepository", AttemptRepository)
    monkeypatch.setattr(durable_skill_runtime.httpx, "Client", Client)
    monkeypatch.setattr(
        durable_skill_runtime, "_billable_usage_wire_from_body", lambda *_args, **_kwargs: None
    )

    result = durable_skill_runtime._execute_structured_batch_requests(
        request={
            "providerEvidence": {
                "capabilityCallId": "call:batch-evidence-failure",
                "attemptNumber": 1,
            }
        },
        skill_id="opentrajectory_source_screening_selection",
        provider_parameters={"candidates": candidates},
        batch_policy=_batch_policy(),
        provider_message_contract="scientific_minimal_v1",
        base_payload=_base_payload(candidates),
        draft_schema=_admissible_batch_schema(),
        runtime_config=SimpleNamespace(
            adapter="huiyan_chat_completions",
            model="terra-model",
            timeoutSeconds=300.0,
        ),
        endpoint="https://provider.test/v1/chat/completions",
        base_headers={"Authorization": "Bearer test"},
        provider_id="openai_primary",
        execution_policy=_execution_policy(),
        task_deadline=None,
        max_output_chars=50_000,
    )

    assert result["successfulBatchCount"] == 1
    assert len(result["draft"]["judgments"]) == 30
    assert result["failedBatches"] == [{
        "batchIndex": 2,
        "reason": "provider_attempt_evidence_unavailable",
        "inputIdentities": [candidate["candidate_id"] for candidate in candidates[30:]],
        "failure": {
            "state": "failed",
            "summary": "The Provider physical-attempt evidence could not be prepared.",
            "failureClassification": "provider_attempt_evidence",
            "failedStage": "provider_attempt_evidence",
            "firstMismatch": "provider_attempt_evidence_unavailable",
        },
    }]
