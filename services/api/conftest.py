"""Shared test fixtures for Cloud-08 tests."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

# Ensure test environment variables are set BEFORE importing app modules
# to avoid side-effects from the developer's real .env
_test_env = {
    "OPENAIR_ENV": "test",
    "OPENAIR_RUNTIME_TYPE": "inline",  # overridden per-test
    "SKILL_EXEC_MODE": "replay",
    "OPENAIR_AUTH_REQUIRED": "0",
    "OPENAIR_DEV_SEED_FIXTURES": "0",
    "OPENAIR_PERSISTENCE_BACKEND": "json",
    "OPENAIR_WORKER_AUTH_TOKEN": "test-worker-token",
    "OPENAIR_WORKSPACE_ROOT": "",
    "OPENAIR_MINIO_ENDPOINT": "localhost:9000",
    "OPENAIR_MINIO_ACCESS_KEY": "test-access",
    "OPENAIR_MINIO_SECRET_KEY": "test-secret",
    "OPENAIR_MINIO_BUCKET": "test-bucket",
    "OPENAIR_ARTIFACT_STORAGE": "local",
    "OPENAIR_LOG_LEVEL": "WARNING",
    # Catalog capability facts read provider publication config without network I/O.
    "LLM_PROVIDER_MODEL": "test-model",
    "LLM_PROVIDER_MODEL_TERRA": "test-model",
    "LLM_PROVIDER_BASE_URL": "https://example.test",
    "LLM_PROVIDER_API_KEY": "test-key",
}

for k, v in _test_env.items():
    os.environ.setdefault(k, v)


@pytest.fixture(autouse=True)
def _reset_process_wide_singletons():
    """Reset memoised process-wide state before/after every test.

    Three globals otherwise leak across tests in the same process:

    * ``repo_paths._repo_root_cache`` -- ``repo_root()`` memoises its first
      result, so the first test to call it pins the root for everyone. Clearing
      it per test makes each test's ``OPENAIR_REPO_ROOT`` authoritative.
    * ``app.config.settings`` -- several tests rebind it to a fresh
      ``Settings()``; without restoring the swap breaks later tests' own
      ``settings`` monkeypatches.
    * ``quota_service._quota_service`` -- a singleton built from
      ``get_user_repository()``; without resetting it a prior test's repo root
      leaks into quota reads.

    These modules are looked up via ``sys.modules`` rather than imported here:
    eagerly importing ``app.config`` would freeze its env-derived class defaults
    (e.g. ``persistence_backend``) to the conftest env before a test can set its
    own, regressing tests that rely on importing ``app.config`` under a bespoke
    env.
    """
    import sys
    from app.modules import repo_paths

    repo_paths._repo_root_cache = None
    facts_mod = sys.modules.get("app.modules.resource_capability_facts")
    if facts_mod is not None and hasattr(facts_mod, "clear_resource_capability_facts_cache"):
        facts_mod.clear_resource_capability_facts_cache()
    cfg_mod = sys.modules.get("app.config")
    saved_settings = cfg_mod.settings if cfg_mod is not None else None
    yield
    repo_paths._repo_root_cache = None
    facts_mod = sys.modules.get("app.modules.resource_capability_facts")
    if facts_mod is not None and hasattr(facts_mod, "clear_resource_capability_facts_cache"):
        facts_mod.clear_resource_capability_facts_cache()
    cfg_mod_now = sys.modules.get("app.config")
    if cfg_mod_now is not None and saved_settings is not None:
        cfg_mod_now.settings = saved_settings
    qs_mod = sys.modules.get("app.modules.quota_service")
    if qs_mod is not None:
        qs_mod._quota_service = None


@pytest.fixture()
def reset_runtime():
    """Reset cached runtime singleton between tests."""
    from app.execution.runtime_boundary import reset_execution_runtime
    reset_execution_runtime()
    yield
    reset_execution_runtime()


@pytest.fixture()
def reset_callback_registry():
    """Reset the singleton callback registry between tests."""
    import app.execution.worker_callback as mod
    mod._registry = None
    yield
    mod._registry = None


@pytest.fixture()
def reset_container_manager():
    """Reset the singleton container manager between tests."""
    import app.execution.container_manager as mod
    mod._manager = None
    yield
    mod._manager = None


@pytest.fixture()
def mock_docker_client():
    """Return a MagicMock that replaces the Docker SDK client."""
    with patch("docker.from_env") as mock_from_env:
        mock_client = MagicMock()
        mock_from_env.return_value = mock_client
        yield mock_client


@pytest.fixture()
def mock_minio_client():
    """Return a MagicMock that replaces the MinIO client."""
    # Force-import the module so patch() can resolve the dotted attribute path
    import app.modules.minio_client  # noqa: F401
    mock = MagicMock()
    with patch("app.modules.minio_client.get_minio_client", return_value=mock):
        yield mock


@pytest.fixture()
def sample_execution_request():
    """Return a sample ExecutionRequest for testing."""
    from app.execution.runtime_boundary import ExecutionRequest
    return ExecutionRequest(
        task_id="task_test_001",
        message="测试消息",
        mode="execution",
        session_id=None,
        task={"taskId": "task_test_001", "title": "测试任务"},
        owner_id="user_001",
        user_skill_names=["my-skill-a"],
        substage_key="literature_review",
        l2_claude_name="literature-search",
        available_skills=["literature_search", "hypothesis_construction"],
    )


@pytest.fixture()
def sample_worker_result():
    """Return a sample worker result dict."""
    return {
        "execution_id": "exec-task_tes-abcd1234",
        "reply": "这是 Claude 返回的测试结果",
        "session_id": "sess_abc123",
        "status": "completed",
        "error": None,
        "runtime": "claude_code",
    }
