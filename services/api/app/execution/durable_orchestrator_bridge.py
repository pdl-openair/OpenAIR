"""Pure planning bridge between a durable run and the OpenAIR controller.

This module intentionally produces no provider request and starts no Skill.
It turns the API-frozen run snapshot into a bounded routing plan for B1.2.
"""

from __future__ import annotations

from copy import deepcopy
import re
from typing import Any

from app.modules.agent_registry import DEFAULT_PROVIDER, RegisteredAgent, load_agent_registry
from app.modules.capability_resolver import model_profile_for_work_unit
from app.execution.durable_orchestrator_runtime import FrozenExecutionRequestError, load_frozen_artifact_contract


class FrozenPlanError(ValueError):
    """A confirmed plan cannot be compiled into authorized specialist work."""


def precompile_creation_plan(*, creation_plan: dict[str, Any], goal_summary: str) -> list[dict[str, Any]]:
    """Use the durable compiler before confirmation without creating a run."""
    units = compile_durable_work_units({
        "input_snapshot": {
            "goalSummary": goal_summary,
            "creationPlan": creation_plan,
        },
    })
    if not units:
        raise FrozenPlanError("no_registered_specialist")
    return units


def build_durable_orchestration_plan(run: dict[str, Any]) -> dict[str, Any]:
    """Create a deterministic, non-secret routing plan from one frozen run."""
    snapshot = dict(run.get("input_snapshot") or {})
    target_substage = _text(run.get("target_substage"))
    registry = load_agent_registry()
    try:
        work_units = compile_durable_work_units(run)
        plan_block: str | None = None
    except FrozenPlanError as exc:
        # Keep the rejected frozen plan durable so the execution endpoint can
        # close it as blocked before a provider request is prepared.
        work_units = []
        plan_block = str(exc)
    # This compatibility metadata is derived from the frozen executable plan,
    # never from a fresh planning selection.  Multi-Agent dispatch uses each
    # work unit's own registered Agent policy below.
    first_unit = work_units[0] if work_units else None
    selected_agent_id = first_unit["agentId"] if first_unit else None
    agent = registry.get(selected_agent_id) if selected_agent_id else None
    selected_skill_ids = list(first_unit["selectedSkillIds"]) if first_unit else []
    provider_policy = _provider_policy(agent)

    plan = {
        "context": {
            "runId": _text(run.get("run_id")),
            "taskId": _text(run.get("task_id")),
            "ownerId": _text(run.get("owner_id")),
            "workspaceId": _text(run.get("workspace_id")),
            "runNumber": int(run.get("run_number") or 0),
            "leaseEpoch": int(run.get("lease_epoch") or 0),
            "sourceMode": _text(run.get("source_mode")),
            "currentStage": _text(snapshot.get("currentStage")),
            "targetSubstage": target_substage or None,
        },
        "selectedAgentId": selected_agent_id,
        "selectedSkillIds": selected_skill_ids,
        "modelProfile": first_unit["modelMode"] if first_unit else None,
        "selectedProvider": provider_policy["defaultProvider"],
        "providerPolicy": provider_policy,
        # These are planned durable facts, not execution events.  The worker
        # later persists them before it can dispatch any specialist work.
        "workUnits": work_units,
        "providerPolicyByAgent": {
            unit["agentId"]: _provider_policy(registry.require(unit["agentId"]))
            for unit in work_units
        },
    }
    if first_unit:
        for field in (
            "artifactContract", "artifactContractSchemaVersion", "artifactRequiredFields",
            "artifactSemanticVersion", "artifactSchemaDigest", "artifactSchema", "artifactSchemaRef",
        ):
            plan[field] = first_unit[field]
    if plan_block:
        plan["planBlock"] = plan_block
    return plan


def compile_durable_work_units(run: dict[str, Any]) -> list[dict[str, Any]]:
    """Compile frozen, authorized specialist work without inventing roles.

    The returned list is the entire executable professional-Agent sequence.
    Controller planning and final synthesis remain controller responsibilities;
    they are not surrogate specialist work units or provider calls.
    """
    snapshot = dict(run.get("input_snapshot") or {})
    plan = snapshot.get("creationPlan")
    plan = dict(plan) if isinstance(plan, dict) else {}
    decision = plan.get("orchestratorDecision")
    decision = dict(decision) if isinstance(decision, dict) else {}
    registry = load_agent_registry()
    goal = _first_text(
        decision.get("interpretedGoal"), decision.get("userGoal"), plan.get("goalSummary"), snapshot.get("goalSummary"),
    ) or "研究任务"
    global_memory_refs = _refs(snapshot.get("inputMemoryRefs"), plan.get("inputMemoryRefs"))
    global_artifact_refs = _refs(snapshot.get("inputArtifactRefs"), plan.get("inputArtifactRefs"))
    capabilities = _selected_agent_capabilities(plan, registry)
    skill_routes = _selected_skill_routes(plan, registry)
    decision_agent_ids = [] if skill_routes else _decision_agent_ids(decision, registry)
    primary_agent_id = decision_agent_ids[0] if decision_agent_ids else None

    units: list[dict[str, Any]] = []
    seen_unit_keys: set[str] = set()
    planned_agent_ids: set[str] = set()
    previous_unit_key: str | None = None
    capability_positions: set[int] = set()

    for stage_index, stage in enumerate(_plan_stages(plan), start=1):
        stage_key = _text(stage.get("stageKey"))
        for substage_index, substage in enumerate(_stage_substages(stage), start=1):
            substage_key = _text(substage.get("substage"))
            if not substage_key:
                continue
            agent_id, matched_capabilities = _agent_for_substage(
                substage_key=substage_key,
                capabilities=capabilities,
                primary_agent_id=primary_agent_id,
            )
            if not agent_id:
                if skill_routes:
                    continue
                raise FrozenPlanError(f"unassigned_planned_substage:{substage_key}")
            capability_positions.update(matched_capabilities)
            unit_key = _unit_key("stage", stage_index, stage_key, "substage", substage_index, substage_key, "agent", agent_id)
            if unit_key in seen_unit_keys:
                continue
            selected_skill_ids = _skills_for_substage(
                substage_key, agent_id, substage, capabilities, matched_capabilities
            )
            selected_definitions, selected_bindings = _authority_for_skills(
                selected_skill_ids, capabilities, matched_capabilities
            )
            units.append({
                "unitKey": unit_key,
                "parentUnitKey": previous_unit_key,
                "stageKey": stage_key or None,
                "substageKey": substage_key,
                "agentId": agent_id,
                "kind": "specialist",
                "state": "planned",
                "objective": _first_text(substage.get("rationale"), stage.get("description"), goal) or goal,
                "modelMode": model_profile_for_work_unit(
                    agent_id, _first_text(substage.get("rationale"), stage.get("description"), goal) or goal,
                ),
                "inputMemoryRefs": _refs(global_memory_refs, stage.get("inputMemoryRefs"), substage.get("inputMemoryRefs")),
                "inputArtifactRefs": _refs(global_artifact_refs, stage.get("inputArtifactRefs"), substage.get("inputArtifactRefs")),
                "selectedSkillIds": selected_skill_ids,
                "selectedSkillDefinitions": selected_definitions,
                "selectedSkillBindings": selected_bindings,
            })
            seen_unit_keys.add(unit_key)
            planned_agent_ids.add(agent_id)
            previous_unit_key = unit_key

    # Capability-only legacy plans have no stage shape.  Keep every registered
    # selected Agent as a planned unit rather than silently collapsing back to
    # a single-agent run.
    for capability_index, capability in enumerate(capabilities, start=1):
        if capability_index - 1 in capability_positions:
            continue
        agent_id = capability["agentId"]
        unit_key = _unit_key("capability", capability_index, agent_id)
        if unit_key in seen_unit_keys:
            continue
        units.append({
            "unitKey": unit_key,
            "parentUnitKey": previous_unit_key,
            "stageKey": None,
            "substageKey": None,
            "agentId": agent_id,
            "kind": "specialist",
            "state": "planned",
            "objective": _first_text(capability.get("rationale"), goal) or goal,
            "modelMode": model_profile_for_work_unit(
                agent_id, _first_text(capability.get("rationale"), goal) or goal,
            ),
            "inputMemoryRefs": global_memory_refs,
            "inputArtifactRefs": global_artifact_refs,
            "selectedSkillIds": list(capability["skillIds"]),
            "selectedSkillDefinitions": deepcopy(capability["skillDefinitions"]),
            "selectedSkillBindings": deepcopy(capability["skillBindings"]),
        })
        seen_unit_keys.add(unit_key)
        planned_agent_ids.add(agent_id)
        previous_unit_key = unit_key

    for route_index, route in enumerate(skill_routes, start=1):
        unit_key = _unit_key("skill-route", route_index, *route["skillIds"])
        if unit_key in seen_unit_keys:
            continue
        units.append({
            "unitKey": unit_key,
            "parentUnitKey": previous_unit_key,
            "stageKey": None,
            "substageKey": None,
            "agentId": route["reviewAgentId"],
            "kind": "skill_route",
            "state": "planned",
            "objective": route["roleBrief"] or goal,
            "inputMemoryRefs": _refs(global_memory_refs, route["memoryRefs"]),
            "inputArtifactRefs": global_artifact_refs,
            "selectedSkillIds": route["skillIds"],
            "selectedSkillDefinitions": route["skillDefinitions"],
            "selectedSkillBindings": route["skillBindings"],
            "resolutionType": "skill_route",
            "requestedCapability": route["requestedCapability"],
            "modelMode": model_profile_for_work_unit(route["reviewAgentId"], route["roleBrief"] or goal),
        })
        seen_unit_keys.add(unit_key)
        previous_unit_key = unit_key

    # A confirmed decision may explicitly authorize a specialist without a
    # detailed substage. Preserve that work, but never turn controller work
    # into a specialist placeholder.
    for decision_index, agent_id in enumerate(decision_agent_ids, start=1):
        if agent_id in planned_agent_ids:
            continue
        unit_key = _unit_key("decision", decision_index, "agent", agent_id)
        if unit_key in seen_unit_keys:
            continue
        units.append({
            "unitKey": unit_key,
            "parentUnitKey": previous_unit_key,
            "stageKey": None,
            "substageKey": None,
            "agentId": agent_id,
            "kind": "specialist",
            "state": "planned",
            "objective": goal,
            "modelMode": model_profile_for_work_unit(agent_id, goal),
            "inputMemoryRefs": global_memory_refs,
            "inputArtifactRefs": global_artifact_refs,
            "selectedSkillIds": [],
        })
        seen_unit_keys.add(unit_key)
        planned_agent_ids.add(agent_id)
        previous_unit_key = unit_key

    # Persist the registry-owned output contract with each executable unit.
    # The provider path must never re-resolve this mutable registry fact after
    # plan creation.
    for unit in units:
        agent = registry.require(unit["agentId"])
        contract = _artifact_contract_snapshot(agent, registry.schemaVersion)
        unit.update(contract)
        unit["mandatoryReviewContract"] = _mandatory_review_contract_snapshot(registry)
    return units


def _provider_policy(agent: RegisteredAgent | None) -> dict[str, Any]:
    if agent is None:
        return {
            "allowedProviders": [DEFAULT_PROVIDER],
            "defaultProvider": DEFAULT_PROVIDER,
            "toolRequirements": {},
            # Existing Skill ``runtime`` declarations are execution-adapter
            # preferences, not provider constraints. B1.2 may add explicit
            # Skill provider requirements that only narrow this set.
            "skillProviderConstraints": [],
        }
    return {
        "allowedProviders": list(agent.allowedProviders),
        "defaultProvider": agent.defaultProvider,
        "toolRequirements": dict(agent.toolRequirements or {}),
        "skillProviderConstraints": [],
    }


def _artifact_contract_snapshot(agent: RegisteredAgent, schema_version: int) -> dict[str, Any]:
    contract = _text(agent.outputContract.get("artifactContract"))
    schema_ref = _text(agent.outputContract.get("artifactSchema"))
    if not contract or not schema_ref or type(schema_version) is not int or schema_version < 1:
        raise FrozenPlanError(f"invalid_registered_artifact_contract:{agent.agentId}")
    try:
        frozen = load_frozen_artifact_contract(contract, schema_ref)
    except FrozenExecutionRequestError as exc:
        raise FrozenPlanError(f"invalid_registered_artifact_contract:{agent.agentId}") from exc
    fields = _schema_required_fields(frozen.schema)
    return {
        "artifactContract": contract,
        "artifactContractSchemaVersion": schema_version,
        "artifactRequiredFields": fields,
        **frozen.snapshot(),
    }


def _mandatory_review_contract_snapshot(registry: Any) -> dict[str, Any]:
    reviewers = [agent for agent in registry.list_agents() if isinstance(agent.reviewContract, dict) and agent.reviewContract]
    if len(reviewers) != 1:
        raise FrozenPlanError("mandatory_review_contract_unavailable")
    reviewer = reviewers[0]
    review = reviewer.reviewContract
    contract = _text(review.get("contractId"))
    schema_ref = _text(review.get("schemaRef"))
    try:
        frozen = load_frozen_artifact_contract(contract, schema_ref)
    except FrozenExecutionRequestError as exc:
        raise FrozenPlanError("mandatory_review_contract_unavailable") from exc
    if _text(review.get("semanticVersion")) != frozen.semantic_version:
        raise FrozenPlanError("mandatory_review_contract_unavailable")
    evaluation_criteria = _refs(review.get("evaluationCriteria"))
    if not evaluation_criteria:
        raise FrozenPlanError("mandatory_review_contract_unavailable")
    return {
        "reviewerAgentId": reviewer.agentId,
        "reviewKind": _text(review.get("kind")),
        "evaluationCriteria": evaluation_criteria,
        "artifactContractSchemaVersion": registry.schemaVersion,
        "artifactRequiredFields": _schema_required_fields(frozen.schema),
        **frozen.snapshot(),
    }


def _schema_required_fields(schema: Any) -> list[str]:
    fields = schema.get("required") if isinstance(schema, dict) else schema.get("required") if hasattr(schema, "get") else None
    if not isinstance(fields, tuple) and not isinstance(fields, list):
        raise FrozenPlanError("artifact_schema_required_fields_unavailable")
    normalized = [_text(field) for field in fields]
    if not normalized or len(normalized) != len(set(normalized)):
        raise FrozenPlanError("artifact_schema_required_fields_unavailable")
    return normalized


def _text(value: Any) -> str:
    return str(value or "").strip()


def _first_text(*values: Any) -> str:
    return next((text for value in values if (text := _text(value))), "")


def _refs(*values: Any) -> list[str]:
    refs: list[str] = []
    for value in values:
        if not isinstance(value, (list, tuple)):
            continue
        refs.extend(_text(item) for item in value)
    return list(dict.fromkeys(ref for ref in refs if ref))


def _registered_agent_id(registry: Any, value: Any, *, field: str) -> str | None:
    agent_id = _text(value)
    if not agent_id:
        return None
    agent = registry.get(agent_id)
    if agent:
        return agent.agentId
    raise FrozenPlanError(f"unregistered_agent:{field}:{agent_id}")


def _selected_agent_capabilities(plan: dict[str, Any], registry: Any) -> list[dict[str, Any]]:
    capabilities: list[dict[str, Any]] = []
    seen: set[tuple[str, tuple[str, ...], tuple[str, ...]]] = set()
    for raw in plan.get("selectedCapabilities") or []:
        if not isinstance(raw, dict) or _text(raw.get("kind")) != "agent":
            continue
        agent_id = _registered_agent_id(registry, raw.get("id"), field="selectedCapabilities")
        skill_ids = (
            _refs(raw.get("resolvedSkillIds"), raw.get("skillIds"))
            if raw.get("routeAvailability", "available") == "available"
            else []
        )
        # Planner capabilities already carry their authorized SDR skill/substage
        # scope. Older drafts omitted the duplicate ``substages`` field.
        substages = _refs(raw.get("substages")) or list(skill_ids)
        key = (agent_id, tuple(skill_ids), tuple(substages))
        if key in seen:
            continue
        seen.add(key)
        capabilities.append({
            "agentId": agent_id,
            "skillIds": skill_ids,
            "skillDefinitions": deepcopy(raw.get("resolvedSkillDefinitions") or []),
            "skillBindings": deepcopy(raw.get("resolvedSkillBindings") or []),
            "substages": substages,
            "rationale": _text(raw.get("rationale")) or None,
        })
    return capabilities


def _selected_skill_routes(plan: dict[str, Any], registry: Any) -> list[dict[str, Any]]:
    routes: list[dict[str, Any]] = []
    unavailable: list[str] = []
    for raw in plan.get("selectedCapabilities") or []:
        if not isinstance(raw, dict) or _text(raw.get("resolutionType")) != "skill_route":
            continue
        requested = _text(raw.get("requestedCapability") or raw.get("id")) or "skill_route"
        if raw.get("routeAvailability") != "available":
            unavailable.append(
                f"{_text(raw.get('routeUnavailableReason')) or 'skill_binding_unavailable'}:{requested}"
            )
            continue
        review_agent = _registered_agent_id(
            registry,
            raw.get("resolvedAgentId") or "general_research_agent",
            field="resolvedAgentId",
        )
        definitions = raw.get("resolvedSkillDefinitions")
        bindings = raw.get("resolvedSkillBindings")
        requested_skill_ids = _refs(raw.get("resolvedSkillIds"), raw.get("skillIds"))
        if not isinstance(definitions, list) or not isinstance(bindings, list):
            unavailable.append(f"skill_binding_unavailable:{requested}")
            continue
        definition_by_identity = {
            (item.get("skillId"), item.get("definitionVersion")): item
            for item in definitions if isinstance(item, dict)
        }
        binding_by_identity = {
            (item.get("skillId"), item.get("skillDefinitionVersion")): item
            for item in bindings if isinstance(item, dict)
        }
        skill_ids: list[str] = []
        selected_definitions: list[dict[str, Any]] = []
        selected_bindings: list[dict[str, Any]] = []
        route_valid = bool(requested_skill_ids) and (
            len(definition_by_identity) == len(definitions)
            and len(binding_by_identity) == len(bindings)
        )
        selected_definition_ref: tuple[Any, Any] | None = None
        for skill_id in requested_skill_ids:
            candidates = [identity for identity in definition_by_identity if identity[0] == skill_id]
            if len(candidates) != 1:
                route_valid = False
                break
            identity = candidates[0]
            definition = definition_by_identity[identity]
            binding = binding_by_identity.get(identity)
            binding_ref = (
                binding.get("definitionId"), binding.get("definitionVersion")
            ) if isinstance(binding, dict) else None
            if (
                definition.get("enabled") is not True
                or definition.get("status") != "ready"
                or not isinstance(binding, dict)
                or binding.get("enabled") is not True
                or binding.get("sourceAuthority") != "skill_binding_source"
                or not _text(binding.get("definitionId"))
                or not isinstance(binding.get("definitionVersion"), int)
                or isinstance(binding.get("definitionVersion"), bool)
                or binding.get("definitionVersion") < 1
                or binding.get("bindingId")
                != f"bind:{binding.get('definitionId')}:skill:{skill_id}"
                or not isinstance(binding.get("bindingVersion"), int)
                or isinstance(binding.get("bindingVersion"), bool)
                or (selected_definition_ref is not None and binding_ref != selected_definition_ref)
            ):
                route_valid = False
                break
            selected_definition_ref = binding_ref
            skill_ids.append(skill_id)
            selected_definitions.append(dict(definition))
            selected_bindings.append(dict(binding))
        if not route_valid or len(skill_ids) != len(requested_skill_ids):
            unavailable.append(f"skill_binding_unavailable:{requested}")
            continue
        routes.append({
            "requestedCapability": requested,
            "reviewAgentId": review_agent,
            "skillIds": skill_ids,
            "skillDefinitions": selected_definitions,
            "skillBindings": selected_bindings,
            "roleBrief": _text(raw.get("roleBrief") or raw.get("rationale")),
            "memoryRefs": _refs(raw.get("memoryRefs")),
            "modelMode": _text(raw.get("generalistModelMode")) or "terra",
        })
    if not routes and unavailable:
        raise FrozenPlanError(f"required_skill_route_unavailable:{unavailable[0]}")
    return routes


def _decision_agent_ids(decision: dict[str, Any], registry: Any) -> list[str]:
    selected: list[str] = []
    supporting = decision.get("supportingAgents") or []
    if not isinstance(supporting, list):
        raise FrozenPlanError("invalid_supporting_agents")
    for index, value in enumerate([decision.get("primaryAgent"), *supporting]):
        agent_id = _registered_agent_id(
            registry,
            value,
            field="primaryAgent" if index == 0 else "supportingAgents",
        )
        if agent_id and agent_id not in selected:
            selected.append(agent_id)
    return selected


def _plan_stages(plan: dict[str, Any]) -> list[dict[str, Any]]:
    return [dict(stage) for stage in plan.get("stages") or [] if isinstance(stage, dict)]


def _stage_substages(stage: dict[str, Any]) -> list[dict[str, Any]]:
    rows = stage.get("substages")
    if isinstance(rows, list):
        return [dict(row) for row in rows if isinstance(row, dict)]
    return [{"substage": _text(value)} for value in stage.get("subStages") or [] if _text(value)]


def _agent_for_substage(*, substage_key: str, capabilities: list[dict[str, Any]], primary_agent_id: str | None) -> tuple[str | None, list[int]]:
    matching = [
        index for index, capability in enumerate(capabilities)
        if capability["substages"] and substage_key in capability["substages"]
    ]
    if matching:
        return capabilities[matching[0]]["agentId"], matching
    if primary_agent_id:
        return primary_agent_id, []
    unscoped = [index for index, capability in enumerate(capabilities) if not capability["substages"]]
    if unscoped:
        return capabilities[unscoped[0]]["agentId"], unscoped
    return None, []


def _skills_for_substage(substage_key: str, agent_id: str, substage: dict[str, Any], capabilities: list[dict[str, Any]], matched_capabilities: list[int]) -> list[str]:
    selected = _refs(substage.get("skills"))
    candidate_indexes = matched_capabilities or [
        index for index, capability in enumerate(capabilities)
        if capability["agentId"] == agent_id and (not capability["substages"] or substage_key in capability["substages"])
    ]
    frozen_ids = {
        _text(row.get("skillId"))
        for index in candidate_indexes
        for row in capabilities[index]["skillDefinitions"]
        if isinstance(row, dict) and _text(row.get("skillId"))
    }
    if frozen_ids:
        # Planner display labels cannot add a route outside the frozen typed
        # Definitions. Legacy unresolved plans retain their prior projection.
        selected = [skill_id for skill_id in selected if skill_id in frozen_ids]
    for index in candidate_indexes:
        selected.extend(capabilities[index]["skillIds"])
    return list(dict.fromkeys(skill_id for skill_id in selected if skill_id))


def _authority_for_skills(
    skill_ids: list[str],
    capabilities: list[dict[str, Any]],
    matched_capabilities: list[int],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not skill_ids:
        return [], []
    indexes = matched_capabilities or list(range(len(capabilities)))
    definitions = [
        deepcopy(row)
        for index in indexes
        for row in capabilities[index]["skillDefinitions"]
        if isinstance(row, dict) and row.get("skillId") in skill_ids
    ]
    bindings = [
        deepcopy(row)
        for index in indexes
        for row in capabilities[index]["skillBindings"]
        if isinstance(row, dict) and row.get("skillId") in skill_ids
    ]
    return definitions, bindings


def _unit_key(*parts: Any) -> str:
    normalized = [re.sub(r"[^a-z0-9_-]+", "-", _text(part).lower()).strip("-") or "x" for part in parts]
    return ":".join(normalized)
