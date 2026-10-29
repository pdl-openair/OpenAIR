"""Narrow runtime regression for candidate dedupe identity closure."""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from adapter.dedup_adapter import deduplicate_candidates  # noqa: E402


def candidate(
    candidate_id: str,
    *,
    dedupe_key: str | None = None,
    normalized_title: str | None = None,
    doi: str | None = None,
    arxiv_id: str | None = None,
    openalex_id: str | None = None,
    source_name: str = "openalex",
    matched_query: str = "query",
    citation_count: int = 0,
    year: int = 2024,
    provenance: list[dict] | None = None,
) -> dict:
    external_ids = {
        "doi": doi,
        "arxiv_id": arxiv_id,
        "openalex_id": openalex_id,
    }
    return {
        "candidate_id": candidate_id,
        "dedupe_key": dedupe_key,
        "normalized_title": normalized_title,
        "external_ids": external_ids,
        "title": normalized_title or candidate_id,
        "source_name": source_name,
        "matched_query": matched_query,
        "citation_count": citation_count,
        "year": year,
        "provenance": provenance
        if provenance is not None
        else [{"source_name": source_name, "matched_query": matched_query}],
    }


def semantic_signature(response: dict) -> list[tuple]:
    rows = []
    for item in response["deduped_candidates"]:
        provenance = tuple(
            sorted((row.get("source_name"), row.get("matched_query")) for row in item["provenance"])
        )
        rows.append(
            (
                item["dedupe_key"],
                item["candidate_id"],
                item["matched_source_count"],
                item["dedup_reason"],
                provenance,
            )
        )
    return rows


def main() -> int:
    # CASE 1 - DOI identity: equivalent DOI forms collapse and strongest version wins.
    doi_rows = [
        candidate(
            "doi-low",
            dedupe_key="https://doi.org/10.1000/Example",
            source_name="semantic_scholar",
            citation_count=4,
        ),
        candidate(
            "doi-high",
            doi="10.1000/example",
            source_name="openalex",
            citation_count=9,
        ),
    ]
    doi_result = deduplicate_candidates({"metadata_candidates": doi_rows})
    assert len(doi_result["deduped_candidates"]) == 1
    assert doi_result["deduped_candidates"][0]["dedupe_key"] == "doi:10.1000/example"
    assert doi_result["deduped_candidates"][0]["candidate_id"] == "doi-high"
    assert doi_result["deduped_candidates"][0]["matched_source_count"] == 2

    # CASE 2 - arXiv versions: v1/v2 are one canonical paper identity.
    arxiv_rows = [
        candidate("arxiv-v1", arxiv_id="2401.12345v1", source_name="arxiv", citation_count=1),
        candidate("arxiv-v2", arxiv_id="2401.12345v2", source_name="semantic_scholar", citation_count=3),
    ]
    arxiv_result = deduplicate_candidates({"metadata_candidates": arxiv_rows})
    assert len(arxiv_result["deduped_candidates"]) == 1
    assert arxiv_result["deduped_candidates"][0]["dedupe_key"] == "arxiv:2401.12345"
    assert arxiv_result["deduped_candidates"][0]["candidate_id"] == "arxiv-v2"

    # CASE 3 - an existing legacy arXiv dedupe_key remains valid and loses version suffix.
    legacy_arxiv = deduplicate_candidates(
        {
            "metadata_candidates": [
                candidate("legacy-arxiv", dedupe_key="arxiv:2401.12345v7", citation_count=2)
            ]
        }
    )
    assert legacy_arxiv["deduped_candidates"][0]["dedupe_key"] == "arxiv:2401.12345"

    # CASE 4 - title fallback stays cross-source when no strong identity exists.
    title_rows = [
        candidate("title-a", normalized_title="same normalized title", source_name="openalex", citation_count=2),
        candidate(
            "title-b",
            normalized_title="same normalized title",
            source_name="semantic_scholar",
            citation_count=5,
        ),
    ]
    title_result = deduplicate_candidates({"metadata_candidates": title_rows})
    assert len(title_result["deduped_candidates"]) == 1
    assert title_result["deduped_candidates"][0]["dedupe_key"].startswith("title:")
    assert title_result["deduped_candidates"][0]["candidate_id"] == "title-b"

    # OpenAlex fallback remains source-specific and stable when no DOI/arXiv/title key exists.
    openalex_result = deduplicate_candidates(
        {"metadata_candidates": [candidate("oa", openalex_id="W12345", normalized_title="openalex title")]}
    )
    assert openalex_result["deduped_candidates"][0]["dedupe_key"] == "openalex:w12345"

    # CASE 5 - merged provenance remains complete and matched_source_count is correct.
    merged = doi_result["deduped_candidates"][0]
    assert {(row["source_name"], row["matched_query"]) for row in merged["provenance"]} == {
        ("semantic_scholar", "query"),
        ("openalex", "query"),
    }
    assert merged["matched_source_count"] == 2

    # CASE 6 - input permutation does not change semantic identity/best-version outcomes.
    forward = deduplicate_candidates({"metadata_candidates": doi_rows + arxiv_rows + title_rows})
    reverse = deduplicate_candidates({"metadata_candidates": list(reversed(doi_rows + arxiv_rows + title_rows))})
    assert semantic_signature(forward) == semantic_signature(reverse)

    # CASE 7 - empty/degraded inputs keep explicit fail-safe/risk behavior.
    empty = deduplicate_candidates({"metadata_candidates": []})
    assert empty["deduped_candidates"] == []
    assert empty["dedup_report"]["input_count"] == 0
    assert empty["dedup_report"]["risk_flags"] == []

    degraded = candidate(
        "degraded",
        dedupe_key="doi:10.1000/degraded",
        source_name="openalex",
        matched_query="degraded query",
        provenance=[],
    )
    degraded_result = deduplicate_candidates({"metadata_candidates": [degraded]})
    degraded_row = degraded_result["deduped_candidates"][0]
    assert "degraded_provenance_recovered" in degraded_result["dedup_report"]["risk_flags"]
    assert degraded_result["dedup_report"]["degraded_provenance_count"] == 1
    assert degraded_row["provenance"][0]["source_name"] == "openalex"
    assert degraded_row["provenance"][0]["matched_query"] == "degraded query"

    print("PASS - candidate dedupe runtime regression")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
