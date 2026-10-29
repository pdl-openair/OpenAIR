"""Task-level execution context: workspace directories, cwd, and runtime environment.

This module centralises all path assumptions that previously hard-coded
``repo_root() / "workspace" / {task_id}``.  In a cloud deployment the workspace
root should be configurable (``OPENAIR_WORKSPACE_ROOT``) so that the API
process does not need to share the repository filesystem.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.config import settings
from app.modules.repo_paths import repo_root

logger = logging.getLogger(__name__)


def get_workspace_root() -> Path:
    """Return the configured workspace root.

    Local dev default: ``repo_root() / "workspace"``
    Cloud override: ``OPENAIR_WORKSPACE_ROOT`` environment variable.
    """
    if settings.workspace_root:
        return Path(settings.workspace_root)
    return repo_root() / "workspace"


def get_task_workspace_dir(task_id: str) -> Path:
    """Return the task-specific workspace directory."""
    return get_workspace_root() / task_id


def get_execution_cwd() -> Path:
    """Return the working directory for the execution runtime.

    Local dev: repository root (so Claude Code can see skills/, schemas/, etc.)
    Cloud worker: workspace root or an isolated context.
    """
    if settings.workspace_root:
        return Path(settings.workspace_root)
    return repo_root()


def prepare_task_workspace(task_id: str, task: dict[str, Any] | None = None) -> Path:
    """Ensure the task workspace directory exists and return its path.

    This is a Phase-1 minimal implementation.  Future cloud worker isolation
    may create per-task temporary directories or mount points here.
    """
    ws = get_task_workspace_dir(task_id)
    ws.mkdir(parents=True, exist_ok=True)
    return ws


def warn_if_local_cli_in_production() -> None:
    """Emit a warning when local CLI execution is used in a production-like env."""
    env = (getattr(settings, "env", "development") or "development").lower()
    if env in ("production", "prod") and not settings.allow_local_cli_in_prod:
        logger.warning(
            "Local Claude Code CLI execution is being used in a production environment. "
            "This requires the API host to have an interactive Claude CLI installed. "
            "For cloud deployments set OPENAIR_RUNTIME_TYPE=worker (Phase-1 stub)."
        )
