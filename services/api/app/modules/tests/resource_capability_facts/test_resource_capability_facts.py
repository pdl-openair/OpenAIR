from __future__ import annotations

from types import SimpleNamespace

from app.modules import resource_capability_facts


def _snapshot() -> dict:
    return {
        "definitions": {
            "substages": [
                {"definitionId": "sdr:source_collection", "definitionVersion": 1, "enabled": True},
                {"definitionId": "sdr:evidence_synthesis", "definitionVersion": 1, "enabled": True},
            ],
            "skills": [{"skillId": "skill_a", "definitionVersion": 2, "enabled": True}],
            "agents": [{"agentId": "agent_a", "definitionVersion": 3, "enabled": True}],
        },
        "bindings": {
            "skills": [{
                "definitionId": "sdr:source_collection",
                "definitionVersion": 1,
                "skillId": "skill_a",
                "skillDefinitionVersion": 2,
                "enabled": True,
            }],
            "agents": [{
                "definitionId": "sdr:evidence_synthesis",
                "definitionVersion": 1,
                "agentId": "agent_a",
                "agentDefinitionVersion": 3,
                "enabled": True,
            }],
        },
    }


def test_catalog_binding_projection_retries_after_transient_registry_failure(monkeypatch) -> None:
    from app.modules import research_orchestration_resolver

    calls = 0
    snapshot = _snapshot()

    def build_snapshot() -> SimpleNamespace:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("registry temporarily unavailable")
        return SimpleNamespace(envelope=snapshot)

    resource_capability_facts.clear_resource_capability_facts_cache()
    monkeypatch.setattr(research_orchestration_resolver, "build_registry_snapshot", build_snapshot)
    try:
        assert resource_capability_facts._bindable_capabilities() == frozenset()
        assert resource_capability_facts._bindable_capabilities() == frozenset(
            {("skill", "skill_a"), ("agent", "agent_a")}
        )
        assert calls == 2
    finally:
        resource_capability_facts.clear_resource_capability_facts_cache()
