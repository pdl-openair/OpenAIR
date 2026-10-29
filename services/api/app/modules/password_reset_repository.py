"""Password-reset token store (S04-01). Tokens are stored hashed."""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Union

import psycopg
from psycopg.rows import dict_row

from app.config import settings
from app.modules.repo_paths import repo_root

logger = logging.getLogger(__name__)

PasswordResetBackend = Union["PasswordResetRepository", "JsonPasswordResetRepository"]

_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS openair_password_reset_tokens (
    token_id     TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL,
    token_hash   TEXT NOT NULL UNIQUE,
    expires_at   TIMESTAMPTZ NOT NULL,
    used_at      TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_password_reset_user
    ON openair_password_reset_tokens (user_id, created_at DESC);
"""


def hash_reset_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def mint_reset_token() -> str:
    return secrets.token_urlsafe(32)


class PasswordResetRepository:
    @contextmanager
    def _connect(self) -> Iterator[psycopg.Connection[Any]]:
        conninfo = (
            f"host={settings.postgres_host} port={settings.postgres_port} "
            f"dbname={settings.postgres_database} user={settings.postgres_user}"
        )
        conn = psycopg.connect(conninfo, password=settings.postgres_password, row_factory=dict_row)  # type: ignore[arg-type]
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def bootstrap(self) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(_TABLE_SQL)

    def create_token(self, user_id: str, *, ttl_minutes: int = 60) -> tuple[str, dict[str, Any]]:
        raw = mint_reset_token()
        token_id = f"prt_{uuid.uuid4().hex[:12]}"
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO openair_password_reset_tokens
                        (token_id, user_id, token_hash, expires_at)
                    VALUES (%s, %s, %s, %s)
                    RETURNING *
                    """,
                    (token_id, user_id, hash_reset_token(raw), expires_at),
                )
                row = cur.fetchone()
        return raw, row  # type: ignore[return-value]

    def consume_token(self, raw_token: str) -> dict[str, Any] | None:
        digest = hash_reset_token(raw_token)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT * FROM openair_password_reset_tokens
                     WHERE token_hash = %s AND used_at IS NULL AND expires_at > NOW()
                    """,
                    (digest,),
                )
                row = cur.fetchone()
                if not row:
                    return None
                cur.execute(
                    "UPDATE openair_password_reset_tokens SET used_at = NOW() WHERE token_id = %s",
                    (row["token_id"],),
                )
                return row


class JsonPasswordResetRepository:
    def __init__(self, root: Path) -> None:
        self._dir = root / "data" / "password_reset_tokens"
        self._dir.mkdir(parents=True, exist_ok=True)

    def bootstrap(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)

    def create_token(self, user_id: str, *, ttl_minutes: int = 60) -> tuple[str, dict[str, Any]]:
        raw = mint_reset_token()
        token_id = f"prt_{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc)
        row = {
            "token_id": token_id,
            "user_id": user_id,
            "token_hash": hash_reset_token(raw),
            "expires_at": (now + timedelta(minutes=ttl_minutes)).isoformat(),
            "used_at": None,
            "created_at": now.isoformat(),
        }
        (self._dir / f"{token_id}.json").write_text(
            json.dumps(row, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return raw, row

    def consume_token(self, raw_token: str) -> dict[str, Any] | None:
        digest = hash_reset_token(raw_token)
        now = datetime.now(timezone.utc)
        for path in self._dir.glob("*.json"):
            try:
                row = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if not isinstance(row, dict) or row.get("token_hash") != digest:
                continue
            if row.get("used_at"):
                return None
            try:
                expires = datetime.fromisoformat(str(row["expires_at"]))
            except (KeyError, ValueError):
                return None
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            if expires <= now:
                return None
            row["used_at"] = now.isoformat()
            path.write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding="utf-8")
            return row
        return None


_reset_singleton: PasswordResetBackend | None = None


def get_password_reset_repository() -> PasswordResetBackend:
    global _reset_singleton
    if _reset_singleton is not None:
        return _reset_singleton
    if settings.persistence_backend == "postgres":
        _reset_singleton = PasswordResetRepository()
    else:
        _reset_singleton = JsonPasswordResetRepository(repo_root())
    return _reset_singleton
