"""Specialist Agent Registry loading, validation, and query boundary."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any

import yaml

from app.modules.pack_config import list_available_packs, load_stage_substage_map
from app.modules.repo_paths import repo_root


REGISTRY_PATH = repo_root() / "schemas" / "agents" / "agent-registry.yaml"

REQUIRED_AGENT_FIELDS = (
    "agentId",
    "displayName",
    "visibility",
    "agentKind",
    "candidateScope",
    "enabled",
    "skillIds",
    "capability",
    "notFor",
    "inputContract",
    "outputContract",
    "failClosedRules",
    "budgetPolicy",
    "evaluator",
    "sdr",
    "allowedProviders",
    "defaultProvider",
    "judgmentSupport",
)

VALID_VISIBILITIES = {"internal", "user_entry", "internal_or_user_entry"}
VALID_EXECUTION_MODES = {
    "route_only",
    "draft_only",
    "evidence_bound",
    "retrieval_bound",
    "full_execution",
    "critique_only",
}
UNBOUNDED_CAPABILITY_PHRASES = (
    "agent can do research",
    "can do research",
    "do research",
    "any research",
    "all research",
    "general research",
    "unbounded research",
    "everything",
    "anything",
    "omnipotent",
)
_LOGICAL_PROVIDER_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
# Compatibility constant for legacy projection callers. Registry loading does
# not consult the provider registry; orchestration provider policy is explicit.
DEFAULT_PROVIDER = "openai_primary"
VALID_AGENT_KINDS = {"specialist", "general_research"}
VALID_CANDIDATE_SCOPES = {"explicit_mappings", "all_enabled_substages"}
VALID_SUPPORTED_ROLES = {"primary", "reviewer", "support"}


class AgentRegistryError(ValueError):
    """Raised when a registry is invalid or an unregistered Agent is requested."""


@dataclass(frozen=True)
class AgentRegistryIssue:
    agentId: str
    field: str
    code: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {
            "agentId": self.agentId,
            "field": self.field,
            "code": self.code,
            "message": self.message,
        }


@dataclass(frozen=True)
class AgentRegistryValidationReport:
    issues: tuple[AgentRegistryIssue, ...]

    @property
    def valid(self) -> bool:
        return not self.issues

    def raise_if_invalid(self) -> None:
        if self.valid:
            return
        rendered = "; ".join(
            f"{issue.agentId}.{issue.field}: {issue.code} ({issue.message})"
            for issue in self.issues
        )
        raise AgentRegistryError(f"Invalid Agent Registry: {rendered}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "issues": [issue.to_dict() for issue in self.issues],
        }


@dataclass(frozen=True)
class RegisteredAgent:
    agentId: str
    displayName: str
    visibility: str
    agentKind: str
    candidateScope: str
    enabled: bool
    skillIds: tuple[str, ...]
    aliases: tuple[str, ...]
    capability: tuple[str, ...]
    notFor: tuple[str, ...]
    inputContract: dict[str, Any]
    outputContract: dict[str, Any]
    reviewContract: dict[str, Any]
    failClosedRules: tuple[str, ...]
    budgetPolicy: dict[str, Any]
    evaluator: dict[str, Any]
    sdr: dict[str, Any]
    allowedProviders: tuple[str, ...]
    defaultProvider: str
    judgmentSupport: dict[str, bool]
    executionMethodRoles: tuple[str, ...] = ()
    toolRequirements: dict[str, Any] | None = None

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "RegisteredAgent":
        return cls(
            agentId=str(payload["agentId"]),
            displayName=str(payload["displayName"]),
            visibility=str(payload["visibility"]),
            agentKind=str(payload["agentKind"]),
            candidateScope=str(payload["candidateScope"]),
            enabled=bool(payload["enabled"]),
            skillIds=tuple(str(value) for value in payload["skillIds"]),
            aliases=tuple(str(value) for value in payload.get("aliases") or ()),
            capability=tuple(str(value) for value in payload.get("capability") or ()),
            notFor=tuple(str(value) for value in payload.get("notFor") or ()),
            inputContract=dict(payload.get("inputContract") or {}),
            outputContract=dict(payload.get("outputContract") or {}),
            reviewContract=dict(payload.get("reviewContract") or {}),
            failClosedRules=tuple(str(value) for value in payload.get("failClosedRules") or ()),
            budgetPolicy=dict(payload.get("budgetPolicy") or {}),
            evaluator=dict(payload.get("evaluator") or {}),
            sdr=dict(payload.get("sdr") or {}),
            allowedProviders=tuple(str(value) for value in payload["allowedProviders"]),
            defaultProvider=str(payload["defaultProvider"]),
            judgmentSupport=dict(payload["judgmentSupport"]),
            executionMethodRoles=tuple(
                str(value) for value in payload.get("executionMethodRoles") or ()
            ),
            toolRequirements=dict(payload.get("toolRequirements") or {}),
        )

    @property
    def artifact_kind(self) -> str:
        return str(self.outputContract.get("artifactKind") or "")

    @property
    def artifact_contract(self) -> str:
        return str(self.outputContract.get("artifactContract") or "")

    @property
    def sdr_mappings(self) -> tuple[dict[str, Any], ...]:
        mappings = self.sdr.get("mappings") or ()
        return tuple(dict(row) for row in mappings if isinstance(row, dict))

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "agentId": self.agentId,
            "displayName": self.displayName,
            "visibility": self.visibility,
            "agentKind": self.agentKind,
            "candidateScope": self.candidateScope,
            "enabled": self.enabled,
            "skillIds": list(self.skillIds),
            "aliases": list(self.aliases),
            "capability": list(self.capability),
            "notFor": list(self.notFor),
            "inputContract": dict(self.inputContract),
            "outputContract": dict(self.outputContract),
            "reviewContract": dict(self.reviewContract),
            "failClosedRules": list(self.failClosedRules),
            "budgetPolicy": dict(self.budgetPolicy),
            "evaluator": dict(self.evaluator),
            "sdr": dict(self.sdr),
            "allowedProviders": list(self.allowedProviders),
            "defaultProvider": self.defaultProvider,
            "judgmentSupport": dict(self.judgmentSupport),
            "toolRequirements": dict(self.toolRequirements or {}),
        }
        if self.executionMethodRoles:
            payload["executionMethodRoles"] = list(self.executionMethodRoles)
        return payload



@dataclass(frozen=True)
class AgentRegistry:
    schemaVersion: int
    agents: dict[str, RegisteredAgent]
    aliases: dict[str, str]

    def list_agents(self) -> list[RegisteredAgent]:
        return [self.agents[agent_id] for agent_id in sorted(self.agents)]

    def ids(self) -> set[str]:
        return set(self.agents)

    def callable_ids(self) -> set[str]:
        """Return every production-callable registry key, including aliases."""
        return set(self.agents) | set(self.aliases)

    def is_callable(self, agent_id_or_alias: str) -> bool:
        return self.resolve_id(agent_id_or_alias) is not None

    def resolve_id(self, agent_id_or_alias: str) -> str | None:
        key = _clean(agent_id_or_alias)
        if key in self.agents:
            return key
        return self.aliases.get(key)

    def get(self, agent_id_or_alias: str) -> RegisteredAgent | None:
        resolved = self.resolve_id(agent_id_or_alias)
        if resolved is None:
            return None
        return self.agents[resolved]

    def require(self, agent_id_or_alias: str) -> RegisteredAgent:
        agent = self.get(agent_id_or_alias)
        if agent is None:
            raise AgentRegistryError(f"Agent is not registered for production orchestration: {agent_id_or_alias}")
        return agent

    def by_sdr_substage(self, stage: str, substage: str) -> list[RegisteredAgent]:
        return [
            agent
            for agent in self.list_agents()
            if any(
                mapping.get("stage") == stage and mapping.get("substage") == substage
                for mapping in agent.sdr_mappings
            )
        ]


_registry_cache: tuple[float, AgentRegistry] | None = None


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _normal_skill_id(value: Any) -> str:
    return _clean(value).lower().replace("-", "_")


def _is_non_empty_string_list(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(item, str) and item.strip() for item in value)


def _issue(agent_id: str, field: str, code: str, message: str) -> AgentRegistryIssue:
    return AgentRegistryIssue(agentId=agent_id or "<unknown>", field=field, code=code, message=message)


def _known_stage_substage_pairs() -> set[tuple[str, str, str]]:
    pairs: set[tuple[str, str, str]] = set()
    for pack in (_clean(row.get("key")) for row in list_available_packs()):
        for stage, entries in load_stage_substage_map(pack).items():
            for entry in entries:
                substage = _clean(entry.get("key"))
                if substage:
                    pairs.add((pack, stage, substage))
    return pairs


def _durable_skill_catalog() -> dict[str, dict[str, Any]]:
    """Load the execution boundary's published durable Skill catalog.

    Agent grants are validated against the same catalog that durable work units
    use. The import remains local so registry-only tooling does not initialize
    execution dependencies until a validation is requested.
    """

    from app.execution.durable_skill_runtime import list_registered_durable_skills

    catalog: dict[str, dict[str, Any]] = {}
    for skill in list_registered_durable_skills():
        if not skill.get("durableExecutable"):
            continue
        for value in (skill.get("skillId"), skill.get("claudeName")):
            normalized = _normal_skill_id(value)
            if normalized:
                catalog[normalized] = skill
    return catalog


def validate_agent_registry_payload(payload: dict[str, Any], *, pack: str = "sdr") -> AgentRegistryValidationReport:
    """Validate raw registry payload without requiring callers to instantiate entries."""

    issues: list[AgentRegistryIssue] = []
    if not isinstance(payload, dict):
        return AgentRegistryValidationReport((
            _issue("<registry>", "<root>", "invalid_registry_payload", "Registry payload must be a mapping."),
        ))

    agents = payload.get("agents")
    if not isinstance(agents, list) or not agents:
        return AgentRegistryValidationReport((
            _issue("<registry>", "agents", "missing_agents", "Registry must contain at least one Agent."),
        ))

    stage_substage_pairs = _known_stage_substage_pairs()
    durable_skills = _durable_skill_catalog()
    seen_ids: set[str] = set()
    seen_aliases: dict[str, str] = {}

    for index, raw_agent in enumerate(agents):
        if not isinstance(raw_agent, dict):
            issues.append(_issue(f"<index:{index}>", "<agent>", "invalid_agent_record", "Agent record must be a mapping."))
            continue

        agent_id = _clean(raw_agent.get("agentId")) or f"<index:{index}>"
        for field in REQUIRED_AGENT_FIELDS:
            if field not in raw_agent:
                issues.append(_issue(agent_id, field, f"missing_{_field_code(field)}", f"Missing required field: {field}."))

        if not _clean(raw_agent.get("agentId")):
            issues.append(_issue(agent_id, "agentId", "missing_agent_id", "agentId must be a non-empty string."))
        elif agent_id in seen_ids:
            issues.append(_issue(agent_id, "agentId", "duplicate_agent_id", "agentId must be unique."))
        elif agent_id in seen_aliases:
            issues.append(_issue(agent_id, "agentId", "duplicate_agent_id", f"agentId collides with alias on {seen_aliases[agent_id]}."))
        seen_ids.add(agent_id)

        visibility = _clean(raw_agent.get("visibility"))
        if visibility and visibility not in VALID_VISIBILITIES:
            issues.append(_issue(agent_id, "visibility", "invalid_visibility", f"Unsupported visibility: {visibility}."))

        agent_kind = _clean(raw_agent.get("agentKind"))
        if agent_kind not in VALID_AGENT_KINDS:
            issues.append(_issue(agent_id, "agentKind", "invalid_agent_kind", "agentKind must be explicit."))
        candidate_scope = _clean(raw_agent.get("candidateScope"))
        if candidate_scope not in VALID_CANDIDATE_SCOPES:
            issues.append(_issue(agent_id, "candidateScope", "invalid_candidate_scope", "candidateScope must be explicit."))
        if not isinstance(raw_agent.get("enabled"), bool):
            issues.append(_issue(agent_id, "enabled", "invalid_enabled", "enabled must be a boolean."))
        skill_ids = raw_agent.get("skillIds")
        if not isinstance(skill_ids, list) or not all(isinstance(item, str) and item.strip() for item in skill_ids):
            issues.append(_issue(agent_id, "skillIds", "invalid_skill_grants", "skillIds must be an explicit list."))
        else:
            _validate_skill_closure(agent_id, raw_agent, durable_skills, issues)

        aliases = raw_agent.get("aliases") or []
        if aliases and not _is_non_empty_string_list(aliases):
            issues.append(_issue(agent_id, "aliases", "invalid_aliases", "aliases must be a list of non-empty strings."))
        for alias in aliases if isinstance(aliases, list) else []:
            alias_value = _clean(alias)
            if not alias_value:
                continue
            if alias_value in seen_ids or alias_value in seen_aliases:
                issues.append(_issue(agent_id, "aliases", "duplicate_alias", f"Alias is already registered: {alias_value}."))
            seen_aliases[alias_value] = agent_id

        method_roles = raw_agent.get("executionMethodRoles") or []
        if (
            not isinstance(method_roles, list)
            or any(not isinstance(value, str) or not value.strip() for value in method_roles)
            or len(set(method_roles)) != len(method_roles)
        ):
            issues.append(_issue(
                agent_id,
                "executionMethodRoles",
                "invalid_execution_method_roles",
                "executionMethodRoles must be a unique list of non-empty role refs.",
            ))

        _validate_capability(agent_id, raw_agent.get("capability"), issues)
        _validate_non_empty_list(agent_id, "notFor", raw_agent.get("notFor"), "missing_failure_boundary", issues)
        _validate_input_contract(agent_id, raw_agent.get("inputContract"), issues)
        _validate_output_contract(agent_id, raw_agent.get("outputContract"), issues)
        _validate_review_contract(agent_id, raw_agent.get("reviewContract"), issues)
        _validate_non_empty_list(agent_id, "failClosedRules", raw_agent.get("failClosedRules"), "missing_fail_closed_rules", issues)
        _validate_budget_policy(agent_id, raw_agent.get("budgetPolicy"), issues)
        _validate_evaluator(agent_id, raw_agent.get("evaluator"), issues)
        _validate_sdr(agent_id, raw_agent.get("sdr"), stage_substage_pairs, issues)
        _validate_provider_policy(agent_id, raw_agent, issues)
        judgment_support = raw_agent.get("judgmentSupport")
        if not isinstance(judgment_support, dict) or set(judgment_support) != {"domainProfessional", "humanAuthority"}:
            issues.append(_issue(agent_id, "judgmentSupport", "invalid_judgment_support", "judgmentSupport must explicitly declare domainProfessional and humanAuthority."))
        elif not all(isinstance(judgment_support[field], bool) for field in judgment_support):
            issues.append(_issue(agent_id, "judgmentSupport", "invalid_judgment_support", "judgmentSupport values must be booleans."))
        elif judgment_support["humanAuthority"]:
            issues.append(_issue(agent_id, "judgmentSupport.humanAuthority", "invalid_judgment_support", "An Agent cannot satisfy a human authority gate."))

    return AgentRegistryValidationReport(tuple(issues))


def _validate_skill_closure(
    agent_id: str,
    payload: dict[str, Any],
    durable_skills: dict[str, dict[str, Any]],
    issues: list[AgentRegistryIssue],
) -> None:
    """Require every enabled Agent to have durable Skills for its SDR work."""

    skill_ids = payload.get("skillIds")
    if not isinstance(skill_ids, list):
        return
    if not payload.get("enabled"):
        return
    if not skill_ids:
        issues.append(_issue(
            agent_id,
            "skillIds",
            "missing_skill_closure",
            "Enabled Agents must grant at least one registered durable Skill.",
        ))
        return

    granted: list[dict[str, Any]] = []
    seen_skill_ids: set[str] = set()
    for raw_skill_id in skill_ids:
        normalized = _normal_skill_id(raw_skill_id)
        if normalized in seen_skill_ids:
            issues.append(_issue(
                agent_id,
                "skillIds",
                "duplicate_skill_grant",
                f"Skill is granted more than once: {raw_skill_id}.",
            ))
            continue
        seen_skill_ids.add(normalized)
        skill = durable_skills.get(normalized)
        if skill is None:
            issues.append(_issue(
                agent_id,
                "skillIds",
                "unknown_durable_skill_grant",
                f"Skill is not registered as durable-executable: {raw_skill_id}.",
            ))
            continue
        granted.append(skill)

    mappings = (payload.get("sdr") or {}).get("mappings")
    if not isinstance(mappings, list):
        return
    required_substages = {
        _clean(mapping.get("substage"))
        for mapping in mappings
        if isinstance(mapping, dict) and mapping.get("enabled") is True and _clean(mapping.get("substage"))
    }
    granted_substages = {
        _clean(substage)
        for skill in granted
        for substage in skill.get("substages") or []
        if _clean(substage)
    }
    missing_substages = sorted(required_substages - granted_substages)
    if missing_substages:
        issues.append(_issue(
            agent_id,
            "skillIds",
            "missing_sdr_skill_coverage",
            "Enabled Agent grants do not cover SDR substages: " + ", ".join(missing_substages) + ".",
        ))


def _field_code(field: str) -> str:
    aliases = {
        "inputContract": "input_contract",
        "outputContract": "output_contract",
        "failClosedRules": "fail_closed_rules",
        "budgetPolicy": "budget_policy",
    }
    return aliases.get(field, field)


def _validate_non_empty_list(
    agent_id: str,
    field: str,
    value: Any,
    code: str,
    issues: list[AgentRegistryIssue],
) -> None:
    if not _is_non_empty_string_list(value):
        issues.append(_issue(agent_id, field, code, f"{field} must be a non-empty list of strings."))


def _validate_capability(agent_id: str, value: Any, issues: list[AgentRegistryIssue]) -> None:
    _validate_non_empty_list(agent_id, "capability", value, "missing_capability", issues)
    if not isinstance(value, list):
        return
    for item in value:
        text = _clean(item).lower()
        if any(phrase in text for phrase in UNBOUNDED_CAPABILITY_PHRASES):
            issues.append(_issue(
                agent_id,
                "capability",
                "unbounded_capability",
                "Capability must be bounded; generic research Agent wording is not allowed.",
            ))


def _validate_input_contract(agent_id: str, value: Any, issues: list[AgentRegistryIssue]) -> None:
    if not isinstance(value, dict):
        issues.append(_issue(agent_id, "inputContract", "missing_input_contract", "inputContract must be a mapping."))
        return
    _validate_non_empty_list(agent_id, "inputContract.required", value.get("required"), "missing_input_contract", issues)


def _validate_output_contract(agent_id: str, value: Any, issues: list[AgentRegistryIssue]) -> None:
    if not isinstance(value, dict):
        issues.append(_issue(agent_id, "outputContract", "missing_output_contract", "outputContract must be a mapping."))
        return
    if not _clean(value.get("artifactKind")):
        issues.append(_issue(agent_id, "outputContract.artifactKind", "missing_output_contract", "artifactKind is required."))
    if not _clean(value.get("artifactContract")):
        issues.append(_issue(agent_id, "outputContract.artifactContract", "missing_artifact_contract", "artifactContract is required."))

    required_fields = value.get("requiredFields")
    artifact_schema = value.get("artifactSchema")
    has_required_fields = _is_non_empty_string_list(required_fields)
    schema_ref = _clean(artifact_schema)

    if required_fields is not None and not has_required_fields:
        issues.append(_issue(
            agent_id,
            "outputContract.requiredFields",
            "missing_output_contract",
            "outputContract.requiredFields must be a non-empty list of strings when present.",
        ))

    if artifact_schema is not None:
        if not schema_ref:
            issues.append(_issue(
                agent_id,
                "outputContract.artifactSchema",
                "missing_artifact_schema",
                "artifactSchema must be a non-empty repo-relative path when present.",
            ))
        else:
            schema_path = (repo_root() / schema_ref).resolve()
            try:
                schema_path.relative_to(repo_root().resolve())
            except ValueError:
                issues.append(_issue(
                    agent_id,
                    "outputContract.artifactSchema",
                    "invalid_artifact_schema",
                    "artifactSchema must stay within the repository root.",
                ))
            else:
                if not schema_path.is_file():
                    issues.append(_issue(
                        agent_id,
                        "outputContract.artifactSchema",
                        "missing_artifact_schema",
                        f"artifactSchema file does not exist: {schema_ref}",
                    ))

    if not has_required_fields and not schema_ref:
        issues.append(_issue(
            agent_id,
            "outputContract",
            "missing_output_contract",
            "outputContract must declare requiredFields or a resolvable artifactSchema.",
        ))


def _validate_review_contract(agent_id: str, value: Any, issues: list[AgentRegistryIssue]) -> None:
    if value is None:
        return
    if not isinstance(value, dict):
        issues.append(_issue(agent_id, "reviewContract", "invalid_review_contract", "reviewContract must be a mapping."))
        return
    for field in ("kind", "contractId", "semanticVersion", "schemaRef"):
        if not _clean(value.get(field)):
            issues.append(_issue(agent_id, f"reviewContract.{field}", "invalid_review_contract", f"{field} is required."))
    if not _is_non_empty_string_list(value.get("evaluationCriteria")):
        issues.append(_issue(
            agent_id,
            "reviewContract.evaluationCriteria",
            "invalid_review_contract",
            "evaluationCriteria must be a non-empty list of strings.",
        ))
    schema_ref = _clean(value.get("schemaRef"))
    if schema_ref:
        schema_path = (repo_root() / schema_ref).resolve()
        try:
            schema_path.relative_to(repo_root().resolve())
        except ValueError:
            issues.append(_issue(agent_id, "reviewContract.schemaRef", "invalid_review_contract", "schemaRef must stay within the repository root."))
        else:
            if schema_path.suffix.lower() != ".json" or not schema_path.is_file():
                issues.append(_issue(agent_id, "reviewContract.schemaRef", "invalid_review_contract", "schemaRef must name an existing JSON file."))


def _validate_budget_policy(agent_id: str, value: Any, issues: list[AgentRegistryIssue]) -> None:
    if not isinstance(value, dict):
        issues.append(_issue(agent_id, "budgetPolicy", "missing_budget_policy", "budgetPolicy must be a mapping."))
        return
    default_mode = _clean(value.get("defaultMode"))
    if not default_mode:
        issues.append(_issue(agent_id, "budgetPolicy.defaultMode", "missing_budget_policy", "defaultMode is required."))
    elif default_mode not in VALID_EXECUTION_MODES:
        issues.append(_issue(agent_id, "budgetPolicy.defaultMode", "invalid_execution_mode", f"Unsupported mode: {default_mode}."))

    allowed_modes = value.get("allowedModes")
    if allowed_modes is not None:
        if not _is_non_empty_string_list(allowed_modes):
            issues.append(_issue(agent_id, "budgetPolicy.allowedModes", "invalid_budget_policy", "allowedModes must be non-empty strings."))
        elif default_mode and default_mode not in allowed_modes:
            issues.append(_issue(agent_id, "budgetPolicy.allowedModes", "invalid_budget_policy", "defaultMode must be included in allowedModes."))
        for mode in allowed_modes if isinstance(allowed_modes, list) else []:
            if _clean(mode) not in VALID_EXECUTION_MODES:
                issues.append(_issue(agent_id, "budgetPolicy.allowedModes", "invalid_execution_mode", f"Unsupported mode: {mode}."))

    wall_time = value.get("maxWallTimeSec")
    if not isinstance(wall_time, int) or wall_time < 1:
        issues.append(_issue(
            agent_id,
            "budgetPolicy.maxWallTimeSec",
            "missing_budget_policy",
            "maxWallTimeSec must be a positive integer.",
        ))
    tool_calls = value.get("maxToolCalls")
    if not isinstance(tool_calls, int) or tool_calls < 0:
        issues.append(_issue(
            agent_id,
            "budgetPolicy.maxToolCalls",
            "missing_budget_policy",
            "maxToolCalls must be a non-negative integer.",
        ))


def _validate_provider_policy(agent_id: str, payload: dict[str, Any], issues: list[AgentRegistryIssue]) -> None:
    allowed = payload.get("allowedProviders")
    default = payload.get("defaultProvider")
    requirements = payload.get("toolRequirements")

    if not _is_non_empty_string_list(allowed):
        issues.append(_issue(agent_id, "allowedProviders", "invalid_provider_policy", "allowedProviders must be a non-empty list of strings."))
    if isinstance(allowed, list):
        for provider_id in allowed:
            if not _LOGICAL_PROVIDER_ID_PATTERN.fullmatch(_clean(provider_id)):
                issues.append(_issue(agent_id, "allowedProviders", "invalid_provider_policy", "allowedProviders must contain logical provider IDs."))
    if not _clean(default):
        issues.append(_issue(agent_id, "defaultProvider", "invalid_provider_policy", "defaultProvider must be a non-empty string."))
    if default is not None and not _LOGICAL_PROVIDER_ID_PATTERN.fullmatch(_clean(default)):
        issues.append(_issue(agent_id, "defaultProvider", "invalid_provider_policy", "defaultProvider must be a logical provider ID."))
    if isinstance(allowed, list) and _is_non_empty_string_list(allowed) and _clean(default) and _clean(default) not in allowed:
        issues.append(_issue(agent_id, "defaultProvider", "invalid_provider_policy", "defaultProvider must be included in allowedProviders."))
    if requirements is not None and not isinstance(requirements, dict):
        issues.append(_issue(agent_id, "toolRequirements", "invalid_provider_policy", "toolRequirements must be a mapping."))


def _validate_evaluator(agent_id: str, value: Any, issues: list[AgentRegistryIssue]) -> None:
    if not isinstance(value, dict):
        issues.append(_issue(agent_id, "evaluator", "missing_evaluator", "evaluator must be a mapping."))
        return
    for field in ("id", "artifactValidator"):
        if not _clean(value.get(field)):
            issues.append(_issue(agent_id, f"evaluator.{field}", "missing_evaluator", f"{field} is required."))
    for field in ("requiredMetadata", "qualitySignals"):
        _validate_non_empty_list(agent_id, f"evaluator.{field}", value.get(field), "missing_evaluator", issues)


def _validate_sdr(
    agent_id: str,
    value: Any,
    stage_substage_pairs: set[tuple[str, str, str]],
    issues: list[AgentRegistryIssue],
) -> None:
    if not isinstance(value, dict):
        issues.append(_issue(agent_id, "sdr", "missing_sdr_mapping", "sdr must be a mapping."))
        return
    _validate_non_empty_list(agent_id, "sdr.primaryStages", value.get("primaryStages"), "missing_sdr_mapping", issues)
    mappings = value.get("mappings")
    if not isinstance(mappings, list) or not mappings:
        issues.append(_issue(agent_id, "sdr.mappings", "missing_sdr_mapping", "sdr.mappings must be non-empty."))
        return

    for index, mapping in enumerate(mappings):
        field = f"sdr.mappings[{index}]"
        if not isinstance(mapping, dict):
            issues.append(_issue(agent_id, field, "invalid_sdr_mapping", "SDR mapping must be a mapping."))
            continue
        stage = _clean(mapping.get("stage"))
        substage = _clean(mapping.get("substage"))
        pack = _clean(mapping.get("pack"))
        artifact_contract = _clean(mapping.get("artifactContract"))
        if not pack:
            issues.append(_issue(agent_id, f"{field}.pack", "missing_pack", "pack is required."))
        if not stage or not substage:
            issues.append(_issue(agent_id, field, "missing_sdr_mapping", "stage and substage are required."))
        elif (pack, stage, substage) not in stage_substage_pairs:
            issues.append(_issue(agent_id, field, "unknown_sdr_mapping", f"Unknown stage/substage: {pack}/{stage}/{substage}."))
        role = _clean(mapping.get("supportedRole"))
        if role not in VALID_SUPPORTED_ROLES:
            issues.append(_issue(agent_id, f"{field}.supportedRole", "invalid_supported_role", "supportedRole must be explicit."))
        if not isinstance(mapping.get("enabled"), bool):
            issues.append(_issue(agent_id, f"{field}.enabled", "invalid_enabled", "enabled must be a boolean."))
        if not artifact_contract:
            issues.append(_issue(agent_id, f"{field}.artifactContract", "missing_artifact_contract", "artifactContract is required."))


def load_agent_registry_payload(path: Path = REGISTRY_PATH) -> dict[str, Any]:
    if not path.exists():
        raise AgentRegistryError(f"Agent Registry file does not exist: {path}")
    with open(path, encoding="utf-8") as file:
        payload = yaml.safe_load(file) or {}
    if not isinstance(payload, dict):
        raise AgentRegistryError(f"Agent Registry file must contain a mapping: {path}")
    return payload


def build_agent_registry(payload: dict[str, Any], *, validate: bool = True, pack: str = "sdr") -> AgentRegistry:
    if validate:
        validate_agent_registry_payload(payload, pack=pack).raise_if_invalid()

    agents: dict[str, RegisteredAgent] = {}
    aliases: dict[str, str] = {}
    for raw_agent in payload.get("agents") or []:
        agent = RegisteredAgent.from_payload(raw_agent)
        agents[agent.agentId] = agent
        for alias in agent.aliases:
            aliases[alias] = agent.agentId
    return AgentRegistry(
        schemaVersion=int(payload.get("schemaVersion") or 1),
        agents=agents,
        aliases=aliases,
    )


def load_agent_registry(path: Path = REGISTRY_PATH, *, validate: bool = True, pack: str = "sdr") -> AgentRegistry:
    global _registry_cache
    mtime = path.stat().st_mtime if path.exists() else -1
    if path == REGISTRY_PATH and validate and pack == "sdr" and _registry_cache is not None and _registry_cache[0] == mtime:
        return _registry_cache[1]

    registry = build_agent_registry(load_agent_registry_payload(path), validate=validate, pack=pack)
    if path == REGISTRY_PATH and validate and pack == "sdr":
        _registry_cache = (mtime, registry)
    return registry


def validate_agent_registry(path: Path = REGISTRY_PATH, *, pack: str = "sdr") -> AgentRegistryValidationReport:
    return validate_agent_registry_payload(load_agent_registry_payload(path), pack=pack)


def list_registered_agents() -> list[RegisteredAgent]:
    return load_agent_registry().list_agents()


def registered_agent_callable_ids() -> set[str]:
    return load_agent_registry().callable_ids()


def is_registered_agent_callable(agent_id_or_alias: str) -> bool:
    return load_agent_registry().is_callable(agent_id_or_alias)


def get_registered_agent(agent_id_or_alias: str) -> RegisteredAgent | None:
    return load_agent_registry().get(agent_id_or_alias)


def require_registered_agent(agent_id_or_alias: str) -> RegisteredAgent:
    return load_agent_registry().require(agent_id_or_alias)
