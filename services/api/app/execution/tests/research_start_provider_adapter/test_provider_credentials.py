"""Provider credential resolution for ResearchStart physical submissions.

The provider registry owns the credential; the legacy ``LLM_PROVIDER_*``
environment pair is only a fallback. Authentication headers follow the
protocol, so an Anthropic Messages attempt never sends a Bearer token.
"""

from types import SimpleNamespace

import pytest
from app.execution import research_start_provider_adapter as adapter
from app.modules import provider_registry


def _registry_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    def unavailable():
        raise provider_registry.ProviderRegistryError("registry unavailable")

    monkeypatch.setattr(provider_registry, "load_provider_registry", unavailable)


def test_anthropic_headers_use_x_api_key_without_bearer(monkeypatch: pytest.MonkeyPatch) -> None:
    _registry_unavailable(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER_API_KEY", "anthropic-key")

    headers = adapter._authorization_headers("anthropic_messages")

    assert headers["x-api-key"] == "anthropic-key"
    assert headers["anthropic-version"]
    assert "Authorization" not in headers


def test_chat_headers_use_bearer_authorization(monkeypatch: pytest.MonkeyPatch) -> None:
    _registry_unavailable(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER_API_KEY", "chat-key")

    headers = adapter._authorization_headers("huiyan_chat_completions")

    assert headers["Authorization"] == "Bearer chat-key"
    assert "x-api-key" not in headers


def test_registry_credential_wins_over_legacy_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        provider_registry, "load_provider_registry",
        lambda: SimpleNamespace(
            defaultProviderId="openai_primary",
            require=lambda _pid: SimpleNamespace(
                environment={
                    "model": "CUSTOM_MODEL",
                    "baseUrl": "CUSTOM_BASE_URL",
                    "apiKey": "CUSTOM_API_KEY",
                },
            ),
        ),
    )
    monkeypatch.setenv("CUSTOM_API_KEY", "registry-key")
    monkeypatch.setenv("CUSTOM_BASE_URL", "https://registry.invalid")
    monkeypatch.setenv("LLM_PROVIDER_API_KEY", "legacy-key")

    assert adapter.provider_api_key() == "registry-key"


def test_missing_credential_raises_configuration_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _registry_unavailable(monkeypatch)
    monkeypatch.delenv("LLM_PROVIDER_API_KEY", raising=False)

    with pytest.raises(adapter._ProviderConfigurationError):
        adapter._authorization_headers("anthropic_messages")
