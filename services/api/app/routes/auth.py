"""Auth routes: register, login, logout, password reset, current-user profile."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.auth import (
    RequestIdentity,
    create_access_token,
    hash_password,
    resolve_request_identity,
    verify_password,
)
from app.config import settings
from app.models.governance_schemas import (
    LogoutResponse,
    PasswordForgotRequest,
    PasswordForgotResponse,
    PasswordResetRequest,
    UserPrefsResponse,
    UserPrefsUpdateRequest,
)
from app.models.user_schemas import (
    AuthTokenResponse,
    LoginRequest,
    RegisterPendingResponse,
    RegisterRequest,
    UserInfoResponse,
)
from app.modules.audit_repository import record_audit
from app.modules.password_reset_repository import get_password_reset_repository
from app.modules.register_captcha import issue_public_captcha, verify_captcha
from app.modules.user_repository import UserRepositoryBackend, get_user_repository

logger = logging.getLogger(__name__)

router = APIRouter(tags=["auth"])


def _user_repo() -> UserRepositoryBackend:
    return get_user_repository()


def _client_meta(request: Request) -> tuple[str | None, str | None]:
    forwarded = request.headers.get("x-forwarded-for")
    ip = forwarded.split(",")[0].strip() if forwarded else (request.client.host if request.client else None)
    return ip, request.headers.get("user-agent")


def _to_user_info(user: dict, default_workspace_id: str) -> UserInfoResponse:
    return UserInfoResponse(
        userId=user["user_id"],
        email=user["email"],
        username=user["username"],
        displayName=user.get("display_name"),
        ownerId=user["user_id"],
        defaultWorkspaceId=default_workspace_id,
        role=user.get("role") or "user",
    )


@router.get("/auth/captcha")
def captcha_challenge() -> dict[str, object]:
    """Issue a one-time image captcha when registration captcha is enabled."""
    return issue_public_captcha()


@router.post("/auth/register", response_model=RegisterPendingResponse, status_code=status.HTTP_201_CREATED)
def register(
    body: RegisterRequest,
    request: Request,
    repo: UserRepositoryBackend = Depends(_user_repo),
) -> RegisterPendingResponse:
    """Create a new user account. It stays inactive until an admin activates it."""
    ip, ua = _client_meta(request)
    verify_captcha(body.captchaId, body.captchaCode)

    if repo.get_user_by_email(body.email):
        record_audit("register", outcome="failure", detail={"reason": "email_taken", "email": body.email}, ip=ip, user_agent=ua)
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="email already registered")
    if repo.get_user_by_username(body.username):
        record_audit("register", outcome="failure", detail={"reason": "username_taken"}, ip=ip, user_agent=ua)
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="username already taken")

    user = repo.create_user(
        email=body.email,
        username=body.username,
        hashed_password=hash_password(body.password),
        display_name=body.display_name or body.username,
        is_active=False,
    )
    ws = repo.create_workspace(
        name=f"{user['username']}的 workspace",
        owner_id=user["user_id"],
        is_personal=True,
    )
    record_audit(
        "register",
        actor_id=user["user_id"],
        outcome="success",
        detail={"pendingActivation": True},
        ip=ip,
        user_agent=ua,
    )
    logger.info("user registered pending activation: %s (%s)", user["username"], user["user_id"])

    return RegisterPendingResponse(
        pendingActivation=True,
        message="账号已创建，待管理员激活后方可登录",
        user=_to_user_info(user, ws["workspace_id"]),
    )


@router.post("/auth/login", response_model=AuthTokenResponse)
def login(
    body: LoginRequest,
    request: Request,
    repo: UserRepositoryBackend = Depends(_user_repo),
) -> AuthTokenResponse:
    """Authenticate with email + password, receive a JWT."""
    ip, ua = _client_meta(request)
    user = repo.get_user_by_email(body.email)
    if not user or not verify_password(body.password, user.get("hashed_password")):
        record_audit(
            "login",
            outcome="failure",
            detail={"reason": "invalid_credentials", "email": body.email},
            ip=ip,
            user_agent=ua,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid email or password",
        )

    if not user.get("is_active", True):
        record_audit(
            "login",
            actor_id=user["user_id"],
            outcome="failure",
            detail={"reason": "disabled"},
            ip=ip,
            user_agent=ua,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="account is disabled",
        )

    workspaces = repo.get_user_workspaces(user["user_id"])
    default_ws_id = workspaces[0]["workspace_id"] if workspaces else ""
    token = create_access_token(user["user_id"], workspace_id=default_ws_id or None)
    record_audit("login", actor_id=user["user_id"], outcome="success", ip=ip, user_agent=ua)
    logger.info("user logged in: %s (%s)", user["username"], user["user_id"])

    return AuthTokenResponse(
        access_token=token,
        user=_to_user_info(user, default_ws_id),
    )


@router.post("/auth/logout", response_model=LogoutResponse)
def logout(
    request: Request,
    identity: RequestIdentity = Depends(resolve_request_identity),
) -> LogoutResponse:
    """Client-side JWT discard + server audit trail (no server-side revoke store)."""
    ip, ua = _client_meta(request)
    record_audit("logout", actor_id=identity["ownerId"], outcome="success", ip=ip, user_agent=ua)
    return LogoutResponse()


@router.post("/auth/password/forgot", response_model=PasswordForgotResponse)
def forgot_password(
    body: PasswordForgotRequest,
    request: Request,
    repo: UserRepositoryBackend = Depends(_user_repo),
) -> PasswordForgotResponse:
    """Request a password-reset token.

    Always returns a generic success message to avoid email enumeration.
    In development, the raw token is returned in ``resetToken`` for local testing.
    """
    ip, ua = _client_meta(request)
    user = repo.get_user_by_email(body.email)
    reset_token: str | None = None
    if user and user.get("is_active", True):
        raw, _row = get_password_reset_repository().create_token(
            user["user_id"],
            ttl_minutes=settings.password_reset_ttl_minutes,
        )
        record_audit(
            "password_forgot",
            actor_id=user["user_id"],
            outcome="success",
            ip=ip,
            user_agent=ua,
        )
        if not settings.is_production:
            reset_token = raw
            logger.info("dev password-reset token for %s: %s", body.email, raw)
        else:
            logger.info("password-reset token issued for user %s (mailer not configured)", user["user_id"])
    else:
        record_audit(
            "password_forgot",
            outcome="success",
            detail={"reason": "unknown_or_inactive", "email": body.email},
            ip=ip,
            user_agent=ua,
        )

    return PasswordForgotResponse(
        message="If the email is registered, a reset token has been issued.",
        resetToken=reset_token,
    )


@router.post("/auth/password/reset", response_model=LogoutResponse)
def reset_password(
    body: PasswordResetRequest,
    request: Request,
    repo: UserRepositoryBackend = Depends(_user_repo),
) -> LogoutResponse:
    """Consume a reset token and set a new password."""
    ip, ua = _client_meta(request)
    row = get_password_reset_repository().consume_token(body.token)
    if not row:
        record_audit("password_reset", outcome="failure", detail={"reason": "invalid_token"}, ip=ip, user_agent=ua)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid or expired reset token")

    user_id = str(row["user_id"])
    updated = repo.update_password(user_id, hash_password(body.newPassword))
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="user not found")
    record_audit("password_reset", actor_id=user_id, outcome="success", ip=ip, user_agent=ua)
    return LogoutResponse()


@router.get("/auth/me", response_model=UserInfoResponse)
def get_current_user(
    identity: RequestIdentity = Depends(resolve_request_identity),
    repo: UserRepositoryBackend = Depends(_user_repo),
) -> UserInfoResponse:
    """Return the authenticated user's profile."""
    user = repo.get_user_by_id(identity["ownerId"])
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="user not found")
    return _to_user_info(user, identity["workspaceId"])


@router.get("/auth/me/prefs", response_model=UserPrefsResponse)
def get_my_prefs(
    identity: RequestIdentity = Depends(resolve_request_identity),
    repo: UserRepositoryBackend = Depends(_user_repo),
) -> UserPrefsResponse:
    user = repo.get_user_by_id(identity["ownerId"])
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="user not found")
    return UserPrefsResponse(
        userId=user["user_id"],
        displayName=user.get("display_name"),
        quotaAlertThresholdPct=int(user.get("quota_alert_threshold_pct") or 80),
        role=str(user.get("role") or "user"),
        isActive=bool(user.get("is_active", True)),
    )


@router.patch("/auth/me/prefs", response_model=UserPrefsResponse)
def update_my_prefs(
    body: UserPrefsUpdateRequest,
    identity: RequestIdentity = Depends(resolve_request_identity),
    repo: UserRepositoryBackend = Depends(_user_repo),
) -> UserPrefsResponse:
    """Self-service prefs only — never tier/bonus (admin-only)."""
    fields: dict = {}
    if body.quotaAlertThresholdPct is not None:
        fields["quota_alert_threshold_pct"] = body.quotaAlertThresholdPct
    if body.displayName is not None:
        fields["display_name"] = body.displayName
    try:
        user = repo.update_user_prefs(identity["ownerId"], **fields)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="user not found")
    record_audit(
        "prefs_update",
        actor_id=identity["ownerId"],
        outcome="success",
        detail={"fields": sorted(fields.keys())},
    )
    return UserPrefsResponse(
        userId=user["user_id"],
        displayName=user.get("display_name"),
        quotaAlertThresholdPct=int(user.get("quota_alert_threshold_pct") or 80),
        role=str(user.get("role") or "user"),
        isActive=bool(user.get("is_active", True)),
    )
