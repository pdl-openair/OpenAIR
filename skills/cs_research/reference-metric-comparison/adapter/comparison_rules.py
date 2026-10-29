"""Pure deterministic comparison rule evaluation."""

from __future__ import annotations

from decimal import Decimal
from typing import Any


def _decimal(value: int | float) -> Decimal:
    return Decimal(str(value))


def _decimal_text(value: Decimal) -> str:
    if value == 0:
        return "0"
    text = format(value.normalize(), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def compare_values(
    actual: Any,
    reference: Any,
    value_type: str,
    rule: dict[str, Any],
) -> tuple[bool, dict[str, Any]]:
    kind = rule["kind"]
    if value_type in {"number", "integer"}:
        actual_decimal = _decimal(actual)
        reference_decimal = _decimal(reference)
        signed_delta = actual_decimal - reference_decimal
        absolute_delta = abs(signed_delta)
        trace: dict[str, Any] = {
            "rule_kind": kind,
            "actual_operand": actual,
            "reference_operand": reference,
            "signed_delta_decimal": _decimal_text(signed_delta),
            "absolute_delta_decimal": _decimal_text(absolute_delta),
        }
        if kind == "exact":
            result = actual_decimal == reference_decimal
            trace.update({"operator": "==", "result": result})
            return result, trace
        if kind == "absolute_tolerance":
            tolerance = _decimal(rule["tolerance"])
            result = absolute_delta <= tolerance
            trace.update({
                "operator": "abs(actual-reference) <= tolerance",
                "tolerance_decimal": _decimal_text(tolerance),
                "allowed_delta_decimal": _decimal_text(tolerance),
                "result": result,
            })
            return result, trace
        if kind == "relative_tolerance":
            tolerance = _decimal(rule["tolerance"])
            allowed_delta = abs(reference_decimal) * tolerance
            result = absolute_delta <= allowed_delta
            trace.update({
                "operator": "abs(actual-reference) <= abs(reference)*tolerance",
                "tolerance_decimal": _decimal_text(tolerance),
                "reference_magnitude_decimal": _decimal_text(abs(reference_decimal)),
                "allowed_delta_decimal": _decimal_text(allowed_delta),
                "result": result,
            })
            return result, trace
        if kind == "actual_gte_reference":
            result = actual_decimal >= reference_decimal
            trace.update({"operator": ">=", "result": result})
            return result, trace
        if kind == "actual_lte_reference":
            result = actual_decimal <= reference_decimal
            trace.update({"operator": "<=", "result": result})
            return result, trace
        raise ValueError("unsupported comparison rule")

    if kind != "exact":
        raise ValueError("non-numeric value only supports exact")
    result = actual == reference
    return result, {
        "rule_kind": kind,
        "actual_operand": actual,
        "reference_operand": reference,
        "operator": "==",
        "result": result,
    }
