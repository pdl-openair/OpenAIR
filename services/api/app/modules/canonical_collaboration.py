"""Versioned, read-only collaboration snapshot for the workbench."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection
from typing import Any

from app.models.research_orchestration import ResearchTaskV12
from app.modules.research_blocker_semantics import (
    ResearchBlocker,
    blocker_user_explanation,
    current_blocker_facts,
)


_TERMINAL_TASK_STATES = {"completed", "failed", "cancelled"}
_TERMINAL_WORK_UNIT_STATES = {"completed", "failed", "cancelled"}
_REVIEW_ARTIFACT_STATES = {"needs_review"}
_INPUT_ARTIFACT_STATES = {"needs_input", "insufficient_materials"}


class CanonicalCollaborationAuthorityMismatch(ValueError):
    """Raised when an action authority snapshot is not fenced to this aggregate."""


def _value(value: Any) -> str | None:
    if value is None:
        return None
    raw = getattr(value, "value", value)
    return str(raw)


def _timestamp(value: Any) -> str | None:
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _role(value: Any) -> str:
    role = _value(value)
    if role == "reviewer":
        return "复核工作"
    if role in {"support", "secondary"}:
        return "支持工作"
    return "执行工作"


def _validate_action_authority(
    task: ResearchTaskV12,
    action_authority: dict[str, Any] | None,
) -> dict[str, Any]:
    if action_authority is None:
        return {
            "schemaVersion": "workbench_user_actions_v1",
            "lifecycleKind": "canonical_v1_2",
            "taskId": task.task_id,
            "revision": task.revision,
            "allowedActions": [],
            "items": [],
        }
    if (
        action_authority.get("schemaVersion") != "workbench_user_actions_v1"
        or action_authority.get("lifecycleKind") != "canonical_v1_2"
        or action_authority.get("taskId") != task.task_id
        or action_authority.get("revision") != task.revision
        or not isinstance(action_authority.get("allowedActions"), list)
        or not isinstance(action_authority.get("items"), list)
    ):
        raise CanonicalCollaborationAuthorityMismatch("canonical_action_authority_revision_mismatch")
    return action_authority


def _project_action_items(
    *,
    authority: dict[str, Any],
    unit_ids: set[str],
    artifacts: list[Any],
    visible_artifact_ids: frozenset[str],
) -> list[dict[str, Any]]:
    artifact_refs_by_work_unit: dict[str, list[str]] = defaultdict(list)
    for artifact in artifacts:
        state = _value(artifact.artifact_state)
        if state in _INPUT_ARTIFACT_STATES:
            artifact_refs_by_work_unit[artifact.work_unit_id].append(artifact.artifact_id)

    projected: list[dict[str, Any]] = []
    for item in authority["items"]:
        if not isinstance(item, dict):
            raise CanonicalCollaborationAuthorityMismatch("canonical_action_authority_item_invalid")
        work_unit_id = item.get("workUnitId")
        if work_unit_id is not None and work_unit_id not in unit_ids:
            raise CanonicalCollaborationAuthorityMismatch("canonical_action_authority_work_unit_mismatch")
        projected.append(
            {
                "itemId": str(item.get("itemId") or ""),
                "kind": str(item.get("kind") or "provide_input"),
                "reason": str(item.get("reason") or "当前需要等待服务端确认后再继续。"),
                "workUnitId": work_unit_id,
                "artifactRefs": [
                    str(ref)
                    for ref in item.get("artifactRefs") or artifact_refs_by_work_unit.get(work_unit_id, [])
                    if str(ref) in visible_artifact_ids
                ],
                "allowedActions": [str(action) for action in item.get("allowedActions") or []],
            }
        )
    return projected


def _waiting_projection(
    *,
    task_status: str | None,
    unit: Any,
    unit_state: str | None,
    units_by_id: dict[str, Any],
    needs_review_artifact_refs: list[str],
    action_items: list[dict[str, Any]],
    blocker: ResearchBlocker | None = None,
    current: bool = True,
) -> dict[str, Any] | None:
    if not current:
        return None
    if blocker is not None:
        explanation = blocker_user_explanation(blocker)
        if blocker.kind == "user_input":
            item = next((row for row in action_items if row.get("kind") == "provide_input"
                         and row.get("workUnitId") == unit.work_unit_id
                         and row.get("allowedActions")), None)
            if item is not None:
                explanation = {**explanation, "reason": item["reason"]}
        return {**explanation, "blockingWorkUnitIds": list(blocker.blocking_work_unit_ids)}
    if unit_state == "external_result_unknown":
        return {
            "kind": "blocked_recoverable",
            "reason": "外部执行结果暂时无法确认，研究进度和已有证据已经保留。系统会按可用能力继续核对；不会自动重复提交。",
            "reasonCode": "recoverable_execution_unknown",
            "blockingWorkUnitIds": [],
        }

    if task_status in _TERMINAL_TASK_STATES or unit_state in _TERMINAL_WORK_UNIT_STATES:
        return None

    user_item = next(
        (
            item
            for item in action_items
            if item.get("kind") == "provide_input" and item.get("workUnitId") == unit.work_unit_id
        ),
        None,
    )
    if user_item is not None:
        return {
            "kind": "user_action",
            "reason": user_item["reason"],
            "reasonCode": "user_input_required",
            "blockingWorkUnitIds": [],
        }

    if needs_review_artifact_refs:
        return {
            "kind": "system_review",
            "reason": "产物已经形成，正在等待独立复核；复核完成前不会用于下游。",
            "reasonCode": "system_review_pending",
            "blockingWorkUnitIds": [],
        }

    incomplete_dependencies = [
        dependency_id
        for dependency_id in unit.dependency_work_unit_ids
        if dependency_id in units_by_id
        and _value(units_by_id[dependency_id].status) != "completed"
    ]
    if incomplete_dependencies:
        return {
            "kind": "dependency",
            "reason": "正在等待前置研究工作完成。",
            "reasonCode": "dependency_pending",
            "blockingWorkUnitIds": incomplete_dependencies,
        }

    if unit_state in {"blocked", "failed"}:
        return {
            "kind": "runtime_unavailable",
            "reason": "当前工作未能继续，系统正在确认原因。你可在下方说明需求。",
            "reasonCode": "execution_condition_unconfirmed",
            "blockingWorkUnitIds": [],
        }
    return None


def project_canonical_collaboration(
    task: ResearchTaskV12,
    *,
    action_authority: dict[str, Any] | None = None,
    visible_artifact_ids: Collection[str] = (),
) -> dict[str, Any]:
    """Expose collaboration facts using the catalog's user-visible Artifact set.

    Membership is supplied by the existing public Artifact authority. The empty
    default deliberately fails closed so a visibility read failure cannot leak
    Artifact identity through either the catalog rows or a nested reference.
    """
    authority = _validate_action_authority(task, action_authority)
    visible_ids = frozenset(str(value) for value in visible_artifact_ids)
    visible_artifacts = [row for row in task.artifacts if row.artifact_id in visible_ids]
    blockers = current_blocker_facts(task)
    blocker_by_work = {row.work_unit_id: row for row in blockers if row.work_unit_id is not None}
    active_ref = task.active_research_design_ref
    active_design = next((row for row in task.research_design_versions if active_ref is not None
                          and row.research_design_id == active_ref.research_design_id
                          and row.version == active_ref.version), None)
    active_keys = {(row.substage_instance_id, row.instance_revision)
                   for row in active_design.instances} if active_design is not None else set()
    runtime_by_key = {(row.substage_instance_id, row.instance_revision): row for row in task.substage_runtime_states}

    def current_wait(unit: Any) -> bool:
        key = (unit.substage_instance_id, unit.substage_instance_revision)
        runtime = runtime_by_key.get(key)
        return (key in active_keys and active_ref is not None
                and unit.research_design_version == active_ref.version
                and _value(getattr(runtime, "status", None)) not in {"completed", "skipped"})
    decision = max(
        task.execution_decisions,
        key=lambda row: (row.decision_sequence, row.decided_at, row.execution_decision_id),
        default=None,
    )
    if decision is None:
        task_level_items = _project_action_items(
            authority=authority,
            unit_ids=set(),
            artifacts=[],
            visible_artifact_ids=visible_ids,
        )
        return {
            "schemaVersion": "canonical_collaboration_v1",
            "lifecycleKind": "canonical_v1_2",
            "taskId": task.task_id,
            "taskStatus": _value(task.status),
            "revision": task.revision,
            "revisionFence": {"kind": "research_task_v1_2_revision", "revision": task.revision},
            "sourceIdentity": None,
            "executionDecision": None,
            "workUnits": [],
            "handoffs": [],
            "reviews": [],
            "artifacts": [],
            "userActionItems": task_level_items,
            "allowedActions": list(authority["allowedActions"]),
        }

    assignments = {row.assignment_id: row for row in decision.assignments}
    units = [row for row in task.work_units if row.execution_decision_id == decision.execution_decision_id]
    unit_ids = {row.work_unit_id for row in units}
    units_by_id = {row.work_unit_id: row for row in units}
    artifacts = [row for row in visible_artifacts if row.work_unit_id in unit_ids]
    action_items = _project_action_items(
        authority=authority,
        unit_ids=unit_ids,
        artifacts=artifacts,
        visible_artifact_ids=visible_ids,
    )

    reviewer_ids_by_target: dict[str, list[str]] = defaultdict(list)
    for reviewer in units:
        assignment = assignments.get(reviewer.assignment_id)
        if _value(assignment.role if assignment is not None else None) != "reviewer":
            continue
        for target_id in reviewer.dependency_work_unit_ids:
            if target_id in unit_ids:
                reviewer_ids_by_target[target_id].append(reviewer.work_unit_id)

    def artifact_refs(refs: list[str]) -> list[str]:
        return [ref for ref in refs if ref in visible_ids]

    work_units = []
    for unit in units:
        assignment = assignments.get(unit.assignment_id)
        state = _value(unit.status)
        output_artifact_refs = [row.artifact_id for row in artifacts if row.work_unit_id == unit.work_unit_id]
        needs_review_artifact_refs = [
            row.artifact_id
            for row in artifacts
            if row.work_unit_id == unit.work_unit_id and _value(row.artifact_state) == "needs_review"
        ]
        work_units.append(
            {
                "workUnitId": unit.work_unit_id,
                "assignmentId": unit.assignment_id,
                "role": _role(assignment.role if assignment is not None else None),
                "objective": unit.instruction,
                "responsibleActorId": unit.responsible_actor_ref.actor_id,
                "responsibleActorType": unit.responsible_actor_ref.actor_type,
                "status": state,
                "parallelGroupId": unit.execution_group_id,
                "dependencyWorkUnitIds": [ref for ref in unit.dependency_work_unit_ids if ref in unit_ids],
                "waiting": _waiting_projection(
                    task_status=_value(task.status),
                    unit=unit,
                    unit_state=state,
                    units_by_id=units_by_id,
                    needs_review_artifact_refs=needs_review_artifact_refs,
                    action_items=action_items,
                    blocker=blocker_by_work.get(unit.work_unit_id),
                    current=current_wait(unit) and _value(decision.status) != "superseded",
                ),
                "reviewerWorkUnitIds": sorted(reviewer_ids_by_target.get(unit.work_unit_id, [])),
                "inputArtifactRefs": artifact_refs(unit.input_artifact_refs),
                "outputArtifactRefs": output_artifact_refs,
                "needsReviewArtifactRefs": needs_review_artifact_refs,
                # Internal terminal enums are intentionally not part of the ordinary UI projection.
                "failureReasonCode": None,
                "dispatchedAt": _timestamp(unit.dispatched_at),
                "finishedAt": _timestamp(unit.finished_at),
            }
        )

    handoffs = []
    reviews = []
    for record in task.agent_interaction_records:
        if record.execution_decision_id != decision.execution_decision_id:
            continue
        refs = artifact_refs(list(record.input_artifact_refs) + list(record.output_artifact_refs))
        if record.interaction_type in {"handoff_requested", "handoff_completed"} and record.target_work_unit_id in unit_ids:
            handoffs.append(
                {
                    "handoffId": record.interaction_id,
                    "state": "completed" if record.interaction_type == "handoff_completed" else "requested",
                    "fromWorkUnitId": record.source_work_unit_id,
                    "toWorkUnitId": record.target_work_unit_id,
                    "summary": record.summary,
                    "artifactRefs": refs,
                    "verdict": None
                    if record.verdict is None
                    else {
                        "decision": _value(record.verdict.decision),
                        "rationaleCode": record.verdict.rationale_code,
                    },
                    "occurredAt": _timestamp(record.created_at),
                }
            )
        elif record.interaction_type == "review_submitted" and record.source_work_unit_id in unit_ids:
            reviews.append(
                {
                    "reviewId": record.interaction_id,
                    "reviewerWorkUnitId": record.source_work_unit_id,
                    "targetWorkUnitId": record.target_work_unit_id if record.target_work_unit_id in unit_ids else None,
                    "artifactRefs": refs,
                    "decision": _value(record.verdict.decision) if record.verdict is not None else "insufficient_evidence",
                    "rationaleCode": record.verdict.rationale_code if record.verdict is not None else "review_without_verdict",
                    "submittedAt": _timestamp(record.created_at),
                }
            )

    decision_status = _value(decision.status)
    if decision_status not in {"selected", "dispatched", "closed"}:
        decision_status = "closed"
    return {
        "schemaVersion": "canonical_collaboration_v1",
        "lifecycleKind": "canonical_v1_2",
        "taskId": task.task_id,
        "taskStatus": _value(task.status),
        "revision": task.revision,
        "revisionFence": {"kind": "research_task_v1_2_revision", "revision": task.revision},
        "sourceIdentity": decision.execution_decision_id,
        "executionDecision": {
            "executionDecisionId": decision.execution_decision_id,
            "decisionSequence": decision.decision_sequence,
            "status": decision_status,
            "outcome": _value(decision.outcome),
            "researchDesignVersion": decision.research_design_version,
            "substageInstanceId": decision.substage_instance_id,
            "substageInstanceRevision": decision.substage_instance_revision,
            "decidedAt": _timestamp(decision.decided_at),
            "closedAt": _timestamp(decision.closed_at),
        },
        "workUnits": work_units,
        "handoffs": handoffs,
        "reviews": reviews,
        "artifacts": [
            {
                "artifactId": row.artifact_id,
                "workUnitId": row.work_unit_id,
                "artifactType": row.artifact_type,
                "artifactState": _value(row.artifact_state),
                "version": row.version,
                "sourceCapabilityCallId": row.source_capability_call_id,
            }
            for row in visible_artifacts
        ],
        "userActionItems": action_items,
        "allowedActions": list(authority["allowedActions"]),
    }
