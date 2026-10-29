"""Assemble one compact, deterministic, auditable CS reproduction record."""

from __future__ import annotations

from typing import Any

from .identity_contract import (
    validate_code_identity,
    validate_environment_record,
    validate_execution_record,
    validate_resource_identities,
)
from .metric_contract import validate_actual_metrics, validate_reference_comparison

OUTPUT_SCHEMA_VERSION = "openair-reproduction-record/v1"
_INPUT_KEYS = {
    "code_identity",
    "resource_identities",
    "environment_record",
    "execution_record",
    "actual_metrics",
    "reference_comparison",
}


def _base_output() -> dict[str, Any]:
    return {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "state": "succeeded",
        "record_status": "incomplete",
        "reproduction_result": "indeterminate",
        "missing_components": [],
        "code_identity": None,
        "resource_identities": [],
        "resource_usage_verification": {"status": "not_evaluated", "resource_checks": []},
        "environment": None,
        "execution": None,
        "actual_metrics": None,
        "reference_comparison": None,
    }


def _failed(result: dict[str, Any], code: str) -> dict[str, Any]:
    result["state"] = "failed"
    result["record_status"] = "incomplete"
    result["reproduction_result"] = "indeterminate"
    result["failure_code"] = code
    return result


def _validate_outer_payload(payload: Any) -> bool:
    if not isinstance(payload, dict) or not _INPUT_KEYS.issubset(payload):
        return False
    if payload.get("resource_identities") is None:
        return False
    return True


def _missing_components(
    code: dict[str, Any] | None,
    environment: dict[str, Any] | None,
    execution: dict[str, Any] | None,
    actual: dict[str, Any] | None,
    comparison: dict[str, Any] | None,
) -> list[str]:
    missing: list[str] = []
    if code is None:
        missing.append("code_identity")
    if environment is None:
        missing.append("environment_record")
    if execution is None or execution.get("state") != "succeeded":
        missing.append("execution_record")
    if actual is None or actual.get("state") != "succeeded" or actual.get("extraction_status") != "complete":
        missing.append("actual_metrics")
    if comparison is None or comparison.get("state") != "succeeded" or comparison.get("comparison_status") != "complete":
        missing.append("reference_comparison")
    return missing


def run_reproduction_record_assembly(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate present upstream records, cross-check lineage, and assemble one record."""
    result = _base_output()
    if not _validate_outer_payload(payload):
        return _failed(result, "skill_input_invalid")

    code, error = validate_code_identity(payload["code_identity"])
    if error:
        return _failed(result, error)
    result["code_identity"] = code

    resources, error = validate_resource_identities(payload["resource_identities"])
    if error:
        return _failed(result, error)
    result["resource_identities"] = resources or []

    environment, error = validate_environment_record(payload["environment_record"])
    if error:
        return _failed(result, error)
    result["environment"] = environment

    execution, error = validate_execution_record(payload["execution_record"])
    if error:
        return _failed(result, error)
    result["execution"] = execution

    actual, error = validate_actual_metrics(payload["actual_metrics"])
    if error:
        return _failed(result, error)
    result["actual_metrics"] = actual

    comparison, error = validate_reference_comparison(payload["reference_comparison"])
    if error:
        return _failed(result, error)
    result["reference_comparison"] = comparison

    resource_usage_verification = {"status": "not_evaluated", "resource_checks": []}
    result["resource_usage_verification"] = resource_usage_verification

    missing = _missing_components(
        code,
        environment,
        execution,
        actual,
        comparison,
    )
    result["missing_components"] = missing
    if missing:
        return result

    result["record_status"] = "complete"
    result["reproduction_result"] = comparison["overall_result"]
    return result
