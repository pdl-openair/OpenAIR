"""Fail-closed HUIYAN chat-completions adapter for one frozen orchestration request."""

from __future__ import annotations

import ast
from copy import deepcopy
import json
import logging
import os
import socket
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping, Protocol

import httpx

from app.execution.durable_orchestrator_runtime import (
    ArtifactContractValidationError,
    FrozenOrchestrationExecutionRequest,
    FrozenArtifactContract,
    validate_frozen_artifact_content,
)
from app.execution.provider_schema_projection import (
    schema_for_provider_prompt as _schema_for_provider_prompt,
)
from app.execution.structured_output_provider_adapter import (
    ProviderSchemaCapabilityError,
    apply_provider_schema_transport,
    structured_response_format,
)
from app.modules.provider_registry import (
    ProviderRegistry,
    ProviderRegistryError,
    ProviderRuntimeConfig,
    ProviderSubmissionSafety,
    load_provider_registry,
    resolve_provider_runtime_config,
)
from app.models.research_external_result import (
    RejectedResponseDiagnosis,
    SafeValidatorDiagnostic,
)
from app.modules.provider_transport_evidence import (
    EVIDENCE_KIND_SUB_CALL,
    ProviderAttemptEvidenceError,
    ProviderHttpAttempt,
    ProviderHttpAttemptRepository,
)
from llm_core.openai_compatible_client import (
    apply_openai_compatible_thinking,
    chat_completions_url,
    openai_compatible_headers,
)

_logger = logging.getLogger("openair.openai_responses_provider")

_TEXT_MIME_TYPES = frozenset({"text/markdown", "text/plain"})
_SAFE_METADATA_KEYS = frozenset({"providerId", "adapter", "contentLength"})
_FINAL_RESULT_KEYS = frozenset({"title", "mimeType", "content"})
_SKILL_REQUEST_KEYS = frozenset({
    "skillId", "inputMemoryRefs", "inputArtifactRefs", "parameters", "reason", "idempotencyKey",
})
_TITLE_LIMIT = 240
_RAW_RESPONSE_LIMIT = 64 * 1024
_CORRECTION_FEEDBACK_LIMIT = 2_000
_CORRECTION_SOURCE_LIMIT = 32 * 1024
_SYSTEM_INSTRUCTIONS_WITH_SKILL_REQUESTS = (
    "You are executing exactly one API-owned frozen research orchestration request. "
    "Do not re-route, select another provider, agent, or skill, invoke tools, or claim that "
    "a Skill body or external material was loaded. Selected agent and Skill IDs are frozen "
    "identifiers only. Return exactly one JSON object: either a final artifact with title, mimeType, and content; "
    "or a skillRequest with skillId, inputMemoryRefs, inputArtifactRefs, parameters, reason, and idempotencyKey. "
    "Do not include both actions. When activeWorkUnit.phase is review_artifact, follow its frozen "
    "reviewContract.evaluationCriteria and evaluate against reviewedArtifactContract; do not apply the reviewer's "
    "own research Artifact input contract or demand downstream research as evidence for a planning-only artifact. "
    "A Skill request asks the server to validate and run an already-authorized capability; "
    "it is not a tool call. Do not use markdown fences or include any text outside the JSON object. "
    "Schema compliance is fail-closed: content must be a JSON object that exactly matches the frozen "
    "artifact schema (required keys present, closed enums exact, no unknown properties, no contractId/"
    "semanticVersion/body envelope around the artifact body)."
)
_SYSTEM_INSTRUCTIONS_FINAL_ONLY = (
    "You are executing exactly one API-owned frozen research orchestration request. "
    "Do not re-route, select another provider, agent, or skill, invoke tools, or claim that "
    "a Skill body or external material was loaded. Selected agent and Skill IDs are frozen "
    "identifiers only. Return exactly one final artifact JSON object with title, mimeType, and content. "
    "Put the frozen artifact body in content (object or JSON string). Do not wrap the body in "
    "contractId/semanticVersion/body. Do not invent retrieved candidates when no retrieval ran: "
    "prefer strategy_only with retrieval_outcome=not_attempted and empty candidate_literature/"
    "reading_priority when the schema allows it. "
    "Do not return a skillRequest. When activeWorkUnit.phase is review_artifact, follow its frozen "
    "reviewContract.evaluationCriteria and evaluate against reviewedArtifactContract; do not apply the reviewer's "
    "own research Artifact input contract or demand downstream research as evidence for a planning-only artifact. "
    "Do not use markdown fences or include any text outside the JSON object. "
    "Schema compliance is fail-closed: every required property must be present with the declared type; "
    "closed enums must use exact allowed strings; do not add unknown properties."
)
_SKILL_REQUEST_SCHEMA = {
    "type": "object",
    "properties": {
        "skillRequest": {
            "type": "object",
            "properties": {
                "skillId": {"type": "string", "minLength": 1, "maxLength": 160},
                "inputMemoryRefs": {"type": "array", "items": {"type": "string", "maxLength": 240}, "maxItems": 100},
                "inputArtifactRefs": {"type": "array", "items": {"type": "string", "maxLength": 240}, "maxItems": 100},
                "parameters": {"type": "object", "maxProperties": 40},
                "reason": {"type": "string", "minLength": 1, "maxLength": 2000},
                "idempotencyKey": {"type": "string", "minLength": 1, "maxLength": 240},
            },
            "required": ["skillId", "inputMemoryRefs", "inputArtifactRefs", "parameters", "reason", "idempotencyKey"],
            "additionalProperties": False,
        },
    },
    "required": ["skillRequest"],
    "additionalProperties": False,
}


class ProviderExecutionError(ValueError):
    """Raised for safe-to-report provider execution failures."""


class ProviderConfigurationError(ProviderExecutionError):
    """Raised before an HTTP request can be issued."""


class ProviderAttemptEvidenceUnavailableError(ProviderExecutionError):
    """Durable physical-attempt evidence could not be prepared or preserved."""


class ProviderTransportNotSubmittedError(ProviderExecutionError):
    """The request provably failed before Provider submission could occur."""

    def __init__(
        self,
        message: str = "Provider transport failed before submission",
        *,
        provider_execution_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.provider_execution_id = provider_execution_id


class ProviderTransportUncertainError(ProviderExecutionError):
    """The provider may have acted but no trustworthy response was received."""

    def __init__(
        self,
        message: str = "Provider transport result is unknown",
        *,
        provider_execution_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.provider_execution_id = provider_execution_id


class ProviderResponseError(ProviderExecutionError):
    """A provider response was received but cannot be accepted."""


class ProviderContractError(ProviderResponseError):
    """The provider completed a response, but it violated the result contract."""


class ProviderRejectedRequestError(ProviderResponseError):
    """The provider explicitly rejected the request before producing a result."""


class ProviderAvailabilityError(ProviderExecutionError):
    """The provider returned an observable transient server-side failure."""


@dataclass(frozen=True)
class OpenAIResponsesTransportRequest:
    """Non-secret request data handed to a transport implementation."""

    providerId: str
    adapter: str
    model: str
    inputText: str = field(repr=False)
    artifactContract: str = ""
    artifactContractSchemaVersion: int = 0
    artifactRequiredFields: tuple[str, ...] = ()
    frozenArtifactContract: FrozenArtifactContract | None = field(default=None, repr=False)
    capabilityCallId: str | None = None
    attemptNumber: int | None = None
    operationIdentity: str = "provider_submission"
    allowSkillRequests: bool = True
    structuredCorrectionAttempt: int = 0
    correctionFeedback: str | None = field(default=None, repr=False)
    previousOutput: str | None = field(default=None, repr=False)
    groundingCorrection: bool = False


class OpenAIResponsesTransport(Protocol):
    """Boundary for the production client and exact-test fake."""

    is_fake_transport: bool

    def execute(
        self,
        request: OpenAIResponsesTransportRequest,
        runtime_config: ProviderRuntimeConfig,
    ) -> tuple[Mapping[str, Any], Mapping[str, Any] | None]:
        """Return ``(structured_response, usage_wire)``.

        ``usage_wire`` is the normalized token usage (S03-14-02) or None when the
        transport could not observe it. Fakes return ``(response, None)``.
        """
        ...


def _provider_http_timeout(runtime_config: ProviderRuntimeConfig) -> httpx.Timeout:
    """Transport-stage guards without granting elapsed time execution authority."""
    guard = max(float(runtime_config.timeoutSeconds), 0.1)
    return httpx.Timeout(
        connect=min(5.0, guard),
        pool=min(5.0, guard),
        write=min(10.0, guard),
        read=None,
    )


class HttpOpenAIResponsesTransport:
    """One non-streaming POST observed for its full Provider execution lifecycle.

    Connect/pool/write stages retain bounded transport guards.  The response
    read has no elapsed-time execution authority: for providers without native
    status lookup, this worker connection is the durable observer until a
    response arrives, transport certainty is lost, or the process is stopped by
    an explicit higher-level action.
    """

    is_fake_transport = False

    def __init__(self, attempt_repository: ProviderHttpAttemptRepository | None = None) -> None:
        self._attempt_repository = attempt_repository or ProviderHttpAttemptRepository()

    def execute(
        self,
        request: OpenAIResponsesTransportRequest,
        runtime_config: ProviderRuntimeConfig,
    ) -> tuple[Mapping[str, Any], Mapping[str, Any] | None]:
        url, payload = self._build_payload(request, runtime_config)
        body_bytes = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
        headers = dict(openai_compatible_headers(runtime_config.apiKey))
        attempt = self._begin_attempt(request, url=url, request_body=body_bytes)
        headers.update(attempt.headers)
        raw: bytes | None = None
        http_status: int | None = None
        response_headers: Mapping[str, Any] | None = None
        try:
            mark_io_started = getattr(self._attempt_repository, "mark_io_started", None)
            if callable(mark_io_started):
                mark_io_started(attempt)
            with httpx.Client(timeout=_provider_http_timeout(runtime_config)) as client:
                response = client.post(url, content=body_bytes, headers=headers)
            http_status = response.status_code
            response_headers = dict(response.headers)
            if response.status_code >= 400:
                error_body = response.content if len(response.content) <= _RAW_RESPONSE_LIMIT else None
                self._finish_attempt(
                    attempt,
                    request_body=body_bytes,
                    outcome="provider_http_error",
                    http_status=response.status_code,
                    response_body=error_body,
                    response_headers=dict(response.headers),
                )
                if 400 <= response.status_code < 500:
                    raise ProviderRejectedRequestError(
                        "Provider rejected this research request before execution"
                    ) from None
                raise ProviderAvailabilityError(
                    f"Provider returned HTTP {response.status_code}"
                ) from None
            try:
                raw = _bounded_http_body(response.content)
            except ProviderContractError:
                self._finish_attempt(
                    attempt,
                    request_body=body_bytes,
                    outcome="response_received",
                    http_status=response.status_code,
                    response_body=None,
                    response_headers=dict(response.headers),
                )
                raise
            self._finish_attempt(
                attempt,
                request_body=body_bytes,
                outcome="response_received",
                http_status=response.status_code,
                response_body=raw,
                response_headers=dict(response.headers),
            )
        except ProviderRejectedRequestError:
            raise
        except ProviderTransportUncertainError:
            raise
        except ProviderContractError:
            raise
        except (httpx.TimeoutException, httpx.TransportError, httpx.HTTPError, TimeoutError, socket.timeout, OSError) as exc:
            definitely_not_submitted = isinstance(
                exc,
                (httpx.ConnectTimeout, httpx.ConnectError, httpx.PoolTimeout),
            )
            outcome = "transport_not_submitted" if definitely_not_submitted else "transport_uncertain"
            self._finish_attempt(
                attempt,
                request_body=body_bytes,
                outcome=outcome,
                http_status=http_status,
                response_body=None,
                response_headers=response_headers,
            )
            if definitely_not_submitted:
                raise ProviderTransportNotSubmittedError(
                    provider_execution_id=attempt.attempt_id,
                ) from None
            raise ProviderTransportUncertainError(
                provider_execution_id=attempt.attempt_id,
            ) from None
        assert raw is not None
        try:
            body = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            # The HTTP response was fully received and durably recorded above.
            # Invalid response syntax is therefore a known completed Provider
            # call with an upper-layer contract failure, never transport unknown.
            diagnosis = diagnose_rejected_response(
                raw,
                finish_reason="stop",
                artifact_contract=request.artifactContract,
                required_fields=request.artifactRequiredFields,
            )
            raise _contract_error(diagnosis, raw) from None
        from llm_core.token_usage import billable_usage_from_openai_body, to_wire_usage

        usage = billable_usage_from_openai_body(
            body,
            request_text=body_bytes.decode("utf-8", errors="replace"),
        )
        usage_wire = to_wire_usage(usage)
        if usage_wire and usage.get("estimated"):
            _logger.warning(
                "provider usage missing; estimated transport tokens provider=%s model=%s total=%s",
                request.providerId,
                request.model,
                usage_wire.get("totalTokens"),
            )
        try:
            structured = _structured_response_from_chat_completions_body(
                body,
                artifact_contract=request.artifactContract,
                required_fields=request.artifactRequiredFields,
            )
        except ProviderContractError as exc:
            # Attach billable usage before re-raise so schema rejects still meter.
            if usage_wire and not getattr(exc, "usage", None):
                exc.usage = dict(usage_wire)
            raise
        except Exception:
            # HTTP 200 + response body is known. Any local normalization bug is
            # an internal contract failure, never an unknown external result.
            _logger.exception(
                "provider_response_normalization_unexpected provider=%s artifactContract=%s capabilityCallId=%s",
                request.providerId,
                request.artifactContract,
                request.capabilityCallId or "",
            )
            error = ProviderContractError("provider_response_contract_invalid")
            error.safe_error_code = "provider_response_normalization_failed"
            error.usage = dict(usage_wire) if usage_wire else None
            raise error from None
        return structured, usage_wire

    @staticmethod
    def _build_payload(
        request: OpenAIResponsesTransportRequest,
        runtime_config: ProviderRuntimeConfig,
    ) -> tuple[str, dict[str, Any]]:
        payload: dict[str, Any] = {
            "model": request.model,
            "messages": _request_messages(request),
        }
        max_tokens_raw = (os.environ.get("OPENAIR_DURABLE_ORCHESTRATE_LLM_MAX_TOKENS") or "").strip()
        if max_tokens_raw:
            try:
                max_tokens = int(max_tokens_raw)
            except ValueError:
                max_tokens = 0
            if max_tokens > 0:
                payload["max_tokens"] = max_tokens
        payload = apply_openai_compatible_thinking(
            payload, "OPENAIR_DURABLE_ORCHESTRATE_LLM_THINKING"
        )
        provider_schema = _durable_research_provider_schema(request)
        response_format = structured_response_format(
            "huiyan_chat_completions",
            schema_name="openair_durable_research_result",
        )
        payload = apply_provider_schema_transport(
            "huiyan_chat_completions",
            payload=payload,
            response_format=response_format,
            provider_schema=provider_schema,
        )
        return _chat_completions_url(runtime_config.baseUrl), payload

    def _begin_attempt(
        self,
        request: OpenAIResponsesTransportRequest,
        *,
        url: str,
        request_body: bytes,
    ) -> ProviderHttpAttempt:
        if request.structuredCorrectionAttempt > 0:
            return self._attempt_repository.begin_sub_attempt(
                capability_call_id=request.capabilityCallId or "",
                evidence_kind=EVIDENCE_KIND_SUB_CALL,
                adapter_name="structured_output_correction",
                request_body=request_body,
                endpoint=url,
                adapter=request.adapter,
                operation_identity=(
                    f"{request.operationIdentity}:structured_output_correction:{request.structuredCorrectionAttempt}"
                ),
                provider=request.providerId,
                model=request.model,
                logical_attempt_ordinal=max(int(request.structuredCorrectionAttempt), 1),
            )
        return self._attempt_repository.begin(
            capability_call_id=request.capabilityCallId or "",
            attempt_number=request.attemptNumber or 0,
            request_body=request_body,
            endpoint=url,
            adapter=request.adapter,
            operation_identity=request.operationIdentity or "provider_submission",
            provider=request.providerId,
            model=request.model,
        )

    def _finish_attempt(
        self,
        attempt: ProviderHttpAttempt,
        *,
        request_body: bytes,
        outcome: str,
        http_status: int | None,
        response_body: bytes | None,
        response_headers: Mapping[str, Any] | None,
    ) -> None:
        self._attempt_repository.finish(
            attempt,
            request_body=request_body,
            outcome=outcome,
            http_status=http_status,
            response_body=response_body,
            response_headers=response_headers,
        )


@dataclass(frozen=True)
class ProviderExecutionResult:
    title: str | None = None
    mimeType: str | None = None
    content: str | None = field(default=None, repr=False)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    skillRequest: Mapping[str, Any] | None = field(default=None, repr=False)
    # Normalized token usage from the provider response (S03-14-02 ledger),
    # camelCase wire shape. None when the transport could not observe usage.
    usage: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.skillRequest is not None:
            if any(value is not None for value in (self.title, self.mimeType, self.content)):
                raise ProviderContractError("Provider response cannot include both actions")
            if not isinstance(self.skillRequest, Mapping):
                raise ProviderContractError("Provider Skill request is invalid")
            object.__setattr__(self, "skillRequest", MappingProxyType(dict(self.skillRequest)))
            return
        title = _required_text(self.title, "title", limit=_TITLE_LIMIT)
        mime_type = _required_text(self.mimeType, "mimeType").lower()
        content = _required_text(self.content, "content")
        if mime_type not in _TEXT_MIME_TYPES:
            raise ProviderContractError("Provider response must use a supported text MIME type")
        if not isinstance(self.metadata, Mapping) or set(self.metadata) != _SAFE_METADATA_KEYS:
            raise ProviderContractError("Provider result metadata is invalid")
        provider_id = _required_text(self.metadata.get("providerId"), "metadata.providerId")
        adapter = _required_text(self.metadata.get("adapter"), "metadata.adapter")
        if not isinstance(self.metadata.get("contentLength"), int) or self.metadata["contentLength"] != len(content):
            raise ProviderContractError("Provider result metadata is invalid")
        object.__setattr__(self, "title", title)
        object.__setattr__(self, "mimeType", mime_type)
        object.__setattr__(self, "content", content)
        object.__setattr__(
            self,
            "metadata",
            MappingProxyType({"providerId": provider_id, "adapter": adapter, "contentLength": len(content)}),
        )


@dataclass(frozen=True)
class PreparedOpenAIResponsesExecution:
    request: OpenAIResponsesTransportRequest = field(repr=False)
    runtime_config: ProviderRuntimeConfig = field(repr=False)
    max_output_chars: int
    provider_id: str
    adapter: str


class OpenAIResponsesProviderAdapter:
    """Execute a frozen request once, without routing or retries."""

    def __init__(
        self,
        *,
        transport: OpenAIResponsesTransport,
        registry: ProviderRegistry | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self._transport = transport
        self._registry = registry or load_provider_registry()
        self._environ = environ if environ is not None else os.environ
        if getattr(transport, "is_fake_transport", False) and _text(self._environ.get("OPENAIR_ENV")).lower() != "test":
            raise ProviderExecutionError("Fake provider transport is only allowed in OPENAIR_ENV=test")

    def prepare(
        self,
        request: FrozenOrchestrationExecutionRequest,
        *,
        capability_call_id: str | None = None,
        attempt_number: int | None = None,
        structured_correction_attempt: int = 0,
        correction_feedback: str | None = None,
        previous_output: str | None = None,
        grounding_correction: bool = False,
    ) -> PreparedOpenAIResponsesExecution:
        provider_id = request.logicalProviderId
        if structured_correction_attempt < 0 or structured_correction_attempt > 1:
            raise ProviderConfigurationError("Structured correction attempt is invalid")
        if structured_correction_attempt:
            feedback = _text(correction_feedback)[:_CORRECTION_FEEDBACK_LIMIT]
            prior = str(previous_output or "")
            if not feedback or not prior or len(prior.encode("utf-8")) > _CORRECTION_SOURCE_LIMIT:
                raise ProviderConfigurationError("Structured correction context is unavailable")
        else:
            feedback = None
            prior = None
        try:
            definition = self._registry.require(provider_id)
            if definition.adapter != "huiyan_chat_completions":
                raise ProviderExecutionError("Provider adapter is not supported by this executor")
            runtime_config = resolve_provider_runtime_config(
                provider_id,
                model_profile=request.modelProfile,
                environ=self._environ,
                registry=self._registry,
            )
        except ProviderRegistryError as exc:
            raise ProviderConfigurationError("Provider configuration is unavailable") from exc
        return PreparedOpenAIResponsesExecution(
            request=OpenAIResponsesTransportRequest(
                providerId=provider_id,
                adapter=definition.adapter,
                model=runtime_config.model,
                inputText=json.dumps(request.transport_payload(), ensure_ascii=True, separators=(",", ":"), sort_keys=True),
                artifactContract=request.artifactContract,
                artifactContractSchemaVersion=request.artifactContractSchemaVersion,
                artifactRequiredFields=request.artifactRequiredFields,
                frozenArtifactContract=FrozenArtifactContract(
                    contract_id=request.artifactContract,
                    semantic_version=request.artifactSemanticVersion,
                    schema_digest=request.artifactSchemaDigest,
                    schema=request.artifactSchema,
                    source_ref=request.artifactSchemaRef,
                ),
                capabilityCallId=capability_call_id,
                attemptNumber=attempt_number,
                operationIdentity=f"provider_submission:{request.runId}",
                allowSkillRequests=request.allowSkillRequests,
                structuredCorrectionAttempt=structured_correction_attempt,
                correctionFeedback=feedback,
                previousOutput=prior,
                groundingCorrection=grounding_correction,
            ),
            runtime_config=runtime_config,
            max_output_chars=definition.maxOutputChars,
            provider_id=provider_id,
            adapter=definition.adapter,
        )

    def execute_prepared(self, prepared: PreparedOpenAIResponsesExecution) -> ProviderExecutionResult:
        _logger.info(
            "provider_execute_start provider=%s model=%s artifactContract=%s "
            "capabilityCallId=%s attempt=%s correction=%s",
            prepared.provider_id,
            prepared.request.model,
            prepared.request.artifactContract,
            prepared.request.capabilityCallId or "",
            prepared.request.attemptNumber or 0,
            prepared.request.structuredCorrectionAttempt > 0,
        )
        try:
            response, usage = self._transport.execute(prepared.request, prepared.runtime_config)
        except ProviderSchemaCapabilityError as exc:
            error = ProviderConfigurationError(str(exc))
            error.safe_error_code = exc.safe_error_code
            raise error from exc
        except ProviderAttemptEvidenceError as exc:
            raise ProviderAttemptEvidenceUnavailableError(
                f"provider_attempt_evidence:{str(exc) or type(exc).__name__}"
            ) from exc
        except ProviderExecutionError:
            raise
        except Exception:
            # Production transports must classify transport ambiguity themselves
            # and attach the reserved execution identity.  An untyped adapter
            # exception has no evidence that the Provider accepted a request, so
            # it must not manufacture external_result_unknown.
            _logger.exception(
                "provider_execute_internal_failure provider=%s artifactContract=%s capabilityCallId=%s",
                prepared.provider_id,
                prepared.request.artifactContract,
                prepared.request.capabilityCallId or "",
            )
            raise ProviderExecutionError("provider_execution_internal_error") from None
        try:
            result = _result_from_transport_response(
                response,
                prepared.max_output_chars,
                prepared.provider_id,
                prepared.adapter,
                artifact_contract=prepared.request.artifactContract,
                required_fields=prepared.request.artifactRequiredFields,
                usage=usage,
            )
        except ProviderContractError as exc:
            diagnosis = getattr(exc, "diagnosis", None)
            category = getattr(diagnosis, "category", None) if diagnosis is not None else None
            _logger.warning(
                "provider_execute_contract_invalid provider=%s artifactContract=%s "
                "capabilityCallId=%s category=%s",
                prepared.provider_id,
                prepared.request.artifactContract,
                prepared.request.capabilityCallId or "",
                category or "provider_response_contract_invalid",
            )
            if usage and not getattr(exc, "usage", None):
                exc.usage = dict(usage)
            raise
        if result.content is not None and prepared.request.frozenArtifactContract is not None:
            try:
                validate_frozen_artifact_content(prepared.request.frozenArtifactContract, result.content)
            except ArtifactContractValidationError as exc:
                content_preview = ""
                if isinstance(result.content, Mapping):
                    try:
                        content_preview = json.dumps(result.content, ensure_ascii=False)[:500]
                    except (TypeError, ValueError):
                        content_preview = str(result.content)[:500]
                else:
                    content_preview = str(result.content)[:500]
                _logger.warning(
                    "dispatch artifact contract invalid provider=%s error=%s fields=%s "
                    "capabilityCallId=%s content=%s",
                    prepared.provider_id,
                    exc.error_code,
                    list(exc.field_paths)[:16],
                    prepared.request.capabilityCallId or "",
                    content_preview,
                )
                error = ProviderContractError("provider_response_contract_invalid")
                error.diagnosis = None
                error.safe_error_code = exc.error_code
                error.safe_field_paths = exc.field_paths
                # Preserve observed usage so callers can still meter burned tokens.
                error.usage = dict(result.usage) if result.usage else (dict(usage) if usage else None)
                raise error from None
        return result

    def execute(
        self,
        request: FrozenOrchestrationExecutionRequest,
        *,
        capability_call_id: str | None = None,
        attempt_number: int | None = None,
        structured_correction_attempt: int = 0,
        correction_feedback: str | None = None,
        previous_output: str | None = None,
        grounding_correction: bool = False,
    ) -> ProviderExecutionResult:
        return self.execute_prepared(
            self.prepare(
                request,
                capability_call_id=capability_call_id,
                attempt_number=attempt_number,
                structured_correction_attempt=structured_correction_attempt,
                correction_feedback=correction_feedback,
                previous_output=previous_output,
                grounding_correction=grounding_correction,
            )
        )

    def physical_submission_capabilities(self, provider_id: str) -> ProviderSubmissionSafety:
        """Expose registry truth; HUIYAN cannot be used by the fenced outbox worker."""
        return self._registry.require(provider_id).submissionSafety


def _durable_research_provider_schema(
    request: OpenAIResponsesTransportRequest,
) -> dict[str, Any]:
    """Build the provider-visible result contract without provider adaptation.

    The shared adapter remains the only component allowed to project, lower, or
    admit this schema.  Embedding a referenced artifact schema under ``content``
    would change local-reference scope; the shared reference admission therefore
    rejects such a request before transport instead of accepting a prompt-only
    approximation.
    """
    contract = request.frozenArtifactContract
    if contract is None:
        raise ProviderConfigurationError("Frozen artifact schema is unavailable")
    artifact_schema = _thaw_json(contract.schema)
    if not isinstance(artifact_schema, Mapping):
        raise ProviderConfigurationError("Frozen artifact schema is invalid")

    final_result = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "title": {"type": "string", "minLength": 1, "maxLength": _TITLE_LIMIT},
            "mimeType": {"type": "string", "enum": ["text/plain"]},
            "content": dict(artifact_schema),
        },
        "required": ["title", "mimeType", "content"],
    }
    if not request.allowSkillRequests:
        return final_result
    return {
        "type": "object",
        "anyOf": [final_result, deepcopy(_SKILL_REQUEST_SCHEMA)],
    }

def _request_messages(request: OpenAIResponsesTransportRequest) -> list[dict[str, str]]:
    contract = request.frozenArtifactContract
    if contract is None:
        raise ProviderConfigurationError("Frozen artifact schema is unavailable")
    schema = _schema_for_provider_prompt(_thaw_json(contract.schema))
    required = schema.get("required") if isinstance(schema, Mapping) else None
    required_fields = (
        [str(item) for item in required if isinstance(item, str)]
        if isinstance(required, list)
        else list(request.artifactRequiredFields)
    )
    base_instruction = (
        _SYSTEM_INSTRUCTIONS_WITH_SKILL_REQUESTS
        if request.allowSkillRequests
        else _SYSTEM_INSTRUCTIONS_FINAL_ONLY
    )
    instruction = (
        f"{base_instruction} For a final artifact, content must be a JSON object that exactly "
        "conforms to the frozen artifact schema below. Set mimeType to text/plain. "
        f"Required content keys: {json.dumps(required_fields, ensure_ascii=True)}. "
        f"Frozen artifact schema: {json.dumps(schema, ensure_ascii=True, separators=(',', ':'), sort_keys=True)}"
    )
    messages = [
        {"role": "system", "content": instruction},
        {"role": "user", "content": request.inputText},
    ]
    if request.structuredCorrectionAttempt > 0:
        feedback = _text(request.correctionFeedback)[:_CORRECTION_FEEDBACK_LIMIT]
        previous = str(request.previousOutput or "")
        if not feedback or not previous:
            raise ProviderConfigurationError("Structured correction context is unavailable")
        messages.extend([
            {"role": "assistant", "content": previous},
            {
                "role": "user",
                "content": (
                    "The previous output contains an ungrounded source identifier. "
                    "The following JSON is the complete closed-world list of the only sources you may cite: "
                    f"{feedback}\n"
                    "Return one corrected raw JSON object only. Each candidate_literature title and "
                    "source_identifier must match one listed title/identifier pair exactly after normalizing "
                    "DOI prefixes, arXiv prefixes, URL trailing slashes, and case. Do not create a DOI, URL, "
                    "source id, or citation; do not infer a DOI from a title or complete a source from author/year. "
                    "Preserve scientific content only when supported by the selected retrieved source. If no "
                    "listed source safely supports a candidate, return no fabricated source; the canonical "
                    "validator will reject the result."
                    if request.groundingCorrection else (
                    "Your previous output failed the canonical structured-output validator. "
                    f"Validation feedback: {feedback}\n"
                    "Return one corrected raw JSON object only. This is a contract/format correction pass, "
                    "not a new research pass. Preserve the scientific meaning already present in the previous "
                    "output. Do not add citations, values, evidence, claims, conclusions, or other scientific "
                    "facts that were not already present. Do not guess missing scientific content. Do not relax "
                    "or reinterpret the schema. If the existing content cannot satisfy a scientific requirement "
                    "without invention, leave that limitation visible so the canonical validator can reject it."
                    )
                ),
            },
        ])
    return messages


def _coerce_provider_document(
    document: Mapping[str, Any],
    required_fields: tuple[str, ...],
) -> Mapping[str, Any]:
    """Normalize common model envelopes into title/mimeType/content or direct body."""
    if not isinstance(document, Mapping):
        return document
    if set(document) == {"skillRequest"} or set(document) == _FINAL_RESULT_KEYS:
        return document
    if required_fields and _is_direct_semantic_object(document, required_fields):
        return document

    body = document.get("body")
    if isinstance(body, Mapping) and (
        "contractId" in document or "semanticVersion" in document or "artifactContract" in document
    ):
        if required_fields and _is_direct_semantic_object(body, required_fields):
            return body
        return {
            "title": str(
                document.get("contractId")
                or document.get("artifactContract")
                or document.get("title")
                or "artifact"
            ),
            "mimeType": "text/plain",
            "content": body,
        }

    if "content" in document and isinstance(document.get("content"), (Mapping, str)):
        title = document.get("title")
        mime = document.get("mimeType") or document.get("mime_type") or "text/plain"
        if isinstance(title, str) and title.strip():
            return {
                "title": title,
                "mimeType": mime if isinstance(mime, str) else "text/plain",
                "content": document["content"],
            }
    return document


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    if isinstance(value, list):
        return [_thaw_json(item) for item in value]
    return value


def _safe_structured_text_candidate(text: str) -> str | None:
    """Return one mechanically recoverable JSON/Python-object candidate.

    This boundary may remove transport-format noise (markdown fences or prose
    around one object) and normalize a Python dict literal, but it never adds,
    deletes, or rewrites fields inside the scientific payload.
    """
    if not isinstance(text, str):
        return None
    candidate = text.strip()
    if not candidate:
        return None

    if candidate.startswith("```") and candidate.endswith("```"):
        first_newline = candidate.find("\n")
        if first_newline > 0:
            candidate = candidate[first_newline + 1 : -3].strip()

    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, Mapping):
        return json.dumps(_thaw_json(parsed), ensure_ascii=True, separators=(",", ":"), sort_keys=True)

    # Safe deterministic normalization for Python-style dict literals sometimes
    # emitted despite JSON-object mode. ast.literal_eval cannot execute code.
    if len(candidate) <= _RAW_RESPONSE_LIMIT:
        try:
            literal = ast.literal_eval(candidate)
        except (SyntaxError, ValueError, TypeError, MemoryError, RecursionError):
            literal = None
        if isinstance(literal, Mapping):
            return json.dumps(_thaw_json(literal), ensure_ascii=True, separators=(",", ":"), sort_keys=True)

    # Extract exactly one top-level object from harmless surrounding prose.
    decoder = json.JSONDecoder()
    found: list[tuple[int, int, Mapping[str, Any]]] = []
    index = 0
    while index < len(candidate):
        start = candidate.find("{", index)
        if start < 0:
            break
        try:
            value, consumed = decoder.raw_decode(candidate[start:])
        except json.JSONDecodeError:
            index = start + 1
            continue
        end = start + consumed
        if isinstance(value, Mapping):
            found.append((start, end, value))
            index = end
        else:
            index = start + 1
        if len(found) > 1:
            return None
    if len(found) != 1:
        return None
    start, end, value = found[0]
    outside = candidate[:start] + candidate[end:]
    # Refuse ambiguous leftovers containing another JSON container.
    if any(token in outside for token in ("{", "}", "[", "]")):
        return None
    return json.dumps(_thaw_json(value), ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def _mechanically_complete_provider_document(
    document: Mapping[str, Any],
    *,
    artifact_contract: str,
    required_fields: tuple[str, ...],
) -> Mapping[str, Any]:
    """Fill only transport-envelope fields when content is already complete."""
    coerced = dict(_coerce_provider_document(document, required_fields))
    if required_fields and _is_direct_semantic_object(coerced, required_fields):
        return _normalized_direct_semantic_object(coerced, artifact_contract)

    if not required_fields or "content" not in coerced:
        return coerced
    content = coerced.get("content")
    semantic: Mapping[str, Any] | None = None
    if isinstance(content, Mapping) and _is_direct_semantic_object(content, required_fields):
        semantic = content
    elif isinstance(content, str):
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, Mapping) and _is_direct_semantic_object(parsed, required_fields):
            semantic = parsed
    if semantic is None:
        return coerced

    title = coerced.get("title")
    mime_type = coerced.get("mimeType") or coerced.get("mime_type")
    return {
        "title": title.strip() if isinstance(title, str) and title.strip() else (artifact_contract or "artifact"),
        "mimeType": mime_type if isinstance(mime_type, str) and mime_type in _TEXT_MIME_TYPES else "text/plain",
        "content": json.dumps(_thaw_json(semantic), ensure_ascii=True, separators=(",", ":"), sort_keys=True),
    }


def _structured_response_from_chat_completions_body(
    body: Any,
    *,
    artifact_contract: str = "",
    required_fields: tuple[str, ...] = (),
) -> Mapping[str, Any]:
    if not isinstance(body, Mapping):
        raise ProviderContractError("Provider response is malformed")
    choices = body.get("choices")
    if not isinstance(choices, list) or len(choices) != 1:
        raise ProviderContractError("Provider response is malformed")
    choice = choices[0]
    if not isinstance(choice, Mapping) or not isinstance(choice.get("message"), Mapping):
        raise ProviderContractError("Provider response is malformed")
    text = choice["message"].get("content")
    if not isinstance(text, str) or not text.strip():
        raise ProviderContractError("Provider response has no single structured output")
    finish_reason = choice.get("finish_reason")
    normalized_text = _safe_structured_text_candidate(text)
    normalized_finish = "stop" if finish_reason is None else str(finish_reason)
    if normalized_text is None:
        diagnosis = diagnose_rejected_response(
            text.encode("utf-8"),
            finish_reason=normalized_finish,
            artifact_contract=artifact_contract,
            required_fields=required_fields,
        )
        raise _contract_error(diagnosis, text.encode("utf-8"))

    structured = json.loads(normalized_text)
    if not isinstance(structured, Mapping):
        raise ProviderContractError("Provider response is not structured output")
    completed = _mechanically_complete_provider_document(
        structured,
        artifact_contract=artifact_contract,
        required_fields=required_fields,
    )
    validation_text = json.dumps(
        _thaw_json(completed),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    diagnosis = diagnose_rejected_response(
        validation_text.encode("utf-8"),
        finish_reason=normalized_finish,
        artifact_contract=artifact_contract,
        required_fields=required_fields,
    )
    if diagnosis.category != "valid":
        # Keep the original provider bytes for safe diagnostics/correction.
        raise _contract_error(diagnosis, text.encode("utf-8"))
    return completed


def _result_from_transport_response(
    response: Mapping[str, Any], max_output_chars: int, provider_id: str, adapter: str,
    *, artifact_contract: str = "", required_fields: tuple[str, ...] = (),
    usage: Mapping[str, Any] | None = None,
) -> ProviderExecutionResult:
    if not isinstance(response, Mapping):
        raise ProviderContractError("Provider response is malformed")
    if set(response) == {"skillRequest"}:
        request = response.get("skillRequest")
        if not _valid_skill_request(request):
            raise ProviderContractError("Provider response is malformed")
        return ProviderExecutionResult(skillRequest=dict(request), usage=usage)
    if set(response) != _FINAL_RESULT_KEYS:
        raise ProviderContractError("Provider response is malformed")
    title = response.get("title")
    mime_type = response.get("mimeType")
    content = response.get("content")
    if isinstance(content, Mapping) and required_fields:
        content = json.dumps(_thaw_json(content), ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    if not all(isinstance(value, str) for value in (title, mime_type, content)):
        raise ProviderContractError("Provider response is malformed")
    if len(content) > max_output_chars:
        _logger.warning("provider_content_contract_gap check=max_output_chars length=%s", len(content))
    if required_fields:
        diagnosis = diagnose_rejected_response(
            json.dumps(response, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("utf-8"),
            finish_reason="stop",
            artifact_contract=artifact_contract,
            required_fields=required_fields,
        )
        if diagnosis.category != "valid":
            raise _contract_error(diagnosis, json.dumps(response, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    return ProviderExecutionResult(
        title=title,
        mimeType=mime_type,
        content=content,
        metadata={"providerId": provider_id, "adapter": adapter, "contentLength": len(content)},
        usage=usage,
    )


def _valid_skill_request(value: Any) -> bool:
    if not isinstance(value, Mapping) or set(value) != _SKILL_REQUEST_KEYS:
        return False
    if not _bounded_text(value.get("skillId"), 160):
        return False
    if not _bounded_text(value.get("reason"), 2_000) or not _bounded_text(value.get("idempotencyKey"), 240):
        return False
    for field in ("inputMemoryRefs", "inputArtifactRefs"):
        refs = value.get(field)
        if not isinstance(refs, list) or len(refs) > 100 or any(not _bounded_text(ref, 240) for ref in refs):
            return False
    parameters = value.get("parameters")
    return isinstance(parameters, Mapping) and len(parameters) <= 40


def _bounded_text(value: Any, limit: int) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value.strip()) <= limit and not any(ord(char) < 32 for char in value)


def _required_text(value: Any, field: str, *, limit: int | None = None) -> str:
    text = _text(value)
    if not text or (limit is not None and len(text) > limit) or any(ord(char) < 32 for char in text):
        raise ProviderResponseError(f"Provider response {field} is invalid")
    return text


def _text(value: Any) -> str:
    return str(value or "").strip()


def _bounded_http_body(raw: bytes) -> bytes:
    if len(raw) <= _RAW_RESPONSE_LIMIT:
        return raw
    error = ProviderContractError("provider_response_capture_limit_exceeded")
    error.evidence_body = raw[:_RAW_RESPONSE_LIMIT]
    error.total_byte_length = len(raw)
    error.hash_incomplete = True
    raise error


def _chat_completions_url(base_url: str) -> str:
    return chat_completions_url(base_url)


def diagnose_rejected_response(
    raw: bytes,
    *,
    finish_reason: str | None,
    artifact_contract: str = "",
    required_fields: tuple[str, ...] = (),
) -> RejectedResponseDiagnosis:
    """Classify rejected bytes without retaining values in ordinary diagnostics."""
    if not isinstance(raw, bytes):
        raise TypeError("provider response evidence must be bytes")
    normalized_finish = _normalized_finish_reason(finish_reason)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return _diagnosis("invalid_encoding", normalized_finish, "response_encoding_invalid", "", "encoding")
    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        stripped = text.lstrip()
        if stripped.startswith(("{", "[")):
            looks_truncated = (
                exc.pos >= max(0, len(text) - 2)
                or exc.msg.startswith("Unterminated")
                or exc.msg.startswith("Expecting ',' delimiter")
                or exc.msg.startswith("Expecting property name enclosed in double quotes")
            )
            category = "truncated_json" if looks_truncated else "invalid_json"
            keyword = "json_parse"
        elif any(marker in text for marker in (":", "[", "]", "---")):
            category = "invalid_yaml"
            keyword = "yaml_parse"
        else:
            category = "prose"
            keyword = "json_parse"
        return _diagnosis(category, normalized_finish, f"provider_response_{category}", "", keyword)
    if not isinstance(document, Mapping):
        return _diagnosis("schema_type", normalized_finish, "provider_response_root_type", "", "type", "object", _json_type(document))
    if normalized_finish != "stop":
        return _diagnosis(
            "finish_reason_non_success",
            normalized_finish,
            "provider_finish_reason_non_success",
            "/finish_reason",
            "const",
            "stop",
            "string",
        )
    document = dict(_coerce_provider_document(document, required_fields))
    if required_fields and _is_direct_semantic_object(document, required_fields):
        return RejectedResponseDiagnosis(
            category="valid",
            normalized_finish_reason=normalized_finish,
            diagnostics=(),
        )
    if set(document) == {"skillRequest"} and _valid_skill_request(document.get("skillRequest")):
        return RejectedResponseDiagnosis(
            category="valid",
            normalized_finish_reason=normalized_finish,
            diagnostics=(),
        )
    expected_keys = _FINAL_RESULT_KEYS
    extra = sorted(set(document) - expected_keys)
    if extra:
        pointer = "/" + _json_pointer_token(str(extra[0]))
        return _diagnosis("extra_fields", normalized_finish, "provider_response_extra_field", pointer, "additionalProperties")
    for field_name in ("title", "mimeType", "content"):
        if field_name not in document:
            return _diagnosis(
                "schema_required", normalized_finish, "provider_response_required_field", f"/{field_name}", "required"
            )
    for field_name in ("title", "mimeType"):
        if not isinstance(document[field_name], str):
            return _diagnosis(
                "schema_type",
                normalized_finish,
                "provider_response_field_type",
                f"/{field_name}",
                "type",
                "string",
                _json_type(document[field_name]),
            )
    content = document["content"]
    if not isinstance(content, str) and not (required_fields and isinstance(content, Mapping)):
        return _diagnosis(
            "schema_type",
            normalized_finish,
            "provider_response_field_type",
            "/content",
            "type",
            "string",
            _json_type(content),
        )
    if document["mimeType"] not in _TEXT_MIME_TYPES:
        return _diagnosis(
            "schema_enum", normalized_finish, "provider_response_mime_type", "/mimeType", "enum", "supported_text_mime", "string"
        )
    if required_fields:
        if isinstance(content, Mapping):
            return RejectedResponseDiagnosis(
                category="valid",
                normalized_finish_reason=normalized_finish,
                diagnostics=(),
            )
        content_diagnosis = _diagnose_artifact_content(
            content,
            normalized_finish,
            artifact_contract,
            required_fields,
        )
        if content_diagnosis is not None:
            return content_diagnosis
    return RejectedResponseDiagnosis(
        category="valid",
        normalized_finish_reason=normalized_finish,
        diagnostics=(),
    )


def _is_direct_semantic_object(document: Mapping[str, Any], required_fields: tuple[str, ...]) -> bool:
    return (
        bool(required_fields)
        and set(document) == set(required_fields)
        and all(document[field] is not None for field in required_fields)
    )


def _normalized_direct_semantic_object(document: Mapping[str, Any], artifact_contract: str) -> Mapping[str, Any]:
    return {
        "title": artifact_contract,
        "mimeType": "text/plain",
        "content": json.dumps(document, ensure_ascii=True, separators=(",", ":"), sort_keys=True),
    }


def _diagnose_artifact_content(
    content: str,
    finish_reason: str,
    artifact_contract: str,
    required_fields: tuple[str, ...],
) -> RejectedResponseDiagnosis | None:
    try:
        document = json.loads(content)
    except json.JSONDecodeError:
        return _diagnosis("artifact_content_invalid_json", finish_reason, "artifact_content_invalid_json", "/content", "json_parse")
    if not isinstance(document, Mapping):
        return _diagnosis("artifact_content_schema_type", finish_reason, "artifact_content_root_type", "/content", "type", "object", _json_type(document))
    missing = [field for field in required_fields if field not in document or document[field] is None]
    extra = sorted(set(document) - set(required_fields))
    if missing or extra:
        _logger.warning("artifact_content_field_gap missing=%s extra=%s", missing, extra)
    return None


def _contract_error(diagnosis: RejectedResponseDiagnosis, evidence_body: bytes) -> ProviderContractError:
    error = ProviderContractError("provider_response_contract_invalid")
    error.diagnosis = diagnosis
    error.evidence_body = evidence_body[:_RAW_RESPONSE_LIMIT]
    error.total_byte_length = len(evidence_body)
    error.hash_incomplete = len(evidence_body) > _RAW_RESPONSE_LIMIT
    return error


def _diagnosis(
    category: str,
    finish_reason: str,
    error_code: str,
    pointer: str,
    keyword: str,
    expected_type: str | None = None,
    actual_type: str | None = None,
) -> RejectedResponseDiagnosis:
    return RejectedResponseDiagnosis(
        category=category,
        normalized_finish_reason=finish_reason,
        diagnostics=(
            SafeValidatorDiagnostic(
                error_code=error_code,
                category=category,
                json_pointer=pointer,
                field_path=pointer.lstrip("/").replace("/", "."),
                expected_type=expected_type,
                actual_type=actual_type,
                validator_keyword=keyword,
            ),
        ),
    )


def _normalized_finish_reason(value: str | None) -> str:
    normalized = str(value or "missing").strip().lower().replace("-", "_")
    if normalized in {"stop", "length", "content_filter", "tool_calls", "missing"}:
        return normalized
    return "other"


def _json_pointer_token(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, Mapping):
        return "object"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    return "unknown"
