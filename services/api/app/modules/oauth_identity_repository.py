"""OAuth provider identity bindings (GitHub / OpenI / GitLink → openair_users)."""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row

from app.config import settings


class OAuthIdentityRepository:
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

    def get_by_provider_user(self, provider: str, provider_user_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT * FROM openair_oauth_identities
                     WHERE provider = %s AND provider_user_id = %s
                    """,
                    (provider, provider_user_id),
                )
                return cur.fetchone()

    def get_by_user_provider(self, user_id: str, provider: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT * FROM openair_oauth_identities
                     WHERE user_id = %s AND provider = %s
                    """,
                    (user_id, provider),
                )
                return cur.fetchone()

    def link(
        self,
        *,
        user_id: str,
        provider: str,
        provider_user_id: str,
        provider_login: str | None = None,
        provider_email: str | None = None,
    ) -> dict[str, Any]:
        row_id = f"oauth_{uuid.uuid4().hex[:16]}"
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO openair_oauth_identities (
                        id, user_id, provider, provider_user_id, provider_login, provider_email
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (provider, provider_user_id) DO UPDATE
                      SET provider_login = EXCLUDED.provider_login,
                          provider_email = EXCLUDED.provider_email
                    RETURNING *
                    """,
                    (row_id, user_id, provider, provider_user_id, provider_login, provider_email),
                )
                linked = cur.fetchone()
        if not linked:
            raise RuntimeError("failed to link oauth identity")
        return linked


def get_oauth_identity_repository() -> OAuthIdentityRepository:
    if settings.persistence_backend != "postgres":
        raise RuntimeError("OAuth identity bindings require postgres persistence")
    return OAuthIdentityRepository()
