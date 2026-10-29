"""Callback delivery registry plus durable callback authority.

The in-memory futures/queues bridge HTTP callbacks to the coroutine waiting in
this API process.  They are deliberately *not* the authentication source.  In
PostgreSQL per-task-token mode, callback identity is loaded from
``openair_worker_callback_authority``; no replica-local fallback is allowed.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
from typing import Any

from app.config import settings
from app.execution.worker_callback_authority import (
    WorkerCallbackAuthority,
    WorkerCallbackAuthorityRepository,
)

logger = logging.getLogger(__name__)


class WorkerCallbackRegistry:
    """Singleton registry for worker execution callback delivery."""

    def __init__(
        self,
        authority_repository: WorkerCallbackAuthorityRepository | None = None,
    ) -> None:
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._queues: dict[str, asyncio.Queue[dict[str, Any] | None]] = {}
        self._authorities: dict[str, WorkerCallbackAuthority] = {}
        self._authority_repository = authority_repository

    @staticmethod
    def _durable_authority_required() -> bool:
        return bool(
            settings.worker_per_task_token
            and settings.persistence_backend == "postgres"
        )

    def _repository(self) -> WorkerCallbackAuthorityRepository:
        if self._authority_repository is None:
            self._authority_repository = WorkerCallbackAuthorityRepository()
        return self._authority_repository

    def register_authority(
        self,
        execution_id: str,
        *,
        task_id: str,
        owner_id: str,
        execution_epoch: str,
        expires_at: datetime,
    ) -> WorkerCallbackAuthority:
        authority = WorkerCallbackAuthority(
            execution_id=execution_id,
            task_id=task_id,
            owner_id=owner_id,
            execution_epoch=execution_epoch,
            expires_at=expires_at,
        )
        existing = self._authorities.get(execution_id)
        if existing is not None and existing != authority:
            raise RuntimeError("callback_authority_identity_conflict")
        if self._durable_authority_required():
            # Fail closed: container launch must not proceed if the durable
            # callback identity cannot be committed.
            self._repository().issue(authority)
        self._authorities[execution_id] = authority
        return authority

    def _prepare_authority(
        self,
        execution_id: str,
        *,
        task_id: str,
        owner_id: str,
        execution_epoch: str,
        expires_at: datetime | None,
    ) -> None:
        identity = (task_id, owner_id, execution_epoch)
        if settings.worker_per_task_token and (
            not all(str(value or "").strip() for value in identity)
            or expires_at is None
        ):
            raise ValueError("callback authority identity is incomplete")
        if all(str(value or "").strip() for value in identity) and expires_at is not None:
            self.register_authority(
                execution_id,
                task_id=task_id,
                owner_id=owner_id,
                execution_epoch=execution_epoch,
                expires_at=expires_at,
            )

    def get_authority(self, execution_id: str) -> WorkerCallbackAuthority | None:
        if self._durable_authority_required():
            # Never fall back to replica-local state in production PostgreSQL
            # mode.  Missing DB mapping means no callback authority.
            return self._repository().load_active(execution_id)
        authority = self._authorities.get(execution_id)
        if authority is None or authority.expires_at <= datetime.now(timezone.utc):
            return None
        return authority

    def register(
        self,
        execution_id: str,
        task_id: str = "",
        owner_id: str = "",
        execution_epoch: str = "",
        expires_at: datetime | None = None,
    ) -> asyncio.Future[dict[str, Any]]:
        self._prepare_authority(
            execution_id,
            task_id=task_id,
            owner_id=owner_id,
            execution_epoch=execution_epoch,
            expires_at=expires_at,
        )
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[execution_id] = future
        logger.info("callback registry: registered execution_id=%s", execution_id)
        return future

    def get_task_id(self, execution_id: str) -> str | None:
        authority = self.get_authority(execution_id)
        return authority.task_id if authority is not None else None

    def resolve(self, execution_id: str, result: dict[str, Any]) -> None:
        future = self._pending.pop(execution_id, None)
        if future is None:
            logger.warning("callback registry: no pending Future for execution_id=%s", execution_id)
            return
        if not future.done():
            future.set_result(result)
            logger.info("callback registry: resolved execution_id=%s", execution_id)
        self._signal_stream_end(execution_id)

    def register_stream(
        self,
        execution_id: str,
        task_id: str = "",
        owner_id: str = "",
        execution_epoch: str = "",
        expires_at: datetime | None = None,
    ) -> asyncio.Queue[dict[str, Any] | None]:
        self._prepare_authority(
            execution_id,
            task_id=task_id,
            owner_id=owner_id,
            execution_epoch=execution_epoch,
            expires_at=expires_at,
        )
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self._queues[execution_id] = queue
        logger.info("callback registry: registered stream for execution_id=%s", execution_id)
        return queue

    def push_event(self, execution_id: str, event: dict[str, Any]) -> None:
        queue = self._queues.get(execution_id)
        if queue is None:
            logger.warning("callback registry: no stream Queue for execution_id=%s", execution_id)
            return
        queue.put_nowait(event)

    def _signal_stream_end(self, execution_id: str) -> None:
        queue = self._queues.get(execution_id)
        if queue is not None:
            queue.put_nowait(None)

    def _revoke_authority(self, execution_id: str) -> None:
        authority = self._authorities.pop(execution_id, None)
        if self._durable_authority_required():
            self._repository().revoke(
                execution_id,
                authority.execution_epoch if authority is not None else None,
            )

    def cleanup(self, execution_id: str) -> None:
        self._pending.pop(execution_id, None)
        self._queues.pop(execution_id, None)
        self._revoke_authority(execution_id)
        logger.info("callback registry: cleaned up execution_id=%s", execution_id)

    def cancel(self, execution_id: str) -> None:
        future = self._pending.pop(execution_id, None)
        if future and not future.done():
            future.cancel()
        self._signal_stream_end(execution_id)
        self._queues.pop(execution_id, None)
        self._revoke_authority(execution_id)


_registry: WorkerCallbackRegistry | None = None


def get_callback_registry() -> WorkerCallbackRegistry:
    global _registry
    if _registry is None:
        _registry = WorkerCallbackRegistry()
    return _registry
