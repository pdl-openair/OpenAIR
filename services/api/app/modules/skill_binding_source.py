"""Canonical source loader for Definition→Skill Binding v2.

The file ``skills/skill-bindings.yaml`` is the production authority for
Definition→Skill usage identity. A Binding may additionally declare handoff
inputs. Scientific sources and runtime inputs are deliberately separate:

* ``inputSources``: Artifact / ScientificState only.
* ``runtimeInputs``: executionIdentity / invocationContext / literal only.

This loader validates source-file identity and the v2 declaration shape. Runtime
lineage and exact-value validation remain RegistrySnapshot/input-gate duties.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping
import yaml

from app.modules.yaml_source_cache import SAFE_LOADER, read_yaml_source

from app.modules.binding_source_path import (
    BindingSourcePathError,
    binding_source_path_steps,
)
from app.modules.repo_paths import repo_root

SKILL_BINDINGS_PATH = repo_root() / "skills" / "skill-bindings.yaml"
BINDING_SOURCE_VERSION = 2
BINDING_CONTRACT_VERSION = 2
_SOURCE_PRESENCE = {"required", "optional", "conditional"}
_SCIENTIFIC_KINDS = {"artifact", "scientificState"}
_RUNTIME_KINDS = {"executionIdentity", "invocationContext", "literal"}


class SkillBindingSourceError(ValueError):
    """Raised when canonical Binding source data is absent or ambiguous."""


class _UniqueKeyLoader(SAFE_LOADER):
    pass


def _construct_unique_mapping(loader: yaml.SafeLoader, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
    result: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            raise SkillBindingSourceError(f"duplicate_yaml_key:{key}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping)


def _positive_int(value: object, *, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise SkillBindingSourceError(f"invalid_{label}")
    return value


def _require_string(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SkillBindingSourceError(f"invalid_{label}")
    return value.strip()


def _json_scalar(value: object) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def _validate_admission_path(path: object) -> bool:
    return (
        isinstance(path, list)
        and bool(path)
        and all(isinstance(part, str) and part.strip() for part in path)
    )


def _validate_one_artifact_source_admission(value: object) -> None:
    if not isinstance(value, Mapping):
        raise SkillBindingSourceError("skill_binding_artifact_source_admission_invalid")
    kind = value.get("kind")
    path = value.get("path")
    if kind == "json_field_equals":
        if (
            set(value) != {"kind", "path", "equals"}
            or not _validate_admission_path(path)
            or not _json_scalar(value.get("equals"))
        ):
            raise SkillBindingSourceError("skill_binding_artifact_source_admission_invalid")
        return
    if kind == "json_field_not_null":
        if set(value) != {"kind", "path"} or not _validate_admission_path(path):
            raise SkillBindingSourceError("skill_binding_artifact_source_admission_invalid")
        return
    raise SkillBindingSourceError("skill_binding_artifact_source_admission_invalid")


def _validate_artifact_source_admission(value: object) -> None:
    if isinstance(value, list):
        if not value:
            raise SkillBindingSourceError("skill_binding_artifact_source_admission_invalid")
        for item in value:
            _validate_one_artifact_source_admission(item)
        return
    _validate_one_artifact_source_admission(value)


def _validate_scientific_state_projection(value: object) -> None:
    """Validate the binding-level scientific_state_projection shape.

    The loader is skill-agnostic, so source_field membership in the bound
    skill's frozen output schema is NOT checked here; that cross-entity
    check happens in the snapshot builder/validator. Here we only assert the
    v2 materialized declaration shape (contract_version/materializer_ref/facts)
    matches ``validate_scientific_state_projection_declaration`` expectations.
    """
    from app.modules.scientific_state_fact_projection import (
        _FACT_KEY,
        _MATERIALIZER_REF,
        _SUPPORTED_MATERIALIZER_REFS,
        MATERIALIZED_PROJECTION_CONTRACT_VERSION,
    )

    if not isinstance(value, Mapping):
        raise SkillBindingSourceError("skill_binding_projection_invalid")
    if set(value) != {"contract_version", "materializer_ref", "facts"}:
        raise SkillBindingSourceError("skill_binding_projection_invalid")
    if value.get("contract_version") != MATERIALIZED_PROJECTION_CONTRACT_VERSION:
        raise SkillBindingSourceError("skill_binding_projection_version_invalid")
    materializer_ref = str(value.get("materializer_ref") or "").strip()
    if (
        _MATERIALIZER_REF.fullmatch(materializer_ref) is None
        or materializer_ref not in _SUPPORTED_MATERIALIZER_REFS
    ):
        raise SkillBindingSourceError("skill_binding_projection_materializer_invalid")
    facts = value.get("facts")
    if not isinstance(facts, list) or not facts:
        raise SkillBindingSourceError("skill_binding_projection_facts_invalid")
    seen_keys: set[str] = set()
    seen_fields: set[str] = set()
    for row in facts:
        if not isinstance(row, Mapping) or set(row) != {"fact_key", "source_field"}:
            raise SkillBindingSourceError("skill_binding_projection_fact_invalid")
        fact_key = str(row.get("fact_key") or "").strip()
        source_field = str(row.get("source_field") or "").strip()
        if (
            _FACT_KEY.fullmatch(fact_key) is None
            or not source_field
            or fact_key in seen_keys
            or source_field in seen_fields
        ):
            raise SkillBindingSourceError("skill_binding_projection_fact_invalid")
        seen_keys.add(fact_key)
        seen_fields.add(source_field)


def _validate_input_sources(value: object) -> None:
    if not isinstance(value, Mapping) or set(value) != {"contractVersion", "fields"}:
        raise SkillBindingSourceError("skill_binding_input_sources_invalid")
    if value.get("contractVersion") != BINDING_CONTRACT_VERSION:
        raise SkillBindingSourceError("skill_binding_input_sources_version_invalid")
    fields = value.get("fields")
    if not isinstance(fields, list):
        raise SkillBindingSourceError("skill_binding_input_sources_fields_invalid")
    targets: set[str] = set()
    for entry in fields:
        if not isinstance(entry, Mapping):
            raise SkillBindingSourceError("skill_binding_input_source_invalid")
        keys = set(entry)
        if not {"targetField", "sourcePresence", "source"}.issubset(keys) or keys - {"targetField", "sourcePresence", "source", "defaultValue"}:
            raise SkillBindingSourceError("skill_binding_input_source_invalid")
        target = _require_string(entry.get("targetField"), label="input_source_target")
        if target in targets:
            raise SkillBindingSourceError("duplicate_skill_binding_target")
        targets.add(target)
        if entry.get("sourcePresence") not in _SOURCE_PRESENCE:
            raise SkillBindingSourceError("skill_binding_source_presence_invalid")
        if "defaultValue" in entry and entry.get("sourcePresence") != "optional":
            raise SkillBindingSourceError("skill_binding_default_requires_optional_source")
        source = entry.get("source")
        if not isinstance(source, Mapping):
            raise SkillBindingSourceError("skill_binding_scientific_source_invalid")
        kind = source.get("kind")
        if kind not in _SCIENTIFIC_KINDS:
            raise SkillBindingSourceError("skill_binding_scientific_source_kind_invalid")
        if kind == "artifact":
            source_keys = set(source)
            if not {"kind", "bindingId", "bindingVersion", "field"}.issubset(source_keys) or source_keys - {
                "kind",
                "bindingId",
                "bindingVersion",
                "field",
                "cardinality",
                "admission",
            }:
                raise SkillBindingSourceError("skill_binding_artifact_source_invalid")
            _require_string(source.get("bindingId"), label="artifact_source_binding_id")
            _positive_int(source.get("bindingVersion"), label="artifact_source_binding_version")
            source_field = _require_string(source.get("field"), label="artifact_source_field")
            try:
                binding_source_path_steps(source_field, ())
            except BindingSourcePathError as exc:
                raise SkillBindingSourceError("skill_binding_artifact_source_path_invalid") from exc
            if source.get("cardinality", "one") not in {"one", "many"}:
                raise SkillBindingSourceError("skill_binding_artifact_cardinality_invalid")
            if "admission" in source:
                _validate_artifact_source_admission(source.get("admission"))
        else:
            if set(source) != {"kind", "field"}:
                raise SkillBindingSourceError("skill_binding_scientific_state_source_invalid")
            _require_string(source.get("field"), label="scientific_state_source_field")


def _validate_runtime_inputs(value: object, *, occupied_targets: set[str]) -> None:
    if not isinstance(value, Mapping) or set(value) != {"contractVersion", "fields"}:
        raise SkillBindingSourceError("skill_binding_runtime_inputs_invalid")
    if value.get("contractVersion") != BINDING_CONTRACT_VERSION:
        raise SkillBindingSourceError("skill_binding_runtime_inputs_version_invalid")
    fields = value.get("fields")
    if not isinstance(fields, list):
        raise SkillBindingSourceError("skill_binding_runtime_inputs_fields_invalid")
    targets = set(occupied_targets)
    for entry in fields:
        if not isinstance(entry, Mapping) or set(entry) != {"targetField", "source"}:
            raise SkillBindingSourceError("skill_binding_runtime_input_invalid")
        target = _require_string(entry.get("targetField"), label="runtime_input_target")
        if target in targets:
            raise SkillBindingSourceError("duplicate_skill_binding_target")
        targets.add(target)
        source = entry.get("source")
        if not isinstance(source, Mapping):
            raise SkillBindingSourceError("skill_binding_runtime_source_invalid")
        kind = source.get("kind")
        if kind not in _RUNTIME_KINDS:
            raise SkillBindingSourceError("skill_binding_runtime_source_kind_invalid")
        if kind in {"executionIdentity", "invocationContext"}:
            if set(source) != {"kind", "field"}:
                raise SkillBindingSourceError("skill_binding_runtime_source_invalid")
            _require_string(source.get("field"), label="runtime_source_field")
        else:
            if set(source) != {"kind", "value"}:
                raise SkillBindingSourceError("skill_binding_literal_source_invalid")


def load_skill_binding_source_rows(path: Path = SKILL_BINDINGS_PATH) -> tuple[dict[str, Any], ...]:
    try:
        payload = read_yaml_source(path, loader=_UniqueKeyLoader) or {}
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise SkillBindingSourceError("skill_binding_source_unavailable") from exc
    if not isinstance(payload, Mapping) or set(payload) != {"version", "bindings"}:
        raise SkillBindingSourceError("skill_binding_source_shape_invalid")
    if payload.get("version") != BINDING_SOURCE_VERSION:
        raise SkillBindingSourceError("skill_binding_source_version_invalid")
    raw_rows = payload.get("bindings")
    if not isinstance(raw_rows, list):
        raise SkillBindingSourceError("skill_binding_source_rows_invalid")

    identity_fields = {
        "bindingId", "bindingVersion", "definitionId", "definitionVersion",
        "skillId", "skillDefinitionVersion",
    }
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for raw in raw_rows:
        if not isinstance(raw, Mapping):
            raise SkillBindingSourceError("skill_binding_source_row_invalid")
        keys = set(raw)
        allowed_extra = {"inputSources", "runtimeInputs", "scientificStateProjection"}
        if not identity_fields.issubset(keys) or keys - identity_fields - allowed_extra:
            raise SkillBindingSourceError("skill_binding_source_row_invalid")
        binding_id = _require_string(raw.get("bindingId"), label="binding_id")
        definition_id = _require_string(raw.get("definitionId"), label="definition_id")
        skill_id = _require_string(raw.get("skillId"), label="skill_id")
        binding_version = _positive_int(raw.get("bindingVersion"), label="binding_version")
        _positive_int(raw.get("definitionVersion"), label="definition_version")
        _positive_int(raw.get("skillDefinitionVersion"), label="skill_definition_version")
        if binding_id != f"bind:{definition_id}:skill:{skill_id}":
            raise SkillBindingSourceError("skill_binding_source_identity_invalid")
        occupied: set[str] = set()
        if "scientificStateProjection" in raw:
            _validate_scientific_state_projection(raw["scientificStateProjection"])
        if "inputSources" in raw:
            _validate_input_sources(raw["inputSources"])
            occupied = {entry["targetField"] for entry in raw["inputSources"]["fields"]}
        if "runtimeInputs" in raw:
            _validate_runtime_inputs(raw["runtimeInputs"], occupied_targets=occupied)
        ref = (binding_id, binding_version)
        if ref in seen:
            raise SkillBindingSourceError("duplicate_skill_binding_source")
        seen.add(ref)
        rows.append(dict(raw))
    return tuple(rows)


def skill_ids_with_binding_inputs(path: Path = SKILL_BINDINGS_PATH) -> frozenset[str]:
    return frozenset(
        str(row["skillId"])
        for row in load_skill_binding_source_rows(path)
        if "inputSources" in row or "runtimeInputs" in row
    )


def skill_ids_requiring_frozen_handoff_schemas(path: Path = SKILL_BINDINGS_PATH) -> frozenset[str]:
    result = set(skill_ids_with_binding_inputs(path))
    for row in load_skill_binding_source_rows(path):
        contract = row.get("inputSources")
        fields = contract.get("fields") if isinstance(contract, Mapping) else None
        if not isinstance(fields, list):
            continue
        for entry in fields:
            source = entry.get("source") if isinstance(entry, Mapping) else None
            if not isinstance(source, Mapping) or source.get("kind") != "artifact":
                continue
            binding_id = _require_string(source.get("bindingId"), label="artifact_source_binding_id")
            prefix, marker, source_skill_id = binding_id.rpartition(":skill:")
            if not prefix or not marker or not source_skill_id:
                raise SkillBindingSourceError("invalid_artifact_source_binding_id")
            result.add(source_skill_id)
    return frozenset(result)


def canonical_binding_handoff_evidence(binding: Mapping[str, Any]) -> dict[str, Any]:
    """Project one Binding v2 declaration to its canonical handoff identity evidence."""
    binding_id = _require_string(binding.get("bindingId"), label="binding_id")
    binding_version = _positive_int(binding.get("bindingVersion"), label="binding_version")
    return {
        "bindingId": binding_id,
        "bindingVersion": binding_version,
        "bindingContractVersion": BINDING_CONTRACT_VERSION,
        "inputSources": binding.get("inputSources"),
        "runtimeInputs": binding.get("runtimeInputs"),
    }


def binding_handoff_evidence_matches(skill_id: str, evidence: Mapping[str, Any] | None, path: Path = SKILL_BINDINGS_PATH) -> bool:
    if not isinstance(evidence, Mapping) or evidence.get("bindingContractVersion") != BINDING_CONTRACT_VERSION:
        return False
    binding_id = evidence.get("bindingId")
    binding_version = evidence.get("bindingVersion")
    if not isinstance(binding_id, str) or not isinstance(binding_version, int) or isinstance(binding_version, bool):
        return False
    row = next((r for r in load_skill_binding_source_rows(path) if r["skillId"] == skill_id and r["bindingId"] == binding_id and r["bindingVersion"] == binding_version), None)
    if row is None:
        return False
    return dict(evidence) == canonical_binding_handoff_evidence(row)


__all__ = [
    "BINDING_CONTRACT_VERSION", "BINDING_SOURCE_VERSION", "SKILL_BINDINGS_PATH",
    "SkillBindingSourceError", "binding_handoff_evidence_matches",
    "canonical_binding_handoff_evidence",
    "load_skill_binding_source_rows", "skill_ids_requiring_frozen_handoff_schemas",
    "skill_ids_with_binding_inputs",
]
