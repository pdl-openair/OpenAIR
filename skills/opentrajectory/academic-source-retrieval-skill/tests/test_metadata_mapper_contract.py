"""Deterministic schema and provenance contracts for P07a metadata mapping."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import jsonschema
import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
SKILL_DIR = REPO_ROOT / "skills" / "opentrajectory" / "academic-source-retrieval-skill"
ADAPTER_DIR = SKILL_DIR / "adapter"
OUTPUT_SCHEMA_PATH = SKILL_DIR / "output.schema.json"

# Make the skill's adapter package importable as a top-level module
# without requiring the skill bridge or the rest of OpenAIR runtime.
if str(ADAPTER_DIR.parent) not in sys.path:
    sys.path.insert(0, str(ADAPTER_DIR.parent))

from adapter.metadata_mapper import (  # type: ignore[import-not-found]  # noqa: E402
    lit_candidate_to_candidate,
)
from adapter.standalone_sources import LiteratureCandidate  # type: ignore[import-not-found]  # noqa: E402


def _candidate_item_validator() -> jsonschema.Draft7Validator:
    schema = json.loads(OUTPUT_SCHEMA_PATH.read_text(encoding="utf-8"))
    item_schema = schema["properties"]["metadata_candidates"]["items"]
    return jsonschema.Draft7Validator(item_schema)


def _make_lit(
    *,
    title: str,
    dedupe_key: str,
    source_backend: str,
    doi: str | None = None,
    arxiv_id: str | None = None,
    landing_page_url: str | None = None,
    pdf_url: str | None = None,
) -> LiteratureCandidate:
    return LiteratureCandidate(
        title=title,
        dedupe_key=dedupe_key,
        source_backend=source_backend,
        abstract_snippet="abstract snippet",
        year=2024,
        doi=doi,
        arxiv_id=arxiv_id,
        pdf_url=pdf_url,
        landing_page_url=landing_page_url,
        authors=["Alice Author"],
        venue="Journal of Tests",
        raw_metadata={"source_backend": source_backend},
    )


def _assert_top_level_doi_arxiv_keys(candidate: dict) -> None:
    # Schema-required identity keys stay present even when values are unknown.
    assert "doi" in candidate, "top-level 'doi' key must always be emitted"
    assert "arxiv_id" in candidate, "top-level 'arxiv_id' key must always be emitted"


def _assert_passes_output_schema(candidate: dict) -> None:
    errors = sorted(
        _candidate_item_validator().iter_errors(candidate),
        key=lambda e: list(e.absolute_path),
    )
    assert not errors, "candidate fails metadata_candidates item schema: " + "; ".join(
        f"{list(e.absolute_path)}: {e.message}" for e in errors
    )


def test_openalex_candidate_emits_doi_and_arxiv_id_keys_when_both_missing() -> None:
    lit = _make_lit(
        title="A study on lithium metal anodes",
        dedupe_key="openalex:W123",
        source_backend="openalex",
        doi=None,
        arxiv_id=None,
        landing_page_url="https://openalex.org/W123",
        pdf_url=None,
    )

    candidate = lit_candidate_to_candidate(
        lit,
        "openalex",
        original_query="lithium metal battery electrolyte",
        query_index=0,
        result_index=0,
        search_intent=None,
        variant_type="strict",
        retrieval_mode="expanded_query_variant",
        openalex_id="W123",
    )

    _assert_top_level_doi_arxiv_keys(candidate)
    assert candidate["doi"] is None
    assert candidate["arxiv_id"] is None

    # Multi-source provenance MUST stay openalex (NOT deepxiv).
    assert candidate["source_name"] == "openalex"
    assert candidate["landing_page_url"] == "https://openalex.org/W123"
    assert candidate["external_ids"]["openalex_id"] == "W123"
    assert candidate["external_ids"]["doi"] is None
    assert candidate["external_ids"]["arxiv_id"] is None
    assert len(candidate["provenance"]) == 1
    assert candidate["provenance"][0]["source_name"] == "openalex"

    _assert_passes_output_schema(candidate)


def test_semantic_scholar_candidate_emits_doi_and_arxiv_id_keys_when_both_missing() -> None:
    lit = _make_lit(
        title="Solid electrolyte interphase under fast charging",
        dedupe_key="s2:abc123",
        source_backend="semantic_scholar",
        doi=None,
        arxiv_id=None,
        landing_page_url="https://www.semanticscholar.org/paper/abc123",
        pdf_url=None,
    )

    candidate = lit_candidate_to_candidate(
        lit,
        "semantic_scholar",
        original_query="lithium metal battery electrolyte",
        query_index=1,
        result_index=2,
        search_intent="strict_match",
        variant_type="broad",
        retrieval_mode="expanded_query_variant",
        semantic_scholar_id="abc123",
    )

    _assert_top_level_doi_arxiv_keys(candidate)
    assert candidate["doi"] is None
    assert candidate["arxiv_id"] is None

    # Multi-source provenance MUST stay semantic_scholar.
    assert candidate["source_name"] == "semantic_scholar"
    assert candidate["landing_page_url"] == "https://www.semanticscholar.org/paper/abc123"
    assert candidate["external_ids"]["semantic_scholar_id"] == "abc123"
    assert len(candidate["provenance"]) == 1
    assert candidate["provenance"][0]["source_name"] == "semantic_scholar"

    _assert_passes_output_schema(candidate)


def test_openalex_candidate_propagates_real_doi_and_arxiv_id_to_top_level() -> None:
    lit = _make_lit(
        title="High-voltage lithium metal electrolyte design",
        dedupe_key="doi:10.1000/test.1",
        source_backend="openalex",
        doi="10.1000/test.1",
        arxiv_id="2401.00001v2",
        landing_page_url="https://openalex.org/W999",
        pdf_url="https://example.org/paper.pdf",
    )

    candidate = lit_candidate_to_candidate(
        lit,
        "openalex",
        original_query="lithium metal battery electrolyte",
        query_index=0,
        result_index=0,
        search_intent=None,
        variant_type="strict",
        retrieval_mode="expanded_query_variant",
        openalex_id="W999",
    )

    _assert_top_level_doi_arxiv_keys(candidate)
    assert candidate["doi"] == "10.1000/test.1"
    # arxiv_id is normalized (version stripped) inside the mapper.
    assert candidate["arxiv_id"] == "2401.00001"
    assert candidate["external_ids"]["doi"] == "10.1000/test.1"
    assert candidate["external_ids"]["arxiv_id"] == "2401.00001"
    assert candidate["source_name"] == "openalex"

    _assert_passes_output_schema(candidate)


# DeepXiv is a later enrichment channel, not a P07a source identity.
@pytest.mark.parametrize(
    "source_name,dedupe_key,landing",
    [
        ("openalex", "openalex:W42", "https://openalex.org/W42"),
        ("semantic_scholar", "s2:s42", "https://www.semanticscholar.org/paper/s42"),
    ],
)
def test_lit_candidate_preserves_upstream_provenance_and_never_becomes_deepxiv(
    source_name: str, dedupe_key: str, landing: str
) -> None:
    lit = _make_lit(
        title="Provenance guard paper",
        dedupe_key=dedupe_key,
        source_backend=source_name,
        doi=None,
        arxiv_id=None,
        landing_page_url=landing,
        pdf_url=None,
    )

    candidate = lit_candidate_to_candidate(
        lit,
        source_name,
        original_query="lithium metal battery electrolyte",
        query_index=0,
        result_index=0,
        search_intent=None,
        variant_type=None,
        retrieval_mode="expanded_query_variant",
    )

    assert candidate["source_name"] == source_name
    assert candidate["source_name"] != "deepxiv"
    assert candidate["landing_page_url"] == landing
    for prov in candidate["provenance"]:
        assert prov["source_name"] == source_name
        assert prov["source_name"] != "deepxiv"

    _assert_passes_output_schema(candidate)
