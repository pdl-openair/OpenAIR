"""OpenI (Gitea-based) OAuth authorization-code helpers."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlencode

import httpx

from app.config import settings
from app.modules.oauth_common import OAuthProfile, make_oauth_state, normalize_email, verify_oauth_state

logger = logging.getLogger(__name__)

PROVIDER = "openi"

__all__ = [
    "PROVIDER",
    "OAuthProfile",
    "openi_oauth_configured",
    "openi_base_url",
    "openi_redirect_uri",
    "make_oauth_state",
    "verify_oauth_state",
    "build_authorize_url",
    "exchange_code_for_token",
    "fetch_openi_profile",
]


def openi_base_url() -> str:
    return (settings.oauth_openi_base_url or "https://openi.pcl.ac.cn").rstrip("/")


def openi_oauth_configured() -> bool:
    return bool(settings.oauth_openi_client_id and settings.oauth_openi_client_secret)


def openi_redirect_uri() -> str:
    return (
        settings.oauth_openi_redirect_uri
        or "http://localhost:8000/api/auth/oauth/openi/callback"
    )


def build_authorize_url(state: str) -> str:
    params: dict[str, str] = {
        "client_id": settings.oauth_openi_client_id,
        "redirect_uri": openi_redirect_uri(),
        "response_type": "code",
        "state": state,
    }
    scopes = (settings.oauth_openi_scopes or "").strip()
    if scopes:
        params["scope"] = scopes
    return f"{openi_base_url()}/login/oauth/authorize?{urlencode(params)}"


def exchange_code_for_token(code: str) -> str:
    """Exchange authorization code for access token (Gitea OAuth2)."""
    payload = {
        "client_id": settings.oauth_openi_client_id,
        "client_secret": settings.oauth_openi_client_secret,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": openi_redirect_uri(),
    }
    with httpx.Client(timeout=20.0) as client:
        response = client.post(
            f"{openi_base_url()}/login/oauth/access_token",
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            json=payload,
        )
        # Some Gitea builds prefer form-urlencoded; retry once on client/media errors.
        if response.status_code >= 400:
            response = client.post(
                f"{openi_base_url()}/login/oauth/access_token",
                headers={"Accept": "application/json"},
                data=payload,
            )
        response.raise_for_status()
        body = response.json()
    token = str(body.get("access_token") or "").strip()
    if not token:
        error = body.get("error_description") or body.get("error") or "token exchange failed"
        raise RuntimeError(str(error))
    return token


def fetch_openi_profile(access_token: str) -> OAuthProfile:
    """Load the authenticated user via Gitea `/api/v1/user`."""
    url = f"{openi_base_url()}/api/v1/user"
    with httpx.Client(timeout=20.0) as client:
        # OpenI/Gitea accept Bearer for OAuth tokens; fall back to `token` scheme.
        user_resp = client.get(
            url,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
                "User-Agent": "OpenAIR-OAuth",
            },
        )
        if user_resp.status_code in (401, 403):
            user_resp = client.get(
                url,
                headers={
                    "Authorization": f"token {access_token}",
                    "Accept": "application/json",
                    "User-Agent": "OpenAIR-OAuth",
                },
            )
        user_resp.raise_for_status()
        user: dict[str, Any] = user_resp.json()

    provider_user_id = str(user.get("id") or "").strip()
    login = str(user.get("login") or "").strip()
    if not provider_user_id or not login:
        raise RuntimeError("openi user profile missing id/login")

    full_name = str(user.get("full_name") or "").strip() or None
    return OAuthProfile(
        provider_user_id=provider_user_id,
        login=login,
        email=normalize_email(user.get("email")),
        display_name=full_name or login,
        avatar_url=str(user.get("avatar_url") or "").strip() or None,
    )
