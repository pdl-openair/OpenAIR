"""One-shot provider boundary for research-start routing and planning.

Prepared attempts deliberately contain no persistence or scheduling behavior. A
caller that wants another ordinal must prepare a new attempt after recording the
result of this one.
"""

from __future__ import annotations

from app.execution.research_start_processing import (
    PlannerProcessingError, PlannerSemanticInputError, local_planning_step,
    record_planning_response,
)

import hashlib

import json
import logging
import os
import time
from typing import Any, Callable, Generic, Mapping, Protocol, TypeVar

import httpx

from app.execution.structured_output_provider_adapter import apply_provider_schema_transport

_logger = logging.getLogger("openair.research_start.provider")


class ProviderTransportUncertainError(RuntimeError):
    """The provider transport did not yield a complete, trustworthy response."""

    uncertain = True
    failure_code = "provider_transport_uncertain"

    def __init__(
        self,
        *,
        error_type: str = "transport_error",
        transport_stage: str = "transport",
        elapsed_seconds: float | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        super().__init__("provider transport outcome is uncertain")
        self.safe_transport_diagnostic = {
            "errorType": str(error_type),
            "transportStage": str(transport_stage),
            "elapsedSeconds": (
                round(max(float(elapsed_seconds), 0.0), 3)
                if elapsed_seconds is not None
                else None
            ),
            "timeoutSeconds": (
                float(timeout_seconds) if timeout_seconds is not None else None
            ),
        }


class ProviderKnownFailureError(RuntimeError):
    """The provider explicitly rejected the frozen request."""

    uncertain = False
    failure_code = "provider_http_rejected"

    def __init__(
        self,
        *,
        status_code: int | None = None,
        provider_request_id: str | None = None,
        cloudflare_ray: str | None = None,
        server: str | None = None,
        elapsed_seconds: float | None = None,
        provider_error_type: str | None = None,
        provider_error_code: str | None = None,
    ) -> None:
        super().__init__("provider returned an explicit HTTP failure")
        self.status_code = status_code
        diagnostic: dict[str, Any] = {
            "httpStatus": status_code,
            "providerRequestId": provider_request_id,
        }
        optional = {
            "cloudflareRay": cloudflare_ray,
            "server": server,
            "elapsedSeconds": (
                round(max(float(elapsed_seconds), 0.0), 3)
                if elapsed_seconds is not None
                else None
            ),
            "providerErrorType": provider_error_type,
            "providerErrorCode": provider_error_code,
        }
        diagnostic.update({key: value for key, value in optional.items() if value is not None})
        self.safe_provider_diagnostic = diagnostic


class ProviderTransportNotSubmittedError(ProviderKnownFailureError):
    """A local transport guard proved the request never reached Provider submission."""

    failure_code = "provider_transport_not_submitted"

    def __init__(
        self,
        *,
        error_type: str = "transport_error",
        transport_stage: str = "connect",
        elapsed_seconds: float | None = None,
    ) -> None:
        RuntimeError.__init__(self, "provider request was not submitted")
        self.status_code = None
        self.safe_transport_diagnostic = {
            "errorType": str(error_type),
            "transportStage": str(transport_stage),
            "elapsedSeconds": (
                round(max(float(elapsed_seconds), 0.0), 3)
                if elapsed_seconds is not None
                else None
            ),
        }


class ProviderContractError(ProviderKnownFailureError):
    """The provider answered, but its answer did not satisfy the local contract."""

    failure_code = "provider_contract_invalid"

    def __init__(
        self,
        *,
        safe_error_code: str | None = None,
        safe_field_paths: tuple[str, ...] = (),
        safe_repair_context: Mapping[str, Any] | None = None,
        safe_diagnostic: Mapping[str, Any] | None = None,
    ) -> None:
        RuntimeError.__init__(self, "provider response violates the expected contract")
        self.status_code = None
        self.safe_error_code = safe_error_code
        self.safe_field_paths = safe_field_paths
        self.safe_repair_context = dict(safe_repair_context or {})
        self.safe_diagnostic = dict(safe_diagnostic or {})


class _ProviderConfigurationError(ProviderKnownFailureError):
    failure_code = "provider_configuration_invalid"

    def __init__(self) -> None:
        RuntimeError.__init__(self, "provider credential is unavailable")
        self.status_code = None


class ResearchStartHttpTransport(Protocol):
    def post(
        self,
        endpoint: str,
        *,
        headers: Mapping[str, str],
        json: Mapping[str, Any],
        timeout: float,
    ) -> Any: ...


class HttpxResearchStartTransport:
    """Default synchronous HTTP transport kept behind an injectable seam."""

    def post(
        self,
        endpoint: str,
        *,
        headers: Mapping[str, str],
        json: Mapping[str, Any],
        timeout: float,
    ) -> httpx.Response:
        request_budget = max(float(timeout), 0.1)
        transport_timeout = httpx.Timeout(
            connect=min(5.0, request_budget),
            pool=min(5.0, request_budget),
            write=min(10.0, request_budget),
            read=None,
        )
        writer = _live_thinking_writer(endpoint)
        payload = dict(json)
        with httpx.Client(timeout=transport_timeout) as client:
            if writer is None:
                return client.post(endpoint, headers=dict(headers), json=payload)
            return self._post_thinking_stream(
                client, endpoint, headers=dict(headers), payload=payload, writer=writer,
            )

    def _post_thinking_stream(
        self,
        client: httpx.Client,
        endpoint: str,
        *,
        headers: dict[str, str],
        payload: Mapping[str, Any],
        writer: Any,
    ) -> httpx.Response:
        from app.modules.research_start_thinking import (
            IncompleteChatStreamError,
            capture_chat_stream,
        )

        try:
            with client.stream("POST", endpoint, headers=headers, json=payload) as response:
                if response.status_code >= 400:
                    response.read()
                    response.raise_for_status()
                raw = capture_chat_stream(response.iter_lines(), writer.add_delta)
        except IncompleteChatStreamError as exc:
            raise httpx.RemoteProtocolError("provider stream ended before completion") from exc
        # Keep response/request identity and normalized stream evidence. Decoding
        # belongs after durable response capture, not inside the I/O boundary.
        response_headers = dict(response.headers)
        for header in ("content-length", "content-encoding", "transfer-encoding"):
            response_headers.pop(header, None)
        return httpx.Response(
            response.status_code,
            content=raw,
            headers=response_headers,
            request=response.request,
        )


def _live_thinking_writer(endpoint: str) -> Any:
    if "/chat/completions" not in endpoint:
        return None
    from app.modules.research_start_thinking_store import current_thinking_writer

    return current_thinking_writer()


def _transport_payload(
    transport: ResearchStartHttpTransport,
    endpoint: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Freeze transport options before recording the actual submitted payload."""
    actual = dict(payload)
    if isinstance(transport, HttpxResearchStartTransport) and _live_thinking_writer(endpoint):
        actual["stream"] = True
    return actual


def _capture_finished_reasoning(body: Mapping[str, Any]) -> None:
    from app.modules.research_start_thinking import reasoning_from_chat_body
    from app.modules.research_start_thinking_store import current_thinking_writer

    writer = current_thinking_writer()
    if writer is None:
        return
    reasoning = reasoning_from_chat_body(body)
    if reasoning:
        writer.note_if_empty(reasoning)


T = TypeVar("T")


class _PreparedAttempt(Generic[T]):
    """Immutable request value object with a privately encoded request body."""

    __slots__ = (
        "_phase",
        "_ordinal",
        "_provider",
        "_model",
        "_endpoint",
        "_request_bytes",
        "_response_format_bytes",
        "_provider_schema_bytes",
        "_timeout_seconds",
        "_decode",
    )

    def __init__(
        self,
        *,
        phase: str,
        ordinal: int,
        provider: str,
        model: str,
        endpoint: str,
        payload: Mapping[str, Any],
        response_format: Mapping[str, Any] | None,
        timeout_seconds: float,
        decode: Callable[[Mapping[str, Any]], T],
        provider_schema: Mapping[str, Any] | None = None,
    ) -> None:
        object.__setattr__(self, "_phase", phase)
        object.__setattr__(self, "_ordinal", ordinal)
        object.__setattr__(self, "_provider", provider)
        object.__setattr__(self, "_model", model)
        object.__setattr__(self, "_endpoint", endpoint)
        object.__setattr__(self, "_request_bytes", self._encode(payload))
        object.__setattr__(
            self,
            "_response_format_bytes",
            self._encode(response_format) if response_format is not None else None,
        )
        object.__setattr__(
            self,
            "_provider_schema_bytes",
            self._encode(provider_schema) if provider_schema is not None else None,
        )
        object.__setattr__(self, "_timeout_seconds", float(timeout_seconds))
        object.__setattr__(self, "_decode", decode)

    def __setattr__(self, _name: str, _value: Any) -> None:
        raise AttributeError("prepared attempts are immutable")

    @staticmethod
    def _encode(value: Mapping[str, Any]) -> bytes:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

    @property
    def phase(self) -> str:
        return self._phase

    @property
    def ordinal(self) -> int:
        return self._ordinal

    @property
    def provider(self) -> str:
        return self._provider

    @property
    def model(self) -> str:
        return self._model

    @property
    def endpoint(self) -> str:
        return self._endpoint

    @property
    def timeout_seconds(self) -> float:
        return self._timeout_seconds

    @property
    def payload(self) -> dict[str, Any]:
        return self._thaw_payload()

    @property
    def response_format(self) -> dict[str, Any] | None:
        if self._response_format_bytes is None:
            return None
        return json.loads(self._response_format_bytes.decode("utf-8"))

    @property
    def provider_schema(self) -> dict[str, Any] | None:
        if self._provider_schema_bytes is None:
            return None
        return json.loads(self._provider_schema_bytes.decode("utf-8"))

    @property
    def system_prompt(self) -> str:
        payload = self._thaw_payload()
        if self.provider == "openai_responses":
            return self._responses_role_prompt(payload, "system")
        messages = payload.get("messages") or []
        return str(messages[0].get("content") or "") if messages else ""

    @property
    def user_prompt(self) -> str:
        payload = self._thaw_payload()
        if self.provider == "openai_responses":
            return self._responses_role_prompt(payload, "user")
        messages = payload.get("messages") or []
        return str(messages[1].get("content") or "") if len(messages) > 1 else ""

    @staticmethod
    def _responses_role_prompt(payload: Mapping[str, Any], role: str) -> str:
        input_items = payload.get("input")
        if not isinstance(input_items, list):
            return ""
        for item in input_items:
            if not isinstance(item, Mapping) or item.get("role") != role:
                continue
            content = item.get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                text_parts = [
                    str(part.get("text"))
                    for part in content
                    if isinstance(part, Mapping) and isinstance(part.get("text"), str)
                ]
                return "".join(text_parts)
            return ""
        return ""

    def _thaw_payload(self) -> dict[str, Any]:
        return json.loads(self._request_bytes.decode("utf-8"))

    def metadata(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "ordinal": self.ordinal,
            "provider": self.provider,
            "model": self.model,
            "responseFormat": self.response_format,
            "timeoutSeconds": self.timeout_seconds,
        }

    def to_dict(self) -> dict[str, Any]:
        return self.metadata()

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}(phase={self.phase!r}, ordinal={self.ordinal!r}, "
            f"provider={self.provider!r}, model={self.model!r})"
        )


class PreparedPlannerAttempt(_PreparedAttempt[T], Generic[T]):
    __slots__ = ()



def _authorization_headers(provider: str) -> dict[str, str]:
    key = os.environ.get("OPENAI_API_KEY") if provider == "openai_responses" else os.environ.get("LLM_PROVIDER_API_KEY")
    if not key:
        raise _ProviderConfigurationError() from None
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}



def _safe_response_header(response: httpx.Response | None, header: str) -> str | None:
    if response is None:
        return None
    value = response.headers.get(header)
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.split()).strip()
    return normalized[:200] or None


def _provider_request_id(response: httpx.Response | None) -> str | None:
    for header in ("x-request-id", "x-provider-request-id", "x-openai-request-id", "request-id", "x-amzn-requestid"):
        value = _safe_response_header(response, header)
        if value:
            return value
    return None


def _safe_route_diagnostic(response: httpx.Response | None, *, elapsed_seconds: float) -> dict[str, Any]:
    return {
        "providerRequestId": _provider_request_id(response),
        "cloudflareRay": _safe_response_header(response, "cf-ray"),
        "server": _safe_response_header(response, "server"),
        "elapsedSeconds": round(max(float(elapsed_seconds), 0.0), 3),
    }


def _safe_provider_error_identity(response: httpx.Response | None) -> tuple[str | None, str | None]:
    if response is None:
        return None, None
    try:
        payload = response.json()
    except (ValueError, TypeError):
        return None, None
    if not isinstance(payload, Mapping):
        return None, None
    error = payload.get("error")
    if not isinstance(error, Mapping):
        return None, None

    def clean(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = " ".join(value.split()).strip()
        return normalized[:120] or None

    return clean(error.get("type")), clean(error.get("code"))


def _transport_failure_stage(exc: httpx.TransportError) -> str:
    if isinstance(exc, httpx.ConnectTimeout):
        return "connect_timeout"
    if isinstance(exc, httpx.ConnectError):
        return "connect"
    if isinstance(exc, httpx.PoolTimeout):
        return "pool_timeout"
    if isinstance(exc, httpx.WriteTimeout):
        return "write_timeout"
    if isinstance(exc, httpx.WriteError):
        return "write"
    if isinstance(exc, httpx.ReadTimeout):
        return "read_timeout"
    if isinstance(exc, httpx.ReadError):
        return "read"
    if isinstance(exc, httpx.RemoteProtocolError):
        return "protocol"
    return "transport"



def execute_prepared_attempt(
    prepared: _PreparedAttempt[T],
    *,
    transport: ResearchStartHttpTransport | None = None,
) -> T:
    """Submit once with durable physical-attempt truth before local interpretation."""
    active_transport = transport if transport is not None else HttpxResearchStartTransport()
    with local_planning_step("request_preparation"):
        request_payload = prepared._thaw_payload()
        provider_schema = prepared.provider_schema
        if provider_schema is not None:
            request_payload = apply_provider_schema_transport(
                prepared.provider, payload=request_payload,
                response_format=prepared.response_format, provider_schema=provider_schema,
            )
        request_payload = _transport_payload(active_transport, prepared.endpoint, request_payload)
    # Local preparation/configuration failures happen before a physical attempt exists.
    headers = _authorization_headers(prepared.provider)
    request_body = _PreparedAttempt._encode(request_payload)

    from app.modules.provider_transport_evidence import (
        ProviderHttpAttemptRepository,
        current_provider_execution_parent,
    )

    attempt_repository = None
    physical_attempt = None
    parent = current_provider_execution_parent()
    if parent is not None:
        attempt_repository = ProviderHttpAttemptRepository()
        physical_attempt = attempt_repository.begin(
            capability_call_id="",
            attempt_number=max(int(prepared.ordinal), 1),
            request_body=request_body,
            endpoint=prepared.endpoint,
            adapter=prepared.provider,
            operation_identity=f"research_start:{prepared.phase}",
            provider=prepared.provider,
            model=prepared.model,
        )
        headers.update(physical_attempt.headers)
        attempt_repository.mark_io_started(physical_attempt)
    elif transport is None:
        # Every production path must bind a durable parent. Injected transports are
        # allowed without DB evidence for isolated unit tests only.
        raise PlannerProcessingError(
            "provider_attempt_evidence",
            RuntimeError("production provider execution parent is missing"),
        )

    started_at = time.monotonic()
    try:
        response = active_transport.post(
            prepared.endpoint, headers=headers, json=request_payload,
            timeout=prepared.timeout_seconds,
        )
        raise_for_status = getattr(response, "raise_for_status", None)
        if callable(raise_for_status):
            raise_for_status()
    except ProviderKnownFailureError as exc:
        if attempt_repository is not None and physical_attempt is not None:
            attempt_repository.finish(
                physical_attempt,
                request_body=request_body,
                outcome="provider_http_error",
                http_status=getattr(exc, "status_code", None),
                response_body=None,
                response_headers=None,
                reason=getattr(exc, "failure_code", None) or "known_provider_failure",
                stage="response_observation",
            )
        raise
    except (httpx.ConnectTimeout, httpx.ConnectError, httpx.PoolTimeout) as exc:
        if attempt_repository is not None and physical_attempt is not None:
            attempt_repository.finish(
                physical_attempt,
                request_body=request_body,
                outcome="transport_not_submitted",
                http_status=None,
                response_body=None,
                response_headers=None,
                stage=_transport_failure_stage(exc),
            )
        raise ProviderTransportNotSubmittedError(
            error_type=type(exc).__name__,
            transport_stage=_transport_failure_stage(exc),
            elapsed_seconds=time.monotonic() - started_at,
        ) from None
    except httpx.TransportError as exc:
        if attempt_repository is not None and physical_attempt is not None:
            attempt_repository.finish(
                physical_attempt,
                request_body=request_body,
                outcome="transport_uncertain",
                http_status=None,
                response_body=None,
                response_headers=None,
                stage=_transport_failure_stage(exc),
            )
        raise ProviderTransportUncertainError(
            error_type=type(exc).__name__, transport_stage=_transport_failure_stage(exc),
            elapsed_seconds=time.monotonic() - started_at,
            timeout_seconds=None,
        ) from None
    except httpx.HTTPStatusError as exc:
        status_code = exc.response.status_code if exc.response is not None else None
        route = _safe_route_diagnostic(exc.response, elapsed_seconds=time.monotonic() - started_at)
        provider_error_type, provider_error_code = _safe_provider_error_identity(exc.response)
        if attempt_repository is not None and physical_attempt is not None:
            attempt_repository.finish(
                physical_attempt,
                request_body=request_body,
                outcome="provider_http_error",
                http_status=status_code,
                response_body=(exc.response.content if exc.response is not None else None),
                response_headers=(dict(exc.response.headers) if exc.response is not None else None),
                stage="response_observation",
            )
        _logger.warning(
            "research_start provider HTTP rejected phase=%s ordinal=%s model=%s "
            "status=%s elapsed=%.3f request_id=%s cf_ray=%s server=%s "
            "provider_error_type=%s provider_error_code=%s",
            prepared.phase, prepared.ordinal, prepared.model, status_code,
            route["elapsedSeconds"], route["providerRequestId"], route["cloudflareRay"],
            route["server"], provider_error_type, provider_error_code,
        )
        capture_route = bool(status_code in {408, 429} or (isinstance(status_code, int) and 500 <= status_code <= 599))
        raise ProviderKnownFailureError(
            status_code=status_code, provider_request_id=route["providerRequestId"],
            cloudflare_ray=route["cloudflareRay"] if capture_route else None,
            server=route["server"] if capture_route else None,
            elapsed_seconds=route["elapsedSeconds"] if capture_route else None,
            provider_error_type=provider_error_type if capture_route else None,
            provider_error_code=provider_error_code if capture_route else None,
        ) from None
    except Exception as exc:
        # Unknown adapter failures after the I/O boundary cannot be asserted as a
        # Provider failure. Preserve uncertainty instead of collapsing to failed.
        if attempt_repository is not None and physical_attempt is not None:
            attempt_repository.finish(
                physical_attempt,
                request_body=request_body,
                outcome="transport_uncertain",
                http_status=None,
                response_body=None,
                response_headers=None,
                reason="transport_adapter_uncertain",
                stage="transport_adapter",
            )
        raise PlannerProcessingError("transport_adapter", exc) from exc

    route = _safe_route_diagnostic(
        response if isinstance(response, httpx.Response) else None,
        elapsed_seconds=time.monotonic() - started_at,
    )
    _logger.info(
        "research_start provider HTTP succeeded phase=%s ordinal=%s model=%s "
        "status=%s elapsed=%.3f request_id=%s cf_ray=%s server=%s",
        prepared.phase, prepared.ordinal, prepared.model, getattr(response, "status_code", None),
        route["elapsedSeconds"], route["providerRequestId"], route["cloudflareRay"], route["server"],
    )
    with local_planning_step("response_capture"):
        raw = response.content if isinstance(response, httpx.Response) else json.dumps(
            response.json() if hasattr(response, "json") else response,
            ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
        metadata = {
            "phase": prepared.phase, "ordinal": prepared.ordinal,
            "provider": prepared.provider, "model": prepared.model,
            "timeoutSeconds": prepared.timeout_seconds,
            "httpStatus": getattr(response, "status_code", None),
            "requestStream": request_payload.get("stream") is True,
            "providerRequestId": route["providerRequestId"],
            "elapsedSeconds": route["elapsedSeconds"],
            "requestSha256": hashlib.sha256(request_body).hexdigest(),
            "schemaSha256": hashlib.sha256(_PreparedAttempt._encode(prepared.provider_schema or {})).hexdigest(),
        }
    # Provider completion is durable before staging/parsing/schema/scientific work.
    if attempt_repository is not None and physical_attempt is not None:
        attempt_repository.finish(
            physical_attempt,
            request_body=request_body,
            outcome="response_received",
            http_status=getattr(response, "status_code", None),
            response_body=raw,
            response_headers=(dict(response.headers) if isinstance(response, httpx.Response) else None),
        )
    record_planning_response(prepared, raw, metadata)
    body = _decode_response_envelope(raw)
    _capture_finished_reasoning(body)
    with local_planning_step("usage_observation"):
        from llm_core.token_usage import record_observed_usage
        record_observed_usage(provider=prepared.provider, model=prepared.model, body=body)
    return _decode_received_response(prepared, body)


def _decode_response_envelope(raw: bytes) -> Mapping[str, Any]:
    try:
        if any(line.lstrip().startswith(b"data:") for line in raw.splitlines()):
            from app.modules.research_start_thinking import consume_chat_stream

            raw = consume_chat_stream(raw.decode("utf-8").splitlines(), lambda _delta: None)
        body = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ProviderContractError(
            safe_error_code="provider_envelope_invalid", safe_field_paths=("/",),
        ) from exc
    if not isinstance(body, Mapping):
        raise ProviderContractError(
            safe_error_code="provider_envelope_invalid", safe_field_paths=("/",),
        )
    return body

def _decode_received_response(prepared: _PreparedAttempt[T], body: Mapping[str, Any]) -> T:
    try:
        return prepared._decode(body)
    except PlannerProcessingError as exc:
        if isinstance(body.get("id"), str) and body["id"].strip():
            exc.provider_call_ref = body["id"].strip()
        raise
    except ProviderKnownFailureError:
        raise
    except (ValueError, KeyError, TypeError, RuntimeError, IndexError) as exc:
        code = getattr(exc, "safe_error_code", None)
        paths = tuple(getattr(exc, "safe_field_paths", ()) or ())
        repair = getattr(exc, "safe_repair_context", None)
        if prepared.phase == "research_planner" and not isinstance(exc, PlannerSemanticInputError):
            # Existing explicitly classified repair contracts remain supported.
            # A bare ValueError/KeyError is never evidence that the model is wrong.
            if not (isinstance(code, str) and isinstance(repair, Mapping) and repair):
                error = PlannerProcessingError("response_decode", exc)
                if isinstance(body.get("id"), str) and body["id"].strip():
                    error.provider_call_ref = body["id"].strip()
                raise error from None
        error = ProviderContractError(
            safe_error_code=code if isinstance(code, str) else None,
            safe_field_paths=tuple(p for p in paths if isinstance(p, str))[:16],
            safe_repair_context=repair if isinstance(repair, Mapping) else None,
            safe_diagnostic=None,
        )
        if prepared.phase == "research_planner":
            error.safe_planner_diagnostic = {
                "stage": getattr(exc, "processing_stage", "planner_response"),
                "errorType": type(exc).__name__,
            }
        if isinstance(body.get("id"), str) and body["id"].strip():
            error.provider_call_ref = body["id"].strip()
        raise error from None

def decode_recorded_planner_response(
    prepared: _PreparedAttempt[T], raw: bytes, *, metadata: Mapping[str, Any],
) -> T:
    """Reprocess an authorized complete response without HTTP, billing or DB writes.

    Does not change a task/attempt state or itself authorize recovery. The caller
    must obtain complete evidence through the scoped evidence reader.
    """
    payload = prepared._thaw_payload()
    if prepared.provider_schema is not None:
        payload = apply_provider_schema_transport(
            prepared.provider, payload=payload,
            response_format=prepared.response_format, provider_schema=prepared.provider_schema,
        )
    if metadata.get("requestStream") is True:
        payload["stream"] = True
    expected = {
        "phase": prepared.phase, "ordinal": prepared.ordinal,
        "provider": prepared.provider, "model": prepared.model,
        "requestSha256": hashlib.sha256(_PreparedAttempt._encode(payload)).hexdigest(),
        "schemaSha256": hashlib.sha256(_PreparedAttempt._encode(prepared.provider_schema or {})).hexdigest(),
        "responseSha256": hashlib.sha256(raw).hexdigest(),
        "responseBytes": len(raw), "truncated": False,
    }
    if prepared.phase != "research_planner" or any(metadata.get(k) != v for k, v in expected.items()):
        raise ValueError("recorded response does not match this frozen Planner attempt")
    return _decode_received_response(prepared, _decode_response_envelope(raw))
