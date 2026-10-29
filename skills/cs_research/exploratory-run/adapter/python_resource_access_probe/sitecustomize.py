"""Controlled child-process hook for explicit OpenAIR resource file-open evidence."""

import builtins
import io
import json
import os

_CONFIG_ENV = "OPENAIR_RESOURCE_ACCESS_PROBE_CONFIG"
_RECEIPT_SCHEMA = "openair-resource-consumption-evidence/v2"
_ORIGINAL_BUILTINS_OPEN = builtins.open
_ORIGINAL_IO_OPEN = io.open
_ORIGINAL_OS_OPEN = os.open
_SEEN = set()
_RESOURCES = []
_WORKSPACE = ""


def _load_config():
    global _RESOURCES, _WORKSPACE
    path = os.environ.get(_CONFIG_ENV)
    if not path:
        return
    try:
        with _ORIGINAL_BUILTINS_OPEN(path, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
        if raw.get("schema_version") != "openair-python-resource-access-probe/v1":
            return
        workspace = os.path.realpath(str(raw.get("workspace") or ""))
        resources = raw.get("resources")
        if not workspace or not isinstance(resources, list):
            return
        normalized = []
        for row in resources:
            if not isinstance(row, dict):
                return
            usage = row.get("usage")
            match_kind = row.get("match_kind")
            relative_paths = row.get("workspace_relative_paths")
            evidence_path = row.get("evidence_path")
            planned = row.get("planned_resource_workspace_relative_path")
            if usage not in {"model", "dataset"} or match_kind not in {"tree", "files"}:
                return
            if not isinstance(relative_paths, list) or not relative_paths or not all(isinstance(value, str) for value in relative_paths):
                return
            if not isinstance(evidence_path, str) or not isinstance(planned, str):
                return
            absolute_paths = [os.path.realpath(os.path.join(workspace, value.replace("/", os.sep))) for value in relative_paths]
            item = {
                "usage": usage,
                "match_kind": match_kind,
                "absolute_paths": absolute_paths,
                "evidence_path": evidence_path,
                "planned": planned,
            }
            manifest = row.get("delivery_manifest_sha256")
            if isinstance(manifest, str):
                item["delivery_manifest_sha256"] = manifest
            normalized.append(item)
        _WORKSPACE = workspace
        _RESOURCES = normalized
    except Exception:
        _WORKSPACE = ""
        _RESOURCES = []


def _candidate_path(value):
    if not _WORKSPACE or isinstance(value, int):
        return None
    try:
        raw = os.fsdecode(os.fspath(value))
        absolute = os.path.realpath(os.path.abspath(raw))
        if os.path.commonpath([_WORKSPACE, absolute]) != _WORKSPACE:
            return None
        relative = os.path.relpath(absolute, _WORKSPACE).replace(os.sep, "/")
        return absolute, relative
    except (TypeError, ValueError, OSError):
        return None


def _matches(absolute, row):
    targets = row["absolute_paths"]
    if row["match_kind"] == "files":
        return absolute in targets
    root = targets[0]
    try:
        return os.path.commonpath([root, absolute]) == root
    except ValueError:
        return False


def _write_receipt(row, relative):
    usage = str(row["usage"])
    if usage in _SEEN:
        return
    payload = {
        "schema_version": _RECEIPT_SCHEMA,
        "usage": usage,
        "evidence_kind": "controlled_python_file_open_read",
        "planned_resource_workspace_relative_path": row["planned"],
        "accessed_workspace_relative_path": relative,
    }
    manifest = row.get("delivery_manifest_sha256")
    if manifest is not None:
        payload["delivery_manifest_sha256"] = manifest
    evidence_path = str(row["evidence_path"])
    temp = evidence_path + ".tmp-" + str(os.getpid())
    try:
        data = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        with _ORIGINAL_BUILTINS_OPEN(temp, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, evidence_path)
        _SEEN.add(usage)
    except OSError:
        try:
            os.unlink(temp)
        except OSError:
            pass


def _track(value):
    candidate = _candidate_path(value)
    if candidate is None:
        return
    absolute, relative = candidate
    for row in _RESOURCES:
        if row["usage"] not in _SEEN and _matches(absolute, row):
            _write_receipt(row, relative)


def _builtins_open(file, mode="r", *args, **kwargs):
    handle = _ORIGINAL_BUILTINS_OPEN(file, mode, *args, **kwargs)
    if isinstance(mode, str) and "r" in mode:
        _track(file)
    return handle


def _io_open(file, mode="r", *args, **kwargs):
    handle = _ORIGINAL_IO_OPEN(file, mode, *args, **kwargs)
    if isinstance(mode, str) and "r" in mode:
        _track(file)
    return handle


def _os_open(path, flags, *args, **kwargs):
    descriptor = _ORIGINAL_OS_OPEN(path, flags, *args, **kwargs)
    if flags & os.O_ACCMODE == os.O_RDONLY:
        _track(path)
    return descriptor


_load_config()
if _RESOURCES:
    builtins.open = _builtins_open
    io.open = _io_open
    os.open = _os_open
