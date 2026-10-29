"""Apply Workbench-authoritative actions proposed by the collaboration agent."""
from __future__ import annotations

import logging
import uuid
from typing import Any, Mapping

from app.auth import RequestIdentity
from app.models.research_external_result import ExplicitRetryCommand
from app.modules.workbench_user_actions import project_workbench_user_actions

logger = logging.getLogger(__name__)


def _string(value: Any) -> str:
    return str(value or "").strip()


def apply_collaboration_proposed_action(
    *,
    task_payload: Mapping[str, Any],
    identity: RequestIdentity,
    proposed: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """Apply one agent-proposed action if and only if the live snapshot allows it."""
    if not isinstance(proposed, Mapping):
        return None
    action_type = _string(proposed.get("type"))
    if not action_type:
        return None

    snapshot = project_workbench_user_actions(
        task_payload=dict(task_payload),
        owner_id=identity["ownerId"],
        workspace_id=identity["workspaceId"],
        actor_id=identity["createdBy"],
    )
    task_id = _string(task_payload.get("taskId"))
    revision = int(task_payload.get("revision") or 0)

    if action_type == "stop":
        if "stop" not in (snapshot.get("allowedActions") or []):
            return {"type": "stop", "applied": False, "reason": "not_allowed"}
        from app.modules.research_dispatch_service import ResearchDispatchService

        try:
            ResearchDispatchService().suppress_with_receipt(
                task_id=task_id,
                owner_id=identity["ownerId"],
                workspace_id=identity["workspaceId"],
                expected_task_revision=revision,
                reason_code="user_stop_requested",
            )
        except Exception:
            logger.exception("collaboration stop failed task_id=%s", task_id)
            return {"type": "stop", "applied": False, "reason": "apply_failed"}
        return {"type": "stop", "applied": True}

    if action_type == "supplement_input":
        if "supplement_input" not in (snapshot.get("allowedActions") or []):
            return {"type": "supplement_input", "applied": False, "reason": "not_allowed"}
        text = _string(proposed.get("text"))
        if not text:
            return {"type": "supplement_input", "applied": False, "reason": "empty_text"}
        from app.modules.controller_command_repository import ControllerCommandRepository

        try:
            row, _replayed = ControllerCommandRepository().create(
                task_id=task_id,
                owner_id=identity["ownerId"],
                workspace_id=identity["workspaceId"],
                kind="supplement_input",
                text=text,
                expected_task_revision=revision,
                idempotency_key=f"collab-supplement-{uuid.uuid4().hex}",
                canonical_progression_available=True,
            )
        except Exception:
            logger.exception("collaboration supplement failed task_id=%s", task_id)
            return {"type": "supplement_input", "applied": False, "reason": "apply_failed"}
        public = row if isinstance(row, dict) else {}
        return {
            "type": "supplement_input",
            "applied": True,
            "commandId": public.get("commandId") or public.get("command_id"),
        }

    if action_type == "regenerate_research_plan":
        return _apply_plan_regeneration(task_payload, identity, task_id)

    if action_type == "explicit_retry":
        work_unit_id = _string(proposed.get("workUnitId"))
        items = _explicit_retry_items(snapshot, work_unit_id)
        if not items:
            return {"type": "explicit_retry", "applied": False, "reason": "not_allowed"}
        context = (items[0].get("commandContexts") or {}).get("explicit_retry")
        if not isinstance(context, Mapping):
            return {"type": "explicit_retry", "applied": False, "reason": "missing_context"}
        try:
            from app.modules.research_external_recovery_service import (
                production_external_recovery_service,
            )

            command = ExplicitRetryCommand(
                task_id=_string(context.get("taskId") or task_id),
                work_unit_id=_string(context.get("workUnitId")),
                dispatch_id=_string(context.get("dispatchId")),
                execution_decision_id=_string(context.get("executionDecisionId")),
                prior_capability_call_id=_string(
                    context.get("priorCapabilityCallId") or context.get("capabilityCallId")
                ),
                prior_attempt_number=int(context.get("priorAttemptNumber") or 1),
                command_request_id=f"collab-retry-{uuid.uuid4().hex}",
                canonical_payload_fingerprint=_string(context.get("canonicalPayloadFingerprint")),
                expected_task_revision=revision,
                owner_id=identity["ownerId"],
                workspace_id=identity["workspaceId"],
                actor_id=identity["createdBy"],
                reason_code=_string(context.get("reasonCode") or "user_confirmed_explicit_retry"),
            )
            production_external_recovery_service().retry(command)
        except Exception:
            logger.exception("collaboration explicit_retry failed task_id=%s", task_id)
            return {"type": "explicit_retry", "applied": False, "reason": "apply_failed"}
        return {
            "type": "explicit_retry",
            "applied": True,
            "workUnitId": _string(context.get("workUnitId")),
        }

    return {"type": action_type, "applied": False, "reason": "unsupported"}


def _explicit_retry_items(
    snapshot: Mapping[str, Any],
    work_unit_id: str,
) -> list[Mapping[str, Any]]:
    items = [
        row
        for row in (snapshot.get("items") or [])
        if isinstance(row, Mapping) and "explicit_retry" in (row.get("allowedActions") or [])
    ]
    if not work_unit_id:
        return items
    return [row for row in items if _string(row.get("workUnitId")) == work_unit_id]


def _apply_plan_regeneration(
    task_payload: Mapping[str, Any],
    identity: RequestIdentity,
    task_id: str,
) -> dict[str, Any]:
    from app.modules.research_start_plan_regeneration import (
        regenerate_failed_research_plan,
        task_has_research_design,
    )

    if task_has_research_design(task_payload):
        return {"type": "regenerate_research_plan", "applied": False, "reason": "design_exists"}
    receipt = regenerate_failed_research_plan(
        owner_id=identity["ownerId"],
        workspace_id=identity["workspaceId"],
        task_id=task_id,
    )
    return {"type": "regenerate_research_plan", **receipt}
