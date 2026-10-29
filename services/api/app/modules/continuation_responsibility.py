"""Single internal contract for DispatchContinuation responsibility transfer.

The contract deliberately separates state facts from responsibility proof.  The
service produces one concrete proof; the repository re-checks the referenced
facts transactionally before the continuation CAS is allowed to complete.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ContinuationResponsibilityKind(StrEnum):
    TASK_TERMINAL = "task_terminal"
    USER_RESPONSIBILITY = "user_responsibility"
    SUCCESSOR_EXECUTION = "successor_execution"
    SYSTEM_RECOVERY = "system_recovery"
    USER_STOPPED = "user_stopped"
    NO_PROOF = "no_proof"


class ContinuationOwnerKind(StrEnum):
    DISPATCH = "dispatch"
    CONTINUATION = "continuation"
    RECOVERY = "recovery"


@dataclass(frozen=True)
class ContinuationResponsibilityProof:
    """Named proof candidate formed by the service and revalidated durably.

    Optional fields are intentionally explicit rather than an untyped facts map:
    a proof may only name an exact target/lineage/owner; it cannot fall back to
    task-wide status or existence checks.
    """

    kind: ContinuationResponsibilityKind
    source_execution_decision_id: str
    reason_code: str
    target_type: str | None = None
    target_id: str | None = None
    target_revision: int | None = None
    user_action: str | None = None
    resume_path: str | None = None
    selection_request_id: str | None = None
    decision_lineage: tuple[str, ...] = ()
    successor_execution_decision_id: str | None = None
    successor_assignment_id: str | None = None
    successor_work_unit_id: str | None = None
    owner_kind: ContinuationOwnerKind | None = None
    owner_id: str | None = None

    @classmethod
    def no_proof(
        cls,
        *,
        source_execution_decision_id: str,
        reason_code: str,
    ) -> "ContinuationResponsibilityProof":
        return cls(
            kind=ContinuationResponsibilityKind.NO_PROOF,
            source_execution_decision_id=source_execution_decision_id,
            reason_code=reason_code,
        )


@dataclass(frozen=True)
class ContinuationOwnerRef:
    kind: ContinuationOwnerKind
    owner_id: str
    execution_decision_id: str
    work_unit_id: str


_USER_RESPONSIBILITY_CONTRACTS: dict[str, tuple[str, str]] = {
    "research_design_confirmation_required": (
        "confirm_research_design",
        "research_design_confirmation",
    ),
    "stage_confirmation_required": (
        "confirm_stage_progression",
        "research_execution_resume",
    ),
    "manual_step_confirmation_required": (
        "confirm_manual_step",
        "research_execution_resume",
    ),
    "reviewer_human_gate_required": (
        "resolve_reviewer_human_gate",
        "research_execution_resume",
    ),
}


def user_responsibility_contract(reason_code: str) -> tuple[str, str] | None:
    """Return the frozen action/resume pair for an explicit user gate.

    Formal ``needs_input`` reasons are handled by the caller because their
    ownership authority lives in ``needs_user_input(reason_code)``.  They use a
    single canonical action/resume pair rather than inventing per-error policy.
    """

    return _USER_RESPONSIBILITY_CONTRACTS.get(reason_code)


NEEDS_INPUT_USER_ACTION = "provide_required_input"
NEEDS_INPUT_RESUME_PATH = "research_execution_resume"
