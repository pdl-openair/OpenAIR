"""Task message repository (E1): postgres append-only chat transcript table.

Replaces the unbounded ``payload -> agentMessages[]`` JSONB array with a
dedicated ``openair_task_messages`` table for O(1) appends and cursor
pagination. Postgres mode only; the JSON store keeps its in-memory array.

Key invariants:
* ``seq`` is per-task monotonic (1, 2, 3 …) — the cursor anchor + ordering.
  It is allocated inside the caller's task-row lock (``FOR UPDATE`` on
  ``openair_tasks`` held by every write caller), which serializes per-task
  appends. ``append`` therefore takes the caller's cursor rather than opening
  its own connection (a nested connection would deadlock against the held task
  row lock).
* ``message_uid`` is the logical message id (``msg_xxx`` chat /
  ``msg_dispatch_xxx`` / ``msg_controller_command_xxx``) and the idempotency
  key. ``UNIQUE(task_id, message_uid)`` + ``ON CONFLICT DO NOTHING`` makes
  appends replay-safe (the dispatch path's deterministic-id dedup, generalized).
* Pure append-only — postgres chat messages never mutate historically
  (``_chat_message`` hardcodes ``confirmationRequests=[]``; the in-place
  ``resolve_confirmation_request`` is JSON-store only).

The table + scope trigger are created by migration
``20260811_02_task_messages.sql``.
"""
from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

import psycopg
from psycopg.errors import UndefinedTable
from psycopg.rows import dict_row

from app.config import settings

_log = logging.getLogger(__name__)

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200


class TaskMessageRepository:
    """Postgres repository for the agent chat transcript (``openair_task_messages``)."""

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

    def append(
        self,
        cur: Any,
        *,
        task_id: str,
        owner_id: str,
        workspace_id: str,
        role: str,
        text: str,
        message_uid: str,
        runtime: str | None = None,
        message_type: str = "chat",
        capability_call_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Append one message in the caller's transaction (O(1) INSERT).

        Per-task ``seq`` allocation is serialized by a ``FOR UPDATE`` on the
        task row acquired here: it is re-entrant when the caller already holds
        the task lock (the chat/gateway writers via ``_lock_chat_task``), and
        acquires it otherwise (the controller-supplement CAS path), so every
        caller gets correct monotonic seq without depending on its own locking.
        Replay-safe via ``ON CONFLICT (task_id, message_uid) DO NOTHING``; the
        existing row is returned on replay (its original ``seq`` preserved).
        """
        if role not in ("user", "assistant", "system"):
            raise ValueError(f"invalid message role: {role}")
        if not text:
            raise ValueError("message text must not be empty")
        meta = json.dumps(metadata or {}, ensure_ascii=False)
        cur.execute(
            "SELECT 1 FROM openair_tasks WHERE task_id = %s FOR UPDATE",
            (task_id,),
        )
        cur.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 AS next_seq "
            "FROM openair_task_messages WHERE task_id = %s",
            (task_id,),
        )
        next_seq = int(cur.fetchone()["next_seq"])
        cur.execute(
            """
            INSERT INTO openair_task_messages
                (task_id, owner_id, workspace_id, seq, message_uid, role,
                 content_text, runtime, message_type, capability_call_id, metadata)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (task_id, message_uid) DO NOTHING
            """,
            (
                task_id, owner_id, workspace_id, next_seq, message_uid, role,
                text, runtime, message_type, capability_call_id, meta,
            ),
        )
        cur.execute(
            "SELECT * FROM openair_task_messages WHERE task_id = %s AND message_uid = %s",
            (task_id, message_uid),
        )
        row = cur.fetchone()
        return _row_to_message(row)

    def list_messages(
        self,
        *,
        task_id: str,
        owner_id: str,
        workspace_id: str,
        before_seq: int | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
    ) -> tuple[list[dict[str, Any]], bool, int | None]:
        """Cursor-paginated read (newest-first).

        Returns ``(messages, has_more, latest_seq)``. ``before_seq`` excludes
        that seq and all after it (exclusive upper bound for scrollback).
        ``latest_seq`` is the task's current max seq (independent of the page,
        for SSE resume / "jump to latest"). Stable under concurrent appends
        because ordering is by ``seq``, not offset.
        """
        page_size = max(1, min(limit, MAX_PAGE_SIZE))
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT MAX(seq) AS latest_seq "
                        "FROM openair_task_messages "
                        "WHERE task_id = %s AND owner_id = %s AND workspace_id = %s",
                        (task_id, owner_id, workspace_id),
                    )
                    latest_seq = cur.fetchone()["latest_seq"]
                    if before_seq is None:
                        cur.execute(
                            """
                            SELECT * FROM openair_task_messages
                             WHERE task_id = %s AND owner_id = %s AND workspace_id = %s
                             ORDER BY seq DESC
                             LIMIT %s
                            """,
                            (task_id, owner_id, workspace_id, page_size + 1),
                        )
                    else:
                        cur.execute(
                            """
                            SELECT * FROM openair_task_messages
                             WHERE task_id = %s AND owner_id = %s AND workspace_id = %s
                               AND seq < %s
                             ORDER BY seq DESC
                             LIMIT %s
                            """,
                            (task_id, owner_id, workspace_id, before_seq, page_size + 1),
                        )
                    rows = cur.fetchall()
        except UndefinedTable:
            # Migration not yet applied (e.g. unit tests bypassing bootstrap, or a
            # not-yet-migrated shard). The messages feature degrades to "empty"
            # rather than crashing the read path; writes still surface the gap.
            return [], False, None
        has_more = len(rows) > page_size
        rows = rows[:page_size]
        messages = [_row_to_message(row) for row in rows]
        return messages, has_more, latest_seq

    def messages_after(
        self,
        *,
        task_id: str,
        owner_id: str,
        workspace_id: str,
        after_seq: int,
    ) -> list[dict[str, Any]]:
        """Return messages with ``seq > after_seq`` oldest-first (SSE increment)."""
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT * FROM openair_task_messages
                         WHERE task_id = %s AND owner_id = %s AND workspace_id = %s
                           AND seq > %s
                         ORDER BY seq ASC
                        """,
                        (task_id, owner_id, workspace_id, after_seq),
                    )
                    rows = cur.fetchall()
        except UndefinedTable:
            return []
        return [_row_to_message(row) for row in rows]

    def latest_seq(
        self,
        *,
        task_id: str,
        owner_id: str,
        workspace_id: str,
    ) -> int | None:
        """Current max seq for the task (SSE change-detection anchor)."""
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT MAX(seq) AS latest_seq "
                        "FROM openair_task_messages "
                        "WHERE task_id = %s AND owner_id = %s AND workspace_id = %s",
                        (task_id, owner_id, workspace_id),
                    )
                    return cur.fetchone()["latest_seq"]
        except UndefinedTable:
            return None


def _row_to_message(row: dict[str, Any]) -> dict[str, Any]:
    """snake catalog row → AgentMessageOut-shaped camel dict.

    The union of the four historical message builders is reconstructed from
    columns + the ``metadata`` JSONB (authorLabel / artifactRefs /
    confirmationRequests / userFacingNarrative / controllerCommandId / workUnitId).
    """
    metadata = row.get("metadata") or {}
    if isinstance(metadata, str):
        metadata = json.loads(metadata)
    created_at = row.get("created_at")
    at = created_at.isoformat() if hasattr(created_at, "isoformat") else str(created_at)
    message: dict[str, Any] = {
        "id": row["message_uid"],
        "seq": int(row["seq"]),
        "role": row["role"],
        "text": row["content_text"],
        "at": at,
        "runtime": row.get("runtime"),
        "messageType": row.get("message_type") or "chat",
        "capabilityCallId": row.get("capability_call_id"),
        "authorLabel": metadata.get("authorLabel"),
        "controllerCommandId": metadata.get("controllerCommandId"),
        "artifactRefs": list(metadata.get("artifactRefs") or []),
        "confirmationRequests": list(metadata.get("confirmationRequests") or []),
        "userFacingNarrative": metadata.get("userFacingNarrative"),
    }
    # Preserve the dispatch/controller-specific extras for callers that read them.
    if metadata.get("workUnitId") is not None:
        message["workUnitId"] = metadata.get("workUnitId")
    return message
