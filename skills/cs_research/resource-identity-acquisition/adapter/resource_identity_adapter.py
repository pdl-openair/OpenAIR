"""Deterministic acquisition and identity verification for Hugging Face resources."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import importlib.metadata
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tempfile
from typing import Any, Callable

_RESOURCE_DIRNAME = "resource"
_STAGING_PREFIX = ".ra-"
_MATERIALIZED_DIRNAME = "r"
_RUNTIME_WORKSPACE_ENV = "OPENAIR_ADAPTER_EXECUTION_CWD"
_COMMIT_SHA = re.compile(r"[0-9a-fA-F]{40}\Z")
_SHA256 = re.compile(r"[0-9a-fA-F]{64}\Z")
_ALLOWED_REPO_TYPES = frozenset({"model", "dataset"})
_ALLOWED_KINDS = frozenset({"huggingface_snapshot", "huggingface_file"})
_MIN_HUGGINGFACE_HUB_VERSION = (1, 26, 0)
_MAX_HUGGINGFACE_HUB_VERSION = (2, 0, 0)
_HUGGINGFACE_HUB_REQUIREMENT = ">=1.26,<2"
_HF_VERSION_PREFIX = re.compile(r"^(\d+)\.(\d+)\.(\d+)")
_MAX_BATCH_RESOURCES = 16
_MAX_USAGE_LENGTH = 64


@dataclass(frozen=True)
class _HuggingFaceClient:
    version: str
    api: Any
    snapshot_download: Callable[..., str]
    hf_hub_download: Callable[..., str]


def _failure(code: str, stage: str, **details: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
        "state": "failed",
        "failure_code": code,
        "failed_stage": stage,
    }
    result.update(details)
    return result


def _contains_control_character(value: str) -> bool:
    return any(ord(char) < 32 or ord(char) == 127 for char in value)


def _valid_text(value: Any, *, max_length: int) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and len(value) <= max_length
        and value == value.strip()
        and not _contains_control_character(value)
    )


def _valid_repo_id(value: Any) -> bool:
    if not _valid_text(value, max_length=256):
        return False
    if value.startswith(("http://", "https://")):
        return False
    parts = value.split("/")
    return len(parts) == 2 and all(part not in {"", ".", ".."} for part in parts)


def _normalized_filename(value: Any) -> str | None:
    if not _valid_text(value, max_length=1024) or "\\" in value:
        return None
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts:
        return None
    if any(part in {"", ".", ".."} for part in path.parts):
        return None
    return path.as_posix()


def _normalized_hex(value: Any, pattern: re.Pattern[str]) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        return None
    return value.lower()


def _validated_input(payload: dict[str, Any]) -> dict[str, Any] | None:
    kind = payload.get("resource_kind")
    if kind not in _ALLOWED_KINDS:
        return None

    common = {"resource_kind", "repo_id", "repo_type", "revision", "expected_commit_sha"}
    allowed = common | ({"filename", "expected_sha256"} if kind == "huggingface_file" else set())
    required = {"resource_kind", "repo_id", "repo_type", "revision"}
    if kind == "huggingface_file":
        required.add("filename")
    if set(payload) - allowed or not required.issubset(payload):
        return None

    repo_id = payload.get("repo_id")
    repo_type = payload.get("repo_type")
    revision = payload.get("revision")
    if not _valid_repo_id(repo_id):
        return None
    if repo_type not in _ALLOWED_REPO_TYPES:
        return None
    if revision is not None and not _valid_text(revision, max_length=512):
        return None

    expected_commit = _normalized_hex(payload.get("expected_commit_sha"), _COMMIT_SHA)
    if payload.get("expected_commit_sha") is not None and expected_commit is None:
        return None

    validated: dict[str, Any] = {
        "resource_kind": kind,
        "repo_id": repo_id,
        "repo_type": repo_type,
        "revision": revision,
        "expected_commit_sha": expected_commit,
    }
    if kind == "huggingface_file":
        filename = _normalized_filename(payload.get("filename"))
        if filename is None:
            return None
        expected_sha256 = _normalized_hex(payload.get("expected_sha256"), _SHA256)
        if payload.get("expected_sha256") is not None and expected_sha256 is None:
            return None
        validated["filename"] = filename
        validated["expected_sha256"] = expected_sha256
    return validated


def _validated_batch_input(payload: dict[str, Any]) -> list[tuple[str, dict[str, Any]]] | None:
    if set(payload) != {"resources"}:
        return None
    raw_resources = payload.get("resources")
    if (
        not isinstance(raw_resources, list)
        or not raw_resources
        or len(raw_resources) > _MAX_BATCH_RESOURCES
    ):
        return None

    validated: list[tuple[str, dict[str, Any]]] = []
    for raw in raw_resources:
        if not isinstance(raw, dict):
            return None
        usage = raw.get("usage")
        if not _valid_text(usage, max_length=_MAX_USAGE_LENGTH):
            return None
        request = _validated_input({key: value for key, value in raw.items() if key != "usage"})
        if request is None:
            return None
        validated.append((usage, request))
    return validated


def _runtime_workspace() -> Path | None:
    raw = os.environ.get(_RUNTIME_WORKSPACE_ENV, "")
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


def _resource_root(workspace: Path) -> tuple[Path | None, str | None]:
    try:
        workspace_root = workspace.resolve(strict=True)
    except OSError:
        return None, "workspace_boundary_invalid"
    root = workspace_root / _RESOURCE_DIRNAME
    try:
        root.absolute().relative_to(workspace_root.absolute())
    except ValueError:
        return None, "workspace_boundary_invalid"
    if root.is_symlink() or (root.exists() and not root.is_dir()):
        return None, "resource_target_conflict"
    return root, None


def _resource_key(request: dict[str, Any], resolved_commit: str) -> str:
    parts = [
        request["resource_kind"],
        request["repo_type"],
        request["repo_id"],
        resolved_commit,
        request.get("filename", ""),
    ]
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def _resource_target(
    root: Path,
    request: dict[str, Any],
    resolved_commit: str,
) -> tuple[Path | None, str | None]:
    target = root / _resource_key(request, resolved_commit)
    if target.exists() or target.is_symlink():
        return None, "resource_target_conflict"
    return target, None


def _installed_huggingface_version() -> str | None:
    try:
        return importlib.metadata.version("huggingface-hub")
    except importlib.metadata.PackageNotFoundError:
        return None


def _load_huggingface_client() -> _HuggingFaceClient | None:
    try:
        import huggingface_hub
        from huggingface_hub import HfApi, hf_hub_download, snapshot_download
    except (ImportError, OSError):
        return None
    return _HuggingFaceClient(
        version=str(getattr(huggingface_hub, "__version__", "unknown")),
        api=HfApi(),
        snapshot_download=snapshot_download,
        hf_hub_download=hf_hub_download,
    )


def _supported_huggingface_version(version: str) -> bool:
    match = _HF_VERSION_PREFIX.match(version)
    if match is None:
        return False
    parsed = tuple(int(part) for part in match.groups())
    return _MIN_HUGGINGFACE_HUB_VERSION <= parsed < _MAX_HUGGINGFACE_HUB_VERSION


def _exception_type_names(error: BaseException) -> set[str]:
    names: set[str] = set()
    current: BaseException | None = error
    seen: set[int] = set()
    for _ in range(6):
        if current is None or id(current) in seen:
            break
        seen.add(id(current))
        names.add(type(current).__name__)
        current = current.__cause__ or current.__context__
    return names


def _provider_http_status_code(error: BaseException) -> int | None:
    current: BaseException | None = error
    seen: set[int] = set()
    for _ in range(6):
        if current is None or id(current) in seen:
            break
        seen.add(id(current))
        response = getattr(current, "response", None)
        status_code = getattr(response, "status_code", None)
        if isinstance(status_code, int):
            return status_code
        current = current.__cause__ or current.__context__
    return None


def _provider_failure_code(error: BaseException, *, default: str) -> str:
    names = _exception_type_names(error)
    if names & {
        "ConnectError",
        "ConnectTimeout",
        "ReadTimeout",
        "WriteTimeout",
        "PoolTimeout",
        "TimeoutException",
        "OfflineModeIsEnabled",
        "ProxyError",
    }:
        return "provider_unavailable"
    status_code = _provider_http_status_code(error)
    if status_code == 401:
        return "provider_authentication_required"
    if status_code == 403 or "GatedRepoError" in names:
        return "provider_access_denied"
    if "RevisionNotFoundError" in names:
        return "resource_revision_unresolvable"
    if "RepositoryNotFoundError" in names:
        return "resource_unavailable"
    if "EntryNotFoundError" in names:
        return "resource_file_unavailable"
    if "IncompleteSnapshotError" in names:
        return "resource_incomplete"
    return default


def _resolve_commit(
    client: _HuggingFaceClient,
    request: dict[str, Any],
) -> tuple[str | None, str | None]:
    try:
        info = client.api.repo_info(
            request["repo_id"],
            repo_type=request["repo_type"],
            revision=request["revision"],
            token=None,
        )
    except Exception as error:
        return None, _provider_failure_code(error, default="resource_revision_resolution_failed")
    sha = getattr(info, "sha", None)
    if not isinstance(sha, str) or _COMMIT_SHA.fullmatch(sha) is None:
        return None, "provider_identity_invalid"
    return sha.lower(), None


def _snapshot_manifest(
    client: _HuggingFaceClient,
    request: dict[str, Any],
    resolved_commit: str,
) -> tuple[tuple[str, ...] | None, str | None]:
    try:
        files = client.api.list_repo_files(
            request["repo_id"],
            repo_type=request["repo_type"],
            revision=resolved_commit,
            token=None,
        )
    except Exception as error:
        return None, _provider_failure_code(error, default="resource_manifest_unavailable")
    if not isinstance(files, list) or not files:
        return None, "provider_manifest_invalid"
    normalized: list[str] = []
    for raw in files:
        path = _normalized_filename(raw)
        if path is None:
            return None, "provider_manifest_invalid"
        normalized.append(path)
    if len(set(normalized)) != len(normalized):
        return None, "provider_manifest_invalid"
    return tuple(sorted(normalized)), None


def _cleanup_staging(staging: Path) -> bool:
    try:
        if staging.exists() or staging.is_symlink():
            shutil.rmtree(staging)
    except OSError:
        return False
    return not staging.exists() and not staging.is_symlink()


def _failure_after_cleanup(staging: Path, code: str, stage: str) -> dict[str, Any]:
    if _cleanup_staging(staging):
        return _failure(code, stage)
    return _failure(
        "resource_cleanup_failed",
        "cleanup",
        cleanup_triggered_by=code,
    )


def _new_staging(workspace: Path) -> Path | None:
    try:
        raw = tempfile.mkdtemp(prefix=_STAGING_PREFIX, dir=str(workspace))
        staging = Path(raw).resolve(strict=True)
        staging.relative_to(workspace.resolve(strict=True))
        return staging
    except (OSError, ValueError):
        return None


def _path_within(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=True).relative_to(root.resolve(strict=True))
        return True
    except (OSError, ValueError):
        return False


def _validate_snapshot_manifest(root: Path, expected_files: tuple[str, ...]) -> bool:
    try:
        resolved_root = root.resolve(strict=True)
    except OSError:
        return False
    if not resolved_root.is_dir() or not expected_files:
        return False
    try:
        for relative in expected_files:
            parts = PurePosixPath(relative).parts
            candidate = resolved_root.joinpath(*parts)
            if not _path_within(candidate, resolved_root) or not candidate.is_file():
                return False
    except OSError:
        return False
    return True


def _sha256_file(path: Path) -> str | None:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def _publish_materialized(materialized: Path, target: Path, staging: Path) -> bool:
    if target.exists() or target.is_symlink():
        return False
    root = target.parent
    created_root = False
    try:
        if root.is_symlink() or (root.exists() and not root.is_dir()):
            return False
        if not root.exists():
            root.mkdir()
            created_root = True
        materialized.rename(target)
    except OSError:
        if created_root:
            try:
                root.rmdir()
            except OSError:
                pass
        return False
    try:
        staging.rmdir()
    except OSError:
        pass
    return True


def _verification_status(request: dict[str, Any]) -> str:
    requested = request.get("expected_commit_sha") is not None
    if request["resource_kind"] == "huggingface_file":
        requested = requested or request.get("expected_sha256") is not None
    return "passed" if requested else "not_requested"


def _snapshot_success(
    request: dict[str, Any],
    client: _HuggingFaceClient,
    resolved_commit: str,
    workspace_relative_path: str,
) -> dict[str, Any]:
    return {
        "state": "succeeded",
        "resource_kind": request["resource_kind"],
        "repo_id": request["repo_id"],
        "repo_type": request["repo_type"],
        "requested_revision": request["revision"],
        "resolved_commit_sha": resolved_commit,
        "actual_identity": {
            "kind": "huggingface_commit",
            "commit_sha": resolved_commit,
        },
        "expected_identity_verification": _verification_status(request),
        "workspace_relative_path": workspace_relative_path,
        "execution_evidence": {
            "provider_client": f"huggingface_hub {client.version}",
            "operation": "snapshot_download",
            "materialized": True,
        },
    }


def _file_success(
    request: dict[str, Any],
    client: _HuggingFaceClient,
    resolved_commit: str,
    sha256: str,
    workspace_relative_path: str,
) -> dict[str, Any]:
    return {
        "state": "succeeded",
        "resource_kind": request["resource_kind"],
        "repo_id": request["repo_id"],
        "repo_type": request["repo_type"],
        "requested_revision": request["revision"],
        "resolved_commit_sha": resolved_commit,
        "actual_identity": {
            "kind": "huggingface_file_at_commit",
            "commit_sha": resolved_commit,
            "filename": request["filename"],
            "sha256": sha256,
        },
        "expected_identity_verification": _verification_status(request),
        "workspace_relative_path": workspace_relative_path,
        "execution_evidence": {
            "provider_client": f"huggingface_hub {client.version}",
            "operation": "hf_hub_download",
            "materialized": True,
        },
    }


def _acquire_huggingface_snapshot(
    request: dict[str, Any],
    *,
    client: _HuggingFaceClient,
    workspace: Path,
    target: Path,
    resolved_commit: str,
    expected_files: tuple[str, ...],
) -> dict[str, Any]:
    staging = _new_staging(workspace)
    if staging is None:
        return _failure("staging_unavailable", "staging")
    materialized = staging / _MATERIALIZED_DIRNAME

    try:
        returned = client.snapshot_download(
            request["repo_id"],
            repo_type=request["repo_type"],
            revision=resolved_commit,
            local_dir=str(materialized),
            token=None,
        )
    except Exception as error:
        code = _provider_failure_code(error, default="resource_acquisition_failed")
        return _failure_after_cleanup(staging, code, "acquire")

    try:
        returned_path = Path(returned).resolve(strict=True)
        expected_path = materialized.resolve(strict=True)
    except (OSError, TypeError):
        return _failure_after_cleanup(staging, "materialized_resource_invalid", "materialization")
    if returned_path != expected_path:
        return _failure_after_cleanup(staging, "materialized_resource_invalid", "materialization")
    if not _validate_snapshot_manifest(materialized, expected_files):
        return _failure_after_cleanup(staging, "resource_incomplete", "materialization")

    if not _publish_materialized(materialized, target, staging):
        return _failure_after_cleanup(staging, "resource_target_conflict", "publish")
    relative = target.relative_to(workspace).as_posix()
    return _snapshot_success(request, client, resolved_commit, relative)


def _acquire_huggingface_file(
    request: dict[str, Any],
    *,
    client: _HuggingFaceClient,
    workspace: Path,
    target: Path,
    resolved_commit: str,
) -> dict[str, Any]:
    staging = _new_staging(workspace)
    if staging is None:
        return _failure("staging_unavailable", "staging")
    materialized = staging / _MATERIALIZED_DIRNAME

    try:
        returned = client.hf_hub_download(
            request["repo_id"],
            request["filename"],
            repo_type=request["repo_type"],
            revision=resolved_commit,
            local_dir=str(materialized),
            token=None,
        )
    except Exception as error:
        code = _provider_failure_code(error, default="resource_acquisition_failed")
        return _failure_after_cleanup(staging, code, "acquire")

    try:
        returned_path = Path(returned).resolve(strict=True)
        relative_parts = PurePosixPath(request["filename"]).parts
        expected_path = (materialized / Path(*relative_parts)).resolve(strict=True)
    except (OSError, TypeError):
        return _failure_after_cleanup(staging, "materialized_resource_invalid", "materialization")
    if returned_path != expected_path or not _path_within(returned_path, materialized):
        return _failure_after_cleanup(staging, "materialized_resource_invalid", "materialization")
    if returned_path.is_symlink() or not returned_path.is_file():
        return _failure_after_cleanup(staging, "materialized_resource_invalid", "materialization")

    sha256 = _sha256_file(returned_path)
    if sha256 is None:
        return _failure_after_cleanup(staging, "materialized_resource_invalid", "materialization")
    expected_sha256 = request.get("expected_sha256")
    if expected_sha256 is not None and sha256 != expected_sha256:
        return _failure_after_cleanup(staging, "resource_identity_mismatch", "verify_identity")

    if not _publish_materialized(materialized, target, staging):
        return _failure_after_cleanup(staging, "resource_target_conflict", "publish")
    relative = PurePosixPath(target.relative_to(workspace).as_posix(), request["filename"]).as_posix()
    return _file_success(request, client, resolved_commit, sha256, relative)


def _run_single_resource_identity_acquisition(payload: dict[str, Any]) -> dict[str, Any]:
    """Acquire one already-selected Hugging Face resource into the runtime workspace."""
    if not isinstance(payload, dict):
        return _failure("skill_input_invalid", "input_validation")
    request = _validated_input(payload)
    if request is None:
        return _failure("skill_input_invalid", "input_validation")

    workspace = _runtime_workspace()
    if workspace is None:
        return _failure("workspace_boundary_invalid", "workspace")
    resource_root, root_error = _resource_root(workspace)
    if resource_root is None:
        return _failure(root_error or "workspace_boundary_invalid", "workspace")

    installed_version = _installed_huggingface_version()
    if installed_version is None:
        return _failure(
            "provider_client_not_installed",
            "provider_preflight",
            provider_client="huggingface_hub",
            required_version=_HUGGINGFACE_HUB_REQUIREMENT,
        )
    if not _supported_huggingface_version(installed_version):
        return _failure(
            "provider_client_unsupported",
            "provider_preflight",
            provider_client="huggingface_hub",
            provider_client_version=installed_version,
            required_version=_HUGGINGFACE_HUB_REQUIREMENT,
        )

    client = _load_huggingface_client()
    if client is None:
        return _failure(
            "provider_client_unavailable",
            "provider_preflight",
            provider_client="huggingface_hub",
            provider_client_version=installed_version,
            required_version=_HUGGINGFACE_HUB_REQUIREMENT,
        )

    resolved_commit, resolution_failure = _resolve_commit(client, request)
    if resolved_commit is None:
        return _failure(resolution_failure or "resource_revision_resolution_failed", "resolve_revision")
    expected_commit = request.get("expected_commit_sha")
    if expected_commit is not None and resolved_commit != expected_commit:
        return _failure("resource_identity_mismatch", "verify_identity")

    target, target_error = _resource_target(resource_root, request, resolved_commit)
    if target is None:
        return _failure(target_error or "resource_target_conflict", "workspace")

    if request["resource_kind"] == "huggingface_snapshot":
        expected_files, manifest_failure = _snapshot_manifest(client, request, resolved_commit)
        if expected_files is None:
            return _failure(manifest_failure or "resource_manifest_unavailable", "resolve_manifest")
        return _acquire_huggingface_snapshot(
            request,
            client=client,
            workspace=workspace,
            target=target,
            resolved_commit=resolved_commit,
            expected_files=expected_files,
        )
    return _acquire_huggingface_file(
        request,
        client=client,
        workspace=workspace,
        target=target,
        resolved_commit=resolved_commit,
    )

def _batch_result(rows: list[dict[str, Any]]) -> dict[str, Any]:
    succeeded = sum(1 for row in rows if row["result"].get("state") == "succeeded")
    failed = len(rows) - succeeded
    if succeeded == 0:
        first_failure = rows[0]["result"]
        return _failure(
            "resource_batch_failed",
            "batch_acquire",
            first_failure_code=first_failure.get("failure_code", "resource_acquisition_failed"),
            failed_resources=failed,
        )
    return {
        "state": "succeeded" if failed == 0 else "partial_failure",
        "resources": rows,
        "summary": {
            "total": len(rows),
            "succeeded": succeeded,
            "failed": failed,
        },
    }


def run_resource_identity_acquisition(payload: dict[str, Any]) -> dict[str, Any]:
    """Acquire one resource or a bounded list of independently identified resources."""
    if not isinstance(payload, dict):
        return _failure("skill_input_invalid", "input_validation")
    if "resources" not in payload:
        return _run_single_resource_identity_acquisition(payload)

    batch = _validated_batch_input(payload)
    if batch is None:
        return _failure("skill_input_invalid", "input_validation")

    rows: list[dict[str, Any]] = []
    for usage, request in batch:
        result = _run_single_resource_identity_acquisition(request)
        rows.append({"usage": usage, "result": result})
    return _batch_result(rows)
