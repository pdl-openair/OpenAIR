"""PostgreSQL authority for durable Agent work units and handoffs."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.modules.artifact_write import ArtifactStorageError, artifact_write_service
from app.modules.durable_execution import DurableExecutionError, DurableExecutionRepository, _json
from app.modules.research_task_lifecycle_repository import (
    TaskLifecycleWriteConflictError,
    commit_reconciled_task_projection_locked,
)


_WORK_UNIT_STATES = {"queued", "running", "succeeded", "failed", "cancelled", "blocked", "unknown"}
_VISIBILITIES = {"main", "detail"}
_INVOCATION_KINDS = {"agent_brain", "skill", "review"}
_TERMINAL_INVOCATION_STATES = {"succeeded", "failed", "blocked", "cancelled", "abandoned", "unknown"}
_TASK_EXECUTION_MODES = {"automatic_research", "stage_confirmation", "manual_step"}
_GATE_STATES = {"pending", "resolved", "cancelled"}
_REVIEW_VERDICTS = {
    "usable",
    "needs_revision",
    "needs_input",
    "insufficient_materials",
    "human_decision_required",
}
_REVIEW_ARTIFACT_STATES = {
    "usable": "usable",
    "needs_revision": "rejected_or_needs_revision",
    "needs_input": "needs_input",
    "insufficient_materials": "insufficient_materials",
    "human_decision_required": "rejected_or_needs_revision",
}
_SUPPORT_AGENT_IDS = {
    "openair_summary_agent",
    "openair_review_agent",
    "openair_presentation_guidance_agent",
}
_GENERAL_AGENT_IDS = {"general_research_agent", "lightweight_research_assistant"}
_INTERNAL_EXECUTION_DETAIL = re.compile(
    r"\b(?:provider|model|prompt|tokens?|runtime|trace|worker|lease|fence|raw[ _-]?event)\b",
    re.IGNORECASE,
)
_FAILURE_STEP_INTERNAL_DETAIL = re.compile(
    r"\b(?:provider|model|prompt|tokens?|runtime|trace|worker|lease|fence|exceptions?|raw(?:[ _-]?event|[ _-]?exception)?)\b",
    re.IGNORECASE,
)
_PERSISTED_SKILL_REQUEST_PREFIXES = ("skill-request.v1:", "skill-request.v2:")
_PERSISTED_SKILL_REQUEST_WRITE_PREFIX = "skill-request.v2:"


def _safe_text(value: Any, field: str, *, required: bool = False, limit: int = 1_000) -> str | None:
    if value is None:
        if required:
            raise DurableExecutionError(422, f"collaboration_{field}_required")
        return None
    text = str(value).strip()
    if (required and not text) or len(text) > limit or any(ord(char) < 32 for char in text):
        raise DurableExecutionError(422, f"invalid_collaboration_{field}")
    return text or None


def _safe_refs(value: Any, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 100:
        raise DurableExecutionError(422, f"invalid_collaboration_{field}")
    refs = [_safe_text(item, field, required=True, limit=240) for item in value]
    return list(dict.fromkeys(refs))


def _immutable_spec_conflicts(existing: dict[str, Any], requested: dict[str, Any], fields: tuple[str, ...]) -> list[str]:
    """Return the immutable fields which disagree with an idempotent record."""
    return [field for field in fields if existing.get(field) != requested[field]]


def _work_unit_spec_conflicts(existing: dict[str, Any], requested: dict[str, Any]) -> list[str]:
    return _immutable_spec_conflicts(
        existing,
        requested,
        (
            "task_id", "owner_id", "workspace_id", "unit_key", "parent_work_unit_id", "agent_id", "kind",
            "stage_key", "substage_key", "model_profile", "objective", "visibility", "title", "summary",
            "decision_summary", "input_memory_refs", "input_artifact_refs",
            "dispatch_mode", "policy_version", "boundary_kind",
        ),
    )


def _handoff_spec_conflicts(existing: dict[str, Any], requested: dict[str, Any]) -> list[str]:
    return _immutable_spec_conflicts(
        existing,
        requested,
        (
            "run_id", "task_id", "owner_id", "workspace_id", "from_work_unit_id", "to_work_unit_id",
            "from_agent_id", "to_agent_id", "summary",
            "input_memory_refs", "input_artifact_refs", "output_artifact_refs",
        ),
    )


def _invocation_spec_conflicts(existing: dict[str, Any], requested: dict[str, Any]) -> list[str]:
    return _immutable_spec_conflicts(
        existing,
        requested,
        (
            "run_id", "task_id", "work_unit_id", "requesting_agent_id", "skill_id", "invocation_kind",
            "idempotency_key", "request_fingerprint", "input_memory_refs", "input_artifact_refs",
        ),
    )


def _completed_invocation_allows_recomputed_fingerprint(
    existing: dict[str, Any],
    conflicts: list[str],
) -> bool:
    """Adopt a fully persisted result when only transient request context drifted."""
    return (
        conflicts == ["request_fingerprint"]
        and existing.get("state") == "succeeded"
        and bool(existing.get("result_summary"))
        and bool(existing.get("output_artifact_refs"))
    )


@dataclass(frozen=True)
class CollaborationScope:
    task_id: str
    run_id: str
    owner_id: str
    workspace_id: str


class CollaborationRepository:
    def __init__(self, durable: DurableExecutionRepository | None = None):
        self._durable = durable or DurableExecutionRepository()

    @staticmethod
    def _policy_out(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "taskId": row["task_id"], "currentMode": row["current_mode"], "pendingMode": row["pending_mode"],
            "policyVersion": int(row["policy_version"]), "pendingPolicyVersion": row["pending_policy_version"],
            "changedBy": row["changed_by"], "changedAt": _projection_time(row["changed_at"]),
            "appliedAt": _projection_time(row["applied_at"]),
        }

    def _scope_task(self, cur: Any, task_id: str, owner_id: str, workspace_id: str) -> dict[str, Any]:
        task = self._durable._locked_task(cur, task_id)
        if not task or task["owner_id"] != owner_id or task["workspace_id"] != workspace_id:
            raise DurableExecutionError(404, "task not found")
        return task

    @staticmethod
    def _policy_mode_from_task(task: dict[str, Any]) -> str:
        payload = task.get("payload") or {}
        if isinstance(payload, str):
            payload = json.loads(payload)
        mode = payload.get("executionMode") if isinstance(payload, dict) else None
        return str(mode) if mode in _TASK_EXECUTION_MODES else "stage_confirmation"

    def _locked_policy(self, cur: Any, task: dict[str, Any], *, changed_by: str) -> dict[str, Any]:
        cur.execute("SELECT * FROM openair_task_execution_policies WHERE task_id = %s FOR UPDATE", (task["task_id"],))
        policy = cur.fetchone()
        if policy:
            return policy
        # This fallback covers a task created concurrently with a deployment
        # that has already run the migration. The row becomes authoritative
        # before any policy mutation proceeds.
        mode = self._policy_mode_from_task(task)
        cur.execute(
            """INSERT INTO openair_task_execution_policies
               (task_id, owner_id, workspace_id, current_mode, policy_version, changed_by)
               VALUES (%s, %s, %s, %s, 1, %s) RETURNING *""",
            (task["task_id"], task["owner_id"], task["workspace_id"], mode, changed_by),
        )
        return cur.fetchone()

    @staticmethod
    def _project_task_execution_policy(
        cur: Any,
        task: dict[str, Any],
        policy: Mapping[str, Any],
    ) -> None:
        """Project the PostgreSQL authority into the canonical Task payload.

        Writes the policy token (executionMode / pendingExecutionMode /
        executionPolicyVersion) back into the task payload, bumping task.revision.
        Unlike the previous jsonb_set form — which only re-stamped
        progressProjection.revision without recomputing its content, desyncing the
        projection from task.revision in a way the SSE read-side fallback can't
        detect (the stamp matches) — this recomputes the full projection via the
        pure reducer so content and stamp stay consistent with the new revision.
        """
        token = (
            f"execution-policy.v{int(policy['policy_version'])}."
            f"{str(policy['current_mode'])}"
        )
        current_mode = str(policy["current_mode"])
        pending_mode = policy.get("pending_mode")
        payload = task.get("payload")
        if isinstance(payload, str):
            payload = json.loads(payload)
        if not isinstance(payload, dict):
            payload = {}
        # Guard: skip the write when nothing actually changed (preserves the
        # original IS DISTINCT FROM semantics so a no-op PATCH doesn't bump revision).
        if (
            payload.get("executionMode") == current_mode
            and payload.get("pendingExecutionMode") == pending_mode
            and payload.get("executionPolicyVersion") == token
        ):
            return
        payload["executionMode"] = current_mode
        payload["pendingExecutionMode"] = pending_mode
        payload["executionPolicyVersion"] = token
        try:
            commit_reconciled_task_projection_locked(
                cur,
                task_row=task,
                payload=payload,
                task_id=str(task["task_id"]),
                owner_id=str(task["owner_id"]),
                workspace_id=str(task["workspace_id"]),
                reconciled_status=str(task["status"]),
                task_column_updates={
                    "current_stage": payload.get("currentStage")
                    or task.get("current_stage")
                    or "problem_modeling",
                    "title": payload.get("title") or task.get("title") or "待补充任务标题",
                    "domain": payload.get("domain") if "domain" in payload else task.get("domain"),
                },
            )
        except TaskLifecycleWriteConflictError as exc:
            raise DurableExecutionError(409, "task_revision_conflict") from exc
        task["revision"] = int(task["revision"]) + 1
        task["payload"] = payload

    def initialize_task_execution_policy(self, *, task_id: str, owner_id: str, workspace_id: str, changed_by: str) -> dict[str, Any]:
        changed_by = _safe_text(changed_by, "changed_by", required=True, limit=240)
        with self._durable._connect() as conn:
            with conn.cursor() as cur:
                task = self._scope_task(cur, task_id, owner_id, workspace_id)
                return self._policy_out(self._locked_policy(cur, task, changed_by=changed_by))

    def get_task_execution_policy(self, *, task_id: str, owner_id: str, workspace_id: str) -> dict[str, Any]:
        with self._durable._connect() as conn:
            with conn.cursor() as cur:
                task = self._scope_task(cur, task_id, owner_id, workspace_id)
                return self._policy_out(self._locked_policy(cur, task, changed_by=owner_id))

    def set_task_execution_policy(self, *, task_id: str, owner_id: str, workspace_id: str,
                                  mode: str, changed_by: str) -> dict[str, Any]:
        if mode not in _TASK_EXECUTION_MODES:
            raise DurableExecutionError(422, "invalid_execution_mode")
        changed_by = _safe_text(changed_by, "changed_by", required=True, limit=240)
        with self._durable._connect() as conn:
            with conn.cursor() as cur:
                task = self._scope_task(cur, task_id, owner_id, workspace_id)
                # A finished task cannot change its advance mode: nothing refreshes
                # its durable progress projection anymore, so a policy change would
                # desync progressProjection.revision from task.revision and break the
                # SSE durable_payload integrity check (HTTP 409 mid-stream). Reject
                # up front instead of letting the caller trigger that desync.
                if str(task.get("status") or "") in {"completed", "failed", "cancelled"}:
                    raise DurableExecutionError(409, "task_in_terminal_state")
                policy = self._locked_policy(cur, task, changed_by=changed_by)
                cur.execute(
                    """SELECT 1
                       WHERE EXISTS (
                           SELECT 1 FROM openair_execution_runs r
                           WHERE r.task_id = %s AND r.owner_id = %s AND r.workspace_id = %s
                             AND r.state IN ('queued', 'leased')
                             AND EXISTS (
                                SELECT 1 FROM openair_execution_capability_calls c
                                WHERE c.run_id = r.run_id AND c.external_action_started
                                  AND c.state NOT IN ('succeeded', 'failed', 'blocked', 'cancelled', 'abandoned', 'unknown')
                             )
                       ) OR EXISTS (
                           SELECT 1 FROM openair_dispatch d
                           WHERE d.task_id = %s
                             AND d.external_action_started_at IS NOT NULL
                             AND d.result_storage_ref IS NULL
                             AND d.status IN ('submission_authorized', 'submitted')
                       )
                       LIMIT 1""",
                    (task_id, owner_id, workspace_id, task_id),
                )
                execution_in_progress = cur.fetchone() is not None
                if execution_in_progress:
                    if mode == policy["current_mode"]:
                        cur.execute(
                            """UPDATE openair_task_execution_policies
                               SET pending_mode = NULL, pending_policy_version = NULL,
                                   changed_by = %s, changed_at = NOW()
                               WHERE task_id = %s RETURNING *""",
                            (changed_by, task_id),
                        )
                    elif mode == policy["pending_mode"]:
                        cur.execute("SELECT * FROM openair_task_execution_policies WHERE task_id = %s", (task_id,))
                    else:
                        cur.execute(
                            """UPDATE openair_task_execution_policies
                               SET pending_mode = %s, pending_policy_version = policy_version + 1,
                                   changed_by = %s, changed_at = NOW()
                               WHERE task_id = %s RETURNING *""",
                            (mode, changed_by, task_id),
                        )
                elif mode == policy["current_mode"] and policy["pending_mode"] is None:
                    cur.execute("SELECT * FROM openair_task_execution_policies WHERE task_id = %s", (task_id,))
                else:
                    cur.execute(
                        """UPDATE openair_task_execution_policies
                           SET current_mode = %s, pending_mode = NULL, policy_version = policy_version + 1,
                               pending_policy_version = NULL, changed_by = %s, changed_at = NOW(), applied_at = NOW()
                           WHERE task_id = %s RETURNING *""",
                        (mode, changed_by, task_id),
                    )
                    updated_policy = cur.fetchone()
                    self._project_task_execution_policy(cur, task, updated_policy)
                    return self._policy_out(updated_policy)
                updated_policy = cur.fetchone()
                self._project_task_execution_policy(cur, task, updated_policy)
                return self._policy_out(updated_policy)

    def _apply_pending_policy_at_boundary(self, cur: Any, *, task: dict[str, Any], changed_by: str) -> dict[str, Any]:
        policy = self._locked_policy(cur, task, changed_by=changed_by)
        if policy["pending_mode"] is None:
            return policy
        cur.execute(
            """UPDATE openair_task_execution_policies
               SET current_mode = pending_mode, policy_version = pending_policy_version,
                   pending_mode = NULL, pending_policy_version = NULL, applied_at = NOW()
               WHERE task_id = %s RETURNING *""",
            (task["task_id"],),
        )
        policy = cur.fetchone()
        self._project_task_execution_policy(cur, task, policy)
        return policy

    def _scope_run(self, cur: Any, run_id: str, owner_id: str, workspace_id: str) -> CollaborationScope:
        task_id = self._durable._task_id_for_run(cur, run_id)
        if not task_id:
            raise DurableExecutionError(404, "execution run not found")
        self._durable._locked_task(cur, task_id)
        cur.execute("SELECT task_id, owner_id, workspace_id FROM openair_execution_runs WHERE run_id = %s FOR UPDATE", (run_id,))
        run = cur.fetchone()
        if not run or run["owner_id"] != owner_id or run["workspace_id"] != workspace_id:
            raise DurableExecutionError(404, "execution run not found")
        return CollaborationScope(task_id=task_id, run_id=run_id, owner_id=owner_id, workspace_id=workspace_id)

    def _assert_current_run_lease(self, cur: Any, scope: CollaborationScope, *, worker_id: str, fence_epoch: int) -> dict[str, Any]:
        """Reuse the durable run's single fence/stop authority."""
        worker_id = _safe_text(worker_id, "worker_id", required=True, limit=240)
        if isinstance(fence_epoch, bool) or not isinstance(fence_epoch, int) or fence_epoch < 1:
            raise DurableExecutionError(422, "invalid_invocation_attempt_identity")
        return self._durable._leased_run(cur, scope.run_id, worker_id, fence_epoch)

    @staticmethod
    def _assert_artifact_refs(
        cur: Any,
        scope: CollaborationScope,
        refs: list[str],
        *,
        require_current_run: bool = False,
        require_usable_live: bool = False,
        failure_detail: str = "collaboration_artifact_reference_not_found",
        source_call_id: str | None = None,
        source_call_ids: list[str] | None = None,
    ) -> None:
        if not refs:
            return
        clauses = [
            "artifact_id = ANY(%s)",
            "owner_id = %s",
            "workspace_id = %s",
            "source_task_id = %s",
        ]
        params: list[Any] = [refs, scope.owner_id, scope.workspace_id, scope.task_id]
        if require_current_run:
            clauses.append("source_execution_run_id = %s")
            params.append(scope.run_id)
        if require_usable_live:
            clauses.extend((
                "source_mode = 'live'",
                "(metadata->>'artifactState' = 'usable' OR metadata->'validatorResult'->>'continueAllowed' = 'true')",
            ))
        if source_call_id:
            clauses.append("source_capability_call_id = %s")
            params.append(source_call_id)
        elif source_call_ids:
            clauses.append("source_capability_call_id = ANY(%s)")
            params.append(source_call_ids)
        cur.execute(
            f"SELECT artifact_id FROM openair_artifacts WHERE {' AND '.join(clauses)}",
            tuple(params),
        )
        found = {row["artifact_id"] for row in cur.fetchall()}
        if found != set(refs):
            raise DurableExecutionError(409, failure_detail)

    @staticmethod
    def _next_work_unit_sequence(cur: Any, run_id: str) -> int:
        cur.execute("SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence FROM openair_execution_work_units WHERE run_id = %s", (run_id,))
        return int(cur.fetchone()["next_sequence"])

    def create_work_unit(self, *, run_id: str, owner_id: str, workspace_id: str, worker_id: str, fence_epoch: int,
                         agent_id: str, kind: str, title: str,
                         unit_key: str | None = None, parent_work_unit_id: str | None = None,
                         stage_key: str | None = None, substage_key: str | None = None,
                         model_profile: str | None = None, objective: str | None = None,
                         visibility: str = "main", summary: str | None = None,
                         decision_summary: str | None = None, input_memory_refs: list[str] | None = None,
                         input_artifact_refs: list[str] | None = None,
                         boundary_kind: str = "dispatch", enforce_execution_policy: bool = False) -> dict[str, Any]:
        if visibility not in _VISIBILITIES:
            raise DurableExecutionError(422, "invalid_collaboration_visibility")
        agent_id, kind, title = (_safe_text(agent_id, "agent_id", required=True, limit=120), _safe_text(kind, "kind", required=True, limit=80), _safe_text(title, "title", required=True, limit=240))
        unit_key = _safe_text(unit_key, "unit_key", limit=160) or f"unit_{uuid4().hex}"
        parent_work_unit_id = _safe_text(parent_work_unit_id, "parent_work_unit_id", limit=120)
        stage_key = _safe_text(stage_key, "stage_key", limit=120)
        substage_key = _safe_text(substage_key, "substage_key", limit=160)
        model_profile = _safe_text(model_profile, "model_profile", limit=40)
        boundary_kind = _safe_text(boundary_kind, "boundary_kind", required=True, limit=80)
        objective = _safe_text(objective, "objective", limit=4_000) or title
        summary, decision_summary = _safe_text(summary, "summary", limit=2_000), _safe_text(decision_summary, "decision_summary", limit=2_000)
        memory_refs, artifact_refs = _safe_refs(input_memory_refs, "input_memory_refs"), _safe_refs(input_artifact_refs, "input_artifact_refs")
        with self._durable._connect() as conn:
            with conn.cursor() as cur:
                scope = self._scope_run(cur, run_id, owner_id, workspace_id)
                self._assert_current_run_lease(cur, scope, worker_id=worker_id, fence_epoch=fence_epoch)
                self._assert_artifact_refs(cur, scope, artifact_refs)
                cur.execute(
                    "SELECT * FROM openair_execution_work_units WHERE run_id = %s AND unit_key = %s FOR UPDATE",
                    (run_id, unit_key),
                )
                existing = cur.fetchone()
                if existing:
                    # An idempotent retry must preserve the policy snapshot
                    # from the original dispatch. It is never a new boundary.
                    requested = {
                        "task_id": scope.task_id, "owner_id": owner_id, "workspace_id": workspace_id,
                        "unit_key": unit_key, "parent_work_unit_id": parent_work_unit_id, "agent_id": agent_id,
                        "kind": kind, "stage_key": stage_key, "substage_key": substage_key,
                        "model_profile": model_profile, "objective": objective, "visibility": visibility,
                        "title": title, "summary": summary, "decision_summary": decision_summary,
                        "input_memory_refs": memory_refs, "input_artifact_refs": artifact_refs,
                        "dispatch_mode": existing["dispatch_mode"], "policy_version": int(existing["policy_version"]),
                        "boundary_kind": boundary_kind,
                    }
                    if _work_unit_spec_conflicts(existing, requested):
                        raise DurableExecutionError(409, "work_unit_idempotency_conflict")
                    result = dict(existing)
                    if enforce_execution_policy:
                        result["dispatch_paused"] = self._pause_execution_policy_dispatch(
                            cur, scope=scope, unit=existing, worker_id=worker_id,
                            fence_epoch=fence_epoch,
                        )
                    return result
                if parent_work_unit_id:
                    cur.execute(
                        "SELECT 1 FROM openair_execution_work_units WHERE work_unit_id = %s AND run_id = %s",
                        (parent_work_unit_id, run_id),
                    )
                    if not cur.fetchone():
                        raise DurableExecutionError(409, "parent_work_unit_must_share_run")
                task = self._scope_task(cur, scope.task_id, owner_id, workspace_id)
                policy = self._apply_pending_policy_at_boundary(cur, task=task, changed_by=owner_id)
                work_unit_id = f"wu_{uuid4().hex}"
                cur.execute(
                    """INSERT INTO openair_execution_work_units
                       (work_unit_id, run_id, task_id, owner_id, workspace_id, sequence, unit_key, parent_work_unit_id,
                        agent_id, kind, stage_key, substage_key, model_profile, objective, state, visibility,
                        title, summary, decision_summary, input_memory_refs, input_artifact_refs,
                        dispatch_mode, policy_version, boundary_kind)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'queued', %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s) RETURNING *""",
                    (work_unit_id, run_id, scope.task_id, owner_id, workspace_id, self._next_work_unit_sequence(cur, run_id),
                     unit_key, parent_work_unit_id, agent_id, kind, stage_key, substage_key, model_profile, objective,
                     visibility, title, summary, decision_summary, _json(memory_refs), _json(artifact_refs),
                     policy["current_mode"], int(policy["policy_version"]), boundary_kind),
                )
                unit = cur.fetchone()
                result = dict(unit)
                if enforce_execution_policy:
                    result["dispatch_paused"] = self._pause_execution_policy_dispatch(
                        cur, scope=scope, unit=unit, worker_id=worker_id,
                        fence_epoch=fence_epoch,
                    )
                return result

    def transition_work_unit(self, *, work_unit_id: str, owner_id: str, workspace_id: str, worker_id: str,
                             fence_epoch: int, state: str, summary: str | None = None,
                             decision_summary: str | None = None,
                             output_artifact_refs: list[str] | None = None) -> dict[str, Any]:
        if state not in _WORK_UNIT_STATES:
            raise DurableExecutionError(422, "invalid_work_unit_state")
        summary, decision_summary = _safe_text(summary, "summary", limit=2_000), _safe_text(decision_summary, "decision_summary", limit=2_000)
        outputs = _safe_refs(output_artifact_refs, "output_artifact_refs")
        with self._durable._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT run_id FROM openair_execution_work_units WHERE work_unit_id = %s", (work_unit_id,))
                found = cur.fetchone()
                if not found:
                    raise DurableExecutionError(404, "work unit not found")
                scope = self._scope_run(cur, found["run_id"], owner_id, workspace_id)
                self._assert_current_run_lease(cur, scope, worker_id=worker_id, fence_epoch=fence_epoch)
                cur.execute("SELECT * FROM openair_execution_work_units WHERE work_unit_id = %s FOR UPDATE", (work_unit_id,))
                unit = cur.fetchone()
                if not unit or unit["task_id"] != scope.task_id:
                    raise DurableExecutionError(404, "work unit not found")
                if unit["state"] == state:
                    return unit
                if unit["state"] in {"succeeded", "failed", "cancelled", "unknown"}:
                    raise DurableExecutionError(409, "terminal_work_unit")
                allowed = {
                    "queued": {"running", "failed", "cancelled", "blocked", "unknown"},
                    "running": {"succeeded", "failed", "cancelled", "blocked", "unknown"},
                    "blocked": {"running", "failed", "cancelled", "unknown"},
                }
                if state not in allowed.get(unit["state"], set()):
                    raise DurableExecutionError(409, "invalid_work_unit_transition")
                if state == "succeeded":
                    cur.execute(
                        """SELECT call_id, state, failure_classification, output_artifact_refs
                           FROM openair_execution_capability_calls
                           WHERE work_unit_id = %s ORDER BY created_at, call_id FOR UPDATE""",
                        (work_unit_id,),
                    )
                    calls = cur.fetchall()
                    is_coordinator_decision = unit["kind"] == "coordinator"
                    if not calls and not is_coordinator_decision:
                        raise DurableExecutionError(409, "work_unit_success_requires_successful_invocation")
                    if any(call["failure_classification"] == "external_result_unknown" or call["state"] == "unknown" for call in calls):
                        raise DurableExecutionError(409, "external_result_unknown_no_automatic_replay")
                    if any(call["state"] not in _TERMINAL_INVOCATION_STATES for call in calls):
                        raise DurableExecutionError(409, "work_unit_completion_barrier_not_met")
                    if any(call["state"] != "succeeded" for call in calls):
                        raise DurableExecutionError(409, "work_unit_invocation_not_succeeded")
                    call_outputs = list(dict.fromkeys(
                        ref for call in calls for ref in list(call["output_artifact_refs"] or [])
                    ))
                    if is_coordinator_decision and not (summary or decision_summary or unit["summary"] or unit["decision_summary"]):
                        raise DurableExecutionError(409, "coordinator_decision_summary_required")
                    if not call_outputs and not is_coordinator_decision:
                        raise DurableExecutionError(409, "work_unit_success_requires_output_reference")
                    if outputs and outputs != call_outputs:
                        raise DurableExecutionError(409, "work_unit_output_reference_conflict")
                    outputs = call_outputs
                    if outputs:
                        self._assert_artifact_refs(
                            cur,
                            scope,
                            outputs,
                            require_current_run=True,
                            source_call_ids=[call["call_id"] for call in calls],
                        )
                cur.execute(
                    """UPDATE openair_execution_work_units SET state = %s, summary = COALESCE(%s, summary),
                       decision_summary = COALESCE(%s, decision_summary), output_artifact_refs = %s::jsonb,
                       started_at = CASE WHEN %s = 'running' THEN COALESCE(started_at, NOW()) ELSE started_at END,
                       ended_at = CASE WHEN %s IN ('succeeded', 'failed', 'cancelled', 'blocked', 'unknown') THEN COALESCE(ended_at, NOW()) ELSE NULL END
                       WHERE work_unit_id = %s RETURNING *""",
                    (state, summary, decision_summary, _json(outputs), state, state, work_unit_id),
                )
                return cur.fetchone()

    def create_capability_invocation(self, *, run_id: str, owner_id: str, workspace_id: str, worker_id: str,
                                     fence_epoch: int, work_unit_id: str,
                                     requesting_agent_id: str, invocation_kind: str, skill_id: str | None = None,
                                     idempotency_key: str, request_fingerprint: str,
                                     state: str = "queued", input_memory_refs: list[str] | None = None,
                                     input_artifact_refs: list[str] | None = None,
                                     output_artifact_refs: list[str] | None = None) -> dict[str, Any]:
        if state not in _WORK_UNIT_STATES:
            raise DurableExecutionError(422, "invalid_capability_call_state")
        if invocation_kind not in _INVOCATION_KINDS:
            raise DurableExecutionError(422, "invalid_invocation_kind")
        requesting_agent_id = _safe_text(requesting_agent_id, "requesting_agent_id", required=True, limit=120)
        skill_id = _safe_text(skill_id, "skill_id", required=invocation_kind == "skill", limit=160)
        idempotency_key = _safe_text(idempotency_key, "idempotency_key", required=True, limit=240)
        request_fingerprint = _safe_text(request_fingerprint, "request_fingerprint", required=True, limit=240)
        memory_refs, input_refs, output_refs = _safe_refs(input_memory_refs, "input_memory_refs"), _safe_refs(input_artifact_refs, "input_artifact_refs"), _safe_refs(output_artifact_refs, "output_artifact_refs")
        with self._durable._connect() as conn:
            with conn.cursor() as cur:
                scope = self._scope_run(cur, run_id, owner_id, workspace_id)
                self._assert_current_run_lease(cur, scope, worker_id=worker_id, fence_epoch=fence_epoch)
                self._assert_artifact_refs(cur, scope, input_refs)
                cur.execute("SELECT * FROM openair_execution_work_units WHERE work_unit_id = %s FOR UPDATE", (work_unit_id,))
                unit = cur.fetchone()
                if not unit or unit["run_id"] != run_id or unit["agent_id"] != requesting_agent_id:
                    raise DurableExecutionError(409, "skill_call_work_unit_ownership_mismatch")
                if unit["state"] in {"succeeded", "failed", "cancelled", "unknown"}:
                    raise DurableExecutionError(409, "terminal_work_unit")
                requested = {
                    "run_id": run_id, "task_id": scope.task_id, "work_unit_id": work_unit_id,
                    "requesting_agent_id": requesting_agent_id, "skill_id": skill_id,
                    "invocation_kind": invocation_kind, "idempotency_key": idempotency_key,
                    "request_fingerprint": request_fingerprint, "input_memory_refs": memory_refs,
                    "input_artifact_refs": input_refs,
                }
                cur.execute(
                    """SELECT * FROM openair_execution_capability_calls
                       WHERE work_unit_id = %s AND requesting_agent_id = %s AND idempotency_key = %s""",
                    (work_unit_id, requesting_agent_id, idempotency_key),
                )
                existing = cur.fetchone()
                if existing:
                    conflicts = _invocation_spec_conflicts(existing, requested)
                    if conflicts and not _completed_invocation_allows_recomputed_fingerprint(existing, conflicts):
                        raise DurableExecutionError(409, "capability_invocation_idempotency_conflict")
                    return existing
                call_id = f"call_{uuid4().hex}"
                cur.execute(
                    """INSERT INTO openair_execution_capability_calls
                       (call_id, run_id, task_id, state, source_mode, work_unit_id, requesting_agent_id, skill_id, invocation_kind,
                        idempotency_key, request_fingerprint, input_memory_refs, input_artifact_refs, output_artifact_refs)
                       SELECT %s, %s, %s, %s, source_mode, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb
                       FROM openair_execution_runs WHERE run_id = %s
                       ON CONFLICT (work_unit_id, requesting_agent_id, idempotency_key)
                         WHERE idempotency_key IS NOT NULL DO NOTHING RETURNING *""",
                    (call_id, run_id, scope.task_id, state, work_unit_id, requesting_agent_id, skill_id, invocation_kind,
                     idempotency_key, request_fingerprint, _json(memory_refs), _json(input_refs), _json(output_refs), run_id),
                )
                call = cur.fetchone()
                if not call:
                    cur.execute(
                        """SELECT * FROM openair_execution_capability_calls
                           WHERE work_unit_id = %s AND requesting_agent_id = %s AND idempotency_key = %s""",
                        (work_unit_id, requesting_agent_id, idempotency_key),
                    )
                    call = cur.fetchone()
                    conflicts = _invocation_spec_conflicts(call, requested) if call else []
                    if not call or (
                        conflicts
                        and not _completed_invocation_allows_recomputed_fingerprint(call, conflicts)
                    ):
                        raise DurableExecutionError(409, "capability_invocation_idempotency_conflict")
                    return call
                return call

    def create_skill_call(self, **kwargs: Any) -> dict[str, Any]:
        """Compatibility-friendly specialization for a Skill invocation."""
        return self.create_capability_invocation(invocation_kind="skill", **kwargs)

    def finish_agent_and_create_skill_call(
        self,
        *,
        call_id: str,
        owner_id: str,
        workspace_id: str,
        worker_id: str,
        fence_epoch: int,
        summary: str,
        skill_id: str,
        skill_idempotency_key: str,
        skill_request_fingerprint: str,
        skill_request: dict[str, Any],
        input_memory_refs: list[str] | None = None,
        input_artifact_refs: list[str] | None = None,
    ) -> dict[str, Any]:
        """Atomically close an Agent decision and persist its requested Skill."""
        summary = _safe_text(summary, "invocation_summary", required=True, limit=2_000)
        skill_id = _safe_text(skill_id, "skill_id", required=True, limit=160)
        skill_idempotency_key = _safe_text(skill_idempotency_key, "idempotency_key", required=True, limit=240)
        skill_request_fingerprint = _safe_text(
            skill_request_fingerprint, "request_fingerprint", required=True, limit=240,
        )
        from app.execution.durable_multiagent_orchestrator import _normalize_persisted_skill_request

        try:
            normalized_request = _normalize_persisted_skill_request(
                skill_request, require_frozen_authority=True,
            )
        except (DurableExecutionError, KeyError, TypeError, ValueError) as exc:
            raise DurableExecutionError(422, "invalid_persisted_skill_request") from exc
        if normalized_request != skill_request:
            raise DurableExecutionError(422, "invalid_persisted_skill_request")
        memory_refs = _safe_refs(input_memory_refs, "input_memory_refs")
        input_refs = _safe_refs(input_artifact_refs, "input_artifact_refs")
        if (
            normalized_request["skillId"] != skill_id
            or _safe_refs(normalized_request["inputMemoryRefs"], "persisted_skill_input_memory_refs") != memory_refs
            or _safe_refs(normalized_request["inputArtifactRefs"], "persisted_skill_input_artifact_refs") != input_refs
            or not _safe_text(normalized_request["reason"], "persisted_skill_reason", required=True, limit=2_000)
            or not _safe_text(
                normalized_request["idempotencyKey"], "persisted_skill_idempotency_key", required=True, limit=240,
            )
        ):
            raise DurableExecutionError(422, "invalid_persisted_skill_request")
        try:
            request_snapshot = _PERSISTED_SKILL_REQUEST_WRITE_PREFIX + json.dumps(
                normalized_request, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False,
            )
        except (TypeError, ValueError) as exc:
            raise DurableExecutionError(422, "invalid_persisted_skill_request") from exc
        if len(request_snapshot) > 25_000:
            raise DurableExecutionError(422, "invalid_persisted_skill_request")

        with self._durable._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT run_id FROM openair_execution_capability_calls WHERE call_id = %s", (call_id,))
                found = cur.fetchone()
                if not found:
                    raise DurableExecutionError(404, "capability invocation not found")
                scope = self._scope_run(cur, found["run_id"], owner_id, workspace_id)
                self._assert_current_run_lease(cur, scope, worker_id=worker_id, fence_epoch=fence_epoch)
                cur.execute(
                    "SELECT * FROM openair_execution_work_units WHERE work_unit_id = "
                    "(SELECT work_unit_id FROM openair_execution_capability_calls WHERE call_id = %s) FOR UPDATE",
                    (call_id,),
                )
                unit = cur.fetchone()
                cur.execute("SELECT * FROM openair_execution_capability_calls WHERE call_id = %s FOR UPDATE", (call_id,))
                parent = cur.fetchone()
                if (
                    not unit
                    or parent["invocation_kind"] != "agent_brain"
                    or parent["requesting_agent_id"] != unit["agent_id"]
                    or unit["state"] != "running"
                    or not parent["external_action_started"]
                    or int(parent["fence_epoch"]) != fence_epoch
                    or parent["failure_classification"] == "external_result_unknown"
                ):
                    raise DurableExecutionError(409, "agent_skill_request_persistence_invalid")
                requested = {
                    "run_id": scope.run_id,
                    "task_id": scope.task_id,
                    "work_unit_id": unit["work_unit_id"],
                    "requesting_agent_id": unit["agent_id"],
                    "skill_id": skill_id,
                    "invocation_kind": "skill",
                    "idempotency_key": skill_idempotency_key,
                    "request_fingerprint": skill_request_fingerprint,
                    "input_memory_refs": memory_refs,
                    "input_artifact_refs": input_refs,
                }
                self._assert_artifact_refs(cur, scope, input_refs)
                cur.execute(
                    """SELECT * FROM openair_execution_capability_calls
                       WHERE work_unit_id = %s AND requesting_agent_id = %s AND idempotency_key = %s
                       FOR UPDATE""",
                    (unit["work_unit_id"], unit["agent_id"], skill_idempotency_key),
                )
                skill_call = cur.fetchone()
                if skill_call:
                    if (
                        _invocation_spec_conflicts(skill_call, requested)
                        or skill_call.get("result_summary") != request_snapshot
                    ):
                        raise DurableExecutionError(409, "capability_invocation_idempotency_conflict")
                    if parent["state"] != "succeeded" or parent["result_summary"] != summary:
                        raise DurableExecutionError(409, "agent_skill_request_atomicity_conflict")
                    return {"parentCall": parent, "skillCall": skill_call}
                if parent["state"] != "running":
                    raise DurableExecutionError(409, "terminal_capability_invocation")
                skill_call_id = f"call_{uuid4().hex}"
                cur.execute(
                    """INSERT INTO openair_execution_capability_calls
                       (call_id, run_id, task_id, state, source_mode, work_unit_id, requesting_agent_id,
                        skill_id, invocation_kind, idempotency_key, request_fingerprint, result_summary,
                        input_memory_refs, input_artifact_refs, output_artifact_refs)
                       SELECT %s, %s, %s, 'queued', source_mode, %s, %s, %s, 'skill', %s, %s, %s,
                              %s::jsonb, %s::jsonb, '[]'::jsonb
                       FROM openair_execution_runs WHERE run_id = %s RETURNING *""",
                    (
                        skill_call_id, scope.run_id, scope.task_id, unit["work_unit_id"], unit["agent_id"],
                        skill_id, skill_idempotency_key, skill_request_fingerprint, request_snapshot,
                        _json(memory_refs), _json(input_refs), scope.run_id,
                    ),
                )
                skill_call = cur.fetchone()
                cur.execute(
                    """UPDATE openair_execution_capability_calls
                       SET state = 'succeeded', result_summary = %s, output_artifact_refs = '[]'::jsonb,
                           failure_classification = NULL, ended_at = NOW()
                       WHERE call_id = %s RETURNING *""",
                    (summary, call_id),
                )
                return {"parentCall": cur.fetchone(), "skillCall": skill_call}

    def find_recoverable_requested_skill_call(
        self,
        *,
        run_id: str,
        owner_id: str,
        workspace_id: str,
        work_unit_id: str,
        requesting_agent_id: str,
    ) -> dict[str, Any] | None:
        """Return the sole persisted requested Skill call for parent recovery."""
        requesting_agent_id = _safe_text(requesting_agent_id, "requesting_agent_id", required=True, limit=120)
        with self._durable._connect() as conn:
            with conn.cursor() as cur:
                scope = self._scope_run(cur, run_id, owner_id, workspace_id)
                cur.execute(
                    """SELECT * FROM openair_execution_capability_calls
                       WHERE run_id = %s AND work_unit_id = %s AND requesting_agent_id = %s
                          AND invocation_kind = 'skill'
                       ORDER BY created_at, call_id
                       LIMIT 2""",
                    (scope.run_id, work_unit_id, requesting_agent_id),
                )
                calls = cur.fetchall()
                if not calls:
                    return None
                if len(calls) != 1:
                    raise DurableExecutionError(409, "requested_skill_recovery_ambiguous")
                call = calls[0]
                refs = list(call["output_artifact_refs"] or [])
                if refs:
                    self._assert_artifact_refs(
                        cur,
                        scope,
                        refs,
                        require_current_run=True,
                        source_call_id=call["call_id"],
                        failure_detail="requested_skill_recovery_artifact_ownership_mismatch",
                    )
                return call

    def begin_capability_invocation(self, *, call_id: str, owner_id: str, workspace_id: str, worker_id: str, fence_epoch: int,
                                    idempotency_key: str, request_fingerprint: str) -> tuple[dict[str, Any], bool]:
        """Record the one outbound attempt before dispatching it.

        A retry with the same idempotency identity only returns the existing
        fact.  An invocation with an uncertain external outcome is never made
        dispatchable again by this repository.
        """
        if isinstance(fence_epoch, bool) or not isinstance(fence_epoch, int) or fence_epoch < 1 or not idempotency_key.strip() or not request_fingerprint.strip():
            raise DurableExecutionError(422, "invalid_invocation_attempt_identity")
        with self._durable._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT run_id FROM openair_execution_capability_calls WHERE call_id = %s", (call_id,))
                found = cur.fetchone()
                if not found:
                    raise DurableExecutionError(404, "capability invocation not found")
                scope = self._scope_run(cur, found["run_id"], owner_id, workspace_id)
                self._assert_current_run_lease(cur, scope, worker_id=worker_id, fence_epoch=fence_epoch)
                cur.execute("SELECT * FROM openair_execution_capability_calls WHERE call_id = %s FOR UPDATE", (call_id,))
                call = cur.fetchone()
                if call["idempotency_key"] != idempotency_key or call["request_fingerprint"] != request_fingerprint:
                    raise DurableExecutionError(409, "capability_invocation_idempotency_conflict")
                if call["state"] == "unknown" or call["failure_classification"] == "external_result_unknown":
                    raise DurableExecutionError(409, "external_result_unknown_no_automatic_replay")
                if call["external_action_started"]:
                    if (call["idempotency_key"] == idempotency_key and call["request_fingerprint"] == request_fingerprint
                            and int(call["fence_epoch"]) == fence_epoch):
                        return call, True
                    raise DurableExecutionError(409, "capability_invocation_already_started")
                cur.execute(
                    "SELECT state FROM openair_execution_work_units WHERE work_unit_id = %s FOR UPDATE",
                    (call["work_unit_id"],),
                )
                unit = cur.fetchone()
                if not unit or unit["state"] != "running":
                    raise DurableExecutionError(409, "capability_invocation_requires_running_work_unit")
                cur.execute(
                    """UPDATE openair_execution_capability_calls
                       SET external_action_started = TRUE, state = 'running', idempotency_key = %s,
                           request_fingerprint = %s, fence_epoch = %s, attempt_number = attempt_number + 1,
                           started_at = COALESCE(started_at, NOW())
                       WHERE call_id = %s RETURNING *""",
                    (idempotency_key, request_fingerprint, fence_epoch, call_id),
                )
                updated = cur.fetchone()
                cur.execute(
                    "UPDATE openair_execution_runs SET external_action_started = TRUE WHERE run_id = %s",
                    (scope.run_id,),
                )
                return updated, False

    def mark_capability_result_unknown(self, *, call_id: str, owner_id: str, workspace_id: str, worker_id: str, fence_epoch: int) -> dict[str, Any]:
        """Close an indeterminate external attempt without scheduling a replay."""
        with self._durable._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT run_id FROM openair_execution_capability_calls WHERE call_id = %s", (call_id,))
                found = cur.fetchone()
                if not found:
                    raise DurableExecutionError(404, "capability invocation not found")
                scope = self._scope_run(cur, found["run_id"], owner_id, workspace_id)
                self._assert_current_run_lease(cur, scope, worker_id=worker_id, fence_epoch=fence_epoch)
                cur.execute("SELECT * FROM openair_execution_capability_calls WHERE call_id = %s FOR UPDATE", (call_id,))
                call = cur.fetchone()
                if not call["external_action_started"] or int(call["fence_epoch"]) != fence_epoch:
                    raise DurableExecutionError(409, "stale_or_unstarted_capability_invocation")
                if call["failure_classification"] == "external_result_unknown":
                    return call
                cur.execute(
                    """UPDATE openair_execution_capability_calls
                       SET state = 'unknown', failure_classification = 'external_result_unknown', ended_at = NOW()
                       WHERE call_id = %s RETURNING *""",
                    (call_id,),
                )
                updated = cur.fetchone()
                cur.execute(
                    """UPDATE openair_execution_work_units
                       SET state = 'unknown', ended_at = COALESCE(ended_at, NOW())
                       WHERE work_unit_id = %s AND state NOT IN ('succeeded', 'failed', 'cancelled', 'unknown')""",
                    (updated["work_unit_id"],),
                )
                return updated

    def fail_unstarted_capability_invocation(
        self,
        *,
        call_id: str,
        owner_id: str,
        workspace_id: str,
        worker_id: str,
        fence_epoch: int,
        summary: str,
        failure_classification: str = "skill_runtime_unavailable",
    ) -> dict[str, Any]:
        """Fail one queued invocation without inventing an external attempt."""
        summary = _safe_text(summary, "invocation_summary", required=True, limit=2_000)
        if failure_classification != "skill_runtime_unavailable":
            raise DurableExecutionError(422, "invalid_unstarted_failure_classification")
        with self._durable._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT run_id FROM openair_execution_capability_calls WHERE call_id = %s", (call_id,))
                found = cur.fetchone()
                if not found:
                    raise DurableExecutionError(404, "capability invocation not found")
                scope = self._scope_run(cur, found["run_id"], owner_id, workspace_id)
                self._assert_current_run_lease(cur, scope, worker_id=worker_id, fence_epoch=fence_epoch)
                cur.execute("SELECT * FROM openair_execution_capability_calls WHERE call_id = %s FOR UPDATE", (call_id,))
                call = cur.fetchone()
                if call["external_action_started"]:
                    raise DurableExecutionError(409, "capability_invocation_already_started")
                if call["state"] == "failed":
                    if (
                        call["result_summary"] != summary
                        or list(call["output_artifact_refs"] or [])
                        or call["failure_classification"] != failure_classification
                    ):
                        raise DurableExecutionError(409, "capability_invocation_result_conflict")
                    return call
                if call["state"] in _TERMINAL_INVOCATION_STATES:
                    raise DurableExecutionError(409, "terminal_capability_invocation")
                if call["state"] != "queued":
                    raise DurableExecutionError(409, "capability_invocation_not_queued")
                cur.execute(
                    """UPDATE openair_execution_capability_calls
                       SET state = 'failed', result_summary = %s, output_artifact_refs = '[]'::jsonb,
                           failure_classification = %s, ended_at = NOW()
                       WHERE call_id = %s RETURNING *""",
                    (summary, failure_classification, call_id),
                )
                return cur.fetchone()

    def finish_capability_invocation(self, *, call_id: str, owner_id: str, workspace_id: str,
                                     worker_id: str, fence_epoch: int, state: str, summary: str,
                                     output_artifact_refs: list[str] | None = None,
                                     failure_classification: str | None = None) -> dict[str, Any]:
        if state not in {"succeeded", "failed", "blocked"}:
            raise DurableExecutionError(422, "invalid_capability_terminal_state")
        summary = _safe_text(summary, "invocation_summary", required=True, limit=2_000)
        output_refs = _safe_refs(output_artifact_refs, "output_artifact_refs")
        if failure_classification not in {
            None,
            "provider_configuration",
            "provider_rejected_request",
            "provider_response_contract_invalid",
            "skill_finalization_runtime_failed",
            "skill_runtime_unavailable",
        }:
            raise DurableExecutionError(422, "invalid_failure_classification")
        if state != "failed" and failure_classification is not None:
            raise DurableExecutionError(422, "failure_classification_requires_failed_state")
        with self._durable._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT run_id FROM openair_execution_capability_calls WHERE call_id = %s", (call_id,))
                found = cur.fetchone()
                if not found:
                    raise DurableExecutionError(404, "capability invocation not found")
                scope = self._scope_run(cur, found["run_id"], owner_id, workspace_id)
                self._assert_current_run_lease(cur, scope, worker_id=worker_id, fence_epoch=fence_epoch)
                cur.execute("SELECT * FROM openair_execution_capability_calls WHERE call_id = %s FOR UPDATE", (call_id,))
                call = cur.fetchone()
                if not call["external_action_started"] or int(call["fence_epoch"]) != fence_epoch:
                    raise DurableExecutionError(409, "stale_or_unstarted_capability_invocation")
                if call["failure_classification"] == "external_result_unknown":
                    raise DurableExecutionError(409, "external_result_unknown_no_automatic_replay")
                if call["state"] == state:
                    if (
                        call["result_summary"] != summary
                        or list(call["output_artifact_refs"] or []) != output_refs
                        or call["failure_classification"] != failure_classification
                    ):
                        raise DurableExecutionError(409, "capability_invocation_result_conflict")
                    return call
                if call["state"] in {"succeeded", "failed", "blocked", "unknown"}:
                    raise DurableExecutionError(409, "terminal_capability_invocation")
                if state == "succeeded":
                    self._assert_artifact_refs(
                        cur,
                        scope,
                        output_refs,
                        require_current_run=True,
                        source_call_id=call_id,
                    )
                cur.execute(
                    """UPDATE openair_execution_capability_calls
                       SET state = %s, result_summary = %s, output_artifact_refs = %s::jsonb,
                           failure_classification = %s, ended_at = NOW()
                       WHERE call_id = %s RETURNING *""",
                    (state, summary, _json(output_refs), failure_classification, call_id),
                )
                return cur.fetchone()

    def read_review_materials(
        self,
        *,
        run_id: str,
        owner_id: str,
        workspace_id: str,
        artifact_refs: list[str],
        max_total_chars: int = 60_000,
    ) -> list[dict[str, Any]]:
        """Read bounded, immutable live artifact text for the reliability reviewer."""
        refs = _safe_refs(artifact_refs, "review_artifact_refs")
        if not refs:
            raise DurableExecutionError(422, "review_artifact_refs_required")
        with self._durable._connect() as conn:
            with conn.cursor() as cur:
                scope = self._scope_run(cur, run_id, owner_id, workspace_id)
                self._assert_artifact_refs(cur, scope, refs, require_current_run=True)
                cur.execute(
                    """SELECT artifact_id, title, mime_type, storage_type, storage_scope, storage_ref,
                              content_size, content_sha256, source_mode, metadata
                       FROM openair_artifacts
                       WHERE artifact_id = ANY(%s) AND source_execution_run_id = %s
                         AND owner_id = %s AND workspace_id = %s AND source_task_id = %s""",
                    (refs, run_id, owner_id, workspace_id, scope.task_id),
                )
                rows = {row["artifact_id"]: row for row in cur.fetchall()}
        if set(rows) != set(refs):
            raise DurableExecutionError(409, "review_artifact_reference_not_found")

        remaining = max_total_chars
        materials: list[dict[str, Any]] = []
        for artifact_id in refs:
            row = rows[artifact_id]
            if row["source_mode"] != "live" or row["storage_type"] not in {"external", "local"}:
                raise DurableExecutionError(409, "review_requires_live_readable_artifact")
            try:
                content = (
                    artifact_write_service.read_durable_bytes(
                        scope.task_id, row["storage_type"], row["storage_ref"], row.get("storage_scope")
                    )
                    if row["storage_type"] == "external"
                    else artifact_write_service.read_durable_bytes(
                        scope.task_id, row["storage_type"], row["storage_ref"]
                    )
                )
            except ArtifactStorageError as exc:
                raise DurableExecutionError(409, "review_requires_live_readable_artifact") from exc
            if (
                not isinstance(content, bytes)
                or len(content) != int(row["content_size"] or -1)
                or hashlib.sha256(content).hexdigest() != row["content_sha256"]
            ):
                raise DurableExecutionError(409, "review_artifact_content_verification_failed")
            try:
                text = content.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise DurableExecutionError(409, "review_artifact_not_text") from exc
            selected = text[:remaining]
            materials.append({
                "artifactId": artifact_id,
                "title": str(row["title"] or "Research artifact"),
                "mimeType": str(row["mime_type"] or "text/plain"),
                "content": selected,
                "truncated": len(selected) < len(text),
                "contentSha256": row["content_sha256"],
            })
            remaining -= len(selected)
            if remaining <= 0:
                break
        if len(materials) != len(refs):
            raise DurableExecutionError(409, "review_material_budget_exceeded")
        return materials

    def finish_artifact_review(
        self,
        *,
        call_id: str,
        owner_id: str,
        workspace_id: str,
        worker_id: str,
        fence_epoch: int,
        verdict: str,
        basis: str,
        issues: list[dict[str, str]],
        missing_inputs: list[str],
        artifact_refs: list[str],
        continue_allowed: bool = False,
        disposition: str | None = None,
        optional_input: bool = False,
    ) -> dict[str, Any]:
        """Atomically persist one independent review verdict and its artifact gates."""
        if verdict not in _REVIEW_VERDICTS:
            raise DurableExecutionError(422, "invalid_artifact_review_verdict")
        basis = _safe_text(basis, "review_basis", required=True, limit=2_000)
        refs = _safe_refs(artifact_refs, "review_artifact_refs")
        missing = _safe_refs(missing_inputs, "review_missing_inputs")
        if not refs:
            raise DurableExecutionError(422, "review_artifact_refs_required")
        if type(continue_allowed) is not bool:
            raise DurableExecutionError(422, "review_continue_allowed_invalid")
        if disposition not in {"continue", "advisory", "human_authority", None}:
            raise DurableExecutionError(422, "review_disposition_invalid")
        if type(optional_input) is not bool:
            raise DurableExecutionError(422, "review_optional_input_invalid")
        if not isinstance(issues, list) or len(issues) > 40:
            raise DurableExecutionError(422, "invalid_artifact_review_issues")
        normalized_issues: list[dict[str, str]] = []
        for issue in issues:
            if not isinstance(issue, dict) or set(issue) != {"code", "severity", "message"}:
                raise DurableExecutionError(422, "invalid_artifact_review_issues")
            normalized_issues.append({
                "code": _safe_text(issue["code"], "review_issue_code", required=True, limit=120),
                "severity": _safe_text(issue["severity"], "review_issue_severity", required=True, limit=40),
                "message": _safe_text(issue["message"], "review_issue_message", required=True, limit=1_000),
            })
        review_artifact_state = _REVIEW_ARTIFACT_STATES[verdict]
        # A valid post-confirmation reviewer verdict may lower confidence, but it
        # must not turn a usable producer result into a user-facing gate.  Keep
        # the raw review state in validator metadata and expose the effective
        # artifact as downstream-usable when continuation is explicitly allowed.
        artifact_state = "usable" if continue_allowed else review_artifact_state
        from app.modules.artifact_gate import human_gate_for_state

        human_gate = human_gate_for_state(artifact_state, missing_inputs=missing)
        if verdict == "human_decision_required" and not continue_allowed:
            human_gate = {**human_gate, "blockedReason": "human_decision_required", "primaryAction": "review"}
        with self._durable._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT run_id FROM openair_execution_capability_calls WHERE call_id = %s", (call_id,))
                found = cur.fetchone()
                if not found:
                    raise DurableExecutionError(404, "capability invocation not found")
                scope = self._scope_run(cur, found["run_id"], owner_id, workspace_id)
                self._assert_current_run_lease(cur, scope, worker_id=worker_id, fence_epoch=fence_epoch)
                cur.execute("SELECT * FROM openair_execution_capability_calls WHERE call_id = %s FOR UPDATE", (call_id,))
                call = cur.fetchone()
                cur.execute("SELECT * FROM openair_execution_work_units WHERE work_unit_id = %s FOR UPDATE", (call["work_unit_id"],))
                unit = cur.fetchone()
                if (
                    call["invocation_kind"] != "review"
                    or call["requesting_agent_id"] != "result_reliability_audit"
                    or unit["kind"] != "review"
                    or unit["agent_id"] != "result_reliability_audit"
                    or list(call["input_artifact_refs"] or []) != refs
                ):
                    raise DurableExecutionError(409, "artifact_review_ownership_mismatch")
                if not call["external_action_started"] or int(call["fence_epoch"]) != fence_epoch:
                    raise DurableExecutionError(409, "stale_or_unstarted_capability_invocation")
                if call["failure_classification"] == "external_result_unknown" or call["state"] == "unknown":
                    raise DurableExecutionError(409, "external_result_unknown_no_automatic_replay")
                self._assert_artifact_refs(cur, scope, refs, require_current_run=True)
                if call["state"] in {"succeeded", "blocked"}:
                    expected_state = "succeeded" if verdict == "usable" or continue_allowed else "blocked"
                    if call["state"] != expected_state or call["result_summary"] != basis:
                        raise DurableExecutionError(409, "artifact_review_result_conflict")
                    return {"call": call, "workUnit": unit, "verdict": verdict, "artifactState": artifact_state}
                if call["state"] != "running" or unit["state"] != "running":
                    raise DurableExecutionError(409, "artifact_review_not_running")
                for artifact_id in refs:
                    cur.execute(
                        """SELECT metadata, source_capability_call_id FROM openair_artifacts
                           WHERE artifact_id = %s FOR UPDATE""",
                        (artifact_id,),
                    )
                    artifact = cur.fetchone()
                    metadata = dict(artifact["metadata"] or {})
                    validator = {
                        "contractVersion": "result-reliability-review.v1",
                        "evaluator": "result_reliability_audit",
                        "artifactId": artifact_id,
                        "sourceCapabilityCallId": artifact["source_capability_call_id"],
                        "reviewInvocationId": call_id,
                        "verdict": verdict,
                        "basis": basis,
                        "artifactState": artifact_state,
                        "reviewArtifactState": review_artifact_state,
                        "usable": verdict == "usable" or continue_allowed,
                        "continueAllowed": continue_allowed,
                        "qualityAdvisory": bool(continue_allowed and verdict != "usable"),
                        "disposition": disposition or ("continue" if continue_allowed else "human_authority"),
                        "optionalInput": optional_input,
                        "missingInputs": missing,
                        "issues": normalized_issues,
                        "nextAction": human_gate["primaryAction"],
                    }
                    metadata.update({
                        "artifactState": artifact_state,
                        "artifactVersion": artifact["source_capability_call_id"],
                        "validatorResult": validator,
                        "humanGate": human_gate,
                        "reviewedAt": datetime.now(timezone.utc).isoformat(),
                        "reviewedBy": "result_reliability_audit",
                        "reviewSource": "independent_model_call",
                    })
                    cur.execute(
                        """UPDATE openair_artifacts SET metadata = %s::jsonb, updated_at = NOW()
                           WHERE artifact_id = %s""",
                        (_json(metadata), artifact_id),
                    )
                terminal_state = "succeeded" if verdict == "usable" or continue_allowed else "blocked"
                cur.execute(
                    """UPDATE openair_execution_capability_calls
                       SET state = %s, result_summary = %s, output_artifact_refs = '[]'::jsonb, ended_at = NOW()
                       WHERE call_id = %s RETURNING *""",
                    (terminal_state, basis, call_id),
                )
                updated_call = cur.fetchone()
                cur.execute(
                    """UPDATE openair_execution_work_units
                       SET state = %s, summary = %s, decision_summary = %s,
                           output_artifact_refs = %s::jsonb, ended_at = NOW()
                       WHERE work_unit_id = %s RETURNING *""",
                        (terminal_state, basis, verdict, _json(refs if verdict == "usable" or continue_allowed else []), unit["work_unit_id"]),
                )
                updated_unit = cur.fetchone()
                gate = None
                paused_run = None
                if verdict != "usable" and not continue_allowed:
                    cur.execute(
                        """SELECT * FROM openair_execution_gates
                           WHERE run_id = %s AND gate_kind = 'artifact_review'
                             AND work_unit_id = %s FOR UPDATE""",
                        (scope.run_id, unit["work_unit_id"]),
                    )
                    gate = cur.fetchone()
                    if gate is None:
                        cur.execute(
                            """INSERT INTO openair_execution_gates
                               (gate_id, run_id, task_id, owner_id, workspace_id, gate_kind, state,
                                work_unit_id, artifact_refs, reason, policy_version)
                               VALUES (%s, %s, %s, %s, %s, 'artifact_review', 'pending', %s, %s::jsonb, %s, %s)
                               RETURNING *""",
                            (
                                f"gate_{uuid4().hex}", scope.run_id, scope.task_id, owner_id, workspace_id,
                                unit["work_unit_id"], _json(refs), basis, int(unit["policy_version"]),
                            ),
                        )
                        gate = cur.fetchone()
                    cur.execute(
                        """UPDATE openair_execution_runs
                           SET state = 'awaiting_human', lease_owner = NULL, lease_expires_at = NULL
                           WHERE run_id = %s AND state = 'leased' AND lease_owner = %s AND lease_epoch = %s
                           RETURNING *""",
                        (scope.run_id, worker_id, fence_epoch),
                    )
                    paused_run = cur.fetchone()
                    if paused_run is None:
                        raise DurableExecutionError(409, "human_gate_pause_lost_lease")
                    self._durable._event(
                        cur,
                        paused_run,
                        "awaiting_human",
                        {"gateId": gate["gate_id"], "reviewVerdict": verdict},
                    )
                return {
                    "call": updated_call,
                    "workUnit": updated_unit,
                    "verdict": verdict,
                    "artifactState": artifact_state,
                    "gate": gate,
                    "run": paused_run,
                }

    def complete_specialist_after_review(
        self,
        *,
        work_unit_id: str,
        owner_id: str,
        workspace_id: str,
        worker_id: str,
        fence_epoch: int,
        artifact_refs: list[str],
        summary: str,
    ) -> dict[str, Any]:
        """Complete a research source only after an independent usable review exists."""
        refs = _safe_refs(artifact_refs, "reviewed_artifact_refs")
        summary = _safe_text(summary, "summary", required=True, limit=2_000)
        with self._durable._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT run_id FROM openair_execution_work_units WHERE work_unit_id = %s", (work_unit_id,))
                found = cur.fetchone()
                if not found:
                    raise DurableExecutionError(404, "work unit not found")
                scope = self._scope_run(cur, found["run_id"], owner_id, workspace_id)
                self._assert_current_run_lease(cur, scope, worker_id=worker_id, fence_epoch=fence_epoch)
                cur.execute("SELECT * FROM openair_execution_work_units WHERE work_unit_id = %s FOR UPDATE", (work_unit_id,))
                unit = cur.fetchone()
                if unit["state"] == "succeeded":
                    if list(unit["output_artifact_refs"] or []) != refs:
                        raise DurableExecutionError(409, "work_unit_output_reference_conflict")
                    return unit
                if unit["kind"] not in {"specialist", "skill_route"} or unit["state"] not in {"running", "blocked"}:
                    raise DurableExecutionError(409, "specialist_review_completion_invalid")
                self._assert_artifact_refs(
                    cur, scope, refs, require_current_run=True, require_usable_live=True,
                    failure_detail="specialist_completion_requires_live_usable_artifacts",
                )
                cur.execute(
                    """SELECT 1 FROM openair_execution_work_units r
                       JOIN openair_execution_capability_calls c ON c.work_unit_id = r.work_unit_id
                       WHERE r.run_id = %s AND r.kind = 'review' AND r.agent_id = 'result_reliability_audit'
                         AND r.state = 'succeeded' AND c.invocation_kind = 'review' AND c.state = 'succeeded'
                         AND r.input_artifact_refs = %s::jsonb LIMIT 1""",
                    (scope.run_id, _json(refs)),
                )
                if not cur.fetchone():
                    raise DurableExecutionError(409, "specialist_completion_requires_independent_review")
                cur.execute(
                    """UPDATE openair_execution_work_units
                       SET state = 'succeeded', summary = %s, output_artifact_refs = %s::jsonb,
                           ended_at = COALESCE(ended_at, NOW()) WHERE work_unit_id = %s RETURNING *""",
                    (summary, _json(refs), work_unit_id),
                )
                return cur.fetchone()

    def list_work_units(self, *, run_id: str, owner_id: str, workspace_id: str) -> list[dict[str, Any]]:
        with self._durable._connect() as conn:
            with conn.cursor() as cur:
                self._scope_run(cur, run_id, owner_id, workspace_id)
                cur.execute(
                    "SELECT * FROM openair_execution_work_units WHERE run_id = %s ORDER BY sequence",
                    (run_id,),
                )
                return cur.fetchall()

    def handoff(self, *, from_work_unit_id: str, to_work_unit_id: str, owner_id: str, workspace_id: str,
                worker_id: str, fence_epoch: int, summary: str,
                input_memory_refs: list[str] | None = None,
                input_artifact_refs: list[str] | None = None,
                output_artifact_refs: list[str] | None = None) -> dict[str, Any]:
        if from_work_unit_id == to_work_unit_id:
            raise DurableExecutionError(422, "handoff_requires_distinct_work_units")
        summary = _safe_text(summary, "handoff_summary", required=True, limit=2_000)
        memory_refs, input_refs, output_refs = _safe_refs(input_memory_refs, "input_memory_refs"), _safe_refs(input_artifact_refs, "input_artifact_refs"), _safe_refs(output_artifact_refs, "output_artifact_refs")
        with self._durable._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT run_id FROM openair_execution_work_units WHERE work_unit_id = %s", (from_work_unit_id,))
                source = cur.fetchone()
                if not source:
                    raise DurableExecutionError(404, "source work unit not found")
                scope = self._scope_run(cur, source["run_id"], owner_id, workspace_id)
                self._assert_current_run_lease(cur, scope, worker_id=worker_id, fence_epoch=fence_epoch)
                cur.execute("SELECT * FROM openair_execution_work_units WHERE work_unit_id IN (%s, %s) FOR UPDATE", (from_work_unit_id, to_work_unit_id))
                units = {row["work_unit_id"]: row for row in cur.fetchall()}
                source_unit, target_unit = units.get(from_work_unit_id), units.get(to_work_unit_id)
                if not source_unit or not target_unit or source_unit["run_id"] != scope.run_id or target_unit["run_id"] != scope.run_id:
                    raise DurableExecutionError(409, "handoff_work_units_must_share_run")
                if source_unit["state"] != "succeeded":
                    raise DurableExecutionError(409, "handoff_source_not_succeeded")
                if target_unit["state"] != "queued":
                    raise DurableExecutionError(409, "handoff_target_already_started")
                if target_unit["parent_work_unit_id"] != from_work_unit_id:
                    raise DurableExecutionError(409, "handoff_must_follow_frozen_parent")
                durable_outputs = list(source_unit["output_artifact_refs"] or [])
                if not durable_outputs:
                    raise DurableExecutionError(409, "handoff_artifact_reference_mismatch")
                if output_refs and output_refs != durable_outputs:
                    raise DurableExecutionError(409, "handoff_artifact_reference_mismatch")
                if input_refs and input_refs != durable_outputs:
                    raise DurableExecutionError(409, "handoff_artifact_reference_mismatch")
                output_refs = durable_outputs
                input_refs = durable_outputs
                self._assert_artifact_refs(cur, scope, output_refs, require_current_run=True)
                self._assert_artifact_refs(
                    cur, scope, output_refs, require_current_run=True, require_usable_live=True,
                    failure_detail="handoff_requires_live_usable_artifacts",
                )
                target_inputs = list(dict.fromkeys([*list(target_unit["input_artifact_refs"] or []), *output_refs]))
                cur.execute(
                    "UPDATE openair_execution_work_units SET input_artifact_refs = %s::jsonb WHERE work_unit_id = %s",
                    (_json(target_inputs), to_work_unit_id),
                )
                cur.execute(
                    "SELECT * FROM openair_execution_handoffs WHERE from_work_unit_id = %s AND to_work_unit_id = %s",
                    (from_work_unit_id, to_work_unit_id),
                )
                existing = cur.fetchone()
                if existing:
                    requested = {
                        "run_id": scope.run_id, "task_id": scope.task_id, "owner_id": owner_id,
                        "workspace_id": workspace_id, "from_work_unit_id": from_work_unit_id,
                        "to_work_unit_id": to_work_unit_id, "from_agent_id": source_unit["agent_id"],
                        "to_agent_id": target_unit["agent_id"], "summary": summary,
                        "input_memory_refs": memory_refs, "input_artifact_refs": input_refs,
                        "output_artifact_refs": output_refs,
                    }
                    if _handoff_spec_conflicts(existing, requested):
                        raise DurableExecutionError(409, "handoff_idempotency_conflict")
                    return existing
                handoff_id = f"handoff_{uuid4().hex}"
                cur.execute(
                    """INSERT INTO openair_execution_handoffs
                       (handoff_id, run_id, task_id, owner_id, workspace_id, from_work_unit_id, to_work_unit_id,
                        from_agent_id, to_agent_id, summary,
                        input_memory_refs, input_artifact_refs, output_artifact_refs)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb) RETURNING *""",
                    (handoff_id, scope.run_id, scope.task_id, owner_id, workspace_id, from_work_unit_id, to_work_unit_id,
                     source_unit["agent_id"], target_unit["agent_id"], summary,
                     _json(memory_refs), _json(input_refs), _json(output_refs)),
                )
                return cur.fetchone()

    def create_gate(self, *, run_id: str, owner_id: str, workspace_id: str, worker_id: str, fence_epoch: int,
                    gate_kind: str, reason: str, policy_version: int, work_unit_id: str | None = None,
                    artifact_refs: list[str] | None = None) -> dict[str, Any]:
        gate_kind = _safe_text(gate_kind, "gate_kind", required=True, limit=120)
        reason = _safe_text(reason, "gate_reason", required=True, limit=2_000)
        if isinstance(policy_version, bool) or not isinstance(policy_version, int) or policy_version < 1:
            raise DurableExecutionError(422, "invalid_gate_policy_version")
        work_unit_id = _safe_text(work_unit_id, "gate_work_unit_id", limit=120)
        refs = _safe_refs(artifact_refs, "gate_artifact_refs")
        with self._durable._connect() as conn:
            with conn.cursor() as cur:
                scope = self._scope_run(cur, run_id, owner_id, workspace_id)
                self._assert_current_run_lease(cur, scope, worker_id=worker_id, fence_epoch=fence_epoch)
                if work_unit_id:
                    cur.execute(
                        "SELECT 1 FROM openair_execution_work_units WHERE work_unit_id = %s AND run_id = %s",
                        (work_unit_id, run_id),
                    )
                    if not cur.fetchone():
                        raise DurableExecutionError(409, "gate_work_unit_must_share_run")
                self._assert_artifact_refs(cur, scope, refs, require_current_run=True)
                cur.execute(
                    """SELECT * FROM openair_execution_gates
                       WHERE run_id = %s AND gate_kind = %s AND work_unit_id IS NOT DISTINCT FROM %s FOR UPDATE""",
                    (run_id, gate_kind, work_unit_id),
                )
                existing = cur.fetchone()
                if existing:
                    if (existing["reason"] != reason or int(existing["policy_version"]) != policy_version
                            or list(existing["artifact_refs"] or []) != refs):
                        raise DurableExecutionError(409, "gate_idempotency_conflict")
                    return existing
                gate_id = f"gate_{uuid4().hex}"
                cur.execute(
                    """INSERT INTO openair_execution_gates
                       (gate_id, run_id, task_id, owner_id, workspace_id, gate_kind, state, work_unit_id,
                        artifact_refs, reason, policy_version)
                       VALUES (%s, %s, %s, %s, %s, %s, 'pending', %s, %s::jsonb, %s, %s) RETURNING *""",
                    (gate_id, run_id, scope.task_id, owner_id, workspace_id, gate_kind, work_unit_id,
                     _json(refs), reason, policy_version),
                )
                return cur.fetchone()

    def _pause_execution_policy_dispatch(
        self,
        cur: Any,
        *,
        scope: CollaborationScope,
        unit: dict[str, Any],
        worker_id: str,
        fence_epoch: int,
    ) -> bool:
        """Create and pause on a policy gate at the same dispatch boundary."""
        if unit["boundary_kind"] != "handoff_dispatch" or unit["kind"] != "specialist":
            return False
        if unit["state"] != "queued":
            return False
        cur.execute(
            """SELECT 1 FROM openair_execution_capability_calls
               WHERE work_unit_id = %s AND (
                 external_action_started OR started_at IS NOT NULL OR state = 'running'
               ) LIMIT 1""",
            (unit["work_unit_id"],),
        )
        if cur.fetchone():
            return False
        mode = str(unit["dispatch_mode"])
        if mode == "automatic_research":
            return False
        if mode == "stage_confirmation":
            parent_id = unit.get("parent_work_unit_id")
            if not parent_id:
                return False
            cur.execute(
                "SELECT stage_key FROM openair_execution_work_units WHERE work_unit_id = %s AND run_id = %s FOR UPDATE",
                (parent_id, scope.run_id),
            )
            parent = cur.fetchone()
            if parent is None or parent.get("stage_key") == unit.get("stage_key"):
                return False
        elif mode != "manual_step":
            return False
        cur.execute(
            """SELECT * FROM openair_execution_gates
               WHERE run_id = %s AND gate_kind = 'execution_policy' AND work_unit_id = %s FOR UPDATE""",
            (scope.run_id, unit["work_unit_id"]),
        )
        gate = cur.fetchone()
        if gate is not None:
            return gate["state"] == "pending"
        cur.execute(
            """INSERT INTO openair_execution_gates
               (gate_id, run_id, task_id, owner_id, workspace_id, gate_kind, state, work_unit_id,
                artifact_refs, reason, policy_version)
               VALUES (%s, %s, %s, %s, %s, 'execution_policy', 'pending', %s, '[]'::jsonb, %s, %s)
               RETURNING *""",
            (
                f"gate_{uuid4().hex}", scope.run_id, scope.task_id, scope.owner_id, scope.workspace_id,
                unit["work_unit_id"], "继续下一步研究前，需要你确认。",
                int(unit["policy_version"]),
            ),
        )
        gate = cur.fetchone()
        cur.execute(
            """UPDATE openair_execution_runs
               SET state = 'awaiting_human', lease_owner = NULL, lease_expires_at = NULL
               WHERE run_id = %s AND state = 'leased' AND lease_owner = %s AND lease_epoch = %s
               RETURNING *""",
            (scope.run_id, worker_id, fence_epoch),
        )
        paused_run = cur.fetchone()
        if paused_run is None:
            raise DurableExecutionError(409, "human_gate_pause_lost_lease")
        self._durable._event(
            cur, paused_run, "awaiting_human",
            {"gateId": gate["gate_id"], "executionPolicy": True},
        )
        return True

    def pause_for_artifact_review(
        self,
        *,
        run_id: str,
        owner_id: str,
        workspace_id: str,
        worker_id: str,
        fence_epoch: int,
    ) -> dict[str, Any]:
        """Recover a terminal review into the same durable human gate used by fresh reviews."""
        with self._durable._connect() as conn:
            with conn.cursor() as cur:
                scope = self._scope_run(cur, run_id, owner_id, workspace_id)
                self._assert_current_run_lease(
                    cur, scope, worker_id=worker_id, fence_epoch=fence_epoch,
                )
                cur.execute(
                    """SELECT * FROM openair_execution_work_units u
                       WHERE u.run_id = %s AND u.kind = 'review'
                         AND u.agent_id = 'result_reliability_audit'
                         AND (
                           u.state = 'blocked' OR EXISTS (
                             SELECT 1 FROM openair_execution_capability_calls c
                             WHERE c.work_unit_id = u.work_unit_id
                               AND c.invocation_kind = 'review'
                               AND c.state IN ('blocked', 'failed', 'cancelled')
                           )
                         )
                       ORDER BY u.sequence DESC LIMIT 1 FOR UPDATE""",
                    (run_id,),
                )
                unit = cur.fetchone()
                if unit is None:
                    raise DurableExecutionError(409, "artifact_review_pause_source_not_found")
                cur.execute(
                    """SELECT * FROM openair_execution_capability_calls
                       WHERE run_id = %s AND work_unit_id = %s AND invocation_kind = 'review'
                         AND state IN ('blocked', 'failed', 'cancelled')
                       ORDER BY created_at DESC LIMIT 1 FOR UPDATE""",
                    (run_id, unit["work_unit_id"]),
                )
                call = cur.fetchone()
                refs = _safe_refs(
                    (call or {}).get("input_artifact_refs") or unit.get("input_artifact_refs"),
                    "review_artifact_refs",
                )
                if not refs:
                    raise DurableExecutionError(409, "review_artifact_refs_required")
                self._assert_artifact_refs(cur, scope, refs, require_current_run=True)
                reason = (
                    str(call.get("result_summary"))
                    if call and call.get("state") == "blocked" and call.get("result_summary")
                    else "Independent artifact review requires a human decision."
                )
                cur.execute(
                    """SELECT * FROM openair_execution_gates
                       WHERE run_id = %s AND gate_kind = 'artifact_review'
                         AND work_unit_id = %s FOR UPDATE""",
                    (run_id, unit["work_unit_id"]),
                )
                gate = cur.fetchone()
                if gate is not None and gate["state"] != "pending":
                    raise DurableExecutionError(409, "terminal_execution_gate")
                if gate is None:
                    cur.execute(
                        """INSERT INTO openair_execution_gates
                           (gate_id, run_id, task_id, owner_id, workspace_id, gate_kind, state,
                            work_unit_id, artifact_refs, reason, policy_version)
                           VALUES (%s, %s, %s, %s, %s, 'artifact_review', 'pending', %s, %s::jsonb, %s, %s)
                           RETURNING *""",
                        (
                            f"gate_{uuid4().hex}", run_id, scope.task_id, owner_id, workspace_id,
                            unit["work_unit_id"], _json(refs), reason, int(unit["policy_version"]),
                        ),
                    )
                    gate = cur.fetchone()
                cur.execute(
                    """UPDATE openair_execution_runs
                       SET state = 'awaiting_human', lease_owner = NULL, lease_expires_at = NULL
                       WHERE run_id = %s AND state = 'leased' AND lease_owner = %s AND lease_epoch = %s
                       RETURNING *""",
                    (run_id, worker_id, fence_epoch),
                )
                paused_run = cur.fetchone()
                if paused_run is None:
                    raise DurableExecutionError(409, "human_gate_pause_lost_lease")
                self._durable._event(
                    cur,
                    paused_run,
                    "awaiting_human",
                    {"gateId": gate["gate_id"], "reviewRecovery": True},
                )
                return {"gate": gate, "run": paused_run}

    def decide_artifact_review_gate(
        self,
        *,
        task_id: str,
        gate_id: str,
        owner_id: str,
        workspace_id: str,
        decided_by: str,
        action: str,
        note: str | None = None,
    ) -> dict[str, Any]:
        """Atomically terminate a paused run or supersede it with a fresh revision run."""
        if action not in {"start_revision_run", "terminate_run"}:
            raise DurableExecutionError(422, "invalid_execution_gate_decision")
        decided_by = _safe_text(decided_by, "gate_decided_by", required=True, limit=240)
        note = _safe_text(note, "gate_resolution_note", limit=2_000)
        with self._durable._connect() as conn:
            with conn.cursor() as cur:
                task = self._scope_task(cur, task_id, owner_id, workspace_id)
                cur.execute(
                    """SELECT run_id, gate_kind FROM openair_execution_gates
                       WHERE gate_id = %s AND task_id = %s AND owner_id = %s AND workspace_id = %s""",
                    (gate_id, task_id, owner_id, workspace_id),
                )
                gate_ref = cur.fetchone()
                if gate_ref is None or gate_ref["gate_kind"] != "artifact_review":
                    raise DurableExecutionError(404, "execution gate not found")
                cur.execute(
                    """SELECT * FROM openair_execution_runs
                       WHERE run_id = %s AND task_id = %s AND owner_id = %s AND workspace_id = %s
                       FOR UPDATE""",
                    (gate_ref["run_id"], task_id, owner_id, workspace_id),
                )
                run = cur.fetchone()
                if run is None:
                    raise DurableExecutionError(404, "execution run not found")
                cur.execute(
                    """SELECT * FROM openair_execution_gates
                       WHERE gate_id = %s AND run_id = %s AND task_id = %s
                         AND owner_id = %s AND workspace_id = %s FOR UPDATE""",
                    (gate_id, run["run_id"], task_id, owner_id, workspace_id),
                )
                gate = cur.fetchone()
                if gate is None or gate["gate_kind"] != "artifact_review":
                    raise DurableExecutionError(404, "execution gate not found")
                if gate["state"] != "pending":
                    if (
                        gate.get("resolution_action") == action
                        and gate.get("resolution_note") == note
                        and gate.get("resolved_by") == decided_by
                    ):
                        return self._gate_decision_out(task, run, gate)
                    raise DurableExecutionError(409, "execution_gate_decision_conflict")
                if run["state"] != "awaiting_human":
                    raise DurableExecutionError(409, "execution_gate_run_not_awaiting_human")
                if action == "terminate_run":
                    cur.execute(
                        """UPDATE openair_execution_gates
                           SET state = 'cancelled', resolution_action = 'terminate_run', resolution_note = %s,
                               resolved_by = %s, resolved_at = NOW()
                           WHERE run_id = %s AND state = 'pending'""",
                        (note, decided_by, run["run_id"]),
                    )
                    cur.execute("SELECT * FROM openair_execution_gates WHERE gate_id = %s", (gate_id,))
                    decided_gate = cur.fetchone()
                    cur.execute(
                        """UPDATE openair_execution_runs
                           SET state = 'cancelled', failure_classification = 'stopped_by_user',
                               lease_owner = NULL, lease_expires_at = NULL, ended_at = NOW()
                           WHERE run_id = %s AND state = 'awaiting_human' RETURNING *""",
                        (run["run_id"],),
                    )
                    terminal = cur.fetchone()
                    if decided_gate is None or decided_gate["state"] != "cancelled" or terminal is None:
                        raise DurableExecutionError(409, "execution_gate_decision_lost_race")
                    cur.execute(
                        """UPDATE openair_execution_capability_calls
                           SET state = 'cancelled', ended_at = COALESCE(ended_at, NOW())
                           WHERE run_id = %s AND state NOT IN
                             ('succeeded', 'failed', 'blocked', 'cancelled', 'abandoned', 'unknown')""",
                        (run["run_id"],),
                    )
                    cur.execute(
                        """UPDATE openair_execution_work_units
                           SET state = 'cancelled', ended_at = COALESCE(ended_at, NOW())
                           WHERE run_id = %s AND state IN ('queued', 'running')""",
                        (run["run_id"],),
                    )
                    self._durable._event(
                        cur, terminal, "human_gate_terminated",
                        {"gateId": gate_id, "decidedBy": decided_by},
                    )
                    self._durable._project_task(
                        cur,
                        task,
                        call_id=run["capability_call_id"],
                        call_status="failed",
                    )
                    return self._gate_decision_out(task, terminal, decided_gate)

                cur.execute(
                    """SELECT 1 FROM openair_execution_capability_calls
                       WHERE run_id = %s AND (
                         state = 'unknown' OR failure_classification = 'external_result_unknown'
                       ) LIMIT 1""",
                    (run["run_id"],),
                )
                if cur.fetchone():
                    raise DurableExecutionError(409, "external_result_unknown_no_automatic_replay")
                cur.execute(
                    """SELECT 1 FROM openair_execution_gates
                       WHERE run_id = %s AND gate_id <> %s AND state = 'pending' LIMIT 1""",
                    (run["run_id"], gate_id),
                )
                if cur.fetchone():
                    raise DurableExecutionError(409, "other_execution_gates_are_pending")
                cur.execute(
                    """SELECT 1 FROM openair_execution_runs
                       WHERE task_id = %s AND run_id <> %s
                         AND state IN ('queued', 'leased', 'awaiting_human', 'cancel_requested')
                       LIMIT 1""",
                    (task_id, run["run_id"]),
                )
                if cur.fetchone():
                    raise DurableExecutionError(409, "another_execution_run_is_active")
                cur.execute(
                    "SELECT COALESCE(MAX(run_number), 0) + 1 AS next_run_number FROM openair_execution_runs WHERE task_id = %s",
                    (task_id,),
                )
                run_number = int(cur.fetchone()["next_run_number"])
                successor_run_id = f"run_{uuid4().hex}"
                successor_call_id = f"call_{uuid4().hex}"
                snapshot = json.loads(_json(run["input_snapshot"] or {}))
                snapshot["gateDecisionContext"] = {
                    "sourceRunId": run["run_id"],
                    "gateId": gate_id,
                    "action": action,
                    "note": note,
                }
                source_mode = str(run["source_mode"])
                fingerprint = hashlib.sha256(
                    _json({"inputSnapshot": snapshot, "sourceMode": source_mode}).encode("utf-8")
                ).hexdigest()
                now = datetime.now(timezone.utc).isoformat()
                payload = self._durable._task_payload(task)
                calls = list(payload.get("capabilityCalls") or [])
                for call_projection in calls:
                    if call_projection.get("id") == run["capability_call_id"]:
                        call_projection["status"] = "failed"
                        call_projection["finishedAt"] = now
                        call_projection["resultSummary"] = "已按你的决定结束上一轮，并开始新一轮研究。"
                        break
                calls.append({
                    "id": successor_call_id,
                    "ownerId": owner_id,
                    "workspaceId": workspace_id,
                    "createdBy": decided_by,
                    "taskId": task_id,
                    "runId": successor_run_id,
                    "name": "durable_artifact_review_revision",
                    "status": "not_started",
                    "requestedAt": now,
                    "sourceMode": source_mode,
                    "runMode": source_mode,
                })
                payload["capabilityCalls"] = calls
                task["payload"] = payload
                projected = self._durable._project_task(cur, task)
                cur.execute(
                    """UPDATE openair_execution_runs
                       SET state = 'superseded', failure_classification = 'human_requested_revision',
                           lease_owner = NULL, lease_expires_at = NULL, ended_at = NOW()
                       WHERE run_id = %s AND state = 'awaiting_human' RETURNING *""",
                    (run["run_id"],),
                )
                superseded = cur.fetchone()
                if superseded is None:
                    raise DurableExecutionError(409, "execution_gate_decision_lost_race")
                cur.execute(
                    """UPDATE openair_execution_capability_calls
                       SET state = 'cancelled', ended_at = COALESCE(ended_at, NOW())
                       WHERE run_id = %s AND state NOT IN
                         ('succeeded', 'failed', 'blocked', 'cancelled', 'abandoned', 'unknown')""",
                    (run["run_id"],),
                )
                cur.execute(
                    """UPDATE openair_execution_work_units
                       SET state = 'cancelled', ended_at = COALESCE(ended_at, NOW())
                       WHERE run_id = %s AND state IN ('queued', 'running')""",
                    (run["run_id"],),
                )
                cur.execute(
                    """INSERT INTO openair_execution_runs
                       (run_id, task_id, owner_id, workspace_id, task_revision, run_number, run_kind,
                        idempotency_key, request_fingerprint, input_snapshot, target_substage,
                        source_mode, capability_call_id, state)
                       VALUES (%s, %s, %s, %s, %s, %s, 'artifact_review_revision', %s, %s, %s::jsonb,
                               %s, %s, %s, 'queued') RETURNING *""",
                    (
                        successor_run_id, task_id, owner_id, workspace_id, int(task["revision"]), run_number,
                        f"gate-decision:{gate_id}", fingerprint, _json(snapshot), run.get("target_substage"),
                        source_mode, successor_call_id,
                    ),
                )
                successor = cur.fetchone()
                cur.execute(
                    """INSERT INTO openair_execution_capability_calls
                       (call_id, run_id, task_id, state, source_mode)
                       VALUES (%s, %s, %s, 'queued', %s)""",
                    (successor_call_id, successor_run_id, task_id, source_mode),
                )
                cur.execute(
                    """UPDATE openair_execution_gates
                       SET state = 'resolved', resolution_action = 'start_revision_run', resolution_note = %s,
                           successor_run_id = %s, resolved_by = %s, resolved_at = NOW()
                       WHERE gate_id = %s AND state = 'pending' RETURNING *""",
                    (note, successor_run_id, decided_by, gate_id),
                )
                decided_gate = cur.fetchone()
                if decided_gate is None:
                    raise DurableExecutionError(409, "execution_gate_decision_lost_race")
                self._durable._event(
                    cur, superseded, "superseded_by_gate_decision",
                    {"gateId": gate_id, "successorRunId": successor_run_id, "decidedBy": decided_by},
                )
                self._durable._event(
                    cur, successor, "start_requested",
                    {"sourceRunId": run["run_id"], "sourceGateId": gate_id, "taskRevision": int(task["revision"])},
                )
                result = self._gate_decision_out(task, superseded, decided_gate)
                result["taskStatus"] = str(projected.get("status") or "running")
                return result

    def decide_execution_policy_gate(
        self,
        *,
        task_id: str,
        gate_id: str,
        owner_id: str,
        workspace_id: str,
        decided_by: str,
        action: str,
        note: str | None = None,
    ) -> dict[str, Any]:
        """Resume exactly the paused run after a policy-boundary confirmation."""
        if action not in {"continue_run", "terminate_run"}:
            raise DurableExecutionError(422, "invalid_execution_gate_decision")
        decided_by = _safe_text(decided_by, "gate_decided_by", required=True, limit=240)
        note = _safe_text(note, "gate_resolution_note", limit=2_000)
        with self._durable._connect() as conn:
            with conn.cursor() as cur:
                task = self._scope_task(cur, task_id, owner_id, workspace_id)
                cur.execute(
                    """SELECT run_id FROM openair_execution_gates
                       WHERE gate_id = %s AND task_id = %s AND owner_id = %s AND workspace_id = %s""",
                    (gate_id, task_id, owner_id, workspace_id),
                )
                gate_ref = cur.fetchone()
                if gate_ref is None:
                    raise DurableExecutionError(404, "execution gate not found")
                cur.execute(
                    """SELECT * FROM openair_execution_runs
                       WHERE run_id = %s AND task_id = %s AND owner_id = %s AND workspace_id = %s
                       FOR UPDATE""",
                    (gate_ref["run_id"], task_id, owner_id, workspace_id),
                )
                run = cur.fetchone()
                if run is None:
                    raise DurableExecutionError(404, "execution run not found")
                cur.execute(
                    """SELECT * FROM openair_execution_gates
                       WHERE gate_id = %s AND run_id = %s AND task_id = %s
                         AND owner_id = %s AND workspace_id = %s FOR UPDATE""",
                    (gate_id, run["run_id"], task_id, owner_id, workspace_id),
                )
                gate = cur.fetchone()
                if gate is None or gate["gate_kind"] != "execution_policy":
                    raise DurableExecutionError(404, "execution gate not found")
                if gate["state"] != "pending":
                    if (
                        gate.get("resolution_action") == action
                        and gate.get("resolution_note") == note
                        and gate.get("resolved_by") == decided_by
                    ):
                        return self._gate_decision_out(task, run, gate)
                    raise DurableExecutionError(409, "execution_gate_decision_conflict")
                cur.execute(
                    """SELECT run_id FROM openair_execution_runs WHERE task_id = %s
                       AND owner_id = %s AND workspace_id = %s ORDER BY run_number DESC LIMIT 1""",
                    (task_id, owner_id, workspace_id),
                )
                latest = cur.fetchone()
                if latest is None or latest["run_id"] != run["run_id"]:
                    raise DurableExecutionError(409, "execution_gate_not_latest_run")
                if run["state"] != "awaiting_human":
                    raise DurableExecutionError(409, "execution_gate_run_not_awaiting_human")
                if action == "terminate_run":
                    cur.execute(
                        """UPDATE openair_execution_gates
                           SET state = 'cancelled', resolution_action = 'terminate_run', resolution_note = %s,
                               resolved_by = %s, resolved_at = NOW()
                           WHERE run_id = %s AND state = 'pending'""",
                        (note, decided_by, run["run_id"]),
                    )
                    cur.execute("SELECT * FROM openair_execution_gates WHERE gate_id = %s", (gate_id,))
                    decided_gate = cur.fetchone()
                    cur.execute(
                        """UPDATE openair_execution_runs
                           SET state = 'cancelled', failure_classification = 'stopped_by_user',
                               lease_owner = NULL, lease_expires_at = NULL, ended_at = NOW()
                           WHERE run_id = %s AND state = 'awaiting_human' RETURNING *""",
                        (run["run_id"],),
                    )
                    terminal = cur.fetchone()
                    if decided_gate is None or terminal is None:
                        raise DurableExecutionError(409, "execution_gate_decision_lost_race")
                    cur.execute(
                        """UPDATE openair_execution_capability_calls SET state = 'cancelled', ended_at = COALESCE(ended_at, NOW())
                           WHERE run_id = %s AND state NOT IN ('succeeded', 'failed', 'blocked', 'cancelled', 'abandoned', 'unknown')""",
                        (run["run_id"],),
                    )
                    cur.execute(
                        """UPDATE openair_execution_work_units SET state = 'cancelled', ended_at = COALESCE(ended_at, NOW())
                           WHERE run_id = %s AND state IN ('queued', 'running')""",
                        (run["run_id"],),
                    )
                    self._durable._event(cur, terminal, "human_gate_terminated", {"gateId": gate_id, "decidedBy": decided_by})
                    projected = self._durable._project_task(
                        cur, task, call_id=run["capability_call_id"], call_status="failed",
                    )
                    result = self._gate_decision_out(task, terminal, decided_gate)
                    result["taskStatus"] = str(projected.get("status") or "cancelled")
                    return result
                cur.execute(
                    """SELECT 1 FROM openair_execution_capability_calls
                       WHERE run_id = %s AND (state = 'unknown' OR failure_classification = 'external_result_unknown')
                       LIMIT 1""",
                    (run["run_id"],),
                )
                if cur.fetchone():
                    raise DurableExecutionError(409, "external_result_unknown_no_automatic_replay")
                cur.execute(
                    """SELECT 1 FROM openair_execution_gates
                       WHERE run_id = %s AND gate_id <> %s AND state = 'pending' LIMIT 1""",
                    (run["run_id"], gate_id),
                )
                if cur.fetchone():
                    raise DurableExecutionError(409, "other_execution_gates_are_pending")
                cur.execute(
                    """UPDATE openair_execution_gates
                       SET state = 'resolved', resolution_action = 'continue_run', resolution_note = %s,
                           resolved_by = %s, resolved_at = NOW()
                       WHERE gate_id = %s AND state = 'pending' RETURNING *""",
                    (note, decided_by, gate_id),
                )
                decided_gate = cur.fetchone()
                cur.execute(
                    """UPDATE openair_execution_runs
                       SET state = 'queued', lease_owner = NULL, lease_expires_at = NULL
                       WHERE run_id = %s AND state = 'awaiting_human' RETURNING *""",
                    (run["run_id"],),
                )
                resumed = cur.fetchone()
                if decided_gate is None or resumed is None:
                    raise DurableExecutionError(409, "execution_gate_decision_lost_race")
                self._durable._event(
                    cur, resumed, "execution_policy_continued",
                    {"gateId": gate_id, "decidedBy": decided_by},
                )
                return self._gate_decision_out(task, resumed, decided_gate)

    def execution_gate_kind(self, *, task_id: str, gate_id: str, owner_id: str, workspace_id: str) -> str:
        with self._durable._connect() as conn:
            with conn.cursor() as cur:
                self._scope_task(cur, task_id, owner_id, workspace_id)
                cur.execute(
                    """SELECT gate_kind FROM openair_execution_gates
                       WHERE gate_id = %s AND task_id = %s AND owner_id = %s AND workspace_id = %s""",
                    (gate_id, task_id, owner_id, workspace_id),
                )
                gate = cur.fetchone()
                if gate is None:
                    raise DurableExecutionError(404, "execution gate not found")
                return str(gate["gate_kind"])

    @staticmethod
    def _gate_decision_out(task: dict[str, Any], run: dict[str, Any], gate: dict[str, Any]) -> dict[str, Any]:
        action = str(gate["resolution_action"])
        if action == "continue_run":
            return {
                "gateId": gate["gate_id"], "taskId": task["task_id"], "runId": run["run_id"],
                "state": gate["state"], "action": action, "successorRunId": None,
                "taskStatus": task.get("status") or (task.get("payload") or {}).get("status"),
                "userMessage": "已继续研究。",
            }
        return {
            "gateId": gate["gate_id"],
            "taskId": task["task_id"],
            "runId": run["run_id"],
            "state": gate["state"],
            "action": action,
            "successorRunId": gate.get("successor_run_id"),
            "taskStatus": task.get("status") or (task.get("payload") or {}).get("status"),
            "userMessage": (
                "已开始新一轮研究。" if action == "start_revision_run" else "本轮研究已结束。"
            ),
        }

    def resolve_gate(self, *, gate_id: str, owner_id: str, workspace_id: str, resolved_by: str,
                     state: str = "resolved") -> dict[str, Any]:
        if state not in _GATE_STATES - {"pending"}:
            raise DurableExecutionError(422, "invalid_gate_resolution_state")
        resolved_by = _safe_text(resolved_by, "resolved_by", required=True, limit=240)
        with self._durable._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT run_id FROM openair_execution_gates WHERE gate_id = %s", (gate_id,))
                found = cur.fetchone()
                if not found:
                    raise DurableExecutionError(404, "execution gate not found")
                scope = self._scope_run(cur, found["run_id"], owner_id, workspace_id)
                cur.execute("SELECT * FROM openair_execution_gates WHERE gate_id = %s FOR UPDATE", (gate_id,))
                gate = cur.fetchone()
                if gate["gate_kind"] == "artifact_review":
                    raise DurableExecutionError(409, "artifact_review_requires_explicit_decision")
                if gate["gate_kind"] == "execution_policy":
                    raise DurableExecutionError(409, "execution_policy_requires_explicit_decision")
                if gate["state"] == state:
                    if gate["resolved_by"] != resolved_by:
                        raise DurableExecutionError(409, "gate_resolution_conflict")
                    return gate
                if gate["state"] != "pending":
                    raise DurableExecutionError(409, "terminal_execution_gate")
                cur.execute(
                    """UPDATE openair_execution_gates
                       SET state = %s, resolution_action = 'legacy_resolved',
                           resolved_by = %s, resolved_at = NOW()
                       WHERE gate_id = %s RETURNING *""",
                    (state, resolved_by, gate_id),
                )
                return cur.fetchone()

    def list_gates(self, *, run_id: str, owner_id: str, workspace_id: str) -> list[dict[str, Any]]:
        with self._durable._connect() as conn:
            with conn.cursor() as cur:
                self._scope_run(cur, run_id, owner_id, workspace_id)
                cur.execute(
                    "SELECT * FROM openair_execution_gates WHERE run_id = %s ORDER BY created_at, gate_id",
                    (run_id,),
                )
                return cur.fetchall()

    def list_handoffs(self, *, run_id: str, owner_id: str, workspace_id: str) -> list[dict[str, Any]]:
        with self._durable._connect() as conn:
            with conn.cursor() as cur:
                self._scope_run(cur, run_id, owner_id, workspace_id)
                cur.execute(
                    "SELECT * FROM openair_execution_handoffs WHERE run_id = %s ORDER BY created_at, handoff_id",
                    (run_id,),
                )
                return cur.fetchall()

    def list_projection(self, *, task_id: str, owner_id: str, workspace_id: str) -> dict[str, Any]:
        """Build a read-only, human-facing projection from durable facts."""
        with self._durable._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT task_id, revision FROM openair_tasks WHERE task_id = %s AND owner_id = %s AND workspace_id = %s AND status <> 'deleted'",
                    (task_id, owner_id, workspace_id),
                )
                task = cur.fetchone()
                if not task:
                    raise DurableExecutionError(404, "task not found")
                task_revision = int(task["revision"])
                cur.execute(
                    """SELECT * FROM openair_execution_runs
                       WHERE task_id = %s AND owner_id = %s AND workspace_id = %s
                       ORDER BY run_number DESC LIMIT 1""",
                    (task_id, owner_id, workspace_id),
                )
                run = cur.fetchone()
                if not run:
                    return _build_safe_task_projection(
                        task_id=task_id,
                        revision=task_revision,
                        run=None,
                        units=[],
                        calls=[],
                        handoffs=[],
                        artifacts=[],
                        gates=[],
                    )
                run_id = run["run_id"]
                cur.execute(
                    """SELECT * FROM openair_execution_work_units
                       WHERE run_id = %s AND owner_id = %s AND workspace_id = %s ORDER BY sequence""",
                    (run_id, owner_id, workspace_id),
                )
                units = cur.fetchall()
                cur.execute(
                    """SELECT * FROM openair_execution_capability_calls
                       WHERE run_id = %s AND work_unit_id IS NOT NULL ORDER BY created_at, call_id""",
                    (run_id,),
                )
                calls = cur.fetchall()
                cur.execute(
                    "SELECT * FROM openair_execution_handoffs WHERE run_id = %s ORDER BY created_at, handoff_id",
                    (run_id,),
                )
                handoffs = cur.fetchall()
                cur.execute(
                    """SELECT gate_id, gate_kind, state, reason, artifact_refs, created_at
                       FROM openair_execution_gates WHERE run_id = %s ORDER BY created_at, gate_id""",
                    (run_id,),
                )
                gates = cur.fetchall()
                cur.execute(
                    """SELECT artifact_id, title, kind, mime_type, metadata, source_mode,
                              source_capability_call_id, created_at
                       FROM openair_artifacts
                       WHERE source_execution_run_id = %s AND owner_id = %s AND workspace_id = %s
                         AND source_task_id = %s ORDER BY created_at, artifact_id""",
                    (run_id, owner_id, workspace_id, task_id),
                )
                artifacts = cur.fetchall()

        return _build_safe_task_projection(
            task_id=task_id,
            revision=task_revision,
            run=run,
            units=units,
            calls=calls,
            handoffs=handoffs,
            artifacts=artifacts,
            gates=gates,
        )


def _projection_time(value: Any) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else str(value or "")


def _projection_status(state: Any, failure_classification: Any = None) -> str:
    if state == "unknown" or failure_classification == "external_result_unknown":
        return "external_result_unknown"
    if state in {"cancelled", "cancel_requested"} or failure_classification == "stopped_by_user":
        return "stopped_by_user"
    if state == "awaiting_human":
        return "awaiting_user_confirmation"
    return {
        "queued": "preparing",
        "leased": "preparing",
        "running": "executing",
        "succeeded": "completed",
        "failed": "failed",
        "blocked": "blocked",
    }.get(str(state or ""), str(state or "not_started"))


def _build_safe_task_projection(
    *, task_id: str, revision: int, run: dict[str, Any] | None, units: list[dict[str, Any]],
    calls: list[dict[str, Any]], handoffs: list[dict[str, Any]], artifacts: list[dict[str, Any]],
    gates: list[dict[str, Any]],
) -> dict[str, Any]:
    """Pure projection builder: no cache, event replay, or mutable UI state."""
    if run is None:
        return {
            "taskId": task_id,
            "revision": revision,
            "status": "not_started",
            "statusLabel": "任务尚未开始",
            "collaboration": {"actual": [], "handoffs": [], "needsUserDecision": False, "pendingDecisions": []},
            "progress": {"status": "not_started", "stages": []},
            "resources": {"skills": [], "materials": [], "sources": [], "files": []},
            "artifacts": {"items": [], "confirmableCount": 0},
            "lifecycleArtifacts": [],
            "entries": [],
        }

    artifact_by_id = {str(row["artifact_id"]): row for row in artifacts}
    calls_by_unit: dict[str, list[dict[str, Any]]] = {}
    calls_by_id: dict[str, dict[str, Any]] = {}
    for call in calls:
        calls_by_unit.setdefault(str(call.get("work_unit_id") or ""), []).append(call)
        calls_by_id[str(call.get("call_id") or "")] = call
    handoff_by_target = {str(row["to_work_unit_id"]): row for row in handoffs}
    agents = _registered_agents(unit.get("agent_id") for unit in units)
    visible_artifact_ids = {
        str(artifact.get("artifact_id") or "")
        for artifact in artifacts
        if _artifact_is_live_confirmed_run(
            artifact,
            str((artifact.get("metadata") or {}).get("artifactState") or "needs_review")
            if isinstance(artifact.get("metadata"), dict) else "needs_review",
            artifact.get("metadata"),
            calls_by_id.get(str(artifact.get("source_capability_call_id") or "")),
        )
    }

    def artifact_refs(values: Any) -> list[dict[str, Any]]:
        return [
            {"id": str(ref), "title": _safe_projection_text((artifact_by_id.get(str(ref)) or {}).get("title"), "研究产物")}
            for ref in list(values or []) if str(ref) in visible_artifact_ids
        ]

    actual: list[dict[str, Any]] = []
    compatibility_entries: list[dict[str, Any]] = []
    actual_skills: list[dict[str, Any]] = []
    nodes_by_unit_id: dict[str, dict[str, Any]] = {}
    supporting_by_parent: dict[str, list[dict[str, Any]]] = {}
    for unit in units:
        unit_id = str(unit["work_unit_id"])
        agent = agents.get(str(unit.get("agent_id") or ""), _neutral_agent())
        unit_skills: list[dict[str, Any]] = []
        for call in calls_by_unit.get(unit_id, []):
            if call.get("invocation_kind") != "skill" or not call.get("external_action_started"):
                continue
            skill = {
                "callId": str(call.get("call_id") or ""),
                "skillId": str(call.get("skill_id") or ""),
                "agentId": str(unit.get("agent_id") or ""),
                "agentName": agent["name"],
                "agentKind": agent["kind"],
                "knownAgent": agent["known"],
                "status": _projection_status(call.get("state"), call.get("failure_classification")),
                "purpose": _safe_projection_text(unit.get("decision_summary") or unit.get("objective") or unit.get("title")),
                "summary": _safe_projection_text(call.get("result_summary")),
                "startedAt": _projection_time(call.get("started_at")),
                "completedAt": _projection_time(call.get("ended_at")),
                "outputArtifactRefs": artifact_refs(call.get("output_artifact_refs")),
            }
            unit_skills.append(skill)
            actual_skills.append(skill)
        handoff = handoff_by_target.get(unit_id)
        safe_handoff = ({
            "handoffId": str(handoff.get("handoff_id") or ""),
            "fromWorkUnitId": str(handoff.get("from_work_unit_id") or ""),
            "toWorkUnitId": str(handoff.get("to_work_unit_id") or ""),
            "fromAgentId": str(handoff.get("from_agent_id") or ""),
            "fromAgentName": agents.get(str(handoff.get("from_agent_id") or ""), _neutral_agent())["name"],
            "toAgentId": str(handoff.get("to_agent_id") or ""),
            "toAgentName": agents.get(str(handoff.get("to_agent_id") or ""), _neutral_agent())["name"],
            "summary": _safe_projection_text(handoff.get("summary")),
            "artifactRefs": artifact_refs(handoff.get("output_artifact_refs")),
            "occurredAt": _projection_time(handoff.get("created_at")),
        } if handoff else None)
        status = _projection_status(unit.get("state"))
        node = {
            "workUnitId": unit_id,
            "sequence": int(unit.get("sequence") or 0),
            "agentId": str(unit.get("agent_id") or ""),
            "agentName": agent["name"],
            "agentKind": agent["kind"],
            "knownAgent": agent["known"],
            "visibility": str(unit.get("visibility") or "main"),
            "isMember": agent["kind"] in {"professional", "general"},
            "title": _safe_projection_text(unit.get("title"), "研究工作"),
            "objective": _safe_projection_text(unit.get("objective")),
            "status": status,
            "summary": _safe_projection_text(unit.get("summary")),
            "stageKey": unit.get("stage_key"),
            "substageKey": unit.get("substage_key"),
            "assignedAt": _projection_time(unit.get("created_at")),
            "startedAt": _projection_time(unit.get("started_at")),
            "completedAt": _projection_time(unit.get("ended_at")),
            "skillCalls": unit_skills,
            "inputArtifactRefs": artifact_refs(unit.get("input_artifact_refs")),
            "outputArtifactRefs": artifact_refs(unit.get("output_artifact_refs")),
            "handoff": safe_handoff,
            "supportingActivities": [],
        }
        nodes_by_unit_id[unit_id] = node
        if agent["kind"] == "support":
            parent_id = str(unit.get("parent_work_unit_id") or "")
            if parent_id:
                supporting_by_parent.setdefault(parent_id, []).append({
                    "agentName": agent["name"], "title": node["title"], "status": status,
                    "summary": node["summary"], "skillCalls": unit_skills,
                })
        else:
            actual.append(node)
        compatibility_entries.append({
            "id": unit_id, "sequence": node["sequence"], "kind": str(unit.get("kind") or "specialist"),
            "occurredAt": node["startedAt"] or node["assignedAt"], "status": status,
            "assignedAt": node["assignedAt"], "startedAt": node["startedAt"], "completedAt": node["completedAt"],
            "visibility": node["visibility"], "agentId": node["agentId"],
            "title": node["title"], "summary": node["summary"], "workUnitId": unit_id,
            "decisionSummary": _safe_projection_text(unit.get("decision_summary") or unit.get("objective")),
            "inputMemoryRefs": [{"id": str(ref), "title": None} for ref in list(unit.get("input_memory_refs") or [])],
            "inputArtifactRefs": node["inputArtifactRefs"], "outputArtifactRefs": node["outputArtifactRefs"],
            "skillCalls": [
                {**skill, "endedAt": skill["completedAt"]} for skill in unit_skills
            ],
            "handoff": safe_handoff,
        })

    for parent_id, activities in supporting_by_parent.items():
        parent = nodes_by_unit_id.get(parent_id)
        if parent is not None:
            parent["supportingActivities"] = activities

    run_status = _run_projection_status(run, calls)
    pending_decisions = [
        {
            "gateId": str(gate.get("gate_id") or ""),
            "kind": str(gate.get("gate_kind") or "confirmation"),
            "reason": _safe_projection_text(gate.get("reason"), "需要你确认后继续"),
            "artifactRefs": artifact_refs(gate.get("artifact_refs")),
            "createdAt": _projection_time(gate.get("created_at")),
            "allowedActions": (
                ["start_revision_run", "terminate_run"]
                if gate.get("gate_kind") == "artifact_review"
                else ["continue_run", "terminate_run"] if gate.get("gate_kind") == "execution_policy" else []
            ),
        }
        for gate in gates if gate.get("state") == "pending"
    ]
    artifact_items: list[dict[str, Any]] = []
    lifecycle_artifacts: list[dict[str, Any]] = []
    for artifact in artifacts:
        metadata = artifact.get("metadata") or {}
        state = str(metadata.get("artifactState") or "needs_review") if isinstance(metadata, dict) else "needs_review"
        if artifact.get("source_mode") == "live" and artifact.get("kind") != "research_start_draft":
            source_call = calls_by_id.get(str(artifact.get("source_capability_call_id") or "")) or {}
            source_work_unit_id = str(source_call.get("work_unit_id") or "")
            source_node = nodes_by_unit_id.get(source_work_unit_id) or {}
            lifecycle_artifacts.append({
                "artifactId": str(artifact.get("artifact_id") or ""),
                "sourceCapabilityCallId": str(artifact.get("source_capability_call_id") or ""),
                "workUnitId": source_work_unit_id,
                "agentId": str(source_node.get("agentId") or ""),
                "title": _safe_projection_text(artifact.get("title"), "Research artifact"),
                "status": "confirmed" if str(artifact.get("artifact_id") or "") in visible_artifact_ids else _safe_artifact_status(state),
                "createdAt": _projection_time(artifact.get("created_at")),
            })
        if str(artifact.get("artifact_id") or "") not in visible_artifact_ids:
            continue
        artifact_items.append({
            "artifactId": str(artifact.get("artifact_id") or ""),
            "sourceCapabilityCallId": str(artifact.get("source_capability_call_id") or ""),
            "title": _safe_projection_text(artifact.get("title"), "研究产物"),
            "kind": str(artifact.get("kind") or "research_output"),
            "format": str(artifact.get("mime_type") or ""),
            "status": "confirmed",
            "confirmable": True,
            "provenance": "live_confirmed_run",
            "createdAt": _projection_time(artifact.get("created_at")),
        })

    failure = _failure_projection(
        run_status=run_status,
        run=run,
        units=units,
        artifacts=artifact_items,
        gates=gates,
    )
    projection = {
        "taskId": task_id,
        "runId": str(run.get("run_id") or ""),
        "runCreatedAt": _projection_time(run.get("created_at")),
        "runStartedAt": _projection_time(run.get("started_at")),
        "runEndedAt": _projection_time(run.get("ended_at")),
        "revision": revision,
        "status": run_status,
        "statusLabel": _status_label(run_status),
        "collaboration": {
            "actual": actual,
            "handoffs": [node["handoff"] for node in actual if node.get("handoff")],
            "needsUserDecision": bool(pending_decisions),
            "pendingDecisions": pending_decisions,
        },
        "progress": {
            "status": run_status,
            "stages": [
                {"sequence": node["sequence"], "stageKey": node["stageKey"], "substageKey": node["substageKey"],
                 "agentId": node["agentId"], "status": node["status"]}
                for node in actual
            ],
        },
        "resources": {"skills": actual_skills, "materials": [], "sources": [], "files": []},
        "artifacts": {"items": artifact_items, "confirmableCount": sum(1 for item in artifact_items if item["confirmable"])},
        "lifecycleArtifacts": lifecycle_artifacts,
        "entries": compatibility_entries,
    }
    if failure is not None:
        projection["failure"] = failure
    return projection


def _failure_projection(
    *, run_status: str, run: dict[str, Any], units: list[dict[str, Any]],
    artifacts: list[dict[str, Any]], gates: list[dict[str, Any]],
) -> dict[str, str] | None:
    """Expose terminal failure facts without replay, retry, or execution detail."""
    if run_status not in {"failed", "external_result_unknown"}:
        return None

    failed_unit = next(
        (unit for unit in units if _projection_status(unit.get("state")) in {"failed", "external_result_unknown"}),
        None,
    )
    failed_step = "Recorded work step"
    if failed_unit is not None:
        for value in (failed_unit.get("title"), failed_unit.get("substage_key"), failed_unit.get("stage_key")):
            safe_value = _safe_failure_step(value)
            if safe_value:
                failed_step = safe_value
                break

    external_started = bool(run.get("external_action_started"))
    if not external_started:
        external_action = "No external action was started."
    elif run_status == "external_result_unknown":
        external_action = "An external action was started. Its final result could not be confirmed."
    else:
        external_action = "At least one external action was started."

    if artifacts:
        retention = "Confirmed artifacts are retained. Unconfirmed or pending results are not available for use."
    else:
        retention = "No confirmed artifacts are retained. Unconfirmed or pending results are not available for use."
    safe_next_step = (
        "Review the existing confirmation requirement before taking another action."
        if any(gate.get("state") == "pending" for gate in gates)
        else "Stop here and check the recorded status with a person before taking another action."
    )
    return {
        "failedStep": failed_step,
        "externalAction": external_action,
        "resultTrustAndRetention": retention,
        "safeNextStep": safe_next_step,
    }


def _safe_failure_step(value: Any) -> str:
    """Failure steps may name work only; they never repeat execution diagnostics."""
    text = str(value or "").strip()
    if not text or _FAILURE_STEP_INTERNAL_DETAIL.search(text):
        return ""
    return _safe_projection_text(text)


def _run_projection_status(run: dict[str, Any], calls: list[dict[str, Any]]) -> str:
    failure = run.get("failure_classification")
    state = run.get("state")
    if failure == "external_result_unknown" or state == "abandoned":
        return "external_result_unknown"
    if failure == "stopped_by_user" or state in {"cancelled", "cancel_requested"}:
        return "stopped_by_user"
    if state == "awaiting_human":
        return "awaiting_user_confirmation"
    if state == "succeeded":
        return "completed"
    if state == "failed":
        return "failed"
    if any(call.get("external_action_started") and call.get("state") not in {"succeeded", "failed", "blocked", "cancelled", "abandoned", "unknown"} for call in calls):
        return "executing"
    if state in {"queued", "leased"}:
        return "preparing"
    return _projection_status(state, failure)


def _registered_agents(agent_ids: Any) -> dict[str, dict[str, Any]]:
    from app.modules.agent_registry import load_agent_registry

    registry = load_agent_registry()
    agents: dict[str, dict[str, Any]] = {}
    for value in agent_ids:
        agent_id = str(value or "")
        agent = registry.get(agent_id)
        if agent:
            agents[agent_id] = {
                "name": agent.displayName,
                "kind": "general" if agent_id in _GENERAL_AGENT_IDS else "professional",
                "known": True,
            }
        elif agent_id in _SUPPORT_AGENT_IDS:
            agents[agent_id] = {"name": _support_agent_name(agent_id), "kind": "support", "known": True}
        elif agent_id in _GENERAL_AGENT_IDS:
            agents[agent_id] = {"name": "通用研究 智能体", "kind": "general", "known": True}
        else:
            agents[agent_id] = _neutral_agent()
    return agents


def _neutral_agent() -> dict[str, Any]:
    return {"name": "研究协作记录", "kind": "neutral", "known": False}


def _support_agent_name(agent_id: str) -> str:
    return {
        "openair_summary_agent": "总结 智能体",
        "openair_review_agent": "复核 智能体",
        "openair_presentation_guidance_agent": "汇报指导 智能体",
    }[agent_id]


def _safe_projection_text(value: Any, fallback: str = "") -> str:
    """Retain concise product facts while excluding execution implementation detail."""
    text = str(value or "").strip()
    if not text:
        return fallback
    if text.startswith(_PERSISTED_SKILL_REQUEST_PREFIXES):
        return fallback
    sentences = re.split(r"(?<=[.!?。！？；;])\s*", text)
    safe = [sentence.strip() for sentence in sentences if sentence.strip() and not _INTERNAL_EXECUTION_DETAIL.search(sentence)]
    return " ".join(safe)[:1_000] or fallback


def _artifact_is_live_confirmed_run(
    artifact: dict[str, Any], state: str, metadata: Any, source_call: dict[str, Any] | None,
) -> bool:
    kind = str(artifact.get("kind") or "")
    return bool(
        artifact.get("source_mode") == "live"
        and kind != "research_start_draft"
        and state == "usable"
        and _artifact_is_confirmed(metadata)
        and source_call
        and source_call.get("external_action_started")
    )


def _safe_artifact_status(state: str) -> str:
    if state == "usable":
        return "usable_unconfirmed"
    return state if state in {"needs_review", "needs_revision", "needs_input", "failed", "partial_success"} else "not_confirmable"


def _artifact_is_confirmed(metadata: Any) -> bool:
    if not isinstance(metadata, dict):
        return False
    if metadata.get("confirmedAt") and metadata.get("confirmedBy"):
        return True
    gate = metadata.get("humanGate")
    return bool(
        isinstance(gate, dict)
        and gate.get("canUseDownstream") is True
        and metadata.get("reviewedAt")
        and metadata.get("reviewedBy")
    )


def _status_label(status: str) -> str:
    return {
        "not_started": "任务尚未开始", "preparing": "正在准备研究", "executing": "研究正在进行",
        "completed": "研究已完成", "stopped_by_user": "已按你的请求停止",
        "external_result_unknown": "外部执行结果未知，未自动重放", "blocked": "等待你确认",
        "awaiting_user_confirmation": "等待你确认",
        "failed": "本项研究未完成",
    }.get(status, "研究状态已更新")
