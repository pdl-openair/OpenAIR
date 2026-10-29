"""Convert parseable metric representations while retaining other source values."""
from __future__ import annotations
from decimal import Decimal, InvalidOperation
from typing import Any


def coerce_text_value(raw: str, value_type: str) -> tuple[Any | None, str | None]:
    text = raw.strip()
    if value_type == "boolean" and text in {"true", "false"}:
        return text == "true", None
    if value_type in {"integer", "number"}:
        try:
            return (int(text) if value_type == "integer" else float(Decimal(text))), None
        except (ValueError, InvalidOperation, OverflowError):
            return raw, None
    return (text if value_type == "string" else raw), None


def validate_json_value(value: Any, value_type: str) -> tuple[Any | None, str | None]:
    return value, None


def failed_metric(metric: dict[str, Any], code: str) -> dict[str, Any]:
    return {"metric_name": metric["metric_name"], "actual_value": None, "value_type": metric["value_type"],
            "unit": metric["unit"], "extraction_status": "failed", "failure_code": code}
