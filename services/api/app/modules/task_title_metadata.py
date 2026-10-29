"""Scoped display-name metadata maintained inside the caller's transaction."""

import logging
from typing import Any

import psycopg


def disable_automatic_title(cur: Any, task_id: str, owner_id: str, workspace_id: str) -> None:
    cur.execute("SAVEPOINT optional_title_rename")
    try:
        cur.execute(
            """UPDATE openair_tasks SET title_is_automatic = FALSE
            WHERE task_id = %s AND owner_id = %s AND workspace_id = %s""",
            (task_id, owner_id, workspace_id),
        )
    except psycopg.errors.UndefinedColumn:
        cur.execute("ROLLBACK TO SAVEPOINT optional_title_rename")
        logging.getLogger(__name__).warning(
            "Optional title metadata migration unavailable during rename"
        )
    cur.execute("RELEASE SAVEPOINT optional_title_rename")


def sync_artifact_source_task_titles(
    cur: Any,
    *,
    task_id: str,
    owner_id: str,
    workspace_id: str,
    title: str,
) -> None:
    """Keep denormalized openair_artifacts.source_task_title aligned on rename.

    Catalog list joins live task titles, but other readers still use the
    denormalized column; scoped rename must update it in the same transaction.
    """
    cur.execute(
        """UPDATE openair_artifacts
              SET source_task_title = %s, updated_at = NOW()
            WHERE source_task_id = %s
              AND owner_id = %s
              AND workspace_id = %s""",
        (title, task_id, owner_id, workspace_id),
    )
