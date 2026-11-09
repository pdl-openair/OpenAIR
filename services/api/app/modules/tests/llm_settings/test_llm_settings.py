"""LLM gateway settings overlay: persist, hot-apply, mask secrets, probe."""

from __future__ import annotations

import json

import httpx
import pytest

from app.modules import llm_settings


@pytest.fixture()
def overlay_path(tmp_path, monkeypatch):
    path = tmp_path / "llm_settings.json"
    monkeypatch.setattr(llm_settings, "settings_path", lambda: path)
    for env_key in llm_settings._FIELD_ENV.values():
        monkeypatch.delenv(env_key, raising=False)
    return path


def test_save_persists_and_applies_to_environ(overlay_path):
    changed = llm_settings.save(
        {
            "providerBaseUrl": "https://gw.example.com",
            "providerApiKey": "test-placeholder-key",
            "providerModel": "gpt-4o-mini",
            "plannerModel": "gpt-4o-mini",
        }
    )
    assert set(changed) == {"providerBaseUrl", "providerApiKey", "providerModel", "plannerModel"}
    import os

    assert os.environ["LLM_PROVIDER_BASE_URL"] == "https://gw.example.com"
    assert os.environ["LLM_PROVIDER_API_KEY"] == "test-placeholder-key"
    persisted = json.loads(overlay_path.read_text(encoding="utf-8"))
    assert persisted["providerModel"] == "gpt-4o-mini"


def test_blank_value_clears_field_and_file(overlay_path):
    llm_settings.save({"claudeModel": "claude-sonnet-4-5"})
    changed = llm_settings.save({"claudeModel": ""})
    assert changed == {"claudeModel": ""}
    assert not overlay_path.exists()


def test_masked_view_never_exposes_secret_values(overlay_path, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER_API_KEY", "placeholder-secret-key")
    view = llm_settings.masked_view()
    assert view["providerApiKey"]["configured"] is True
    assert "placeholder-secret-key" not in json.dumps(view, ensure_ascii=False)
    assert view["workerRestartRequired"] is True


def test_invalid_base_url_rejected(overlay_path):
    with pytest.raises(ValueError, match="base URL"):
        llm_settings.save({"providerBaseUrl": "not a url://||"})


def test_test_connection_merges_env_overlay_and_overrides(overlay_path, monkeypatch):
    import app.modules.provider_registry as registry_module

    monkeypatch.setenv("LLM_PROVIDER_BASE_URL", "https://gw.example.com")
    monkeypatch.setenv("LLM_PROVIDER_API_KEY", "sk-live")
    monkeypatch.setenv("LLM_PROVIDER_MODEL", "gpt-4o-mini")
    monkeypatch.setenv("OPENAIR_RESEARCH_START_PLANNER_LLM_MODEL", "gpt-4o-mini")

    def fake_discover(config):
        assert config.baseUrl == "https://candidate.example.com"
        return frozenset({"gpt-4o-mini", "claude-sonnet-4-5"})

    monkeypatch.setattr(registry_module, "discover_provider_models", fake_discover)
    result = llm_settings.test_connection({"providerBaseUrl": "https://candidate.example.com"})
    assert result["provider"]["ok"] is True
    assert result["provider"]["modelAvailable"] is True
    assert result["anthropic"]["configured"] is False


def test_test_connection_reports_provider_failure(overlay_path, monkeypatch):
    import app.modules.provider_registry as registry_module

    monkeypatch.setenv("LLM_PROVIDER_BASE_URL", "https://gw.example.com")
    monkeypatch.setenv("LLM_PROVIDER_API_KEY", "sk-live")
    monkeypatch.setenv("LLM_PROVIDER_MODEL", "gpt-4o-mini")

    def fake_discover(config):
        raise registry_module.ProviderRegistryError(
            "Provider model discovery failed: HTTP 401"
        )

    monkeypatch.setattr(registry_module, "discover_provider_models", fake_discover)
    result = llm_settings.test_connection()
    assert result["provider"]["ok"] is False
    assert "401" in result["provider"]["detail"]
