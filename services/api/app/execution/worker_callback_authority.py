"""Durable authority for worker callbacks.

The asyncio callback registry is a delivery mechanism, not an authorization
source: it disappears on API restart and is replica-local.  This repository
stores the exact execution epoch that may post callbacks so every API replica
can authenticate the same durable fact.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Iterator

from app.config import settings


@dataclass(frozen=True)
class WorkerCallbackAuthority:
    execution_id: str
    task_id: str
    owner_id: str
    execution_epoch: str
    expires_at: datetime

    def __post_init__(self) -> None:
        if not all(
            str(value or "").strip()
            for value in (self.execution_id, self.task_id, self.owner_id, self.execution_epoch)
        ):
            raise ValueError("callback authority identity is incomplete")
        if self.expires_at.tzinfo is None:
            raise ValueError("callback authority expiry must be timezone-aware")


class WorkerCallbackAuthorityRepository:
    """PostgreSQL-backed callback authorization authority."""

    def __init__(self, connection_factory: Callable[[], Any] | None = None) -> None:
        self._connection_factory = connection_factory

    @contextmanager
    def _connect(self) -> Iterator[Any]:
        if self._connection_factory is not None:
            with self._connection_factory() as connection:
                yield connection
            return
        import psycopg
        from psycopg.rows import dict_row

        conninfo = (
            f"host={settings.postgres_host} port={settings.postgres_port} "
            f"dbname={settings.postgres_database} user={settings.postgres_user} connect_timeout=2"
        )
        connection = psycopg.connect(
            conninfo,
            password=settings.postgres_password,
            row_factory=dict_row,
        )
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def issue(self, authority: WorkerCallbackAuthority) -> None:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """INSERT INTO openair_worker_callback_authority (
                           execution_id, task_id, owner_id, execution_epoch,
                           state, expires_at, created_at, updated_at
                       ) VALUES (%s, %s, %s, %s, 'active', %s, clock_timestamp(), clock_timestamp())
                       ON CONFLICT (execution_id) DO NOTHING""",
                    (
                        authority.execution_id,
                        authority.task_id,
                        authority.owner_id,
                        authority.execution_epoch,
                        authority.expires_at,
                    ),
                )
                if cursor.rowcount == 1:
                    return
                cursor.execute(
                    """SELECT task_id, owner_id, execution_epoch, state, expires_at
                         FROM openair_worker_callback_authority
                        WHERE execution_id = %s
                        FOR UPDATE""",
                    (authority.execution_id,),
                )
                row = cursor.fetchone()
                if row is None:
                    raise RuntimeError("callback_authority_insert_race")
                existing = (
                    row["task_id"],
                    row["owner_id"],
                    row["execution_epoch"],
                )
                expected = (
                    authority.task_id,
                    authority.owner_id,
                    authority.execution_epoch,
                )
                if existing != expected or row["state"] != "active":
                    raise RuntimeError("callback_authority_identity_conflict")
                cursor.execute(
                    """UPDATE openair_worker_callback_authority
                          SET expires_at = GREATEST(expires_at, %s),
                              updated_at = clock_timestamp()
                        WHERE execution_id = %s AND state = 'active'""",
                    (authority.expires_at, authority.execution_id),
                )

    def load_active(self, execution_id: str) -> WorkerCallbackAuthority | None:
        if not str(execution_id or "").strip():
            return None
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """SELECT execution_id, task_id, owner_id, execution_epoch, expires_at
                         FROM openair_worker_callback_authority
                        WHERE execution_id = %s
                          AND state = 'active'
                          AND expires_at > clock_timestamp()""",
                    (execution_id,),
                )
                row = cursor.fetchone()
        if row is None:
            return None
        return WorkerCallbackAuthority(
            execution_id=row["execution_id"],
            task_id=row["task_id"],
            owner_id=row["owner_id"],
            execution_epoch=row["execution_epoch"],
            expires_at=row["expires_at"],
        )

    def revoke(self, execution_id: str, execution_epoch: str | None = None) -> None:
        if not str(execution_id or "").strip():
            return
        predicate = "execution_id = %s AND state = 'active'"
        params: list[Any] = [execution_id]
        if execution_epoch:
            predicate += " AND execution_epoch = %s"
            params.append(execution_epoch)
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""UPDATE openair_worker_callback_authority
                           SET state = 'revoked', updated_at = clock_timestamp()
                         WHERE {predicate}""",  # nosec B608: predicate is fixed above
                    tuple(params),
                )


__all__ = ["WorkerCallbackAuthority", "WorkerCallbackAuthorityRepository"]
