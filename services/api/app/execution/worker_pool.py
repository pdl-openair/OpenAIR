"""In-memory pool of warm (keepalive) worker containers, keyed by ``task_id``.

Design (multi-tenant safe default: per-task keepalive + tight reap + per-user cap):

* A warm worker is **bound to a single task** (and therefore a single owner). It
  never crosses tasks/owners, so there is zero cross-tenant leakage — a Bash
  escape can only ever reach the same tenant's own ``/workspace/{task_id}``.
* ``acquire`` returns an idle warm worker for the same ``task_id`` (and matching
  owner) so a back-to-back turn skips container creation + worker bootstrap.
* ``release`` parks a worker as idle instead of destroying it; the
  :mod:`app.execution.reaper` evicts idle workers past the keepalive window and
  enforces the per-user idle cap.
* All Docker operations live in :mod:`app.execution.container_manager`; this
  module is pure bookkeeping (returns container ids to act on) so it is unit
  testable without Docker.

Concurrency: all access happens on the API's single asyncio event loop, so no
thread locking is required. ``asyncio.Event`` coordinates turn hand-off.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# Worker states
IDLE = "idle"
BUSY = "busy"


@dataclass
class WarmWorker:
    """Bookkeeping for one warm (keepalive) worker container."""

    task_id: str
    owner_id: str
    container_id: str
    # Skill generation snapshotted at launch. Bumped via invalidate_owner_skills when the
    # owner's user skills change (upload/delete); a mismatch makes a warm worker ineligible
    # for reuse so its cached skills are never served stale (see acquire).
    skill_gen: int = 0
    state: str = BUSY
    last_used_at: float = field(default_factory=time.monotonic)
    # Turn hand-off between API (offer) and worker (claim via /worker/claim).
    _offered: dict[str, Any] | None = field(default=None, repr=False)
    _turn_ready: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    _claimed: asyncio.Event = field(default_factory=asyncio.Event, repr=False)

    @property
    def is_idle(self) -> bool:
        return self.state == IDLE


class WorkerPool:
    """Singleton registry of warm workers, keyed by ``task_id`` (one per task).

    A task's turns are sequential (the user awaits each reply before sending the
    next), so one warm worker per task is sufficient — no per-task queueing.
    """

    def __init__(self) -> None:
        self._warm: dict[str, WarmWorker] = {}
        # owner_id → skill generation. Bumped by invalidate_owner_skills on a skill
        # upload/delete; acquire refuses to reuse a warm worker whose snapshot gen differs,
        # forcing a fresh launch (fresh manifest + re-download) so cached skills go stale.
        self._owner_skill_gen: dict[str, int] = {}

    # ------------------------------------------------------------------
    # Lifecycle (called by ContainerRuntime)
    # ------------------------------------------------------------------

    def get(self, task_id: str) -> WarmWorker | None:
        return self._warm.get(task_id)

    def acquire(self, task_id: str, owner_id: str) -> WarmWorker | None:
        """Try to reuse an idle warm worker for this task.

        Returns the worker (marked busy) if an idle one exists AND its owner
        matches (defensive: task→owner should be immutable). Returns ``None``
        when no reusable worker exists — the caller must launch fresh.
        """
        ww = self._warm.get(task_id)
        if ww is not None and ww.is_idle and ww.owner_id == owner_id:
            cur_gen = self._owner_skill_gen.get(owner_id, 0)
            if ww.skill_gen != cur_gen:
                # Owner's user skills changed since this worker launched — its cached skills
                # are stale. Refuse reuse; the caller's acquire→launch path evicts this idle
                # worker and launches a fresh one (re-signs the manifest, re-downloads).
                logger.info(
                    "pool: warm worker task=%s stale (skill gen %s != current %s); not reusing",
                    task_id, ww.skill_gen, cur_gen,
                )
                return None
            ww.state = BUSY
            logger.info(
                "pool: reuse warm worker task=%s container=%s", task_id, ww.container_id[:12]
            )
            return ww
        return None

    def register(self, task_id: str, owner_id: str, container_id: str) -> WarmWorker:
        """Register a freshly launched worker as busy (first turn in flight)."""
        ww = WarmWorker(
            task_id=task_id,
            owner_id=owner_id,
            container_id=container_id,
            skill_gen=self._owner_skill_gen.get(owner_id, 0),
        )
        self._warm[task_id] = ww
        logger.info(
            "pool: registered warm worker task=%s owner=%s container=%s gen=%s",
            task_id, owner_id, container_id[:12], ww.skill_gen,
        )
        return ww

    def release(self, ww: WarmWorker) -> None:
        """Park a worker as idle after its turn completed successfully."""
        ww.state = IDLE
        ww.last_used_at = time.monotonic()
        ww._offered = None
        ww._turn_ready.clear()
        ww._claimed.clear()

    def evict(self, ww: WarmWorker) -> None:
        """Remove a worker from the pool (caller also stops/removes the container)."""
        removed = self._warm.pop(ww.task_id, None)
        if removed is not None:
            logger.info(
                "pool: evicted warm worker task=%s container=%s",
                ww.task_id, ww.container_id[:12],
            )

    def invalidate_owner_skills(self, owner_id: str) -> None:
        """Bump the owner's skill generation after a user-skill upload/delete.

        A keepalive worker caches the owner's user skills at bootstrap, so once those
        skills change any of the owner's warm workers would serve stale scripts/files on
        subsequent turns. Bumping the gen makes ``acquire`` refuse to reuse them; the next
        turn's acquire→launch path then evicts the stale idle worker and launches a fresh
        one (re-signs the manifest, re-downloads). A worker mid-turn is never killed here —
        it finishes, then is refused on its next acquire. No-op when keepalive is off (the
        pool is empty and acquire is never called).
        """
        gen = self._owner_skill_gen.get(owner_id, 0) + 1
        self._owner_skill_gen[owner_id] = gen
        logger.info("pool: invalidated owner=%s skills → gen %d", owner_id, gen)

    # ------------------------------------------------------------------
    # Turn hand-off (API offers, worker claims via /worker/claim)
    # ------------------------------------------------------------------

    def offer_turn(self, ww: WarmWorker, turn: dict[str, Any]) -> None:
        """Deposit a turn for the worker to claim (marks it ready)."""
        logger.info(
            "pool.offer_turn task=%s ww=%s ready_was=%s",
            ww.task_id, id(ww), ww._turn_ready.is_set(),
        )
        ww._claimed.clear()
        ww._offered = turn
        ww._turn_ready.set()

    async def wait_for_turn(
        self, task_id: str, timeout: float
    ) -> dict[str, Any] | None:
        """Worker-side: block until a turn is offered for this task, or timeout.

        Called by the ``POST /internal/worker/claim`` handler. Returns the turn
        dict (and confirms the claim) or ``None`` on timeout (worker re-polls).
        """
        ww = self._warm.get(task_id)
        if ww is None:
            # Not tracked (e.g. evicted between polls) — nothing to do.
            logger.info("pool.wait_for_turn task=%s NO WORKER (untracked)", task_id)
            return None
        logger.info(
            "pool.wait_for_turn task=%s ww=%s ready=%s offered=%s",
            task_id, id(ww), ww._turn_ready.is_set(), ww._offered is not None,
        )
        try:
            await asyncio.wait_for(ww._turn_ready.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning(
                "pool.wait_for_turn TIMEOUT task=%s ww=%s (no offer within %ss)",
                task_id, id(ww), timeout,
            )
            return None
        turn = ww._offered
        ww._offered = None
        ww._turn_ready.clear()
        ww._claimed.set()  # confirm liveness to the awaiting submitter
        logger.info("pool.wait_for_turn CLAIMED task=%s ww=%s", task_id, id(ww))
        return turn

    async def wait_for_claim(self, ww: WarmWorker, timeout: float) -> bool:
        """API-side: wait for the worker to claim an offered turn (liveness check).

        Returns ``True`` if the worker claimed within ``timeout`` (it's alive),
        ``False`` if it didn't (it's dead — caller should evict and relaunch).
        """
        try:
            await asyncio.wait_for(ww._claimed.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    # ------------------------------------------------------------------
    # Reaping decisions (pure; reaper performs the Docker removal)
    # ------------------------------------------------------------------

    def reap_candidates(
        self,
        *,
        now: float | None = None,
        keepalive_seconds: int,
        max_per_user: int,
    ) -> list[WarmWorker]:
        """Return idle warm workers that should be evicted right now.

        Two reasons to evict:
        1. Idle longer than ``keepalive_seconds`` (tight reap, avoids B explosion).
        2. Owner holds more idle warm workers than ``max_per_user`` (fairness cap);
           the oldest idle ones of the over-cap owner are evicted first.
        """
        now = now if now is not None else time.monotonic()
        to_evict: list[WarmWorker] = []
        seen: set[int] = set()

        # 1. Idle + expired.
        for ww in self._warm.values():
            if ww.is_idle and (now - ww.last_used_at) >= keepalive_seconds:
                to_evict.append(ww)
                seen.add(id(ww))

        # 2. Per-owner idle cap (oldest idle first).
        by_owner: dict[str, list[WarmWorker]] = defaultdict(list)
        for ww in self._warm.values():
            if ww.is_idle:
                by_owner[ww.owner_id].append(ww)
        for workers in by_owner.values():
            workers.sort(key=lambda w: w.last_used_at)  # oldest first
            over = len(workers) - max_per_user
            for ww in workers[: max(0, over)]:
                if id(ww) not in seen:
                    to_evict.append(ww)
                    seen.add(id(ww))

        return to_evict

    # ------------------------------------------------------------------
    # Observability
    # ------------------------------------------------------------------

    def snapshot(self) -> list[dict[str, Any]]:
        """Return a debug view of all warm workers (for /worker/containers etc.)."""
        return [
            {
                "task_id": ww.task_id,
                "owner_id": ww.owner_id,
                "container_id": ww.container_id[:12],
                "state": ww.state,
                "idle_for_s": round(time.monotonic() - ww.last_used_at, 1) if ww.is_idle else 0,
            }
            for ww in self._warm.values()
        ]


# Module-level singleton
_pool: WorkerPool | None = None


def get_worker_pool() -> WorkerPool:
    """Return the singleton worker pool."""
    global _pool
    if _pool is None:
        _pool = WorkerPool()
    return _pool
