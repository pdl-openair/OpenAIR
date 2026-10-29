"""Own live execution tasks independently from a browser stream subscription."""

from __future__ import annotations

import asyncio
from collections import defaultdict

_runs_by_task: dict[str, set[asyncio.Task[None]]] = defaultdict(set)


def track_background_execution(task_id: str, execution: asyncio.Task[None]) -> None:
    """Keep a strong reference until execution reaches a terminal state."""
    _runs_by_task[task_id].add(execution)

    def _remove(completed: asyncio.Task[None]) -> None:
        runs = _runs_by_task.get(task_id)
        if not runs:
            return
        runs.discard(completed)
        if not runs:
            _runs_by_task.pop(task_id, None)

    execution.add_done_callback(_remove)


def cancel_task_background_executions(task_id: str) -> int:
    """Cancel only when a user explicitly stops a task."""
    executions = list(_runs_by_task.get(task_id, set()))
    for execution in executions:
        if not execution.done():
            execution.cancel()
    return len(executions)
