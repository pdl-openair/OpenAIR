"""Phase-2 #3 — per-task workspace isolation via primary POSIX gid.

Threat: the shared workspace named volume is mounted whole at ``/workspace`` in every
worker, so a worker process can read sibling task dirs (cross-task / cross-owner file
leakage). This module enforces per-task isolation by giving each task directory a UNIQUE
group and mode ``0770 root:<gid>``, and conveying that gid to the worker (via env → the
entrypoint sets it as the worker's PRIMARY gid). The worker runs as the unprivileged
``openair`` user (not the dir owner), so access is decided by the gid — it matches ONLY its
own task.

Gid allocation is **collision-free and restart-safe**: the workspace volume is the source of
truth. A new task scans its siblings for gids already in use and takes max+1 (so it never
collides with a gid baked into another task dir — even after an API restart, where any
in-memory counter would reset). An existing isolation-prepped task reuses its own dir's gid.

Requirements (validated at deploy/e2e time, not here):
- The API process must run as **root** (to ``chown root:<gid>``). ``prepare_task_workspace``
  degrades gracefully (returns ``None``) when not root, so non-root deployments keep working
  without isolation rather than failing.
- The worker entrypoint must NOT recursively re-own ``/workspace``; it skips its legacy
  ``chown -R`` under ``OPENAIR_WORKSPACE_ISOLATION=1`` and drops to ``openair:<gid>``.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# Start well above typical image/system gids (openair is ~1000 / group 101).
_GID_BASE = 200000

# task_id → gid cache for the current process. The workspace volume (on disk) is the
# authoritative source; this only avoids re-scanning / re-allocating within a session.
_gid_registry: dict[str, int] = {}
# High-water mark of gids in use, seeded ONCE per process from the volume scan, then
# advanced in-memory — so new-task allocation is O(1) after the first task per restart,
# regardless of how many task dirs have accumulated. None ⇒ not yet seeded.
_gid_high_water: int | None = None
# Whether the one-shot legacy-dir migration sweep has run this process.
_migration_done: bool = False


def _isolated_gids_on_disk(workspace_root: Path) -> set[int]:
    """Gids already baked into isolation-prepped sibling task dirs on the volume."""
    used: set[int] = set()
    try:
        for entry in os.scandir(workspace_root):
            if not entry.is_dir():
                continue
            try:
                st = entry.stat()
            except OSError:
                continue
            if st.st_uid == 0 and st.st_gid >= _GID_BASE:
                used.add(st.st_gid)
    except OSError:
        pass
    return used


def release_task_gid(task_id: str) -> None:
    """Drop the task's in-process gid cache entry (best-effort hygiene)."""
    _gid_registry.pop(task_id, None)


def _running_as_root() -> bool:
    try:
        return os.geteuid() == 0
    except AttributeError:
        return False


def _next_fresh_gid(workspace_root: Path) -> int:
    """Allocate a gid above every one in use on disk / in-process. The high-water mark is
    seeded ONCE per process from a volume scan, then advanced in-memory — O(1) after the
    first allocation per restart, and collision-free."""
    global _gid_high_water
    if _gid_high_water is None:
        used = _isolated_gids_on_disk(workspace_root) | set(_gid_registry.values())
        _gid_high_water = (max(used) if used else _GID_BASE - 1)
    _gid_high_water += 1
    return _gid_high_water


def migrate_workspace_isolation(workspace_root: Path) -> int:
    """Lock every un-isolated task dir under ``workspace_root`` to ``root:<unique gid> 0770``.

    Legacy ``openair:openair`` dirs would otherwise be readable by ANY worker via uid (POSIX
    checks uid before gid), bypassing per-task isolation entirely. Runs at most once per
    process (a flag guards the O(N) sweep — under isolation no new openair-owned dirs
    appear, so one pass suffices). Already-isolated dirs are untouched, so there's no race
    with workers currently running. Returns the count of dirs migrated.
    """
    global _migration_done
    if _migration_done:
        return 0
    _migration_done = True
    if not _running_as_root():
        return 0
    migrated = 0
    try:
        entries = list(os.scandir(workspace_root))
    except OSError:
        return 0
    for entry in entries:
        if not entry.is_dir():
            continue
        try:
            st = entry.stat()
        except OSError:
            continue
        if st.st_uid == 0 and st.st_gid >= _GID_BASE:
            continue  # already isolated — leave it (a running worker may depend on its gid)
        gid = _next_fresh_gid(workspace_root)
        try:
            os.chown(entry.path, 0, gid)
            os.chmod(entry.path, 0o770)
            migrated += 1
            logger.info("workspace isolation: migrated legacy dir %s → root:%s 0770", entry.path, gid)
        except OSError as exc:
            logger.warning("workspace isolation: failed to migrate %s: %s", entry.path, exc)
    return migrated


def _resolve_task_gid(task_id: str, task_dir: Path) -> int:
    """Determine a UNIQUE gid for a task dir, restart-safe and collision-free.

    Reuses the dir's own gid if already isolation-prepped (ownership persists across API
    restarts); otherwise allocates a fresh gid above all in-use via ``_next_fresh_gid``.
    """
    cached = _gid_registry.get(task_id)
    if cached is not None:
        return cached
    try:
        st = os.stat(task_dir)
        if st.st_uid == 0 and st.st_gid >= _GID_BASE:
            _gid_registry[task_id] = st.st_gid
            return st.st_gid
    except OSError:
        pass
    gid = _next_fresh_gid(task_dir.parent)
    _gid_registry[task_id] = gid
    return gid


def prepare_task_workspace(task_id: str, task_dir: Path) -> int | None:
    """Set up per-task group isolation on the task's workspace directory.

    Creates the directory (root:gid, mode 0770) and returns the gid to attach to the
    worker as a supplementary group. Returns ``None`` (no isolation, no failure) when the
    API isn't running as root — callers proceed without isolation.
    """
    if not _running_as_root():
        logger.warning(
            "workspace isolation: API not running as root; skipping for task=%s "
            "(isolation requires root to chown task dirs)",
            task_id,
        )
        return None

    # First, lock down any legacy openair:openair sibling dirs — a worker running as
    # openair could read them via uid (checked before gid), which would bypass per-task
    # isolation. No-op after the first call per process.
    migrate_workspace_isolation(task_dir.parent)

    gid = _resolve_task_gid(task_id, task_dir)
    try:
        task_dir.mkdir(parents=True, exist_ok=True)
        os.chown(task_dir, 0, gid)
        os.chmod(task_dir, 0o770)
    except OSError as exc:
        logger.warning(
            "workspace isolation: failed to prepare %s for task=%s: %s "
            "(continuing without isolation)",
            task_dir, task_id, exc,
        )
        return None
    logger.info("workspace isolation: task=%s dir=%s gid=%s", task_id, task_dir, gid)
    return gid
