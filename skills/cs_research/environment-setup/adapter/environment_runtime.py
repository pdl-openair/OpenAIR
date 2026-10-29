"""Environment creation, dependency installation, verification, readiness, and cleanup."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from typing import Any

from . import environment_contract as contract
from .environment_common import (
    BUILD_TIMEOUT_SECONDS,
    VERIFY_TIMEOUT_SECONDS,
    bounded_diagnostic_text,
    command_failure_diagnostic,
    failure,
    run_bounded_command,
    run_command,
)

_IMPORT_VERIFY_SCRIPT = r"""import importlib
import json
from pathlib import Path
import sys
modules = json.loads(sys.argv[1])
result_path = Path(sys.argv[2])
results = []
failed = False
for name in modules:
    try:
        importlib.import_module(name)
    except BaseException as exc:
        failed = True
        results.append({"module": name, "status": "failed", "error_type": type(exc).__name__, "error_message": str(exc)})
    else:
        results.append({"module": name, "status": "passed"})
result_path.write_text(json.dumps(results, sort_keys=True), encoding="utf-8")
sys.exit(1 if failed else 0)"""
_PYTHON_MODULE_FAILURE = re.compile(
    r"(?m)^(ModuleNotFoundError|ImportError):\s*(.+?)\s*$"
)


def _builder_version(executable: str, workspace: Path) -> str | None:
    try:
        result = run_command([executable, "--version"], cwd=workspace, timeout_seconds=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    return result.stdout.strip()


def _rmtree_onerror(function: Any, path: str, exc_info: Any) -> None:
    try:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
        function(path)
    except OSError:
        raise exc_info[1]


def cleanup_environment(target: Path) -> bool:
    if not target.exists() and not target.is_symlink():
        return True
    try:
        if target.is_symlink() or target.is_file():
            target.unlink()
        else:
            shutil.rmtree(target, onerror=_rmtree_onerror)
    except OSError:
        return False
    return not target.exists() and not target.is_symlink()


def create_environment(
    request: dict[str, Any], *, workspace: Path, target: Path
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    candidates: list[tuple[list[str], dict[str, str]]] = []
    uv = shutil.which("uv")
    if uv:
        version = _builder_version(uv, workspace)
        if version is not None:
            candidates.append(
                (
                    [
                        uv,
                        "venv",
                        "--no-project",
                        "--seed",
                        "--python",
                        request["python_version"],
                        str(target),
                    ],
                    {"kind": "uv", "version": version},
                )
            )

    if contract.current_python_matches(request["python_version"]):
        candidates.append(
            (
                [sys.executable, "-m", "venv", str(target)],
                {
                    "kind": "venv",
                    "version": f"Python {'.'.join(str(v) for v in sys.version_info[:3])}",
                },
            )
        )

    if not candidates:
        return None, failure(
            "requested_python_unavailable",
            "builder_preflight",
            requested_python_version=request["python_version"],
        )

    last_failure: dict[str, Any] | None = None
    for argv, builder in candidates:
        if target.exists() or target.is_symlink():
            if not cleanup_environment(target):
                return None, failure(
                    "environment_cleanup_failed",
                    "cleanup",
                    cleanup_triggered_by=(last_failure or {}).get(
                        "failure_code", "environment_creation_failed"
                    ),
                )
        try:
            result = run_command(argv, cwd=workspace, timeout_seconds=BUILD_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired as exc:
            last_failure = failure(
                "environment_creation_timeout",
                "environment_creation",
                **command_failure_diagnostic(
                    argv, error=exc, timeout_seconds=BUILD_TIMEOUT_SECONDS
                ),
            )
            continue
        except OSError as exc:
            last_failure = failure(
                "environment_creation_failed",
                "environment_creation",
                **command_failure_diagnostic(argv, error=exc),
            )
            continue
        if result.returncode == 0:
            return {"builder": builder, "exit_code": 0}, None
        last_failure = failure(
            "environment_creation_failed",
            "environment_creation",
            **command_failure_diagnostic(argv, result=result),
        )

    return None, last_failure or failure(
        "environment_creation_failed", "environment_creation"
    )


def install_requirements(
    request: dict[str, Any], *, python: Path, workspace: Path
) -> dict[str, Any] | None:
    timeout_seconds = request["dependency_install_timeout_seconds"]
    argv = [
        str(python),
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--no-input",
    ]
    for link in request["find_links"]:
        argv.extend(["--find-links", link])
    argv.extend(request["requirements"])
    try:
        result = run_command(argv, cwd=workspace, timeout_seconds=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        return failure(
            "dependency_install_timeout",
            "dependency_installation",
            **command_failure_diagnostic(
                argv, error=exc, timeout_seconds=timeout_seconds
            ),
        )
    except OSError as exc:
        return failure(
            "dependency_install_failed",
            "dependency_installation",
            **command_failure_diagnostic(
                argv, error=exc, timeout_seconds=timeout_seconds
            ),
        )
    if result.returncode != 0:
        return failure(
            "dependency_install_failed",
            "dependency_installation",
            **command_failure_diagnostic(
                argv, result=result, timeout_seconds=timeout_seconds
            ),
        )
    return None


def pip_check(python: Path, *, workspace: Path) -> dict[str, Any] | None:
    argv = [str(python), "-m", "pip", "check"]
    try:
        result = run_command(
            argv,
            cwd=workspace,
            timeout_seconds=VERIFY_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        return failure(
            "dependency_verification_failed",
            "pip_check",
            **command_failure_diagnostic(
                argv, error=exc, timeout_seconds=VERIFY_TIMEOUT_SECONDS
            ),
        )
    except OSError as exc:
        return failure(
            "dependency_verification_failed",
            "pip_check",
            **command_failure_diagnostic(argv, error=exc),
        )
    if result.returncode != 0:
        return failure(
            "dependency_verification_failed",
            "pip_check",
            **command_failure_diagnostic(argv, result=result),
        )
    return None


def verify_imports(
    modules: list[str], *, python: Path, workspace: Path
) -> tuple[list[dict[str, str]] | None, dict[str, Any] | None]:
    with tempfile.TemporaryDirectory(
        prefix=".openair-import-verification-", dir=workspace
    ) as result_directory:
        result_path = Path(result_directory) / "result.json"
        argv = [
            str(python),
            "-c",
            _IMPORT_VERIFY_SCRIPT,
            json.dumps(modules),
            str(result_path),
        ]
        try:
            result = run_command(
                argv,
                cwd=workspace,
                timeout_seconds=VERIFY_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            return None, failure(
                "import_verification_failed",
                "import_verification",
                **command_failure_diagnostic(
                    argv, error=exc, timeout_seconds=VERIFY_TIMEOUT_SECONDS
                ),
            )
        except OSError as exc:
            return None, failure(
                "import_verification_failed",
                "import_verification",
                **command_failure_diagnostic(argv, error=exc),
            )

        if not result_path.is_file():
            return None, _invalid_import_protocol(
                argv, result, reason="result_file_missing"
            )
        try:
            details = json.loads(result_path.read_text(encoding="utf-8"))
        except OSError as exc:
            return None, _invalid_import_protocol(
                argv, result, reason="result_file_unreadable", error=exc
            )
        except (UnicodeError, json.JSONDecodeError) as exc:
            return None, _invalid_import_protocol(
                argv, result, reason="result_json_invalid", error=exc
            )

        structure_error = _import_result_structure_error(details, modules)
        if structure_error is not None:
            return None, _invalid_import_protocol(
                argv, result, reason=structure_error
            )

        for item in details:
            if item["status"] == "failed":
                item["error_message"] = bounded_diagnostic_text(item["error_message"])

        failed = any(item["status"] == "failed" for item in details)
        expected_returncode = 1 if failed else 0
        if result.returncode != expected_returncode:
            return None, _invalid_import_protocol(
                argv, result, reason="exit_status_inconsistent"
            )
        if failed:
            return None, failure(
                "import_verification_failed",
                "import_verification",
                imports=details,
                **command_failure_diagnostic(argv, result=result),
            )
        return details, None


def _import_result_structure_error(details: Any, modules: list[str]) -> str | None:
    if not isinstance(details, list):
        return "result_not_list"
    if len(details) != len(modules):
        return "result_length_mismatch"
    for index, (item, module) in enumerate(zip(details, modules)):
        if not isinstance(item, dict):
            return f"result_item_not_object:{index}"
        if item.get("module") != module:
            return f"result_module_mismatch:{index}"
        status = item.get("status")
        if status == "passed":
            if set(item) != {"module", "status"}:
                return f"result_passed_item_invalid:{index}"
        elif status == "failed":
            if set(item) != {"module", "status", "error_type", "error_message"}:
                return f"result_failed_item_invalid:{index}"
            if not isinstance(item.get("error_type"), str) or not item["error_type"]:
                return f"result_error_type_invalid:{index}"
            if not isinstance(item.get("error_message"), str):
                return f"result_error_message_invalid:{index}"
        else:
            return f"result_status_invalid:{index}"
    return None


def _invalid_import_protocol(
    argv: list[str],
    result: subprocess.CompletedProcess[str],
    *,
    reason: str,
    error: BaseException | None = None,
) -> dict[str, Any]:
    diagnostic = command_failure_diagnostic(argv, result=result)["diagnostic"]
    diagnostic.setdefault("stdout_tail", "")
    diagnostic.setdefault("stderr_tail", "")
    protocol_error: dict[str, str] = {"reason": reason}
    if error is not None:
        error_diagnostic = command_failure_diagnostic([], error=error)["diagnostic"]
        protocol_error["type"] = error_diagnostic["error_type"]
        if "error_message" in error_diagnostic:
            protocol_error["message"] = error_diagnostic["error_message"]
    diagnostic["protocol_error"] = protocol_error
    return failure(
        "import_verification_invalid",
        "import_verification",
        diagnostic=diagnostic,
    )


def _python_module_failure(stderr_text: str) -> dict[str, str] | None:
    matches = list(_PYTHON_MODULE_FAILURE.finditer(stderr_text))
    if not matches:
        return None
    match = matches[-1]
    return {"type": match.group(1), "message": match.group(2)}


def verify_runtime_readiness(
    probe: dict[str, Any], *, target: Path, workspace: Path
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Execute one upstream-defined, low-cost readiness probe in the completed environment."""
    working_directory = contract.resolve_probe_working_directory(
        workspace, probe["working_directory_parts"]
    )
    base: dict[str, Any] = {
        "configured": True,
        "requested_executable": probe["executable"],
        "argv": list(probe["argv"]),
        "working_directory": probe["working_directory"],
        "timeout_seconds": probe["timeout_seconds"],
    }
    if working_directory is None:
        return None, failure(
            "runtime_readiness_working_directory_invalid",
            "runtime_readiness_probe",
            runtime_readiness={**base, "status": "working_directory_invalid"},
        )

    executable = contract.resolve_probe_executable(target, probe["executable_parts"])
    if executable is None:
        return None, failure(
            "runtime_readiness_executable_unavailable",
            "runtime_readiness_probe",
            runtime_readiness={**base, "status": "executable_unavailable"},
        )

    actual_executable = contract.workspace_relative(executable, workspace)
    actual_working_directory = contract.workspace_relative(working_directory, workspace)
    argv = [str(executable), *probe["argv"]]
    process = run_bounded_command(
        argv,
        cwd=working_directory,
        timeout_seconds=probe["timeout_seconds"],
    )
    evidence: dict[str, Any] = {
        **base,
        "actual_executable": actual_executable,
        "working_directory": actual_working_directory,
        "exit_code": process.get("exit_code"),
        "stdout": process["stdout"],
        "stderr": process["stderr"],
    }

    if not process["started"]:
        evidence.update(
            {
                "status": "process_start_failed",
                "process_start_error": {
                    "type": process.get("start_error_type"),
                    "message": process.get("start_error_message"),
                },
            }
        )
        return None, failure(
            "runtime_readiness_process_start_failed",
            "runtime_readiness_probe",
            runtime_readiness=evidence,
        )

    if process["timed_out"]:
        evidence["status"] = "timeout"
        return None, failure(
            "runtime_readiness_timeout",
            "runtime_readiness_probe",
            runtime_readiness=evidence,
        )

    if process["exit_code"] != 0:
        python_error = _python_module_failure(str(process["stderr"].get("text", "")))
        if python_error is not None:
            evidence["status"] = "python_module_failure"
            evidence["python_error"] = python_error
            code = "runtime_readiness_python_module_failure"
        else:
            evidence["status"] = "non_zero_exit"
            code = "runtime_readiness_non_zero_exit"
        return None, failure(
            code,
            "runtime_readiness_probe",
            runtime_readiness=evidence,
        )

    evidence["status"] = "passed"
    return evidence, None
