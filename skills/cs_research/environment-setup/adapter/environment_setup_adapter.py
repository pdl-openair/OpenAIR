"""Build, verify, identify, and record one isolated Python environment."""

from __future__ import annotations

from typing import Any

from . import environment_contract as contract
from . import environment_identity as identity
from . import environment_runtime as runtime
from .environment_common import ENVIRONMENT_DIRNAME, RECORD_FILENAME, failure


def _failure_after_cleanup(target: Any, result: dict[str, Any]) -> dict[str, Any]:
    if runtime.cleanup_environment(target):
        return result
    return failure(
        "environment_cleanup_failed",
        "cleanup",
        cleanup_triggered_by=result.get("failure_code", "unknown"),
    )


def run_environment_setup(payload: dict[str, Any]) -> dict[str, Any]:
    """Create one trustworthy Python environment in the runtime-authorized workspace."""
    if not isinstance(payload, dict):
        return failure("skill_input_invalid", "input_validation")
    request = contract.validated_input(payload)
    if request is None:
        return failure("skill_input_invalid", "input_validation")

    workspace = contract.runtime_workspace()
    if workspace is None:
        return failure("workspace_boundary_invalid", "workspace")
    target, target_error = contract.environment_target(workspace)
    if target is None:
        return failure(target_error or "workspace_boundary_invalid", "environment_target")

    creation, error = runtime.create_environment(request, workspace=workspace, target=target)
    if error is not None:
        return _failure_after_cleanup(target, error)
    assert creation is not None

    python = contract.environment_python(target)
    if not python.is_file():
        return _failure_after_cleanup(
            target, failure("environment_python_missing", "environment_identity")
        )

    initial_facts, error = identity.read_environment_facts(python, workspace=workspace)
    if error is not None:
        return _failure_after_cleanup(target, error)
    assert initial_facts is not None
    if not contract.requested_python_matches(request["python_version"], initial_facts["python"]):
        return _failure_after_cleanup(
            target,
            failure(
                "python_version_mismatch",
                "python_version_verification",
                requested_python_version=request["python_version"],
                actual_python_version=initial_facts["python"].get("version"),
            ),
        )

    error = runtime.install_requirements(request, python=python, workspace=workspace)
    if error is not None:
        return _failure_after_cleanup(target, error)

    error = runtime.pip_check(python, workspace=workspace)
    if error is not None:
        return _failure_after_cleanup(target, error)

    imports, error = runtime.verify_imports(
        request["verify_imports"], python=python, workspace=workspace
    )
    if error is not None:
        return _failure_after_cleanup(target, error)
    assert imports is not None

    readiness: dict[str, Any]
    probe = request["runtime_readiness_probe"]
    if probe is None:
        readiness = {"configured": False, "status": "not_configured"}
    else:
        readiness_result, error = runtime.verify_runtime_readiness(
            probe, target=target, workspace=workspace
        )
        if error is not None:
            return _failure_after_cleanup(target, error)
        assert readiness_result is not None
        readiness = readiness_result

    actual_facts, error = identity.read_environment_facts(python, workspace=workspace)
    if error is not None:
        return _failure_after_cleanup(target, error)
    assert actual_facts is not None
    environment_identity = identity.identity_with_fingerprint(actual_facts)

    operations = [
        {"operation": "create_environment", "exit_code": creation["exit_code"]},
        {
            "operation": "install_requirements",
            "exit_code": 0,
            "timeout_seconds": request["dependency_install_timeout_seconds"],
        },
        {"operation": "pip_check", "exit_code": 0},
        {"operation": "verify_imports", "exit_code": 0},
    ]
    if probe is not None:
        operations.append({"operation": "runtime_readiness_probe", "exit_code": 0})
    operations.append({"operation": "read_environment_identity", "exit_code": 0})

    record = {
        "schema_version": "openair-environment-record/v1",
        "state": "succeeded",
        "environment_identity": environment_identity,
        "verification": {
            "requested_python_version": request["python_version"],
            "python_version_match": True,
            "pip_check": "passed",
            "imports": imports,
            "runtime_readiness": readiness,
        },
        "execution_evidence": {
            "builder": creation["builder"],
            "operations": operations,
            "pip_version": identity.pip_version(actual_facts),
        },
        "workspace_relative_path": ENVIRONMENT_DIRNAME,
        "environment_record_relative_path": f"{ENVIRONMENT_DIRNAME}/{RECORD_FILENAME}",
    }
    if not identity.write_record_atomically(target, record):
        return _failure_after_cleanup(
            target, failure("environment_record_write_failed", "environment_record")
        )

    return record
