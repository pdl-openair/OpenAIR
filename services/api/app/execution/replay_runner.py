"""Replay skill_io fixtures (dev/demo when SKILL_EXEC_MODE=replay)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app.modules.repo_paths import repo_root


def load_skill_io(skill_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    base = repo_root() / "fixtures" / "skill_io" / skill_id
    req_path = base / "request.json"
    res_path = base / "response.json"
    if not req_path.is_file() or not res_path.is_file():
        raise FileNotFoundError(f"skill_io not found for {skill_id}")
    with req_path.open(encoding="utf-8") as f:
        request = json.load(f)
    with res_path.open(encoding="utf-8") as f:
        response = json.load(f)
    return request, response


def replay_skill(skill_id: str) -> dict[str, Any]:
    _, response = load_skill_io(skill_id)
    return response


def replay_chat_reply(task: dict[str, Any], user_message: str, skill_id: str | None) -> str:
    sid = skill_id or "literature_review"
    try:
        response = replay_skill(sid)
        summary = response.get("summary") or "（回放无摘要）"
        prompt = _build_confirmation_prompt(user_message, summary)
        return (
            f"[回放模式 · {sid}]\n\n"
            f"已收到你的指令：{user_message}\n\n"
            f"**模拟执行结果：**\n{summary}\n\n"
            f"{prompt}"
        )
    except OSError:
        return (
            f"[回放模式]\n\n已收到：{user_message}\n\n"
            f"（未找到 skill_io/{sid}，请在仓库 fixtures/skill_io 下补充或使用 SKILL_EXEC_MODE=live。）"
        )


def _build_confirmation_prompt(user_message: str, summary: str) -> str:
    lowered = user_message.lower()
    if any(token in lowered for token in ["按默认计划", "默认计划", "草稿确认", "确认计划", "草拟"]):
        return (
            "## 待用户确认\n"
            "- 是否按当前默认理解继续生成计划草稿？\n"
            "- 若需调整，请补充最想修改的目标、范围或输出形式。"
        )
    if re.search(r"\b(是否|可否|要不要|需不需要|能否)\b", user_message):
        return (
            "## 待用户确认\n"
            "- 是否采用上述建议继续推进？\n"
            "- 若不采用，请说明你希望调整的部分。"
        )
    if len(summary.strip()) >= 80:
        return (
            "## 待用户确认\n"
            "- 是否确认采用当前结果？\n"
            "- 若需调整，请补充你希望修改的重点。"
        )
    return ""
