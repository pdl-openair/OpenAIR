"""GitHub OAuth is product-paused; public entry points must stay off."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.routes import oauth as oauth_routes


def test_github_oauth_status_reports_temporarily_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(oauth_routes, "GITHUB_OAUTH_TEMPORARILY_UNAVAILABLE", True)
    monkeypatch.setattr(oauth_routes.settings, "oauth_github_enabled", True)
    monkeypatch.setattr(oauth_routes.github_oauth, "github_oauth_configured", lambda: True)

    client = TestClient(app)
    response = client.get("/api/auth/oauth/github/status")
    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is False
    assert body["temporarilyUnavailable"] is True
    assert body["provider"] == "github"


def test_github_oauth_start_redirects_to_login_while_paused(monkeypatch) -> None:
    monkeypatch.setattr(oauth_routes, "GITHUB_OAUTH_TEMPORARILY_UNAVAILABLE", True)
    monkeypatch.setattr(oauth_routes.settings, "oauth_github_enabled", True)
    monkeypatch.setattr(oauth_routes.github_oauth, "github_oauth_configured", lambda: True)
    monkeypatch.setattr(
        oauth_routes.settings,
        "oauth_success_redirect_url",
        "http://localhost:8080/auth/oauth/callback",
    )

    client = TestClient(app)
    response = client.get("/api/auth/oauth/github/start", follow_redirects=False)
    assert response.status_code in {302, 307}
    location = response.headers["location"]
    assert "/login" in location
    assert "oauth_error=not_configured" in location
