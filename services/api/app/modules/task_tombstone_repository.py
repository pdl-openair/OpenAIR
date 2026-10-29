"""Dedicated persistence boundary for logical Task deletion tombstones."""

from __future__ import annotations

import json
from collections.abc import Mapping
from copy import deepcopy
from typing import Any

TASK_TOMBSTONE_STATUS = "deleted"


def commit_task_tombstone_locked(
    cur: Any,
    *,
    task_row: Mapping[str, Any],
    task_id: str,
    owner_id: str,
    workspace_id: str,
    deleted_at: str,
) -> None:
    """Atomically hide one already-locked Task without entering its lifecycle.

    A tombstone is deletion metadata, not a public canonical Task conclusion.
    The caller must already have fenced live execution and removed public
    Artifact pointers in the same transaction before calling this function.
    """

    raw_payload = task_row["payload"]
    payload = raw_payload if isinstance(raw_payload, dict) else json.loads(raw_payload)
    if not isinstance(payload, dict):
        raise RuntimeError("task_tombstone_payload_invalid")
    current_revision = int(task_row["revision"])
    next_revision = current_revision + 1
    tombstone = deepcopy(payload)
    tombstone.update(
        {
            "status": TASK_TOMBSTONE_STATUS,
            "deletedAt": deleted_at,
            "updatedAt": deleted_at,
            "revision": next_revision,
            "artifacts": [],
            "capabilityCalls": [],
        }
    )
    cur.execute(
        """UPDATE openair_tasks
              SET status = %s, updated_at = NOW(), revision = %s, payload = %s::jsonb
            WHERE task_id = %s
              AND owner_id = %s
              AND workspace_id = %s
              AND status <> %s
              AND revision = %s""",
        (
            TASK_TOMBSTONE_STATUS,
            next_revision,
            json.dumps(tombstone, ensure_ascii=False),
            task_id,
            owner_id,
            workspace_id,
            TASK_TOMBSTONE_STATUS,
            current_revision,
        ),
    )
    if cur.rowcount != 1:
        raise RuntimeError("task_revision_conflict")
