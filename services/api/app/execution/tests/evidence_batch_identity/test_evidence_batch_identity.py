from __future__ import annotations

import json
from copy import deepcopy
from types import SimpleNamespace

import httpx
from app.execution import durable_skill_runtime as runtime
from app.execution.evidence_batch_identity import evidence_paper_operation_identity
from app.modules.provider_transport_evidence import DuplicateProviderAttemptError


def _parameters(candidate: str) -> dict:
    return {
        "task_id": "protocol-fixture",
        "evidence_schema": {"evidence_targets": []},
        "paper_packets": [{"candidate_id": candidate, "sections": [{"content": candidate}]}],
    }


def _offline_executor(monkeypatch, statuses: list[int]):
    terminal: set[str] = set()
    attempts: list[str] = []
    submissions: list[str] = []

    class Attempts:
        def begin_sub_attempt(self, **kwargs):
            operation = kwargs["operation_identity"]
            if operation in terminal:
                raise DuplicateProviderAttemptError("provider_execution_terminal_replay_forbidden")
            attempts.append(operation)
            return SimpleNamespace(headers={}, attempt_id=operation)

        def mark_io_started(self, _attempt):
            return None

        def finish(self, attempt, **kwargs):
            if kwargs["http_status"] == 200:
                terminal.add(attempt.attempt_id)

    class Client:
        def __init__(self, **_kwargs):
            return None

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def post(self, _url, *, headers, content):
            submissions.append(content.decode())
            status = statuses.pop(0)
            return httpx.Response(
                status,
                json={"choices": [{"message": {"content": '{"evidence_items": []}'}}]},
            )

    monkeypatch.setattr(runtime, "ProviderHttpAttemptRepository", Attempts)
    monkeypatch.setattr(runtime.httpx, "Client", Client)
    monkeypatch.setattr(runtime, "_llm_http_timeout", lambda **_kwargs: 1)
    monkeypatch.setattr(
        runtime, "_admit_batch_provider_payload", lambda payload, **_kwargs: payload
    )
    monkeypatch.setattr(
        runtime,
        "_payload_for_evidence_paper",
        lambda _base, **kwargs: {"paper": kwargs["packet"], "retry": kwargs["retry"]},
    )
    monkeypatch.setattr(runtime, "_billable_usage_wire_from_body", lambda *_args, **_kwargs: None)

    def execute(candidate: str, wave: str):
        parameters = _parameters(candidate)
        return runtime._execute_evidence_batch_requests(
            request={
                "providerEvidence": {
                    "capabilityCallId": "same-parent",
                    "evidenceKind": "sub_call",
                    "adapterName": wave,
                }
            },
            skill_id="pvd_evidence_node_extraction",
            parameters=parameters,
            provider_parameters=deepcopy(parameters),
            field="evidence_items",
            base_payload={},
            draft_schema={
                "type": "object",
                "required": ["evidence_items"],
                "properties": {
                    "evidence_items": {"type": "array", "items": {"type": "object"}},
                },
            },
            runtime_config=SimpleNamespace(adapter="huiyan_chat_completions", model="offline"),
            endpoint="https://offline.invalid",
            base_headers={},
            provider_id="offline",
            max_output_chars=1000,
        )

    return execute, attempts, submissions


def test_later_wave_new_paper_executes_but_completed_work_replay_is_blocked(monkeypatch):
    execute, attempts, submissions = _offline_executor(monkeypatch, [200, 200])
    assert execute("paper-a", "wave-1")["successfulPaperCount"] == 1
    assert execute("paper-b", "wave-2")["successfulPaperCount"] == 1
    assert attempts[0] != attempts[1]
    replay = execute("paper-a", "wave-1")
    assert replay["successfulPaperCount"] == 0
    assert len(submissions) == 2


def test_bounded_provider_retry_preserves_paper_work_identity(monkeypatch):
    execute, attempts, submissions = _offline_executor(monkeypatch, [429, 200])
    assert execute("paper-a", "wave-1")["successfulPaperCount"] == 1
    assert len(submissions) == 2
    assert attempts[0] == attempts[1]
    assert json.loads(submissions[0])["retry"] is False
    assert json.loads(submissions[1])["retry"] is True


def test_frozen_work_identity_is_independent_of_packet_order_and_mapping_key_order():
    parameters = _parameters("paper-a")
    packet = parameters["paper_packets"][0]
    scope = {"adapterName": "wave-1"}
    original = evidence_paper_operation_identity("skill", parameters, packet, scope)
    reordered = dict(reversed(list(parameters.items())))
    reordered["paper_packets"] = [_parameters("other")["paper_packets"][0], packet]
    assert original == evidence_paper_operation_identity("skill", reordered, packet, scope)
    assert original != evidence_paper_operation_identity(
        "skill", parameters, packet, {"adapterName": "wave-2"}
    )
    changed = deepcopy(packet)
    changed["sections"][0]["content"] = "changed frozen source"
    assert original != evidence_paper_operation_identity("skill", parameters, changed, scope)
