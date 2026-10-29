"""Read-only Resource Plaza projection over published capability facts.

This module never executes Skills, Agents, Providers, or Runs. It only reads
registry publication state already exposed by current-dev public loaders.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.execution.durable_skill_runtime import list_registered_durable_skills
from app.execution.run_single_structured_skill import PYTHON_ADAPTER_SKILLS
from app.modules.agent_registry import AgentRegistryError, load_agent_registry
from app.modules.provider_registry import (
    ProviderRegistryError,
    load_provider_registry,
    resolve_provider_runtime_config,
)


@dataclass(frozen=True)
class ResourceCapabilityFacts:
    capability_id: str
    durable_executable: bool
    launch_mode: str | None
    dependency_gaps: tuple[str, ...]
    unavailable_reason: str | None
    runtime_family: str | None


_UNSET = object()
_SKILL_INDEX: dict[str, dict] | None = None
_PROVIDER_RUNTIME_FAMILY: str | None | object = _UNSET
_PROVIDER_GAP: str | None = None
_BINDABLE_CAPABILITIES: frozenset[tuple[str, str]] | None = None


def clear_resource_capability_facts_cache() -> None:
    """Test helper: drop memoised registry projections."""
    global _SKILL_INDEX, _PROVIDER_RUNTIME_FAMILY, _PROVIDER_GAP, _BINDABLE_CAPABILITIES
    _SKILL_INDEX = None
    _PROVIDER_RUNTIME_FAMILY = _UNSET
    _PROVIDER_GAP = None
    _BINDABLE_CAPABILITIES = None


def _bindable_capabilities() -> frozenset[tuple[str, str]]:
    """Project enabled Skill/Agent execution bindings from Registry authority.

    Any Registry snapshot failure is fail-closed for Catalog tryability: a
    resource must never advertise execution when the runtime resolver cannot
    prove a binding for that exact capability.
    """
    global _BINDABLE_CAPABILITIES
    if _BINDABLE_CAPABILITIES is not None:
        return _BINDABLE_CAPABILITIES
    try:
        from app.modules.research_orchestration_resolver import build_registry_snapshot

        snapshot = build_registry_snapshot().envelope
    except Exception:
        # A transient Registry read failure must fail closed for this request,
        # but must not make every Catalog capability unavailable until restart.
        return frozenset()
    from app.modules.catalog_execution_binding import capability_is_bindable

    definitions = snapshot.get("definitions", {})
    values: set[tuple[str, str]] = set()
    for kind, rows_key, id_key in (
        ("skill", "skills", "skillId"),
        ("agent", "agents", "agentId"),
    ):
        rows = definitions.get(rows_key, []) if isinstance(definitions, dict) else []
        capability_ids = {
            str(row[id_key])
            for row in rows
            if isinstance(row, dict) and row.get("enabled") is True and row.get(id_key)
        }
        values.update(
            (kind, capability_id)
            for capability_id in capability_ids
            if capability_is_bindable(snapshot, f"{kind}:{capability_id}", capability_id)
        )
    _BINDABLE_CAPABILITIES = frozenset(values)
    return _BINDABLE_CAPABILITIES


def _has_execution_binding(kind: str, capability_id: str) -> bool:
    return (kind, capability_id) in _bindable_capabilities()


def resolve_resource_capability_facts(resource_id: str, resource_type: str) -> ResourceCapabilityFacts:
    """Project catalog-facing capability facts without starting execution."""
    if resource_type == "skill":
        return _skill_facts(resource_id.removeprefix("skill:"))
    if resource_type == "agent":
        return _agent_facts(resource_id.removeprefix("agent:"))
    return ResourceCapabilityFacts(
        capability_id=resource_id,
        durable_executable=False,
        launch_mode=None,
        dependency_gaps=(),
        unavailable_reason="workflow_execution_not_published",
        runtime_family="workflow",
    )


def _normal(value: str) -> str:
    return value.strip().casefold().replace("-", "_")


def _skill_index() -> dict[str, dict]:
    global _SKILL_INDEX
    if _SKILL_INDEX is None:
        index: dict[str, dict] = {}
        for skill in list_registered_durable_skills():
            skill_id = str(skill.get("skillId") or "").strip()
            claude_name = str(skill.get("claudeName") or "").strip()
            if skill_id:
                index[_normal(skill_id)] = skill
            if claude_name:
                index[_normal(claude_name)] = skill
        _SKILL_INDEX = index
    return _SKILL_INDEX


def _lookup_skill(skill_id: str) -> dict | None:
    return _skill_index().get(_normal(skill_id))


def _provider_runtime_projection() -> tuple[str | None, str | None]:
    """Return (runtime_family, gap). Memoised; never opens a network connection."""
    global _PROVIDER_RUNTIME_FAMILY, _PROVIDER_GAP
    if _PROVIDER_RUNTIME_FAMILY is not _UNSET:
        return _PROVIDER_RUNTIME_FAMILY, _PROVIDER_GAP  # type: ignore[return-value]
    try:
        registry = load_provider_registry()
        runtime = resolve_provider_runtime_config(registry.defaultProviderId, registry=registry)
    except (ProviderRegistryError, OSError, ValueError) as exc:
        _PROVIDER_RUNTIME_FAMILY = "huiyan_structured"
        _PROVIDER_GAP = str(exc)
    else:
        _PROVIDER_RUNTIME_FAMILY = runtime.adapter
        _PROVIDER_GAP = None
    return _PROVIDER_RUNTIME_FAMILY, _PROVIDER_GAP  # type: ignore[return-value]


def _skill_facts(skill_id: str, *, require_binding: bool = True) -> ResourceCapabilityFacts:
    skill = _lookup_skill(skill_id)
    if skill is None:
        return ResourceCapabilityFacts(
            capability_id=skill_id,
            durable_executable=False,
            launch_mode=None,
            dependency_gaps=(),
            unavailable_reason="unknown_capability",
            runtime_family=None,
        )

    capability_id = str(skill["skillId"])
    if require_binding and not _has_execution_binding("skill", capability_id):
        return ResourceCapabilityFacts(
            capability_id=capability_id,
            durable_executable=False,
            launch_mode=None,
            dependency_gaps=(),
            unavailable_reason="execution_binding_unavailable",
            runtime_family=None,
        )
    if not skill.get("durableExecutable"):
        return ResourceCapabilityFacts(
            capability_id=capability_id,
            durable_executable=False,
            launch_mode=None,
            dependency_gaps=(),
            unavailable_reason="durable_contract_unavailable",
            runtime_family=None,
        )

    if _normal(capability_id) in {_normal(item) for item in PYTHON_ADAPTER_SKILLS}:
        return ResourceCapabilityFacts(
            capability_id=capability_id,
            durable_executable=True,
            launch_mode="durable_skill",
            dependency_gaps=(),
            unavailable_reason=None,
            runtime_family="python_adapter",
        )

    if str(skill.get("status") or "").strip().lower() != "ready":
        return ResourceCapabilityFacts(
            capability_id=capability_id,
            durable_executable=True,
            launch_mode="durable_skill",
            dependency_gaps=("Capability package is not published as ready.",),
            unavailable_reason=None,
            runtime_family="huiyan_structured",
        )

    runtime_family, gap = _provider_runtime_projection()
    if gap:
        return ResourceCapabilityFacts(
            capability_id=capability_id,
            durable_executable=True,
            launch_mode="durable_skill",
            dependency_gaps=(gap,),
            unavailable_reason=None,
            runtime_family=runtime_family or "huiyan_structured",
        )

    return ResourceCapabilityFacts(
        capability_id=capability_id,
        durable_executable=True,
        launch_mode="durable_skill",
        dependency_gaps=(),
        unavailable_reason=None,
        runtime_family=runtime_family,
    )


def _agent_facts(agent_id: str) -> ResourceCapabilityFacts:
    try:
        agent = load_agent_registry().get(agent_id)
    except AgentRegistryError:
        return ResourceCapabilityFacts(agent_id, False, None, (), "agent_contract_invalid", "agent")
    if agent is None:
        return ResourceCapabilityFacts(agent_id, False, None, (), "unknown_capability", "agent")
    if not agent.enabled:
        return ResourceCapabilityFacts(agent.agentId, False, None, (), "agent_not_enabled", "agent")
    if not _has_execution_binding("agent", agent.agentId):
        return ResourceCapabilityFacts(
            agent.agentId,
            False,
            None,
            (),
            "execution_binding_unavailable",
            "agent",
        )

    grants = [_skill_facts(skill_id, require_binding=False) for skill_id in agent.skillIds]
    unavailable = next((grant for grant in grants if not grant.durable_executable), None)
    if unavailable is not None:
        return ResourceCapabilityFacts(
            agent.agentId,
            False,
            None,
            (),
            unavailable.unavailable_reason,
            "agent",
        )
    gaps = tuple(gap for grant in grants for gap in grant.dependency_gaps)
    return ResourceCapabilityFacts(
        capability_id=agent.agentId,
        durable_executable=True,
        launch_mode="durable_agent",
        dependency_gaps=gaps,
        unavailable_reason=None,
        runtime_family="agent",
    )
