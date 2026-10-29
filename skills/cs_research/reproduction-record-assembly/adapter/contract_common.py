"""Shared deterministic contract primitives."""

from __future__ import annotations

import math
from pathlib import PurePosixPath
import re
from typing import Any

SHA40 = re.compile(r"[0-9a-f]{40}\Z")
SHA40_OR_64 = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
EXECUTION_STATUSES = {
    "succeeded",
    "process_start_failed",
    "non_zero_exit",
    "timeout",
    "termination_failed",
}
VALUE_TYPES = {"number", "integer", "string", "boolean"}
SOURCE_KINDS = {"execution_stream", "workspace_file"}
SOURCE_FORMATS = {"json", "csv", "tsv", "pipe_table"}


def valid_relative_path(value: Any) -> bool:
    if not isinstance(value, str) or value != value.strip() or not value or len(value) > 1024:
        return False
    if "\x00" in value or any(ord(char) < 32 for char in value):
        return False
    path = PurePosixPath(value.replace("\\", "/"))
    if path.is_absolute() or not path.parts:
        return False
    return not any(part in {"", ".", ".."} or ":" in part for part in path.parts)


def valid_text(value: Any, *, limit: int = 4096) -> bool:
    return (
        isinstance(value, str)
        and value == value.strip()
        and 0 < len(value) <= limit
        and "\x00" not in value
        and not any(ord(char) < 32 for char in value)
    )


def finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and (
        not isinstance(value, float) or math.isfinite(value)
    )


def value_matches_type(value: Any, value_type: str) -> bool:
    if value_type == "number":
        return finite_number(value)
    if value_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if value_type == "string":
        return isinstance(value, str)
    if value_type == "boolean":
        return isinstance(value, bool)
    return False
