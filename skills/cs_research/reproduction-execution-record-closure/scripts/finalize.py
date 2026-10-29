"""Restore execution facts and verify them against frozen execution input."""

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


def _trace(raw: Mapping[str, Any]) -> list[dict[str, Any]]:
    trace: list[dict[str, Any]] = []
    for stream in ("stdout", "stderr"):
        capture = raw.get(stream)
        if isinstance(capture, Mapping):
            trace.append({"stream": stream, "capture": deepcopy(dict(capture))})
    return trace


def _provenance(raw: Mapping[str, Any], metrics: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    path = raw.get("execution_record_relative_path")
    if isinstance(path, str) and path:
        result["execution_record_relative_path"] = path
    if isinstance(metrics, Mapping):
        extracted = {
            key: deepcopy(metrics[key])
            for key in (
                "execution_record_relative_path",
                "execution_record_sha256",
                "result_source",
            )
            if key in metrics
        }
        if extracted:
            result["actual_metric_extraction"] = extracted
    return result


def _source_failures(raw: Any, consistency: Mapping[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(raw, Mapping):
        return [{"scope": "execution", "failure_code": "raw_execution_record_missing"}]
    failures: list[dict[str, Any]] = []
    if raw.get("state") != "succeeded":
        item = {"scope": "execution"}
        for key in ("state", "execution_status", "failure_code"):
            if key in raw:
                item[key] = deepcopy(raw[key])
        failures.append(item)
    for check in consistency.get("checks", []):
        if isinstance(check, Mapping) and check.get("status") != "consistent":
            failures.append({"scope": "frozen_input", **deepcopy(dict(check))})
    return failures


def _unknowns(draft: Mapping[str, Any], raw: Any, consistency: Mapping[str, Any]) -> list[str]:
    values = deepcopy(draft.get("unknowns", []))
    result = values if isinstance(values, list) else [values]
    if not isinstance(raw, Mapping):
        result.append("raw_execution_record_unavailable")
    for check in consistency.get("checks", []):
        if isinstance(check, Mapping) and check.get("status") == "unknown":
            result.append(f"frozen_input_{check.get('field')}_unknown")
    return result


def finalize(
    input_payload: Mapping[str, Any],
    draft_payload: Mapping[str, Any],
) -> FinalizationResult:
    """Keep scientific unknowns while restoring exact execution facts."""
    if not isinstance(input_payload, Mapping) or not isinstance(draft_payload, Mapping):
        raise ValueError("execution_record_finalization_payload_invalid")

    raw = input_payload.get("raw_execution_record")
    consistency = {"status": "not_evaluated", "checks": []}
    execution = deepcopy(dict(raw)) if isinstance(raw, Mapping) else None
    if not isinstance(raw, Mapping):
        status = "failed"
        trace: list[dict[str, Any]] = []
        provenance: dict[str, Any] = {}
    else:
        status = (
            "failed"
            if raw.get("state") != "succeeded"
            else "succeeded"
        )
        trace = _trace(raw)
        provenance = _provenance(raw, input_payload.get("raw_actual_metrics"))
    output: dict[str, Any] = {
        "status": status,
        "execution": execution,
        "frozen_input_consistency": consistency,
        "raw_output_trace": trace,
        "actual_result_provenance": provenance,
        "partial_failures": _source_failures(raw, consistency),
        "unknowns": _unknowns(draft_payload, raw, consistency),
    }
    return FinalizationResult("succeeded", output)
