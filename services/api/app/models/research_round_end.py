"""Machine receipt shape; scientific achievement keeps its existing meaning."""

from typing import Annotated, Any, Literal

from pydantic import Field, model_validator

from app.models.research_contract_base import ContractModel, NonEmptyStr


class RoundEndInstanceRef(ContractModel):
    substage_instance_id: NonEmptyStr
    instance_revision: Annotated[int, Field(gt=0, strict=True)]


class RoundEndNonExecutionRef(RoundEndInstanceRef):
    reason_code: NonEmptyStr


class RoundEndEvidence(ContractModel):
    reason: Literal["confirmed_scope_settled"]
    instance_refs: Annotated[list[RoundEndInstanceRef], Field(min_length=1)]
    source_decision_refs: Annotated[list[NonEmptyStr], Field(min_length=1)]
    source_artifact_refs: Annotated[list[NonEmptyStr], Field(min_length=1)]

    non_execution_refs: list[RoundEndNonExecutionRef] = Field(
        default_factory=list, exclude_if=lambda value: not value
    )

    @model_validator(mode="after")
    def validate_unique_receipt_refs(self) -> "RoundEndEvidence":
        instances = [
            (row.substage_instance_id, row.instance_revision) for row in self.instance_refs
        ]
        if len(set(instances)) != len(instances):
            raise ValueError("round end instance refs must be unique")
        non_executed = [
            (row.substage_instance_id, row.instance_revision) for row in self.non_execution_refs
        ]
        if len(set(non_executed)) != len(non_executed) or not set(non_executed) <= set(instances):
            raise ValueError(
                "round end non-execution refs must uniquely belong to the confirmed scope"
            )
        for refs in (self.source_decision_refs, self.source_artifact_refs):
            if len(set(refs)) != len(refs):
                raise ValueError("round end source refs must be unique")
        return self


def validate_completion_decision_authority(decision: Any) -> Any:
    """Retain the old target assessment; v2 only adds a round-settlement receipt."""
    version = decision.completion_authority_version
    if version != "project_completion_v2" and decision.round_end is not None:
        raise ValueError("round end requires project_completion_v2")
    if version is None:
        return decision
    if decision.research_design_ref is None or decision.can_complete is None:
        raise ValueError("project completion decision requires Design binding and canComplete")
    if not decision.requirement_assessments:
        raise ValueError("project completion decision requires requirement assessments")
    ids = [row.completion_requirement_id for row in decision.requirement_assessments]
    if len(set(ids)) != len(ids):
        raise ValueError("CompletionRequirementAssessment completionRequirementId must be unique")
    expected_can_complete = all(
        row.status == "satisfied" for row in decision.requirement_assessments
    )
    if decision.can_complete is not expected_can_complete:
        raise ValueError(
            "project completion decision canComplete contradicts requirement assessments"
        )
    if decision.rationale is None:
        raise ValueError("project completion decision requires rationale")
    if version == "project_completion_v2" and (
        decision.round_end is None or decision.can_complete is not False
    ):
        raise ValueError("v2 requires an honest unmet-goal round settlement")
    return decision
