"""Whole-task export archive: zip of every artifact body plus a manifest.

Mirrors the agent-session zip builder (`agent_session_artifact_archive`) but
sources artifacts from a research task. Failure handling is best-effort and
*recorded*: an artifact whose body cannot be loaded is listed in the manifest
with a skipReason instead of failing the whole export.
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from datetime import datetime, timezone
from typing import Any, Callable

from app.modules.artifact_content import load_artifact_binary

# Cap the in-memory archive so one huge task cannot exhaust the API process.
_MAX_EXPORT_BYTES = 512 * 1024 * 1024

# Keep CJK letters in filenames; strip only path separators, Windows-reserved
# characters and control bytes.
_UNSAFE_FILENAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')

_FORMAT_EXTENSIONS = {
    "markdown": ".md",
    "md": ".md",
    "text": ".txt",
    "txt": ".txt",
    "json": ".json",
    "csv": ".csv",
    "yaml": ".yaml",
    "yml": ".yml",
    "png": ".png",
    "svg": ".svg",
    "jpg": ".jpg",
    "jpeg": ".jpg",
    "pdf": ".pdf",
    "cif": ".cif",
    "py": ".py",
}


class TaskExportError(ValueError):
    """Raised when the task has no artifacts at all to pack."""


def _artifact_filename(artifact: dict[str, Any], index: int) -> str:
    raw = str(
        artifact.get("title")
        or artifact.get("path")
        or artifact.get("id")
        or f"artifact_{index}"
    )
    raw = raw.replace("\\", "/").split("/")[-1]
    stem = _UNSAFE_FILENAME.sub("_", raw).strip("._-") or f"artifact_{index}"
    extension = _FORMAT_EXTENSIONS.get(str(artifact.get("format") or "").lower())
    if extension and not stem.lower().endswith(extension):
        stem = f"{stem}{extension}"
    return stem[:160]


def _unique_name(name: str, index: int, used: set[str]) -> str:
    if name not in used:
        return name
    dot = name.rfind(".")
    base, ext = (name[:dot], name[dot:]) if dot > 0 else (name, "")
    candidate = f"{base}_{index}{ext}"
    while candidate in used:
        candidate = f"{base}_{index}_{len(used)}{ext}"
    return candidate


def build_task_export_zip(
    *,
    task: dict[str, Any],
    prepare_artifact: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> tuple[bytes, str]:
    """Pack every artifact of ``task`` into a zip next to a manifest.json."""
    task_id = str(task.get("taskId") or task.get("id") or "task")
    capability_calls = task.get("capabilityCalls") or []
    artifacts = [item for item in task.get("artifacts") or [] if isinstance(item, dict)]
    if not artifacts:
        raise TaskExportError("no artifacts to export")

    entries: list[dict[str, Any]] = []
    used: set[str] = set()
    packed_bytes = 0
    size_truncated = False
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        for index, artifact in enumerate(artifacts, start=1):
            entry: dict[str, Any] = {
                "artifactId": artifact.get("id") or artifact.get("artifactId"),
                "title": artifact.get("title"),
                "kind": artifact.get("kind"),
                "format": artifact.get("format"),
                "stage": artifact.get("relatedStage") or artifact.get("stage"),
                "substage": artifact.get("substage"),
            }
            entries.append(entry)
            if size_truncated:
                entry["skipped"] = True
                entry["skipReason"] = "export_size_limit"
                continue
            try:
                candidate = dict(artifact)
                if prepare_artifact is not None:
                    candidate = prepare_artifact(candidate)
                blob, _source_mode = load_artifact_binary(
                    task_id, candidate, capability_calls, allow_replay_fallback=True
                )
            except (OSError, ValueError, KeyError):
                entry["skipped"] = True
                entry["skipReason"] = "content_unavailable"
                continue
            if packed_bytes + len(blob) > _MAX_EXPORT_BYTES:
                size_truncated = True
                entry["skipped"] = True
                entry["skipReason"] = "export_size_limit"
                continue
            packed_bytes += len(blob)
            name = _unique_name(_artifact_filename(artifact, index), index, used)
            used.add(name)
            entry["file"] = name
            archive.writestr(name, blob)

        manifest = {
            "taskId": task_id,
            "taskTitle": task.get("title"),
            "exportedAt": datetime.now(timezone.utc).isoformat(),
            "artifactCount": len(artifacts),
            "packedCount": len(used),
            "truncated": size_truncated,
            "artifacts": entries,
        }
        archive.writestr(
            "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2)
        )

    task_stem = (
        _UNSAFE_FILENAME.sub("_", str(task.get("title") or task_id)).strip("._-")
        or task_id
    )
    zip_name = f"openair_{task_stem[:60]}_{task_id[:8]}.zip"
    return buffer.getvalue(), zip_name
