"""Execution-scoped worker callback tokens.

User-controlled worker code must never receive the global worker control-plane
secret.  Callback tokens are therefore HMAC-signed and bound to one exact
``(task_id, owner_id, execution_id, execution_epoch)`` authority.  They carry an
explicit expiry and are valid only while the API's server-side callback
authority remains active.

Token format::

    wt.<task>.<owner>.<execution>.<epoch>.<issued_at>.<expires_at>.<signature>

All string fields are base64url encoded.  The signature covers every field.
Legacy task-only tokens are intentionally not accepted by verification.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import hmac
import time

from app.config import settings


@dataclass(frozen=True)
class WorkerTokenClaims:
    task_id: str
    owner_id: str
    execution_id: str
    execution_epoch: str
    issued_at: int
    expires_at: int


def _secret() -> str:
    # Callback signing is independent from token presentation.  Existing
    # deployments already configure OPENAIR_INGEST_CALLBACK_SECRET; retain the
    # worker control-plane token only as a development fallback.
    return settings.ingest_callback_secret or settings.worker_auth_token or "openair-dev"


def _b64(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii").rstrip("=")


def _ub64(value: str) -> str:
    pad = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + pad).decode("utf-8")


def _message(claims: WorkerTokenClaims) -> bytes:
    return (
        "wt|"
        f"{claims.task_id}|{claims.owner_id}|{claims.execution_id}|"
        f"{claims.execution_epoch}|{claims.issued_at}|{claims.expires_at}"
    ).encode("utf-8")


def mint_worker_token(
    task_id: str,
    owner_id: str,
    execution_id: str = "",
    execution_epoch: str = "",
    *,
    now: float | None = None,
    ttl_seconds: int | None = None,
) -> str:
    """Mint a callback token for one exact execution epoch.

    Empty execution fields are permitted only for compatibility with callers
    constructing a token to prove fail-closed behaviour; such a token can never
    pass :func:`verify_worker_token` because verification requires non-empty
    expected execution and epoch values.
    """
    issued_at = int(now if now is not None else time.time())
    ttl = int(ttl_seconds if ttl_seconds is not None else settings.worker_token_ttl_seconds)
    if ttl < 1:
        raise ValueError("worker token ttl must be positive")
    claims = WorkerTokenClaims(
        task_id=str(task_id or ""),
        owner_id=str(owner_id or ""),
        execution_id=str(execution_id or ""),
        execution_epoch=str(execution_epoch or ""),
        issued_at=issued_at,
        expires_at=issued_at + ttl,
    )
    signature = hmac.new(_secret().encode("utf-8"), _message(claims), hashlib.sha256).hexdigest()
    return ".".join(
        (
            "wt",
            _b64(claims.task_id),
            _b64(claims.owner_id),
            _b64(claims.execution_id),
            _b64(claims.execution_epoch),
            _b64(str(claims.issued_at)),
            _b64(str(claims.expires_at)),
            signature,
        )
    )


def parse_worker_token(token: str | None) -> WorkerTokenClaims | None:
    """Parse and authenticate a callback token, without applying authority binding."""
    raw = str(token or "")
    if not raw.startswith("wt."):
        return None
    parts = raw.split(".")
    if len(parts) != 8:
        return None
    _, task_b64, owner_b64, execution_b64, epoch_b64, issued_b64, expires_b64, signature = parts
    try:
        claims = WorkerTokenClaims(
            task_id=_ub64(task_b64),
            owner_id=_ub64(owner_b64),
            execution_id=_ub64(execution_b64),
            execution_epoch=_ub64(epoch_b64),
            issued_at=int(_ub64(issued_b64)),
            expires_at=int(_ub64(expires_b64)),
        )
    except (TypeError, ValueError, UnicodeDecodeError, base64.binascii.Error):
        return None
    expected = hmac.new(_secret().encode("utf-8"), _message(claims), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None
    return claims


def verify_worker_token(
    token: str | None,
    *,
    expected_task_id: str | None = None,
    expected_owner_id: str | None = None,
    expected_execution_id: str | None = None,
    expected_execution_epoch: str | None = None,
    now: float | None = None,
) -> bool:
    """Verify signature, explicit expiry, and complete server-side binding.

    No partial binding is accepted.  In particular, a task-only comparison is
    insufficient: a token from a prior execution of the same task must not be
    able to authorize a later callback.
    """
    claims = parse_worker_token(token)
    if claims is None:
        return False
    expected = (
        str(expected_task_id or ""),
        str(expected_owner_id or ""),
        str(expected_execution_id or ""),
        str(expected_execution_epoch or ""),
    )
    if any(not item for item in expected):
        return False
    if (
        claims.task_id,
        claims.owner_id,
        claims.execution_id,
        claims.execution_epoch,
    ) != expected:
        return False
    current = int(now if now is not None else time.time())
    # Reject tokens issued implausibly in the future as well as expired tokens.
    if claims.issued_at > current + 30 or current >= claims.expires_at:
        return False
    if claims.expires_at <= claims.issued_at:
        return False
    return True
