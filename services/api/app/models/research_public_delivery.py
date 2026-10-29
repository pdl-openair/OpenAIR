"""Public research delivery fields; private action receipts remain internal."""

from typing import Any, Literal

from pydantic import BaseModel


class PublicCompletionDecision(BaseModel):
    completionDecisionId: str
    scientificStateVersion: int
    goalAchieved: bool | None
    roundEndReason: Literal["goal_achieved", "confirmed_scope_settled"] | None
    disposition: Literal["full", "partial", "degraded", "evidence_gap"]
    unmetRequirementIds: list[str]
    unresolvedRequirementIds: list[str]


class PublicResearchDeliveryFields(BaseModel):
    finalResultAuthority: dict[str, Any] | None = None
    finalResult: dict[str, Any] | None = None
    completionDecision: PublicCompletionDecision | None = None
