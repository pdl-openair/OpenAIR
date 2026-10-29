from __future__ import annotations

import os
import shutil
from pathlib import Path
from unittest.mock import patch
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from jose import jwt

from app.auth import create_access_token, hash_password, verify_password
from app.modules.json_user_repository import DEV_USER_ID, DEV_WORKSPACE_ID, JsonUserRepository
from app.modules.user_dev_seed import DEV_EMAIL, DEV_PASSWORD, DEV_USERNAME, seed_dev_user
from app.modules.user_repository import UserRepository, get_user_repository


_REAL_REPO_ROOT = Path(__file__).resolve().parents[3]


def _seed_repo_scaffold(repo_root: Path) -> None:
    """Mirror read-only schemas/ + skills/ into the isolated tmp repo root.

    app.main's import-time agent-registry validation reads the durable-skill
    catalog from <repo_root>/skills, so the tmp repo root must carry both trees
    (schemas/ for the agent registry, skills/ for durable-skill contracts).
    """
    for sub in ("schemas", "skills"):
        src = _REAL_REPO_ROOT / sub
        if src.is_dir():
            shutil.copytree(src, repo_root / sub, dirs_exist_ok=True)


@pytest.fixture()
def json_repo(tmp_path: Path) -> JsonUserRepository:
    repo = JsonUserRepository(
        users_dir=tmp_path / "data" / "users",
        workspaces_dir=tmp_path / "data" / "workspaces",
    )
    repo.bootstrap()
    return repo


def test_bootstrap_creates_directories(json_repo: JsonUserRepository, tmp_path: Path) -> None:
    assert (tmp_path / "data" / "users").is_dir()
    assert (tmp_path / "data" / "workspaces").is_dir()


def test_seed_dev_user_uses_stable_ids(json_repo: JsonUserRepository) -> None:
    assert seed_dev_user(json_repo) is True
    assert seed_dev_user(json_repo) is False

    user = json_repo.get_user_by_email(DEV_EMAIL)
    assert user is not None
    assert user["user_id"] == DEV_USER_ID

    workspaces = json_repo.get_user_workspaces(DEV_USER_ID)
    assert len(workspaces) == 1
    assert workspaces[0]["workspace_id"] == DEV_WORKSPACE_ID
    assert user["is_active"] is True


def test_password_hash_roundtrip(json_repo: JsonUserRepository) -> None:
    hashed = hash_password(DEV_PASSWORD)
    json_repo.create_user(
        email=DEV_EMAIL,
        username=DEV_USERNAME,
        hashed_password=hashed,
        display_name="Dev",
    )
    user = json_repo.get_user_by_email(DEV_EMAIL)
    assert user is not None
    assert verify_password(DEV_PASSWORD, user["hashed_password"])
    assert user["is_active"] is False


def test_get_user_repository_selects_json_backend(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # This is the first test to point OPENAIR_REPO_ROOT at a tmp dir; importing
    # app.config here transitively imports agent_registry, whose REGISTRY_PATH is
    # a module-level constant frozen at first import. Seed schemas/+skills/ so
    # the frozen path resolves and later app.main imports stay valid.
    _seed_repo_scaffold(tmp_path)
    monkeypatch.setenv("OPENAIR_REPO_ROOT", str(tmp_path))

    import app.config as cfg_mod
    import app.modules.user_repository as user_repo_mod

    monkeypatch.setattr(cfg_mod.settings, "persistence_backend", "json")
    user_repo_mod.settings = cfg_mod.settings
    repo = get_user_repository()
    assert isinstance(repo, JsonUserRepository)


def test_get_user_repository_selects_postgres_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.config as cfg_mod
    import app.modules.user_repository as user_repo_mod

    monkeypatch.setattr(cfg_mod.settings, "persistence_backend", "postgres")
    user_repo_mod.settings = cfg_mod.settings
    repo = get_user_repository()
    assert isinstance(repo, UserRepository)


def test_get_user_repository_rejects_json_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.config as cfg_mod
    import app.modules.user_repository as user_repo_mod

    monkeypatch.setattr(cfg_mod.settings, "persistence_backend", "json")
    monkeypatch.setattr(cfg_mod.settings, "env", "production")
    user_repo_mod.settings = cfg_mod.settings
    with pytest.raises(RuntimeError, match="not supported in production"):
        get_user_repository()


def test_auth_login_and_me_with_json_persistence(tmp_path: Path) -> None:
    _seed_repo_scaffold(tmp_path)
    env = {
        "OPENAIR_ENV": "test",
        "OPENAIR_RUNTIME_TYPE": "inline",
        "SKILL_EXEC_MODE": "replay",
        "OPENAIR_AUTH_REQUIRED": "0",
        "OPENAIR_DEV_SEED_FIXTURES": "0",
        "OPENAIR_PERSISTENCE_BACKEND": "json",
        "OPENAIR_REPO_ROOT": str(tmp_path),
        "OPENAIR_ARTIFACT_STORAGE": "local",
    }
    with patch.dict(os.environ, env, clear=False):
        import app.config as cfg_mod

        cfg_mod.settings = cfg_mod.Settings()
        from app.main import app

        with TestClient(app, raise_server_exceptions=False) as client:
            login = client.post(
                "/api/auth/login",
                json={"email": DEV_EMAIL, "password": DEV_PASSWORD},
            )
            assert login.status_code == 200, login.text
            body = login.json()
            assert body.get("access_token")
            assert body["user"]["userId"] == DEV_USER_ID

            me = client.get(
                "/api/auth/me",
                headers={"Authorization": f"Bearer {body['access_token']}"},
            )
            assert me.status_code == 200, me.text
            assert me.json()["userId"] == DEV_USER_ID


def test_dev_login_me_uses_jwt_identity_in_open_mode(tmp_path: Path) -> None:
    _seed_repo_scaffold(tmp_path)
    env = {
        "OPENAIR_ENV": "test",
        "OPENAIR_RUNTIME_TYPE": "inline",
        "SKILL_EXEC_MODE": "replay",
        "OPENAIR_AUTH_REQUIRED": "0",
        "OPENAIR_DEV_SEED_FIXTURES": "0",
        "OPENAIR_PERSISTENCE_BACKEND": "json",
        "OPENAIR_REPO_ROOT": str(tmp_path),
        "OPENAIR_ARTIFACT_STORAGE": "local",
    }
    with patch.dict(os.environ, env, clear=False):
        import app.config as cfg_mod

        cfg_mod.settings = cfg_mod.Settings()
        from app.main import app

        with TestClient(app, raise_server_exceptions=False) as client:
            login = client.post(
                "/api/auth/login",
                json={"email": DEV_EMAIL, "password": DEV_PASSWORD},
            )
            assert login.status_code == 200, login.text
            body = login.json()
            token = body["access_token"]
            payload = jwt.get_unverified_claims(token)
            assert body["user"]["userId"] == DEV_USER_ID
            assert body["user"]["defaultWorkspaceId"] == DEV_WORKSPACE_ID
            assert payload["sub"] == DEV_USER_ID
            assert payload["workspaceId"] == DEV_WORKSPACE_ID

            me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
            assert me.status_code == 200, me.text
            assert me.json()["userId"] == DEV_USER_ID
            assert me.json()["defaultWorkspaceId"] == DEV_WORKSPACE_ID


def test_open_mode_without_jwt_keeps_default_identity_for_legacy_dev_calls(tmp_path: Path) -> None:
    _seed_repo_scaffold(tmp_path)
    env = {
        "OPENAIR_ENV": "test",
        "OPENAIR_RUNTIME_TYPE": "inline",
        "SKILL_EXEC_MODE": "replay",
        "OPENAIR_AUTH_REQUIRED": "0",
        "OPENAIR_DEV_SEED_FIXTURES": "0",
        "OPENAIR_PERSISTENCE_BACKEND": "json",
        "OPENAIR_REPO_ROOT": str(tmp_path),
        "OPENAIR_ARTIFACT_STORAGE": "local",
    }
    with patch.dict(os.environ, env, clear=False):
        import app.config as cfg_mod

        cfg_mod.settings = cfg_mod.Settings()
        from app.main import app

        with TestClient(app, raise_server_exceptions=False) as client:
            created = client.post("/api/tasks", json={"title": "legacy open task"})
            assert created.status_code == 201, created.text
            task = created.json()
            assert task["ownerId"] == "user_demo_owner"
            assert task["workspaceId"] == "ws_demo_default"


def test_open_mode_invalid_jwt_is_rejected_without_clearing_to_demo_identity(tmp_path: Path) -> None:
    _seed_repo_scaffold(tmp_path)
    env = {
        "OPENAIR_ENV": "test",
        "OPENAIR_RUNTIME_TYPE": "inline",
        "SKILL_EXEC_MODE": "replay",
        "OPENAIR_AUTH_REQUIRED": "0",
        "OPENAIR_DEV_SEED_FIXTURES": "0",
        "OPENAIR_PERSISTENCE_BACKEND": "json",
        "OPENAIR_REPO_ROOT": str(tmp_path),
        "OPENAIR_ARTIFACT_STORAGE": "local",
    }
    with patch.dict(os.environ, env, clear=False):
        import app.config as cfg_mod

        cfg_mod.settings = cfg_mod.Settings()
        from app.main import app

        with TestClient(app, raise_server_exceptions=False) as client:
            me = client.get("/api/auth/me", headers={"Authorization": "Bearer not-a-jwt"})
            assert me.status_code == 401, me.text
            assert me.json()["detail"] == "invalid or expired token"


def test_expired_jwt_is_rejected(tmp_path: Path) -> None:
    _seed_repo_scaffold(tmp_path)
    env = {
        "OPENAIR_ENV": "test",
        "OPENAIR_RUNTIME_TYPE": "inline",
        "SKILL_EXEC_MODE": "replay",
        "OPENAIR_AUTH_REQUIRED": "0",
        "OPENAIR_DEV_SEED_FIXTURES": "0",
        "OPENAIR_PERSISTENCE_BACKEND": "json",
        "OPENAIR_REPO_ROOT": str(tmp_path),
        "OPENAIR_ARTIFACT_STORAGE": "local",
    }
    with patch.dict(os.environ, env, clear=False):
        import app.config as cfg_mod

        cfg_mod.settings = cfg_mod.Settings()
        from app.main import app

        with TestClient(app, raise_server_exceptions=False) as client:
            user = get_user_repository().get_user_by_email(DEV_EMAIL)
            assert user is not None
            token = create_access_token(
                user["user_id"],
                workspace_id=DEV_WORKSPACE_ID,
                expires_delta=timedelta(seconds=-1),
            )
            me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
            assert me.status_code == 401, me.text
            assert me.json()["detail"] == "invalid or expired token"


def test_jwt_for_missing_user_is_rejected(tmp_path: Path) -> None:
    _seed_repo_scaffold(tmp_path)
    env = {
        "OPENAIR_ENV": "test",
        "OPENAIR_RUNTIME_TYPE": "inline",
        "SKILL_EXEC_MODE": "replay",
        "OPENAIR_AUTH_REQUIRED": "0",
        "OPENAIR_DEV_SEED_FIXTURES": "0",
        "OPENAIR_PERSISTENCE_BACKEND": "json",
        "OPENAIR_REPO_ROOT": str(tmp_path),
        "OPENAIR_ARTIFACT_STORAGE": "local",
    }
    with patch.dict(os.environ, env, clear=False):
        import app.config as cfg_mod

        cfg_mod.settings = cfg_mod.Settings()
        from app.main import app

        with TestClient(app, raise_server_exceptions=False) as client:
            token = create_access_token("u_missing_subject", workspace_id=DEV_WORKSPACE_ID)
            me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
            assert me.status_code == 401, me.text
            assert me.json()["detail"] == "user not found or inactive"


def test_jwt_workspace_claim_cannot_select_another_users_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_repo_scaffold(tmp_path)
    env = {
        "OPENAIR_ENV": "test",
        "OPENAIR_RUNTIME_TYPE": "inline",
        "SKILL_EXEC_MODE": "replay",
        "OPENAIR_AUTH_REQUIRED": "0",
        "OPENAIR_DEV_SEED_FIXTURES": "0",
        "OPENAIR_PERSISTENCE_BACKEND": "json",
        "OPENAIR_REPO_ROOT": str(tmp_path),
        "OPENAIR_ARTIFACT_STORAGE": "local",
    }
    with patch.dict(os.environ, env, clear=False):
        import app.config as cfg_mod
        import app.modules.repo_paths as repo_paths

        cfg_mod.settings = cfg_mod.Settings()
        monkeypatch.setattr(repo_paths, "_repo_root_cache", tmp_path)
        from app.main import app

        with TestClient(app, raise_server_exceptions=False) as client:
            repo = get_user_repository()
            other_user = repo.create_user(
                email="other@example.test",
                username="other-user",
                hashed_password=hash_password("other-password"),
                display_name="Other",
            )
            other_workspace = repo.create_workspace(
                name="Other workspace",
                owner_id=other_user["user_id"],
                is_personal=True,
            )
            token = create_access_token(
                DEV_USER_ID,
                workspace_id=other_workspace["workspace_id"],
            )

            me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
            assert me.status_code == 200, me.text
            assert me.json()["userId"] == DEV_USER_ID
            assert me.json()["defaultWorkspaceId"] == DEV_WORKSPACE_ID

            overridden = client.get(
                "/api/auth/me",
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-OpenAIR-Workspace-Id": other_workspace["workspace_id"],
                },
            )
            assert overridden.status_code == 200, overridden.text
            assert overridden.json()["userId"] == DEV_USER_ID
            assert overridden.json()["defaultWorkspaceId"] == DEV_WORKSPACE_ID
