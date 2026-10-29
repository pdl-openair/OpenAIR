"""Provider configuration registry and fail-closed environment resolver."""

from __future__ import annotations

import os
import re
from math import isfinite
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Mapping
from urllib.parse import urlsplit
from uuid import uuid4

import yaml
import httpx

from app.modules.repo_paths import repo_root


REGISTRY_PATH = repo_root() / "schemas" / "providers" / "provider-registry.yaml"
VALID_ADAPTERS = {"huiyan_chat_completions"}
REQUIRED_ENVIRONMENT_KEYS = ("model", "baseUrl", "apiKey")
REQUIRED_HUIYAN_ENVIRONMENT = {
    "model": "LLM_PROVIDER_MODEL",
    "baseUrl": "LLM_PROVIDER_BASE_URL",
    "apiKey": "LLM_PROVIDER_API_KEY",
}
MODEL_PROFILE_ENVIRONMENT = {
    "terra": "LLM_PROVIDER_MODEL_TERRA",
    "sol": "LLM_PROVIDER_MODEL_SOL",
}
_PROVIDER_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


class ProviderRegistryError(ValueError):
    """Raised for invalid provider policy or unavailable provider configuration."""


def is_transient_provider_error(exc: ProviderRegistryError) -> bool:
    """Return whether an error denotes temporary upstream unavailability.

    Provider configuration and binding failures intentionally use different
    messages and remain fatal.  Keep this classification narrow and tied to
    the explicit prefix emitted by network preflight/discovery paths.
    """
    return str(exc).startswith("Provider temporarily unavailable: ")


@dataclass(frozen=True)
class ProviderSubmissionSafety:
    acceptsIdempotencyKey: bool
    returnsReceiptOrRemoteRunId: bool
    queryByIdempotencyKey: bool
    queryByReceiptOrRemoteRunId: bool
    submissionMode: Literal["durable_idempotent", "single_attempt_no_replay"] = "durable_idempotent"

    @property
    def supports_safe_submission(self) -> bool:
        if self.submissionMode == "single_attempt_no_replay":
            return not any((
                self.acceptsIdempotencyKey,
                self.returnsReceiptOrRemoteRunId,
                self.queryByIdempotencyKey,
                self.queryByReceiptOrRemoteRunId,
            ))
        return bool(
            self.acceptsIdempotencyKey
            and self.returnsReceiptOrRemoteRunId
            and self.queryByIdempotencyKey
        )


@dataclass(frozen=True)
class ProviderDefinition:
    providerId: str
    adapter: str
    maxOutputChars: int
    environment: dict[str, str]
    submissionSafety: ProviderSubmissionSafety


@dataclass(frozen=True)
class ProviderRuntimeConfig:
    providerId: str
    adapter: str
    model: str
    baseUrl: str = field(repr=False)
    apiKey: str = field(repr=False)
    timeoutSeconds: float = 0.0


@dataclass(frozen=True)
class ModelBinding:
    role: str
    provider: str
    endpoint: str
    model: str


@dataclass(frozen=True)
class ProviderRegistry:
    schemaVersion: int
    defaultProviderId: str
    providers: dict[str, ProviderDefinition]

    def ids(self) -> set[str]:
        return set(self.providers)

    def require(self, provider_id: str) -> ProviderDefinition:
        provider = self.providers.get(provider_id)
        if provider is None:
            raise ProviderRegistryError(f"Provider is not registered: {provider_id}")
        return provider


def load_provider_registry_payload(path: Path = REGISTRY_PATH) -> dict[str, Any]:
    if not path.exists():
        raise ProviderRegistryError(f"Provider Registry file does not exist: {path}")
    with path.open(encoding="utf-8") as stream:
        payload = yaml.safe_load(stream) or {}
    if not isinstance(payload, dict):
        raise ProviderRegistryError(f"Provider Registry file must contain a mapping: {path}")
    return payload


def validate_provider_registry_payload(payload: dict[str, Any]) -> None:
    if not isinstance(payload, dict) or payload.get("schemaVersion") != 1:
        raise ProviderRegistryError("Provider Registry schemaVersion must be 1")
    providers = payload.get("providers")
    if not isinstance(providers, list) or not providers:
        raise ProviderRegistryError("Provider Registry must contain at least one provider")

    ids: set[str] = set()
    for raw in providers:
        if not isinstance(raw, dict):
            raise ProviderRegistryError("Provider record must be a mapping")
        provider_id = _clean(raw.get("providerId"))
        if not _PROVIDER_ID_PATTERN.fullmatch(provider_id):
            raise ProviderRegistryError("providerId must be a logical lowercase identifier")
        if provider_id in ids:
            raise ProviderRegistryError(f"Duplicate providerId: {provider_id}")
        ids.add(provider_id)
        if _clean(raw.get("adapter")) not in VALID_ADAPTERS:
            raise ProviderRegistryError(f"Unsupported provider adapter for {provider_id}")
        max_output_chars = raw.get("maxOutputChars")
        if not isinstance(max_output_chars, int) or isinstance(max_output_chars, bool) or max_output_chars < 1:
            raise ProviderRegistryError(f"Provider {provider_id} requires a positive maxOutputChars")
        environment = raw.get("environment")
        if not isinstance(environment, dict):
            raise ProviderRegistryError(f"Provider environment must be a mapping: {provider_id}")
        for key, expected_variable in REQUIRED_HUIYAN_ENVIRONMENT.items():
            if _clean(environment.get(key)) != expected_variable:
                raise ProviderRegistryError(f"Provider {provider_id} requires HUIYAN environment key: {key}")
        submission_safety = raw.get("submissionSafety")
        expected_safety_keys = {
            "acceptsIdempotencyKey",
            "returnsReceiptOrRemoteRunId",
            "queryByIdempotencyKey",
            "queryByReceiptOrRemoteRunId",
            "submissionMode",
        }
        if not isinstance(submission_safety, dict) or set(submission_safety) != expected_safety_keys:
            raise ProviderRegistryError(f"Provider {provider_id} requires explicit submissionSafety flags")
        if any(
            not isinstance(submission_safety[key], bool)
            for key in expected_safety_keys
            if key != "submissionMode"
        ):
            raise ProviderRegistryError(f"Provider {provider_id} submissionSafety flags must be boolean")
        submission_mode = _clean(submission_safety.get("submissionMode"))
        if submission_mode not in {"durable_idempotent", "single_attempt_no_replay"}:
            raise ProviderRegistryError(f"Provider {provider_id} submissionMode is invalid")
        claimed_safety = any(
            submission_safety[key]
            for key in expected_safety_keys
            if key != "submissionMode"
        )
        if _clean(raw.get("adapter")) == "huiyan_chat_completions" and (
            submission_mode != "single_attempt_no_replay" or claimed_safety
        ):
            raise ProviderRegistryError(
                f"Provider {provider_id} cannot claim unsupported HUIYAN submission safety"
            )
    default_provider_id = _clean(payload.get("defaultProviderId"))
    if not _PROVIDER_ID_PATTERN.fullmatch(default_provider_id):
        raise ProviderRegistryError("defaultProviderId must be a logical lowercase identifier")
    if default_provider_id not in ids:
        raise ProviderRegistryError("defaultProviderId must reference a registered providerId")


def build_provider_registry(payload: dict[str, Any], *, validate: bool = True) -> ProviderRegistry:
    if validate:
        validate_provider_registry_payload(payload)
    providers = {
        str(row["providerId"]): ProviderDefinition(
            providerId=str(row["providerId"]),
            adapter=str(row["adapter"]),
            maxOutputChars=int(row["maxOutputChars"]),
            environment={key: str(value) for key, value in dict(row["environment"]).items()},
            submissionSafety=ProviderSubmissionSafety(**dict(row["submissionSafety"])),
        )
        for row in payload.get("providers") or []
    }
    return ProviderRegistry(
        schemaVersion=int(payload.get("schemaVersion") or 1),
        defaultProviderId=str(payload.get("defaultProviderId") or ""),
        providers=providers,
    )


def load_provider_registry(path: Path = REGISTRY_PATH) -> ProviderRegistry:
    return build_provider_registry(load_provider_registry_payload(path))


def resolve_provider_runtime_config(
    provider_id: str,
    *,
    model_profile: str | None = None,
    model: str | None = None,
    environ: Mapping[str, str] | None = None,
    registry: ProviderRegistry | None = None,
) -> ProviderRuntimeConfig:
    """Resolve one configured provider without attempting a network connection."""
    provider = (registry or load_provider_registry()).require(provider_id)
    values = environ if environ is not None else os.environ
    environment = dict(provider.environment)
    if model is not None and model_profile is not None:
        raise ProviderRegistryError("Provider model authority is ambiguous")
    explicit_model = _clean(model) if model is not None else ""
    if model is not None:
        if not explicit_model:
            raise ProviderRegistryError("Provider model is invalid")
        environment.pop("model", None)
    elif model_profile is not None:
        profile = _clean(model_profile)
        model_environment = MODEL_PROFILE_ENVIRONMENT.get(profile)
        if model_environment is None:
            raise ProviderRegistryError("Provider model profile is invalid")
        environment["model"] = model_environment
    resolved = {key: _clean(values.get(variable)) for key, variable in environment.items()}
    if model is not None:
        resolved["model"] = explicit_model
    missing = [key for key in REQUIRED_ENVIRONMENT_KEYS if not resolved.get(key)]
    if missing:
        raise ProviderRegistryError(f"Provider configuration is incomplete for {provider_id}: {', '.join(missing)}")
    parsed_url = urlsplit(resolved["baseUrl"])
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc or parsed_url.query or parsed_url.fragment:
        raise ProviderRegistryError(f"Provider base URL is invalid: {provider_id}")
    try:
        timeout_seconds = float(_clean(values.get("OPENAIR_DURABLE_ORCHESTRATE_TIMEOUT_SECONDS")) or "300")
    except ValueError as exc:
        raise ProviderRegistryError(f"Provider timeout is invalid: {provider_id}") from exc
    if not isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ProviderRegistryError(f"Provider timeout is invalid: {provider_id}")
    return ProviderRuntimeConfig(
        providerId=provider.providerId,
        adapter=provider.adapter,
        model=resolved["model"],
        baseUrl=resolved["baseUrl"],
        apiKey=resolved["apiKey"],
        timeoutSeconds=timeout_seconds,
    )


def _models_endpoint(base_url: str) -> str:
    base = base_url.rstrip("/")
    return f"{base}/models" if base.endswith("/v1") else f"{base}/v1/models"


def _probe_context(*, authority: str):
    from app.modules.provider_transport_evidence import (
        current_provider_execution_parent,
        provider_execution_parent,
    )

    current = current_provider_execution_parent()
    if current is not None:
        from contextlib import nullcontext
        return nullcontext(current)
    return provider_execution_parent(
        kind="system_probe",
        parent_id=f"probe_{uuid4().hex}",
        originating_authority=authority,
    )


def _finish_probe_transport_error(repository, attempt, request_body: bytes, exc: BaseException) -> None:
    definitely_not_submitted = isinstance(
        exc, (httpx.ConnectTimeout, httpx.ConnectError, httpx.PoolTimeout)
    )
    repository.finish(
        attempt,
        request_body=request_body,
        outcome="transport_not_submitted" if definitely_not_submitted else "transport_uncertain",
        http_status=None,
        response_body=None,
        response_headers=None,
        stage="provider_readiness_transport",
    )


def discover_provider_models(config: ProviderRuntimeConfig) -> frozenset[str]:
    """Return live model IDs with the probe itself recorded as physical I/O."""
    from app.modules.provider_transport_evidence import ProviderHttpAttemptRepository

    endpoint = _models_endpoint(config.baseUrl)
    request_body = b'{"method":"GET","operation":"model_discovery"}'
    with _probe_context(authority="provider_registry:model_discovery"):
        repository = ProviderHttpAttemptRepository(checkpoint_repository=None)
        attempt = repository.begin(
            capability_call_id="",
            attempt_number=1,
            request_body=request_body,
            endpoint=endpoint,
            adapter=config.adapter,
            operation_identity="model_discovery",
            provider=config.providerId,
            model=config.model,
        )
        headers = {
            "Authorization": f"Bearer {config.apiKey}",
            **attempt.headers,
        }
        repository.mark_io_started(attempt)
        try:
            response = httpx.get(
                endpoint,
                headers=headers,
                timeout=min(config.timeoutSeconds, 30.0),
            )
        except httpx.TimeoutException as exc:
            _finish_probe_transport_error(repository, attempt, request_body, exc)
            raise ProviderRegistryError("Provider temporarily unavailable: model discovery timed out") from exc
        except httpx.HTTPError as exc:
            _finish_probe_transport_error(repository, attempt, request_body, exc)
            raise ProviderRegistryError("Provider temporarily unavailable: model discovery transport failure") from exc
        repository.finish(
            attempt,
            request_body=request_body,
            outcome="response_received" if response.status_code < 400 else "provider_http_error",
            http_status=response.status_code,
            response_body=response.content,
            response_headers=response.headers,
            stage="provider_readiness_response",
        )
    if response.status_code in {404, 405}:
        raise ProviderRegistryError("Provider model discovery is unsupported")
    if response.status_code >= 500 or response.status_code == 429:
        raise ProviderRegistryError(f"Provider temporarily unavailable: model discovery HTTP {response.status_code}")
    if response.status_code != 200:
        raise ProviderRegistryError(f"Provider model discovery failed: HTTP {response.status_code}")
    try:
        payload = response.json()
        ids = frozenset(str(row["id"]).strip() for row in payload["data"] if isinstance(row, dict) and str(row.get("id") or "").strip())
    except (TypeError, ValueError, KeyError) as exc:
        raise ProviderRegistryError("Provider model discovery returned an invalid response") from exc
    if not ids:
        raise ProviderRegistryError("Provider model discovery returned no models")
    return ids


def validate_model_binding(binding: ModelBinding, *, environ: Mapping[str, str] | None = None,
                           registry: ProviderRegistry | None = None) -> ModelBinding:
    """Fail before business work when the configured model is absent upstream."""
    env = dict(environ if environ is not None else os.environ)
    env["LLM_PROVIDER_MODEL"] = binding.model
    active_registry = registry or load_provider_registry()
    provider_id = active_registry.defaultProviderId
    config = resolve_provider_runtime_config(provider_id, environ=env, registry=active_registry)
    if binding.model not in discover_provider_models(config):
        raise ProviderRegistryError(
            f"Invalid model binding: role={binding.role} provider={binding.provider} model={binding.model} "
            "reason=model is not supported by configured provider"
        )
    _preflight_model(config, binding)
    return binding


def _preflight_model(config: ProviderRuntimeConfig, binding: ModelBinding) -> None:
    """Verify routability while preserving the physical probe result separately."""
    from app.modules.provider_transport_evidence import ProviderHttpAttemptRepository

    endpoint = f"{config.baseUrl.rstrip('/')}/v1/chat/completions"
    payload = {"model": binding.model, "messages": [{"role": "user", "content": "Reply with OK."}], "max_tokens": 1}
    request_body = __import__("json").dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    with _probe_context(authority="provider_registry:model_preflight"):
        repository = ProviderHttpAttemptRepository(checkpoint_repository=None)
        attempt = repository.begin(
            capability_call_id="",
            attempt_number=1,
            request_body=request_body,
            endpoint=endpoint,
            adapter=config.adapter,
            operation_identity="model_preflight",
            provider=config.providerId,
            model=binding.model,
        )
        headers = {
            "Authorization": f"Bearer {config.apiKey}",
            **attempt.headers,
        }
        repository.mark_io_started(attempt)
        try:
            response = httpx.post(
                endpoint,
                headers=headers,
                json=payload,
                timeout=min(config.timeoutSeconds, 30.0),
            )
        except httpx.TimeoutException as exc:
            _finish_probe_transport_error(repository, attempt, request_body, exc)
            raise ProviderRegistryError("Provider temporarily unavailable: model preflight timed out") from exc
        except httpx.HTTPError as exc:
            _finish_probe_transport_error(repository, attempt, request_body, exc)
            raise ProviderRegistryError("Provider temporarily unavailable: model preflight transport failure") from exc
        repository.finish(
            attempt,
            request_body=request_body,
            outcome="response_received" if response.status_code < 400 else "provider_http_error",
            http_status=response.status_code,
            response_body=response.content,
            response_headers=response.headers,
            stage="provider_readiness_response",
        )
    if response.status_code in {429} or response.status_code >= 500:
        raise ProviderRegistryError(f"Provider temporarily unavailable: model preflight HTTP {response.status_code}")
    if response.status_code >= 400:
        detail = response.text.lower()
        deterministic = ("unknown model", "unknown provider for model", "model_not_found", "unsupported_model")
        if any(marker in detail for marker in deterministic):
            raise ProviderRegistryError(
                f"Invalid model binding: role={binding.role} provider={binding.provider} model={binding.model} "
                f"reason=provider rejected model (HTTP {response.status_code})"
            )
        raise ProviderRegistryError(f"Provider model preflight failed: HTTP {response.status_code}")


def resolve_model_binding(role: str, *, environ: Mapping[str, str] | None = None,
                          registry: ProviderRegistry | None = None, validate: bool = True) -> ModelBinding:
    values = environ if environ is not None else os.environ
    role_key = str(role or "").strip().lower()
    prefix = {"planner": "OPENAIR_RESEARCH_START_PLANNER", "execution": "LLM_PROVIDER"}.get(role_key)
    if prefix is None:
        raise ProviderRegistryError(f"Invalid model binding: role={role_key} reason=unknown role")
    active_registry = registry or load_provider_registry()
    configured_provider = _clean(values.get(f"{prefix}_LLM_PROVIDER" if role_key != "execution" else "OPENTRAJECTORY_LLM_PROVIDER"))
    provider_id = active_registry.defaultProviderId
    provider = active_registry.require(provider_id)
    if configured_provider and configured_provider != provider.adapter:
        raise ProviderRegistryError(
            f"Invalid model binding: role={role_key} provider={configured_provider} "
            f"reason=provider is not the configured adapter ({provider.adapter})"
        )
    model_var = f"{prefix}_LLM_MODEL" if role_key != "execution" else "LLM_PROVIDER_MODEL"
    model = _clean(values.get(model_var))
    if not model:
        raise ProviderRegistryError(f"Invalid model binding: role={role_key} provider={provider.adapter} model=<missing> reason=explicit model configuration is required")
    env = dict(values)
    if model:
        env["LLM_PROVIDER_MODEL"] = model
    try:
        config = resolve_provider_runtime_config(provider_id, environ=env, registry=active_registry)
    except ProviderRegistryError as exc:
        raise ProviderRegistryError(
            f"Invalid model binding: role={role_key} provider={provider.adapter} model={model or '<missing>'} reason={exc}"
        ) from exc
    binding = ModelBinding(role_key, config.adapter, config.baseUrl, config.model)
    return validate_model_binding(binding, environ=env, registry=active_registry) if validate else binding


def _clean(value: Any) -> str:
    return str(value or "").strip()
