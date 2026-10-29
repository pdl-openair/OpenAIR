from __future__ import annotations

import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.modules.artifact_resolver import get_resolver
from app.modules.repo_paths import repo_root

def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _durable_storage_ref(artifact: dict[str, Any]) -> tuple[str, object]:
    storage_type = str(artifact.get("storageType") or "local")
    storage_ref = {
        "inline": artifact.get("inlineContent"),
        "external": artifact.get("externalKey"),
        "local": artifact.get("path"),
        "object": artifact.get("contentSha256"),
    }.get(storage_type)
    if storage_type not in {"inline", "external", "local", "object"} or storage_ref is None:
        raise ValueError("artifact_storage_reference_invalid")
    return storage_type, storage_ref


_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


def _immutable_content_contract(artifact: dict[str, Any]) -> tuple[str, int]:
    """Return the committed digest/size or fail closed for PostgreSQL reads."""
    expected_hash = artifact.get("contentSha256")
    if not isinstance(expected_hash, str) or _SHA256_RE.fullmatch(expected_hash.strip()) is None:
        raise ValueError("artifact_content_digest_missing_or_invalid")
    expected_size = artifact.get("contentSize")
    if isinstance(expected_size, bool) or not isinstance(expected_size, int) or expected_size < 0:
        raise ValueError("artifact_content_size_missing_or_invalid")
    return expected_hash.strip().lower(), expected_size


def has_valid_immutable_content_identity(artifact: dict[str, Any]) -> bool:
    """Return whether an Artifact carries a complete immutable hash/size identity."""
    try:
        _immutable_content_contract(artifact)
    except ValueError:
        return False
    return True


def validate_durable_bytes(artifact: dict[str, Any], content: bytes) -> None:
    """Verify bytes against the immutable catalog contract; never infer it at read time."""
    _durable_storage_ref(artifact)
    expected_hash, expected_size = _immutable_content_contract(artifact)
    if len(content) != expected_size:
        raise ValueError("artifact_content_size_mismatch")
    if hashlib.sha256(content).hexdigest().lower() != expected_hash:
        raise ValueError("artifact_content_hash_mismatch")


def _validate_durable_content(artifact: dict[str, Any], content: str) -> None:
    """Verify durable storage metadata before exposing authoritative text."""
    validate_durable_bytes(artifact, content.encode("utf-8"))


def _validate_durable_binary_path(artifact: dict[str, Any], path: Path) -> None:
    """Verify the resolved file still matches the committed catalog bytes."""
    storage_type, _storage_ref = _durable_storage_ref(artifact)
    if storage_type == "external":
        raise ValueError("external_artifact_requires_presigned_download")
    if not path.is_file():
        raise FileNotFoundError("artifact file is unavailable")
    validate_durable_bytes(artifact, path.read_bytes())


def _content_manifest(artifact: dict[str, Any]) -> dict[str, dict[str, Any]]:
    raw = artifact.get("contentManifest")
    if not isinstance(raw, dict) or not raw:
        raise ValueError("artifact_content_manifest_missing")
    manifest: dict[str, dict[str, Any]] = {}
    for name, contract in raw.items():
        if not isinstance(name, str) or not name.strip() or not isinstance(contract, dict):
            raise ValueError("artifact_content_manifest_invalid")
        relative = Path(name)
        if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
            raise ValueError("artifact_content_manifest_invalid")
        normalized = relative.as_posix()
        _immutable_content_contract(contract)
        manifest[normalized] = contract
    return manifest


def _validate_manifest_file(path: Path, contract: dict[str, Any]) -> None:
    if not path.is_file():
        raise FileNotFoundError("artifact file is unavailable")
    validate_durable_bytes(
        {
            "storageType": "local",
            "path": path.name,
            "contentSha256": contract.get("contentSha256"),
            "contentSize": contract.get("contentSize"),
        },
        path.read_bytes(),
    )


def _validate_durable_tree(root: Path, artifact: dict[str, Any]) -> None:
    storage_type, _storage_ref = _durable_storage_ref(artifact)
    if storage_type != "local" or not root.is_dir():
        raise ValueError("artifact_storage_reference_invalid")
    manifest = _content_manifest(artifact)
    actual_files = {
        path.relative_to(root).as_posix(): path
        for path in root.rglob("*")
        if path.is_file() and not any(part in _SKIP_NAMES for part in path.relative_to(root).parts)
        and path.suffix not in _SKIP_EXTS
    }
    if set(actual_files) != set(manifest):
        raise ValueError("artifact_content_manifest_mismatch")
    for relative, path in actual_files.items():
        _validate_manifest_file(path, manifest[relative])


# ---------------------------------------------------------------------------
# Replay / fixture helpers (dev/demo paths — not part of the production cloud
# artifact contract, kept for backward compatibility with recorded runs)
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _recorded_run_dirs_by_task_id() -> dict[str, Path]:
    runs_root = repo_root() / "fixtures" / "recorded_runs"
    out: dict[str, Path] = {}
    if not runs_root.is_dir():
        return out

    for run_dir in runs_root.iterdir():
        if not run_dir.is_dir():
            continue
        task_path = run_dir / "task.json"
        if not task_path.is_file():
            continue
        try:
            with task_path.open(encoding="utf-8") as f:
                task = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        task_id = task.get("task_id")
        if isinstance(task_id, str) and task_id:
            out[task_id] = run_dir
    return out


def _task_artifact_dir(task_id: str) -> Path | None:
    return _recorded_run_dirs_by_task_id().get(task_id)


def _load_skill_meta(skill_id: str) -> dict[str, Any]:
    registry_path = repo_root() / "skills" / "registry.yaml"
    if not registry_path.is_file():
        return {}
    try:
        import yaml

        with registry_path.open(encoding="utf-8") as f:
            registry = yaml.safe_load(f) or {}
    except Exception:
        return {}

    for entry in registry.get("skills", []):
        if entry.get("skill_id") != skill_id:
            continue
        skill_rel = entry.get("skill_path")
        if not skill_rel:
            return entry
        skill_yaml = repo_root() / skill_rel / "skill.yaml"
        if not skill_yaml.is_file():
            return entry
        try:
            import yaml as _yaml

            with skill_yaml.open(encoding="utf-8") as f:
                meta = _yaml.safe_load(f) or {}
        except Exception:
            meta = {}
        return {**entry, **meta}
    return {}


def _load_replay_response(skill_id: str) -> dict[str, Any]:
    response_path = repo_root() / "fixtures" / "skill_io" / skill_id / "response.json"
    if not response_path.is_file():
        return {}
    with response_path.open(encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, dict) else {}


def _recorded_run_candidates(task_id: str, artifact_path: str, skill_id: str | None) -> list[Path]:
    run_dir = _task_artifact_dir(task_id)
    if not run_dir:
        return []

    candidates: list[Path] = []
    seen: set[Path] = set()

    def add_candidate(path_value: str | None) -> None:
        if not path_value:
            return
        rel = Path(path_value)
        for candidate in (run_dir / rel, run_dir / rel.name):
            if candidate in seen:
                continue
            seen.add(candidate)
            candidates.append(candidate)

    add_candidate(artifact_path)

    meta = _load_skill_meta(skill_id) if skill_id else {}
    output_file = meta.get("output_file")
    if isinstance(output_file, str):
        add_candidate(output_file)

    replay = _load_replay_response(skill_id) if skill_id else {}
    for row in replay.get("artifacts") or []:
        if not isinstance(row, dict):
            continue
        path_value = row.get("path")
        if isinstance(path_value, str):
            add_candidate(path_value)

    return candidates


def _resolve_recorded_run_path(task_id: str, artifact_path: str, skill_id: str | None) -> Path | None:
    for candidate in _recorded_run_candidates(task_id, artifact_path, skill_id):
        if candidate.is_file():
            return candidate
    return None


def _resolve_replay_path(task_id: str, artifact_path: str, skill_id: str | None) -> Path | None:
    recorded_run_path = _resolve_recorded_run_path(task_id, artifact_path, skill_id)
    if recorded_run_path:
        return recorded_run_path

    if not skill_id:
        return None

    meta = _load_skill_meta(skill_id)
    skill_rel = meta.get("skill_path")
    if skill_rel:
        assets_dir = repo_root() / skill_rel / "assets"
        named = assets_dir / Path(artifact_path).name
        if named.is_file():
            return named

    replay = _load_replay_response(skill_id)
    for row in replay.get("artifacts") or []:
        if not isinstance(row, dict):
            continue
        path = row.get("path")
        if path and Path(path).name == Path(artifact_path).name:
            if skill_rel:
                asset = repo_root() / skill_rel / "assets" / Path(path).name
                if asset.is_file():
                    return asset
    return None


# ---------------------------------------------------------------------------
# Public API (backward-compatible function signatures)
# ---------------------------------------------------------------------------

def load_artifact_content(
    task_id: str,
    artifact: dict[str, Any],
    capability_calls: list[dict[str, Any]] | None = None,
    *,
    allow_replay_fallback: bool = True,
) -> tuple[str, str]:
    """Load artifact text content.

    Resolution order:
      1. ArtifactResolver (inline / local / external)
      2. Replay / fixture fallback (dev/demo data)
    """
    if not allow_replay_fallback:
        blob, source_mode = load_artifact_binary(
            task_id,
            artifact,
            capability_calls,
            allow_replay_fallback=False,
        )
        try:
            return blob.decode("utf-8"), source_mode
        except UnicodeDecodeError as exc:
            raise ValueError("artifact_content_not_utf8") from exc

    resolver = get_resolver()

    # 1) Primary resolver path
    try:
        content, source_mode = resolver.resolve_content(task_id, artifact)
        return content, source_mode
    except (FileNotFoundError, NotImplementedError, PermissionError):
        pass

    # 2) Replay / fixture fallback
    artifact_path = artifact.get("path")
    skill_id = artifact.get("relatedSkillId")
    if artifact_path:
        replay_path = _resolve_replay_path(task_id, artifact_path, skill_id)
        if replay_path:
            return _read_text(replay_path), "replay"

    raise FileNotFoundError(f"artifact content not found: {artifact.get('id')}")


def load_artifact_binary(
    task_id: str,
    artifact: dict[str, Any],
    capability_calls: list[dict[str, Any]] | None = None,
    *,
    allow_replay_fallback: bool = True,
) -> tuple[bytes, str]:
    """Return the exact binary bytes that were validated for this response."""
    # Content-addressed object-store artifacts are backend-blob reads keyed by
    # sha256; they bypass the filesystem/replay resolution entirely.
    if str(artifact.get("storageType") or "local") == "object":
        sha = artifact.get("contentSha256")
        if not sha:
            raise FileNotFoundError("artifact content is unavailable")
        from app.modules import object_store

        try:
            blob = object_store.get_content(str(sha))
            if not allow_replay_fallback:
                validate_durable_bytes(artifact, blob)
            return blob, "object"
        except (FileNotFoundError, ValueError) as exc:
            raise FileNotFoundError("artifact content is unavailable") from exc
    if allow_replay_fallback:
        path = resolve_artifact_path(
            task_id, artifact, capability_calls, allow_replay_fallback=True
        )
        if path is None or not path.is_file():
            raise FileNotFoundError("artifact content is unavailable")
        return path.read_bytes(), str(artifact.get("storageType") or "local")

    storage_type, storage_ref = _durable_storage_ref(artifact)
    if storage_type == "inline":
        value = artifact.get("inlineContent")
        if value is None:
            raise FileNotFoundError("artifact content is unavailable")
        blob = str(value).encode("utf-8")
    elif storage_type in {"external", "local"}:
        from app.modules.artifact_write import artifact_write_service

        try:
            blob = artifact_write_service.read_durable_bytes(
                task_id, storage_type, str(storage_ref), artifact.get("storageScope")
            )
        except Exception as exc:
            raise FileNotFoundError("artifact content is unavailable") from exc
        if blob is None:
            raise FileNotFoundError("artifact content is unavailable")
    validate_durable_bytes(artifact, blob)
    return blob, storage_type


def load_artifact_sibling_bytes(
    task_id: str,
    artifact: dict[str, Any],
    filename: str,
    *,
    allow_legacy_fallback: bool = True,
) -> tuple[bytes, Path]:
    """Return one sibling's validated bytes and filename from the same read."""
    sibling = resolve_artifact_sibling(
        task_id, artifact, filename, allow_legacy_fallback=allow_legacy_fallback
    )
    blob = sibling.read_bytes()
    if not allow_legacy_fallback:
        manifest = _content_manifest(artifact)
        contract = manifest.get(Path(filename).as_posix())
        if contract is None:
            raise ValueError("artifact_content_manifest_missing_file")
        validate_durable_bytes(
            {
                "storageType": "local",
                "path": sibling.name,
                "contentSha256": contract.get("contentSha256"),
                "contentSize": contract.get("contentSize"),
            },
            blob,
        )
    return blob, sibling


def resolve_artifact_path(
    task_id: str,
    artifact: dict[str, Any],
    capability_calls: list[dict[str, Any]] | None = None,
    *,
    allow_replay_fallback: bool = True,
) -> Path | None:
    """Resolve the on-disk path for an artifact (binary-safe, no text reading).

    Resolution order:
      1. ArtifactResolver (inline writes temp file / local filesystem)
      2. Replay / fixture fallback
    """
    resolver = get_resolver()

    # 1) Primary resolver path
    try:
        path = resolver.resolve_binary_path(task_id, artifact)
        if not allow_replay_fallback:
            _validate_durable_binary_path(artifact, path)
        return path
    except (FileNotFoundError, NotImplementedError, PermissionError):
        pass

    if not allow_replay_fallback:
        return None

    # 2) Replay / fixture fallback
    artifact_path = artifact.get("path")
    skill_id = artifact.get("relatedSkillId")
    if artifact_path:
        replay_path = _resolve_replay_path(task_id, artifact_path, skill_id)
        if replay_path:
            return replay_path

    return None


def resolve_artifact_tree_root(
    task_id: str,
    artifact: dict[str, Any],
    *,
    allow_replay_fallback: bool = True,
) -> Path:
    """Resolve a directory root suitable for ``build_file_tree``.

    Raises FileNotFoundError when the artifact is not a directory or does not exist.
    """
    resolver = get_resolver()
    try:
        root = resolver.resolve_tree_root(task_id, artifact)
        if not allow_replay_fallback:
            _validate_durable_tree(root, artifact)
        return root
    except (FileNotFoundError, NotImplementedError, PermissionError):
        pass

    if not allow_replay_fallback:
        raise FileNotFoundError(f"artifact directory not found: {artifact.get('id')}")

    # Fallback: replay paths are files, not directories, so this will likely fail
    # with a clear message — which is acceptable because replay artifacts are
    # not expected to produce directory trees in Phase-1.
    artifact_path = artifact.get("path")
    skill_id = artifact.get("relatedSkillId")
    if artifact_path:
        replay_path = _resolve_replay_path(task_id, artifact_path, skill_id)
        if replay_path and replay_path.is_dir():
            return replay_path

    raise FileNotFoundError(f"artifact directory not found: {artifact.get('id')}")


def resolve_artifact_sibling(
    task_id: str,
    artifact: dict[str, Any],
    filename: str,
    *,
    allow_legacy_fallback: bool = True,
) -> Path:
    """Resolve a sibling file next to the artifact's path."""
    resolver = get_resolver()
    try:
        sibling = resolver.resolve_sibling_file(task_id, artifact, filename)
        if not allow_legacy_fallback:
            storage_type, _storage_ref = _durable_storage_ref(artifact)
            if storage_type != "local" or not sibling.is_file():
                raise ValueError("artifact_storage_reference_invalid")
            manifest = _content_manifest(artifact)
            normalized = Path(filename).as_posix()
            contract = manifest.get(normalized)
            if contract is None:
                raise ValueError("artifact_content_manifest_missing_file")
            _validate_manifest_file(sibling, contract)
        return sibling
    except (FileNotFoundError, NotImplementedError, PermissionError):
        pass

    if not allow_legacy_fallback:
        raise FileNotFoundError(f"sibling file not found: {filename}")

    # Fallback to legacy local path resolution for sibling files
    from app.execution.execution_context import get_task_workspace_dir
    artifact_path = artifact.get("path")
    skill_id = artifact.get("relatedSkillId")
    if artifact_path:
        if artifact_path.startswith("workspace/"):
            parts = artifact_path.split("/", 2)
            ws_dir = get_task_workspace_dir(task_id)
            path = ws_dir / parts[2] if len(parts) >= 3 else ws_dir / artifact_path
        else:
            path = get_task_workspace_dir(task_id) / artifact_path
        if path.exists():
            sibling = (path.parent / filename).resolve()
            if str(sibling).startswith(str(path.parent.resolve())) and sibling.is_file():
                return sibling

    raise FileNotFoundError(f"sibling file not found: {filename}")


# ---------------------------------------------------------------------------
# File tree builder (unchanged)
# ---------------------------------------------------------------------------

_SKIP_NAMES = {".git", "__pycache__", ".venv", "node_modules", ".idea", ".vscode"}
_SKIP_EXTS = {".pyc"}


def build_file_tree(root: Path, max_depth: int = 8, max_entries: int = 500) -> dict[str, Any] | None:
    """Recursively build a file tree dict from a directory."""
    count = 0

    def _walk(dir_path: Path, depth: int) -> list[dict[str, Any]] | None:
        nonlocal count
        if depth > max_depth:
            return None
        try:
            entries = sorted(dir_path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except PermissionError:
            return None
        children: list[dict[str, Any]] = []
        for entry in entries:
            if entry.name in _SKIP_NAMES or entry.suffix in _SKIP_EXTS:
                continue
            count += 1
            if count > max_entries:
                break
            if entry.is_dir():
                sub = _walk(entry, depth + 1)
                if sub is not None:
                    children.append({"name": entry.name, "type": "directory", "children": sub})
            else:
                try:
                    size = entry.stat().st_size
                except OSError:
                    size = 0
                children.append({"name": entry.name, "type": "file", "size": size})
        return children

    children = _walk(root, 0)
    if children is None:
        return None
    return {"name": root.name, "type": "directory", "children": children}
