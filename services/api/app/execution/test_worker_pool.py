"""Unit tests for app.execution.worker_pool.

Pure-logic tests (no Docker) covering acquire/park/evict, per-user cap,
turn hand-off, and liveness (claim-grace). Uses ``asyncio.run`` so no
pytest-asyncio plugin is required.
"""

from __future__ import annotations

import asyncio
import time

from app.execution.worker_pool import BUSY, IDLE, WorkerPool


# ---------------------------------------------------------------------------
# sync helpers
# ---------------------------------------------------------------------------

def test_acquire_returns_none_when_empty():
    pool = WorkerPool()
    assert pool.acquire("t1", "ownerA") is None


def test_register_then_acquire_reuses_and_marks_busy():
    pool = WorkerPool()
    ww = pool.register("t1", "ownerA", "cid-1")
    assert ww.state == BUSY  # freshly launched = first turn in flight

    pool.release(ww)  # turn done → idle
    assert ww.is_idle

    reused = pool.acquire("t1", "ownerA")
    assert reused is not None
    assert reused is ww
    assert reused.state == BUSY


def test_acquire_does_not_reuse_busy_worker():
    pool = WorkerPool()
    ww = pool.register("t1", "ownerA", "cid-1")
    # worker is busy (turn in flight) → cannot be reused concurrently
    assert pool.acquire("t1", "ownerA") is None
    pool.release(ww)
    assert pool.acquire("t1", "ownerA") is ww


def test_acquire_rejects_owner_mismatch():
    """Defensive: even if task_id collides, owner must match."""
    pool = WorkerPool()
    ww = pool.register("t1", "ownerA", "cid-1")
    pool.release(ww)
    assert pool.acquire("t1", "ownerB") is None
    # original idle worker is untouched
    assert ww.is_idle


def test_release_resets_idle_clock_and_turn_state():
    pool = WorkerPool()
    ww = pool.register("t1", "ownerA", "cid-1")
    old = ww.last_used_at
    # Sleep enough to advance time.monotonic() reliably — Windows' default timer
    # granularity (~15ms) means a 1ms sleep may not advance the clock at all.
    time.sleep(0.05)
    pool.release(ww)
    assert ww.is_idle
    assert ww.last_used_at > old
    pool.evict(ww)
    assert pool.get("t1") is None


# ---------------------------------------------------------------------------
# reaping decisions
# ---------------------------------------------------------------------------

def test_reap_expired_idle_only():
    pool = WorkerPool()
    ww = pool.register("t1", "ownerA", "cid-1")
    pool.release(ww)
    # not yet expired
    assert pool.reap_candidates(keepalive_seconds=180, max_per_user=2) == []
    # backdate last_used to simulate idle past the window
    ww.last_used_at = time.monotonic() - 200
    cands = pool.reap_candidates(keepalive_seconds=180, max_per_user=2)
    assert cands == [ww]


def test_reap_busy_worker_is_exempt():
    pool = WorkerPool()
    ww = pool.register("t1", "ownerA", "cid-1")  # busy
    ww.last_used_at = time.monotonic() - 9999
    assert pool.reap_candidates(keepalive_seconds=1, max_per_user=2) == []


def test_reap_enforces_per_user_cap_oldest_first():
    pool = WorkerPool()
    # ownerA holds 3 idle warm workers (cap=2) → oldest evicted
    w1 = pool.register("t1", "ownerA", "c1")
    w2 = pool.register("t2", "ownerA", "c2")
    w3 = pool.register("t3", "ownerA", "c3")
    for ww in (w1, w2, w3):
        pool.release(ww)
    # Use a fixed "now" and set idle times as offsets so the time-expiry branch
    # does NOT fire (keepalive window huge) — only the per-user cap applies.
    now = 100_000.0
    w1.last_used_at = now - 300  # oldest
    w2.last_used_at = now - 200
    w3.last_used_at = now - 100  # newest
    cands = pool.reap_candidates(now=now, keepalive_seconds=9999, max_per_user=2)
    assert w1 in cands
    assert w2 not in cands
    assert w3 not in cands


def test_reap_cap_is_per_owner_independent():
    pool = WorkerPool()
    # each owner within cap → nobody evicted
    a1 = pool.register("t1", "ownerA", "c1")
    a2 = pool.register("t2", "ownerA", "c2")
    b1 = pool.register("t3", "ownerB", "c3")
    b2 = pool.register("t4", "ownerB", "c4")
    for ww in (a1, a2, b1, b2):
        pool.release(ww)
    assert pool.reap_candidates(keepalive_seconds=9999, max_per_user=2) == []


# ---------------------------------------------------------------------------
# async: turn hand-off + liveness
# ---------------------------------------------------------------------------

def test_offer_then_wait_for_turn_delivers_payload():
    pool = WorkerPool()
    ww = pool.register("t1", "ownerA", "cid-1")
    pool.release(ww)
    pool.acquire("t1", "ownerA")  # mark busy again

    async def scenario() -> None:
        pool.offer_turn(ww, {"execution_id": "ex1", "message": "hi"})
        # worker claims
        turn = await pool.wait_for_turn("t1", timeout=1.0)
        assert turn == {"execution_id": "ex1", "message": "hi"}
        # claimed flag set → liveness confirmed
        assert await pool.wait_for_claim(ww, timeout=1.0) is True

    asyncio.run(scenario())


def test_wait_for_turn_returns_none_on_timeout():
    pool = WorkerPool()
    pool.register("t1", "ownerA", "cid-1")

    async def scenario() -> None:
        turn = await pool.wait_for_turn("t1", timeout=0.05)
        assert turn is None

    asyncio.run(scenario())


def test_wait_for_claim_reports_dead_worker():
    """If the worker never claims within grace, the API learns it is dead."""
    pool = WorkerPool()
    ww = pool.register("t1", "ownerA", "cid-1")

    async def scenario() -> None:
        pool.offer_turn(ww, {"execution_id": "ex1"})
        # worker never calls wait_for_turn → never claims
        claimed = await pool.wait_for_claim(ww, timeout=0.05)
        assert claimed is False

    asyncio.run(scenario())


def test_wait_for_turn_untracked_task_returns_none():
    pool = WorkerPool()

    async def scenario() -> None:
        assert await pool.wait_for_turn("nope", timeout=0.05) is None

    asyncio.run(scenario())


def test_snapshot_reflects_state():
    pool = WorkerPool()
    ww = pool.register("t1", "ownerA", "cid-1")
    pool.release(ww)
    snap = pool.snapshot()
    assert len(snap) == 1
    assert snap[0]["task_id"] == "t1"
    assert snap[0]["state"] == IDLE
    pool.evict(ww)
    assert pool.snapshot() == []
