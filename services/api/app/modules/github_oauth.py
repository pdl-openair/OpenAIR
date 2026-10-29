"""GitHub OAuth authorization-code helpers."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlencode

import httpx

from app.config import settings
from app.modules.oauth_common import OAuthProfile, make_oauth_state, normalize_email, verify_oauth_state

logger = logging.getLogger(__name__)

PROVIDER = "github"
AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
TOKEN_URL = "https://github.com/login/oauth/access_token"
USER_URL = "https://api.github.com/user"
EMAILS_URL = "https://api.github.com/user/emails"
DEFAULT_SCOPES = "read:user user:email"

# Re-export state helpers so existing imports keep working.
__all__ = [
    "PROVIDER",
    "OAuthProfile",
    "GitHubProfile",
    "github_oauth_configured",
    "github_redirect_uri",
    "make_oauth_state",
    "verify_oauth_state",
    "build_authorize_url",
    "exchange_code_for_token",
    "fetch_github_profile",
]

GitHubProfile = OAuthProfile


def github_oauth_configured() -> bool:
    return bool(settings.oauth_github_client_id and settings.oauth_github_client_secret)


def github_redirect_uri() -> str:
    return (
        settings.oauth_github_redirect_uri
        or "http://localhost:8000/api/auth/oauth/github/callback"
    )


def build_authorize_url(state: str) -> str:
    query = urlencode(
        {
            "client_id": settings.oauth_github_client_id,
            "redirect_uri": github_redirect_uri(),
            "scope": settings.oauth_github_scopes or DEFAULT_SCOPES,
            "state": state,
            "allow_signup": "true",
        }
    )
    return f"{AUTHORIZE_URL}?{query}"


def exchange_code_for_token(code: str) -> str:
    with httpx.Client(timeout=20.0) as client:
        response = client.post(
            TOKEN_URL,
            headers={"Accept": "application/json"},
            data={
                "client_id": settings.oauth_github_client_id,
                "client_secret": settings.oauth_github_client_secret,
                "code": code,
                "redirect_uri": github_redirect_uri(),
            },
        )
        response.raise_for_status()
        payload = response.json()
    token = str(payload.get("access_token") or "").strip()
    if not token:
        error = payload.get("error_description") or payload.get("error") or "token exchange failed"
        raise RuntimeError(str(error))
    return token


def fetch_github_profile(access_token: str) -> OAuthProfile:
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "OpenAIR-OAuth",
    }
    with httpx.Client(timeout=20.0) as client:
        user_resp = client.get(USER_URL, headers=headers)
        user_resp.raise_for_status()
        user: dict[str, Any] = user_resp.json()

        email = normalize_email(user.get("email"))
        if not email:
            emails_resp = client.get(EMAILS_URL, headers=headers)
            if emails_resp.status_code == 200:
                email = _pick_primary_email(emails_resp.json())

    provider_user_id = str(user.get("id") or "").strip()
    login = str(user.get("login") or "").strip()
    if not provider_user_id or not login:
        raise RuntimeError("github user profile missing id/login")

    return OAuthProfile(
        provider_user_id=provider_user_id,
        login=login,
        email=email,
        display_name=(str(user.get("name")).strip() if user.get("name") else None) or login,
        avatar_url=str(user.get("avatar_url") or "").strip() or None,
    )


def _pick_primary_email(rows: Any) -> str | None:
    if not isinstance(rows, list):
        return None
    primary: str | None = None
    verified: str | None = None
    for row in rows:
        if not isinstance(row, dict):
            continue
        email = normalize_email(row.get("email"))
        if not email:
            continue
        if row.get("primary") and row.get("verified"):
            return email
        if row.get("primary") and not primary:
            primary = email
        if row.get("verified") and not verified:
            verified = email
    return primary or verified
