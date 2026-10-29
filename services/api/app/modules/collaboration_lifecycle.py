"""Stable, safe lifecycle events projected from durable collaboration facts."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from app.models.research_orchestration import ResearchTaskV12


_TERMINAL_STATUSES = {
    "completed",
    "failed",
    "blocked",
    "stopped_by_user",
    "external_result_unknown",
}
_EVENT_PRIORITY = {
    "planning_lifecycle": 5,
    "research_design_lifecycle": 7,
    "orchestrator_lifecycle": 10,
    "agent_lifecycle": 20,
    "skill_lifecycle": 30,
    "artifact_persisted": 40,
    "mandatory_review_lifecycle": 50,
    "handoff_completed": 60,
    "execution_terminal": 90,
}


def project_research_task_lifecycle(task: ResearchTaskV12) -> dict[str, Any]:
    """Adapt the canonical v1.2 aggregate to lifecycle facts without legacy tables."""
    planning_attempts = sorted(
        task.planning_attempts,
        key=lambda row: (row.started_at, row.planning_attempt_id),
    )
    research_designs = sorted(
        task.research_design_versions,
        key=lambda row: (row.created_at, row.version, row.research_design_id),
    )
    planning_projection = {
        "planningAttempts": [
            {
                "planningAttemptId": row.planning_attempt_id,
                "ordinal": index,
                "status": str(row.status),
                "startedAt": row.started_at,
                "finishedAt": row.finished_at,
            }
            for index, row in enumerate(planning_attempts, start=1)
        ],
        "researchDesigns": [
            {
                "researchDesignId": row.research_design_id,
                "version": row.version,
                "status": str(row.status),
                "createdAt": row.created_at,
                "confirmedAt": row.confirmed_at,
            }
            for row in research_designs
        ],
    }
    if not task.execution_decisions:
        return {
            "taskId": task.task_id,
            "runId": None,
            "runCreatedAt": None,
            "runStartedAt": None,
            "runEndedAt": None,
            "revision": task.revision,
            "status": (
                "awaiting_confirmation"
                if str(task.status) == "awaiting_confirmation"
                else "not_started"
            ),
            "entries": [],
            "lifecycleArtifacts": [],
            **planning_projection,
        }

    # executionDecisionId is the durable v1.2 run identity. A successor has a
    # larger decisionSequence and supersedes the source decision; never merge
    # work from multiple decisions into one user-visible lifecycle.
    selected_decision = max(
        task.execution_decisions,
        key=lambda row: (row.decision_sequence, row.decided_at, row.execution_decision_id),
    )
    run_id = selected_decision.execution_decision_id
    assignments = {row.assignment_id: row for row in selected_decision.assignments}
    selected_units = [row for row in task.work_units if row.execution_decision_id == run_id]
    selected_unit_ids = {row.work_unit_id for row in selected_units}
    calls_by_work_unit: dict[str, list[Any]] = {}
    for call in task.capability_calls:
        if call.work_unit_id not in selected_unit_ids:
            continue
        calls_by_work_unit.setdefault(call.work_unit_id, []).append(call)
    artifacts_by_work_unit: dict[str, list[Any]] = {}
    for artifact in task.artifacts:
        if artifact.work_unit_id not in selected_unit_ids:
            continue
        artifacts_by_work_unit.setdefault(artifact.work_unit_id, []).append(artifact)
    interactions_by_target: dict[str, list[Any]] = {}
    for interaction in task.agent_interaction_records:
        if interaction.execution_decision_id != run_id:
            continue
        if interaction.interaction_type == "handoff_completed" and interaction.target_work_unit_id:
            interactions_by_target.setdefault(interaction.target_work_unit_id, []).append(interaction)

    ordered_units = sorted(
        selected_units,
        key=lambda row: (
            row.dispatched_at,
            row.work_unit_id,
        ),
    )
    entries: list[dict[str, Any]] = []
    lifecycle_artifacts: list[dict[str, Any]] = []
    for sequence, unit in enumerate(ordered_units, start=1):
        assignment = assignments.get(unit.assignment_id)
        unit_calls = sorted(
            calls_by_work_unit.get(unit.work_unit_id, []),
            key=lambda row: (row.started_at, row.capability_call_id),
        )
        output_artifacts = artifacts_by_work_unit.get(unit.work_unit_id, [])
        is_review = bool(assignment is not None and str(assignment.role) == "reviewer")
        agent_id = unit.responsible_actor_ref.actor_id
        started_at = unit_calls[0].started_at if unit_calls else (
            unit.dispatched_at if str(unit.status) != "queued" else None
        )
        entry: dict[str, Any] = {
            "id": unit.work_unit_id,
            "workUnitId": unit.work_unit_id,
            "sequence": sequence,
            "kind": "review" if is_review else "specialist",
            "agentId": agent_id,
            "title": unit.substage_definition_id,
            "decisionSummary": unit.terminal_reason_code or "",
            "status": str(unit.status),
            "assignedAt": unit.frozen_at,
            "startedAt": started_at,
            "completedAt": unit.finished_at,
            "inputMemoryRefs": list(unit.input_memory_refs),
            "inputArtifactRefs": list(unit.input_artifact_refs),
            "outputArtifactRefs": [row.artifact_id for row in output_artifacts],
            "skillCalls": [
                {
                    "callId": call.capability_call_id,
                    "skillId": call.capability_id,
                    "status": str(call.status),
                    "startedAt": call.started_at,
                    "completedAt": call.finished_at,
                    "outputArtifactRefs": list(call.output_artifact_refs),
                }
                for call in unit_calls
                if str(call.capability_type) == "skill"
            ],
            "handoff": None,
        }
        handoffs = interactions_by_target.get(unit.work_unit_id, [])
        if handoffs:
            handoff = sorted(handoffs, key=lambda row: (row.created_at, row.interaction_id))[-1]
            source_unit = next(
                (row for row in selected_units if row.work_unit_id == handoff.source_work_unit_id),
                None,
            )
            entry["handoff"] = {
                "handoffId": handoff.interaction_id,
                "fromWorkUnitId": handoff.source_work_unit_id,
                "toWorkUnitId": handoff.target_work_unit_id,
                "fromAgentId": source_unit.responsible_actor_ref.actor_id if source_unit else "",
                "toAgentId": agent_id,
                "summary": handoff.summary,
                "artifactRefs": list(handoff.input_artifact_refs or handoff.output_artifact_refs),
                "occurredAt": handoff.created_at,
            }
        entries.append(entry)

        calls_by_id = {row.capability_call_id: row for row in unit_calls}
        for artifact in output_artifacts:
            source_call = calls_by_id.get(artifact.source_capability_call_id or "")
            occurred_at = (
                source_call.finished_at if source_call and source_call.finished_at
                else unit.finished_at or started_at or unit.frozen_at
            )
            lifecycle_artifacts.append({
                "artifactId": artifact.artifact_id,
                "sourceCapabilityCallId": artifact.source_capability_call_id or "",
                "workUnitId": unit.work_unit_id,
                "agentId": agent_id,
                "title": artifact.artifact_type,
                "status": str(artifact.artifact_state),
                "createdAt": occurred_at,
            })

    run_created_at = selected_decision.decided_at
    run_started_at = min(
        (row.dispatched_at for row in selected_units),
        default=None,
    )
    run_ended_at = selected_decision.closed_at or max(
        (row.finished_at for row in selected_units if row.finished_at is not None),
        default=None,
    )
    unit_statuses = {str(row.status) for row in selected_units}
    status = str(task.status)
    if "external_result_unknown" in unit_statuses:
        status = "external_result_unknown"
    elif status == "cancelled":
        status = "stopped_by_user"
    elif status == "running":
        status = "executing"

    return {
        "taskId": task.task_id,
        "runId": run_id,
        "runCreatedAt": run_created_at,
        "runStartedAt": run_started_at,
        "runEndedAt": run_ended_at,
        "revision": task.revision,
        "status": status,
        "entries": entries,
        "lifecycleArtifacts": lifecycle_artifacts,
        **planning_projection,
    }


def build_collaboration_lifecycle_events(projection: dict[str, Any]) -> list[dict[str, Any]]:
    """Return a deterministic event history without inventing execution facts.

    The projection is rebuilt from PostgreSQL on every poll. Started and terminal
    events remain derivable after state transitions, so reconnects can resume by
    stable event id instead of replaying process-local notifications.
    """
    task_id = str(projection.get("taskId") or "")
    run_id = str(projection.get("runId") or "")
    revision = projection.get("revision")
    if not task_id or isinstance(revision, bool) or not isinstance(revision, int):
        return []

    facts: list[dict[str, Any]] = []
    run_scope = f"run:{run_id}" if run_id else "pre-run"

    def add(
        event_type: str,
        identity: str,
        phase: str,
        occurred_at: Any,
        payload: dict[str, Any],
        *,
        scope_identity: str | None = None,
    ) -> None:
        timestamp = _timestamp(occurred_at)
        if not timestamp:
            return
        fact_scope = scope_identity or run_scope
        facts.append(
            {
                "id": f"lifecycle:{task_id}:{fact_scope}:{event_type}:{identity}:{phase}",
                "type": event_type,
                "taskId": task_id,
                "runId": run_id or None,
                "revision": revision,
                "occurredAt": timestamp,
                "payload": {
                    "revision": revision,
                    "runId": run_id or None,
                    "kind": event_type.removesuffix("_lifecycle"),
                    "phase": phase,
                    **payload,
                },
            }
        )

    planning_attempts = [
        row for row in projection.get("planningAttempts") or [] if isinstance(row, dict)
    ]
    for index, attempt in enumerate(planning_attempts, start=1):
        attempt_id = str(attempt.get("planningAttemptId") or f"attempt:{index}")
        ordinal = _positive_int(attempt.get("ordinal")) or index
        status = str(attempt.get("status") or "")
        base_payload = {
            "planningAttemptId": attempt_id,
            "attemptOrdinal": ordinal,
            "status": status or "started",
        }
        add(
            "planning_lifecycle",
            attempt_id,
            "started",
            attempt.get("startedAt"),
            {**base_payload, "status": "started"},
            scope_identity="planning",
        )
        if attempt.get("finishedAt") and status in {"completed", "failed"}:
            add(
                "planning_lifecycle",
                attempt_id,
                status,
                attempt.get("finishedAt"),
                base_payload,
                scope_identity="planning",
            )

    for thought in projection.get("planningThoughtHistory") or []:
        content_version = hashlib.sha256(
            str(thought.get("summary") or "").encode()
        ).hexdigest()[:16]
        add(
            "planning_thought", f"{thought['activityKey']}:{content_version}",
            thought["phase"], thought.get("occurredAt"),
            {"activityKey": thought["activityKey"], "text": thought["summary"]},
            scope_identity="planning",
        )

    research_designs = [
        row for row in projection.get("researchDesigns") or [] if isinstance(row, dict)
    ]
    for index, design in enumerate(research_designs, start=1):
        design_id = str(design.get("researchDesignId") or f"design:{index}")
        version = _positive_int(design.get("version")) or index
        identity = f"{design_id}:v{version}"
        base_payload = {
            "researchDesignId": design_id,
            "designVersion": version,
            "status": str(design.get("status") or "draft"),
        }
        add(
            "research_design_lifecycle",
            identity,
            "formed",
            design.get("createdAt"),
            base_payload,
            scope_identity="planning",
        )
        if design.get("confirmedAt"):
            add(
                "research_design_lifecycle",
                identity,
                "confirmed",
                design.get("confirmedAt"),
                {**base_payload, "status": "confirmed"},
                scope_identity="planning",
            )

    run_created_at = projection.get("runCreatedAt")
    if run_id:
        add(
            "orchestrator_lifecycle",
            run_id,
            "scheduled",
            run_created_at,
            {"status": "preparing", "summary": "Research orchestration was scheduled."},
        )
        run_started_at = projection.get("runStartedAt")
        if run_started_at:
            add(
                "orchestrator_lifecycle",
                run_id,
                "started",
                run_started_at,
                {"status": "executing", "summary": "Research orchestration started."},
            )

    entries = (
        [entry for entry in projection.get("entries") or [] if isinstance(entry, dict)]
        if run_id
        else []
    )
    artifact_source: dict[str, tuple[str, str]] = {}
    work_unit_sequence: dict[str, int] = {}
    for entry in entries:
        work_unit_id = str(entry.get("workUnitId") or entry.get("id") or "")
        if not work_unit_id:
            continue
        unit_sequence = _positive_int(entry.get("sequence"))
        work_unit_sequence[work_unit_id] = unit_sequence
        kind = str(entry.get("kind") or "specialist")
        event_type = "mandatory_review_lifecycle" if kind == "review" else "agent_lifecycle"
        agent_id = str(entry.get("agentId") or "")
        base_payload = {
            "workUnitId": work_unit_id,
            "workUnitSequence": unit_sequence,
            "agentId": agent_id,
            "status": str(entry.get("status") or "preparing"),
            "title": _text(entry.get("title")),
            "summary": _text(entry.get("decisionSummary") or entry.get("summary")),
            "inputMemoryRefs": _safe_ref_ids(entry.get("inputMemoryRefs")),
            "inputArtifactRefs": _safe_ref_ids(entry.get("inputArtifactRefs")),
            "outputArtifactRefs": _safe_ref_ids(entry.get("outputArtifactRefs")),
        }
        assigned_at = entry.get("assignedAt") or entry.get("occurredAt")
        started_at = entry.get("startedAt")
        completed_at = entry.get("completedAt")
        add(event_type, work_unit_id, "assigned", assigned_at, {**base_payload, "status": "preparing"})
        if started_at:
            add(event_type, work_unit_id, "started", started_at, {**base_payload, "status": "executing"})
        if completed_at or base_payload["status"] in _TERMINAL_STATUSES:
            add(
                event_type,
                work_unit_id,
                _terminal_phase(base_payload["status"]),
                completed_at or started_at or assigned_at,
                base_payload,
            )

        for artifact_id in base_payload["outputArtifactRefs"]:
            artifact_source.setdefault(artifact_id, (work_unit_id, agent_id))

        for index, skill in enumerate(entry.get("skillCalls") or []):
            if not isinstance(skill, dict):
                continue
            call_id = str(skill.get("callId") or f"{work_unit_id}:skill:{index}")
            skill_payload = {
                "callId": str(skill.get("callId") or ""),
                "workUnitId": work_unit_id,
                "workUnitSequence": unit_sequence,
                "agentId": agent_id,
                "skillId": str(skill.get("skillId") or ""),
                "status": str(skill.get("status") or "preparing"),
                "summary": _text(skill.get("summary") or skill.get("purpose")),
                "outputArtifactRefs": _safe_ref_ids(skill.get("outputArtifactRefs")),
            }
            skill_started_at = skill.get("startedAt")
            skill_completed_at = skill.get("completedAt") or skill.get("endedAt")
            if skill_started_at:
                add("skill_lifecycle", call_id, "started", skill_started_at, {**skill_payload, "status": "executing"})
            if skill_completed_at or skill_payload["status"] in _TERMINAL_STATUSES:
                add(
                    "skill_lifecycle",
                    call_id,
                    _terminal_phase(skill_payload["status"]),
                    skill_completed_at or skill_started_at or assigned_at,
                    skill_payload,
                )
            for artifact_id in skill_payload["outputArtifactRefs"]:
                artifact_source[artifact_id] = (work_unit_id, agent_id)

        handoff = entry.get("handoff")
        if isinstance(handoff, dict):
            from_work_unit_id = str(handoff.get("fromWorkUnitId") or "")
            to_work_unit_id = str(handoff.get("toWorkUnitId") or work_unit_id)
            handoff_id = str(handoff.get("handoffId") or f"{from_work_unit_id}:{to_work_unit_id}")
            add(
                "handoff_completed",
                handoff_id,
                "completed",
                handoff.get("occurredAt"),
                {
                    "handoffId": str(handoff.get("handoffId") or ""),
                    "fromWorkUnitId": from_work_unit_id,
                    "toWorkUnitId": to_work_unit_id,
                    "fromAgentId": str(handoff.get("fromAgentId") or ""),
                    "toAgentId": str(handoff.get("toAgentId") or ""),
                    "status": "completed",
                    "summary": _text(handoff.get("summary")),
                    "artifactRefs": _safe_ref_ids(handoff.get("artifactRefs")),
                },
            )

    lifecycle_artifacts = projection.get("lifecycleArtifacts") if run_id else []
    if run_id and not isinstance(lifecycle_artifacts, list):
        lifecycle_artifacts = projection.get("artifacts", {}).get("items", [])
    for artifact in lifecycle_artifacts:
        if not isinstance(artifact, dict):
            continue
        artifact_id = str(artifact.get("artifactId") or "")
        if not artifact_id:
            continue
        inferred_work_unit_id, inferred_agent_id = artifact_source.get(artifact_id, ("", ""))
        work_unit_id = str(artifact.get("workUnitId") or inferred_work_unit_id)
        agent_id = str(artifact.get("agentId") or inferred_agent_id)
        add(
            "artifact_persisted",
            artifact_id,
            "persisted",
            artifact.get("createdAt"),
            {
                "artifactId": artifact_id,
                "sourceCapabilityCallId": str(artifact.get("sourceCapabilityCallId") or ""),
                "workUnitId": work_unit_id,
                "workUnitSequence": work_unit_sequence.get(work_unit_id, 0),
                "agentId": agent_id,
                "status": str(artifact.get("status") or "persisted"),
                "title": _text(artifact.get("title")),
            },
        )

    run_status = str(projection.get("status") or "")
    if run_id and run_status in _TERMINAL_STATUSES:
        add(
            "execution_terminal",
            run_id,
            _terminal_phase(run_status),
            projection.get("runEndedAt") or _latest_timestamp(facts),
            {
                "status": run_status,
                "summary": _terminal_summary(run_status),
                **({"failure": projection["failure"]} if isinstance(projection.get("failure"), dict) else {}),
            },
        )

    facts.sort(
        key=lambda item: (
            lifecycle_sequence(item["occurredAt"]),
            _EVENT_PRIORITY.get(item["type"], 80),
            item["id"],
        )
    )
    for event in facts:
        # Unix microseconds remain below JavaScript's safe-integer ceiling for
        # centuries. Equal transaction timestamps are ordered by type/id while
        # retaining a stable sequence across later projection rebuilds.
        event["sequence"] = lifecycle_sequence(event["occurredAt"])
    return facts


def _timestamp(value: Any) -> str:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    text = str(value or "").strip()
    return text


def lifecycle_sequence(value: Any) -> int:
    timestamp = _timestamp(value)
    normalized = timestamp[:-1] + "+00:00" if timestamp.endswith("Z") else timestamp
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return 0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1_000_000)


def _latest_timestamp(events: list[dict[str, Any]]) -> str:
    return max((str(event.get("occurredAt") or "") for event in events), default="")


def _safe_ref_ids(value: Any) -> list[str]:
    refs: list[str] = []
    for item in value or []:
        ref = item.get("id") if isinstance(item, dict) else item
        text = str(ref or "").strip()
        if text and text not in refs:
            refs.append(text)
    return refs


def _positive_int(value: Any) -> int:
    return int(value) if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def _text(value: Any) -> str:
    return str(value or "").strip()[:1_000]


def _terminal_phase(status: str) -> str:
    return "unknown" if status == "external_result_unknown" else status


def _terminal_summary(status: str) -> str:
    return {
        "completed": "Research orchestration completed.",
        "failed": "Research orchestration failed.",
        "blocked": "Research orchestration is blocked.",
        "stopped_by_user": "Research orchestration was stopped by the user.",
        "external_result_unknown": "The external result is unknown and was not replayed.",
    }.get(status, "Research orchestration reached a terminal state.")
