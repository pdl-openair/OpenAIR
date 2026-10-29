"""S04-04: public registration stays inactive until an admin activates the account."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

_REAL_REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    src_schemas = _REAL_REPO_ROOT / "schemas"
    if src_schemas.is_dir():
        shutil.copytree(src_schemas, tmp_path / "schemas", dirs_exist_ok=True)
    src_skills = _REAL_REPO_ROOT / "skills"
    if src_skills.is_dir():
        shutil.copytree(src_skills, tmp_path / "skills", dirs_exist_ok=True)

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

        with patch.object(cfg_mod, "settings", cfg_mod.Settings()):
            from app.main import app

            with TestClient(app, raise_server_exceptions=True) as c:
                yield c


def _login(client: TestClient, email: str, password: str) -> TestClient:
    return client.post("/api/auth/login", json={"email": email, "password": password})


def _admin_headers(client: TestClient) -> dict[str, str]:
    resp = _login(client, "admin@openair.dev", "admin123456")
    assert resp.status_code == 200, resp.text
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def test_register_creates_inactive_account_without_token(client: TestClient) -> None:
    resp = client.post(
        "/api/auth/register",
        json={
            "email": "pending@example.com",
            "username": "pending_user",
            "password": "secret123",
            "display_name": "Pending",
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["pendingActivation"] is True
    assert "access_token" not in body
    assert body["user"]["email"] == "pending@example.com"
    assert body["user"]["userId"]

    login = _login(client, "pending@example.com", "secret123")
    assert login.status_code == 403
    assert login.json()["detail"] == "account is disabled"


def test_admin_activate_then_login_and_quota_assignment(client: TestClient) -> None:
    created = client.post(
        "/api/auth/register",
        json={
            "email": "waitlist@example.com",
            "username": "waitlist_user",
            "password": "secret123",
        },
    )
    assert created.status_code == 201, created.text
    user_id = created.json()["user"]["userId"]
    headers = _admin_headers(client)

    blocked = client.post(
        f"/api/admin/users/{user_id}/quota/adjustments",
        headers=headers,
        json={"type": "bonus_quota", "delta": 1000, "reason": "too early"},
    )
    assert blocked.status_code == 409
    assert "激活" in blocked.json()["detail"]

    accounts = client.get("/api/admin/accounts", headers=headers)
    assert accounts.status_code == 200, accounts.text
    row = next(u for u in accounts.json() if u["userId"] == user_id)
    assert row["isActive"] is False

    quota_before = client.get(f"/api/admin/users/{user_id}/quota", headers=headers)
    assert quota_before.status_code == 200, quota_before.text
    assert quota_before.json()["user"]["quota"]["remaining"] == 0

    activated = client.post(
        f"/api/admin/accounts/{user_id}/active",
        headers=headers,
        json={"isActive": True, "reason": "admin activate"},
    )
    assert activated.status_code == 200, activated.text
    assert activated.json()["isActive"] is True

    login = _login(client, "waitlist@example.com", "secret123")
    assert login.status_code == 200, login.text
    assert login.json()["access_token"]

    quota_after = client.get(f"/api/admin/users/{user_id}/quota", headers=headers)
    assert quota_after.status_code == 200, quota_after.text
    remaining = quota_after.json()["user"]["quota"]["remaining"]
    assert remaining is not None and remaining > 0

    adj = client.post(
        f"/api/admin/users/{user_id}/quota/adjustments",
        headers=headers,
        json={"type": "bonus_quota", "delta": 5000, "reason": "onboard remaining"},
    )
    assert adj.status_code == 201, adj.text
