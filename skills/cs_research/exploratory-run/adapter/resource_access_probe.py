"""Prepare an explicit Python file-open probe for bound execution resources."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any

PROBE_CONFIG_ENVIRONMENT = "OPENAIR_RESOURCE_ACCESS_PROBE_CONFIG"
PROBE_SCHEMA_VERSION = "openair-python-resource-access-probe/v1"


def _write_config(run_dir: Path, payload: dict[str, Any]) -> Path | None:
    probe_dir = run_dir / "python-resource-access-probe"
    try:
        probe_dir.mkdir()
        target = probe_dir / "config.json"
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        fd, raw_temp = tempfile.mkstemp(prefix=".probe-config-", suffix=".tmp", dir=str(probe_dir))
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
    except OSError:
        return None


def prepare_python_access_probe(
    run_dir: Path,
    workspace: Path,
    usage_records: list[dict[str, Any]],
    probe_targets: dict[str, dict[str, Any]],
    evidence_paths: dict[str, Path],
    existing_pythonpath: str | None,
) -> dict[str, str] | None:
    if not probe_targets:
        return {}

    by_usage = {entry["usage"]: entry for entry in usage_records}
    resources: list[dict[str, Any]] = []
    for usage in sorted(probe_targets):
        entry = by_usage.get(usage)
        evidence_path = evidence_paths.get(usage)
        target = probe_targets[usage]
        if entry is None or evidence_path is None:
            return None
        row = {
            "usage": usage,
            "planned_resource_workspace_relative_path": entry["planned_resource"]["workspace_relative_path"],
            "match_kind": target["kind"],
            "workspace_relative_paths": list(target["workspace_relative_paths"]),
            "evidence_path": str(evidence_path),
        }
        manifest = target.get("delivery_manifest_sha256")
        if manifest is not None:
            row["delivery_manifest_sha256"] = manifest
        resources.append(row)

    config = {
        "schema_version": PROBE_SCHEMA_VERSION,
        "workspace": str(workspace.resolve(strict=True)),
        "resources": resources,
    }
    config_path = _write_config(run_dir, config)
    if config_path is None:
        return None

    runtime_dir = Path(__file__).with_name("python_resource_access_probe")
    sitecustomize = runtime_dir / "sitecustomize.py"
    if not sitecustomize.is_file():
        return None
    pythonpath = str(runtime_dir)
    if existing_pythonpath:
        pythonpath = pythonpath + os.pathsep + existing_pythonpath
    return {
        PROBE_CONFIG_ENVIRONMENT: str(config_path),
        "PYTHONPATH": pythonpath,
    }


def probe_config_sha256(config_path: Path) -> str | None:
    try:
        return hashlib.sha256(config_path.read_bytes()).hexdigest()
    except OSError:
        return None
