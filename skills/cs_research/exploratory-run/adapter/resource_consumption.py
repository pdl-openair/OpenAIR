"""Optional deterministic evidence that a started process actually consumed a bound resource."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any

from .execution_resources import resolve_resource_location

EVIDENCE_SCHEMA_VERSION = "openair-resource-consumption-evidence/v1"
ACCESS_EVIDENCE_SCHEMA_VERSION = "openair-resource-consumption-evidence/v2"
_MAX_EVIDENCE_BYTES = 16 * 1024
_EVIDENCE_ENVIRONMENT = {
    "model": "OPENAIR_RESOURCE_CONSUMPTION_EVIDENCE_MODEL",
    "dataset": "OPENAIR_RESOURCE_CONSUMPTION_EVIDENCE_DATASET",
}
_PROBE_CONFIG_ENVIRONMENT = "OPENAIR_RESOURCE_ACCESS_PROBE_CONFIG"


def evidence_environment_names() -> frozenset[str]:
    return frozenset({*_EVIDENCE_ENVIRONMENT.values(), _PROBE_CONFIG_ENVIRONMENT})


def prepare_consumption_evidence(
    run_dir: Path,
    usage_records: list[dict[str, Any]],
) -> tuple[dict[str, str], dict[str, Path]]:
    if not usage_records:
        return {}, {}
    evidence_dir = run_dir / "resource-consumption"
    evidence_dir.mkdir()
    environment: dict[str, str] = {}
    paths: dict[str, Path] = {}
    for entry in usage_records:
        usage = entry["usage"]
        path = evidence_dir / f"{usage}.json"
        environment[_EVIDENCE_ENVIRONMENT[usage]] = str(path)
        paths[usage] = path
    return environment, paths


def _workspace_relative_path(value: Any) -> str | None:
    if not isinstance(value, str) or not value or value != value.strip():
        return None
    if "\x00" in value or any(ord(char) < 32 for char in value):
        return None
    path = PurePosixPath(value.replace("\\", "/"))
    if path.is_absolute() or not path.parts:
        return None
    if any(part in {"", ".", ".."} or ":" in part for part in path.parts):
        return None
    return path.as_posix()


def _resource_relative_path(value: Any) -> str | None:
    normalized = _workspace_relative_path(value)
    if normalized is None or PurePosixPath(normalized).parts[0] != "resource":
        return None
    return normalized


def _read_receipt(evidence_path: Path) -> tuple[dict[str, Any], str] | None:
    try:
        if evidence_path.is_symlink() or evidence_path.parent.is_symlink() or not evidence_path.is_file():
            return None
        data = evidence_path.read_bytes()
    except OSError:
        return None
    if not data or len(data) > _MAX_EVIDENCE_BYTES:
        return None
    try:
        raw = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    return raw, hashlib.sha256(data).hexdigest()


def _parse_direct_receipt(
    workspace: Path,
    raw: dict[str, Any],
    digest: str,
    usage: str,
) -> dict[str, Any] | None:
    if set(raw) != {"schema_version", "usage", "consumed_workspace_relative_path"}:
        return None
    if raw.get("schema_version") != EVIDENCE_SCHEMA_VERSION or raw.get("usage") != usage:
        return None
    consumed = _resource_relative_path(raw.get("consumed_workspace_relative_path"))
    if consumed is None or resolve_resource_location(workspace, consumed) is None:
        return None
    return {"kind": "direct_resource_receipt", "consumed": consumed, "sha256": digest}


def _valid_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(char in "0123456789abcdef" for char in value)
    )


def _parse_access_receipt(
    raw: dict[str, Any],
    digest: str,
    usage: str,
) -> dict[str, Any] | None:
    allowed_keys = {
        "schema_version",
        "usage",
        "evidence_kind",
        "planned_resource_workspace_relative_path",
        "accessed_workspace_relative_path",
    }
    if set(raw) not in (allowed_keys, allowed_keys | {"delivery_manifest_sha256"}):
        return None
    if raw.get("schema_version") != ACCESS_EVIDENCE_SCHEMA_VERSION or raw.get("usage") != usage:
        return None
    if raw.get("evidence_kind") != "controlled_python_file_open_read":
        return None
    planned = _resource_relative_path(raw.get("planned_resource_workspace_relative_path"))
    accessed = _workspace_relative_path(raw.get("accessed_workspace_relative_path"))
    if planned is None or accessed is None:
        return None
    manifest = raw.get("delivery_manifest_sha256")
    if manifest is not None and not _valid_sha256(manifest):
        return None
    return {
        "kind": "controlled_python_file_open_read",
        "planned": planned,
        "accessed": accessed,
        "delivery_manifest_sha256": manifest,
        "sha256": digest,
    }


def _load_receipt(
    workspace: Path,
    evidence_path: Path,
    usage: str,
) -> dict[str, Any] | None:
    loaded = _read_receipt(evidence_path)
    if loaded is None:
        return None
    raw, digest = loaded
    if raw.get("schema_version") == EVIDENCE_SCHEMA_VERSION:
        return _parse_direct_receipt(workspace, raw, digest, usage)
    if raw.get("schema_version") == ACCESS_EVIDENCE_SCHEMA_VERSION:
        return _parse_access_receipt(raw, digest, usage)
    return None


def _probe_target_matches(accessed: str, target: dict[str, Any]) -> bool:
    paths = target.get("workspace_relative_paths")
    if not isinstance(paths, list) or not paths:
        return False
    if target.get("kind") == "files":
        return accessed in paths
    if target.get("kind") != "tree" or len(paths) != 1:
        return False
    root_parts = PurePosixPath(paths[0]).parts
    accessed_parts = PurePosixPath(accessed).parts
    return accessed_parts[: len(root_parts)] == root_parts


def _base_usage(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "usage": entry["usage"],
        "planned_resource": dict(entry["planned_resource"]),
        "delivery": dict(entry["delivery"]),
        "verification": "unverified",
    }


def _receipt_reference(workspace: Path, evidence_path: Path, digest: str) -> dict[str, str]:
    return {
        "receipt_relative_path": evidence_path.relative_to(workspace).as_posix(),
        "sha256": digest,
    }


def _apply_direct_receipt(
    workspace: Path,
    current: dict[str, Any],
    entry: dict[str, Any],
    receipt: dict[str, Any],
    evidence_path: Path,
    resources_by_path: dict[str, dict[str, Any]],
) -> None:
    consumed = receipt["consumed"]
    current["consumption_evidence"] = {
        "kind": "workspace_json_receipt",
        **_receipt_reference(workspace, evidence_path, receipt["sha256"]),
    }
    actual_resource = resources_by_path.get(consumed)
    if actual_resource is not None:
        current["actual_usage"] = {
            "workspace_relative_path": consumed,
            "immutable_identity": dict(actual_resource["actual_identity"]),
        }
    current["verification"] = (
        "verified" if consumed == entry["planned_resource"]["workspace_relative_path"] else "mismatch"
    )


def _apply_access_receipt(
    workspace: Path,
    current: dict[str, Any],
    entry: dict[str, Any],
    receipt: dict[str, Any],
    evidence_path: Path,
    target: dict[str, Any] | None,
) -> None:
    current["consumption_evidence"] = {
        "kind": "controlled_python_file_open_read",
        **_receipt_reference(workspace, evidence_path, receipt["sha256"]),
        "accessed_workspace_relative_path": receipt["accessed"],
    }
    planned_matches = receipt["planned"] == entry["planned_resource"]["workspace_relative_path"]
    target_matches = target is not None and _probe_target_matches(receipt["accessed"], target)
    expected_manifest = target.get("delivery_manifest_sha256") if target is not None else None
    manifest_matches = receipt.get("delivery_manifest_sha256") == expected_manifest
    if not (planned_matches and target_matches and manifest_matches):
        current["verification"] = "mismatch"
        return
    current["actual_usage"] = {
        "workspace_relative_path": entry["planned_resource"]["workspace_relative_path"],
        "immutable_identity": dict(entry["planned_resource"]["actual_identity"]),
        "observed_access": {
            "kind": "python_file_open_read",
            "workspace_relative_path": receipt["accessed"],
        },
    }
    current["verification"] = "verified"


def finalize_resource_usage(
    workspace: Path,
    usage_records: list[dict[str, Any]],
    evidence_paths: dict[str, Path],
    probe_targets: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    probe_targets = probe_targets or {}
    resources_by_path = {
        entry["planned_resource"]["workspace_relative_path"]: entry["planned_resource"]
        for entry in usage_records
    }
    finalized: list[dict[str, Any]] = []
    for entry in usage_records:
        current = _base_usage(entry)
        evidence_path = evidence_paths.get(entry["usage"])
        receipt = _load_receipt(workspace, evidence_path, entry["usage"]) if evidence_path is not None else None
        if receipt is not None and evidence_path is not None:
            if receipt["kind"] == "direct_resource_receipt":
                _apply_direct_receipt(workspace, current, entry, receipt, evidence_path, resources_by_path)
            else:
                _apply_access_receipt(
                    workspace,
                    current,
                    entry,
                    receipt,
                    evidence_path,
                    probe_targets.get(entry["usage"]),
                )
        finalized.append(current)
    return finalized
