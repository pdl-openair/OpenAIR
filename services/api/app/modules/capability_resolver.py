"""Resolve planner role briefs against production Agent and Skill authority."""

from __future__ import annotations

from copy import deepcopy
import re
from typing import Any, Mapping

from app.execution.durable_skill_runtime import (
    list_registered_durable_skills,
    registered_skill_capability_contract,
)
from app.modules.agent_registry import load_agent_registry
from app.modules.substage_identity import canonical_substage_key


GENERAL_RESEARCH_AGENT_ID = "general_research_agent"
CONTROLLER_ROLE_IDS = {"research_progress_planning"}
_EXTERNAL_MARKERS = (
    "retrieve", "search", "browse", "download", "fetch", "execute", "run code", "filesystem",
    "检索", "搜索", "浏览", "下载", "抓取", "执行", "运行代码", "外部", "联网", "文献召回",
)
_HIGH_RISK_MARKERS = (
    "safety", "security", "clinical", "medical", "legal", "policy", "high risk",
    "安全", "临床", "医疗", "法律", "政策", "高风险", "公平", "偏差",
)
_SOL_SPECIALIST_IDS = {"result_reliability_audit"}


class CapabilityResolutionError(ValueError):
    """The requested work cannot be authorized before confirmation."""


def resolve_creation_plan_capabilities(plan: dict[str, Any], *, skill_pack: str = "sdr") -> dict[str, Any]:
    """Freeze qualification and Binding without consulting runtime availability."""
    resolved_plan = deepcopy(plan)
    selected = plan.get("selectedCapabilities") or []
    if not selected:
        # An empty plan has no Skill route and therefore no Skill authority to
        # resolve.  In particular, do not make it depend on Registry health.
        resolved_plan["selectedCapabilities"] = []
        return resolved_plan

    registry = load_agent_registry()
    skills = list_registered_durable_skills()
    binding_snapshot: dict[str, Any] | None = None
    memory_refs = _refs(plan.get("inputMemoryRefs"))
    resolved_capabilities: list[dict[str, Any]] = []
    controller_roles: list[dict[str, Any]] = []

    for raw in selected:
        if not isinstance(raw, dict):
            continue
        requested = _text(raw.get("requestedCapability") or raw.get("id"))
        role_brief = _text(raw.get("roleBrief") or raw.get("rationale") or requested)
        requested_skills = _refs(raw.get("resolvedSkillIds"), raw.get("skillIds"))
        requested_substages = _refs(raw.get("substages"), raw.get("subStages"))
        requires_external = _requires_external_action(raw, role_brief, requested_skills)

        if requested in CONTROLLER_ROLE_IDS:
            controller_roles.append({
                "requestedCapability": requested,
                "roleBrief": role_brief,
                "reason": "研究推进规划由总控写入四阶段草案，不创建专业智能体工作单元。",
            })
            continue

        specialist = _resolve_specialist(raw, requested=requested, role_brief=role_brief)
        if specialist is not None:
            # An Agent is an owner for an executable Skill set; it must never
            # bypass the durable capability boundary. Preserve an explicit
            # Planner subset, otherwise freeze the Agent's published grants.
            selected_skill_ids = requested_skills or list(specialist.skillIds)
            durable_skill_ids = _require_qualified_skill_ids(
                skills,
                selected_skill_ids,
                requested=requested or specialist.agentId,
                requested_substages=requested_substages,
                filter_to_requested_substage=not requested_skills,
            )
            if binding_snapshot is None:
                binding_snapshot = _load_registry_snapshot()
            definitions, bindings, binding_reason = _freeze_selected_skill_authority(
                skills,
                durable_skill_ids,
                binding_snapshot,
                raw=raw,
                requested=requested or specialist.agentId,
                requested_substages=requested_substages,
            )
            capability = {
                **raw,
                "kind": "agent",
                "id": specialist.agentId,
                "skillIds": durable_skill_ids,
                "requestedCapability": requested,
                "resolutionType": "specialist",
                "resolvedAgentId": specialist.agentId,
                "resolvedSkillIds": durable_skill_ids,
                "resolvedSkillDefinitions": definitions,
                "resolvedSkillBindings": bindings,
                "routeAvailability": "available" if bindings else "unavailable",
                "roleBrief": role_brief,
                "reason": "生产智能体注册表中存在语义匹配的专业智能体。",
                "requiresExternalAction": requires_external,
                "memoryRefs": memory_refs,
            }
            if binding_reason is not None:
                capability["routeUnavailableReason"] = binding_reason
            resolved_capabilities.append(capability)
            continue

        matched_skills = _resolve_skills(
            skills,
            requested=requested,
            requested_skills=requested_skills,
            requested_substages=requested_substages,
        )
        qualified = [
            (row, definition)
            for row in matched_skills
            if (definition := _qualified_skill_definition(row, raw)) is not None
        ]
        if qualified:
            if binding_snapshot is None:
                binding_snapshot = _load_registry_snapshot()
            skill_ids = [str(row["skillId"]) for row, _definition in qualified]
            definitions = [definition for _row, definition in qualified]
            bindings, binding_reason = _freeze_route_bindings(
                definitions,
                binding_snapshot,
                raw=raw,
                requested=requested,
                requested_substages=requested_substages,
            )
            capability = {
                **raw,
                "kind": "skill",
                "id": skill_ids[0],
                "skillIds": skill_ids,
                "requestedCapability": requested,
                "resolutionType": "skill_route",
                "resolvedAgentId": GENERAL_RESEARCH_AGENT_ID,
                "resolvedSkillIds": skill_ids,
                "roleBrief": role_brief,
                "reason": "未命中专业智能体，已解析为生产技能的冻结执行序列。",
                "requiresExternalAction": requires_external,
                "memoryRefs": memory_refs,
                "generalistModelMode": _generalist_mode(role_brief),
                "resolvedSkillDefinitions": definitions,
                "resolvedSkillBindings": bindings,
                "routeAvailability": "available" if bindings else "unavailable",
            }
            if binding_reason is not None:
                capability["routeUnavailableReason"] = binding_reason
            resolved_capabilities.append(capability)
            continue

        if requested_skills or requires_external:
            unavailable = requested or (requested_skills[0] if requested_skills else "external_action")
            raise CapabilityResolutionError(
                f"required_capability_unavailable:{unavailable}"
            )

        generalist = registry.require(GENERAL_RESEARCH_AGENT_ID)
        durable_skill_ids = _require_qualified_skill_ids(
            skills,
            list(generalist.skillIds),
            requested=generalist.agentId,
            requested_substages=requested_substages,
            filter_to_requested_substage=True,
        )
        if binding_snapshot is None:
            binding_snapshot = _load_registry_snapshot()
        definitions, bindings, binding_reason = _freeze_selected_skill_authority(
            skills,
            durable_skill_ids,
            binding_snapshot,
            raw=raw,
            requested=requested or generalist.agentId,
            requested_substages=requested_substages,
        )
        capability = {
            **raw,
            "kind": "agent",
            "id": generalist.agentId,
            "skillIds": durable_skill_ids,
            "requestedCapability": requested,
            "resolutionType": "generalist",
            "resolvedAgentId": generalist.agentId,
            "resolvedSkillIds": durable_skill_ids,
            "resolvedSkillDefinitions": definitions,
            "resolvedSkillBindings": bindings,
            "routeAvailability": "available" if bindings else "unavailable",
            "roleBrief": role_brief,
            "reason": "没有更合适的专业智能体或技能；该工作可由授权上下文内的纯推理完成。",
            "requiresExternalAction": False,
            "memoryRefs": memory_refs,
            "generalistModelMode": _generalist_mode(role_brief),
        }
        if binding_reason is not None:
            capability["routeUnavailableReason"] = binding_reason
        resolved_capabilities.append(capability)

    resolved_plan["selectedCapabilities"] = resolved_capabilities
    if controller_roles:
        resolved_plan["controllerResponsibilities"] = controller_roles
    return resolved_plan


def _require_qualified_skill_ids(
    skills: list[dict[str, Any]],
    requested_skill_ids: list[str],
    *,
    requested: str,
    requested_substages: list[str],
    filter_to_requested_substage: bool = False,
) -> list[str]:
    """Resolve selected Skill ids from formal capability facts only."""
    if not requested_skill_ids:
        raise CapabilityResolutionError(f"required_capability_unavailable:{requested}")

    resolved: list[str] = []
    for skill_id in requested_skill_ids:
        normalized = _normal(skill_id)
        match = next(
            (
                skill
                for skill in skills
                if normalized in {_normal(skill.get("skillId")), _normal(skill.get("claudeName"))}
            ),
            None,
        )
        if match is None or _qualified_skill_definition(match, {}) is None:
            raise CapabilityResolutionError(f"required_capability_unavailable:{requested}")
        if not _matches_requested_substage(match, requested_substages):
            if filter_to_requested_substage:
                continue
            raise CapabilityResolutionError(f"required_capability_unavailable:{requested}")
        canonical = _text(match.get("skillId"))
        if canonical and canonical not in resolved:
            resolved.append(canonical)
    if not resolved:
        raise CapabilityResolutionError(f"required_capability_unavailable:{requested}")
    return resolved


def _qualified_skill_definition(
    skill: Mapping[str, Any],
    requested: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Return the minimum sufficient formal SkillDefinition projection.

    Binding state, Provider secrets, workspace health and live runtime facts
    are deliberately absent from this projection.
    """
    skill_id = _text(skill.get("skillId"))
    version = skill.get("definitionVersion")
    if (
        not skill_id
        or not isinstance(version, int)
        or isinstance(version, bool)
        or version < 1
        or skill.get("enabled") is not True
        or skill.get("status") != "ready"
    ):
        return None
    try:
        input_contract, output_contract, judgment_support = (
            registered_skill_capability_contract(skill.get("sourceContract"))
        )
    except ValueError:
        return None
    source = skill.get("sourceContract")
    roles = source.get("execution_method_roles") if isinstance(source, Mapping) else []
    if roles is None:
        roles = []
    if (
        not isinstance(roles, list)
        or any(not isinstance(value, str) or not value.strip() for value in roles)
    ):
        return None
    normalized_roles = sorted(set(value.strip() for value in roles))

    required_inputs = _refs(
        requested.get("requiredInputRefs"),
        requested.get("acceptedInputRefs"),
    )
    required_artifacts = _refs(
        requested.get("requiredArtifactTypes"),
        requested.get("artifactTypes"),
    )
    required_roles = _refs(
        requested.get("requiredExecutionMethodRoles"),
        requested.get("executionMethodRoles"),
    )
    required_support = requested.get("requiredJudgmentSupport")
    if required_support is None:
        required_support = requested.get("judgmentSupport")
    if not _contains_all(input_contract.get("acceptedInputRefs"), required_inputs):
        return None
    if not _contains_all(output_contract.get("artifactTypes"), required_artifacts):
        return None
    if not _contains_all(normalized_roles, required_roles):
        return None
    if isinstance(required_support, Mapping):
        for key in ("deterministic", "domainProfessional", "humanAuthority"):
            if key in required_support and judgment_support.get(key) is not required_support.get(key):
                return None
    elif required_support is not None:
        return None

    return {
        "skillId": skill_id,
        "definitionVersion": version,
        "status": "ready",
        "enabled": True,
        # Preserve the typed Definition's execution contract in the same
        # frozen object. Qualification above still reads only capability
        # facts; dispatch later compares only execution-critical identities.
        "sourceContract": deepcopy(dict(source)) if isinstance(source, Mapping) else {},
        "inputContract": deepcopy(input_contract),
        "outputContract": {
            **deepcopy(output_contract),
            "artifactTypes": sorted(set(output_contract.get("artifactTypes") or [])),
        },
        "judgmentSupport": deepcopy(judgment_support),
        **({"executionMethodRoles": normalized_roles} if normalized_roles else {}),
    }


def _contains_all(actual: Any, required: list[str]) -> bool:
    if not required:
        return True
    if not isinstance(actual, (list, tuple)):
        return False
    return set(required).issubset({_text(value) for value in actual if _text(value)})


def _load_registry_snapshot() -> dict[str, Any]:
    # Kept lazy so empty and agent-only plans do not acquire a RegistrySnapshot
    # dependency merely because Skill routing exists elsewhere in the product.
    from app.modules.research_orchestration_resolver import build_registry_snapshot

    return dict(build_registry_snapshot().envelope)


def _freeze_selected_skill_authority(
    skills: list[dict[str, Any]],
    skill_ids: list[str],
    snapshot: Mapping[str, Any],
    *,
    raw: Mapping[str, Any],
    requested: str,
    requested_substages: list[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str | None]:
    by_id = {str(skill.get("skillId")): skill for skill in skills}
    definitions: list[dict[str, Any]] = []
    for skill_id in skill_ids:
        skill = by_id.get(skill_id)
        definition = _qualified_skill_definition(skill or {}, raw)
        if definition is None:
            return [], [], "skill_definition_unavailable"
        definitions.append(definition)
    bindings, reason = _freeze_route_bindings(
        definitions,
        snapshot,
        raw=raw,
        requested=requested,
        requested_substages=requested_substages,
    )
    return definitions, bindings, reason


def _freeze_route_bindings(
    definitions: list[dict[str, Any]],
    snapshot: Mapping[str, Any],
    *,
    raw: Mapping[str, Any],
    requested: str,
    requested_substages: list[str],
) -> tuple[list[dict[str, Any]], str | None]:
    """Freeze one common Definition→Skill Binding for an ordered route."""
    snapshot_definitions = snapshot.get("definitions")
    snapshot_bindings = snapshot.get("bindings")
    if not isinstance(snapshot_definitions, Mapping) or not isinstance(snapshot_bindings, Mapping):
        return [], "skill_binding_authority_unavailable"
    substages = [
        row for row in snapshot_definitions.get("substages") or []
        if isinstance(row, Mapping) and row.get("enabled") is True
    ]
    substage_by_ref = {
        (row.get("definitionId"), row.get("definitionVersion")): row
        for row in substages
    }
    requested_refs: set[tuple[Any, Any]] = set()
    explicit_definition_id = _text(raw.get("definitionId"))
    explicit_definition_version = raw.get("definitionVersion")
    if explicit_definition_id:
        requested_refs.add((explicit_definition_id, explicit_definition_version or 1))
    requested_keys = {
        canonical_substage_key(value)
        for value in [requested, *requested_substages]
        if _text(value)
    }
    requested_refs.update(
        ref for ref, row in substage_by_ref.items()
        if canonical_substage_key(row.get("substageKey")) in requested_keys
        or _normal(row.get("definitionId")) in {_normal(value) for value in requested_keys}
    )

    rows_by_skill: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for definition in definitions:
        identity = (definition["skillId"], definition["definitionVersion"])
        rows: list[dict[str, Any]] = []
        for binding in snapshot_bindings.get("skills") or []:
            if not isinstance(binding, Mapping):
                continue
            definition_ref = (binding.get("definitionId"), binding.get("definitionVersion"))
            if (
                binding.get("enabled") is True
                and binding.get("sourceAuthority") == "skill_binding_source"
                and (binding.get("skillId"), binding.get("skillDefinitionVersion")) == identity
                and definition_ref in substage_by_ref
                and binding.get("bindingId")
                == f"bind:{binding.get('definitionId')}:skill:{identity[0]}"
                and isinstance(binding.get("bindingVersion"), int)
                and not isinstance(binding.get("bindingVersion"), bool)
            ):
                if requested_refs and definition_ref not in requested_refs:
                    continue
                rows.append(deepcopy(dict(binding)))
        rows_by_skill[identity] = rows

    common_refs: set[tuple[Any, Any]] | None = None
    for rows in rows_by_skill.values():
        refs = {(row["definitionId"], row["definitionVersion"]) for row in rows}
        common_refs = refs if common_refs is None else common_refs & refs
    if not common_refs:
        return [], "skill_binding_unavailable"
    if len(common_refs) != 1:
        return [], "skill_binding_ambiguous"
    selected_ref = next(iter(common_refs))
    frozen: list[dict[str, Any]] = []
    for definition in definitions:
        identity = (definition["skillId"], definition["definitionVersion"])
        matches = [
            row for row in rows_by_skill[identity]
            if (row["definitionId"], row["definitionVersion"]) == selected_ref
        ]
        if len(matches) != 1:
            return [], "skill_binding_unavailable"
        frozen.append(matches[0])
    return frozen, None


def _resolve_specialist(raw: dict[str, Any], *, requested: str, role_brief: str) -> Any | None:
    registry = load_agent_registry()
    exact = registry.get(requested)
    if exact is not None and exact.agentId != GENERAL_RESEARCH_AGENT_ID:
        return exact
    if _text(raw.get("kind")) == "skill":
        return None
    query_tokens = _tokens(f"{requested} {role_brief}")
    if not query_tokens:
        return None
    scored: list[tuple[int, Any]] = []
    for agent in registry.list_agents():
        if agent.agentId == GENERAL_RESEARCH_AGENT_ID:
            continue
        haystack = " ".join([agent.agentId, agent.displayName, *agent.aliases, *agent.capability])
        score = len(query_tokens & _tokens(haystack))
        if score >= 2:
            scored.append((score, agent))
    if not scored:
        return None
    scored.sort(key=lambda row: (-row[0], row[1].agentId))
    return scored[0][1]


def _resolve_skills(
    skills: list[dict[str, Any]],
    *,
    requested: str,
    requested_skills: list[str],
    requested_substages: list[str],
) -> list[dict[str, Any]]:
    wanted = [_normal(value) for value in [requested, *requested_skills] if _text(value)]
    matched: list[dict[str, Any]] = []
    for wanted_id in wanted:
        for skill in skills:
            aliases = {_normal(skill.get("skillId")), _normal(skill.get("claudeName"))}
            if wanted_id not in aliases:
                continue
            if not _matches_requested_substage(skill, requested_substages):
                continue
            if skill not in matched:
                matched.append(skill)
            break
    return matched


def _matches_requested_substage(skill: dict[str, Any], requested_substages: list[str]) -> bool:
    if not requested_substages:
        return True
    declared = {
        canonical_substage_key(value)
        for value in skill.get("substages") or []
        if _text(value)
    }
    requested = {
        canonical_substage_key(value)
        for value in requested_substages
        if _text(value)
    }
    return bool(declared & requested)


def _requires_external_action(raw: dict[str, Any], role_brief: str, skill_ids: list[str]) -> bool:
    explicit = raw.get("requiresExternalAction")
    if isinstance(explicit, bool):
        return explicit
    text = " ".join([role_brief, *skill_ids]).lower()
    normalized_tokens = _tokens(text)
    for marker in _EXTERNAL_MARKERS:
        marker_tokens = _tokens(marker)
        if marker_tokens and marker_tokens <= normalized_tokens:
            return True
    return False


def model_profile_for_work_unit(agent_id: str, objective: str) -> str:
    """Select the bounded model profile from server-owned risk rules."""
    lowered = objective.lower()
    if agent_id in _SOL_SPECIALIST_IDS or any(marker in lowered for marker in _HIGH_RISK_MARKERS):
        return "sol"
    return "terra"


def _generalist_mode(role_brief: str) -> str:
    return model_profile_for_work_unit(GENERAL_RESEARCH_AGENT_ID, role_brief)


def _tokens(value: str) -> set[str]:
    return {
        token for token in re.findall(r"[a-z0-9\u4e00-\u9fff]+", value.lower().replace("_", " "))
        if len(token) > 2
    }


def _normal(value: Any) -> str:
    return _text(value).replace("-", "_").lower()


def _refs(*values: Any) -> list[str]:
    refs: list[str] = []
    for value in values:
        if isinstance(value, (list, tuple)):
            refs.extend(_text(item) for item in value)
    return list(dict.fromkeys(ref for ref in refs if ref))


def _text(value: Any) -> str:
    return str(value or "").strip()
