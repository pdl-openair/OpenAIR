"""Worker-side Claude Code runner.

Reuses prompt/options construction logic from ``app.execution.claude_code_runner``,
but adapted for the container context where:
- System skills are at ``/app/.claude/skills/``
- User skills are at ``/app/.claude/user-skills/``
- Working directory is ``/workspace/{task_id}/``
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Worker-specific execution context
# ---------------------------------------------------------------------------

_WORKSPACE_ROOT = os.environ.get("OPENAIR_WORKSPACE_ROOT", "/workspace")


# ---------------------------------------------------------------------------
# Re-export prompt building from the shared module
# ---------------------------------------------------------------------------

def _build_worker_system_prompt(
    task: dict[str, Any],
    mode: str,
    *,
    substage_key: str | None = None,
    l2_claude_name: str | None = None,
    available_skills: list[str] | None = None,
    spec_template: str | None = None,
    output_file: str | None = None,
    input_files: list[str] | None = None,
    enabled_skills: list[str] | None = None,
    l3_skills_meta: list[dict[str, str]] | None = None,
    existing_l3_plan: dict[str, Any] | None = None,
) -> str:
    """Build system prompt using the shared claude_code_runner logic."""
    from app.execution.claude_code_runner import _build_system_prompt

    return _build_system_prompt(
        task, mode,
        substage_key=substage_key,
        l2_claude_name=l2_claude_name,
        available_skills=available_skills,
        spec_template=spec_template,
        output_file=output_file,
        input_files=input_files,
        enabled_skills=enabled_skills,
        l3_skills_meta=l3_skills_meta,
        existing_l3_plan=existing_l3_plan,
    )


def _build_worker_options(
    task: dict[str, Any],
    mode: str,
    resume_session_id: str | None,
    *,
    substage_key: str | None = None,
    l2_claude_name: str | None = None,
    available_skills: list[str] | None = None,
    spec_template: str | None = None,
    output_file: str | None = None,
    input_files: list[str] | None = None,
    requested_skill_id: str | None = None,
    l3_skills_meta: list[dict[str, str]] | None = None,
    existing_l3_plan: dict[str, Any] | None = None,
    permission_mode: str = "bypassPermissions",
    user_skill_names: list[str] | None = None,
):
    """Build ClaudeAgentOptions for the worker context."""
    from claude_agent_sdk import ClaudeAgentOptions

    from app.execution.claude_code_runner import _resolve_skills_for_turn

    enabled_skills = _resolve_skills_for_turn(
        mode,
        l2_claude_name=l2_claude_name,
        available_skills=available_skills,
        requested_skill_id=requested_skill_id,
    )

    # Append user skill names to enabled_skills
    if user_skill_names:
        seen = set(enabled_skills)
        for us in user_skill_names:
            if us not in seen:
                seen.add(us)
                enabled_skills.append(us)

    # Resolve cwd. Creation mode drives the new-task-planning skill, which
    # Reads repo-root-relative config (schemas/packs.yaml,
    # schemas/{pack}/*.yaml) — so it must run with cwd = repo root, mirroring
    # the inline runtime (worker image keeps schemas/ under /app). Execution
    # mode works inside the isolated per-task workspace.
    task_id = task.get("taskId") or ""
    if mode == "creation":
        from app.modules.repo_paths import repo_root
        cwd = str(repo_root())
    else:
        cwd = os.path.join(_WORKSPACE_ROOT, task_id) if task_id else _WORKSPACE_ROOT
    os.makedirs(cwd, exist_ok=True)

    logger.info(
        "worker_runner mode=%s enabled_skills=%s cwd=%s permission_mode=%s",
        mode, enabled_skills, cwd, permission_mode,
    )

    system_prompt = _build_worker_system_prompt(
        task, mode,
        substage_key=substage_key,
        l2_claude_name=l2_claude_name,
        available_skills=available_skills,
        spec_template=spec_template,
        output_file=output_file,
        input_files=input_files,
        enabled_skills=enabled_skills,
        l3_skills_meta=l3_skills_meta,
        existing_l3_plan=existing_l3_plan,
    )

    option_kwargs: dict[str, Any] = {
        "cwd": cwd,
        "system_prompt": system_prompt,
        "resume": resume_session_id,
        "permission_mode": permission_mode,
        "setting_sources": ["project", "local", "user"],
        "skills": enabled_skills,
        "stderr": lambda line: logger.warning("[claude-cli stderr] %s", line),
    }

    # Add allowed tools for non-bypass modes (U-03: shared constant from claude_turn)
    if permission_mode in ("acceptEdits", "default", "dontAsk"):
        from llm_core.claude_turn import HEADLESS_ALLOWED_TOOLS

        option_kwargs["allowed_tools"] = list(HEADLESS_ALLOWED_TOOLS)

    # Add task workspace as add_dirs
    if task_id:
        ws_dir = os.path.join(_WORKSPACE_ROOT, task_id)
        if os.path.isdir(ws_dir):
            option_kwargs["add_dirs"] = [ws_dir]

    # Unified mmp-platform MCP server (HTTP). Mirrors the inline runtime
    # (_build_options in claude_code_runner.py): container_runtime injects
    # OPENAIR_MMP_MCP_URL into worker env; without this the mmp-* skills cannot
    # reach their platform tools. (U-01 fix: this was previously missing.)
    if settings.mmp_mcp_url:
        option_kwargs["mcp_servers"] = {
            "mmp-platform": {"type": "http", "url": settings.mmp_mcp_url}
        }
        logger.info("worker_runner mcp_servers=mmp-platform(%s)", settings.mmp_mcp_url)

    # Opt-in CLI override (same semantics as inline).
    cli_override = (os.environ.get("OPENAIR_CLAUDE_CLI_PATH") or "").strip()
    if cli_override:
        cli_p = Path(cli_override).expanduser()
        if cli_p.is_file():
            option_kwargs["cli_path"] = str(cli_p.resolve())
        else:
            logger.warning("OPENAIR_CLAUDE_CLI_PATH not a file: %s", cli_override)

    # Model override (same precedence as inline).
    model = (
        os.environ.get("OPENAIR_CLAUDE_MODEL")
        or os.environ.get("ANTHROPIC_MODEL")
        or ""
    ).strip()
    if model:
        option_kwargs["model"] = model

    return ClaudeAgentOptions(**option_kwargs)


# _build_prompt is imported from llm_core.claude_turn (U-03).
from llm_core.claude_turn import build_prompt as _build_prompt  # noqa: E402,F401


# ---------------------------------------------------------------------------
# Execution functions
# ---------------------------------------------------------------------------

async def run_chat(
    task: dict[str, Any],
    message: str,
    *,
    mode: str = "execution",
    resume_session_id: str | None = None,
    substage_key: str | None = None,
    l2_claude_name: str | None = None,
    available_skills: list[str] | None = None,
    spec_template: str | None = None,
    output_file: str | None = None,
    input_files: list[str] | None = None,
    requested_skill_id: str | None = None,
    l3_skills_meta: list[dict[str, str]] | None = None,
    existing_l3_plan: dict[str, Any] | None = None,
    permission_mode: str = "bypassPermissions",
    user_skill_names: list[str] | None = None,
) -> tuple[str, str | None, dict[str, Any] | None]:
    """Run one chat turn through Claude Code.

    Returns ``(reply, session_id, usage_wire)`` where ``usage_wire`` is the
    normalized token usage (camelCase, callback-ready) or None (S03-14-02).
    """
    options = _build_worker_options(
        task, mode, resume_session_id,
        substage_key=substage_key,
        l2_claude_name=l2_claude_name,
        available_skills=available_skills,
        spec_template=spec_template,
        output_file=output_file,
        input_files=input_files,
        requested_skill_id=requested_skill_id,
        l3_skills_meta=l3_skills_meta,
        existing_l3_plan=existing_l3_plan,
        permission_mode=permission_mode,
        user_skill_names=user_skill_names,
    )
    prompt = _build_prompt(message, resume_session_id)
    timeout_seconds = int(os.environ.get("OPENAIR_WORKER_TIMEOUT_SECONDS", "600"))

    # U-03: non-stream query loop shared with the inline runtime via claude_turn.
    from llm_core.claude_turn import run_turn
    from llm_core.token_usage import to_wire_usage

    result = await run_turn(
        options=options,
        prompt=prompt,
        resume_session_id=resume_session_id,
        timeout_seconds=timeout_seconds,
        empty_reply="（Claude Code 未返回文本。）",
    )
    return result.reply, result.session_id, to_wire_usage(result.usage)


async def run_chat_stream(
    task: dict[str, Any],
    message: str,
    *,
    mode: str = "execution",
    resume_session_id: str | None = None,
    substage_key: str | None = None,
    l2_claude_name: str | None = None,
    available_skills: list[str] | None = None,
    spec_template: str | None = None,
    output_file: str | None = None,
    input_files: list[str] | None = None,
    requested_skill_id: str | None = None,
    l3_skills_meta: list[dict[str, str]] | None = None,
    existing_l3_plan: dict[str, Any] | None = None,
    permission_mode: str = "bypassPermissions",
    user_skill_names: list[str] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Stream Claude Code chat output. Yields event dicts."""
    from claude_agent_sdk import AssistantMessage, ResultMessage, StreamEvent, TaskProgressMessage, TextBlock, query

    options = _build_worker_options(
        task, mode, resume_session_id,
        substage_key=substage_key,
        l2_claude_name=l2_claude_name,
        available_skills=available_skills,
        spec_template=spec_template,
        output_file=output_file,
        input_files=input_files,
        requested_skill_id=requested_skill_id,
        l3_skills_meta=l3_skills_meta,
        existing_l3_plan=existing_l3_plan,
        permission_mode=permission_mode,
        user_skill_names=user_skill_names,
    )
    prompt = _build_prompt(message, resume_session_id)
    timeout_seconds = int(os.environ.get("OPENAIR_WORKER_TIMEOUT_SECONDS", "600"))

    # U-03: stream loop shared with the inline runtime via claude_turn.run_turn_stream.
    # The worker uses the flat-timeout profile (no staged timeout / heartbeats) and
    # does not emit tool_use/tool_result events; producer errors are surfaced.
    from llm_core.claude_turn import run_turn_stream

    async for event in run_turn_stream(
        options=options,
        prompt=prompt,
        resume_session_id=resume_session_id,
        timeout_seconds=timeout_seconds,
        timeout_reply="（Claude Code 响应超时。）",
    ):
        yield event
