"""Authentication: JWT access tokens + bcrypt password hashing.

Phase-1 legacy (static Bearer Token) is preserved when
``OPENAIR_AUTH_REQUIRED=0`` (development mode).

When ``OPENAIR_AUTH_REQUIRED=1``, every protected endpoint validates a JWT
and resolves the caller's ownerId / workspaceId from the database.
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, TypedDict

import bcrypt
from fastapi import Depends, Header, HTTPException, Query, status
from jose import JWTError, jwt

from app.config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Password hashing (bcrypt directly, avoids passlib compatibility issues)
# ---------------------------------------------------------------------------


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str | None) -> bool:
    if not hashed_password:
        return False
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------------------
# JWT helpers
# ---------------------------------------------------------------------------

ALGORITHM = "HS256"


def _get_jwt_secret() -> str:
    """Return the JWT signing key.

    - If ``OPENAIR_JWT_SECRET_KEY`` is set → use it (production).
    - Otherwise → generate and persist to ``.dev_jwt_secret`` in the API dir
      so tokens survive server restarts in development.
    """
    key = settings.jwt_secret_key
    if key:
        return key
    # Lazy-init a persistent dev key
    global _dev_jwt_secret  # noqa: PLW0603
    if not _dev_jwt_secret:
        _dev_jwt_secret = _load_or_create_dev_secret()
    return _dev_jwt_secret


_dev_jwt_secret: str = ""


def _load_or_create_dev_secret() -> str:
    """Load dev JWT secret from file, or create one and persist it."""
    from pathlib import Path

    secret_file = Path(__file__).resolve().parent.parent / ".dev_jwt_secret"
    if secret_file.is_file():
        secret = secret_file.read_text(encoding="utf-8").strip()
        if secret:
            logger.info("loaded dev JWT secret from %s", secret_file)
            return secret
    secret = secrets.token_urlsafe(32)
    try:
        secret_file.write_text(secret, encoding="utf-8")
        logger.info("generated and saved dev JWT secret to %s", secret_file)
    except OSError:
        logger.warning("could not persist dev JWT secret — tokens will not survive restart")
    return secret


def create_access_token(
    user_id: str,
    workspace_id: str | None = None,
    expires_delta: timedelta | None = None,
) -> str:
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.jwt_access_token_expire_minutes)
    )
    payload = {
        "sub": user_id,
        "exp": expire,
        "iat": datetime.now(timezone.utc),
    }
    if workspace_id:
        payload["workspaceId"] = workspace_id
    return jwt.encode(payload, _get_jwt_secret(), algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any] | None:
    """Decode and validate a JWT. Returns payload dict or ``None``."""
    try:
        payload: dict[str, Any] = jwt.decode(
            token, _get_jwt_secret(), algorithms=[ALGORITHM]
        )
        return payload
    except JWTError:
        return None


# ---------------------------------------------------------------------------
# Request identity (unchanged interface)
# ---------------------------------------------------------------------------


class RequestIdentity(TypedDict):
    ownerId: str
    workspaceId: str
    createdBy: str


def resolve_request_identity(
    authorization: str | None = Header(default=None),
    x_openair_owner_id: str | None = Header(default=None, alias="X-OpenAIR-Owner-Id"),
    x_openair_workspace_id: str | None = Header(default=None, alias="X-OpenAIR-Workspace-Id"),
    x_openair_created_by: str | None = Header(default=None, alias="X-OpenAIR-Created-By"),
) -> RequestIdentity:
    """FastAPI dependency — resolve the caller's identity from the request.

    * Development mode (``OPENAIR_AUTH_REQUIRED=0``):
      No auth required; returns defaults from config (unchanged behavior).
    * Production mode (``OPENAIR_AUTH_REQUIRED=1``):
      Validates the Bearer JWT, looks up the user in the database, and resolves
      their default workspace (or the one specified via header override).
    """
    bearer_token = _extract_bearer_token(authorization)

    if not settings.auth_required:
        if bearer_token and bearer_token != settings.auth_token:
            payload = decode_access_token(bearer_token)
            if payload and "sub" in payload:
                return _resolve_identity_from_jwt(payload, x_openair_workspace_id)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid or expired token",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return {
            "ownerId": (x_openair_owner_id or settings.default_owner_id).strip() or settings.default_owner_id,
            "workspaceId": (x_openair_workspace_id or settings.default_workspace_id).strip() or settings.default_workspace_id,
            "createdBy": (x_openair_created_by or settings.default_created_by).strip() or settings.default_created_by,
        }

    # --- Authenticate via JWT ---
    if not bearer_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Try JWT validation first (new system)
    payload = decode_access_token(bearer_token)
    if payload and "sub" in payload:
        return _resolve_identity_from_jwt(payload, x_openair_workspace_id)

    # Fallback: legacy static token for migration / admin access.
    # Dev-only (auth_required=0 short-circuits above). In auth_required=1 mode this
    # branch is a full auth bypass that trusts the X-OpenAIR-Owner-Id header, so it
    # must be explicitly enabled via OPENAIR_ALLOW_STATIC_TOKEN_IN_PROD (default off).
    # A publicly known default token must never impersonate arbitrary owners in prod.
    if settings.allow_static_token_in_prod and bearer_token == settings.auth_token:
        return {
            "ownerId": (x_openair_owner_id or settings.default_owner_id).strip() or settings.default_owner_id,
            "workspaceId": (x_openair_workspace_id or settings.default_workspace_id).strip() or settings.default_workspace_id,
            "createdBy": (x_openair_created_by or settings.default_owner_id).strip() or settings.default_owner_id,
        }

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="invalid or expired token",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _extract_bearer_token(authorization: str | None) -> str | None:
    if not authorization or not authorization.startswith("Bearer "):
        return None
    token = authorization[len("Bearer "):].strip()
    return token or None


def require_admin(
    identity: RequestIdentity = Depends(resolve_request_identity),
) -> RequestIdentity:
    """FastAPI dependency: the caller must be role=admin (S03-14-04).

    Unlike the ops-token ``/api/admin/tasks/import`` path, this is a real
    logged-in-user admin check so audit rows record a real operator_id.
    """
    from app.modules.user_repository import get_user_repository

    user = get_user_repository().get_user_by_id(identity["ownerId"])
    if not user or str(user.get("role") or "user") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="需要管理员权限",
        )
    return identity


def _resolve_identity_from_jwt(
    payload: dict[str, Any],
    x_openair_workspace_id: str | None,
) -> RequestIdentity:
    """Resolve RequestIdentity from a validated JWT payload."""
    from app.modules.user_repository import get_user_repository

    user_id: str = payload["sub"]
    user_repo = get_user_repository()
    user = user_repo.get_user_by_id(user_id)
    if not user or not user.get("is_active", True):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="user not found or inactive",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Determine workspace: header override → user's first (personal) workspace
    workspace_id: str | None = None
    requested_workspace_id = x_openair_workspace_id or payload.get("workspaceId")
    if requested_workspace_id:
        # Verify the requested workspace belongs to this user
        ws = user_repo.get_workspace_by_id(str(requested_workspace_id).strip())
        if ws and ws["owner_id"] == user_id:
            workspace_id = str(requested_workspace_id).strip()

    if not workspace_id:
        workspaces = user_repo.get_user_workspaces(user_id)
        if workspaces:
            workspace_id = workspaces[0]["workspace_id"]
        else:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="user has no workspace — please contact admin",
            )

    assert workspace_id  # guaranteed after the guard above
    return {
        "ownerId": user_id,
        "workspaceId": workspace_id,
        "createdBy": user_id,
    }


# ---------------------------------------------------------------------------
# Scope guard (unchanged)
# ---------------------------------------------------------------------------


def ensure_task_in_scope(task: dict[str, Any] | None, identity: RequestIdentity) -> dict[str, Any]:
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="task not found")
    if task.get("workspaceId") != identity["workspaceId"]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="task is outside workspace scope")
    if task.get("ownerId") != identity["ownerId"]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="task is outside owner scope")
    return task


def ensure_artifact_in_scope(
    artifact: dict[str, Any] | None, identity: RequestIdentity
) -> dict[str, Any]:
    """Artifact 继承所属 task 的 owner/workspace；复用同样的 scope 校验。"""
    if not artifact:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="artifact not found")
    if artifact.get("workspaceId") != identity["workspaceId"]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="artifact is outside workspace scope")
    if artifact.get("ownerId") != identity["ownerId"]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="artifact is outside owner scope")
    return artifact


async def resolve_media_identity(
    authorization: str | None = Header(default=None),
    access_token: str | None = Query(default=None, alias="access_token"),
    x_openair_owner_id: str | None = Header(default=None, alias="X-OpenAIR-Owner-Id"),
    x_openair_workspace_id: str | None = Header(default=None, alias="X-OpenAIR-Workspace-Id"),
    x_openair_created_by: str | None = Header(default=None, alias="X-OpenAIR-Created-By"),
) -> RequestIdentity:
    """Identity for browser-native media fetches (``<img>``/``<iframe>``/``<video>``).

    Those requests cannot attach an Authorization header, so the same Bearer
    JWT is accepted via the ``?access_token=`` query parameter.  Never enabled
    for mutation routes; the token is the caller's own short-lived JWT.
    """
    effective_authorization = authorization
    if not _extract_bearer_token(authorization or "") and access_token:
        effective_authorization = f"Bearer {access_token.strip()}"
    return resolve_request_identity(
        authorization=effective_authorization,
        x_openair_owner_id=x_openair_owner_id,
        x_openair_workspace_id=x_openair_workspace_id,
        x_openair_created_by=x_openair_created_by,
    )
