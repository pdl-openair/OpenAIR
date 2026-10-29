"""Artifact content resolver: unified entry for metadata vs content/blob layering.

Storage types:
  - ``inline`` — content is stored directly in artifact metadata (``inlineContent``).
  - ``local`` — content resides on the local filesystem; paths are resolved via
    ``execution_context.get_task_workspace_dir()`` rather than hard-coded
    ``repo_root() / "workspace"``.
  - ``external`` — content is stored in MinIO / object storage.  Object key is
    stored in the artifact's ``externalKey`` field.

Backward compatibility:
  - Artifacts without ``storageType`` are treated as ``local``.
  - The legacy ``path`` field (e.g. ``workspace/{taskId}/file.md``) continues to
    work for local resolution.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Any

from app.execution.execution_context import get_task_workspace_dir

logger = logging.getLogger(__name__)


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _resolve_object_text(artifact: dict[str, Any]) -> tuple[str, str]:
    """Read a content-addressed (``object``) artifact as text.

    The blob lives in the content store keyed by ``contentSha256``.
    """
    from app.modules import object_store

    sha = artifact.get("contentSha256")
    if not sha:
        raise FileNotFoundError("artifact content is unavailable")
    blob = object_store.get_content(str(sha))
    try:
        return blob.decode("utf-8"), "object"
    except UnicodeDecodeError as exc:
        raise FileNotFoundError("artifact content is not decodable as text") from exc


def _resolve_object_binary_path(artifact: dict[str, Any], suffix: str) -> Path:
    """Materialise a content-addressed (``object``) artifact to a temp file."""
    from app.modules import object_store

    sha = artifact.get("contentSha256")
    if not sha:
        raise FileNotFoundError("artifact content is unavailable")
    blob = object_store.get_content(str(sha))
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
        f.write(blob)
        return Path(f.name)


def _resolve_local_path(task_id: str, artifact_path: str | None) -> Path | None:
    """Resolve a local artifact path using the configured workspace root.

    Supports the legacy ``workspace/{taskId}/filename`` format as well as
    bare filenames inside the task workspace.
    """
    if not artifact_path:
        return None

    ws_dir = get_task_workspace_dir(task_id)

    # 1) Full relative path: workspace/{taskId}/filename
    if artifact_path.startswith("workspace/"):
        # Strip the "workspace/{taskId}/" prefix if present so we can rebuild
        # from the configured workspace root.
        parts = artifact_path.split("/", 2)
        if len(parts) >= 3:
            candidate = ws_dir / parts[2]
        else:
            candidate = ws_dir / artifact_path
        if candidate.is_file() or candidate.is_dir():
            return candidate
        return None

    # 2) Bare filename or short path — resolve inside task workspace
    candidate = ws_dir / artifact_path
    if candidate.is_file() or candidate.is_dir():
        return candidate

    # 3) Search by basename (fallback for legacy mismatch)
    target_name = Path(artifact_path).name
    if ws_dir.is_dir():
        for hit in ws_dir.rglob(target_name):
            if hit.is_file() or hit.is_dir():
                return hit
    return None


class ArtifactResolver:
    """Resolve artifact content regardless of underlying storage type."""

    def resolve_content(
        self,
        task_id: str,
        artifact: dict[str, Any],
    ) -> tuple[str, str]:
        """Return (text_content, source_mode).

        Raises:
            FileNotFoundError: when the artifact content cannot be resolved.
        """
        storage_type = artifact.get("storageType", "local")

        if storage_type == "inline":
            content = artifact.get("inlineContent")
            if content is not None:
                return str(content), "inline"
            # Fallback to local if inlineContent is missing (backward compat)
            logger.warning("artifact %s marked inline but inlineContent missing; falling back to local", artifact.get("id"))
            storage_type = "local"

        if storage_type == "local":
            path = _resolve_local_path(task_id, artifact.get("path"))
            if path and path.is_file():
                return _read_text(path), "local"
            raise FileNotFoundError(f"artifact file not found: {artifact.get('path')}")

        if storage_type == "object":
            return _resolve_object_text(artifact)

        if storage_type == "external":
            return self._resolve_external_content(task_id, artifact)

        raise ValueError(f"unknown artifact storageType: {storage_type}")

    def resolve_binary_path(
        self,
        task_id: str,
        artifact: dict[str, Any],
    ) -> Path:
        """Return a filesystem path suitable for FileResponse.

        For inline artifacts, a temporary file is created. Callers should
        consider the lifecycle of that temp file.
        """
        storage_type = artifact.get("storageType", "local")

        if storage_type == "inline":
            content = artifact.get("inlineContent")
            if content is not None:
                # Write to a temporary file so FileResponse can serve it
                suffix = self._guess_suffix(artifact.get("format"))
                with tempfile.NamedTemporaryFile(mode="w", suffix=suffix, delete=False, encoding="utf-8") as f:
                    f.write(str(content))
                    return Path(f.name)
            logger.warning("artifact %s marked inline but inlineContent missing; falling back to local", artifact.get("id"))
            storage_type = "local"

        if storage_type == "local":
            path = _resolve_local_path(task_id, artifact.get("path"))
            if path and (path.is_file() or path.is_dir()):
                return path
            raise FileNotFoundError(f"artifact file not found: {artifact.get('path')}")

        if storage_type == "object":
            return _resolve_object_binary_path(artifact, self._guess_suffix(artifact.get("format")))

        if storage_type == "external":
            return self._resolve_external_binary(task_id, artifact)

        raise ValueError(f"unknown artifact storageType: {storage_type}")

    def resolve_tree_root(
        self,
        task_id: str,
        artifact: dict[str, Any],
    ) -> Path:
        """Return the directory root for building a file tree."""
        storage_type = artifact.get("storageType", "local")

        if storage_type == "inline":
            raise FileNotFoundError("inline artifact does not have a directory tree")

        if storage_type == "local":
            path = _resolve_local_path(task_id, artifact.get("path"))
            if path and path.is_dir():
                return path
            if path and path.is_file():
                raise FileNotFoundError(f"artifact is a file, not a directory: {artifact.get('path')}")
            raise FileNotFoundError(f"artifact directory not found: {artifact.get('path')}")

        if storage_type == "external":
            raise FileNotFoundError(
                "external artifact directory tree listing is not yet supported; "
                "use individual file access instead."
            )

        raise ValueError(f"unknown artifact storageType: {storage_type}")

    def resolve_sibling_file(
        self,
        task_id: str,
        artifact: dict[str, Any],
        filename: str,
    ) -> Path:
        """Resolve a sibling file next to the artifact's local path."""
        storage_type = artifact.get("storageType", "local")

        if storage_type == "inline":
            raise FileNotFoundError("inline artifact does not have sibling files on disk")

        if storage_type == "local":
            path = _resolve_local_path(task_id, artifact.get("path"))
            if not path:
                raise FileNotFoundError(f"artifact file not found: {artifact.get('path')}")
            sibling = (path.parent / filename).resolve()
            # Prevent path traversal
            if not str(sibling).startswith(str(path.parent.resolve())):
                raise PermissionError("path traversal denied")
            if sibling.is_file():
                return sibling
            raise FileNotFoundError(f"sibling file not found: {filename}")

        if storage_type == "external":
            return self._resolve_external_sibling(task_id, artifact, filename)

        raise ValueError(f"unknown artifact storageType: {storage_type}")

    # ------------------------------------------------------------------
    # External (MinIO) helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _get_object_key(task_id: str, artifact: dict[str, Any]) -> str:
        """Determine the MinIO object key for an artifact."""
        key = artifact.get("externalKey")
        if key:
            return key
        # Fallback: derive from path field
        path = artifact.get("path") or ""
        if path.startswith("workspace/"):
            parts = path.split("/", 2)
            return parts[2] if len(parts) >= 3 else path
        return f"{task_id}/{Path(path).name}" if path else f"{task_id}/{artifact.get('id', 'unknown')}"

    def _resolve_external_content(self, task_id: str, artifact: dict[str, Any]) -> tuple[str, str]:
        """Resolve artifact text content from MinIO."""
        from app.modules.minio_client import download_bytes

        object_key = self._get_object_key(task_id, artifact)
        data = download_bytes(object_key)
        return data.decode("utf-8"), "external"

    def _resolve_external_binary(self, task_id: str, artifact: dict[str, Any]) -> Path:
        """Download artifact from MinIO to a temp file and return the path."""
        from app.modules.minio_client import download_to_tempfile

        object_key = self._get_object_key(task_id, artifact)
        suffix = self._guess_suffix(artifact.get("format"))
        return download_to_tempfile(object_key, suffix=suffix)

    def _resolve_external_sibling(self, task_id: str, _artifact: dict[str, Any], filename: str) -> Path:
        """Download a sibling file from MinIO."""
        from app.modules.minio_client import download_to_tempfile

        # Sibling shares the same prefix (task_id/) but different filename
        sibling_key = f"{task_id}/{filename}"
        suffix = Path(filename).suffix
        return download_to_tempfile(sibling_key, suffix=suffix)

    @staticmethod
    def _guess_suffix(fmt: str | None) -> str:
        fmt = (fmt or "").lower()
        if fmt in ("yaml", "yml"):
            return ".yaml"
        if fmt == "json":
            return ".json"
        if fmt == "markdown":
            return ".md"
        return ".txt"


# Global singleton resolver (stateless)
_resolver = ArtifactResolver()


def get_resolver() -> ArtifactResolver:
    return _resolver
