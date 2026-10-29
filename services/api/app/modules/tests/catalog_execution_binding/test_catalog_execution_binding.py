from __future__ import annotations

from app.modules.catalog_execution_binding import (
    CatalogExecutionBinding,
    bound_definition_refs,
    candidate_identity,
    capability_is_bindable,
)


def _snapshot() -> dict:
    return {
        "definitions": {
            "substages": [
                {"definitionId": "sdr:source_collection", "definitionVersion": 1, "enabled": True},
                {"definitionId": "sdr:evidence_synthesis", "definitionVersion": 1, "enabled": True},
            ],
            "skills": [
                {"skillId": "skill_a", "definitionVersion": 2, "enabled": True},
                {"skillId": "skill_unbound", "definitionVersion": 1, "enabled": True},
            ],
            "agents": [
                {"agentId": "agent_a", "definitionVersion": 3, "enabled": True},
            ],
        },
        "bindings": {
            "skills": [
                {
                    "definitionId": "sdr:source_collection",
                    "definitionVersion": 1,
                    "skillId": "skill_a",
                    "skillDefinitionVersion": 2,
                    "enabled": True,
                },
            ],
            "agents": [
                {
                    "definitionId": "sdr:evidence_synthesis",
                    "definitionVersion": 1,
                    "agentId": "agent_a",
                    "agentDefinitionVersion": 3,
                    "enabled": True,
                },
            ],
        },
    }


def _binding(kind: str, capability_id: str) -> CatalogExecutionBinding:
    return CatalogExecutionBinding(
        catalog_intent_id="intent_0123456789ab",
        resource_id=f"{kind}:{capability_id}",
        resource_version="release-1",
        capability_id=capability_id,
        capability_kind=kind,
    )


def test_skill_binding_resolves_one_registry_candidate_and_definition() -> None:
    snapshot = _snapshot()
    binding = _binding("skill", "skill_a")

    assert candidate_identity(snapshot, binding) == "skill:skill_a:2"
    assert bound_definition_refs(snapshot, binding) == frozenset({("sdr:source_collection", 1)})
    assert capability_is_bindable(snapshot, "skill:skill_a", "skill_a") is True


def test_agent_binding_resolves_one_registry_candidate_and_definition() -> None:
    snapshot = _snapshot()
    binding = _binding("agent", "agent_a")

    assert candidate_identity(snapshot, binding) == "agent:agent_a:3"
    assert bound_definition_refs(snapshot, binding) == frozenset({("sdr:evidence_synthesis", 1)})
    assert capability_is_bindable(snapshot, "agent:agent_a", "agent_a") is True


def test_unbound_catalog_capability_is_not_bindable() -> None:
    snapshot = _snapshot()

    assert candidate_identity(snapshot, _binding("skill", "skill_unbound")) == "skill:skill_unbound:1"
    assert bound_definition_refs(snapshot, _binding("skill", "skill_unbound")) == frozenset()
    assert capability_is_bindable(snapshot, "skill:skill_unbound", "skill_unbound") is False
