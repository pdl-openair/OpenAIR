"""Real HTTPX transport, isolated network/evidence sinks; never a Fresh result."""

import json
from collections.abc import Iterator
from types import SimpleNamespace

import httpx
import pytest
from app.execution import research_start_provider_adapter as adapter
from app.execution.research_start_processing import observe_planning_response
from app.execution.research_start_provider_adapter import (
    HttpxResearchStartTransport,
    PreparedPlannerAttempt,
    ProviderContractError,
    ProviderKnownFailureError,
    ProviderTransportNotSubmittedError,
    ProviderTransportUncertainError,
    decode_recorded_planner_response,
    execute_prepared_attempt,
)
from app.modules import provider_transport_evidence as evidence
from app.modules.research_start_response_evidence import describe_response
from app.modules.research_start_thinking import fold_thinking_transcript
from app.modules.research_start_thinking_store import (
    _WRITER,
    SessionThinkingWriter,
    push_thinking_writer,
    reset_thinking_writer,
)

ENDPOINT = "https://provider.example.test/v1/chat/completions"
THOUGHT = 'data: {"choices":[{"delta":{"reasoning_content":"先核对研究依据。"}}]}\n\n'
ANSWER = 'data: {"id":"completion-1","choices":[{"delta":{"content":"answer"}}]}\n\n'
DONE = "data: [DONE]\n\n"
FINISH = 'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'


class EventStream(httpx.SyncByteStream):
    def __init__(self, pieces: list, events: list) -> None:
        self.pieces = pieces
        self.events = events

    def __iter__(self) -> Iterator[bytes]:
        for piece in self.pieces:
            self.events.append("receive")
            if isinstance(piece, Exception):
                raise piece
            yield piece.encode("utf-8")


@pytest.fixture
def boundary(monkeypatch: pytest.MonkeyPatch):
    """Use production HTTPX and thinking writer, replacing only external sinks."""
    state = SimpleNamespace(
        pieces=[THOUGHT, ANSWER, DONE],
        status=200,
        requests=[],
        events=[],
        begins=[],
        finishes=[],
        responses=[],
        transcript=[],
        error=None,
    )

    def persist(**values):
        state.events.append("publish")
        state.transcript = fold_thinking_transcript(
            state.transcript,
            key="research_planner:1",
            text=values["text"],
            seal=values["seal"],
        )

    writer = SessionThinkingWriter(
        request_id="isolated",
        phase="research_planner",
        ordinal=1,
        persist=persist,
    )
    state.writer = writer
    token = push_thinking_writer(writer)

    class Repository:
        def begin(self, **values):
            state.begins.append(values)
            return SimpleNamespace(headers={"x-openair-attempt": "isolated-attempt"})

        def mark_io_started(self, _attempt):
            state.events.append("io_started")

        def finish(self, _attempt, **values):
            state.finishes.append(values)
            state.events.append("settled")

    def handler(request):
        state.requests.append(request)
        if state.error:
            raise state.error
        return httpx.Response(
            state.status,
            request=request,
            headers={"content-type": "text/event-stream", "x-request-id": "route-1"},
            stream=EventStream(state.pieces, state.events),
        )

    client_type = httpx.Client
    monkeypatch.setattr(
        adapter.httpx,
        "Client",
        lambda **options: client_type(transport=httpx.MockTransport(handler), **options),
    )
    monkeypatch.setattr(evidence, "ProviderHttpAttemptRepository", Repository)
    monkeypatch.setenv("LLM_PROVIDER_API_KEY", "isolated-key")
    with (
        evidence.provider_execution_parent(
            kind="research_start",
            parent_id="isolated",
            originating_authority="test",
        ),
        observe_planning_response(
            lambda _prepared, raw, metadata: state.responses.append((raw, metadata)),
        ),
    ):
        try:
            yield state
        finally:
            writer.seal()
            reset_thinking_writer(token)


def prepared(provider_schema=None):
    return PreparedPlannerAttempt(
        phase="research_planner",
        ordinal=1,
        provider="huiyan_chat_completions",
        model="isolated-model",
        endpoint=ENDPOINT,
        payload={"model": "isolated-model", "messages": []},
        response_format={"type": "json_object"},
        timeout_seconds=20,
        decode=lambda body: dict(body),
        provider_schema=provider_schema,
    )


def run(provider_schema=None):
    return execute_prepared_attempt(
        prepared(provider_schema),
        transport=HttpxResearchStartTransport(),
    )


def recorded_metadata(raw, metadata):
    return describe_response(
        raw,
        scope={
            "ownerId": "owner",
            "workspaceId": "space",
            "requestId": "req",
            "attemptId": "attempt",
        },
        metadata=metadata,
    )["metadata"]


def test_stream_response_identity_and_physical_payload_are_preserved(boundary):
    body = run()
    assert body["choices"][0]["message"]["content"] == "answer"
    assert len(boundary.requests) == len(boundary.begins) == len(boundary.finishes) == 1
    actual = json.loads(boundary.requests[0].content)
    assert actual["stream"] is True
    assert json.loads(boundary.begins[0]["request_body"]) == actual
    result = boundary.finishes[0]
    assert result["outcome"] == "response_received"
    assert result["http_status"] == 200
    assert result["response_headers"]["x-request-id"] == "route-1"
    assert boundary.responses[0][1]["providerRequestId"] == "route-1"


def test_reasoning_is_published_before_next_network_chunk(boundary):
    run()
    events = boundary.events
    first = events.index("receive")
    second = events.index("receive", first + 1)
    assert events.index("publish") < second
    assert boundary.transcript[0]["text"] == "先核对研究依据。"


def test_schema_transport_and_stream_options_share_the_recorded_request(boundary):
    schema = {
        "type": "object",
        "required": ["accepted"],
        "properties": {"accepted": {"type": "boolean"}},
        "additionalProperties": False,
    }
    boundary.pieces = ['{"accepted":true}']
    assert run(schema) == {"accepted": True}
    actual = json.loads(boundary.requests[0].content)
    assert actual["response_format"] == {"type": "json_object"}
    assert actual["stream"] is True
    assert json.loads(boundary.begins[0]["request_body"]) == actual
    assert len(boundary.requests) == 1


def test_prior_formed_thought_survives_a_later_interruption(boundary):
    prior = {"key": "understanding:1", "state": "formed", "text": "已经明确研究目标。"}
    boundary.transcript = [dict(prior)]
    boundary.pieces = [THOUGHT, httpx.ReadError("isolated interruption")]
    with pytest.raises(ProviderTransportUncertainError):
        run()
    boundary.writer.seal()
    assert boundary.transcript[0] == prior
    assert boundary.transcript[1]["text"] == "先核对研究依据。"
    assert boundary.transcript[1]["state"] == "formed"


@pytest.mark.parametrize("prefix", [[], [THOUGHT], [ANSWER]])
def test_interruption_never_submits_a_hidden_second_request(boundary, prefix):
    boundary.pieces = [*prefix, httpx.ReadError("isolated interruption")]
    with pytest.raises(ProviderTransportUncertainError):
        run()
    assert len(boundary.requests) == 1
    assert boundary.finishes[0]["outcome"] == "transport_uncertain"
    if prefix == [THOUGHT]:
        assert boundary.transcript[0]["text"] == "先核对研究依据。"


@pytest.mark.parametrize("status", [400, 401, 429, 500])
def test_explicit_http_failure_remains_one_recorded_failure(boundary, status):
    boundary.status = status
    boundary.pieces = ['{"error":{"type":"invalid_request"}}']
    with pytest.raises(ProviderKnownFailureError) as caught:
        run()
    assert caught.value.status_code == status
    assert len(boundary.requests) == 1
    assert boundary.finishes[0]["outcome"] == "provider_http_error"
    assert boundary.finishes[0]["http_status"] == status


def test_complete_malformed_stream_is_saved_before_contract_rejection(boundary):
    boundary.pieces = [THOUGHT, "data: {broken\n\n", DONE]
    with pytest.raises(ProviderContractError):
        run()
    assert len(boundary.requests) == 1
    assert boundary.finishes[0]["outcome"] == "response_received"
    assert b"{broken" in boundary.responses[0][0]
    assert boundary.transcript[0]["text"] == "先核对研究依据。"


def test_unfinished_stream_is_unknown_even_when_answer_is_valid_json(boundary):
    boundary.pieces = [THOUGHT, ANSWER]
    with pytest.raises(ProviderTransportUncertainError):
        run()
    assert len(boundary.requests) == 1
    assert boundary.finishes[0]["outcome"] == "transport_uncertain"


def test_finish_marker_without_done_is_a_complete_response(boundary):
    boundary.pieces = [THOUGHT, ANSWER, FINISH]
    assert run()["choices"][0]["finish_reason"] == "stop"


def test_plain_json_response_to_stream_request_still_works(boundary):
    boundary.pieces = ['{"choices":[{"message":{"content":"answer"}}]}']
    assert run()["choices"][0]["message"]["content"] == "answer"
    assert len(boundary.requests) == 1


def test_proven_connect_failure_keeps_existing_not_submitted_classification(boundary):
    boundary.error = httpx.ConnectError("isolated not connected")
    with pytest.raises(ProviderTransportNotSubmittedError):
        run()
    assert len(boundary.requests) == 1
    assert boundary.finishes[0]["outcome"] == "transport_not_submitted"


def test_no_thinking_writer_uses_one_non_stream_request(boundary):
    token = _WRITER.set(None)
    boundary.pieces = ['{"accepted":true}']
    try:
        assert run() == {"accepted": True}
    finally:
        reset_thinking_writer(token)
    assert len(boundary.requests) == 1
    assert "stream" not in json.loads(boundary.requests[0].content)


def test_recorded_stream_decodes_without_submission_or_a_live_thinking_writer(boundary):
    body = run()
    raw, metadata = boundary.responses[0]
    record = recorded_metadata(raw, metadata)
    token = _WRITER.set(None)
    try:
        assert decode_recorded_planner_response(prepared(), raw, metadata=record) == body
        with pytest.raises(ValueError, match="does not match"):
            decode_recorded_planner_response(
                prepared(),
                raw,
                metadata={**record, "requestStream": False},
            )
    finally:
        reset_thinking_writer(token)
    assert len(boundary.requests) == 1


def test_historical_plain_response_metadata_without_stream_flag_still_matches(boundary):
    token = _WRITER.set(None)
    boundary.pieces = ['{"accepted":true}']
    try:
        body = run()
        raw, metadata = boundary.responses[0]
        record = recorded_metadata(raw, metadata)
        record.pop("requestStream")
        assert decode_recorded_planner_response(prepared(), raw, metadata=record) == body
    finally:
        reset_thinking_writer(token)
    assert len(boundary.requests) == 1
