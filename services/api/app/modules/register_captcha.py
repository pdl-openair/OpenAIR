"""Self-hosted image captcha for public registration (Python ``captcha`` library)."""

from __future__ import annotations

import base64
import logging
import secrets
import threading
import time
from dataclasses import dataclass

from fastapi import HTTPException, status

from app import config as app_config

logger = logging.getLogger(__name__)

_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_CODE_LENGTH = 4
_TTL_SECONDS = 300.0
_MAX_PENDING = 5000

_lock = threading.Lock()


@dataclass
class _Entry:
    code: str
    expires_at: float


_store: dict[str, _Entry] = {}


def captcha_enabled() -> bool:
    return bool(getattr(app_config.settings, "captcha_enabled", False))


def reset_store() -> None:
    with _lock:
        _store.clear()


def _random_code() -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(_CODE_LENGTH))


def _prune_unlocked(now: float) -> None:
    expired = [key for key, entry in _store.items() if entry.expires_at <= now]
    for key in expired:
        _store.pop(key, None)
    while len(_store) > _MAX_PENDING:
        oldest = min(_store, key=lambda item: _store[item].expires_at)
        _store.pop(oldest, None)


def create_challenge() -> tuple[str, bytes]:
    from captcha.image import ImageCaptcha

    code = _random_code()
    png = ImageCaptcha(width=160, height=56).generate(code).getvalue()
    captcha_id = secrets.token_urlsafe(16)
    now = time.time()
    with _lock:
        _prune_unlocked(now)
        _store[captcha_id] = _Entry(code=code, expires_at=now + _TTL_SECONDS)
    return captcha_id, png


def issue_public_captcha() -> dict[str, object]:
    if not captcha_enabled():
        return {"enabled": False, "captchaId": None, "image": None}
    try:
        captcha_id, png = create_challenge()
    except Exception:
        logger.warning("image captcha generation failed", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="captcha_unavailable",
        ) from None
    return {
        "enabled": True,
        "captchaId": captcha_id,
        "image": "data:image/png;base64," + base64.b64encode(png).decode("ascii"),
    }


def verify_captcha(captcha_id: str | None, captcha_code: str | None) -> None:
    """No-op when captcha is disabled. Raises HTTPException on failure."""
    if not captcha_enabled():
        return
    challenge_id = (captcha_id or "").strip()
    guess = (captcha_code or "").strip().upper()
    if not challenge_id or not guess:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="captcha_required",
        )
    now = time.time()
    with _lock:
        entry = _store.pop(challenge_id, None)
    if entry is None or entry.expires_at <= now:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="captcha_failed",
        )
    if not secrets.compare_digest(guess, entry.code):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="captcha_failed",
        )
