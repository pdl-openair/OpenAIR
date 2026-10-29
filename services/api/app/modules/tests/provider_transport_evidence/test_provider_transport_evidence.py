import json

from app.modules.provider_transport_evidence import _safe_rejection_diagnostics


def test_provider_rejection_diagnostics_are_bounded_and_body_free() -> None:
    request = {
        "model": "huiyan-research-profile",
        "messages": [
            {"role": "system", "content": "SYSTEM_SECRET_PROMPT"},
            {"role": "user", "content": "USER_SENSITIVE_CONTENT"},
        ],
        "response_format": {"type": "json_object"},
        "max_tokens": 512,
        "tools": [{"type": "function"}],
        "tool_choice": "auto",
        "stream": False,
    }
    response = {
        "error": {
            "type": "invalid_request_error",
            "code": "unsupported_parameter",
            "message": "response_format is unsupported; Bearer PROVIDER_SECRET_VALUE",
        }
    }

    diagnostics = _safe_rejection_diagnostics(
        json.dumps(request).encode("utf-8"), json.dumps(response).encode("utf-8")
    )

    assert diagnostics == {
        "providerErrorType": "invalid_request_error",
        "providerErrorCode": "unsupported_parameter",
        "providerErrorMessageSummary": "response_format is unsupported; Bearer [REDACTED]",
        "requestShape": {
            "modelIdentity": "huiyan-research-profile",
            "parameterKeys": [
                "max_tokens", "messages", "model", "response_format", "stream", "tool_choice", "tools"
            ],
            "responseFormatMode": "json_object",
            "hasMaxTokens": True,
            "hasMaxCompletionTokens": False,
            "hasTools": True,
            "hasToolChoice": True,
            "stream": False,
            "messageCount": 2,
        },
    }
    serialized = json.dumps(diagnostics)
    assert "SYSTEM_SECRET_PROMPT" not in serialized
    assert "USER_SENSITIVE_CONTENT" not in serialized
    assert "PROVIDER_SECRET_VALUE" not in serialized


def test_resolved_execution_policy_is_persisted_for_primary_and_recovery_attempts(monkeypatch) -> None:
    from contextlib import contextmanager

    from app.modules.provider_transport_evidence import (
        EVIDENCE_KIND_SUB_CALL,
        ProviderHttpAttemptRepository,
    )

    policy = {
        "contractVersion": 1,
        "providerId": "openai_primary",
        "adapterId": "huiyan_chat_completions",
        "model": "frozen-terra-model",
        "modelProfile": "terra",
        "reasoningEffort": "none",
        "maxOutputTokens": 6000,
        "readTimeoutSeconds": 60.0,
        "responseMode": "json_object",
        "loadReferences": False,
        "structuredOutputRecoveryAttempts": 1,
    }

    class Cursor:
        def __init__(self) -> None:
            self.calls: list[tuple[str, tuple[object, ...]]] = []
            self.row: dict[str, object] | None = None

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def execute(self, sql: str, params=()) -> None:
            assert sql.count("%s") == len(params)
            self.calls.append((sql, tuple(params)))
            if "RETURNING attempt_number" in sql:
                self.row = {"attempt_number": 2}
            elif "RETURNING attempt_id" in sql:
                self.row = {"attempt_id": "pat-primary"}
            else:
                self.row = None

        def fetchone(self):
            row = self.row
            self.row = None
            return row

    class Connection:
        def __init__(self) -> None:
            self.cursor_instance = Cursor()

        def cursor(self):
            return self.cursor_instance

    connections: list[Connection] = []

    @contextmanager
    def fake_connect():
        connection = Connection()
        connections.append(connection)
        yield connection

    repository = ProviderHttpAttemptRepository(checkpoint_repository=None)
    monkeypatch.setattr(repository, "_connect", fake_connect)

    repository.begin(
        capability_call_id="call-policy-evidence",
        attempt_number=1,
        request_body=b"{}",
        endpoint="https://provider.test/v1/chat/completions",
        adapter="huiyan_chat_completions",
        resolved_execution_policy=policy,
    )
    primary_params = next(
        params
        for sql, params in connections[-1].cursor_instance.calls
        if "INSERT INTO openair_provider_http_attempts" in sql
    )
    assert json.loads(primary_params[8]) == policy

    repository.begin_sub_attempt(
        capability_call_id="call-policy-evidence",
        evidence_kind=EVIDENCE_KIND_SUB_CALL,
        adapter_name="structured_output_correction",
        request_body=b"{}",
        endpoint="https://provider.test/v1/chat/completions",
        adapter="huiyan_chat_completions",
        resolved_execution_policy=policy,
    )
    recovery_calls = connections[-1].cursor_instance.calls
    assert "pg_advisory_xact_lock" in recovery_calls[0][0]
    recovery_params = next(
        params
        for sql, params in recovery_calls
        if "INSERT INTO openair_provider_http_attempts" in sql
    )
    assert json.loads(recovery_params[8]) == policy


def test_provider_execution_observation_maps_only_durable_facts(monkeypatch) -> None:
    from contextlib import contextmanager

    from app.modules.provider_transport_evidence import ProviderHttpAttemptRepository

    rows = [
        {
            "attempt_id": "pat-in-flight",
            "capability_call_id": "call-observe",
            "attempt_number": 1,
            "finished_at": None,
            "http_status": None,
            "provider_request_id": None,
            "provider_response_id": None,
            "transport_outcome": "in_flight",
            "execution_state": "running",
            "io_started": None,
        },
        {
            "attempt_id": "pat-unsent",
            "capability_call_id": "call-observe",
            "attempt_number": 1,
            "finished_at": object(),
            "http_status": None,
            "provider_request_id": None,
            "provider_response_id": None,
            "transport_outcome": "transport_not_submitted",
            "execution_state": "failed",
            "execution_reason": "transport_not_submitted",
            "io_started": True,
            "submission_proven": False,
        },
        {
            "attempt_id": "pat-complete",
            "capability_call_id": "call-observe",
            "attempt_number": 1,
            "finished_at": object(),
            "http_status": 200,
            "provider_request_id": "req-1",
            "provider_response_id": "resp-1",
            "transport_outcome": "response_received",
            "execution_state": "completed",
            "execution_reason": "provider_response_received",
            "io_started": True,
            "submission_proven": True,
        },
        {
            "attempt_id": "pat-failed",
            "capability_call_id": "call-observe",
            "attempt_number": 1,
            "finished_at": object(),
            "http_status": 503,
            "provider_request_id": "req-2",
            "provider_response_id": None,
            "transport_outcome": "provider_http_error",
            "execution_state": "failed",
            "execution_reason": "known_provider_failure",
            "io_started": True,
            "submission_proven": True,
        },
        {
            "attempt_id": "pat-unknown",
            "capability_call_id": "call-observe",
            "attempt_number": 1,
            "finished_at": object(),
            "http_status": None,
            "provider_request_id": None,
            "provider_response_id": None,
            "transport_outcome": "transport_uncertain",
            "execution_state": "unknown",
            "execution_reason": "transport_uncertain",
            "io_started": True,
            "submission_proven": None,
        },
    ]

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def execute(self, _sql, _params=()):
            return None

        def fetchone(self):
            return rows.pop(0)

    class Connection:
        def cursor(self):
            return Cursor()

    @contextmanager
    def fake_connect():
        yield Connection()

    repository = ProviderHttpAttemptRepository()
    monkeypatch.setattr(repository, "_connect", fake_connect)

    running = repository.observe(capability_call_id="call-observe")
    assert running is not None
    assert running.state == "running"
    assert running.io_started is None
    not_submitted = repository.observe(capability_call_id="call-observe")
    assert not_submitted is not None
    assert not_submitted.state == "failed"
    assert not_submitted.reason == "transport_not_submitted"
    assert not_submitted.submission_proven is False
    completed = repository.observe(capability_call_id="call-observe")
    assert completed is not None
    assert completed.state == "completed"
    assert completed.provider_request_id == "req-1"
    assert completed.provider_response_id == "resp-1"
    assert repository.observe(capability_call_id="call-observe").state == "failed"
    assert repository.observe(capability_call_id="call-observe").state == "unknown"


def test_sub_call_finish_does_not_touch_capability_checkpoint(monkeypatch) -> None:
    from contextlib import contextmanager

    from app.modules.external_execution_checkpoint import ExternalExecutionCheckpointError
    from app.modules.provider_transport_evidence import (
        EVIDENCE_KIND_SUB_CALL,
        ProviderHttpAttempt,
        ProviderHttpAttemptRepository,
    )

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def execute(self, sql: str, params=()) -> None:
            assert sql.count("%s") == len(params)

        def fetchone(self):
            return {"attempt_id": "pat-sub-finish"}

    class Connection:
        def cursor(self):
            return Cursor()

    @contextmanager
    def fake_connect():
        yield Connection()

    stage_calls: list[dict[str, object]] = []
    mark_not_submitted_calls: list[dict[str, object]] = []

    class FakeCheckpointRepository:
        def get(self, *, capability_call_id: str) -> object:
            assert capability_call_id == "call-correction-handoff"
            return object()

        def stage_response(self, **kwargs: object) -> None:
            stage_calls.append(dict(kwargs))
            raise ExternalExecutionCheckpointError("external_checkpoint_response_conflict")

        def mark_not_submitted(self, **kwargs: object) -> None:
            mark_not_submitted_calls.append(dict(kwargs))

    repository = ProviderHttpAttemptRepository(checkpoint_repository=FakeCheckpointRepository())
    monkeypatch.setattr(repository, "_connect", fake_connect)

    sub_attempt = ProviderHttpAttempt(
        attempt_id="pat-sub-finish",
        capability_call_id="call-correction-handoff",
        attempt_number=2,
        request_hash="deadbeef",
        provider_endpoint_identity="https://provider.test/v1/chat/completions",
        evidence_kind=EVIDENCE_KIND_SUB_CALL,
        adapter_name="structured_output_correction",
    )
    repository.finish(
        sub_attempt,
        request_body=b"{}",
        outcome="response_received",
        http_status=200,
        response_body=b'{"corrected": true}',
        response_headers={"x-request-id": "req-correction"},
    )

    assert stage_calls == []
    assert mark_not_submitted_calls == []
