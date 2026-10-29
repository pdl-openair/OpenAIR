"""API-owned canonical Research WorkUnit executor for explicit inline/fallback modes.

The durable worker remains the production default.  When operators explicitly
select ``inline`` or ``worker_with_fallback``, API processes may participate as
an additional executor, but only by calling the exact durable dispatch runner
used by the worker.  Database claim/lease/fence state remains the single
execution-ownership authority.
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket

from app.config import settings

logger = logging.getLogger("openair")


def api_workunit_executor_enabled() -> bool:
    return (
        settings.research_orchestration_v1
        and settings.persistence_backend == "postgres"
        and settings.workunit_execution_mode in {"inline", "worker_with_fallback"}
    )


async def run_api_workunit_executor(stop_event: asyncio.Event) -> None:
    """Poll at most one canonical dispatch at a time without blocking HTTP."""
    if not api_workunit_executor_enabled():
        return

    # Import lazily so normal worker mode does not load worker-only execution
    # wiring into the API process.  The shared service image intentionally owns
    # both modules; the called runner still uses the same durable claim/fence.
    from worker.entrypoint import claim_and_run_research_dispatch

    lease_owner = f"api-workunit:{socket.gethostname()}:{os.getpid()}"
    logger.info(
        "[workunit] api executor enabled mode=%s fallbackAfterSeconds=%d owner=%s",
        settings.workunit_execution_mode,
        settings.workunit_fallback_after_seconds,
        lease_owner,
    )

    while not stop_event.is_set():
        try:
            handled = await asyncio.to_thread(
                claim_and_run_research_dispatch,
                execution_role="api",
                lease_owner=lease_owner,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Execution ownership is durable.  A poll failure must degrade this
            # executor, never API availability or the user's Workbench access.
            logger.warning("[workunit] api executor poll failed: %s", exc)
            handled = False

        if handled:
            # Yield to HTTP handlers and then look for the next item.  Processing
            # remains strictly one dispatch per iteration.
            await asyncio.sleep(0)
            continue
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=1.0)
        except TimeoutError:
            pass
