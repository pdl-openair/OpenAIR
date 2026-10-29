"""Trusted worker entry point for legacy durable orchestration.

The public internal HTTP route is intentionally fail closed. This module is
imported by the independently launched worker process and preserves the
existing repository lease, fence, collaboration, and unknown-result logic.
"""

from __future__ import annotations

from typing import Any

from app.routes.internal import DurableLeaseIn, _execute_durable_orchestration_in_worker


def process_durable_worker_run(run_id: str, worker_id: str, lease_epoch: int) -> dict[str, Any]:
    return _execute_durable_orchestration_in_worker(
        run_id,
        DurableLeaseIn(workerId=worker_id, leaseEpoch=lease_epoch),
    )
