"""User catalog projection from registries gated by CatalogRelease facts."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import jsonschema
import yaml

from app.models.resource_catalog import (
    ResourceAvailability,
    ResourceCatalogSort,
    ResourceDomain,
    ResourceScenario,
    ResourceType,
)
from app.modules.agent_registry import AgentRegistryError, load_agent_registry
from app.modules.multi_agent_recipe_registry import (
    MultiAgentRecipeRegistryError,
    load_multi_agent_recipe_registry,
)
from app.modules.repo_paths import repo_root
from app.modules.contribution_repository import get_contribution_repository
from app.modules.resource_capability_facts import ResourceCapabilityFacts, resolve_resource_capability_facts


SKILLS_REGISTRY_PATH = repo_root() / "skills" / "registry.yaml"
CATALOG_RELEASES_PATH = repo_root() / "schemas" / "resources" / "catalog-releases.yaml"
CATALOG_RELEASE_SCHEMA_PATH = repo_root() / "schemas" / "resources" / "catalog-release.schema.json"

# This Registry entry creates fixtures and recorded runs for development and
# replay workflows. It is not a user-facing research capability.
NON_PRODUCT_SKILL_IDS = frozenset({"fixture_recorded_run_creation"})

logger = logging.getLogger(__name__)

# In-process projection cache for GET /api/resources (mtime + explicit invalidate).
_catalog_projection_cache: list | None = None
_catalog_projection_fingerprint: tuple[float, ...] | None = None

_PUBLIC_AGENT_CONTENT: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "general_research_agent": ("通用研究智能体", "帮助你梳理研究目标、整合已有资料，并形成下一步研究思路。适合尚未确定具体研究工具时使用。", ("基于你提供的研究材料进行分析，不能替代文献检索、实验或专业判断。",)),
    "research_question_refinement": ("研究问题聚焦", "把宽泛的研究想法聚焦为可研究、可验证的问题，明确研究对象、变量和范围。", ("用于形成研究问题和假设，不直接给出文献结论或实验结果。",)),
    "key_literature_positioning": ("关键文献定位", "围绕研究问题梳理检索主题和关键词，建立优先阅读的文献方向与清单。", ("用于确定检索方向和阅读重点，文献覆盖范围需在实际检索后确认。",)),
    "mechanism_dispute_mapping": ("机制分析与争议梳理", "基于已有文献、表征结果或实验记录，梳理材料性质变化的可能机制、证据和分歧。", ("需要提供文献、表征或实验依据，不能把推测当作已验证结论。",)),
    "paper_evidence_extraction": ("论文证据整理", "从论文片段、图表和实验记录中整理结论与证据的对应关系，便于比较、复核和引用。", ("需要提供可阅读的论文或实验材料，不直接生成最终科研结论。",)),
    "result_reliability_audit": ("研究结果可信度检查", "核对数据、表征结果与研究结论之间的对应关系，找出证据缺口和需要复核的环节。", ("需要提供相关数据或记录，不能替代重复实验或补造缺失数据。",)),
    "validation_design": ("验证方案设计", "把研究假设转成可执行的验证方案，明确变量、对照、表征方法和判断标准。", ("提供研究思路和约束后形成方案，不直接执行实验或产出实验结果。",)),
    "research_gap_identification": ("研究空白梳理", "依据已有文献和证据，梳理尚待回答的问题，以及需要补充的研究依据。", ("需要提供文献笔记或证据材料，不能仅凭研究主题判断创新点。",)),
}

_PUBLIC_SKILL_NAME_OVERRIDES: dict[str, str] = {
    "晶体设计 · 公式交叉搜索（SAGA-Opt MVP）": "晶体设计与配方筛选",
    "永磁体设计 MVP（SAGA-Opt）": "永磁体设计",
    "MP 约束检索（L3）": "材料数据库约束检索",
    "新晶体演化生成（L3）": "新晶体生成",
    "Flux 制备参数推荐（L3）": "熔盐制备参数推荐",
    "VASP 热力学稳定性分析（L3）": "热力学稳定性分析",
    "学术文献多源 Metadata 召回": "学术文献多源检索",
    "DeepXiv 渐进式文献深读": "文献逐步精读",
    "候选文献 Top-K 选择": "候选文献优选",
}

_PUBLIC_SKILL_SCENARIO_SUMMARIES: dict[str, str] = {
    "problem_modeling": "梳理研究问题、对象和条件",
    "solution_design": "设计研究方案和比较思路",
    "execution_validation": "检查研究过程和结果",
    "insight_generation": "整理证据并形成研究结论",
}


def _public_agent_content(agent_id: str) -> tuple[str, str, tuple[str, ...]]:
    return _PUBLIC_AGENT_CONTENT.get(agent_id, ("智能体", "帮助开展与研究目标相关的分析、整理和研究设计工作。", ("具体适用范围请结合研究材料确认。",)))


def _public_skill_content(name: str, release: "CatalogRelease") -> tuple[str, str]:
    public_name = _PUBLIC_SKILL_NAME_OVERRIDES.get(name, name)
    # Registry metadata may contain implementation names and English descriptions.
    # The catalog presents a short research-facing summary instead.
    public_name = re.sub(r"\s*[（(][A-Za-z0-9 ._-]+[）)]", "", public_name).strip()
    scenario = next(
        (_PUBLIC_SKILL_SCENARIO_SUMMARIES[value] for value in release.scenarios if value in _PUBLIC_SKILL_SCENARIO_SUMMARIES),
        "推进具体研究步骤",
    )
    return public_name, f"帮助你{scenario}，适合在“{public_name}”这一研究环节使用。"

_EXECUTION_ENTRY_BY_RESOURCE_TYPE: dict[ResourceType, str] = {
    ResourceType.SKILL: "durable_skill",
    ResourceType.AGENT: "durable_agent",
}


class ResourceCatalogUnavailableError(RuntimeError):
    """Raised when a CatalogRelease or Registry cannot be trusted."""


@dataclass(frozen=True)
class CatalogRelease:
    resource_id: str
    resource_type: ResourceType
    release_version: str
    catalog_released: bool
    discoverable: bool
    visible_on_home: bool
    visible_in_catalog: bool
    visible_in_detail: bool
    source: str
    maintainer: str
    usage_policy: str
    license_summary: str
    evidence_status: str
    evidence_conflict_disposition: str
    availability: ResourceAvailability
    execution_blocked: bool
    primary_action: str
    configuration_requirements: tuple[str, ...]
    permission_requirements: tuple[str, ...]
    execution_entry: str | None
    safety_risk: str | None
    domains: tuple[str, ...]
    scenarios: tuple[str, ...]


@dataclass(frozen=True)
class CatalogResource:
    resource_id: str
    name: str
    description: str
    resource_type: ResourceType
    input: tuple[str, ...]
    output: tuple[str, ...]
    trust_summary: str
    requirements: tuple[str, ...]
    limitations: tuple[str, ...]
    availability: ResourceAvailability
    execution_blocked: bool
    safety_risk: str | None
    primary_action: str
    keywords: tuple[str, ...]
    source: str | None = None
    maintainer: str | None = None
    skill_path: Path | None = None
    release: CatalogRelease | None = None
    capability_id: str | None = None
    durable_executable: bool | None = None
    launch_mode: str | None = None
    dependency_gaps: tuple[str, ...] = ()
    unavailable_reason: str | None = None
    runtime_family: str | None = None

    def to_dict(self) -> dict[str, Any]:
        release = self.release
        return {
            "id": self.resource_id,
            "name": self.name,
            "description": self.description,
            "resourceType": self.resource_type,
            "input": list(self.input),
            "output": list(self.output),
            "trustSummary": self.trust_summary,
            "requirements": list(self.requirements),
            "limitations": list(self.limitations),
            "source": release.source if release else self.source,
            "maintainer": release.maintainer if release else self.maintainer,
            "releaseVersion": release.release_version if release else None,
            "visibleOnHome": release.visible_on_home if release else False,
            "visibleInCatalog": release.visible_in_catalog if release else True,
            "visibleInDetail": release.visible_in_detail if release else True,
            "usagePolicy": release.usage_policy if release else None,
            "licenseSummary": release.license_summary if release else None,
            "evidenceStatus": _public_evidence_summary(release) if release else None,
            "evidenceConflictDisposition": None,
            "configurationRequirements": list(release.configuration_requirements) if release else [],
            "permissionRequirements": list(release.permission_requirements) if release else [],
            "executionEntry": release.execution_entry if release else None,
            "availability": self.availability,
            "executionBlocked": self.execution_blocked,
            # This legacy alias is intentionally omitted for Registry defaults.  The
            # user-facing availability already explains whether an action is blocked.
            "safetyRisk": self.safety_risk if release else None,
            "primaryAction": self.primary_action,
            # Reserved for the capability-runtime line.  A missing value is unknown,
            # never an inference from Registry readiness or a local adapter.
            "capabilityId": self.capability_id or self.resource_id,
            "durableExecutable": self.durable_executable,
            "launchMode": self.launch_mode,
            "dependencyGaps": list(self.dependency_gaps),
            "unavailableReason": self.unavailable_reason,
            "runtimeFamily": self.runtime_family,
            "domains": list(release.domains) if release else [],
            "scenarios": list(release.scenarios) if release else [],
            # Frozen FE-02 aliases of the canonical fields above.
            "kind": self.resource_type,
            "inputs": list(self.input),
            "outputs": list(self.output),
            "usageConditions": list(self.requirements),
            "safetyStatus": self.safety_risk,
            # Search uses the internal projection before this conversion.  Do not
            # leak Registry identifiers, provider labels, or adapter names to users.
            "keywords": [],
        }


def _public_evidence_summary(release: CatalogRelease) -> str | None:
    if not release.evidence_status:
        return None
    return "已声明验证事实"


def _load_yaml(path: Path, label: str) -> Any:
    if not path.is_file():
        raise ResourceCatalogUnavailableError(f"{label} is unavailable")
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ResourceCatalogUnavailableError(f"{label} cannot be read") from exc


def _load_catalog_releases() -> dict[str, CatalogRelease]:
    """Load the authoritative public CatalogRelease facts, failing closed."""
    if not CATALOG_RELEASES_PATH.is_file():
        raise ResourceCatalogUnavailableError("CatalogRelease data is unavailable")
    if not CATALOG_RELEASE_SCHEMA_PATH.is_file():
        raise ResourceCatalogUnavailableError("CatalogRelease schema is unavailable")
    try:
        schema = json.loads(CATALOG_RELEASE_SCHEMA_PATH.read_text(encoding="utf-8"))
        payload = _load_yaml(CATALOG_RELEASES_PATH, "CatalogRelease data")
        jsonschema.Draft202012Validator.check_schema(schema)
        errors = list(jsonschema.Draft202012Validator(schema).iter_errors(payload))
    except (OSError, json.JSONDecodeError, jsonschema.SchemaError) as exc:
        raise ResourceCatalogUnavailableError("CatalogRelease schema is invalid") from exc
    except jsonschema.ValidationError as exc:
        raise ResourceCatalogUnavailableError("CatalogRelease data is invalid") from exc
    if errors:
        raise ResourceCatalogUnavailableError("CatalogRelease data is invalid")
    if not isinstance(payload, dict) or not isinstance(payload.get("catalogReleases"), list):
        raise ResourceCatalogUnavailableError("CatalogRelease data has an invalid shape")

    releases: dict[str, CatalogRelease] = {}
    for record in payload["catalogReleases"]:
        if not isinstance(record, dict):
            raise ResourceCatalogUnavailableError("CatalogRelease record is invalid")
        resource_id = record["resourceId"]
        if resource_id in releases:
            raise ResourceCatalogUnavailableError("CatalogRelease contains a duplicate resourceId")
        try:
            release = CatalogRelease(
                resource_id=resource_id,
                resource_type=ResourceType(record["resourceType"]),
                release_version=record["releaseVersion"],
                catalog_released=record["catalogReleased"],
                discoverable=record["discoverable"],
                visible_on_home=record["visibleOnHome"],
                visible_in_catalog=record["visibleInCatalog"],
                visible_in_detail=record["visibleInDetail"],
                source=record["source"],
                maintainer=record["maintainer"],
                usage_policy=record["usagePolicy"],
                license_summary=record["licenseSummary"],
                evidence_status=record["evidenceStatus"],
                evidence_conflict_disposition=record["evidenceConflictDisposition"],
                availability=ResourceAvailability(record["availability"]),
                execution_blocked=record["executionBlocked"],
                primary_action=record["primaryAction"],
                configuration_requirements=tuple(record["configurationRequirements"]),
                permission_requirements=tuple(record["permissionRequirements"]),
                execution_entry=record["executionEntry"],
                safety_risk=record["safetyRisk"],
                domains=tuple(str(value) for value in record["domains"]),
                scenarios=tuple(str(value) for value in record["scenarios"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ResourceCatalogUnavailableError("CatalogRelease record is invalid") from exc
        _validate_release_state(release)
        releases[resource_id] = release
    return releases


def _validate_release_state(release: CatalogRelease) -> None:
    action_by_availability = {
        ResourceAvailability.TRYABLE: (False, "try"),
        ResourceAvailability.CONFIGURATION_REQUIRED: (True, "configure"),
        ResourceAvailability.VIEW_ONLY: (True, "view"),
        ResourceAvailability.UNAVAILABLE: (True, "none"),
    }
    if (release.execution_blocked, release.primary_action) != action_by_availability[release.availability]:
        raise ResourceCatalogUnavailableError("CatalogRelease state and action conflict")
    if release.availability == ResourceAvailability.UNAVAILABLE and release.safety_risk != "execution_blocked":
        raise ResourceCatalogUnavailableError("Unavailable CatalogRelease lacks execution block risk")
    if release.availability != ResourceAvailability.UNAVAILABLE and release.safety_risk is not None:
        raise ResourceCatalogUnavailableError("CatalogRelease safety risk conflicts with availability")

    expected_entry = _EXECUTION_ENTRY_BY_RESOURCE_TYPE.get(release.resource_type)
    if release.execution_entry is not None and release.execution_entry != expected_entry:
        raise ResourceCatalogUnavailableError("CatalogRelease execution entry does not match resource type")
    if release.availability == ResourceAvailability.TRYABLE and (
        expected_entry is None or release.execution_entry != expected_entry
    ):
        raise ResourceCatalogUnavailableError("Tryable CatalogRelease lacks a valid execution entry")
    if release.availability == ResourceAvailability.VIEW_ONLY and release.execution_entry is not None:
        raise ResourceCatalogUnavailableError("View-only CatalogRelease cannot expose an execution entry")
    # Featured shelf (visibleOnHome) is tryable-only; marketing preview must use catalog, not home.
    if release.visible_on_home and (
        release.availability != ResourceAvailability.TRYABLE or release.execution_blocked
    ):
        raise ResourceCatalogUnavailableError("Featured CatalogRelease must be tryable")


def _safe_skill_path(raw_path: object) -> Path | None:
    if not isinstance(raw_path, str) or not raw_path.strip():
        return None
    root = repo_root().resolve()
    candidate = (root / raw_path).resolve()
    if root not in candidate.parents:
        return None
    return candidate


def _load_skill_entries() -> list[dict[str, Any]]:
    payload = _load_yaml(SKILLS_REGISTRY_PATH, "Skill registry")
    if not isinstance(payload, dict) or not isinstance(payload.get("skills"), list):
        raise ResourceCatalogUnavailableError("Skill registry has an invalid shape")
    if any(not isinstance(entry, dict) for entry in payload["skills"]):
        raise ResourceCatalogUnavailableError("Skill registry contains an invalid entry")
    return payload["skills"]


def _project_skills() -> list[CatalogResource]:
    resources: dict[str, CatalogResource] = {}
    duplicate_ids: set[str] = set()
    for entry in _load_skill_entries():
        skill_id = entry.get("skill_id")
        if not isinstance(skill_id, str) or not skill_id.strip():
            continue
        if skill_id.strip() in NON_PRODUCT_SKILL_IDS:
            continue
        resource_id = f"skill:{skill_id.strip()}"
        if resource_id in resources:
            duplicate_ids.add(resource_id)
            continue
        skill_path = _safe_skill_path(entry.get("skill_path"))
        resources[resource_id] = CatalogResource(
            resource_id=resource_id,
            name=str(entry.get("display_name") or skill_id).strip(),
            description=str(entry.get("description") or "").strip(),
            resource_type=ResourceType.SKILL,
            input=(), output=(),
            trust_summary="已登记资源事实；版本、许可、验证和执行事实均未声明。",
            requirements=(),
            limitations=("尚未声明可独立验证的用户运行入口。",),
            availability=ResourceAvailability.VIEW_ONLY, execution_blocked=True,
            safety_risk=None, primary_action="view",
            keywords=tuple(value for value in (skill_id, entry.get("domain"), *(entry.get("stages") or [])) if isinstance(value, str) and value),
            source=_registry_text(entry.get("source")),
            maintainer=_registry_text(entry.get("owner"), reject=("tbd",)),
            skill_path=skill_path,
        )
    for resource_id in duplicate_ids:
        resources.pop(resource_id, None)
    return list(resources.values())


def _project_agents() -> list[CatalogResource]:
    try:
        agents = load_agent_registry().list_agents()
    except (AgentRegistryError, OSError, yaml.YAMLError) as exc:
        raise ResourceCatalogUnavailableError("Agent registry is unavailable") from exc
    resources: list[CatalogResource] = []
    for agent in agents:
        name, description, limitations = _public_agent_content(agent.agentId)
        resources.append(CatalogResource(
            resource_id=f"agent:{agent.agentId}", name=name,
            description=description, resource_type=ResourceType.AGENT,
            input=tuple(str(value) for value in agent.inputContract.get("required") or []),
            output=tuple(str(value) for value in agent.outputContract.get("requiredFields") or [agent.artifact_contract]),
            trust_summary="已登记资源事实；版本、许可、验证和执行事实均未声明。",
            requirements=(),
            limitations=limitations,
            availability=ResourceAvailability.VIEW_ONLY,
            execution_blocked=True, safety_risk=None, primary_action="view",
            keywords=tuple([agent.agentId, agent.agentKind, *agent.skillIds]),
            source="OpenAIR 智能体注册表",
        ))
    return resources


def _project_workflows() -> list[CatalogResource]:
    try:
        recipes = load_multi_agent_recipe_registry().list_recipes()
    except (MultiAgentRecipeRegistryError, OSError, yaml.YAMLError) as exc:
        raise ResourceCatalogUnavailableError("Workflow registry is unavailable") from exc
    return [
        CatalogResource(
            resource_id=f"workflow:{recipe.recipe_id}@{recipe.recipe_version}",
            name=recipe.recipe_id.replace("_", " ").title(),
            description="由多个智能体协作完成研究环节的工作流模板。", resource_type=ResourceType.WORKFLOW,
            input=tuple(str(value) for value in recipe.applicability_rule_refs),
            output=tuple(str(item.artifact_type) for item in recipe.result_assembly_policy_template.final_artifact_requirements),
            trust_summary="已登记资源事实；版本、许可、验证和执行事实均未声明。", requirements=(),
            limitations=("尚未声明可独立验证的用户运行入口。",),
            availability=ResourceAvailability.VIEW_ONLY, execution_blocked=True,
            safety_risk=None, primary_action="view", keywords=(recipe.recipe_id, recipe.substage_definition_id),
            source="OpenAIR 工作流模板注册表",
        ) for recipe in recipes
    ]


def _registry_text(value: object, *, reject: tuple[str, ...] = ()) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.casefold() in reject:
        return None
    return text


def _schema_required_fields(path: Path) -> tuple[str, ...]:
    if not path.is_file():
        raise ResourceCatalogUnavailableError("Released Skill schema is unavailable")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ResourceCatalogUnavailableError("Released Skill schema is invalid") from exc
    required = payload.get("required") if isinstance(payload, dict) else None
    if not isinstance(required, list) or any(not isinstance(value, str) for value in required):
        raise ResourceCatalogUnavailableError("Released Skill schema is invalid")
    return tuple(required)


def _release_limitations(resource_id: str) -> tuple[str, ...]:
    # These are the externally visible boundaries verified by the Skills' contracts.
    limits = {
        "skill:source_access_readiness_resolver": (
            "仅评估既有元数据和访问状态，不能证明全文实际可得。",
        ),
        "skill:title_abstract_screening": (
            "仅基于标题和摘要进行初筛，不构成实质性文献评估或最终纳入决定。",
        ),
        "skill:academic_source_retrieval_skill": (
            "依赖外部网络和第三方学术数据源；当前目录仅供查看，不开放用户执行。",
        ),
    }
    return limits.get(resource_id, ())


def _apply_catalog_release(resource: CatalogResource, release: CatalogRelease) -> CatalogResource:
    if resource.resource_type != release.resource_type:
        raise ResourceCatalogUnavailableError("CatalogRelease resource type does not match Registry")
    input_fields = resource.input
    output_fields = resource.output
    description = resource.description
    name = resource.name
    if resource.resource_type == ResourceType.SKILL and resource.skill_path is not None:
        name, description = _public_skill_content(resource.name, release)
        input_schema = resource.skill_path / "input.schema.json"
        output_schema = resource.skill_path / "output.schema.json"
        if input_schema.is_file() and output_schema.is_file():
            try:
                input_fields = _schema_required_fields(input_schema)
                output_fields = _schema_required_fields(output_schema)
            except ResourceCatalogUnavailableError:
                # Incomplete schemas must not fail-close the whole public catalog.
                input_fields = resource.input
                output_fields = resource.output
    return replace(
        resource,
        name=name, description=description, input=input_fields, output=output_fields,
        trust_summary="CatalogRelease 已声明验证事实；当前未开放用户执行。",
        requirements=release.configuration_requirements,
        limitations=_release_limitations(resource.resource_id) or resource.limitations, availability=release.availability,
        execution_blocked=release.execution_blocked, safety_risk=release.safety_risk,
        primary_action=release.primary_action, release=release,
    )


def _release_allows_research_start(resource: CatalogResource) -> bool:
    """Return whether the formal release grants the maximum start permission."""
    release = resource.release
    expected_entry = _EXECUTION_ENTRY_BY_RESOURCE_TYPE.get(resource.resource_type)
    return bool(
        release
        and release.availability == ResourceAvailability.TRYABLE
        and not release.execution_blocked
        and release.primary_action == "try"
        and expected_entry is not None
        and release.execution_entry == expected_entry
    )


def _apply_runtime_facts(resource: CatalogResource) -> CatalogResource:
    """Apply runtime facts without ever widening the CatalogRelease permission ceiling."""
    facts: ResourceCapabilityFacts = resolve_resource_capability_facts(
        resource.resource_id, resource.resource_type.value
    )

    # A non-tryable formal release remains authoritative. Runtime facts are still
    # attached for diagnostics, but cannot promote view_only/configuration_required.
    if not _release_allows_research_start(resource):
        availability = resource.availability
        execution_blocked = resource.execution_blocked
        primary_action = resource.primary_action
        safety_risk = resource.safety_risk
        unavailable_reason = facts.unavailable_reason
    elif not facts.durable_executable:
        availability = ResourceAvailability.UNAVAILABLE
        execution_blocked = True
        primary_action = "none"
        safety_risk = None
        unavailable_reason = facts.unavailable_reason or "durable_contract_unavailable"
    elif facts.dependency_gaps:
        availability = ResourceAvailability.CONFIGURATION_REQUIRED
        execution_blocked = True
        primary_action = "configure"
        safety_risk = None
        unavailable_reason = facts.unavailable_reason
    elif facts.unavailable_reason:
        availability = ResourceAvailability.UNAVAILABLE
        execution_blocked = True
        primary_action = "none"
        safety_risk = None
        unavailable_reason = facts.unavailable_reason
    elif (
        not isinstance(facts.capability_id, str)
        or not facts.capability_id.strip()
        or facts.launch_mode != resource.release.execution_entry
    ):
        availability = ResourceAvailability.UNAVAILABLE
        execution_blocked = True
        primary_action = "none"
        safety_risk = None
        unavailable_reason = "execution_entry_mismatch"
    else:
        availability = ResourceAvailability.TRYABLE
        execution_blocked = False
        primary_action = "try"
        safety_risk = None
        unavailable_reason = None

    return replace(
        resource,
        availability=availability,
        execution_blocked=execution_blocked,
        safety_risk=safety_risk,
        primary_action=primary_action,
        capability_id=facts.capability_id,
        durable_executable=facts.durable_executable,
        launch_mode=facts.launch_mode,
        dependency_gaps=facts.dependency_gaps,
        unavailable_reason=unavailable_reason,
        runtime_family=facts.runtime_family,
    )



def clear_resource_catalog_cache() -> None:
    """Drop the in-process catalog projection so the next read rebuilds.

    Called after contribution publish/approve and available for tests that
    monkeypatch catalog loaders.
    """
    global _catalog_projection_cache, _catalog_projection_fingerprint
    _catalog_projection_cache = None
    _catalog_projection_fingerprint = None


def _path_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return -1.0


def _catalog_source_fingerprint() -> tuple[float, ...]:
    """Filesystem mtimes for inputs that gate the official catalog projection.

    Contribution rows are not fingerprintable cheaply here; approve/publish must
    call ``clear_resource_catalog_cache`` (already wired in contributions routes).
    """
    from app.modules.agent_registry import REGISTRY_PATH as AGENT_REGISTRY_PATH
    from app.modules.multi_agent_recipe_registry import REGISTRY_PATH as RECIPE_REGISTRY_PATH

    return (
        _path_mtime(CATALOG_RELEASES_PATH),
        _path_mtime(CATALOG_RELEASE_SCHEMA_PATH),
        _path_mtime(SKILLS_REGISTRY_PATH),
        _path_mtime(AGENT_REGISTRY_PATH),
        _path_mtime(RECIPE_REGISTRY_PATH),
    )


def _build_catalog_resources() -> list[CatalogResource]:
    releases = _load_catalog_releases()
    registered = [*_project_skills(), *_project_agents(), *_project_workflows()]
    registry_by_id = {resource.resource_id: resource for resource in registered}
    if len(registry_by_id) != len(registered):
        raise ResourceCatalogUnavailableError("Registry resources contain duplicate IDs")
    for resource_id in releases:
        if resource_id not in registry_by_id:
            raise ResourceCatalogUnavailableError("CatalogRelease references an unknown Registry resource")
    official = [
        _apply_runtime_facts(_apply_catalog_release(resource, releases[resource.resource_id]))
        for resource in registered
        if resource.resource_id in releases
    ]
    official_index = {resource.resource_id: resource for resource in official}
    contributions = _project_approved_contributions()
    duplicate_ids = official_index.keys() & {resource.resource_id for resource in contributions}
    if duplicate_ids:
        raise ResourceCatalogUnavailableError("Public catalog contains duplicate resource IDs")
    return [*official, *contributions]


def _catalog_resources() -> list[CatalogResource]:
    """Return the public catalog projection, reusing an in-process cache when sources are unchanged."""
    global _catalog_projection_cache, _catalog_projection_fingerprint
    fingerprint = _catalog_source_fingerprint()
    if (
        _catalog_projection_cache is not None
        and _catalog_projection_fingerprint == fingerprint
    ):
        return _catalog_projection_cache
    built = _build_catalog_resources()
    _catalog_projection_cache = built
    _catalog_projection_fingerprint = fingerprint
    return built


def warm_resource_catalog_cache() -> int:
    """Build (or refresh) the catalog projection for startup preheat.

    Returns the number of projected resources. Failures propagate to the caller
    so startup can log and continue without blocking the API process.
    """
    clear_resource_catalog_cache()
    return len(_catalog_resources())


def _manifest_object(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _manifest_string_list(manifest: dict[str, Any], key: str) -> tuple[str, ...]:
    value = manifest.get(key)
    if not isinstance(value, list):
        return ()
    return tuple(str(item).strip() for item in value if isinstance(item, str) and item.strip())


def _project_approved_contributions() -> list[CatalogResource]:
    """Project approved immutable contribution snapshots into ordinary discovery.

    Public execution is deliberately not inferred from a structurally valid Skill.
    Until a separate public runtime contract exists, contributed resources are visible
    as ``configuration_required`` and never become a false tryable entry.
    """
    repo = get_contribution_repository()
    rows: list[dict[str, Any]] = []
    offset = 0
    try:
        while True:
            page = repo.list_public_catalog(limit=200, offset=offset)
            rows.extend(page)
            if len(page) < 200:
                break
            offset += len(page)
    except Exception as exc:
        raise ResourceCatalogUnavailableError("Contribution catalog is unavailable") from exc

    projected: list[CatalogResource] = []
    for row in rows:
        resource_id = str(row.get("catalog_resource_id") or "").strip()
        release_version = str(row.get("release_version") or "").strip()
        title = str(row.get("title") or "").strip()
        manifest = _manifest_object(row.get("published_manifest"))
        if not resource_id or not release_version or not title or not manifest:
            logger.warning(
                "skipping incomplete approved contribution in catalog: %s",
                row.get("contribution_id"),
            )
            continue
        domain = str(row.get("domain") or "general")
        if domain not in {value.value for value in ResourceDomain}:
            domain = ResourceDomain.GENERAL.value
        description = str(row.get("description") or manifest.get("description") or "").strip()
        release = CatalogRelease(
            resource_id=resource_id,
            resource_type=ResourceType.SKILL,
            release_version=release_version,
            catalog_released=True,
            discoverable=True,
            visible_on_home=False,
            visible_in_catalog=True,
            visible_in_detail=True,
            source="用户公开贡献",
            maintainer="OpenAIR 社区",
            usage_policy="公开查看；公共执行入口配置完成后方可运行。",
            license_summary="按 OpenAIR 公开贡献规则展示。",
            evidence_status="system_check_and_human_review",
            evidence_conflict_disposition="none",
            availability=ResourceAvailability.CONFIGURATION_REQUIRED,
            execution_blocked=True,
            primary_action="configure",
            configuration_requirements=("公共执行入口尚未配置。",),
            permission_requirements=(),
            execution_entry=None,
            safety_risk=None,
            domains=(domain,),
            scenarios=(),
        )
        projected.append(
            CatalogResource(
                resource_id=resource_id,
                name=title,
                description=description,
                resource_type=ResourceType.SKILL,
                input=_manifest_string_list(manifest, "input"),
                output=_manifest_string_list(manifest, "output"),
                trust_summary="已完成结构检查和人工审核；公共执行入口仍需平台配置。",
                requirements=("公共执行入口尚未配置。",),
                limitations=("当前可公开查看，暂不开放公共执行。",),
                availability=ResourceAvailability.CONFIGURATION_REQUIRED,
                execution_blocked=True,
                safety_risk=None,
                primary_action="configure",
                keywords=(
                    str(row.get("resource_ref") or ""),
                    title,
                    description,
                ),
                source=release.source,
                maintainer=release.maintainer,
                release=release,
                capability_id=resource_id,
                durable_executable=False,
                launch_mode=None,
                dependency_gaps=("公共执行入口尚未配置。",),
                unavailable_reason=None,
                runtime_family=None,
            )
        )
    return projected


def _is_catalog_visible(resource: CatalogResource) -> bool:
    release = resource.release
    return bool(
        release
        and release.catalog_released
        and release.discoverable
        and release.visible_in_catalog
    )


def _is_detail_visible(resource: CatalogResource) -> bool:
    release = resource.release
    return bool(
        release
        and release.catalog_released
        and release.discoverable
        and release.visible_in_detail
    )


def _is_publicly_visible(resource: CatalogResource) -> bool:
    """Research Start may originate from either a catalog card or detail page."""
    return _is_catalog_visible(resource) or _is_detail_visible(resource)


def list_resources(
    *,
    q: str | None = None,
    resource_type: ResourceType | None = None,
    availability: ResourceAvailability | None = None,
    domain: ResourceDomain | None = None,
    scenario: ResourceScenario | None = None,
    visible_on_home: bool | None = None,
    sort: ResourceCatalogSort = ResourceCatalogSort.RELEVANCE,
) -> list[dict[str, Any]]:
    """Return only resources admitted by their authoritative CatalogRelease."""
    query = (q or "").strip().casefold()
    rows = _catalog_resources()
    filtered: list[dict[str, Any]] = []
    for row in rows:
        if not _is_catalog_visible(row):
            continue
        if resource_type is not None and row.resource_type != resource_type:
            continue
        if availability is not None and row.availability != availability:
            continue
        release = row.release
        if domain is not None and (release is None or domain.value not in release.domains):
            continue
        if scenario is not None and (release is None or scenario.value not in release.scenarios):
            continue
        if visible_on_home is not None and (release is None or release.visible_on_home is not visible_on_home):
            continue
        # Home featured query: effective tryable only (runtime may demote a tryable Release).
        if visible_on_home is True and (
            row.availability != ResourceAvailability.TRYABLE or row.execution_blocked
        ):
            continue
        haystack = " ".join((row.name, row.description, *row.keywords)).casefold()
        if query and query not in haystack:
            continue
        filtered.append(row.to_dict())
    if sort == ResourceCatalogSort.NAME_ASC:
        return sorted(filtered, key=lambda row: (row["name"].casefold(), row["id"]))
    if sort == ResourceCatalogSort.NAME_DESC:
        return sorted(filtered, key=lambda row: (row["name"].casefold(), row["id"]), reverse=True)
    return sorted(filtered, key=lambda row: (row["resourceType"], row["id"]))


def get_resource(resource_id: str) -> dict[str, Any] | None:
    for resource in _catalog_resources():
        if resource.resource_id == resource_id and _is_detail_visible(resource):
            return resource.to_dict()
    return None


def get_resource_for_research_start(
    resource_id: str,
    resource_version: str | None = None,
    capability_id: str | None = None,
) -> dict[str, Any] | None:
    """Resolve the same effective catalog projection used by list and detail."""
    if (resource_version is None) != (capability_id is None):
        return None

    for resource in _catalog_resources():
        if resource.resource_id != resource_id or not _is_publicly_visible(resource):
            continue
        # Effective ``tryable`` is produced only after Release authority and all
        # runtime prerequisites have passed. Research Start does not keep a
        # second, independently drifting admission checklist.
        if resource.availability != ResourceAvailability.TRYABLE:
            return None
        release = resource.release
        if release is None:
            return None
        if resource_version is not None and release.release_version != resource_version:
            return None
        if capability_id is not None and resource.capability_id != capability_id:
            return None
        return resource.to_dict()
    return None
