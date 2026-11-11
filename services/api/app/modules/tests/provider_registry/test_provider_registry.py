from __future__ import annotations

import copy
from dataclasses import asdict
import json

import pytest

from app.modules.provider_registry import (
    REGISTRY_PATH,
    ProviderRegistryError,
    adapter_protocol,
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


def test_registry_schema_admits_protocol_adapters_and_provider_environments() -> None:
    schema = json.loads(REGISTRY_PATH.with_suffix(".schema.json").read_text(encoding="utf-8"))
    provider = schema["$defs"]["provider"]
    environment = provider["properties"]["environment"]

    assert set(provider["properties"]["adapter"]["enum"]) == {
        "openai_chat_completions",
        "openai_responses",
        "anthropic_messages",
        "huiyan_chat_completions",
    }
    assert set(environment["required"]) == {"model", "baseUrl", "apiKey"}
    assert environment["properties"]["model"] == {"type": "string", "minLength": 1}
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
    response = type(
        "Response",
        (),
        {
            "status_code": 400,
            "text": "unknown provider for model bad-model",
            "content": b"{}",
            "headers": {},
        },
    )()
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



# ---------------------------------------------------------------------------
# Protocol-dimension adapters (openai_chat_completions / openai_responses /
# anthropic_messages; huiyan_chat_completions kept as compatibility alias).
# ---------------------------------------------------------------------------


def test_adapter_protocol_resolves_alias_and_rejects_unknown() -> None:
    assert adapter_protocol("huiyan_chat_completions") == "openai_chat_completions"
    assert adapter_protocol("openai_chat_completions") == "openai_chat_completions"
    assert adapter_protocol("openai_responses") == "openai_responses"
    assert adapter_protocol("anthropic_messages") == "anthropic_messages"
    with pytest.raises(ProviderRegistryError, match="Unsupported provider adapter"):
        adapter_protocol("openai_files")


def test_registry_accepts_openai_responses_adapter_with_provider_specific_environment() -> None:
    registry = build_provider_registry(_protocol_payload("openai_responses", "OPENAI_RESPONSES"))
    config = resolve_provider_runtime_config(
        "openai_primary",
        environ={
            "OPENAI_RESPONSES_MODEL": "gpt-5.6",
            "OPENAI_RESPONSES_BASE_URL": "https://provider.invalid",
            "OPENAI_RESPONSES_API_KEY": "test-key",
        },
        registry=registry,
    )
    assert config.adapter == "openai_responses"
    assert config.protocol == "openai_responses"


def test_registry_accepts_anthropic_messages_adapter_with_provider_specific_environment() -> None:
    registry = build_provider_registry(
        _protocol_payload("anthropic_messages", "ANTHROPIC_MESSAGES")
    )
    config = resolve_provider_runtime_config(
        "openai_primary",
        environ={
            "ANTHROPIC_MESSAGES_MODEL": "claude-sonnet-4-5",
            "ANTHROPIC_MESSAGES_BASE_URL": "https://provider.invalid",
            "ANTHROPIC_MESSAGES_API_KEY": "test-key",
        },
        registry=registry,
    )
    assert config.adapter == "anthropic_messages"
    assert config.protocol == "anthropic_messages"


def test_registry_rejects_unknown_adapter() -> None:
    with pytest.raises(ProviderRegistryError, match="Unsupported provider adapter"):
        build_provider_registry(_protocol_payload("openai_files", "OPENAI_FILES"))


def test_registry_rejects_missing_environment_key_for_any_protocol() -> None:
    payload = _protocol_payload("anthropic_messages", "ANTHROPIC_MESSAGES")
    del payload["providers"][0]["environment"]["apiKey"]
    with pytest.raises(ProviderRegistryError, match="environment key"):
        build_provider_registry(payload)


def test_registry_rejects_fabricated_submission_safety_for_non_chat_protocol() -> None:
    payload = _protocol_payload("openai_responses", "OPENAI_RESPONSES")
    payload["providers"][0]["submissionSafety"] = {
        "submissionMode": "durable_idempotent",
        "acceptsIdempotencyKey": True,
        "returnsReceiptOrRemoteRunId": True,
        "queryByIdempotencyKey": True,
        "queryByReceiptOrRemoteRunId": False,
    }
    with pytest.raises(ProviderRegistryError, match="submission safety"):
        build_provider_registry(payload)


def test_runtime_config_exposes_canonical_protocol_for_alias() -> None:
    registry = build_provider_registry(_payload())
    config = resolve_provider_runtime_config(
        "openai_primary",
        environ={
            "LLM_PROVIDER_MODEL": "loopback-model",
            "LLM_PROVIDER_BASE_URL": "https://provider.invalid",
            "LLM_PROVIDER_API_KEY": "test-key",
        },
        registry=registry,
    )
    assert config.adapter == "huiyan_chat_completions"
    assert config.protocol == "openai_chat_completions"


def test_model_binding_accepts_canonical_protocol_name_for_aliased_adapter() -> None:
    registry = build_provider_registry(_payload())
    env = {
        "LLM_PROVIDER_BASE_URL": "https://provider.invalid",
        "LLM_PROVIDER_API_KEY": "test-key",
        "LLM_PROVIDER_MODEL": "execution-model",
        "OPENTRAJECTORY_LLM_PROVIDER": "openai_chat_completions",
    }
    binding = resolve_model_binding("execution", environ=env, registry=registry, validate=False)
    assert binding.provider == "huiyan_chat_completions"


def test_model_binding_accepts_openai_responses_provider_selection() -> None:
    registry = build_provider_registry(_protocol_payload("openai_responses", "OPENAI_RESPONSES"))
    env = {
        "OPENAI_RESPONSES_BASE_URL": "https://provider.invalid",
        "OPENAI_RESPONSES_API_KEY": "test-key",
        "OPENAI_RESPONSES_MODEL": "planner-model",
        "OPENAIR_RESEARCH_START_PLANNER_LLM_PROVIDER": "openai_responses",
        "OPENAIR_RESEARCH_START_PLANNER_LLM_MODEL": "planner-model",
    }
    binding = resolve_model_binding("planner", environ=env, registry=registry, validate=False)
    assert binding.provider == "openai_responses"


def test_model_binding_accepts_anthropic_messages_provider_selection() -> None:
    registry = build_provider_registry(
        _protocol_payload("anthropic_messages", "ANTHROPIC_MESSAGES")
    )
    env = {
        "ANTHROPIC_MESSAGES_BASE_URL": "https://provider.invalid",
        "ANTHROPIC_MESSAGES_API_KEY": "test-key",
        "ANTHROPIC_MESSAGES_MODEL": "planner-model",
        "OPENAIR_RESEARCH_START_PLANNER_LLM_PROVIDER": "anthropic_messages",
        "OPENAIR_RESEARCH_START_PLANNER_LLM_MODEL": "planner-model",
    }
    binding = resolve_model_binding("planner", environ=env, registry=registry, validate=False)
    assert binding.provider == "anthropic_messages"


@pytest.mark.parametrize(
    ("adapter", "env_prefix", "expected_path", "expected_header"),
    [
        ("openai_responses", "OPENAI_RESPONSES", "/v1/responses", "authorization"),
        ("anthropic_messages", "ANTHROPIC_MESSAGES", "/v1/messages", "x-api-key"),
    ],
)
def test_preflight_probes_protocol_native_endpoint_and_headers(
    monkeypatch: pytest.MonkeyPatch,
    adapter: str,
    env_prefix: str,
    expected_path: str,
    expected_header: str,
) -> None:
    registry = build_provider_registry(_protocol_payload(adapter, env_prefix))
    binding = ModelBinding("planner", adapter, "https://provider.invalid", "known-good")
    monkeypatch.setattr(
        "app.modules.provider_registry.discover_provider_models",
        lambda _config: frozenset({"known-good"}),
    )
    captured: dict = {}

    def _fake_post(url: str, *, headers: dict, json: dict, timeout: float) -> object:
        captured.update({"url": url, "headers": dict(headers), "payload": json})
        return type(
            "Response",
            (),
            {"status_code": 200, "text": "", "content": b"{}", "headers": {}},
        )()

    monkeypatch.setattr("app.modules.provider_registry.httpx.post", _fake_post)
    environ = {
        f"{env_prefix}_MODEL": "known-good",
        f"{env_prefix}_BASE_URL": "https://provider.invalid",
        f"{env_prefix}_API_KEY": "test-key",
    }
    validate_model_binding(binding, environ=environ, registry=registry)
    assert captured["url"].endswith(expected_path)
    assert expected_header in {name.lower() for name in captured["headers"]}


def test_preflight_anthropic_payload_carries_required_max_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = build_provider_registry(
        _protocol_payload("anthropic_messages", "ANTHROPIC_MESSAGES")
    )
    binding = ModelBinding(
        "planner", "anthropic_messages", "https://provider.invalid", "known-good"
    )
    monkeypatch.setattr(
        "app.modules.provider_registry.discover_provider_models",
        lambda _config: frozenset({"known-good"}),
    )
    captured: dict = {}

    def _fake_post(url: str, *, headers: dict, json: dict, timeout: float) -> object:
        captured.update({"url": url, "payload": json})
        return type(
            "Response",
            (),
            {"status_code": 200, "text": "", "content": b"{}", "headers": {}},
        )()

    monkeypatch.setattr("app.modules.provider_registry.httpx.post", _fake_post)
    validate_model_binding(
        binding,
        environ={
            "ANTHROPIC_MESSAGES_MODEL": "known-good",
            "ANTHROPIC_MESSAGES_BASE_URL": "https://provider.invalid",
            "ANTHROPIC_MESSAGES_API_KEY": "test-key",
        },
        registry=registry,
    )
    payload = captured["payload"]
    assert payload["max_tokens"] >= 1
    assert payload["messages"][0]["role"] == "user"
    assert "response_format" not in payload
    assert "tools" not in payload


def _protocol_payload(adapter: str, env_prefix: str) -> dict:
    payload = _payload()
    provider = payload["providers"][0]
    provider["adapter"] = adapter
    provider["environment"] = {
        "model": f"{env_prefix}_MODEL",
        "baseUrl": f"{env_prefix}_BASE_URL",
        "apiKey": f"{env_prefix}_API_KEY",
    }
    return payload
