"""Completion of conditional/reused Workflow targets from accepted facts only."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class TargetOutputReuseAcceptance(BaseModel):
    """A scoped scientific applicability judgment carried by an accepted Artifact."""

    model_config = ConfigDict(extra="forbid", strict=True)
    researchDesignId: str = Field(min_length=1)
    researchDesignVersion: int = Field(ge=1)
    targetOutputRef: str = Field(min_length=1)
    responsibilityKey: str = Field(min_length=1)
    reusedResultRefs: list[str] = Field(min_length=1)
    acceptanceArtifactRef: str = Field(min_length=1)
    verdict: str = Field(pattern="^accepted$")


def _get(value: Any, snake: str, camel: str) -> Any:
    return (
        value.get(camel, value.get(snake))
        if isinstance(value, Mapping)
        else getattr(value, snake, None)
    )


def non_work_target_assessment(requirement: Any, design: Any, state: Any) -> dict[str, Any] | None:
    owner = _get(requirement, "workflow_responsibility_key", "workflowResponsibilityKey")
    if owner is None:
        return None
    rows = _get(design, "workflow_responsibility_coverage", "workflowResponsibilityCoverage") or []
    coverage = [
        row for row in rows if _get(row, "responsibility_key", "responsibilityKey") == owner
    ]
    if len(coverage) != 1:
        raise ValueError("target output owner has no unique Workflow responsibility coverage")
    row = coverage[0]
    mode = _get(row, "coverage_mode", "coverageMode")
    if mode == "planned_work":
        return None
    if mode == "not_selected":
        if _get(row, "requirement", "requirement") != "conditional":
            raise ValueError("a core target cannot be marked not applicable")
        return {"status": "satisfied", "fulfillmentStatus": "not_applicable", "uncertainty": []}
    if mode != "reused_result":
        raise ValueError("target output owner has unsupported coverage")
    accepted = _has_current_reuse_acceptance(requirement, design, state, row)
    requirement_id = _get(requirement, "completion_requirement_id", "completionRequirementId")
    return {
        "status": "satisfied" if accepted else "unresolved",
        "fulfillmentStatus": "reuse_accepted" if accepted else "reuse_pending",
        "uncertainty": []
        if accepted
        else [f"completion_requirement:{requirement_id}:reuse_acceptance_missing"],
    }


def _has_current_reuse_acceptance(requirement: Any, design: Any, state: Any, coverage: Any) -> bool:
    facts = _get(state, "scientific_facts", "scientificFacts") or {}
    execution = _get(state, "execution_state", "executionState") or {}
    if not isinstance(facts, Mapping) or not isinstance(execution, Mapping):
        return False
    rows = facts.get("targetOutputReuseAcceptances")
    if not isinstance(rows, list):
        return False
    source_refs = _get(state, "source_refs", "sourceRefs") or []
    validated = execution.get("validatedArtifactRefs") or []
    approved = execution.get("approvedArtifactRefs") or []
    expected_refs = _get(coverage, "reused_result_refs", "reusedResultRefs")
    for raw in rows:
        acceptance = _parse_reuse_acceptance(raw)
        if acceptance is None:
            continue
        if (
            acceptance.researchDesignId == _get(design, "research_design_id", "researchDesignId")
            and acceptance.researchDesignVersion == _get(design, "version", "version")
            and acceptance.targetOutputRef
            == _get(requirement, "target_output_ref", "targetOutputRef")
            and acceptance.responsibilityKey
            == _get(coverage, "responsibility_key", "responsibilityKey")
            and len(set(acceptance.reusedResultRefs)) == len(acceptance.reusedResultRefs)
            and set(acceptance.reusedResultRefs) == set(expected_refs or [])
            and acceptance.acceptanceArtifactRef in source_refs
            and acceptance.acceptanceArtifactRef in validated
            and acceptance.acceptanceArtifactRef in approved
        ):
            return True
    return False


def validate_reuse_acceptance_projection(
    facts: Mapping[str, Any],
    payload: Mapping[str, Any],
    source_artifact_id: str,
    design_version: int,
) -> None:
    rows = facts.get("targetOutputReuseAcceptances")
    if rows is None:
        return
    if not isinstance(rows, list):
        raise ValueError("target output reuse acceptance projection must be a list")
    designs = [
        row for row in payload.get("researchDesignVersions", []) if row["version"] == design_version
    ]
    if len(designs) != 1:
        raise ValueError("reuse acceptance requires the current ResearchDesign")
    design = designs[0]
    requirements = {row["targetOutputRef"]: row for row in design["projectCompletionRequirements"]}
    coverage = {
        row["responsibilityKey"]: row for row in design.get("workflowResponsibilityCoverage", [])
    }
    for raw in rows:
        acceptance = TargetOutputReuseAcceptance.model_validate(raw)
        requirement = requirements.get(acceptance.targetOutputRef)
        owner = coverage.get(acceptance.responsibilityKey)
        if (
            acceptance.researchDesignId != design["researchDesignId"]
            or acceptance.researchDesignVersion != design_version
            or acceptance.acceptanceArtifactRef != source_artifact_id
            or requirement is None
            or acceptance.responsibilityKey not in _requirement_responsibilities(requirement)
            or owner is None
            or owner["coverageMode"] != "reused_result"
            or set(owner["reusedResultRefs"]) != set(acceptance.reusedResultRefs)
            or len(acceptance.reusedResultRefs) != len(set(acceptance.reusedResultRefs))
        ):
            raise ValueError("reuse acceptance conflicts with the current target, source, or owner")


def _parse_reuse_acceptance(raw: Any) -> TargetOutputReuseAcceptance | None:
    try:
        return TargetOutputReuseAcceptance.model_validate(raw)
    except ValidationError:
        return None


def _requirement_responsibilities(requirement: Any) -> list[str]:
    from app.modules.target_output_contributions import responsibility_keys

    return responsibility_keys(requirement)


def approved_artifact_refs(task: Any, validated_refs: list[str]) -> list[str]:
    """Acceptance producers use outputs for direct acceptance, inputs for review."""
    validated = set(validated_refs)
    return sorted(
        {
            ref
            for row in task.agent_interaction_records
            if row.interaction_type in {"acceptance_recorded", "target_output_reuse_accepted"}
            and row.verdict is not None
            and row.verdict.decision.value == "approved"
            for ref in [*row.input_artifact_refs, *row.output_artifact_refs]
            if ref in validated
        }
    )


def merge_reuse_acceptances(previous: Any, incoming: Any) -> list[dict[str, Any]]:
    """An unrelated later judgment cannot erase already accepted target evidence."""
    rows = list(previous) if isinstance(previous, list) else []
    for row in incoming:
        if row not in rows:
            rows.append(row)
    return rows
