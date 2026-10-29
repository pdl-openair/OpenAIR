"""Prepare capability input and enforce its frozen contract before dispatch.

The gate is deliberately domain-agnostic. It reads only immutable execution
facts plus the frozen Registry definition, performs declarative composition,
and validates the exact payload that the provider will execute. Scientific
field transformations must be represented by a proper domain capability or
contract; they are never inferred here.
"""

from __future__ import annotations

import copy
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from app.modules.binding_source_path import BindingSourcePathError


CapabilityKind = Literal["skill", "agent"]
_ABSENT = object()
_SCIENTIFIC_SOURCE_KINDS = frozenset({"artifact", "scientificState"})
_RUNTIME_SOURCE_KINDS = frozenset({"executionIdentity", "invocationContext", "literal"})


@dataclass(frozen=True)
class CapabilityContractIdentity:
    capability_kind: CapabilityKind
    capability_id: str
    definition_version: int


class InputContractGateFailure(RuntimeError):
    def __init__(
        self,
        *,
        code: str,
        capability_kind: CapabilityKind,
        capability_id: str,
        definition_version: int,
        field_paths: Sequence[str] = (),
        failure_kind: Literal["material_unavailable", "contract_violation"] = "contract_violation",
    ) -> None:
        self.failure_kind = failure_kind
        self.code = code
        self.capability_kind = capability_kind
        self.capability_id = capability_id
        self.definition_version = definition_version
        self.field_paths = tuple(field_paths)
        super().__init__(code)

    @property
    def facts(self) -> dict[str, Any]:
        facts: dict[str, Any] = {
            "failureKind": self.failure_kind,
            "capabilityKind": self.capability_kind,
            "capabilityId": self.capability_id,
            "definitionVersion": self.definition_version,
        }
        if self.field_paths:
            facts["fieldPaths"] = list(self.field_paths)
        return facts


@dataclass(frozen=True)
class ExecutionInputCandidate:
    task_id: str
    research_design_version: int
    research_goal: str
    execution_decision_id: str
    assignment_id: str
    assignment_role: str
    strategy: str
    capability_kind: CapabilityKind
    capability_id: str
    definition_version: int
    instruction: str
    input_artifact_refs: tuple[str, ...]
    input_artifact_snapshots: tuple[Mapping[str, Any], ...]
    input_memory_refs: tuple[str, ...]
    @property
    def identity(self) -> CapabilityContractIdentity:
        return CapabilityContractIdentity(
            capability_kind=self.capability_kind,
            capability_id=self.capability_id,
            definition_version=self.definition_version,
        )


@dataclass(frozen=True)
class SkillInputRequest:
    task_id: str
    strategy: str
    skill_id: str
    definition_version: int
    @property
    def identity(self) -> CapabilityContractIdentity:
        return CapabilityContractIdentity(
            capability_kind="skill",
            capability_id=self.skill_id,
            definition_version=self.definition_version,
        )


def execution_input_candidate_from_assignment(
    *,
    task: Any,
    decision: Any,
    assignment: Any,
    instruction: str,
    input_artifact_refs: Sequence[str],
    input_artifact_snapshots: Sequence[Any],
    input_memory_refs: Sequence[str],
) -> ExecutionInputCandidate:
    """Project the exact execution input before a WorkUnit is frozen.

    The caller supplies only facts already fixed by the ExecutionDecision and
    the prepared input references.  This is the pre-WorkUnit form of the same
    authority projection used again at dispatch time.
    """
    authoritative_assignment = next(
        (
            row
            for row in decision.assignments
            if row.assignment_id == assignment.assignment_id
        ),
        None,
    )
    if authoritative_assignment is None or authoritative_assignment != assignment:
        raise ValueError("execution_input_assignment_mismatch")

    definition_version = assignment.executor_ref.definition_version
    if not isinstance(definition_version, int) or isinstance(definition_version, bool):
        raise ValueError("execution_input_definition_version_missing")

    design = next(
        (
            row
            for row in task.research_design_versions
            if row.version == decision.research_design_version
            and getattr(row.status, "value", row.status) == "confirmed"
        ),
        None,
    )
    if design is None:
        raise ValueError("execution_input_research_design_missing")

    capability_kind = assignment.executor_ref.executor_type
    if capability_kind not in {"skill", "agent"}:
        raise ValueError("execution_input_executor_type_invalid")

    snapshots = tuple(
        row.model_dump(mode="json", by_alias=True)
        if hasattr(row, "model_dump")
        else dict(row)
        for row in input_artifact_snapshots
    )
    return ExecutionInputCandidate(
        task_id=task.task_id,
        research_design_version=decision.research_design_version,
        research_goal=design.goal,
        execution_decision_id=decision.execution_decision_id,
        assignment_id=assignment.assignment_id,
        assignment_role=assignment.role.value,
        strategy=decision.strategy.value,
        capability_kind=capability_kind,
        capability_id=assignment.executor_ref.executor_id,
        definition_version=definition_version,
        instruction=instruction,
        input_artifact_refs=tuple(input_artifact_refs),
        input_artifact_snapshots=snapshots,
        input_memory_refs=tuple(input_memory_refs),
    )


def execution_input_candidate_from_authority(
    *,
    task: Any,
    decision: Any,
    work_unit: Any,
) -> ExecutionInputCandidate:
    """Project the same execution-input facts from a frozen WorkUnit."""
    assignment = next(
        (
            row
            for row in decision.assignments
            if row.assignment_id == work_unit.assignment_id
        ),
        None,
    )
    if assignment is None or assignment.executor_ref != work_unit.executor_ref:
        raise ValueError("execution_input_assignment_mismatch")

    return execution_input_candidate_from_assignment(
        task=task,
        decision=decision,
        assignment=assignment,
        instruction=work_unit.instruction,
        input_artifact_refs=work_unit.input_artifact_refs,
        input_artifact_snapshots=work_unit.input_artifact_snapshots,
        input_memory_refs=work_unit.input_memory_refs,
    )


def resolve_and_validate_execution_input(
    *,
    snapshot: Mapping[str, Any],
    candidate: ExecutionInputCandidate,
    input_context: Mapping[str, Any],
    skill_binding: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the exact validated provider input for one execution candidate.

    Skill input provenance is frozen on the concrete Definition→Skill Binding.
    A Skill definition remains only the public input/output contract authority.
    """
    definition = _capability_definition(snapshot, candidate.identity)
    if candidate.capability_kind == "skill":
        if not isinstance(skill_binding, Mapping):
            raise _failure(candidate.identity, "input_contract_skill_binding_missing")
        return resolve_registered_skill_input(
            snapshot=snapshot,
            request=SkillInputRequest(
                task_id=candidate.task_id,
                strategy=candidate.strategy,
                skill_id=candidate.capability_id,
                definition_version=candidate.definition_version,
            ),
            input_context=input_context,
            skill_binding=skill_binding,
        )

    resolved = _resolve_agent_input(candidate, input_context)
    if candidate.assignment_role == "reviewer":
        _validate_reviewer_contract(definition, candidate.identity, resolved)
    else:
        _validate_agent_contract(definition, candidate.identity, resolved)
    return resolved


def resolve_registered_skill_input(
    *,
    snapshot: Mapping[str, Any],
    request: SkillInputRequest,
    input_context: Mapping[str, Any],
    skill_binding: Mapping[str, Any],
) -> dict[str, Any]:
    """Assemble one Skill input from its frozen Binding and validate it.

    Binding sources may only copy declared values mechanically. Scientific
    reshaping belongs in a capability/public contract, never in this gate.
    """
    definition = _capability_definition(snapshot, request.identity)
    resolved = _resolve_skill_input(
        snapshot=snapshot,
        definition=definition,
        request=request,
        input_context=input_context,
        skill_binding=skill_binding,
    )
    scientific_fields = frozenset(
        row["targetField"] for row in (skill_binding.get("inputSources") or {}).get("fields", [])
    )
    _validate_skill_schema(
        definition, request.identity, resolved, unavailable_source_fields=scientific_fields,
        execution_fields=frozenset(
            row["targetField"] for row in (skill_binding.get("runtimeInputs") or {}).get("fields", [])
        ),
    )
    return resolved


def validate_registered_skill_input_snapshot(
    *,
    snapshot: Mapping[str, Any],
    request: SkillInputRequest,
    resolved_input: Mapping[str, Any],
    skill_binding: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate one WorkUnit-frozen Skill payload against frozen authorities.

    The payload was already assembled from formal sources before the WorkUnit
    was frozen. Dispatch therefore re-validates identity and schema only; it
    must not re-read a newer ScientificState and silently change execution.
    """
    definition = _capability_definition(snapshot, request.identity)
    _validate_target_binding(skill_binding, request)
    resolved = dict(resolved_input)
    _validate_skill_schema(
        definition, request.identity, resolved,
        unavailable_source_fields=frozenset(
            row["targetField"]
            for row in (skill_binding.get("inputSources") or {}).get("fields", [])
        ),
        execution_fields=frozenset(
            row["targetField"] for row in (skill_binding.get("runtimeInputs") or {}).get("fields", [])
        ),
    )
    return resolved


def _capability_definition(
    snapshot: Mapping[str, Any],
    identity: CapabilityContractIdentity,
) -> Mapping[str, Any]:
    definitions = snapshot.get("definitions")
    if not isinstance(definitions, Mapping):
        raise _failure(identity, "input_contract_registry_snapshot_invalid")

    collection = "skills" if identity.capability_kind == "skill" else "agents"
    identity_field = "skillId" if identity.capability_kind == "skill" else "agentId"
    rows = definitions.get(collection)
    if not isinstance(rows, list):
        raise _failure(identity, "input_contract_registry_snapshot_invalid")

    matches = [
        row
        for row in rows
        if isinstance(row, Mapping)
        and row.get(identity_field) == identity.capability_id
        and row.get("definitionVersion") == identity.definition_version
    ]
    if len(matches) != 1:
        raise _failure(identity, "input_contract_capability_definition_missing")
    return matches[0]


def _resolve_skill_input(
    *,
    snapshot: Mapping[str, Any],
    definition: Mapping[str, Any],
    request: SkillInputRequest,
    input_context: Mapping[str, Any],
    skill_binding: Mapping[str, Any],
) -> dict[str, Any]:
    identity = request.identity
    _validate_target_binding(skill_binding, request)
    resolved: dict[str, Any] = {}

    input_sources = skill_binding.get("inputSources")
    if input_sources is not None:
        if not isinstance(input_sources, Mapping) or input_sources.get("contractVersion") != 2:
            raise _failure(identity, "input_composition_contract_invalid")
        fields = input_sources.get("fields")
        if not isinstance(fields, list):
            raise _failure(identity, "input_composition_contract_invalid")
        for field_spec in fields:
            if not isinstance(field_spec, Mapping):
                raise _failure(identity, "input_composition_contract_invalid")
            target_field = field_spec.get("targetField")
            presence = field_spec.get("sourcePresence")
            source = field_spec.get("source")
            if (
                not isinstance(target_field, str) or not target_field
                or target_field in resolved
                or presence not in {"required", "optional", "conditional"}
                or not isinstance(source, Mapping)
                or source.get("kind") not in _SCIENTIFIC_SOURCE_KINDS
            ):
                raise _failure(identity, "input_composition_contract_invalid")
            value = _scientific_source_value(
                snapshot=snapshot,
                identity=identity,
                input_context=input_context,
                source=source,
                source_presence=presence,
            )
            if value is _ABSENT and "defaultValue" in field_spec:
                value = copy.deepcopy(field_spec["defaultValue"])
            if value is not _ABSENT:
                resolved[target_field] = value

    runtime_inputs = skill_binding.get("runtimeInputs")
    if runtime_inputs is not None:
        if not isinstance(runtime_inputs, Mapping) or runtime_inputs.get("contractVersion") != 2:
            raise _failure(identity, "input_composition_contract_invalid")
        fields = runtime_inputs.get("fields")
        if not isinstance(fields, list):
            raise _failure(identity, "input_composition_contract_invalid")
        for field_spec in fields:
            if not isinstance(field_spec, Mapping):
                raise _failure(identity, "input_composition_contract_invalid")
            target_field = field_spec.get("targetField")
            source = field_spec.get("source")
            if (
                not isinstance(target_field, str) or not target_field
                or target_field in resolved
                or not isinstance(source, Mapping)
                or source.get("kind") not in _RUNTIME_SOURCE_KINDS
            ):
                raise _failure(identity, "input_composition_contract_invalid")
            resolved[target_field] = _runtime_source_value(
                identity=identity,
                request=request,
                input_context=input_context,
                source=source,
            )
    return resolved


def _validate_target_binding(binding: Mapping[str, Any], request: SkillInputRequest) -> None:
    identity = request.identity
    if (
        binding.get("skillId") != request.skill_id
        or binding.get("skillDefinitionVersion") != request.definition_version
        or binding.get("enabled") is not True
        or not isinstance(binding.get("bindingId"), str)
        or not binding.get("bindingId")
        or not isinstance(binding.get("bindingVersion"), int)
        or isinstance(binding.get("bindingVersion"), bool)
        or binding.get("bindingVersion") < 1
    ):
        raise _failure(identity, "input_contract_skill_binding_mismatch")


def _scientific_source_value(**kwargs: Any) -> Any:
    from app.execution.research_source_document import _scientific_source_value as resolve_source

    return resolve_source(**kwargs)


def _resolve_artifact_source_path(
    *,
    identity: CapabilityContractIdentity,
    source_document: Mapping[str, Any],
    source_path: str,
) -> Any:
    try:
        from app.execution.research_source_material import resolve_scientific_source_value

        return resolve_scientific_source_value(source_document, source_path)
    except BindingSourcePathError as exc:
        if exc.code in {"binding_source_path_missing", "binding_source_path_non_object"}:
            logging.getLogger(__name__).warning(
                "scientific_source_field_gap capability=%s path=%s code=%s",
                identity.capability_id, source_path, exc.code,
            )
            return None
        code = {
            "binding_source_path_invalid": "input_composition_source_path_invalid",
            "binding_source_path_missing": "input_composition_source_field_missing",
            "binding_source_path_non_object": "input_composition_source_path_non_object",
        }[exc.code]
        raise _failure(
            identity,
            code,
            (source_path,),
            failure_kind="material_unavailable",
        ) from exc


def _runtime_source_value(
    *,
    identity: CapabilityContractIdentity,
    request: SkillInputRequest,
    input_context: Mapping[str, Any],
    source: Mapping[str, Any],
) -> Any:
    kind = source.get("kind")
    if kind == "executionIdentity":
        field = source.get("field")
        values = input_context.get("executionIdentity")
        if field != "task_id" or not isinstance(values, Mapping) or not values.get(field):
            raise _failure(identity, "input_composition_context_value_missing", (str(field or ""),))
        if values[field] != request.task_id:
            raise _failure(identity, "input_composition_context_identity_mismatch", (field,))
        return values[field]
    if kind == "invocationContext":
        field = source.get("field")
        values = input_context.get("invocationContext")
        if not isinstance(field, str) or not field or not isinstance(values, Mapping) or field not in values:
            raise _failure(identity, "input_composition_invocation_context_missing", (str(field or ""),))
        return values[field]
    if kind == "literal":
        if "value" not in source:
            raise _failure(identity, "input_composition_contract_invalid")
        return source["value"]
    raise _failure(identity, "input_composition_contract_invalid")


def _skill_binding(
    *,
    snapshot: Mapping[str, Any],
    binding_id: str,
    binding_version: int,
    identity: CapabilityContractIdentity,
) -> Mapping[str, Any]:
    bindings = snapshot.get("bindings")
    rows = bindings.get("skills") if isinstance(bindings, Mapping) else None
    matches = [
        row
        for row in rows or []
        if isinstance(row, Mapping)
        and row.get("bindingId") == binding_id
        and row.get("bindingVersion") == binding_version
        and row.get("enabled") is True
    ]
    if len(matches) != 1:
        raise _failure(identity, "input_composition_source_binding_missing")
    return matches[0]

def _resolve_agent_input(
    candidate: ExecutionInputCandidate,
    input_context: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the generic frozen Agent input without inventing semantic aliases."""
    resolved_memory: dict[str, str] = {}
    if (
        candidate.research_goal
        and any(ref in candidate.input_memory_refs for ref in ("research_goal", "user_goal"))
    ):
        resolved_memory["user_goal"] = candidate.research_goal

    resolved: dict[str, Any] = {
        "instruction": candidate.instruction,
        "inputArtifactRefs": list(candidate.input_artifact_refs),
        "inputMemoryRefs": list(candidate.input_memory_refs),
        "resolvedInputMemory": resolved_memory,
    }
    artifacts = input_context.get("artifacts")
    if isinstance(artifacts, list) and artifacts:
        resolved["inputArtifactMaterials"] = artifacts
    return resolved


def _validate_agent_contract(
    definition: Mapping[str, Any],
    identity: CapabilityContractIdentity,
    resolved_input: Mapping[str, Any],
) -> None:
    contract = definition.get("inputContract")
    required = contract.get("required") if isinstance(contract, Mapping) else None
    if (
        not isinstance(required, list)
        or any(not isinstance(value, str) or not value for value in required)
    ):
        raise _failure(identity, "input_contract_agent_contract_invalid")

    available_refs = set(resolved_input.get("inputMemoryRefs") or [])
    resolved_memory = resolved_input.get("resolvedInputMemory")
    if isinstance(resolved_memory, Mapping):
        available_refs.update(
            key
            for key, value in resolved_memory.items()
            if isinstance(key, str) and key and value is not None
        )

    missing = tuple(value for value in required if value not in available_refs)
    if missing:
        raise _failure(
            identity,
            "input_contract_invalid",
            tuple(f"/{value}" for value in missing),
        )


def _validate_reviewer_contract(
    definition: Mapping[str, Any],
    identity: CapabilityContractIdentity,
    resolved_input: Mapping[str, Any],
) -> None:
    """Validate the artifact-review input fixed by the Agent review contract."""
    contract = definition.get("reviewContract")
    if not isinstance(contract, Mapping) or not isinstance(
        contract.get("frozenArtifactContract"),
        Mapping,
    ):
        raise _failure(identity, "input_contract_reviewer_contract_invalid")

    artifact_refs = resolved_input.get("inputArtifactRefs")
    if (
        not isinstance(artifact_refs, list)
        or not artifact_refs
        or any(not isinstance(value, str) or not value for value in artifact_refs)
        or len(set(artifact_refs)) != len(artifact_refs)
    ):
        raise _failure(identity, "input_contract_invalid", ("/inputArtifactRefs",))

    materials = resolved_input.get("inputArtifactMaterials")
    if not isinstance(materials, list) or not materials:
        raise _failure(identity, "input_contract_invalid", ("/inputArtifactMaterials",))

    material_ids = [
        row.get("artifactId")
        for row in materials
        if isinstance(row, Mapping)
    ]
    if (
        len(material_ids) != len(materials)
        or any(not isinstance(value, str) or not value for value in material_ids)
        or len(set(material_ids)) != len(material_ids)
        or set(material_ids) != set(artifact_refs)
    ):
        raise _failure(identity, "input_contract_invalid", ("/inputArtifactMaterials",))


def _read_source_documents(**kwargs: Any) -> list[dict[str, Any]] | object:
    from app.execution.research_source_document import _read_source_documents as read_sources

    return read_sources(**kwargs)


def _read_source_document(**kwargs: Any) -> dict[str, Any] | object:
    from app.execution.research_source_document import _read_source_document as read_source

    return read_source(**kwargs)


def _validate_skill_schema(
    definition: Mapping[str, Any],
    identity: CapabilityContractIdentity,
    resolved_input: Mapping[str, Any],
    *, unavailable_source_fields: frozenset[str] = frozenset(),
    execution_fields: frozenset[str] = frozenset(),
) -> None:
    schema = _frozen_skill_schema(definition, "input")
    if schema is None:
        # Skills that do not cross an external action boundary (llm_reasoning skills
        # without structured_output / capability_contract / finalization / etc.) are
        # registered without frozenSchemas because registration_requires_frozen_schemas
        # returns False for them.  Enforcing the schema gate unconditionally here blocks
        # every such skill at execution selection.  Mirror the guards in
        # research_dispatch_skill_provider and research_internal_skill_call_preflight:
        # only require a frozen input schema when the skill actually requires external
        # action evidence (requiresExternalAction == True in runtimeContract).
        runtime_contract = definition.get("runtimeContract")
        requires_external = (
            runtime_contract.get("requiresExternalAction")
            if isinstance(runtime_contract, Mapping)
            else None
        )
        if requires_external:
            raise _failure(identity, "input_contract_skill_schema_missing")
        return
    schema = copy.deepcopy(dict(schema))
    properties = schema.setdefault("properties", {})
    for field in execution_fields:
        if field not in properties:
            raise _failure(identity, "input_contract_invalid", ("/" + field,))
        properties[field]["x-execution-critical"] = True
    _validate_json_schema(
        identity=identity,
        document=resolved_input,
        schema=schema,
        failure_code="input_contract_invalid",
        unavailable_source_fields=unavailable_source_fields - execution_fields,
    )


def _validate_json_schema(
    *, identity: CapabilityContractIdentity, document: Mapping[str, Any],
    schema: Mapping[str, Any], failure_code: str,
    unavailable_source_fields: frozenset[str] = frozenset(),
) -> None:
    from app.execution.skill_contract_policy import validate_composed_input_schema

    validate_composed_input_schema(
        identity=identity, document=document, schema=schema,
        failure_code=failure_code, unavailable_source_fields=unavailable_source_fields,
    )


def _skill_definition(
    snapshot: Mapping[str, Any],
    skill_id: str,
    definition_version: Any,
    identity: CapabilityContractIdentity,
) -> Mapping[str, Any]:
    definitions = snapshot.get("definitions")
    rows = definitions.get("skills") if isinstance(definitions, Mapping) else None
    matches = [
        row
        for row in rows or []
        if isinstance(row, Mapping)
        and row.get("skillId") == skill_id
        and row.get("definitionVersion") == definition_version
    ]
    if len(matches) != 1:
        raise _failure(identity, "input_composition_source_definition_missing")
    return matches[0]


def _frozen_skill_schema(
    definition: Mapping[str, Any],
    schema_name: str,
) -> Mapping[str, Any] | None:
    source_contract = definition.get("sourceContract")
    frozen_schemas = (
        source_contract.get("frozenSchemas")
        if isinstance(source_contract, Mapping)
        else None
    )
    frozen = (
        frozen_schemas.get(schema_name)
        if isinstance(frozen_schemas, Mapping)
        else None
    )
    if not isinstance(frozen, Mapping):
        return None
    schema = frozen.get("schema")
    digest = frozen.get("sha256")
    if (
        not isinstance(schema, Mapping)
        or not isinstance(digest, str)
        or len(digest) != 64
    ):
        return None
    return schema


def _failure(
    identity: CapabilityContractIdentity,
    code: str,
    field_paths: Sequence[str] = (),
    *, failure_kind: Literal["material_unavailable", "contract_violation"] = "contract_violation",
) -> InputContractGateFailure:
    return InputContractGateFailure(
        code=code,
        failure_kind=failure_kind,
        capability_kind=identity.capability_kind,
        capability_id=identity.capability_id,
        definition_version=identity.definition_version,
        field_paths=tuple(field_paths),
    )


__all__ = [
    "ExecutionInputCandidate",
    "InputContractGateFailure",
    "SkillInputRequest",
    "execution_input_candidate_from_assignment",
    "execution_input_candidate_from_authority",
    "resolve_and_validate_execution_input",
    "resolve_registered_skill_input",
]
