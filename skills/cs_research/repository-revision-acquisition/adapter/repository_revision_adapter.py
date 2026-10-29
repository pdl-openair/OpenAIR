"""Deterministic Git repository + revision acquisition for CS reproducibility."""

from __future__ import annotations

import os
from pathlib import Path
import re
import stat
import shutil
import subprocess
from typing import Any
from urllib.parse import urlsplit

_REPOSITORY_DIRNAME = "repository"
_ALLOWED_REPOSITORY_SCHEMES = frozenset({"https", "git"})
_COMMIT_SHA = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_GIT_TIMEOUT_SECONDS = 180
_RUNTIME_WORKSPACE_ENV = "OPENAIR_ADAPTER_EXECUTION_CWD"
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


def _failure(code: str, stage: str, *, exit_code: int | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "state": "failed",
        "failure_code": code,
        "failed_stage": stage,
    }
    if exit_code is not None:
        result["git_exit_code"] = exit_code
    return result


def _contains_control_character(value: str) -> bool:
    return any(ord(char) < 32 or ord(char) == 127 for char in value)


def _validate_repository_url(repository: str) -> bool:
    try:
        parsed = urlsplit(repository)
    except ValueError:
        return False
    if parsed.scheme.lower() not in _ALLOWED_REPOSITORY_SCHEMES:
        return False
    if not parsed.hostname:
        return False
    if parsed.username is not None or parsed.password is not None:
        return False
    if parsed.query or parsed.fragment:
        return False
    return True


def _normalized_repository_identity(repository: str) -> str:
    parsed = urlsplit(repository)
    path = parsed.path.rstrip("/")
    if path.casefold().endswith(".git"):
        path = path[:-4]
    return f"{parsed.scheme.casefold()}://{parsed.netloc.casefold()}{path.casefold()}"


def _valid_optional_paper_evidence(value: dict[str, Any]) -> bool:
    if "content_form" in value and value["content_form"] not in (
        "metadata_only",
        "landing_page",
        "html",
        "pdf",
        "xml",
        "tei",
    ):
        return False
    if "paper_identity" not in value:
        return True
    identity = value["paper_identity"]
    limits = {"arxiv_id": 128, "title": 1024, "doi": 256, "openalex_id": 256}
    if not isinstance(identity, dict) or not identity or set(identity) - limits.keys():
        return False
    return all(
        isinstance(text, str) and 1 <= len(text) <= limits[key] for key, text in identity.items()
    )


def _validated_source_evidence(value: Any, repository: str) -> dict[str, Any] | None:
    if value is None:
        return None
    allowed = {
        "kind", "source_url", "repository_url", "selection_basis", "model_id",
        "content_form", "paper_identity",
    }
    required = {"kind", "source_url", "repository_url", "selection_basis"}
    if not isinstance(value, dict) or set(value) - allowed or not required.issubset(value):
        return None
    if not _valid_optional_paper_evidence(value):
        return None
    limits = {"kind": 128, "source_url": 4096, "repository_url": 2048, "selection_basis": 256}
    if any(not isinstance(value.get(key), str) or not value[key].strip() or value[key] != value[key].strip() or len(value[key]) > limit for key, limit in limits.items()):
        return None
    if not _validate_repository_url(value["repository_url"]):
        return None
    if _normalized_repository_identity(value["repository_url"]) != _normalized_repository_identity(repository):
        return None
    model_id = value.get("model_id")
    if model_id is not None and (not isinstance(model_id, str) or not model_id.strip() or model_id != model_id.strip() or len(model_id) > 256):
        return None
    return dict(value)


def _validated_input(payload: dict[str, Any]) -> tuple[str, str, dict[str, Any] | None] | None:
    if set(payload) not in ({"repository", "revision"}, {"repository", "revision", "source_evidence"}):
        return None
    repository = payload.get("repository")
    revision = payload.get("revision")
    if not isinstance(repository, str) or not isinstance(revision, str):
        return None
    if repository != repository.strip() or revision != revision.strip():
        return None
    if not repository or not revision or len(repository) > 4096 or len(revision) > 512:
        return None
    if _contains_control_character(repository) or _contains_control_character(revision):
        return None
    if not _validate_repository_url(repository):
        return None
    source_evidence = _validated_source_evidence(payload.get("source_evidence"), repository)
    if "source_evidence" in payload and source_evidence is None:
        return None
    return repository, revision, source_evidence


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
    env["GIT_ALLOW_PROTOCOL"] = "https:git"
    env["GIT_PROTOCOL_FROM_USER"] = "0"
    return env


def _run_git(git_executable: str, args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    command = [git_executable, *args]
    if os.name == "nt":
        from .windows_process import run_windows_process_tree

        return run_windows_process_tree(
            command,
            cwd=cwd,
            env=_git_environment(),
            timeout_seconds=_GIT_TIMEOUT_SECONDS,
            encoding="utf-8",
            errors="replace",
        )
    return subprocess.run(
        command,
        cwd=str(cwd),
        env=_git_environment(),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
        shell=False,
        timeout=_GIT_TIMEOUT_SECONDS,
    )


def _repository_target(workspace: Path) -> tuple[Path | None, str | None]:
    try:
        resolved_workspace = workspace.resolve(strict=True)
    except OSError:
        return None, "workspace_boundary_invalid"
    if not resolved_workspace.is_dir():
        return None, "workspace_boundary_invalid"
    target = (resolved_workspace / _REPOSITORY_DIRNAME).resolve(strict=False)
    try:
        target.relative_to(resolved_workspace)
    except ValueError:
        return None, "workspace_boundary_invalid"
    if target.exists() or target.is_symlink():
        return None, "repository_target_conflict"
    return target, None


def _retry_readonly_removal(func: Any, path: str, error: BaseException) -> None:
    """Retry one Windows removal after clearing the read-only attribute."""
    if not isinstance(error, PermissionError):
        raise error
    os.chmod(path, stat.S_IWRITE)
    func(path)


def _remove_repository_tree(target: Path) -> None:
    if os.name == "nt":
        shutil.rmtree(target, onexc=_retry_readonly_removal)
        return
    shutil.rmtree(target)


def _cleanup_repository_target(target: Path) -> bool:
    """Remove an untrusted repository target and verify that it is gone."""
    try:
        entry = target.lstat()
    except FileNotFoundError:
        return True
    except OSError:
        return False

    try:
        if stat.S_ISDIR(entry.st_mode) and not stat.S_ISLNK(entry.st_mode):
            _remove_repository_tree(target)
        else:
            target.unlink()
    except OSError:
        return False

    try:
        target.lstat()
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return False


def _failure_after_cleanup(
    target: Path,
    code: str,
    stage: str,
    *,
    exit_code: int | None = None,
) -> dict[str, Any]:
    if _cleanup_repository_target(target):
        return _failure(code, stage, exit_code=exit_code)

    result = _failure("repository_cleanup_failed", "cleanup")
    result["cleanup_triggered_by"] = code
    if exit_code is not None:
        result["git_exit_code"] = exit_code
    return result


def _commit_from_stdout(value: str) -> str | None:
    candidate = value.strip().lower()
    if not _COMMIT_SHA.fullmatch(candidate):
        return None
    return candidate


def _remote_branch_candidate(revision: str) -> str | None:
    if revision.startswith("refs/heads/"):
        branch = revision[len("refs/heads/") :]
        return f"refs/remotes/origin/{branch}" if branch else None
    if revision.startswith("refs/") or revision.startswith("origin/"):
        return None
    return f"refs/remotes/origin/{revision}"


def _resolve_revision(
    git_executable: str,
    revision: str,
    *,
    cwd: Path,
) -> tuple[str | None, subprocess.CompletedProcess[str]]:
    candidates = [revision]
    remote_branch = _remote_branch_candidate(revision)
    if remote_branch is not None:
        candidates.append(remote_branch)

    last = subprocess.CompletedProcess([], 128, stdout="", stderr="revision not resolved")
    for candidate in candidates:
        last = _run_git(
            git_executable,
            ["rev-parse", "--verify", "--end-of-options", f"{candidate}^{{commit}}"],
            cwd=cwd,
        )
        if last.returncode != 0:
            continue
        resolved = _commit_from_stdout(last.stdout)
        if resolved is not None:
            return resolved, last
    return None, last


def _reuse_verified_repository(
    repository: str, revision: str, *, workspace: Path,
    source_evidence: dict[str, Any] | None,
) -> dict[str, Any]:
    """Verify an existing checkout without modifying the preserved workspace."""
    target = workspace / _REPOSITORY_DIRNAME
    conflict = _failure("repository_target_conflict", "workspace")
    if target.is_symlink() or (hasattr(target, "is_junction") and target.is_junction()) or not target.is_dir():
        return conflict
    git_executable = shutil.which("git")
    if not git_executable:
        return _failure("git_unavailable", "git_preflight")
    try:
        resolved_target = target.resolve(strict=True)
        if resolved_target.parent != workspace.resolve(strict=True):
            return conflict
        top = _run_git(git_executable, ["rev-parse", "--show-toplevel"], cwd=target)
        gitdir = _run_git(git_executable, ["rev-parse", "--absolute-git-dir"], cwd=target)
        origin = _run_git(git_executable, ["remote", "get-url", "origin"], cwd=target)
        clean = _run_git(git_executable, ["status", "--porcelain", "--untracked-files=all"], cwd=target)
        head = _run_git(git_executable, ["rev-parse", "--verify", "--end-of-options", "HEAD^{commit}"], cwd=target)
        version = _run_git(git_executable, ["--version"], cwd=target)
        resolved_commit, resolved = _resolve_revision(git_executable, revision, cwd=target)
        if any(row.returncode != 0 for row in (top, gitdir, origin, clean, head, version, resolved)):
            return conflict
        if Path(top.stdout.strip()).resolve(strict=True) != resolved_target:
            return conflict
        Path(gitdir.stdout.strip()).resolve(strict=True).relative_to(resolved_target)
        actual_origin = origin.stdout.strip()
        if not _validate_repository_url(actual_origin):
            return conflict
        if _normalized_repository_identity(actual_origin) != _normalized_repository_identity(repository):
            return conflict
        verified_head = _commit_from_stdout(head.stdout)
        if clean.stdout.strip() or resolved_commit is None or verified_head != resolved_commit:
            return conflict
        if not version.stdout.strip():
            return conflict
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return conflict
    result = {
        "state": "succeeded", "repository": repository,
        "requested_revision": revision, "resolved_commit_sha": resolved_commit,
        "verified_head": verified_head, "workspace_relative_path": _REPOSITORY_DIRNAME,
        "execution_evidence": {
            "git_version": version.stdout.strip(),
            "operations": [
                {"operation": "verify_origin", "exit_code": origin.returncode},
                {"operation": "resolve_revision", "exit_code": resolved.returncode},
                {"operation": "verify_clean", "exit_code": clean.returncode},
                {"operation": "read_head", "exit_code": head.returncode},
            ],
            "head_matches_resolved_commit": True,
        },
    }
    if source_evidence is not None:
        result["source_evidence"] = source_evidence
    return result


def _acquire_repository_in_workspace(
    repository: str,
    revision: str,
    *,
    workspace: Path,
    source_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute real Git actions after caller-side input authority is established."""
    target, target_error = _repository_target(workspace)
    if target is None:
        if target_error == "repository_target_conflict":
            return _reuse_verified_repository(repository, revision, workspace=workspace, source_evidence=source_evidence)
        return _failure(target_error or "workspace_boundary_invalid", "workspace")

    git_executable = shutil.which("git")
    if not git_executable:
        return _failure("git_unavailable", "git_preflight")

    try:
        version = _run_git(git_executable, ["--version"], cwd=workspace)
    except (OSError, subprocess.TimeoutExpired):
        return _failure("git_unavailable", "git_preflight")
    if version.returncode != 0 or not version.stdout.strip():
        return _failure("git_unavailable", "git_preflight", exit_code=version.returncode)

    try:
        clone = _run_git(
            git_executable,
            ["clone", "--no-checkout", "--", repository, _REPOSITORY_DIRNAME],
            cwd=workspace,
        )
    except (OSError, subprocess.TimeoutExpired):
        return _failure_after_cleanup(target, "repository_unavailable", "clone")
    if clone.returncode != 0:
        return _failure_after_cleanup(target, "repository_unavailable", "clone", exit_code=clone.returncode)

    try:
        resolved_target = target.resolve(strict=True)
        resolved_target.relative_to(workspace.resolve(strict=True))
    except (OSError, ValueError):
        return _failure_after_cleanup(target, "workspace_boundary_invalid", "post_clone_workspace_check")

    try:
        resolved_commit, resolved = _resolve_revision(
            git_executable,
            revision,
            cwd=resolved_target,
        )
    except (OSError, subprocess.TimeoutExpired):
        return _failure_after_cleanup(target, "revision_unresolvable", "resolve_revision")
    if resolved_commit is None:
        return _failure_after_cleanup(target, "revision_unresolvable", "resolve_revision", exit_code=resolved.returncode)

    try:
        checkout = _run_git(
            git_executable,
            ["checkout", "--detach", resolved_commit],
            cwd=resolved_target,
        )
    except (OSError, subprocess.TimeoutExpired):
        return _failure_after_cleanup(target, "checkout_failed", "checkout")
    if checkout.returncode != 0:
        return _failure_after_cleanup(target, "checkout_failed", "checkout", exit_code=checkout.returncode)

    try:
        head = _run_git(
            git_executable,
            ["rev-parse", "--verify", "--end-of-options", "HEAD^{commit}"],
            cwd=resolved_target,
        )
    except (OSError, subprocess.TimeoutExpired):
        return _failure_after_cleanup(target, "head_read_failed", "read_head")
    verified_head = _commit_from_stdout(head.stdout) if head.returncode == 0 else None
    if verified_head is None:
        return _failure_after_cleanup(target, "head_read_failed", "read_head", exit_code=head.returncode)
    if verified_head != resolved_commit:
        return _failure_after_cleanup(target, "head_verification_mismatch", "verify_head")

    result = {
        "state": "succeeded",
        "repository": repository,
        "requested_revision": revision,
        "resolved_commit_sha": resolved_commit,
        "verified_head": verified_head,
        "workspace_relative_path": _REPOSITORY_DIRNAME,
        "execution_evidence": {
            "git_version": version.stdout.strip(),
            "operations": [
                {"operation": "clone", "exit_code": clone.returncode},
                {"operation": "resolve_revision", "exit_code": resolved.returncode},
                {"operation": "checkout", "exit_code": checkout.returncode},
                {"operation": "read_head", "exit_code": head.returncode},
            ],
            "head_matches_resolved_commit": True,
        },
    }
    if source_evidence is not None:
        result["source_evidence"] = source_evidence
    return result


def run_repository_revision_acquisition(payload: dict[str, Any]) -> dict[str, Any]:
    """Acquire one repository at one requested revision in a runtime-authorized workspace."""
    if not isinstance(payload, dict):
        return _failure("skill_input_invalid", "input_validation")
    validated = _validated_input(payload)
    if validated is None:
        return _failure("skill_input_invalid", "input_validation")
    workspace = _runtime_workspace()
    if workspace is None:
        return _failure("workspace_boundary_invalid", "workspace")
    repository, revision, source_evidence = validated
    return _acquire_repository_in_workspace(
        repository,
        revision,
        workspace=workspace,
        source_evidence=source_evidence,
    )
