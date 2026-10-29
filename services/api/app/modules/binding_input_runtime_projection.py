"""Project frozen Binding input Artifacts into conditional-evaluator runtime facts.

Fact refs use the binding-local namespace ``binding-input:<targetField>``.  They are
resolved from the consumer Binding's ``inputSources`` plus already-admitted
Artifact scalar projections.  Nothing is written to ScientificState and no global
runtime fact registry is introduced.
"""

from __future__ import annotations

from typing import Any, Mapping

from app.modules.artifact_field_projection import artifact_field_projection_from_validator


BINDING_INPUT_FACT_PREFIX = "binding-input:"


def binding_input_fact_ref(target_field: str) -> str:
    return f"{BINDING_INPUT_FACT_PREFIX}{target_field}"


def is_binding_input_fact_ref(ref: str) -> bool:
    return (
        isinstance(ref, str)
        and ref.startswith(BINDING_INPUT_FACT_PREFIX)
        and len(ref) > len(BINDING_INPUT_FACT_PREFIX)
    )


def binding_input_target_field(ref: str) -> str | None:
    if not is_binding_input_fact_ref(ref):
        return None
    return ref[len(BINDING_INPUT_FACT_PREFIX) :]


def project_binding_input_runtime_facts(
    *,
    binding: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    research_task: Mapping[str, Any] | None,
    required_refs: set[str],
) -> dict[str, Any]:
    """Resolve required ``binding-input:*`` refs for one conditional Skill binding."""

    requested = {
        ref: binding_input_target_field(ref)
        for ref in required_refs
        if is_binding_input_fact_ref(ref)
    }
    if not requested or research_task is None:
        return {}

    input_sources = binding.get("inputSources")
    if (
        not isinstance(input_sources, Mapping)
        or input_sources.get("contractVersion") != 2
        or not isinstance(input_sources.get("fields"), list)
    ):
        return {}

    fields_by_target = {
        row.get("targetField"): row
        for row in input_sources["fields"]
        if isinstance(row, Mapping) and isinstance(row.get("targetField"), str)
    }
    projected: dict[str, Any] = {}
    for fact_ref, target_field in requested.items():
        if target_field is None:
            continue
        field_spec = fields_by_target.get(target_field)
        if not isinstance(field_spec, Mapping):
            continue
        source = field_spec.get("source")
        if not isinstance(source, Mapping) or source.get("kind") != "artifact":
            continue
        source_binding = _source_binding(snapshot, source)
        if source_binding is None:
            continue
        document = _projected_source_document(
            research_task=research_task,
            source_binding=source_binding,
        )
        if document is None:
            continue
        source_field = source.get("field")
        if source_field in {None, "$"}:
            projected[fact_ref] = document
            continue
        if not isinstance(source_field, str) or source_field not in document:
            continue
        projected[fact_ref] = {source_field: document[source_field]}
    return projected


def _source_binding(
    snapshot: Mapping[str, Any],
    source: Mapping[str, Any],
) -> Mapping[str, Any] | None:
    binding_id = source.get("bindingId")
    binding_version = source.get("bindingVersion")
    if (
        not isinstance(binding_id, str)
        or not binding_id
        or not isinstance(binding_version, int)
        or isinstance(binding_version, bool)
        or binding_version < 1
    ):
        return None
    rows = snapshot.get("bindings", {}).get("skills", [])
    if not isinstance(rows, list):
        return None
    matches = [
        row
        for row in rows
        if isinstance(row, Mapping)
        and row.get("bindingId") == binding_id
        and row.get("bindingVersion") == binding_version
        and row.get("enabled") is True
    ]
    return matches[0] if len(matches) == 1 else None


def _projected_source_document(
    *,
    research_task: Mapping[str, Any],
    source_binding: Mapping[str, Any],
) -> dict[str, Any] | None:
    skill_id = source_binding.get("skillId")
    skill_version = source_binding.get("skillDefinitionVersion")
    binding_id = source_binding.get("bindingId")
    binding_version = source_binding.get("bindingVersion")
    if (
        not isinstance(skill_id, str)
        or not skill_id
        or not isinstance(skill_version, int)
        or isinstance(skill_version, bool)
        or not isinstance(binding_id, str)
        or not binding_id
        or not isinstance(binding_version, int)
        or isinstance(binding_version, bool)
    ):
        return None

    work_units = {
        row.get("workUnitId"): row
        for row in research_task.get("workUnits") or []
        if isinstance(row, Mapping) and isinstance(row.get("workUnitId"), str)
    }
    assignments = _assignments_by_id(research_task)
    matches: list[tuple[str, dict[str, Any]]] = []
    for artifact in research_task.get("artifacts") or []:
        if not isinstance(artifact, Mapping):
            continue
        if artifact.get("artifactState") != "usable":
            continue
        work_unit = work_units.get(artifact.get("workUnitId"))
        if not isinstance(work_unit, Mapping) or work_unit.get("status") != "completed":
            continue
        executor = work_unit.get("executorRef")
        if (
            not isinstance(executor, Mapping)
            or executor.get("executorType") != "skill"
            or executor.get("executorId") != skill_id
            or executor.get("definitionVersion") != skill_version
        ):
            continue
        assignment = assignments.get(work_unit.get("assignmentId"))
        if not isinstance(assignment, Mapping):
            continue
        refs = {
            (ref.get("bindingId"), ref.get("bindingVersion"))
            for ref in assignment.get("sourceBindingRefs") or []
            if isinstance(ref, Mapping)
        }
        if (binding_id, binding_version) not in refs:
            continue
        projected = artifact_field_projection_from_validator(
            artifact.get("validatorResult")
        )
        if projected is None:
            continue
        artifact_id = str(artifact.get("artifactId") or "")
        matches.append((artifact_id, projected))
    if len(matches) != 1:
        return None
    return dict(matches[0][1])


def _assignments_by_id(research_task: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    assignments: dict[str, Mapping[str, Any]] = {}
    for decision in research_task.get("executionDecisions") or []:
        if not isinstance(decision, Mapping):
            continue
        for assignment in decision.get("assignments") or []:
            if not isinstance(assignment, Mapping):
                continue
            assignment_id = assignment.get("assignmentId")
            if isinstance(assignment_id, str) and assignment_id:
                assignments[assignment_id] = assignment
    return assignments


__all__ = [
    "BINDING_INPUT_FACT_PREFIX",
    "binding_input_fact_ref",
    "binding_input_target_field",
    "is_binding_input_fact_ref",
    "project_binding_input_runtime_facts",
]
