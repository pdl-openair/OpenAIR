"""Restore actual metric facts from their deterministic extraction source."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

@dataclass(frozen=True)
class FinalizationResult:
    status: str
    output: dict[str, Any]
    errors: tuple[dict[str, str], ...] = ()


def _unknowns(draft: Mapping[str, Any], additions: list[str]) -> list[str]:
    values = deepcopy(draft.get("unknowns", []))
    retained = values if isinstance(values, list) else [values]
    for item in additions:
        if item not in retained:
            retained.append(item)
    return retained


def _reference(source: Mapping[str, Any], raw_execution: Any) -> tuple[str | None, list[str]]:
    path = source.get("execution_record_relative_path")
    digest = source.get("execution_record_sha256")
    return (f"{path}#sha256={digest}", []) if path is not None else (None, [])


def _source_failure(source: Mapping[str, Any]) -> dict[str, Any] | None:
    if source.get("state") == "succeeded" and source.get("extraction_status") == "complete":
        return None
    failure: dict[str, Any] = {"scope": "actual_metric_extraction"}
    for key in (
        "state",
        "extraction_status",
        "failure_code",
        "failed_stage",
        "requested_metric_count",
        "extracted_metric_count",
    ):
        if key in source:
            failure[key] = deepcopy(source[key])
    return failure


def _metric_failures(metrics: list[Any]) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    for index, metric in enumerate(metrics):
        if not isinstance(metric, Mapping) or metric.get("extraction_status") != "failed":
            continue
        item: dict[str, Any] = {"scope": "metric", "metric_index": index}
        for key in ("metric_name", "failure_code"):
            if key in metric:
                item[key] = deepcopy(metric[key])
        failures.append(item)
    return failures


def _status(source: Mapping[str, Any], metrics: list[Any], reference: str | None) -> str:
    if source.get("state") == "succeeded" and source.get("extraction_status") == "complete":
        return "complete"
    if any(
        isinstance(row, Mapping) and row.get("extraction_status") == "succeeded" for row in metrics
    ):
        return "incomplete"
    return "failed"


def finalize(
    input_payload: Mapping[str, Any],
    draft_payload: Mapping[str, Any],
) -> FinalizationResult:
    """Keep model unknowns while restoring source-owned metric records."""
    if not isinstance(input_payload, Mapping) or not isinstance(draft_payload, Mapping):
        raise ValueError("actual_metric_finalization_payload_invalid")

    source = input_payload.get("raw_actual_metrics")
    raw_execution = input_payload.get("raw_execution_record")
    if not isinstance(source, Mapping) or not isinstance(source.get("metrics"), list):
        output: dict[str, Any] = {
            "status": "failed",
            "execution_record_ref": None,
            "metrics": [],
            "reference_values_used": False,
            "partial_failures": [
                {"scope": "actual_metric_extraction", "failure_code": "raw_actual_metrics_missing"}
            ],
            "unknowns": _unknowns(draft_payload, ["raw_actual_metrics_unavailable"]),
        }
    else:
        metrics = deepcopy(source["metrics"])
        reference, source_unknowns = _reference(source, raw_execution)
        failures = _metric_failures(metrics)
        extraction_failure = _source_failure(source)
        if extraction_failure is not None:
            failures.insert(0, extraction_failure)
        output = {
            "status": _status(source, metrics, reference),
            "execution_record_ref": reference,
            "metrics": metrics,
            "reference_values_used": False,
            "partial_failures": failures,
            "unknowns": _unknowns(draft_payload, source_unknowns),
        }
    return FinalizationResult("succeeded", output)
