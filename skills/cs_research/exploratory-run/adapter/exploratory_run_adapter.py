"""Execute one already-defined experiment and persist its actual execution facts."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import time
from typing import Any
import uuid

from .execution_common import (
    ENVIRONMENT_DIRNAME,
    ENVIRONMENT_RECORD_RELATIVE_PATH,
    EXECUTION_RECORD_FILENAME,
    EXECUTION_ROOT_DIRNAME,
    EXECUTION_SCHEMA_VERSION,
    failure,
    load_verified_environment,
    relative_path_parts,
    resolve_environment_executable,
    resolve_working_directory,
    runtime_workspace,
    sensitive_values,
    redact_text,
    validated_input,
    workspace_relative,
)
from .execution_process import execute_process
from .resource_access_probe import prepare_python_access_probe
from .execution_resources import (
    bind_resources,
    cleanup_bound_resources,
    persist_bound_delivery_evidence,
)
from .resource_consumption import (
    evidence_environment_names,
    finalize_resource_usage,
    prepare_consumption_evidence,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_text(value: datetime) -> str:
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _execution_directory(workspace: Path, started: datetime) -> tuple[Path | None, dict[str, Any] | None]:
    root = workspace / EXECUTION_ROOT_DIRNAME
    try:
        if root.is_symlink():
            return None, failure("execution_record_path_invalid", "record")
        root.mkdir(exist_ok=True)
        resolved_root = root.resolve(strict=True)
        resolved_root.relative_to(workspace.resolve(strict=True))
        execution_id = f"exec-{started.strftime('%Y%m%dT%H%M%S%fZ')}-{uuid.uuid4().hex[:8]}"
        run_dir = resolved_root / execution_id
        run_dir.mkdir()
    except (OSError, ValueError):
        return None, failure("execution_record_path_invalid", "record")
    return run_dir, None


def _write_record(run_dir: Path, record: dict[str, Any]) -> Path:
    target = run_dir / EXECUTION_RECORD_FILENAME
    serialized = json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    fd, raw_temp = tempfile.mkstemp(prefix=".execution-record-", suffix=".tmp", dir=str(run_dir))
    temp = Path(raw_temp)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        temp.replace(target)
    except Exception:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return target


def _record_status(process_result: dict[str, object]) -> tuple[str, str, str | None]:
    if not process_result["started"]:
        return "failed", "process_start_failed", "process_start_failed"
    if process_result.get("timed_out"):
        if not process_result.get("termination_succeeded"):
            return "failed", "termination_failed", "process_termination_failed"
        return "failed", "timeout", "execution_timeout"
    exit_code = process_result.get("exit_code")
    if exit_code != 0:
        return "failed", "non_zero_exit", "non_zero_exit"
    return "succeeded", "succeeded", None


def _validated_request(payload: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if isinstance(payload, dict):
        if "working_directory" in payload and relative_path_parts(payload.get("working_directory")) is None:
            return None, failure("working_directory_invalid", "working_directory")
        if "executable" in payload and relative_path_parts(payload.get("executable")) is None:
            return None, failure("executable_not_found_or_invalid", "executable")

    request = validated_input(payload)
    if request is None or evidence_environment_names().intersection(request["environment_variables"]):
        return None, failure("skill_input_invalid", "input")
    return request, None


def _resolved_execution_inputs(
    workspace: Path,
    request: dict[str, Any],
) -> tuple[dict[str, Any] | None, Path | None, Path | None, dict[str, Any] | None]:
    environment_record, environment_error = load_verified_environment(workspace)
    if environment_record is None:
        return None, None, None, environment_error or failure("verified_environment_unavailable", "environment")

    working_directory = resolve_working_directory(workspace, request["working_directory_parts"])
    if working_directory is None:
        return None, None, None, failure("working_directory_invalid", "working_directory")

    executable = resolve_environment_executable(workspace, request["executable_parts"])
    if executable is None:
        return None, None, None, failure("executable_not_found_or_invalid", "executable")
    return environment_record, working_directory, executable, None


def _prepare_evidence_environment(
    run_dir: Path,
    workspace: Path,
    request: dict[str, Any],
    resource_usage: list[dict[str, Any]],
    probe_targets: list[dict[str, Any]],
) -> tuple[dict[str, str] | None, dict[str, Path] | None]:
    try:
        evidence_environment, evidence_paths = prepare_consumption_evidence(run_dir, resource_usage)
    except OSError:
        return None, None

    child_environment = os.environ.copy()
    child_environment.update(request["environment_variables"])
    child_environment.update(evidence_environment)
    probe_environment = prepare_python_access_probe(
        run_dir,
        workspace,
        resource_usage,
        probe_targets,
        evidence_paths,
        child_environment.get("PYTHONPATH"),
    )
    if probe_environment is None:
        return None, None
    child_environment.update(probe_environment)
    return child_environment, evidence_paths


def _build_execution_record(
    *,
    workspace: Path,
    environment_record: dict[str, Any],
    working_directory: Path,
    executable: Path,
    request: dict[str, Any],
    actual_argv: list[str],
    resource_usage: list[dict[str, Any]],
    evidence_paths: dict[str, Path],
    probe_targets: list[dict[str, Any]],
    process_result: dict[str, Any],
    started: datetime,
    ended: datetime,
    duration_seconds: float,
    secrets: list[str],
) -> dict[str, Any]:
    state, execution_status, failure_code = _record_status(process_result)
    identity = environment_record["environment_identity"]
    record: dict[str, Any] = {
        "schema_version": EXECUTION_SCHEMA_VERSION,
        "state": state,
        "execution_status": execution_status,
        "actual_executable": workspace_relative(executable, workspace),
        "argv": [redact_text(value, secrets) for value in actual_argv],
        "working_directory": workspace_relative(working_directory, workspace),
        "environment": {
            "workspace_relative_path": ENVIRONMENT_DIRNAME,
            "environment_record_relative_path": ENVIRONMENT_RECORD_RELATIVE_PATH,
            "fingerprint_sha256": identity["fingerprint_sha256"],
        },
        "environment_variable_names": sorted(request["environment_variables"]),
        "started_at_utc": _utc_text(started),
        "ended_at_utc": _utc_text(ended),
        "duration_seconds": duration_seconds,
        "exit_code": process_result.get("exit_code"),
        "timed_out": bool(process_result.get("timed_out", False)),
        "stdout": process_result["stdout_capture"].as_record(secrets),
        "stderr": process_result["stderr_capture"].as_record(secrets),
    }
    if resource_usage and process_result.get("started"):
        record["resource_usage"] = finalize_resource_usage(
            workspace,
            resource_usage,
            evidence_paths,
            probe_targets,
        )
    if process_result.get("started"):
        record["process_id"] = process_result.get("pid")
    else:
        record["process_start_error"] = {
            "type": process_result.get("start_error_type"),
            "message": redact_text(str(process_result.get("start_error_message") or ""), secrets),
        }
    if process_result.get("termination_evidence") is not None:
        record["termination_evidence"] = process_result["termination_evidence"]
    if failure_code is not None:
        record["failure_code"] = failure_code
    return record


def run_exploratory_run(payload: dict[str, Any]) -> dict[str, Any]:
    request, request_error = _validated_request(payload)
    if request is None:
        return request_error or failure("skill_input_invalid", "input")

    workspace = runtime_workspace()
    if workspace is None:
        return failure("workspace_boundary_invalid", "workspace")

    environment_record, working_directory, executable, input_error = _resolved_execution_inputs(workspace, request)
    if environment_record is None or working_directory is None or executable is None:
        return input_error or failure("verified_environment_unavailable", "environment")

    actual_argv, resource_usage, delivery_states, probe_targets = bind_resources(
        workspace,
        working_directory,
        request["argv"],
        request["resource_bindings"],
    )
    if actual_argv is None or resource_usage is None:
        return failure("resource_binding_unavailable", "resource_binding")

    started = _utc_now()
    run_dir, record_error = _execution_directory(workspace, started)
    if run_dir is None:
        cleanup_bound_resources(delivery_states)
        return record_error or failure("execution_record_path_invalid", "record")

    if not persist_bound_delivery_evidence(run_dir, workspace, resource_usage, delivery_states):
        cleanup_bound_resources(delivery_states)
        return failure("resource_evidence_unavailable", "resource_binding")

    child_environment, evidence_paths = _prepare_evidence_environment(
        run_dir,
        workspace,
        request,
        resource_usage,
        probe_targets,
    )
    if child_environment is None or evidence_paths is None:
        cleanup_bound_resources(delivery_states)
        return failure("resource_evidence_unavailable", "resource_binding")

    secrets = sensitive_values(child_environment)
    monotonic_started = time.monotonic()
    process_result = execute_process(
        executable,
        actual_argv,
        cwd=working_directory,
        environment=child_environment,
        timeout_seconds=request["timeout_seconds"],
    )
    duration_seconds = round(time.monotonic() - monotonic_started, 6)
    ended = _utc_now()

    try:
        record = _build_execution_record(
            workspace=workspace,
            environment_record=environment_record,
            working_directory=working_directory,
            executable=executable,
            request=request,
            actual_argv=actual_argv,
            resource_usage=resource_usage,
            evidence_paths=evidence_paths,
            probe_targets=probe_targets,
            process_result=process_result,
            started=started,
            ended=ended,
            duration_seconds=duration_seconds,
            secrets=secrets,
        )
    finally:
        cleanup_bound_resources(delivery_states)

    try:
        record_path = _write_record(run_dir, record)
    except OSError as error:
        return failure(
            "execution_record_failed",
            "record",
            execution_status=record["execution_status"],
            error_type=type(error).__name__,
        )

    result = dict(record)
    result["execution_record_relative_path"] = workspace_relative(record_path, workspace)
    return result
