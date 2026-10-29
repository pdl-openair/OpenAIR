"""Validate persisted Workflow lineage for a confirmed ResearchDesign."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from pydantic import ValidationError

from app.models.research_orchestration import (
    PlanningAttemptStatus,
    ResearchDesignStatus,
    ResearchTaskV12,
)
from app.models.workflow_definition import parse_frozen_workflow_definition


class CanonicalExecutionLineageError(ValueError):
    """The persisted task cannot prove canonical Workflow lineage."""

    code = "canonical_workflow_lineage_mismatch"


@dataclass(frozen=True)
class CanonicalExecutionLineage:
    workflow_id: str
    workflow_version: int
    workflow_content_hash: str
    skill_pack: str
    research_design_id: str
    research_design_version: int
    planning_attempt_id: str

    def as_lineage_facts(self) -> dict[str, Any]:
        return {
            "workflowId": self.workflow_id,
            "workflowVersion": self.workflow_version,
            "workflowContentHash": self.workflow_content_hash,
            "skillPack": self.skill_pack,
            "researchDesignId": self.research_design_id,
            "researchDesignVersion": self.research_design_version,
            "planningAttemptId": self.planning_attempt_id,
        }


def _fail() -> CanonicalExecutionLineageError:
    return CanonicalExecutionLineageError(CanonicalExecutionLineageError.code)


def validate_canonical_execution_lineage(
    *,
    task: ResearchTaskV12,
    workflow_admission: Mapping[str, Any],
    persisted_skill_pack: object,
) -> CanonicalExecutionLineage:
    """Validate pre-plan Workflow records against confirmed Design provenance.

    The confirmed ResearchDesign owns the Workflow identity used by execution.
    The pre-plan admission snapshot remains an engineering record used only to
    verify provenance and recover the frozen Workflow definition.
    """

    snapshot_payload = workflow_admission.get("workflowDefinitionSnapshot")
    if not isinstance(snapshot_payload, Mapping):
        raise _fail()
    try:
        frozen = parse_frozen_workflow_definition(snapshot_payload)
    except ValidationError as exc:
        raise _fail() from exc

    definition = frozen.definition
    if (
        workflow_admission.get("workflowId") != frozen.workflow_id
        or workflow_admission.get("workflowVersion") != frozen.workflow_version
        or workflow_admission.get("workflowContentHash") != frozen.content_hash
        or workflow_admission.get("skillPack") != definition.skill_pack
        or persisted_skill_pack != definition.skill_pack
        or frozen.registry_snapshot_ref.model_dump(mode="json", by_alias=True)
        != task.registry_snapshot_ref.model_dump(mode="json", by_alias=True)
    ):
        raise _fail()

    active_ref = task.active_research_design_ref
    if active_ref is None:
        raise _fail()
    active_design = next(
        (
            row
            for row in task.research_design_versions
            if row.research_design_id == active_ref.research_design_id
            and row.version == active_ref.version
        ),
        None,
    )
    if (
        active_design is None
        or active_design.status != ResearchDesignStatus.CONFIRMED
        or active_design.coverage_report is None
        or active_design.coverage_report.design_ref.research_design_id
        != active_ref.research_design_id
        or active_design.coverage_report.design_ref.version != active_ref.version
    ):
        raise _fail()

    provenance = active_design.workflow_provenance_ref
    provenance_hash = (
        provenance.content_hash.removeprefix("sha256:")
        if provenance is not None
        else None
    )
    if (
        provenance is None
        or provenance.workflow_id != frozen.workflow_id
        or provenance.workflow_version != frozen.workflow_version
        or provenance_hash != frozen.content_hash
    ):
        raise _fail()

    planning_attempt_id = active_design.coverage_report.planning_attempt_id
    planning_attempt = next(
        (
            row
            for row in task.planning_attempts
            if row.planning_attempt_id == planning_attempt_id
        ),
        None,
    )
    if (
        planning_attempt is None
        or planning_attempt.task_id != task.task_id
        or planning_attempt.status != PlanningAttemptStatus.COMPLETED
    ):
        raise _fail()

    workflow_nodes = {
        (row.definition_id, row.definition_version): row
        for row in definition.nodes
    }
    design_definition_refs = {
        (row.substage_definition_id, row.substage_definition_version)
        for row in active_design.instances
    }
    required_definition_refs = {
        identity
        for identity, row in workflow_nodes.items()
        if str(row.requirement) == "required"
    }
    if (
        not required_definition_refs.issubset(design_definition_refs)
    ):
        raise _fail()

    return CanonicalExecutionLineage(
        workflow_id=provenance.workflow_id,
        workflow_version=provenance.workflow_version,
        workflow_content_hash=provenance_hash,
        skill_pack=definition.skill_pack,
        research_design_id=active_ref.research_design_id,
        research_design_version=active_ref.version,
        planning_attempt_id=planning_attempt.planning_attempt_id,
    )


__all__ = [
    "CanonicalExecutionLineage",
    "CanonicalExecutionLineageError",
    "validate_canonical_execution_lineage",
]
