"""Project AgentSession artifact JSON bodies into downloadable Markdown."""

from __future__ import annotations

from typing import Any

_FIELD_LABELS: dict[str, str] = {
    "assumptions": "前提与假设",
    "candidate_literature": "候选文献与公开实现",
    "handoff": "下一步衔接",
    "next_action": "下一步行动",
    "next_evidence_needs": "下一步证据需求",
    "question_candidates": "候选研究问题",
    "reading_priority": "阅读优先级",
    "reasoning_summary": "推理摘要",
    "retrieval_outcome": "检索结果",
    "scope": "研究范围",
    "scope_boundary": "范围边界",
    "strategy_only": "是否仅输出策略",
    "summary": "摘要",
    "uncertainty": "不确定性",
    "verification_readiness": "验证准备情况",
}

_ENUM_LABELS: dict[str, str] = {
    "not_attempted": "尚未执行检索",
    "strategy_only": "仅形成检索策略",
    "true": "是",
    "false": "否",
}

_SKIP_KEYS = {
    "artifactid",
    "id",
    "schema",
    "schemaversion",
    "version",
    "runtimemetadata",
}


def _norm(key: str) -> str:
    return "".join(ch for ch in key if ch.isalnum()).lower()


def _label(key: str) -> str:
    known = _FIELD_LABELS.get(key) or _FIELD_LABELS.get(_norm(key))
    if known:
        return known
    text = key.replace("_", " ").replace("-", " ").strip()
    return text[:1].upper() + text[1:] if text else "补充信息"


def _scalar(value: Any) -> str:
    if value is None:
        return "未提供"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value).strip()
    return _ENUM_LABELS.get(text, text) or "未提供"


def _lines_for(value: Any, *, depth: int = 2) -> list[str]:
    if value is None or isinstance(value, (str, int, float, bool)):
        return [_scalar(value)]

    if isinstance(value, list):
        if not value:
            return ["未提供"]
        if all(item is None or isinstance(item, (str, int, float, bool)) for item in value):
            return [f"- {_scalar(item)}" for item in value]
        out: list[str] = []
        for index, item in enumerate(value, start=1):
            if isinstance(item, dict):
                title = None
                for key in ("title", "name", "question", "statement", "description", "label"):
                    raw = item.get(key)
                    if isinstance(raw, str) and raw.strip():
                        title = raw.strip()
                        break
                heading = title or f"条目 {index}"
                out.append(f"{'#' * min(depth, 6)} {heading}")
                out.append("")
                out.extend(_lines_for(item, depth=depth + 1))
                out.append("")
            else:
                out.extend(_lines_for(item, depth=depth + 1))
                out.append("")
        return out

    if isinstance(value, dict):
        entries = [
            (key, child)
            for key, child in value.items()
            if _norm(str(key)) not in _SKIP_KEYS
        ]
        if not entries:
            return ["未提供"]
        out: list[str] = []
        for key, child in entries:
            out.append(f"{'#' * min(depth, 6)} {_label(str(key))}")
            out.append("")
            out.extend(_lines_for(child, depth=depth + 1))
            out.append("")
        return out

    return [_scalar(value)]


def project_agent_session_artifact_markdown(body: dict[str, Any], title: str) -> str:
    """Return a readable Markdown document for one AgentSession artifact body."""
    heading = (title or "").strip() or "会话产物"
    parts = [f"# {heading}", ""]
    parts.extend(_lines_for(body, depth=2))
    text = "\n".join(parts).strip()
    return f"{text}\n"
