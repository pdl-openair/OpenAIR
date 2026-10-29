from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Literal

from app.modules.agent_session_executor import (
    AgentSessionExecutionError,
    run_agent_session_turn,
)
from app.modules.literature_live import (
    LiteratureRun,
    SOURCE_MODE_LABELS,
    SourceRow,
    run_literature_live,
)

PositioningStatus = Literal[
    "ready",
    "partial",
    "needs_clarification",
    "no_reliable_candidates",
]

ROLE_LABELS = {
    "entry_review": "入口综述",
    "method_basis": "方法基础",
    "benchmark": "数据集/评测基准",
    "baseline": "对比基线",
    "negative_or_dispute": "负结果/争议线索",
    "empirical_case": "代表性实验",
    "other": "候选线索",
}

INTERNAL_TERMS = (
    "sdr",
    "ope",
    "opentrajectory",
    "skillpack",
    "planner",
    "workspace path",
)

OVERCLAIM_TERMS = (
    "已读全文",
    "读完全文",
    "已完成综述",
    "系统综述完成",
    "已证明",
    "证明了",
    "已完成检索",
    "完成了检索",
    "全文证据",
)


class KeyLiteraturePositioningUnavailable(RuntimeError):
    pass


def position_key_literature(
    question: str,
    *,
    top_k: int = 6,
    timeout: float = 12.0,
    owner_id: str,
    workspace_id: str,
) -> dict[str, Any]:
    normalized = _normalize(question)
    top_k = max(1, min(int(top_k or 6), 12))
    run = run_literature_live(
        normalized,
        top_k=max(top_k * 2, 12),
        timeout=timeout,
    )
    artifact = _run_positioning_agent(
        question=normalized,
        run=run,
        top_k=top_k,
        owner_id=owner_id,
        workspace_id=workspace_id,
    )
    return _sanitize_user_text(
        _result_from_agent_artifact(
            question=question,
            normalized=normalized,
            run=run,
            artifact=artifact,
            top_k=top_k,
        )
    )


def _run_positioning_agent(
    *,
    question: str,
    run: LiteratureRun,
    top_k: int,
    owner_id: str,
    workspace_id: str,
) -> dict[str, Any]:
    supplied = [
        _candidate_payload(row, index=index)
        for index, row in enumerate(run.sources[:20], start=1)
    ]
    retrieval_unknown = (
        not supplied
        and any(
            attempt.status in {"failed", "timeout", "partial_error"}
            for attempt in run.attempted_apis
        )
    )
    controlled_context = {
        "executionAuthorization": "explicit_user_request",
        "retrievalOutcome": "unknown" if retrieval_unknown else "attempted",
        "screeningLimit": top_k,
        "instructions": [
            "Use only the supplied retrieval candidates; never invent a paper.",
            "Return every supplied candidate exactly once in candidate_literature.",
            "Copy candidate_id and source_identifier exactly as supplied.",
            "Judge scientific relevance, screening status, role_hint, reading priority, and confidence.",
            "Treat title/abstract/metadata as bounded evidence; do not claim full-text review.",
        ],
        "retrievalCandidates": supplied,
        "retrievalAttempts": [
            {
                "sourceName": attempt.source_api,
                "query": attempt.query,
                "status": attempt.status,
                "resultCount": attempt.result_count,
                "error": attempt.error,
            }
            for attempt in run.attempted_apis
        ],
    }
    digest = hashlib.sha256(
        json.dumps(
            {
                "question": question,
                "sources": [item["source_identifier"] for item in supplied],
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()[:12]
    session = {
        "sessionKind": "agent",
        "agentId": "key_literature_positioning",
        "sessionId": f"klp_{digest}",
        "steps": [],
        "activeStepKey": None,
        "messages": [
            {
                "role": "user",
                "content": (
                    "Controlled retrieval context follows. "
                    "Use it as the only retrieval evidence:\n"
                    + json.dumps(
                        controlled_context,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                ),
            }
        ],
    }
    try:
        output = run_agent_session_turn(
            session=session,
            user_goal=question,
            owner_id=owner_id,
            workspace_id=workspace_id,
        )
    except AgentSessionExecutionError as exc:
        raise KeyLiteraturePositioningUnavailable(
            f"key literature positioning agent unavailable: {exc.code}"
        ) from exc

    body = output.get("body")
    if not isinstance(body, dict):
        raise KeyLiteraturePositioningUnavailable(
            "key literature positioning agent returned no structured artifact"
        )
    _validate_agent_artifact(body, supplied)
    return body


def _candidate_payload(row: SourceRow, *, index: int) -> dict[str, Any]:
    return {
        "candidate_id": f"c{index}",
        "source_identifier": _source_identifier(row),
        "source_ref": row.source_ref,
        "title": row.title,
        "authors": row.authors[:8],
        "year": row.year,
        "venue": row.venue,
        "abstract": (row.abstract or "")[:1800],
        "source_api": row.source_api,
        "source_mode": row.source_mode,
        "url": row.url,
        "doi": row.doi,
        "arxiv_id": row.arxiv_id,
    }


def _source_identifier(row: SourceRow) -> str:
    if row.doi:
        return f"doi:{row.doi.strip().lower()}"
    if row.arxiv_id:
        return f"arxiv:{row.arxiv_id.strip().lower()}"
    if row.url:
        return f"url:{row.url.strip()}"
    return f"{row.source_api}:{row.external_id}"


def _validate_agent_artifact(
    artifact: dict[str, Any],
    supplied: list[dict[str, Any]],
) -> None:
    candidates = artifact.get("candidate_literature")
    priority = artifact.get("reading_priority")
    if not isinstance(candidates, list) or not isinstance(priority, list):
        raise KeyLiteraturePositioningUnavailable(
            "agent artifact missing candidate_literature or reading_priority"
        )

    supplied_ids = {
        str(item["source_identifier"]): str(item["candidate_id"])
        for item in supplied
    }
    returned_ids: dict[str, str] = {}
    candidate_ids: set[str] = set()

    for item in candidates:
        if not isinstance(item, dict):
            raise KeyLiteraturePositioningUnavailable(
                "agent candidate row is not an object"
            )
        source_identifier = str(item.get("source_identifier") or "")
        candidate_id = str(item.get("candidate_id") or "")
        confidence = item.get("confidence")
        if source_identifier not in supplied_ids:
            raise KeyLiteraturePositioningUnavailable(
                "agent returned a candidate that was not supplied by retrieval"
            )
        if supplied_ids[source_identifier] != candidate_id:
            raise KeyLiteraturePositioningUnavailable(
                "agent changed a supplied candidate identity"
            )
        if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
            raise KeyLiteraturePositioningUnavailable(
                "agent candidate confidence is missing or invalid"
            )
        if not 0.0 <= float(confidence) <= 1.0:
            raise KeyLiteraturePositioningUnavailable(
                "agent candidate confidence is outside 0..1"
            )
        if source_identifier in returned_ids or candidate_id in candidate_ids:
            raise KeyLiteraturePositioningUnavailable(
                "agent returned duplicate candidate identity"
            )
        returned_ids[source_identifier] = candidate_id
        candidate_ids.add(candidate_id)

    if set(returned_ids) != set(supplied_ids):
        raise KeyLiteraturePositioningUnavailable(
            "agent did not return exactly the supplied retrieval candidate set"
        )

    priority_ids: set[str] = set()
    for row in priority:
        if not isinstance(row, dict):
            raise KeyLiteraturePositioningUnavailable(
                "agent reading priority row is not an object"
            )
        candidate_id = str(row.get("candidate_id") or "")
        if candidate_id not in candidate_ids:
            raise KeyLiteraturePositioningUnavailable(
                "agent reading priority references an unknown candidate"
            )
        if candidate_id in priority_ids:
            raise KeyLiteraturePositioningUnavailable(
                "agent reading priority contains duplicate candidate"
            )
        priority_ids.add(candidate_id)


def _result_from_agent_artifact(
    *,
    question: str,
    normalized: str,
    run: LiteratureRun,
    artifact: dict[str, Any],
    top_k: int,
) -> dict[str, Any]:
    supplied_rows = {
        _source_identifier(row): row
        for row in run.sources[:20]
    }
    candidates = list(artifact.get("candidate_literature") or [])
    priorities = sorted(
        list(artifact.get("reading_priority") or []),
        key=lambda row: int(row.get("rank") or 10_000),
    )
    priority_by_id = {
        str(row.get("candidate_id")): row
        for row in priorities[:top_k]
    }

    key_readings: list[dict[str, Any]] = []
    candidate_readings: list[dict[str, Any]] = []
    fallback_rank = len(priority_by_id) + 1

    for candidate in candidates:
        row = supplied_rows[str(candidate["source_identifier"])]
        candidate_id = str(candidate["candidate_id"])
        priority = priority_by_id.get(candidate_id)
        reading = _reading_from_agent(
            row=row,
            candidate=candidate,
            priority=priority,
            rank=int(priority["rank"]) if priority else fallback_rank,
            key=priority is not None,
        )
        if priority is not None:
            key_readings.append(reading)
        else:
            candidate_readings.append(reading)
            fallback_rank += 1

    source_errors = [
        attempt
        for attempt in run.attempted_apis
        if attempt.status in {"failed", "timeout", "partial_error"} or attempt.error
    ]
    handoff = artifact.get("handoff") or {}
    handoff_status = str(handoff.get("handoff_status") or "")
    if handoff_status == "needs_user_clarification":
        status: PositioningStatus = "needs_clarification"
    elif handoff_status == "blocked_on_retrieval_unknown":
        status = "no_reliable_candidates"
    elif key_readings:
        status = "partial" if source_errors else "ready"
    else:
        status = "no_reliable_candidates"

    screened_in = sum(
        1
        for item in candidates
        if item.get("screening_status") in {"seed", "screened_in"}
    )
    coverage_limitations = [
        str(item.get("statement") or "").strip()
        for item in artifact.get("coverage_limitations") or []
        if isinstance(item, dict) and str(item.get("statement") or "").strip()
    ]
    covered_roles = list(
        dict.fromkeys(
            reading["role"]
            for reading in key_readings
            if reading.get("role")
        )
    )

    search_boundary = artifact.get("search_boundary") or {}
    keyword_families = artifact.get("keyword_families") or []
    source_scope = artifact.get("source_scope") or {}
    keywords = list(
        dict.fromkeys(
            str(keyword).strip()
            for family in keyword_families
            if isinstance(family, dict)
            for keyword in family.get("keywords") or []
            if str(keyword).strip()
        )
    )
    query_phrases = [
        " ".join(
            str(keyword).strip()
            for keyword in family.get("keywords") or []
            if str(keyword).strip()
        )
        for family in keyword_families
        if isinstance(family, dict)
    ]
    query_phrases = [value for value in query_phrases if value]
    constraints = [
        str(item).strip()
        for item in search_boundary.get("constraints") or []
        if str(item).strip()
    ]
    boundary_parts = [
        f"纳入：{search_boundary.get('included_scope')}",
        f"排除：{search_boundary.get('excluded_scope')}",
    ]
    if constraints:
        boundary_parts.append("约束：" + "；".join(constraints))

    instruction = str(handoff.get("instruction") or "").strip()
    next_questions = [instruction] if instruction else []

    return {
        "status": status,
        "querySummary": {
            "originalQuestion": question,
            "normalizedQuery": (
                str(search_boundary.get("target_question") or "").strip()
                or normalized
            ),
            "topK": top_k,
            "retrievalResultCount": run.source_count,
            "dedupedResultCount": run.deduped_count,
            "relevantCandidateCount": screened_in,
        },
        "searchStrategy": {
            "normalizedQuery": (
                str(search_boundary.get("target_question") or "").strip()
                or normalized
            ),
            "queryVariants": [normalized] if normalized else [],
            "coreTerms": keywords[:20],
            "queryPhrases": query_phrases[:12],
            "sourcePriority": [
                str(value)
                for value in (
                    source_scope.get("collections")
                    or source_scope.get("source_kinds")
                    or []
                )
            ],
            "topK": top_k,
            "boundary": "；".join(boundary_parts),
        },
        "keyReadings": key_readings,
        "candidateReadings": candidate_readings,
        "coverage": {
            "coveredAspects": [f"Agent 已识别{role}候选" for role in covered_roles],
            "missingAspects": coverage_limitations,
        },
        "sourceAttempts": [
            {
                "sourceName": attempt.source_api,
                "query": attempt.query,
                "status": attempt.status,
                "retrievedAt": attempt.retrieved_at,
                "resultCount": attempt.result_count,
                "error": attempt.error,
            }
            for attempt in run.attempted_apis
        ],
        "nextQuestions": next_questions,
        "userFacingSummary": _summary(
            status=status,
            key_count=len(key_readings),
            candidate_count=len(candidate_readings),
            source_error_count=len(source_errors),
        ),
    }


def _reading_from_agent(
    *,
    row: SourceRow,
    candidate: dict[str, Any],
    priority: dict[str, Any] | None,
    rank: int,
    key: bool,
) -> dict[str, Any]:
    role_hint = str(candidate.get("role_hint") or "other")
    rationale = str(candidate.get("screening_rationale") or "").strip()
    why_read = (
        str((priority or {}).get("reason") or "").strip()
        or rationale
        or "由 key_literature_positioning Agent 完成科研筛选。"
    )
    return {
        "rank": rank,
        "sourceRef": row.source_ref,
        "title": row.title,
        "authors": row.authors,
        "year": row.year,
        "venue": row.venue,
        "url": row.url,
        "doi": row.doi,
        "arxivId": row.arxiv_id,
        "sourceName": row.source_api,
        "sourceMode": row.source_mode,
        "sourceModeLabel": SOURCE_MODE_LABELS.get(
            row.source_mode,
            "来源状态未明",
        ),
        "role": ROLE_LABELS.get(role_hint, "候选线索"),
        "whyRead": why_read,
        "matchedSignals": [rationale] if rationale else [],
        "limitations": _limitations(row, key=key),
        "confidence": round(float(candidate["confidence"]), 3),
        "provenance": {
            "sourceName": row.source_api,
            "externalId": row.external_id,
            "query": (row.provenance or {}).get("query"),
            "retrievedAt": row.retrieved_at,
            "sourceMode": row.source_mode,
            "sourceIdentifier": candidate["source_identifier"],
            "semanticAuthority": "key_literature_positioning_agent",
            "selectionStatus": (
                "key_reading_candidate"
                if key
                else "candidate_not_priority"
            ),
        },
    }


def _limitations(row: SourceRow, *, key: bool) -> list[str]:
    limitations = [
        "本次只使用题录、摘要、来源元数据和检索结果，没有阅读全文。",
        "该定位不能替代系统综述或人工质量评价。",
    ]
    if row.source_mode == "metadata_only":
        limitations.append("该候选只有题录/元数据，不能当作可引用依据。")
    elif row.source_mode == "abstract_available":
        limitations.append("该候选有摘要，但摘要不能支撑证据结论。")
    elif row.source_mode == "arxiv_fulltext":
        limitations.append("arXiv 仅表示有论文页面线索；本次没有解析或阅读全文。")
    elif row.source_mode == "oa_located":
        limitations.append("开放获取线索不等于已经读取或核验证据。")
    if not key:
        limitations.append("当前未进入优先阅读清单，不能当作关键文献结论。")
    return limitations


def _summary(
    *,
    status: PositioningStatus,
    key_count: int,
    candidate_count: int,
    source_error_count: int,
) -> str:
    if status == "needs_clarification":
        return "当前研究问题仍需补充信息；系统不会用关键词规则替代科研判断。"
    if status == "no_reliable_candidates":
        return (
            f"本轮保留 {candidate_count} 条真实检索候选，但正式 Agent "
            "没有形成可交付的优先阅读清单。"
        )
    if status == "partial":
        return (
            f"正式 Agent 已形成 {key_count} 条优先阅读候选；"
            f"同时有 {source_error_count} 个来源出现异常，因此当前结果属于部分覆盖。"
        )
    return (
        f"正式 Agent 已基于真实检索结果形成 {key_count} 条优先阅读候选；"
        "科研相关性、角色和排序均来自 Agent 判断。"
    )


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip())


def _sanitize_user_text(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: item if key == "provenance" else _sanitize_user_text(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_sanitize_user_text(item) for item in value]
    if isinstance(value, str):
        return _clean_user_text(value)
    return value


def _clean_user_text(value: str) -> str:
    text = value
    lowered = text.lower()
    for term in INTERNAL_TERMS:
        pattern = re.compile(
            rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])",
            re.IGNORECASE,
        )
        if pattern.search(text):
            text = pattern.sub("内部能力", text)
            lowered = text.lower()
    for claim in OVERCLAIM_TERMS:
        if claim in text:
            text = text.replace(claim, "当前未核验")
    return text
