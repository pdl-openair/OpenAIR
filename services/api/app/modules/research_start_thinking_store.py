"""Durable append of one research-start session's thinking transcript."""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from collections.abc import Callable
from contextvars import ContextVar
from typing import Any

from psycopg.types.json import Json

from app.modules.research_start_thinking import fold_thinking_transcript, public_thinking_text

_logger = logging.getLogger("openair.research_start.thinking")
_WRITER: ContextVar[SessionThinkingWriter | None] = ContextVar(
    "research_start_thinking_writer",
    default=None,
)
_FLUSH_CHARS = 16
_FLUSH_SECONDS = 0.4


def current_thinking_writer() -> SessionThinkingWriter | None:
    return _WRITER.get()


def push_thinking_writer(writer: SessionThinkingWriter) -> Any:
    return _WRITER.set(writer)


def reset_thinking_writer(token: Any) -> None:
    _WRITER.reset(token)


class SessionThinkingWriter:
    """In-memory growth of the current thought, flushed onto the session row."""

    def __init__(
        self,
        *,
        request_id: str,
        phase: str,
        ordinal: int,
        persist: Callable[..., None],
    ) -> None:
        self.request_id = request_id
        self.phase = phase
        self.ordinal = ordinal
        self._persist = persist
        self._raw = ""
        self._last_flushed = ""
        self._last_flush_at = 0.0
        self._sealed = False

    def add_delta(self, delta: str) -> None:
        if self._sealed or not delta:
            return
        self._raw += delta
        public = public_thinking_text(self._raw)
        if not public or public == self._last_flushed:
            return
        grown = len(public) - len(self._last_flushed)
        punctuated = public.endswith(("。", "！", "？", "\n"))
        elapsed = time.monotonic() - self._last_flush_at
        due = grown >= _FLUSH_CHARS or punctuated or elapsed >= _FLUSH_SECONDS
        if due:
            self._write(public, seal=False)

    def note_if_empty(self, text: str) -> None:
        """Record a finished reasoning body when the call did not stream deltas."""
        if self._sealed or self._raw or not text.strip():
            return
        self._raw = text
        public = public_thinking_text(self._raw)
        if public:
            self._write(public, seal=False)

    def seal(self) -> None:
        if self._sealed:
            return
        public = public_thinking_text(self._raw)
        if not public:
            self._sealed = True
            return
        if self._write(public, seal=True):
            self._sealed = True

    def _write(self, public: str, *, seal: bool) -> bool:
        try:
            self._persist(
                request_id=self.request_id,
                phase=self.phase,
                ordinal=self.ordinal,
                text=public,
                seal=seal,
            )
        except Exception:  # noqa: BLE001 — a thinking flush must not fail the research answer
            _logger.warning(
                "research_start thinking flush failed phase=%s ordinal=%s",
                self.phase,
                self.ordinal,
                exc_info=True,
            )
            return False
        self._last_flushed = public
        self._last_flush_at = time.monotonic()
        return True


def writer_for_request(
    connect: Callable[[], Any],
    request_id: str,
    phase: str,
    ordinal: int,
) -> SessionThinkingWriter:
    def persist(**kwargs: Any) -> None:
        persist_thinking_transcript(connect, **kwargs)

    return SessionThinkingWriter(
        request_id=request_id,
        phase=phase,
        ordinal=ordinal,
        persist=persist,
    )


def persist_thinking_transcript(
    connect: Callable[[], Any],
    *,
    request_id: str,
    phase: str,
    ordinal: int,
    text: str,
    seal: bool,
) -> None:
    key = f"{request_id}:{phase}:{ordinal}"
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT s.session_id, s.owner_id, s.workspace_id, s.state, s.thinking_transcript
                     FROM research_start_requests r
                     JOIN research_start_sessions s
                       ON s.session_id = r.session_id
                      AND s.owner_id = r.owner_id
                      AND s.workspace_id = r.workspace_id
                    WHERE r.request_id = %s
                    FOR UPDATE OF s""",
                (request_id,),
            )
            row = cur.fetchone()
            if not row or row.get("state") != "running":
                return
            folded = fold_thinking_transcript(
                row.get("thinking_transcript"),
                key=key,
                text=text,
                seal=seal,
                occurred_at=datetime.now(UTC).isoformat(),
            )
            cur.execute(
                """UPDATE research_start_sessions
                      SET thinking_transcript = %s
                    WHERE session_id = %s AND owner_id = %s AND workspace_id = %s
                      AND state = 'running'""",
                (Json(folded), row["session_id"], row["owner_id"], row["workspace_id"]),
            )
