"""Close comparability evidence and deterministic metric comparison into a final three-state verdict."""

from __future__ import annotations

from typing import Any

_CONDITION_KEYS = (
    "task_scope", "implementation", "data_split", "preprocessing",
    "evaluation_protocol", "metric_definition", "randomness", "hardware_environment",
)


def _valid_comparable(assessment: Any) -> bool:
    if not isinstance(assessment, dict) or assessment.get("status") != "comparable":
        return False
    conditions = assessment.get("conditions")
    if not isinstance(conditions, dict) or set(conditions) != set(_CONDITION_KEYS):
        return False
    for key in _CONDITION_KEYS:
        item = conditions.get(key)
        if not isinstance(item, dict) or item.get("status") != "compatible":
            return False
        refs = item.get("evidence_refs")
        if not isinstance(refs, list) or not refs or not all(isinstance(v, str) and v.strip() for v in refs):
            return False
    if assessment.get("condition_mismatches") not in ([], None):
        return False
    refs = assessment.get("evidence_refs")
    return isinstance(refs, list) and bool(refs) and all(isinstance(v, str) and v.strip() for v in refs)


def _safe_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def run_reproduction_comparison_closure(payload: dict[str, Any]) -> dict[str, Any]:
    assessment = payload.get("reproduction_comparability_assessment") if isinstance(payload, dict) else None
    comparison = payload.get("deterministic_comparison") if isinstance(payload, dict) else None
    comparable = _valid_comparable(assessment)
    deterministic_ok = (
        isinstance(comparison, dict)
        and comparison.get("state") == "succeeded"
        and comparison.get("comparison_status") == "complete"
        and comparison.get("overall_result") in {"meets_standard", "does_not_meet_standard"}
    )
    verdict = comparison["overall_result"] if comparable and deterministic_ok else "indeterminate"

    rule_application: dict[str, Any] = {
        "deterministic_state": comparison.get("state") if isinstance(comparison, dict) else None,
        "comparison_status": comparison.get("comparison_status") if isinstance(comparison, dict) else None,
        "deterministic_overall_result": comparison.get("overall_result") if isinstance(comparison, dict) else None,
        "requested_reference_count": comparison.get("requested_reference_count") if isinstance(comparison, dict) else 0,
        "compared_metric_count": comparison.get("compared_metric_count") if isinstance(comparison, dict) else 0,
    }
    if isinstance(comparison, dict) and comparison.get("failure_code"):
        rule_application["failure_code"] = comparison["failure_code"]

    unknowns = _safe_list(assessment.get("unknowns") if isinstance(assessment, dict) else None)
    if not comparable and not unknowns and isinstance(assessment, dict) and assessment.get("status") == "indeterminate":
        unknowns.append("comparability_not_established")
    if not deterministic_ok and isinstance(comparison, dict) and comparison.get("failure_code"):
        unknowns.append(str(comparison["failure_code"]))
    unknowns = list(dict.fromkeys(v for v in unknowns if isinstance(v, str) and v))

    return {
        "overall_verdict": verdict,
        "comparability": assessment if isinstance(assessment, dict) else {},
        "metric_judgments": _safe_list(comparison.get("metrics") if isinstance(comparison, dict) else None),
        "condition_mismatches": _safe_list(assessment.get("condition_mismatches") if isinstance(assessment, dict) else None),
        "rule_application": rule_application,
        "evidence_refs": _safe_list(assessment.get("evidence_refs") if isinstance(assessment, dict) else None),
        "unknowns": unknowns,
    }
