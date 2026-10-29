"""Env-driven admin bootstrap (production first admin, no SQL)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.auth import verify_password
from app.modules.admin_bootstrap import ensure_bootstrap_admin
from app.modules.json_user_repository import JsonUserRepository


@pytest.fixture()
def repo(tmp_path: Path) -> JsonUserRepository:
    store = JsonUserRepository(
        users_dir=tmp_path / "users",
        workspaces_dir=tmp_path / "workspaces",
    )
    store.bootstrap()
    return store


def _settings(**overrides: object) -> SimpleNamespace:
    base = {
        "bootstrap_admin_email": "",
        "bootstrap_admin_password": "",
        "bootstrap_admin_username": "",
        "bootstrap_admin_sync_password": False,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_bootstrap_skipped_when_unset(repo: JsonUserRepository, monkeypatch: pytest.MonkeyPatch) -> None:
    import app.config as cfg_mod

    monkeypatch.setattr(cfg_mod, "settings", _settings())
    assert ensure_bootstrap_admin(repo) is None
    assert repo.list_users() == []


def test_bootstrap_creates_active_admin(repo: JsonUserRepository, monkeypatch: pytest.MonkeyPatch) -> None:
    import app.config as cfg_mod

    monkeypatch.setattr(
        cfg_mod,
        "settings",
        _settings(
            bootstrap_admin_email="Ops@Example.COM",
            bootstrap_admin_password="strong-pass-1",
            bootstrap_admin_username="ops",
        ),
    )
    user = ensure_bootstrap_admin(repo)
    assert user is not None
    assert user["email"] == "ops@example.com"
    assert user["username"] == "ops"
    assert user["is_active"] is True
    assert user["role"] == "admin"
    assert user["quota_tier"] == "admin"
    assert verify_password("strong-pass-1", user["hashed_password"])
    assert repo.get_user_workspaces(user["user_id"])


def test_bootstrap_promotes_pending_user_without_resetting_password(
    repo: JsonUserRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.config as cfg_mod
    from app.auth import hash_password

    pending = repo.create_user(
        email="ops@example.com",
        username="ops",
        hashed_password=hash_password("original-secret"),
        display_name="ops",
        is_active=False,
    )
    monkeypatch.setattr(
        cfg_mod,
        "settings",
        _settings(
            bootstrap_admin_email="ops@example.com",
            bootstrap_admin_password="new-secret-should-not-apply",
            bootstrap_admin_username="ops",
            bootstrap_admin_sync_password=False,
        ),
    )
    user = ensure_bootstrap_admin(repo)
    assert user is not None
    assert user["user_id"] == pending["user_id"]
    assert user["is_active"] is True
    assert user["role"] == "admin"
    assert verify_password("original-secret", user["hashed_password"])


def test_bootstrap_sync_password_overwrites(
    repo: JsonUserRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.config as cfg_mod
    from app.auth import hash_password

    repo.create_user(
        email="ops@example.com",
        username="ops",
        hashed_password=hash_password("old-secret"),
        display_name="ops",
        is_active=True,
    )
    monkeypatch.setattr(
        cfg_mod,
        "settings",
        _settings(
            bootstrap_admin_email="ops@example.com",
            bootstrap_admin_password="rotated-secret",
            bootstrap_admin_sync_password=True,
        ),
    )
    user = ensure_bootstrap_admin(repo)
    assert user is not None
    assert verify_password("rotated-secret", user["hashed_password"])


def test_production_rejects_weak_bootstrap_password() -> None:
    from app.config import Settings

    settings = Settings()
    settings.bootstrap_admin_email = "ops@example.com"
    settings.bootstrap_admin_password = "admin123456"
    errors = settings.production_secrets_errors()
    assert any("BOOTSTRAP_ADMIN_PASSWORD" in err for err in errors)
