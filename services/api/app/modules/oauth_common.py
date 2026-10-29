"""Shared helpers for additive OAuth providers (GitHub, OpenI, GitLink, …)."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass

from app.config import settings

STATE_TTL_SECONDS = 600


@dataclass(frozen=True)
class OAuthProfile:
    provider_user_id: str
    login: str
    email: str | None
    display_name: str | None
    avatar_url: str | None


def _state_secret() -> bytes:
    raw = settings.jwt_secret_key or "openair-dev-oauth"
    return raw.encode("utf-8")


def make_oauth_state() -> str:
    exp = int(time.time()) + STATE_TTL_SECONDS
    nonce = secrets.token_urlsafe(16)
    payload = f"{exp}.{nonce}"
    sig = hmac.new(_state_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def verify_oauth_state(state: str) -> bool:
    try:
        exp_s, nonce, sig = state.split(".", 2)
        exp = int(exp_s)
    except ValueError:
        return False
    if exp < int(time.time()):
        return False
    if not nonce:
        return False
    payload = f"{exp_s}.{nonce}"
    expected = hmac.new(_state_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig)


def normalize_email(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    email = value.strip().lower()
    return email or None
