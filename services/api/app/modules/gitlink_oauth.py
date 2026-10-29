"""GitLink OAuth authorization-code helpers.

Endpoints follow GitLink's OAuth2 wiki (GitHub-compatible style):
https://gitlink.org.cn/gitlink/forgeplus/wiki?wiki=Oauth2%E6%8E%A5%E5%85%A5%E8%AF%B4%E6%98%8E
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlencode

import httpx

from app.config import settings
from app.modules.oauth_common import OAuthProfile, make_oauth_state, normalize_email, verify_oauth_state

logger = logging.getLogger(__name__)

PROVIDER = "gitlink"
DEFAULT_AUTHORIZE_URL = "https://www.gitlink.org.cn/oauth/authorize"
DEFAULT_TOKEN_URL = "https://www.gitlink.org.cn/oauth/token"
DEFAULT_USERINFO_URL = "https://www.gitlink.org.cn/api/users/me.json"
USER_AGENT = "OpenAIR-OAuth"

__all__ = [
    "PROVIDER",
    "OAuthProfile",
    "gitlink_oauth_configured",
    "gitlink_redirect_uri",
    "make_oauth_state",
    "verify_oauth_state",
    "build_authorize_url",
    "exchange_code_for_token",
    "fetch_gitlink_profile",
]


def gitlink_oauth_configured() -> bool:
    return bool(settings.oauth_gitlink_client_id and settings.oauth_gitlink_client_secret)


def gitlink_redirect_uri() -> str:
    return (
        settings.oauth_gitlink_redirect_uri
        or "http://localhost:8000/api/auth/oauth/gitlink/callback"
    )


def _authorize_url() -> str:
    return (settings.oauth_gitlink_authorize_url or DEFAULT_AUTHORIZE_URL).strip()


def _token_url() -> str:
    return (settings.oauth_gitlink_token_url or DEFAULT_TOKEN_URL).strip()


def _userinfo_url() -> str:
    return (settings.oauth_gitlink_userinfo_url or DEFAULT_USERINFO_URL).strip()


def build_authorize_url(state: str) -> str:
    params: dict[str, str] = {
        "client_id": settings.oauth_gitlink_client_id,
        "redirect_uri": gitlink_redirect_uri(),
        "response_type": "code",
        "state": state,
    }
    scopes = (settings.oauth_gitlink_scopes or "").strip()
    if scopes:
        params["scope"] = scopes
    base = _authorize_url()
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}{urlencode(params)}"


def exchange_code_for_token(code: str) -> str:
    """Exchange authorization code for access token.

    Wiki: POST /oauth/token with client_secret in the body; set User-Agent
    (missing UA may yield HTTP 403).
    """
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": settings.oauth_gitlink_client_id,
        "client_secret": settings.oauth_gitlink_client_secret,
        "redirect_uri": gitlink_redirect_uri(),
    }
    with httpx.Client(timeout=20.0) as client:
        response = client.post(
            _token_url(),
            headers={
                "Accept": "application/json",
                "User-Agent": USER_AGENT,
            },
            data=data,
        )
        response.raise_for_status()
        # Some gateways return HTML error pages with 200; require JSON object.
        try:
            body = response.json()
        except Exception as exc:
            raise RuntimeError("gitlink token response is not JSON") from exc

    if not isinstance(body, dict):
        raise RuntimeError("gitlink token response is not an object")

    token = str(body.get("access_token") or "").strip()
    if not token:
        error = body.get("error_description") or body.get("error") or "token exchange failed"
        raise RuntimeError(str(error))
    return token


def fetch_gitlink_profile(access_token: str) -> OAuthProfile:
    """Load the authenticated user (default: /api/users/me.json)."""
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
    }
    with httpx.Client(timeout=20.0) as client:
        user_resp = client.get(_userinfo_url(), headers=headers)
        if user_resp.status_code in (401, 403):
            # Fallback: some installs accept `token` scheme (GitHub/Gitea style).
            headers["Authorization"] = f"token {access_token}"
            user_resp = client.get(_userinfo_url(), headers=headers)
        user_resp.raise_for_status()
        payload: Any = user_resp.json()

    if not isinstance(payload, dict):
        raise RuntimeError("gitlink userinfo is not an object")

    # Reject explicit API error envelopes (status != 0 / message-only).
    status_code = payload.get("status")
    if status_code not in (None, 0, "0", 200, "200") and "id" not in payload and "user_id" not in payload:
        nested = payload.get("data") or payload.get("user")
        if not isinstance(nested, dict):
            msg = payload.get("message") or payload.get("error") or "userinfo failed"
            raise RuntimeError(str(msg))

    data = payload
    for nest_key in ("user", "data", "account"):
        nested = payload.get(nest_key)
        if isinstance(nested, dict):
            data = nested
            break

    provider_user_id = _first_str(data, ("user_id", "id", "uid", "openid"))
    login = _first_str(data, ("login", "username", "name", "account"))
    if not provider_user_id or not login:
        raise RuntimeError("gitlink user profile missing id/login")

    email = normalize_email(_first_str(data, ("email", "mail")))
    display_name = _first_str(data, ("display_name", "displayName", "nickname", "name", "real_name"))
    if display_name == login:
        alt = _first_str(data, ("nickname", "real_name", "realName"))
        display_name = alt or display_name

    return OAuthProfile(
        provider_user_id=provider_user_id,
        login=login,
        email=email,
        display_name=display_name or login,
        avatar_url=_first_str(data, ("avatar_url", "avatar", "image")) or None,
    )


def _first_str(data: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        val = data.get(key)
        if val is None:
            continue
        text = str(val).strip()
        if text:
            return text
    return None
