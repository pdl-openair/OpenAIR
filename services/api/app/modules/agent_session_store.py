"""In-memory AgentSession store (preview / P0; not ResearchDesign)."""

from __future__ import annotations

import copy
import threading
from typing import Any
from uuid import uuid4


def new_agent_session_id() -> str:
    return f"ags_{uuid4().hex[:12]}"


def new_message_id() -> str:
    return f"agm_{uuid4().hex[:12]}"


def new_artifact_id() -> str:
    return f"aga_{uuid4().hex[:12]}"


class AgentSessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()

    def create(self, session: dict[str, Any]) -> dict[str, Any]:
        session_id = str(session["sessionId"])
        with self._lock:
            self._sessions[session_id] = copy.deepcopy(session)
            return copy.deepcopy(self._sessions[session_id])

    def get(self, session_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._sessions.get(session_id)
            return copy.deepcopy(row) if row else None

    def get_scoped(self, session_id: str, *, owner_id: str, workspace_id: str) -> dict[str, Any] | None:
        row = self.get(session_id)
        if row is None:
            return None
        if row.get("ownerId") != owner_id or row.get("workspaceId") != workspace_id:
            return None
        return row

    def replace(self, session: dict[str, Any]) -> dict[str, Any]:
        session_id = str(session["sessionId"])
        with self._lock:
            if session_id not in self._sessions:
                raise KeyError(session_id)
            self._sessions[session_id] = copy.deepcopy(session)
            return copy.deepcopy(self._sessions[session_id])

    def list_summaries(
        self,
        *,
        owner_id: str,
        workspace_id: str,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        capped = max(1, min(int(limit), 200))
        with self._lock:
            rows = [
                copy.deepcopy(session)
                for session in self._sessions.values()
                if session.get("ownerId") == owner_id and session.get("workspaceId") == workspace_id
            ]
        rows.sort(key=lambda item: str(item.get("updatedAt") or ""), reverse=True)
        summaries: list[dict[str, Any]] = []
        for session in rows[:capped]:
            summaries.append(
                {
                    "sessionId": session["sessionId"],
                    "title": session["title"],
                    "status": session["status"],
                    "resourceId": session["resourceId"],
                    "agentId": session["agentId"],
                    "agentDisplayName": session["agentDisplayName"],
                    "sessionKind": str(session.get("sessionKind") or "agent"),
                    "messageCount": len(session.get("messages") or []),
                    "artifactCount": len(session.get("artifacts") or []),
                    "createdAt": session["createdAt"],
                    "updatedAt": session["updatedAt"],
                }
            )
        return summaries


agent_session_store = AgentSessionStore()
