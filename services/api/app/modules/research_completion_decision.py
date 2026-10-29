"""Single authority for deciding whether a research project can complete.

The decision is derived only from a confirmed ResearchDesign and the latest
ScientificState. Runtime state contributes execution facts to ScientificState;
it is never project-completion authority by itself.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from app.models.research_orchestration import (
    CompletionDecision,
    ResearchDesignStatus,
    ResearchDesignVersion,
    ResearchTaskV12,
    ScientificStateV1,
)
from app.modules.research_round_end import derive_round_end

COMPLETION_AUTHORITY_VERSION = "project_completion_v1"
ROUND_COMPLETION_AUTHORITY_VERSION = "project_completion_v2"


class ProjectCompletionAuthorityError(ValueError):
    """Persisted facts cannot form one valid project-completion decision."""


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _list(value: object) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _field(value: object, attr: str, alias: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(alias, value.get(attr))
    return getattr(value, attr, None)


def _enum_value(value: object) -> object:
    return value.value if isinstance(value, Enum) else value


def _unique_strings(values: object) -> list[str]:
    result: list[str] = []
    for value in _list(values):
        if isinstance(value, str) and value and value not in result:
            result.append(value)
    return result


def scientific_state_artifact_refs(state: object) -> list[str]:
    execution_state = _mapping(_field(state, "execution_state", "executionState"))
    return _unique_strings(execution_state.get("validatedArtifactRefs"))


def scientific_state_completion_rows(state: object) -> list[Mapping[str, Any]]:
    execution_state = _mapping(_field(state, "execution_state", "executionState"))
    return [
        row
        for value in _list(execution_state.get("substageCompletion"))
        if (row := _mapping(value))
    ]


def completion_row_coverage(
    row: Mapping[str, Any],
) -> tuple[bool, bool, list[str], list[str]]:
    """Recompute criterion coverage and detect contradictory summary fields."""

    criteria = [
        criterion
        for value in _list(row.get("criteria"))
        if (criterion := _mapping(value))
    ]
    unresolved: list[str] = []
    unmet: list[str] = []
    all_satisfied = bool(criteria)
    for criterion in criteria:
        criterion_id = criterion.get("criterionId")
        status = criterion.get("status")
        if status == "satisfied":
            continue
        all_satisfied = False
        if not isinstance(criterion_id, str) or not criterion_id:
            continue
        if status == "unmet":
            unmet.append(criterion_id)
        else:
            unresolved.append(criterion_id)
    consistent = (
        row.get("completionCriteriaSatisfied") is all_satisfied
        and _unique_strings(row.get("unresolvedCriterionIds")) == unresolved
        and _unique_strings(row.get("unmetCriterionIds")) == unmet
    )
    return all_satisfied, consistent, unresolved, unmet


def expected_completion_disposition(state: object) -> str:
    """Describe current durable completion quality without deciding project end."""

    artifact_refs = scientific_state_artifact_refs(state)
    rows = scientific_state_completion_rows(state)
    all_required_satisfied = bool(rows) and all(
        completion_row_coverage(row)[0] for row in rows
    )
    all_runtime_completed = bool(rows) and all(
        row.get("runtimeStatus") == "completed" for row in rows
    )
    has_gap = any(_unique_strings(row.get("gaps")) for row in rows)
    has_uncertainty = any(_unique_strings(row.get("uncertainty")) for row in rows)
    has_degradation = any(row.get("hasExecutionDegradation") is True for row in rows)

    if (
        artifact_refs
        and all_required_satisfied
        and all_runtime_completed
        and not has_gap
        and not has_uncertainty
        and not has_degradation
    ):
        return "full"
    if not artifact_refs:
        return "evidence_gap"
    if has_degradation:
        return "degraded"
    return "partial"


def _criterion_status_index(
    state: object,
) -> tuple[dict[str, str], dict[str, Mapping[str, Any]], set[str]]:
    statuses: dict[str, str] = {}
    rows_by_criterion: dict[str, Mapping[str, Any]] = {}
    conflicts: set[str] = set()
    for row in scientific_state_completion_rows(state):
        for value in _list(row.get("criteria")):
            criterion = _mapping(value)
            criterion_id = criterion.get("criterionId")
            status = criterion.get("status")
            if not isinstance(criterion_id, str) or not criterion_id:
                continue
            if not isinstance(status, str) or not status:
                status = "unresolved"
            previous = statuses.get(criterion_id)
            if previous is not None and previous != status:
                conflicts.add(criterion_id)
            statuses[criterion_id] = status
            rows_by_criterion[criterion_id] = row
    return statuses, rows_by_criterion, conflicts


def _project_completion_requirements(design: object) -> list[object]:
    return _list(_field(design, "project_completion_requirements", "projectCompletionRequirements"))


def _coverage_targets(design: object) -> list[object]:
    report = _field(design, "coverage_report", "coverageReport")
    return _list(_field(report, "targets", "targets"))


def _coverage_claims(design: object) -> list[object]:
    report = _field(design, "coverage_report", "coverageReport")
    return _list(_field(report, "claims", "claims"))


def _coverage_mapping(
    requirement_id: object,
    target_output_ref: object,
    *,
    targets: list[object],
    claims: list[object],
) -> tuple[str | None, list[str], list[str]]:
    matching_targets = [
        target
        for target in targets
        if _enum_value(_field(target, "source_type", "sourceType")) == "target_output"
        and _field(target, "source_path", "sourcePath") == target_output_ref
    ]
    if len(matching_targets) != 1:
        return None, [], [
            f"completion_requirement:{requirement_id}:coverage_target_unresolved"
        ]

    target_id = _field(matching_targets[0], "target_id", "targetId")
    coverage_target_id = target_id if isinstance(target_id, str) and target_id else None
    full_claims = [
        claim
        for claim in claims
        if _field(claim, "target_id", "targetId") == target_id
        and _enum_value(_field(claim, "coverage_mode", "coverageMode")) == "full"
    ]
    if len(full_claims) != 1:
        return coverage_target_id, [], [
            f"completion_requirement:{requirement_id}:coverage_claim_unresolved"
        ]

    criterion_refs = _unique_strings(
        _field(full_claims[0], "acceptance_criterion_refs", "acceptanceCriterionRefs")
    )
    if not criterion_refs:
        return coverage_target_id, [], [
            f"completion_requirement:{requirement_id}:criterion_mapping_missing"
        ]
    return coverage_target_id, criterion_refs, []


def _criterion_completion_status(
    requirement_id: object,
    criterion_refs: list[str],
    *,
    statuses_by_criterion: Mapping[str, str],
    conflicts: set[str],
) -> tuple[str, list[str]]:
    if not criterion_refs:
        return "unresolved", []
    missing = [ref for ref in criterion_refs if ref not in statuses_by_criterion]
    conflicting = [ref for ref in criterion_refs if ref in conflicts]
    uncertainty = [
        f"completion_requirement:{requirement_id}:criterion_status_missing:{ref}"
        for ref in missing
    ] + [
        f"completion_requirement:{requirement_id}:criterion_status_conflict:{ref}"
        for ref in conflicting
    ]
    statuses = [statuses_by_criterion.get(ref, "unresolved") for ref in criterion_refs]
    if missing or conflicting or any(value not in {"satisfied", "unmet"} for value in statuses):
        return "unresolved", uncertainty
    if any(value == "unmet" for value in statuses):
        return "unmet", uncertainty
    return "satisfied", uncertainty


def _extend_unique(target: list[str], values: object) -> None:
    for value in _unique_strings(values):
        if value not in target:
            target.append(value)


def _criterion_row_diagnostics(
    criterion_refs: list[str],
    rows_by_criterion: Mapping[str, Mapping[str, Any]],
) -> tuple[list[str], list[str]]:
    gaps: list[str] = []
    uncertainty: list[str] = []
    for ref in criterion_refs:
        row = rows_by_criterion.get(ref)
        if row is None:
            continue
        _extend_unique(gaps, row.get("gaps"))
        _extend_unique(uncertainty, row.get("uncertainty"))
    return gaps, uncertainty


def _assessment_for_requirement(
    requirement: object,
    *,
    targets: list[object],
    claims: list[object],
    criterion_statuses: Mapping[str, str],
    rows_by_criterion: Mapping[str, Mapping[str, Any]],
    criterion_conflicts: set[str],
    design: object,
    state: object,
) -> dict[str, Any]:
    requirement_id = _field(requirement, "completion_requirement_id", "completionRequirementId")
    target_output_ref = _field(requirement, "target_output_ref", "targetOutputRef")
    target_id, criterion_refs, mapping_uncertainty = _coverage_mapping(
        requirement_id, target_output_ref, targets=targets, claims=claims
    )
    from app.modules.target_output_completion import non_work_target_assessment
    from app.modules.target_output_contributions import (
        combine_contribution_status,
        contribution_assessments,
    )

    combined = _field(
        requirement, "contributing_responsibility_keys", "contributingResponsibilityKeys"
    )
    extra_assessments: list[dict[str, Any]] = []
    if combined is not None:
        has_current_work, extra_assessments = contribution_assessments(requirement, design, state)
        non_work = None if has_current_work else {
            "status": combine_contribution_status("satisfied", extra_assessments),
            "uncertainty": [value for row in extra_assessments for value in row["uncertainty"]],
        }
    else:
        non_work = non_work_target_assessment(requirement, design, state)
    if non_work is not None:
        if target_id is None:
            raise ProjectCompletionAuthorityError("workflow_target_coverage_missing")
        return {
            "completionRequirementId": requirement_id,
            "targetOutputRef": target_output_ref,
            "coverageTargetId": target_id,
            "acceptanceCriterionRefs": [],
            "gaps": [],
            **non_work,
        }
    status, status_uncertainty = _criterion_completion_status(
        requirement_id,
        criterion_refs,
        statuses_by_criterion=criterion_statuses,
        conflicts=criterion_conflicts,
    )
    gaps, row_uncertainty = _criterion_row_diagnostics(criterion_refs, rows_by_criterion)
    uncertainty = [*mapping_uncertainty, *status_uncertainty]
    _extend_unique(uncertainty, row_uncertainty)
    if combined is not None:
        status = combine_contribution_status(status, extra_assessments)
        for assessment in extra_assessments:
            _extend_unique(uncertainty, assessment["uncertainty"])

    if status == "unmet":
        gaps.append(f"completion_requirement:{requirement_id}:unmet")
    elif status == "unresolved":
        marker = f"completion_requirement:{requirement_id}:unresolved"
        if marker not in uncertainty:
            uncertainty.append(marker)

    return {
        "completionRequirementId": requirement_id,
        "targetOutputRef": target_output_ref,
        "status": status,
        "coverageTargetId": target_id,
        "acceptanceCriterionRefs": criterion_refs,
        "gaps": gaps,
        "uncertainty": uncertainty,
    }


def _completion_diagnostics(
    assessments: list[dict[str, Any]], state: object
) -> tuple[list[str], list[str], list[str]]:
    gaps: list[str] = []
    uncertainty: list[str] = []
    limitations: list[str] = []
    for assessment in assessments:
        _extend_unique(gaps, assessment["gaps"])
        _extend_unique(uncertainty, assessment["uncertainty"])
    for row in scientific_state_completion_rows(state):
        _extend_unique(gaps, row.get("gaps"))
        _extend_unique(uncertainty, row.get("uncertainty"))
        if row.get("hasExecutionDegradation") is True:
            marker = f"execution_degradation:{row.get('substageInstanceId') or 'unknown'}"
            if marker not in limitations:
                limitations.append(marker)
    return gaps, uncertainty, limitations


def _completion_rationale(can_complete: bool, assessments: list[dict[str, Any]]) -> str:
    if can_complete:
        return "All confirmed project completion requirements are satisfied by the current ScientificState."
    unmet = [
        row["completionRequirementId"] for row in assessments if row["status"] == "unmet"
    ]
    unresolved = [
        row["completionRequirementId"]
        for row in assessments
        if row["status"] == "unresolved"
    ]
    return (
        "Project cannot complete because confirmed project completion requirements remain "
        f"unmet={unmet} unresolved={unresolved}."
    )


def expected_project_completion_evaluation(
    design: object,
    state: object,
) -> dict[str, Any]:
    """Return the deterministic project-completion judgment for one Design + State."""

    if _field(design, "version", "version") != _field(
        state, "research_design_version", "researchDesignVersion"
    ):
        raise ProjectCompletionAuthorityError("completion_state_design_mismatch")
    requirements = _project_completion_requirements(design)
    if not requirements:
        raise ProjectCompletionAuthorityError("project_completion_requirements_missing")
    if _field(design, "coverage_report", "coverageReport") is None:
        raise ProjectCompletionAuthorityError("research_design_coverage_report_missing")

    criterion_statuses, rows_by_criterion, conflicts = _criterion_status_index(state)
    targets = _coverage_targets(design)
    claims = _coverage_claims(design)
    assessments = [
        _assessment_for_requirement(
            requirement,
            targets=targets,
            claims=claims,
            criterion_statuses=criterion_statuses,
            rows_by_criterion=rows_by_criterion,
            criterion_conflicts=conflicts,
            design=design,
            state=state,
        )
        for requirement in requirements
    ]
    can_complete = all(row["status"] == "satisfied" for row in assessments)
    gaps, uncertainty, limitations = _completion_diagnostics(assessments, state)
    return {
        "canComplete": can_complete,
        "requirementAssessments": assessments,
        "disposition": expected_completion_disposition(state),
        "gaps": gaps,
        "uncertainty": uncertainty,
        "limitations": limitations,
        "rationale": _completion_rationale(can_complete, assessments),
        "sourceRefs": scientific_state_artifact_refs(state),
    }


def completion_decision_id(design: object, state: object, *, round_end: bool = False) -> str:
    research_design_id = _field(design, "research_design_id", "researchDesignId")
    design_version = _field(design, "version", "version")
    state_version = _field(state, "scientific_state_version", "scientificStateVersion")
    version = "v2" if round_end else "v1"
    return f"completion:{version}:{research_design_id}:{design_version}:{state_version}"


def _latest_state_version(task: ResearchTaskV12, design: ResearchDesignVersion) -> int | None:
    return max(
        (
            row.scientific_state_version
            for row in task.scientific_states
            if row.research_design_version == design.version
        ),
        default=None,
    )


def _require_current_scope(
    task: ResearchTaskV12,
    design: ResearchDesignVersion,
    state: ScientificStateV1 | Mapping[str, Any],
) -> int:
    if design.status is not ResearchDesignStatus.CONFIRMED:
        raise ProjectCompletionAuthorityError("active_design_not_confirmed")
    active_ref = task.active_research_design_ref
    if (
        active_ref is None
        or active_ref.research_design_id != design.research_design_id
        or active_ref.version != design.version
    ):
        raise ProjectCompletionAuthorityError("completion_decision_requires_active_design")
    state_version = int(_field(state, "scientific_state_version", "scientificStateVersion"))
    if _latest_state_version(task, design) != state_version:
        raise ProjectCompletionAuthorityError("completion_decision_requires_latest_scientific_state")
    return state_version


def record_project_completion_decision(
    task: ResearchTaskV12,
    design: ResearchDesignVersion,
    state: ScientificStateV1 | Mapping[str, Any],
    *,
    occurred_at: datetime,
) -> tuple[ResearchTaskV12, CompletionDecision]:
    """Persist/reuse CompletionDecision only after current Design + State can end."""

    state_version = _require_current_scope(task, design, state)
    evaluation = expected_project_completion_evaluation(design, state)
    round_end = None if evaluation["canComplete"] is True else derive_round_end(task, design, state)
    if evaluation["canComplete"] is not True and round_end is None:
        raise ProjectCompletionAuthorityError("completion_decision_requires_project_end")
    decision_id = completion_decision_id(design, state, round_end=round_end is not None)
    payload = task.model_dump(mode="json", by_alias=True)
    decisions = payload.setdefault("completionDecisions", [])
    expected = {
        "completionDecisionId": decision_id,
        "completionAuthorityVersion": (
            ROUND_COMPLETION_AUTHORITY_VERSION
            if round_end is not None
            else COMPLETION_AUTHORITY_VERSION
        ),
        "researchDesignRef": {
            "researchDesignId": design.research_design_id,
            "version": design.version,
        },
        "scientificStateVersion": state_version,
        **evaluation,
        "decidedAt": occurred_at.astimezone(timezone.utc).isoformat(),
    }
    if round_end is not None:
        expected["roundEnd"] = round_end
    existing = next(
        (row for row in decisions if row.get("completionDecisionId") == decision_id),
        None,
    )
    if existing is None:
        decisions.append(expected)
    else:
        for key, value in expected.items():
            if key == "decidedAt":
                continue
            if existing.get(key) != value:
                raise ProjectCompletionAuthorityError("completion_decision_conflict")
    updated = ResearchTaskV12.model_validate(payload)
    decision = next(
        row for row in updated.completion_decisions
        if row.completion_decision_id == decision_id
    )
    return updated, decision


def current_project_completion_decision(
    task: ResearchTaskV12,
    design: ResearchDesignVersion,
    state: ScientificStateV1 | Mapping[str, Any],
) -> CompletionDecision | None:
    state_version = int(_field(state, "scientific_state_version", "scientificStateVersion"))
    if _latest_state_version(task, design) != state_version:
        return None
    ids = {
        completion_decision_id(design, state),
        completion_decision_id(design, state, round_end=True),
    }
    matches = [
        row
        for row in task.completion_decisions
        if row.completion_decision_id in ids
        and row.completion_authority_version
        in {COMPLETION_AUTHORITY_VERSION, ROUND_COMPLETION_AUTHORITY_VERSION}
    ]
    if len(matches) > 1:
        raise ProjectCompletionAuthorityError("current_completion_decision_ambiguous")
    return matches[0] if matches else None


__all__ = [
    "COMPLETION_AUTHORITY_VERSION",
    "ProjectCompletionAuthorityError",
    "completion_decision_id",
    "completion_row_coverage",
    "current_project_completion_decision",
    "expected_completion_disposition",
    "expected_project_completion_evaluation",
    "record_project_completion_decision",
    "scientific_state_artifact_refs",
    "scientific_state_completion_rows",
]
