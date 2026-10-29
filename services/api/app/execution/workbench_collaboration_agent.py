"""Workbench collaboration reception: real LLM agent for user composer turns.

This is the standby research assistant for the right-rail input box. It must
understand free-text requests and may propose authoritative Workbench actions
that the route applies only when the current user-actions snapshot allows them.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Mapping

logger = logging.getLogger(__name__)

_ACTION_FENCE = re.compile(
    r"```openair-action\s*(\{.*?\})\s*```",
    re.DOTALL | re.IGNORECASE,
)
_COLLABORATION_ACTION_TYPES = frozenset(
    {"explicit_retry", "stop", "supplement_input", "regenerate_research_plan"}
)
_PLAN_REGENERATION_REQUEST = re.compile(
    r"重试|再试|重新生成|再生成|重来|再来一版|再出一版|retry|regenerate",
    re.IGNORECASE,
)
_PLAN_REGENERATION_REFUSAL = re.compile(r"不要重试|别重试|先别重试|不用重试|无需重试|先不要重新")
_VISIBLE_USER_REQUEST = "Visible user request:\n"
_RUNTIME_ENVELOPE = "\n\nRuntime Gateway envelope:"

PLAN_REGENERATION_ACCEPTED_REPLY = "我会在这项研究上重新生成研究方案，并沿用你一开始选定的方式。"
PLAN_REGENERATION_ALREADY_REPLY = "这项研究已经在重新生成研究方案。"
PLAN_REGENERATION_UNCONFIRMED_REPLY = "这次生成结果还不能确认，所以我不会另起一次研究方案。"
PLAN_REGENERATION_BUSY_REPLY = "研究方案的生成队列正忙，请稍后再说一次重试。"
PLAN_REGENERATION_REFUSED_REPLY = "这次还不能重新生成研究方案。"


@dataclass(frozen=True)
class CollaborationAgentResult:
    reply: str
    runtime: str
    proposed_action: dict[str, Any] | None = None
    source_metadata: dict[str, Any] | None = None


class CollaborationAgentUnavailable(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _timeout_seconds() -> float:
    raw = (os.environ.get("OPENAIR_WORKBENCH_COLLABORATION_LLM_TIMEOUT_SECONDS") or "").strip()
    try:
        return max(15.0, float(raw or "90"))
    except ValueError:
        return 90.0


def _model_name() -> str:
    return (
        (os.environ.get("OPENAIR_WORKBENCH_COLLABORATION_LLM_MODEL") or "").strip()
        or (os.environ.get("LLM_PROVIDER_MODEL_SOL") or "").strip()
        or (os.environ.get("LLM_PROVIDER_MODEL") or "").strip()
        or "gpt-4o-mini"
    )


def _compact_actions(snapshot: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(snapshot, Mapping):
        return {"allowedActions": [], "items": []}
    items = []
    for row in snapshot.get("items") or []:
        if not isinstance(row, Mapping):
            continue
        items.append(
            {
                "itemId": row.get("itemId"),
                "kind": row.get("kind"),
                "title": row.get("title"),
                "workUnitId": row.get("workUnitId"),
                "allowedActions": list(row.get("allowedActions") or []),
                "commandContexts": row.get("commandContexts") or {},
            }
        )
    return {
        "allowedActions": list(snapshot.get("allowedActions") or []),
        "executionStopped": bool(snapshot.get("executionStopped")),
        "items": items,
    }


def _interruption_brief(task: Mapping[str, Any]) -> dict[str, Any] | None:
    progress = task.get("progressProjection")
    if not isinstance(progress, Mapping):
        return None
    interruption = progress.get("interruption")
    if not isinstance(interruption, Mapping):
        return None
    return {
        "kind": interruption.get("kind"),
        "summary": interruption.get("summary"),
        "userAction": interruption.get("userAction"),
        "responsibility": interruption.get("responsibility"),
    }


def _research_design_brief(task: Mapping[str, Any]) -> dict[str, Any]:
    """Facts the assistant must use before talking about confirmation or review."""
    progress = task.get("progressProjection")
    task_progress = progress.get("taskProgress") if isinstance(progress, Mapping) else None
    awaiting_refs = []
    progress_status = None
    if isinstance(task_progress, Mapping):
        progress_status = task_progress.get("status")
        raw_refs = task_progress.get("awaitingConfirmationRefs") or []
        if isinstance(raw_refs, list):
            awaiting_refs = [ref for ref in raw_refs if ref]
    has_design = bool(
        task.get("activeResearchDesignRef")
        or task.get("pendingResearchDesignRef")
        or task.get("creationPlan")
        or (task.get("researchDesignVersions") or [])
    )
    return {
        "hasResearchDesign": has_design,
        "awaitingConfirmationCount": len(awaiting_refs),
        "progressStatus": progress_status,
        "creationStatus": task.get("creationStatus"),
        "userFacingSituation": (
            "research_design_incomplete"
            if not has_design and len(awaiting_refs) == 0
            else "has_research_design"
        ),
    }


def build_collaboration_system_prompt(
    *,
    task: Mapping[str, Any],
    actions_snapshot: Mapping[str, Any] | None,
) -> str:
    actions = _compact_actions(actions_snapshot)
    interruption = _interruption_brief(task)
    design = _research_design_brief(task)
    return (
        "你是 OpenAIR 科研工作台的候命研究助手。用户通过右侧输入框与你对话。\n"
        "你的职责：理解用户自然语言，用简洁中文回复，并在确有必要时提出一个权威动作。\n"
        "规则：\n"
        "1. 先理解用户意图（提问、补充材料、重试、停止等），再决定是否动作。\n"
        "2. 只能建议下方「当前允许的操作」里已有的动作；例外是研究方案尚未形成时的 "
        "regenerate_research_plan。不允许就解释原因，不要假装已执行。\n"
        "3. 若用户要求重试且允许 explicit_retry：提出动作，并说明会创建一次新的执行。\n"
        "4. 若用户要求停止且允许 stop：提出 stop。已阻塞且无可停执行时不要建议停止。\n"
        "5. 若用户在提供研究材料且允许 supplement_input：提出 supplement_input，text 用用户原话或整理后的补充内容。\n"
        "6. 普通问答只回复，不要强行动作。\n"
        "7. 回复正文面向科研用户，不要暴露内部 id/堆栈/配置路径。\n"
        "8. 若 userFacingSituation=research_design_incomplete（没有研究方案、也没有待确认项）："
        "不得告诉用户去确认方案或核查结果；应说明方案还未形成。"
        "用户要求重试、再试或重新生成时，提出 regenerate_research_plan，不要说目前不能重试。"
        "该动作在同一项研究上重新生成研究方案，并沿用用户一开始选定的方式。"
        "回复不要出现 workflow、planner、规划器、编译器等内部词。"
        "explicit_retry 只用于已经进入执行后的失败，不要用它代替方案重生成。\n"
        "9. 若需要动作，在回复末尾单独附加一个 fenced JSON 块，格式严格为：\n"
        "```openair-action\n"
        '{"type":"explicit_retry"|"stop"|"supplement_input"|"regenerate_research_plan",'
        '"workUnitId":"...可选...","text":"...补充时必填..."}\n'
        "```\n"
        "没有动作时不要输出该代码块。\n\n"
        f"任务标题：{task.get('title') or ''}\n"
        f"任务目标：{task.get('goalSummary') or ''}\n"
        f"任务状态：{task.get('status') or ''}\n"
        f"研究方案事实：{json.dumps(design, ensure_ascii=False)}\n"
        f"当前中断：{json.dumps(interruption, ensure_ascii=False) if interruption else '无'}\n"
        f"当前允许的操作：{json.dumps(actions, ensure_ascii=False)}\n"
    )


def parse_collaboration_action(reply: str) -> tuple[str, dict[str, Any] | None]:
    """Split user-visible reply from an optional trailing action fence."""
    text = str(reply or "")
    match = _ACTION_FENCE.search(text)
    if not match:
        return text.strip(), None
    visible = (text[: match.start()] + text[match.end() :]).strip()
    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError:
        return visible or text.strip(), None
    if not isinstance(payload, dict):
        return visible or text.strip(), None
    action_type = str(payload.get("type") or "").strip()
    if action_type not in _COLLABORATION_ACTION_TYPES:
        return visible or text.strip(), None
    return visible or text.strip(), payload


def visible_user_message(message: str) -> str:
    """Return the user's own words when a runtime envelope wrapped them."""

    text = str(message or "")
    if _VISIBLE_USER_REQUEST not in text:
        return text.strip()
    rest = text.split(_VISIBLE_USER_REQUEST, 1)[1]
    end = rest.find(_RUNTIME_ENVELOPE)
    return (rest[:end] if end >= 0 else rest).strip()


def user_requests_plan_regeneration(message: str) -> bool:
    """True when the user is asking to generate the research plan again."""

    text = visible_user_message(message)
    if not text or _PLAN_REGENERATION_REFUSAL.search(text):
        return False
    return _PLAN_REGENERATION_REQUEST.search(text) is not None


def _explicit_retry_allowed(snapshot: Mapping[str, Any] | None) -> bool:
    if not isinstance(snapshot, Mapping):
        return False
    if "explicit_retry" in list(snapshot.get("allowedActions") or []):
        return True
    for row in snapshot.get("items") or []:
        if isinstance(row, Mapping) and "explicit_retry" in list(row.get("allowedActions") or []):
            return True
    return False


def should_propose_plan_regeneration(
    message: str,
    task: Mapping[str, Any],
    actions_snapshot: Mapping[str, Any] | None,
) -> bool:
    """Regenerate only when the plan is missing and execution retry is not the subject."""

    if not user_requests_plan_regeneration(message):
        return False
    if _explicit_retry_allowed(actions_snapshot):
        return False
    return _research_design_brief(task)["userFacingSituation"] == "research_design_incomplete"


def reply_for_plan_regeneration(receipt: Mapping[str, Any] | None) -> str:
    """User-facing sentence after the regeneration command has been decided."""

    if not isinstance(receipt, Mapping) or receipt.get("applied") is not True:
        reason = "" if not isinstance(receipt, Mapping) else str(receipt.get("reason") or "")
        if reason == "external_result_unknown":
            return PLAN_REGENERATION_UNCONFIRMED_REPLY
        if reason == "heavy_queue_full":
            return PLAN_REGENERATION_BUSY_REPLY
        return PLAN_REGENERATION_REFUSED_REPLY
    if receipt.get("reason") == "already_regenerating":
        return PLAN_REGENERATION_ALREADY_REPLY
    return PLAN_REGENERATION_ACCEPTED_REPLY


def _extract_chat_content(body: Mapping[str, Any]) -> str:
    from llm_core.openai_compatible_client import extract_final_chat_content

    content, _choice = extract_final_chat_content(dict(body))
    return str(content or "").strip()


async def run_workbench_collaboration_agent(
    *,
    task: Mapping[str, Any],
    message: str,
    actions_snapshot: Mapping[str, Any] | None,
) -> CollaborationAgentResult:
    """One observable LLM collaboration turn for the workbench composer."""
    if should_propose_plan_regeneration(message, task, actions_snapshot):
        return CollaborationAgentResult(
            reply=PLAN_REGENERATION_ACCEPTED_REPLY,
            runtime="research_plan_regeneration",
            proposed_action={"type": "regenerate_research_plan"},
            source_metadata={
                "provider": "research_plan_regeneration",
                "collaboration": True,
                "proposedActionType": "regenerate_research_plan",
            },
        )
    api_key = (os.environ.get("LLM_PROVIDER_API_KEY") or "").strip()
    api_base = (os.environ.get("LLM_PROVIDER_BASE_URL") or "").strip()
    if not api_key or not api_base:
        raise CollaborationAgentUnavailable("collaboration_llm_not_configured")

    from llm_core.openai_compatible_client import ProviderEndpoint, ainvoke_chat_completions

    model = _model_name()
    system_prompt = build_collaboration_system_prompt(task=task, actions_snapshot=actions_snapshot)
    endpoint = ProviderEndpoint(base_url=api_base, api_key=api_key, timeout_seconds=_timeout_seconds())
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": message},
        ],
        "temperature": 0.2,
        "max_tokens": 1200,
    }
    try:
        result = await ainvoke_chat_completions(endpoint, payload)
    except Exception as exc:
        logger.warning("workbench collaboration llm failed: %s", exc)
        raise CollaborationAgentUnavailable("collaboration_llm_failed") from exc

    raw_reply = str(getattr(result, "content", "") or "").strip()
    if not raw_reply and isinstance(getattr(result, "raw_body", None), Mapping):
        raw_reply = _extract_chat_content(result.raw_body)
    if not raw_reply:
        raise CollaborationAgentUnavailable("collaboration_llm_empty_reply")

    visible, action = parse_collaboration_action(raw_reply)
    return CollaborationAgentResult(
        reply=visible or "我已收到你的消息。",
        runtime="huiyan_chat_completions",
        proposed_action=action,
        source_metadata={
            "provider": "huiyan_chat_completions",
            "model": model,
            "collaboration": True,
            "proposedActionType": None if action is None else action.get("type"),
        },
    )
