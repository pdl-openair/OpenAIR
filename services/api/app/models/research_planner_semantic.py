"""Internal compiler-input contract for ResearchDesign planning.

Provider-facing scientific DTOs are intentionally smaller. This model is the
server-side assembly shape consumed by the ResearchDesign compiler after formal
identities and references have been restored deterministically.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


def _to_camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part.capitalize() for part in tail)


class _SemanticModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=_to_camel,
        extra="forbid",
        populate_by_name=True,
        strict=True,
    )


# This is an internal assembly shape, not a second model-content contract.
# Provider text is normalized before it reaches this model; Formal ResearchDesign
# remains the single authority for executable structure.
SemanticText = str


class PlannerSemanticUnderstandingV2(_SemanticModel):
    user_intent: SemanticText
    material_summary: SemanticText | None
    primary_research_questions: list[SemanticText] = Field(default_factory=list)
    auxiliary_research_dimensions: list[SemanticText] = Field(default_factory=list)
    target_outputs: list[SemanticText] = Field(default_factory=list)
    evidence_boundaries: list[SemanticText] = Field(default_factory=list)
    missing_inputs: list[SemanticText] = Field(default_factory=list)
    risk_notes: list[SemanticText] = Field(default_factory=list)


class PlannerSemanticWorkItemV2(_SemanticModel):
    local_key: SemanticText
    objective: SemanticText
    definition_id: SemanticText
    input_refs: list[SemanticText] = Field(default_factory=list)
    expected_artifact_types: list[SemanticText] = Field(default_factory=list)
    target_output_refs: list[SemanticText] = Field(default_factory=list)
    depends_on_local_keys: list[SemanticText] = Field(default_factory=list)


CompletionMode = Literal["artifact_presence", "professional_judgment"]


class PlannerSemanticTargetRequirementV2(_SemanticModel):
    """A query-specific statement of how one owned target is completed.

    This deliberately captures only the requested kind of completion.  The
    compiler resolves any evaluator from the selected frozen Definition.
    """

    work_item_local_key: SemanticText
    target_output_ref: SemanticText
    completion_mode: CompletionMode


WorkflowResponsibilityCoverageMode = Literal["planned_work", "reused_result", "not_selected"]
WorkflowResponsibilityRequirement = Literal["core", "conditional"]


class PlannerWorkflowResponsibilityCoverageV1(_SemanticModel):
    responsibility_key: SemanticText
    definition_id: SemanticText
    requirement: WorkflowResponsibilityRequirement
    coverage_mode: WorkflowResponsibilityCoverageMode
    work_item_local_keys: list[SemanticText] = Field(default_factory=list)
    reused_result_refs: list[SemanticText] = Field(default_factory=list)
    rationale: SemanticText


class PlannerSemanticProposalV2(_SemanticModel):
    schema_version: Literal["planner-semantic.v2"]
    goal: SemanticText
    understanding: PlannerSemanticUnderstandingV2
    constraints: list[SemanticText] = Field(default_factory=list)
    requested_external_actions: list[SemanticText] = Field(default_factory=list)
    work_items: list[PlannerSemanticWorkItemV2] = Field(min_length=1)
    target_requirements: list[PlannerSemanticTargetRequirementV2] = Field(default_factory=list)
    target_output_owners: dict[str, str] = Field(default_factory=dict)
    target_output_contributors: dict[str, list[str]] = Field(default_factory=dict)
    workflow_responsibility_coverage: list[PlannerWorkflowResponsibilityCoverageV1] = Field(default_factory=list)

__all__ = [
    "PlannerSemanticProposalV2",
    "PlannerSemanticUnderstandingV2",
    "PlannerSemanticWorkItemV2",
    "PlannerSemanticTargetRequirementV2",
    "PlannerWorkflowResponsibilityCoverageV1",
    "WorkflowResponsibilityCoverageMode",
    "WorkflowResponsibilityRequirement",
]
