"""Execution runtime boundary: API layer submits requests; runtime layer handles invocation.

This module defines the formal boundary between the API orchestration layer
(``agent_service.py``) and the actual execution mechanisms (Claude Code CLI,
Python adapters, replay fixtures, or future cloud workers).

Phase-1 cut line
----------------
- ``InlineRuntime`` — default for local dev / simple deployments.  Runs inside
  the API process and still requires the host to have Claude CLI installed when
  ``skill_exec_mode=live``.
- ``WorkerRuntimeStub`` — documents the expected cloud worker interface but
  raises ``NotImplementedError`` because a full distributed worker queue is
  **out of Phase-1 scope**.

Design rules
------------
1. ``agent_service.py`` must NOT import ``claude_code_runner`` directly.
   It should call ``get_execution_runtime().submit()`` / ``submit_stream()``.
2. ``claude_code_runner.py`` is a *runtime internal* implementation detail.
3. API-layer concerns (task state, messages, artifacts, capability calls)
   stay in ``agent_service.py``.  Runtime concerns (LLM/CLI interaction,
   cwd, timeout) stay inside the ``ExecutionRuntime`` implementation.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.config import settings

logger = logging.getLogger(__name__)
_LIVE_PROGRESS_INTERVAL_SECONDS = 5.0


class UnobservableProviderTransportError(RuntimeError):
    """A CapabilityCall cannot use a transport whose HTTP attempts are opaque."""


class SkillDependencyPreconditionError(RuntimeError):
    """A direct selected-Skill runtime would bypass its declared input handoff."""


def require_observable_provider_transport(request: "ExecutionRequest") -> None:
    """Fail before Claude CLI can submit an unobservable Provider request."""
    if str(request.capability_call_id or "").strip():
        raise UnobservableProviderTransportError("provider_transport_attempts_unobservable")


def require_materialized_skill_dependency(request: "ExecutionRequest") -> None:
    """Fail closed when a direct runtime would bypass concrete Binding-v2 input authority."""
    if settings.skill_exec_mode == "replay" or request.mode != "execution":
        return
    skill_id = str(request.requested_skill_id or "").strip()
    if not skill_id:
        return
    from app.modules.skill_binding_source import SkillBindingSourceError, skill_ids_with_binding_inputs
    try:
        requires_binding = skill_id in skill_ids_with_binding_inputs()
    except SkillBindingSourceError as exc:
        raise SkillDependencyPreconditionError("skill_binding_contract_invalid") from exc
    if requires_binding:
        raise SkillDependencyPreconditionError("skill_binding_precondition_missing")

def _gate_user_quota(owner_id: str | None) -> None:
    """Pre-call quota gate (S03-14-03). No-op when owner is unknown (open dev mode).

    Raises :class:`QuotaExceeded` (handled globally → HTTP 402) when the user has
    no remaining quota. Replay mode bypasses the gate (no real model cost).
    """
    if not owner_id or settings.skill_exec_mode == "replay":
        return
    from app.modules.quota_service import QuotaExceeded, get_quota_service

    get_quota_service().check_and_gate(owner_id)  # raises QuotaExceeded if exhausted


def _selected_skill_uses_huiyan(request: "ExecutionRequest") -> bool:
    """Route an explicitly selected Skill according to its declared runtime."""
    if request.mode != "execution" or not request.requested_skill_id:
        return False
    override = (os.environ.get("OPENAIR_LIVE_SKILL_RUNTIME") or "").strip().lower()
    if override in {"huiyan", "huiyan_chat_completions"}:
        return True
    try:
        from app.execution.huiyan_skill_runner import _skill_contract

        metadata, _instructions = _skill_contract(request.requested_skill_id)
    except Exception:
        logger.warning("unable to read runtime contract for selected skill=%s", request.requested_skill_id)
        return False
    runtime = str(metadata.get("runtime") or "claude_code").strip().lower()
    return runtime in {"huiyan", "huiyan_chat_completions"}


@dataclass
class ExecutionRequest:
    """Payload submitted by the API layer to an ExecutionRuntime."""

    task_id: str
    message: str
    mode: str  # "creation" | "execution"
    session_id: str | None = None
    task: dict[str, Any] = field(default_factory=dict)

    # Skill / substage orchestration context
    skill_id: str | None = None
    substage_key: str | None = None
    l2_claude_name: str | None = None
    available_skills: list[str] | None = None
    spec_template: str | None = None
    output_file: str | None = None
    input_files: list[str] | None = None
    requested_skill_id: str | None = None
    l3_skills_meta: list[dict[str, str]] | None = None
    existing_l3_plan: dict[str, Any] | None = None

    # Container runtime context
    owner_id: str | None = None
    user_skill_names: list[str] | None = None
    capability_call_id: str | None = None


@dataclass
class ExecutionResult:
    """Result returned by an ExecutionRuntime to the API layer."""

    reply: str
    session_id: str | None = None
    status: str = "completed"  # completed | failed | timeout
    error: str | None = None
    runtime: str = "unknown"
    audit: dict[str, Any] = field(default_factory=dict)


class ExecutionRuntime(Protocol):
    """Protocol for execution runtimes."""

    async def submit(self, request: ExecutionRequest) -> ExecutionResult:
        ...

    async def submit_stream(self, request: ExecutionRequest) -> AsyncIterator[dict[str, Any]]:
        ...


class InlineRuntime:
    """Phase-1 default runtime: executes inside the API process.

    This is a **development / simple-deployment** path.  When
    ``skill_exec_mode=live`` it still requires the API host to have an
    interactive Claude CLI installed and logged in.  Cloud production
    deployments should eventually switch to ``runtime_type=worker``.
    """

    async def submit(self, request: ExecutionRequest) -> ExecutionResult:
        if settings.skill_exec_mode == "replay":
            return await self._submit_replay(request)
        return await self._submit_live(request)

    async def _submit_live(self, request: ExecutionRequest) -> ExecutionResult:
        _gate_user_quota(getattr(request, "owner_id", None))
        require_materialized_skill_dependency(request)
        if _selected_skill_uses_huiyan(request):
            from app.execution.huiyan_skill_runner import RUNTIME

            result = await self._run_huiyan_selected_skill(request)
            return ExecutionResult(reply=result["reply"], status="completed", runtime=RUNTIME, audit=result["audit"])
        require_observable_provider_transport(request)
        from app.execution.claude_code_runner import run_claude_code_chat

        reply, session_id, usage = await run_claude_code_chat(
            request.task,
            request.message,
            mode=request.mode,  # type: ignore[arg-type]
            resume_session_id=request.session_id,
            substage_key=request.substage_key,
            l2_claude_name=request.l2_claude_name,
            available_skills=request.available_skills,
            spec_template=request.spec_template,
            output_file=request.output_file,
            input_files=request.input_files,
            requested_skill_id=request.requested_skill_id,
            l3_skills_meta=request.l3_skills_meta,
            existing_l3_plan=request.existing_l3_plan,
        )
        from llm_core.token_usage import to_wire_usage

        return ExecutionResult(
            reply=reply,
            session_id=session_id,
            status="completed",
            runtime="claude_code",
            audit={"usage": to_wire_usage(usage)} if usage else {},
        )

    async def _submit_replay(self, request: ExecutionRequest) -> ExecutionResult:
        from app.execution.replay_runner import replay_chat_reply

        try:
            reply = replay_chat_reply(request.task, request.message, request.skill_id)
            return ExecutionResult(reply=reply, status="completed", runtime="replay")
        except OSError as exc:
            return ExecutionResult(
                reply=f"回放失败：{exc}",
                status="failed",
                error=str(exc),
                runtime="replay",
            )

    async def submit_stream(self, request: ExecutionRequest) -> AsyncIterator[dict[str, Any]]:
        if settings.skill_exec_mode == "replay":
            async for ev in self._stream_replay(request):
                yield ev
            return

        async for ev in self._stream_live(request):
            yield ev

    async def _stream_live(self, request: ExecutionRequest) -> AsyncIterator[dict[str, Any]]:
        _gate_user_quota(getattr(request, "owner_id", None))
        require_materialized_skill_dependency(request)
        if _selected_skill_uses_huiyan(request):
            from app.execution.huiyan_skill_runner import RUNTIME

            started_at = time.monotonic()
            execution = asyncio.create_task(self._run_huiyan_selected_skill(request))
            yield {
                "type": "progress",
                "text": "模型正在整理本次任务的最小输入并生成可审阅产物。",
            }
            try:
                while not execution.done():
                    done, _pending = await asyncio.wait({execution}, timeout=_LIVE_PROGRESS_INTERVAL_SECONDS)
                    if done:
                        break
                    elapsed = max(1, int(time.monotonic() - started_at))
                    yield {
                        "type": "progress",
                        "text": f"模型仍在执行，已等待 {elapsed} 秒；完成后会写入本次产物。",
                    }
                result = execution.result()
                yield {"type": "assistant_delta", "text": result["reply"]}
                yield {"type": "done", "sessionId": None, "runtime": RUNTIME, "audit": result["audit"]}
            except Exception as exc:  # the orchestration layer persists a real failed call
                yield {"type": "done", "sessionId": None, "runtime": RUNTIME, "error": str(exc)}
            finally:
                if not execution.done():
                    execution.cancel()
            return
        require_observable_provider_transport(request)
        from app.execution.claude_code_runner import run_claude_code_chat_stream

        async for event in run_claude_code_chat_stream(
            request.task,
            request.message,
            mode=request.mode,  # type: ignore[arg-type]
            resume_session_id=request.session_id,
            substage_key=request.substage_key,
            l2_claude_name=request.l2_claude_name,
            available_skills=request.available_skills,
            spec_template=request.spec_template,
            output_file=request.output_file,
            input_files=request.input_files,
            requested_skill_id=request.requested_skill_id,
            l3_skills_meta=request.l3_skills_meta,
            existing_l3_plan=request.existing_l3_plan,
        ):
            yield event

    async def _run_huiyan_selected_skill(self, request: ExecutionRequest) -> dict[str, Any]:
        """Keep external Skill execution off the API event loop.

        Task reads and refresh recovery must remain responsive while a live
        provider generates an artifact. The runner has no request-local state,
        so it is safe to own a dedicated event loop in a worker thread.
        """
        from app.execution.huiyan_skill_runner import run_huiyan_selected_skill

        return await asyncio.to_thread(lambda: asyncio.run(run_huiyan_selected_skill(request)))

    async def _stream_replay(self, request: ExecutionRequest) -> AsyncIterator[dict[str, Any]]:
        from app.execution.replay_runner import replay_chat_reply

        try:
            reply = replay_chat_reply(request.task, request.message, request.skill_id)
            yield {"type": "assistant_delta", "text": reply}
            yield {"type": "done", "sessionId": None}
        except OSError as exc:
            fail_text = f"回放失败：{exc}"
            yield {"type": "assistant_delta", "text": fail_text}
            yield {"type": "done", "sessionId": None, "error": str(exc)}


class WorkerRuntimeStub:
    """Cloud worker runtime stub for Phase-1.

    **N/A:** A full distributed worker queue with task isolation, scheduling,
    and autoscaling is out of Phase-1 scope.  This stub exists only to:
    1. Document the expected worker interface.
    2. Prevent accidental production deployments with ``runtime_type=worker``
       without implementing the actual worker backend.
    """

    async def submit(self, request: ExecutionRequest) -> ExecutionResult:
        raise NotImplementedError(
            "WorkerRuntime.submit() is not implemented in Phase-1. "
            "Distributed worker execution requires a worker queue, job dispatcher, "
            "and durable state write-back path that are out of Phase-1 scope. "
            "For local development use OPENAIR_RUNTIME_TYPE=inline (default)."
        )

    async def submit_stream(self, request: ExecutionRequest) -> AsyncIterator[dict[str, Any]]:
        raise NotImplementedError(
            "WorkerRuntime.submit_stream() is not implemented in Phase-1. "
            "See OPENAIR_RUNTIME_TYPE=inline for local development."
        )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

_runtime_instance: ExecutionRuntime | None = None


def get_execution_runtime() -> ExecutionRuntime:
    """Return the current execution runtime based on configuration.

    ``OPENAIR_RUNTIME_TYPE``:
      - ``inline`` (default) → ``InlineRuntime``
      - ``container`` → ``ContainerRuntime`` (Docker per-task isolation)
      - ``worker`` → ``WorkerRuntimeStub`` (Phase-1 placeholder)
    """
    global _runtime_instance
    if _runtime_instance is not None:
        return _runtime_instance

    rtype = settings.runtime_type
    if rtype == "container":
        from app.execution.container_runtime import ContainerRuntime
        _runtime_instance = ContainerRuntime()
        logger.info("execution_runtime=ContainerRuntime")
    elif rtype == "worker":
        _runtime_instance = WorkerRuntimeStub()
        logger.info("execution_runtime=WorkerRuntimeStub (Phase-1 stub)")
    else:
        _runtime_instance = InlineRuntime()
        logger.info("execution_runtime=InlineRuntime mode=%s", settings.skill_exec_mode)
    return _runtime_instance


def reset_execution_runtime() -> None:
    """Reset the cached runtime instance (useful for tests)."""
    global _runtime_instance
    _runtime_instance = None
