"""Artifact evaluator and human-gate rules.

This module is intentionally deterministic: it gives the API, task traces, and
workbench UI one fail-closed contract for deciding whether an artifact is usable
or still needs human/material action.
"""

from __future__ import annotations

from typing import Any

from app.modules.research_blocker_semantics import needs_user_input

ARTIFACT_STATES = {
    "usable",
    "needs_review",
    "needs_input",
    "insufficient_materials",
    "failed",
    "partial_success",
    "rejected_or_needs_revision",
}

LEGACY_ARTIFACT_STATE_MAP = {
    "ready": "usable",
    "verified_artifact": "usable",
    "pending_review": "needs_review",
    "raw_output": "needs_review",
    "stage_summary": "partial_success",
    "insufficient_evidence": "insufficient_materials",
    "blocked_recoverable": "failed",
}

CONFIRMABLE_STATES = {"needs_review"}
SUPPLEMENT_ONLY_STATES = {"needs_input", "insufficient_materials"}
RETRY_OR_RETURN_STATES = {"failed", "rejected_or_needs_revision"}
SCOPE_EXPANSION_STATES = {"partial_success"}

def normalize_artifact_state(state: Any, *, kind: str | None = None) -> str:
    raw = str(state or "").strip()
    if raw in LEGACY_ARTIFACT_STATE_MAP:
        return LEGACY_ARTIFACT_STATE_MAP[raw]
    if raw in ARTIFACT_STATES:
        return raw
    return "partial_success" if kind == "stage_summary" else "needs_review"


def artifact_state_for_call_status(
    status: Any,
    *,
    reason_code: Any = None,
    fallback: str = "needs_review",
) -> str:
    status_text = str(status or "").strip()
    if status_text == "needs_input":
        return "needs_input" if needs_user_input(reason_code) else fallback
    if status_text == "blocked_recoverable":
        return fallback
    if status_text == "insufficient_evidence":
        return "insufficient_materials"
    if status_text == "failed":
        return "failed"
    if status_text == "completed":
        return fallback
    return fallback


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _existing_validator_result(artifact: dict[str, Any]) -> dict[str, Any]:
    result = artifact.get("validatorResult")
    return result if isinstance(result, dict) else {}


def _missing_inputs(artifact: dict[str, Any], state: str) -> list[str]:
    existing = artifact.get("missingInputs")
    if isinstance(existing, list):
        normalized = [str(item) for item in existing if str(item).strip()]
        if normalized:
            return normalized
    result = _existing_validator_result(artifact)
    if isinstance(result.get("missingInputs"), list):
        normalized = [str(item) for item in result["missingInputs"] if str(item).strip()]
        if normalized:
            return normalized
    if state == "needs_input":
        return ["user_materials_or_scope"]
    if state == "insufficient_materials":
        return ["source_materials"]
    if state == "partial_success":
        return ["scope_expansion_confirmation"]
    return []


def human_gate_for_state(state: str, *, missing_inputs: list[str] | None = None) -> dict[str, Any]:
    """Project Artifact review/use operations, never Task recovery eligibility.

    Supplement, continue, resume and retry require the revision-fenced Workbench
    action snapshot and its matching command consumer. An Artifact state alone
    cannot prove that any of those operations changes the current Task blocker.
    """
    missing = missing_inputs or []
    if state == "usable":
        return {
            "canConfirmPass": False,
            "canUseDownstream": True,
            "requiresHumanReview": False,
            "requiresScopeExpansionConfirmation": False,
            "allowedActions": ["use_downstream"],
            "primaryAction": "use_downstream",
            "blockedReason": None,
        }
    if state in CONFIRMABLE_STATES:
        return {
            "canConfirmPass": True,
            "canUseDownstream": False,
            "requiresHumanReview": True,
            "requiresScopeExpansionConfirmation": False,
            "allowedActions": ["confirm_pass", "request_revision"],
            "primaryAction": "confirm_pass",
            "blockedReason": None,
        }
    if state in SUPPLEMENT_ONLY_STATES:
        return {
            "canConfirmPass": False,
            "canUseDownstream": False,
            "requiresHumanReview": True,
            "requiresScopeExpansionConfirmation": False,
            "allowedActions": [],
            "primaryAction": "",
            "blockedReason": "missing_inputs" if missing else "insufficient_materials",
        }
    if state in SCOPE_EXPANSION_STATES:
        return {
            "canConfirmPass": False,
            "canUseDownstream": False,
            "requiresHumanReview": True,
            "requiresScopeExpansionConfirmation": True,
            "allowedActions": ["confirm_scope_expansion"],
            "primaryAction": "confirm_scope_expansion",
            "blockedReason": "partial_success_requires_scope_confirmation",
        }
    return {
        "canConfirmPass": False,
        "canUseDownstream": False,
        "requiresHumanReview": True,
        "requiresScopeExpansionConfirmation": False,
        "allowedActions": [],
        "primaryAction": "",
        "blockedReason": "artifact_not_usable",
    }

def validator_result_for_artifact(
    artifact: dict[str, Any],
    *,
    source_call: dict[str, Any] | None = None,
) -> dict[str, Any]:
    existing_result = _existing_validator_result(artifact)
    raw_state = artifact.get("artifactState")
    if raw_state is None:
        raw_state = existing_result.get("artifactState")
    state = normalize_artifact_state(raw_state, kind=artifact.get("kind"))
    if source_call is not None:
        state = artifact_state_for_call_status(
            source_call.get("status"),
            reason_code=(
                source_call.get("statusReasonCode")
                or source_call.get("terminalReasonCode")
                or source_call.get("reasonCode")
            ),
            fallback=state,
        )
    missing_inputs = _missing_inputs(artifact, state)
    issues = _as_list(artifact.get("validatorIssues"))
    if not issues:
        issues = _as_list(existing_result.get("issues"))
    if not issues and missing_inputs:
        issues = [
            {
                "code": "missing_inputs",
                "severity": "blocker" if state in {"needs_input", "insufficient_materials"} else "warning",
                "message": "Artifact is missing inputs required for downstream use.",
            }
        ]
    usable = state == "usable"
    return {
        "contractVersion": existing_result.get("contractVersion") or "artifact-evaluator.v1",
        "evaluator": existing_result.get("evaluator") or "openair_artifact_gate",
        "artifactId": artifact.get("id") or existing_result.get("artifactId"),
        "sourceCapabilityCallId": artifact.get("sourceCapabilityCallId")
        or existing_result.get("sourceCapabilityCallId"),
        "artifactState": state,
        "usable": usable,
        "missingInputs": missing_inputs,
        "issues": issues,
        "nextAction": human_gate_for_state(state, missing_inputs=missing_inputs)["primaryAction"],
    }


def apply_artifact_gate(
    artifact: dict[str, Any],
    *,
    source_call: dict[str, Any] | None = None,
) -> None:
    result = validator_result_for_artifact(artifact, source_call=source_call)
    state = result["artifactState"]
    artifact["artifactState"] = state
    artifact["validatorResult"] = result
    artifact["humanGate"] = human_gate_for_state(state, missing_inputs=result["missingInputs"])


def append_validator_result_to_call(call: dict[str, Any], validator_result: dict[str, Any]) -> None:
    artifact_id = validator_result.get("artifactId")
    existing = [
        item
        for item in _as_list(call.get("artifactValidatorResults"))
        if not artifact_id or item.get("artifactId") != artifact_id
    ]
    existing.append(dict(validator_result))
    call["artifactValidatorResults"] = existing
