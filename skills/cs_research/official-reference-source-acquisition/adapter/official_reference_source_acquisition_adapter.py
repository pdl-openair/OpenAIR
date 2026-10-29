"""Read bounded official reference documents from the already-fixed repository checkout."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
from typing import Any

_SCHEMA_VERSION = "openair-reference-source-package/v1"
_ALLOWED_TEXT_SUFFIXES = frozenset({".md", ".rst", ".txt", ".adoc"})
_COMMIT_SHA = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_RUNTIME_WORKSPACE_ENV = "OPENAIR_ADAPTER_EXECUTION_CWD"
_GIT_TIMEOUT_SECONDS = 60
_MAX_DOCUMENTS = 12
_MAX_DOCUMENT_BYTES = 49152
_MAX_TOTAL_BYTES = 131072
_GIT_CONTEXT_ENV_KEYS = frozenset({
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_CEILING_DIRECTORIES",
    "GIT_COMMON_DIR",
    "GIT_DIR",
    "GIT_EXEC_PATH",
    "GIT_INDEX_FILE",
    "GIT_NAMESPACE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_PREFIX",
    "GIT_TEMPLATE_DIR",
    "GIT_WORK_TREE",
})


def _failure(code: str, stage: str) -> dict[str, Any]:
    return {"state": "failed", "failure_code": code, "failed_stage": stage}


def _validated_input(payload: dict[str, Any]) -> dict[str, str] | None:
    if set(payload) != {"code_source"}:
        return None
    raw = payload.get("code_source")
    if not isinstance(raw, dict):
        return None
    required = {
        "state",
        "repository",
        "requested_revision",
        "resolved_commit_sha",
        "verified_head",
        "workspace_relative_path",
    }
    if not required.issubset(raw):
        return None
    values = {key: raw.get(key) for key in required}
    if any(not isinstance(value, str) or not value for value in values.values()):
        return None
    if values["state"] != "succeeded":
        return None
    commit = values["resolved_commit_sha"].lower()
    head = values["verified_head"].lower()
    if not _COMMIT_SHA.fullmatch(commit) or head != commit:
        return None
    relative = values["workspace_relative_path"]
    posix = PurePosixPath(relative)
    if relative.startswith("/") or ".." in posix.parts or posix.is_absolute():
        return None
    return {
        "repository": values["repository"],
        "requested_revision": values["requested_revision"],
        "resolved_commit_sha": commit,
        "workspace_relative_path": relative,
    }


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


def _repository_dir(workspace: Path, relative: str) -> Path | None:
    target = workspace.joinpath(*PurePosixPath(relative).parts)
    try:
        resolved_workspace = workspace.resolve(strict=True)
        resolved_target = target.resolve(strict=True)
        resolved_target.relative_to(resolved_workspace)
    except (OSError, ValueError):
        return None
    return resolved_target if resolved_target.is_dir() else None


def _git_environment() -> dict[str, str]:
    env = os.environ.copy()
    for key in tuple(env):
        if (
            key in _GIT_CONTEXT_ENV_KEYS
            or key in {"GIT_CONFIG_COUNT", "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_SYSTEM", "GIT_CONFIG_GLOBAL"}
            or key.startswith("GIT_CONFIG_KEY_")
            or key.startswith("GIT_CONFIG_VALUE_")
        ):
            env.pop(key, None)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_LFS_SKIP_SMUDGE"] = "1"
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    return env


def _run_git(git_executable: str, args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [git_executable, *args],
        cwd=str(cwd),
        env=_git_environment(),
        capture_output=True,
        check=False,
        shell=False,
        timeout=_GIT_TIMEOUT_SECONDS,
    )


def _commit_from_stdout(value: bytes) -> str | None:
    try:
        candidate = value.decode("ascii").strip().lower()
    except UnicodeDecodeError:
        return None
    return candidate if _COMMIT_SHA.fullmatch(candidate) else None


def _parse_tree_rows(raw: bytes) -> list[tuple[str, int]]:
    rows: list[tuple[str, int]] = []
    text = raw.decode("utf-8", errors="replace")
    for line in text.splitlines():
        if "\t" not in line:
            continue
        metadata, path = line.split("\t", 1)
        parts = metadata.split()
        if len(parts) != 4 or parts[1] != "blob":
            continue
        try:
            size = int(parts[3])
        except ValueError:
            continue
        posix = PurePosixPath(path)
        if (
            not path
            or path.startswith("/")
            or ".." in posix.parts
            or posix.suffix.lower() not in _ALLOWED_TEXT_SUFFIXES
            or size <= 0
            or size > _MAX_DOCUMENT_BYTES
        ):
            continue
        rows.append((path, size))
    rows.sort(key=lambda row: (len(PurePosixPath(row[0]).parts), row[0].casefold(), row[0]))
    return rows


def _line_count(content: str) -> int:
    return max(1, len(content.splitlines()))


def _document_id(repository: str, commit: str, path: str, sha256: str) -> str:
    identity = f"{repository}\n{commit}\n{path}\n{sha256}".encode("utf-8")
    return f"refdoc:{hashlib.sha256(identity).hexdigest()}"


def _collect_documents(
    git_executable: str,
    *,
    repository_dir: Path,
    repository: str,
    commit: str,
) -> tuple[list[dict[str, Any]], int, int] | None:
    tree = _run_git(git_executable, ["ls-tree", "-r", "-l", commit], cwd=repository_dir)
    if tree.returncode != 0:
        return None
    candidates = _parse_tree_rows(tree.stdout)
    if not candidates:
        return None

    documents: list[dict[str, Any]] = []
    total = 0
    for path, _declared_size in candidates:
        if len(documents) >= _MAX_DOCUMENTS:
            break
        result = _run_git(git_executable, ["show", f"{commit}:{path}"], cwd=repository_dir)
        if result.returncode != 0 or not result.stdout:
            continue
        if len(result.stdout) > _MAX_DOCUMENT_BYTES or total + len(result.stdout) > _MAX_TOTAL_BYTES:
            continue
        try:
            content = result.stdout.decode("utf-8")
        except UnicodeDecodeError:
            continue
        if not content.strip():
            continue
        digest = hashlib.sha256(result.stdout).hexdigest()
        documents.append(
            {
                "document_id": _document_id(repository, commit, path, digest),
                "path": path,
                "sha256": digest,
                "line_count": _line_count(content),
                "content": content,
            }
        )
        total += len(result.stdout)
    if not documents:
        return None
    return documents, len(candidates), total


def _read_fixed_checkout(code_source: dict[str, str], *, workspace: Path) -> dict[str, Any]:
    git_executable = shutil.which("git")
    if git_executable is None:
        return _failure("git_unavailable", "git_preflight")
    repository_dir = _repository_dir(workspace, code_source["workspace_relative_path"])
    if repository_dir is None:
        return _failure("fixed_repository_unavailable", "workspace")

    try:
        version = _run_git(git_executable, ["--version"], cwd=repository_dir)
        head = _run_git(
            git_executable,
            ["rev-parse", "--verify", "--end-of-options", "HEAD^{commit}"],
            cwd=repository_dir,
        )
    except (OSError, subprocess.TimeoutExpired):
        return _failure("fixed_repository_unavailable", "verify_head")
    verified_head = _commit_from_stdout(head.stdout) if head.returncode == 0 else None
    if version.returncode != 0 or verified_head is None:
        return _failure("fixed_repository_unavailable", "verify_head")
    if verified_head != code_source["resolved_commit_sha"]:
        return _failure("fixed_repository_identity_mismatch", "verify_head")

    try:
        collected = _collect_documents(
            git_executable,
            repository_dir=repository_dir,
            repository=code_source["repository"],
            commit=verified_head,
        )
    except (OSError, subprocess.TimeoutExpired):
        return _failure("official_source_read_failed", "document_collection")
    if collected is None:
        return _failure("official_text_source_unavailable", "document_collection")
    documents, candidate_count, total = collected
    return {
        "schema_version": _SCHEMA_VERSION,
        "state": "succeeded",
        "source_kind": "official_repository",
        "repository": code_source["repository"],
        "requested_revision": code_source["requested_revision"],
        "resolved_commit_sha": verified_head,
        "workspace_relative_path": code_source["workspace_relative_path"],
        "documents": documents,
        "read_evidence": {
            "git_version": version.stdout.decode("utf-8", errors="replace").strip(),
            "verified_head": verified_head,
            "head_matches_upstream_artifact": True,
            "candidate_document_count": candidate_count,
            "included_document_count": len(documents),
            "total_included_bytes": total,
        },
    }


def run_official_reference_source_acquisition(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return _failure("skill_input_invalid", "input_validation")
    code_source = _validated_input(payload)
    if code_source is None:
        return _failure("skill_input_invalid", "input_validation")
    workspace = _runtime_workspace()
    if workspace is None:
        return _failure("runtime_workspace_invalid", "workspace")
    return _read_fixed_checkout(code_source, workspace=workspace)


__all__ = ["run_official_reference_source_acquisition"]
