"""Authority projection and fail-closed reconciliation for Workbench operations."""

from __future__ import annotations

from contextlib import contextmanager
import re
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row
from pydantic import ValidationError

from app.config import settings
from app.models.research_orchestration import ResearchTaskV12
from app.execution.research_dispatch_provider import (
    SKILL_FINALIZATION_RUNTIME_FAILED,
    SKILL_FINALIZATION_SYSTEM_RETRY_REASON,
    STRUCTURED_OUTPUT_CORRECTION_EXHAUSTED,
    STRUCTURED_OUTPUT_EXPLICIT_RETRY_REASON,
)
from app.modules.canonical_progress_reducer import is_durable_progression_pause
from app.modules.research_blocker_semantics import (
    active_user_stop_receipt,
    current_blocker_facts,
)
from app.modules.research_orchestration_repository import _extract_contract_payload


_TERMINAL_TASK_STATES = {"completed", "failed", "cancelled"}
_INPUT_ARTIFACT_STATES = {"needs_input", "insufficient_materials"}
_RUNTIME_INPUT_STATUSES = {"needs_input", "insufficient_evidence"}
_INSTANCE_GAP_ARTIFACT_STATES = {
    "needs_input",
    "insufficient_materials",
    "needs_review",
    "partial_success",
    "rejected_or_needs_revision",
    "usable",
}
@contextmanager
def _connect() -> Iterator[psycopg.Connection[Any]]:
    conn = psycopg.connect(
        f"host={settings.postgres_host} port={settings.postgres_port} "
        f"dbname={settings.postgres_database} user={settings.postgres_user}",
        password=settings.postgres_password,
        row_factory=dict_row,
    )
    try:
        yield conn
    finally:
        conn.close()


def _canonical_task(payload: dict[str, Any]) -> ResearchTaskV12 | None:
    try:
        return ResearchTaskV12.model_validate(_extract_contract_payload(payload))
    except (KeyError, TypeError, ValidationError, ValueError):
        return None


def _external_context_rows(
    *,
    task_id: str,
    owner_id: str,
    workspace_id: str,
    execution_decision_id: str,
) -> list[dict[str, Any]]:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT d.dispatch_id, d.execution_decision_id, d.work_unit_id,
                          d.status AS dispatch_status, d.remote_run_id AS dispatch_remote_run_id,
                          d.last_error_code,
                          x.capability_call_id, x.attempt_number, x.owner_id, x.workspace_id,
                          x.external_state, x.remote_run_id AS external_remote_run_id,
                          x.canonical_payload_fingerprint,
                          COALESCE(array_agg(e.evidence_id ORDER BY e.created_at, e.evidence_id)
                                   FILTER (WHERE e.evidence_id IS NOT NULL), '{}') AS evidence_refs
                     FROM openair_dispatch d
                     JOIN openair_external_execution x ON x.dispatch_id = d.dispatch_id
                     LEFT JOIN openair_external_evidence e
                       ON e.dispatch_id = d.dispatch_id
                      AND e.task_id = d.task_id
                      AND e.execution_decision_id = d.execution_decision_id
                      AND e.work_unit_id = d.work_unit_id
                      AND e.capability_call_id = x.capability_call_id
                      AND e.attempt_number = x.attempt_number
                      AND e.owner_id = x.owner_id
                      AND e.workspace_id = x.workspace_id
                    WHERE d.task_id=%s AND d.execution_decision_id=%s
                      AND x.owner_id=%s AND x.workspace_id=%s
                    GROUP BY d.dispatch_id, d.execution_decision_id, d.work_unit_id,
                             d.status, d.remote_run_id, d.last_error_code, x.capability_call_id,
                             x.attempt_number, x.owner_id, x.workspace_id,
                             x.external_state, x.remote_run_id,
                             x.canonical_payload_fingerprint
                    ORDER BY d.created_at, d.dispatch_id""",
                (task_id, execution_decision_id, owner_id, workspace_id),
            )
            return list(cur.fetchall())


def _pending_controller_command_rows(
    *,
    task_id: str,
    owner_id: str,
    workspace_id: str,
) -> list[dict[str, Any]]:
    """Read received canonical supplements without claiming or applying them."""
    progression_available = _canonical_supplement_progression_available()
    with _connect() as conn:
        with conn.cursor() as cur:
            if not progression_available:
                cur.execute(
                    """UPDATE openair_controller_commands
                          SET state='rejected',
                              terminal_reason_code='supplement_progression_service_unavailable',
                              updated_at=NOW()
                        WHERE task_id=%s AND owner_id=%s AND workspace_id=%s
                          AND lifecycle_kind='canonical_v1_2'
                          AND kind='supplement_input' AND state='received'""",
                    (task_id, owner_id, workspace_id),
                )
                cur.execute(
                    """SELECT submitted_task_revision, created_at, state, terminal_reason_code
                         FROM openair_controller_commands
                        WHERE task_id=%s AND owner_id=%s AND workspace_id=%s
                          AND lifecycle_kind='canonical_v1_2'
                          AND kind='supplement_input' AND state='rejected'
                          AND terminal_reason_code='supplement_progression_service_unavailable'
                        ORDER BY updated_at DESC, command_id DESC LIMIT 5""",
                    (task_id, owner_id, workspace_id),
                )
                rows = list(cur.fetchall())
                conn.commit()
                return rows
            cur.execute(
                """SELECT submitted_task_revision, created_at, state, terminal_reason_code
                     FROM openair_controller_commands
                    WHERE task_id=%s AND owner_id=%s AND workspace_id=%s
                      AND lifecycle_kind='canonical_v1_2'
                      AND kind='supplement_input' AND state='received'
                    ORDER BY created_at, command_id""",
                (task_id, owner_id, workspace_id),
            )
            return list(cur.fetchall())


def _string_value(value: Any) -> str:
    return str(getattr(value, "value", value))


_MISSING_INPUT_LABELS = {
    "source_records": "可追溯的来源记录",
    "source_record": "可追溯的来源记录",
    "user_materials_or_scope": "研究材料或范围说明",
    "expanded_scope_materials_or_execution": "扩大范围所需的材料或执行条件",
    "hypothesis": "待验证的研究假说",
    "outcome metric": "结果评价指标",
    "baseline/control": "对照或基线",
    "resource and safety confirmation": "资源与安全确认",
    "confirmation gates before execution": "执行前需要你确认的条件",
}

_REASON_CODE_COPY = {
    "required_input_missing": "当前步骤还缺少继续研究所需的输入材料或上游结果。",
    "budget_expansion_required": "当前研究已达到已确认的预算边界，需要你确认是否扩大预算后再继续。",
    "policy_prohibited": "当前步骤触及已确认的风险或安全边界，需要你决定是否调整范围。",
    "authorization_required": "当前步骤需要你补充授权后才能继续。",
    "needs_input": "当前研究无法安全继续，需要你补充缺失信息。",
}


def _safe_research_input_text(value: Any) -> str:
    """Keep concrete research requests in the default UI, omit diagnostics."""
    if not isinstance(value, str):
        return ""
    normalized = " ".join(value.split()).strip()
    lowered = normalized.lower()
    if not normalized or len(normalized) > 600:
        return ""
    if any(marker in lowered for marker in (
        "traceback", "stack trace", "exception:", "error_code", "errorcode",
        "terminalreasoncode", "provider_configuration", "dispatch_id", "workunitid",
        "services/api/", "site-packages/",
    )):
        return ""
    if re.search(r"(?:^|[\s(])(?:[A-Za-z]:[\\/]|/[A-Za-z0-9_.-]+/)", normalized):
        return ""
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]*_[A-Za-z0-9_.-]+", normalized):
        return ""
    return normalized


def _user_facing_missing_input(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        return ""
    mapped = _MISSING_INPUT_LABELS.get(normalized) or _MISSING_INPUT_LABELS.get(normalized.lower())
    return mapped or _safe_research_input_text(normalized)


def _format_missing_inputs(values: Any) -> str:
    if not isinstance(values, (list, tuple)):
        return ""
    labels = [
        _user_facing_missing_input(item)
        for item in values if isinstance(item, str) and item.strip()
    ]
    return "、".join([item for item in labels if item][:5])


def _reason_code_copy(reason_code: str | None) -> str:
    code = str(reason_code or "").strip()
    return _REASON_CODE_COPY.get(code) or _REASON_CODE_COPY["needs_input"]


def _instance_objective(canonical: ResearchTaskV12, instance_id: str | None) -> str | None:
    design = _active_research_design(canonical)
    if not instance_id or design is None:
        return None
    for instance in design.instances:
        if instance.substage_instance_id == instance_id:
            return _safe_research_input_text(getattr(instance, "objective", None)) or None
    return None


def _runtime_input_instance_ids(canonical: ResearchTaskV12) -> set[str]:
    return {
        fact.substage_instance_id
        for fact in current_blocker_facts(canonical)
        if fact.kind == "user_input" and fact.substage_instance_id
    }




def _work_instance_id(canonical: ResearchTaskV12, work_unit_id: str) -> str:
    for work in canonical.work_units:
        if work.work_unit_id == work_unit_id:
            return str(getattr(work, "substage_instance_id", "") or "")
    return ""


def _artifact_continue_allowed(artifact: Any) -> bool | None:
    validator = getattr(artifact, "validator_result", None)
    if not isinstance(validator, dict):
        return None
    value = validator.get("continueAllowed")
    if value is True:
        return True
    if value is False:
        return False
    return None


def _artifact_reviewer_disposition(artifact: Any) -> str | None:
    validator = getattr(artifact, "validator_result", None)
    if not isinstance(validator, dict):
        return None
    value = str(validator.get("disposition") or "").strip()
    return value or None

def _artifact_is_blocking_gap(
    canonical: ResearchTaskV12,
    artifact: Any,
    runtime_input_instances: set[str],
) -> bool:
    state = _string_value(getattr(artifact, "artifact_state", ""))
    work_unit_id = str(getattr(artifact, "work_unit_id", "") or "")
    # Keep R0-B's optional advisory entry. A coarse needs_input Artifact without
    # canonical user responsibility is not authority to request Task recovery.
    if state in _INPUT_ARTIFACT_STATES and _artifact_continue_allowed(artifact) is True:
        return True
    instance_id = _work_instance_id(canonical, work_unit_id)
    user_owned = instance_id in runtime_input_instances or any(
        fact.kind == "user_input" and fact.work_unit_id == work_unit_id
        for fact in current_blocker_facts(canonical)
    )
    return (
        user_owned
        and state in _INSTANCE_GAP_ARTIFACT_STATES
        and _artifact_continue_allowed(artifact) is not True
    )


def _gap_materials_for_instance(
    canonical: ResearchTaskV12,
    instance_id: str,
) -> tuple[str | None, list[str], str | None]:
    design = _active_research_design(canonical)
    if design is None:
        return None, [], None
    active_keys = {
        (instance.substage_instance_id, instance.instance_revision)
        for instance in design.instances if instance.substage_instance_id == instance_id
    }
    latest = max(
        (
            decision for decision in canonical.execution_decisions
            if decision.research_design_version == design.version
            and (decision.substage_instance_id, decision.substage_instance_revision) in active_keys
        ),
        key=lambda row: (row.decision_sequence, row.decided_at, row.execution_decision_id),
        default=None,
    )
    if latest is None or _string_value(latest.status) == "superseded":
        return None, [], None
    work_ids = sorted(
        work.work_unit_id for work in canonical.work_units
        if work.execution_decision_id == latest.execution_decision_id
        and work.research_design_version == design.version
        and (work.substage_instance_id, work.substage_instance_revision) in active_keys
    )
    artifact_refs: list[str] = []
    reason: str | None = None
    source_work_id: str | None = None
    for work_unit_id in work_ids:
        if reason is None:
            reason = _concrete_blocking_input_reason(canonical, work_unit_id)
            if reason:
                source_work_id = work_unit_id
        for artifact in canonical.artifacts:
            if artifact.work_unit_id != work_unit_id:
                continue
            artifact_id = str(getattr(artifact, "artifact_id", "") or "")
            if artifact_id:
                artifact_refs.append(artifact_id)
    return reason, list(dict.fromkeys(artifact_refs)), (source_work_id or next(iter(work_ids), None))


def _concrete_blocking_input_reason(canonical: ResearchTaskV12, work_unit_id: str) -> str | None:
    """Render the concrete user request already preserved by the reviewer."""
    for artifact in canonical.artifacts:
        if artifact.work_unit_id != work_unit_id:
            continue
        validator = getattr(artifact, "validator_result", None)
        if not isinstance(validator, dict) or validator.get("continueAllowed") is True:
            continue
        missing = _format_missing_inputs(
            validator.get("reviewMissingInputs") or validator.get("missingInputs")
        )
        issues = validator.get("reviewIssues") or validator.get("issues")
        if isinstance(issues, list):
            for issue in issues:
                if not isinstance(issue, dict):
                    continue
                severity = str(issue.get("severity") or "").strip().lower()
                message = _safe_research_input_text(issue.get("message"))
                if severity in {"blocker", "blocking", "critical", "error"} and message:
                    suffix = f" 当前缺少：{missing}。" if missing else ""
                    return f"{message}{suffix}这是当前步骤继续所必需的信息。"
        if missing:
            return f"当前步骤缺少：{missing}。这些信息是继续研究所必需的。"
        if validator.get("reviewVerdict") == "human_decision_required":
            basis = _safe_research_input_text(validator.get("reviewBasis"))
            if basis:
                return f"当前步骤等待你的确认：{basis}。这项确认会影响下一步研究。"
    return None


def _unavailable_input_reason(concrete_reason: str | None = None) -> str:
    explanation = f"{concrete_reason} " if concrete_reason else ""
    return (
        explanation
        + "当前步骤仍缺必要信息，但系统暂不能接收并应用这次补充；你现在无需操作。"
    )


def render_user_input_request(canonical: ResearchTaskV12, instance_id: str | None) -> str | None:
    """Render a proven current input gap without authorizing an action."""
    if not any(
        fact.kind == "user_input" and fact.substage_instance_id == instance_id
        for fact in current_blocker_facts(canonical)
    ):
        return None
    reason, _artifact_refs, _work_unit_id = _gap_materials_for_instance(
        canonical, str(instance_id or "")
    )
    return reason


def _active_research_design(canonical: ResearchTaskV12) -> Any | None:
    active_ref = getattr(canonical, "active_research_design_ref", None)
    if active_ref is None:
        return None
    return next(
        (
            design for design in canonical.research_design_versions
            if design.research_design_id == active_ref.research_design_id
            and design.version == active_ref.version
        ),
        None,
    )


def _research_intent_is_confirmed(canonical: ResearchTaskV12) -> bool:
    design = _active_research_design(canonical)
    return design is not None and _string_value(design.status) == "confirmed"


def _canonical_supplement_progression_available() -> bool:
    """Expose canonical supplement only when Line B publishes a safe trigger."""
    try:
        from app.modules.research_dispatch_service import ResearchDispatchService
        service = ResearchDispatchService()
    except Exception:
        return False
    return callable(getattr(service, "progress_controller_command", None))


def _active_stop(task_payload: dict[str, Any]) -> bool:
    return active_user_stop_receipt(
        str(task_payload.get("taskId") or ""), task_payload.get("stopReceipt")
    ) is not None


def _pending_research_work(canonical: ResearchTaskV12) -> bool:
    """Use the confirmed Design's unfinished runtime facts as resume evidence."""
    if not _research_intent_is_confirmed(canonical):
        return False
    design = _active_research_design(canonical)
    active_instances = {
        (row.substage_instance_id, row.instance_revision)
        for row in design.instances
    }
    return any(
        (runtime.substage_instance_id, runtime.instance_revision) in active_instances
        and _string_value(runtime.status) in {
            "planned", "ready", "queued", "running", "reviewing",
        }
        for runtime in canonical.substage_runtime_states
    ) or any(
        work.research_design_version == design.version
        and (work.substage_instance_id, work.substage_instance_revision) in active_instances
        and _string_value(work.status) in {"queued", "running", "reviewing"}
        for work in canonical.work_units
    )


def _supplement_input_eligibility(
    *,
    canonical: ResearchTaskV12 | None,
    task_status: str,
    execution_stopped: bool,
    progression_available: bool,
) -> tuple[str | None, str | None]:
    """Return a denial or the proven existing consumer's input target."""
    if task_status in _TERMINAL_TASK_STATES or (
        canonical is not None
        and _string_value(canonical.status) in _TERMINAL_TASK_STATES
    ):
        return "task_terminal", None
    if execution_stopped:
        return "execution_stopped", None
    if not progression_available:
        return "supplement_progression_service_unavailable", None
    if canonical is None:
        return None, None
    if not canonical.execution_decisions:
        return "supplement_progression_target_unavailable", None
    blockers = current_blocker_facts(canonical)
    user_input_blockers = [fact for fact in blockers if fact.kind == "user_input"]
    if not user_input_blockers:
        # Researcher conversation stays open during running research and during
        # system/external interruptions. Apply at the next safe work boundary;
        # do not silence the user because the current blocker is system-owned.
        return None, None

    # Reuse the real consumer's recovery target proof. A callable service
    # alone cannot establish that this input can reach a primary WorkUnit.
    from app.modules.research_dispatch_service import ResearchDispatchService

    latest = max(
        canonical.execution_decisions,
        key=lambda row: (row.decision_sequence, row.decided_at, row.execution_decision_id),
    )
    try:
        target, _work = ResearchDispatchService._controller_recovery_target(
            canonical, original_decision_id=latest.execution_decision_id
        )
    except RuntimeError:
        return "supplement_progression_target_unavailable", None
    design = _active_research_design(canonical)
    if (
        not _research_intent_is_confirmed(canonical)
        or target.research_design_version != design.version
        or not any(
            instance.substage_instance_id == target.substage_instance_id
            and instance.instance_revision == target.substage_instance_revision
            for instance in design.instances
        )
        or not any(
            fact.substage_instance_id == target.substage_instance_id
            for fact in user_input_blockers
        )
    ):
        return "supplement_progression_target_unavailable", None
    return None, target.substage_instance_id


def project_supplement_input_action(
    task_payload: dict[str, Any],
    *,
    progression_available: bool | None = None,
) -> dict[str, Any]:
    """The same pure eligibility used by the snapshot and locked consumer."""
    canonical = _canonical_task(task_payload)
    stopped = _active_stop(task_payload)
    if progression_available is None:
        progression_available = canonical is None or _canonical_supplement_progression_available()
    reason, target_instance_id = _supplement_input_eligibility(
        canonical=canonical,
        task_status=str(task_payload.get("status") or "not_started"),
        execution_stopped=stopped,
        progression_available=progression_available,
    )
    if canonical is None and {
        "researchDesignVersions", "executionDecisions", "workUnits"
    }.issubset(task_payload):
        reason = "canonical_action_facts_unavailable"
    return {
        "revision": int(task_payload.get("revision") or 0),
        "executionStopped": stopped,
        "allowed": reason is None,
        "reasonCode": reason,
        "targetSubstageInstanceId": target_instance_id,
    }


def explicit_retry_reason(
    *,
    canonical: ResearchTaskV12,
    work_unit_id: str,
    execution_decision_id: str,
    prior_capability_call_id: str,
    prior_attempt_number: int,
    dispatch_status: str,
    external_state: str,
    dispatch_remote_run_id: Any,
    external_remote_run_id: Any,
    last_error_code: str | None,
    execution_stopped: bool = False,
    proven_no_external_action_retry: bool = False,
) -> str | None:
    """Authorize independent retry from durable failure or frozen no-action facts."""
    if _string_value(canonical.status) != "running" or execution_stopped:
        return None
    reasoning_recovery = bool(
        proven_no_external_action_retry
        and external_state == "unknown"
        and dispatch_status == "submission_unknown"
    )
    if (
        (dispatch_status != "failed" and not reasoning_recovery)
        or dispatch_remote_run_id is not None
        or external_remote_run_id is not None
    ):
        return None
    if external_state not in {"never_submitted", "known_failed"} and not reasoning_recovery:
        return None
    known_failed_retry = external_state == "known_failed"
    non_external_recovery = known_failed_retry and proven_no_external_action_retry
    if known_failed_retry or reasoning_recovery:
        from app.config import settings

        if prior_attempt_number >= max(1, settings.research_dispatch_max_attempts):
            return None
    work = next(
        (row for row in canonical.work_units if row.work_unit_id == work_unit_id), None
    )
    allowed_work_statuses = (
        {"external_result_unknown"} if reasoning_recovery else {"blocked", "failed"}
    )
    if (
        work is None
        or work.execution_decision_id != execution_decision_id
        or _string_value(work.status) not in allowed_work_statuses
    ):
        return None
    calls = [
        row for row in canonical.capability_calls if row.work_unit_id == work_unit_id
    ]
    if not any(
        row.capability_call_id == prior_capability_call_id
        and row.attempt_number == prior_attempt_number
        for row in calls
    ) or max((row.attempt_number for row in calls), default=0) != prior_attempt_number:
        return None
    latest = max(
        canonical.execution_decisions,
        key=lambda row: (row.decision_sequence, row.decided_at, row.execution_decision_id),
        default=None,
    )
    if not non_external_recovery and (
        latest is None or latest.execution_decision_id != execution_decision_id
    ):
        return None
    # Preserve user-owned blockers and waits outside this exact WorkUnit.
    for fact in current_blocker_facts(canonical):
        if fact.kind == "system_failure":
            continue
        if (
            reasoning_recovery
            and fact.kind == "external_result_unknown"
            and fact.work_unit_id == work_unit_id
            and not fact.blocking_work_unit_ids
        ):
            # This exact unknown is the recovery obligation, not a second gate.
            # Its frozen no-action proof does not authorize any other blocker.
            continue
        if (
            fact.kind == "external_dependency"
            and fact.blocking_work_unit_ids
            and set(fact.blocking_work_unit_ids) == {work_unit_id}
        ):
            continue
        return None

    from app.modules.research_orchestration_repository import (
        DispatchAuthorizationError,
        ResearchOrchestrationRepository,
    )

    queued = work.model_copy(update={
        "status": type(work.status)("queued"),
        "terminal_reason_code": None,
        "finished_at": None,
    })
    projected = canonical.model_copy(update={
        "work_units": [queued if row.work_unit_id == work_unit_id else row for row in canonical.work_units]
    })
    dispatch_scope = {
        "execution_decision_id": execution_decision_id,
        "work_unit_id": work_unit_id,
        "idempotency_key": work.idempotency_key,
    }
    try:
        if known_failed_retry:
            ResearchOrchestrationRepository._validate_known_failed_safe_retry_authorization(
                projected,
                dispatch_scope,
                proven_no_external_action_retry=non_external_recovery,
            )
        else:
            ResearchOrchestrationRepository._validate_authorization(
                projected,
                dispatch_scope,
            )
    except DispatchAuthorizationError:
        return None
    if non_external_recovery:
        return "system_non_external_result_recovery"
    if reasoning_recovery:
        return "system_pure_reasoning_result_recovery"
    if known_failed_retry and last_error_code == STRUCTURED_OUTPUT_CORRECTION_EXHAUSTED:
        return STRUCTURED_OUTPUT_EXPLICIT_RETRY_REASON
    if known_failed_retry and last_error_code == SKILL_FINALIZATION_RUNTIME_FAILED:
        return SKILL_FINALIZATION_SYSTEM_RETRY_REASON
    return "user_confirmed_explicit_retry"


def reconcile_external_result_eligible(
    *,
    canonical: ResearchTaskV12,
    work_unit_id: str,
    execution_decision_id: str,
    capability_call_id: str,
    attempt_number: int,
    dispatch_status: str,
    external_state: str,
    evidence_refs: Any,
    execution_stopped: bool = False,
) -> bool:
    """True when durable facts match the same observable shape as user reconciliation."""
    if _string_value(canonical.status) != "running" or execution_stopped:
        return False
    work = next((row for row in canonical.work_units if row.work_unit_id == work_unit_id), None)
    capability_call = next(
        (
            row for row in canonical.capability_calls
            if row.capability_call_id == capability_call_id
            and row.work_unit_id == work_unit_id
        ),
        None,
    )
    if (
        work is None
        or capability_call is None
        or work.execution_decision_id != execution_decision_id
        or capability_call.attempt_number != attempt_number
        or capability_call.work_unit_id != work_unit_id
    ):
        return False
    latest = max(
        canonical.execution_decisions,
        key=lambda row: (row.decision_sequence, row.decided_at, row.execution_decision_id),
        default=None,
    )
    if latest is None or latest.execution_decision_id != execution_decision_id:
        return False
    refs = evidence_refs if isinstance(evidence_refs, (list, tuple)) else ()
    if not any(str(ref).strip() for ref in refs):
        return False
    work_status = _string_value(work.status)
    call_status = _string_value(capability_call.status)
    unknown_shape = (
        external_state == "unknown"
        and dispatch_status == "submission_unknown"
        and work_status == "external_result_unknown"
        and call_status == "external_result_unknown"
    )
    in_flight_shape = (
        external_state in {"submitted", "running"}
        and dispatch_status == "submitted"
        and work_status == "running"
        and call_status == "running"
    )
    return unknown_shape or in_flight_shape


def _has_inflight_execution(canonical: ResearchTaskV12, blockers: tuple[Any, ...]) -> bool:
    """True when stop would cancel live work rather than a already-blocked step."""
    live_call = any(
        _string_value(row.status) in {"queued", "running", "submitted", "external_result_unknown"}
        for row in canonical.capability_calls
    )
    live_work = any(
        _string_value(row.status) in {"running", "queued", "external_result_unknown"}
        for row in canonical.work_units
    )
    if live_call or live_work:
        return True
    # Pure system/user blockers with no live work: Stop is meaningless.
    if blockers and all(getattr(fact, "kind", None) in {"system_failure", "user_input", "external_dependency"} for fact in blockers):
        return False
    return False


def _continue_research_allowed(
    *,
    canonical: ResearchTaskV12 | None,
    task_status: str,
    execution_stopped: bool,
) -> bool:
    if canonical is None:
        return False
    if task_status in {"completed", "failed"} or _string_value(canonical.status) in {"completed", "failed"}:
        return False
    if execution_stopped:
        return (
            task_status in {"running", "cancelled"}
            and _string_value(canonical.status) in {"running", "cancelled"}
            and _pending_research_work(canonical)
        )
    if task_status != "awaiting_confirmation" or _string_value(canonical.status) != "awaiting_confirmation":
        return False
    if not _research_intent_is_confirmed(canonical):
        return False
    design = _active_research_design(canonical)
    active_instances = {
        (row.substage_instance_id, row.instance_revision)
        for row in design.instances
    }
    return any(
        (runtime.substage_instance_id, runtime.instance_revision) in active_instances
        and is_durable_progression_pause(runtime.status, runtime.status_reason_code)
        and not any(
            work.substage_instance_id == runtime.substage_instance_id
            and work.substage_instance_revision == runtime.instance_revision
            and _string_value(work.status) == "external_result_unknown"
            for work in canonical.work_units
        )
        for runtime in canonical.substage_runtime_states
    )


def project_continue_research_action(task_payload: dict[str, Any]) -> dict[str, Any]:
    execution_stopped = _active_stop(task_payload)
    return {
        "revision": int(task_payload.get("revision") or 0),
        "executionStopped": execution_stopped,
        "allowed": _continue_research_allowed(
            canonical=_canonical_task(task_payload),
            task_status=str(task_payload.get("status") or "not_started"),
            execution_stopped=execution_stopped,
        ),
    }


def project_workbench_user_actions(
    *,
    task_payload: dict[str, Any],
    owner_id: str,
    workspace_id: str,
    actor_id: str,
) -> dict[str, Any]:
    task_id = str(task_payload.get("taskId") or "")
    task_revision = int(task_payload.get("revision") or 0)
    task_status = str(task_payload.get("status") or "not_started")
    execution_stopped = _active_stop(task_payload)
    canonical = _canonical_task(task_payload)
    lifecycle_kind = "canonical_v1_2" if canonical is not None else "legacy_durable_run"
    canonical_supplement_ready = (
        canonical is None or _canonical_supplement_progression_available()
    )
    blockers = current_blocker_facts(canonical) if canonical is not None else ()
    input_details_allowed = (
        task_status not in _TERMINAL_TASK_STATES
        and not execution_stopped
        and not any(fact.kind != "user_input" for fact in blockers)
    )
    top_level_actions: list[str] = []
    supplement = project_supplement_input_action(
        task_payload, progression_available=canonical_supplement_ready
    )
    if supplement["allowed"]:
        top_level_actions.append("supplement_input")
    if _continue_research_allowed(
        canonical=canonical,
        task_status=task_status,
        execution_stopped=execution_stopped,
    ):
        top_level_actions.append("continue_research")
    # Stop only when something is still in flight. A blocked/failed step with
    # retry available must not offer a meaningless Stop alongside Retry.
    if (
        canonical is not None
        and task_status == "running"
        and not execution_stopped
        and _has_inflight_execution(canonical, blockers)
    ):
        top_level_actions.append("stop")

    def supplement_actions_for_instance(instance_id: str | None) -> list[str]:
        target = supplement["targetSubstageInstanceId"]
        return ["supplement_input"] if (
            supplement["allowed"]
            and (target is None or target == instance_id)
        ) else []

    items: list[dict[str, Any]] = []
    decision = None
    work_by_id: dict[str, Any] = {}
    if canonical is not None and canonical.execution_decisions:
        decision = max(
            canonical.execution_decisions,
            key=lambda row: (row.decision_sequence, row.decided_at, row.execution_decision_id),
        )
        work_by_id = {
            row.work_unit_id: row
            for row in canonical.work_units
            if row.execution_decision_id == decision.execution_decision_id
        }
        assignment_objectives = {
            str(assignment.assignment_id): str(assignment.objective)
            for assignment in getattr(decision, "assignments", [])
            if getattr(assignment, "assignment_id", None) and getattr(assignment, "objective", None)
        }
        runtime_input_instances = _runtime_input_instance_ids(canonical)
        artifact_refs_by_work_unit: dict[str, list[str]] = {}
        for artifact in canonical.artifacts:
            work_unit_id = str(getattr(artifact, "work_unit_id", "") or "")
            artifact_id = str(getattr(artifact, "artifact_id", "") or "")
            if (
                not work_unit_id
                or not artifact_id
                or not _artifact_is_blocking_gap(canonical, artifact, runtime_input_instances)
            ):
                continue
            artifact_refs_by_work_unit.setdefault(work_unit_id, []).append(artifact_id)

        def action_context(work_unit_id: str) -> dict[str, Any]:
            work = work_by_id.get(work_unit_id)
            assignment_id = str(getattr(work, "assignment_id", "") or "") if work is not None else ""
            return {
                "objective": assignment_objectives.get(assignment_id),
                "artifactRefs": list(artifact_refs_by_work_unit.get(work_unit_id, [])),
            }

        # One live CTA per WorkUnit: prefer latest retry-eligible attempt.
        latest_retry_by_work: dict[str, tuple[int, dict[str, Any]]] = {}
        reconcile_by_work: dict[str, tuple[int, dict[str, Any]]] = {}
        for row in _external_context_rows(
            task_id=task_id,
            owner_id=owner_id,
            workspace_id=workspace_id,
            execution_decision_id=decision.execution_decision_id,
        ):
            work = work_by_id.get(str(row["work_unit_id"]))
            if work is None:
                continue
            attempt_number = int(row["attempt_number"])
            common = {
                "taskId": task_id,
                "workUnitId": work.work_unit_id,
                "dispatchId": str(row["dispatch_id"]),
                "executionDecisionId": decision.execution_decision_id,
                "capabilityCallId": str(row["capability_call_id"]),
                "attemptNumber": attempt_number,
                "canonicalPayloadFingerprint": str(row["canonical_payload_fingerprint"]),
                "ownerId": owner_id,
                "workspaceId": workspace_id,
                "actorId": actor_id,
            }
            allowed: list[str] = []
            contexts: dict[str, Any] = {}
            retry_reason = explicit_retry_reason(
                canonical=canonical,
                work_unit_id=work.work_unit_id,
                execution_decision_id=decision.execution_decision_id,
                prior_capability_call_id=str(row["capability_call_id"]),
                prior_attempt_number=attempt_number,
                dispatch_status=str(row["dispatch_status"]),
                external_state=str(row["external_state"]),
                dispatch_remote_run_id=row.get("dispatch_remote_run_id"),
                external_remote_run_id=row.get("external_remote_run_id"),
                last_error_code=str(row.get("last_error_code") or ""),
                execution_stopped=execution_stopped,
            )
            structured_output_exhausted = retry_reason == STRUCTURED_OUTPUT_EXPLICIT_RETRY_REASON
            skill_finalization_retry = retry_reason == SKILL_FINALIZATION_SYSTEM_RETRY_REASON
            if retry_reason is not None:
                allowed.append("explicit_retry")
                contexts["explicit_retry"] = {
                    **common,
                    "priorCapabilityCallId": str(row["capability_call_id"]),
                    "priorAttemptNumber": attempt_number,
                    "reasonCode": retry_reason,
                }
                system_recoverable_retry = structured_output_exhausted or skill_finalization_retry
                candidate = {
                    "itemId": f"external-result:{work.work_unit_id}",
                    "kind": (
                        "structured_output_retry_ready"
                        if structured_output_exhausted
                        else (
                            "skill_finalization_retry_ready"
                            if skill_finalization_retry
                            else "explicit_retry_ready"
                        )
                    ),
                    "title": "重新生成这一步" if system_recoverable_retry else "重试",
                    "reason": (
                        "这一步的返回格式连续未通过校验。重试会创建一次新的执行；已有成果保留。"
                        if structured_output_exhausted
                        else (
                            "这一步的结果整理失败。重试会创建一次新的执行；已有成果保留。"
                            if skill_finalization_retry
                            else "上一次执行未成功完成。重试会创建一次新的执行；已有成果保留。"
                        )
                    ),
                    "workUnitId": work.work_unit_id,
                    **action_context(work.work_unit_id),
                    "allowedActions": allowed,
                    "commandContexts": contexts,
                }
                previous = latest_retry_by_work.get(work.work_unit_id)
                if previous is None or attempt_number >= previous[0]:
                    latest_retry_by_work[work.work_unit_id] = (attempt_number, candidate)
                continue
            if reconcile_external_result_eligible(
                canonical=canonical,
                work_unit_id=work.work_unit_id,
                execution_decision_id=decision.execution_decision_id,
                capability_call_id=str(row["capability_call_id"]),
                attempt_number=attempt_number,
                dispatch_status=str(row["dispatch_status"]),
                external_state=str(row["external_state"]),
                evidence_refs=row.get("evidence_refs"),
                execution_stopped=execution_stopped,
            ):
                candidate = {
                    "itemId": f"external-result:{work.work_unit_id}",
                    "kind": "external_result_unknown",
                    "title": "结果待核对",
                    "reason": (
                        "外部执行结果尚未确认。系统会继续核对这一步；"
                        "已完成的研究内容和产物不会丢失。"
                    ),
                    "workUnitId": work.work_unit_id,
                    **action_context(work.work_unit_id),
                    "allowedActions": [],
                    "commandContexts": {},
                }
                previous = reconcile_by_work.get(work.work_unit_id)
                if previous is None or attempt_number >= previous[0]:
                    reconcile_by_work[work.work_unit_id] = (attempt_number, candidate)
        for work_unit_id, (_attempt, retry_item) in latest_retry_by_work.items():
            items.append(retry_item)
            reconcile_by_work.pop(work_unit_id, None)
        for _attempt, reconcile_item in reconcile_by_work.values():
            items.append(reconcile_item)

        existing_work_ids = {
            str(item.get("workUnitId"))
            for item in items
            if item.get("workUnitId") is not None
        }
        input_artifact_work_ids = {
            artifact.work_unit_id
            for artifact in canonical.artifacts
            if artifact.work_unit_id in work_by_id
            and _artifact_is_blocking_gap(canonical, artifact, runtime_input_instances)
        }
        required_authority_work_ids = {
            artifact.work_unit_id
            for artifact in canonical.artifacts
            if artifact.work_unit_id in work_by_id
            and _artifact_reviewer_disposition(artifact) == "human_authority"
            and _artifact_is_blocking_gap(canonical, artifact, runtime_input_instances)
        }
        optional_advisory_work_ids = {
            artifact.work_unit_id
            for artifact in canonical.artifacts
            if artifact.work_unit_id in work_by_id
            and _artifact_reviewer_disposition(artifact) == "advisory"
            and isinstance(getattr(artifact, "validator_result", None), dict)
            and getattr(artifact, "validator_result").get("optionalInput") is True
            and action_context(artifact.work_unit_id).get("objective")
        }
        for work_unit_id in sorted(input_artifact_work_ids | required_authority_work_ids | optional_advisory_work_ids):
            if not input_details_allowed or work_unit_id in existing_work_ids:
                continue
            optional_advisory = work_unit_id in optional_advisory_work_ids
            allowed = supplement_actions_for_instance(_work_instance_id(canonical, work_unit_id))
            advisory = any(
                artifact.work_unit_id == work_unit_id
                and isinstance(getattr(artifact, "validator_result", None), dict)
                and getattr(artifact, "validator_result").get("continueAllowed") is True
                for artifact in canonical.artifacts
            )
            advisory = advisory or optional_advisory
            concrete_blocking_reason = (
                None if advisory else _concrete_blocking_input_reason(canonical, work_unit_id)
            )
            items.append(
                {
                    "itemId": f"provide-input:{work_unit_id}",
                    "kind": "provide_input",
                    "title": (
                        "可补充研究信息" if advisory and allowed
                        else "研究信息说明" if advisory
                        else "需要补充研究材料" if allowed
                        else "当前研究信息缺口"
                    ),
                    "reason": (
                        "当前结果可继续使用；补充内容提交后会在下一个安全工作节点生效。"
                        if advisory and allowed
                        else "当前结果可继续使用；系统暂不能接收并应用补充，你现在无需操作。"
                        if advisory
                        else _unavailable_input_reason(concrete_blocking_reason)
                        if not allowed
                        else concrete_blocking_reason
                        if concrete_blocking_reason
                        else (
                            "当前步骤还缺少继续研究所需的具体条件或材料。"
                            "请补充你目前能提供的范围、材料或约束；仅输入「继续」或「确认」无法代替这些信息。"
                        )
                    ),
                    "workUnitId": work_unit_id,
                    **action_context(work_unit_id),
                    "allowedActions": allowed,
                    "commandContexts": {},
                }
            )
            existing_work_ids.add(work_unit_id)


        if task_status not in _TERMINAL_TASK_STATES:
            for index, row in enumerate(
                _pending_controller_command_rows(
                    task_id=task_id,
                    owner_id=owner_id,
                    workspace_id=workspace_id,
                )
            ):
                submitted_revision = int(row.get("submitted_task_revision") or 0)
                rejected = row.get("state") == "rejected"
                items.append(
                    {
                        "itemId": f"supplement-{'rejected' if rejected else 'pending'}:{submitted_revision}:{index + 1}",
                        "kind": "supplement_rejected" if rejected else "supplement_pending",
                        "title": "补充未生效" if rejected else "补充已提交",
                        "reason": (
                            "这次补充没有生效。"
                            if rejected
                            else "系统已收到补充内容，正在等待下一个安全工作节点应用。"
                        ),
                        "workUnitId": None,
                        "objective": None,
                        "artifactRefs": [],
                        "allowedActions": [],
                        "commandContexts": {},
                    }
                )

    if (
        canonical is not None
        and input_details_allowed
    ):
        represented_input_instances = {
            _work_instance_id(canonical, str(item.get("workUnitId") or ""))
            for item in items if item.get("kind") == "provide_input"
        }
        for runtime in canonical.substage_runtime_states:
            if _string_value(runtime.status) not in _RUNTIME_INPUT_STATUSES:
                continue
            instance_id = str(getattr(runtime, "substage_instance_id", "") or "")
            if (
                instance_id not in _runtime_input_instance_ids(canonical)
                or instance_id in represented_input_instances
            ):
                continue
            allowed = supplement_actions_for_instance(instance_id)
            reason_code = str(getattr(runtime, "status_reason_code", "") or "").strip()
            concrete_reason, artifact_refs, work_unit_id = _gap_materials_for_instance(canonical, instance_id)
            if not allowed:
                fallback_reason = _unavailable_input_reason(concrete_reason)
            elif concrete_reason:
                fallback_reason = concrete_reason
            elif artifact_refs:
                fallback_reason = (
                    f"{_reason_code_copy(reason_code)}"
                    "中间已有初步结果，但还不能进入下一步。"
                    "请补充还缺的判断依据、范围或约束；仅输入「继续」或「确认」无法代替这些信息。"
                )
            else:
                fallback_reason = (
                    f"{_reason_code_copy(reason_code)}"
                    "请补充你目前能提供的范围、材料或约束；仅输入「继续」或「确认」无法代替这些信息。"
                )
            items.append(
                {
                    "itemId": f"provide-input:runtime:{instance_id or 'task'}",
                    "kind": "provide_input",
                    "title": "需要补充研究信息" if allowed else "当前研究信息缺口",
                    "reason": fallback_reason,
                    "workUnitId": work_unit_id,
                    "objective": _instance_objective(canonical, instance_id),
                    "artifactRefs": artifact_refs,
                    "allowedActions": allowed,
                    "commandContexts": {},
                }
            )
            represented_input_instances.add(instance_id)

    unrecoverable = canonical is not None and task_status == "failed" and not items
    return {
        "schemaVersion": "workbench_user_actions_v1",
        "lifecycleKind": lifecycle_kind,
        "taskId": task_id,
        "revision": task_revision,
        "taskStatus": task_status,
        "executionStopped": execution_stopped,
        "allowedActions": top_level_actions,
        "items": items,
        "unrecoverable": unrecoverable,
        "unrecoverableMessage": (
            "这一步没有完成，系统暂时无法自动继续。\n已完成的研究内容和产物不会丢失。"
            if unrecoverable
            else None
        ),
    }
