"""Invoke local Claude Code via Claude Agent SDK (live mode).

**Runtime internal implementation — do not import directly from API layer.**
API orchestration code should use ``app.execution.runtime_boundary`` instead.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Literal

from app.config import settings
from app.execution.execution_context import get_execution_cwd, get_task_workspace_dir
from app.modules.repo_paths import repo_root

# U-03: shared Claude-turn helpers live in claude_turn. Aliased to the legacy
# underscore names so this module's existing call sites (and its stream path)
# keep working without editing every reference.
from llm_core.claude_turn import (  # noqa: E402,F401
    HEADLESS_ALLOWED_TOOLS as _HEADLESS_ALLOWED_TOOLS,
    build_prompt as _build_prompt,
    extract_text_from_message as _extract_text_from_message,
    run_turn,
)

logger = logging.getLogger(__name__)

AgentMode = Literal["creation", "execution"]

# Claude Code 工具名须与官方 Tools reference 一致（case-sensitive）；bypassPermissions 会忽略 allowed_tools
CLAUDE_QUERY_TIMEOUT_SECONDS = 600
CLAUDE_INITIAL_STREAM_TIMEOUT_SECONDS = int(os.environ.get("OPENAIR_CLAUDE_INITIAL_STREAM_TIMEOUT_SECONDS", "120").strip())
CLAUDE_WAIT_PROGRESS_INTERVAL_SECONDS = 5.0

# _HEADLESS_ALLOWED_TOOLS is imported from llm_core.claude_turn (U-03).


def log_headless_allowed_tools() -> None:
    """启动时打印最终生效的 allowed_tools，便于排障权限/工具名不匹配。"""
    mode = settings.claude_permission_mode
    if mode in ("acceptEdits", "default", "dontAsk"):
        names = ", ".join(_HEADLESS_ALLOWED_TOOLS)
        logger.info("claude allowed_tools permission_mode=%s tools=[%s]", mode, names)
        print(f"[openair] claude allowed_tools ({mode}): {names}")
    else:
        logger.info("claude permission_mode=%s (allowed_tools list not applied)", mode)
        print(f"[openair] claude permission_mode={mode} (allowed_tools not applied)")

# Output schema may live in two layouts:
# - inline/dev source tree: <repo>/skills/general/new-task-planning/output_schema.json
# - worker container: <repo>/.claude/skills/new-task-planning/output_schema.json
#   (services/worker/sync_skills.py flattens skills/<domain>/<skill>/
#    → .claude/skills/<skill>/, dropping the domain level, and the source
#    `skills/` dir is not copied into the worker image at all)
_CREATION_PLAN_SCHEMA_CANDIDATES = [
    repo_root() / "skills" / "general" / "new-task-planning" / "output_schema.json",
    repo_root() / ".claude" / "skills" / "new-task-planning" / "output_schema.json",
]
_CREATION_PLAN_EXAMPLE = """{
  "goalSummary": "…",
  "domain": "reinforcement_learning",
  "skillPack": "ope",
  "researchType": "H",
  "clarifications": ["…"],
  "stages": [
    {
      "stageKey": "problem_modeling",
      "substages": [
        { "substage": "info_retrieval", "skills": ["ope-rs-info-retrieval"] }
      ]
    }
  ],
  "readyToConfirm": true
}"""


def _to_claude_skill_name(skill_ref: str) -> str:
    """OpenAIR skill_id / substage key → Claude Code skill folder name."""
    return skill_ref.strip().replace("_", "-")


def _resolve_skills_for_turn(
    mode: AgentMode,
    *,
    l2_claude_name: str | None = None,
    available_skills: list[str] | None = None,
    requested_skill_id: str | None = None,
) -> list[str]:
    if mode == "creation":
        return ["new-task-planning"]

    names: list[str] = []
    seen: set[str] = set()

    def add(skill_name: str | None) -> None:
        if not skill_name:
            return
        if skill_name not in seen:
            seen.add(skill_name)
            names.append(skill_name)

    if requested_skill_id and requested_skill_id != "workbench_chat":
        add(requested_skill_id)
    if l2_claude_name:
        add(l2_claude_name)
    for skill_name in available_skills or []:
        add(skill_name)
    return names


def _load_creation_plan_schema_block() -> str:
    for path in _CREATION_PLAN_SCHEMA_CANDIDATES:
        try:
            if path.is_file():
                return path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
    return ""


def _build_system_prompt(
    task: dict[str, Any],
    mode: AgentMode,
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
    goal = task.get("goalSummary") or ""
    title = task.get("title") or ""
    stage = task.get("currentStage") or "" #BUG：stage应该根据substage确定。需要在stage-substage-map里反向索引。
    task_id = task.get("taskId") or ""
    workspace = f"workspace/{task_id}/" if task_id else ""
    workspace_line = f"任务工作区：{workspace}\n" if workspace else ""
    base = (
        "你是 OpenAIR 科研任务工作台中的 Claude Code Agent。"
        "回答应简洁、可执行，并标明需要用户确认的结论。\n\n"
        f"任务标题：{title}\n"
        f"任务目标：{goal}\n"
        f"当前阶段：{stage}\n"
        f"{workspace_line}"
    )
    if mode == "creation":
        existing_pack = (task.get("skillPack") or "").strip()
        pack_hint_line = (
            f"任务上下文已有 skillPack={existing_pack}（仅作参考；若与用户意图冲突，以 skill 内部判定为准）。\n"
            if existing_pack else ""
        )
        schema_block = _load_creation_plan_schema_block()
        schema_section = ""
        if schema_block:
            schema_section = (
                "\n工作流 JSON 输出契约（必须严格遵守，stageKey 只能用 problem_modeling / "
                "solution_design / execution_validation / insight_generation）：\n"
                f"```json\n{schema_block}\n```\n\n"
                f"示例结构：\n```json\n{_CREATION_PLAN_EXAMPLE}\n```\n\n"
            )
        return (
            base
            + "当前处于新任务创建流程（creation mode）。"
            "你的核心任务是调用 `new-task-planning` 技能为用户编排工作流；"
            "由该 skill 内部读取 `schemas/packs.yaml` 并根据用户意图判定使用哪个 skill pack（如 sdr / ope / opentrajectory）。\n\n"
            + pack_hint_line
            + "流程：\n"
            "1. 调用 `new-task-planning`，由 skill 澄清意图、判定 skillPack、判断研究类型（M/D/C/H）；信息不足时 skill 会持续追问\n"
            "2. skill 决定 pack 后会读取对应 `schemas/<pack>/` 下的配置生成工作流（workflow）\n"
            "3. 工作流必须等待用户确认\n\n"
            "输出格式（重要）：\n"
            "- 工作流结果必须以 ```json 代码块输出\n"
            "- 必填字段：goalSummary / domain / skillPack / researchType / clarifications / stages / readyToConfirm\n"
            "- skillPack 必须由 skill 显式给出（如 \"sdr\" / \"ope\"），不得省略\n"
            "- 禁止使用 workflow、workflow_status 等自定义字段名\n"
            "- 不要以 Markdown 表格、列表或纯文本形式输出工作流\n"
            "- 澄清阶段可以自由对话，但最终工作流必须是可解析的 JSON\n\n"
            + schema_section
            + "用户确认前不得开始执行任务，不得承诺任务已开始执行。"
            "不得调用正式执行能力，不得推进 task_status 为 running，不得写入 stageDetails。"
        )
    exec_parts = [
        base,
        "当前处于执行流程（execution mode）。请围绕当前 substage 推进执行，给出结论后等待用户确认。",
        "注意：substage 的推进由系统管理，你不需要自行切换或推进到下一个 substage。",
        "执行完成后必须用自然语言总结结论，并标出需要用户确认的事项（即使主要产物已写入文件）。",
    ]
    if enabled_skills:
        exec_parts.append(f"本轮启用的 Claude Code skills：{', '.join(enabled_skills)}")
        exec_parts.append(
            "若 Skill 工具调用失败，请 Read `.claude/skills/<skill-name>/SKILL.md` 并按说明执行，不要静默失败。"
        )
    if substage_key or l2_claude_name or spec_template:
        phase1_lines: list[str] = []
        if substage_key:
            phase1_lines.append(f"当前 substage：{substage_key}")
        if l2_claude_name:
            phase1_lines.append(
                f"你必须先调用 L2 编排 skill：`{l2_claude_name}`（用 Skill 工具）。"
                "该 skill 用于加载本 substage 的编排规范与产出契约，不计入下方 l3plan。"
            )
        if spec_template:
            phase1_lines.append(f"Spec 模板路径：schemas/{task.get('skillPack', 'sdr')}/specs/{spec_template}")
        exec_parts.append("\n## 阶段一：调用 L2 编排 skill\n\n" + "\n".join(phase1_lines))
    if l3_skills_meta:
        skill_table = ["| skill_id | 名称 | 说明 |", "|---|---|---|"]
        for s in l3_skills_meta:
            skill_table.append(f"| `{s['skill_id']}` | {s['display_name']} | {s['description']} |")
        exec_parts.append(
            "\n## 阶段二：L3 技能编排\n\n"
            "**在阶段一完成后再开始本阶段。**\n\n"
            "你可以调用以下 L3 原子技能来完成当前 substage 的工作。请按以下流程操作：\n\n"
            "### 第一步：制定 L3 计划\n"
            "在正式调用任何 L3 skill 之前，先输出 L3 执行计划。"
            "`<l3plan>` **只能包含 L3 skill_id，不要把 L2 skill 写入此计划**：\n"
            "```\n"
            "<l3plan>\n"
            "steps:\n"
            "  - skill_id: <L3 skill_id>\n"
            "    purpose: <简述此步骤的目的>\n"
            "</l3plan>\n"
            "```\n\n"
            "### 第二步：逐个执行\n"
            "按计划顺序调用每个 L3 skill。每完成一个，立即输出：\n"
            "```\n"
            '<l3done skill="<skill_id>" result="<简述执行结果>" />\n'
            "```\n"
            "如果某个 skill 失败或被跳过，result 写明原因。\n\n"
            "### 第三步：总结\n"
            "所有 L3 skill 执行完毕后，严格按 spec 模板输出最终产物。\n\n"
            "### 可用 L3 技能列表\n"
            + "\n".join(skill_table) + "\n\n"
            "**重要**：\n"
            "- L2 skill 调用（阶段一）与 L3 skill 调用（阶段二）是两个独立阶段，不要把 L2 写入 `<l3plan>`。\n"
            "- 你必须先输出 `<l3plan>` 再调用 L3 skill，不要跳过计划步骤。\n"
            "- 只选择你确实需要调用的 L3 skill，不需要全部调用。"
        )
    elif available_skills:
        exec_parts.append(f"可用 L3 skill：{', '.join(available_skills)}。")

    if existing_l3_plan and existing_l3_plan.get("steps"):
        progress_lines = ["\n## 当前 L3 计划执行状态（请继续执行未完成的步骤）\n"]
        progress_lines.append("| skill_id | 状态 | 结果 |")
        progress_lines.append("|---|---|---|")
        for step in existing_l3_plan["steps"]:
            status_icon = {"completed": "✅", "running": "▶", "failed": "❌"}.get(step.get("status", ""), "⏳")
            progress_lines.append(
                f"| `{step['skill_id']}` | {status_icon} {step.get('status', 'pending')} "
                f"| {step.get('result') or '-'} |"
            )
        pending = [s for s in existing_l3_plan["steps"] if s.get("status") == "pending"]
        if pending:
            progress_lines.append(
                f"\n**接下来请执行**：{', '.join('`' + s['skill_id'] + '`' for s in pending)}"
            )
            progress_lines.append("不要再输出 <l3plan>，直接从下一个未完成的 skill 继续执行，完成后输出 <l3done> 标记。")
        exec_parts.append("\n".join(progress_lines))
    if output_file:
        task_id = task.get("taskId") or ""
        exec_parts.append(f"产出文件路径：workspace/{task_id}/{output_file}（必须写到此路径）")
    if input_files:
        files_desc = "\n".join(f"  - {f}" for f in input_files)
        exec_parts.append(f"输入文件（前序 Skill 产出，严格阅读）：\n{files_desc}")
    return "\n".join(exec_parts)


# ---------------------------------------------------------------------------
# L3 plan tag parsers
# ---------------------------------------------------------------------------

_L3PLAN_RE = re.compile(r"<l3plan>\s*(.*?)\s*</l3plan>", re.DOTALL)
_L3DONE_RE = re.compile(r'<l3done\s+skill="([^"]+)"\s+result="([^"]*)"\s*/>')


def _parse_l3plan(text: str) -> list[dict[str, str]] | None:
    """Extract L3 plan steps from <l3plan>...</l3plan> tag."""
    match = _L3PLAN_RE.search(text)
    if not match:
        return None
    try:
        import yaml
        parsed = yaml.safe_load(match.group(1))
        if isinstance(parsed, dict) and isinstance(parsed.get("steps"), list):
            steps = [
                {"skill_id": str(s["skill_id"]), "purpose": str(s.get("purpose", ""))}
                for s in parsed["steps"]
                if isinstance(s, dict) and s.get("skill_id")
            ]
            return steps or None
    except Exception:
        logger.warning("Failed to parse l3plan YAML")
    return None


def _parse_l3done_tags(text: str) -> list[dict[str, str]]:
    """Extract all <l3done skill="..." result="..." /> tags from text."""
    return [
        {"skill_id": m.group(1), "result": m.group(2)}
        for m in _L3DONE_RE.finditer(text)
    ]


def _extract_tool_results_from_message(message: Any) -> list[dict[str, Any]]:
    """Extract tool result blocks from an AssistantMessage for per-tool progress."""
    from claude_agent_sdk import AssistantMessage, ToolResultBlock

    results: list[dict[str, Any]] = []
    if isinstance(message, AssistantMessage):
        for block in message.content:
            if isinstance(block, ToolResultBlock):
                content = block.content
                # Summarize tool result content (may be str or list)
                if isinstance(content, str):
                    summary = content[:200] if content else ""
                elif isinstance(content, list):
                    summary = str(content)[:200] if content else ""
                else:
                    summary = ""
                results.append({
                    "type": "tool_result",
                    "toolUseId": block.tool_use_id,
                    "content": summary,
                    "isError": bool(block.is_error),
                })
    return results


def _build_options(
    task: dict[str, Any],
    mode: AgentMode,
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
):
    from claude_agent_sdk import ClaudeAgentOptions

    enabled_skills = _resolve_skills_for_turn(
        mode,
        l2_claude_name=l2_claude_name,
        available_skills=available_skills,
        requested_skill_id=requested_skill_id,
    )
    logger.info(
        "claude_code_runner mode=%s enabled_skills=%s substage=%s requested=%s permission_mode=%s",
        mode,
        enabled_skills,
        substage_key,
        requested_skill_id,
        settings.claude_permission_mode,
    )

    permission_mode = settings.claude_permission_mode
    option_kwargs: dict[str, Any] = {
        "cwd": str(get_execution_cwd()),
        "system_prompt": _build_system_prompt(
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
        ),
        "resume": resume_session_id,
        "permission_mode": permission_mode,
        "setting_sources": ["project"],
        "skills": enabled_skills,
        "stderr": lambda line: logger.warning("[claude-cli stderr] %s", line),
    }
    if permission_mode in ("acceptEdits", "default", "dontAsk"):
        option_kwargs["allowed_tools"] = list(_HEADLESS_ALLOWED_TOOLS)
    task_id = task.get("taskId") or ""
    if task_id:
        workspace_dir = get_task_workspace_dir(task_id)
        if workspace_dir.is_dir():
            option_kwargs["add_dirs"] = [str(workspace_dir)]

    # Unified mmp-platform MCP server (HTTP). Required by the mmp-resources /
    # mmp-training-workflow / mmp-deploy-app skills — without it their platform
    # tools (login/model_query/run_training_pipeline/...) are unavailable.
    # Note: only applies to the local/inline runtime; the container worker
    # rebuilds options from env and must thread OPENAIR_MMP_MCP_URL separately.
    if settings.mmp_mcp_url:
        option_kwargs["mcp_servers"] = {
            "mmp-platform": {"type": "http", "url": settings.mmp_mcp_url}
        }
        logger.info("claude_code_runner mcp_servers=mmp-platform(%s)", settings.mmp_mcp_url)

    # Opt-in CLI override only: SDK default spawn handles PATH/PATHEXT correctly
    # and avoids the .cmd shim's broken initialize handshake on Windows.
    # Set OPENAIR_CLAUDE_CLI_PATH only if SDK default fails in your environment.
    cli_override = (os.environ.get("OPENAIR_CLAUDE_CLI_PATH") or "").strip()
    if cli_override:
        cli_p = Path(cli_override).expanduser()
        if cli_p.is_file():
            option_kwargs["cli_path"] = str(cli_p.resolve())
        else:
            logger.warning("OPENAIR_CLAUDE_CLI_PATH not a file: %s", cli_override)

    model = (
        os.environ.get("OPENAIR_CLAUDE_MODEL")
        or os.environ.get("ANTHROPIC_MODEL")
        or ""
    ).strip()
    if model:
        option_kwargs["model"] = model

    return ClaudeAgentOptions(**option_kwargs)


def _normalize_delta(prev: str, current: str) -> tuple[str, str]:
    if not current:
        return "", prev
    if current.startswith(prev):
        delta = current[len(prev):]
        return delta, prev + delta
    if prev.endswith(current):
        return "", prev
    return current, prev + current


def _tool_progress_label(tool_name: str, *, completed: bool = False, is_error: bool = False) -> str:
    labels = {
        "Skill": "正在加载并执行已选研究能力",
        "Read": "正在读取本次任务所需材料",
        "Write": "正在写入本次产物",
        "Edit": "正在更新本次任务所需内容",
        "WebSearch": "正在查询公开来源",
        "WebFetch": "正在读取已选公开来源",
    }
    subject = labels.get(tool_name, "正在执行本次任务所需操作")
    if is_error:
        return f"{subject}时出现异常，正在整理可恢复结果。"
    if completed:
        return f"{subject}已返回，正在整理结果。"
    return f"{subject}。"


async def run_claude_code_chat(
    task: dict[str, Any],
    user_message: str,
    *,
    mode: AgentMode = "execution",
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
) -> tuple[str, str | None, dict[str, Any] | None]:
    """
    Run one user turn through Claude Code CLI (via Agent SDK).
    Returns (assistant_text, session_id_for_resume, usage).

    ``usage`` is the normalized token usage from the final ResultMessage
    (S03-14-02 ledger), or None when the run ended without one (timeout).
    """
    from claude_agent_sdk import ResultMessage, query

    from app.execution.execution_context import warn_if_local_cli_in_production

    warn_if_local_cli_in_production()

    from app.execution.execution_context import warn_if_local_cli_in_production

    warn_if_local_cli_in_production()

    loop_name = type(asyncio.get_running_loop()).__name__
    if os.name == "nt" and "Selector" in loop_name:
        raise RuntimeError(
            "Windows SelectorEventLoop 不支持 Claude Code 子进程。"
            "请使用 run_dev.py 且关闭 OPENAIR_API_RELOAD。"
        )

    if settings.runtime_type == "worker":
        raise RuntimeError(
            "Claude Code inline execution is disabled when OPENAIR_RUNTIME_TYPE=worker. "
            "Use the worker runtime boundary instead."
        )

    options = _build_options(
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
    )
    prompt = _build_prompt(user_message, resume_session_id)

    if settings.runtime_type == "worker":
        raise RuntimeError(
            "Claude Code inline execution is disabled when OPENAIR_RUNTIME_TYPE=worker. "
            "Use the worker runtime boundary instead."
        )

    # U-03: the non-stream query loop is shared with the worker via claude_turn.
    result = await run_turn(
        options=options,
        prompt=prompt,
        resume_session_id=resume_session_id,
        timeout_seconds=CLAUDE_QUERY_TIMEOUT_SECONDS,
    )
    return result.reply, result.session_id, result.usage



async def run_claude_code_chat_stream(
    task: dict[str, Any],
    user_message: str,
    *,
    mode: AgentMode = "execution",
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
) -> AsyncIterator[dict[str, Any]]:
    """
    Stream Claude Code chat output.
    Yields NDJSON-friendly event dicts:
      - {"type":"assistant_delta","text":"..."}
      - {"type":"progress","text":"..."} (optional)
      - {"type":"done","sessionId":"..."}
    """
    from claude_agent_sdk import AssistantMessage, ResultMessage, StreamEvent, TaskProgressMessage, query

    loop_name = type(asyncio.get_running_loop()).__name__
    if os.name == "nt" and "Selector" in loop_name:
        raise RuntimeError(
            "Windows SelectorEventLoop 不支持 Claude Code 子进程。"
            "请使用 run_dev.py 且关闭 OPENAIR_API_RELOAD。"
        )

    options = _build_options(
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
    )
    prompt = _build_prompt(user_message, resume_session_id)

    # U-03: the stream loop is shared with the worker via claude_turn.run_turn_stream.
    # The inline runtime opts into the richer behavior: tool_use/tool_result events
    # with progress labels, a staged timeout (shorter before any output), and idle
    # heartbeats. Producer errors are now surfaced (backported from the worker).
    from llm_core.claude_turn import run_turn_stream

    async for event in run_turn_stream(
        options=options,
        prompt=prompt,
        resume_session_id=resume_session_id,
        timeout_seconds=CLAUDE_QUERY_TIMEOUT_SECONDS,
        initial_timeout_seconds=CLAUDE_INITIAL_STREAM_TIMEOUT_SECONDS,
        heartbeat_interval_seconds=CLAUDE_WAIT_PROGRESS_INTERVAL_SECONDS,
        emit_tool_events=True,
        tool_progress_label=lambda name: _tool_progress_label(name),
        tool_progress_label_done=lambda name, completed, is_error: _tool_progress_label(
            name, completed=completed, is_error=is_error
        ),
        extract_tool_results=_extract_tool_results_from_message,
        heartbeat_text_fn=lambda elapsed: f"已等待 {elapsed} 秒，尚未收到新的模型或工具事件。",
    ):
        yield event
