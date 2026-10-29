"""Deterministic lifecycle decisions for canonical Research Tasks.

Lifecycle facts describe what happened. This module alone translates the
Research Start, ResearchDesign, and Artifact facts it owns into Task state.
Persistence belongs to ``research_task_lifecycle_repository``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class ResearchStartLifecycleFact(StrEnum):
    """Durable Research Start facts that may affect a reserved Task."""

    PLANNING_FAILED = "planning_failed"
    EXTERNAL_RESULT_UNKNOWN = "external_result_unknown"
    RETRY_AUTHORIZED_AFTER_NOT_SUBMITTED = "retry_authorized_after_not_submitted"
    PLAN_REGENERATION_AUTHORIZED = "plan_regeneration_authorized"
    PLAN_CONFIRMED = "plan_confirmed"


class ResearchDesignLifecycleFact(StrEnum):
    """Durable ResearchDesign facts that may affect canonical Task state."""

    DESIGN_APPROVED = "design_approved"
    SEMANTIC_MATERIAL_GAP_RECOVERY_DRAFTED = "semantic_material_gap_recovery_drafted"


class ArtifactLifecycleFact(StrEnum):
    """Durable Artifact facts that may affect canonical Task state."""

    ALL_RESEARCH_OUTPUTS_NO_LONGER_USABLE = "all_research_outputs_no_longer_usable"


class RecoveryLifecycleFact(StrEnum):
    """Durable Recovery facts that may affect canonical Task state."""

    SUCCESSOR_EXECUTION_CONFIRMED = "successor_execution_confirmed"


class UserTaskLifecycleFact(StrEnum):
    """Explicit user commands that may affect canonical Task state."""

    DRAFT_CANCELLED = "draft_cancelled"


ResearchTaskLifecycleFact = (
    ResearchStartLifecycleFact
    | ResearchDesignLifecycleFact
    | ArtifactLifecycleFact
    | RecoveryLifecycleFact
    | UserTaskLifecycleFact
)


@dataclass(frozen=True)
class TaskLifecycleTransition:
    """One canonical state conclusion and its read-model explanation."""

    task_status: str
    progress_status: str
    expected_database_status: str | None = None


def reconcile_task_lifecycle(
    task: Mapping[str, Any],
    *,
    fact: ResearchTaskLifecycleFact,
) -> TaskLifecycleTransition | None:
    """Derive one canonical Task conclusion from one durable lifecycle fact.

    Returning ``None`` means that the fact cannot legitimately alter the
    current Task. The mechanism owns the status conclusion; callers only
    report the durable fact they have recorded.
    """

    if isinstance(fact, ResearchStartLifecycleFact):
        return _reconcile_research_start_task_lifecycle(task, fact=fact)
    if isinstance(fact, ResearchDesignLifecycleFact):
        return _reconcile_research_design_task_lifecycle(task, fact=fact)
    if isinstance(fact, ArtifactLifecycleFact):
        return _reconcile_artifact_task_lifecycle(task, fact=fact)
    if isinstance(fact, RecoveryLifecycleFact):
        return _reconcile_recovery_task_lifecycle(task, fact=fact)
    if isinstance(fact, UserTaskLifecycleFact):
        return _reconcile_user_task_lifecycle(task, fact=fact)
    raise ValueError(f"unsupported Task lifecycle fact: {fact}")


def reconcile_research_start_task_lifecycle(
    task: Mapping[str, Any],
    *,
    fact: ResearchStartLifecycleFact,
) -> TaskLifecycleTransition | None:
    """Compatibility facade for Research Start's legacy reducer name."""

    return reconcile_task_lifecycle(task, fact=fact)


def _reconcile_research_start_task_lifecycle(
    task: Mapping[str, Any],
    *,
    fact: ResearchStartLifecycleFact,
) -> TaskLifecycleTransition | None:
    """Derive a Task conclusion from one durable pre-Design fact."""

    task_status = str(task.get("status") or "")
    if fact is ResearchStartLifecycleFact.PLANNING_FAILED:
        return _transition_from_draft(task_status, "failed", "failed")
    if fact is ResearchStartLifecycleFact.EXTERNAL_RESULT_UNKNOWN:
        return _transition_from_draft(
            task_status,
            "awaiting_confirmation",
            "external_result_unknown",
        )
    if fact is ResearchStartLifecycleFact.RETRY_AUTHORIZED_AFTER_NOT_SUBMITTED:
        if task_status != "awaiting_confirmation":
            return None
        if _progress_status(task) != "external_result_unknown":
            return None
        return TaskLifecycleTransition(
            task_status="draft",
            progress_status="draft",
            expected_database_status="awaiting_confirmation",
        )
    if fact is ResearchStartLifecycleFact.PLAN_REGENERATION_AUTHORIZED:
        if task_status not in {"failed", "draft"}:
            return None
        return TaskLifecycleTransition(
            task_status="draft",
            progress_status="draft",
            expected_database_status=task_status,
        )
    if fact is ResearchStartLifecycleFact.PLAN_CONFIRMED:
        if (
            task_status != "draft"
            or str(task.get("creationStatus") or "") != "awaiting_confirmation"
        ):
            return None
        return TaskLifecycleTransition(
            task_status="not_started",
            progress_status="not_started",
            expected_database_status="draft",
        )
    raise ValueError(f"unsupported Research Start lifecycle fact: {fact}")


def _reconcile_research_design_task_lifecycle(
    task: Mapping[str, Any],
    *,
    fact: ResearchDesignLifecycleFact,
) -> TaskLifecycleTransition:
    if fact is ResearchDesignLifecycleFact.DESIGN_APPROVED:
        status = "running" if _has_execution_history(task) else "not_started"
        return TaskLifecycleTransition(task_status=status, progress_status=status)
    if fact is ResearchDesignLifecycleFact.SEMANTIC_MATERIAL_GAP_RECOVERY_DRAFTED:
        return TaskLifecycleTransition(
            task_status="awaiting_confirmation",
            progress_status="awaiting_confirmation",
        )
    raise ValueError(f"unsupported ResearchDesign lifecycle fact: {fact}")


def _reconcile_artifact_task_lifecycle(
    task: Mapping[str, Any],
    *,
    fact: ArtifactLifecycleFact,
) -> TaskLifecycleTransition | None:
    """Keep completed work honest when its final usable output is withdrawn."""

    if fact is ArtifactLifecycleFact.ALL_RESEARCH_OUTPUTS_NO_LONGER_USABLE:
        if str(task.get("status") or "") != "completed":
            return None
        return TaskLifecycleTransition(
            task_status="awaiting_confirmation",
            progress_status="awaiting_confirmation",
            expected_database_status="completed",
        )
    raise ValueError(f"unsupported Artifact lifecycle fact: {fact}")


def _reconcile_recovery_task_lifecycle(
    task: Mapping[str, Any],
    *,
    fact: RecoveryLifecycleFact,
) -> TaskLifecycleTransition | None:
    """Resume only a recoverable Task after its successor is durably created."""

    if fact is RecoveryLifecycleFact.SUCCESSOR_EXECUTION_CONFIRMED:
        current_status = str(task.get("status") or "")
        if current_status not in {"failed", "running", "awaiting_confirmation"}:
            return None
        return TaskLifecycleTransition(
            task_status="running",
            progress_status="running",
            expected_database_status=current_status,
        )
    raise ValueError(f"unsupported Recovery lifecycle fact: {fact}")


def _reconcile_user_task_lifecycle(
    task: Mapping[str, Any],
    *,
    fact: UserTaskLifecycleFact,
) -> TaskLifecycleTransition | None:
    if fact is UserTaskLifecycleFact.DRAFT_CANCELLED:
        current_status = str(task.get("status") or "")
        if current_status not in {"draft", "cancelled"}:
            return None
        return TaskLifecycleTransition(
            task_status="cancelled",
            progress_status="cancelled",
            expected_database_status=current_status,
        )
    raise ValueError(f"unsupported user Task lifecycle fact: {fact}")


def _transition_from_draft(
    task_status: str,
    next_status: str,
    progress_status: str,
) -> TaskLifecycleTransition | None:
    if task_status != "draft":
        return None
    return TaskLifecycleTransition(
        task_status=next_status,
        progress_status=progress_status,
        expected_database_status="draft",
    )


def _progress_status(task: Mapping[str, Any]) -> str:
    projection = task.get("progressProjection")
    if not isinstance(projection, Mapping):
        return ""
    task_progress = projection.get("taskProgress")
    if not isinstance(task_progress, Mapping):
        return ""
    return str(task_progress.get("status") or "")


def _has_execution_history(task: Mapping[str, Any]) -> bool:
    return bool(
        task.get("startedAt")
        or task.get("started_at")
        or task.get("executionDecisions")
        or task.get("execution_decisions")
        or task.get("workUnits")
        or task.get("work_units")
        or task.get("capabilityCalls")
        or task.get("capability_calls")
    )
