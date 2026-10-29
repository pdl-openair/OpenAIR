"""OAuth login routes (GitHub + OpenI + GitLink)."""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse

from app.auth import create_access_token
from app.config import settings
from app.modules import github_oauth, gitlink_oauth, openi_oauth
from app.modules.audit_repository import record_audit
from app.modules.oauth_common import OAuthProfile, make_oauth_state, verify_oauth_state
from app.modules.oauth_identity_repository import get_oauth_identity_repository
from app.modules.user_repository import UserRepositoryBackend, get_user_repository

logger = logging.getLogger(__name__)

router = APIRouter(tags=["auth-oauth"])

# Product pause: GitHub OAuth is temporarily unavailable. Flip to False (and keep
# OPENAIR_GITHUB_OAUTH_* credentials + OPENAIR_GITHUB_OAUTH_ENABLED=true) to reopen.
GITHUB_OAUTH_TEMPORARILY_UNAVAILABLE = True

_USERNAME_SAFE = re.compile(r"[^a-zA-Z0-9._-]+")


def _github_oauth_publicly_enabled() -> bool:
    if GITHUB_OAUTH_TEMPORARILY_UNAVAILABLE:
        return False
    return bool(settings.oauth_github_enabled and github_oauth.github_oauth_configured())


def _user_repo() -> UserRepositoryBackend:
    return get_user_repository()


def _client_meta(request: Request) -> tuple[str | None, str | None]:
    forwarded = request.headers.get("x-forwarded-for")
    ip = forwarded.split(",")[0].strip() if forwarded else (request.client.host if request.client else None)
    return ip, request.headers.get("user-agent")


def _frontend_error_redirect(reason: str) -> RedirectResponse:
    base = settings.oauth_success_redirect_url.rstrip("/")
    # Land on login with a safe reason code (no secrets).
    login = base.rsplit("/auth/oauth/callback", 1)[0] + "/login"
    query = urlencode({"oauth_error": reason})
    return RedirectResponse(url=f"{login}?{query}", status_code=status.HTTP_302_FOUND)


def _frontend_success_redirect(token: str) -> RedirectResponse:
    base = settings.oauth_success_redirect_url
    sep = "&" if "?" in base else "?"
    return RedirectResponse(
        url=f"{base}{sep}{urlencode({'token': token})}",
        status_code=status.HTTP_302_FOUND,
    )


def _allocate_username(repo: UserRepositoryBackend, desired: str, fallback: str) -> str:
    base = _USERNAME_SAFE.sub("", desired).strip("._-")[:24] or fallback
    candidate = base
    suffix = 1
    while repo.get_user_by_username(candidate):
        candidate = f"{base[:20]}_{suffix}"
        suffix += 1
        if suffix > 1000:
            raise RuntimeError("unable to allocate unique username")
    return candidate


def _resolve_or_create_oauth_user(
    *,
    repo: UserRepositoryBackend,
    provider: str,
    profile: OAuthProfile,
    ip: str | None,
    ua: str | None,
) -> tuple[dict[str, Any] | None, RedirectResponse | None]:
    """Return (user, None) on success, or (None, error_redirect) on failure."""
    identities = get_oauth_identity_repository()
    existing_link = identities.get_by_provider_user(provider, profile.provider_user_id)
    user = repo.get_user_by_id(existing_link["user_id"]) if existing_link else None

    created_new = False
    if user is None:
        email = profile.email or f"{provider}_{profile.provider_user_id}@users.noreply.openair.local"
        conflict = repo.get_user_by_email(email)
        if conflict and not email.endswith("@users.noreply.openair.local"):
            record_audit(
                "oauth_login",
                outcome="failure",
                detail={
                    "provider": provider,
                    "reason": "email_conflict",
                    "provider_user_id": profile.provider_user_id,
                },
                ip=ip,
                user_agent=ua,
            )
            return None, _frontend_error_redirect("email_conflict")

        username = _allocate_username(repo, profile.login, fallback=f"{provider}_user")
        user = repo.create_user(
            email=email,
            username=username,
            hashed_password=None,
            display_name=profile.display_name or profile.login,
            is_active=False,
        )
        repo.create_workspace(
            name=f"{user['username']}的 workspace",
            owner_id=user["user_id"],
            is_personal=True,
        )
        identities.link(
            user_id=user["user_id"],
            provider=provider,
            provider_user_id=profile.provider_user_id,
            provider_login=profile.login,
            provider_email=profile.email,
        )
        created_new = True
    else:
        identities.link(
            user_id=user["user_id"],
            provider=provider,
            provider_user_id=profile.provider_user_id,
            provider_login=profile.login,
            provider_email=profile.email,
        )

    if not user.get("is_active", True):
        reason = "pending_activation" if created_new else "disabled"
        record_audit(
            "oauth_login",
            actor_id=user["user_id"],
            outcome="failure",
            detail={"provider": provider, "reason": reason},
            ip=ip,
            user_agent=ua,
        )
        return None, _frontend_error_redirect(reason)

    return user, None


def _issue_oauth_session(
    *,
    repo: UserRepositoryBackend,
    user: dict[str, Any],
    provider: str,
    profile: OAuthProfile,
    ip: str | None,
    ua: str | None,
) -> RedirectResponse:
    workspaces = repo.get_user_workspaces(user["user_id"])
    default_ws_id = workspaces[0]["workspace_id"] if workspaces else None
    token = create_access_token(user["user_id"], workspace_id=default_ws_id)
    record_audit(
        "oauth_login",
        actor_id=user["user_id"],
        outcome="success",
        detail={"provider": provider, "provider_user_id": profile.provider_user_id},
        ip=ip,
        user_agent=ua,
    )
    logger.info("%s oauth login ok: %s (%s)", provider, user["username"], user["user_id"])
    return _frontend_success_redirect(token)


# ---------------------------------------------------------------------------
# GitHub
# ---------------------------------------------------------------------------


@router.get("/auth/oauth/github/status")
def github_oauth_status() -> dict[str, object]:
    """Public flag for the login page button."""
    enabled = _github_oauth_publicly_enabled()
    return {
        "enabled": enabled,
        "provider": github_oauth.PROVIDER,
        "temporarilyUnavailable": GITHUB_OAUTH_TEMPORARILY_UNAVAILABLE,
    }


@router.get("/auth/oauth/github/start")
def github_oauth_start(request: Request) -> RedirectResponse:
    ip, ua = _client_meta(request)
    if not _github_oauth_publicly_enabled():
        reason = "temporarily_unavailable" if GITHUB_OAUTH_TEMPORARILY_UNAVAILABLE else "not_configured"
        record_audit(
            "oauth_login",
            outcome="failure",
            detail={"provider": github_oauth.PROVIDER, "reason": reason},
            ip=ip,
            user_agent=ua,
        )
        if GITHUB_OAUTH_TEMPORARILY_UNAVAILABLE:
            return _frontend_error_redirect("not_configured")
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="GitHub OAuth is not configured")

    state = make_oauth_state()
    url = github_oauth.build_authorize_url(state)
    return RedirectResponse(url=url, status_code=status.HTTP_302_FOUND)


@router.get("/auth/oauth/github/callback")
def github_oauth_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    repo: UserRepositoryBackend = Depends(_user_repo),
) -> RedirectResponse:
    ip, ua = _client_meta(request)
    provider = github_oauth.PROVIDER

    if error:
        record_audit(
            "oauth_login",
            outcome="failure",
            detail={"provider": provider, "reason": "provider_denied", "error": error},
            ip=ip,
            user_agent=ua,
        )
        return _frontend_error_redirect("denied")

    if not code or not state or not verify_oauth_state(state):
        record_audit(
            "oauth_login",
            outcome="failure",
            detail={"provider": provider, "reason": "invalid_state_or_code"},
            ip=ip,
            user_agent=ua,
        )
        return _frontend_error_redirect("invalid_state")

    if not (settings.oauth_github_enabled and github_oauth.github_oauth_configured()):
        return _frontend_error_redirect("not_configured")

    try:
        access_token = github_oauth.exchange_code_for_token(code)
        profile = github_oauth.fetch_github_profile(access_token)
    except Exception as exc:
        logger.warning("github oauth exchange failed: %s", exc)
        record_audit(
            "oauth_login",
            outcome="failure",
            detail={"provider": provider, "reason": "exchange_failed"},
            ip=ip,
            user_agent=ua,
        )
        return _frontend_error_redirect("exchange_failed")

    user, err = _resolve_or_create_oauth_user(
        repo=repo, provider=provider, profile=profile, ip=ip, ua=ua
    )
    if err is not None:
        return err
    assert user is not None
    return _issue_oauth_session(repo=repo, user=user, provider=provider, profile=profile, ip=ip, ua=ua)


# ---------------------------------------------------------------------------
# OpenI (Gitea)
# ---------------------------------------------------------------------------


@router.get("/auth/oauth/openi/status")
def openi_oauth_status() -> dict[str, object]:
    """Public flag for the login page button."""
    enabled = bool(settings.oauth_openi_enabled and openi_oauth.openi_oauth_configured())
    return {"enabled": enabled, "provider": openi_oauth.PROVIDER}


@router.get("/auth/oauth/openi/start")
def openi_oauth_start(request: Request) -> RedirectResponse:
    ip, ua = _client_meta(request)
    if not (settings.oauth_openi_enabled and openi_oauth.openi_oauth_configured()):
        record_audit(
            "oauth_login",
            outcome="failure",
            detail={"provider": openi_oauth.PROVIDER, "reason": "not_configured"},
            ip=ip,
            user_agent=ua,
        )
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="OpenI OAuth is not configured")

    state = make_oauth_state()
    url = openi_oauth.build_authorize_url(state)
    return RedirectResponse(url=url, status_code=status.HTTP_302_FOUND)


@router.get("/auth/oauth/openi/callback")
def openi_oauth_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    repo: UserRepositoryBackend = Depends(_user_repo),
) -> RedirectResponse:
    ip, ua = _client_meta(request)
    provider = openi_oauth.PROVIDER

    if error:
        record_audit(
            "oauth_login",
            outcome="failure",
            detail={"provider": provider, "reason": "provider_denied", "error": error},
            ip=ip,
            user_agent=ua,
        )
        return _frontend_error_redirect("denied")

    if not code or not state or not verify_oauth_state(state):
        record_audit(
            "oauth_login",
            outcome="failure",
            detail={"provider": provider, "reason": "invalid_state_or_code"},
            ip=ip,
            user_agent=ua,
        )
        return _frontend_error_redirect("invalid_state")

    if not (settings.oauth_openi_enabled and openi_oauth.openi_oauth_configured()):
        return _frontend_error_redirect("not_configured")

    try:
        access_token = openi_oauth.exchange_code_for_token(code)
        profile = openi_oauth.fetch_openi_profile(access_token)
    except Exception as exc:
        logger.warning("openi oauth exchange failed: %s", exc)
        record_audit(
            "oauth_login",
            outcome="failure",
            detail={"provider": provider, "reason": "exchange_failed"},
            ip=ip,
            user_agent=ua,
        )
        return _frontend_error_redirect("exchange_failed")

    user, err = _resolve_or_create_oauth_user(
        repo=repo, provider=provider, profile=profile, ip=ip, ua=ua
    )
    if err is not None:
        return err
    assert user is not None
    return _issue_oauth_session(repo=repo, user=user, provider=provider, profile=profile, ip=ip, ua=ua)


# ---------------------------------------------------------------------------
# GitLink
# ---------------------------------------------------------------------------


@router.get("/auth/oauth/gitlink/status")
def gitlink_oauth_status() -> dict[str, object]:
    """Public flag for the login page button."""
    enabled = bool(settings.oauth_gitlink_enabled and gitlink_oauth.gitlink_oauth_configured())
    return {"enabled": enabled, "provider": gitlink_oauth.PROVIDER}


@router.get("/auth/oauth/gitlink/start")
def gitlink_oauth_start(request: Request) -> RedirectResponse:
    ip, ua = _client_meta(request)
    if not (settings.oauth_gitlink_enabled and gitlink_oauth.gitlink_oauth_configured()):
        record_audit(
            "oauth_login",
            outcome="failure",
            detail={"provider": gitlink_oauth.PROVIDER, "reason": "not_configured"},
            ip=ip,
            user_agent=ua,
        )
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="GitLink OAuth is not configured")

    state = make_oauth_state()
    url = gitlink_oauth.build_authorize_url(state)
    return RedirectResponse(url=url, status_code=status.HTTP_302_FOUND)


@router.get("/auth/oauth/gitlink/callback")
def gitlink_oauth_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    repo: UserRepositoryBackend = Depends(_user_repo),
) -> RedirectResponse:
    ip, ua = _client_meta(request)
    provider = gitlink_oauth.PROVIDER

    if error:
        record_audit(
            "oauth_login",
            outcome="failure",
            detail={"provider": provider, "reason": "provider_denied", "error": error},
            ip=ip,
            user_agent=ua,
        )
        return _frontend_error_redirect("denied")

    if not code or not state or not verify_oauth_state(state):
        record_audit(
            "oauth_login",
            outcome="failure",
            detail={"provider": provider, "reason": "invalid_state_or_code"},
            ip=ip,
            user_agent=ua,
        )
        return _frontend_error_redirect("invalid_state")

    if not (settings.oauth_gitlink_enabled and gitlink_oauth.gitlink_oauth_configured()):
        return _frontend_error_redirect("not_configured")

    try:
        access_token = gitlink_oauth.exchange_code_for_token(code)
        profile = gitlink_oauth.fetch_gitlink_profile(access_token)
    except Exception as exc:
        logger.warning("gitlink oauth exchange failed: %s", exc)
        record_audit(
            "oauth_login",
            outcome="failure",
            detail={"provider": provider, "reason": "exchange_failed"},
            ip=ip,
            user_agent=ua,
        )
        return _frontend_error_redirect("exchange_failed")

    user, err = _resolve_or_create_oauth_user(
        repo=repo, provider=provider, profile=profile, ip=ip, ua=ua
    )
    if err is not None:
        return err
    assert user is not None
    return _issue_oauth_session(repo=repo, user=user, provider=provider, profile=profile, ip=ip, ua=ua)
