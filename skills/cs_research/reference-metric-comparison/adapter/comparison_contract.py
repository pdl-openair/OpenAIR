"""Required-key structure and metric identity joins for comparison execution."""
from __future__ import annotations
from typing import Any

def normalized_rule(raw: Any, value_type: str) -> tuple[dict[str, Any] | None, str | None]:
    if not isinstance(raw, dict) or "kind" not in raw:
        return None, "comparison_rule_kind_missing"
    return dict(raw), None


def validated_actual_metrics_document(actual: Any) -> list[dict[str, Any]] | None:
    if not isinstance(actual, dict) or "metrics" not in actual or not isinstance(actual["metrics"], list):
        return None
    required = {"metric_name", "value_type", "unit", "extraction_status"}
    if any(not isinstance(row, dict) or not required.issubset(row) for row in actual["metrics"]):
        return None
    return [dict(row) for row in actual["metrics"]]


def validated_actual_metric_records(records: Any) -> list[dict[str, Any]] | None:
    if not isinstance(records, dict) or not {"status", "execution_record_ref", "metrics", "reference_values_used", "partial_failures", "unknowns"}.issubset(records):
        return None
    return validated_actual_metrics_document(records)


def validated_payload(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict) or "reference_metrics" not in payload:
        return None
    if ("actual_metrics" in payload) == ("actual_metric_records" in payload):
        return None
    actual = (validated_actual_metrics_document(payload.get("actual_metrics")) if "actual_metrics" in payload else validated_actual_metric_records(payload.get("actual_metric_records")))
    references = payload["reference_metrics"]
    required = {"metric_name", "reference_value", "value_type", "unit", "comparison_rule"}
    if actual is None or not isinstance(references, list) or any(not isinstance(row, dict) or not required.issubset(row) for row in references):
        return None
    return {"actual_metrics": actual, "reference_metrics": [dict(row) for row in references]}
