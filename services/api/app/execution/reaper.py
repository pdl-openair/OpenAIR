"""Background reaper for warm worker containers.

Two responsibilities:

1. **Periodic sweep** — evict idle warm workers past the keepalive window and
   enforce the per-user idle cap (fairness). Delegates the *decision* to
   :meth:`WorkerPool.reap_candidates` (pure, unit-tested) and performs the
   Docker removal here.

2. **Startup reconciliation** — on boot the in-memory pool is empty, so every
   ``openair.managed`` container left over from a previous (crashed) API run is
   an orphan. We force-remove all of them so a restart never leaks containers.

The sweep is bounded and defensive: a failure removing one container does not
abort the loop.
"""

from __future__ import annotations

import asyncio
import logging

from app.config import settings
from app.execution.container_manager import get_container_manager
from app.execution.worker_pool import get_worker_pool

logger = logging.getLogger(__name__)


class WorkerReaper:
    """Periodic sweeper + startup reconciler for warm worker containers."""

    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the background sweep loop (idempotent)."""
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._loop(), name="openair-worker-reaper")
        logger.info(
            "worker reaper started (interval=%ss keepalive=%ss max_per_user=%s)",
            settings.worker_reaper_interval_seconds,
            settings.worker_keepalive_seconds,
            settings.worker_max_per_user,
        )

    async def stop(self) -> None:
        """Cancel the background loop (graceful shutdown)."""
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except (asyncio.CancelledError, Exception):
            pass
        self._task = None
        logger.info("worker reaper stopped")

    # ------------------------------------------------------------------
    # Sweep
    # ------------------------------------------------------------------

    async def _loop(self) -> None:
        """Run sweep() on a fixed interval until cancelled."""
        interval = max(1, settings.worker_reaper_interval_seconds)
        while True:
            try:
                await asyncio.sleep(interval)
                self.sweep()
            except asyncio.CancelledError:
                raise
            except Exception:
                # Never let the loop die on a transient failure.
                logger.exception("worker reaper sweep failed (will retry)")

    def sweep(self) -> int:
        """Evict expired/over-cap idle warm workers. Returns count removed."""
        pool = get_worker_pool()
        manager = get_container_manager()
        candidates = pool.reap_candidates(
            keepalive_seconds=settings.worker_keepalive_seconds,
            max_per_user=settings.worker_max_per_user,
        )
        if not candidates:
            return 0
        removed = 0
        for ww in candidates:
            try:
                manager.stop_and_remove_container(ww.container_id)
                pool.evict(ww)
                removed += 1
                logger.info(
                    "reaper: evicted idle worker task=%s owner=%s container=%s",
                    ww.task_id, ww.owner_id, ww.container_id[:12],
                )
            except Exception:
                logger.exception(
                    "reaper: failed to remove container %s (task=%s)",
                    ww.container_id[:12], ww.task_id,
                )
        return removed

    # ------------------------------------------------------------------
    # Startup reconciliation
    # ------------------------------------------------------------------

    def reconcile_on_startup(self) -> int:
        """Force-remove every orphan ``openair.managed`` container at boot.

        At startup the in-memory pool is empty, so any managed container found
        is a leftover from a previous API process (e.g. it crashed mid-turn).
        Removing them prevents zombie containers from accumulating across
        restarts. Safe because a freshly started API has no in-flight workers.
        """
        if not settings.worker_reconcile_on_startup:
            return 0
        manager = get_container_manager()
        try:
            removed = manager.remove_all_managed()
        except Exception:
            logger.exception("startup reconcile: failed")
            return 0
        if removed:
            logger.info("startup reconcile: removed %d orphan worker container(s)", removed)
        return removed


# Module-level singleton
_reaper: WorkerReaper | None = None


def get_reaper() -> WorkerReaper:
    """Return the singleton reaper."""
    global _reaper
    if _reaper is None:
        _reaper = WorkerReaper()
    return _reaper
