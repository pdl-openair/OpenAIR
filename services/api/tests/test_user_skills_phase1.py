"""Phase-1 user-skill hardening — security口子 closure for skill distribution.

Covers:
- ``build_presigned_skills_manifest``: object-scoped presigned GETs for the owner's
  OWN skills only (no root key reaches the worker; no owner-header-spoofable endpoint).
- upload validation: dangerous names rejected, file-count / per-file size caps.
- ``list_user_skill_names`` + ``_resolve_owner_user_skills``: auto-populate path.
- ``_serialise_env``: emits ``OPENAIR_USER_SKILLS_MANIFEST`` and NEVER emits
  ``OPENAIR_MINIO_ACCESS_KEY``/``SECRET_KEY``/``OPENAIR_USER_SKILL_NAMES``.

Threat closed: a malicious user skill can no longer read the MinIO root key from env
(it isn't there) nor fetch another owner's skills (delivery is object-scoped presigned
GETs for this owner's keys only). Residual risks (worker token / Anthropic key theft,
shared-volume sibling reads) are Phase-2 items.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app

OWNER = "ownerA"


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def _minio_obj(key: str) -> MagicMock:
    m = MagicMock()
    m.object_name = key
    return m


def _prefix_list(objects):
    """MinIO honours the ``prefix`` arg — listing is scoped to that prefix.

    The cross-tenant guarantee rests on this: the API only ever asks for
    ``user-skills/{owner}/``, so other tenants' objects are never returned. Tests must
    simulate that prefix-scoping rather than returning every object regardless.
    """

    def _side_effect(bucket, prefix="", recursive=False):
        return [o for o in objects if o.object_name.startswith(prefix)]

    return _side_effect


# ---------------------------------------------------------------------------
# Presigned manifest builder
# ---------------------------------------------------------------------------


class TestPresignedManifest:
    def test_manifest_contains_only_owners_own_skills(self, mock_minio_client, monkeypatch):
        """Only this owner's object keys flow into presigned GETs — no cross-tenant leak."""
        monkeypatch.setattr(settings, "artifact_storage", "minio")
        mock_minio_client.list_objects.side_effect = _prefix_list([
            _minio_obj("user-skills/ownerA/my-skill/SKILL.md"),
            _minio_obj("user-skills/ownerA/my-skill/skill.yaml"),
            _minio_obj("user-skills/ownerB/secret/SKILL.md"),  # other owner — must NOT appear
        ])
        with patch(
            "app.modules.minio_client.presigned_get_object",
            side_effect=lambda key, **_: f"http://mock/{key}",
        ):
            from app.modules.user_skills_service import build_presigned_skills_manifest

            manifest = build_presigned_skills_manifest(OWNER, None, ttl_seconds=300)

        assert [s["name"] for s in manifest] == ["my-skill"]
        files = manifest[0]["files"]
        assert {f["path"] for f in files} == {"SKILL.md", "skill.yaml"}
        # every URL is scoped to this owner's prefix
        for f in files:
            assert f["url"].startswith("http://mock/user-skills/ownerA/my-skill/")
        # no other-owner key was ever signed
        assert all("ownerB" not in f["url"] for f in files)

    def test_manifest_names_filter(self, mock_minio_client, monkeypatch):
        monkeypatch.setattr(settings, "artifact_storage", "minio")
        mock_minio_client.list_objects.side_effect = _prefix_list([
            _minio_obj("user-skills/ownerA/foo/SKILL.md"),
            _minio_obj("user-skills/ownerA/bar/SKILL.md"),
            _minio_obj("user-skills/ownerA/baz/SKILL.md"),
        ])
        with patch("app.modules.minio_client.presigned_get_object", side_effect=lambda key, **_: f"http://mock/{key}"):
            from app.modules.user_skills_service import build_presigned_skills_manifest

            manifest = build_presigned_skills_manifest(OWNER, ["foo", "baz"], ttl_seconds=60)
        assert sorted(s["name"] for s in manifest) == ["baz", "foo"]

    def test_manifest_empty_when_no_skills(self, mock_minio_client, monkeypatch):
        monkeypatch.setattr(settings, "artifact_storage", "minio")
        mock_minio_client.list_objects.return_value = []
        with patch("app.modules.minio_client.presigned_get_object") as presign:
            from app.modules.user_skills_service import build_presigned_skills_manifest

            assert build_presigned_skills_manifest(OWNER, None, ttl_seconds=60) == []
        presign.assert_not_called()

    def test_manifest_skips_skill_without_skill_md(self, mock_minio_client, monkeypatch):
        """A skill dir lacking SKILL.md is not delivered (worker can't use it anyway)."""
        monkeypatch.setattr(settings, "artifact_storage", "minio")
        mock_minio_client.list_objects.side_effect = _prefix_list([
            _minio_obj("user-skills/ownerA/has-md/SKILL.md"),
            _minio_obj("user-skills/ownerA/no-md/readme.md"),
        ])
        with patch("app.modules.minio_client.presigned_get_object", side_effect=lambda key, **_: f"http://mock/{key}"):
            from app.modules.user_skills_service import build_presigned_skills_manifest

            manifest = build_presigned_skills_manifest(OWNER, None, ttl_seconds=60)
        assert [s["name"] for s in manifest] == ["has-md"]

    def test_manifest_raises_on_file_count_overflow(self, mock_minio_client, monkeypatch):
        """Total file count over the hard cap raises — never bakes an oversized env var."""
        monkeypatch.setattr(settings, "artifact_storage", "minio")
        monkeypatch.setattr(settings, "user_skill_manifest_max_files", 2)
        monkeypatch.setattr(settings, "user_skill_manifest_max_bytes", 1_000_000)
        mock_minio_client.list_objects.side_effect = _prefix_list([
            _minio_obj("user-skills/ownerA/big/SKILL.md"),
            _minio_obj("user-skills/ownerA/big/a.py"),
            _minio_obj("user-skills/ownerA/big/b.py"),
        ])
        with patch("app.modules.minio_client.presigned_get_object", side_effect=lambda key, **_: f"http://mock/{key}"):
            from app.modules.user_skills_service import build_presigned_skills_manifest

            with pytest.raises(ValueError, match="file count"):
                build_presigned_skills_manifest(OWNER, None, ttl_seconds=60)

    def test_manifest_raises_on_byte_overflow(self, mock_minio_client, monkeypatch):
        """Serialized byte size over the hard cap raises — protects worker env size."""
        monkeypatch.setattr(settings, "artifact_storage", "minio")
        monkeypatch.setattr(settings, "user_skill_manifest_max_files", 1_000_000)
        monkeypatch.setattr(settings, "user_skill_manifest_max_bytes", 8)
        mock_minio_client.list_objects.side_effect = _prefix_list([
            _minio_obj("user-skills/ownerA/big/SKILL.md"),
        ])
        with patch("app.modules.minio_client.presigned_get_object", side_effect=lambda key, **_: f"http://mock/{key}"):
            from app.modules.user_skills_service import build_presigned_skills_manifest

            with pytest.raises(ValueError, match="size"):
                build_presigned_skills_manifest(OWNER, None, ttl_seconds=60)

    def test_manifest_skips_dirty_skill_name(self, mock_minio_client, monkeypatch):
        """Historical dirty object keys (traversal / leading-dash) must not be signed into
        the manifest — the name becomes a path segment on the worker."""
        monkeypatch.setattr(settings, "artifact_storage", "minio")
        mock_minio_client.list_objects.side_effect = _prefix_list([
            _minio_obj("user-skills/ownerA/clean/SKILL.md"),
            _minio_obj("user-skills/ownerA/../etc/SKILL.md"),  # historical traversal
            _minio_obj("user-skills/ownerA/-evil/SKILL.md"),  # leading dash
        ])
        with patch("app.modules.minio_client.presigned_get_object", side_effect=lambda key, **_: f"http://mock/{key}"):
            from app.modules.user_skills_service import build_presigned_skills_manifest

            manifest = build_presigned_skills_manifest(OWNER, None, ttl_seconds=60)
        assert [s["name"] for s in manifest] == ["clean"]
        # no URL was ever signed for a dirty key
        signed = [f["url"] for s in manifest for f in s["files"]]
        assert all("../" not in u and "-evil/" not in u for u in signed)


# ---------------------------------------------------------------------------
# Upload hardening
# ---------------------------------------------------------------------------


class TestUploadHardening:
    def _enable_minio(self, monkeypatch):
        monkeypatch.setattr(settings, "artifact_storage", "minio")

    def test_rejects_path_traversal_name(self, client, mock_minio_client, monkeypatch):
        self._enable_minio(monkeypatch)
        r = client.post(
            "/api/skills/user",
            data={"skill_name": "../etc/pwned"},
            files=[("files", ("SKILL.md", b"# x", "text/markdown"))],
        )
        assert r.status_code == 400

    def test_rejects_empty_name(self, client, mock_minio_client, monkeypatch):
        self._enable_minio(monkeypatch)
        r = client.post(
            "/api/skills/user",
            data={"skill_name": "   "},
            files=[("files", ("SKILL.md", b"# x", "text/markdown"))],
        )
        assert r.status_code == 400

    def test_rejects_leading_dash_name(self, client, mock_minio_client, monkeypatch):
        self._enable_minio(monkeypatch)
        r = client.post(
            "/api/skills/user",
            data={"skill_name": "-evil"},
            files=[("files", ("SKILL.md", b"# x", "text/markdown"))],
        )
        assert r.status_code == 400

    def test_rejects_too_many_files(self, client, mock_minio_client, monkeypatch):
        self._enable_minio(monkeypatch)
        monkeypatch.setattr(settings, "user_skill_max_files", 2)
        files = [("files", (f"f{i}.md", b"x", "text/markdown")) for i in range(3)]
        r = client.post(
            "/api/skills/user",
            data={"skill_name": "ok-name"},
            files=files,
        )
        assert r.status_code == 400
        assert "too many files" in r.json()["detail"]

    def test_rejects_oversize_file(self, client, mock_minio_client, monkeypatch):
        self._enable_minio(monkeypatch)
        monkeypatch.setattr(settings, "user_skill_max_file_bytes", 8)
        r = client.post(
            "/api/skills/user",
            data={"skill_name": "ok-name"},
            files=[("files", ("SKILL.md", b"x" * 100, "text/markdown"))],
        )
        assert r.status_code == 400

    def test_accepts_valid_named_skill(self, client, mock_minio_client, monkeypatch):
        self._enable_minio(monkeypatch)
        mock_minio_client.put_object.return_value = None
        r = client.post(
            "/api/skills/user",
            data={"skill_name": "my-cool-skill"},
            files=[
                ("files", ("SKILL.md", b"# My Cool Skill\n", "text/markdown")),
                ("files", ("skill.yaml", b"description: cool", "application/yaml")),
            ],
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["skill_name"] == "my-cool-skill"
        assert "SKILL.md" in body["files"]


class TestUploadDirectoryStructure:
    """A skill is a directory tree; upload must preserve subdirs (not flatten)."""

    def test_preserves_nested_paths(self, client, mock_minio_client, monkeypatch):
        monkeypatch.setattr(settings, "artifact_storage", "minio")
        mock_minio_client.put_object.return_value = None
        r = client.post(
            "/api/skills/user",
            data={"skill_name": "my-skill"},
            files=[
                ("files", ("SKILL.md", b"# x", "text/markdown")),
                ("files", ("scripts/run.py", b"print(1)", "text/x-python")),
                ("files", ("schemas/in.json", b"{}", "application/json")),
            ],
        )
        assert r.status_code == 200, r.text
        keys = [call.args[1] for call in mock_minio_client.put_object.call_args_list]
        assert "user-skills/user_demo_owner/my-skill/SKILL.md" in keys
        assert "user-skills/user_demo_owner/my-skill/scripts/run.py" in keys
        assert "user-skills/user_demo_owner/my-skill/schemas/in.json" in keys
        # the response reports the preserved relative paths
        assert set(r.json()["files"]) == {"SKILL.md", "scripts/run.py", "schemas/in.json"}

    def test_normalises_backslash_paths(self, client, mock_minio_client, monkeypatch):
        monkeypatch.setattr(settings, "artifact_storage", "minio")
        mock_minio_client.put_object.return_value = None
        r = client.post(
            "/api/skills/user",
            data={"skill_name": "my-skill"},
            files=[
                ("files", ("SKILL.md", b"# x", "text/markdown")),
                ("files", ("scripts\\run.py", b"print(1)", "text/x-python")),
            ],
        )
        assert r.status_code == 200
        keys = [call.args[1] for call in mock_minio_client.put_object.call_args_list]
        assert "user-skills/user_demo_owner/my-skill/scripts/run.py" in keys

    def test_rejects_traversal_in_file_path(self, client, mock_minio_client, monkeypatch):
        """A ``..`` segment in any file path aborts atomically — nothing is written."""
        monkeypatch.setattr(settings, "artifact_storage", "minio")
        r = client.post(
            "/api/skills/user",
            data={"skill_name": "my-skill"},
            files=[
                ("files", ("SKILL.md", b"# x", "text/markdown")),
                ("files", ("../../etc/evil", b"pwned", "text/plain")),
            ],
        )
        assert r.status_code == 400
        mock_minio_client.put_object.assert_not_called()

    def test_rejects_absolute_file_path(self, client, mock_minio_client, monkeypatch):
        monkeypatch.setattr(settings, "artifact_storage", "minio")
        r = client.post(
            "/api/skills/user",
            data={"skill_name": "my-skill"},
            files=[
                ("files", ("SKILL.md", b"# x", "text/markdown")),
                ("files", ("/etc/passwd", b"x", "text/plain")),
            ],
        )
        assert r.status_code == 400
        mock_minio_client.put_object.assert_not_called()


class TestReuploadCleanup:
    """Re-uploading a skill with the same name must wipe the old prefix, so a script the
    user removed in the new upload stops being distributed/executed."""

    def test_reupload_removes_stale_files(self, client, mock_minio_client, monkeypatch):
        monkeypatch.setattr(settings, "artifact_storage", "minio")
        # Objects left by the previous upload of the same skill name.
        mock_minio_client.list_objects.return_value = [
            _minio_obj("user-skills/user_demo_owner/my-skill/SKILL.md"),
            _minio_obj("user-skills/user_demo_owner/my-skill/scripts/old.py"),
        ]
        mock_minio_client.put_object.return_value = None
        r = client.post(
            "/api/skills/user",
            data={"skill_name": "my-skill"},
            files=[
                ("files", ("SKILL.md", b"# x", "text/markdown")),
                ("files", ("scripts/new.py", b"print(2)", "text/x-python")),
            ],
        )
        assert r.status_code == 200, r.text
        # the whole old prefix (incl. the now-deleted old.py) is removed before the new write
        removed = [c.args[1] for c in mock_minio_client.remove_object.call_args_list]
        assert "user-skills/user_demo_owner/my-skill/scripts/old.py" in removed
        put_keys = [c.args[1] for c in mock_minio_client.put_object.call_args_list]
        assert "user-skills/user_demo_owner/my-skill/scripts/new.py" in put_keys
        assert not any("scripts/old.py" in k for k in put_keys)

    def test_first_upload_cleans_nothing(self, client, mock_minio_client, monkeypatch):
        """When no prior objects exist, re-upload cleanup removes nothing and upload proceeds."""
        monkeypatch.setattr(settings, "artifact_storage", "minio")
        mock_minio_client.list_objects.return_value = []
        mock_minio_client.put_object.return_value = None
        r = client.post(
            "/api/skills/user",
            data={"skill_name": "fresh-skill"},
            files=[("files", ("SKILL.md", b"# x", "text/markdown"))],
        )
        assert r.status_code == 200, r.text
        mock_minio_client.remove_object.assert_not_called()
        mock_minio_client.put_object.assert_called_once()


# ---------------------------------------------------------------------------
# Auto-populate helper
# ---------------------------------------------------------------------------


class TestAutoPopulate:
    def test_list_user_skill_names_returns_owner_skills(self, mock_minio_client, monkeypatch):
        monkeypatch.setattr(settings, "artifact_storage", "minio")
        mock_minio_client.list_objects.return_value = [
            _minio_obj("user-skills/ownerA/foo/SKILL.md"),
            _minio_obj("user-skills/ownerA/bar/SKILL.md"),
        ]
        from app.modules.user_skills_service import list_user_skill_names

        assert set(list_user_skill_names(OWNER)) == {"foo", "bar"}

    def test_list_user_skill_names_empty_when_minio_off(self, monkeypatch):
        monkeypatch.setattr(settings, "artifact_storage", "local")
        from app.modules.user_skills_service import list_user_skill_names

        assert list_user_skill_names(OWNER) == []

    def test_resolve_owner_user_skills_reads_task_owner(self, monkeypatch):
        with patch("app.modules.user_skills_service.list_user_skill_names", return_value=["foo"]):
            from app.execution.agent_service import _resolve_owner_user_skills

            assert _resolve_owner_user_skills({"ownerId": OWNER}) == ["foo"]

    def test_resolve_owner_user_skills_no_owner(self, monkeypatch):
        from app.execution.agent_service import _resolve_owner_user_skills

        assert _resolve_owner_user_skills({}) == []

    def test_resolve_owner_user_skills_swallow_errors(self, monkeypatch):
        with patch("app.modules.user_skills_service.list_user_skill_names", side_effect=RuntimeError("boom")):
            from app.execution.agent_service import _resolve_owner_user_skills

            assert _resolve_owner_user_skills({"ownerId": OWNER}) == []


# ---------------------------------------------------------------------------
# owner_id propagation: chat → ExecutionRequest → ContainerRuntime env
# ---------------------------------------------------------------------------


class _CapturingRuntime:
    """Fake ExecutionRuntime that records the request submitted/streamed to it."""

    def __init__(self):
        self.captured = None

    async def submit(self, request):
        self.captured = request
        from app.execution.runtime_boundary import ExecutionResult

        return ExecutionResult(reply="ok", session_id=None)

    async def submit_stream(self, request):
        self.captured = request
        yield {"type": "done", "sessionId": None}


class TestOwnerPropagationToRuntime:
    """``_run_claude_chat``/``_stream_claude_chat`` must set ``owner_id`` on the real
    turn's ExecutionRequest — it is the only link that makes ContainerRuntime build the
    user-skills manifest and the worker download them. Regression for the Phase-1 gap
    where owner_id rode only on ensure_task_session's throwaway probe, so user skills
    were never delivered in the normal path.
    """

    def _minio_settings(self):
        return MagicMock(
            claude_permission_mode="bypassPermissions",
            artifact_storage="minio",
            artifact_presign_ttl_seconds=3600,
            worker_callback_url="http://api:8000",
            worker_auth_token="tok",
            mmp_mcp_url=None,
        )

    async def _capture_request(self, *, stream: bool):
        from app.execution import agent_service as svc

        rt = _CapturingRuntime()
        with patch.object(svc, "ensure_task_session", return_value=None), patch(
            "app.modules.user_skills_service.list_user_skill_names",
            return_value=["my-skill"],
        ), patch.object(svc, "get_execution_runtime", return_value=rt):
            if stream:
                async for _ in svc._stream_claude_chat(
                    {"ownerId": OWNER, "status": "running"}, "hi", "t1",
                ):
                    pass
            else:
                await svc._run_claude_chat(
                    {"ownerId": OWNER, "status": "running"}, "hi", "t1",
                )
        return rt.captured

    async def test_run_claude_chat_sets_owner_and_skill_names(self):
        request = await self._capture_request(stream=False)
        assert request.owner_id == OWNER
        assert request.user_skill_names == ["my-skill"]

    async def test_stream_claude_chat_sets_owner_and_skill_names(self):
        request = await self._capture_request(stream=True)
        assert request.owner_id == OWNER
        assert request.user_skill_names == ["my-skill"]

    async def test_chat_request_becomes_manifest_env(self):
        """End-to-end: the request the chat builds → ContainerRuntime env carries the manifest."""
        request = await self._capture_request(stream=False)
        import app.execution.container_runtime as cr_mod
        from app.execution.container_runtime import _serialise_env

        with patch.object(cr_mod, "settings", self._minio_settings()), patch(
            "app.modules.user_skills_service.build_presigned_skills_manifest",
            return_value=[{"name": "my-skill", "files": [{"path": "SKILL.md", "url": "http://mock/x"}]}],
        ):
            env = _serialise_env(request)
        assert env["OPENAIR_OWNER_ID"] == OWNER
        assert "OPENAIR_USER_SKILLS_MANIFEST" in env


# ---------------------------------------------------------------------------
# Worker env secret isolation
# ---------------------------------------------------------------------------


class TestWorkerEnvSecretIsolation:
    def _request(self):
        from app.execution.runtime_boundary import ExecutionRequest

        return ExecutionRequest(
            task_id="t1",
            message="m",
            mode="execution",
            task={"taskId": "t1"},
            owner_id=OWNER,
            user_skill_names=["my-skill"],
        )

    def _mock_settings(self, *, artifact_storage: str) -> MagicMock:
        """Hermetic settings mock — independent of whatever global state prior tests left."""
        return MagicMock(
            claude_permission_mode="bypassPermissions",
            artifact_storage=artifact_storage,
            minio_access_key="root-ak",
            minio_secret_key="root-sk",
            minio_endpoint="minio:9000",
            minio_bucket="b",
            minio_secure=False,
            minio_worker_endpoint="",
            worker_callback_url="http://api:8000",
            worker_auth_token="tok",
            artifact_presign_ttl_seconds=3600,
        )

    def test_env_has_manifest_and_no_minio_creds(self):
        import app.execution.container_runtime as cr_mod
        from app.execution.container_runtime import _serialise_env

        with patch.object(cr_mod, "settings", self._mock_settings(artifact_storage="minio")), patch(
            "app.modules.user_skills_service.build_presigned_skills_manifest",
            return_value=[{"name": "my-skill", "files": [{"path": "SKILL.md", "url": "http://mock/x"}]}],
        ):
            env = _serialise_env(self._request())
        assert "OPENAIR_USER_SKILLS_MANIFEST" in env
        assert "OPENAIR_MINIO_ACCESS_KEY" not in env
        assert "OPENAIR_MINIO_SECRET_KEY" not in env
        assert "OPENAIR_USER_SKILL_NAMES" not in env

    def test_env_no_manifest_when_minio_off(self):
        import app.execution.container_runtime as cr_mod
        from app.execution.container_runtime import _serialise_env

        with patch.object(cr_mod, "settings", self._mock_settings(artifact_storage="local")):
            env = _serialise_env(self._request())
        assert "OPENAIR_USER_SKILLS_MANIFEST" not in env
        assert "OPENAIR_MINIO_ACCESS_KEY" not in env

    def test_env_skips_manifest_when_builder_overflows(self):
        """When the manifest builder raises on byte/file overflow, _serialise_env must
        NOT bake the env var (graceful degradation) — the turn proceeds without user
        skills instead of shipping an oversized env var or crashing."""
        import app.execution.container_runtime as cr_mod
        from app.execution.container_runtime import _serialise_env

        with patch.object(cr_mod, "settings", self._mock_settings(artifact_storage="minio")), patch(
            "app.modules.user_skills_service.build_presigned_skills_manifest",
            side_effect=ValueError("user skills manifest size 999999 bytes exceeds cap 32768"),
        ):
            env = _serialise_env(self._request())
        assert "OPENAIR_USER_SKILLS_MANIFEST" not in env
        # owner_id still propagates so the rest of the turn works
        assert env["OPENAIR_OWNER_ID"] == OWNER


class TestSystemSkillDescriptions:
    """System skills must surface a real description, not an empty value."""

    def test_descriptions_sourced_from_skill_md(self):
        import app.modules.user_skills_service as mod

        mod._registry_cache = None  # bust cache → fresh read incl. SKILL.md frontmatter
        skills = mod.load_system_skills_registry()
        assert len(skills) > 0
        # at least some skills carry a real description now (registry.yaml has none)
        assert any(s.get("description") for s in skills), "no description sourced from SKILL.md"
        # a known well-described skill carries its actual frontmatter description
        lit = next((s for s in skills if s["skill_id"] == "literature_review"), None)
        assert lit is not None
        assert "文献" in lit["description"]
