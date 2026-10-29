"""Shared constants, subprocess primitives, and bounded failure diagnostics."""

from __future__ import annotations

from pathlib import Path
import locale
import re
import subprocess
import tempfile
from typing import Any, BinaryIO, Sequence

ENVIRONMENT_DIRNAME = "environment"
RECORD_FILENAME = "environment-record.json"
RUNTIME_WORKSPACE_ENV = "OPENAIR_ADAPTER_EXECUTION_CWD"
BUILD_TIMEOUT_SECONDS = 180
INSTALL_TIMEOUT_SECONDS = 240
MAX_INSTALL_TIMEOUT_SECONDS = 3600
VERIFY_TIMEOUT_SECONDS = 60
RUNTIME_READINESS_CAPTURE_LIMIT_BYTES = 64 * 1024
DIAGNOSTIC_TEXT_LIMIT = 4000
_URL_USERINFO = re.compile(r"(?i)(https?://)[^/\s@]+@")


def failure(code: str, stage: str, **details: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
        "state": "failed",
        "failure_code": code,
        "failed_stage": stage,
    }
    result.update(details)
    return result


def _sanitize_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        text = value.decode("utf-8", errors="replace")
    else:
        text = str(value)
    return _URL_USERINFO.sub(r"\1<redacted>@", text)


def _bounded_diagnostic_text(value: Any) -> str:
    sanitized = _sanitize_text(value)
    if len(sanitized) <= DIAGNOSTIC_TEXT_LIMIT:
        return sanitized
    return sanitized[-DIAGNOSTIC_TEXT_LIMIT:]


def bounded_diagnostic_text(value: Any) -> str:
    """Return text using the shared diagnostic redaction and size boundary."""
    return _bounded_diagnostic_text(value)


def command_failure_diagnostic(
    argv: Sequence[str],
    *,
    result: subprocess.CompletedProcess[str] | None = None,
    error: BaseException | None = None,
    timeout_seconds: int | None = None,
) -> dict[str, Any]:
    """Return bounded, credential-redacted evidence for one failed command."""
    diagnostic: dict[str, Any] = {"command": [str(part) for part in argv]}
    if result is not None:
        diagnostic["exit_code"] = result.returncode
        stdout_tail = _bounded_diagnostic_text(result.stdout)
        stderr_tail = _bounded_diagnostic_text(result.stderr)
        if stdout_tail:
            diagnostic["stdout_tail"] = stdout_tail
        if stderr_tail:
            diagnostic["stderr_tail"] = stderr_tail
    if error is not None:
        diagnostic["error_type"] = type(error).__name__
        error_message = _bounded_diagnostic_text(error)
        if error_message:
            diagnostic["error_message"] = error_message
    if timeout_seconds is not None:
        diagnostic["timeout_seconds"] = timeout_seconds
    return {"diagnostic": diagnostic}


def _decode_subprocess_output(value: bytes | None) -> str:
    if not value:
        return ""
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError:
        host_encoding = locale.getpreferredencoding(False)
        normalized = host_encoding.lower().replace("-", "").replace("_", "")
        if normalized != "utf8":
            try:
                return value.decode(host_encoding)
            except (LookupError, UnicodeDecodeError):
                pass
        return value.decode("utf-8", errors="replace")


def run_command(
    argv: Sequence[str], *, cwd: Path, timeout_seconds: int
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        list(argv),
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=timeout_seconds,
        shell=False,
    )
    return subprocess.CompletedProcess(
        args=result.args,
        returncode=result.returncode,
        stdout=_decode_subprocess_output(result.stdout),
        stderr=_decode_subprocess_output(result.stderr),
    )


def _bounded_file_capture(stream: BinaryIO) -> dict[str, Any]:
    stream.flush()
    stream.seek(0, 2)
    total_bytes = stream.tell()
    start = max(0, total_bytes - RUNTIME_READINESS_CAPTURE_LIMIT_BYTES)
    stream.seek(start)
    captured = stream.read(RUNTIME_READINESS_CAPTURE_LIMIT_BYTES)
    return {
        "text": _sanitize_text(captured),
        "total_bytes": total_bytes,
        "captured_bytes": len(captured),
        "truncated": total_bytes > len(captured),
    }


def run_bounded_command(
    argv: Sequence[str], *, cwd: Path, timeout_seconds: int
) -> dict[str, Any]:
    """Run one argv command with file-backed, bounded stdout/stderr evidence."""
    with tempfile.TemporaryFile(mode="w+b") as stdout_file, tempfile.TemporaryFile(
        mode="w+b"
    ) as stderr_file:
        try:
            result = subprocess.run(
                list(argv),
                cwd=str(cwd),
                stdout=stdout_file,
                stderr=stderr_file,
                check=False,
                timeout=timeout_seconds,
                shell=False,
            )
        except subprocess.TimeoutExpired as error:
            return {
                "started": True,
                "timed_out": True,
                "exit_code": None,
                "stdout": _bounded_file_capture(stdout_file),
                "stderr": _bounded_file_capture(stderr_file),
                "timeout_error_type": type(error).__name__,
            }
        except OSError as error:
            return {
                "started": False,
                "timed_out": False,
                "exit_code": None,
                "stdout": _bounded_file_capture(stdout_file),
                "stderr": _bounded_file_capture(stderr_file),
                "start_error_type": type(error).__name__,
                "start_error_message": _bounded_diagnostic_text(error),
            }
        return {
            "started": True,
            "timed_out": False,
            "exit_code": result.returncode,
            "stdout": _bounded_file_capture(stdout_file),
            "stderr": _bounded_file_capture(stderr_file),
        }
