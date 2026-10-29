"""Minimal durable task identity read for task-scoped artifact endpoints."""

from typing import Any

from app.modules.task_repository import TASK_DELETED_STATUS, TaskRepository


def read_task_scope(task_id: str) -> dict[str, Any] | None:
    """Use row columns as scope authority without loading the research payload."""
    with TaskRepository()._connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT task_id, owner_id, workspace_id FROM openair_tasks "
                "WHERE task_id = %s AND status <> %s",
                (task_id, TASK_DELETED_STATUS),
            )
            row = cur.fetchone()
    if row is None:
        return None
    return {
        "taskId": row["task_id"],
        "ownerId": row["owner_id"],
        "workspaceId": row["workspace_id"],
    }
