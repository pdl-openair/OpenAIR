"""Deterministic runtime contracts for bounded academic retrieval."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
SKILL_DIR = REPO_ROOT / "skills" / "opentrajectory" / "academic-source-retrieval-skill"
SOURCES = ("openalex", "semantic_scholar", "arxiv")
QUERY_VARIANTS = tuple(
    {
        "query": f"lithium metal electrolyte q{index}",
        "variant_type": "strict",
        "search_intent": "core",
    }
    for index in range(3)
)

if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from adapter import retrieval_adapter  # type: ignore[import-not-found]  # noqa: E402


def _candidate(source: str, query_index: int, result_index: int) -> dict[str, str]:
    return {"candidate_id": f"{source}_{query_index}_{result_index}"}


def _install_fetcher(
    monkeypatch: pytest.MonkeyPatch,
    *,
    source: str,
    fetch_log: list[tuple[int, str]],
    fail: bool = False,
) -> None:
    def fake_fetcher(
        adapted_query: str,
        original_query: str,
        q_idx: int,
        search_intent: str | None,
        variant_type: str | None,
        retrieval_mode: str,
        max_results: int,
        timeout_s: int,
        *args: Any,
        **kwargs: Any,
    ) -> tuple[list[dict[str, str]], str | None]:
        del adapted_query, original_query, search_intent, variant_type
        del retrieval_mode, timeout_s, args, kwargs
        fetch_log.append((q_idx, source))
        if fail:
            return [], "simulated provider failure"
        return [
            _candidate(source, q_idx, result_index)
            for result_index in range(max_results)
        ], None

    monkeypatch.setitem(retrieval_adapter._SOURCE_FETCHERS, source, fake_fetcher)


def _run_retrieval() -> dict[str, Any]:
    return retrieval_adapter.run_retrieval(
        skill2_data={"expanded_query_variants": list(QUERY_VARIANTS)},
        target_sources=list(SOURCES),
        max_results_per_query=10,
        max_total_candidates=120,
        timeout_seconds=5,
        allow_partial_success=True,
    )


def _expected_fetch_units() -> set[tuple[int, str]]:
    return {
        (query_index, source)
        for query_index in range(len(QUERY_VARIANTS))
        for source in SOURCES
    }


def test_candidate_budget_has_no_hidden_fifty_item_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetch_log: list[tuple[int, str]] = []
    for source in SOURCES:
        _install_fetcher(monkeypatch, source=source, fetch_log=fetch_log)

    result = _run_retrieval()
    report = result["retrieval_report"]

    assert set(fetch_log) == _expected_fetch_units()
    assert len(fetch_log) == 9
    assert report["total_candidates"] == 90
    assert sum(int(count) for count in report["source_counts"].values()) == 90
    assert "candidate_limit_reached" not in report["risk_flags"]


def test_provider_failure_does_not_starve_later_fetch_units(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetch_log: list[tuple[int, str]] = []
    for source in SOURCES:
        _install_fetcher(
            monkeypatch,
            source=source,
            fetch_log=fetch_log,
            fail=source == "semantic_scholar",
        )

    result = _run_retrieval()
    report = result["retrieval_report"]

    assert set(fetch_log) == _expected_fetch_units()
    assert len(fetch_log) == 9
    assert report["partial_success"] is True
    assert report["source_counts"]["semantic_scholar"] == 0
    assert report["failed_sources"] == [
        {"source_name": "semantic_scholar", "error": "simulated provider failure"}
    ]
    assert report["total_candidates"] == 60
    assert (
        int(report["source_counts"]["openalex"])
        + int(report["source_counts"]["arxiv"])
    ) == 60
