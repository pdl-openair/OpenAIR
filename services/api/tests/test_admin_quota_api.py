"""Tests for the admin quota API (S03-14-04), JSON persistence + TestClient."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

# Real repo root (services/api/tests/test_*.py → parents[3] = repo root) so we
# can copy the read-only schemas/ tree into the tmp repo root; the agent registry
# is loaded at module-import time against repo_root and must resolve.
_REAL_REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    # The agent registry + pack configs are read from <repo_root>/schemas; copy
    # them into the isolated tmp repo root so app.main imports cleanly.
    src_schemas = _REAL_REPO_ROOT / "schemas"
    if src_schemas.is_dir():
        shutil.copytree(src_schemas, tmp_path / "schemas", dirs_exist_ok=True)
    # The agent-registry validation (run at app.main import time) also reads the
    # durable-skill catalog from <repo_root>/skills, so mirror that tree too.
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

        # Use patch.object so the settings swap is restored after the fixture;
        # a bare `cfg_mod.settings = ...` leaks the replacement into later
        # tests and breaks their own settings monkeypatches.
        with patch.object(cfg_mod, "settings", cfg_mod.Settings()):
            from app.main import app

            with TestClient(app, raise_server_exceptions=True) as c:
                yield c


def _login(client: TestClient, email: str, password: str) -> str:
    resp = client.post("/api/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def test_admin_can_list_users_and_view_quota(client: TestClient) -> None:
    admin_token = _login(client, "admin@openair.dev", "admin123456")
    headers = {"Authorization": f"Bearer {admin_token}"}

    users = client.get("/api/admin/users", headers=headers)
    assert users.status_code == 200, users.text
    emails = {u["email"] for u in users.json()}
    assert "dev@openair.dev" in emails
    assert "admin@openair.dev" in emails

    dev_user_id = next(u["userId"] for u in users.json() if u["email"] == "dev@openair.dev")
    detail = client.get(f"/api/admin/users/{dev_user_id}/quota", headers=headers)
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["user"]["quota"]["tier"] == "trial"
    assert "usageByModel" in body
    assert "recentUsage" in body
    assert body["timeseriesDaily"]["granularity"] == "day"
    assert body["timeseriesWeekly"]["granularity"] == "week"
    assert body["timeseriesMonthly"]["granularity"] == "month"
    assert isinstance(body["timeseriesDaily"]["points"], list)


def test_non_admin_is_forbidden(client: TestClient) -> None:
    dev_token = _login(client, "dev@openair.dev", "dev123456")
    resp = client.get("/api/admin/users", headers={"Authorization": f"Bearer {dev_token}"})
    assert resp.status_code == 403


def test_admin_bonus_adjustment_changes_remaining_and_audits(client: TestClient) -> None:
    admin_token = _login(client, "admin@openair.dev", "admin123456")
    headers = {"Authorization": f"Bearer {admin_token}", "Content-Type": "application/json"}

    users = client.get("/api/admin/users", headers=headers).json()
    dev_user_id = next(u["userId"] for u in users if u["email"] == "dev@openair.dev")
    before = client.get(f"/api/admin/users/{dev_user_id}/quota", headers=headers).json()
    bonus_before = before["user"]["quota"]["bonusQuota"]

    adj = client.post(
        f"/api/admin/users/{dev_user_id}/quota/adjustments",
        headers=headers,
        json={"type": "bonus_quota", "delta": 100000, "reason": "试用支持"},
    )
    assert adj.status_code == 201, adj.text
    body = adj.json()
    assert body["adjustType"] == "bonus_quota"
    assert body["delta"] == 100000
    assert body["reason"] == "试用支持"

    after = client.get(f"/api/admin/users/{dev_user_id}/quota", headers=headers).json()
    assert after["user"]["quota"]["bonusQuota"] == bonus_before + 100000

    records = client.get(f"/api/admin/users/{dev_user_id}/quota/adjustments", headers=headers).json()
    assert any(r["adjustType"] == "bonus_quota" and r["delta"] == 100000 for r in records)


def test_tier_change_audits_old_and_new(client: TestClient) -> None:
    admin_token = _login(client, "admin@openair.dev", "admin123456")
    headers = {"Authorization": f"Bearer {admin_token}", "Content-Type": "application/json"}

    users = client.get("/api/admin/users", headers=headers).json()
    dev_user_id = next(u["userId"] for u in users if u["email"] == "dev@openair.dev")

    adj = client.post(
        f"/api/admin/users/{dev_user_id}/quota/adjustments",
        headers=headers,
        json={"type": "tier_change", "newTier": "pro", "reason": "升级专业"},
    )
    assert adj.status_code == 201, adj.text
    body = adj.json()
    assert body["oldTier"] == "trial"
    assert body["newTier"] == "pro"

    detail = client.get(f"/api/admin/users/{dev_user_id}/quota", headers=headers).json()
    assert detail["user"]["quota"]["tier"] == "pro"


def test_admin_cannot_demote_self(client: TestClient) -> None:
    admin_token = _login(client, "admin@openair.dev", "admin123456")
    headers = {"Authorization": f"Bearer {admin_token}", "Content-Type": "application/json"}

    users = client.get("/api/admin/users", headers=headers).json()
    admin_user_id = next(u["userId"] for u in users if u["email"] == "admin@openair.dev")

    adj = client.post(
        f"/api/admin/users/{admin_user_id}/quota/adjustments",
        headers=headers,
        json={"type": "tier_change", "newTier": "pro", "reason": "self-demote"},
    )
    assert adj.status_code == 403


def test_invalid_adjustment_inputs_rejected(client: TestClient) -> None:
    admin_token = _login(client, "admin@openair.dev", "admin123456")
    headers = {"Authorization": f"Bearer {admin_token}", "Content-Type": "application/json"}
    users = client.get("/api/admin/users", headers=headers).json()
    dev_user_id = next(u["userId"] for u in users if u["email"] == "dev@openair.dev")

    # empty reason → 422
    assert client.post(
        f"/api/admin/users/{dev_user_id}/quota/adjustments",
        headers=headers,
        json={"type": "bonus_quota", "delta": 10, "reason": ""},
    ).status_code == 422
    # non-positive delta → 422
    assert client.post(
        f"/api/admin/users/{dev_user_id}/quota/adjustments",
        headers=headers,
        json={"type": "bonus_quota", "delta": 0, "reason": "x"},
    ).status_code == 422
    # invalid tier → 422
    assert client.post(
        f"/api/admin/users/{dev_user_id}/quota/adjustments",
        headers=headers,
        json={"type": "tier_change", "newTier": "platinum", "reason": "x"},
    ).status_code == 422


def test_admin_can_list_quota_tier_catalog(client: TestClient) -> None:
    from app.config import settings

    admin_token = _login(client, "admin@openair.dev", "admin123456")
    headers = {"Authorization": f"Bearer {admin_token}"}
    resp = client.get("/api/admin/quota/tiers", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    by_tier = {row["tier"]: row for row in body}
    assert by_tier["trial"]["periodType"] == "permanent"
    assert by_tier["trial"]["periodLimit"] == settings.quota_trial_limit
    assert by_tier["free"]["periodType"] == "daily"
    assert by_tier["free"]["periodLimit"] == settings.quota_free_limit
    assert by_tier["admin"]["periodType"] == "unlimited"
    assert by_tier["admin"]["periodLimit"] is None

    # Non-admin cannot read the catalog.
    dev_token = _login(client, "dev@openair.dev", "dev123456")
    forbidden = client.get("/api/admin/quota/tiers", headers={"Authorization": f"Bearer {dev_token}"})
    assert forbidden.status_code == 403
