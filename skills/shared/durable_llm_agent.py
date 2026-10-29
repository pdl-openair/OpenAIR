"""Shared durable LLM agent loop skeleton for skill_script contracts.

Usage in a per-skill script:
    from skills.shared.durable_llm_agent import run_agent, make_tool

    tools = [
        make_tool("read_file", "Read a file", {"path": {"type": "string"}}, ["path"], my_fn),
    ]
    result = run_agent(
        tools=tools,
        system_prompt="You are ...",
        user_message="Do X given context Y",
        output_path="skills/my_skill/result.json",
    )
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable


def make_tool(
    name: str,
    description: str,
    properties: dict[str, Any],
    required: list[str],
    fn: Callable,
) -> dict[str, Any]:
    """Build an Anthropic tool definition with an attached callable."""
    return {
        "name": name,
        "description": description,
        "input_schema": {
            "type": "object",
            "properties": properties,
            "required": required,
        },
        "_fn": fn,
    }


def _extract_text(response: Any) -> str:
    """Pull plain text from an Anthropic response content list."""
    return "".join(
        block.text
        for block in (response.content or [])
        if hasattr(block, "text")
    )


def _dispatch_tool(name: str, input_data: dict, tools: list[dict]) -> Any:
    """Find the registered function and call it with the LLM-supplied args."""
    for tool in tools:
        if tool.get("name") == name and callable(tool.get("_fn")):
            try:
                return tool["_fn"](**input_data)
            except Exception as exc:  # noqa: BLE001
                return {"error": f"tool {name!r} raised {type(exc).__name__}: {exc}"}
    return {"error": f"tool {name!r} not registered"}


def _anthropic_tool_defs(tools: list[dict]) -> list[dict]:
    """Strip the internal `_fn` key before sending to the API."""
    return [
        {k: v for k, v in tool.items() if k != "_fn"}
        for tool in tools
    ]


def _write_json(path: str, data: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _append_progress(progress_path: str | None, message: str) -> None:
    """向 progress.md 追加一条带时间戳的里程碑（前端实时轮询渲染，写失败忽略）。"""
    if not progress_path:
        return
    try:
        p = Path(progress_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        import time as _time

        with p.open("a", encoding="utf-8") as fh:
            fh.write(f"- **[{_time.strftime('%H:%M:%S')}]** {message}\n")
    except OSError:
        pass


def _summarize_tool_input(name: str, input_data: dict) -> str:
    """One short human-readable clause per tool call for the progress file."""
    for key in ("path", "output_path", "file_path", "query", "pattern"):
        value = input_data.get(key)
        if isinstance(value, str) and value:
            short = value.rsplit("/", 1)[-1] if "/" in value else value[:60]
            return f"{name}({key}={short})"
    return name


def _meter_llm_call(response: Any, *, meter_context: dict | None, model: str) -> None:
    """把本次 LLM 调用的真实 usage 记入平台 token ledger（尽力而为）。

    只有在平台 worker 进程内（可 import app 包）且调用方传了 meter_context
    时才生效；独立运行/导入失败时静默跳过——计量绝不影响 agent 执行。
    计费口径与 normalize_claude_sdk_usage 一致：input+output，缓存字段只
    进 usage_detail 不计费。
    """
    if not isinstance(meter_context, dict) or not str(meter_context.get("user_id") or ""):
        return
    usage = getattr(response, "usage", None)
    input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    if input_tokens <= 0 and output_tokens <= 0:
        return
    cache_detail = {
        key: int(getattr(usage, key, 0) or 0)
        for key in ("cache_creation_input_tokens", "cache_read_input_tokens")
        if int(getattr(usage, key, 0) or 0) > 0
    }
    try:
        from app.modules.token_ledger_repository import meter_call

        meter_call(
            user_id=str(meter_context["user_id"]),
            source="durable_skill_agent",
            usage_wire={
                "inputTokens": input_tokens,
                "outputTokens": output_tokens,
                "totalTokens": input_tokens + output_tokens,
                "estimated": False,
                "raw": cache_detail or None,
            },
            workspace_id=meter_context.get("workspace_id") or None,
            task_id=meter_context.get("task_id") or None,
            provider="anthropic",
            model=model,
        )
    except Exception:  # noqa: BLE001 — 计量是卫星系统，绝不打断 agent
        pass


def run_agent(
    *,
    tools: list[dict],
    system_prompt: str,
    user_message: str,
    output_path: str,
    progress_path: str | None = None,
    model: str | None = None,
    max_tokens: int = 8192,
    max_iterations: int = 30,
    meter_context: dict | None = None,
) -> dict[str, Any]:
    """Run a bounded LLM agent loop.

    The loop ends when the model returns stop_reason == 'end_turn' or
    max_iterations is reached. The result dict is written to output_path
    (JSON) and also returned.

    Args:
        tools: List of tool defs built with make_tool().
        system_prompt: System-level instructions for the agent.
        user_message: The initial user turn (task + injected context).
        output_path: Where to write the final result.json.
        progress_path: Optional work-unit progress.md；每轮迭代追加一行里程碑，
               前端轮询渲染「执行进展」面板。
        model: Anthropic model ID; defaults to DURABLE_AGENT_MODEL env var
               or ANTHROPIC_MODEL (must be valid for ANTHROPIC_BASE_URL).
        max_tokens: Max tokens per LLM call.
        max_iterations: Hard cap on tool-call rounds.
        meter_context: Optional {"user_id", "task_id", "workspace_id"}；每轮
               LLM 调用按真实 usage 写一条 token ledger（平台 worker 进程内
               才生效，缺省不计量）。

    Returns:
        {"state": "succeeded"|"partial_failure", "output": str, "iterations": int}
    """
    import anthropic  # imported here so the module is importable without the SDK

    # 模型名必须与 anthropic 客户端实际指向的 ANTHROPIC_BASE_URL 匹配。
    # LLM_PROVIDER_MODEL 属于另一家 provider（huiyan），发给 bigmodel 的
    # anthropic 网关会 400「模型不存在」code 1211（task_120c1277 教训），
    # 故不进回退链。
    resolved_model = (
        model
        or os.environ.get("DURABLE_AGENT_MODEL", "")
        or os.environ.get("ANTHROPIC_MODEL", "")
    )
    client = anthropic.Anthropic()
    api_tools = _anthropic_tool_defs(tools)
    messages: list[dict] = [{"role": "user", "content": user_message}]

    last_text = ""
    for iteration in range(max_iterations):
        _append_progress(progress_path, "LLM 思考中…")
        response = client.messages.create(
            model=resolved_model,
            max_tokens=max_tokens,
            system=system_prompt,
            tools=api_tools,
            messages=messages,
        )
        _meter_llm_call(response, meter_context=meter_context, model=resolved_model)

        last_text = _extract_text(response)

        if response.stop_reason == "end_turn":
            result: dict[str, Any] = {
                "state": "succeeded",
                "output": last_text,
                "iterations": iteration + 1,
            }
            _write_json(output_path, result)
            _append_progress(progress_path, "agent 结束（end_turn）")
            return result

        # Collect tool_use blocks and dispatch
        tool_results = []
        has_tool_use = False
        pending: list[str] = []
        for block in (response.content or []):
            if getattr(block, "type", None) == "tool_use":
                has_tool_use = True
                pending.append(_summarize_tool_input(block.name, dict(block.input or {})))
        if pending:
            _append_progress(progress_path, f"调用 {'、'.join(pending)}")
        for block in (response.content or []):
            if getattr(block, "type", None) == "tool_use":
                tool_output = _dispatch_tool(block.name, dict(block.input or {}), tools)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": (
                        tool_output
                        if isinstance(tool_output, str)
                        else json.dumps(tool_output, ensure_ascii=False)
                    ),
                })

        messages.append({"role": "assistant", "content": response.content})
        if tool_results:
            messages.append({"role": "user", "content": tool_results})
        elif not has_tool_use:
            # Model stopped without tool use and without end_turn — treat as done
            result = {"state": "succeeded", "output": last_text, "iterations": iteration + 1}
            _write_json(output_path, result)
            _append_progress(progress_path, "agent 结束（无工具调用）")
            return result

    result = {
        "state": "partial_failure",
        "reason": "max_iterations_reached",
        "output": last_text,
        "iterations": max_iterations,
    }
    _write_json(output_path, result)
    _append_progress(progress_path, f"达到轮次上限（{max_iterations}），带现有产出返回")
    return result
