"""Durable continuation lease support shared by research dispatch workers."""

from __future__ import annotations

import logging
import threading
from typing import Any


class DispatchContinuationLeaseKeeper:
    """Keep one continuation fence live while its downstream work is running."""

    def __init__(
        self,
        repository: Any,
        fence: Any,
        *,
        lease_seconds: int,
    ) -> None:
        self._renew = getattr(repository, "renew_dispatch_continuation", None)
        self._fence = fence
        self._lease_seconds = max(1, int(lease_seconds))
        self._renew_interval = max(0.1, min(10.0, self._lease_seconds / 3.0))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.lost = False

    def __enter__(self) -> DispatchContinuationLeaseKeeper:
        if callable(self._renew):
            if not self._try_renew():
                return self
            self._thread = threading.Thread(
                target=self._run,
                name=f"continuation-lease-{self._fence.dispatch_id}",
                daemon=True,
            )
            self._thread.start()
        return self

    def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        self.stop()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def _try_renew(self) -> bool:
        renew = self._renew
        if not callable(renew):
            return False
        try:
            renewed = bool(renew(self._fence, lease_seconds=self._lease_seconds))
        except Exception:
            logging.getLogger("openair").exception(
                "dispatch continuation lease renewal failed dispatch=%s task=%s",
                self._fence.dispatch_id,
                self._fence.task_id,
            )
            self.lost = True
            return False
        if not renewed:
            logging.getLogger("openair").warning(
                "dispatch continuation lease lost dispatch=%s task=%s",
                self._fence.dispatch_id,
                self._fence.task_id,
            )
            self.lost = True
            return False
        return True

    def _run(self) -> None:
        while not self._stop.wait(self._renew_interval):
            if not self._try_renew():
                return


__all__ = ["DispatchContinuationLeaseKeeper"]
