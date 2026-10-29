"""Layer 2: Integration tests for Cloud-08 API endpoints.

Uses FastAPI TestClient with mocked Docker and MinIO dependencies.
No real containers, no real object storage — but real HTTP routing,
auth checks, and response serialization.
"""

# pyright: reportUnusedImport=false

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# TestClient fixture — creates app with test config
# ---------------------------------------------------------------------------


@pytest.fixture()
def api_client():
    """Create a TestClient with test-appropriate settings."""
    with patch.dict(os.environ, {
        "OPENAIR_RUNTIME_TYPE": "inline",
        "SKILL_EXEC_MODE": "replay",
        "OPENAIR_AUTH_REQUIRED": "0",
        "OPENAIR_DEV_SEED_FIXTURES": "0",
        "OPENAIR_PERSISTENCE_BACKEND": "json",
        "OPENAIR_WORKER_AUTH_TOKEN": "test-worker-token",
        "OPENAIR_ARTIFACT_STORAGE": "local",
    }):
        # Force reload settings
        import app.config as cfg_mod
        cfg_mod.settings = cfg_mod.Settings()

        from app.main import app
        client = TestClient(app, raise_server_exceptions=False)
        yield client


@pytest.fixture()
def minio_api_client(api_client, monkeypatch):
    """Enable the mocked MinIO boundary on the shared Settings instance."""
    import app.modules.minio_client as minio_client
    import app.modules.user_skills_service as user_skills_service

    monkeypatch.setattr(minio_client.settings, "artifact_storage", "minio")
    monkeypatch.setattr(user_skills_service.settings, "artifact_storage", "minio")
    return api_client


def _get_app():
    """Return the FastAPI app instance (for httpx.ASGITransport in async tests)."""
    from app.main import app
    return app


# ===========================================================================
# Internal Worker Routes — auth + callback flow
# ===========================================================================


class TestWorkerCallbackRoutes:
    """Test POST /internal/worker/result and /internal/worker/stream-event."""

    def test_result_no_token_rejected(self, api_client):
        """Missing auth token → 403."""
        resp = api_client.post("/internal/worker/result", json={
            "execution_id": "exec-001",
            "reply": "test",
            "status": "completed",
        })
        assert resp.status_code == 403

    def test_result_wrong_token_rejected(self, api_client):
        """Wrong auth token → 403."""
        resp = api_client.post(
            "/internal/worker/result",
            json={"execution_id": "exec-001", "reply": "test", "status": "completed"},
            headers={"x-openair-worker-token": "wrong-token"},
        )
        assert resp.status_code == 403

    def test_result_valid_token_accepted(self, reset_callback_registry):
        """Correct token → resolves the registered Future and returns 200.

        Uses httpx.AsyncClient so register(), the HTTP call, and the await
        all share one event loop (TestClient uses a separate loop, which
        breaks cross-loop Future resolution).
        """
        import httpx
        from app.execution.worker_callback import get_callback_registry

        async def _runner():
            # Register inside the running loop
            registry = get_callback_registry()
            future = registry.register("exec-100")

            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=_get_app()),
                base_url="http://test",
            ) as client:
                # Fire the callback; it resolves the Future in the same loop
                import asyncio
                task = asyncio.create_task(client.post(
                    "/internal/worker/result",
                    json={
                        "execution_id": "exec-100",
                        "reply": "Claude completed the task",
                        "session_id": "sess_xyz",
                        "status": "completed",
                    },
                    headers={"x-openair-worker-token": "test-worker-token"},
                ))
                # Wait for the Future to resolve (or timeout)
                result = await asyncio.wait_for(future, timeout=3.0)
                resp = await task

            assert resp.status_code == 200
            assert resp.json()["status"] == "ok"
            assert result["reply"] == "Claude completed the task"
            assert result["session_id"] == "sess_xyz"

        import asyncio
        asyncio.new_event_loop().run_until_complete(_runner())

    def test_result_unknown_execution_id(self, api_client, reset_callback_registry):
        """Resolving an unknown execution_id → 200 (idempotent, just logs warning)."""
        resp = api_client.post(
            "/internal/worker/result",
            json={"execution_id": "exec-unknown", "reply": "ghost", "status": "completed"},
            headers={"x-openair-worker-token": "test-worker-token"},
        )
        assert resp.status_code == 200

    def test_stream_event_no_token_rejected(self, api_client):
        """Missing auth token on stream-event → 403."""
        resp = api_client.post("/internal/worker/stream-event", json={
            "execution_id": "exec-002",
            "event": {"type": "assistant_delta", "text": "hi"},
        })
        assert resp.status_code == 403

    def test_stream_event_valid_token_accepted(self, api_client, reset_callback_registry):
        """Correct token → pushes event to the Queue and returns 200."""
        from app.execution.worker_callback import get_callback_registry
        registry = get_callback_registry()
        queue = registry.register_stream("exec-200")

        resp = api_client.post(
            "/internal/worker/stream-event",
            json={
                "execution_id": "exec-200",
                "event": {"type": "assistant_delta", "text": "streaming..."},
            },
            headers={"x-openair-worker-token": "test-worker-token"},
        )
        assert resp.status_code == 200

        # Verify the event was pushed
        import asyncio
        event = asyncio.get_event_loop().run_until_complete(
            asyncio.wait_for(queue.get(), timeout=1.0)
        )
        assert event is not None
        assert event["text"] == "streaming..."

    def test_containers_list_requires_token(self, api_client):
        """GET /internal/worker/containers without token → 403."""
        resp = api_client.get("/internal/worker/containers")
        assert resp.status_code == 403

    def test_containers_list_returns_list(self, api_client):
        """With valid token → returns a list (empty in inline mode)."""
        resp = api_client.get(
            "/internal/worker/containers",
            headers={"x-openair-worker-token": "test-worker-token"},
        )
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)


# ===========================================================================
# Skills API Routes
# ===========================================================================


class TestSystemSkillsRoute:
    """Test GET /api/skills/system."""

    def test_returns_list(self, api_client):
        """System skills endpoint returns a list."""
        resp = api_client.get("/api/skills/system")
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, list)

    def test_returns_empty_without_registry(self, api_client):
        """load_system_skills_registry() returns [] when registry.yaml is missing."""
        from app.modules.user_skills_service import load_system_skills_registry
        from pathlib import Path

        # Patch repo_root to point at a temp dir with no skills/registry.yaml
        fake_root = Path(os.environ.get("TEMP", "/tmp")) / "openair_no_skills_test"
        with patch("app.modules.user_skills_service.repo_root", return_value=fake_root):
            result = load_system_skills_registry()
        assert result == []


class TestUserSkillsRoutes:
    """Test user skills CRUD endpoints with mocked MinIO."""

    def test_list_user_skills_no_auth_in_dev_mode(self, minio_api_client, mock_minio_client):
        """In dev mode (AUTH_REQUIRED=0), returns list without explicit auth."""
        # Mock MinIO list_objects to return empty
        mock_minio_client.list_objects.return_value = []

        resp = minio_api_client.get("/api/skills/user")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_upload_skill_no_skill_md(self, minio_api_client, mock_minio_client):
        """Upload without SKILL.md → 400."""
        resp = minio_api_client.post(
            "/api/skills/user",
            data={"skill_name": "bad-skill"},
            files=[("files", ("readme.md", b"# Readme", "text/markdown"))],
        )
        assert resp.status_code == 400
        assert "SKILL.md" in resp.json()["detail"]

    def test_upload_skill_success(self, minio_api_client, mock_minio_client):
        """Upload with SKILL.md → 200, returns skill metadata."""
        mock_minio_client.put_object.return_value = None

        resp = minio_api_client.post(
            "/api/skills/user",
            data={"skill_name": "my-test-skill"},
            files=[
                ("files", ("SKILL.md", b"# My Test Skill\nA test description.", "text/markdown")),
                ("files", ("skill.yaml", b"description: 'test'", "application/yaml")),
            ],
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["skill_name"] == "my-test-skill"
        assert "SKILL.md" in body["files"]
        assert "skill.yaml" in body["files"]

    def test_upload_skill_sanitises_name(self, minio_api_client, mock_minio_client):
        """Skill name with slashes/spaces gets sanitised."""
        mock_minio_client.put_object.return_value = None

        resp = minio_api_client.post(
            "/api/skills/user",
            data={"skill_name": "my/evil\\skill name"},
            files=[
                ("files", ("SKILL.md", b"# Skill", "text/markdown")),
            ],
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["skill_name"] == "my-evil-skill-name"

    def test_get_user_skill_not_found(self, minio_api_client, mock_minio_client):
        """GET a non-existent skill → 404."""
        mock_minio_client.list_objects.return_value = []

        resp = minio_api_client.get("/api/skills/user/nonexistent")
        assert resp.status_code == 404

    def test_get_user_skill_found(self, minio_api_client, mock_minio_client):
        """GET an existing skill → 200 with details."""
        # Mock list_objects to return skill files
        mock_obj1 = MagicMock()
        mock_obj1.object_name = "user-skills/user_demo_owner/my-skill/SKILL.md"
        mock_obj2 = MagicMock()
        mock_obj2.object_name = "user-skills/user_demo_owner/my-skill/skill.yaml"
        mock_minio_client.list_objects.return_value = [mock_obj1, mock_obj2]

        # Mock get_object for skill.yaml
        mock_response = MagicMock()
        mock_response.read.return_value = b"description: 'a great skill'"
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_minio_client.get_object.return_value = mock_response

        resp = minio_api_client.get("/api/skills/user/my-skill")
        assert resp.status_code == 200
        body = resp.json()
        assert body["skill_name"] == "my-skill"
        assert "SKILL.md" in body["files"]

    def test_delete_user_skill_not_found(self, minio_api_client, mock_minio_client):
        """DELETE a non-existent skill → 404."""
        mock_minio_client.list_objects.return_value = []

        resp = minio_api_client.delete("/api/skills/user/nonexistent")
        assert resp.status_code == 404

    def test_delete_user_skill_success(self, minio_api_client, mock_minio_client):
        """DELETE an existing skill → 200."""
        mock_obj = MagicMock()
        mock_obj.object_name = "user-skills/user_demo_owner/my-skill/SKILL.md"
        mock_minio_client.list_objects.return_value = [mock_obj]
        mock_minio_client.remove_object.return_value = None

        resp = minio_api_client.delete("/api/skills/user/my-skill")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "deleted"
        assert body["skill_name"] == "my-skill"


# ===========================================================================
# ContainerManager — Docker SDK interaction (mocked)
# ===========================================================================


class TestContainerManagerMocked:
    """Test ContainerManager with mocked Docker SDK."""

    def test_launch_creates_container(self, mock_docker_client, reset_container_manager):
        from app.execution.container_manager import ContainerManager

        mock_container = MagicMock()
        mock_container.id = "container-abc123"
        mock_docker_client.containers.run.return_value = mock_container

        with patch("app.config.settings") as mock_settings:
            mock_settings.worker_image = "openair-worker:test"
            mock_settings.worker_cpu_limit = 1.0
            mock_settings.worker_memory_limit = "2g"

            import app.execution.container_manager as cm_mod
            cm_mod.settings = mock_settings

            mgr = ContainerManager()
            cid = mgr.launch(
                task_id="task-001",
                execution_id="exec-task-001-abcd",
                env_vars={"OPENAIR_TASK_ID": "task-001"},
                workspace_bind="/tmp/workspace",
            )

        assert cid == "container-abc123"
        mock_docker_client.containers.run.assert_called_once()

        # Verify call arguments (convenience kwargs that docker-py routes into config/HostConfig).
        call_kwargs = mock_docker_client.containers.run.call_args[1]
        assert call_kwargs["image"] == "openair-worker:test"
        assert call_kwargs["mem_limit"] == "2g"
        assert call_kwargs["detach"] is True
        assert "openair-worker-exec-task" in call_kwargs["name"]

    def test_get_status_running(self, mock_docker_client, reset_container_manager):
        from app.execution.container_manager import ContainerManager

        mock_container = MagicMock()
        mock_container.id = "c1"
        mock_container.status = "running"
        mock_docker_client.containers.run.return_value = mock_container
        mock_docker_client.containers.get.return_value = mock_container

        with patch("app.config.settings") as mock_settings:
            mock_settings.worker_image = "openair-worker:test"
            mock_settings.worker_cpu_limit = 1.0
            mock_settings.worker_memory_limit = "2g"

            import app.execution.container_manager as cm_mod
            cm_mod.settings = mock_settings

            mgr = ContainerManager()
            mgr.launch("t1", "exec-001", {})
            status = mgr.get_status("exec-001")

        assert status == "running"

    def test_get_status_unknown_returns_none(self, mock_docker_client, reset_container_manager):
        from app.execution.container_manager import ContainerManager

        mgr = ContainerManager()
        assert mgr.get_status("exec-nope") is None

    def test_cleanup_removes_container(self, mock_docker_client, reset_container_manager):
        from app.execution.container_manager import ContainerManager

        mock_container = MagicMock()
        mock_container.id = "c2"
        mock_docker_client.containers.run.return_value = mock_container
        mock_docker_client.containers.get.return_value = mock_container

        with patch("app.config.settings") as mock_settings:
            mock_settings.worker_image = "openair-worker:test"
            mock_settings.worker_cpu_limit = 1.0
            mock_settings.worker_memory_limit = "2g"

            import app.execution.container_manager as cm_mod
            cm_mod.settings = mock_settings

            mgr = ContainerManager()
            mgr.launch("t2", "exec-002", {})
            mgr.cleanup("exec-002")

        mock_container.remove.assert_called_once_with(force=True)

    def test_list_active(self, mock_docker_client, reset_container_manager):
        from app.execution.container_manager import ContainerManager

        mock_c = MagicMock()
        mock_c.short_id = "abc123"
        mock_c.name = "openair-worker-test"
        mock_c.status = "running"
        mock_c.labels = {
            "openair.task_id": "t1",
            "openair.execution_id": "exec-001",
        }
        mock_docker_client.containers.list.return_value = [mock_c]

        mgr = ContainerManager()
        active = mgr.list_active()

        assert len(active) == 1
        assert active[0]["task_id"] == "t1"
        assert active[0]["status"] == "running"
