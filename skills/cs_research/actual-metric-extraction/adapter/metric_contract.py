"""Strict input contract normalization for actual-metric extraction."""

from __future__ import annotations

from pathlib import PurePosixPath
import re
from typing import Any

_FORMATS = {"json", "csv", "tsv", "pipe_table"}
_VALUE_TYPES = {"number", "integer", "string", "boolean"}
_STREAMS = {"stdout", "stderr"}
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def relative_path_parts(value: Any) -> tuple[str, ...] | None:
    if not isinstance(value, str) or value != value.strip() or not value or len(value) > 1024:
        return None
    if "\x00" in value or any(ord(char) < 32 for char in value):
        return None
    path = PurePosixPath(value.replace("\\", "/"))
    if path.is_absolute() or not path.parts:
        return None
    if any(part in {"", ".", ".."} or ":" in part for part in path.parts):
        return None
    return tuple(path.parts)


def _valid_text(value: Any, *, limit: int) -> bool:
    return isinstance(value, str) and value == value.strip() and 0 < len(value) <= limit and "\x00" not in value


def _validated_table(raw: Any) -> dict[str, Any] | None:
    if raw is None:
        return {}
    if not isinstance(raw, dict) or not raw or set(raw) - {"header", "row_index"}:
        return None
    normalized: dict[str, Any] = {}
    if "header" in raw:
        header = raw.get("header")
        if not isinstance(header, list) or not 1 <= len(header) <= 256:
            return None
        if any(not _valid_text(item, limit=256) for item in header) or len(set(header)) != len(header):
            return None
        normalized["header"] = list(header)
    if "row_index" in raw:
        row_index = raw.get("row_index")
        if not isinstance(row_index, int) or isinstance(row_index, bool) or not 0 <= row_index <= 1_000_000:
            return None
        normalized["row_index"] = row_index
    return normalized


def _validated_source(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    kind = raw.get("kind")
    fmt = raw.get("format")
    if fmt not in _FORMATS:
        return None
    normalized: dict[str, Any] = {"kind": kind, "format": fmt}

    if kind == "execution_stream":
        if set(raw) - {"kind", "stream", "format", "table"} or raw.get("stream") not in _STREAMS:
            return None
        normalized["stream"] = raw["stream"]
    elif kind == "workspace_file":
        if set(raw) - {"kind", "relative_path", "expected_sha256", "format", "table"}:
            return None
        parts = relative_path_parts(raw.get("relative_path"))
        digest = raw.get("expected_sha256")
        if parts is None or (
            digest is not None
            and (not isinstance(digest, str) or _SHA256.fullmatch(digest) is None)
        ):
            return None
        normalized.update({
            "relative_path_parts": parts,
            "relative_path": "/".join(parts),
        })
        if digest is not None:
            normalized["expected_sha256"] = digest
    else:
        return None

    if fmt in {"csv", "tsv", "pipe_table"}:
        table = _validated_table(raw.get("table"))
        if table is None:
            return None
        normalized["table"] = table
    elif "table" in raw:
        return None
    return normalized


def _validated_metrics(raw_metrics: Any, fmt: str) -> list[dict[str, Any]] | None:
    if not isinstance(raw_metrics, list) or not 1 <= len(raw_metrics) <= 128:
        return None
    metrics: list[dict[str, Any]] = []
    names: set[str] = set()
    for raw in raw_metrics:
        if not isinstance(raw, dict) or set(raw) - {"metric_name", "value_type", "unit", "selector"}:
            return None
        if not {"metric_name", "value_type"}.issubset(raw):
            return None
        name = raw.get("metric_name")
        value_type = raw.get("value_type")
        unit = raw.get("unit")
        selector = raw.get("selector")
        if not _valid_text(name, limit=256) or name in names or value_type not in _VALUE_TYPES:
            return None
        if unit is not None and not _valid_text(unit, limit=128):
            return None

        if fmt == "json":
            if not isinstance(selector, dict):
                return None
            pointer = selector.get("json_pointer")
            if set(selector) != {"json_pointer"} or not isinstance(pointer, str) or len(pointer) > 2048:
                return None
            normalized_selector = {"json_pointer": pointer}
        else:
            if selector is None:
                normalized_selector = {"column": name}
            elif isinstance(selector, dict) and set(selector) == {"column"} and _valid_text(selector.get("column"), limit=256):
                normalized_selector = {"column": selector["column"]}
            else:
                return None

        names.add(name)
        metrics.append({
            "metric_name": name,
            "value_type": value_type,
            "unit": unit,
            "selector": normalized_selector,
        })
    return metrics


def validated_input(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict) or set(payload) != {
        "execution_record_relative_path",
        "result_source",
        "metrics",
    }:
        return None
    execution_parts = relative_path_parts(payload.get("execution_record_relative_path"))
    if execution_parts is None or len(execution_parts) < 3 or execution_parts[0] != "execution" or execution_parts[-1] != "execution-record.json":
        return None
    source = _validated_source(payload.get("result_source"))
    if source is None:
        return None
    metrics = _validated_metrics(payload.get("metrics"), source["format"])
    if metrics is None:
        return None
    return {
        "execution_record_relative_path": "/".join(execution_parts),
        "execution_record_parts": execution_parts,
        "result_source": source,
        "metrics": metrics,
    }
