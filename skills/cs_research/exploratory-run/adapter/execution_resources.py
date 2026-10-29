"""Deterministic validation, delivery, and evidence for execution resource bindings."""

from __future__ import annotations

from pathlib import Path, PurePosixPath
import re
from typing import Any

_COMMIT_SHA = re.compile(r"[0-9a-f]{40}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_ALLOWED_USAGES = frozenset({"model", "dataset"})
_RESOURCE_REQUIRED_KEYS = frozenset(
    {
        "state",
        "resource_kind",
        "repo_id",
        "repo_type",
        "requested_revision",
        "resolved_commit_sha",
        "actual_identity",
        "expected_identity_verification",
        "workspace_relative_path",
        "execution_evidence",
    }
)


def _valid_text(value: Any, *, max_length: int = 4096) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and len(value) <= max_length
        and value == value.strip()
        and "\x00" not in value
        and not any(ord(char) < 32 for char in value)
    )


def _resource_path_parts(value: Any) -> tuple[str, ...] | None:
    if not _valid_text(value, max_length=2048):
        return None
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or not path.parts or path.parts[0] != "resource":
        return None
    if any(part in {"", ".", ".."} or ":" in part for part in path.parts):
        return None
    return tuple(path.parts)


def _valid_actual_identity(raw: Any, resolved_commit: str) -> bool:
    if not isinstance(raw, dict) or raw.get("commit_sha") != resolved_commit:
        return False
    kind = raw.get("kind")
    if kind == "huggingface_commit":
        return set(raw) == {"kind", "commit_sha"}
    if kind != "huggingface_file_at_commit" or set(raw) != {"kind", "commit_sha", "filename", "sha256"}:
        return False
    return _valid_text(raw.get("filename"), max_length=1024) and isinstance(raw.get("sha256"), str) and _SHA256.fullmatch(raw["sha256"]) is not None


def _validated_resource(raw: Any, usage: str) -> dict[str, Any] | None:
    if not isinstance(raw, dict) or set(raw) != _RESOURCE_REQUIRED_KEYS:
        return None
    if raw.get("state") != "succeeded" or raw.get("repo_type") != usage:
        return None
    if raw.get("resource_kind") not in {"huggingface_snapshot", "huggingface_file"}:
        return None
    if not _valid_text(raw.get("repo_id"), max_length=256):
        return None
    requested_revision = raw.get("requested_revision")
    if requested_revision is not None and not _valid_text(requested_revision, max_length=512):
        return None
    resolved_commit = raw.get("resolved_commit_sha")
    if not isinstance(resolved_commit, str) or _COMMIT_SHA.fullmatch(resolved_commit) is None:
        return None
    if raw.get("expected_identity_verification") not in {"passed", "not_requested"}:
        return None
    if _resource_path_parts(raw.get("workspace_relative_path")) is None:
        return None
    if not _valid_actual_identity(raw.get("actual_identity"), resolved_commit):
        return None
    evidence = raw.get("execution_evidence")
    if (
        not isinstance(evidence, dict)
        or set(evidence) != {"provider_client", "operation", "materialized"}
        or not _valid_text(evidence.get("provider_client"), max_length=256)
        or evidence.get("operation") not in {"snapshot_download", "hf_hub_download"}
        or evidence.get("materialized") is not True
    ):
        return None
    return {
        "resource_kind": raw["resource_kind"],
        "repo_id": raw["repo_id"],
        "repo_type": raw["repo_type"],
        "resolved_commit_sha": resolved_commit,
        "actual_identity": dict(raw["actual_identity"]),
        "workspace_relative_path": raw["workspace_relative_path"].replace("\\", "/"),
    }


def placeholder_for(usage: str) -> str:
    return f"{{{{openair-resource:{usage}}}}}"


def validate_resource_bindings(raw: Any, argv: list[str]) -> list[dict[str, Any]] | None:
    if raw is None:
        return []
    if not isinstance(raw, list) or not 1 <= len(raw) <= 2:
        return None

    validated: list[dict[str, Any]] = []
    usages: set[str] = set()
    indices: set[int] = set()
    resource_paths: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            return None
        keys = set(item)
        base_keys = keys - {"consumption_probe"}
        if base_keys not in (
            {"usage", "argv_index", "resource"},
            {"usage", "delivery", "resource"},
        ):
            return None
        probe = item.get("consumption_probe")
        if "consumption_probe" in item and probe != {"kind": "python_file_open_read"}:
            return None
        usage = item.get("usage")
        if usage not in _ALLOWED_USAGES or usage in usages:
            return None
        resource = _validated_resource(item.get("resource"), usage)
        if resource is None or resource["workspace_relative_path"] in resource_paths:
            return None

        if "argv_index" in item:
            index = item.get("argv_index")
            if not isinstance(index, int) or isinstance(index, bool) or index < 0 or index >= len(argv) or index in indices:
                return None
            if argv[index] != placeholder_for(usage):
                return None
            indices.add(index)
            normalized = {"usage": usage, "argv_index": index, "resource": resource}
        else:
            delivery = item.get("delivery")
            if delivery != {"kind": "tar_extract_working_directory"}:
                return None
            identity = resource["actual_identity"]
            if resource["resource_kind"] != "huggingface_file" or identity.get("kind") != "huggingface_file_at_commit":
                return None
            if placeholder_for(usage) in argv:
                return None
            normalized = {
                "usage": usage,
                "delivery": {"kind": "tar_extract_working_directory"},
                "resource": resource,
            }

        if probe is not None:
            normalized["consumption_probe"] = dict(probe)
        usages.add(usage)
        resource_paths.add(resource["workspace_relative_path"])
        validated.append(normalized)
    return validated


def resolve_resource_location(workspace: Path, relative_path: str) -> Path | None:
    parts = _resource_path_parts(relative_path)
    if parts is None:
        return None
    candidate = workspace.joinpath(*parts)
    try:
        workspace_root = workspace.resolve(strict=True)
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(workspace_root)
    except (OSError, ValueError):
        return None

    current = candidate.absolute()
    stop = workspace.absolute()
    while current != stop:
        if current.is_symlink():
            return None
        parent = current.parent
        if parent == current:
            return None
        current = parent
    return resolved


def _resolve_resource_path(workspace: Path, resource: dict[str, Any]) -> Path | None:
    resolved = resolve_resource_location(workspace, resource["workspace_relative_path"])
    if resolved is None:
        return None
    if resource["resource_kind"] == "huggingface_snapshot":
        return resolved if resolved.is_dir() else None
    return resolved if resolved.is_file() else None


def _delivery_functions() -> tuple[Any, Any, Any]:
    if __package__:
        from .resource_delivery import (
            cleanup_resource_deliveries,
            deliver_tar_to_working_directory,
            persist_archive_delivery_manifest,
        )
    else:
        import importlib.util

        module_path = Path(__file__).with_name("resource_delivery.py")
        spec = importlib.util.spec_from_file_location("_openair_resource_delivery", module_path)
        if spec is None or spec.loader is None:
            raise RuntimeError("resource delivery module unavailable")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        cleanup_resource_deliveries = module.cleanup_resource_deliveries
        deliver_tar_to_working_directory = module.deliver_tar_to_working_directory
        persist_archive_delivery_manifest = module.persist_archive_delivery_manifest
    return deliver_tar_to_working_directory, cleanup_resource_deliveries, persist_archive_delivery_manifest


def bind_resources(
    workspace: Path,
    working_directory: Path,
    argv: list[str],
    bindings: list[dict[str, Any]],
) -> tuple[list[str] | None, list[dict[str, Any]] | None, dict[str, Any], dict[str, dict[str, Any]]]:
    actual_argv = list(argv)
    usage_records: list[dict[str, Any]] = []
    delivery_states: dict[str, Any] = {}
    probe_targets: dict[str, dict[str, Any]] = {}
    deliver_tar, cleanup_deliveries, _ = _delivery_functions()

    for binding in bindings:
        resource = binding["resource"]
        path = _resolve_resource_path(workspace, resource)
        if path is None:
            cleanup_deliveries(list(delivery_states.values()))
            return None, None, {}, {}

        if "argv_index" in binding:
            actual_argv[binding["argv_index"]] = str(path)
            delivery = {
                "kind": "argv_path",
                "argument_index": binding["argv_index"],
            }
        else:
            delivered = deliver_tar(
                workspace,
                working_directory,
                path,
                expected_sha256=resource["actual_identity"]["sha256"],
            )
            if delivered is None:
                cleanup_deliveries(list(delivery_states.values()))
                return None, None, {}, {}
            delivery, state = delivered
            delivery_states[binding["usage"]] = state

        if "consumption_probe" in binding:
            if delivery["kind"] == "argv_path":
                probe_targets[binding["usage"]] = {
                    "kind": "tree" if resource["resource_kind"] == "huggingface_snapshot" else "files",
                    "workspace_relative_paths": [resource["workspace_relative_path"]],
                }
            else:
                probe_targets[binding["usage"]] = {
                    "kind": "files",
                    "workspace_relative_paths": [
                        path.relative_to(workspace).as_posix() for path in state.published_files
                    ],
                    "delivery_manifest_sha256": delivery["manifest_sha256"],
                }

        usage_records.append(
            {
                "usage": binding["usage"],
                "planned_resource": dict(resource),
                "delivery": delivery,
                "verification": "unverified",
            }
        )
    return actual_argv, usage_records, delivery_states, probe_targets


def persist_bound_delivery_evidence(
    run_dir: Path,
    workspace: Path,
    usage_records: list[dict[str, Any]],
    delivery_states: dict[str, Any],
) -> bool:
    if not delivery_states:
        return True
    _, _, persist_manifest = _delivery_functions()
    by_usage = {entry["usage"]: entry for entry in usage_records}
    for usage, state in delivery_states.items():
        entry = by_usage.get(usage)
        if entry is None or not persist_manifest(run_dir, workspace, usage, entry["delivery"], state):
            return False
    return True


def cleanup_bound_resources(delivery_states: dict[str, Any]) -> None:
    if not delivery_states:
        return
    _, cleanup_deliveries, _ = _delivery_functions()
    cleanup_deliveries(list(delivery_states.values()))
