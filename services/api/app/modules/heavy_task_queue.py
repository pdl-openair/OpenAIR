"""Heavy-task admission control / queue (S04-01).

Limits concurrent heavy operations (research-start, agent chat) so interactive
CRUD/auth stays responsive for invited users.

Usage (async)::

    async with acquire_heavy_slot(owner_id, kind="agent_chat") as ticket:
        ...

Usage (sync)::

    with acquire_heavy_slot_sync(owner_id, kind="research_start") as ticket:
        ...
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from typing import AsyncIterator, Iterator

from app.config import settings

logger = logging.getLogger(__name__)


class HeavyQueueFull(Exception):
    def __init__(self, message: str, *, retry_after: int = 5, position: int | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after
        self.position = position


@dataclass
class _Slot:
    ticket_id: str
    owner_id: str
    kind: str
    started_at: float = field(default_factory=time.monotonic)


class HeavyTaskQueue:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active: dict[str, _Slot] = {}
        self._waiting: int = 0

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return {
                "active": len(self._active),
                "waiting": self._waiting,
                "globalLimit": settings.heavy_task_global_limit,
                "perUserLimit": settings.heavy_task_per_user_limit,
            }

    def try_acquire(self, owner_id: str, *, kind: str) -> str | None:
        if not settings.heavy_task_queue_enabled:
            return f"bypass_{uuid.uuid4().hex[:8]}"
        with self._lock:
            owner_active = sum(1 for s in self._active.values() if s.owner_id == owner_id)
            if (
                len(self._active) < settings.heavy_task_global_limit
                and owner_active < settings.heavy_task_per_user_limit
            ):
                ticket_id = f"htq_{uuid.uuid4().hex[:12]}"
                self._active[ticket_id] = _Slot(ticket_id=ticket_id, owner_id=owner_id, kind=kind)
                return ticket_id
        return None

    def acquire(self, owner_id: str, *, kind: str, timeout: float | None = None) -> str:
        if not settings.heavy_task_queue_enabled:
            return f"bypass_{uuid.uuid4().hex[:8]}"

        deadline = time.monotonic() + (
            timeout if timeout is not None else settings.heavy_task_queue_wait_seconds
        )
        with self._lock:
            self._waiting += 1
        try:
            while True:
                ticket = self.try_acquire(owner_id, kind=kind)
                if ticket and not ticket.startswith("bypass_"):
                    logger.info(
                        "heavy slot acquired ticket=%s owner=%s kind=%s",
                        ticket,
                        owner_id,
                        kind,
                    )
                    return ticket
                if ticket and ticket.startswith("bypass_"):
                    return ticket
                if time.monotonic() >= deadline:
                    with self._lock:
                        position = self._waiting
                    raise HeavyQueueFull(
                        "heavy task queue is full; please retry shortly",
                        retry_after=5,
                        position=position,
                    )
                time.sleep(0.2)
        finally:
            with self._lock:
                self._waiting = max(0, self._waiting - 1)

    def release(self, ticket_id: str) -> None:
        if ticket_id.startswith("bypass_"):
            return
        with self._lock:
            self._active.pop(ticket_id, None)


_queue = HeavyTaskQueue()


def get_heavy_task_queue() -> HeavyTaskQueue:
    return _queue


@contextmanager
def acquire_heavy_slot_sync(owner_id: str, *, kind: str) -> Iterator[str]:
    ticket = _queue.acquire(owner_id, kind=kind)
    try:
        yield ticket
    finally:
        _queue.release(ticket)


@asynccontextmanager
async def acquire_heavy_slot(owner_id: str, *, kind: str) -> AsyncIterator[str]:
    # Offload blocking wait to a thread so the event loop stays responsive.
    import asyncio

    ticket = await asyncio.to_thread(_queue.acquire, owner_id, kind=kind)
    try:
        yield ticket
    finally:
        _queue.release(ticket)
