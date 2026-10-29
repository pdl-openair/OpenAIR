"""Task-local durable projection adapter backed by CanonicalProgressReducer."""

from __future__ import annotations

import hashlib
import logging
from typing import Any, Collection, Mapping

from app.models.research_orchestration import ResearchTaskV12
from app.modules.canonical_progress_reducer import CanonicalProgressReducer

_log = logging.getLogger(__name__)


def recompute_progress_projection(task: dict, revision: int) -> dict | None:
    """Recompute the progress projection from the canonical task (pure reducer).

    Returns None if the task can't be validated as ``ResearchTaskV12`` or the
    reducer raises — callers fall back to re-stamping the cached snapshot.
    """
    try:
        model = ResearchTaskV12.model_validate(task)
    except Exception:
        return None
    try:
        recomputed = project_task_progress(model, revision=revision)
        payload = recomputed.model_dump(mode="json", by_alias=True)
        projection = payload.get("progressProjection")
        return projection if isinstance(projection, dict) else None
    except Exception:
        _log.warning(
            "progress projection recompute failed for task %s",
            task.get("taskId"),
            exc_info=True,
        )
        return None


def stamp_progress_projection(payload: dict, *, next_revision: int) -> None:
    """In-place: ensure ``payload['progressProjection']`` matches ``next_revision``.

    Used by every write path that bumps ``openair_tasks.revision``. Recomputes the
    projection content via the pure reducer so the revision stamp and the content
    stay in sync; if recomputation itself fails (legacy task that can't validate as
    ``ResearchTaskV12``), falls back to re-stamping the cached snapshot's revision
    number only. Either way the caller never fails its write because of this.

    Parity with the SSE read-side fallback in ``tasks.py`` ``durable_payload``:
    a stale stamp must reliably mean unchanged content, never a wrong one.
    """
    recomputed = recompute_progress_projection(payload, next_revision)
    if recomputed is not None:
        payload["progressProjection"] = recomputed
        return
    projection = payload.get("progressProjection")
    if isinstance(projection, dict):
        projection["revision"] = next_revision
    # else: no projection to stamp; leave as-is (legacy/validate-failing task).


def project_task_progress(task: ResearchTaskV12, *, revision: int) -> ResearchTaskV12:
    """Apply the canonical projection to a task candidate before its CAS write."""
    active_design = None
    if task.active_research_design_ref is not None:
        active_design = next(
            (
                design
                for design in task.research_design_versions
                if design.research_design_id == task.active_research_design_ref.research_design_id
                and design.version == task.active_research_design_ref.version
            ),
            None,
        )
    projection = CanonicalProgressReducer().reduce(
        revision=revision,
        active_design=active_design,
        substage_runtime_states=task.substage_runtime_states,
        work_units=task.work_units,
        capability_calls=task.capability_calls,
        artifacts=task.artifacts,
        confirmations=task.confirmations,
        task_status=task.status,
        agent_interactions=task.agent_interaction_records,
    )
    payload: dict[str, Any] = task.model_dump(mode="json", by_alias=True)
    payload["revision"] = revision
    payload["progressProjection"] = projection
    # Projection is read-only with respect to canonical Task/Substage state.
    return ResearchTaskV12.model_validate(payload)


_TERMINAL_WORK_UNIT_PHASES = {
    "completed": "completed",
    "failed": "failed",
    "blocked": "blocked",
    "blocked_recoverable": "blocked",
    "cancelled": "stopped",
    "stopped": "stopped",
    "stopped_by_user": "stopped_by_user",
    "external_result_unknown": "blocked",
}

_WORK_UNIT_HEADLINES = {
    "queued": "研究步骤已进入执行队列",
    "running": "研究步骤正在执行",
    "reviewing": "研究结果正在核查",
    "completed": "研究步骤已完成",
    "failed": "研究步骤执行失败",
    "blocked": "研究步骤当前受阻",
    "blocked_recoverable": "研究步骤当前受阻",
    "external_result_unknown": "研究执行结果仍待核查",
    "cancelled": "研究步骤已停止",
    "stopped": "研究步骤已停止",
    "stopped_by_user": "研究步骤已停止",
}

_SUBSTAGE_BLOCKER_HEADLINES = {
    "needs_input": "等待必要信息补充",
    "blocked_recoverable": "研究当前受阻，已有结果仍被保留",
    "insufficient_evidence": "当前证据仍不足，研究需要继续补证",
    "failed": "该研究步骤未能完成",
}

_TASK_TERMINAL_HEADLINES = {
    "completed": "研究任务已完成",
    "failed": "研究任务未能完成",
    "cancelled": "研究任务已停止",
}


def _payload(value: ResearchTaskV12 | Mapping[str, Any]) -> Mapping[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", by_alias=True)
    return value


def _string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(getattr(value, "value", value)).strip()
    return text or None


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(text for item in value if (text := _string(item))))


def _int_list(value: Any) -> list[int]:
    if not isinstance(value, list):
        return []
    rows: list[int] = []
    for item in value:
        try:
            number = int(item)
        except (TypeError, ValueError):
            continue
        if number >= 1 and number not in rows:
            rows.append(number)
    return rows


def _visible_artifact_refs(
    value: Any,
    visible_artifact_ids: frozenset[str],
) -> list[str]:
    return [
        artifact_id for artifact_id in _string_list(value) if artifact_id in visible_artifact_ids
    ]


def _history_event(
    *,
    event_id: str,
    headline: str,
    occurred_at: Any = None,
    event_type: str = "meaningful_progress",
    presentation_level: str = "primary",
    work_unit_id: Any = None,
    started_at: Any = None,
    activity_key: str | None = None,
    phase: str | None = None,
    artifact_refs: Any = None,
    result_refs: Any = None,
    scientific_state_versions: Any = None,
) -> dict[str, Any]:
    """Build a public process fact without copying provider/raw reasoning text."""
    return {
        "eventId": event_id,
        "eventType": event_type,
        "presentationLevel": presentation_level,
        "workUnitId": _string(work_unit_id),
        "headline": headline,
        "summary": None,
        "sequence": None,
        "occurredAt": _string(occurred_at),
        "startedAt": _string(started_at),
        "aggregateCount": None,
        "activityKey": activity_key,
        "phase": phase,
        "artifactRefs": _string_list(artifact_refs),
        "resultRefs": _string_list(result_refs),
        "scientificStateVersions": _int_list(scientific_state_versions),
    }


def project_research_process_history(
    task: ResearchTaskV12 | Mapping[str, Any],
    *,
    visible_artifact_ids: Collection[str] | None = None,
) -> list[dict[str, Any]]:
    """Rebuild user-visible research history from durable canonical task facts.

    The returned rows are a read-only projection.  They intentionally avoid raw
    Result content, prompts, provider traces, terminal reason codes, percentages,
    ETA, or inferred future steps.  Refresh therefore replays the same history
    from persisted ResearchDesign / execution / accepted-state facts instead of
    relying on an in-memory SSE buffer or creating a second research authority.
    """

    source = _payload(task)
    # Public Artifact visibility is a separate shared Authority.  Fail closed if
    # the caller has not supplied its already-authorized visible id set.
    visible_artifacts = frozenset(
        artifact_id for item in (visible_artifact_ids or ()) if (artifact_id := _string(item))
    )
    events: list[dict[str, Any]] = []

    for attempt in source.get("planningAttempts") or []:
        if not isinstance(attempt, Mapping):
            continue
        attempt_id = _string(attempt.get("planningAttemptId"))
        if not attempt_id:
            continue
        for phase, at, attempt_headline in (
            ("started", attempt.get("startedAt"), "已开始组织研究方案"),
            (
                "failed",
                attempt.get("finishedAt") if attempt.get("status") == "failed" else None,
                "本次研究方案生成未完成",
            ),
        ):
            if at:
                events.append(
                    _history_event(
                        event_id=f"durable:planning-attempt:{attempt_id}:{phase}",
                        headline=attempt_headline,
                        occurred_at=at,
                        activity_key=f"planning-attempt:{attempt_id}",
                        phase=phase,
                    )
                )

    for design in source.get("researchDesignVersions") or []:
        if not isinstance(design, Mapping):
            continue
        confirmed_at = design.get("confirmedAt")
        design_id = _string(design.get("researchDesignId")) or "design"
        raw_design_version = design.get("version")
        if raw_design_version is None:
            continue
        try:
            version = int(raw_design_version)
        except (TypeError, ValueError):
            continue
        if design.get("createdAt"):
            events.append(
                _history_event(
                    event_id=f"durable:research-design:{design_id}:v{version}:formed",
                    headline="研究方案已形成"
                    if version == 1
                    else f"研究方案已更新至第 {version} 版",
                    occurred_at=design.get("createdAt"),
                    activity_key=f"research-design:{design_id}:v{version}",
                    phase="formed",
                )
            )
        if not confirmed_at:
            continue
        # activity_key must match task_events research_design_lifecycle so
        # durable bootstrap and SSE share one public confirm outlet.
        events.append(
            _history_event(
                event_id=f"durable:research-design:{design_id}:v{version}:confirmed",
                headline=(
                    "研究方案已确认，准备进入执行"
                    if version == 1
                    else f"研究方案第 {version} 版已确认，准备进入执行"
                ),
                occurred_at=confirmed_at,
                activity_key=f"research-design:{design_id}:v{version}",
                phase="confirmed",
            )
        )

    for work in source.get("workUnits") or []:
        if not isinstance(work, Mapping):
            continue
        work_unit_id = _string(work.get("workUnitId"))
        status = _string(work.get("status"))
        if not work_unit_id or not status:
            continue
        headline = _WORK_UNIT_HEADLINES.get(status)
        if headline is None:
            continue
        work_phase = _TERMINAL_WORK_UNIT_PHASES.get(status, status)
        finished_at = work.get("finishedAt")
        started_at = work.get("startedAt")
        events.append(
            _history_event(
                event_id=f"durable:work-unit:{work_unit_id}:status",
                headline=headline,
                occurred_at=finished_at or started_at,
                work_unit_id=work_unit_id,
                started_at=started_at,
                phase=work_phase,
                artifact_refs=_visible_artifact_refs(
                    work.get("outputArtifactRefs") or work.get("artifactRefs"),
                    visible_artifacts,
                ),
            )
        )

    # One public tool-summary outlet per WorkUnit: keep the latest call, and
    # collapse earlier automatic failures into a single researcher-facing fact.
    calls_by_work: dict[str, list[Mapping[str, Any]]] = {}
    for call in source.get("capabilityCalls") or []:
        if not isinstance(call, Mapping):
            continue
        work_unit_id = _string(call.get("workUnitId"))
        call_id = _string(call.get("capabilityCallId"))
        status = _string(call.get("status"))
        if not work_unit_id or not call_id or not status:
            continue
        calls_by_work.setdefault(work_unit_id, []).append(call)

    def _call_sort_key(call: Mapping[str, Any]) -> tuple[str, str]:
        return (
            _string(call.get("finishedAt")) or _string(call.get("startedAt")) or "",
            _string(call.get("capabilityCallId")) or "",
        )

    for work_unit_id, calls in calls_by_work.items():
        ordered = sorted(calls, key=_call_sort_key)
        prior_failures = [call for call in ordered[:-1] if _string(call.get("status")) == "failed"]
        if len(prior_failures) >= 1:
            first = prior_failures[0]
            last_prior = prior_failures[-1]
            events.append(
                _history_event(
                    event_id=f"durable:capability-call:{work_unit_id}:prior-failures",
                    event_type="research_tool_summary",
                    presentation_level="detail",
                    headline=(
                        f"系统已自动重试 {len(prior_failures)} 次，仍未成功"
                        if len(prior_failures) > 1
                        else "系统已自动重试一次，仍未成功"
                    ),
                    occurred_at=last_prior.get("finishedAt") or last_prior.get("startedAt"),
                    work_unit_id=work_unit_id,
                    started_at=first.get("startedAt") or first.get("finishedAt"),
                    activity_key=f"capability-call-prior-failures:{work_unit_id}",
                    phase="failed",
                )
            )
        latest = ordered[-1]
        call_id = _string(latest.get("capabilityCallId"))
        status = _string(latest.get("status"))
        if status is None:
            continue
        call_phase = {
            "queued": "queued",
            "running": "running",
            "succeeded": "completed",
            "completed": "completed",
            "failed": "failed",
            "external_result_unknown": "blocked",
        }.get(status)
        if call_phase is None or not call_id:
            continue
        headline = {
            "queued": "研究工具已进入执行队列",
            "running": "研究工具正在执行",
            "succeeded": "研究工具执行完成",
            "completed": "研究工具执行完成",
            "failed": "研究工具执行失败",
            "external_result_unknown": "研究工具结果仍待核查",
        }[status]
        events.append(
            _history_event(
                event_id=f"durable:capability-call:{call_id}",
                event_type="research_tool_summary",
                presentation_level="detail",
                headline=headline,
                occurred_at=latest.get("finishedAt") or latest.get("startedAt"),
                work_unit_id=work_unit_id,
                started_at=latest.get("startedAt"),
                # Match task_events._activity_key() so a durable bootstrap and
                # an SSE replay identify the same capability-call fact.
                activity_key=hashlib.sha256(call_id.encode("utf-8")).hexdigest()[:24],
                phase=call_phase,
                artifact_refs=_visible_artifact_refs(
                    latest.get("outputArtifactRefs"),
                    visible_artifacts,
                ),
            )
        )

    scientific_state_times: dict[int, str] = {}
    for state in source.get("scientificStates") or []:
        if not isinstance(state, Mapping):
            continue
        raw_state_version = state.get("scientificStateVersion")
        if raw_state_version is None:
            continue
        try:
            version = int(raw_state_version)
        except (TypeError, ValueError):
            continue
        created_at = _string(state.get("createdAt"))
        if created_at:
            scientific_state_times[version] = created_at

    for result in source.get("results") or []:
        if not isinstance(result, Mapping) or result.get("checkStatus") != "passed":
            continue
        result_id = _string(result.get("resultId"))
        work_unit_id = _string(result.get("workUnitId"))
        state_versions = _int_list(result.get("scientificStateVersions"))
        # A structurally valid Result is not automatically accepted scientific
        # content.  Only the explicit persisted link into ScientificState is
        # strong enough for the process timeline to call it accepted.
        if (
            not result_id
            or not state_versions
            or any(version not in scientific_state_times for version in state_versions)
        ):
            continue
        occurred_at = max(scientific_state_times[version] for version in state_versions)
        events.append(
            _history_event(
                event_id=f"durable:result:{result_id}:accepted",
                headline="可信研究结果已更新",
                occurred_at=occurred_at,
                work_unit_id=work_unit_id,
                activity_key=f"accepted-result:{result_id}",
                phase="accepted",
                artifact_refs=_visible_artifact_refs(
                    result.get("artifactRefs"),
                    visible_artifacts,
                ),
                result_refs=[result_id],
                scientific_state_versions=state_versions,
            )
        )

    for runtime in source.get("substageRuntimeStates") or []:
        if not isinstance(runtime, Mapping):
            continue
        status = _string(runtime.get("status"))
        headline = _SUBSTAGE_BLOCKER_HEADLINES.get(status or "")
        instance_id = _string(runtime.get("substageInstanceId"))
        if headline is None or not instance_id:
            continue
        revision = runtime.get("instanceRevision", 1)
        events.append(
            _history_event(
                event_id=f"durable:substage:{instance_id}:r{revision}:{status}",
                headline=headline,
                occurred_at=runtime.get("updatedAt"),
                activity_key=f"substage:{instance_id}:r{revision}:current-blocker",
                phase="needs_input" if status == "needs_input" else "paused",
                artifact_refs=_visible_artifact_refs(
                    runtime.get("artifactRefs"),
                    visible_artifacts,
                ),
            )
        )

    task_status = _string(source.get("status"))
    task_headline = _TASK_TERMINAL_HEADLINES.get(task_status or "")
    # Plan-generation failure has no research design. Do not narrate it as a finished research task.
    if task_status == "failed" and not (source.get("researchDesignVersions") or []):
        task_headline = None
    if task_status is not None and task_headline:
        events.append(
            _history_event(
                event_id="durable:task:terminal",
                headline=task_headline,
                occurred_at=source.get("finishedAt"),
                activity_key="task-terminal",
                phase={
                    "completed": "completed",
                    "failed": "failed",
                    "cancelled": "stopped",
                }[task_status],
            )
        )

    events.sort(
        key=lambda row: (
            row.get("occurredAt") is None,
            row.get("occurredAt") or "",
            row["eventId"],
        )
    )
    # Keep durable bootstrap rows before subsequent live stream rows while
    # preserving a deterministic chronological order inside the bootstrap.
    offset = len(events)
    for index, event in enumerate(events):
        event["sequence"] = index - offset
    return events
