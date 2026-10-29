import logging

import httpx
import pytest

from app.execution import research_start_provider_adapter as adapter
from app.execution.research_start_provider_adapter import (
    PreparedPlannerAttempt,
    ProviderKnownFailureError,
    execute_prepared_attempt,
)


class _OneResponseTransport:
    def __init__(self, response: httpx.Response) -> None:
        self.response = response
        self.calls = 0
        self.requests: list[dict] = []

    def post(self, endpoint: str, *, headers: dict, json: dict, timeout: float) -> httpx.Response:
        self.calls += 1
        self.requests.append(dict(json))
        return self.response


def _prepared() -> PreparedPlannerAttempt[dict]:
    return PreparedPlannerAttempt(
        phase="research_planner",
        ordinal=1,
        provider="huiyan_chat_completions",
        model="gpt-5.6-terra",
        endpoint="https://api.example-gateway.com/v1/chat/completions",
        payload={"model": "gpt-5.6-terra", "messages": []},
        response_format={"type": "json_object"},
        timeout_seconds=90.0,
        decode=lambda body: dict(body),
    )


def test_http_504_persists_safe_route_diagnostic_without_secret_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_PROVIDER_API_KEY", "SENTINEL_API_KEY")
    ticks = iter((100.0, 161.489))
    monkeypatch.setattr(adapter.time, "monotonic", lambda: next(ticks))
    request = httpx.Request("POST", "https://api.example-gateway.com/v1/chat/completions")
    response = httpx.Response(
        504,
        request=request,
        headers={
            "x-request-id": "req-failed-504",
            "cf-ray": "abc123-DFW",
            "server": "nginx/1.29.0",
            "set-cookie": "SENTINEL_COOKIE",
            "x-debug-secret": "SENTINEL_SECRET",
        },
        json={
            "error": {
                "message": "openai_error",
                "type": "bad_response_status_code",
                "code": "bad_response_status_code",
            }
        },
    )

    with pytest.raises(ProviderKnownFailureError) as captured:
        execute_prepared_attempt(_prepared(), transport=_OneResponseTransport(response))

    assert captured.value.safe_provider_diagnostic == {
        "httpStatus": 504,
        "providerRequestId": "req-failed-504",
        "cloudflareRay": "abc123-DFW",
        "server": "nginx/1.29.0",
        "elapsedSeconds": 61.489,
        "providerErrorType": "bad_response_status_code",
        "providerErrorCode": "bad_response_status_code",
    }
    assert "SENTINEL" not in repr(captured.value.safe_provider_diagnostic)


def test_success_log_emits_same_safe_route_identity_for_comparison(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("LLM_PROVIDER_API_KEY", "SENTINEL_API_KEY")
    ticks = iter((200.0, 343.646))
    monkeypatch.setattr(adapter.time, "monotonic", lambda: next(ticks))
    request = httpx.Request("POST", "https://api.example-gateway.com/v1/chat/completions")
    response = httpx.Response(
        200,
        request=request,
        headers={
            "x-request-id": "req-success-200",
            "cf-ray": "def456-DFW",
            "server": "nginx/1.29.0",
            "set-cookie": "SENTINEL_COOKIE",
        },
        json={"accepted": True},
    )
    caplog.set_level(logging.INFO, logger="openair.research_start.provider")

    result = execute_prepared_attempt(_prepared(), transport=_OneResponseTransport(response))

    assert result == {"accepted": True}
    message = next(record.getMessage() for record in caplog.records if "provider HTTP succeeded" in record.getMessage())
    assert "elapsed=143.646" in message
    assert "request_id=req-success-200" in message
    assert "cf_ray=def456-DFW" in message
    assert "server=nginx/1.29.0" in message
    assert "SENTINEL" not in message


def test_client_rejection_keeps_existing_compact_diagnostic_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_PROVIDER_API_KEY", "test-key")
    ticks = iter((10.0, 10.1))
    monkeypatch.setattr(adapter.time, "monotonic", lambda: next(ticks))
    request = httpx.Request("POST", "https://api.example-gateway.com/v1/chat/completions")
    response = httpx.Response(
        400,
        request=request,
        headers={"x-request-id": "req-bad-request", "cf-ray": "ignored-DFW"},
        json={"error": {"type": "invalid_request_error", "code": "bad_request"}},
    )

    with pytest.raises(ProviderKnownFailureError) as captured:
        execute_prepared_attempt(_prepared(), transport=_OneResponseTransport(response))

    assert captured.value.safe_provider_diagnostic == {
        "httpStatus": 400,
        "providerRequestId": "req-bad-request",
    }


def test_prepared_attempt_without_provider_schema_does_not_gain_schema_protocol(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_PROVIDER_API_KEY", "test-key")
    request = httpx.Request("POST", "https://api.example-gateway.com/v1/chat/completions")
    response = httpx.Response(200, request=request, json={"accepted": True})
    transport = _OneResponseTransport(response)

    execute_prepared_attempt(_prepared(), transport=transport)

    assert transport.requests[0]["messages"] == []
