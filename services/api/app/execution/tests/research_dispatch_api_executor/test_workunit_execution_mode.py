from __future__ import annotations

import app.config as config_module
import worker.entrypoint as worker_entrypoint
from app.execution import research_dispatch_api_executor


def test_workunit_execution_mode_defaults_to_worker(monkeypatch):
    monkeypatch.delenv("OPENAIR_WORKUNIT_EXECUTION_MODE", raising=False)
    assert config_module._workunit_execution_mode() == "worker"


def test_workunit_execution_mode_invalid_fails_safe_to_worker(monkeypatch):
    monkeypatch.setenv("OPENAIR_WORKUNIT_EXECUTION_MODE", "surprise")
    assert config_module._workunit_execution_mode() == "worker"


def test_workunit_execution_modes_are_explicit(monkeypatch):
    for value in ("worker", "inline", "worker_with_fallback"):
        monkeypatch.setenv("OPENAIR_WORKUNIT_EXECUTION_MODE", value)
        assert config_module._workunit_execution_mode() == value


def test_worker_role_is_disabled_in_inline_mode(monkeypatch):
    calls: list[str] = []

    class Resolver:
        def resolve(self):
            calls.append("resolve")
            return None

    monkeypatch.setattr(worker_entrypoint.settings, "workunit_execution_mode", "inline")
    assert worker_entrypoint.claim_and_run_research_dispatch(
        binding_resolver=Resolver(),
        execution_role="worker",
    ) is False
    assert calls == []


def test_api_role_is_disabled_in_worker_mode(monkeypatch):
    calls: list[str] = []

    class Resolver:
        def resolve(self):
            calls.append("resolve")
            return None

    monkeypatch.setattr(worker_entrypoint.settings, "workunit_execution_mode", "worker")
    assert worker_entrypoint.claim_and_run_research_dispatch(
        binding_resolver=Resolver(),
        execution_role="api",
    ) is False
    assert calls == []


def test_fallback_api_role_applies_pending_age_gate(monkeypatch):
    captured: list[int] = []

    class Resolver:
        def resolve(self):
            return None

    def resolver_factory(*, min_pending_age_seconds: int = 0, **_kwargs):
        captured.append(min_pending_age_seconds)
        return Resolver()

    monkeypatch.setattr(worker_entrypoint.settings, "workunit_execution_mode", "worker_with_fallback")
    monkeypatch.setattr(worker_entrypoint.settings, "workunit_fallback_after_seconds", 30)
    monkeypatch.setattr(worker_entrypoint, "PostgresDispatchProviderBindingResolver", resolver_factory)

    assert worker_entrypoint.claim_and_run_research_dispatch(execution_role="api") is False
    assert captured == [30]


def test_inline_api_role_has_no_pending_age_delay(monkeypatch):
    captured: list[int] = []

    class Resolver:
        def resolve(self):
            return None

    def resolver_factory(*, min_pending_age_seconds: int = 0, **_kwargs):
        captured.append(min_pending_age_seconds)
        return Resolver()

    monkeypatch.setattr(worker_entrypoint.settings, "workunit_execution_mode", "inline")
    monkeypatch.setattr(worker_entrypoint, "PostgresDispatchProviderBindingResolver", resolver_factory)

    assert worker_entrypoint.claim_and_run_research_dispatch(execution_role="api") is False
    assert captured == [0]


def test_api_executor_enablement_is_mode_scoped(monkeypatch):
    settings = research_dispatch_api_executor.settings
    monkeypatch.setattr(settings, "research_orchestration_v1", True)
    monkeypatch.setattr(settings, "persistence_backend", "postgres")

    monkeypatch.setattr(settings, "workunit_execution_mode", "worker")
    assert research_dispatch_api_executor.api_workunit_executor_enabled() is False

    monkeypatch.setattr(settings, "workunit_execution_mode", "inline")
    assert research_dispatch_api_executor.api_workunit_executor_enabled() is True

    monkeypatch.setattr(settings, "workunit_execution_mode", "worker_with_fallback")
    assert research_dispatch_api_executor.api_workunit_executor_enabled() is True
