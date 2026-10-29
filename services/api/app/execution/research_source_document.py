"""Read one frozen producer document, including its authorized typed projections."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def compose_source_document(
    sources: Sequence[Mapping[str, Any]],
    schema: Mapping[str, Any],
    identity: Any,
) -> dict[str, Any]:
    from app.execution.research_input_contract_gate import _failure

    required = schema.get("required")
    properties = schema.get("properties")
    field_set = (
        set(required)
        if isinstance(required, list) and all(isinstance(v, str) for v in required)
        else set()
    )
    # The writer splits only multi-output productions; a single source remains
    # the historical whole document, including ordinary scientific gaps.
    parsed = [_parse_source(source, identity) for source in sources]
    if len(parsed) == 1:
        if not isinstance(parsed[0], dict):
            raise _failure(identity, "input_composition_source_artifact_invalid")
        return parsed[0]
    types = [source.get("artifactType") for source in sources]
    if (
        not field_set
        or not isinstance(properties, Mapping)
        or any(not isinstance(kind, str) or kind not in field_set for kind in types)
        or len(set(types)) != len(types)
        or set(types) != field_set
        or not field_set.issubset(properties)
    ):
        raise _failure(identity, "input_composition_source_artifact_ambiguous")
    calls = [source.get("sourceCapabilityCallId") for source in sources]
    if any(not isinstance(call, str) or not call for call in calls) or len(set(calls)) != 1:
        raise _failure(identity, "input_composition_source_artifact_ambiguous")
    return {kind: value for kind, value in zip(types, parsed, strict=True) if isinstance(kind, str)}


def _parse_source(source: Mapping[str, Any], identity: Any) -> Any:
    from app.execution.research_source_material import parse_scientific_source

    return parse_scientific_source(source, identity)


def _read_source_document(
    *,
    snapshot: Mapping[str, Any],
    identity: Any,
    input_context: Mapping[str, Any],
    source_binding: Mapping[str, Any],
    required: bool,
) -> dict[str, Any] | object:
    from app.execution.research_input_contract_gate import (
        _ABSENT,
        _failure,
        _frozen_skill_schema,
        _skill_definition,
        _validate_json_schema,
    )

    source_id = source_binding.get("skillId")
    source_version = source_binding.get("skillDefinitionVersion")
    source_binding_id = source_binding.get("bindingId")
    source_binding_version = source_binding.get("bindingVersion")
    if (
        not isinstance(source_id, str)
        or not source_id
        or not isinstance(source_version, int)
        or isinstance(source_version, bool)
        or not isinstance(source_binding_id, str)
        or not source_binding_id
        or not isinstance(source_binding_version, int)
        or isinstance(source_binding_version, bool)
    ):
        raise _failure(identity, "input_composition_source_binding_invalid")

    materials = input_context.get("artifacts")
    if not isinstance(materials, list):
        raise _failure(identity, "input_composition_materials_missing")

    exact_matches: list[Mapping[str, Any]] = []
    same_skill_wrong_binding: list[Mapping[str, Any]] = []
    for row in materials:
        if not isinstance(row, Mapping):
            continue
        if (
            row.get("sourceCapabilityType") != "skill"
            or row.get("sourceCapabilityId") != source_id
            or row.get("sourceCapabilityDefinitionVersion") != source_version
        ):
            continue
        refs = row.get("sourceBindingRefs")
        if not isinstance(refs, list):
            raise _failure(identity, "input_composition_source_lineage_missing")
        normalized = {
            (ref.get("bindingId"), ref.get("bindingVersion"))
            for ref in refs
            if isinstance(ref, Mapping)
        }
        if (source_binding_id, source_binding_version) in normalized:
            exact_matches.append(row)
        else:
            same_skill_wrong_binding.append(row)

    if not exact_matches:
        if same_skill_wrong_binding:
            raise _failure(identity, "input_composition_source_lineage_mismatch")
        if required:
            raise _failure(
                identity,
                "input_composition_source_artifact_missing",
                failure_kind="material_unavailable",
            )
        return _ABSENT

    source_definition = _skill_definition(snapshot, source_id, source_version, identity)
    source_schema = _frozen_skill_schema(source_definition, "output")
    if source_schema is None:
        raise _failure(identity, "input_composition_source_schema_missing")
    parsed = compose_source_document(exact_matches, source_schema, identity)
    _validate_json_schema(
        identity=identity,
        document=parsed,
        schema=source_schema,
        failure_code="input_composition_source_artifact_invalid",
        unavailable_source_fields=frozenset(source_schema.get("properties", {})),
    )
    return parsed


def _scientific_source_value(
    *,
    snapshot: Mapping[str, Any],
    identity: Any,
    input_context: Mapping[str, Any],
    source: Mapping[str, Any],
    source_presence: str,
) -> Any:

    from app.execution.research_input_contract_gate import (
        _ABSENT,
        _failure,
        _read_source_documents,
        _resolve_artifact_source_path,
        _skill_binding,
    )

    kind = source.get("kind")
    required = source_presence in {"required", "conditional"}
    if kind == "artifact":
        binding_id = source.get("bindingId")
        binding_version = source.get("bindingVersion")
        source_field = source.get("field")
        if (
            not isinstance(binding_id, str)
            or not binding_id
            or not isinstance(binding_version, int)
            or isinstance(binding_version, bool)
            or binding_version < 1
            or not isinstance(source_field, str)
            or not source_field
        ):
            raise _failure(identity, "input_composition_contract_invalid")
        source_binding = _skill_binding(
            snapshot=snapshot,
            binding_id=binding_id,
            binding_version=binding_version,
            identity=identity,
        )
        cardinality = source.get("cardinality", "one")
        if cardinality not in {"one", "many"}:
            raise _failure(identity, "input_composition_contract_invalid")
        if cardinality == "many":
            source_documents = _read_source_documents(
                snapshot=snapshot,
                identity=identity,
                input_context=input_context,
                source_binding=source_binding,
                required=False,
            )
            if source_documents is _ABSENT:
                return _missing_scientific_source(identity, source, required)
            if not isinstance(source_documents, list):
                raise _failure(identity, "input_composition_source_artifact_invalid")
            if source_field == "$":
                return source_documents
            projected: list[Any] = []
            for source_document in source_documents:
                value = _resolve_artifact_source_path(
                    identity=identity,
                    source_document=source_document,
                    source_path=source_field,
                )
                projected.extend(value if isinstance(value, list) else [value])
            return projected
        source_document = _read_source_document(
            snapshot=snapshot,
            identity=identity,
            input_context=input_context,
            source_binding=source_binding,
            required=False,
        )
        if source_document is _ABSENT:
            return _missing_scientific_source(identity, source, required)
        if not isinstance(source_document, Mapping):
            raise _failure(identity, "input_composition_source_artifact_invalid")
        if source_field == "$":
            return source_document
        return _resolve_artifact_source_path(
            identity=identity,
            source_document=source_document,
            source_path=source_field,
        )

    if kind == "scientificState":
        return _scientific_state_source_value(identity, input_context, source, required)
    raise _failure(identity, "input_composition_contract_invalid")


def _scientific_state_source_value(
    identity: Any,
    input_context: Mapping[str, Any],
    source: Mapping[str, Any],
    required: bool,
) -> Any:
    import logging

    from app.execution.research_input_contract_gate import _ABSENT, _failure

    field = source.get("field")
    if not isinstance(field, str) or not field:
        raise _failure(identity, "input_composition_contract_invalid")
    state = input_context.get("scientificState")
    state_present = input_context.get("scientificStatePresent") is True
    if not state_present or (isinstance(state, Mapping) and field != "$" and field not in state):
        if not required:
            return _ABSENT
        logging.getLogger(__name__).warning(
            "scientific_source_field_gap capability=%s path=%s",
            identity.capability_id,
            field,
        )
        return None
    if not isinstance(state, Mapping):
        raise _failure(identity, "input_composition_scientific_state_invalid")
    return dict(state) if field == "$" else state[field]


def _missing_scientific_source(identity: Any, source: Mapping[str, Any], required: bool) -> Any:
    import logging

    from app.execution.research_input_contract_gate import _ABSENT

    if not required:
        return _ABSENT
    logging.getLogger(__name__).warning(
        "scientific_source_artifact_gap capability=%s binding=%s path=%s",
        identity.capability_id,
        source.get("bindingId"),
        source.get("field"),
    )
    return None


def _read_source_documents(
    *,
    snapshot: Mapping[str, Any],
    identity: Any,
    input_context: Mapping[str, Any],
    source_binding: Mapping[str, Any],
    required: bool,
) -> list[dict[str, Any]] | object:
    from app.execution.research_input_contract_gate import (
        _ABSENT,
        _failure,
        _frozen_skill_schema,
        _skill_definition,
        _validate_json_schema,
    )

    source_id = source_binding.get("skillId")
    source_version = source_binding.get("skillDefinitionVersion")
    source_binding_id = source_binding.get("bindingId")
    source_binding_version = source_binding.get("bindingVersion")
    if (
        not isinstance(source_id, str)
        or not source_id
        or not isinstance(source_version, int)
        or isinstance(source_version, bool)
        or not isinstance(source_binding_id, str)
        or not source_binding_id
        or not isinstance(source_binding_version, int)
        or isinstance(source_binding_version, bool)
    ):
        raise _failure(identity, "input_composition_source_binding_invalid")

    materials = input_context.get("artifacts")
    if not isinstance(materials, list):
        raise _failure(identity, "input_composition_materials_missing")

    exact_matches: list[Mapping[str, Any]] = []
    same_skill_wrong_binding: list[Mapping[str, Any]] = []
    for row in materials:
        if not isinstance(row, Mapping):
            continue
        if (
            row.get("sourceCapabilityType") != "skill"
            or row.get("sourceCapabilityId") != source_id
            or row.get("sourceCapabilityDefinitionVersion") != source_version
        ):
            continue
        refs = row.get("sourceBindingRefs")
        if not isinstance(refs, list):
            raise _failure(identity, "input_composition_source_lineage_missing")
        normalized = {
            (ref.get("bindingId"), ref.get("bindingVersion"))
            for ref in refs
            if isinstance(ref, Mapping)
        }
        if (source_binding_id, source_binding_version) in normalized:
            exact_matches.append(row)
        else:
            same_skill_wrong_binding.append(row)

    if not exact_matches:
        if same_skill_wrong_binding:
            raise _failure(identity, "input_composition_source_lineage_mismatch")
        if required:
            raise _failure(
                identity,
                "input_composition_source_artifact_missing",
                failure_kind="material_unavailable",
            )
        return _ABSENT

    def artifact_sort_key(row: Mapping[str, Any]) -> tuple[str, str]:
        artifact_id = row.get("artifactId")
        content_sha = row.get("contentSha256")
        return (str(artifact_id or ""), str(content_sha or ""))

    parsed_documents: list[dict[str, Any]] = []
    source_definition = _skill_definition(snapshot, source_id, source_version, identity)
    source_schema = _frozen_skill_schema(source_definition, "output")
    if source_schema is None:
        raise _failure(identity, "input_composition_source_schema_missing")

    for source in sorted(exact_matches, key=artifact_sort_key):
        parsed = _parse_source(source, identity)
        _validate_json_schema(
            identity=identity,
            document=parsed,
            schema=source_schema,
            failure_code="input_composition_source_artifact_invalid",
            unavailable_source_fields=frozenset(source_schema.get("properties", {})),
        )
        parsed_documents.append(parsed)
    return parsed_documents
