"""Layer 1: Pure unit tests for Cloud-08 components.

No Docker, no MinIO, no database. Tests core logic in isolation.
"""

# pyright: reportUnusedImport=false

from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

import pytest

# ===========================================================================
# WorkerCallbackRegistry — in-memory Future/Queue bridge
# ===========================================================================


class TestWorkerCallbackRegistry:
    """Test the callback registry that bridges worker HTTP callbacks to awaiting coroutines."""

    def test_register_and_resolve(self, reset_callback_registry):
        """Register a Future, then resolve it — awaiter should get the result."""
        from app.execution.worker_callback import WorkerCallbackRegistry

        reg = WorkerCallbackRegistry()

        async def _runner():
            future = reg.register("exec-001")
            # Simulate worker callback resolving the Future
            reg.resolve("exec-001", {"reply": "hello", "status": "completed"})
            result = await future
            assert result["reply"] == "hello"
            assert result["status"] == "completed"

        asyncio.get_event_loop().run_until_complete(_runner())

    def test_resolve_unknown_execution_id(self, reset_callback_registry):
        """Resolving a non-existent execution_id should not raise."""
        from app.execution.worker_callback import WorkerCallbackRegistry

        reg = WorkerCallbackRegistry()
        reg.resolve("exec-nonexistent", {"reply": "oops"})
        # Should not raise — just logs a warning

    def test_register_stream_and_push(self, reset_callback_registry):
        """Register a stream Queue, push events, consume them."""
        from app.execution.worker_callback import WorkerCallbackRegistry

        reg = WorkerCallbackRegistry()

        async def _runner():
            queue = reg.register_stream("exec-002")

            reg.push_event("exec-002", {"type": "assistant_delta", "text": "Hi"})
            reg.push_event("exec-002", {"type": "assistant_delta", "text": " there"})
            reg.push_event("exec-002", {"type": "done", "sessionId": "sess1"})

            # Signal end
            reg._signal_stream_end("exec-002")

            events = []
            while True:
                event = await asyncio.wait_for(queue.get(), timeout=1.0)
                if event is None:
                    break
                events.append(event)

            assert len(events) == 3
            assert events[0]["text"] == "Hi"
            assert events[2]["type"] == "done"

        asyncio.get_event_loop().run_until_complete(_runner())

    def test_push_event_unknown_execution_id(self, reset_callback_registry):
        """Pushing events to a non-existent stream should not raise."""
        from app.execution.worker_callback import WorkerCallbackRegistry

        reg = WorkerCallbackRegistry()
        reg.push_event("exec-nope", {"type": "test"})
        # Should not raise

    def test_cleanup(self, reset_callback_registry):
        """cleanup() should remove both pending Future and stream Queue."""
        from app.execution.worker_callback import WorkerCallbackRegistry

        reg = WorkerCallbackRegistry()

        async def _runner():
            reg.register("exec-003")
            reg.register_stream("exec-003")
            reg.cleanup("exec-003")
            assert "exec-003" not in reg._pending
            assert "exec-003" not in reg._queues

        asyncio.get_event_loop().run_until_complete(_runner())

    def test_cancel(self, reset_callback_registry):
        """cancel() should cancel the pending Future."""
        from app.execution.worker_callback import WorkerCallbackRegistry

        reg = WorkerCallbackRegistry()
        future = None

        async def _runner():
            nonlocal future
            future = reg.register("exec-004")
            reg.register_stream("exec-004")
            reg.cancel("exec-004")

        asyncio.get_event_loop().run_until_complete(_runner())

        assert future is not None
        assert future.cancelled() or future.done()
        assert "exec-004" not in reg._pending
        assert "exec-004" not in reg._queues

    def test_resolve_signals_stream_end(self, reset_callback_registry):
        """When resolve() is called, it should also signal stream end."""
        from app.execution.worker_callback import WorkerCallbackRegistry

        reg = WorkerCallbackRegistry()

        async def _runner():
            queue = reg.register_stream("exec-005")
            future = reg.register("exec-005")

            reg.resolve("exec-005", {"reply": "done"})
            result = await future
            assert result["reply"] == "done"

            # Stream should also get a None sentinel
            sentinel = await asyncio.wait_for(queue.get(), timeout=1.0)
            assert sentinel is None

        asyncio.get_event_loop().run_until_complete(_runner())


# ===========================================================================
# Skill Schemas — Pydantic model validation
# ===========================================================================


class TestSkillSchemas:
    """Test Pydantic models for Skills API."""

    def test_user_skill_out_defaults(self):
        from app.models.skill_schemas import UserSkillOut

        skill = UserSkillOut(skill_name="my-skill")
        assert skill.skill_name == "my-skill"
        assert skill.description == ""
        assert skill.created_at == ""
        assert skill.files == []

    def test_user_skill_out_full(self):
        from app.models.skill_schemas import UserSkillOut

        skill = UserSkillOut(
            skill_name="test-skill",
            description="A test skill",
            created_at="2026-01-01T00:00:00Z",
            files=["SKILL.md", "skill.yaml"],
        )
        assert skill.description == "A test skill"
        assert len(skill.files) == 2

    def test_user_skill_detail_out(self):
        from app.models.skill_schemas import UserSkillDetailOut

        detail = UserSkillDetailOut(
            skill_name="detailed-skill",
            skill_yaml={"description": "yaml-desc"},
            skill_md_preview="# My Skill\nThis is a preview.",
        )
        assert detail.skill_yaml is not None
        assert detail.skill_yaml["description"] == "yaml-desc"
        assert "My Skill" in detail.skill_md_preview

    def test_system_skill_out(self):
        from app.models.skill_schemas import SystemSkillOut

        skill = SystemSkillOut(
            skill_id="literature_search",
            claude_name="literature-search",
            display_name="文献检索",
            domain="common_research",
            stages=["planning", "execution"],
            skill_type="atomic",
            status="active",
        )
        assert skill.domain == "common_research"
        assert len(skill.stages) == 2


# ===========================================================================
# ExecutionRequest / ExecutionResult — dataclass tests
# ===========================================================================


class TestExecutionDataClasses:
    """Test ExecutionRequest and ExecutionResult dataclasses."""

    def test_execution_request_defaults(self):
        from app.execution.runtime_boundary import ExecutionRequest

        req = ExecutionRequest(
            task_id="t001",
            message="hello",
            mode="creation",
        )
        assert req.session_id is None
        assert req.task == {}
        assert req.owner_id is None
        assert req.user_skill_names is None

    def test_execution_request_container_fields(self):
        from app.execution.runtime_boundary import ExecutionRequest

        req = ExecutionRequest(
            task_id="t002",
            message="run",
            mode="execution",
            owner_id="user_001",
            user_skill_names=["skill-a", "skill-b"],
        )
        assert req.owner_id == "user_001"
        assert req.user_skill_names is not None
        assert len(req.user_skill_names) == 2

    def test_execution_result_defaults(self):
        from app.execution.runtime_boundary import ExecutionResult

        result = ExecutionResult(reply="done")
        assert result.status == "completed"
        assert result.session_id is None
        assert result.error is None
        assert result.runtime == "unknown"

    def test_execution_result_failed(self):
        from app.execution.runtime_boundary import ExecutionResult

        result = ExecutionResult(
            reply="",
            status="failed",
            error="Container execution timed out",
            runtime="container",
        )
        assert result.status == "failed"
        assert result.error is not None
        assert "timed out" in result.error.lower()


# ===========================================================================
# Runtime factory — get_execution_runtime()
# ===========================================================================


class TestRuntimeFactory:
    """Test that the runtime factory returns the correct implementation."""

    def test_inline_runtime_default(self, reset_runtime):
        from app.execution.runtime_boundary import (
            InlineRuntime,
            get_execution_runtime,
        )

        with patch("app.config.settings") as mock_settings:
            mock_settings.runtime_type = "inline"
            mock_settings.skill_exec_mode = "replay"
            runtime = get_execution_runtime()
            assert isinstance(runtime, InlineRuntime)

    def test_container_runtime(self, reset_runtime):
        from app.execution.runtime_boundary import get_execution_runtime

        with patch("app.config.settings") as mock_settings:
            mock_settings.runtime_type = "container"
            # Need to also patch the import inside get_execution_runtime
            with patch("app.execution.runtime_boundary.settings", mock_settings):
                from app.execution.container_runtime import ContainerRuntime

                # Patch the global settings used by the factory
                import app.execution.runtime_boundary as rb_mod
                rb_mod.settings = mock_settings
                runtime = get_execution_runtime()
                assert isinstance(runtime, ContainerRuntime)
                # Restore
                from app.config import settings as real_settings
                rb_mod.settings = real_settings

    def test_worker_runtime_stub(self, reset_runtime):
        from app.execution.runtime_boundary import (
            WorkerRuntimeStub,
            get_execution_runtime,
        )

        with patch("app.config.settings") as mock_settings:
            mock_settings.runtime_type = "worker"
            import app.execution.runtime_boundary as rb_mod
            rb_mod.settings = mock_settings
            runtime = get_execution_runtime()
            assert isinstance(runtime, WorkerRuntimeStub)
            # Restore
            from app.config import settings as real_settings
            rb_mod.settings = real_settings

    def test_worker_stub_raises_not_implemented(self, reset_runtime):
        from app.execution.runtime_boundary import WorkerRuntimeStub

        stub = WorkerRuntimeStub()

        with pytest.raises(NotImplementedError, match="not implemented in Phase-1"):
            asyncio.get_event_loop().run_until_complete(
                stub.submit(
                    __import__("app.execution.runtime_boundary", fromlist=["ExecutionRequest"]).ExecutionRequest(
                        task_id="t001", message="test", mode="creation"
                    )
                )
            )


# ===========================================================================
# Environment serialisation — _serialise_env
# ===========================================================================


class TestSerialiseEnv:
    """Test ExecutionRequest → env vars serialisation."""

    def test_basic_fields(self, sample_execution_request):
        from app.execution.container_runtime import _serialise_env

        with patch("app.config.settings") as mock_settings:
            mock_settings.claude_permission_mode = "bypassPermissions"
            mock_settings.artifact_storage = "local"
            mock_settings.worker_callback_url = "http://host.docker.internal:8000"
            mock_settings.worker_auth_token = "test-token"

            import app.execution.container_runtime as cr_mod
            original = cr_mod.settings
            cr_mod.settings = mock_settings
            try:
                env = _serialise_env(sample_execution_request)
            finally:
                cr_mod.settings = original

        assert env["OPENAIR_TASK_ID"] == "task_test_001"
        assert env["OPENAIR_MESSAGE"] == "测试消息"
        assert env["OPENAIR_MODE"] == "execution"
        assert "OPENAIR_TASK_JSON" in env
        task_json = json.loads(env["OPENAIR_TASK_JSON"])
        assert task_json["taskId"] == "task_test_001"

    def test_owner_id_and_skills(self, sample_execution_request):
        """owner_id passes through; Phase-1 changed skill delivery to the presigned
        manifest — the legacy OPENAIR_USER_SKILL_NAMES env var is intentionally gone."""
        from app.execution.container_runtime import _serialise_env

        with patch("app.config.settings") as mock_settings:
            mock_settings.claude_permission_mode = "bypassPermissions"
            mock_settings.artifact_storage = "local"
            mock_settings.worker_callback_url = "http://test:8000"
            mock_settings.worker_auth_token = "tok"

            import app.execution.container_runtime as cr_mod
            original = cr_mod.settings
            cr_mod.settings = mock_settings
            try:
                env = _serialise_env(sample_execution_request)
            finally:
                cr_mod.settings = original

        assert env["OPENAIR_OWNER_ID"] == "user_001"
        # Phase-1: plain skill-names env removed; with minio off, no manifest either.
        assert "OPENAIR_USER_SKILL_NAMES" not in env
        assert "OPENAIR_USER_SKILLS_MANIFEST" not in env

    def test_minio_credentials_not_in_worker_env(self, sample_execution_request):
        """Phase-1 security closure: MinIO root credentials must NEVER reach the worker
        env. Skill delivery is object-scoped presigned GETs; the worker holds no root key."""
        from app.execution.container_runtime import _serialise_env

        with patch("app.execution.container_runtime.settings") as mock_settings:
            mock_settings.claude_permission_mode = "bypassPermissions"
            mock_settings.artifact_storage = "minio"
            mock_settings.minio_endpoint = "minio:9000"
            mock_settings.minio_access_key = "akey"
            mock_settings.minio_secret_key = "skey"
            mock_settings.minio_bucket = "my-bucket"
            mock_settings.minio_secure = False
            mock_settings.worker_callback_url = "http://api:8000"
            mock_settings.worker_auth_token = "tok"
            env = _serialise_env(sample_execution_request)

        assert "OPENAIR_MINIO_ACCESS_KEY" not in env
        assert "OPENAIR_MINIO_SECRET_KEY" not in env
        assert "OPENAIR_MINIO_ENDPOINT" not in env
        assert "OPENAIR_MINIO_BUCKET" not in env

    def test_proxy_rewrite(self, sample_execution_request):
        """Proxy env vars should have localhost → host.docker.internal rewritten."""
        from app.execution.container_runtime import _serialise_env

        with patch.dict(os.environ, {"HTTP_PROXY": "http://localhost:8080"}, clear=False):
            with patch("app.config.settings") as mock_settings:
                mock_settings.claude_permission_mode = "bypassPermissions"
                mock_settings.artifact_storage = "local"
                mock_settings.worker_callback_url = "http://test:8000"
                mock_settings.worker_auth_token = "tok"

                import app.execution.container_runtime as cr_mod
                original = cr_mod.settings
                cr_mod.settings = mock_settings
                try:
                    env = _serialise_env(sample_execution_request)
                finally:
                    cr_mod.settings = original

        assert "host.docker.internal" in env.get("HTTP_PROXY", "")

    def test_claude_env_passthrough(self, sample_execution_request):
        """ANTHROPIC_API_KEY and related vars should pass through."""
        from app.execution.container_runtime import _serialise_env

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test-123"}, clear=False):
            with patch("app.config.settings") as mock_settings:
                mock_settings.claude_permission_mode = "bypassPermissions"
                mock_settings.artifact_storage = "local"
                mock_settings.worker_callback_url = "http://test:8000"
                mock_settings.worker_auth_token = "tok"

                import app.execution.container_runtime as cr_mod
                original = cr_mod.settings
                cr_mod.settings = mock_settings
                try:
                    env = _serialise_env(sample_execution_request)
                finally:
                    cr_mod.settings = original

        assert env.get("ANTHROPIC_API_KEY") == "sk-test-123"


def test_turn_payload_never_carries_session_id():
    """Regression guard: the keepalive turn payload must NOT propagate
    session_id. A task-level session_id cannot be resumed inside a worker whose
    ~/.claude doesn't contain it, and resuming a non-existent session makes the
    Claude CLI exit 1 ("No conversation found with session ID"). The one-shot
    env path omits OPENAIR_SESSION_ID for the same reason; the keepalive payload
    must match that invariant. See container_runtime._turn_payload.
    """
    from app.execution.container_runtime import _turn_payload
    from app.execution.runtime_boundary import ExecutionRequest

    request = ExecutionRequest(
        task_id="t1",
        message="hi",
        mode="execution",
        session_id="sess-that-must-not-leak",
        task={"taskId": "t1"},
    )
    payload = _turn_payload(request, "exec-1", streaming=False)
    assert payload["session_id"] is None
    # other per-turn fields still flow through
    assert payload["message"] == "hi"
    assert payload["execution_id"] == "exec-1"
    assert payload["streaming"] is False


# ===========================================================================
# Config — runtime type parsing
# ===========================================================================


class TestConfigRuntimeType:
    """Test the _runtime_type() helper."""

    def test_default_inline(self):
        from app.config import _runtime_type

        with patch.dict(os.environ, {"OPENAIR_RUNTIME_TYPE": ""}, clear=False):
            # Remove the key entirely to test default
            os.environ.pop("OPENAIR_RUNTIME_TYPE", None)
            assert _runtime_type() == "inline"

    def test_container(self):
        from app.config import _runtime_type

        with patch.dict(os.environ, {"OPENAIR_RUNTIME_TYPE": "container"}):
            assert _runtime_type() == "container"

    def test_worker(self):
        from app.config import _runtime_type

        with patch.dict(os.environ, {"OPENAIR_RUNTIME_TYPE": "worker"}):
            assert _runtime_type() == "worker"

    def test_invalid_falls_back_to_inline(self):
        from app.config import _runtime_type

        with patch.dict(os.environ, {"OPENAIR_RUNTIME_TYPE": "kubernetes"}):
            assert _runtime_type() == "inline"


# Need to import os for the proxy test
import os
