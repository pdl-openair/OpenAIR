"""Phase-2 #3 — per-task workspace isolation (gid-based).

Covers the unit-testable parts: gid allocation, the prepare decisions (root vs non-root,
correct owner/group/mode, graceful degradation), the container-launch `groups` wiring, and
the worker-env flag that tells the entrypoint to skip its recursive `/workspace` chown.

The actual POSIX enforcement (a worker with supplementary gid X denied entry to a dir owned
by root:gid Y) is a Docker-e2e property, flagged in the PR — it cannot be exercised here.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.execution import workspace_isolation as wi


# ---------------------------------------------------------------------------
# gid resolution (collision-free, restart-safe)
# ---------------------------------------------------------------------------


class TestResolveTaskGid:
    def setup_method(self):
        wi._gid_registry.clear()
        wi._gid_high_water = None
        wi._migration_done = False

    def test_new_task_no_siblings_gets_base(self, tmp_path, monkeypatch):
        monkeypatch.setattr(wi.os, "stat", MagicMock(side_effect=OSError("noent")), raising=False)
        monkeypatch.setattr(wi, "_isolated_gids_on_disk", lambda root: set())
        assert wi._resolve_task_gid("task-1", tmp_path / "task-1") == wi._GID_BASE

    def test_new_task_avoids_sibling_gid_collision(self, tmp_path, monkeypatch):
        """The bug this guards: a freshly-restarted API must not hand a new task a gid
        already baked into another task's dir (which would let the two workers read each
        other). A sibling occupying 200000 on disk → new task gets 200001."""
        monkeypatch.setattr(wi.os, "stat", MagicMock(side_effect=OSError("noent")), raising=False)
        monkeypatch.setattr(wi, "_isolated_gids_on_disk", lambda root: {200000})
        assert wi._resolve_task_gid("task-2", tmp_path / "task-2") == 200001

    def test_reuses_own_dir_gid(self, tmp_path, monkeypatch):
        import os as _os
        from stat import S_IFDIR

        monkeypatch.setattr(
            wi.os, "stat",
            lambda p, *a, **k: _os.stat_result((S_IFDIR | 0o770, 0, 0, 1, 0, 200000, 0, 0, 0, 0)),
            raising=False,
        )
        assert wi._resolve_task_gid("task-1", tmp_path / "task-1") == 200000

    def test_inprocess_cache_is_stable(self, tmp_path, monkeypatch):
        monkeypatch.setattr(wi.os, "stat", MagicMock(side_effect=OSError("noent")), raising=False)
        monkeypatch.setattr(wi, "_isolated_gids_on_disk", lambda root: set())
        a = wi._resolve_task_gid("task-1", tmp_path / "task-1")
        b = wi._resolve_task_gid("task-1", tmp_path / "task-1")
        assert a == b

    def test_volume_scan_runs_once_per_process(self, tmp_path, monkeypatch):
        """Allocation cost is independent of task count: the volume is scanned once to seed
        the high-water mark, then subsequent new tasks are O(1) (no rescan)."""
        scan = MagicMock(return_value={200000})
        monkeypatch.setattr(wi, "_isolated_gids_on_disk", scan)
        monkeypatch.setattr(wi.os, "stat", MagicMock(side_effect=OSError("noent")), raising=False)
        g1 = wi._resolve_task_gid("task-A", tmp_path / "task-A")
        g2 = wi._resolve_task_gid("task-B", tmp_path / "task-B")
        g3 = wi._resolve_task_gid("task-C", tmp_path / "task-C")
        assert scan.call_count == 1  # seeded once, then O(1)
        assert (g1, g2, g3) == (200001, 200002, 200003)  # monotonic, collision-free


class TestIsolatedGidsOnDisk:
    def test_collects_only_isolated_sibling_gids(self, tmp_path, monkeypatch):
        isolated = lambda uid, gid: MagicMock(
            is_dir=lambda: True, stat=lambda: SimpleNamespace(st_uid=uid, st_gid=gid)
        )
        a_file = MagicMock(is_dir=lambda: False, stat=lambda: SimpleNamespace(st_uid=0, st_gid=200099))
        entries = [isolated(0, 200000), isolated(0, 200001), isolated(1000, 101), a_file]
        monkeypatch.setattr(wi.os, "scandir", lambda root: iter(entries))
        assert wi._isolated_gids_on_disk(tmp_path) == {200000, 200001}


# ---------------------------------------------------------------------------
# prepare_task_workspace
# ---------------------------------------------------------------------------


class TestPrepareTaskWorkspace:
    def setup_method(self):
        wi._gid_registry.clear()
        wi._gid_high_water = None
        wi._migration_done = False

    def test_non_root_returns_none_and_does_not_chown(self, tmp_path, monkeypatch):
        monkeypatch.setattr(wi, "_running_as_root", lambda: False)
        chown = MagicMock()
        chmod = MagicMock()
        monkeypatch.setattr(wi.os, "chown", chown, raising=False)
        monkeypatch.setattr(wi.os, "chmod", chmod, raising=False)
        assert wi.prepare_task_workspace("task-1", tmp_path / "task-1") is None
        chown.assert_not_called()
        chmod.assert_not_called()

    def test_root_sets_root_gid_770(self, tmp_path, monkeypatch):
        monkeypatch.setattr(wi, "_running_as_root", lambda: True)
        chown = MagicMock()
        chmod = MagicMock()
        monkeypatch.setattr(wi.os, "chown", chown, raising=False)
        monkeypatch.setattr(wi.os, "chmod", chmod, raising=False)

        task_dir = tmp_path / "task-1"
        gid = wi.prepare_task_workspace("task-1", task_dir)

        assert gid is not None
        assert task_dir.is_dir()  # created
        chown.assert_called_once_with(task_dir, 0, gid)
        chmod.assert_called_once_with(task_dir, 0o770)

    def test_chown_failure_degrades_to_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(wi, "_running_as_root", lambda: True)
        monkeypatch.setattr(wi.os, "chown", MagicMock(side_effect=OSError("denied")), raising=False)
        assert wi.prepare_task_workspace("task-1", tmp_path / "task-1") is None

    def test_prepare_reuses_existing_dir_gid(self, tmp_path, monkeypatch):
        """An isolation-prepped dir's gid is reused (the dir's ownership persists across API
        restarts) — even when a sibling also occupies that gid on disk."""
        import os as _os
        from stat import S_IFDIR

        monkeypatch.setattr(wi, "_running_as_root", lambda: True)
        task_dir = tmp_path / "task-1"
        task_dir.mkdir()
        monkeypatch.setattr(
            wi.os, "stat",
            lambda p, *a, **k: _os.stat_result((S_IFDIR | 0o770, 0, 0, 1, 0, 200000, 0, 0, 0, 0)),
            raising=False,
        )
        monkeypatch.setattr(wi, "_isolated_gids_on_disk", lambda root: {200000})  # sibling also has it
        monkeypatch.setattr(wi.os, "scandir", lambda root: iter([]))  # no legacy siblings to migrate
        chown = MagicMock()
        monkeypatch.setattr(wi.os, "chown", chown, raising=False)
        gid = wi.prepare_task_workspace("task-1", task_dir)
        assert gid == 200000  # reused from own dir, not reallocated
        chown.assert_called_once_with(task_dir, 0, 200000)


# ---------------------------------------------------------------------------
# _serialise_env conveys the per-task gid to the worker (applied as primary gid
# by the entrypoint — Docker group_add is discarded by su-exec)
# ---------------------------------------------------------------------------


class TestSerialiseEnvTaskGid:
    def _request(self):
        from app.execution.runtime_boundary import ExecutionRequest

        return ExecutionRequest(
            task_id="task-1", message="m", mode="execution", task={}, owner_id="owner-1"
        )

    def test_task_gid_set_in_env(self):
        from unittest.mock import MagicMock

        import app.execution.container_runtime as cr_mod
        from app.execution.container_runtime import _serialise_env

        mock = MagicMock(
            claude_permission_mode="bypassPermissions",
            worker_workspace_isolation=True,
            worker_callback_url="http://api:8000",
            worker_auth_token="tok",
            artifact_storage="local",
        )
        with patch.object(cr_mod, "settings", mock):
            env = _serialise_env(self._request(), task_gid=200000)
        assert env.get("OPENAIR_WORKER_TASK_GID") == "200000"

    def test_no_task_gid_omits_env(self):
        from unittest.mock import MagicMock

        import app.execution.container_runtime as cr_mod
        from app.execution.container_runtime import _serialise_env

        mock = MagicMock(
            claude_permission_mode="bypassPermissions",
            worker_workspace_isolation=True,
            worker_callback_url="http://api:8000",
            worker_auth_token="tok",
            artifact_storage="local",
        )
        with patch.object(cr_mod, "settings", mock):
            env = _serialise_env(self._request())
        assert "OPENAIR_WORKER_TASK_GID" not in env
        assert "OPENAIR_WORKSPACE_ISOLATION" not in env  # full legacy, not half-open


# ---------------------------------------------------------------------------
# _serialise_env advertises isolation to the worker entrypoint
# ---------------------------------------------------------------------------


class TestSerialiseEnvIsolationFlag:
    def _request(self):
        from app.execution.runtime_boundary import ExecutionRequest

        return ExecutionRequest(
            task_id="task-1", message="m", mode="execution", task={}, owner_id="owner-1"
        )

    def test_flag_on_with_gid_sets_marker(self):
        """The isolation marker is set only when prep succeeded (a gid was conveyed)."""
        from unittest.mock import MagicMock

        import app.execution.container_runtime as cr_mod
        from app.execution.container_runtime import _serialise_env

        mock = MagicMock(
            claude_permission_mode="bypassPermissions",
            worker_workspace_isolation=True,
            worker_callback_url="http://api:8000",
            worker_auth_token="tok",
            artifact_storage="local",
        )
        with patch.object(cr_mod, "settings", mock):
            env = _serialise_env(self._request(), task_gid=200000)
        assert env.get("OPENAIR_WORKSPACE_ISOLATION") == "1"
        assert env.get("OPENAIR_WORKER_TASK_GID") == "200000"

    def test_flag_on_prep_failure_fully_degrades(self):
        """Regression for the half-open bug: flag ON but prep failed (task_gid=None) must set
        NEITHER the isolation marker NOR the gid — so the entrypoint runs the full legacy
        path (chown /workspace → openair:openair writable). The old behaviour set the marker
        without a gid, which made the entrypoint skip the chown while running as
        openair:openair against an unprepared dir: no isolation AND an unwritable workspace,
        so the task failed outright."""
        from unittest.mock import MagicMock

        import app.execution.container_runtime as cr_mod
        from app.execution.container_runtime import _serialise_env

        mock = MagicMock(
            claude_permission_mode="bypassPermissions",
            worker_workspace_isolation=True,
            worker_callback_url="http://api:8000",
            worker_auth_token="tok",
            artifact_storage="local",
        )
        with patch.object(cr_mod, "settings", mock):
            env = _serialise_env(self._request())  # task_gid omitted ⇒ None (prep missed)
        assert "OPENAIR_WORKSPACE_ISOLATION" not in env
        assert "OPENAIR_WORKER_TASK_GID" not in env

    def test_flag_off_no_marker(self):
        from unittest.mock import MagicMock

        import app.execution.container_runtime as cr_mod
        from app.execution.container_runtime import _serialise_env

        mock = MagicMock(
            claude_permission_mode="bypassPermissions",
            worker_workspace_isolation=False,
            worker_callback_url="http://api:8000",
            worker_auth_token="tok",
            artifact_storage="local",
        )
        with patch.object(cr_mod, "settings", mock):
            env = _serialise_env(self._request())
        assert "OPENAIR_WORKSPACE_ISOLATION" not in env


# ---------------------------------------------------------------------------
# legacy-dir migration (the reviewer's requirement: openair:openair siblings
# must be locked to root:<gid> 0770 so a worker can't read them via uid)
# ---------------------------------------------------------------------------


class TestMigration:
    def setup_method(self):
        wi._gid_registry.clear()
        wi._gid_high_water = None
        wi._migration_done = False

    def test_migrates_legacy_openair_sibling_only(self, tmp_path, monkeypatch):
        monkeypatch.setattr(wi, "_running_as_root", lambda: True)
        already_iso = MagicMock(
            is_dir=lambda: True,
            stat=lambda: SimpleNamespace(st_uid=0, st_gid=200000),
            path=str(tmp_path / "iso"),
        )
        legacy = MagicMock(
            is_dir=lambda: True,
            stat=lambda: SimpleNamespace(st_uid=1000, st_gid=101),  # openair:openair
            path=str(tmp_path / "legacy"),
        )
        a_file = MagicMock(is_dir=lambda: False)
        monkeypatch.setattr(wi.os, "scandir", lambda root: iter([already_iso, legacy, a_file]))
        monkeypatch.setattr(wi, "_isolated_gids_on_disk", lambda root: {200000})
        chown = MagicMock()
        chmod = MagicMock()
        monkeypatch.setattr(wi.os, "chown", chown, raising=False)
        monkeypatch.setattr(wi.os, "chmod", chmod, raising=False)

        migrated = wi.migrate_workspace_isolation(tmp_path)

        assert migrated == 1  # only the legacy dir; already-isolated + file untouched
        chown.assert_called_once_with(str(tmp_path / "legacy"), 0, 200001)  # fresh gid above 200000
        chmod.assert_called_once_with(str(tmp_path / "legacy"), 0o770)

    def test_runs_at_most_once_per_process(self, tmp_path, monkeypatch):
        monkeypatch.setattr(wi, "_running_as_root", lambda: True)
        scandir = MagicMock(return_value=iter([]))
        monkeypatch.setattr(wi.os, "scandir", scandir)
        wi.migrate_workspace_isolation(tmp_path)
        wi.migrate_workspace_isolation(tmp_path)
        assert scandir.call_count == 1  # guarded after the first pass

    def test_prepare_migrates_before_own_dir(self, tmp_path, monkeypatch):
        """prepare_task_workspace migrates siblings first, so by the time any worker runs no
        openair-owned sibling remains (the leak vector the reviewer flagged)."""
        monkeypatch.setattr(wi, "_running_as_root", lambda: True)
        legacy = MagicMock(
            is_dir=lambda: True,
            stat=lambda: SimpleNamespace(st_uid=1000, st_gid=101),
            path=str(tmp_path / "legacy"),
        )
        monkeypatch.setattr(wi.os, "scandir", lambda root: iter([legacy]))
        monkeypatch.setattr(wi, "_isolated_gids_on_disk", lambda root: set())
        monkeypatch.setattr(wi.os, "chown", MagicMock(), raising=False)
        monkeypatch.setattr(wi.os, "chmod", MagicMock(), raising=False)
        wi.prepare_task_workspace("task-1", tmp_path / "task-1")
        # legacy sibling was migrated (chown'd to root:<gid> 0770) during prepare
        assert wi._migration_done is True


# ---------------------------------------------------------------------------
# fail-closed: flag on but can't isolate (not root) ⇒ refuse to launch
# ---------------------------------------------------------------------------


class TestPrepareFailClosed:
    def test_raises_when_flag_on_but_not_root(self, monkeypatch):
        import app.execution.container_runtime as cr_mod

        monkeypatch.setattr(cr_mod.settings, "worker_workspace_isolation", True)
        # prepare_task_workspace returns None (e.g. API not root) → must fail closed
        monkeypatch.setattr(
            "app.execution.workspace_isolation.prepare_task_workspace",
            lambda *a, **k: None,
        )
        from app.execution.container_runtime import _prepare_workspace_isolation

        with pytest.raises(RuntimeError, match="fail-closed"):
            _prepare_workspace_isolation("task-1", Path("/tmp/task-1"))

    def test_returns_none_when_flag_off(self, monkeypatch):
        import app.execution.container_runtime as cr_mod

        monkeypatch.setattr(cr_mod.settings, "worker_workspace_isolation", False)
        from app.execution.container_runtime import _prepare_workspace_isolation

        assert _prepare_workspace_isolation("task-1", Path("/tmp/task-1")) is None
