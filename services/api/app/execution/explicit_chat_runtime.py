"""Pure runtime boundary for an explicitly user-triggered collaboration turn.

It deliberately owns no task, call, message, artifact, session, or execution
run persistence.  The API route records those facts through TaskRepository.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping

from app.execution.replay_runner import replay_chat_reply
from app.execution.workbench_collaboration_agent import (
    CollaborationAgentResult,
    CollaborationAgentUnavailable,
    run_workbench_collaboration_agent,
)


class ExplicitChatRuntimeError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ExplicitChatRuntimeResult:
    reply: str
    runtime: str
    source_mode: str
    session_id: str | None = None
    source_metadata: dict[str, Any] | None = None
    proposed_action: dict[str, Any] | None = None


async def run_explicit_chat_runtime(
    *,
    task: dict[str, Any],
    message: str,
    skill_id: str | None,
    source_mode: str,
    actions_snapshot: Mapping[str, Any] | None = None,
) -> ExplicitChatRuntimeResult:
    """Run one chat turn without persistence or implicit replay fallback."""
    if source_mode == "replay":
        return ExplicitChatRuntimeResult(
            reply=replay_chat_reply(task, message, skill_id),
            runtime="replay",
            source_mode="replay",
        )
    if source_mode != "live":
        raise ExplicitChatRuntimeError("unsupported_source_mode")

    # This seam is test-only and must never silently replace a production live
    # provider.  It proves policy/routing/CAS behavior, not G-LIVE.
    seam = os.environ.get("OPENAIR_IB01_TEST_LIVE_CHAT_SEAM", "").strip()
    if seam:
        if os.environ.get("OPENAIR_ENV") != "test":
            raise ExplicitChatRuntimeError("test_runtime_seam_forbidden")
        if seam == "controlled_failure":
            raise ExplicitChatRuntimeError("controlled_live_runtime_failure")
        if seam == "controlled_unknown":
            raise ExplicitChatRuntimeError("controlled_live_runtime_unknown")
        if seam == "controlled_result":
            return ExplicitChatRuntimeResult(
                reply="Live runtime controlled test result.",
                runtime="claude_code",
                source_mode="live",
                source_metadata={
                    "runtimeSeam": "test_only_controlled_result",
                    "providerDispatch": "not_called",
                },
            )
        raise ExplicitChatRuntimeError("invalid_test_runtime_seam")

    # Workbench composer reception uses an observable chat-completions agent
    # (not Claude CLI CapabilityCall transport). The agent may propose
    # Workbench-authoritative actions; the route applies them only when allowed.
    try:
        result: CollaborationAgentResult = await run_workbench_collaboration_agent(
            task=task,
            message=message,
            actions_snapshot=actions_snapshot,
        )
    except CollaborationAgentUnavailable as exc:
        raise ExplicitChatRuntimeError(exc.code) from exc

    return ExplicitChatRuntimeResult(
        reply=result.reply,
        runtime=result.runtime,
        source_mode="live",
        source_metadata=result.source_metadata,
        proposed_action=result.proposed_action,
    )
