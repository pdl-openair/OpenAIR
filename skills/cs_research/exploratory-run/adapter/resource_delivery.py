"""Deterministic, bounded delivery of formal resources into one execution workspace."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import tarfile
import tempfile
from typing import Any, NamedTuple

_MAX_ARCHIVE_MEMBERS = 20_000
_MAX_ARCHIVE_FILE_BYTES = 512 * 1024 * 1024
_MAX_ARCHIVE_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
_MAX_ARCHIVE_PATH_LENGTH = 1024
_MAX_ARCHIVE_PATH_DEPTH = 64
_COPY_CHUNK_BYTES = 1024 * 1024
_DELIVERY_MANIFEST_SCHEMA_VERSION = "openair-resource-delivery-manifest/v1"


class ArchiveDeliveryState(NamedTuple):
    published_files: tuple[Path, ...]
    created_directories: tuple[Path, ...]
    manifest_bytes: bytes


class StagedArchive(NamedTuple):
    directories: set[tuple[str, ...]]
    files: set[tuple[str, ...]]
    file_rows: list[dict[str, Any]]
    member_count: int
    total_file_bytes: int


def _sha256_file(path: Path) -> str | None:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(_COPY_CHUNK_BYTES)
                if not chunk:
                    break
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def _archive_member_parts(name: Any) -> tuple[str, ...] | None:
    if not isinstance(name, str) or not name or len(name) > _MAX_ARCHIVE_PATH_LENGTH:
        return None
    if "\\" in name or "\x00" in name or any(ord(char) < 32 for char in name):
        return None
    path = PurePosixPath(name)
    if path.is_absolute() or not path.parts or len(path.parts) > _MAX_ARCHIVE_PATH_DEPTH:
        return None
    if any(part in {"", ".", ".."} or ":" in part for part in path.parts):
        return None
    return tuple(path.parts)


def _copy_member(
    archive: tarfile.TarFile,
    member: tarfile.TarInfo,
    staging: Path,
    parts: tuple[str, ...],
) -> dict[str, Any] | None:
    extracted = archive.extractfile(member)
    if extracted is None:
        return None
    target = staging.joinpath(*parts)
    target.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    copied = 0
    try:
        with target.open("xb") as output:
            while True:
                chunk = extracted.read(_COPY_CHUNK_BYTES)
                if not chunk:
                    break
                copied += len(chunk)
                if copied > member.size:
                    return None
                digest.update(chunk)
                output.write(chunk)
    finally:
        extracted.close()
    if copied != member.size:
        return None
    return {
        "path": PurePosixPath(*parts).as_posix(),
        "size": member.size,
        "sha256": digest.hexdigest(),
    }


def _stage_archive(source: Path, staging: Path) -> StagedArchive | None:
    directories: set[tuple[str, ...]] = set()
    files: set[tuple[str, ...]] = set()
    file_rows: list[dict[str, Any]] = []
    member_count = 0
    total_file_bytes = 0
    try:
        archive = tarfile.open(source, mode="r:*")
    except (OSError, tarfile.TarError):
        return None

    with archive:
        for member in archive:
            member_count += 1
            if member_count > _MAX_ARCHIVE_MEMBERS:
                return None
            parts = _archive_member_parts(member.name)
            if parts is None:
                return None
            if member.isdir():
                if parts in files:
                    return None
                directories.add(parts)
                continue
            if not member.isfile() or parts in files or parts in directories:
                return None
            if member.size < 0 or member.size > _MAX_ARCHIVE_FILE_BYTES:
                return None
            total_file_bytes += member.size
            if total_file_bytes > _MAX_ARCHIVE_TOTAL_BYTES:
                return None
            row = _copy_member(archive, member, staging, parts)
            if row is None:
                return None
            files.add(parts)
            directories.update(parts[:index] for index in range(1, len(parts)))
            file_rows.append(row)

    if not files:
        return None
    return StagedArchive(directories, files, file_rows, member_count, total_file_bytes)


def _existing_directory_is_safe(root: Path, candidate: Path) -> bool:
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError):
        return False
    return not candidate.is_symlink() and candidate.is_dir()


def _validate_publish_targets(
    root: Path,
    directories: set[tuple[str, ...]],
    files: set[tuple[str, ...]],
) -> bool:
    for parts in sorted(directories | files, key=lambda value: (len(value), value)):
        target = root.joinpath(*parts)
        current = target.parent
        while current != root:
            if os.path.lexists(current) and not _existing_directory_is_safe(root, current):
                return False
            parent = current.parent
            if parent == current:
                return False
            current = parent
        if parts in files and os.path.lexists(target):
            return False
        if parts in directories and os.path.lexists(target) and not _existing_directory_is_safe(root, target):
            return False
    return True


def _rollback_delivery(state: ArchiveDeliveryState) -> None:
    for path in reversed(state.published_files):
        try:
            if os.path.lexists(path) and (path.is_file() or path.is_symlink()):
                path.unlink()
        except OSError:
            pass
    for path in sorted(state.created_directories, key=lambda item: len(item.parts), reverse=True):
        try:
            path.rmdir()
        except OSError:
            pass


def cleanup_resource_deliveries(states: list[ArchiveDeliveryState]) -> None:
    for state in reversed(states):
        _rollback_delivery(state)


def _publish_archive(root: Path, staging: Path, archive: StagedArchive) -> ArchiveDeliveryState | None:
    if not _validate_publish_targets(root, archive.directories, archive.files):
        return None
    created_directories: list[Path] = []
    published_files: list[Path] = []
    try:
        for parts in sorted(archive.directories, key=lambda value: (len(value), value)):
            target = root.joinpath(*parts)
            if os.path.lexists(target):
                continue
            target.mkdir()
            created_directories.append(target)
        for parts in sorted(archive.files):
            source_file = staging.joinpath(*parts)
            target = root.joinpath(*parts)
            if os.path.lexists(target):
                raise OSError("archive delivery target changed before publish")
            source_file.replace(target)
            published_files.append(target)
    except OSError:
        _rollback_delivery(ArchiveDeliveryState(tuple(published_files), tuple(created_directories), b""))
        return None
    return ArchiveDeliveryState(tuple(published_files), tuple(created_directories), b"")


def _manifest_bytes(
    workspace: Path,
    root: Path,
    archive: StagedArchive,
    archive_sha256: str,
) -> bytes | None:
    try:
        workspace_root = workspace.resolve(strict=True)
        target = root.relative_to(workspace_root).as_posix()
        files = [
            {
                "workspace_relative_path": root.joinpath(*PurePosixPath(row["path"]).parts)
                .relative_to(workspace_root)
                .as_posix(),
                "size": row["size"],
                "sha256": row["sha256"],
            }
            for row in sorted(archive.file_rows, key=lambda item: item["path"])
        ]
    except ValueError:
        return None
    payload = {
        "schema_version": _DELIVERY_MANIFEST_SCHEMA_VERSION,
        "archive_sha256": archive_sha256,
        "target_working_directory": target,
        "files": files,
    }
    return (json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _atomic_write(path: Path, data: bytes) -> bool:
    try:
        fd, raw_temp = tempfile.mkstemp(prefix=".delivery-manifest-", suffix=".tmp", dir=str(path.parent))
        temp = Path(raw_temp)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            temp.replace(path)
        except Exception:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        return True
    except OSError:
        return False


def persist_archive_delivery_manifest(
    run_dir: Path,
    workspace: Path,
    usage: str,
    delivery: dict[str, Any],
    state: ArchiveDeliveryState,
) -> bool:
    if not state.manifest_bytes or hashlib.sha256(state.manifest_bytes).hexdigest() != delivery.get("manifest_sha256"):
        return False
    evidence_dir = run_dir / "resource-delivery"
    try:
        evidence_dir.mkdir(exist_ok=True)
        if evidence_dir.is_symlink() or not evidence_dir.is_dir():
            return False
    except OSError:
        return False
    target = evidence_dir / f"{usage}.json"
    if target.exists() or not _atomic_write(target, state.manifest_bytes):
        return False
    try:
        delivery["manifest_relative_path"] = target.relative_to(workspace.resolve(strict=True)).as_posix()
    except ValueError:
        return False
    return True


def deliver_tar_to_working_directory(
    workspace: Path,
    working_directory: Path,
    source: Path,
    *,
    expected_sha256: str,
) -> tuple[dict[str, Any], ArchiveDeliveryState] | None:
    actual_sha256 = _sha256_file(source)
    if actual_sha256 is None or actual_sha256 != expected_sha256:
        return None
    try:
        staging = Path(tempfile.mkdtemp(prefix=".openair-resource-delivery-", dir=str(workspace)))
    except OSError:
        return None

    try:
        archive = _stage_archive(source, staging)
        if archive is None:
            return None
        root = working_directory.resolve(strict=True)
        state = _publish_archive(root, staging, archive)
        if state is None:
            return None
        manifest = _manifest_bytes(workspace, root, archive, actual_sha256)
        if manifest is None:
            _rollback_delivery(state)
            return None
        state = ArchiveDeliveryState(state.published_files, state.created_directories, manifest)
        delivery = {
            "kind": "tar_extract_working_directory",
            "target_working_directory": root.relative_to(workspace.resolve(strict=True)).as_posix(),
            "archive_sha256": actual_sha256,
            "manifest_sha256": hashlib.sha256(manifest).hexdigest(),
            "member_count": archive.member_count,
            "file_count": len(archive.file_rows),
            "total_file_bytes": archive.total_file_bytes,
        }
        return delivery, state
    finally:
        shutil.rmtree(staging, ignore_errors=True)
