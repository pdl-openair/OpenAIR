"""Development seed: create a test user + personal workspace for quick local login."""

from __future__ import annotations

import logging

from app.auth import hash_password
from app.modules.user_repository import UserRepositoryBackend

logger = logging.getLogger(__name__)

DEV_EMAIL = "dev@openair.dev"
DEV_USERNAME = "dev"
DEV_PASSWORD = "dev123456"
DEV_DISPLAY_NAME = "开发测试用户"

ADMIN_EMAIL = "admin@openair.dev"
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "admin123456"
ADMIN_DISPLAY_NAME = "开发管理员"


def seed_dev_user(user_repo: UserRepositoryBackend) -> bool:
    """Create the development test user if it doesn't exist yet.

    Returns ``True`` if a new user was created.
    """
    existing = user_repo.get_user_by_email(DEV_EMAIL)
    if existing:
        return False

    user = user_repo.create_user(
        email=DEV_EMAIL,
        username=DEV_USERNAME,
        hashed_password=hash_password(DEV_PASSWORD),
        display_name=DEV_DISPLAY_NAME,
        is_active=True,
    )
    user_repo.create_workspace(
        name=f"{DEV_USERNAME}的 workspace",
        owner_id=user["user_id"],
        is_personal=True,
    )
    logger.info("dev seed user created: %s", DEV_EMAIL)
    return True


def seed_admin_user(user_repo: UserRepositoryBackend) -> bool:
    """Create the development admin user (role=admin, unlimited quota tier).

    Sprint-03 quota: admin role is required to exercise the adjustment/audit
    APIs locally. Returns ``True`` if a new user was created.
    """
    existing = user_repo.get_user_by_email(ADMIN_EMAIL)
    if existing:
        return False

    user = user_repo.create_user(
        email=ADMIN_EMAIL,
        username=ADMIN_USERNAME,
        hashed_password=hash_password(ADMIN_PASSWORD),
        display_name=ADMIN_DISPLAY_NAME,
        is_active=True,
    )
    user_repo.update_user_quota_fields(user["user_id"], role="admin", quota_tier="admin")
    user_repo.create_workspace(
        name=f"{ADMIN_USERNAME}的 workspace",
        owner_id=user["user_id"],
        is_personal=True,
    )
    logger.info("dev seed admin user created: %s", ADMIN_EMAIL)
    return True
