"""Runtime version metadata for diagnostics and acceptance evidence."""

from __future__ import annotations

import os
import subprocess
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

from app.modules.repo_paths import repo_root

_STARTED_AT = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _env_first(*names: str) -> str | None:
    for name in names:
        value = (os.environ.get(name) or "").strip()
        if value:
            return value
    return None


@lru_cache(maxsize=1)
def _git_commit() -> str | None:
    env_commit = _env_first(
        "OPENAIR_GIT_COMMIT",
        "GIT_COMMIT",
        "VERCEL_GIT_COMMIT_SHA",
        "COMMIT_SHA",
    )
    if env_commit:
        return env_commit
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root(),
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
        return result.stdout.strip() or None
    except Exception:
        return None


@lru_cache(maxsize=1)
def _git_branch() -> str | None:
    env_branch = _env_first("OPENAIR_GIT_BRANCH", "GIT_BRANCH", "VERCEL_GIT_COMMIT_REF")
    if env_branch:
        return env_branch
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=repo_root(),
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
        return result.stdout.strip() or None
    except Exception:
        return None


def runtime_version() -> dict[str, Any]:
    commit = _git_commit()
    return {
        "startedAt": _STARTED_AT,
        "buildTime": _env_first("OPENAIR_BUILD_TIME", "BUILD_TIME"),
        "gitCommit": commit,
        "gitCommitShort": commit[:12] if commit else None,
        "gitBranch": _git_branch(),
    }

