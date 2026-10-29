"""PostgreSQL persistence for a caller's saved public resources."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row

from app.config import settings


class ResourceSaveRepository:
    @contextmanager
    def _connect(self) -> Iterator[psycopg.Connection[Any]]:
        conninfo = (
            f"host={settings.postgres_host} port={settings.postgres_port} "
            f"dbname={settings.postgres_database} user={settings.postgres_user}"
        )
        conn = psycopg.connect(
            conninfo,
            password=settings.postgres_password,
            row_factory=dict_row,
        )  # type: ignore[arg-type]
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def save(self, *, owner_id: str, workspace_id: str, resource_id: str) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO openair_resource_saves (owner_id, workspace_id, resource_id)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (owner_id, workspace_id, resource_id) DO NOTHING
                    """,
                    (owner_id, workspace_id, resource_id),
                )

    def remove(self, *, owner_id: str, workspace_id: str, resource_id: str) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    DELETE FROM openair_resource_saves
                    WHERE owner_id = %s AND workspace_id = %s AND resource_id = %s
                    """,
                    (owner_id, workspace_id, resource_id),
                )

    def list_resource_ids(self, *, owner_id: str, workspace_id: str) -> list[str]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT resource_id
                    FROM openair_resource_saves
                    WHERE owner_id = %s AND workspace_id = %s
                    ORDER BY created_at DESC, resource_id ASC
                    """,
                    (owner_id, workspace_id),
                )
                return [str(row["resource_id"]) for row in cur.fetchall()]

    def saved_resource_ids(
        self,
        *,
        owner_id: str,
        workspace_id: str,
        resource_ids: list[str],
    ) -> list[str]:
        if not resource_ids:
            return []
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT resource_id
                    FROM openair_resource_saves
                    WHERE owner_id = %s
                      AND workspace_id = %s
                      AND resource_id = ANY(%s)
                    """,
                    (owner_id, workspace_id, resource_ids),
                )
                return [str(row["resource_id"]) for row in cur.fetchall()]
