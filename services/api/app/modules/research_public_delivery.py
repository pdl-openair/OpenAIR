"""Read-only current Final Result and separate original-goal judgment."""

from collections.abc import Mapping
from typing import Any


def public_final_delivery(canonical: Any, authority: Mapping[str, Any] | None) -> dict[str, Any]:
    result = next(
        (
            row
            for row in canonical.final_results
            if authority is not None
            and authority.get("status") == "current"
            and row.final_result_id == canonical.final_result_authority.final_result_id
        ),
        None,
    )
    decision = next(
        (
            row
            for row in canonical.completion_decisions
            if result is not None and row.completion_decision_id == result.completion_decision_id
        ),
        None,
    )
    summary = None
    if decision is not None:
        reason = (
            decision.round_end.reason
            if decision.round_end is not None
            else "goal_achieved"
            if decision.can_complete is True
            else None
        )
        summary = {
            "completionDecisionId": decision.completion_decision_id,
            "scientificStateVersion": decision.scientific_state_version,
            "goalAchieved": decision.can_complete,
            "roundEndReason": reason,
            "disposition": decision.disposition.value,
            "unmetRequirementIds": [
                row.completion_requirement_id
                for row in decision.requirement_assessments
                if row.status == "unmet"
            ],
            "unresolvedRequirementIds": [
                row.completion_requirement_id
                for row in decision.requirement_assessments
                if row.status == "unresolved"
            ],
        }
    return {
        "finalResult": None if result is None else result.model_dump(mode="json", by_alias=True),
        "completionDecision": summary,
    }
