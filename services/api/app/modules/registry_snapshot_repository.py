"""Canonical immutable Registry snapshots and content-addressed archive."""

from __future__ import annotations

from copy import deepcopy

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import PurePosixPath
import re
from typing import Any, Callable, Mapping, Protocol
import warnings

import yaml

from app.execution.skill_finalization_runtime import (
    validate_finalization_declaration,
    validate_frozen_finalization_contract,
)
from app.models.external_action import EXTERNAL_ACTION_KIND_VALUES
from app.modules.binding_source_path import (
    BindingSourcePathError,
    binding_source_path_steps,
)
from app.modules.conditional_capability_binding import validate_conditional_policy


HASH_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
ENVELOPE_KEYS = {"schemaVersion", "definitions", "bindings", "recipes", "staticPolicies"}
SET_LIKE_FIELDS = {
    "aliases",
    "allowedProviders",
    "capability",
    "failClosedRules",
    "notFor",
    "packs",
    "primaryStages",
    "skillIds",
    "stages",
    "substages",
    "applicabilityRuleRefs",
    "requiredFields",
    "requiredMetadata",
    "qualitySignals",
    "requiredActionKinds",
    "requiredRuntimeFactRefs",
    "executionMethodRoles",
    "capabilityKinds",
    "completionRoleIds",
    "qualityConstraints",
    "dependsOn",
    "repeatRoleIds",
    "requiredInputRefs",
    "outputArtifactTypes",
}
ORDERED_FIELDS = {
    "assignmentTemplates",
    "dependencyTemplateKeys",
    "requiredTemplateKeys",
    "reviewerTemplateKeys",
    "finalArtifactRequirements",
    "sourceTemplateKeys",
}
POLICY_KEYS = {
    "policyVersion",
    "unknownCanonicalRiskClass",
    "unboundSkillPolicy",
    "providerReadinessSource",
    "systemRecoveryAuthorities",
}
LEGACY_POLICY_KEYS = POLICY_KEYS - {"systemRecoveryAuthorities"}
# ``9484a55b`` added this authority after already-valid v1 snapshots had been
# archived.  It is intentionally a literal, versioned compatibility value:
# legacy replay must not invent, infer, or broaden recovery authority.
LEGACY_SYSTEM_RECOVERY_AUTHORITIES = ({
    "reasonCode": "semantic_material_insufficient",
    "definitionId": "opentrajectory:progressive_reading",
    "objective": "Recover source material needed by the blocked research step.",
    "expectedArtifactTypes": ["source_materials"],
},)
AGENT_ROLES = {"primary", "reviewer", "support"}
SKILL_ROLES = {"tool"}
AGENT_BINDING_SOURCES = {"agent_registry_mapping", "general_fallback_policy"}
SKILL_BINDING_SOURCES = {"skill_binding_source"}
GRANT_SOURCES = {"agent_registry_skill_grant"}


class _UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(loader: yaml.SafeLoader, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
    result: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            raise RegistrySnapshotIntegrityError(f"spec YAML contains duplicate key: {key}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping)


class RegistrySnapshotError(ValueError):
    """Base class for snapshot archive failures."""


class RegistrySnapshotIntegrityError(RegistrySnapshotError):
    """Raised when canonical payload and content identity disagree."""


class RegistrySnapshotNotFoundError(RegistrySnapshotError):
    """Raised when a requested content hash has not been archived."""


class RegistrySnapshotAdmissionWarning(UserWarning):
    """A non-blocking handoff check beyond the local supported rules."""


@dataclass(frozen=True)
class _HandoffContext:
    producer_skill: str
    producer_binding: str
    consumer_skill: str
    consumer_binding: str
    source_path: str
    target_path: str


@dataclass(frozen=True)
class _HandoffConflict:
    category: str
    producer_allowed: str
    consumer_accepted: str


class _SchemaPathError(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(code)


class _UnsupportedHandoffSchema(ValueError):
    pass


@dataclass(frozen=True)
class _SchemaConstraint:
    types: frozenset[str]
    values: tuple[Any, ...] | None


_DEFAULT_ABSENT = "absent"
_DEFAULT_ACCEPTED = "accepted"
_DEFAULT_UNKNOWN = "unknown"


def _normalize_lf(content: str) -> str:
    return content.replace("\r\n", "\n").replace("\r", "\n")


def _section_kind(title: str) -> str | None:
    normalized = title.strip().lower()
    if normalized in {"input", "inputs", "输入"}:
        return "input"
    if normalized in {"output", "outputs", "产出物", "输出", "输出物"} or normalized.startswith("产出物（"):
        return "output"
    if normalized in {"acceptance", "acceptance criteria", "验收", "验收标准"}:
        return "acceptance"
    return None


def _explicit_list_values(body: str) -> list[str]:
    values: list[str] = []
    pattern = re.compile(r"^\s*[-*]\s+`?([a-z][a-z0-9_.:-]*)`?\s*$")
    for line in body.split("\n"):
        match = pattern.fullmatch(line)
        if match:
            values.append(match.group(1))
    return list(dict.fromkeys(values))
def _explicit_list_values_v2(body: str) -> list[str]:
    """Parse Contract v2 refs without rewriting their case-sensitive identity."""
    values: list[str] = []
    pattern = re.compile(r"^\s*[-*]\s+`?([A-Za-z][A-Za-z0-9_.:-]*)`?\s*$")
    for line in body.split("\n"):
        match = pattern.fullmatch(line)
        if match:
            values.append(match.group(1))
    return list(dict.fromkeys(values))



def _parse_execution_method_section(sections: list[dict[str, Any]]) -> dict[str, Any] | None:
    matches = [
        section for section in sections
        if section["title"].strip().lower() in {"execution method", "执行方法"}
    ]
    if not matches:
        return None
    if len(matches) != 1:
        raise RegistrySnapshotIntegrityError("spec execution method section is ambiguous")
    body = matches[0]["body"]
    fenced = re.fullmatch(
        r"\s*```(?:yaml|yml)\s*\n(.*?)\n```\s*", body, flags=re.DOTALL | re.IGNORECASE
    )
    if fenced is None:
        raise RegistrySnapshotIntegrityError("spec execution method must be one fenced YAML mapping")
    try:
        payload = yaml.load(fenced.group(1), Loader=_UniqueKeyLoader)
    except yaml.YAMLError as exc:
        raise RegistrySnapshotIntegrityError(f"spec execution method YAML is invalid: {exc}") from exc
    if not isinstance(payload, dict):
        raise RegistrySnapshotIntegrityError("spec execution method must be a mapping")
    _validate_execution_method(payload)
    return payload


def _json_scalar(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def _validate_state_condition(value: Any, *, label: str) -> None:
    if not isinstance(value, Mapping):
        raise RegistrySnapshotIntegrityError(f"{label} must be a mapping")
    if set(value) != {"kind", "path", "equals"}:
        raise RegistrySnapshotIntegrityError(f"{label} fields are invalid")
    path = value.get("path")
    if (
        value.get("kind") != "scientific_state_equals"
        or not isinstance(path, list)
        or not path
        or any(not isinstance(part, str) or not part for part in path)
        or not _json_scalar(value.get("equals"))
    ):
        raise RegistrySnapshotIntegrityError(f"{label} is invalid")


def _validate_admission_path(path: Any) -> bool:
    return (
        isinstance(path, list)
        and bool(path)
        and all(isinstance(part, str) and part for part in path)
    )


def _validate_one_artifact_source_admission(value: Any) -> None:
    if not isinstance(value, Mapping):
        raise RegistrySnapshotIntegrityError("SkillBinding artifact source admission must be a mapping")
    kind = value.get("kind")
    path = value.get("path")
    if kind == "json_field_equals":
        if (
            set(value) != {"kind", "path", "equals"}
            or not _validate_admission_path(path)
            or not _json_scalar(value.get("equals"))
        ):
            raise RegistrySnapshotIntegrityError("SkillBinding artifact source admission is invalid")
        return
    if kind == "json_field_not_null":
        if set(value) != {"kind", "path"} or not _validate_admission_path(path):
            raise RegistrySnapshotIntegrityError("SkillBinding artifact source admission is invalid")
        return
    raise RegistrySnapshotIntegrityError("SkillBinding artifact source admission is invalid")


def _validate_artifact_source_admission(value: Any) -> None:
    if isinstance(value, list):
        if not value:
            raise RegistrySnapshotIntegrityError("SkillBinding artifact source admission is invalid")
        for item in value:
            _validate_one_artifact_source_admission(item)
        return
    _validate_one_artifact_source_admission(value)


def _validate_execution_method(value: Any) -> None:
    if not isinstance(value, Mapping):
        raise RegistrySnapshotIntegrityError("SubstageDefinition.executionMethod must be a mapping")
    required = {
        "methodId", "methodVersion", "semantics", "roles",
        "completionRoleIds", "stopConditions", "qualityConstraints",
    }
    optional = {"reentryTransitions"}
    if not required <= set(value) or not set(value) <= required | optional:
        raise RegistrySnapshotIntegrityError("SubstageDefinition.executionMethod fields are invalid")
    if (
        not isinstance(value.get("methodId"), str)
        or not value["methodId"]
        or not isinstance(value.get("methodVersion"), int)
        or isinstance(value["methodVersion"], bool)
        or value["methodVersion"] < 1
        or value.get("semantics") != "role_graph_v1"
    ):
        raise RegistrySnapshotIntegrityError("SubstageDefinition.executionMethod identity is invalid")
    roles = value.get("roles")
    if not isinstance(roles, list) or not roles:
        raise RegistrySnapshotIntegrityError("executionMethod roles are invalid")
    role_ids: list[str] = []
    graph: dict[str, list[str]] = {}
    for role in roles:
        role_fields = {
            "roleId", "requirement", "dependsOn", "capabilityKinds",
            "releasePolicy", "failurePolicy", "parallelism",
        }
        if (
            not isinstance(role, Mapping)
            or not role_fields <= set(role)
            or not set(role) <= role_fields | {"qualificationContract"}
        ):
            raise RegistrySnapshotIntegrityError("executionMethod role fields are invalid")
        role_id = role.get("roleId")
        depends_on = role.get("dependsOn")
        kinds = role.get("capabilityKinds")
        release = role.get("releasePolicy")
        qualification = role.get("qualificationContract")
        if (
            not isinstance(role_id, str)
            or not role_id
            or role_id in role_ids
            or role.get("requirement") not in {"required", "optional", "conditional"}
            or not isinstance(depends_on, list)
            or any(not isinstance(item, str) or not item for item in depends_on)
            or len(set(depends_on)) != len(depends_on)
            or not isinstance(kinds, list)
            or not kinds
            or any(kind not in {"agent", "skill"} for kind in kinds)
            or len(set(kinds)) != len(kinds)
            or role.get("failurePolicy") not in {"fail_closed", "continue_degraded", "isolate_item"}
            or role.get("parallelism") not in {"single", "per_item"}
        ):
            raise RegistrySnapshotIntegrityError("executionMethod role is invalid")
        if qualification is not None:
            if not isinstance(qualification, Mapping) or set(qualification) != {
                "objective", "requiredInputRefs", "outputArtifactTypes", "judgmentRequirement"
            }:
                raise RegistrySnapshotIntegrityError(
                    "executionMethod role qualificationContract fields are invalid"
                )
            role_inputs = qualification.get("requiredInputRefs")
            role_outputs = qualification.get("outputArtifactTypes")
            if (
                not isinstance(qualification.get("objective"), str)
                or not qualification["objective"].strip()
                or not isinstance(role_inputs, list)
                or any(not isinstance(item, str) or not item for item in role_inputs)
                or len(set(role_inputs)) != len(role_inputs)
                or not isinstance(role_outputs, list)
                or any(not isinstance(item, str) or not item for item in role_outputs)
                or len(set(role_outputs)) != len(role_outputs)
                or qualification.get("judgmentRequirement") not in {
                    "none", "deterministic", "research_synthesis",
                    "domain_professional", "human_authority",
                }
            ):
                raise RegistrySnapshotIntegrityError(
                    "executionMethod role qualificationContract is invalid"
                )
        if not isinstance(release, Mapping) or release.get("kind") not in {"always", "scientific_state_equals"}:
            raise RegistrySnapshotIntegrityError("executionMethod releasePolicy is invalid")
        if release.get("kind") == "always":
            if set(release) != {"kind"}:
                raise RegistrySnapshotIntegrityError("executionMethod always releasePolicy is invalid")
        else:
            _validate_state_condition(release, label="executionMethod releasePolicy")
        role_ids.append(role_id)
        graph[role_id] = list(depends_on)
    known = set(role_ids)
    if any(dep not in known for deps in graph.values() for dep in deps):
        raise RegistrySnapshotIntegrityError("executionMethod dependency references an unknown role")
    visiting: set[str] = set()
    visited: set[str] = set()
    def visit(role_id: str) -> None:
        if role_id in visiting:
            raise RegistrySnapshotIntegrityError("executionMethod role dependency contains a cycle")
        if role_id in visited:
            return
        visiting.add(role_id)
        for dep in graph[role_id]:
            visit(dep)
        visiting.remove(role_id)
        visited.add(role_id)
    for role_id in role_ids:
        visit(role_id)
    transitions = value.get("reentryTransitions") or []
    if not isinstance(transitions, list):
        raise RegistrySnapshotIntegrityError("executionMethod reentryTransitions are invalid")
    seen_transitions: set[str] = set()
    claimed_repeat_roles: set[str] = set()
    for transition in transitions:
        if not isinstance(transition, Mapping) or set(transition) != {
            "transitionId", "afterRoleId", "restartRoleId", "repeatRoleIds", "source", "action"
        }:
            raise RegistrySnapshotIntegrityError("executionMethod reentry transition fields are invalid")
        transition_id = transition.get("transitionId")
        repeat_roles = transition.get("repeatRoleIds")
        after_role = transition.get("afterRoleId")
        restart_role = transition.get("restartRoleId")
        if (
            not isinstance(transition_id, str)
            or not transition_id
            or transition_id in seen_transitions
            or after_role not in known
            or restart_role not in known
            or transition.get("action") != "start_next_iteration"
            or not isinstance(repeat_roles, list)
            or not repeat_roles
            or any(role_id not in known for role_id in repeat_roles)
            or len(set(repeat_roles)) != len(repeat_roles)
            or after_role not in repeat_roles
            or restart_role not in repeat_roles
            or bool(set(repeat_roles) & claimed_repeat_roles)
        ):
            raise RegistrySnapshotIntegrityError("executionMethod reentry transition is invalid")
        _validate_state_condition(
            transition.get("source"), label="executionMethod reentry transition source"
        )
        seen_transitions.add(transition_id)
        claimed_repeat_roles.update(repeat_roles)
    completion = value.get("completionRoleIds")
    if (
        not isinstance(completion, list)
        or not completion
        or any(role_id not in known for role_id in completion)
        or len(set(completion)) != len(completion)
    ):
        raise RegistrySnapshotIntegrityError("executionMethod completionRoleIds are invalid")
    required_roles = {role["roleId"] for role in roles if role["requirement"] == "required"}
    if not set(completion) <= required_roles:
        raise RegistrySnapshotIntegrityError("executionMethod completion roles must be required")
    stops = value.get("stopConditions")
    if not isinstance(stops, list):
        raise RegistrySnapshotIntegrityError("executionMethod stopConditions are invalid")
    seen_conditions: set[str] = set()
    for condition in stops:
        if not isinstance(condition, Mapping) or set(condition) != {
            "conditionId", "afterRoleId", "source", "action"
        }:
            raise RegistrySnapshotIntegrityError("executionMethod stop condition fields are invalid")
        condition_id = condition.get("conditionId")
        if (
            not isinstance(condition_id, str)
            or not condition_id
            or condition_id in seen_conditions
            or condition.get("afterRoleId") not in known
            or condition.get("action") != "complete_method"
        ):
            raise RegistrySnapshotIntegrityError("executionMethod stop condition is invalid")
        _validate_state_condition(condition.get("source"), label="executionMethod stop condition source")
        seen_conditions.add(condition_id)
    constraints = value.get("qualityConstraints")
    if (
        not isinstance(constraints, list)
        or any(not isinstance(item, str) or not item for item in constraints)
        or len(set(constraints)) != len(constraints)
    ):
        raise RegistrySnapshotIntegrityError("executionMethod qualityConstraints are invalid")


def _parse_spec_document_v1(pack: str, ref: str, content: str, *, repo_path: str | None = None) -> dict[str, Any]:
    if not isinstance(pack, str) or not pack or not isinstance(ref, str) or not ref:
        raise RegistrySnapshotIntegrityError("spec identity must be non-empty strings")
    if not isinstance(content, str):
        raise RegistrySnapshotIntegrityError("spec content must be UTF-8 text")
    if "\\" in ref:
        raise RegistrySnapshotIntegrityError("spec ref is not canonical")
    normalized = _normalize_lf(content)
    relative_ref = PurePosixPath(ref)
    if relative_ref.is_absolute() or any(part in {"", ".", ".."} for part in relative_ref.parts):
        raise RegistrySnapshotIntegrityError("spec ref is not canonical")
    if relative_ref.parts[0] != "specs":
        relative_ref = PurePosixPath("specs") / relative_ref
    canonical_repo_path = f"schemas/{pack}/{relative_ref.as_posix()}"
    if repo_path is not None and repo_path != canonical_repo_path:
        raise RegistrySnapshotIntegrityError("spec repoPath does not match canonical ref")
    if ref.lower().endswith((".yaml", ".yml")):
        try:
            yaml_document = yaml.load(normalized, Loader=_UniqueKeyLoader)
        except yaml.YAMLError as exc:
            raise RegistrySnapshotIntegrityError(f"spec YAML is invalid: {exc}") from exc
        if not isinstance(yaml_document, dict):
            raise RegistrySnapshotIntegrityError("spec YAML must contain a mapping")
        parsed = {
            "format": "yaml-v1",
            "parserVersion": "registry-spec-parser.v1",
            "headings": [],
            "sections": [],
            "tables": [],
            "checklists": [],
            "yamlDocument": yaml_document,
        }
        unknown = {"status": "unknown", "sections": []}
        result = {
            "specRef": ref,
            "specSource": {
                "ref": ref,
                "repoPath": canonical_repo_path,
                "content": normalized,
                "tracked": True,
                "parsed": parsed,
            },
            "inputContract": {**unknown, "requiredInputRefs": {"status": "unknown", "values": []}},
            "outputContract": {**unknown, "artifactTypes": {"status": "unknown", "values": []}},
            "acceptanceContract": {**unknown, "criterionRefs": {"status": "unknown", "values": []}},
        }
        if "executionMethod" in yaml_document:
            _validate_execution_method(yaml_document["executionMethod"])
            result["executionMethod"] = yaml_document["executionMethod"]
        return result

    headings: list[dict[str, Any]] = []
    sections: list[dict[str, Any]] = []
    heading_pattern = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
    current: dict[str, Any] | None = None
    body_lines: list[str] = []
    fenced = False
    for line in normalized.split("\n"):
        if line.lstrip().startswith("```"):
            fenced = not fenced
            if current is not None:
                body_lines.append(line)
            continue
        if fenced:
            if current is not None:
                body_lines.append(line)
            continue
        match = heading_pattern.match(line)
        if match:
            if current is not None:
                current["body"] = "\n".join(body_lines).strip("\n")
                sections.append(current)
            current = {"level": len(match.group(1)), "title": match.group(2).strip()}
            headings.append({"level": current["level"], "title": current["title"]})
            body_lines = []
        elif current is not None:
            body_lines.append(line)
    if current is not None:
        current["body"] = "\n".join(body_lines).strip("\n")
        sections.append(current)

    tables: list[dict[str, Any]] = []
    for section_index, section in enumerate(sections):
        lines = section["body"].split("\n")
        index = 0
        while index + 1 < len(lines):
            if "|" not in lines[index] or not re.fullmatch(r"\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*", lines[index + 1]):
                index += 1
                continue
            def cells(value: str) -> list[str]:
                return [cell.strip() for cell in value.strip().strip("|").split("|")]
            headers = cells(lines[index])
            rows: list[list[str]] = []
            index += 2
            while index < len(lines) and "|" in lines[index]:
                row = cells(lines[index])
                if len(row) != len(headers):
                    break
                rows.append(row)
                index += 1
            tables.append({"sectionIndex": section_index, "headers": headers, "rows": rows})

    by_kind: dict[str, list[dict[str, Any]]] = {"input": [], "output": [], "acceptance": []}
    for index, section in enumerate(sections):
        kind = _section_kind(section["title"])
        if kind:
            by_kind[kind].append({"sectionIndex": index, "title": section["title"], "body": section["body"]})

    def contract(kind: str, values_key: str) -> dict[str, Any]:
        matched = by_kind[kind]
        values: list[str] = []
        for section in matched:
            values.extend(_explicit_list_values(section["body"]))
        values = list(dict.fromkeys(values))
        return {
            "status": "known" if matched else "unknown",
            "sections": matched,
            values_key: {"status": "known" if values else "unknown", "values": values},
        }

    checklists = [
        {"sectionIndex": section["sectionIndex"], "text": match.group(1).strip()}
        for section in by_kind["acceptance"]
        for line in section["body"].split("\n")
        if (match := re.fullmatch(r"\s*[-*]\s+\[[ xX]\]\s+(.+?)\s*", line))
    ]
    result = {
        "specRef": ref,
        "specSource": {
            "ref": ref,
            "repoPath": canonical_repo_path,
            "content": normalized,
            "tracked": True,
            "parsed": {
                "format": "markdown-v1",
                "parserVersion": "registry-spec-parser.v1",
                "headings": headings,
                "sections": sections,
                "tables": tables,
                "checklists": checklists,
                "yamlDocument": None,
            },
        },
        "inputContract": contract("input", "requiredInputRefs"),
        "outputContract": contract("output", "artifactTypes"),
        "acceptanceContract": contract("acceptance", "criterionRefs"),
    }
    execution_method = _parse_execution_method_section(sections)
    if execution_method is not None:
        result["executionMethod"] = execution_method
    return result


def _parse_contract_metadata_section(sections: list[dict[str, Any]]) -> dict[str, Any] | None:
    matches = [
        section for section in sections
        if section["title"].strip().lower() in {"contract metadata", "合同元数据"}
    ]
    if not matches:
        return None
    if len(matches) != 1:
        raise RegistrySnapshotIntegrityError("spec contract metadata section is ambiguous")
    body = matches[0]["body"]
    fenced = re.fullmatch(
        r"\s*```(?:yaml|yml)\s*\n(.*?)\n```\s*", body, flags=re.DOTALL | re.IGNORECASE
    )
    if fenced is None:
        raise RegistrySnapshotIntegrityError("spec contract metadata must be one fenced YAML mapping")
    try:
        payload = yaml.load(fenced.group(1), Loader=_UniqueKeyLoader)
    except yaml.YAMLError as exc:
        raise RegistrySnapshotIntegrityError(f"spec contract metadata YAML is invalid: {exc}") from exc
    if not isinstance(payload, dict):
        raise RegistrySnapshotIntegrityError("spec contract metadata must be a mapping")
    allowed = {
        "schemaVersion", "judgmentRequirement", "evaluatorRefs", "deterministicValidatorRefs"
    }
    if set(payload) - allowed or payload.get("schemaVersion") != "definition-contract-metadata.v1":
        raise RegistrySnapshotIntegrityError("spec contract metadata fields are invalid")
    judgment = payload.get("judgmentRequirement")
    if judgment is not None and judgment not in {
        "deterministic", "research_synthesis", "domain_professional", "human_authority"
    }:
        raise RegistrySnapshotIntegrityError("spec contract metadata judgmentRequirement is invalid")
    for key in ("evaluatorRefs", "deterministicValidatorRefs"):
        if key not in payload:
            continue
        values = payload[key]
        if (
            not isinstance(values, list)
            or any(not isinstance(value, str) or not value.strip() for value in values)
            or len(set(values)) != len(values)
        ):
            raise RegistrySnapshotIntegrityError(f"spec contract metadata {key} is invalid")
    return payload


def _parse_spec_document_v2(pack: str, ref: str, content: str, *, repo_path: str | None = None) -> dict[str, Any]:
    result = _parse_spec_document_v1(pack, ref, content, repo_path=repo_path)
    parsed = result["specSource"]["parsed"]
    if parsed["format"] != "markdown-v1":
        raise RegistrySnapshotIntegrityError("registry-spec-parser.v2 requires a Markdown Definition spec")
    sections = parsed["sections"]
    metadata = _parse_contract_metadata_section(sections)
    if metadata is None:
        raise RegistrySnapshotIntegrityError("registry-spec-parser.v2 requires Contract Metadata")

    def contract_v2(kind: str, values_key: str) -> dict[str, Any]:
        matched = [
            {"sectionIndex": index, "title": section["title"], "body": section["body"]}
            for index, section in enumerate(sections)
            if _section_kind(section["title"]) == kind
        ]
        values: list[str] = []
        for section in matched:
            values.extend(_explicit_list_values_v2(section["body"]))
        values = list(dict.fromkeys(values))
        return {
            "status": "known" if matched else "unknown",
            "sections": matched,
            values_key: {"status": "known" if values else "unknown", "values": values},
        }

    result["inputContract"] = contract_v2("input", "requiredInputRefs")
    result["outputContract"] = contract_v2("output", "artifactTypes")
    acceptance_sections = [
        {"sectionIndex": index, "title": section["title"], "body": section["body"]}
        for index, section in enumerate(sections)
        if _section_kind(section["title"]) == "acceptance"
    ]
    quality_rules: list[str] = []
    for section in acceptance_sections:
        quality_rules.extend(_explicit_list_values_v2(section["body"]))
    quality_rules = list(dict.fromkeys(quality_rules))

    def list_field(key: str) -> dict[str, Any]:
        if key not in metadata:
            return {"status": "unknown", "values": []}
        return {"status": "known", "values": list(metadata[key])}

    result["acceptanceContract"] = {
        "status": "known" if acceptance_sections or metadata is not None else "unknown",
        "sections": acceptance_sections,
        "qualityRuleRefs": {
            "status": "known" if acceptance_sections else "unknown",
            "values": quality_rules,
        },
        "judgmentRequirement": {
            "status": "known" if "judgmentRequirement" in metadata else "unknown",
            "value": metadata.get("judgmentRequirement"),
        },
        "evaluatorRefs": list_field("evaluatorRefs"),
        "deterministicValidatorRefs": list_field("deterministicValidatorRefs"),
    }
    parsed["parserVersion"] = "registry-spec-parser.v2"
    parsed["contractMetadata"] = metadata
    return result


_SPEC_PARSERS = {
    "registry-spec-parser.v1": _parse_spec_document_v1,
    "registry-spec-parser.v2": _parse_spec_document_v2,
}


def parse_spec_document(
    pack: str,
    ref: str,
    content: str,
    *,
    repo_path: str | None = None,
    parser_version: str | None = None,
) -> dict[str, Any]:
    if parser_version is None:
        parser_version = (
            "registry-spec-parser.v2"
            if re.search(r"^#{1,6}\s+(?:Contract Metadata|合同元数据)\s*$", _normalize_lf(content), flags=re.MULTILINE | re.IGNORECASE)
            else "registry-spec-parser.v1"
        )
    parser = _SPEC_PARSERS.get(parser_version)
    if parser is None:
        raise RegistrySnapshotIntegrityError("specSource parserVersion is unsupported")
    return parser(pack, ref, content, repo_path=repo_path)


@dataclass(frozen=True)
class RegistrySnapshot:
    schema_version: int
    content_hash: str
    canonical_payload: bytes
    runtime_payload: bytes | None = None

    @property
    def envelope(self) -> dict[str, Any]:
        """Return the validated runtime view without changing archived identity."""
        return json.loads(self.runtime_payload or self.canonical_payload)


class RegistrySnapshotArchive(Protocol):
    def put(self, snapshot: RegistrySnapshot) -> RegistrySnapshot: ...

    def get(self, content_hash: str) -> RegistrySnapshot: ...


def _canonical_json(value: Any) -> bytes:
    try:
        rendered = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise RegistrySnapshotIntegrityError(f"snapshot is not canonical JSON: {exc}") from exc
    return rendered.encode("utf-8")


def _dedupe_and_sort(values: list[Any]) -> list[Any]:
    unique = {_canonical_json(value): value for value in values}
    return [unique[key] for key in sorted(unique)]


def _normalize(value: Any, *, field: str | None = None) -> Any:
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise RegistrySnapshotIntegrityError("snapshot object keys must be strings")
        return {key: _normalize(item, field=key) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        normalized = [_normalize(item) for item in value]
        if field in SET_LIKE_FIELDS and field not in ORDERED_FIELDS:
            return _dedupe_and_sort(normalized)
        return normalized
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise RegistrySnapshotIntegrityError(f"snapshot contains unsupported value: {type(value).__name__}")


def canonicalize_registry_value(value: Any) -> Any:
    """Canonicalize a Registry value with the snapshot's existing authority."""
    return _normalize(value)


def _identity(row: Any, *fields: str) -> tuple[Any, ...]:
    if not isinstance(row, dict):
        raise RegistrySnapshotIntegrityError("snapshot collection members must be objects")
    identity = tuple(row.get(field) for field in fields)
    if any(value is None or value == "" for value in identity):
        raise RegistrySnapshotIntegrityError(f"snapshot collection identity is incomplete: {fields}")
    return identity


def _sort_collection(rows: Any, *fields: str) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        raise RegistrySnapshotIntegrityError("snapshot collection must be an array")
    normalized = [_normalize(row) for row in rows]
    seen: dict[tuple[Any, ...], bytes] = {}
    result: list[dict[str, Any]] = []
    for row in normalized:
        identity = _identity(row, *fields)
        payload = _canonical_json(row)
        previous = seen.get(identity)
        if previous is not None and previous != payload:
            raise RegistrySnapshotIntegrityError(f"snapshot identity collision: {identity}")
        if previous is None:
            seen[identity] = payload
            result.append(row)
    return sorted(result, key=lambda row: (_identity(row, *fields), _canonical_json(row)))


def _require_fields(
    row: Any,
    fields: set[str],
    label: str,
    *,
    optional: set[str] | None = None,
) -> dict[str, Any]:
    if not isinstance(row, dict):
        raise RegistrySnapshotIntegrityError(f"{label} must be an object")
    missing = fields - set(row)
    if missing:
        raise RegistrySnapshotIntegrityError(f"{label} missing fields: {sorted(missing)}")
    extra = set(row) - fields - (optional or set())
    if extra:
        raise RegistrySnapshotIntegrityError(f"{label} contains unknown fields: {sorted(extra)}")
    return row


def _strict_bool(row: Mapping[str, Any], field: str, label: str) -> None:
    if not isinstance(row.get(field), bool):
        raise RegistrySnapshotIntegrityError(f"{label}.{field} must be a boolean")


def _positive_int(row: Mapping[str, Any], field: str, label: str) -> None:
    value = row.get(field)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise RegistrySnapshotIntegrityError(f"{label}.{field} must be a positive integer")



def validate_skill_finalization_source_contract(source_contract: Any) -> None:
    """Validate the finalization authority embedded in one frozen Skill source contract."""
    if not isinstance(source_contract, dict):
        raise RegistrySnapshotIntegrityError("SkillDefinition.sourceContract must be an object")
    declaration = source_contract.get("finalization")
    frozen_finalization = source_contract.get("frozenFinalization")
    if (declaration is None) != (frozen_finalization is None):
        raise RegistrySnapshotIntegrityError("Skill finalization declaration/frozen contract mismatch")
    if declaration is None:
        return
    try:
        declared = validate_finalization_declaration(declaration)
        frozen_contract = validate_frozen_finalization_contract(frozen_finalization)
    except ValueError as exc:
        raise RegistrySnapshotIntegrityError("Skill finalization contract is invalid") from exc
    frozen_entrypoint = frozen_contract["entrypoint"]
    if declared["entrypoint"] != f"{frozen_entrypoint['module']}:{frozen_entrypoint['callable']}":
        raise RegistrySnapshotIntegrityError("Skill finalization entrypoint is not frozen faithfully")
    if declared["draft_schema"] != frozen_contract["draftSchema"]["path"]:
        raise RegistrySnapshotIntegrityError("Skill finalization draft schema is not frozen faithfully")



def _validate_skill_binding_input_sources(value: Any) -> None:
    contract = _require_fields(value, {"contractVersion", "fields"}, "SkillBinding.inputSources")
    _positive_int(contract, "contractVersion", "SkillBinding.inputSources")
    if contract["contractVersion"] != 2:
        raise RegistrySnapshotIntegrityError("SkillBinding inputSources contractVersion is unsupported")
    fields = contract["fields"]
    if not isinstance(fields, list):
        raise RegistrySnapshotIntegrityError("SkillBinding inputSources fields must be a list")
    targets: set[str] = set()
    for entry in fields:
        entry = _require_fields(
            entry, {"targetField", "sourcePresence", "source"}, "SkillBinding.inputSources field",
            optional={"defaultValue"},
        )
        target = entry["targetField"]
        if not isinstance(target, str) or not target or target in targets:
            raise RegistrySnapshotIntegrityError("SkillBinding inputSources targetField is invalid")
        targets.add(target)
        if entry["sourcePresence"] not in {"required", "optional", "conditional"}:
            raise RegistrySnapshotIntegrityError("SkillBinding inputSources sourcePresence is invalid")
        if "defaultValue" in entry and entry["sourcePresence"] != "optional":
            raise RegistrySnapshotIntegrityError("SkillBinding inputSources defaultValue requires optional source")
        source = entry["source"]
        if not isinstance(source, dict):
            raise RegistrySnapshotIntegrityError("SkillBinding inputSources source must be an object")
        kind = source.get("kind")
        if kind == "artifact":
            source = _require_fields(
                source, {"kind", "bindingId", "bindingVersion", "field"}, "SkillBinding artifact source",
                optional={"cardinality", "admission"},
            )
            _positive_int(source, "bindingVersion", "SkillBinding artifact source")
            if not isinstance(source["bindingId"], str) or not source["bindingId"] or not isinstance(source["field"], str) or not source["field"]:
                raise RegistrySnapshotIntegrityError("SkillBinding artifact source is invalid")
            try:
                binding_source_path_steps(source["field"], ())
            except BindingSourcePathError as exc:
                raise RegistrySnapshotIntegrityError(
                    "SkillBinding artifact source field path is invalid"
                ) from exc
            if source.get("cardinality", "one") not in {"one", "many"}:
                raise RegistrySnapshotIntegrityError("SkillBinding artifact source cardinality is invalid")
            if "admission" in source:
                _validate_artifact_source_admission(source["admission"])
        elif kind == "scientificState":
            _require_fields(source, {"kind", "field"}, "SkillBinding ScientificState source")
            if not isinstance(source["field"], str) or not source["field"]:
                raise RegistrySnapshotIntegrityError("SkillBinding ScientificState source is invalid")
        else:
            raise RegistrySnapshotIntegrityError("SkillBinding inputSources kind must be Artifact or ScientificState")


def _validate_skill_binding_runtime_inputs(value: Any, *, scientific_targets: set[str]) -> None:
    contract = _require_fields(value, {"contractVersion", "fields"}, "SkillBinding.runtimeInputs")
    _positive_int(contract, "contractVersion", "SkillBinding.runtimeInputs")
    if contract["contractVersion"] != 2:
        raise RegistrySnapshotIntegrityError("SkillBinding runtimeInputs contractVersion is unsupported")
    fields = contract["fields"]
    if not isinstance(fields, list):
        raise RegistrySnapshotIntegrityError("SkillBinding runtimeInputs fields must be a list")
    targets = set(scientific_targets)
    for entry in fields:
        entry = _require_fields(entry, {"targetField", "source"}, "SkillBinding.runtimeInputs field")
        target = entry["targetField"]
        if not isinstance(target, str) or not target or target in targets:
            raise RegistrySnapshotIntegrityError("SkillBinding runtimeInputs targetField is invalid")
        targets.add(target)
        source = entry["source"]
        if not isinstance(source, dict):
            raise RegistrySnapshotIntegrityError("SkillBinding runtimeInputs source must be an object")
        kind = source.get("kind")
        if kind in {"executionIdentity", "invocationContext"}:
            _require_fields(source, {"kind", "field"}, "SkillBinding runtime source")
            if not isinstance(source["field"], str) or not source["field"]:
                raise RegistrySnapshotIntegrityError("SkillBinding runtime source field is invalid")
            if kind == "executionIdentity" and source["field"] != "task_id":
                raise RegistrySnapshotIntegrityError("SkillBinding executionIdentity field is invalid")
        elif kind == "literal":
            _require_fields(source, {"kind", "value"}, "SkillBinding literal source")
        else:
            raise RegistrySnapshotIntegrityError("SkillBinding runtimeInputs source kind is invalid")


_JSON_TYPES = frozenset({"array", "boolean", "integer", "null", "number", "object", "string"})
_COMPLEX_HANDOFF_KEYWORDS = frozenset({
    "allOf", "anyOf", "oneOf", "not", "if", "then", "else", "contains",
    "dependentRequired", "dependentSchemas", "patternProperties", "propertyNames",
    "prefixItems", "unevaluatedItems", "unevaluatedProperties",
})
_REFERENCE_ANNOTATION_KEYS = frozenset({"$ref", "$schema", "$comment", "title", "description", "default", "examples"})


def _deref_local_schema(node: Any, root: Mapping[str, Any]) -> Any:
    seen: set[str] = set()
    while isinstance(node, Mapping) and "$ref" in node:
        ref = node["$ref"]
        if not isinstance(ref, str) or not ref.startswith("#"):
            raise _UnsupportedHandoffSchema("non_local_ref")
        if set(node) - _REFERENCE_ANNOTATION_KEYS:
            raise _UnsupportedHandoffSchema("ref_with_constraints")
        if ref in seen:
            raise _UnsupportedHandoffSchema("cyclic_local_ref")
        seen.add(ref)
        current: Any = root
        if ref != "#":
            if not ref.startswith("#/"):
                raise _UnsupportedHandoffSchema("invalid_local_ref")
            for encoded_part in ref[2:].split("/"):
                part = encoded_part.replace("~1", "/").replace("~0", "~")
                if not isinstance(current, Mapping) or part not in current:
                    raise _SchemaPathError("local_ref_missing", ref)
                current = current[part]
        node = current
    if not isinstance(node, (bool, Mapping)):
        raise _UnsupportedHandoffSchema("invalid_schema_node")
    return node


def _schema_mapping(node: Any, root: Mapping[str, Any]) -> Mapping[str, Any] | None:
    resolved = _deref_local_schema(node, root)
    if resolved is False:
        return None
    if resolved is True:
        return {}
    if any(key in resolved for key in _COMPLEX_HANDOFF_KEYWORDS):
        raise _UnsupportedHandoffSchema("complex_schema_combination")
    return resolved


def _schema_properties(node: Any, root: Mapping[str, Any]) -> Mapping[str, Any]:
    mapping = _schema_mapping(node, root)
    if mapping is None:
        return {}
    properties = mapping.get("properties", {})
    if not isinstance(properties, Mapping) or any(
        not isinstance(name, str) or not isinstance(value, (bool, Mapping))
        for name, value in properties.items()
    ):
        raise _UnsupportedHandoffSchema("invalid_properties")
    return properties


def _schema_required(node: Any, root: Mapping[str, Any]) -> frozenset[str]:
    mapping = _schema_mapping(node, root)
    if mapping is None:
        return frozenset()
    required = mapping.get("required", [])
    if (
        not isinstance(required, list)
        or any(not isinstance(value, str) or not value for value in required)
        or len(set(required)) != len(required)
    ):
        raise _UnsupportedHandoffSchema("invalid_required")
    return frozenset(required)


def _schema_additional_properties(node: Any, root: Mapping[str, Any]) -> Any:
    mapping = _schema_mapping(node, root)
    if mapping is None:
        return False
    additional = mapping.get("additionalProperties", True)
    if not isinstance(additional, (bool, Mapping)):
        raise _UnsupportedHandoffSchema("invalid_additional_properties")
    return additional


def _schema_types(node: Any, root: Mapping[str, Any]) -> frozenset[str]:
    mapping = _schema_mapping(node, root)
    if mapping is None:
        return frozenset()
    declared = mapping.get("type")
    if declared is None:
        return _JSON_TYPES
    if isinstance(declared, str):
        values = [declared]
    elif isinstance(declared, list):
        values = declared
    else:
        raise _UnsupportedHandoffSchema("invalid_type")
    if (
        not values
        or any(not isinstance(value, str) or value not in _JSON_TYPES for value in values)
        or len(set(values)) != len(values)
    ):
        raise _UnsupportedHandoffSchema("invalid_type")
    return frozenset(values)


def _json_value_key(value: Any) -> bytes:
    try:
        return _canonical_json(value)
    except RegistrySnapshotIntegrityError as exc:
        raise _UnsupportedHandoffSchema("invalid_const_or_enum") from exc


def _unique_json_values(values: list[Any]) -> tuple[Any, ...]:
    unique: dict[bytes, Any] = {}
    for value in values:
        unique.setdefault(_json_value_key(value), value)
    return tuple(unique.values())


def _value_matches_types(value: Any, types: frozenset[str]) -> bool:
    if value is None:
        return "null" in types
    if isinstance(value, bool):
        return "boolean" in types
    if isinstance(value, int):
        return "integer" in types or "number" in types
    if isinstance(value, float):
        return "number" in types or ("integer" in types and value.is_integer())
    if isinstance(value, str):
        return "string" in types
    if isinstance(value, list):
        return "array" in types
    if isinstance(value, Mapping):
        return "object" in types
    return False


def _schema_constraint(node: Any, root: Mapping[str, Any]) -> _SchemaConstraint:
    mapping = _schema_mapping(node, root)
    if mapping is None:
        return _SchemaConstraint(frozenset(), ())
    types = _schema_types(mapping, root)
    values: list[Any] | None = None
    if "const" in mapping:
        values = [mapping["const"]]
    if "enum" in mapping:
        enum = mapping["enum"]
        if not isinstance(enum, list):
            raise _UnsupportedHandoffSchema("invalid_enum")
        enum_values = list(_unique_json_values(enum))
        if values is None:
            values = enum_values
        else:
            enum_keys = {_json_value_key(value) for value in enum_values}
            values = [value for value in values if _json_value_key(value) in enum_keys]
    if values is not None:
        return _SchemaConstraint(
            types,
            tuple(value for value in _unique_json_values(values) if _value_matches_types(value, types)),
        )
    if types <= {"null", "boolean"}:
        finite = [value for value in (None, False, True) if _value_matches_types(value, types)]
        return _SchemaConstraint(types, tuple(finite))
    return _SchemaConstraint(types, None)


def _constraint_range(constraint: _SchemaConstraint) -> str:
    if constraint.values is not None:
        rendered = json.dumps(list(constraint.values), ensure_ascii=False, separators=(",", ":"))
        return f"values={rendered[:160]}"
    return "types=" + ",".join(sorted(constraint.types))


def _constraint_accepts_value(constraint: _SchemaConstraint, value: Any) -> bool:
    if not _value_matches_types(value, constraint.types):
        return False
    if constraint.values is None:
        return True
    value_key = _json_value_key(value)
    return any(_json_value_key(candidate) == value_key for candidate in constraint.values)


def _consumer_value_conflict_category(node: Any, root: Mapping[str, Any]) -> str:
    mapping = _schema_mapping(node, root)
    if mapping is not None and "const" in mapping:
        return "const"
    if mapping is not None and "enum" in mapping:
        return "enum"
    return "type"


def _schema_items(node: Any, root: Mapping[str, Any]) -> Any:
    mapping = _schema_mapping(node, root)
    if mapping is None:
        return False
    items = mapping.get("items", True)
    if isinstance(items, list) or not isinstance(items, (bool, Mapping)):
        raise _UnsupportedHandoffSchema("unsupported_array_items")
    return items


def _schema_property(node: Any, root: Mapping[str, Any], field: str) -> Any:
    properties = _schema_properties(node, root)
    if field in properties:
        return properties[field]
    return _schema_additional_properties(node, root)


def _schema_accepts_value(value: Any, node: Any, root: Mapping[str, Any], depth: int = 0) -> str | None:
    if depth > 24:
        raise _UnsupportedHandoffSchema("schema_nesting_limit")
    constraint = _schema_constraint(node, root)
    if not _value_matches_types(value, constraint.types):
        return "type"
    if constraint.values is not None and not _constraint_accepts_value(constraint, value):
        return _consumer_value_conflict_category(node, root)
    if isinstance(value, list):
        item_schema = _schema_items(node, root)
        for item in value:
            conflict = _schema_accepts_value(item, item_schema, root, depth + 1)
            if conflict is not None:
                return f"items.{conflict}"
    if isinstance(value, Mapping):
        required = _schema_required(node, root)
        if not required <= set(value):
            return "required"
        properties = _schema_properties(node, root)
        additional = _schema_additional_properties(node, root)
        for field, item in value.items():
            child = properties.get(field, additional)
            if child is False:
                return "additional_properties"
            conflict = _schema_accepts_value(item, child, root, depth + 1)
            if conflict is not None:
                return f"property.{field}.{conflict}"
    return None


def _types_assignable(producer: frozenset[str], consumer: frozenset[str]) -> bool:
    return all(
        value in consumer or (value == "integer" and "number" in consumer)
        for value in producer
    )


def _compare_handoff_schema(
    producer_node: Any,
    producer_root: Mapping[str, Any],
    consumer_node: Any,
    consumer_root: Mapping[str, Any],
    depth: int = 0,
) -> _HandoffConflict | None:
    if depth > 24:
        raise _UnsupportedHandoffSchema("schema_nesting_limit")
    producer = _schema_constraint(producer_node, producer_root)
    consumer = _schema_constraint(consumer_node, consumer_root)
    producer_range = _constraint_range(producer)
    consumer_range = _constraint_range(consumer)
    if not producer.types:
        return None
    if producer.values is not None:
        for value in producer.values:
            category = _schema_accepts_value(value, consumer_node, consumer_root, depth + 1)
            if category is not None:
                return _HandoffConflict(category, producer_range, consumer_range)
        return None
    if not _types_assignable(producer.types, consumer.types):
        return _HandoffConflict("type", producer_range, consumer_range)
    if consumer.values is not None:
        return _HandoffConflict(
            _consumer_value_conflict_category(consumer_node, consumer_root),
            producer_range,
            consumer_range,
        )
    if "array" in producer.types:
        child = _compare_handoff_schema(
            _schema_items(producer_node, producer_root),
            producer_root,
            _schema_items(consumer_node, consumer_root),
            consumer_root,
            depth + 1,
        )
        if child is not None:
            return _HandoffConflict(f"items.{child.category}", producer_range, consumer_range)
    if "object" in producer.types:
        producer_required = _schema_required(producer_node, producer_root)
        for field in _schema_required(consumer_node, consumer_root):
            if field not in producer_required:
                return _HandoffConflict("required", producer_range, consumer_range)
            child = _compare_handoff_schema(
                _schema_property(producer_node, producer_root, field),
                producer_root,
                _schema_property(consumer_node, consumer_root, field),
                consumer_root,
                depth + 1,
            )
            if child is not None:
                return _HandoffConflict(
                    f"property.{field}.{child.category}", producer_range, consumer_range
                )
    return None


def _conditional_null_is_unavailable(
    producer_node: Any,
    producer_root: Mapping[str, Any],
    consumer_node: Any,
    consumer_root: Mapping[str, Any],
) -> bool:
    """A conditional source may use null solely to report unavailable material."""
    producer = _schema_constraint(producer_node, producer_root)
    if "null" not in producer.types:
        return False
    if producer.values is not None:
        return all(
            value is None
            or _schema_accepts_value(value, consumer_node, consumer_root) is None
            for value in producer.values
        )
    remaining_types = producer.types - {"null"}
    return not remaining_types or (
        _types_assignable(remaining_types, _schema_constraint(consumer_node, consumer_root).types)
        and _schema_constraint(consumer_node, consumer_root).values is None
    )


def _resolve_source_schema_path(
    schema: Mapping[str, Any],
    source_path: str,
) -> tuple[Any, bool]:
    root = _deref_local_schema(schema, schema)
    properties = _schema_properties(root, schema)
    try:
        steps = binding_source_path_steps(source_path, properties.keys())
    except BindingSourcePathError as exc:
        raise _SchemaPathError("source_path_invalid", exc.source_path) from exc
    current: Any = root
    guaranteed = True
    for step in steps:
        if "object" not in _schema_types(current, schema):
            raise _SchemaPathError("source_path_non_object", step)
        current_properties = _schema_properties(current, schema)
        if step in current_properties:
            guaranteed = guaranteed and step in _schema_required(current, schema)
            current = current_properties[step]
            continue
        current = _schema_additional_properties(current, schema)
        if current is False:
            raise _SchemaPathError("source_path_missing", step)
        guaranteed = False
    return current, guaranteed


def _resolve_target_schema_path(
    schema: Mapping[str, Any],
    target_path: str,
) -> tuple[Any, bool]:
    root = _deref_local_schema(schema, schema)
    properties = _schema_properties(root, schema)
    if target_path in properties:
        return properties[target_path], target_path in _schema_required(root, schema)
    additional = _schema_additional_properties(root, schema)
    if additional is False:
        raise _SchemaPathError("target_path_missing", target_path)
    return additional, target_path in _schema_required(root, schema)


def _resolve_direct_target_schema_path(
    schema: Mapping[str, Any],
    target_path: str,
) -> tuple[Any, bool]:
    root = _deref_local_schema(schema, schema)
    if not isinstance(root, Mapping):
        if root is False:
            raise _SchemaPathError("target_path_missing", target_path)
        return True, False
    properties = root.get("properties", {})
    if not isinstance(properties, Mapping) or any(
        not isinstance(name, str) or not isinstance(value, (bool, Mapping))
        for name, value in properties.items()
    ):
        raise _UnsupportedHandoffSchema("invalid_properties")
    required = root.get("required", [])
    if (
        not isinstance(required, list)
        or any(not isinstance(value, str) or not value for value in required)
        or len(set(required)) != len(required)
    ):
        raise _UnsupportedHandoffSchema("invalid_required")
    if target_path in properties:
        return properties[target_path], target_path in required
    additional = root.get("additionalProperties", True)
    if not isinstance(additional, (bool, Mapping)):
        raise _UnsupportedHandoffSchema("invalid_additional_properties")
    if additional is False:
        raise _SchemaPathError("target_path_missing", target_path)
    return additional, target_path in required


def _frozen_handoff_schema(skill: Mapping[str, Any], schema_name: str) -> Mapping[str, Any]:
    source_contract = skill.get("sourceContract")
    frozen_schemas = source_contract.get("frozenSchemas") if isinstance(source_contract, Mapping) else None
    frozen = frozen_schemas.get(schema_name) if isinstance(frozen_schemas, Mapping) else None
    schema = frozen.get("schema") if isinstance(frozen, Mapping) else None
    if not isinstance(schema, Mapping):
        raise _SchemaPathError(f"{schema_name}_schema_missing", schema_name)
    return schema


def _narrow_leaf_by_admission(
    child: Any,
    producer_root: Mapping[str, Any],
    admission: Mapping[str, Any],
    *,
    step: str,
) -> Any:
    kind = admission["kind"]
    if kind == "json_field_equals":
        equals = admission["equals"]
        if _schema_accepts_value(equals, child, producer_root) is not None:
            raise _SchemaPathError("admission_value_unavailable", step)
        narrowed_child: Any = {"const": equals}
        types = _schema_types(child, producer_root)
        if types and types != _JSON_TYPES:
            narrowed_child = (
                {"type": next(iter(types)), "const": equals}
                if len(types) == 1
                else {"type": sorted(types), "const": equals}
            )
        return narrowed_child
    if kind == "json_field_not_null":
        types = _schema_types(child, producer_root)
        remaining = types - {"null"}
        if not remaining:
            raise _SchemaPathError("admission_value_unavailable", step)
        mapping = _schema_mapping(child, producer_root)
        if mapping is None:
            return (
                {"type": next(iter(remaining))}
                if len(remaining) == 1
                else {"type": sorted(remaining)}
            )
        narrowed = {
            key: value
            for key, value in mapping.items()
            if key not in {"type", "enum", "const"}
        }
        if len(remaining) == 1:
            narrowed["type"] = next(iter(remaining))
        else:
            narrowed["type"] = sorted(remaining)
        return narrowed
    raise _SchemaPathError("admission_kind_unsupported", step)


def _narrow_producer_schema_by_admission(
    producer_node: Any,
    producer_root: Mapping[str, Any],
    admission: Mapping[str, Any],
) -> Any:
    """Prove handoff against the Binding-admitted value branch only."""
    path = list(admission["path"])
    current = producer_node
    for index, step in enumerate(path):
        if "object" not in _schema_types(current, producer_root):
            raise _SchemaPathError("admission_path_non_object", step)
        properties = _schema_properties(current, producer_root)
        if step not in properties:
            additional = _schema_additional_properties(current, producer_root)
            if additional is False:
                raise _SchemaPathError("admission_path_missing", step)
            child = additional
        else:
            child = properties[step]
        if index + 1 == len(path):
            narrowed_child = _narrow_leaf_by_admission(
                child, producer_root, admission, step=step
            )
        else:
            nested = {"kind": admission["kind"], "path": path[index + 1 :]}
            if "equals" in admission:
                nested["equals"] = admission["equals"]
            narrowed_child = _narrow_producer_schema_by_admission(
                child,
                producer_root,
                nested,
            )
        rebuilt: dict[str, Any] = {
            "type": "object",
            "properties": {**properties, step: narrowed_child},
            "required": sorted(_schema_required(current, producer_root)),
        }
        additional = _schema_additional_properties(current, producer_root)
        if additional is not True:
            rebuilt["additionalProperties"] = additional
        return rebuilt
    raise _SchemaPathError("admission_path_invalid", ".")


def _apply_artifact_source_admissions(
    producer_node: Any,
    producer_root: Mapping[str, Any],
    admission: Any,
) -> Any:
    predicates: list[Any]
    if isinstance(admission, list):
        predicates = list(admission)
    elif isinstance(admission, Mapping):
        predicates = [admission]
    else:
        return producer_node
    narrowed = producer_node
    for predicate in predicates:
        if not isinstance(predicate, Mapping):
            raise _SchemaPathError("admission_kind_unsupported", ".")
        narrowed = _narrow_producer_schema_by_admission(narrowed, producer_root, predicate)
    return narrowed


def _format_handoff_diagnostic(
    context: _HandoffContext,
    *,
    category: str,
    producer_allowed: str,
    consumer_accepted: str,
) -> str:
    return (
        "Artifact handoff admission: "
        f"producerSkill={context.producer_skill}; producerBinding={context.producer_binding}; "
        f"consumerSkill={context.consumer_skill}; consumerBinding={context.consumer_binding}; "
        f"sourcePath={context.source_path}; targetPath={context.target_path}; "
        f"conflict={category}; producerAllowed={producer_allowed}; "
        f"consumerAccepted={consumer_accepted}"
    )


def _warn_handoff_conflict(context: _HandoffContext, conflict: _HandoffConflict) -> None:
    warnings.warn(
        _format_handoff_diagnostic(
            context,
            category=conflict.category,
            producer_allowed=conflict.producer_allowed,
            consumer_accepted=conflict.consumer_accepted,
        ),
        RegistrySnapshotAdmissionWarning,
        stacklevel=3,
    )


def _validate_binding_default(
    *,
    consumer_binding: Mapping[str, Any],
    consumer_skill: Mapping[str, Any],
    entry: Mapping[str, Any],
) -> str:
    if "defaultValue" not in entry:
        return _DEFAULT_ABSENT

    source = entry["source"]
    source_kind = str(source.get("kind") or "unknown")
    context = _HandoffContext(
        producer_skill=source_kind,
        producer_binding=str(source.get("bindingId") or source_kind),
        consumer_skill=str(consumer_skill["skillId"]),
        consumer_binding=str(consumer_binding["bindingId"]),
        source_path=str(source.get("field") or "$"),
        target_path=str(entry["targetField"]),
    )
    try:
        consumer_root = _frozen_handoff_schema(consumer_skill, "input")
        consumer_node, _ = _resolve_direct_target_schema_path(
            consumer_root, context.target_path
        )
        conflict = _schema_accepts_value(
            entry["defaultValue"], consumer_node, consumer_root
        )
        if conflict is not None:
            default_json = json.dumps(
                entry["defaultValue"], ensure_ascii=False, separators=(",", ":")
            )
            _warn_handoff_conflict(
                context,
                _HandoffConflict(
                    f"default.{conflict}",
                    f"defaultValue={default_json[:160]}",
                    _constraint_range(_schema_constraint(consumer_node, consumer_root)),
                ),
            )
            return _DEFAULT_UNKNOWN
        consumer_root_node = _deref_local_schema(consumer_root, consumer_root)
        if (
            isinstance(consumer_root_node, Mapping)
            and any(
                keyword in consumer_root_node
                for keyword in _COMPLEX_HANDOFF_KEYWORDS
            )
        ):
            raise _UnsupportedHandoffSchema("complex_consumer_root")
        return _DEFAULT_ACCEPTED
    except _SchemaPathError as exc:
        _warn_handoff_conflict(
            context,
            _HandoffConflict(exc.code, exc.detail, "schema node required"),
        )
        return _DEFAULT_UNKNOWN
    except _UnsupportedHandoffSchema as exc:
        warnings.warn(
            _format_handoff_diagnostic(
                context,
                category="unsupported_schema",
                producer_allowed=f"defaultValue not evaluated: {exc}",
                consumer_accepted="runtime payload validation required",
            ),
            RegistrySnapshotAdmissionWarning,
            stacklevel=3,
        )
        return _DEFAULT_UNKNOWN
    raise AssertionError("unreachable binding default validation state")


def _validate_artifact_handoff(
    *,
    consumer_binding: Mapping[str, Any],
    consumer_skill: Mapping[str, Any],
    source_binding: Mapping[str, Any],
    producer_skill: Mapping[str, Any],
    entry: Mapping[str, Any],
    default_status: str | None = None,
) -> None:
    source = entry["source"]
    context = _HandoffContext(
        producer_skill=str(producer_skill["skillId"]),
        producer_binding=str(source_binding["bindingId"]),
        consumer_skill=str(consumer_skill["skillId"]),
        consumer_binding=str(consumer_binding["bindingId"]),
        source_path=str(source["field"]),
        target_path=str(entry["targetField"]),
    )
    try:
        if default_status is None:
            default_status = _validate_binding_default(
                consumer_binding=consumer_binding,
                consumer_skill=consumer_skill,
                entry=entry,
            )
        consumer_root = _frozen_handoff_schema(consumer_skill, "input")
        consumer_node, target_required = _resolve_target_schema_path(
            consumer_root, context.target_path
        )
        producer_root = _frozen_handoff_schema(producer_skill, "output")
        producer_node, source_guaranteed = _resolve_source_schema_path(
            producer_root, context.source_path
        )
        if source.get("cardinality", "one") == "many":
            # Runtime flattens selected array fields across all source artifacts.
            selected_node = _deref_local_schema(producer_node, producer_root)
            if context.source_path == "$" or selected_node.get("type") != "array":
                producer_node = {"type": "array", "items": producer_node}
        admission = source.get("admission")
        if admission is not None:
            producer_node = _apply_artifact_source_admissions(
                producer_node, producer_root, admission
            )
        conflict = _compare_handoff_schema(
            producer_node, producer_root, consumer_node, consumer_root
        )
        if (
            conflict is not None
            and entry["sourcePresence"] == "conditional"
            and conflict.category == "type"
            and _conditional_null_is_unavailable(
                producer_node, producer_root, consumer_node, consumer_root
            )
        ):
            conflict = None
        if conflict is not None:
            _warn_handoff_conflict(context, conflict)
        delivery_guaranteed = (
            entry["sourcePresence"] == "required" and source_guaranteed
        )
        if not delivery_guaranteed and default_status == _DEFAULT_ACCEPTED:
            delivery_guaranteed = True
        if (
            target_required
            and entry["sourcePresence"] != "conditional"
            and not delivery_guaranteed
            and default_status != _DEFAULT_UNKNOWN
        ):
            _warn_handoff_conflict(
                context,
                _HandoffConflict(
                    "required",
                    "source may be absent or omit the selected path",
                    "target is required",
                ),
            )
    except _SchemaPathError as exc:
        _warn_handoff_conflict(
            context,
            _HandoffConflict(exc.code, exc.detail, "schema node required"),
        )
    except _UnsupportedHandoffSchema as exc:
        warnings.warn(
            _format_handoff_diagnostic(
                context,
                category="unsupported_schema",
                producer_allowed=str(exc),
                consumer_accepted="not evaluated",
            ),
            RegistrySnapshotAdmissionWarning,
            stacklevel=3,
        )

def _validate_snapshot_scope(envelope: Mapping[str, Any]) -> None:
    """Version 1 remains byte-for-byte stable; version 2 separates freeze times."""
    if not isinstance(envelope, Mapping):
        raise RegistrySnapshotIntegrityError("snapshot envelope must be a mapping")
    version = envelope.get("schemaVersion")
    expected_keys = ENVELOPE_KEYS if version == 1 else ENVELOPE_KEYS | {"scope"}
    if not isinstance(version, int) or isinstance(version, bool) or version not in {1, 2} or set(envelope) != expected_keys:
        raise RegistrySnapshotIntegrityError("snapshot version or envelope keys are invalid")
    if version == 1:
        return
    scope = envelope.get("scope")
    if not isinstance(scope, Mapping):
        raise RegistrySnapshotIntegrityError("snapshot scope is invalid")
    kind = scope.get("kind")
    if kind == "research_planning" and set(scope) == {"kind"}:
        definitions = envelope.get("definitions") or {}
        bindings = envelope.get("bindings") or {}
        if not isinstance(definitions, Mapping) or not isinstance(bindings, Mapping):
            raise RegistrySnapshotIntegrityError("planning snapshot collections are invalid")
        if (
            definitions.get("skills") != [] or definitions.get("agents") != []
            or any(bindings.get(key) != [] for key in ("skills", "agents", "agentSkills"))
            or envelope.get("recipes") != []
            or any("executionComposition" in row for row in definitions.get("substages", ()))
        ):
            raise RegistrySnapshotIntegrityError("planning snapshot contains execution capabilities")
    elif kind == "execution" and set(scope) == {"kind", "planningSnapshotRef"}:
        ref = scope.get("planningSnapshotRef")
        if (
            not isinstance(ref, Mapping) or set(ref) != {"schemaVersion", "contentHash"}
            or not isinstance(ref.get("schemaVersion"), int)
            or isinstance(ref.get("schemaVersion"), bool) or ref.get("schemaVersion") != 2
            or not isinstance(ref.get("contentHash"), str)
            or not HASH_PATTERN.fullmatch(ref["contentHash"])
        ):
            raise RegistrySnapshotIntegrityError("execution planning snapshot ref is invalid")
    else:
        raise RegistrySnapshotIntegrityError("snapshot scope is unsupported")

def planning_envelope_from_execution(envelope: Mapping[str, Any]) -> dict[str, Any]:
    """Recover the exact frozen research contract, excluding execution-only data."""
    result = deepcopy(dict(envelope))
    result["schemaVersion"] = 2
    result["scope"] = {"kind": "research_planning"}
    result["definitions"]["skills"] = []
    result["definitions"]["agents"] = []
    for row in result["definitions"]["substages"]:
        row.pop("executionComposition", None)
    result["bindings"] = {"skills": [], "agents": [], "agentSkills": []}
    result["recipes"] = []
    return result

def registry_envelope_ref(envelope: Mapping[str, Any]) -> dict[str, Any]:
    snapshot = snapshot_from_envelope(envelope)
    return {"schemaVersion": snapshot.schema_version, "contentHash": snapshot.content_hash}


def _validate_binding_scientific_state_projection(
    projection: Any, skill: Mapping[str, Any]
) -> None:
    """Cross-entity check: a binding-level projection's source_fields must be
    required properties of the bound skill's frozen output schema.

    The loader already validated the v2 shape; here we re-validate against the
    skill-owned frozen output schema (both entities live in the same snapshot).
    """
    from app.modules.scientific_state_fact_projection import (
        validate_scientific_state_projection_declaration,
    )

    if not isinstance(projection, Mapping):
        raise RegistrySnapshotIntegrityError(
            "SkillBinding scientificStateProjection is invalid"
        )
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
        raise RegistrySnapshotIntegrityError(
            "SkillBinding scientificStateProjection requires a frozen skill output schema"
        )
    try:
        validate_scientific_state_projection_declaration(
            projection, output_schema=output_schema
        )
    except ValueError as exc:
        raise RegistrySnapshotIntegrityError(
            "SkillBinding scientificStateProjection is invalid"
        ) from exc

def execution_snapshot_matches_planning(
    planning_ref: Mapping[str, Any],
    execution_ref: Mapping[str, Any],
    envelope: Mapping[str, Any],
) -> bool:
    """Keep old frozen execution, or verify the complete v2 parent contract hash."""
    if dict(planning_ref) == dict(execution_ref):
        # Existing v1 snapshots already froze planning and execution together.
        return (execution_ref.get("schemaVersion") == 1
                and envelope.get("schemaVersion", 1) == 1 and "scope" not in envelope)
    try:
        return (
            envelope.get("scope", {}).get("kind") == "execution"
            and dict(execution_ref) == registry_envelope_ref(envelope)
            and dict(planning_ref) == envelope["scope"]["planningSnapshotRef"]
        )
    except (RegistrySnapshotIntegrityError, KeyError, TypeError, ValueError):
        return False

def validate_envelope(
    envelope: Mapping[str, Any],
    *,
    allow_legacy_static_policies: bool = False,
) -> None:
    _validate_snapshot_scope(envelope)
    definitions = envelope.get("definitions")
    bindings = envelope.get("bindings")
    if not isinstance(definitions, Mapping) or set(definitions) != {"packs", "substages", "skills", "agents"}:
        raise RegistrySnapshotIntegrityError("snapshot definitions shape is invalid")
    if not isinstance(bindings, Mapping) or set(bindings) != {"skills", "agents", "agentSkills"}:
        raise RegistrySnapshotIntegrityError("snapshot bindings shape is invalid")
    policies = envelope.get("staticPolicies")
    policy_keys = set(policies) if isinstance(policies, Mapping) else set()
    is_legacy_policy_shape = allow_legacy_static_policies and policy_keys == LEGACY_POLICY_KEYS
    if not isinstance(policies, Mapping) or (
        policy_keys != POLICY_KEYS and not is_legacy_policy_shape
    ):
        raise RegistrySnapshotIntegrityError("snapshot staticPolicies shape is invalid")
    if policies.get("policyVersion") != "registry-policy.v1":
        raise RegistrySnapshotIntegrityError("snapshot policyVersion is unknown")
    if policies.get("unknownCanonicalRiskClass") != "high" or policies.get("unboundSkillPolicy") != "not_applicable":
        raise RegistrySnapshotIntegrityError("snapshot static policy values are invalid")
    if policies.get("providerReadinessSource") != "resolution_facts_only":
        raise RegistrySnapshotIntegrityError("snapshot provider readiness policy is invalid")
    if not is_legacy_policy_shape:
        authorities = policies.get("systemRecoveryAuthorities")
        if not isinstance(authorities, list) or any(
            not isinstance(authority, Mapping) for authority in authorities
        ):
            raise RegistrySnapshotIntegrityError(
                "snapshot systemRecoveryAuthorities shape is invalid"
            )

    pack_ids: set[str] = set()
    for row in definitions["packs"]:
        row = _require_fields(row, {"pack", "definitionVersion", "source"}, "pack")
        _positive_int(row, "definitionVersion", "pack")
        if not isinstance(row["pack"], str) or not row["pack"]:
            raise RegistrySnapshotIntegrityError("pack identity is invalid")
        pack_ids.add(row["pack"])

    substage_ids: set[tuple[str, int]] = set()
    substage_rows: dict[tuple[str, int], dict[str, Any]] = {}
    spec_repo_paths: dict[str, str] = {}
    for row in definitions["substages"]:
        row = _require_fields(
            row,
            {
                "definitionId", "definitionVersion", "pack", "stage", "substageKey", "enabled",
                "label", "description", "specRef", "specSource", "inputContract", "outputContract",
                "acceptanceContract",
            },
            "SubstageDefinition",
            optional={
                "riskPolicy",
                "instancePolicy",
                "executionComposition",
                "executionMethod",
                "externalActionContract",
            },
        )
        _positive_int(row, "definitionVersion", "SubstageDefinition")
        _strict_bool(row, "enabled", "SubstageDefinition")
        if row["pack"] not in pack_ids or row["definitionId"] != f"{row['pack']}:{row['substageKey']}":
            raise RegistrySnapshotIntegrityError("SubstageDefinition identity is invalid")
        if row["stage"] not in {"problem_modeling", "solution_design", "execution_validation", "insight_generation"}:
            raise RegistrySnapshotIntegrityError("SubstageDefinition stage is invalid")
        instance_policy = _require_fields(
            row.get("instancePolicy") or {"mode": "repeatable"},
            {"mode"},
            "SubstageDefinition.instancePolicy",
            optional={"executionReentry"},
        )
        if instance_policy["mode"] not in {"repeatable", "singleton_collection"}:
            raise RegistrySnapshotIntegrityError("SubstageDefinition instancePolicy is invalid")
        execution_reentry = instance_policy.get("executionReentry")
        if execution_reentry is not None:
            execution_reentry = _require_fields(
                execution_reentry,
                {"policyVersion", "triggerDefinitionIds"},
                "SubstageDefinition.instancePolicy.executionReentry",
            )
            trigger_ids = execution_reentry["triggerDefinitionIds"]
            if (
                instance_policy["mode"] != "repeatable"
                or execution_reentry["policyVersion"]
                != "repeatable_definition_reentry_v1"
                or not isinstance(trigger_ids, list)
                or not trigger_ids
                or any(not isinstance(value, str) or not value for value in trigger_ids)
                or len(set(trigger_ids)) != len(trigger_ids)
            ):
                raise RegistrySnapshotIntegrityError(
                    "SubstageDefinition execution re-entry policy is invalid"
                )
        external_actions = row.get("externalActionContract")
        if external_actions is not None:
            external_actions = _require_fields(
                external_actions,
                {"status", "requiredActionKinds"},
                "SubstageDefinition.externalActionContract",
            )
            if external_actions["status"] not in {"known", "unknown"}:
                raise RegistrySnapshotIntegrityError(
                    "SubstageDefinition externalActionContract status is invalid"
                )
            required_actions = external_actions["requiredActionKinds"]
            if (
                not isinstance(required_actions, list)
                or any(
                    action not in EXTERNAL_ACTION_KIND_VALUES
                    for action in required_actions
                )
                or len(set(required_actions)) != len(required_actions)
                or (external_actions["status"] == "unknown" and required_actions)
            ):
                raise RegistrySnapshotIntegrityError(
                    "SubstageDefinition externalActionContract is invalid"
                )
        spec_source = _require_fields(
            row["specSource"], {"ref", "repoPath", "content", "tracked", "parsed"}, "specSource"
        )
        if spec_source["tracked"] is not True:
            raise RegistrySnapshotIntegrityError("specSource must be tracked canonical content")
        existing_spec_owner = spec_repo_paths.get(spec_source["repoPath"])
        if existing_spec_owner is not None and existing_spec_owner != row["definitionId"]:
            raise RegistrySnapshotIntegrityError("canonical spec repoPath is ambiguous")
        spec_repo_paths[spec_source["repoPath"]] = row["definitionId"]
        archived_parser = spec_source["parsed"]
        if (
            not isinstance(archived_parser, Mapping)
            or archived_parser.get("parserVersion") not in _SPEC_PARSERS
        ):
            raise RegistrySnapshotIntegrityError("specSource parserVersion is unsupported")
        parsed = parse_spec_document(
            row["pack"],
            row["specRef"],
            spec_source["content"],
            repo_path=spec_source["repoPath"],
            parser_version=archived_parser["parserVersion"],
        )
        for field in ("specRef", "specSource", "inputContract", "outputContract", "acceptanceContract"):
            if row[field] != parsed[field]:
                raise RegistrySnapshotIntegrityError(f"SubstageDefinition {field} does not match archived spec")
        parsed_method = parsed.get("executionMethod")
        if parsed_method is not None:
            parsed_method = canonicalize_registry_value(parsed_method)
        declared_method = row.get("executionMethod")
        if declared_method is not None:
            declared_method = canonicalize_registry_value(declared_method)
        if declared_method != parsed_method:
            raise RegistrySnapshotIntegrityError("SubstageDefinition executionMethod does not match archived spec")
        if row.get("executionMethod") is not None:
            _validate_execution_method(row["executionMethod"])
        composition = row.get("executionComposition")
        if composition is not None and row.get("executionMethod") is not None:
            raise RegistrySnapshotIntegrityError("SubstageDefinition cannot declare both executionMethod and executionComposition")
        if composition is not None:
            composition = _require_fields(
                composition,
                {
                    "workflowId", "skillPack", "compositionVersion", "semantics",
                    "sourceAuthority", "requiredSkillRefs",
                },
                "SubstageDefinition.executionComposition",
            )
            _positive_int(
                composition, "compositionVersion", "SubstageDefinition.executionComposition"
            )
            allowed_workflows = {
                ("opentrajectory_research_workflow", "opentrajectory"),
                ("materials_candidate_discovery_workflow", "materials"),
                ("materials_full_discovery_workflow", "materials"),
                ("pvd_process_optimization_workflow", "pvd"),
                ("cs_paper_reproduction_workflow", "cs_reproduction"),
            }
            if (
                (composition["workflowId"], composition["skillPack"]) not in allowed_workflows
                or composition["semantics"] != "ordered_required"
                or row["pack"] != composition["skillPack"]
            ):
                raise RegistrySnapshotIntegrityError("executionComposition authority is invalid")
            refs = composition["requiredSkillRefs"]
            if not isinstance(refs, list) or not refs:
                raise RegistrySnapshotIntegrityError("executionComposition requiredSkillRefs is invalid")
            for ref in refs:
                ref = _require_fields(
                    ref, {"skillId", "definitionVersion"}, "executionComposition SkillRef"
                )
                _positive_int(ref, "definitionVersion", "executionComposition SkillRef")
                if not isinstance(ref["skillId"], str) or not ref["skillId"]:
                    raise RegistrySnapshotIntegrityError("executionComposition SkillRef identity is invalid")
            if len({(ref["skillId"], ref["definitionVersion"]) for ref in refs}) != len(refs):
                raise RegistrySnapshotIntegrityError("executionComposition SkillRefs must be unique")
        substage_ids.add((row["definitionId"], row["definitionVersion"]))
        substage_rows[(row["definitionId"], row["definitionVersion"])] = row

    skill_ids: set[tuple[str, int]] = set()
    skill_rows: dict[tuple[str, int], dict[str, Any]] = {}
    for row in definitions["skills"]:
        row = _require_fields(
            row,
            {
                "skillId", "definitionVersion", "status", "enabled", "runtime", "skillPath", "skillType",
                "riskPolicy", "sourceContract", "inputContract", "outputContract", "judgmentSupport",
            },
            "SkillDefinition",
            optional={"executionMethodRoles"},
        )
        _positive_int(row, "definitionVersion", "SkillDefinition")
        _strict_bool(row, "enabled", "SkillDefinition")
        method_roles = row.get("executionMethodRoles") or []
        if (
            not isinstance(method_roles, list)
            or any(not isinstance(value, str) or not value for value in method_roles)
            or len(set(method_roles)) != len(method_roles)
        ):
            raise RegistrySnapshotIntegrityError("SkillDefinition executionMethodRoles are invalid")
        source_contract = row["sourceContract"]
        validate_skill_finalization_source_contract(source_contract)
        if isinstance(source_contract, dict) and source_contract.get("finalization") is not None:
            runtime_contract = source_contract.get("runtime_contract")
            if row.get("runtime") != "llm_structured" or not isinstance(runtime_contract, dict):
                raise RegistrySnapshotIntegrityError(
                    "Skill finalization is only valid for registered llm_structured runtime"
                )
            if (
                runtime_contract.get("kind") != "llm_reasoning"
                or runtime_contract.get("requires_external_action") is not False
            ):
                raise RegistrySnapshotIntegrityError(
                    "Skill finalization runtime contract is invalid"
                )
        support = _require_fields(
            row["judgmentSupport"],
            {"deterministic", "source"},
            "Skill judgmentSupport",
            optional={"domainProfessional", "humanAuthority"},
        )
        _strict_bool(support, "deterministic", "Skill judgmentSupport")
        if "domainProfessional" in support:
            _strict_bool(support, "domainProfessional", "Skill judgmentSupport")
        if "humanAuthority" in support:
            _strict_bool(support, "humanAuthority", "Skill judgmentSupport")
            if support["humanAuthority"]:
                raise RegistrySnapshotIntegrityError(
                    "SkillDefinition cannot satisfy human authority"
                )
        skill_ids.add((row["skillId"], row["definitionVersion"]))
        skill_rows[(row["skillId"], row["definitionVersion"])] = row

    agent_ids: set[tuple[str, int]] = set()
    agent_rows: dict[tuple[str, int], dict[str, Any]] = {}
    for row in definitions["agents"]:
        row = _require_fields(
            row,
            {
                "agentId", "definitionVersion", "agentKind", "candidateScope", "enabled", "visibility",
                "aliases", "capability", "notFor", "inputContract", "outputContract", "failClosedRules",
                "budgetPolicy", "evaluator", "allowedProviders", "defaultProvider", "toolRequirements",
                "judgmentSupport",
            },
            "AgentDefinition",
            optional={"reviewContract", "executionMethodRoles"},
        )
        _positive_int(row, "definitionVersion", "AgentDefinition")
        _strict_bool(row, "enabled", "AgentDefinition")
        method_roles = row.get("executionMethodRoles") or []
        if (
            not isinstance(method_roles, list)
            or any(not isinstance(value, str) or not value for value in method_roles)
            or len(set(method_roles)) != len(method_roles)
        ):
            raise RegistrySnapshotIntegrityError("AgentDefinition executionMethodRoles are invalid")
        support = _require_fields(row["judgmentSupport"], {"domainProfessional", "humanAuthority"}, "judgmentSupport")
        _strict_bool(support, "domainProfessional", "judgmentSupport")
        _strict_bool(support, "humanAuthority", "judgmentSupport")
        if support["humanAuthority"]:
            raise RegistrySnapshotIntegrityError("AgentDefinition cannot satisfy human authority")
        if not isinstance(row["allowedProviders"], list) or row["defaultProvider"] not in row["allowedProviders"]:
            raise RegistrySnapshotIntegrityError("AgentDefinition provider policy is invalid")
        agent_ids.add((row["agentId"], row["definitionVersion"]))
        agent_rows[(row["agentId"], row["definitionVersion"])] = row

    for row in bindings["skills"]:
        row = _require_fields(
            row,
            {"bindingId", "bindingVersion", "definitionId", "definitionVersion", "skillId", "skillDefinitionVersion", "supportedRole", "enabled", "sourceAuthority"},
            "SkillBinding",
            optional={
                "conditionalPolicy",
                "inputSources",
                "runtimeInputs",
                "scientificStateProjection",
            },
        )
        _positive_int(row, "bindingVersion", "SkillBinding")
        _strict_bool(row, "enabled", "SkillBinding")
        if row["supportedRole"] not in SKILL_ROLES or row["sourceAuthority"] not in SKILL_BINDING_SOURCES:
            raise RegistrySnapshotIntegrityError("SkillBinding role/source authority is invalid")
        if (row["definitionId"], row["definitionVersion"]) not in substage_ids or (row["skillId"], row["skillDefinitionVersion"]) not in skill_ids:
            raise RegistrySnapshotIntegrityError("SkillBinding contains an unknown reference")
        if row["bindingId"] != f"bind:{row['definitionId']}:skill:{row['skillId']}":
            raise RegistrySnapshotIntegrityError("SkillBinding identity is invalid")
        if row.get("conditionalPolicy") is not None:
            try:
                validate_conditional_policy(row["conditionalPolicy"])
            except ValueError as exc:
                raise RegistrySnapshotIntegrityError(
                    "SkillBinding conditionalPolicy is invalid"
                ) from exc
        scientific_targets: set[str] = set()
        if "inputSources" in row:
            _validate_skill_binding_input_sources(row["inputSources"])
            scientific_targets = {entry["targetField"] for entry in row["inputSources"]["fields"]}
        if "runtimeInputs" in row:
            _validate_skill_binding_runtime_inputs(row["runtimeInputs"], scientific_targets=scientific_targets)
        substage = substage_rows[
            (row["definitionId"], row["definitionVersion"])
        ]
        skill = skill_rows[(row["skillId"], row["skillDefinitionVersion"])]
        if "scientificStateProjection" in row:
            _validate_binding_scientific_state_projection(row["scientificStateProjection"], skill)
        if row["enabled"] and not substage["enabled"]:
            raise RegistrySnapshotIntegrityError(
                "enabled SkillBinding requires an enabled SubstageDefinition"
            )
        if row["enabled"] and (not skill["enabled"] or skill["status"] != "ready"):
            raise RegistrySnapshotIntegrityError("enabled SkillBinding requires a ready enabled Skill")

    skill_binding_refs = {
        (row["definitionId"], row["definitionVersion"], row["skillId"], row["skillDefinitionVersion"])
        for row in bindings["skills"]
    }
    binding_refs_by_id = {(row["bindingId"], row["bindingVersion"]): row for row in bindings["skills"]}
    for row in bindings["skills"]:
        input_sources = row.get("inputSources")
        if not isinstance(input_sources, Mapping):
            continue
        for entry in input_sources["fields"]:
            source = entry["source"]
            default_status = _DEFAULT_ABSENT
            if row["enabled"] and "defaultValue" in entry:
                default_status = _validate_binding_default(
                    consumer_binding=row,
                    consumer_skill=skill_rows[
                        (row["skillId"], row["skillDefinitionVersion"])
                    ],
                    entry=entry,
                )
            if source["kind"] != "artifact":
                continue
            source_ref = (source["bindingId"], source["bindingVersion"])
            source_binding = binding_refs_by_id.get(source_ref)
            if source_binding is None:
                raise RegistrySnapshotIntegrityError("SkillBinding artifact source binding is unknown")
            if source_ref == (row["bindingId"], row["bindingVersion"]):
                raise RegistrySnapshotIntegrityError("SkillBinding cannot source its own Artifact")
            if (
                row["enabled"]
                and entry["sourcePresence"] in {"required", "conditional"}
                and not source_binding.get("enabled")
            ):
                raise RegistrySnapshotIntegrityError("SkillBinding artifact source binding is unavailable")
            if row["enabled"] and source_binding.get("enabled"):
                _validate_artifact_handoff(
                    consumer_binding=row,
                    consumer_skill=skill_rows[
                        (row["skillId"], row["skillDefinitionVersion"])
                    ],
                    source_binding=source_binding,
                    producer_skill=skill_rows[
                        (
                            source_binding["skillId"],
                            source_binding["skillDefinitionVersion"],
                        )
                    ],
                    entry=entry,
                    default_status=default_status,
                )
    for identity, substage in substage_rows.items():
        composition = substage.get("executionComposition")
        if composition is None:
            continue
        for ref in composition["requiredSkillRefs"]:
            skill_identity = (ref["skillId"], ref["definitionVersion"])
            if skill_identity not in skill_ids:
                raise RegistrySnapshotIntegrityError("executionComposition contains an unknown Skill")
            if (*identity, *skill_identity) not in skill_binding_refs:
                raise RegistrySnapshotIntegrityError(
                    "executionComposition Skill is not bound to its SubstageDefinition"
                )

    for row in bindings["agents"]:
        row = _require_fields(
            row,
            {"bindingId", "bindingVersion", "definitionId", "definitionVersion", "agentId", "agentDefinitionVersion", "supportedRole", "enabled", "sourceAuthority"},
            "AgentBinding",
            optional={"artifactContract"},
        )
        _positive_int(row, "bindingVersion", "AgentBinding")
        _strict_bool(row, "enabled", "AgentBinding")
        if row["supportedRole"] not in AGENT_ROLES or row["sourceAuthority"] not in AGENT_BINDING_SOURCES:
            raise RegistrySnapshotIntegrityError("AgentBinding role/source authority is invalid")
        if (row["definitionId"], row["definitionVersion"]) not in substage_ids or (row["agentId"], row["agentDefinitionVersion"]) not in agent_ids:
            raise RegistrySnapshotIntegrityError("AgentBinding contains an unknown reference")
        if row["bindingId"] != f"bind:{row['definitionId']}:agent:{row['agentId']}:{row['supportedRole']}":
            raise RegistrySnapshotIntegrityError("AgentBinding identity is invalid")
        agent = agent_rows[(row["agentId"], row["agentDefinitionVersion"])]
        if row["sourceAuthority"] == "general_fallback_policy":
            if "artifactContract" in row:
                raise RegistrySnapshotIntegrityError("fallback AgentBinding cannot carry mapping authority")
            if not (
                row["supportedRole"] == "primary"
                and agent["agentKind"] == "general_research"
                and agent["candidateScope"] == "all_enabled_substages"
                and agent["enabled"]
            ):
                raise RegistrySnapshotIntegrityError("fallback AgentBinding policy is invalid")
        elif "artifactContract" not in row:
            raise RegistrySnapshotIntegrityError("Agent Registry mapping requires artifactContract")

    for row in bindings["agentSkills"]:
        row = _require_fields(
            row,
            {"bindingId", "bindingVersion", "agentId", "agentDefinitionVersion", "skillId", "skillDefinitionVersion", "enabled", "sourceAuthority"},
            "AgentSkillBinding",
        )
        _positive_int(row, "bindingVersion", "AgentSkillBinding")
        _strict_bool(row, "enabled", "AgentSkillBinding")
        if row["sourceAuthority"] not in GRANT_SOURCES:
            raise RegistrySnapshotIntegrityError("AgentSkillBinding source authority is invalid")
        if (row["agentId"], row["agentDefinitionVersion"]) not in agent_ids or (row["skillId"], row["skillDefinitionVersion"]) not in skill_ids:
            raise RegistrySnapshotIntegrityError("AgentSkillBinding contains an unknown reference")
        if row["bindingId"] != f"grant:{row['agentId']}:{row['skillId']}":
            raise RegistrySnapshotIntegrityError("AgentSkillBinding identity is invalid")

    fallback_definitions = {
        (row["definitionId"], row["definitionVersion"])
        for row in bindings["agents"]
        if row["enabled"] and row["sourceAuthority"] == "general_fallback_policy"
    }
    enabled_definitions = {identity for identity, row in substage_rows.items() if row["enabled"]}
    if envelope.get("scope") != {"kind": "research_planning"} and enabled_definitions != fallback_definitions:
        raise RegistrySnapshotIntegrityError("general fallback Bindings must exactly cover enabled Definitions")

    try:
        from app.models.research_orchestration import MultiAgentRecipeDefinition
        recipes = [MultiAgentRecipeDefinition.model_validate(row) for row in envelope.get("recipes")]
    except Exception as exc:
        raise RegistrySnapshotIntegrityError(f"Recipe authority is invalid: {exc}") from exc
    if any((row.substage_definition_id, row.substage_definition_version) not in substage_ids for row in recipes):
        raise RegistrySnapshotIntegrityError("Recipe contains an unknown Definition reference")


def canonicalize_envelope(
    envelope: Mapping[str, Any],
    *,
    allow_legacy_static_policies: bool = False,
) -> bytes:
    validate_envelope(
        envelope,
        allow_legacy_static_policies=allow_legacy_static_policies,
    )
    schema_version = envelope.get("schemaVersion")
    if schema_version not in {1, 2} or isinstance(schema_version, bool):
        raise RegistrySnapshotIntegrityError("snapshot schemaVersion is unsupported")
    definitions = envelope.get("definitions")
    bindings = envelope.get("bindings")
    if not isinstance(definitions, Mapping) or set(definitions) != {"packs", "substages", "skills", "agents"}:
        raise RegistrySnapshotIntegrityError("snapshot definitions shape is invalid")
    if not isinstance(bindings, Mapping) or set(bindings) != {"skills", "agents", "agentSkills"}:
        raise RegistrySnapshotIntegrityError("snapshot bindings shape is invalid")
    canonical = {
        "schemaVersion": schema_version,
        "definitions": {
            "packs": _sort_collection(definitions["packs"], "pack"),
            "substages": _sort_collection(definitions["substages"], "definitionId", "definitionVersion"),
            "skills": _sort_collection(definitions["skills"], "skillId", "definitionVersion"),
            "agents": _sort_collection(definitions["agents"], "agentId", "definitionVersion"),
        },
        "bindings": {
            "skills": _sort_collection(bindings["skills"], "bindingId", "bindingVersion"),
            "agents": _sort_collection(bindings["agents"], "bindingId", "bindingVersion"),
            "agentSkills": _sort_collection(bindings["agentSkills"], "bindingId", "bindingVersion"),
        },
        "recipes": _sort_collection(envelope.get("recipes"), "recipeId", "recipeVersion"),
        "staticPolicies": _normalize(envelope.get("staticPolicies")),
    }
    if schema_version == 2:
        canonical["scope"] = _normalize(envelope["scope"])
        if envelope["scope"]["kind"] == "execution":
            frozen_planning_ref = registry_envelope_ref(planning_envelope_from_execution(canonical))
            if frozen_planning_ref != envelope["scope"]["planningSnapshotRef"]:
                raise RegistrySnapshotIntegrityError("execution snapshot changed its frozen research contract")
    return _canonical_json(canonical)


def snapshot_from_envelope(envelope: Mapping[str, Any]) -> RegistrySnapshot:
    from app.modules.registry_content_cache import snapshot_for_content

    return snapshot_for_content(_canonical_json(envelope).decode("utf-8"))


def _legacy_runtime_snapshot(snapshot: RegistrySnapshot) -> RegistrySnapshot:
    """Adapt only the known pre-9484a55 v1 policy shape after hash validation."""
    raw_envelope = json.loads(snapshot.canonical_payload)
    policies = raw_envelope["staticPolicies"]
    if set(policies) != LEGACY_POLICY_KEYS:
        raise RegistrySnapshotIntegrityError("snapshot legacy staticPolicies shape is invalid")
    runtime_envelope = dict(raw_envelope)
    runtime_policies = dict(policies)
    runtime_policies["systemRecoveryAuthorities"] = [
        dict(authority) for authority in LEGACY_SYSTEM_RECOVERY_AUTHORITIES
    ]
    runtime_envelope["staticPolicies"] = runtime_policies
    return RegistrySnapshot(
        schema_version=snapshot.schema_version,
        content_hash=snapshot.content_hash,
        canonical_payload=snapshot.canonical_payload,
        runtime_payload=_canonical_json(runtime_envelope),
    )


def _verify_snapshot(snapshot: RegistrySnapshot) -> RegistrySnapshot:
    try:
        envelope = json.loads(snapshot.canonical_payload)
    except (TypeError, json.JSONDecodeError) as exc:
        raise RegistrySnapshotIntegrityError("snapshot payload is not valid UTF-8 JSON") from exc
    try:
        computed = snapshot_from_envelope(envelope)
    except RegistrySnapshotIntegrityError as current_error:
        try:
            legacy_payload = canonicalize_envelope(
                envelope,
                allow_legacy_static_policies=True,
            )
        except RegistrySnapshotIntegrityError:
            raise current_error
        computed = RegistrySnapshot(
            schema_version=json.loads(legacy_payload)["schemaVersion"],
            content_hash=f"sha256:{sha256(legacy_payload).hexdigest()}",
            canonical_payload=legacy_payload,
        )
        if (
            snapshot.schema_version != computed.schema_version
            or snapshot.content_hash != computed.content_hash
            or snapshot.canonical_payload != computed.canonical_payload
        ):
            raise RegistrySnapshotIntegrityError("caller snapshot hash/schema/payload mismatch")
        return _legacy_runtime_snapshot(computed)
    if (
        snapshot.schema_version != computed.schema_version
        or snapshot.content_hash != computed.content_hash
        or snapshot.canonical_payload != computed.canonical_payload
    ):
        raise RegistrySnapshotIntegrityError("caller snapshot hash/schema/payload mismatch")
    return computed


class InMemoryRegistrySnapshotRepository:
    def __init__(self) -> None:
        self._rows: dict[str, tuple[int, bytes]] = {}

    def put(self, snapshot: RegistrySnapshot) -> RegistrySnapshot:
        computed = _verify_snapshot(snapshot)
        if computed.runtime_payload is not None:
            raise RegistrySnapshotIntegrityError("legacy snapshots are read-only")
        existing = self._rows.get(computed.content_hash)
        candidate = (computed.schema_version, computed.canonical_payload)
        if existing is not None and existing != candidate:
            raise RegistrySnapshotIntegrityError("content hash collision with different payload")
        self._rows[computed.content_hash] = candidate
        return computed

    def get(self, content_hash: str) -> RegistrySnapshot:
        if not HASH_PATTERN.fullmatch(content_hash):
            raise RegistrySnapshotIntegrityError("invalid Registry snapshot hash")
        row = self._rows.get(content_hash)
        if row is None:
            raise RegistrySnapshotNotFoundError(f"snapshot_not_archived:{content_hash}")
        schema_version, payload = row
        snapshot = RegistrySnapshot(schema_version, content_hash, payload)
        return _verify_snapshot(snapshot)

    def corrupt_for_test(self, content_hash: str, envelope: Mapping[str, Any]) -> None:
        canonical = canonicalize_envelope(envelope)
        self._rows[content_hash] = (json.loads(canonical)["schemaVersion"], canonical)


class RegistrySnapshotRepository:
    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self._connection_factory = connection_factory

    def put(self, snapshot: RegistrySnapshot) -> RegistrySnapshot:
        computed = _verify_snapshot(snapshot)
        if computed.runtime_payload is not None:
            raise RegistrySnapshotIntegrityError("legacy snapshots are read-only")
        payload_text = computed.canonical_payload.decode("utf-8")
        with self._connection_factory() as connection:
            connection.execute(
                """INSERT INTO openair_registry_snapshots (schema_version, content_hash, payload)
                   VALUES (%s, %s, %s::jsonb)
                   ON CONFLICT (content_hash) DO NOTHING""",
                (computed.schema_version, computed.content_hash, payload_text),
            )
            row = connection.execute(
                """SELECT schema_version, content_hash, payload
                   FROM openair_registry_snapshots WHERE content_hash = %s""",
                (computed.content_hash,),
            ).fetchone()
            if row is None:
                raise RegistrySnapshotIntegrityError("snapshot archive insert was not observable")
            stored_payload = row["payload"]
            if isinstance(stored_payload, str):
                stored_payload = json.loads(stored_payload)
            stored = snapshot_from_envelope(stored_payload)
            if (
                row["schema_version"] != stored.schema_version
                or row["content_hash"] != stored.content_hash
                or stored.canonical_payload != computed.canonical_payload
            ):
                raise RegistrySnapshotIntegrityError("content hash collision with different payload")
        return computed

    def get(self, content_hash: str) -> RegistrySnapshot:
        if not HASH_PATTERN.fullmatch(content_hash):
            raise RegistrySnapshotIntegrityError("invalid Registry snapshot hash")
        with self._connection_factory() as connection:
            row = connection.execute(
                """SELECT schema_version, content_hash, payload
                   FROM openair_registry_snapshots WHERE content_hash = %s""",
                (content_hash,),
            ).fetchone()
        if row is None:
            raise RegistrySnapshotNotFoundError(f"snapshot_not_archived:{content_hash}")
        payload = row["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        try:
            canonical_payload = canonicalize_envelope(payload)
        except RegistrySnapshotIntegrityError as current_error:
            try:
                canonical_payload = canonicalize_envelope(
                    payload,
                    allow_legacy_static_policies=True,
                )
            except RegistrySnapshotIntegrityError:
                raise current_error
        return _verify_snapshot(
            RegistrySnapshot(
                schema_version=row["schema_version"],
                content_hash=row["content_hash"],
                canonical_payload=canonical_payload,
            )
        )
