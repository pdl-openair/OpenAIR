import json
import re
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from app.main import app
from app.modules import key_literature_positioning as klp
from app.modules import literature_live
from app.modules.literature_live import Attempt, LiteratureRun, SourceRow


client = TestClient(app)
_EVAL_CASES_PATH = Path(__file__).parent / "fixtures" / "key_literature_positioning_eval_cases.json"


class _ServiceResult:
    def __init__(self, status_code: int, payload: dict[str, Any]) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict[str, Any]:
        return self._payload


def _position(question: str, *, top_k: int = 5) -> _ServiceResult:
    try:
        return _ServiceResult(
            200,
            klp.position_key_literature(
                question,
                top_k=top_k,
                owner_id="user_demo_owner",
                workspace_id="ws_demo_default",
            ),
        )
    except klp.KeyLiteraturePositioningUnavailable:
        return _ServiceResult(503, {"detail": "key_literature_positioning_unavailable"})


def _row(
    title: str,
    abstract: str | None,
    *,
    source_api: str = "OpenAlex",
    source_mode: str = "abstract_available",
    external_id: str | None = None,
) -> SourceRow:
    external_id = external_id or title.lower().replace(" ", "-")[:48]
    return SourceRow(
        source_ref="S0",
        title=title,
        authors=["A. Researcher", "B. Scholar"],
        year=2024,
        venue="arXiv" if source_api == "arXiv" else "Journal of Testable Research",
        doi=None if source_api == "arXiv" else f"10.0000/{external_id}",
        arxiv_id=external_id if source_api == "arXiv" else None,
        url=f"https://example.org/{external_id}",
        abstract=abstract,
        source_api=source_api,
        external_id=external_id,
        retrieved_at=literature_live.utc_now(),
        source_mode=source_mode,
        readiness_label=source_mode,
        selection_reason="retrieved candidate",
        provenance={
            "sourceApi": source_api,
            "query": "",
            "externalId": external_id,
            "url": f"https://example.org/{external_id}",
            "retrievedAt": literature_live.utc_now(),
            "sourceMode": source_mode,
        },
    )


def _run(query: str, rows: list[SourceRow], *, attempts: list[Attempt] | None = None) -> LiteratureRun:
    run = LiteratureRun(
        topic=query,
        strategy=literature_live.build_search_strategy(query, top_k=12),
        key_status={},
        attempted_apis=attempts
        or [
            Attempt(
                "OpenAlex",
                query,
                "ok",
                literature_live.utc_now(),
                result_count=len(rows),
            )
        ],
    )
    run.sources = rows
    for idx, row in enumerate(run.sources, start=1):
        row.source_ref = f"S{idx}"
        row.provenance["query"] = query
        row.provenance["sourceRef"] = row.source_ref
    return run


def _identifier(row: SourceRow) -> str:
    if row.doi:
        return f"doi:{row.doi.strip().lower()}"
    if row.arxiv_id:
        return f"arxiv:{row.arxiv_id.strip().lower()}"
    if row.url:
        return f"url:{row.url.strip()}"
    return f"{row.source_api}:{row.external_id}"


def _artifact(
    rows: list[SourceRow],
    *,
    priority_count: int | None = None,
    handoff_status: str = "ready_for_next_consumer",
) -> dict[str, Any]:
    limit = len(rows) if priority_count is None else max(0, min(priority_count, len(rows)))
    roles = [
        "entry_review",
        "benchmark",
        "baseline",
        "negative_or_dispute",
        "method_basis",
        "empirical_case",
    ]
    candidates = []
    priorities = []
    for idx, row in enumerate(rows, start=1):
        candidate_id = f"c{idx}"
        candidates.append(
            {
                "candidate_id": candidate_id,
                "source_identifier": _identifier(row),
                "title": row.title,
                "screening_status": "screened_in" if idx <= limit else "screened_out",
                "screening_rationale": f"Controlled Agent judgment for candidate {idx}.",
                "role_hint": roles[(idx - 1) % len(roles)],
                "confidence": round(0.9 - (idx - 1) * 0.05, 2),
            }
        )
        if idx <= limit:
            priorities.append(
                {
                    "rank": idx,
                    "candidate_id": candidate_id,
                    "reason": f"Controlled Agent reading priority {idx}.",
                }
            )
    if handoff_status == "needs_user_clarification":
        next_consumer = "user_clarification"
    elif handoff_status == "blocked_on_retrieval_unknown":
        next_consumer = "controller_planning"
    else:
        next_consumer = "paper_evidence_extraction"
    return {
        "search_boundary": {
            "authorization_status": "retrieval_authorized",
            "retrieval_outcome": "attempted" if rows else "unknown",
            "target_question": "Controlled research question for literature positioning.",
            "included_scope": "Literature directly relevant to the controlled research question.",
            "excluded_scope": "Sources outside the bounded research question.",
            "constraints": ["Metadata and abstract bounded screening."],
        },
        "keyword_families": [
            {
                "family_id": "topic",
                "label": "Controlled topic",
                "keywords": ["controlled", "research", "topic"],
            }
        ],
        "source_scope": {
            "source_kinds": ["peer_reviewed", "preprint"],
            "languages": ["en"],
            "collections": ["OpenAlex", "Semantic Scholar", "arXiv"],
        },
        "candidate_literature": candidates,
        "reading_priority": priorities,
        "coverage_limitations": [
            {
                "limitation_id": "limit_1",
                "limitation_kind": "coverage",
                "statement": "Only retrieved metadata and abstracts were supplied for controlled screening.",
            }
        ],
        "handoff": {
            "handoff_status": handoff_status,
            "next_consumer": next_consumer,
            "candidate_ids": [
                item["candidate_id"]
                for item in candidates
                if item["screening_status"] != "screened_out"
            ],
            "instruction": "Review the Agent-screened candidates before evidence extraction.",
            "requires_user_confirmation_before_external_execution": False,
        },
    }


def _install_agent(monkeypatch, artifact: dict[str, Any]) -> None:
    monkeypatch.setattr(klp, "_run_positioning_agent", lambda **_kwargs: artifact)


def _all_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        out = []
        for item in value:
            out.extend(_all_strings(item))
        return out
    if isinstance(value, dict):
        out = []
        for item in value.values():
            out.extend(_all_strings(item))
        return out
    return []


def test_agent_semantics_drive_key_readings_and_preserve_provenance(monkeypatch) -> None:
    query = "retrieval augmented generation evaluation benchmark hallucination mitigation"
    rows = [
        _row("A survey of retrieval augmented generation evaluation benchmarks", "Review of evaluation benchmarks.", external_id="rag-survey"),
        _row("Benchmarking retrieval augmented generation systems", "Benchmark datasets.", source_api="arXiv", source_mode="arxiv_fulltext", external_id="2401.00001"),
        _row("Baseline comparisons for retrieval augmented generation", "Comparative baseline study.", external_id="rag-baseline"),
    ]
    monkeypatch.setattr(klp, "run_literature_live", lambda query, top_k=12, timeout=12.0: _run(query, rows))
    _install_agent(monkeypatch, _artifact(rows))
    res = _position(query, top_k=3)
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ready"
    assert len(body["keyReadings"]) == 3
    assert body["keyReadings"][0]["role"] == "入口综述"
    assert body["keyReadings"][0]["provenance"]["semanticAuthority"] == "key_literature_positioning_agent"


def test_chinese_question_uses_raw_user_query_without_code_keyword_translation(monkeypatch) -> None:
    query = "锂金属电池 SEI 界面稳定性的关键综述和评测基准"
    rows = [_row("锂金属电池 SEI 界面研究", "摘要。", external_id="sei-1")]
    seen = {}
    def fake_run(query, top_k=12, timeout=12.0):
        seen["query"] = query
        return _run(query, rows)
    monkeypatch.setattr(klp, "run_literature_live", fake_run)
    _install_agent(monkeypatch, _artifact(rows))
    res = _position(query)
    assert res.status_code == 200
    assert seen["query"] == query


def test_partial_source_failure_preserves_attempts_and_agent_result(monkeypatch) -> None:
    query = "retrieval augmented generation evaluation benchmark"
    rows = [_row("RAG benchmark survey", "Benchmark survey abstract.", external_id="rag-1")]
    attempts = [
        Attempt("OpenAlex", query, "failed", literature_live.utc_now(), error="HTTPError:503", result_count=0),
        Attempt("arXiv", query, "ok", literature_live.utc_now(), result_count=1),
    ]
    monkeypatch.setattr(klp, "run_literature_live", lambda query, top_k=12, timeout=12.0: _run(query, rows, attempts=attempts))
    _install_agent(monkeypatch, _artifact(rows))
    res = _position(query)
    assert res.status_code == 200
    assert res.json()["status"] == "partial"
    assert any(row["status"] == "failed" for row in res.json()["sourceAttempts"])


def test_agent_can_screen_all_candidates_out_without_keyword_gate(monkeypatch) -> None:
    query = "molecular property prediction graph neural network benchmark"
    rows = [_row("Generic neural network optimization", "Image optimizer paper.", external_id="generic-1")]
    monkeypatch.setattr(klp, "run_literature_live", lambda query, top_k=12, timeout=12.0: _run(query, rows))
    _install_agent(monkeypatch, _artifact(rows, priority_count=0))
    res = _position(query)
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "no_reliable_candidates"
    assert body["keyReadings"] == []
    assert len(body["candidateReadings"]) == 1


def test_agent_can_request_clarification_without_code_semantic_oracle(monkeypatch) -> None:
    rows = []
    monkeypatch.setattr(klp, "run_literature_live", lambda query, top_k=12, timeout=12.0: _run(query, rows))
    _install_agent(monkeypatch, _artifact(rows, priority_count=0, handoff_status="needs_user_clarification"))
    res = _position("帮我找文献")
    assert res.status_code == 200
    assert res.json()["status"] == "needs_clarification"


def test_metadata_only_candidate_is_not_fulltext_evidence(monkeypatch) -> None:
    query = "retrieval augmented generation evaluation benchmark"
    rows = [_row("Metadata-only survey", None, source_mode="metadata_only", external_id="metadata-1")]
    monkeypatch.setattr(klp, "run_literature_live", lambda query, top_k=12, timeout=12.0: _run(query, rows))
    _install_agent(monkeypatch, _artifact(rows))
    res = _position(query)
    assert res.status_code == 200
    reading = res.json()["keyReadings"][0]
    assert any("只有题录/元数据" in item for item in reading["limitations"])


def test_agent_cannot_invent_candidate_identity(monkeypatch) -> None:
    query = "retrieval augmented generation evaluation benchmark"
    rows = [_row("RAG benchmark survey", "Benchmark survey.", external_id="rag-1")]
    monkeypatch.setattr(klp, "run_literature_live", lambda query, top_k=12, timeout=12.0: _run(query, rows))
    bad = _artifact(rows)
    bad["candidate_literature"][0]["source_identifier"] = "doi:10.0000/invented"
    monkeypatch.setattr(klp, "run_agent_session_turn", lambda **_kwargs: {"body": bad})
    res = _position(query)
    assert res.status_code == 503
    assert res.json()["detail"] == "key_literature_positioning_unavailable"


def test_user_visible_text_avoids_internal_terms_and_overclaims(monkeypatch) -> None:
    query = "multi-agent workflow literature"
    rows = [_row("Multi-agent workflow study", "Agent workflow abstract.", external_id="agent-1")]
    monkeypatch.setattr(klp, "run_literature_live", lambda query, top_k=12, timeout=12.0: _run(query, rows))
    artifact = _artifact(rows)
    artifact["candidate_literature"][0]["screening_rationale"] = "planner workflow says 已完成检索 and 已证明 relevance."
    _install_agent(monkeypatch, artifact)
    res = _position(query)
    assert res.status_code == 200
    body = res.json()
    visible = {
        "summary": body["userFacingSummary"],
        "nextQuestions": body["nextQuestions"],
        "coverage": body["coverage"],
        "readings": body["keyReadings"] + body["candidateReadings"],
    }
    text = "\n".join(_all_strings(visible)).lower()
    for term in ("sdr", "ope", "opentrajectory", "skillpack", "workspace path", "planner"):
        assert re.search(
            rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])",
            text,
        ) is None
    for claim in ("已读全文", "已完成综述", "已证明", "已完成检索"):
        assert claim not in text


def test_eval_cases_fixture_covers_required_statuses() -> None:
    cases = json.loads(_EVAL_CASES_PATH.read_text(encoding="utf-8"))
    assert cases
    allowed = {"ready", "partial", "needs_clarification", "no_reliable_candidates"}
    for case in cases:
        assert set(case["expectedStatuses"]) <= allowed
