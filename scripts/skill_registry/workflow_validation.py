"""Read-only checks for a workflow pack before it is connected to OpenAIR."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from ._yaml import yaml_safe_load
from .registry import REGISTRY_REL_PATH, load_registry
from .stage_model import RESEARCH_STAGE_META


PACK_CONFIG_FILES = (
    "stage-substage-map.yaml",
    "substage-spec-map.yaml",
    "substage-skill-map.yaml",
    "skill-id-to-substage.yaml",
    "research_type_overrides.yaml",
)
RUNTIME_CONTRACT_KEYS = {
    "kind",
    "requires_external_action",
    "adapter_id",
    "evidence_contract_version",
}


@dataclass(frozen=True)
class WorkflowValidationIssue:
    severity: str
    code: str
    message: str


def _load_yaml(path: Path, issues: list[WorkflowValidationIssue]) -> Any:
    try:
        return yaml_safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, ValueError) as exc:
        issues.append(WorkflowValidationIssue("error", "YAML_INVALID", f"{path}: {exc}"))
        return {}


def _skill_ids(entries: Any) -> list[str]:
    if not isinstance(entries, list):
        return []
    ids: list[str] = []
    for entry in entries:
        if isinstance(entry, str) and entry.strip():
            ids.append(entry.strip())
        elif isinstance(entry, Mapping) and isinstance(entry.get("skill_id"), str):
            ids.append(entry["skill_id"].strip())
    return ids


def _registry_packs(row: Mapping[str, Any]) -> set[str]:
    values = row.get("packs")
    if isinstance(values, list):
        return {str(value) for value in values if isinstance(value, str) and value}
    value = row.get("pack")
    return {value} if isinstance(value, str) and value else set()


def validate_workflow_pack(
    repo_root: Path,
    *,
    pack: str,
    composition_map: Path | None = None,
) -> list[WorkflowValidationIssue]:
    """Validate pack data and the Skills selected by its actual composition map.

    This intentionally does not import the API runtime. It proves static
    registry/config consistency; an end-to-end test still proves live runtime
    support and external-service availability.
    """

    issues: list[WorkflowValidationIssue] = []
    packs_payload = _load_yaml(repo_root / "schemas" / "packs.yaml", issues)
    packs = packs_payload.get("packs") if isinstance(packs_payload, Mapping) else None
    matching_packs = [row for row in packs or [] if isinstance(row, Mapping) and row.get("key") == pack]
    if len(matching_packs) != 1:
        issues.append(WorkflowValidationIssue("error", "PACK_UNDECLARED", f"schemas/packs.yaml must declare exactly one pack '{pack}'"))

    pack_dir = repo_root / "schemas" / pack
    payloads: dict[str, Any] = {}
    for name in PACK_CONFIG_FILES:
        path = pack_dir / name
        if not path.is_file():
            issues.append(WorkflowValidationIssue("error", "PACK_FILE_MISSING", f"missing schemas/{pack}/{name}"))
            continue
        payloads[name] = _load_yaml(path, issues)

    stage_map = payloads.get("stage-substage-map.yaml", {})
    known_substages: set[str] = set()
    if not isinstance(stage_map, Mapping):
        issues.append(WorkflowValidationIssue("error", "STAGE_MAP_INVALID", "stage-substage-map.yaml must be a mapping"))
    else:
        for stage, stage_row in stage_map.items():
            if stage not in RESEARCH_STAGE_META:
                issues.append(WorkflowValidationIssue("error", "STAGE_UNKNOWN", f"unknown stage '{stage}'"))
                continue
            entries = stage_row.get("substages") if isinstance(stage_row, Mapping) else stage_row
            if not isinstance(entries, list):
                issues.append(WorkflowValidationIssue("error", "SUBSTAGE_LIST_INVALID", f"stage '{stage}' has no substages list"))
                continue
            for entry in entries:
                key = entry.get("key") if isinstance(entry, Mapping) else None
                if not isinstance(key, str) or not key:
                    issues.append(WorkflowValidationIssue("error", "SUBSTAGE_KEY_INVALID", f"stage '{stage}' has a substage without a key"))
                elif key in known_substages:
                    issues.append(WorkflowValidationIssue("error", "SUBSTAGE_DUPLICATE", f"substage '{key}' appears more than once"))
                else:
                    known_substages.add(key)

    spec_map = payloads.get("substage-spec-map.yaml", {})
    if not isinstance(spec_map, Mapping):
        issues.append(WorkflowValidationIssue("error", "SPEC_MAP_INVALID", "substage-spec-map.yaml must be a mapping"))
    else:
        for substage, spec_ref in spec_map.items():
            if substage not in known_substages:
                issues.append(WorkflowValidationIssue("error", "SPEC_SUBSTAGE_UNKNOWN", f"spec map references unknown substage '{substage}'"))
            spec_path = pack_dir / spec_ref if isinstance(spec_ref, str) else pack_dir
            if isinstance(spec_ref, str) and not (spec_path).is_file():
                spec_path = pack_dir / "specs" / spec_ref
            if not isinstance(spec_ref, str) or not spec_ref or not spec_path.is_file():
                issues.append(WorkflowValidationIssue("error", "SPEC_FILE_MISSING", f"substage '{substage}' has no readable spec file '{spec_ref}'"))

    overrides = payloads.get("research_type_overrides.yaml", {})
    if not isinstance(overrides, Mapping):
        issues.append(WorkflowValidationIssue("error", "RESEARCH_TYPE_MAP_INVALID", "research_type_overrides.yaml must be a mapping"))
    else:
        for research_type in ("M", "D", "C", "H"):
            row = overrides.get(research_type)
            if not isinstance(row, Mapping):
                issues.append(WorkflowValidationIssue("error", "RESEARCH_TYPE_MISSING", f"research type '{research_type}' is missing"))
                continue
            required = row.get("required_substages", [])
            optional = row.get("optional_substages", [])
            if not isinstance(required, list) or not isinstance(optional, list):
                issues.append(WorkflowValidationIssue("error", "RESEARCH_TYPE_LIST_INVALID", f"research type '{research_type}' must use lists"))
                continue
            for substage in [*required, *optional]:
                if substage not in known_substages:
                    issues.append(WorkflowValidationIssue("error", "RESEARCH_TYPE_SUBSTAGE_UNKNOWN", f"research type '{research_type}' references unknown substage '{substage}'"))
            overlap = set(required) & set(optional)
            if overlap:
                issues.append(WorkflowValidationIssue("error", "RESEARCH_TYPE_OVERLAP", f"research type '{research_type}' repeats {sorted(overlap)} as required and optional"))

    selected_map_path = composition_map or pack_dir / "substage-skill-map.yaml"
    if not selected_map_path.is_absolute():
        selected_map_path = repo_root / selected_map_path
    composition = _load_yaml(selected_map_path, issues) if selected_map_path.is_file() else {}
    if not selected_map_path.is_file():
        issues.append(WorkflowValidationIssue("error", "COMPOSITION_MAP_MISSING", f"missing composition map {selected_map_path.relative_to(repo_root)}"))
    if not isinstance(composition, Mapping):
        issues.append(WorkflowValidationIssue("error", "COMPOSITION_MAP_INVALID", f"{selected_map_path} must be a mapping"))
        composition = {}

    _meta, registry_rows, registry_warnings = load_registry(repo_root)
    for warning in registry_warnings:
        issues.append(WorkflowValidationIssue("error", "REGISTRY_INVALID", warning))
    registry_by_id = {row.get("skill_id"): row for row in registry_rows if isinstance(row.get("skill_id"), str)}
    for substage, entries in composition.items():
        if substage not in known_substages:
            issues.append(WorkflowValidationIssue("error", "COMPOSITION_SUBSTAGE_UNKNOWN", f"composition references unknown substage '{substage}'"))
        skill_ids = _skill_ids(entries)
        if not skill_ids:
            issues.append(WorkflowValidationIssue("error", "COMPOSITION_EMPTY", f"substage '{substage}' has no Skill"))
        for skill_id in skill_ids:
            row = registry_by_id.get(skill_id)
            if row is None:
                issues.append(WorkflowValidationIssue("error", "SKILL_UNREGISTERED", f"substage '{substage}' references unregistered Skill '{skill_id}'"))
                continue
            if pack not in _registry_packs(row):
                issues.append(WorkflowValidationIssue("error", "SKILL_PACK_MISMATCH", f"Skill '{skill_id}' is not registered for pack '{pack}'"))
            if substage not in set(row.get("substages") or []):
                issues.append(WorkflowValidationIssue("error", "SKILL_SUBSTAGE_MISMATCH", f"Skill '{skill_id}' is not registered for substage '{substage}'"))
            contract = row.get("runtime_contract")
            if not isinstance(contract, Mapping) or set(contract) != RUNTIME_CONTRACT_KEYS:
                runtime = str(row.get("runtime") or "")
                severity = "warning" if runtime == "claude_code" else "error"
                issues.append(WorkflowValidationIssue(severity, "RUNTIME_CONTRACT_MISSING", f"Skill '{skill_id}' has no complete runtime_contract; legacy claude_code execution may still be available"))
            skill_path = row.get("skill_path")
            if not isinstance(skill_path, str) or not (repo_root / skill_path / "SKILL.md").is_file():
                issues.append(WorkflowValidationIssue("error", "SKILL_SOURCE_MISSING", f"Skill '{skill_id}' has no readable source directory"))

    return issues
