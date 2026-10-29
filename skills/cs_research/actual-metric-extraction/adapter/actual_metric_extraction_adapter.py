"""Deterministically extract declared actual metrics from real execution output."""

from __future__ import annotations

from typing import Any

from .metric_contract import validated_input
from .metric_extraction_common import OUTPUT_SCHEMA_VERSION
from .metric_source import load_execution_record, load_result_source, runtime_workspace
from .metric_values import failed_metric
from .json_result_parser import extract_json_metrics
from .parser_errors import ParseFailure
from .tabular_metric_extractor import extract_table_metrics


def _base_result(request: dict[str, Any], execution: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "execution_record_relative_path": request["execution_record_relative_path"],
        "execution_record_sha256": execution["sha256"],
        "result_source": dict(source["trace"]),
        "requested_metric_count": len(request["metrics"]),
    }


def _source_failure_result(
    request: dict[str, Any],
    execution_sha256: str | None,
    code: str,
    stage: str,
) -> dict[str, Any]:
    source = request["result_source"]
    result_source: dict[str, Any] = {
        "kind": source["kind"],
        "format": source["format"],
        "sha256": None,
    }
    if source["kind"] == "execution_stream":
        result_source["stream"] = source["stream"]
    else:
        result_source["relative_path"] = source["relative_path"]
    return {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "state": "failed",
        "extraction_status": "incomplete",
        "failure_code": code,
        "failed_stage": stage,
        "execution_record_relative_path": request["execution_record_relative_path"],
        "execution_record_sha256": execution_sha256,
        "result_source": result_source,
        "requested_metric_count": len(request["metrics"]),
        "extracted_metric_count": 0,
        "metrics": [failed_metric(metric, code) for metric in request["metrics"]],
    }


def run_actual_metric_extraction(payload: dict[str, Any]) -> dict[str, Any]:
    request = validated_input(payload)
    if request is None:
        return {
            "schema_version": OUTPUT_SCHEMA_VERSION,
            "state": "failed",
            "extraction_status": "incomplete",
            "failure_code": "skill_input_invalid",
            "failed_stage": "input",
            "execution_record_relative_path": payload.get("execution_record_relative_path") if isinstance(payload, dict) and isinstance(payload.get("execution_record_relative_path"), str) else None,
            "execution_record_sha256": None,
            "result_source": None,
            "requested_metric_count": len(payload.get("metrics") or []) if isinstance(payload, dict) and isinstance(payload.get("metrics"), list) else 0,
            "extracted_metric_count": 0,
            "metrics": [],
        }

    workspace = runtime_workspace()
    if workspace is None:
        return _source_failure_result(request, None, "workspace_boundary_invalid", "workspace")

    execution, execution_error = load_execution_record(workspace, request)
    if execution is None:
        return _source_failure_result(
            request,
            None,
            execution_error.get("failure_code", "execution_record_invalid"),
            execution_error.get("failed_stage", "execution_record"),
        )

    source, source_error = load_result_source(workspace, execution, request["result_source"])
    if source is None:
        return _source_failure_result(
            request,
            execution["sha256"],
            source_error.get("failure_code", "result_source_unavailable"),
            source_error.get("failed_stage", "source"),
        )

    try:
        if request["result_source"]["format"] == "json":
            parsed = extract_json_metrics(source["text"], request["metrics"])
        else:
            parsed = extract_table_metrics(
                source["text"],
                request["result_source"]["format"],
                request["result_source"].get("table", {}),
                request["metrics"],
            )
    except ParseFailure as error:
        result = _base_result(request, execution, source)
        result.update({
            "state": "failed",
            "extraction_status": "incomplete",
            "failure_code": error.code,
            "failed_stage": "parse",
            "extracted_metric_count": 0,
            "metrics": [failed_metric(metric, error.code) for metric in request["metrics"]],
        })
        return result

    metric_results: list[dict[str, Any]] = []
    successful = 0
    for row in parsed:
        metric = row["metric"]
        if "failure_code" in row:
            metric_results.append(failed_metric(metric, row["failure_code"]))
            continue
        successful += 1
        metric_results.append({
            "metric_name": metric["metric_name"],
            "actual_value": row["actual_value"],
            "value_type": metric["value_type"],
            "unit": metric["unit"],
            "extraction_status": "succeeded",
            "source_trace": {
                "execution_record_relative_path": request["execution_record_relative_path"],
                "source_kind": source["trace"]["kind"],
                "source_sha256": source["trace"]["sha256"],
                "format": source["trace"]["format"],
                **({"stream": source["trace"]["stream"]} if "stream" in source["trace"] else {}),
                **({"relative_path": source["trace"]["relative_path"]} if "relative_path" in source["trace"] else {}),
                **row["source_trace"],
            },
        })

    complete = successful == len(request["metrics"])
    result = _base_result(request, execution, source)
    result.update({
        "state": "succeeded" if complete else "failed",
        "extraction_status": "complete" if complete else "incomplete",
        "extracted_metric_count": successful,
        "metrics": metric_results,
    })
    if not complete:
        result["failure_code"] = "metric_extraction_incomplete"
        result["failed_stage"] = "metrics"
    return result
