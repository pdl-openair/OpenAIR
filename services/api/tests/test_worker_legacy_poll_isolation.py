"""Legacy claim transport outages must not starve PostgreSQL continuation."""

from urllib.error import HTTPError, URLError

import pytest
from psycopg import OperationalError
from worker import entrypoint


@pytest.mark.parametrize("error", [URLError("offline"), TimeoutError(), OSError()])
def test_legacy_transport_failure_still_consumes_continuation(monkeypatch, error):
    consumed = []

    def fail_claim(path, body):
        assert path == "/internal/durable-runs/claim"
        raise error

    def consume_continuation():
        consumed.append("continuation")
        return True

    monkeypatch.setattr(entrypoint, "_post", fail_claim)
    monkeypatch.setattr(entrypoint, "_durable_claim_not_before", lambda: None)
    monkeypatch.setattr(entrypoint, "_claim_and_run_research_start", lambda: False)
    monkeypatch.setattr(entrypoint, "_claim_and_run_research_continuation", consume_continuation)
    assert entrypoint._poll_once(0) == (True, 3)
    assert consumed == ["continuation"]


def test_legacy_transport_failure_with_empty_queues_advances_cursor(monkeypatch):
    def fail_claim(*_args, **_kwargs):
        raise URLError("offline")

    monkeypatch.setattr(entrypoint, "_post", fail_claim)
    monkeypatch.setattr(entrypoint, "_durable_claim_not_before", lambda: None)
    monkeypatch.setattr(
        entrypoint,
        "_poll_handlers",
        lambda: (entrypoint._claim_and_run_legacy_durable, lambda: False),
    )
    assert entrypoint._poll_once(0) == (False, 1)


@pytest.mark.parametrize("status", [401, 403, 500])
def test_legacy_http_errors_preserve_existing_policy(monkeypatch, status):
    error = HTTPError("http://localhost/claim", status, "error", {}, None)

    def fail_claim(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(entrypoint, "_post", fail_claim)
    monkeypatch.setattr(entrypoint, "_durable_claim_not_before", lambda: None)
    with pytest.raises(HTTPError) as caught:
        entrypoint._poll_once(0)
    assert caught.value is error


def test_database_authority_error_still_propagates(monkeypatch):
    error = OperationalError("database unavailable")

    def fail_database():
        raise error

    monkeypatch.setattr(entrypoint, "_claim_and_run_legacy_durable", lambda: False)
    monkeypatch.setattr(entrypoint, "_claim_and_run_research_start", fail_database)
    with pytest.raises(OperationalError) as caught:
        entrypoint._poll_once(0)
    assert caught.value is error


def test_legacy_post_claim_execution_transport_error_is_not_hidden(monkeypatch):
    error = TimeoutError("execution interrupted")

    def fail_renew(_run):
        raise error

    monkeypatch.setattr(
        entrypoint, "_post", lambda *_args: {"run": {"runId": "run-1", "leaseEpoch": 1}}
    )
    monkeypatch.setattr(entrypoint, "_durable_claim_not_before", lambda: None)
    monkeypatch.setattr(entrypoint.settings, "worker_per_task_token", False)
    monkeypatch.setattr(entrypoint, "PAUSE_AFTER_CLAIM_SECONDS", 0)
    monkeypatch.setattr(entrypoint, "_renew_before_execution", fail_renew)
    with pytest.raises(TimeoutError) as caught:
        entrypoint._claim_and_run_legacy_durable()
    assert caught.value is error
