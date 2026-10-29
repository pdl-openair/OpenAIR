"""Canonical ownership boundary for research execution blockers.

`needs_input` is exceptional: only blocker reasons that explicitly require a
user-owned input or confirmed boundary change may enter that state. Unknown or
system-owned reasons stay on the system recovery path.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


_USER_NEEDS_INPUT_REASON_CODES = frozenset({
    "authorization_required",
    "budget_expansion_required",
    "active_design_not_confirmed",
    "current_confirmed_design_required",
    # Caller-designated invocation input (e.g. PVD experiment feedback designates
    # the experiment_ref to diagnose). Only the triggering caller holds that fact.
    "execution_selection_invocation_context_unsatisfied",
})

# Semantic gaps limit the formal conclusion that depends on the missing fact.
# They do not stop the research, and they do not ask the user to unblock them.
_RESEARCH_GAP_REASON_CODES = frozenset({
    "required_input_missing",
    "dependency_not_satisfied",
    "dependency_pending",
    "dependency_terminal_unsatisfied",
    "dependency_instance_missing",
    "final_assembly_upstream_pending",
    "final_assembly_upstream_missing",
    "execution_readiness_not_ready",
    "policy_prohibited",
    "review_needs_input",
    "review_human_decision_required",
    "semantic_material_insufficient",
    "upstream_semantic_material_insufficient",
    "acceptance_criteria_unresolved",
    "instance_not_ready",
    "ready_instance_missing",
    "next_task_legal_candidate_missing",
    "preflight_blocked",
    "final_artifact_requirement_unsatisfied",
})


def needs_user_input(reason_code: object) -> bool:
    """Return True only when the user uniquely holds the next fact.

    Authorization, an already-confirmed budget increase, and an unconfirmed
    research design are user gates. A missing research input, an evidence gap,
    or a risk judgment is not.
    """
    return str(reason_code or "").strip() in _USER_NEEDS_INPUT_REASON_CODES


def research_gap_continues(reason_code: object) -> bool:
    """True when the reason records a gap and the research must keep moving."""
    return str(reason_code or "").strip() in _RESEARCH_GAP_REASON_CODES


def prohibited_external_action(reason_code: object) -> bool:
    """A hard boundary skips that action. It does not stop the rest of the research."""
    return str(reason_code or "").strip() == "policy_prohibited"


def runtime_blocker_status(reason_code: object) -> str:
    """Map a blocker reason onto the canonical runtime blocker state."""
    if research_gap_continues(reason_code):
        return "skipped"
    return "needs_input" if needs_user_input(reason_code) else "blocked_recoverable"


@dataclass(frozen=True)
class ReviewerDisposition:
    """The single public ownership decision for a completed reviewer verdict."""

    kind: str
    continue_allowed: bool
    optional_input: bool = False


def reviewer_disposition(
    *,
    verdict: object,
    basis: object,
    issues: object,
    missing_inputs: object,
    research_objective: object,
) -> ReviewerDisposition:
    """Decide whether a reviewer outcome belongs to the system or the user."""
    normalized = str(verdict or "").strip()
    if normalized == "external_result_unknown":
        return ReviewerDisposition("system_reconcile", continue_allowed=False)
    if normalized == "human_decision_required":
        return ReviewerDisposition("human_authority", continue_allowed=False)
    if normalized == "usable":
        return ReviewerDisposition("continue", continue_allowed=True)

    missing = [str(value).strip() for value in missing_inputs if str(value).strip()] if isinstance(missing_inputs, list) else []
    issue_messages = [
        str(issue.get("message") or "").strip()
        for issue in issues if isinstance(issue, dict)
        if str(issue.get("message") or "").strip()
    ] if isinstance(issues, list) else []
    optional_input = bool(missing and issue_messages and str(basis or "").strip() and str(research_objective or "").strip())
    return ReviewerDisposition("advisory", continue_allowed=True, optional_input=optional_input)

@dataclass(frozen=True)
class ResearchBlocker:
    """A read-only view of an unresolved canonical execution fact.

    This is neither a persisted state nor permission to perform recovery. The
    revision-fenced action snapshot and its command consumer own permissions.
    """

    kind: str
    reason_code: str
    work_unit_id: str | None = None
    substage_instance_id: str | None = None
    blocking_work_unit_ids: tuple[str, ...] = ()


_DEPENDENCY_WAIT_REASONS = frozenset({
    "dependency_not_satisfied", "dependency_pending",
    "final_assembly_upstream_pending", "external_dependency_pending",
})
_UNKNOWN_EXECUTION_REASONS = frozenset({
    "external_result_unknown", "provider_result_unknown",
    "recoverable_execution_unknown", "submission_unknown",
})
_CONTROL_REASONS = frozenset({"user_stop_requested", "task_stopped"})


def _field(row: object, name: str, alias: str | None = None, default=None):
    if isinstance(row, dict):
        return row.get(name, row.get(alias, default) if alias else default)
    return getattr(row, name, default)


def _state(value: object) -> str:
    return str(getattr(value, "value", value) or "")


def active_user_stop_receipt(task_id: str, receipt: object) -> dict | None:
    """Only a matching committed stop receipt establishes user control."""
    if (
        isinstance(receipt, dict)
        and receipt.get("taskId") == task_id
        and receipt.get("status") == "stopped"
        and receipt.get("active") is True
        and receipt.get("controlAction") == "stop"
    ):
        return receipt
    return None


def blocker_kind(reason_code: object, *, status: object = None, user_input_proven: bool = False) -> str:
    """Translate observed ownership without inferring user fault from status."""
    reason = str(reason_code or "").strip()
    if _state(status) == "external_result_unknown" or reason in _UNKNOWN_EXECUTION_REASONS:
        return "external_result_unknown"
    if needs_user_input(reason):
        return "user_input"
    if research_gap_continues(reason):
        return "research_gap"
    if reason in _DEPENDENCY_WAIT_REASONS:
        return "external_dependency"
    return "system_failure"


def current_blocker_facts(task: object) -> tuple[ResearchBlocker, ...]:
    """Read the current Design/runtime/decision facts, preserving failure at stop.

    Completed/skipped runtime and superseded execution history do not become
    fresh blockers. A Task cancellation alone cannot erase its local failure.
    No lifecycle, result, Artifact or scientific-state mutation occurs here.
    """
    if _state(_field(task, "status")) == "completed":
        return ()
    active_ref = _field(task, "active_research_design_ref", "activeResearchDesignRef")
    if active_ref is None:
        return ()
    version = _field(active_ref, "version")
    designs = _field(task, "research_design_versions", "researchDesignVersions", []) or []
    design = next((row for row in designs if (
        _field(row, "research_design_id", "researchDesignId") == _field(active_ref, "research_design_id", "researchDesignId")
        and _field(row, "version") == version
    )), None) if active_ref is not None else None
    if design is None:
        return ()
    instances = list(_field(design, "instances", default=[]) or [])
    # Current project scope is exactly confirmed Design.instances. Historical
    # reentry_execution_instances are compatibility history, not extra scope.

    def instance_key(row):
        return (
            _field(row, "substage_instance_id", "substageInstanceId"),
            _field(row, "instance_revision", "instanceRevision",
                   _field(row, "substage_instance_revision", "substageInstanceRevision", 1)),
        )

    active_keys = {instance_key(row) for row in instances}
    runtimes = {
        instance_key(row): row
        for row in _field(task, "substage_runtime_states", "substageRuntimeStates", []) or []
        if not active_keys or instance_key(row) in active_keys
    }
    latest: dict[tuple[Any, Any], tuple[tuple[Any, str, str], Any]] = {}
    decisions = _field(task, "execution_decisions", "executionDecisions", []) or []
    for row in decisions:
        row_version = _field(row, "research_design_version", "researchDesignVersion")
        if version is not None and row_version is not None and row_version != version:
            continue
        key = instance_key(row)
        if active_keys and key not in active_keys:
            continue
        ordering = (
            _field(row, "decision_sequence", "decisionSequence", 0),
            str(_field(row, "decided_at", "decidedAt", "")),
            str(_field(row, "execution_decision_id", "executionDecisionId", "")),
        )
        if key not in latest or ordering > latest[key][0]:
            latest[key] = (ordering, row)

    all_units = _field(task, "work_units", "workUnits", []) or []
    units_by_id = {_field(row, "work_unit_id", "workUnitId"): row for row in all_units}
    current_units = []
    for row in all_units:
        key = instance_key(row)
        if active_keys and key not in active_keys:
            continue
        row_version = _field(row, "research_design_version", "researchDesignVersion")
        if version is not None and row_version is not None and row_version != version:
            continue
        selected = latest.get(key)
        if selected is not None and (
            _state(_field(selected[1], "status")) == "superseded"
            or _field(row, "execution_decision_id", "executionDecisionId")
            != _field(selected[1], "execution_decision_id", "executionDecisionId")
        ):
            continue
        if _state(_field(row, "source_mode", "sourceMode")) == "legacy_projection":
            continue
        if _state(_field(runtimes.get(key), "status")) in {"completed", "skipped"}:
            continue
        current_units.append(row)

    def explicit_review_input(key):
        runtime = runtimes.get(key)
        if _state(_field(runtime, "status")) != "needs_input":
            return False
        reason = str(_field(runtime, "status_reason_code", "statusReasonCode", "") or "")
        if reason not in {"review_needs_input", "review_human_decision_required"}:
            return False
        current_ids = {_field(row, "work_unit_id", "workUnitId") for row in current_units if instance_key(row) == key}
        for artifact in _field(task, "artifacts", default=[]) or []:
            if _field(artifact, "work_unit_id", "workUnitId") not in current_ids:
                continue
            validator = _field(artifact, "validator_result", "validatorResult", {}) or {}
            if validator.get("continueAllowed") is True:
                continue
            verdict = validator.get("reviewVerdict")
            missing = validator.get("reviewMissingInputs") or validator.get("missingInputs") or []
            if verdict == "needs_input" and isinstance(missing, list) and any(str(v).strip() for v in missing):
                return True
            if verdict == "human_decision_required" and str(validator.get("reviewBasis") or "").strip():
                return True
        return False

    facts = []
    covered_keys = set()
    for work in current_units:
        status = _state(_field(work, "status"))
        if status not in {"blocked", "failed", "external_result_unknown"}:
            continue
        key = instance_key(work)
        reason = str(_field(work, "terminal_reason_code", "terminalReasonCode") or
                     _field(runtimes.get(key), "status_reason_code", "statusReasonCode") or "").strip()
        if reason in _CONTROL_REASONS:
            continue
        dependencies = tuple(
            ref for ref in _field(work, "dependency_work_unit_ids", "dependencyWorkUnitIds", []) or []
            if ref in units_by_id and _state(_field(units_by_id[ref], "status")) != "completed"
        )
        kind = blocker_kind(reason, status=status, user_input_proven=explicit_review_input(key))
        # Dependency links explain a wait only when no contrary failure fact is
        # present. They never turn a provider/configuration failure into a wait.
        if not reason and status == "blocked" and dependencies:
            kind = "external_dependency"
            reason = "dependency_pending"
        if research_gap_continues(reason):
            continue
        facts.append(ResearchBlocker(kind, reason, _field(work, "work_unit_id", "workUnitId"), key[0], dependencies))
        covered_keys.add(key)

    for key, runtime in runtimes.items():
        status = _state(_field(runtime, "status"))
        if status not in {"needs_input", "blocked_recoverable", "failed", "insufficient_evidence"}:
            continue
        reason = str(_field(runtime, "status_reason_code", "statusReasonCode") or "").strip()
        if reason in _CONTROL_REASONS:
            continue
        if research_gap_continues(reason):
            continue
        kind = blocker_kind(reason, status=status, user_input_proven=explicit_review_input(key))
        if key in covered_keys and reason in {"", "blocked", "failed", "needs_input", "rejected", "known_failed", "partial"}:
            # Decision outcome is a coarser summary of the same WorkUnit fact;
            # it must not turn explicit user/dependency evidence into a new
            # system failure. A specific contradictory runtime reason remains.
            continue
        # A specific system/runtime blocker remains visible even if a unit has
        # a different (e.g. user-owned) lower-level reason.
        if key in covered_keys and any(
            row.substage_instance_id == key[0] and row.kind == kind and row.reason_code == reason
            for row in facts
        ):
            continue
        facts.append(ResearchBlocker(kind, reason, None, key[0]))
    return tuple(facts)


def blocker_user_explanation(fact: ResearchBlocker) -> dict[str, str]:
    """Safe default copy; raw reason codes remain diagnostic-only."""
    if fact.kind == "external_result_unknown":
        return {
            "kind": "blocked_recoverable",
            "reason": "外部执行可能已经开始，结果暂时无法确认。系统会继续核对，避免重复执行；你现在无需操作。",
            "reasonCode": "recoverable_execution_unknown",
        }
    if fact.kind == "research_gap":
        return {
            "kind": "research_gap",
            "reason": "这一步仍有缺口或风险。已有结果保留，依赖该事实的正式结论不会写成已确认，研究继续进行。",
            "reasonCode": fact.reason_code or "research_gap",
        }
    if fact.kind == "user_input":
        reason = {
            "authorization_required": "当前步骤需要你确认授权，才能继续执行。",
            "budget_expansion_required": "当前步骤已达到确认的预算边界，需要你决定是否扩大预算。",
            "review_human_decision_required": "当前结果涉及需要你确认的研究边界，确认后才能决定下一步。",
        }.get(fact.reason_code, "当前步骤缺少你掌握的必要信息，补齐后才能形成下一步判断。")
        return {"kind": "user_action", "reason": reason, "reasonCode": "user_input_required"}
    if fact.kind == "external_dependency":
        return {
            "kind": "dependency", "reason": "当前步骤正在等待前置研究结果或外部条件，后续工作暂未继续；你现在无需操作。",
            "reasonCode": "dependency_pending",
        }
    if fact.reason_code == "budget_exceeded":
        reason = "当前资源预算不足，执行已安全暂停。"
    elif fact.reason_code in {"semantic_material_insufficient", "upstream_semantic_material_insufficient", "acceptance_criteria_unresolved", "review_insufficient_materials"}:
        reason = "当前证据还不足以支持下一步研究判断。系统需要继续核对，已有结果保留；当前不要求你补充材料。"
    else:
        reason = "当前步骤因系统执行条件未满足而中断。你可以说明需求（例如重试），或使用可用操作。"
    return {"kind": "runtime_unavailable", "reason": reason, "reasonCode": "system_execution_interrupted"}
