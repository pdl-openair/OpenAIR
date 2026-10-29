"""Lightweight persisted Intent Session store for research start routing."""

from __future__ import annotations

import copy
import json
import re
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from collections.abc import Iterator
from uuid import uuid4
from typing import Any


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


_SESSION_ID_RE = re.compile(r"^intent_[0-9a-f]{12}$")


def is_valid_intent_session_id(session_id: str) -> bool:
    return bool(_SESSION_ID_RE.fullmatch(session_id))


def _turn_from_session(session: dict[str, Any]) -> dict[str, Any]:
    return {
        "clientRequestId": session.get("clientRequestId"),
        "rawInput": session.get("rawInput"),
        "sourcePage": session.get("sourcePage"),
        "selectedHint": session.get("selectedHint"),
        "presetQuery": session.get("presetQuery"),
        "createdAt": session.get("createdAt"),
    }


def _session_turns(session: dict[str, Any]) -> list[dict[str, Any]]:
    turns = session.get("turns")
    if isinstance(turns, list):
        return [copy.deepcopy(turn) for turn in turns if isinstance(turn, dict)]
    return [_turn_from_session(session)]


def _combined_raw_input(turns: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for turn in turns:
        value = turn.get("rawInput")
        if isinstance(value, str):
            normalized = value.strip()
            if normalized and normalized not in parts:
                parts.append(normalized)
    return "\n\n".join(parts)


class IntentSessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, dict[str, Any]] = {}
        self._request_index: dict[tuple[str, str, str], str] = {}
        self._persist_dir: Path | None = None
        self._lock = threading.RLock()

    def enable_persistence(self, persist_dir: Path) -> None:
        self._persist_dir = persist_dir
        persist_dir.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def transaction(self) -> Iterator[None]:
        with self._lock:
            yield

    def load_persisted_sessions(self) -> int:
        if not self._persist_dir or not self._persist_dir.exists():
            return 0
        loaded = 0
        with self._lock:
            for fp in self._persist_dir.glob("*.json"):
                try:
                    session = json.loads(fp.read_text(encoding="utf-8"))
                except Exception:
                    continue
                session_id = session.get("sessionId")
                if not isinstance(session_id, str) or not is_valid_intent_session_id(session_id):
                    continue
                self._sessions[session_id] = session
                client_request_id = session.get("clientRequestId")
                owner_id = session.get("ownerId")
                workspace_id = session.get("workspaceId")
                if all(isinstance(v, str) and v for v in (client_request_id, owner_id, workspace_id)):
                    self._request_index[(owner_id, workspace_id, client_request_id)] = session_id
                for turn in _session_turns(session):
                    turn_client_request_id = turn.get("clientRequestId")
                    if isinstance(turn_client_request_id, str) and turn_client_request_id:
                        self._request_index[(owner_id, workspace_id, turn_client_request_id)] = session_id
                loaded += 1
        return loaded

    def get_by_request(self, *, owner_id: str, workspace_id: str, client_request_id: str) -> dict[str, Any] | None:
        with self._lock:
            session_id = self._request_index.get((owner_id, workspace_id, client_request_id))
            if not session_id:
                return None
            session = self._sessions.get(session_id)
            return copy.deepcopy(session) if session else None

    def get(self, session_id: str) -> dict[str, Any] | None:
        with self._lock:
            if not is_valid_intent_session_id(session_id):
                return None
            session = self._sessions.get(session_id)
            return copy.deepcopy(session) if session else None

    def get_scoped(self, *, session_id: str, owner_id: str, workspace_id: str) -> dict[str, Any] | None:
        with self._lock:
            if not is_valid_intent_session_id(session_id):
                return None
            session = self._sessions.get(session_id)
            if not session:
                return None
            if session.get("ownerId") != owner_id or session.get("workspaceId") != workspace_id:
                return None
            return copy.deepcopy(session)

    def create_or_update(
        self,
        *,
        owner_id: str,
        workspace_id: str,
        created_by: str,
        client_request_id: str,
        raw_input: str,
        source_page: str,
        selected_hint: str | None = None,
        preset_query: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        now = _utc_now()
        with self._lock:
            existing_id = self._request_index.get((owner_id, workspace_id, client_request_id))
            if existing_id and existing_id in self._sessions:
                session = copy.deepcopy(self._sessions[existing_id])
                session.update(
                    {
                        "rawInput": raw_input,
                        "sourcePage": source_page,
                        "selectedHint": selected_hint,
                        "presetQuery": preset_query,
                        "failureMessage": None,
                        "updatedAt": now,
                    }
                )
            elif session_id is not None:
                if not is_valid_intent_session_id(session_id):
                    raise ValueError(f"invalid intent session id: {session_id!r}")
                existing_session = self._sessions.get(session_id)
                if (
                    not existing_session
                    or existing_session.get("ownerId") != owner_id
                    or existing_session.get("workspaceId") != workspace_id
                ):
                    raise KeyError(session_id)
                turns = _session_turns(existing_session)
                if all(turn.get("clientRequestId") != client_request_id for turn in turns):
                    turns.append(
                        {
                            "clientRequestId": client_request_id,
                            "rawInput": raw_input,
                            "sourcePage": source_page,
                            "selectedHint": selected_hint,
                            "presetQuery": preset_query,
                            "createdAt": now,
                        }
                    )
                session = copy.deepcopy(existing_session)
                session.update(
                    {
                        "clientRequestId": client_request_id,
                        "rawInput": _combined_raw_input(turns),
                        "sourcePage": source_page,
                        "selectedHint": selected_hint if selected_hint is not None else session.get("selectedHint"),
                        "presetQuery": preset_query if preset_query is not None else session.get("presetQuery"),
                        "turns": turns,
                        "outcome": None,
                        "decision": None,
                        "result": None,
                        "failureMessage": None,
                        "updatedAt": now,
                    }
                )
            else:
                session = {
                    "sessionId": f"intent_{uuid4().hex[:12]}",
                    "ownerId": owner_id,
                    "workspaceId": workspace_id,
                    "createdBy": created_by,
                    "clientRequestId": client_request_id,
                    "rawInput": raw_input,
                    "sourcePage": source_page,
                    "selectedHint": selected_hint,
                    "presetQuery": preset_query,
                    "outcome": None,
                    "taskId": None,
                    "decision": None,
                    "result": None,
                    "turns": [
                        {
                            "clientRequestId": client_request_id,
                            "rawInput": raw_input,
                            "sourcePage": source_page,
                            "selectedHint": selected_hint,
                            "presetQuery": preset_query,
                            "createdAt": now,
                        }
                    ],
                    "createdAt": now,
                    "updatedAt": now,
                }
            self._save_locked(session)
            return copy.deepcopy(session)

    def create_catalog_resource_intent(
        self,
        *,
        owner_id: str,
        workspace_id: str,
        created_by: str,
        resource_id: str,
        resource_version: str,
        capability_id: str,
    ) -> dict[str, Any]:
        values = {
            "owner_id": owner_id,
            "workspace_id": workspace_id,
            "created_by": created_by,
            "resource_id": resource_id,
            "resource_version": resource_version,
            "capability_id": capability_id,
        }
        if any(not isinstance(value, str) or not value.strip() for value in values.values()):
            raise ValueError("catalog resource intent fields must be non-empty strings")

        # Each explicit Resource Plaza launch is a new research-start attempt.
        # The catalog intent itself is the idempotency boundary for promote; reusing
        # one deterministic request id here would permanently route later launches
        # back to the first durable session, including terminal/completed sessions.
        client_request_id = f"catalog:{resource_id}:{resource_version}:{uuid4().hex[:12]}"
        now = _utc_now()
        with self._lock:
            session = {
                "sessionId": f"intent_{uuid4().hex[:12]}",
                "ownerId": owner_id,
                "workspaceId": workspace_id,
                "createdBy": created_by,
                "clientRequestId": client_request_id,
                "rawInput": "",
                "sourcePage": "resource_plaza",
                "selectedHint": capability_id,
                "selectedResourceId": resource_id,
                "selectedResourceVersion": resource_version,
                "selectedCapabilityId": capability_id,
                "presetQuery": None,
                "outcome": None,
                "taskId": None,
                "decision": None,
                "result": None,
                "turns": [
                    {
                        "clientRequestId": client_request_id,
                        "rawInput": "",
                        "sourcePage": "resource_plaza",
                        "selectedHint": capability_id,
                        "createdAt": now,
                    }
                ],
                "createdAt": now,
                "updatedAt": now,
            }
            self._save_locked(session)
            return copy.deepcopy(session)

    def complete(
        self,
        session_id: str,
        *,
        outcome: str,
        decision: dict[str, Any],
        result: dict[str, Any],
        task_id: str | None = None,
    ) -> dict[str, Any]:
        now = _utc_now()
        with self._lock:
            session = copy.deepcopy(self._sessions[session_id])
            session.update(
                {
                    "outcome": outcome,
                    "taskId": task_id,
                    "decision": copy.deepcopy(decision),
                    "result": copy.deepcopy(result),
                    "failureMessage": None,
                    "updatedAt": now,
                }
            )
            self._save_locked(session)
            return copy.deepcopy(session)

    def fail(self, session_id: str, *, user_message: str) -> dict[str, Any] | None:
        """Persist a recoverable terminal failure for a session already created."""
        with self._lock:
            session = self._sessions.get(session_id)
            if not session:
                return None
            updated = copy.deepcopy(session)
            updated.update(
                {
                    "failureMessage": user_message,
                    "updatedAt": _utc_now(),
                }
            )
            self._save_locked(updated)
            return copy.deepcopy(updated)

    def _save_locked(self, session: dict[str, Any]) -> None:
        session_id = session["sessionId"]
        if not is_valid_intent_session_id(session_id):
            raise ValueError(f"invalid intent session id: {session_id!r}")
        self._sessions[session_id] = copy.deepcopy(session)
        self._request_index[(session["ownerId"], session["workspaceId"], session["clientRequestId"])] = session_id
        for turn in _session_turns(session):
            client_request_id = turn.get("clientRequestId")
            if isinstance(client_request_id, str) and client_request_id:
                self._request_index[(session["ownerId"], session["workspaceId"], client_request_id)] = session_id
        if not self._persist_dir:
            return
        dest = (self._persist_dir / f"{session_id}.json").resolve()
        persist_root = self._persist_dir.resolve()
        if persist_root != dest.parent:
            raise ValueError(f"invalid intent session id: {session_id!r}")
        dest.write_text(json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8")


intent_session_store = IntentSessionStore()
