"""Deterministic user-facing system agents.

These helpers are the first backend implementation of the Summary, Review, and
Presentation/Guidance agents. They do not make LLM calls; they translate runtime
facts into the user-facing narrative contract consumed by the workbench UI.
"""

from __future__ import annotations

from typing import Any, Literal

NarrativeSourceType = Literal[
    "assistant_message",
    "capability_call",
    "artifact",
    "confirmation",
    "stage",
    "guidance",
    "raw_event",
]


SUMMARY_AGENT_ID = "openair_summary_agent"
REVIEW_AGENT_ID = "openair_review_agent"
PRESENTATION_AGENT_ID = "openair_presentation_guidance_agent"


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _compact_sentence(text: str, *, fallback: str, limit: int = 120) -> str:
    cleaned = " ".join(_clean_text(text).split())
    if not cleaned:
        return fallback
    first = cleaned
    for marker in ("。", ".", "\n"):
        if marker in first:
            first = first.split(marker, 1)[0].strip()
            break
    if len(first) > limit:
        first = first[: limit - 1].rstrip() + "..."
    return first or fallback


def _detail_markdown(text: str, *, title: str = "详细信息") -> str | None:
    cleaned = _clean_text(text)
    if not cleaned:
        return None
    return f"### {title}\n\n{cleaned}"


def build_summary_agent_narrative(
    text: str,
    *,
    source_type: NarrativeSourceType = "assistant_message",
    source_id: str | None = None,
    severity: str = "info",
) -> dict[str, Any]:
    """Summarize an assistant/task output for the default visible message."""

    return {
        "summary": _compact_sentence(text, fallback="已整理出这一步的主要结论，详细内容可展开查看。"),
        "detailMarkdown": _detail_markdown(text, title="原始回答"),
        "detailText": None,
        "detailCollapsedByDefault": True,
        "sourceType": source_type,
        "sourceId": source_id,
        "severity": severity,
        "suggestedActions": [],
        "agentId": SUMMARY_AGENT_ID,
    }


def build_review_agent_narrative(
    *,
    artifact_title: str | None = None,
    decision: str | None = None,
    note: str | None = None,
    source_id: str | None = None,
) -> dict[str, Any]:
    """Explain review state in research-user language."""

    title = _clean_text(artifact_title) or "该产物"
    decision_label = {
        "approved": "已通过复核",
        "usable": "已通过复核",
        "verified_artifact": "已通过复核",
        "rejected": "未通过复核",
        "needs_revision": "需要修改后再用",
        "rejected_or_needs_revision": "需要修改后再用",
        "pending_review": "等待复核",
        "needs_review": "等待复核",
        "needs_input": "需要补充材料后再用",
        "insufficient_materials": "材料不足，不能确认通过",
        "failed": "未形成可用产物",
        "partial_success": "部分完成，需确认范围后再用",
    }.get(_clean_text(decision), "已完成复核记录")
    summary = f"{title}{decision_label}。"
    detail = _clean_text(note)
    return {
        "summary": summary,
        "detailMarkdown": _detail_markdown(detail, title="复核说明"),
        "detailText": None,
        "detailCollapsedByDefault": True,
        "sourceType": "artifact",
        "sourceId": source_id,
        "severity": "success" if "通过" in decision_label else "warning",
        "suggestedActions": [],
        "agentId": REVIEW_AGENT_ID,
    }


def build_presentation_agent_narrative(
    text: str,
    *,
    source_type: NarrativeSourceType = "guidance",
    source_id: str | None = None,
    artifact_refs: list[str] | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    """Turn execution facts into concise guidance for the conversation panel."""

    refs = artifact_refs or []
    if status == "failed":
        summary = "这一步没有顺利完成，需要先查看失败原因再继续。"
        severity = "error"
    elif refs:
        summary = f"这一步已产出 {len(refs)} 个可查看产物，建议先看摘要，再展开技术细节。"
        severity = "success"
    else:
        summary = _compact_sentence(text, fallback="这一步已完成，没有登记新的产物。")
        severity = "success" if status == "completed" else "info"
    return {
        "summary": summary,
        "detailMarkdown": _detail_markdown(text, title="展开查看细节"),
        "detailText": None,
        "detailCollapsedByDefault": True,
        "sourceType": source_type,
        "sourceId": source_id,
        "severity": severity,
        "suggestedActions": [
            {"label": "查看产物", "intent": "open_artifacts", "primary": True}
        ]
        if refs
        else [],
        "agentId": PRESENTATION_AGENT_ID,
    }
