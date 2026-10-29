"""Research understanding facts with exact historical field-name compatibility."""

from __future__ import annotations

from typing import Annotated

from pydantic import AliasChoices, Field, ValidationInfo, model_validator
from pydantic.json_schema import SkipJsonSchema

from app.models.research_contract_base import ContractModel, NonEmptyStr

# Missing historical semantics remain restricted to explicit read-only loads.
READ_ONLY_LEGACY_RESEARCH_DESIGN = "read_only_legacy_research_design"


class ResearchUnderstanding(ContractModel):
    research_type: NonEmptyStr | None
    user_intent: NonEmptyStr
    material_summary: NonEmptyStr | None
    primary_research_questions: (
        Annotated[list[NonEmptyStr], Field(min_length=1)] | SkipJsonSchema[None]
    )
    auxiliary_research_dimensions: list[NonEmptyStr] | SkipJsonSchema[None]
    target_outputs: list[NonEmptyStr]
    evidence_boundaries: list[NonEmptyStr]
    missing_inputs: list[NonEmptyStr]
    risk_notes: list[NonEmptyStr]
    # Exact historical spelling from pre-95cbb411 persisted designs. The
    # alias changes no facts or execution authority; output uses the current name.
    planning_rationale: NonEmptyStr = Field(
        validation_alias=AliasChoices(
            "planningRationale", "planning_rationale", "routeRationale", "route_rationale"
        )
    )

    @model_validator(mode="before")
    @classmethod
    def project_read_only_legacy_unknowns(cls, value: object, info: ValidationInfo) -> object:
        if not bool((info.context or {}).get(READ_ONLY_LEGACY_RESEARCH_DESIGN)):
            return value
        if not isinstance(value, dict):
            return value
        primary_present = (
            "primaryResearchQuestions" in value or "primary_research_questions" in value
        )
        auxiliary_present = (
            "auxiliaryResearchDimensions" in value or "auxiliary_research_dimensions" in value
        )
        if primary_present != auxiliary_present:
            return value
        if primary_present:
            primary = value.get("primaryResearchQuestions", value.get("primary_research_questions"))
            auxiliary = value.get(
                "auxiliaryResearchDimensions", value.get("auxiliary_research_dimensions")
            )
            if primary is None or auxiliary is None:
                raise ValueError(
                    "explicit null ResearchUnderstanding semantics are not a legacy persisted shape"
                )
            return value
        projected = dict(value)
        projected["primaryResearchQuestions"] = None
        projected["auxiliaryResearchDimensions"] = None
        return projected

    @model_validator(mode="after")
    def validate_read_only_legacy_unknowns(self, info: ValidationInfo) -> ResearchUnderstanding:
        if self.primary_research_questions is None or self.auxiliary_research_dimensions is None:
            if not bool((info.context or {}).get(READ_ONLY_LEGACY_RESEARCH_DESIGN)):
                raise ValueError("legacy ResearchUnderstanding unknowns are read-only")
            if not (
                self.primary_research_questions is None
                and self.auxiliary_research_dimensions is None
            ):
                raise ValueError(
                    "legacy ResearchUnderstanding unknowns must preserve both fields together"
                )
        return self
