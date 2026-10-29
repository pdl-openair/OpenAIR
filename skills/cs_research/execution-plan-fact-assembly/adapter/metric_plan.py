"""Join result-source semantics to already-formal reference metric identities."""
from __future__ import annotations

from typing import Any, Mapping

from .plan_common import AssemblyContext, safe_relative

def validate_reference_metrics(rows: list[Any]) -> dict[str, Mapping[str, Any]] | None:
    by_id: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping) or "reference_id" not in row:
            return None
        by_id[str(row["reference_id"])] = row
    return by_id


def _result_source(
    row: Mapping[str, Any],
    *,
    target: str,
    context: AssemblyContext,
) -> tuple[dict[str, Any] | None, list[str]]:
    context.basis_for_excerpt(target=target, document_id=row.get("source_document_id"), excerpt=row.get("source_excerpt"))
    format_name = row.get("format")
    table = row.get("table")

    if row.get("kind") == "execution_stream" and row.get("stream") in {"stdout", "stderr"}:
        source = {"kind": "execution_stream", "stream": row["stream"], "format": format_name}
        runtime_pending: list[str] = []
    elif row.get("kind") == "workspace_file":
        relative_path = safe_relative(row.get("relative_path"), allow_dot=False)
        if relative_path is None:
            context.add_unresolved(target, "result_source_invalid")
            context.provenance.pop()
            return None, []
        source = {"kind": "workspace_file", "relative_path": relative_path, "format": format_name}
        runtime_pending = ["expected_sha256"]
    else:
        context.add_unresolved(target, "result_source_invalid")
        context.provenance.pop()
        return None, []
    if table is not None:
        source["table"] = table
    return source, runtime_pending


def assemble_metric_plan(
    jobs: list[Any],
    *,
    reference_metrics: Mapping[str, Mapping[str, Any]],
    context: AssemblyContext,
) -> dict[str, Any]:
    metric_jobs: list[dict[str, Any]] = []
    flat_requests: list[dict[str, Any]] = []
    covered_reference_ids: set[str] = set()

    for index, job in enumerate(jobs):
        target = f"metric_extraction_jobs[{index}]"
        if not isinstance(job, Mapping) or not isinstance(job.get("result_source"), Mapping):
            context.add_unresolved(target, "metric_job_invalid")
            continue
        result_source, runtime_pending = _result_source(
            job["result_source"],
            target=target + ".result_source",
            context=context,
        )
        if result_source is None:
            continue

        selectors: dict[str, dict[str, Any]] = {}
        selectors_valid = True
        raw_selectors = job.get("selectors", [])
        if not isinstance(raw_selectors, list):
            selectors_valid = False
        else:
            for raw in raw_selectors:
                if not isinstance(raw, Mapping) or not {"reference_id", "selector"}.issubset(raw):
                    selectors_valid = False
                    break
                reference_id = raw.get("reference_id")
                selector = raw["selector"]
                if not isinstance(reference_id, str) or selector is None:
                    selectors_valid = False
                    break
                selectors[reference_id] = selector
        if not selectors_valid:
            context.add_unresolved(target + ".selectors", "selector_invalid")
            continue

        reference_ids = job.get("reference_ids")
        if not isinstance(reference_ids, list):
            context.add_unresolved(target + ".reference_ids", "reference_ids_invalid")
            continue
        requests: list[dict[str, Any]] = []
        for reference_id in reference_ids:
            reference = reference_metrics.get(str(reference_id))
            if reference is None:
                context.add_unresolved(
                    target + ".reference_ids",
                    f"unknown_reference_id:{reference_id}",
                )
                continue
            request = {
                "metric_name": reference["metric_name"],
                "value_type": reference["value_type"],
                "unit": reference.get("unit"),
            }
            if reference_id in selectors:
                request["selector"] = selectors[reference_id]
            covered_reference_ids.add(reference_id)
            requests.append(request)
            flat_requests.append(request)
        if requests:
            metric_jobs.append(
                {
                    "result_source_plan": result_source,
                    "metrics": requests,
                    "runtime_pending": runtime_pending,
                }
            )
            provenance_target = target + ".result_source"
            if context.provenance and context.provenance[-1].get("target") == provenance_target:
                context.provenance[-1]["target"] = f"metric_extraction_jobs[{len(metric_jobs) - 1}]"

    for reference_id in reference_metrics:
        if reference_id not in covered_reference_ids:
            context.add_unresolved(
                "metric_extraction_jobs",
                f"reference_metric_without_extraction_plan:{reference_id}",
            )

    runtime_pending = ["execution_record_relative_path"]
    if any("expected_sha256" in row["runtime_pending"] for row in metric_jobs):
        runtime_pending.append("workspace_result_expected_sha256")
    actual_metric_result_source = None
    if len(metric_jobs) == 1 and metric_jobs[0]["result_source_plan"].get("kind") == "execution_stream":
        actual_metric_result_source = metric_jobs[0]["result_source_plan"]
    elif metric_jobs:
        runtime_pending.append("actual_metric_result_source_materialization")
    return {
        "actual_metric_result_source": actual_metric_result_source,
        "actual_metric_requests": flat_requests,
        "metric_extraction_jobs": metric_jobs,
        "metric_runtime_pending": runtime_pending,
    }
