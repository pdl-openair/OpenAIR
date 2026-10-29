"""Execution layer: runtime boundary and internal runners.

API-layer code (``agent_service.py``) should import only from
``app.execution.runtime_boundary`` and never call ``claude_code_runner``
directly.
"""

from app.execution.runtime_boundary import (
    ExecutionRequest,
    ExecutionResult,
    ExecutionRuntime,
    InlineRuntime,
    WorkerRuntimeStub,
    get_execution_runtime,
    reset_execution_runtime,
)

__all__ = [
    "ExecutionRequest",
    "ExecutionResult",
    "ExecutionRuntime",
    "InlineRuntime",
    "WorkerRuntimeStub",
    "get_execution_runtime",
    "reset_execution_runtime",
]
