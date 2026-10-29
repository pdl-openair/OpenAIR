"""Compare explicit actual metrics with explicit reference metrics deterministically."""

from __future__ import annotations

from typing import Any

from .comparison_contract import (
    normalized_rule,
    validated_payload,
)
from .comparison_rules import compare_values

OUTPUT_SCHEMA_VERSION = "openair-reference-comparison/v1"


def _input_failure(payload: Any) -> dict[str, Any]:
    count = 0
    if isinstance(payload, dict) and isinstance(payload.get("reference_metrics"), list):
        count = len(payload["reference_metrics"])
    return {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "state": "failed",
        "comparison_status": "incomplete",
        "overall_result": "indeterminate",
        "failure_code": "skill_input_invalid",
        "requested_reference_count": count,
        "compared_metric_count": 0,
        "metrics": [],
    }


def _actual_view(row: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "value": row.get("actual_value"),
        **{key: row[key] for key in ("value_type", "unit", "extraction_status") if key in row},
    }
    if "failure_code" in row:
        result["failure_code"] = row["failure_code"]
    if "source_trace" in row:
        result["source_trace"] = row["source_trace"]
    return result


def _reference_view(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "value": row["reference_value"],
        "value_type": row["value_type"],
        "unit": row["unit"],
    }


def _rule_trace(raw_rule: Any) -> dict[str, Any]:
    if isinstance(raw_rule, dict):
        return dict(raw_rule)
    return {"invalid_rule": raw_rule}


def _failed_result(
    reference: dict[str, Any],
    code: str,
    *,
    actual: dict[str, Any] | None,
    rule: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "metric_name": reference["metric_name"],
        "comparison_status": "failed",
        "meets_rule": None,
        "failure_code": code,
        "actual": _actual_view(actual) if actual is not None else None,
        "reference": _reference_view(reference),
        "rule": rule if rule is not None else _rule_trace(reference.get("comparison_rule")),
    }


def _actual_availability_failure(row: dict[str, Any]) -> str | None:
    if not {"value_type", "unit", "extraction_status"}.issubset(row):
        return "actual_metric_record_invalid"
    if row["extraction_status"] != "succeeded":
        return "actual_metric_unavailable"
    return None


def run_reference_metric_comparison(payload: dict[str, Any]) -> dict[str, Any]:
    request = validated_payload(payload)
    if request is None:
        return _input_failure(payload)

    references = request["reference_metrics"]
    actual_rows = request["actual_metrics"]
    pairs = [
        (reference, actual)
        for reference in references
        for actual in ([row for row in actual_rows if row["metric_name"] == reference["metric_name"]] or [None])
    ]

    results: list[dict[str, Any]] = []
    compared = 0
    all_met = True

    for reference, actual in pairs:
        name = reference["metric_name"]
        if actual is None:
            results.append(_failed_result(reference, "actual_metric_missing", actual=None))
            continue
        availability_failure = _actual_availability_failure(actual)
        if availability_failure is not None:
            results.append(_failed_result(reference, availability_failure, actual=actual))
            continue
        rule, rule_error = normalized_rule(reference["comparison_rule"], reference["value_type"])
        if rule is None:
            results.append(_failed_result(reference, rule_error or "comparison_rule_invalid", actual=actual))
            continue

        try:
            meets_rule, calculation_trace = compare_values(
                actual.get("actual_value"), reference["reference_value"],
                reference["value_type"], rule,
            )
        except (KeyError, TypeError, ValueError, ArithmeticError):
            results.append(_failed_result(reference, "comparison_calculation_unavailable", actual=actual, rule=rule))
            continue
        compared += 1
        all_met = all_met and meets_rule
        results.append({
            "metric_name": name,
            "comparison_status": "succeeded",
            "meets_rule": meets_rule,
            "actual": _actual_view(actual),
            "reference": _reference_view(reference),
            "rule": rule,
            "calculation_trace": calculation_trace,
        })

    complete = compared == len(pairs)
    result = {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "state": "succeeded" if complete else "failed",
        "comparison_status": "complete" if complete else "incomplete",
        "overall_result": (
            "meets_standard" if complete and all_met
            else "does_not_meet_standard" if complete
            else "indeterminate"
        ),
        "requested_reference_count": len(references),
        "compared_metric_count": compared,
        "metrics": results,
    }
    if not complete:
        result["failure_code"] = "comparison_incomplete"
    return result
