"""Load and identify execution records and declared result sources."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

RUNTIME_WORKSPACE_ENV = "OPENAIR_ADAPTER_EXECUTION_CWD"
EXECUTION_SCHEMA_VERSION = "openair-execution-record/v1"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def failure(code: str, stage: str, **details: Any) -> dict[str, Any]:
    result: dict[str, Any] = {"state": "failed", "failure_code": code, "failed_stage": stage}
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


def resolve_workspace_file(workspace: Path, parts: tuple[str, ...]) -> Path | None:
    candidate = workspace.joinpath(*parts)
    try:
        resolved_workspace = workspace.resolve(strict=True)
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(resolved_workspace)
    except (OSError, ValueError):
        return None
    return resolved if resolved.is_file() else None


def load_execution_record(workspace: Path, request: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    path = resolve_workspace_file(workspace, request["execution_record_parts"])
    if path is None or path.is_symlink():
        return None, failure("execution_record_unavailable", "execution_record")
    try:
        data = path.read_bytes()
        record = json.loads(data.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None, failure("execution_record_invalid", "execution_record")
    if not isinstance(record, dict) or record.get("schema_version") != EXECUTION_SCHEMA_VERSION:
        return None, failure("execution_record_invalid", "execution_record")
    if (
        record.get("state") != "succeeded"
        or record.get("execution_status") != "succeeded"
        or record.get("exit_code") != 0
        or record.get("timed_out") is not False
    ):
        return None, failure("execution_not_succeeded", "execution_record")
    return {"record": record, "sha256": sha256_bytes(data), "path": path}, {}


def _execution_stream_source(execution: dict[str, Any], source: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    stream_name = source["stream"]
    capture = execution["record"].get(stream_name)
    if not isinstance(capture, dict) or not isinstance(capture.get("text"), str):
        return None, failure("result_source_unavailable", "source")
    text = capture["text"]
    data = text.encode("utf-8")
    return {
        "text": text,
        "trace": {
            "kind": "execution_stream",
            "stream": stream_name,
            "format": source["format"],
            "sha256": sha256_bytes(data),
        },
    }, {}


def _workspace_file_source(workspace: Path, source: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    path = resolve_workspace_file(workspace, source["relative_path_parts"])
    if path is None:
        return None, failure("result_source_unavailable", "source")
    try:
        data = path.read_bytes()
    except OSError:
        return None, failure("result_source_unavailable", "source")
    digest = sha256_bytes(data)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None, failure("result_source_not_utf8", "source")
    return {
        "text": text,
        "trace": {
            "kind": "workspace_file",
            "relative_path": source["relative_path"],
            "format": source["format"],
            "sha256": digest,
        },
    }, {}


def load_result_source(
    workspace: Path,
    execution: dict[str, Any],
    source: dict[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    if source["kind"] == "execution_stream":
        return _execution_stream_source(execution, source)
    return _workspace_file_source(workspace, source)
