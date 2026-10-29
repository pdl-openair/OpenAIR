"""Pure, user-safe projections for durable Research Start rows."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from app.modules.research_start_thinking import project_public_thoughts

COMPLETED_NON_TASK_OUTCOMES = frozenset(
    {"needs_clarification", "instant_answer", "lightweight_assist"}
)
REQUEST_TERMINAL_STATES = frozenset({"succeeded", "failed", "unknown"})
QUEUED_STALE_SECONDS = 60
UNKNOWN_USER_MESSAGE = (
    "本次研究准备结果暂时无法确认。"
    "已保存的思考和进度会保留，当前不能安全重新尝试。"
)


def _json(value: Mapping[str, Any] | None) -> str:
    return json.dumps(
        value or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _row(value: Mapping[str, Any] | None) -> dict[str, Any] | None:
    return dict(value) if value is not None else None


def _waiting_phase(session_state: str, attempt_phase: str | None) -> str:
    if session_state == "completed" or attempt_phase == "research_planner":
        return "planning"
    return "understanding"


def _mapping(value: Any) -> Mapping[str, Any] | None:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return None
    return value if isinstance(value, Mapping) else None


def _iso_instant(value: Any) -> str | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _public_text(value: Any, *, max_length: int = 240) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.split()).strip()
    if not normalized:
        return None
    return normalized[:max_length]


def _planning_key_results(attempt_result: Any) -> dict[str, Any] | None:
    result = _mapping(attempt_result)
    if result is None:
        return None
    proposal = _mapping(result.get("proposal"))
    if proposal is None and isinstance(result.get("instances"), list):
        proposal = result
    if proposal is None:
        return None

    instances = proposal.get("instances")
    instance_rows = (
        [row for row in instances if isinstance(row, Mapping)]
        if isinstance(instances, list)
        else []
    )
    objectives = [
        value for row in instance_rows if (value := _public_text(row.get("objective"))) is not None
    ][:6]
    expected_artifact_types: list[str] = []
    for row in instance_rows:
        values = row.get("expectedArtifactTypes")
        if not isinstance(values, list):
            continue
        for value in values:
            public_value = _public_text(value, max_length=120)
            if public_value is not None and public_value not in expected_artifact_types:
                expected_artifact_types.append(public_value)
            if len(expected_artifact_types) >= 12:
                break
        if len(expected_artifact_types) >= 12:
            break
    understanding = _mapping(proposal.get("understanding")) or {}
    research_questions = [
        public_value
        for value in understanding.get("primaryResearchQuestions", [])
        if (public_value := _public_text(value, max_length=240)) is not None
    ][:6]
    target_outputs = [
        public_value
        for value in understanding.get("targetOutputs", [])
        if (public_value := _public_text(value, max_length=160)) is not None
    ][:8]
    planning_rationale = _public_text(understanding.get("planningRationale"), max_length=480)
    planning_mode = proposal.get("planningMode")
    if planning_mode not in {"lightweight", "partial", "sdr"}:
        planning_mode = None
    public = {
        "planningMode": planning_mode if isinstance(planning_mode, str) and planning_mode else None,
        "substageCount": len(instance_rows),
        "researchQuestions": research_questions,
        "objectives": objectives,
        "targetOutputs": target_outputs,
        "expectedArtifactTypes": expected_artifact_types,
        "planningRationale": planning_rationale,
    }
    return (
        public
        if any(
            (
                public["planningMode"],
                public["substageCount"],
                research_questions,
                objectives,
                target_outputs,
                planning_rationale,
            )
        )
        else None
    )


def _planning_progress(row: Mapping[str, Any]) -> dict[str, Any]:
    session_state = str(row.get("state") or "running")
    request_state = str(row.get("request_state") or "queued")
    attempt_phase = row.get("attempt_phase")
    attempt_state = row.get("attempt_state")
    attempt_ordinal = row.get("attempt_ordinal")
    ordinal = int(attempt_ordinal) if isinstance(attempt_ordinal, int) else None

    if session_state == "completed":
        code = "design_ready"
        message = "研究规划已通过校验，可以查看并确认。"
    elif session_state == "failed":
        code = "failed"
        message = str(row.get("user_message") or "研究规划未能完成，请检查输入后重试。")
    elif session_state == "external_result_unknown":
        code = "external_result_unknown"
        message = UNKNOWN_USER_MESSAGE
    elif attempt_phase == "research_planner":
        if attempt_state == "succeeded":
            code = "compiling_design"
            message = "语义规划已返回，服务端正在编译并核验研究合同。"
        elif (ordinal or 0) > 1 or attempt_state == "failed":
            code = "repairing_design"
            message = "初版规划未通过校验，正在自动修正研究路线。"
        else:
            code = "planning_design"
            message = "正在理解研究目标、研究范围、约束和必要工作项。"
    elif request_state == "leased":
        code = "worker_assigned"
        message = "系统已接手任务，正在准备研究规划。"
    else:
        request_age = row.get("request_age_seconds")
        request_age_seconds = (
            max(0, int(request_age)) if isinstance(request_age, (int, float)) else 0
        )
        if request_age_seconds < QUEUED_STALE_SECONDS:
            code = "still_queued"
            message = "研究任务已进入可靠队列，正在等待规划 Worker 接手。"
        elif row.get("has_live_research_start_lease") is True:
            code = "queued_stale"
            message = "研究任务仍在队列中，但等待时间已超出正常范围。"
        else:
            code = "worker_unavailable"
            message = "当前没有可确认的规划 Worker 正在处理队列，请稍后重试。"

    elapsed = row.get("elapsed_seconds")
    elapsed_seconds = max(0, int(elapsed)) if isinstance(elapsed, (int, float)) else 0
    updated_at = (
        row.get("attempt_finished_at")
        or row.get("attempt_started_at")
        or row.get("request_updated_at")
        or row.get("updated_at")
        or row.get("created_at")
    )
    key_results = None
    if attempt_phase == "research_planner" and attempt_state == "succeeded":
        key_results = _planning_key_results(row.get("attempt_result_payload"))
    progress = {
        "code": code,
        "message": message,
        "elapsedSeconds": elapsed_seconds,
        "startedAt": _iso_instant(row.get("created_at")),
        "updatedAt": _iso_instant(updated_at),
        "attemptOrdinal": ordinal,
        "repairRound": max(0, (ordinal or 1) - 1) if attempt_phase == "research_planner" else 0,
        "keyResults": key_results,
    }
    thoughts = project_public_thoughts(row.get("thinking_transcript"))
    if thoughts:
        progress["thoughts"] = thoughts
    return progress


def _session_public(row: Mapping[str, Any], *, attempt_phase: str | None = None) -> dict[str, Any]:
    result = row.get("result_payload")
    # Older completed non-task results predate the durable result contract. Project
    # their immutable session identity at read time without changing persisted data.
    if (
        row.get("state") == "completed"
        and isinstance(result, Mapping)
        and result.get("outcome") in COMPLETED_NON_TASK_OUTCOMES
        and "taskId" not in result
        and "sessionId" not in result
    ):
        result = {**result, "sessionId": row["session_id"]}
    request_state = row.get("request_state")
    progress = _planning_progress({**dict(row), "attempt_phase": attempt_phase})
    projected_status = row["state"]
    false_running = bool(row.get("state") == "running" and request_state in REQUEST_TERMINAL_STATES)
    if row.get("state") == "external_result_unknown":
        projected_status = "external_result_unknown"
    elif false_running:
        # Fail closed at the public boundary: a terminal Request cannot support
        # a claim that preparation is actively running. Repository recovery or
        # an authoritative Task receipt must establish the next public state.
        projected_status = "failed"
        progress = {
            **progress,
            "code": "responsibility_gap",
            "message": "研究准备责任暂时无法确认，系统已停止显示运行状态。",
        }
    public = {
        "sessionId": row["session_id"],
        "status": projected_status,
        "currentPhase": _waiting_phase(row["state"], attempt_phase),
        "reservedTaskId": row.get("reserved_task_id"),
        "planningProgress": progress,
        "result": result,
        "userMessage": row.get("user_message"),
    }
    if row.get("state") == "external_result_unknown":
        public.update(
            userMessage=UNKNOWN_USER_MESSAGE,
            recoveryReason="external_result_unknown",
            # Physical-result uncertainty is never automatically replayed.
            # The explicit retry/reconciliation entry point owns any resume.
            automaticRetryAllowed=False,
        )
    elif false_running:
        public.update(
            recoveryReason="responsibility_gap",
            automaticRetryAllowed=False,
        )
    elif (
        row.get("state") == "running"
        and request_state == "queued"
        and isinstance(row.get("request_age_seconds"), (int, float))
    ):
        recovery_reason = str(progress["code"])
        public.update(
            recoveryReason=recovery_reason,
            retryAfter=(
                3
                if recovery_reason == "still_queued"
                else 10
                if recovery_reason == "worker_unavailable"
                else 5
            ),
            automaticRetryAllowed=True,
        )
    if row.get("catalog_intent_id") is not None:
        public.update(
            {
                "catalogIntentId": row["catalog_intent_id"],
                "selectedResourceId": row["selected_resource_id"],
                "selectedResourceVersion": row["selected_resource_version"],
                "selectedCapabilityId": row["selected_capability_id"],
            }
        )
    return public


def _catalog_session_public(session: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "sessionId": session["session_id"],
        "catalogIntentId": session["catalog_intent_id"],
        "selectedResourceId": session["selected_resource_id"],
        "selectedResourceVersion": session["selected_resource_version"],
        "selectedCapabilityId": session["selected_capability_id"],
    }


def _reserve_public(
    outcome: str, session: Mapping[str, Any], request: Mapping[str, Any]
) -> dict[str, Any]:
    return {
        "outcome": outcome,
        "sessionId": session["session_id"],
        "requestId": request["request_id"],
        "status": request["state"],
        "reservedTaskId": session["reserved_task_id"],
        "reservedArtifactId": session["reserved_artifact_id"],
    }


def _claim_internal(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "requestId": row["request_id"],
        "sessionId": row["session_id"],
        "ownerId": row["owner_id"],
        "workspaceId": row["workspace_id"],
        "status": row["state"],
        "intent": row["request_payload"],
        "fingerprint": row["fingerprint"],
        "leaseOwner": row["lease_owner"],
        "leaseEpoch": row["lease_epoch"],
        "leaseExpiresAt": row["lease_expires_at"],
        "runtimeSourceVersion": row.get("runtime_source_version"),
    }


def _attempt_internal(row: Mapping[str, Any], *, permitted: bool, replay: bool) -> dict[str, Any]:
    return {
        "attemptId": row["attempt_id"],
        "requestId": row["request_id"],
        "phase": row["phase"],
        "ordinal": row["ordinal"],
        "status": row["state"],
        "fenceEpoch": row["fence_epoch"],
        "externalCallPermitted": permitted,
        "replay": replay,
    }
