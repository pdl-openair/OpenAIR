from __future__ import annotations

import json
import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Callable

from jsonschema import Draft7Validator, Draft201909Validator, Draft202012Validator, Draft4Validator, Draft6Validator


CheckResult = dict[str, Any]


_FAILURE_LEVELS = {"hard_gate", "score_penalty", "manual_review_trigger"}


def _result(check_id: str, status: str, failure_level: str, message: str, evidence: dict[str, Any] | None = None) -> CheckResult:
    return {
        "check_id": check_id,
        "status": status,
        "failure_level": failure_level,
        "message": message,
        "evidence": evidence or {},
    }


def _is_nonempty_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) > 0
    return True


def _is_empty_artifact(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) == 0
    return False


def _resolve_output_payload(context: dict[str, Any]) -> dict[str, Any] | None:
    payload = context.get("output_payload")
    if isinstance(payload, dict):
        return payload
    nested = context.get("output")
    if isinstance(nested, dict) and isinstance(nested.get("output_payload"), dict):
        return nested["output_payload"]
    return None


def _schema_validator(schema: dict[str, Any]):
    schema_uri = str(schema.get("$schema", "")).lower()
    if "draft-04" in schema_uri:
        return Draft4Validator(schema)
    if "draft-06" in schema_uri:
        return Draft6Validator(schema)
    if "draft-2019-09" in schema_uri:
        return Draft201909Validator(schema)
    if "2020-12" in schema_uri:
        return Draft202012Validator(schema)
    return Draft7Validator(schema)


def _format_path(path: Any) -> str:
    tokens = list(path)
    if not tokens:
        return "$"
    out = "$"
    for tok in tokens:
        if isinstance(tok, int):
            out += f"[{tok}]"
        else:
            out += f".{tok}"
    return out


def check_com_001(context: dict[str, Any]) -> CheckResult:
    if context.get("execution_mode") != "executed":
        return _result("COM-001", "not_applicable", "hard_gate", "execution_mode is not executed")
    payload = _resolve_output_payload(context)
    if isinstance(payload, dict):
        return _result("COM-001", "passed", "hard_gate", "output_payload is a JSON object", {"source": "output_payload"})
    text = context.get("output_text")
    if not isinstance(text, str) or not text.strip():
        return _result("COM-001", "failed", "hard_gate", "output_text is empty or missing", {})
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        return _result("COM-001", "failed", "hard_gate", f"output_text is not valid JSON object: {exc.msg}", {"json_error": exc.msg})
    if not isinstance(parsed, dict):
        return _result("COM-001", "failed", "hard_gate", "output_text parsed successfully but is not a JSON object", {"parsed_type": type(parsed).__name__})
    return _result("COM-001", "passed", "hard_gate", "output_text parsed as JSON object", {"source": "output_text"})


def check_com_002(context: dict[str, Any]) -> CheckResult:
    if context.get("execution_mode") != "executed":
        return _result("COM-002", "not_applicable", "hard_gate", "execution_mode is not executed")
    schema = context.get("output_schema")
    if not schema:
        return _result("COM-002", "not_applicable", "hard_gate", "output_schema is empty")
    payload = _resolve_output_payload(context)
    if not isinstance(payload, dict):
        return _result("COM-002", "failed", "hard_gate", "output_payload is missing or not a dict after COM-001", {"error_type": "missing_payload", "message": "output_payload is missing or not a dict after COM-001"})
    try:
        schema_validator = _schema_validator(schema)
        schema_validator.check_schema(schema)
    except Exception as exc:
        return _result("COM-002", "failed", "hard_gate", f"invalid schema definition: {exc}", {"error_type": type(exc).__name__, "message": str(exc)})
    payload_validator = _schema_validator(schema)
    payload_errors = sorted(payload_validator.iter_errors(payload), key=lambda e: list(e.path))
    if payload_errors:
        err = payload_errors[0]
        return _result(
            "COM-002",
            "failed",
            "hard_gate",
            f"payload invalid at {_format_path(err.path)}: {err.message}",
            {"location": _format_path(err.path), "reason": err.message},
        )
    return _result("COM-002", "passed", "hard_gate", "output_payload conforms to schema", {})


def check_com_003(context: dict[str, Any]) -> CheckResult:
    if context.get("execution_mode") != "executed":
        return _result("COM-003", "not_applicable", "hard_gate", "execution_mode is not executed")
    artifacts = context.get("artifacts")
    if not isinstance(artifacts, dict) or not artifacts:
        return _result("COM-003", "failed", "hard_gate", "artifacts are missing or empty", {})
    empty_keys = [k for k, v in artifacts.items() if _is_empty_artifact(v)]
    if empty_keys:
        return _result("COM-003", "failed", "hard_gate", "artifact declaration contains empty values", {"empty_artifacts": empty_keys})
    return _result("COM-003", "passed", "hard_gate", "artifacts are non-empty", {})


def check_com_004(context: dict[str, Any]) -> CheckResult:
    if context.get("execution_mode") != "executed":
        return _result("COM-004", "not_applicable", "hard_gate", "execution_mode is not executed")
    paths = context.get("core_nonempty_paths") or []
    if not paths:
        return _result("COM-004", "not_applicable", "hard_gate", "core_nonempty_paths not provided")
    payload = context.get("output_payload")
    if not isinstance(payload, dict):
        return _result("COM-004", "failed", "hard_gate", "output_payload missing for path validation", {})
    missing: list[str] = []
    empty: list[str] = []
    for path in paths:
        cur: Any = payload
        ok = True
        for part in str(path).split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                ok = False
                break
        if not ok:
            missing.append(str(path))
        elif not _is_nonempty_value(cur):
            empty.append(str(path))
    if missing or empty:
        return _result("COM-004", "failed", "hard_gate", "core result fields are missing or empty", {"missing_paths": missing, "empty_paths": empty})
    return _result("COM-004", "passed", "hard_gate", "core result fields are non-empty", {})


def check_com_005(context: dict[str, Any]) -> CheckResult:
    mode = context.get("execution_mode")
    artifacts = context.get("artifacts") or {}
    has_output = isinstance(artifacts, dict) and any(not _is_empty_artifact(v) for k, v in artifacts.items() if str(k).startswith("output"))
    has_trace = isinstance(artifacts, dict) and any(not _is_empty_artifact(v) for k, v in artifacts.items() if "trace" in str(k).lower())
    if mode == "executed":
        if not has_output:
            return _result("COM-005", "failed", "hard_gate", "executed mode requires normal output artifact", {"execution_mode": mode})
        return _result("COM-005", "passed", "hard_gate", "execution mode matches artifact presence", {"has_trace": has_trace})
    if mode in {"skipped", "validate_only"}:
        if has_output:
            return _result("COM-005", "failed", "hard_gate", f"{mode} must not produce normal output artifact", {"execution_mode": mode})
        return _result("COM-005", "passed", "hard_gate", "execution mode matches artifact absence", {"has_trace": has_trace})
    return _result("COM-005", "failed", "hard_gate", f"unsupported execution_mode: {mode}", {})


def check_com_006(context: dict[str, Any]) -> CheckResult:
    if not context.get("scenario_b_placeholder"):
        return _result("COM-006", "not_applicable", "hard_gate", "scenario_b_placeholder is false")
    if context.get("execution_mode") != "skipped":
        return _result("COM-006", "failed", "hard_gate", "Scenario B placeholder must be skipped", {"execution_mode": context.get("execution_mode")})
    if context.get("baseline_eligible") is not False:
        return _result("COM-006", "failed", "hard_gate", "Scenario B placeholder must not be baseline eligible", {"baseline_eligible": context.get("baseline_eligible")})
    artifacts = context.get("artifacts") or {}
    if isinstance(artifacts, dict) and any(not _is_empty_artifact(v) for k, v in artifacts.items() if str(k).startswith("output")):
        return _result("COM-006", "failed", "hard_gate", "Scenario B placeholder must not have a normal output artifact", {})
    return _result("COM-006", "passed", "hard_gate", "Scenario B placeholder is isolated", {})


def check_com_007(context: dict[str, Any]) -> CheckResult:
    handling_class = context.get("handling_class")
    if handling_class not in {
        "normal_execute",
        "conservative_generate",
        "manual_review_required",
        "reject_unreliable_input",
        "scenario_b_placeholder",
    }:
        return _result("COM-007", "failed", "hard_gate", f"unsupported handling_class: {handling_class}", {})
    if handling_class == "normal_execute":
        return _result("COM-007", "passed", "hard_gate", "normal_execute may enter normal validation", {})
    if handling_class == "scenario_b_placeholder":
        return check_com_006(context)
    if handling_class == "reject_unreliable_input":
        if context.get("overall_status") == "passed" or context.get("baseline_eligible") is True:
            return _result("COM-007", "failed", "hard_gate", "reject_unreliable_input must not be treated as success", {})
        return _result("COM-007", "passed", "hard_gate", "reject_unreliable_input is not baseline eligible", {})
    if handling_class == "conservative_generate":
        return _result("COM-007", "manual_review_trigger", "manual_review_trigger", "conservative_generate requires manual review", {})
    if handling_class == "manual_review_required":
        return _result("COM-007", "manual_review_trigger", "manual_review_trigger", "manual_review_required requires manual review", {})
    return _result("COM-007", "passed", "hard_gate", "handling_class accepted", {})


COMMON_CHECKS: list[Callable[[dict[str, Any]], CheckResult]] = [
    check_com_001,
    check_com_002,
    check_com_003,
    check_com_004,
    check_com_005,
    check_com_006,
    check_com_007,
]


@dataclass(frozen=True)
class DeterministicCheckSpec:
    check_id: str
    function: Callable[[dict[str, Any]], CheckResult]


def _summary_from_checks(checks: list[CheckResult]) -> dict[str, list[str]]:
    return {
        "hard_gate_failed": [c["check_id"] for c in checks if c["status"] == "failed" and c["failure_level"] == "hard_gate"],
        "score_penalties": [c["check_id"] for c in checks if c["status"] == "failed" and c["failure_level"] == "score_penalty"],
        "manual_review_triggers": [c["check_id"] for c in checks if c["status"] == "manual_review_trigger"],
    }


def aggregate_deterministic_results(checks: list[CheckResult], *, baseline_eligible: bool | None = None) -> dict[str, Any]:
    summary = _summary_from_checks(checks)
    if summary["hard_gate_failed"]:
        overall_status = "failed"
    elif summary["manual_review_triggers"]:
        overall_status = "manual_review_required"
    else:
        overall_status = "passed"
    if baseline_eligible is None:
        baseline_eligible = overall_status == "passed"
    if overall_status in {"failed", "manual_review_required"}:
        baseline_eligible = False
    return {
        "schema_version": "deterministic-result-v0.1",
        "overall_status": overall_status,
        "baseline_eligible": baseline_eligible,
        "checks": checks,
        "summary": summary,
    }


def run_common_checks(context: dict[str, Any]) -> dict[str, Any]:
    checks = [fn(context) for fn in COMMON_CHECKS]
    return aggregate_deterministic_results(checks, baseline_eligible=context.get("baseline_eligible"))
