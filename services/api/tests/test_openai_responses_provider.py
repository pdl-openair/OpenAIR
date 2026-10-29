from __future__ import annotations

import json
import hashlib
import threading
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Mapping

import pytest
import httpx

from app.execution.durable_orchestrator_runtime import FrozenOrchestrationExecutionRequest
from app.execution.openai_responses_provider import (
    HttpOpenAIResponsesTransport,
    OpenAIResponsesProviderAdapter,
    ProviderConfigurationError,
    ProviderContractError,
    ProviderExecutionError,
    ProviderAttemptEvidenceUnavailableError,
    ProviderAvailabilityError,
    ProviderRejectedRequestError,
    ProviderResponseError,
    ProviderTransportNotSubmittedError,
    ProviderTransportUncertainError,
    _provider_http_timeout,
    _coerce_provider_document,
    _request_messages,
    _result_from_transport_response,
    _schema_for_provider_prompt,
    _structured_response_from_chat_completions_body,
    diagnose_rejected_response,
)
from app.modules.provider_registry import build_provider_registry
from app.modules.provider_transport_evidence import ProviderHttpAttempt


class FakeTransport:
    is_fake_transport = True

    def __init__(self, response: Mapping[str, Any]) -> None:
        self.response = response
        self.calls = 0
        self.models: list[str] = []

    def execute(self, request: Any, runtime_config: Any) -> tuple[Mapping[str, Any], Mapping[str, Any] | None]:
        self.calls += 1
        self.models.append(request.model)
        return self.response, None




class RecordingAttemptRepository:
    def __init__(self) -> None:
        self.started: list[tuple[str, str]] = []
        self.finishes: list[dict[str, Any]] = []

    def _attempt(self, kwargs: Mapping[str, Any], *, label: str) -> ProviderHttpAttempt:
        attempt = ProviderHttpAttempt(
            attempt_id=f"attempt-{label}",
            capability_call_id=str(kwargs["capability_call_id"]),
            attempt_number=int(kwargs.get("attempt_number") or 2),
            request_hash=hashlib.sha256(kwargs["request_body"]).hexdigest(),
            provider_endpoint_identity="test:endpoint",
            evidence_kind=str(kwargs.get("evidence_kind") or "provider_submission"),
            adapter_name=kwargs.get("adapter_name"),
        )
        self.started.append((label, attempt.attempt_id))
        return attempt

    def begin(self, **kwargs: Any) -> ProviderHttpAttempt:
        return self._attempt(kwargs, label="primary")

    def begin_sub_attempt(self, **kwargs: Any) -> ProviderHttpAttempt:
        return self._attempt(kwargs, label=str(kwargs["adapter_name"]))

    def finish(self, attempt: ProviderHttpAttempt, **kwargs: Any) -> None:
        self.finishes.append({"attempt_id": attempt.attempt_id, **kwargs})


class ReservedAttemptRepository:
    def begin(self, **kwargs: Any) -> ProviderHttpAttempt:
        assert kwargs["capability_call_id"] == "call-http-unit"
        assert kwargs["attempt_number"] == 1
        return ProviderHttpAttempt(
            attempt_id="attempt-http-unit",
            capability_call_id=kwargs["capability_call_id"],
            attempt_number=1,
            request_hash=hashlib.sha256(kwargs["request_body"]).hexdigest(),
            provider_endpoint_identity="test:endpoint",
        )

    def finish(self, _attempt: ProviderHttpAttempt, **_kwargs: Any) -> None:
        return None


def _skill_request() -> dict[str, Any]:
    return {
        "skillId": "academic_source_retrieval_skill",
        "inputMemoryRefs": ["memory_1"],
        "inputArtifactRefs": [],
        "parameters": {"user_query": "bounded query"},
        "reason": "Need verified sources",
        "idempotencyKey": "source-pass-1",
    }


QUESTION_FIELDS = (
    "question_candidates", "scope_boundary", "assumptions",
    "verification_readiness", "next_evidence_needs", "handoff",
)


def _question_artifact() -> dict[str, dict[str, str]]:
    return {field: {"status": "recorded"} for field in QUESTION_FIELDS}


def test_recorded_question_refinement_direct_object_normalizes_only_for_exact_contract() -> None:
    direct = _question_artifact()
    parsed = _structured_response_from_chat_completions_body(
        {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(direct)}}]},
        artifact_contract="research_question_refinement_artifact_v1",
        required_fields=QUESTION_FIELDS,
    )

    assert parsed["title"] == "research_question_refinement_artifact_v1"
    assert parsed["mimeType"] == "text/plain"
    assert json.loads(parsed["content"]) == direct


@pytest.mark.parametrize(
    "raw",
    (
        b'{"question_candidates":',
        json.dumps({"question_candidates": {}}).encode(),
        json.dumps({**_question_artifact(), "unknown": {}}).encode(),
        json.dumps({**_question_artifact(), "handoff": None}).encode(),
    ),
)
def test_recorded_question_refinement_invalid_shapes_fail_closed(raw: bytes) -> None:
    diagnosis = diagnose_rejected_response(
        raw,
        finish_reason="stop",
        artifact_contract="research_question_refinement_artifact_v1",
        required_fields=QUESTION_FIELDS,
    )

    assert diagnosis.category != "valid"


def test_adapter_requires_only_existing_huiyan_configuration_and_records_chat_adapter() -> None:
    fake = FakeTransport({"title": "Answer", "mimeType": "text/plain", "content": "{\"result\":{}}"})
    result = _adapter(fake).execute(_request())

    assert fake.calls == 1
    assert result.metadata == {"providerId": "openai_primary", "adapter": "huiyan_chat_completions", "contentLength": 13}


def test_durable_profiles_select_their_own_environment_models_without_global_fallback() -> None:
    fake = FakeTransport({"title": "Answer", "mimeType": "text/plain", "content": "{\"result\":{}}"})
    adapter = _adapter(fake)

    adapter.execute(_request("terra"))
    adapter.execute(_request("sol"))

    assert fake.models == ["terra-model", "sol-model"]


def test_skill_review_request_uses_its_work_unit_profile_before_transport() -> None:
    fake = FakeTransport({"title": "Answer", "mimeType": "text/plain", "content": "{\"result\":{}}"})

    _adapter(fake).execute(_request("sol", phase="review_skill_result"))

    assert fake.models == ["sol-model"]


def test_final_only_request_forbids_skill_requests_in_the_provider_instruction() -> None:
    prepared = _adapter(FakeTransport({"title": "unused", "mimeType": "text/plain", "content": "unused"})).prepare(
        _request(allow_skill_requests=False)
    )

    instruction = _request_messages(prepared.request)[0]["content"]

    assert "Do not return a skillRequest." in instruction
    assert "or a skillRequest with" not in instruction


def test_final_only_prompt_strips_schema_metadata_and_rejects_contract_envelope() -> None:
    prepared = _adapter(
        FakeTransport({"title": "unused", "mimeType": "text/plain", "content": "unused"}),
    ).prepare(_request(allow_skill_requests=False))
    system = _request_messages(prepared.request)[0]["content"]
    assert "Do not wrap the body in contractId/semanticVersion/body" in system
    assert "Required content keys:" in system
    assert "Schema compliance is fail-closed" in system
    assert '"contractId"' not in system
    assert '"semanticVersion"' not in system
    assert '"$schema"' not in system
    assert '"type":"object"' in system or '"type": "object"' in system


def test_structured_correction_request_contains_validation_feedback_and_no_invention_guard() -> None:
    prepared = _adapter(
        FakeTransport({"title": "unused", "mimeType": "text/plain", "content": "unused"})
    ).prepare(
        _request(allow_skill_requests=False),
        capability_call_id="call-http-unit",
        attempt_number=1,
        structured_correction_attempt=1,
        correction_feedback="category=invalid_json; error=provider_response_invalid_json; path=/",
        previous_output='{"title":"A","mimeType":"text/plain","content":{}} trailing',
    )

    messages = _request_messages(prepared.request)

    assert len(messages) == 4
    assert "provider_response_invalid_json" in messages[-1]["content"]
    assert "not a new research pass" in messages[-1]["content"]
    assert "Do not add citations, values, evidence, claims, conclusions" in messages[-1]["content"]
    assert messages[-2]["role"] == "assistant"
    assert "trailing" in messages[-2]["content"]


def test_artifact_schema_invalid_is_one_adapter_submit_and_preserves_usage() -> None:
    class InvalidTransport:
        is_fake_transport = True

        def __init__(self) -> None:
            self.requests: list[Any] = []

        def execute(self, request: Any, runtime_config: Any) -> tuple[Mapping[str, Any], Mapping[str, Any] | None]:
            self.requests.append(request)
            return {
                "title": "Broken",
                "mimeType": "text/plain",
                "content": json.dumps({"result": {}}, separators=(",", ":")),
            }, {"inputTokens": 4, "outputTokens": 1, "totalTokens": 5}

    transport = InvalidTransport()
    result = _adapter(transport).execute(_nested_schema_request())

    assert len(transport.requests) == 1
    assert json.loads(result.content) == {"result": {}}
    assert result.usage == {
        "inputTokens": 4,
        "outputTokens": 1,
        "totalTokens": 5,
    }


def test_authorized_correction_schema_failure_does_not_trigger_third_submit() -> None:
    class InvalidCorrectionTransport:
        is_fake_transport = True

        def __init__(self) -> None:
            self.requests: list[Any] = []

        def execute(self, request: Any, runtime_config: Any) -> tuple[Mapping[str, Any], Mapping[str, Any] | None]:
            self.requests.append(request)
            return {
                "title": "Broken",
                "mimeType": "text/plain",
                "content": json.dumps({"result": {}}, separators=(",", ":")),
            }, None

    transport = InvalidCorrectionTransport()
    result = _adapter(transport).execute(
        _nested_schema_request(),
        structured_correction_attempt=1,
        correction_feedback="artifact_schema_invalid",
        previous_output='{"title":"Broken","mimeType":"text/plain","content":{"result":{}}}',
    )

    assert len(transport.requests) == 1
    assert transport.requests[0].structuredCorrectionAttempt == 1
    assert json.loads(result.content) == {"result": {}}


def test_schema_for_provider_prompt_drops_document_metadata() -> None:
    cleaned = _schema_for_provider_prompt({
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "contractId": "demo",
        "semanticVersion": "1.0.0",
        "title": "demo",
        "type": "object",
        "required": ["a"],
        "properties": {"a": {"type": "string"}},
        "$defs": {"x": {"type": "string"}},
    })
    assert cleaned == {
        "type": "object",
        "required": ["a"],
        "properties": {"a": {"type": "string"}},
        "$defs": {"x": {"type": "string"}},
    }


def test_coerce_provider_document_unwraps_contract_body_envelope() -> None:
    required = ("search_boundary", "handoff")
    body = {"search_boundary": {"x": 1}, "handoff": {"y": 2}}
    coerced = _coerce_provider_document(
        {"contractId": "key_literature_positioning_artifact_v1", "semanticVersion": "1.0.0", "body": body},
        required,
    )
    assert coerced == body


def test_structured_response_accepts_contract_body_envelope() -> None:
    required = QUESTION_FIELDS
    body = {field: {"status": "recorded"} for field in required}
    wire = {
        "choices": [{
            "finish_reason": "stop",
            "message": {
                "content": json.dumps({
                    "contractId": "question_refinement_artifact_v1",
                    "semanticVersion": "1.0.0",
                    "body": body,
                }),
            },
        }],
    }
    structured = _structured_response_from_chat_completions_body(
        wire,
        artifact_contract="question_refinement_artifact_v1",
        required_fields=required,
    )
    assert structured["mimeType"] == "text/plain"
    assert json.loads(structured["content"]) == body


def test_default_request_keeps_skill_request_instruction_for_durable_orchestration() -> None:
    prepared = _adapter(FakeTransport({"title": "unused", "mimeType": "text/plain", "content": "unused"})).prepare(
        _request(allow_skill_requests=True)
    )

    assert "or a skillRequest with" in _request_messages(prepared.request)[0]["content"]


@pytest.mark.parametrize("profile", ("terra", "sol"))
def test_missing_or_empty_profile_model_fails_before_transport(profile: str) -> None:
    for value in (None, ""):
        environment = _environment()
        environment["LLM_PROVIDER_MODEL_TERRA" if profile == "terra" else "LLM_PROVIDER_MODEL_SOL"] = value or ""
        fake = FakeTransport({"title": "unused", "mimeType": "text/plain", "content": "unused"})
        with pytest.raises(ProviderConfigurationError):
            _adapter(fake, environ=environment).execute(_request(profile))
        assert fake.calls == 0


@pytest.mark.parametrize("environment", ({}, {"LLM_PROVIDER_MODEL": "m"}, {"LLM_PROVIDER_BASE_URL": "https://x"}))
def test_missing_existing_huiyan_configuration_fails_closed_before_transport(environment: dict[str, str]) -> None:
    fake = FakeTransport({"title": "unused", "mimeType": "text/plain", "content": "unused"})
    with pytest.raises(ProviderConfigurationError):
        _adapter(fake, environ={"OPENAIR_ENV": "test", **environment}).execute(_request())
    assert fake.calls == 0


def test_real_http_transport_without_provider_evidence_submits_zero_requests() -> None:
    submissions = 0

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            nonlocal submissions
            submissions += 1
            self.send_response(500)
            self.end_headers()

        def log_message(self, format: str, *args: Any) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with pytest.raises(ProviderAttemptEvidenceUnavailableError, match="provider_attempt_evidence"):
            _adapter(
                HttpOpenAIResponsesTransport(),
                environ=_environment(f"http://127.0.0.1:{server.server_port}"),
            ).execute(_request())
    finally:
        server.shutdown()
        thread.join()
        server.server_close()

    assert submissions == 0


def _prepared_http_transport_request():
    prepared = _adapter(FakeTransport({"title": "unused", "mimeType": "text/plain", "content": "unused"})).prepare(_request())
    return replace(
        prepared.request,
        capabilityCallId="call-http-unit",
        attemptNumber=1,
    ), prepared.runtime_config


def test_provider_http_timeout_keeps_read_observer_unbounded() -> None:
    _request_value, runtime_config = _prepared_http_transport_request()

    timeout = _provider_http_timeout(runtime_config)

    assert timeout.read is None
    assert 0 < timeout.connect <= 5.0
    assert 0 < timeout.pool <= 5.0
    assert 0 < timeout.write <= 10.0


def test_connect_failure_is_proven_not_submitted_and_keeps_execution_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    request, runtime_config = _prepared_http_transport_request()
    repository = RecordingAttemptRepository()
    seen_timeouts: list[httpx.Timeout] = []

    class Client:
        def __init__(self, *, timeout: httpx.Timeout) -> None:
            seen_timeouts.append(timeout)
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            return False
        def post(self, *_args, **_kwargs):
            raise httpx.ConnectError("connect failed", request=httpx.Request("POST", "https://provider.invalid"))

    monkeypatch.setattr("app.execution.openai_responses_provider.httpx.Client", Client)

    with pytest.raises(ProviderTransportNotSubmittedError) as captured:
        HttpOpenAIResponsesTransport(repository).execute(request, runtime_config)

    assert captured.value.provider_execution_id == "attempt-primary"
    assert seen_timeouts and seen_timeouts[0].read is None
    assert repository.finishes[-1]["outcome"] == "transport_not_submitted"


def test_read_transport_loss_is_unknown_without_replay_and_keeps_execution_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    request, runtime_config = _prepared_http_transport_request()
    repository = RecordingAttemptRepository()
    posts = 0

    class Client:
        def __init__(self, *, timeout: httpx.Timeout) -> None:
            assert timeout.read is None
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            return False
        def post(self, *_args, **_kwargs):
            nonlocal posts
            posts += 1
            raise httpx.ReadError("response lost", request=httpx.Request("POST", "https://provider.invalid"))

    monkeypatch.setattr("app.execution.openai_responses_provider.httpx.Client", Client)

    with pytest.raises(ProviderTransportUncertainError) as captured:
        HttpOpenAIResponsesTransport(repository).execute(request, runtime_config)

    assert captured.value.provider_execution_id == "attempt-primary"
    assert posts == 1
    assert repository.finishes[-1]["outcome"] == "transport_uncertain"


def test_structured_correction_reserves_sub_attempt_identity_before_http(monkeypatch: pytest.MonkeyPatch) -> None:
    request, runtime_config = _prepared_http_transport_request()
    request = replace(
        request,
        structuredCorrectionAttempt=1,
        correctionFeedback="artifact_schema_invalid",
        previousOutput='{"title":"Broken","mimeType":"text/plain","content":{"result":{}}}',
    )
    repository = RecordingAttemptRepository()

    body = json.dumps({
        "choices": [{"finish_reason": "stop", "message": {"content": json.dumps({
            "title": "Repaired", "mimeType": "text/plain", "content": {"result": {}}
        })}}]
    }).encode("utf-8")

    class Client:
        def __init__(self, *, timeout: httpx.Timeout) -> None:
            assert timeout.read is None
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            return False
        def post(self, *_args, **_kwargs):
            assert repository.started == [
                ("structured_output_correction", "attempt-structured_output_correction")
            ]
            return httpx.Response(200, content=body, request=httpx.Request("POST", "https://provider.invalid"))

    monkeypatch.setattr("app.execution.openai_responses_provider.httpx.Client", Client)

    structured, _usage = HttpOpenAIResponsesTransport(repository).execute(request, runtime_config)

    assert structured["title"] == "Repaired"
    assert repository.finishes[-1]["attempt_id"] == "attempt-structured_output_correction"
    assert repository.finishes[-1]["outcome"] == "response_received"


def test_loopback_transport_honors_thinking_and_max_tokens_env(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, Any]] = []
    monkeypatch.setenv("OPENAIR_DURABLE_ORCHESTRATE_LLM_THINKING", "disabled")
    monkeypatch.setenv("OPENAIR_DURABLE_ORCHESTRATE_LLM_MAX_TOKENS", "2400")

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            seen.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            body = json.dumps({"choices": [{"message": {"content": json.dumps({"title": "Loopback", "mimeType": "text/plain", "content": {"result": {}}})}}]}).encode()
            self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        _execute_http(f"http://127.0.0.1:{server.server_port}/v1")
    finally:
        server.shutdown(); thread.join()

    assert len(seen) == 1
    assert seen[0]["thinking"] == {"type": "disabled"}
    assert seen[0]["reasoning_effort"] == "none"
    assert seen[0]["max_tokens"] == 2400


def test_admitted_durable_payload_is_the_exact_http_payload() -> None:
    seen: list[dict[str, Any]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            seen.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            body = json.dumps({
                "choices": [{
                    "message": {
                        "content": json.dumps({
                            "title": "Exact",
                            "mimeType": "text/plain",
                            "content": {"result": {}},
                        })
                    }
                }]
            }).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        transport = HttpOpenAIResponsesTransport(ReservedAttemptRepository())
        adapter = _adapter(
            transport,
            environ=_environment(f"http://127.0.0.1:{server.server_port}"),
        )
        prepared = adapter.prepare(_request())
        prepared = replace(
            prepared,
            request=replace(
                prepared.request,
                capabilityCallId="call-http-unit",
                attemptNumber=1,
            ),
        )
        _url, admitted_payload = transport._build_payload(
            prepared.request,
            prepared.runtime_config,
        )
        adapter.execute_prepared(prepared)
    finally:
        server.shutdown()
        thread.join()
        server.server_close()

    assert seen == [admitted_payload]


@pytest.mark.parametrize(("status", "error"), ((400, ProviderResponseError), (500, ProviderAvailabilityError)))
def test_loopback_http_statuses_remain_known_or_uncertain(status: int, error: type[ProviderExecutionError]) -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            # Drain the request body before responding so keep-alive / truncated
            # reads cannot turn a deterministic status mapping into transport noise.
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            self.send_response(status); self.send_header("Content-Length", "0"); self.end_headers()

        def log_message(self, format: str, *args: Any) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        with pytest.raises(error):
            _execute_http(f"http://127.0.0.1:{server.server_port}")
    finally:
        server.shutdown(); thread.join(); server.server_close()


def test_malformed_chat_body_is_known_response_failure() -> None:
    fake = FakeTransport({"choices": []})
    # Fake transports return structured data, so malformed validation is covered
    # by the production parser through a loopback 200 response instead.
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            self.rfile.read(int(self.headers["Content-Length"]))
            body = b'{"choices":[]}'
            self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
        def log_message(self, format: str, *args: Any) -> None: return
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler); thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        with pytest.raises(ProviderContractError):
            _execute_http(f"http://127.0.0.1:{server.server_port}")
    finally:
        server.shutdown(); thread.join()


def test_production_parser_accepts_exact_final_artifact() -> None:
    result = _parse({"title": "Answer", "mimeType": "text/plain", "content": "result"})

    assert result.content == "result"
    assert result.skillRequest is None


def test_production_parser_accepts_exact_skill_request() -> None:
    request = _skill_request()

    result = _parse({"skillRequest": request})

    assert dict(result.skillRequest or {}) == request
    assert result.content is None


def test_structured_output_422_is_terminal_without_compatibility_resubmission() -> None:
    calls = 0

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            nonlocal calls
            calls += 1
            # Always consume the posted body first. Leaving unread bytes on the
            # loopback socket has historically surfaced as non-422 statuses /
            # transport failures when this test shares a process with siblings.
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            if calls == 1:
                error = b'{"error":"unsupported response_format"}'
                self.send_response(422); self.send_header("Content-Length", str(len(error))); self.end_headers(); self.wfile.write(error)
                return
            self.send_response(500); self.send_header("Content-Length", "0"); self.end_headers()

        def log_message(self, format: str, *args: Any) -> None: return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler); thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        with pytest.raises(ProviderRejectedRequestError):
            _execute_http(f"http://127.0.0.1:{server.server_port}")
    finally:
        server.shutdown(); thread.join(); server.server_close()
    assert calls == 1


def test_non_json_output_is_contract_failure() -> None:
    with pytest.raises(ProviderContractError):
        _parse({"title": "Answer", "mimeType": "application/json", "content": "{}"})


def test_non_success_finish_reason_rejects_valid_inner_json_without_leaking_content() -> None:
    from app.execution.openai_responses_provider import _structured_response_from_chat_completions_body

    canary = "fictional-sensitive-finish-canary"
    body = {
        "choices": [
            {
                "finish_reason": "length",
                "message": {
                    "content": json.dumps(
                        {"title": "Answer", "mimeType": "text/plain", "content": canary}
                    )
                },
            }
        ]
    }
    with pytest.raises(ProviderContractError) as caught:
        _structured_response_from_chat_completions_body(body)
    assert str(caught.value) == "provider_response_contract_invalid"
    assert canary not in str(caught.value)
    assert caught.value.diagnosis.category == "finish_reason_non_success"


@pytest.mark.parametrize(
    "response",
    (
        {"title": "Answer", "mimeType": "text/plain", "content": "result", "skillRequest": {}},
        {"title": "Answer", "mimeType": "text/plain", "content": "result", "unexpected": True},
        {"skillRequest": {**{"skillId": "approved"}, "unexpected": True}},
        {"title": "Answer", "mimeType": "text/plain"},
        {"skillRequest": {"skillId": "approved"}},
        {"skillRequest": {**_skill_request(), "parameters": []}},
        {"skillRequest": {**_skill_request(), "inputMemoryRefs": "memory_1"}},
    ),
)
def test_production_parser_rejects_mixed_extra_or_malformed_actions(response: Mapping[str, Any]) -> None:
    with pytest.raises(ProviderResponseError):
        _parse(response)


def _adapter(transport: Any, *, environ: Mapping[str, str] | None = None) -> OpenAIResponsesProviderAdapter:
    return OpenAIResponsesProviderAdapter(transport=transport, registry=_registry(), environ=environ or _environment())


def _execute_http(base_url: str):
    adapter = _adapter(
        HttpOpenAIResponsesTransport(ReservedAttemptRepository()),
        environ=_environment(base_url),
    )
    prepared = adapter.prepare(_request())
    prepared = replace(
        prepared,
        request=replace(
            prepared.request,
            capabilityCallId="call-http-unit",
            attemptNumber=1,
        ),
    )
    return adapter.execute_prepared(prepared)


def _parse(response: Mapping[str, Any]):
    return _result_from_transport_response(response, 64, "openai_primary", "huiyan_chat_completions")


def _registry() -> Any:
    return build_provider_registry({"schemaVersion": 1, "defaultProviderId": "openai_primary", "providers": [{
        "providerId": "openai_primary", "adapter": "huiyan_chat_completions", "maxOutputChars": 64,
        "submissionSafety": {
            "submissionMode": "single_attempt_no_replay",
            "acceptsIdempotencyKey": False,
            "returnsReceiptOrRemoteRunId": False,
            "queryByIdempotencyKey": False,
            "queryByReceiptOrRemoteRunId": False,
        },
        "environment": {"model": "LLM_PROVIDER_MODEL", "baseUrl": "LLM_PROVIDER_BASE_URL", "apiKey": "LLM_PROVIDER_API_KEY"},
    }]})


def _environment(base_url: str = "https://provider.invalid") -> dict[str, str]:
    return {"OPENAIR_ENV": "test", "LLM_PROVIDER_MODEL": "legacy-model", "LLM_PROVIDER_MODEL_TERRA": "terra-model", "LLM_PROVIDER_MODEL_SOL": "sol-model", "LLM_PROVIDER_BASE_URL": base_url, "LLM_PROVIDER_API_KEY": "test-key", "OPENAIR_DURABLE_ORCHESTRATE_TIMEOUT_SECONDS": "30"}


def _request(
    profile: str = "terra", *, phase: str | None = None, allow_skill_requests: bool = False,
) -> FrozenOrchestrationExecutionRequest:
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema", "contractId": "test_artifact_v1",
        "semanticVersion": "1.0.0", "type": "object", "additionalProperties": False,
        "required": ["result"], "properties": {"result": {}},
    }
    return _request_with_schema(schema, profile=profile, phase=phase, allow_skill_requests=allow_skill_requests)


def _nested_schema_request() -> FrozenOrchestrationExecutionRequest:
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "contractId": "test_artifact_v1",
        "semanticVersion": "1.0.0",
        "type": "object",
        "additionalProperties": False,
        "required": ["result"],
        "properties": {
            "result": {
                "type": "object",
                "additionalProperties": False,
                "required": ["status"],
                "properties": {"status": {"type": "string", "const": "ok"}},
            }
        },
    }
    return _request_with_schema(schema, allow_skill_requests=False)


def _request_with_schema(
    schema: dict[str, Any],
    *,
    profile: str = "terra",
    phase: str | None = None,
    allow_skill_requests: bool = False,
) -> FrozenOrchestrationExecutionRequest:
    digest = __import__("hashlib").sha256(json.dumps(schema, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode()).hexdigest()
    plan = {
        "context": {"runId": "run", "taskId": "task", "ownerId": "owner", "workspaceId": "workspace"},
        "selectedAgentId": "agent",
        "selectedSkillIds": [],
        "selectedProvider": "openai_primary",
        "providerPolicy": {"allowedProviders": ["openai_primary"], "defaultProvider": "openai_primary"},
        "artifactContract": "test_artifact_v1",
        "artifactContractSchemaVersion": 1,
        "artifactRequiredFields": ["result"],
        "artifactSemanticVersion": "1.0.0", "artifactSchemaDigest": digest,
        "artifactSchema": schema, "artifactSchemaRef": "tests/test-schema.json",
    }
    snapshot: dict[str, Any] = {"task": "frozen"}
    if phase:
        snapshot["activeWorkUnit"] = {"phase": phase}
    return FrozenOrchestrationExecutionRequest(runId="run", taskId="task", ownerId="owner", workspaceId="workspace", orchestrationPlan=plan, frozenInputSnapshot=snapshot, selectedAgentId="agent", selectedSkillIds=(), logicalProviderId="openai_primary", modelProfile=profile, artifactContract="test_artifact_v1", artifactContractSchemaVersion=1, artifactRequiredFields=("result",), artifactSemanticVersion="1.0.0", artifactSchemaDigest=digest, artifactSchema=schema, artifactSchemaRef="tests/test-schema.json", allowSkillRequests=allow_skill_requests)


def _chat_body(content: str) -> dict[str, Any]:
    return {
        "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
    }


def test_mechanical_normalizer_accepts_markdown_fenced_direct_semantic_object_without_model_retry() -> None:
    result = _structured_response_from_chat_completions_body(
        _chat_body('```json\n{"result":{"status":"ok"}}\n```'),
        artifact_contract="test_artifact_v1",
        required_fields=("result",),
    )

    assert result == {
        "title": "test_artifact_v1",
        "mimeType": "text/plain",
        "content": '{"result":{"status":"ok"}}',
    }


def test_mechanical_normalizer_extracts_single_json_object_from_surrounding_prose() -> None:
    result = _structured_response_from_chat_completions_body(
        _chat_body('Here is the requested object:\n{"result":{"status":"ok"}}\nDone.'),
        artifact_contract="test_artifact_v1",
        required_fields=("result",),
    )

    assert result["content"] == '{"result":{"status":"ok"}}'


def test_mechanical_normalizer_accepts_python_dict_literal_without_changing_payload() -> None:
    result = _structured_response_from_chat_completions_body(
        _chat_body("{'result': {'status': 'ok'}}"),
        artifact_contract="test_artifact_v1",
        required_fields=("result",),
    )

    assert json.loads(result["content"]) == {"result": {"status": "ok"}}


def test_mechanical_normalizer_completes_only_missing_envelope_fields_when_content_is_complete() -> None:
    result = _structured_response_from_chat_completions_body(
        _chat_body('{"content":{"result":{"status":"ok"}}}'),
        artifact_contract="test_artifact_v1",
        required_fields=("result",),
    )

    assert result == {
        "title": "test_artifact_v1",
        "mimeType": "text/plain",
        "content": '{"result":{"status":"ok"}}',
    }


def test_mechanical_normalizer_refuses_ambiguous_multiple_objects() -> None:
    with pytest.raises(ProviderContractError):
        _structured_response_from_chat_completions_body(
            _chat_body('{"result":{"status":"first"}} {"result":{"status":"second"}}'),
            artifact_contract="test_artifact_v1",
            required_fields=("result",),
        )
