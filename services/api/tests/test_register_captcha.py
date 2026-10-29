"""Self-hosted image captcha on POST /api/auth/register."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.modules.register_captcha import (
    captcha_enabled,
    create_challenge,
    issue_public_captcha,
    reset_store,
    verify_captcha,
)

_REAL_REPO_ROOT = Path(__file__).resolve().parents[3]


def _settings(**overrides: object):
    from types import SimpleNamespace

    base = {"captcha_enabled": False}
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.fixture(autouse=True)
def _clear_captcha_store() -> None:
    reset_store()
    yield
    reset_store()


def test_captcha_disabled_skips_verify(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.config as cfg_mod

    monkeypatch.setattr(cfg_mod, "settings", _settings(captcha_enabled=False))
    assert captcha_enabled() is False
    assert issue_public_captcha() == {"enabled": False, "captchaId": None, "image": None}
    verify_captcha(None, None)


def test_captcha_required_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.config as cfg_mod
    from fastapi import HTTPException

    monkeypatch.setattr(cfg_mod, "settings", _settings(captcha_enabled=True))
    with pytest.raises(HTTPException) as exc:
        verify_captcha("  ", "")
    assert exc.value.status_code == 400
    assert exc.value.detail == "captcha_required"


def test_wrong_code_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.config as cfg_mod
    import app.modules.register_captcha as captcha_mod
    from fastapi import HTTPException

    monkeypatch.setattr(cfg_mod, "settings", _settings(captcha_enabled=True))
    monkeypatch.setattr(captcha_mod, "_random_code", lambda: "AB3D")
    captcha_id, png = create_challenge()
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    with pytest.raises(HTTPException) as exc:
        verify_captcha(captcha_id, "XXXX")
    assert exc.value.detail == "captcha_failed"
    with pytest.raises(HTTPException) as exc2:
        verify_captcha(captcha_id, "AB3D")
    assert exc2.value.detail == "captcha_failed"


def test_correct_code_is_consumed_once(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.config as cfg_mod
    import app.modules.register_captcha as captcha_mod
    from fastapi import HTTPException

    monkeypatch.setattr(cfg_mod, "settings", _settings(captcha_enabled=True))
    monkeypatch.setattr(captcha_mod, "_random_code", lambda: "AB3D")
    captcha_id, _png = create_challenge()
    verify_captcha(captcha_id, "ab3d")
    with pytest.raises(HTTPException) as exc:
        verify_captcha(captcha_id, "AB3D")
    assert exc.value.detail == "captcha_failed"


@pytest.fixture()
def client_with_captcha(tmp_path: Path) -> TestClient:
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
        "OPENAIR_CAPTCHA_ENABLED": "1",
    }
    with patch.dict(os.environ, env, clear=False):
        import app.config as cfg_mod

        fresh = cfg_mod.Settings()
        fresh.captcha_enabled = True
        with patch.object(cfg_mod, "settings", fresh):
            from app.main import app

            with TestClient(app, raise_server_exceptions=True) as c:
                yield c


def test_captcha_endpoint_returns_image(client_with_captcha: TestClient) -> None:
    resp = client_with_captcha.get("/api/auth/captcha")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["enabled"] is True
    assert body["captchaId"]
    assert str(body["image"]).startswith("data:image/png;base64,")


def test_register_without_captcha_is_rejected(client_with_captcha: TestClient) -> None:
    resp = client_with_captcha.post(
        "/api/auth/register",
        json={
            "email": "bot@example.com",
            "username": "bot_user",
            "password": "secret123",
        },
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "captcha_required"


def test_register_with_valid_captcha_succeeds(
    client_with_captcha: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.modules.register_captcha as captcha_mod

    monkeypatch.setattr(captcha_mod, "_random_code", lambda: "AB3D")
    issued = client_with_captcha.get("/api/auth/captcha").json()
    resp = client_with_captcha.post(
        "/api/auth/register",
        json={
            "email": "human@example.com",
            "username": "human_user",
            "password": "secret123",
            "captchaId": issued["captchaId"],
            "captchaCode": "AB3D",
        },
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["pendingActivation"] is True


def test_failed_captcha_does_not_create_user(
    client_with_captcha: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.modules.register_captcha as captcha_mod

    monkeypatch.setattr(captcha_mod, "_random_code", lambda: "AB3D")
    issued = client_with_captcha.get("/api/auth/captcha").json()
    failed = client_with_captcha.post(
        "/api/auth/register",
        json={
            "email": "retry@example.com",
            "username": "retry_user",
            "password": "secret123",
            "captchaId": issued["captchaId"],
            "captchaCode": "XXXX",
        },
    )
    assert failed.status_code == 400
    assert failed.json()["detail"] == "captcha_failed"

    issued2 = client_with_captcha.get("/api/auth/captcha").json()
    created = client_with_captcha.post(
        "/api/auth/register",
        json={
            "email": "retry@example.com",
            "username": "retry_user",
            "password": "secret123",
            "captchaId": issued2["captchaId"],
            "captchaCode": "AB3D",
        },
    )
    assert created.status_code == 201, created.text
