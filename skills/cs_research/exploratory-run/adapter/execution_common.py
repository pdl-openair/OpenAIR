"""Input authority, workspace boundaries, environment identity, and bounded capture."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path, PurePosixPath
import re
from typing import Any

RUNTIME_WORKSPACE_ENV = "OPENAIR_ADAPTER_EXECUTION_CWD"
ENVIRONMENT_DIRNAME = "environment"
ENVIRONMENT_RECORD_RELATIVE_PATH = "environment/environment-record.json"
EXECUTION_ROOT_DIRNAME = "execution"
EXECUTION_RECORD_FILENAME = "execution-record.json"
EXECUTION_SCHEMA_VERSION = "openair-execution-record/v1"
ENVIRONMENT_SCHEMA_VERSION = "openair-environment-record/v1"
CAPTURE_LIMIT_BYTES = 64 * 1024
MAX_ARGV_ITEMS = 256
MAX_ARGUMENT_LENGTH = 4096
MAX_ENVIRONMENT_VARIABLES = 64
MAX_ENVIRONMENT_VALUE_LENGTH = 4096
_FINGERPRINT = re.compile(r"[0-9a-f]{64}\Z")
_ENVIRONMENT_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_SECRET_NAME_PARTS = (
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "PASSWD",
    "API_KEY",
    "ACCESS_KEY",
    "PRIVATE_KEY",
    "CREDENTIAL",
)


def failure(code: str, stage: str, **details: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
        "state": "failed",
        "failure_code": code,
        "failed_stage": stage,
    }
    result.update(details)
    return result


def runtime_workspace() -> Path | None:
    raw = os.environ.get(RUNTIME_WORKSPACE_ENV, "")
    if not raw:
        return None
    try:
        expected = Path(raw).resolve(strict=True)
        actual = Path.cwd().resolve(strict=True)
    except OSError:
        return None
    if expected != actual or not expected.is_dir():
        return None
    return expected


def relative_path_parts(value: Any) -> tuple[str, ...] | None:
    if not isinstance(value, str) or value != value.strip() or not value:
        return None
    if "\x00" in value or any(ord(char) < 32 for char in value):
        return None
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or not path.parts:
        return None
    if any(part in {"", ".", ".."} or ":" in part for part in path.parts):
        return None
    return tuple(path.parts)


def _validate_resource_bindings(raw: Any, argv: list[str]) -> list[dict[str, Any]] | None:
    # Keep execution_common loadable as a standalone compatibility surface.
    if __package__:
        from .execution_resources import validate_resource_bindings
    else:
        import importlib.util
        module_path = Path(__file__).with_name("execution_resources.py")
        spec = importlib.util.spec_from_file_location("_openair_execution_resources", module_path)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        validate_resource_bindings = module.validate_resource_bindings
    return validate_resource_bindings(raw, argv)


def validated_input(payload: dict[str, Any]) -> dict[str, Any] | None:
    allowed = {
        "executable",
        "argv",
        "working_directory",
        "timeout_seconds",
        "environment_variables",
        "resource_bindings",
    }
    required = {"executable", "argv", "working_directory", "timeout_seconds"}
    if not isinstance(payload, dict) or set(payload) - allowed or not required.issubset(payload):
        return None

    executable_parts = relative_path_parts(payload.get("executable"))
    working_directory_parts = relative_path_parts(payload.get("working_directory"))
    if executable_parts is None or working_directory_parts is None:
        return None

    argv = payload.get("argv")
    if not isinstance(argv, list) or len(argv) > MAX_ARGV_ITEMS:
        return None
    normalized_argv: list[str] = []
    for value in argv:
        if not isinstance(value, str) or len(value) > MAX_ARGUMENT_LENGTH or "\x00" in value:
            return None
        normalized_argv.append(value)

    timeout_seconds = payload.get("timeout_seconds")
    if (
        not isinstance(timeout_seconds, int)
        or isinstance(timeout_seconds, bool)
        or timeout_seconds < 1
        or timeout_seconds > 86400
    ):
        return None

    resource_bindings = _validate_resource_bindings(payload.get("resource_bindings"), normalized_argv)
    if resource_bindings is None:
        return None

    raw_environment = payload.get("environment_variables", {})
    if not isinstance(raw_environment, dict) or len(raw_environment) > MAX_ENVIRONMENT_VARIABLES:
        return None
    normalized_environment: dict[str, str] = {}
    for key, value in raw_environment.items():
        if not isinstance(key, str) or _ENVIRONMENT_NAME.fullmatch(key) is None:
            return None
        if not isinstance(value, str) or len(value) > MAX_ENVIRONMENT_VALUE_LENGTH or "\x00" in value:
            return None
        normalized_environment[key] = value

    return {
        "executable_parts": executable_parts,
        "argv": normalized_argv,
        "working_directory_parts": working_directory_parts,
        "timeout_seconds": timeout_seconds,
        "environment_variables": normalized_environment,
        "resource_bindings": resource_bindings,
    }


def load_verified_environment(workspace: Path) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    environment = workspace / ENVIRONMENT_DIRNAME
    record_path = workspace / ENVIRONMENT_RECORD_RELATIVE_PATH
    try:
        if environment.is_symlink() or not environment.is_dir() or not record_path.is_file():
            return None, failure("verified_environment_unavailable", "environment")
        record = json.loads(record_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None, failure("verified_environment_unavailable", "environment")

    identity = record.get("environment_identity") if isinstance(record, dict) else None
    fingerprint = identity.get("fingerprint_sha256") if isinstance(identity, dict) else None
    if (
        record.get("schema_version") != ENVIRONMENT_SCHEMA_VERSION
        or record.get("state") != "succeeded"
        or record.get("workspace_relative_path") != ENVIRONMENT_DIRNAME
        or record.get("environment_record_relative_path") != ENVIRONMENT_RECORD_RELATIVE_PATH
        or not isinstance(fingerprint, str)
        or _FINGERPRINT.fullmatch(fingerprint) is None
    ):
        return None, failure("verified_environment_record_invalid", "environment")
    return record, None


def resolve_working_directory(workspace: Path, parts: tuple[str, ...]) -> Path | None:
    candidate = workspace.joinpath(*parts)
    try:
        resolved_workspace = workspace.resolve(strict=True)
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(resolved_workspace)
    except (OSError, ValueError):
        return None
    if not resolved.is_dir():
        return None
    return resolved


def resolve_environment_executable(workspace: Path, parts: tuple[str, ...]) -> Path | None:
    environment = workspace / ENVIRONMENT_DIRNAME
    candidate = environment.joinpath(*parts)
    try:
        environment_absolute = environment.absolute()
        candidate_absolute = candidate.absolute()
        candidate_absolute.relative_to(environment_absolute)
    except (OSError, ValueError):
        return None

    current = candidate_absolute.parent
    while current != environment_absolute:
        if current.is_symlink():
            return None
        parent = current.parent
        if parent == current:
            return None
        current = parent

    try:
        if not candidate_absolute.is_file():
            return None
    except OSError:
        return None
    return candidate_absolute


def workspace_relative(path: Path, workspace: Path) -> str:
    return path.absolute().relative_to(workspace.absolute()).as_posix()


def sensitive_values(environment: dict[str, str]) -> tuple[str, ...]:
    values: set[str] = set()
    for key, value in environment.items():
        upper = key.upper()
        if value and len(value) >= 4 and any(part in upper for part in _SECRET_NAME_PARTS):
            values.add(value)
    return tuple(sorted(values, key=len, reverse=True))


def redact_text(text: str, secrets: tuple[str, ...]) -> str:
    sanitized = text
    for value in secrets:
        sanitized = sanitized.replace(value, "<redacted>")
    return sanitized


@dataclass
class BoundedCapture:
    limit_bytes: int = CAPTURE_LIMIT_BYTES
    total_bytes: int = 0
    _data: bytearray = field(default_factory=bytearray)
    stream_complete: bool = True

    def consume(self, chunk: bytes) -> None:
        self.total_bytes += len(chunk)
        remaining = self.limit_bytes - len(self._data)
        if remaining > 0:
            self._data.extend(chunk[:remaining])

    def mark_incomplete(self) -> None:
        self.stream_complete = False

    def as_record(self, secrets: tuple[str, ...]) -> dict[str, Any]:
        captured = bytes(self._data)
        return {
            "text": redact_text(captured.decode("utf-8", errors="replace"), secrets),
            "total_bytes": self.total_bytes,
            "captured_bytes": len(captured),
            "truncated": self.total_bytes > len(captured),
            "stream_complete": self.stream_complete,
        }
