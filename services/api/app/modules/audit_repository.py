"""Append-only audit log persistence (S04-01)."""

from __future__ import annotations

import json
import logging
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Union

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.config import settings
from app.modules.repo_paths import repo_root

logger = logging.getLogger(__name__)

AuditBackend = Union["AuditRepository", "JsonAuditRepository"]

_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS openair_audit_logs (
    audit_id     TEXT PRIMARY KEY,
    action       TEXT NOT NULL,
    actor_id     TEXT,
    target_id    TEXT,
    outcome      TEXT NOT NULL DEFAULT 'success',
    detail       JSONB NOT NULL DEFAULT '{}'::jsonb,
    ip           TEXT,
    user_agent   TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_audit_logs_action_created
    ON openair_audit_logs (action, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_logs_actor_created
    ON openair_audit_logs (actor_id, created_at DESC);
"""


class AuditRepository:
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

    def append(
        self,
        *,
        action: str,
        actor_id: str | None = None,
        target_id: str | None = None,
        outcome: str = "success",
        detail: dict[str, Any] | None = None,
        ip: str | None = None,
        user_agent: str | None = None,
    ) -> dict[str, Any]:
        audit_id = f"aud_{uuid.uuid4().hex[:16]}"
        row = {
            "audit_id": audit_id,
            "action": action,
            "actor_id": actor_id,
            "target_id": target_id,
            "outcome": outcome,
            "detail": detail or {},
            "ip": ip,
            "user_agent": user_agent,
        }
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO openair_audit_logs
                        (audit_id, action, actor_id, target_id, outcome, detail, ip, user_agent)
                    VALUES
                        (%(audit_id)s, %(action)s, %(actor_id)s, %(target_id)s,
                         %(outcome)s, %(detail)s, %(ip)s, %(user_agent)s)
                    RETURNING *
                    """,
                    {**row, "detail": Jsonb(row["detail"])},
                )
                return cur.fetchone()  # type: ignore[return-value]

    def list_logs(
        self,
        *,
        action: str | None = None,
        actor_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if action:
            clauses.append("action = %(action)s")
            params["action"] = action
        if actor_id:
            clauses.append("actor_id = %(actor_id)s")
            params["actor_id"] = actor_id
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT * FROM openair_audit_logs
                    {where}
                    ORDER BY created_at DESC
                    LIMIT %(limit)s OFFSET %(offset)s
                    """,
                    params,
                )
                return cur.fetchall()


class JsonAuditRepository:
    def __init__(self, root: Path) -> None:
        self._dir = root / "data" / "audit_logs"
        self._dir.mkdir(parents=True, exist_ok=True)

    def bootstrap(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)

    def append(
        self,
        *,
        action: str,
        actor_id: str | None = None,
        target_id: str | None = None,
        outcome: str = "success",
        detail: dict[str, Any] | None = None,
        ip: str | None = None,
        user_agent: str | None = None,
    ) -> dict[str, Any]:
        audit_id = f"aud_{uuid.uuid4().hex[:16]}"
        row = {
            "audit_id": audit_id,
            "action": action,
            "actor_id": actor_id,
            "target_id": target_id,
            "outcome": outcome,
            "detail": detail or {},
            "ip": ip,
            "user_agent": user_agent,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        path = self._dir / f"{audit_id}.json"
        path.write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding="utf-8")
        return row

    def list_logs(
        self,
        *,
        action: str | None = None,
        actor_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for path in sorted(self._dir.glob("*.json"), reverse=True):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if not isinstance(raw, dict):
                continue
            if action and raw.get("action") != action:
                continue
            if actor_id and raw.get("actor_id") != actor_id:
                continue
            rows.append(raw)
        rows.sort(key=lambda r: str(r.get("created_at") or ""), reverse=True)
        return rows[offset : offset + limit]


_audit_singleton: AuditBackend | None = None


def get_audit_repository() -> AuditBackend:
    global _audit_singleton
    if _audit_singleton is not None:
        return _audit_singleton
    if settings.persistence_backend == "postgres":
        _audit_singleton = AuditRepository()
    else:
        _audit_singleton = JsonAuditRepository(repo_root())
    return _audit_singleton


def record_audit(
    action: str,
    *,
    actor_id: str | None = None,
    target_id: str | None = None,
    outcome: str = "success",
    detail: dict[str, Any] | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
) -> None:
    """Best-effort audit write; never raises to callers."""
    try:
        get_audit_repository().append(
            action=action,
            actor_id=actor_id,
            target_id=target_id,
            outcome=outcome,
            detail=detail,
            ip=ip,
            user_agent=user_agent,
        )
    except Exception:
        logger.exception("failed to append audit log action=%s", action)
