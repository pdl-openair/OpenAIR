"""Immutable Registry adapter and deterministic instance candidate evaluation."""

from __future__ import annotations

from copy import deepcopy

from pathlib import Path, PurePosixPath, PureWindowsPath
import json
import re
import subprocess
from typing import Any, Mapping
import unicodedata

from pydantic import ConfigDict, Field, model_validator
import yaml
from app.modules.yaml_source_cache import SAFE_LOADER, read_yaml_source

from app.models.research_orchestration import (
    AgentRef,
    BindingRef,
    CandidateEvaluation,
    CandidateExecutionBlockerCode,
    ExecutionMethodRoleEvaluation,
    CandidatePreference,
    CandidateRejectionCode,
    CompletionCoverage,
    ContractModel,
    InstanceRef,
    MultiAgentRecipeDefinition,
    MultiAgentRecipeRef,
    ResearchDesignRef,
    ResearchDesignVersion,
    RegistrySnapshotRef,
    RiskClass,
    SemanticFitEvidence,
    SkillRef,
    StrictBool,
    JsonAwareDatetime,
    NonNegativeInt,
)
from app.modules.agent_registry import load_agent_registry_payload, validate_agent_registry_payload
from app.modules.definition_external_action_source import (
    DefinitionExternalActionSourceError,
    load_definition_external_action_contracts,
)
from app.execution.durable_orchestrator_runtime import load_frozen_artifact_contract
from app.modules.multi_agent_recipe_registry import load_multi_agent_recipe_registry
from app.modules.registry_snapshot_repository import (
    canonicalize_registry_value,
    registry_envelope_ref,
    RegistrySnapshot,
    RegistrySnapshotArchive,
    parse_spec_document,
    snapshot_from_envelope,
)
from app.execution.durable_skill_runtime import resolve_registered_durable_skill_runtime
from app.modules.repo_paths import repo_root
from app.modules.substage_identity import canonical_substage_key as _canonical_substage_key
from app.modules.skill_binding_source import (
    SkillBindingSourceError,
    load_skill_binding_source_rows,
    skill_ids_requiring_frozen_handoff_schemas,
)
from app.modules.conditional_capability_binding import (
    CapabilityApplicabilityResult,
    validate_conditional_policy,
)


ROOT = repo_root()
PACKS_PATH = ROOT / "schemas" / "packs.yaml"
SKILLS_PATH = ROOT / "skills" / "registry.yaml"
OPENTRAJECTORY_SKILL_MAP_PATH = ROOT / "schemas" / "opentrajectory" / "substage-skill-map.yaml"
MATERIALS_WORKFLOW_SKILL_MAP_PATH = ROOT / "schemas" / "materials" / "candidate-discovery-workflow-skill-map.yaml"
MATERIALS_FULL_DISCOVERY_SKILL_MAP_PATH = ROOT / "schemas" / "materials" / "full-discovery-workflow-skill-map.yaml"
PVD_WORKFLOW_SKILL_MAP_PATH = ROOT / "schemas" / "pvd" / "substage-skill-map.yaml"
CS_REPRODUCTION_SKILL_MAP_PATH = ROOT / "schemas" / "cs_reproduction" / "substage-skill-map.yaml"
SCHEMA_VERSION = 1
POLICY_VERSION = "registry-policy.v1"

class _UniqueKeyLoader(SAFE_LOADER):
    pass


def _construct_unique_mapping(loader: yaml.SafeLoader, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
    result: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            raise RegistryAuthorityError(f"duplicate_yaml_key:{key}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping)


class RegistryAuthorityError(ValueError):
    """Raised when canonical Registry authority is incomplete or ambiguous."""


class ResolutionRejectedError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        super().__init__(f"{code}:{detail}" if detail else code)


class ResolutionFacts(ContractModel):
    model_config = ConfigDict(frozen=True)

    runtime_readiness: dict[str, StrictBool]
    provider_readiness: dict[str, StrictBool]
    authorized_candidate_refs: set[str]
    available_input_refs: set[str]
    applicable_rule_refs: set[str]
    satisfied_human_gate_refs: set[str] = Field(default_factory=set)
    risk_allowed: StrictBool
    budget_available: StrictBool
    policy_version: str = Field(min_length=1, strict=True)
    active_work_unit_counts: dict[str, NonNegativeInt] = Field(default_factory=dict)
    last_assigned_at: dict[str, JsonAwareDatetime] = Field(default_factory=dict)
    current_scientific_state: dict[str, Any] | None = None
    authoritative_runtime_fact_refs: set[str] = Field(default_factory=set)
    capability_applicability_results: dict[
        str, CapabilityApplicabilityResult
    ] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_applicability_result_keys(self) -> "ResolutionFacts":
        if any(
            key != result.capability_ref
            for key, result in self.capability_applicability_results.items()
        ):
            raise ValueError(
                "capabilityApplicabilityResults keys must match capabilityRef"
            )
        return self


class ResolutionResult(ContractModel):
    design_ref: ResearchDesignRef
    instance_ref: InstanceRef
    registry_snapshot_ref: RegistrySnapshotRef
    candidate_evaluations: list[CandidateEvaluation]
    preferred_candidate_evaluation_refs: list[str]


def state_outstanding_criterion_ids(
    instance: Any,
    facts: ResolutionFacts,
) -> set[str] | None:
    """Return current Design criteria that still require an explicit outcome.

    ScientificState distinguishes unresolved uncertainty from unmet criteria after
    a failed/recoverable execution.  Both remain outstanding when the same
    SubstageInstance is intentionally re-entered.  Returning ``None`` means no
    exact State projection exists, so callers must keep the full confirmed
    Design contract; an empty set means State explicitly has no outstanding
    criteria for this instance revision.
    """
    state = facts.current_scientific_state
    if not isinstance(state, Mapping):
        return None
    execution_state = state.get("executionState")
    if not isinstance(execution_state, Mapping):
        return None
    rows = execution_state.get("substageCompletion")
    if not isinstance(rows, list):
        return None
    row = next(
        (
            candidate for candidate in rows
            if isinstance(candidate, Mapping)
            and candidate.get("substageInstanceId") == instance.substage_instance_id
            and candidate.get("instanceRevision") == instance.instance_revision
        ),
        None,
    )
    if row is None:
        return None
    unresolved = row.get("unresolvedCriterionIds")
    unmet = row.get("unmetCriterionIds")
    if not isinstance(unresolved, list) and not isinstance(unmet, list):
        return None
    return {
        value
        for values in (unresolved, unmet)
        if isinstance(values, list)
        for value in values
        if isinstance(value, str)
    }


def _load_yaml(path: Path) -> dict[str, Any]:
    payload = read_yaml_source(path, loader=_UniqueKeyLoader) or {}
    if not isinstance(payload, dict):
        raise RegistryAuthorityError(f"canonical source must be a mapping: {path}")
    return payload


def parse_definition_spec(pack: str, ref: str, content: str) -> dict[str, Any]:
    return parse_spec_document(pack, ref, content)


def _tracked_repo_paths() -> set[str]:
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z", "--", "schemas"],
            cwd=ROOT,
            check=True,
            capture_output=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        # Production images deliberately omit .git. The Docker build copies
        # the authoritative schemas directory, so use its regular files as
        # the packaged source inventory while retaining path containment and
        # missing-file checks in load_definition_spec().
        schemas_root = ROOT / "schemas"
        if not schemas_root.is_dir():
            return set()
        return {
            path.relative_to(ROOT).as_posix()
            for path in schemas_root.rglob("*")
            if path.is_file() and not path.is_symlink()
        }
    return {
        value.decode("utf-8").replace("\\", "/")
        for value in result.stdout.split(b"\0")
        if value
    }


def load_definition_spec(
    pack: str,
    ref: str,
    *,
    tracked_paths: set[str] | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    if not isinstance(ref, str) or not ref.strip():
        raise RegistryAuthorityError("missing_spec_ref")
    ref = ref.strip()
    if "\\" in ref:
        raise RegistryAuthorityError(f"invalid_spec_ref:{pack}:{ref}")
    posix_ref = PurePosixPath(ref)
    if PureWindowsPath(ref).is_absolute() or posix_ref.is_absolute():
        raise RegistryAuthorityError(f"absolute_spec_ref:{pack}:{ref}")
    if any(part in {"", ".", ".."} for part in posix_ref.parts):
        raise RegistryAuthorityError(f"invalid_spec_ref:{pack}:{ref}")
    source_root = (root or ROOT).resolve()
    schemas_root = (source_root / "schemas").resolve()
    pack_root = (schemas_root / pack).resolve()
    try:
        pack_root.relative_to(schemas_root)
    except ValueError as exc:
        raise RegistryAuthorityError(f"invalid_pack:{pack}") from exc
    relative = posix_ref if posix_ref.parts[0] == "specs" else PurePosixPath("specs") / posix_ref
    lexical_path = pack_root.joinpath(*relative.parts)
    try:
        resolved = lexical_path.resolve(strict=True)
    except FileNotFoundError as exc:
        raise RegistryAuthorityError(f"missing_spec:{pack}:{ref}") from exc
    try:
        resolved.relative_to(pack_root)
    except ValueError as exc:
        raise RegistryAuthorityError(f"spec_escape:{pack}:{ref}") from exc
    if resolved != lexical_path.absolute():
        raise RegistryAuthorityError(f"symlink_spec_ref:{pack}:{ref}")
    repo_relative = resolved.relative_to(source_root).as_posix()
    if repo_relative not in (tracked_paths if tracked_paths is not None else _tracked_repo_paths()):
        raise RegistryAuthorityError(f"untracked_spec:{pack}:{ref}")
    try:
        content = resolved.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise RegistryAuthorityError(f"non_utf8_spec:{pack}:{ref}") from exc
    return parse_spec_document(pack, ref, content, repo_path=repo_relative)


def _definition_rows() -> tuple[list[dict[str, Any]], dict[tuple[str, str], dict[str, Any]]]:
    packs_payload = _load_yaml(PACKS_PATH)
    packs: list[dict[str, Any]] = []
    definitions: dict[tuple[str, str], dict[str, Any]] = {}
    tracked_paths = _tracked_repo_paths()
    used_spec_paths: dict[str, tuple[str, str]] = {}
    for pack_row in packs_payload.get("packs") or []:
        pack = str(pack_row.get("key") or "").strip()
        if not pack or any(row["pack"] == pack for row in packs):
            raise RegistryAuthorityError(f"duplicate_or_missing_pack:{pack}")
        planning_scope = str(pack_row.get("planning_scope") or "").strip()
        if planning_scope not in {"general", "specialized"}:
            raise RegistryAuthorityError(f"invalid_pack_planning_scope:{pack}")
        packs.append({"pack": pack, "definitionVersion": 1, "source": dict(pack_row)})
        stage_payload = _load_yaml(ROOT / "schemas" / pack / "stage-substage-map.yaml")
        spec_payload = _load_yaml(ROOT / "schemas" / pack / "substage-spec-map.yaml")
        seen: set[str] = set()
        pack_substages: list[str] = []
        for stage, stage_value in stage_payload.items():
            entries = stage_value.get("substages", []) if isinstance(stage_value, dict) else stage_value
            if not isinstance(entries, list):
                raise RegistryAuthorityError(f"invalid_stage_map:{pack}:{stage}")
            for entry in entries:
                substage = str(entry.get("key") or "").strip()
                if not substage or substage in seen:
                    raise RegistryAuthorityError(f"duplicate_or_missing_definition:{pack}:{substage}")
                seen.add(substage)
                pack_substages.append(substage)
                if substage not in spec_payload:
                    raise RegistryAuthorityError(f"missing_substage_spec:{pack}:{substage}")
                current_version = entry.get("definitionVersion", 1)
                if (
                    not isinstance(current_version, int)
                    or isinstance(current_version, bool)
                    or current_version < 1
                ):
                    raise RegistryAuthorityError(
                        f"invalid_definition_version:{pack}:{substage}"
                    )
                historical_versions = entry.get("historicalVersions") or []
                if not isinstance(historical_versions, list):
                    raise RegistryAuthorityError(
                        f"invalid_definition_history:{pack}:{substage}"
                    )
                current_spec_ref = str(spec_payload[substage])
                version_specs: list[tuple[int, str, str, bool]] = [
                    (current_version, current_spec_ref, current_spec_ref, True)
                ]
                seen_versions = {current_version}
                for historical in historical_versions:
                    if (
                        not isinstance(historical, Mapping)
                        or set(historical) not in {
                            frozenset({"definitionVersion", "specRef"}),
                            frozenset({"definitionVersion", "specRef", "sourceSpecRef"}),
                        }
                    ):
                        raise RegistryAuthorityError(
                            f"invalid_definition_history:{pack}:{substage}"
                        )
                    version = historical.get("definitionVersion")
                    spec_ref = historical.get("specRef")
                    source_spec_ref = historical.get("sourceSpecRef", spec_ref)
                    if (
                        not isinstance(version, int)
                        or isinstance(version, bool)
                        or version < 1
                        or version >= current_version
                        or version in seen_versions
                        or not isinstance(spec_ref, str)
                        or not spec_ref.strip()
                        or not isinstance(source_spec_ref, str)
                        or not source_spec_ref.strip()
                    ):
                        raise RegistryAuthorityError(
                            f"invalid_definition_history:{pack}:{substage}"
                        )
                    seen_versions.add(version)
                    version_specs.append(
                        (
                            version,
                            spec_ref.strip(),
                            source_spec_ref.strip(),
                            False,
                        )
                    )
                enabled = entry.get("enabled", True)
                if not isinstance(enabled, bool):
                    raise RegistryAuthorityError(f"invalid_definition_enabled:{pack}:{substage}")
                raw_instance_policy = entry.get("instancePolicy", "repeatable")
                instance_policy = (
                    {"mode": raw_instance_policy}
                    if isinstance(raw_instance_policy, str)
                    else raw_instance_policy
                )
                if not isinstance(instance_policy, dict) or set(instance_policy) - {
                    "mode",
                    "executionReentry",
                }:
                    raise RegistryAuthorityError(
                        f"invalid_definition_instance_policy:{pack}:{substage}"
                    )
                if instance_policy.get("mode") not in {
                    "repeatable",
                    "singleton_collection",
                }:
                    raise RegistryAuthorityError(
                        f"invalid_definition_instance_policy:{pack}:{substage}"
                    )
                execution_reentry = instance_policy.get("executionReentry")
                if execution_reentry is not None:
                    trigger_ids = (
                        execution_reentry.get("triggerDefinitionIds")
                        if isinstance(execution_reentry, dict)
                        else None
                    )
                    if (
                        instance_policy["mode"] != "repeatable"
                        or set(execution_reentry) != {
                            "policyVersion",
                            "triggerDefinitionIds",
                        }
                        or execution_reentry.get("policyVersion")
                        != "repeatable_definition_reentry_v1"
                        or not isinstance(trigger_ids, list)
                        or not trigger_ids
                        or any(not isinstance(value, str) or not value.strip() for value in trigger_ids)
                        or len(set(trigger_ids)) != len(trigger_ids)
                    ):
                        raise RegistryAuthorityError(
                            f"invalid_definition_execution_reentry:{pack}:{substage}"
                        )
                for version, spec_ref, source_spec_ref, is_current in version_specs:
                    source_spec_contract = load_definition_spec(
                        pack,
                        source_spec_ref,
                        tracked_paths=tracked_paths,
                    )
                    if source_spec_ref == spec_ref:
                        spec_contract = source_spec_contract
                    else:
                        # Preserve the historical frozen spec identity while reading
                        # its immutable content from a separately tracked archive file.
                        spec_contract = parse_spec_document(
                            pack,
                            spec_ref,
                            source_spec_contract["specSource"]["content"],
                        )
                    resolved_identity = spec_contract["specSource"]["repoPath"]
                    owner = used_spec_paths.get(resolved_identity)
                    if owner is not None and owner != (pack, substage):
                        raise RegistryAuthorityError(
                            f"duplicate_spec_ref:{resolved_identity}"
                        )
                    used_spec_paths[resolved_identity] = (pack, substage)
                    key = (
                        (pack, substage)
                        if is_current
                        else (pack, f"{substage}@{version}")
                    )
                    definitions[key] = {
                        "definitionId": f"{pack}:{substage}",
                        "definitionVersion": version,
                        "pack": pack,
                        "stage": stage,
                        "substageKey": substage,
                        "enabled": enabled,
                        "label": entry.get("label", substage),
                        "description": entry.get("description", ""),
                        "instancePolicy": dict(instance_policy),
                        **spec_contract,
                    }
        if set(spec_payload) != seen:
            raise RegistryAuthorityError(f"orphan_substage_spec:{pack}")
        try:
            external_action_contracts = load_definition_external_action_contracts(
                pack,
                {
                    substage: int(
                        definitions[(pack, substage)]["definitionVersion"]
                    )
                    for substage in pack_substages
                },
                repo_root=ROOT,
                tracked_paths=tracked_paths,
            )
        except DefinitionExternalActionSourceError as exc:
            raise RegistryAuthorityError(str(exc)) from exc
        for substage in pack_substages:
            required_actions = external_action_contracts[substage]
            external_action_contract = {
                "status": "known" if required_actions is not None else "unknown",
                "requiredActionKinds": (
                    [action.value for action in required_actions]
                    if required_actions is not None
                    else []
                ),
            }
            for definition in definitions.values():
                if definition["pack"] == pack and definition["substageKey"] == substage:
                    definition["externalActionContract"] = deepcopy(
                        external_action_contract
                    )
    definition_ids = {row["definitionId"] for row in definitions.values()}
    for row in definitions.values():
        execution_reentry = (row.get("instancePolicy") or {}).get("executionReentry")
        if execution_reentry is not None and any(
            trigger_id not in definition_ids
            for trigger_id in execution_reentry["triggerDefinitionIds"]
        ):
            raise RegistryAuthorityError(
                f"unknown_definition_execution_reentry_trigger:{row['definitionId']}"
            )
    return packs, definitions


def _validate_binding_projection_against_skill(
    projection: Any, skill: Mapping[str, Any]
) -> dict[str, Any]:
    """Validate a binding-level scientific_state_projection against the bound
    skill's frozen output schema and return the normalized declaration.

    Both the binding and the skill live in the same snapshot envelope, so
    cross-entity validation is possible here (the skill's frozenSchemas are
    attached to its sourceContract during _skill_rows). Reuses the shared
    projection validator; no new freeze authority.
    """
    from app.modules.scientific_state_fact_projection import (
        validate_scientific_state_projection_declaration,
    )

    if not isinstance(projection, Mapping):
        raise RegistryAuthorityError("skill_binding_projection_invalid")
    source_contract = skill.get("sourceContract")
    frozen_schemas = (
        source_contract.get("frozenSchemas") if isinstance(source_contract, Mapping) else None
    )
    output_schema = (
        frozen_schemas.get("output", {}).get("schema")
        if isinstance(frozen_schemas, Mapping)
        else None
    )
    if not isinstance(output_schema, Mapping):
        raise RegistryAuthorityError(
            f"skill_binding_projection_schema_unavailable:{skill.get('skillId')}"
        )
    try:
        return validate_scientific_state_projection_declaration(
            projection, output_schema=output_schema
        )
    except ValueError as exc:
        raise RegistryAuthorityError("skill_binding_projection_invalid") from exc


def _skill_rows(
    definitions: Mapping[tuple[str, str], dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, dict[str, Any]]]:
    # Registry owns capability facts. Definition→Skill usage identity comes only
    # from the canonical Binding v2 source.
    from app.execution.durable_skill_runtime import (
        build_frozen_skill_schema_contract,
        freeze_registered_source_contract,
        registered_skill_capability_contract,
        list_registered_durable_skills,
    )

    durable_catalog = {
        str(item.get("skillId") or "").strip(): bool(item.get("durableExecutable"))
        for item in list_registered_durable_skills()
    }
    payload = _load_yaml(SKILLS_PATH)
    skill_definitions: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    binding_enabled_by_skill: dict[str, bool] = {}
    conditional_policies: dict[str, dict[str, Any]] = {}

    for row in payload.get("skills") or []:
        skill_id = str(row.get("skill_id") or "").strip()
        if not skill_id or skill_id in by_id:
            raise RegistryAuthorityError(f"duplicate_or_missing_skill:{skill_id}")
        enabled = row.get("enabled", True)
        if not isinstance(enabled, bool):
            raise RegistryAuthorityError(f"invalid_skill_enabled:{skill_id}")
        durable_executable = durable_catalog.get(skill_id, False)
        registry_status = str(row.get("status") or "").strip()
        if registry_status not in {"draft", "ready"}:
            raise RegistryAuthorityError(f"invalid_skill_status:{skill_id}")

        raw_conditional_policy = row.get("conditional_binding")
        if raw_conditional_policy is not None:
            if not isinstance(raw_conditional_policy, Mapping):
                raise RegistryAuthorityError(
                    f"invalid_skill_conditional_binding:{skill_id}"
                )
            conditional_policy = {
                "policyVersion": raw_conditional_policy.get("policy_version"),
                "evaluatorRef": raw_conditional_policy.get("evaluator_ref"),
                "requiredRuntimeFactRefs": list(
                    raw_conditional_policy.get("required_runtime_fact_refs") or []
                ),
            }
            try:
                validate_conditional_policy(conditional_policy)
            except ValueError as exc:
                raise RegistryAuthorityError(
                    f"invalid_skill_conditional_binding:{skill_id}"
                ) from exc
            conditional_policies[skill_id] = conditional_policy

        raw_method_roles = row.get("execution_method_roles") or []
        if (
            not isinstance(raw_method_roles, list)
            or any(not isinstance(value, str) or not value.strip() for value in raw_method_roles)
            or len(set(raw_method_roles)) != len(raw_method_roles)
        ):
            raise RegistryAuthorityError(f"invalid_skill_execution_method_roles:{skill_id}")
        source_contract = {
            key: value
            for key, value in row.items()
            if key not in {"display_name", "owner", "fixture_status", "notes", "conditional_binding"}
        }
        try:
            source_contract = freeze_registered_source_contract(
                source_contract,
                skill_path=str(row.get("skill_path") or ""),
            )
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError, yaml.YAMLError) as exc:
            raise RegistryAuthorityError(
                f"skill_registration_contract_unavailable:{skill_id}"
            ) from exc
        try:
            input_contract, output_contract, judgment_support = (
                registered_skill_capability_contract(source_contract)
            )
        except ValueError as exc:
            raise RegistryAuthorityError(
                f"skill_capability_contract_invalid:{skill_id}:{exc}"
            ) from exc
        definition = {
            "skillId": skill_id,
            "definitionVersion": 1,
            "status": registry_status,
            "enabled": enabled,
            "runtime": row.get("runtime"),
            "skillPath": row.get("skill_path"),
            "skillType": row.get("skill_type"),
            "riskPolicy": {"riskClass": None, "source": "canonical_registry_unspecified"},
            "sourceContract": source_contract,
            "inputContract": input_contract,
            "outputContract": output_contract,
            "judgmentSupport": judgment_support,
        }
        if raw_method_roles:
            definition["executionMethodRoles"] = list(raw_method_roles)
        by_id[skill_id] = definition
        skill_definitions.append(definition)
        binding_enabled_by_skill[skill_id] = bool(
            enabled and registry_status == "ready" and durable_executable
        )

    try:
        declared_bindings = load_skill_binding_source_rows()
    except SkillBindingSourceError as exc:
        raise RegistryAuthorityError(str(exc)) from exc

    definition_by_ref = {
        (row["definitionId"], row["definitionVersion"]): row
        for row in definitions.values()
    }
    bindings: list[dict[str, Any]] = []
    seen_refs: set[tuple[str, int]] = set()
    for declared in declared_bindings:
        definition_id = declared["definitionId"]
        skill_id = declared["skillId"]
        definition = definition_by_ref.get(
            (definition_id, declared["definitionVersion"])
        )
        skill = by_id.get(skill_id)
        if definition is None:
            raise RegistryAuthorityError(
                f"skill_binding_definition_unknown:{definition_id}"
            )
        if skill is None:
            raise RegistryAuthorityError(f"skill_binding_skill_unknown:{skill_id}")
        if declared["skillDefinitionVersion"] != int(skill["definitionVersion"]):
            raise RegistryAuthorityError(
                f"skill_binding_source_identity_mismatch:{declared['bindingId']}:skillDefinitionVersion"
            )
        ref = (declared["bindingId"], declared["bindingVersion"])
        if ref in seen_refs:
            raise RegistryAuthorityError("duplicate_declared_skill_binding")
        seen_refs.add(ref)
        binding = {
            "bindingId": declared["bindingId"],
            "bindingVersion": declared["bindingVersion"],
            "definitionId": definition_id,
            "definitionVersion": declared["definitionVersion"],
            "skillId": skill_id,
            "skillDefinitionVersion": declared["skillDefinitionVersion"],
            "supportedRole": "tool",
            "enabled": binding_enabled_by_skill[skill_id],
            "sourceAuthority": "skill_binding_source",
        }
        conditional_policy = conditional_policies.get(skill_id)
        if conditional_policy is not None:
            binding["conditionalPolicy"] = dict(conditional_policy)
        if "inputSources" in declared:
            binding["inputSources"] = dict(declared["inputSources"])
        if "runtimeInputs" in declared:
            binding["runtimeInputs"] = dict(declared["runtimeInputs"])
        if "scientificStateProjection" in declared:
            # Shape was validated by the loader; the cross-entity source_field
            # check against the skill's frozen output schema is deferred to a
            # second pass after all handoff frozenSchemas are attached below.
            binding["scientificStateProjection"] = dict(declared["scientificStateProjection"])
        bindings.append(binding)

    try:
        handoff_skill_ids = skill_ids_requiring_frozen_handoff_schemas()
    except SkillBindingSourceError as exc:
        raise RegistryAuthorityError(str(exc)) from exc
    for skill_id in sorted(handoff_skill_ids):
        definition = by_id.get(skill_id)
        if definition is None:
            raise RegistryAuthorityError(f"skill_binding_schema_skill_unknown:{skill_id}")
        try:
            frozen = build_frozen_skill_schema_contract(str(definition.get("skillPath") or ""))
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            raise RegistryAuthorityError(f"skill_binding_schema_unavailable:{skill_id}") from exc
        source_contract = definition.get("sourceContract")
        if not isinstance(source_contract, dict):
            raise RegistryAuthorityError(f"skill_source_contract_invalid:{skill_id}")
        source_contract["frozenSchemas"] = frozen

    for binding in bindings:
        projection = binding.get("scientificStateProjection")
        if projection is None:
            continue
        skill = by_id.get(binding["skillId"])
        if skill is None:
            raise RegistryAuthorityError(
                f"skill_binding_projection_skill_unknown:{binding['bindingId']}"
            )
        binding["scientificStateProjection"] = _validate_binding_projection_against_skill(
            projection, skill
        )

    definition_enabled_by_ref = {
        (row["definitionId"], row["definitionVersion"]): bool(row["enabled"])
        for row in definitions.values()
    }
    for binding in bindings:
        definition_ref = (
            binding["definitionId"],
            binding["definitionVersion"],
        )
        if definition_ref not in definition_enabled_by_ref:
            raise RegistryAuthorityError(
                f"skill_binding_definition_unknown:{binding['bindingId']}"
            )
        binding["enabled"] = bool(
            binding["enabled"]
            and definition_enabled_by_ref[definition_ref]
        )

    _propagate_required_artifact_binding_availability(bindings)
    return skill_definitions, bindings, by_id


def _propagate_required_artifact_binding_availability(
    bindings: list[dict[str, Any]],
) -> None:
    # Base availability is publication status + runtime readiness.
    # A required Artifact dependency propagates unavailability downstream.
    # Optional Artifact sources never disable the target binding.
    # Unknown/self-referential source bindings remain structural errors.
    by_ref = {
        (row["bindingId"], row["bindingVersion"]): row
        for row in bindings
    }
    if len(by_ref) != len(bindings):
        raise RegistryAuthorityError("duplicate_generated_skill_binding")

    for row in bindings:
        row_ref = (row["bindingId"], row["bindingVersion"])
        input_sources = row.get("inputSources")
        if not isinstance(input_sources, Mapping):
            continue
        for entry in input_sources.get("fields") or []:
            source = entry.get("source")
            if not isinstance(source, Mapping) or source.get("kind") != "artifact":
                continue
            source_ref = (source.get("bindingId"), source.get("bindingVersion"))
            if source_ref not in by_ref:
                raise RegistryAuthorityError(
                    f"skill_binding_artifact_source_unknown:{row['bindingId']}:{source.get('bindingId')}"
                )
            if source_ref == row_ref:
                raise RegistryAuthorityError(
                    f"skill_binding_artifact_source_self_reference:{row['bindingId']}"
                )

    changed = True
    while changed:
        changed = False
        for row in bindings:
            if not row.get("enabled"):
                continue
            input_sources = row.get("inputSources")
            if not isinstance(input_sources, Mapping):
                continue
            for entry in input_sources.get("fields") or []:
                if entry.get("sourcePresence") != "required":
                    continue
                source = entry.get("source")
                if not isinstance(source, Mapping) or source.get("kind") != "artifact":
                    continue
                source_ref = (source["bindingId"], source["bindingVersion"])
                if not by_ref[source_ref].get("enabled"):
                    row["enabled"] = False
                    changed = True
                    break

def _attach_opentrajectory_skill_compositions(
    definitions: Mapping[tuple[str, str], dict[str, Any]],
    skills: Mapping[str, dict[str, Any]],
    skill_bindings: list[dict[str, Any]],
) -> None:
    # Freeze canonical OT L2 -> ordered required L3 Skill composition.
    # Membership and order both come from the canonical OT workflow map.
    # Registry contributes only Skill capability facts.
    payload = _load_yaml(OPENTRAJECTORY_SKILL_MAP_PATH)
    bindings_by_definition_skill = {
        (row["definitionId"], row["skillId"]): row
        for row in skill_bindings
    }

    for substage, entries in payload.items():
        target = definitions.get(("opentrajectory", str(substage)))
        if target is None:
            raise RegistryAuthorityError(
                f"opentrajectory_composition_definition_unknown:{substage}"
            )
        if not isinstance(entries, list) or not entries:
            raise RegistryAuthorityError(
                f"opentrajectory_composition_empty:{substage}"
            )

        required_refs: list[dict[str, Any]] = []
        seen: set[str] = set()

        for entry in entries:
            if not isinstance(entry, Mapping):
                raise RegistryAuthorityError(
                    f"opentrajectory_composition_entry_invalid:{substage}"
                )

            skill_id = str(entry.get("skill_id") or "").strip()
            if not skill_id or skill_id in seen:
                raise RegistryAuthorityError(
                    f"opentrajectory_composition_skill_invalid:{substage}:{skill_id}"
                )
            seen.add(skill_id)

            skill = skills.get(skill_id)
            if skill is None:
                raise RegistryAuthorityError(
                    f"opentrajectory_composition_skill_unknown:{substage}:{skill_id}"
                )
            if skill.get("skillType") != "atomic":
                raise RegistryAuthorityError(
                    f"opentrajectory_composition_skill_type_mismatch:{substage}:{skill_id}"
                )
            if (
                target["definitionId"],
                skill_id,
            ) not in bindings_by_definition_skill:
                raise RegistryAuthorityError(
                    f"opentrajectory_composition_binding_missing:{substage}:{skill_id}"
                )

            required_refs.append(
                {
                    "skillId": skill_id,
                    "definitionVersion": int(skill["definitionVersion"]),
                }
            )

        target["executionComposition"] = {
            "workflowId": "opentrajectory_research_workflow",
            "skillPack": "opentrajectory",
            "compositionVersion": 1,
            "semantics": "ordered_required",
            "sourceAuthority": "schemas/opentrajectory/substage-skill-map.yaml",
            "requiredSkillRefs": required_refs,
        }



def _attach_cs_reproduction_skill_compositions(
    definitions: Mapping[tuple[str, str], dict[str, Any]],
    skills: Mapping[str, dict[str, Any]],
    skill_bindings: list[dict[str, Any]],
) -> None:
    """Freeze canonical CS reproduction research-work -> ordered L3 composition."""
    payload = _load_yaml(CS_REPRODUCTION_SKILL_MAP_PATH)
    bindings = {
        (row["definitionId"], row["definitionVersion"], row["skillId"])
        for row in skill_bindings
    }
    for substage, entries in payload.items():
        targets = [
            row
            for row in definitions.values()
            if row["pack"] == "cs_reproduction"
            and row["substageKey"] == str(substage)
        ]
        if not targets or not isinstance(entries, list) or not entries:
            raise RegistryAuthorityError(f"cs_reproduction_composition_invalid:{substage}")
        for target in targets:
            refs: list[dict[str, Any]] = []
            seen: set[str] = set()
            for entry in entries:
                if not isinstance(entry, Mapping):
                    raise RegistryAuthorityError(
                        f"cs_reproduction_composition_entry_invalid:{substage}"
                    )
                skill_id = str(entry.get("skill_id") or "").strip()
                skill = skills.get(skill_id)
                if (
                    not skill_id
                    or skill_id in seen
                    or skill is None
                    or skill.get("skillType") != "atomic"
                    or (
                        target["definitionId"],
                        target["definitionVersion"],
                        skill_id,
                    )
                    not in bindings
                ):
                    raise RegistryAuthorityError(
                        f"cs_reproduction_composition_skill_invalid:{substage}:{skill_id}"
                    )
                seen.add(skill_id)
                refs.append({
                    "skillId": skill_id,
                    "definitionVersion": int(skill["definitionVersion"]),
                })
            target["executionComposition"] = {
                "workflowId": "cs_paper_reproduction_workflow",
                "skillPack": "cs_reproduction",
                "compositionVersion": 1,
                "semantics": "ordered_required",
                "sourceAuthority": "schemas/cs_reproduction/substage-skill-map.yaml",
                "requiredSkillRefs": refs,
            }

def _attach_materials_workflow_skill_compositions(
    definitions: Mapping[tuple[str, str], dict[str, Any]],
    skills: Mapping[str, dict[str, Any]],
    skill_bindings: list[dict[str, Any]],
) -> None:
    """Freeze the two atomic skills admitted by the materials workflow."""
    payload = _load_yaml(MATERIALS_WORKFLOW_SKILL_MAP_PATH)
    bindings = {(row["definitionId"], row["skillId"]) for row in skill_bindings}
    for substage, skill_ids in payload.items():
        target = definitions.get(("materials", str(substage)))
        if target is None or not isinstance(skill_ids, list) or len(skill_ids) != 1:
            raise RegistryAuthorityError(f"materials_workflow_composition_invalid:{substage}")
        skill_id = str(skill_ids[0]).strip()
        skill = skills.get(skill_id)
        if (
            not skill_id
            or skill is None
            or skill.get("skillType") != "atomic"
            or (target["definitionId"], skill_id) not in bindings
        ):
            raise RegistryAuthorityError(f"materials_workflow_composition_skill_invalid:{substage}:{skill_id}")
        target["executionComposition"] = {
            "workflowId": "materials_candidate_discovery_workflow",
            "skillPack": "materials",
            "compositionVersion": 1,
            "semantics": "ordered_required",
            "sourceAuthority": "schemas/materials/candidate-discovery-workflow-skill-map.yaml",
            "requiredSkillRefs": [{"skillId": skill_id, "definitionVersion": int(skill["definitionVersion"])}],
        }


def _attach_materials_full_discovery_workflow_skill_compositions(
    definitions: Mapping[tuple[str, str], dict[str, Any]],
    skills: Mapping[str, dict[str, Any]],
    skill_bindings: list[dict[str, Any]],
) -> None:
    """Freeze the full-chain materials discovery workflow node -> skill composition.

    Unlike the bounded candidate-discovery prefix (exactly one skill per
    substage), the full-discovery workflow admits multiple L3 skills per
    substage; the L2 orchestration selects among them at execution time.
    """
    payload = _load_yaml(MATERIALS_FULL_DISCOVERY_SKILL_MAP_PATH)
    bindings = {(row["definitionId"], row["skillId"]) for row in skill_bindings}
    for substage, skill_ids in payload.items():
        target = definitions.get(("materials", str(substage)))
        if target is None or not isinstance(skill_ids, list) or not skill_ids:
            raise RegistryAuthorityError(f"materials_full_discovery_composition_invalid:{substage}")
        refs: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw_skill_id in skill_ids:
            skill_id = str(raw_skill_id or "").strip()
            skill = skills.get(skill_id)
            if (
                not skill_id
                or skill_id in seen
                or skill is None
                or skill.get("skillType") != "atomic"
                or (target["definitionId"], skill_id) not in bindings
            ):
                raise RegistryAuthorityError(
                    f"materials_full_discovery_composition_skill_invalid:{substage}:{skill_id}"
                )
            seen.add(skill_id)
            refs.append({"skillId": skill_id, "definitionVersion": int(skill["definitionVersion"])})
        target["executionComposition"] = {
            "workflowId": "materials_full_discovery_workflow",
            "skillPack": "materials",
            "compositionVersion": 1,
            "semantics": "ordered_required",
            "sourceAuthority": "schemas/materials/full-discovery-workflow-skill-map.yaml",
            "requiredSkillRefs": refs,
        }


def _attach_pvd_workflow_skill_compositions(
    definitions: Mapping[tuple[str, str], dict[str, Any]],
    skills: Mapping[str, dict[str, Any]],
    skill_bindings: list[dict[str, Any]],
) -> None:
    """Freeze the approved PVD node -> ordered required Skill composition."""
    payload = _load_yaml(PVD_WORKFLOW_SKILL_MAP_PATH)
    bindings = {(row["definitionId"], row["skillId"]) for row in skill_bindings}
    for substage, skill_ids in payload.items():
        target = definitions.get(("pvd", str(substage)))
        if target is None or not isinstance(skill_ids, list) or not skill_ids:
            raise RegistryAuthorityError(f"pvd_workflow_composition_invalid:{substage}")
        refs: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw_skill_id in skill_ids:
            skill_id = str(raw_skill_id or "").strip()
            skill = skills.get(skill_id)
            if (
                not skill_id
                or skill_id in seen
                or skill is None
                or skill.get("skillType") != "atomic"
                or (target["definitionId"], skill_id) not in bindings
            ):
                raise RegistryAuthorityError(
                    f"pvd_workflow_composition_skill_invalid:{substage}:{skill_id}"
                )
            seen.add(skill_id)
            refs.append({"skillId": skill_id, "definitionVersion": int(skill["definitionVersion"])})
        target["executionComposition"] = {
            "workflowId": "pvd_process_optimization_workflow",
            "skillPack": "pvd",
            "compositionVersion": 1,
            "semantics": "ordered_required",
            "sourceAuthority": "schemas/pvd/substage-skill-map.yaml",
            "requiredSkillRefs": refs,
        }


def _agent_rows(
    definitions: Mapping[tuple[str, str], dict[str, Any]],
    skills: Mapping[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    payload = load_agent_registry_payload()
    report = validate_agent_registry_payload(payload)
    if not report.valid:
        raise RegistryAuthorityError(str(report.to_dict()))
    agent_definitions: list[dict[str, Any]] = []
    agent_bindings: list[dict[str, Any]] = []
    grants: list[dict[str, Any]] = []
    general_agents: list[dict[str, Any]] = []
    for row in payload["agents"]:
        agent_id = row["agentId"]
        output_contract = dict(row["outputContract"])
        output_schema_ref = str(output_contract.get("artifactSchema") or "").strip()
        if output_schema_ref:
            output_contract["frozenArtifactContract"] = load_frozen_artifact_contract(
                str(output_contract["artifactContract"]),
                output_schema_ref,
            ).snapshot()
        review_contract = None
        if isinstance(row.get("reviewContract"), dict):
            review_contract = dict(row["reviewContract"])
            review_contract["frozenArtifactContract"] = load_frozen_artifact_contract(
                str(review_contract["contractId"]),
                str(review_contract["schemaRef"]),
            ).snapshot()
        agent_definition = {
                "agentId": agent_id,
                "definitionVersion": 1,
                "agentKind": row["agentKind"],
                "candidateScope": row["candidateScope"],
                "enabled": row["enabled"],
                "visibility": row["visibility"],
                "aliases": row.get("aliases") or [],
                "capability": row["capability"],
                "notFor": row["notFor"],
                "inputContract": row["inputContract"],
                "outputContract": output_contract,
                **({"reviewContract": review_contract} if review_contract is not None else {}),
                "failClosedRules": row["failClosedRules"],
                "budgetPolicy": row["budgetPolicy"],
                "evaluator": row["evaluator"],
                "allowedProviders": row["allowedProviders"],
                "defaultProvider": row["defaultProvider"],
                "judgmentSupport": row["judgmentSupport"],
                "toolRequirements": row.get("toolRequirements") or {},
            }
        method_roles = list(row.get("executionMethodRoles") or [])
        if method_roles:
            agent_definition["executionMethodRoles"] = method_roles
        agent_definitions.append(agent_definition)
        if row["agentKind"] == "general_research" and row["candidateScope"] == "all_enabled_substages" and row["enabled"]:
            general_agents.append(row)
        for mapping in row["sdr"]["mappings"]:
            target = definitions.get((mapping["pack"], mapping["substage"]))
            if target is None or target["stage"] != mapping["stage"]:
                raise RegistryAuthorityError(f"agent_binding_stage_mismatch:{agent_id}")
            agent_bindings.append(
                {
                    "bindingId": f"bind:{mapping['pack']}:{mapping['substage']}:agent:{agent_id}:{mapping['supportedRole']}",
                    "bindingVersion": target["definitionVersion"],
                    "definitionId": target["definitionId"],
                    "definitionVersion": target["definitionVersion"],
                    "agentId": agent_id,
                    "agentDefinitionVersion": 1,
                    "supportedRole": mapping["supportedRole"],
                    "enabled": mapping["enabled"],
                    "artifactContract": mapping["artifactContract"],
                    "sourceAuthority": "agent_registry_mapping",
                }
            )
        for skill_id in row["skillIds"]:
            skill = skills.get(skill_id)
            if skill is None or skill["status"] != "ready" or not skill["enabled"]:
                raise RegistryAuthorityError(f"invalid_agent_skill_grant:{agent_id}:{skill_id}")
            grants.append(
                {
                    "bindingId": f"grant:{agent_id}:{skill_id}",
                    "bindingVersion": 1,
                    "agentId": agent_id,
                    "agentDefinitionVersion": 1,
                    "skillId": skill_id,
                    "skillDefinitionVersion": 1,
                    "enabled": True,
                    "sourceAuthority": "agent_registry_skill_grant",
                }
            )
    if not general_agents:
        raise RegistryAuthorityError("general_fallback_policy_missing")
    for target in definitions.values():
        if not target["enabled"]:
            continue
        for row in general_agents:
            agent_id = row["agentId"]
            agent_bindings.append(
                {
                    "bindingId": f"bind:{target['definitionId']}:agent:{agent_id}:primary",
                    "bindingVersion": target["definitionVersion"],
                    "definitionId": target["definitionId"],
                    "definitionVersion": target["definitionVersion"],
                    "agentId": agent_id,
                    "agentDefinitionVersion": 1,
                    "supportedRole": "primary",
                    "enabled": True,
                    "sourceAuthority": "general_fallback_policy",
                }
            )
    return agent_definitions, agent_bindings, grants


def _registry_static_policies() -> dict[str, Any]:
    return {
            "policyVersion": POLICY_VERSION,
            "unknownCanonicalRiskClass": "high",
            "unboundSkillPolicy": "not_applicable",
            "providerReadinessSource": "resolution_facts_only",
            # This is registry authority, not a domain-service special case.
            # A frozen task can use it only for the explicitly named canonical
            # semantic gap and only through the normal resolver afterwards.
            "systemRecoveryAuthorities": [{
                "reasonCode": "semantic_material_insufficient",
                "definitionId": "opentrajectory:progressive_reading",
                "objective": "Recover source material needed by the blocked research step.",
                "expectedArtifactTypes": ["source_materials"],
            }],
        }

def build_planning_registry_snapshot() -> RegistrySnapshot:
    """Freeze research requirements from their canonical source, without capability I/O."""
    packs, definitions = _definition_rows()
    return snapshot_from_envelope({
        "schemaVersion": 2,
        "scope": {"kind": "research_planning"},
        "definitions": {"packs": packs, "substages": list(definitions.values()), "skills": [], "agents": []},
        "bindings": {"skills": [], "agents": [], "agentSkills": []},
        "recipes": [],
        "staticPolicies": _registry_static_policies(),
    })


def _reachable_skill_bindings(
    bindings: list[dict[str, Any]],
    *,
    selected_definition_refs: set[tuple[str, int]],
) -> list[dict[str, Any]]:
    """Resolve the exact Binding closure reachable from selected Definitions."""
    by_ref = {
        (row["bindingId"], row["bindingVersion"]): row
        for row in bindings
    }
    reachable = {
        ref
        for ref, row in by_ref.items()
        if (row["definitionId"], row["definitionVersion"])
        in selected_definition_refs
    }
    pending = list(reachable)
    while pending:
        row = by_ref[pending.pop()]
        for entry in (row.get("inputSources") or {}).get("fields", []):
            source = entry.get("source") or {}
            if source.get("kind") != "artifact":
                continue
            source_ref = (source.get("bindingId"), source.get("bindingVersion"))
            if source_ref not in by_ref:
                raise RegistryAuthorityError(
                    f"skill_binding_artifact_source_unknown:{row['bindingId']}:{source.get('bindingId')}"
                )
            if source_ref not in reachable:
                reachable.add(source_ref)
                pending.append(source_ref)
    return [
        row
        for row in bindings
        if (row["bindingId"], row["bindingVersion"]) in reachable
    ]


def build_execution_registry_snapshot(
    planning_envelope: Mapping[str, Any],
    *,
    required_definition_refs: tuple[tuple[str, int], ...],
) -> RegistrySnapshot:
    """Bind current executable capabilities to the unchanged, confirmed research contract."""
    planning_ref = registry_envelope_ref(planning_envelope)
    if planning_envelope.get("scope") != {"kind": "research_planning"}:
        raise RegistryAuthorityError("execution_requires_planning_snapshot")
    # The execution route already carries the exact Definition identities selected
    # by the confirmed design.  Build from the same canonical Registry authority,
    # but do not admit the global catalog before that route scope is applied:
    # historical Bindings remain available to catalog checks and legacy routes,
    # while an unrelated historical handoff cannot reject this execution snapshot.
    current = _build_registry_envelope()
    current_definitions = {
        (row["definitionId"], row["definitionVersion"]): row
        for row in current["definitions"]["substages"]
    }
    frozen_definitions = deepcopy(planning_envelope["definitions"]["substages"])
    selected_definition_refs = set(required_definition_refs)
    compatible: set[tuple[str, int]] = set()
    for row in frozen_definitions:
        identity = (row["definitionId"], row["definitionVersion"])
        if identity not in selected_definition_refs:
            continue
        present = current_definitions.get(identity)
        if present is None:
            continue
        research_contract = {
            key: value
            for key, value in present.items()
            if key != "executionComposition"
        }
        if canonicalize_registry_value(research_contract) != row:
            continue
        compatible.add(identity)
        if "executionComposition" in present:
            row["executionComposition"] = deepcopy(present["executionComposition"])
    if not set(required_definition_refs) <= compatible:
        raise RegistryAuthorityError("execution_definition_contract_changed_since_planning")
    envelope = deepcopy(dict(planning_envelope))
    envelope["scope"] = {"kind": "execution", "planningSnapshotRef": planning_ref}
    envelope["definitions"]["substages"] = frozen_definitions
    for key in ("skills", "agents"):
        envelope["definitions"][key] = deepcopy(current["definitions"][key])
    envelope["bindings"] = {
        key: [deepcopy(row) for row in current["bindings"][key]
              if (row["definitionId"], row["definitionVersion"]) in compatible]
        for key in ("skills", "agents")
    }
    envelope["bindings"]["skills"] = _reachable_skill_bindings(
        current["bindings"]["skills"],
        selected_definition_refs=compatible,
    )
    retained_skill_refs = {
        (
            row["definitionId"],
            row["definitionVersion"],
            row["skillId"],
            row["skillDefinitionVersion"],
        )
        for row in envelope["bindings"]["skills"]
    }
    for row in frozen_definitions:
        composition = row.get("executionComposition")
        if composition is None:
            continue
        definition_ref = (row["definitionId"], row["definitionVersion"])
        if any(
            (*definition_ref, ref["skillId"], ref["definitionVersion"])
            not in retained_skill_refs
            for ref in composition["requiredSkillRefs"]
        ):
            row.pop("executionComposition")
    # The existing general-agent policy applies to every frozen enabled research
    # requirement. It does not replace specialized bindings whose contract changed.
    fallback_ids = {
        (row["agentId"], row["definitionVersion"])
        for row in current["definitions"]["agents"]
        if row["agentKind"] == "general_research"
        and row["candidateScope"] == "all_enabled_substages" and row["enabled"]
    }
    for row in frozen_definitions:
        if not row["enabled"] or (row["definitionId"], row["definitionVersion"]) in compatible:
            continue
        for agent_id, agent_version in sorted(fallback_ids):
            envelope["bindings"]["agents"].append({
                "bindingId": f"bind:{row['definitionId']}:agent:{agent_id}:primary",
                "bindingVersion": row["definitionVersion"], "definitionId": row["definitionId"],
                "definitionVersion": row["definitionVersion"], "agentId": agent_id,
                "agentDefinitionVersion": agent_version, "supportedRole": "primary",
                "enabled": True, "sourceAuthority": "general_fallback_policy",
            })
    envelope["bindings"]["agentSkills"] = deepcopy(current["bindings"]["agentSkills"])
    envelope["recipes"] = [
        deepcopy(row) for row in current["recipes"]
        if (row["substageDefinitionId"], row["substageDefinitionVersion"]) in compatible
    ]
    return snapshot_from_envelope(envelope)

def _build_registry_envelope(
    *, consistencyAudit: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    # Audit material can reject a known conflict, but it never contributes
    # identity, bindings, policy, serialization, or the content hash.
    if consistencyAudit and any(value == "canonical_conflict" for value in consistencyAudit.values()):
        raise RegistryAuthorityError("consistency_audit_conflict")
    packs, definitions = _definition_rows()
    skill_definitions, skill_bindings, skill_by_id = _skill_rows(definitions)
    _attach_opentrajectory_skill_compositions(definitions, skill_by_id, skill_bindings)
    _attach_materials_workflow_skill_compositions(definitions, skill_by_id, skill_bindings)
    _attach_materials_full_discovery_workflow_skill_compositions(definitions, skill_by_id, skill_bindings)
    _attach_pvd_workflow_skill_compositions(definitions, skill_by_id, skill_bindings)
    _attach_cs_reproduction_skill_compositions(definitions, skill_by_id, skill_bindings)
    agent_definitions, agent_bindings, grants = _agent_rows(definitions, skill_by_id)
    recipes = load_multi_agent_recipe_registry().list_recipes()
    definition_identities = {
        (row["definitionId"], row["definitionVersion"]) for row in definitions.values()
    }
    for recipe in recipes:
        if (recipe.substage_definition_id, recipe.substage_definition_version) not in definition_identities:
            raise RegistryAuthorityError(f"recipe_definition_unknown:{recipe.recipe_id}")
    return {
        "schemaVersion": SCHEMA_VERSION,
        "definitions": {
            "packs": packs,
            "substages": list(definitions.values()),
            "skills": skill_definitions,
            "agents": agent_definitions,
        },
        "bindings": {"skills": skill_bindings, "agents": agent_bindings, "agentSkills": grants},
        "recipes": [recipe.model_dump(mode="json", by_alias=True) for recipe in recipes],
        "staticPolicies": _registry_static_policies(),
    }


def build_registry_snapshot(
    *, consistencyAudit: Mapping[str, Any] | None = None,
) -> RegistrySnapshot:
    """Build and globally admit the complete Registry catalog."""
    return snapshot_from_envelope(
        _build_registry_envelope(consistencyAudit=consistencyAudit)
    )


def _candidate_id(candidate_ref: AgentRef | SkillRef | MultiAgentRecipeRef) -> str:
    if isinstance(candidate_ref, AgentRef):
        return f"agent:{candidate_ref.agent_id}:{candidate_ref.definition_version}"
    if isinstance(candidate_ref, SkillRef):
        return f"skill:{candidate_ref.skill_id}:{candidate_ref.definition_version}"
    return f"recipe:{candidate_ref.recipe_id}:{candidate_ref.recipe_version}"


def _binding_refs(rows: list[dict[str, Any]]) -> list[BindingRef]:
    identities = sorted({(row["bindingId"], row["bindingVersion"]) for row in rows})
    return [BindingRef(bindingId=binding_id, bindingVersion=version) for binding_id, version in identities]


class ResearchOrchestrationResolver:
    def __init__(self, snapshot_repository: RegistrySnapshotArchive) -> None:
        self._snapshots = snapshot_repository

    def resolve(
        self,
        *,
        design: ResearchDesignVersion,
        designRef: ResearchDesignRef,
        instanceRef: InstanceRef,
        registrySnapshotRef: RegistrySnapshotRef,
        facts: ResolutionFacts,
    ) -> ResolutionResult:
        snapshot = self._snapshots.get(registrySnapshotRef.content_hash)
        if snapshot.schema_version != registrySnapshotRef.schema_version:
            raise ResolutionRejectedError("snapshot_schema_mismatch")
        if design.research_design_id != designRef.research_design_id or design.version != designRef.version:
            raise ResolutionRejectedError("design_ref_mismatch")
        instance = next(
            (
                row
                for row in design.instances
                if row.substage_instance_id == instanceRef.substage_instance_id
                and row.instance_revision == instanceRef.instance_revision
            ),
            None,
        )
        if instance is None:
            raise ResolutionRejectedError("unknown_instance")

        envelope = snapshot.envelope
        substages = envelope["definitions"]["substages"]
        matching_id = [row for row in substages if row["definitionId"] == instance.substage_definition_id]
        if not matching_id:
            raise ResolutionRejectedError("definition_unknown")
        definition = next(
            (row for row in matching_id if row["definitionVersion"] == instance.substage_definition_version),
            None,
        )
        if definition is None:
            raise ResolutionRejectedError("definition_version_mismatch")
        if definition["stage"] != instance.stage_key.value:
            raise ResolutionRejectedError("definition_stage_mismatch")
        if not definition["enabled"]:
            raise ResolutionRejectedError("definition_disabled")

        agent_definitions = {row["agentId"]: row for row in envelope["definitions"]["agents"]}
        skill_definitions = {row["skillId"]: row for row in envelope["definitions"]["skills"]}
        definition_identity = (definition["definitionId"], definition["definitionVersion"])
        skill_bindings = [
            row for row in envelope["bindings"]["skills"]
            if (row["definitionId"], row["definitionVersion"]) == definition_identity
        ]
        agent_bindings = [
            row for row in envelope["bindings"]["agents"]
            if (row["definitionId"], row["definitionVersion"]) == definition_identity
        ]
        fallback_bindings = [
            row for row in agent_bindings
            if row.get("sourceAuthority") == "general_fallback_policy"
            and row["enabled"]
            and (agent_definitions.get(row["agentId"]) or {}).get("agentKind") == "general_research"
            and (agent_definitions.get(row["agentId"]) or {}).get("candidateScope") == "all_enabled_substages"
            and (agent_definitions.get(row["agentId"]) or {}).get("enabled")
        ]
        if not fallback_bindings:
            raise ResolutionRejectedError("general_fallback_missing")

        try:
            facts = ResolutionFacts.model_validate(facts.model_dump(mode="python", by_alias=True))
        except Exception as exc:
            raise ResolutionRejectedError("invalid_resolution_facts", str(exc)) from exc
        if facts.policy_version != envelope["staticPolicies"]["policyVersion"]:
            raise ResolutionRejectedError("policy_version_mismatch")
        applicable_skill_ids = {
            _candidate_id(SkillRef(skillId=row["skillId"], definitionVersion=row["skillDefinitionVersion"]))
            for row in skill_bindings
            if row["enabled"]
            and (skill_definitions.get(row["skillId"]) or {}).get("enabled")
            and (skill_definitions.get(row["skillId"]) or {}).get("status") == "ready"
        }
        applicable_agent_rows = [
            row for row in agent_bindings
            if row["enabled"]
            and (agent_definitions.get(row["agentId"]) or {}).get("enabled")
            and not (
                (agent_definitions.get(row["agentId"]) or {}).get("agentKind") == "general_research"
                and row.get("sourceAuthority") != "general_fallback_policy"
            )
        ]
        applicable_agent_ids = {
            _candidate_id(AgentRef(agentId=row["agentId"], definitionVersion=row["agentDefinitionVersion"]))
            for row in applicable_agent_rows
        }
        applicable_recipe_rows = [
            row for row in envelope["recipes"]
            if (row["substageDefinitionId"], row["substageDefinitionVersion"]) == definition_identity
            and row["enabled"]
        ]
        applicable_recipe_ids = {
            _candidate_id(MultiAgentRecipeRef(recipeId=row["recipeId"], recipeVersion=row["recipeVersion"]))
            for row in applicable_recipe_rows
        }
        evaluation_ids = applicable_skill_ids | applicable_agent_ids | applicable_recipe_ids
        runtime_ids = applicable_skill_ids | applicable_agent_ids
        for mapping, allowed, code in (
            (set(facts.runtime_readiness), runtime_ids, "unknown_candidate_fact"),
            (facts.authorized_candidate_refs, evaluation_ids, "unknown_candidate_fact"),
            (set(facts.active_work_unit_counts), evaluation_ids, "unknown_candidate_fact"),
            (set(facts.last_assigned_at), evaluation_ids, "unknown_candidate_fact"),
        ):
            unknown = mapping - allowed
            if unknown:
                raise ResolutionRejectedError(code, sorted(unknown)[0])
        provider_ids = {
            provider
            for binding in applicable_agent_rows
            for provider in agent_definitions[binding["agentId"]]["allowedProviders"]
        }
        unknown_providers = set(facts.provider_readiness) - provider_ids
        if unknown_providers:
            raise ResolutionRejectedError("unknown_provider_fact", sorted(unknown_providers)[0])
        governed_rules = {rule for row in applicable_recipe_rows for rule in row["applicabilityRuleRefs"]}
        unknown_rules = facts.applicable_rule_refs - governed_rules
        if unknown_rules:
            raise ResolutionRejectedError("unknown_policy_rule_fact", sorted(unknown_rules)[0])
        human_gate_refs = {
            criterion.evaluator_ref
            for criterion in instance.acceptance_criteria
            if criterion.judgment_requirement.value == "human_authority" and criterion.evaluator_ref is not None
        }
        unknown_human_gates = facts.satisfied_human_gate_refs - human_gate_refs
        if unknown_human_gates:
            raise ResolutionRejectedError("unknown_human_gate_fact", sorted(unknown_human_gates)[0])

        responsibility_runtime_contracts = self._responsibility_runtime_contracts(
            skill_bindings,
            skill_definitions,
        )
        execution_method = definition.get("executionMethod")
        if execution_method is not None:
            self._validate_execution_method_completion_contract(
                execution_method=execution_method,
                definition=definition,
                instance=instance,
                facts=facts,
            )

        evaluations: list[tuple[str, CandidateEvaluation]] = []
        agent_analyses: list[dict[str, Any]] = []
        for binding in skill_bindings:
            skill = skill_definitions.get(binding["skillId"])
            if skill is None:
                raise ResolutionRejectedError("binding_source_unknown")
            if not binding["enabled"] or not skill["enabled"] or skill["status"] != "ready":
                continue
            ref = SkillRef(skillId=skill["skillId"], definitionVersion=skill["definitionVersion"])
            analysis = self._evaluate_direct(
                    ref=ref,
                    bindings=[binding],
                    supported_role=binding["supportedRole"],
                    is_fallback=False,
                    definition=definition,
                    candidate=skill,
                    instance=instance,
                    facts=facts,
                    provider_id=None,
                    responsibility_runtime_contracts=responsibility_runtime_contracts,
                    execution_method=definition.get("executionMethod"),
                )
            evaluations.append((analysis["canonicalId"], analysis["evaluation"]))
        for binding in agent_bindings:
            agent = agent_definitions.get(binding["agentId"])
            if agent is None:
                raise ResolutionRejectedError("binding_source_unknown")
            is_general = agent["agentKind"] == "general_research"
            if is_general and binding.get("sourceAuthority") != "general_fallback_policy":
                continue
            if not binding["enabled"] or not agent["enabled"]:
                continue
            ref = AgentRef(agentId=agent["agentId"], definitionVersion=agent["definitionVersion"])
            analysis = self._evaluate_direct(
                    ref=ref,
                    bindings=[binding],
                    supported_role=binding["supportedRole"],
                    is_fallback=binding.get("sourceAuthority") == "general_fallback_policy",
                    definition=definition,
                    candidate=agent,
                    instance=instance,
                    facts=facts,
                    provider_id=agent["defaultProvider"],
                    responsibility_runtime_contracts=responsibility_runtime_contracts,
                    execution_method=definition.get("executionMethod"),
                )
            evaluations.append((analysis["canonicalId"], analysis["evaluation"]))
            analysis["binding"] = binding
            analysis["agent"] = agent
            agent_analyses.append(analysis)

        for recipe_row in applicable_recipe_rows:
            recipe = MultiAgentRecipeDefinition.model_validate(recipe_row)
            evaluations.append(
                self._evaluate_recipe(
                    recipe=recipe,
                    agent_analyses=agent_analyses,
                    definition=definition,
                    instance=instance,
                    facts=facts,
                )
            )

        if not evaluations:
            raise ResolutionRejectedError("candidate_evaluation_empty")
        evaluations.sort(key=lambda item: item[0])
        result = ResolutionResult(
            designRef=designRef,
            instanceRef=instanceRef,
            registrySnapshotRef=registrySnapshotRef,
            candidateEvaluations=[item[1] for item in evaluations],
            preferredCandidateEvaluationRefs=[],
        )
        return result

    @staticmethod
    def _normalize_exact(value: str) -> str:
        return " ".join(unicodedata.normalize("NFKC", value).split()).casefold()

    @staticmethod
    def _definition_contract_values(definition: Mapping[str, Any], field: str, values_field: str) -> tuple[bool, set[str]]:
        contract = definition.get(field) or {}
        values = contract.get(values_field) or {}
        return contract.get("status") == "known" and values.get("status") == "known", set(values.get("values") or ())

    @staticmethod
    def _dedupe(values: list[Any]) -> list[Any]:
        return list(dict.fromkeys(values))

    @staticmethod
    def _effective_acceptance_criteria(instance: Any, facts: ResolutionFacts) -> list[Any]:
        """Project State-confirmed outstanding criteria into this resolution.

        The confirmed Design remains the source of every criterion.  A current
        ScientificState may narrow an intentional re-entry to criteria that are
        still unresolved or unmet; it never creates criteria, mutates the
        instance, or selects a candidate itself.
        """
        outstanding_ids = state_outstanding_criterion_ids(instance, facts)
        if outstanding_ids is None:
            return list(instance.acceptance_criteria)
        return [
            criterion for criterion in instance.acceptance_criteria
            if criterion.criterion_id in outstanding_ids
        ]

    @staticmethod
    def _has_current_substage_state(instance: Any, facts: ResolutionFacts) -> bool:
        """Whether facts carry a State projection for this exact instance."""
        return state_outstanding_criterion_ids(instance, facts) is not None

    @staticmethod
    def _instance_completion_contract_known(instance: Any) -> bool:
        """Return whether confirmed instance truth can prove mechanical completion.

        Canonical Definition contracts remain the stronger authority when they
        are known. Semantic-only Definitions may still form an executable
        SubstageInstance after planning: the instance must name at least one
        expected artifact and each expected artifact must be covered by an
        explicit instance acceptance criterion. This does not mutate or infer
        missing Definition-level artifact/evaluator truth.
        """
        expected_outputs = {
            str(value).strip()
            for value in instance.expected_artifact_types
            if str(value).strip()
        }
        if not expected_outputs or not instance.acceptance_criteria:
            return False
        covered_outputs = {
            criterion.required_artifact_type
            for criterion in instance.acceptance_criteria
            if criterion.required_artifact_type is not None
            and criterion.required_artifact_type in expected_outputs
        }
        return expected_outputs <= covered_outputs

    @staticmethod
    def _skill_runtime_contract(candidate: Mapping[str, Any]) -> dict[str, Any] | None:
        """Normalize one frozen Skill runtime contract for semantic authorization.

        Existing kinds stay snapshot-local. ``skill_script`` is admitted only when
        the frozen candidate itself is still proven executable by the public
        durable runtime authority; the returned contract is always the frozen
        snapshot's own fields, never a live-registry overlay.
        """
        source = candidate.get("sourceContract") or {}
        raw = source.get("runtime_contract") if isinstance(source, Mapping) else None
        if not isinstance(raw, Mapping):
            return None
        kind = str(raw.get("kind") or "").strip()
        adapter_id = str(raw.get("adapter_id") or "").strip()
        requires_external_action = raw.get("requires_external_action")
        evidence_contract_version = str(raw.get("evidence_contract_version") or "").strip()
        if (
            not adapter_id
            or not isinstance(requires_external_action, bool)
            or not evidence_contract_version
        ):
            return None
        frozen_contract = {
            "kind": kind,
            "adapter_id": adapter_id,
            "requires_external_action": requires_external_action,
            "evidence_contract_version": evidence_contract_version,
        }
        if kind in {"llm_reasoning", "reasoning_adapter", "deterministic", "external_adapter"}:
            return frozen_contract
        if kind != "skill_script":
            return None
        skill_id = str(candidate.get("skillId") or "").strip()
        definition_version = candidate.get("definitionVersion")
        if (
            not skill_id
            or not isinstance(definition_version, int)
            or isinstance(definition_version, bool)
        ):
            return None
        if resolve_registered_durable_skill_runtime(
            skill_id,
            definition_version=definition_version,
            snapshot_definition=dict(candidate),
        ) is None:
            return None
        return frozen_contract

    @classmethod
    def _responsibility_runtime_contracts(
        cls,
        skill_bindings: list[dict[str, Any]],
        skill_definitions: Mapping[str, dict[str, Any]],
    ) -> tuple[dict[str, Any], ...] | None:
        """Return existing Skill runtime truth for one selected responsibility.

        This is a completion-authority input only. Missing or malformed Skill
        runtime contracts fail closed here but never remove the Definition from
        the Planner catalog.
        """
        if not skill_bindings:
            return ()
        contracts: list[dict[str, Any]] = []
        for binding in skill_bindings:
            if not binding.get("enabled"):
                return None
            skill = skill_definitions.get(str(binding.get("skillId") or ""))
            if skill is None:
                return None
            contract = cls._skill_runtime_contract(skill)
            if contract is None:
                return None
            contracts.append(contract)
        return tuple(contracts) if contracts else None

    @staticmethod
    def _general_fallback_matches_responsibility(
        candidate: Mapping[str, Any],
        responsibility_runtime_contracts: tuple[dict[str, Any], ...] | None,
        *,
        definition: Mapping[str, Any],
    ) -> bool:
        """Prove that the general fallback is sufficient for this responsibility.

        The current general fallback contract is reasoning/drafting only. A
        Definition whose bound Skill contracts require an external adapter, a
        deterministic runtime, or whose runtime contract is unknown therefore
        cannot inherit completion authority merely because the fallback exists.
        """
        external = definition.get("externalActionContract") or {}
        if external.get("status") == "known" and external.get("requiredActionKinds"):
            return False
        if responsibility_runtime_contracts is None:
            return False
        if not responsibility_runtime_contracts:
            if not (
                external.get("status") == "known"
                and external.get("requiredActionKinds") == []
            ):
                return False
        elif not all(
            contract["kind"] == "llm_reasoning"
            and contract["requires_external_action"] is False
            for contract in responsibility_runtime_contracts
        ):
            return False
        output = candidate.get("outputContract") or {}
        budget = candidate.get("budgetPolicy") or {}
        default_mode = str(budget.get("defaultMode") or "").strip()
        allowed_modes = set(budget.get("allowedModes") or ())
        max_tool_calls = budget.get("maxToolCalls")
        draft_authorized = default_mode == "draft_only" or "draft_only" in allowed_modes
        return bool(
            str(output.get("artifactKind") or "").strip()
            and str(output.get("artifactContract") or "").strip()
            and draft_authorized
            and max_tool_calls == 0
        )

    @staticmethod
    def _execution_method_role_contract(role: Mapping[str, Any]) -> Mapping[str, Any]:
        contract = role.get("qualificationContract")
        if not isinstance(contract, Mapping):
            raise ResolutionRejectedError("execution_method_role_contract_missing")
        return contract

    def _validate_execution_method_completion_contract(
        self,
        *,
        execution_method: Mapping[str, Any],
        definition: Mapping[str, Any],
        instance: Any,
        facts: ResolutionFacts,
    ) -> None:
        roles = {
            str(role.get("roleId") or ""): role
            for role in execution_method.get("roles") or []
            if isinstance(role, Mapping)
        }
        completion_role_ids = [str(value) for value in execution_method.get("completionRoleIds") or []]
        for role in roles.values():
            self._execution_method_role_contract(role)
        completion_path_role_ids = set(completion_role_ids)
        pending_role_ids = list(completion_role_ids)
        while pending_role_ids:
            role_id = pending_role_ids.pop()
            try:
                dependencies = roles[role_id].get("dependsOn") or []
            except KeyError as exc:
                raise ResolutionRejectedError(
                    "execution_method_completion_role_contract_missing", str(exc)
                ) from exc
            for dependency_id in dependencies:
                dependency_id = str(dependency_id)
                if dependency_id not in completion_path_role_ids:
                    completion_path_role_ids.add(dependency_id)
                    pending_role_ids.append(dependency_id)
        try:
            completion_contracts = [
                self._execution_method_role_contract(roles[role_id])
                for role_id in completion_path_role_ids
            ]
        except KeyError as exc:
            raise ResolutionRejectedError(
                "execution_method_completion_role_contract_missing", str(exc)
            ) from exc
        effective_criteria = self._effective_acceptance_criteria(instance, facts)
        required_outputs = (
            set()
            if self._has_current_substage_state(instance, facts)
            else set(instance.expected_artifact_types)
        ) | {
            criterion.required_artifact_type
            for criterion in effective_criteria
            if criterion.required_artifact_type is not None
        }
        definition_outputs_known, definition_outputs = self._definition_contract_values(
            definition, "outputContract", "artifactTypes"
        )
        if definition_outputs_known and not required_outputs <= definition_outputs:
            raise ResolutionRejectedError(
                "execution_method_definition_output_incompatible",
                ",".join(sorted(required_outputs - definition_outputs)),
            )
        completion_outputs = {
            str(value)
            for contract in completion_contracts
            for value in contract.get("outputArtifactTypes") or []
        }
        if not required_outputs <= completion_outputs:
            raise ResolutionRejectedError(
                "execution_method_completion_output_incompatible",
                ",".join(sorted(required_outputs - completion_outputs)),
            )
        required_professional = {
            criterion.judgment_requirement.value
            for criterion in effective_criteria
            if criterion.judgment_requirement.value in {
                "domain_professional", "research_synthesis"
            }
        }
        completion_judgments = {
            str(contract.get("judgmentRequirement") or "none")
            for contract in completion_contracts
        }
        missing_professional = required_professional - completion_judgments
        if missing_professional:
            raise ResolutionRejectedError(
                "execution_method_completion_acceptance_incompatible",
                ",".join(sorted(missing_professional)),
            )

    def _evaluate_execution_method_candidate(
        self,
        *,
        ref: AgentRef | SkillRef,
        bindings: list[dict[str, Any]],
        supported_role: str,
        definition: dict[str, Any],
        candidate: dict[str, Any],
        instance: Any,
        facts: ResolutionFacts,
        provider_id: str | None,
        execution_method: Mapping[str, Any],
    ) -> dict[str, Any]:
        canonical_id = _candidate_id(ref)
        method_id = str(execution_method.get("methodId") or "")
        method_version = execution_method.get("methodVersion")
        if not method_id or not isinstance(method_version, int):
            raise ResolutionRejectedError("execution_method_identity_invalid")
        candidate_role_refs = {
            str(value)
            for value in candidate.get("executionMethodRoles") or []
            if isinstance(value, str) and value
        }
        candidate_kind = "skill" if isinstance(ref, SkillRef) else "agent"

        if isinstance(ref, SkillRef):
            candidate_input = candidate.get("inputContract") or {}
            candidate_output = candidate.get("outputContract") or {}
            candidate_inputs_known = candidate_input.get("status") == "known"
            candidate_outputs_known = candidate_output.get("status") == "known"
            accepted_inputs = set(candidate_input.get("acceptedInputRefs") or ())
            candidate_required_inputs: set[str] = set()
            candidate_outputs = set(candidate_output.get("artifactTypes") or ())
            deterministic_supported = bool(
                (candidate.get("judgmentSupport") or {}).get("deterministic")
            )
        else:
            candidate_inputs_known = True
            candidate_outputs_known = True
            accepted_inputs = set()
            candidate_required_inputs = set(
                (candidate.get("inputContract") or {}).get("required") or ()
            )
            artifact_kind = (candidate.get("outputContract") or {}).get("artifactKind")
            candidate_outputs = {artifact_kind} if artifact_kind else set()
            deterministic_supported = True

        role_evaluations: list[ExecutionMethodRoleEvaluation] = []
        role_contracts: dict[str, Mapping[str, Any]] = {}
        for role in execution_method.get("roles") or []:
            role_id = str(role.get("roleId") or "")
            role_ref = f"{method_id}:{role_id}"
            if role_ref not in candidate_role_refs:
                continue
            role_contract = self._execution_method_role_contract(role)
            role_contracts[role_ref] = role_contract
            rejection_codes: list[CandidateRejectionCode] = []
            input_match = "exact"
            output_match = "exact"
            role_inputs = set(role_contract.get("requiredInputRefs") or ())
            role_outputs = set(role_contract.get("outputArtifactTypes") or ())
            judgment_requirement = str(role_contract.get("judgmentRequirement") or "none")

            if candidate_kind not in set(role.get("capabilityKinds") or []):
                rejection_codes.append(CandidateRejectionCode.ROLE_MISMATCH)
            if not candidate_inputs_known:
                input_match = "none"
                rejection_codes.append(CandidateRejectionCode.SEMANTIC_FIT_INSUFFICIENT)
            elif isinstance(ref, SkillRef) and not role_inputs <= accepted_inputs:
                input_match = "none"
                rejection_codes.append(CandidateRejectionCode.INPUT_INCOMPATIBLE)
            elif isinstance(ref, AgentRef) and not candidate_required_inputs <= role_inputs:
                input_match = "none"
                rejection_codes.append(CandidateRejectionCode.INPUT_INCOMPATIBLE)
            if role_outputs:
                if not candidate_outputs_known:
                    output_match = "none"
                    rejection_codes.append(CandidateRejectionCode.SEMANTIC_FIT_INSUFFICIENT)
                elif not role_outputs <= candidate_outputs:
                    output_match = "partial" if role_outputs & candidate_outputs else "none"
                    rejection_codes.append(CandidateRejectionCode.OUTPUT_INCOMPATIBLE)

            professional_required = judgment_requirement in {
                "domain_professional", "research_synthesis", "human_authority"
            }
            if judgment_requirement == "none":
                judgment_supported = True
            elif judgment_requirement == "deterministic":
                judgment_supported = deterministic_supported
            elif judgment_requirement == "domain_professional":
                judgment_supported = bool(
                    (candidate.get("judgmentSupport") or {}).get("domainProfessional")
                )
            elif judgment_requirement == "research_synthesis":
                judgment_supported = isinstance(ref, AgentRef)
            else:
                judgment_supported = bool(
                    isinstance(ref, AgentRef)
                    and (candidate.get("judgmentSupport") or {}).get("humanAuthority")
                )
            if not judgment_supported:
                rejection_codes.append(CandidateRejectionCode.SEMANTIC_FIT_INSUFFICIENT)

            if isinstance(ref, AgentRef):
                objective = str(role_contract.get("objective") or "")
                targets = {
                    self._normalize_exact(instance.objective),
                    self._normalize_exact(objective),
                }
                if any(
                    self._normalize_exact(value) in targets
                    for value in candidate.get("notFor") or ()
                ):
                    rejection_codes.append(CandidateRejectionCode.EXPLICITLY_NOT_FOR)

            rejection_codes = self._dedupe(rejection_codes)
            eligible = not rejection_codes
            blockers: list[CandidateExecutionBlockerCode] = []
            # Execution-method roles are selected as one frozen graph before
            # upstream roles have produced their artifacts. Binding v2 owns the
            # exact per-role input materialization when a role is released;
            # Definition preflight has already checked the method's external
            # inputs. Treating role-local fields as immediately available here
            # would reject every honest downstream role in a multi-step method.
            if canonical_id not in facts.authorized_candidate_refs or not eligible:
                blockers.append(CandidateExecutionBlockerCode.AUTHORIZATION_DENIED)
            if not facts.risk_allowed:
                blockers.append(CandidateExecutionBlockerCode.RISK_EXCEEDED)
            if not facts.budget_available:
                blockers.append(CandidateExecutionBlockerCode.BUDGET_EXCEEDED)
            if not facts.runtime_readiness.get(canonical_id, False):
                blockers.append(CandidateExecutionBlockerCode.RUNTIME_UNAVAILABLE)
            if provider_id is not None and not facts.provider_readiness.get(provider_id, False):
                blockers.append(CandidateExecutionBlockerCode.PROVIDER_UNAVAILABLE)
            blockers = self._dedupe(blockers)
            role_evaluations.append(
                ExecutionMethodRoleEvaluation(
                    roleRef=role_ref,
                    eligible=eligible,
                    rejectionCodes=rejection_codes,
                    executableNow=eligible and not blockers,
                    executionBlockerCodes=blockers,
                    semanticFitEvidence=SemanticFitEvidence(
                        objectiveMatch="compatible",
                        inputContractMatch=input_match,
                        outputContractMatch=output_match,
                        professionalJudgmentFit=(
                            "required_supported"
                            if professional_required and judgment_supported
                            else "unsupported"
                            if professional_required
                            else "not_required"
                        ),
                        positiveRuleRefs=(
                            [POLICY_VERSION + ":definition-binding", POLICY_VERSION + ":execution-method-role"]
                            if eligible else []
                        ),
                        negativeRuleRefs=(
                            [POLICY_VERSION + ":execution-method-role-contract"]
                            if rejection_codes else []
                        ),
                        adapterRefs=[],
                        policyVersion=facts.policy_version,
                        rationale="Evaluated against the frozen execution-method role qualification contract.",
                    ),
                )
            )

        eligible_roles = [row for row in role_evaluations if row.eligible]
        executable_roles = [row for row in role_evaluations if row.executable_now]
        eligible = bool(eligible_roles)
        if role_evaluations and not eligible:
            rejection_codes = self._dedupe(
                [code for row in role_evaluations for code in row.rejection_codes]
            )
        elif not role_evaluations:
            rejection_codes = [CandidateRejectionCode.ROLE_MISMATCH]
        else:
            rejection_codes = []
        if executable_roles:
            blockers: list[CandidateExecutionBlockerCode] = []
        else:
            blockers = self._dedupe(
                [code for row in role_evaluations for code in row.execution_blocker_codes]
            )
            if not blockers:
                blockers = [CandidateExecutionBlockerCode.AUTHORIZATION_DENIED]
        evidence = (
            executable_roles[0].semantic_fit_evidence
            if executable_roles
            else eligible_roles[0].semantic_fit_evidence
            if eligible_roles
            else role_evaluations[0].semantic_fit_evidence
            if role_evaluations
            else SemanticFitEvidence(
                objectiveMatch="none",
                inputContractMatch="none",
                outputContractMatch="none",
                professionalJudgmentFit="not_required",
                positiveRuleRefs=[],
                negativeRuleRefs=[POLICY_VERSION + ":execution-method-role-membership"],
                adapterRefs=[],
                policyVersion=facts.policy_version,
                rationale="Candidate is not registered for any role in the active execution method.",
            )
        )
        supports_professional = any(
            row.eligible
            and role_contracts[row.role_ref].get("judgmentRequirement")
            in {"domain_professional", "research_synthesis", "human_authority"}
            for row in role_evaluations
        )
        risk_value = (definition.get("riskPolicy") or {}).get("riskClass") or "high"
        preference = CandidatePreference(
            semanticFit="compatible" if eligible else "weak",
            semanticFitEvidence=evidence,
            completionCoverage=CompletionCoverage.PROGRESS_CAPABLE,
            executableNow=bool(executable_roles),
            executionBlockerCodes=blockers,
            riskClass=RiskClass(risk_value),
            estimatedCostAmount=None,
            permissionBreadth="standard" if isinstance(ref, AgentRef) else "narrow",
            supportsProfessionalJudgment=supports_professional,
            activeWorkUnitCount=facts.active_work_unit_counts.get(canonical_id, 0),
            lastAssignedAt=facts.last_assigned_at.get(canonical_id),
        )
        evaluation = CandidateEvaluation(
            candidateEvaluationId=f"eval:{canonical_id}",
            candidateRef=ref,
            sourceBindingRefs=_binding_refs(bindings),
            supportedRole=supported_role,
            eligible=eligible,
            rejectionCodes=rejection_codes,
            preference=preference,
            rationale="Execution-method candidate evaluated only against its declared role contracts.",
            executionMethodRoleEvaluations=role_evaluations,
        )
        return {
            "canonicalId": canonical_id,
            "evaluation": evaluation,
            "candidateOutputs": candidate_outputs,
            "isFallback": False,
            "agent": candidate,
            "binding": bindings[0],
        }

    def _evaluate_direct(
        self,
        *,
        ref: AgentRef | SkillRef,
        bindings: list[dict[str, Any]],
        supported_role: str,
        is_fallback: bool,
        definition: dict[str, Any],
        candidate: dict[str, Any],
        instance: Any,
        facts: ResolutionFacts,
        provider_id: str | None,
        responsibility_runtime_contracts: tuple[dict[str, Any], ...] | None,
        execution_method: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if execution_method is not None:
            return self._evaluate_execution_method_candidate(
                ref=ref,
                bindings=bindings,
                supported_role=supported_role,
                definition=definition,
                candidate=candidate,
                instance=instance,
                facts=facts,
                provider_id=provider_id,
                execution_method=execution_method,
            )
        canonical_id = _candidate_id(ref)
        required_inputs_known, required_inputs = self._definition_contract_values(
            definition, "inputContract", "requiredInputRefs"
        )
        required_outputs_known, definition_outputs = self._definition_contract_values(
            definition, "outputContract", "artifactTypes"
        )
        acceptance_contract = definition.get("acceptanceContract") or {}
        acceptance_values_field = (
            "evaluatorRefs" if "evaluatorRefs" in acceptance_contract else "criterionRefs"
        )
        acceptance_known, _criterion_refs = self._definition_contract_values(
            definition, "acceptanceContract", acceptance_values_field
        )
        effective_criteria = self._effective_acceptance_criteria(instance, facts)
        required_outputs = (
            set()
            if self._has_current_substage_state(instance, facts)
            else set(instance.expected_artifact_types)
        ) | {
            criterion.required_artifact_type
            for criterion in effective_criteria
            if criterion.required_artifact_type is not None
        }
        rejection_codes: list[CandidateRejectionCode] = []
        input_match = "exact"
        output_match = "exact"
        domain_professional_required = any(
            criterion.judgment_requirement.value == "domain_professional"
            for criterion in effective_criteria
        )
        research_synthesis_required = any(
            criterion.judgment_requirement.value == "research_synthesis"
            for criterion in effective_criteria
        )
        professional_required = domain_professional_required or research_synthesis_required
        professional_supported = True
        candidate_outputs: set[str]

        is_reviewer = isinstance(ref, AgentRef) and supported_role == "reviewer"
        if isinstance(ref, SkillRef):
            candidate_input = candidate.get("inputContract") or {}
            candidate_output = candidate.get("outputContract") or {}
            candidate_inputs_known = candidate_input.get("status") == "known"
            candidate_outputs_known = candidate_output.get("status") == "known"
            accepted_inputs = set(candidate_input.get("acceptedInputRefs") or ())
            candidate_outputs = set(candidate_output.get("artifactTypes") or ())
            deterministic_supported = bool((candidate.get("judgmentSupport") or {}).get("deterministic"))
        else:
            candidate_inputs_known = True
            candidate_outputs_known = True
            accepted_inputs = set((candidate.get("inputContract") or {}).get("required") or ())
            artifact_kind = (candidate.get("outputContract") or {}).get("artifactKind")
            candidate_outputs = {artifact_kind} if artifact_kind else set()
            deterministic_supported = True

        if is_reviewer:
            review_contract = candidate.get("reviewContract") or {}
            frozen_review = review_contract.get("frozenArtifactContract")
            candidate_inputs_known = True
            accepted_inputs = set()
            candidate_outputs_known = isinstance(frozen_review, dict)
            candidate_outputs = set()

        instance_contract_known = self._instance_completion_contract_known(instance)
        canonical_contract_complete = (
            required_inputs_known and required_outputs_known and acceptance_known
        )
        strict_output_authorized = bool(
            required_outputs_known
            and candidate_outputs_known
            and required_outputs <= definition_outputs
            and required_outputs <= candidate_outputs
        )
        skill_runtime_contract = (
            self._skill_runtime_contract(candidate) if isinstance(ref, SkillRef) else None
        )
        semantic_skill_authorized = bool(
            isinstance(ref, SkillRef)
            and not canonical_contract_complete
            and instance_contract_known
            and skill_runtime_contract is not None
        )
        semantic_fallback_authorized = bool(
            is_fallback
            and not canonical_contract_complete
            and instance_contract_known
            and all(
                criterion.judgment_requirement.value != "human_authority"
                or criterion.evaluator_ref in facts.satisfied_human_gate_refs
                for criterion in effective_criteria
            )
            and self._general_fallback_matches_responsibility(
                candidate,
                responsibility_runtime_contracts,
                definition=definition,
            )
        )

        if is_reviewer:
            if not candidate_outputs_known:
                output_match = "none"
                rejection_codes.append(CandidateRejectionCode.SEMANTIC_FIT_INSUFFICIENT)
            professional_supported = True
        elif is_fallback:
            rejection_codes = []
            input_match = "exact" if required_inputs_known and required_inputs <= set(instance.input_refs) else "none"
            if canonical_contract_complete:
                output_match = "exact" if strict_output_authorized else (
                    "partial" if required_outputs & candidate_outputs else "none"
                )
            else:
                output_match = "adapter_compatible" if semantic_fallback_authorized else "partial"
            professional_supported = (
                (not domain_professional_required or semantic_fallback_authorized)
                and (
                    not research_synthesis_required
                    or semantic_fallback_authorized
                )
            )
        elif semantic_skill_authorized:
            input_match = "adapter_compatible"
            output_match = "adapter_compatible"
            professional_supported = not domain_professional_required
            if domain_professional_required:
                rejection_codes.append(CandidateRejectionCode.SEMANTIC_FIT_INSUFFICIENT)
        else:
            if not required_inputs_known or not candidate_inputs_known:
                input_match = "none"
                rejection_codes.append(CandidateRejectionCode.SEMANTIC_FIT_INSUFFICIENT)
            elif not required_inputs <= set(instance.input_refs) or not accepted_inputs <= set(instance.input_refs):
                input_match = "none"
                rejection_codes.append(CandidateRejectionCode.INPUT_INCOMPATIBLE)
            if not required_outputs_known or not candidate_outputs_known or not acceptance_known:
                output_match = "none"
                rejection_codes.append(CandidateRejectionCode.SEMANTIC_FIT_INSUFFICIENT)
            elif not required_outputs <= definition_outputs or not required_outputs <= candidate_outputs:
                output_match = "partial" if required_outputs & candidate_outputs else "none"
                rejection_codes.append(CandidateRejectionCode.OUTPUT_INCOMPATIBLE)

            if isinstance(ref, AgentRef):
                targets = {self._normalize_exact(instance.objective)} | {
                    self._normalize_exact(criterion.description) for criterion in effective_criteria
                }
                if any(self._normalize_exact(value) in targets for value in candidate.get("notFor") or ()):
                    rejection_codes.append(CandidateRejectionCode.EXPLICITLY_NOT_FOR)

            for criterion in effective_criteria:
                requirement = criterion.judgment_requirement.value
                if requirement == "deterministic":
                    supported = deterministic_supported
                elif requirement == "domain_professional":
                    if isinstance(ref, SkillRef):
                        supported = bool(
                            (candidate.get("judgmentSupport") or {}).get(
                                "domainProfessional"
                            )
                        )
                    else:
                        supported = (
                            isinstance(ref, AgentRef)
                            and bool(
                                (candidate.get("judgmentSupport") or {}).get(
                                    "domainProfessional"
                                )
                            )
                            and criterion.evaluator_ref
                            == (candidate.get("evaluator") or {}).get("id")
                        )
                elif requirement == "research_synthesis":
                    supported = isinstance(ref, AgentRef) and (
                        not is_fallback or semantic_fallback_authorized
                    )
                else:
                    supported = criterion.evaluator_ref in facts.satisfied_human_gate_refs
                professional_supported = professional_supported and supported
            if not professional_supported:
                rejection_codes.append(CandidateRejectionCode.SEMANTIC_FIT_INSUFFICIENT)

        rejection_codes = self._dedupe(rejection_codes)
        eligible = is_fallback or not rejection_codes
        blockers: list[CandidateExecutionBlockerCode] = []
        if not required_inputs <= facts.available_input_refs:
            blockers.append(CandidateExecutionBlockerCode.REQUIRED_INPUT_MISSING)
        if canonical_id not in facts.authorized_candidate_refs or not eligible:
            blockers.append(CandidateExecutionBlockerCode.AUTHORIZATION_DENIED)
        if not facts.risk_allowed:
            blockers.append(CandidateExecutionBlockerCode.RISK_EXCEEDED)
        if not facts.budget_available:
            blockers.append(CandidateExecutionBlockerCode.BUDGET_EXCEEDED)
        if not facts.runtime_readiness.get(canonical_id, False):
            blockers.append(CandidateExecutionBlockerCode.RUNTIME_UNAVAILABLE)
        if provider_id is not None and not facts.provider_readiness.get(provider_id, False):
            blockers.append(CandidateExecutionBlockerCode.PROVIDER_UNAVAILABLE)
        blockers = self._dedupe(blockers)

        all_deterministic = all(
            criterion.judgment_requirement.value == "deterministic"
            for criterion in effective_criteria
        )
        skill_judgment_types_supported = all(
            criterion.judgment_requirement.value
            in {"deterministic", "domain_professional"}
            for criterion in effective_criteria
        )
        completion_contract_known = acceptance_known or instance_contract_known
        if isinstance(ref, SkillRef):
            executor_capability_authorized = (
                strict_output_authorized if canonical_contract_complete else semantic_skill_authorized
            )
            completion_capable = (
                eligible
                and skill_judgment_types_supported
                and completion_contract_known
                and executor_capability_authorized
                and professional_supported
            )
        else:
            executor_capability_authorized = (
                strict_output_authorized if canonical_contract_complete else semantic_fallback_authorized
            ) if is_fallback else strict_output_authorized
            completion_capable = (
                eligible
                and supported_role == "primary"
                and professional_supported
                and completion_contract_known
                and executor_capability_authorized
            )
        completion = CompletionCoverage.COMPLETION_CAPABLE if completion_capable else CompletionCoverage.PROGRESS_CAPABLE
        risk_value = (definition.get("riskPolicy") or {}).get("riskClass") or "high"
        preference = CandidatePreference(
            semanticFit="weak" if is_fallback or not eligible else "compatible",
            semanticFitEvidence=SemanticFitEvidence(
                objectiveMatch="compatible",
                inputContractMatch=input_match,
                outputContractMatch=output_match,
                professionalJudgmentFit=(
                    "required_supported" if professional_required and professional_supported else
                    "unsupported" if professional_required else "not_required"
                ),
                positiveRuleRefs=(
                    ([POLICY_VERSION + ":definition-binding"] if eligible else [])
                    + ([POLICY_VERSION + ":executor-capability"] if executor_capability_authorized else [])
                ),
                negativeRuleRefs=[POLICY_VERSION + ":semantic-contract"] if rejection_codes else [],
                adapterRefs=[],
                policyVersion=facts.policy_version,
                rationale="Evaluated from the archived Definition and explicit Binding authority.",
            ),
            completionCoverage=completion,
            executableNow=eligible and not blockers,
            executionBlockerCodes=blockers,
            riskClass=RiskClass(risk_value),
            estimatedCostAmount=None,
            permissionBreadth="broad" if is_fallback else ("standard" if isinstance(ref, AgentRef) else "narrow"),
            supportsProfessionalJudgment=professional_required and professional_supported,
            activeWorkUnitCount=facts.active_work_unit_counts.get(canonical_id, 0),
            lastAssignedAt=facts.last_assigned_at.get(canonical_id),
        )
        evaluation = CandidateEvaluation(
            candidateEvaluationId=f"eval:{canonical_id}",
            candidateRef=ref,
            sourceBindingRefs=_binding_refs(bindings),
            supportedRole=supported_role,
            eligible=eligible,
            rejectionCodes=rejection_codes,
            preference=preference,
            rationale="Applicable candidate evaluated without selection or execution authority.",
        )
        return {
            "canonicalId": canonical_id,
            "evaluation": evaluation,
            "candidateOutputs": candidate_outputs,
            "isFallback": is_fallback,
            "agent": candidate,
            "binding": bindings[0],
        }

    def _evaluate_recipe(
        self,
        *,
        recipe: MultiAgentRecipeDefinition,
        agent_analyses: list[dict[str, Any]],
        definition: dict[str, Any],
        instance: Any,
        facts: ResolutionFacts,
    ) -> tuple[str, CandidateEvaluation]:
        ref = MultiAgentRecipeRef(recipeId=recipe.recipe_id, recipeVersion=recipe.recipe_version)
        canonical_id = _candidate_id(ref)
        governed_agents = [row for row in agent_analyses if not row["isFallback"]]
        pools: dict[str, list[dict[str, Any]]] = {}
        eligible_pools: dict[str, list[dict[str, Any]]] = {}
        executable_pools: dict[str, list[dict[str, Any]]] = {}
        rejections: list[CandidateRejectionCode] = []
        for template in recipe.assignment_templates:
            pool = [
                row for row in governed_agents
                if row["binding"]["supportedRole"] == template.required_supported_role.value
            ]
            eligible_pool = [row for row in pool if row["evaluation"].eligible]
            executable_pool = [
                row for row in eligible_pool if row["evaluation"].preference.executable_now
            ]
            pools[template.template_key] = pool
            eligible_pools[template.template_key] = eligible_pool
            executable_pools[template.template_key] = executable_pool
            if not pool:
                rejections.append(CandidateRejectionCode.ROLE_MISMATCH)
            elif not eligible_pool:
                rejections.append(CandidateRejectionCode.SEMANTIC_FIT_INSUFFICIENT)
        if set(recipe.applicability_rule_refs) - facts.applicable_rule_refs:
            rejections.append(CandidateRejectionCode.SEMANTIC_FIT_INSUFFICIENT)
        rejections = self._dedupe(rejections)
        eligible = not rejections

        blockers: list[CandidateExecutionBlockerCode] = []
        if canonical_id not in facts.authorized_candidate_refs or not eligible:
            blockers.append(CandidateExecutionBlockerCode.AUTHORIZATION_DENIED)
        if not facts.risk_allowed:
            blockers.append(CandidateExecutionBlockerCode.RISK_EXCEEDED)
        if not facts.budget_available:
            blockers.append(CandidateExecutionBlockerCode.BUDGET_EXCEEDED)
        for template in recipe.assignment_templates:
            eligible_pool = eligible_pools[template.template_key]
            if eligible_pool and not executable_pools[template.template_key]:
                for row in eligible_pool:
                    blockers.extend(row["evaluation"].preference.execution_blocker_codes)
        blockers = self._dedupe(blockers)

        completion_keys = set(recipe.completion_policy_template.required_template_keys)
        completion_capable = eligible and all(bool(eligible_pools[key]) for key in completion_keys)
        if recipe.completion_policy_template.review_requirement.value != "none":
            completion_capable = completion_capable and bool(recipe.completion_policy_template.reviewer_template_keys)
        effective_criteria = self._effective_acceptance_criteria(instance, facts)
        required_outputs = (
            set()
            if self._has_current_substage_state(instance, facts)
            else set(instance.expected_artifact_types)
        ) | {
            criterion.required_artifact_type
            for criterion in effective_criteria
            if criterion.required_artifact_type is not None
        }
        assembly_types = {
            requirement.artifact_type for requirement in recipe.result_assembly_policy_template.final_artifact_requirements
        }
        completion_capable = completion_capable and required_outputs <= assembly_types
        for requirement in recipe.result_assembly_policy_template.final_artifact_requirements:
            for template_key in requirement.source_template_keys:
                completion_capable = completion_capable and any(
                    requirement.artifact_type in row["candidateOutputs"] for row in eligible_pools[template_key]
                )
        completion = CompletionCoverage.COMPLETION_CAPABLE if completion_capable else CompletionCoverage.PROGRESS_CAPABLE
        bindings = [row["binding"] for pool in pools.values() for row in pool]
        professional_required = any(
            criterion.judgment_requirement.value in {"domain_professional", "research_synthesis"}
            for criterion in effective_criteria
        )
        preference = CandidatePreference(
            semanticFit="exact" if completion_capable else "compatible" if eligible else "weak",
            semanticFitEvidence=SemanticFitEvidence(
                objectiveMatch="compatible",
                inputContractMatch="exact" if eligible else "none",
                outputContractMatch="exact" if completion_capable else "partial",
                professionalJudgmentFit=(
                    "required_supported" if professional_required and completion_capable else
                    "unsupported" if professional_required else "not_required"
                ),
                positiveRuleRefs=list(recipe.applicability_rule_refs) if eligible else [],
                negativeRuleRefs=[POLICY_VERSION + ":recipe-constituent"] if rejections else [],
                adapterRefs=[],
                policyVersion=facts.policy_version,
                rationale="Aggregated from governed constituent Agent evaluations and the archived Recipe.",
            ),
            completionCoverage=completion,
            executableNow=eligible and not blockers,
            executionBlockerCodes=blockers,
            riskClass=RiskClass((definition.get("riskPolicy") or {}).get("riskClass") or "high"),
            estimatedCostAmount=None,
            permissionBreadth="standard",
            supportsProfessionalJudgment=professional_required and completion_capable,
            activeWorkUnitCount=facts.active_work_unit_counts.get(canonical_id, 0),
            lastAssignedAt=facts.last_assigned_at.get(canonical_id),
        )
        evaluation = CandidateEvaluation(
            candidateEvaluationId=f"eval:{canonical_id}",
            candidateRef=ref,
            sourceBindingRefs=_binding_refs(bindings),
            supportedRole="multi_agent",
            eligible=eligible,
            rejectionCodes=rejections,
            preference=preference,
            rationale="Registered Recipe evaluated without constituent selection or execution authority.",
        )
        return canonical_id, evaluation
