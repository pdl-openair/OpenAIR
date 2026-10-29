"""Capability-checked boundary for one physical provider submission."""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Literal, Mapping, Protocol

from app.execution.research_dispatch_context import DispatchSubmissionRequest

from app.models.result_protocol import (
    normalize_result_protocol,
    result_protocol_usable,
)
from app.modules.provider_registry import (
    ProviderRegistry,
    ProviderSubmissionSafety,
    load_provider_registry,
)


STRUCTURED_OUTPUT_CORRECTION_EXHAUSTED = "provider_structured_output_correction_exhausted"
STRUCTURED_OUTPUT_EXPLICIT_RETRY_REASON = "user_confirmed_structured_output_retry"
SKILL_FINALIZATION_RUNTIME_FAILED = "skill_finalization_runtime_failed"
SKILL_FINALIZATION_SYSTEM_RETRY_REASON = "system_skill_finalization_runtime_retry"


class DispatchProviderError(RuntimeError):
    """Base class for safe, structured dispatch-provider failures."""


class DispatchProviderPermanentPreflightError(DispatchProviderError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class DispatchProviderRetryablePreflightError(DispatchProviderError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class DispatchProviderCapabilityError(DispatchProviderPermanentPreflightError):
    """Raised before transport when safe submission cannot be proved."""


class DispatchProviderKnownRejected(DispatchProviderError):
    def __init__(
        self,
        code: str,
        *,
        usage: Mapping[str, Any] | None = None,
        failure_detail: Mapping[str, str] | None = None,
    ) -> None:
        self.code = _safe_error_code(code)
        self.usage = dict(usage) if isinstance(usage, Mapping) else None
        self.failure_detail = dict(failure_detail) if isinstance(failure_detail, Mapping) else None
        super().__init__(self.code)


class DispatchProviderResultUnknown(DispatchProviderError):
    def __init__(
        self,
        code: str = "provider_result_unknown",
        *,
        remote_run_id: str | None = None,
    ) -> None:
        self.code = _safe_error_code(code)
        normalized = str(remote_run_id or "").strip() or None
        if normalized is not None and (
            len(normalized) > 240 or any(ord(char) < 32 for char in normalized)
        ):
            raise ValueError("remote run id must be a bounded stable identifier")
        self.remote_run_id = normalized
        super().__init__(self.code)


@dataclass(frozen=True)
class DispatchSubmissionAccepted:
    remote_run_id: str

    def __post_init__(self) -> None:
        if not self.remote_run_id.strip():
            raise ValueError("accepted submission requires a durable remote run id or receipt")


@dataclass(frozen=True)
class DispatchSubmissionCompleted:
    title: str
    mime_type: str
    content: str = field(repr=False)
    provider_id: str
    adapter: str
    # Normalized token usage (S03-14-02 ledger), camelCase wire shape; None when
    # the provider path did not observe usage (e.g. recovered external result).
    usage: Mapping[str, Any] | None = field(default=None, repr=False)
    external_action_evidence: tuple[Mapping[str, str], ...] = field(default=(), repr=False)
    # Optional model / modelProfile for ledger visibility (may be redacted for users).
    model: str | None = None
    # Code-owned result-quality facts. This is independent from reviewer
    # responsibility, ArtifactState and external execution outcome.
    result_protocol: Mapping[str, Any] = field(default_factory=dict, repr=False)
    warnings: tuple[str, ...] = field(default=(), repr=False)

    def __post_init__(self) -> None:
        if not all(str(value or "").strip() for value in (
            self.title, self.mime_type, self.content, self.provider_id, self.adapter
        )):
            raise ValueError("completed submission requires a validated provider result")
        normalized: list[Mapping[str, str]] = []
        for item in self.external_action_evidence:
            if not isinstance(item, Mapping) or set(item) != {
                "adapter", "outcome", "evidenceRef", "contentSha256"
            }:
                raise ValueError("completed submission external action evidence is invalid")
            normalized.append(dict(item))
        object.__setattr__(self, "external_action_evidence", tuple(normalized))
        model = str(self.model or "").strip() or None
        object.__setattr__(self, "model", model)
        warnings = tuple(
            dict.fromkeys(str(value).strip() for value in self.warnings if str(value).strip())
        )
        protocol = normalize_result_protocol(self.result_protocol, warnings=warnings)
        if not result_protocol_usable(protocol):
            raise ValueError("completed submission requires a usable result protocol")
        object.__setattr__(self, "result_protocol", protocol)
        object.__setattr__(self, "warnings", tuple(protocol["warnings"]))


@dataclass(frozen=True)
class DispatchSubmissionQueryResult:
    status: Literal[
        "accepted",
        "not_submitted",
        "known_rejected",
        "submitted",
        "running",
        "completed",
        "known_failed",
        "unknown",
    ]
    remote_run_id: str | None = None
    error_code: str | None = None
    result_evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.status not in {
            "accepted", "not_submitted", "known_rejected", "submitted", "running",
            "completed", "known_failed", "unknown",
        }:
            raise ValueError("invalid dispatch submission query status")
        remote_run_id = str(self.remote_run_id or "").strip()
        error_code = str(self.error_code or "").strip()
        if error_code:
            _safe_error_code(error_code)
        if len(remote_run_id) > 240 or any(ord(char) < 32 for char in remote_run_id):
            raise ValueError("remote run id must be a bounded stable identifier")
        evidence_refs = tuple(str(ref or "").strip() for ref in self.result_evidence_refs)
        if any(not ref for ref in evidence_refs):
            raise ValueError("query result evidence refs must be non-empty")
        if self.status in {"accepted", "submitted", "running"} and (
            not remote_run_id or error_code or evidence_refs
        ):
            raise ValueError(f"{self.status} query result requires only a durable remote run id")
        if self.status == "completed" and (not remote_run_id or error_code or not evidence_refs):
            raise ValueError("completed query result requires remote identity and protected result evidence")
        if self.status == "known_rejected" and (not error_code or remote_run_id):
            raise ValueError("known_rejected query result requires only an error code")
        if self.status == "known_failed" and (not error_code or not remote_run_id):
            raise ValueError("known_failed query result requires remote identity and error code")
        if self.status == "unknown" and (not error_code or evidence_refs):
            raise ValueError("unknown query result requires an error code and no result evidence")
        if self.status == "not_submitted" and (remote_run_id or error_code or evidence_refs):
            raise ValueError("not_submitted query result cannot carry an external outcome")


def _safe_error_code(value: str) -> str:
    code = str(value or "").strip()
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,95}", code):
        raise ValueError("provider error code must be a bounded stable identifier")
    return code


class PreparedExternalResultQueryProvider(Protocol):
    """Read-only execution-state query; distinct from T04 submission recovery."""

    query_capable: bool

    def query_external_result(
        self,
        *,
        idempotency_key: str,
        remote_run_id: str | None,
    ) -> DispatchSubmissionQueryResult: ...


class ExternalResultQueryProvider(Protocol):
    def preflight_query(self) -> PreparedExternalResultQueryProvider: ...


class PreparedDispatchProvider(Protocol):
    capabilities: ProviderSubmissionSafety
    provider_id: str
    adapter: str

    def submit(
        self, request: DispatchSubmissionRequest
    ) -> DispatchSubmissionAccepted | DispatchSubmissionCompleted: ...

    def query_by_idempotency_key(self, idempotency_key: str) -> DispatchSubmissionQueryResult: ...


class ResearchDispatchProvider(Protocol):
    """Preflight must perform no transport and return an immutable prepared handle."""

    def preflight(self) -> PreparedDispatchProvider: ...


def require_safe_submission(capabilities: ProviderSubmissionSafety) -> None:
    if capabilities.submissionMode == "single_attempt_no_replay":
        if any((
            capabilities.acceptsIdempotencyKey,
            capabilities.returnsReceiptOrRemoteRunId,
            capabilities.queryByIdempotencyKey,
            capabilities.queryByReceiptOrRemoteRunId,
        )):
            raise DispatchProviderCapabilityError("single_attempt_capability_contradiction")
        return
    if not capabilities.acceptsIdempotencyKey:
        raise DispatchProviderCapabilityError("provider_idempotency_unsupported")
    if not capabilities.returnsReceiptOrRemoteRunId:
        raise DispatchProviderCapabilityError("provider_durable_receipt_unsupported")
    if not capabilities.queryByIdempotencyKey:
        code = (
            "provider_idempotency_query_unsupported"
            if capabilities.queryByReceiptOrRemoteRunId
            else "provider_submission_query_unsupported"
        )
        raise DispatchProviderCapabilityError(code)


class RegistryDispatchProviderFactory:
    """Resolve only an explicit durable provider id; registry defaults are never authority."""

    def __init__(
        self,
        *,
        registry: ProviderRegistry | None = None,
        adapters: Mapping[str, ResearchDispatchProvider] | None = None,
    ) -> None:
        self._registry = registry or load_provider_registry()
        self._adapters = dict(adapters or {})

    def create(self, provider_id: str) -> ResearchDispatchProvider:
        if not str(provider_id or "").strip():
            raise DispatchProviderCapabilityError("dispatch_provider_binding_missing")
        definition = self._registry.require(provider_id)
        require_safe_submission(definition.submissionSafety)
        provider = self._adapters.get(definition.adapter)
        if provider is None and definition.adapter == "huiyan_chat_completions":
            from app.execution.research_dispatch_huiyan_provider import (
                HuiyanResearchDispatchProvider,
            )

            provider = HuiyanResearchDispatchProvider(
                provider_id=provider_id,
                registry=self._registry,
            )
        if provider is None:
            raise DispatchProviderCapabilityError("dispatch_provider_adapter_unsupported")
        return provider


__all__ = [
    "DispatchProviderCapabilityError",
    "DispatchProviderError",
    "DispatchProviderKnownRejected",
    "DispatchProviderPermanentPreflightError",
    "DispatchProviderRetryablePreflightError",
    "DispatchProviderResultUnknown",
    "DispatchSubmissionAccepted",
    "DispatchSubmissionCompleted",
    "DispatchSubmissionRequest",
    "DispatchSubmissionQueryResult",
    "ExternalResultQueryProvider",
    "PreparedExternalResultQueryProvider",
    "PreparedDispatchProvider",
    "ResearchDispatchProvider",
    "RegistryDispatchProviderFactory",
    "SKILL_FINALIZATION_RUNTIME_FAILED",
    "SKILL_FINALIZATION_SYSTEM_RETRY_REASON",
    "STRUCTURED_OUTPUT_CORRECTION_EXHAUSTED",
    "STRUCTURED_OUTPUT_EXPLICIT_RETRY_REASON",
    "require_safe_submission",
]
