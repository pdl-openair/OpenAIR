"""Read explicit contributions inside the existing target completion policy."""

from collections.abc import Mapping
from typing import Any


def field(value: Any, snake: str, camel: str) -> Any:
    return (
        value.get(camel, value.get(snake))
        if isinstance(value, Mapping)
        else getattr(value, snake, None)
    )


def responsibility_keys(requirement: Any) -> list[str]:
    keys = field(requirement, "contributing_responsibility_keys", "contributingResponsibilityKeys")
    if keys is not None:
        return list(keys)
    owner = field(requirement, "workflow_responsibility_key", "workflowResponsibilityKey")
    return [owner] if owner is not None else []


def contribution_assessments(
    requirement: Any, design: Any, state: Any
) -> tuple[bool, list[dict[str, Any]]]:
    from app.modules.target_output_completion import non_work_target_assessment

    rows = []
    has_current_work = False
    for key in responsibility_keys(requirement):
        scoped = {
            "completionRequirementId": field(
                requirement, "completion_requirement_id", "completionRequirementId"
            ),
            "targetOutputRef": field(requirement, "target_output_ref", "targetOutputRef"),
            "workflowResponsibilityKey": key,
        }
        assessment = non_work_target_assessment(scoped, design, state)
        if assessment is None:
            has_current_work = True
        else:
            rows.append(assessment)
    return has_current_work, rows


def combine_contribution_status(current: str, assessments: list[dict[str, Any]]) -> str:
    statuses = [current, *(row["status"] for row in assessments)]
    if any(status not in {"satisfied", "unmet"} for status in statuses):
        return "unresolved"
    if all(status == "satisfied" for status in statuses):
        return "satisfied"
    return "unmet"
