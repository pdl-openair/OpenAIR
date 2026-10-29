"""Read public planning thoughts from their durable, task-scoped session."""

from __future__ import annotations

import hashlib
from typing import Any

from app.modules.research_start_thinking import project_public_thoughts


def load_planning_thought_history(cur: Any, task_id: str) -> list[dict[str, Any]]:
    cur.execute(
        """SELECT s.session_id, s.thinking_transcript
             FROM research_start_sessions s
             JOIN openair_tasks t ON t.task_id = s.reserved_task_id
              AND t.owner_id = s.owner_id AND t.workspace_id = s.workspace_id
            WHERE t.task_id = %s
            ORDER BY s.created_at, s.session_id""",
        (task_id,),
    )
    return project_planning_thought_history(list(cur.fetchall()))


def project_planning_thought_history(sessions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for session in sessions:
        for thought in project_public_thoughts(session.get("thinking_transcript")):
            identity = hashlib.sha256(
                f"{session['session_id']}:{thought['key']}".encode()
            ).hexdigest()[:24]
            rows.append(
                {
                    "eventId": f"planning-thought:{identity}",
                    "eventType": "meaningful_progress",
                    "presentationLevel": "primary",
                    "workUnitId": None,
                    "headline": "方案思考",
                    "summary": thought["text"],
                    "sequence": len(rows),
                    "occurredAt": thought.get("occurredAt"),
                    "aggregateCount": None,
                    "activityKey": f"planning-thought:{identity}",
                    "phase": thought["state"],
                    "artifactRefs": [],
                }
            )
    return rows
