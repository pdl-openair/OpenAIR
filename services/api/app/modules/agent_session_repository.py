"""PostgreSQL persistence for plaza AgentSession (independent of ResearchDesign)."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.config import settings


def _iso_from_db(value: Any) -> str:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    text = str(value or "").strip()
    if text.endswith("+00:00"):
        return text[:-6] + "Z"
    return text


def _payload_from_session(session: dict[str, Any]) -> dict[str, Any]:
    return {
        "steps": list(session.get("steps") or []),
        "messages": list(session.get("messages") or []),
        "artifacts": list(session.get("artifacts") or []),
        "sessionKind": str(session.get("sessionKind") or "agent"),
        "turnRequests": dict(session.get("turnRequests") or {}),
    }


def _session_from_row(row: dict[str, Any]) -> dict[str, Any]:
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    return {
        "sessionId": str(row["session_id"]),
        "ownerId": str(row["owner_id"]),
        "workspaceId": str(row["workspace_id"]),
        "title": str(row["title"]),
        "status": str(row["status"]),
        "resourceId": str(row["resource_id"]),
        "resourceVersion": str(row.get("resource_version") or ""),
        "agentId": str(row["agent_id"]),
        "agentDisplayName": str(row["agent_display_name"]),
        "sessionKind": str(payload.get("sessionKind") or "agent"),
        "activeStepKey": row.get("active_step_key"),
        "steps": list(payload.get("steps") or []),
        "messages": list(payload.get("messages") or []),
        "artifacts": list(payload.get("artifacts") or []),
        "turnRequests": dict(payload.get("turnRequests") or {}),
        "createdAt": _iso_from_db(row.get("created_at")),
        "updatedAt": _iso_from_db(row.get("updated_at")),
    }


def _summary_from_row(row: dict[str, Any]) -> dict[str, Any]:
    def _count(key: str) -> int:
        raw = row.get(key)
        try:
            return int(raw) if raw is not None else 0
        except (TypeError, ValueError):
            return 0

    return {
        "sessionId": str(row["session_id"]),
        "title": str(row["title"]),
        "status": str(row["status"]),
        "resourceId": str(row["resource_id"]),
        "agentId": str(row["agent_id"]),
        "agentDisplayName": str(row["agent_display_name"]),
        "sessionKind": str(row.get("session_kind") or "agent"),
        "messageCount": _count("message_count"),
        "artifactCount": _count("artifact_count"),
        "createdAt": _iso_from_db(row.get("created_at")),
        "updatedAt": _iso_from_db(row.get("updated_at")),
    }


class AgentSessionRepository:
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

    def create(self, session: dict[str, Any]) -> dict[str, Any]:
        payload = _payload_from_session(session)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO openair_agent_sessions (
                        session_id, owner_id, workspace_id, title, status,
                        resource_id, resource_version, agent_id, agent_display_name,
                        active_step_key, payload, created_at, updated_at
                    ) VALUES (
                        %s, %s, %s, %s, %s,
                        %s, %s, %s, %s,
                        %s, %s::jsonb, %s::timestamptz, %s::timestamptz
                    )
                    RETURNING *
                    """,
                    (
                        session["sessionId"],
                        session["ownerId"],
                        session["workspaceId"],
                        session["title"],
                        session["status"],
                        session["resourceId"],
                        session.get("resourceVersion") or "",
                        session["agentId"],
                        session["agentDisplayName"],
                        session.get("activeStepKey"),
                        Jsonb(payload),
                        session["createdAt"],
                        session["updatedAt"],
                    ),
                )
                row = cur.fetchone()
        assert row is not None
        return _session_from_row(row)

    def get_scoped(self, session_id: str, *, owner_id: str, workspace_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT *
                    FROM openair_agent_sessions
                    WHERE session_id = %s
                      AND owner_id = %s
                      AND workspace_id = %s
                    """,
                    (session_id, owner_id, workspace_id),
                )
                row = cur.fetchone()
        return _session_from_row(row) if row else None

    def replace(self, session: dict[str, Any]) -> dict[str, Any]:
        payload = _payload_from_session(session)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE openair_agent_sessions
                       SET title = %s,
                           status = %s,
                           resource_id = %s,
                           resource_version = %s,
                           agent_id = %s,
                           agent_display_name = %s,
                           active_step_key = %s,
                           payload = %s::jsonb,
                           updated_at = %s::timestamptz
                     WHERE session_id = %s
                       AND owner_id = %s
                       AND workspace_id = %s
                 RETURNING *
                    """,
                    (
                        session["title"],
                        session["status"],
                        session["resourceId"],
                        session.get("resourceVersion") or "",
                        session["agentId"],
                        session["agentDisplayName"],
                        session.get("activeStepKey"),
                        Jsonb(payload),
                        session["updatedAt"],
                        session["sessionId"],
                        session["ownerId"],
                        session["workspaceId"],
                    ),
                )
                row = cur.fetchone()
        if row is None:
            raise KeyError(session["sessionId"])
        return _session_from_row(row)

    def list_summaries(
        self,
        *,
        owner_id: str,
        workspace_id: str,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        capped = max(1, min(int(limit), 200))
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT session_id, title, status, resource_id, agent_id, agent_display_name,
                           created_at, updated_at,
                           COALESCE(payload->>'sessionKind', 'agent') AS session_kind,
                           COALESCE(jsonb_array_length(payload->'messages'), 0) AS message_count,
                           COALESCE(jsonb_array_length(payload->'artifacts'), 0) AS artifact_count
                    FROM openair_agent_sessions
                    WHERE owner_id = %s AND workspace_id = %s
                    ORDER BY updated_at DESC
                    LIMIT %s
                    """,
                    (owner_id, workspace_id, capped),
                )
                rows = cur.fetchall()
        return [_summary_from_row(row) for row in rows]
