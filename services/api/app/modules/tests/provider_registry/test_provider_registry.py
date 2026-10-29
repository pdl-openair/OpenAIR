from __future__ import annotations

import copy
from dataclasses import asdict
import json

import pytest

from app.modules.provider_registry import (
    REGISTRY_PATH,
    ProviderRegistryError,
    build_provider_registry,
    load_provider_registry,
    load_provider_registry_payload,
    ModelBinding,
    validate_model_binding,
    resolve_provider_runtime_config,
    resolve_model_binding,
    is_transient_provider_error,
)


def test_registry_keeps_logical_provider_id_and_uses_existing_huiyan_environment() -> None:
    provider = load_provider_registry().require("openai_primary")

    assert provider.adapter == "huiyan_chat_completions"
    assert provider.environment == {
        "model": "LLM_PROVIDER_MODEL",
        "baseUrl": "LLM_PROVIDER_BASE_URL",
        "apiKey": "LLM_PROVIDER_API_KEY",
    }


def test_huiyan_registry_declares_single_attempt_without_fabricated_capabilities() -> None:
    provider = load_provider_registry_payload()["providers"][0]

    assert provider.get("submissionSafety") == {
        "submissionMode": "single_attempt_no_replay",
        "acceptsIdempotencyKey": False,
        "returnsReceiptOrRemoteRunId": False,
        "queryByIdempotencyKey": False,
        "queryByReceiptOrRemoteRunId": False,
    }
    assert asdict(load_provider_registry().require("openai_primary").submissionSafety) == provider["submissionSafety"]


def test_receipt_only_query_is_not_safe_for_automatic_submission_recovery() -> None:
    safety = load_provider_registry().require("openai_primary").submissionSafety
    receipt_only = safety.__class__(True, True, False, True)

    assert receipt_only.supports_safe_submission is False


def test_registry_schema_requires_chat_completions_and_existing_environment_names() -> None:
    schema = json.loads(REGISTRY_PATH.with_suffix(".schema.json").read_text(encoding="utf-8"))
    provider = schema["$defs"]["provider"]
    environment = provider["properties"]["environment"]

    assert provider["properties"]["adapter"]["const"] == "huiyan_chat_completions"
    assert set(environment["required"]) == {"model", "baseUrl", "apiKey"}
    assert environment["properties"]["model"]["const"] == "LLM_PROVIDER_MODEL"
    assert set(provider["properties"]["submissionSafety"]["required"]) == {
        "submissionMode",
        "acceptsIdempotencyKey",
        "returnsReceiptOrRemoteRunId",
        "queryByIdempotencyKey",
        "queryByReceiptOrRemoteRunId",
    }


def test_registry_rejects_missing_or_replaced_existing_environment_names() -> None:
    payload = _payload()
    payload["providers"][0]["environment"].pop("apiKey")
    with pytest.raises(ProviderRegistryError, match="apiKey"):
        build_provider_registry(payload)


def test_huiyan_registry_rejects_fabricated_submission_safety() -> None:
    payload = _payload()
    payload["providers"][0]["submissionSafety"]["acceptsIdempotencyKey"] = True

    with pytest.raises(ProviderRegistryError, match="cannot claim unsupported"):
        build_provider_registry(payload)

    payload = _payload()
    payload["providers"][0]["environment"]["model"] = "OPENAIR_HUIYAN_MODEL"
    with pytest.raises(ProviderRegistryError, match="HUIYAN"):
        build_provider_registry(payload)


def test_runtime_config_fails_closed_for_missing_existing_configuration() -> None:
    registry = build_provider_registry(_payload())
    with pytest.raises(ProviderRegistryError, match="incomplete"):
        resolve_provider_runtime_config("openai_primary", environ={}, registry=registry)

    config = resolve_provider_runtime_config(
        "openai_primary",
        environ={
            "LLM_PROVIDER_MODEL": "loopback-model",
            "LLM_PROVIDER_BASE_URL": "https://provider.invalid",
            "LLM_PROVIDER_API_KEY": "test-key",
            "OPENAIR_DURABLE_ORCHESTRATE_TIMEOUT_SECONDS": "30",
            "OPENAIR_OPENAI_ENABLED": "false",
        },
        registry=registry,
    )
    assert config.adapter == "huiyan_chat_completions"
    assert config.model == "loopback-model"
    assert config.timeoutSeconds == 30


def test_model_binding_is_role_scoped_and_configuration_driven() -> None:
    registry = build_provider_registry(_payload())
    env = {
        "LLM_PROVIDER_BASE_URL": "https://provider.invalid",
        "LLM_PROVIDER_API_KEY": "test-key",
        "OPENAIR_RESEARCH_START_PLANNER_LLM_MODEL": "known-a",
        "LLM_PROVIDER_MODEL": "execution-model",
    }
    assert resolve_model_binding("planner", environ=env, registry=registry, validate=False).model == "known-a"
    assert resolve_model_binding("execution", environ=env, registry=registry, validate=False).model == "execution-model"


def test_unsupported_model_binding_fails_before_provider_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = build_provider_registry(_payload())
    env = {
        "LLM_PROVIDER_BASE_URL": "https://provider.invalid",
        "LLM_PROVIDER_API_KEY": "test-key",
        "OPENAIR_RESEARCH_START_PLANNER_LLM_MODEL": "definitely-unsupported",
    }
    monkeypatch.setattr("app.modules.provider_registry.discover_provider_models", lambda _config: frozenset({"known-good"}))
    monkeypatch.setattr("app.modules.provider_registry._preflight_model", lambda *_args: None)
    with pytest.raises(ProviderRegistryError, match="role=planner.*model=definitely-unsupported.*not supported"):
        resolve_model_binding("planner", environ=env, registry=registry)


def test_discovery_failure_is_not_misclassified_as_unsupported(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = build_provider_registry(_payload())
    env = {
        "LLM_PROVIDER_BASE_URL": "https://provider.invalid",
        "LLM_PROVIDER_API_KEY": "test-key",
        "OPENAIR_RESEARCH_START_PLANNER_LLM_MODEL": "known-good",
    }
    monkeypatch.setattr(
        "app.modules.provider_registry.discover_provider_models",
        lambda _config: (_ for _ in ()).throw(ProviderRegistryError("Provider temporarily unavailable: model discovery timed out")),
    )
    with pytest.raises(ProviderRegistryError, match="temporarily unavailable"):
        resolve_model_binding("planner", environ=env, registry=registry)


def test_transient_provider_error_classification_is_narrow() -> None:
    assert is_transient_provider_error(
        ProviderRegistryError("Provider temporarily unavailable: model preflight timed out")
    )
    assert not is_transient_provider_error(
        ProviderRegistryError("Invalid model binding: provider configuration is incomplete")
    )
    assert not is_transient_provider_error(
        ProviderRegistryError("Provider model preflight failed: HTTP 400")
    )


def test_preflight_maps_deterministic_model_rejection_to_invalid_binding(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = build_provider_registry(_payload())
    binding = ModelBinding("planner", "huiyan_chat_completions", "https://provider.invalid", "bad-model")
    monkeypatch.setattr("app.modules.provider_registry.discover_provider_models", lambda _config: frozenset({"bad-model"}))
    response = type("Response", (), {"status_code": 400, "text": "unknown provider for model bad-model"})()
    monkeypatch.setattr("app.modules.provider_registry.httpx.post", lambda *args, **kwargs: response)
    with pytest.raises(ProviderRegistryError, match="Invalid model binding.*provider rejected model"):
        validate_model_binding(binding, environ={"LLM_PROVIDER_MODEL": "bad-model", "LLM_PROVIDER_BASE_URL": "https://provider.invalid", "LLM_PROVIDER_API_KEY": "test-key"}, registry=registry)


def _payload() -> dict:
    return copy.deepcopy({
        "schemaVersion": 1,
        "defaultProviderId": "openai_primary",
        "providers": [{
            "providerId": "openai_primary",
            "adapter": "huiyan_chat_completions",
        "maxOutputChars": 64,
        "submissionSafety": {
            "submissionMode": "single_attempt_no_replay",
            "acceptsIdempotencyKey": False,
            "returnsReceiptOrRemoteRunId": False,
            "queryByIdempotencyKey": False,
            "queryByReceiptOrRemoteRunId": False,
        },
        "environment": {
                "model": "LLM_PROVIDER_MODEL",
                "baseUrl": "LLM_PROVIDER_BASE_URL",
                "apiKey": "LLM_PROVIDER_API_KEY",
            },
        }],
    })
