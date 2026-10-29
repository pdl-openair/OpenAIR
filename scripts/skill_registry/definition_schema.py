"""Formal SkillDefinition field schema and Registry projection contract."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


REGISTRY_VERSION = "1.0.0"


@dataclass(frozen=True)
class FieldSpec:
    source: str
    value_types: tuple[type, ...]
    registry_key: str | None = None
    allow_none: bool = False


def _yaml(*value_types: type, registry_key: str | None = None, allow_none: bool = False) -> FieldSpec:
    return FieldSpec("skill_yaml", value_types, registry_key, allow_none)


REGISTRY_FIELD_SPECS: dict[str, FieldSpec] = {
    "skill_id": _yaml(str), "claude_name": _yaml(str), "display_name": _yaml(str),
    "domain": FieldSpec("directory", (str,)), "stages": _yaml(list), "substages": _yaml(list),
    "pack": _yaml(str), "packs": _yaml(list), "sequence": _yaml(int), "enabled": _yaml(bool),
    "spec": _yaml(str), "input_skills": _yaml(list), "skill_type": _yaml(str),
    "runtime": _yaml(str), "status": _yaml(str), "owner": _yaml(str), "source": _yaml(str),
    "skill_path": FieldSpec("directory", (str,)),
    "claude_skill_path": FieldSpec("directory", (str,)),
    "input_schema": _yaml(str, dict), "output_schema": _yaml(str, dict), "output_file": _yaml(str),
    "capability_contract": _yaml(dict), "execution_method_roles": _yaml(list),
    "runtime_contract": _yaml(dict, allow_none=True), "durable_model_profile": _yaml(str),
    "external_source_records": _yaml(list),
    "durable_llm_execution_policy": _yaml(dict), "durable_llm_batch_policy": _yaml(dict),
    "structured_output": _yaml(dict), "finalization": _yaml(dict), "review_policy": _yaml(dict),
    "scientific_state_projection": _yaml(dict), "conditional_binding": _yaml(dict),
    "example_request": _yaml(str), "example_response": _yaml(str), "fixture_status": _yaml(str),
    "notes": _yaml(str),
}

SKILL_YAML_FIELD_SPECS: dict[str, FieldSpec] = {
    **REGISTRY_FIELD_SPECS,
    "version": _yaml(str), "description": _yaml(str), "stage": _yaml(str), "substage": _yaml(str),
    "auto_allowed": _yaml(bool), "review_required": _yaml(bool), "risk_level": _yaml(str),
    "input_handoff": _yaml(dict), "llm": _yaml(dict), "durable_execution": _yaml(dict),
    "config": _yaml(dict), "capability_kind": _yaml(str), "external_actions": _yaml(list),
    "side_effects": _yaml(str), "inputs_schema": _yaml(str), "outputs_schema": _yaml(str),
}

REGISTRY_FIELD_ORDER = tuple(REGISTRY_FIELD_SPECS)
PROJECTION_DEFAULTS: dict[str, Any] = {
    "stages": [], "skill_type": "atomic", "runtime": "claude_code", "status": "draft",
    "owner": "TBD", "source": "manual", "fixture_status": "missing", "notes": "",
}


def known_skill_yaml_fields() -> frozenset[str]:
    return frozenset(SKILL_YAML_FIELD_SPECS)


def validate_skill_yaml_field_types(skill_yaml: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    for key in sorted(set(skill_yaml) - known_skill_yaml_fields()):
        issues.append(f"unknown SkillDefinition field '{key}'")
    for key, value in skill_yaml.items():
        spec = SKILL_YAML_FIELD_SPECS.get(key)
        if spec is None or (value is None and spec.allow_none):
            continue
        if not isinstance(value, spec.value_types):
            expected = " / ".join(type_.__name__ for type_ in spec.value_types)
            issues.append(f"skill.yaml.{key} must be {expected}")
    return issues
