"""Read physical attempt truth before settling an internal admission failure."""

from collections.abc import Mapping, Sequence
from typing import Any

from app.execution.research_internal_skill_call import InternalSkillCallError


def terminal_provider_admission_feedback(
    rows: Sequence[Mapping[str, Any]], reason: str
) -> Mapping[str, Any] | None:
    """No row/active/contradictory evidence never proves terminal execution.

    A received primary response followed by a denied correction is failed tool
    processing, not an entirely unsubmitted call. Original physical evidence is
    retained by the response checkpoint and the exact attempt IDs below.
    """
    if not rows:
        return None
    accepted = {
        "completed": {"response_received"},
        "failed": {"transport_not_submitted", "provider_http_error"},
        "cancelled": {"provider_cancelled"},
        "unknown": {"transport_uncertain"},
    }
    for row in rows:
        state = row["execution_state"]
        if state == "running":
            return None
        if row["transport_outcome"] not in accepted.get(state, set()):
            raise InternalSkillCallError("internal_skill_provider_outcome_conflict")
        expected_submission = {
            "response_received": True,
            "provider_http_error": True,
            "provider_cancelled": True,
            "transport_not_submitted": False,
            "transport_uncertain": None,
        }[row["transport_outcome"]]
        if row["submission_proven"] is not expected_submission:
            raise InternalSkillCallError("internal_skill_provider_submission_conflict")
    if any(row["execution_state"] == "unknown" for row in rows):
        state = "unknown"
    elif rows[-1]["execution_state"] in {"failed", "cancelled"}:
        state = "failed"
    else:
        # Completed transport alone is not the tool's processed output.
        return None
    return {
        "state": state,
        "summary": "The tool could not complete; original physical attempt evidence is retained.",
        "failureClassification": "internal_skill_io_admission_rejected",
        "reasonCode": reason,
        "providerAttemptEvidence": [dict(row) for row in rows],
    }


def reconcile_internal_admission_failure(
    repository: Any, *, owner: Any, prepared: Any, checkpoint: Any, error: ValueError
) -> tuple[Mapping[str, Any] | None, bool]:
    reason = getattr(error, "code", str(error))
    if checkpoint is None or checkpoint.dispatch_id != owner.dispatch_id:
        raise InternalSkillCallError("internal_skill_admission_checkpoint_missing")
    if (
        checkpoint.phase == "prepared"
        and checkpoint.response_payload is None
        and checkpoint.execution_kind == "external_adapter"
    ):
        return {
            "state": "failed",
            "summary": "The tool action was rejected before submission.",
            "failureClassification": "internal_skill_pre_io_rejected",
            "reasonCode": reason,
        }, True
    if checkpoint.execution_kind != "provider_http":
        return None, False
    with repository._uow.transaction() as connection:
        with connection.cursor() as cursor:
            task, _ = repository._load_locked_task(
                cursor,
                task_id=owner.task_id,
                owner_id=owner.owner_id,
                workspace_id=owner.workspace_id,
            )
            child = next(
                (
                    row
                    for row in task.capability_calls
                    if row.capability_call_id == prepared.call.capability_call_id
                ),
                None,
            )
            if (
                child is None
                or child.internal_call_input_snapshot is None
                or child.internal_call_input_snapshot.parent_capability_call_id
                != owner.parent_call_id
                or child.work_unit_id != prepared.call.work_unit_id
            ):
                raise InternalSkillCallError("internal_skill_admission_responsibility_conflict")
            cursor.execute(
                """SELECT attempt_id, attempt_number, execution_state, transport_outcome,
                          io_started, submission_proven, execution_reason
                     FROM openair_provider_http_attempts
                    WHERE parent_kind='capability_call' AND parent_id=%s
                    ORDER BY attempt_number FOR SHARE""",
                (child.capability_call_id,),
            )
            rows = cursor.fetchall()
    feedback = terminal_provider_admission_feedback(rows, reason)
    not_submitted = bool(rows) and all(
        row["execution_state"] == "failed"
        and row["transport_outcome"] == "transport_not_submitted"
        and row["submission_proven"] is False
        for row in rows
    )
    return feedback, not_submitted
