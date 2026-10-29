"""Fail-closed assembly of the exact input authorized for a reproduction run."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any

from app.execution.plan_resource_relation import verify_plan_resource_relation

_PLAN_ID = re.compile(r"execplan:[0-9a-f]{64}\Z")
_FINGERPRINT = re.compile(r"[0-9a-f]{64}\Z")


def _failure(code: str) -> dict[str, str]:
    return {"state": "failed", "failure_code": code}


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value) and value == value.strip() and "\x00" not in value


def _validated_code(plan: Mapping[str, Any], raw: Any) -> dict[str, Any] | None:
    return dict(raw) if verify_plan_resource_relation(plan, raw) == "verified" else None


def _validated_environment(raw: Any) -> dict[str, Any] | None:
    identity = raw.get("environment_identity") if isinstance(raw, Mapping) else None
    fingerprint = identity.get("fingerprint_sha256") if isinstance(identity, Mapping) else None
    if (
        not isinstance(raw, Mapping)
        or raw.get("schema_version") != "openair-environment-record/v1"
        or raw.get("state") != "succeeded"
        or raw.get("workspace_relative_path") != "environment"
        or raw.get("environment_record_relative_path") != "environment/environment-record.json"
        or not isinstance(fingerprint, str)
        or _FINGERPRINT.fullmatch(fingerprint) is None
    ):
        return None
    return dict(raw)


def _resource_rows(raw: Any) -> list[tuple[str, Mapping[str, Any]]] | None:
    if not isinstance(raw, Mapping) or raw.get("state") != "succeeded":
        return None
    rows = raw.get("resources")
    if not isinstance(rows, list) or not rows:
        return None
    normalized: list[tuple[str, Mapping[str, Any]]] = []
    for row in rows:
        result = row.get("result") if isinstance(row, Mapping) else None
        usage = row.get("usage") if isinstance(row, Mapping) else None
        if (
            not _text(usage)
            or not isinstance(result, Mapping)
            or result.get("state") != "succeeded"
        ):
            return None
        normalized.append((usage, result))
    return normalized


def _resource_matches_request(result: Mapping[str, Any], request: Mapping[str, Any]) -> bool:
    if (
        result.get("resource_kind") != request.get("resource_kind")
        or result.get("repo_id") != request.get("repo_id")
        or result.get("repo_type") != request.get("repo_type")
        or result.get("requested_revision") != request.get("revision")
    ):
        return False
    expected_commit = request.get("expected_commit_sha")
    if (
        expected_commit is not None
        and result.get("resolved_commit_sha") != str(expected_commit).lower()
    ):
        return False
    identity = result.get("actual_identity")
    if request.get("filename") is not None and (
        not isinstance(identity, Mapping) or identity.get("filename") != request.get("filename")
    ):
        return False
    expected_sha = request.get("expected_sha256")
    return expected_sha is None or (
        isinstance(identity, Mapping) and identity.get("sha256") == str(expected_sha).lower()
    )


def _binding_from_plan(
    plan: Mapping[str, Any], usage: str, result: Mapping[str, Any]
) -> dict[str, Any] | None:
    binding: dict[str, Any] = {"usage": usage, "resource": dict(result)}
    if "argv_index" in plan:
        binding["argv_index"] = plan.get("argv_index")
    elif plan.get("delivery") == {"kind": "tar_extract_working_directory"}:
        binding["delivery"] = {"kind": "tar_extract_working_directory"}
    else:
        return None
    if plan.get("consumption_probe") is not None:
        binding["consumption_probe"] = plan.get("consumption_probe")
    return binding


def _bindings(
    plan: Mapping[str, Any], raw_identities: Any
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]] | None:
    requests = plan.get("resource_acquisition_requests")
    plans = plan.get("execution_resource_binding_plan", [])
    if not isinstance(requests, list) or not isinstance(plans, list) or len(plans) > 2:
        return None
    if not requests and not plans:
        return [], []
    rows = _resource_rows(raw_identities)
    if rows is None or len(rows) != len(requests):
        return None

    by_usage: dict[str, Mapping[str, Any]] = {}
    for usage, result in rows:
        if usage in by_usage:
            return None
        by_usage[usage] = result

    bindings: list[dict[str, Any]] = []
    frozen: list[dict[str, Any]] = []
    used_indices: set[int] = set()
    for plan in plans:
        if not isinstance(plan, Mapping):
            return None
        index = plan.get("resource_request_index")
        usage = plan.get("usage")
        if (
            not isinstance(index, int)
            or isinstance(index, bool)
            or index < 0
            or index >= len(requests)
        ):
            return None
        if index in used_indices or usage not in {"model", "dataset"}:
            return None
        request = requests[index]
        if not isinstance(request, Mapping) or request.get("repo_type") != usage:
            return None
        result = by_usage.get(request.get("usage"))
        if result is None or not _resource_matches_request(result, request):
            return None
        binding = _binding_from_plan(plan, usage, result)
        if binding is None:
            return None
        bindings.append(binding)
        frozen.append({"request_index": index, "request": dict(request), "actual": dict(result)})
        used_indices.add(index)
    if len(used_indices) != len(requests):
        return None
    return bindings, frozen


def run_reproduction_execution_input_closure(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) not in (
        {
            "reproduction_experiment_design",
            "execution_plan_facts",
            "execution_readiness_record",
            "code_identity",
            "environment_record",
        },
        {
            "reproduction_experiment_design",
            "execution_plan_facts",
            "execution_readiness_record",
            "code_identity",
            "resource_identities",
            "environment_record",
        },
    ):
        return _failure("skill_input_invalid")
    design = payload.get("reproduction_experiment_design")
    plan = payload.get("execution_plan_facts")
    readiness = payload.get("execution_readiness_record")
    if not isinstance(design, Mapping) or design.get("status") != "ready":
        return _failure("experiment_design_not_ready")
    if (
        not isinstance(plan, Mapping)
        or plan.get("schema_version") != "openair-execution-plan/v1"
        or plan.get("state") != "succeeded"
        or plan.get("plan_status") != "complete"
    ):
        return _failure("execution_plan_not_ready")
    if (
        not isinstance(readiness, Mapping)
        or readiness.get("status") != "ready"
        or readiness.get("blocking_gaps") != []
        or not isinstance(readiness.get("executable_scope"), list)
        or not readiness.get("executable_scope")
    ):
        return _failure("execution_readiness_not_ready")
    plan_id = plan.get("execution_plan_id")
    if not isinstance(plan_id, str) or _PLAN_ID.fullmatch(plan_id) is None:
        return _failure("execution_plan_identity_invalid")
    expected_design_values = {
        "resource_acquisition_requests": plan.get("resource_acquisition_requests"),
        "execution_resource_binding_plan": plan.get("execution_resource_binding_plan"),
        "executable": plan.get("executable"),
        "argv": plan.get("argv"),
        "working_directory": plan.get("working_directory"),
        "timeout_seconds": plan.get("timeout_seconds"),
        "environment_variables": plan.get("environment_variables"),
        "actual_metric_result_source": plan.get("actual_metric_result_source"),
        "actual_metric_requests": plan.get("actual_metric_requests"),
    }
    if any(design.get(key) != value for key, value in expected_design_values.items()):
        return _failure("experiment_design_execution_facts_mismatch")
    code = _validated_code(plan, payload.get("code_identity"))
    environment = _validated_environment(payload.get("environment_record"))
    if code is None:
        return _failure("code_identity_mismatch")
    if environment is None:
        return _failure("environment_identity_invalid")
    assembled = _bindings(plan, payload.get("resource_identities"))
    if assembled is None:
        return _failure("resource_identity_mismatch")
    resource_bindings, frozen_resources = assembled

    executable = plan.get("executable")
    argv = plan.get("argv")
    working_directory = plan.get("working_directory")
    timeout_seconds = plan.get("timeout_seconds")
    environment_variables = plan.get("environment_variables", {})
    actual_metric_result_source = plan.get("actual_metric_result_source")
    actual_metric_requests = plan.get("actual_metric_requests")
    if (
        not _text(executable)
        or not isinstance(argv, list)
        or not all(isinstance(item, str) and "\x00" not in item for item in argv)
        or not _text(working_directory)
        or not isinstance(timeout_seconds, int)
        or isinstance(timeout_seconds, bool)
        or not 1 <= timeout_seconds <= 86400
        or not isinstance(environment_variables, Mapping)
        or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in environment_variables.items()
        )
        or not isinstance(actual_metric_result_source, Mapping)
        or not isinstance(actual_metric_requests, list)
        or not actual_metric_requests
    ):
        return _failure("execution_input_invalid")
    core = {
        "execution_plan_id": plan_id,
        "readiness_status": "ready",
        "executable": executable,
        "argv": list(argv),
        "working_directory": working_directory,
        "timeout_seconds": timeout_seconds,
        "environment_variables": dict(environment_variables),
        "resource_bindings": resource_bindings,
        "actual_metric_result_source": dict(actual_metric_result_source),
        "actual_metric_requests": list(actual_metric_requests),
        "frozen_identities": {
            "code": code,
            "environment": environment,
            "resources": frozen_resources,
        },
    }
    digest = hashlib.sha256(
        json.dumps(core, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "schema_version": "openair-reproduction-execution-input/v1",
        "state": "succeeded",
        **core,
        "frozen_input_sha256": digest,
    }
