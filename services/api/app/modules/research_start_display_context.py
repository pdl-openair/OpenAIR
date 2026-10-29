"""Scoped startup input and optional name metadata; never research-state Authority."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import psycopg

from app.modules.research_start_repository import ResearchStartRepository
from app.modules.task_title_metadata import sync_artifact_source_task_titles

_logger = logging.getLogger(__name__)


class ResearchStartDisplayRepository:
    def __init__(self, connection_factory: Callable[[], Any] | None = None):
        self._repository = ResearchStartRepository(connection_factory=connection_factory)

    def read(
        self, task_id: str, owner_id: str, workspace_id: str, session_id: str | None = None
    ) -> dict[str, Any]:
        with self._repository._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT s.session_id, to_jsonb(s)->>'display_title' AS display_title,
                              to_jsonb(s)->>'title_attempted_at' AS title_attempted_at,
                              to_jsonb(s)->>'title_finished_at' AS title_finished_at,
                              r.request_payload->>'input' AS original_input, t.title AS task_title,
                              COALESCE((to_jsonb(t)->>'title_is_automatic')::boolean, FALSE)
                                  AS title_is_automatic
                         FROM research_start_sessions s
                         LEFT JOIN LATERAL (
                             SELECT request_payload FROM research_start_requests
                              WHERE session_id = s.session_id AND owner_id = s.owner_id
                                AND workspace_id = s.workspace_id
                              ORDER BY created_at, request_id LIMIT 1
                         ) r ON TRUE
                         LEFT JOIN openair_tasks t ON t.task_id = s.reserved_task_id
                              AND t.owner_id = s.owner_id AND t.workspace_id = s.workspace_id
                              AND t.status <> 'deleted'
                        WHERE s.reserved_task_id = %s AND s.owner_id = %s AND s.workspace_id = %s
                          AND (%s::text IS NULL OR s.session_id = %s)""",
                    (task_id, owner_id, workspace_id, session_id, session_id),
                )
                row = cur.fetchone()
        if row is None:
            return {
                "taskId": task_id,
                "sessionId": None,
                "originalInput": None,
                "displayTitle": None,
                "titlePending": False,
            }
        return {
            "taskId": task_id,
            "sessionId": row["session_id"],
            "originalInput": row["original_input"],
            "displayTitle": row["task_title"] or row["display_title"],
            "titlePending": row["title_finished_at"] is None
            and (row["task_title"] is None or row["title_is_automatic"]),
        }

    def claim_title(
        self, session_id: str, owner_id: str, workspace_id: str
    ) -> dict[str, Any] | None:
        with self._repository._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE research_start_sessions s SET title_attempted_at = clock_timestamp()
                        WHERE s.session_id = %s AND s.owner_id = %s AND s.workspace_id = %s
                          AND s.title_attempted_at IS NULL
                          AND NOT EXISTS (
                              SELECT 1 FROM openair_tasks t WHERE t.task_id = s.reserved_task_id
                                AND t.owner_id = s.owner_id AND t.workspace_id = s.workspace_id
                                AND (t.status = 'deleted' OR NOT t.title_is_automatic)
                          )
                        RETURNING s.reserved_task_id AS task_id,
                          (SELECT r.request_payload->>'input' FROM research_start_requests r
                            WHERE r.session_id = s.session_id AND r.owner_id = s.owner_id
                              AND r.workspace_id = s.workspace_id
                            ORDER BY r.created_at, r.request_id LIMIT 1) AS original_input""",
                    (session_id, owner_id, workspace_id),
                )
                return cur.fetchone()

    def finish_title(
        self, session_id: str, owner_id: str, workspace_id: str, title: str | None
    ) -> None:
        with self._repository._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE research_start_sessions SET display_title = %s,
                              title_finished_at = clock_timestamp()
                        WHERE session_id = %s AND owner_id = %s AND workspace_id = %s
                          AND title_attempted_at IS NOT NULL AND title_finished_at IS NULL
                        RETURNING reserved_task_id""",
                    (title, session_id, owner_id, workspace_id),
                )
                row = cur.fetchone()
                if row is None or not title:
                    return
                # Do not advance research revision for display metadata. The user rename
                # transaction disables this write, even if the user chooses 新研究.
                cur.execute(
                    """UPDATE openair_tasks SET title = %s,
                              payload = jsonb_set(payload, '{title}', to_jsonb(%s::text)),
                              updated_at = clock_timestamp()
                        WHERE task_id = %s AND owner_id = %s AND workspace_id = %s
                          AND title_is_automatic AND status <> 'deleted'""",
                    (title, title, row["reserved_task_id"], owner_id, workspace_id),
                )
                if cur.rowcount == 1:
                    sync_artifact_source_task_titles(
                        cur,
                        task_id=row["reserved_task_id"],
                        owner_id=owner_id,
                        workspace_id=workspace_id,
                        title=title,
                    )

    def insert_task(self, task: dict[str, Any], *, automatic: bool) -> None:
        from app.modules.task_repository import TaskRepository

        with self._repository._connect() as conn:
            with conn.cursor() as cur:
                # Same session-before-task order as finish_title: no lost title if
                # name generation finishes while the first Task row is being created.
                cur.execute(
                    """SELECT to_jsonb(research_start_sessions)->>'display_title'
                                  AS display_title FROM research_start_sessions
                        WHERE reserved_task_id = %s AND owner_id = %s AND workspace_id = %s
                        FOR UPDATE""",
                    (task["taskId"], task["ownerId"], task["workspaceId"]),
                )
                row = cur.fetchone()
                if automatic and row and row["display_title"]:
                    task["title"] = row["display_title"]
                TaskRepository().insert_task_with_cursor(cur, task)
                if automatic:
                    cur.execute("SAVEPOINT optional_title_metadata")
                    try:
                        cur.execute(
                            """UPDATE openair_tasks SET title_is_automatic = TRUE
                                WHERE task_id = %s AND owner_id = %s AND workspace_id = %s""",
                            (task["taskId"], task["ownerId"], task["workspaceId"]),
                        )
                    except psycopg.errors.UndefinedColumn:
                        cur.execute("ROLLBACK TO SAVEPOINT optional_title_metadata")
                        _logger.warning(
                            "Optional title migration unavailable; keeping neutral title"
                        )
                    cur.execute("RELEASE SAVEPOINT optional_title_metadata")
