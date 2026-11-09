"""First-user bootstrap: with zero active admins, the first registrant self-activates.

A fresh self-hosted deployment that skipped OPENAIR_BOOTSTRAP_ADMIN_* would
otherwise be permanently locked out: registrations stay inactive and no admin
exists to activate them.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from app.routes import auth as auth_routes


class FakeRepo:
    def __init__(self, *, has_admin: bool) -> None:
        self.has_admin = has_admin
        self.user: dict = {
            "user_id": "u-first",
            "email": "first@example.com",
            "username": "first_user",
            "role": "user",
            "is_active": False,
        }

    def get_user_by_email(self, email):
        return None

    def get_user_by_username(self, username):
        return None

    def create_user(self, **kwargs):
        self.user.update(kwargs)
        return dict(self.user)

    def create_workspace(self, **kwargs):
        return {"workspace_id": "ws-1", **kwargs}

    def has_active_admin(self) -> bool:
        return self.has_admin

    def set_is_active(self, user_id, is_active):
        assert user_id == "u-first"
        self.user["is_active"] = is_active
        return dict(self.user)

    def update_user_quota_fields(self, user_id, **fields):
        assert user_id == "u-first"
        self.user.update(fields)
        return dict(self.user)


def _register(repo) -> object:
    body = SimpleNamespace(
        email="first@example.com",
        username="first_user",
        password="secret123",
        display_name="First",
        captchaId=None,
        captchaCode=None,
    )
    request = SimpleNamespace(headers={}, client=SimpleNamespace(host="", headers={}))
    with patch.object(auth_routes, "verify_captcha", lambda *a, **k: None), patch.object(
        auth_routes, "record_audit", lambda *a, **k: None
    ):
        return auth_routes.register(body, request, repo)


def test_first_registrant_promoted_when_no_active_admin():
    repo = FakeRepo(has_admin=False)
    response = _register(repo)
    assert response.pendingActivation is False
    assert "首个管理员" in response.message
    assert repo.user["is_active"] is True
    assert repo.user["role"] == "admin"


def test_registration_stays_pending_when_admin_exists():
    repo = FakeRepo(has_admin=True)
    response = _register(repo)
    assert response.pendingActivation is True
    assert "待管理员激活" in response.message
    assert repo.user["is_active"] is False
    assert repo.user["role"] == "user"
