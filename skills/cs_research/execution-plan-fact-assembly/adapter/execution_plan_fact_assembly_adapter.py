"""Deterministically assemble a source-grounded reproduction execution plan."""
from __future__ import annotations

from typing import Any, Mapping

from .environment_plan import assemble_environment
from .execution_resource_plan import assemble_execution, assemble_resources
from .metric_plan import assemble_metric_plan, validate_reference_metrics
from .plan_common import (
    AssemblyContext,
    SCHEMA_VERSION,
    dedupe_unresolved,
    documents_from_source,
    plan_id,
)


def _validated_input(
    payload: Any,
) -> tuple[Mapping[str, Any], Mapping[str, Any], list[Any]] | tuple[None, None, None]:
    if not isinstance(payload, dict) or set(payload) != {
        "source_package",
        "interpretation",
        "reference_metrics",
    }:
        return None, None, None
    source = payload.get("source_package")
    interpretation = payload.get("interpretation")
    reference_metrics = payload.get("reference_metrics")
    if (
        not isinstance(source, Mapping)
        or not isinstance(interpretation, Mapping)
        or not isinstance(reference_metrics, list)
    ):
        return None, None, None
    return source, interpretation, reference_metrics


def _valid_interpretation(value: Mapping[str, Any]) -> bool:
    required = {"resources", "environment", "execution", "metric_extraction_jobs", "unresolved"}
    return bool(
        required.issubset(value)
        and isinstance(value.get("resources"), list)
        and isinstance(value.get("environment"), Mapping)
        and isinstance(value.get("execution"), Mapping)
        and isinstance(value.get("metric_extraction_jobs"), list)
        and isinstance(value.get("unresolved"), list)
    )


def run_execution_plan_fact_assembly(payload: dict[str, Any]) -> dict[str, Any]:
    source, interpretation, raw_reference_metrics = _validated_input(payload)
    if source is None or interpretation is None or raw_reference_metrics is None:
        return {"state": "failed", "failure_code": "skill_input_invalid"}
    documents = documents_from_source(source)
    if documents is None:
        return {"state": "failed", "failure_code": "source_package_invalid"}
    reference_metrics = validate_reference_metrics(raw_reference_metrics)
    if reference_metrics is None:
        return {"state": "failed", "failure_code": "reference_metrics_invalid"}
    if not _valid_interpretation(interpretation):
        return {"state": "failed", "failure_code": "interpretation_invalid"}

    context = AssemblyContext(source_package=source, documents=documents)
    for row in interpretation["unresolved"]:
        if isinstance(row, Mapping) and isinstance(row.get("target"), str) and isinstance(row.get("reason"), str):
            context.add_unresolved(row["target"], row["reason"])

    environment = assemble_environment(interpretation["environment"], context)
    execution = assemble_execution(
        interpretation["execution"],
        workspace_relative_path=source.get("workspace_relative_path"),
        context=context,
    )
    resources = assemble_resources(
        interpretation["resources"],
        entrypoint=execution["entrypoint"],
        arguments=execution["arguments"],
        context=context,
    )
    metrics = assemble_metric_plan(
        interpretation["metric_extraction_jobs"],
        reference_metrics=reference_metrics,
        context=context,
    )

    runtime_pending = list(metrics.pop("metric_runtime_pending"))
    if resources["execution_resource_binding_plan"]:
        runtime_pending.append("resource_bindings")
    unresolved = dedupe_unresolved(context.unresolved)
    core = {
        "source_identity": {
            "repository": source.get("repository"),
            "resolved_commit_sha": source.get("resolved_commit_sha"),
        },
        **resources,
        **environment,
        "executable": execution["executable"],
        "working_directory": execution["working_directory"],
        "timeout_seconds": execution["timeout_seconds"],
        "environment_variables": execution["environment_variables"],
        **metrics,
        "execution_plan_provenance": context.provenance,
        "execution_plan_unresolved": unresolved,
        "execution_plan_runtime_pending": sorted(set(runtime_pending)),
    }
    identifier = plan_id(core)
    return {
        "schema_version": SCHEMA_VERSION,
        "state": "succeeded",
        "plan_status": "complete" if not unresolved else "incomplete",
        "execution_plan_id": identifier,
        **core,
    }
