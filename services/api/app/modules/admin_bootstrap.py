"""Ensure a configured admin account exists (production-safe, env-driven).

Unlike the local-only seed (`admin@openair.dev` / `admin123456`), this path
runs in every environment when `OPENAIR_BOOTSTRAP_ADMIN_EMAIL` and
`OPENAIR_BOOTSTRAP_ADMIN_PASSWORD` are both set. Existing rows are promoted
and activated; the password is only overwritten when
`OPENAIR_BOOTSTRAP_ADMIN_SYNC_PASSWORD=1`.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from app import config as app_config
from app.auth import hash_password
from app.modules.user_repository import UserRepositoryBackend

logger = logging.getLogger(__name__)

_USERNAME_SAFE = re.compile(r"[^a-zA-Z0-9_]+")


def _desired_username(email: str) -> str:
    configured = (app_config.settings.bootstrap_admin_username or "").strip()
    if configured:
        return configured[:50]
    local = email.split("@", 1)[0]
    cleaned = _USERNAME_SAFE.sub("", local).strip("_")[:24]
    return cleaned or "admin"


def _allocate_username(user_repo: UserRepositoryBackend, desired: str, owner_email: str) -> str:
    candidate = desired
    suffix = 1
    while True:
        existing = user_repo.get_user_by_username(candidate)
        if existing is None or existing.get("email") == owner_email:
            return candidate
        candidate = f"{desired[:20]}_{suffix}"
        suffix += 1
        if suffix > 1000:
            raise RuntimeError("unable to allocate unique bootstrap admin username")


def ensure_bootstrap_admin(user_repo: UserRepositoryBackend) -> dict[str, Any] | None:
    """Create or promote the env-configured admin. Returns the user row, or None if skipped."""
    email = (app_config.settings.bootstrap_admin_email or "").strip().lower()
    password = app_config.settings.bootstrap_admin_password or ""
    if not email and not password:
        return None
    if not email or not password:
        logger.warning(
            "bootstrap admin skipped: set both OPENAIR_BOOTSTRAP_ADMIN_EMAIL and "
            "OPENAIR_BOOTSTRAP_ADMIN_PASSWORD"
        )
        return None
    if len(password) < 6:
        logger.warning("bootstrap admin skipped: password must be at least 6 characters")
        return None

    username = _allocate_username(user_repo, _desired_username(email), email)
    existing = user_repo.get_user_by_email(email)
    if existing is None:
        user = user_repo.create_user(
            email=email,
            username=username,
            hashed_password=hash_password(password),
            display_name=username,
            is_active=True,
        )
        user_repo.create_workspace(
            name=f"{username}的 workspace",
            owner_id=user["user_id"],
            is_personal=True,
        )
        logger.info("bootstrap admin created: %s", email)
    else:
        user = existing
        if app_config.settings.bootstrap_admin_sync_password:
            user_repo.update_password(user["user_id"], hash_password(password))
        if not user.get("is_active", True):
            user_repo.set_is_active(user["user_id"], True)
        workspaces = user_repo.get_user_workspaces(user["user_id"])
        if not workspaces:
            user_repo.create_workspace(
                name=f"{username}的 workspace",
                owner_id=user["user_id"],
                is_personal=True,
            )
        logger.info("bootstrap admin ensured: %s", email)

    updated = user_repo.update_user_quota_fields(user["user_id"], role="admin", quota_tier="admin")
    return updated or user_repo.get_user_by_id(user["user_id"])
