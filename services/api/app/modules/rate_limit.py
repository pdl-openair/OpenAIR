"""In-process sliding-window rate limiter for invited-user scale (S04-01).

Designed for a single API process / hundreds of concurrent users. Not a
distributed Redis limiter — sufficient for Phase-1 cloud prototype.
"""

from __future__ import annotations

import threading
import time
import hashlib
import logging
from collections import defaultdict, deque
from typing import Deque

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.config import settings


class SlidingWindowLimiter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._hits: dict[str, Deque[float]] = defaultdict(deque)

    def allow(self, key: str, *, limit: int, window_seconds: float) -> tuple[bool, int]:
        """Return (allowed, retry_after_seconds)."""
        if limit <= 0:
            return True, 0
        now = time.monotonic()
        cutoff = now - window_seconds
        with self._lock:
            q = self._hits[key]
            while q and q[0] < cutoff:
                q.popleft()
            if len(q) >= limit:
                retry = int(max(1, window_seconds - (now - q[0])))
                return False, retry
            q.append(now)
            return True, 0


_limiter = SlidingWindowLimiter()
_log = logging.getLogger(__name__)


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


def _identity_key(request: Request) -> str:
    """Use the authenticated credential as the partition, never an owner header alone."""
    authorization = request.headers.get("authorization", "")
    if authorization:
        return hashlib.sha256(authorization.encode("utf-8", "replace")).hexdigest()[:24]
    # Local development may use the configured anonymous identity. The IP remains
    # part of the key so an arbitrary owner/workspace header cannot isolate traffic.
    return f"anon:{_client_ip(request)}"


def _endpoint_group(request: Request) -> str:
    path = request.url.path or ""
    method = request.method.upper()
    if path.startswith("/internal"):
        return "provider_internal"
    if method == "GET" and (path.endswith("/research-events") or path.endswith("/events")):
        return "sse_connect"
    if method in {"POST", "PATCH", "PUT", "DELETE"} and any(
        marker in path
        for marker in ("/stop-run", "/confirm", "/decisions/", "/controller-commands", "/start", "/retry", "/advance")
    ):
        return "durable_user_command"
    return "interactive_read" if method in {"GET", "HEAD", "OPTIONS"} else "durable_user_command"


def _group_limit(group: str) -> int:
    configured = {
        "interactive_read": getattr(settings, "rate_limit_api_per_minute", 120),
        "sse_connect": max(10, getattr(settings, "rate_limit_api_per_minute", 120) // 2),
        "durable_user_command": max(20, getattr(settings, "rate_limit_api_per_minute", 120)),
        "provider_internal": max(60, getattr(settings, "rate_limit_api_per_minute", 120) * 2),
    }
    return configured[group]


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Apply coarse API + stricter auth endpoint limits."""

    async def dispatch(self, request: Request, call_next) -> Response:
        if not settings.rate_limit_enabled:
            return await call_next(request)

        path = request.url.path or ""
        if not path.startswith("/api") and not path.startswith("/internal"):
            return await call_next(request)
        if path in ("/health", "/api/health"):
            return await call_next(request)
        # Durable/worker control plane must not share the invited-user limiter:
        # crash-loop startup-contract / claim traffic would otherwise 429 itself.
        if path.startswith("/internal/"):
            return await call_next(request)

        ip = _client_ip(request)
        identity_key = _identity_key(request)
        group = _endpoint_group(request)

        # Stricter bucket for auth-sensitive endpoints
        if path.startswith("/api/auth/login") or path.startswith("/api/auth/register") or path.startswith(
            "/api/auth/password"
        ) or path.startswith("/api/auth/captcha"):
            ok, retry = _limiter.allow(
                f"auth:{ip}",
                limit=settings.rate_limit_auth_per_minute,
                window_seconds=60.0,
            )
            if not ok:
                return JSONResponse(
                    status_code=429,
                    content={"code": "rate_limited", "message": "API rate limit exceeded", "retryAfter": retry},
                    headers={"Retry-After": str(retry)},
                )
        else:
            ok, retry = _limiter.allow(
                f"{group}:{identity_key}:{ip}",
                limit=_group_limit(group),
                window_seconds=60.0,
            )
            if not ok:
                _log.warning(
                    "rate_limit_hit endpoint_group=%s task_id=%s retry_after=%s",
                    group,
                    request.path_params.get("task_id", "-"),
                    retry,
                )
                return JSONResponse(
                    status_code=429,
                    content={"code": "rate_limited", "message": "API rate limit exceeded", "retryAfter": retry},
                    headers={"Retry-After": str(retry)},
                )

        return await call_next(request)
