"""Phase-1 follow-up: keepalive workers must not serve stale user skills.

A server-mode worker downloads the owner's user skills ONCE at bootstrap and reuses them
for every keepalive turn. So after an upload/delete (or re-upload — the MinIO side already
wipes stale files), an already-running warm worker would keep executing the old scripts
until it expired. The fix is a per-owner skill generation: the skills route bumps it on
change, and ``WorkerPool.acquire`` refuses to reuse a worker whose snapshot gen is stale —
forcing a fresh launch (re-signed manifest + re-download) on the next turn. A worker
mid-turn is never killed; it is refused on its next acquire once idle.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.config import settings  # noqa: F401  (ensures app imports)
from app.execution.worker_pool import WorkerPool, get_worker_pool
from app.main import app

OWNER = "ownerA"
OTHER = "ownerB"


def _minio_obj(key: str) -> MagicMock:
    m = MagicMock()
    m.object_name = key
    return m


# ---------------------------------------------------------------------------
# Pool-level skill-generation behaviour
# ---------------------------------------------------------------------------


def _idle_worker(pool: WorkerPool, task_id: str = "t1", owner_id: str = OWNER, cid: str = "c1"):
    """Register a worker and park it idle (ready for acquire)."""
    ww = pool.register(task_id, owner_id, cid)
    pool.release(ww)
    return ww


class TestSkillGeneration:
    def test_reuses_same_generation(self):
        pool = WorkerPool()
        _idle_worker(pool)
        assert pool.acquire("t1", OWNER) is not None

    def test_invalidate_blocks_stale_reuse(self):
        """After the owner's skills change, the stale idle worker is not reused (forces a
        fresh launch with re-downloaded skills)."""
        pool = WorkerPool()
        _idle_worker(pool)
        pool.invalidate_owner_skills(OWNER)
        assert pool.acquire("t1", OWNER) is None

    def test_post_invalidation_register_uses_new_gen(self):
        """A worker launched AFTER the invalidation picks up the new gen and is reusable."""
        pool = WorkerPool()
        _idle_worker(pool)
        pool.invalidate_owner_skills(OWNER)
        assert pool.acquire("t1", OWNER) is None  # old worker stale
        ww = pool.register("t2", OWNER, "c2")
        assert ww.skill_gen == 1
        pool.release(ww)
        assert pool.acquire("t2", OWNER) is not None  # new worker reusable

    def test_invalidate_isolated_per_owner(self):
        pool = WorkerPool()
        _idle_worker(pool, task_id="tA", owner_id=OWNER)
        _idle_worker(pool, task_id="tB", owner_id=OTHER)
        pool.invalidate_owner_skills(OWNER)
        assert pool.acquire("tA", OWNER) is None  # owner A stale
        assert pool.acquire("tB", OTHER) is not None  # owner B unaffected

    def test_busy_worker_not_killed_then_refused_when_idle(self):
        """A worker mid-turn is never killed; once it goes idle it is refused (stale)."""
        pool = WorkerPool()
        busy = pool.register("t1", OWNER, "c1")  # BUSY (in-flight turn)
        assert not busy.is_idle
        pool.invalidate_owner_skills(OWNER)
        assert pool.acquire("t1", OWNER) is None  # not idle → not reused (and not killed)
        # the turn finishes; the now-idle worker is still stale → still refused
        pool.release(busy)
        assert pool.acquire("t1", OWNER) is None

    def test_failed_upload_does_not_invalidate(self):
        """Invalidation must only fire on a successful change — but the pool method itself
        is a pure bump; guard is exercised at the route level (see TestRouteInvalidatesPool)."""
        pool = WorkerPool()
        pool.invalidate_owner_skills(OWNER)
        pool.invalidate_owner_skills(OWNER)
        assert pool._owner_skill_gen[OWNER] == 2  # monotonic


# ---------------------------------------------------------------------------
# Route-level: upload/delete bump the owner's skill generation
# ---------------------------------------------------------------------------


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


class TestRouteInvalidatesPool:
    def test_upload_skill_invalidates_owner_pool(self, client, mock_minio_client, monkeypatch):
        monkeypatch.setattr(settings, "artifact_storage", "minio")
        mock_minio_client.list_objects.return_value = []  # nothing pre-existing to clean
        mock_minio_client.put_object.return_value = None
        pool = get_worker_pool()
        with patch.object(pool, "invalidate_owner_skills") as spy:
            r = client.post(
                "/api/skills/user",
                data={"skill_name": "my-skill"},
                files=[("files", ("SKILL.md", b"# x", "text/markdown"))],
            )
        assert r.status_code == 200, r.text
        spy.assert_called_once_with("user_demo_owner")

    def test_delete_skill_invalidates_owner_pool(self, client, mock_minio_client, monkeypatch):
        monkeypatch.setattr(settings, "artifact_storage", "minio")
        mock_minio_client.list_objects.return_value = [
            _minio_obj("user-skills/user_demo_owner/my-skill/SKILL.md")
        ]
        mock_minio_client.remove_object.return_value = None
        pool = get_worker_pool()
        with patch.object(pool, "invalidate_owner_skills") as spy:
            r = client.delete("/api/skills/user/my-skill")
        assert r.status_code == 200, r.text
        spy.assert_called_once_with("user_demo_owner")

    def test_failed_upload_does_not_invalidate(self, client, mock_minio_client, monkeypatch):
        """A rejected upload (e.g. missing SKILL.md) must not bump the generation."""
        monkeypatch.setattr(settings, "artifact_storage", "minio")
        mock_minio_client.put_object.return_value = None
        pool = get_worker_pool()
        with patch.object(pool, "invalidate_owner_skills") as spy:
            r = client.post(
                "/api/skills/user",
                data={"skill_name": "my-skill"},
                files=[("files", ("readme.md", b"# x", "text/markdown"))],  # no SKILL.md
            )
        assert r.status_code == 400
        spy.assert_not_called()
