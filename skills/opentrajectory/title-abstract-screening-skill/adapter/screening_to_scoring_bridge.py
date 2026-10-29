"""Thin bridge mapper: title-abstract-screening -> candidate-source-scoring input."""

from __future__ import annotations

from typing import Any

_ALLOWED_DECISIONS = {"include", "maybe"}
_PASSTHROUGH_FIELDS = (
    "candidate_id",
    "dedupe_key",
    "title",
    "abstract",
    "source_name",
    "source_backend",
    "year",
    "citation_count",
    "provenance",
    "external_ids",
    "access_status",
    "needs_acquisition",
    "available_access_types",
    "preferred_access_type",
    "source_url",
    "landing_page_url",
    "pdf_url",
    "matched_source_count",
)


def map_screened_to_scoring_input(
    screened_output: dict[str, Any],
    facets: dict[str, Any],
) -> dict[str, Any]:
    """Assemble scoring input while preserving upstream metadata and readiness."""
    screened = screened_output.get("screened_candidates", [])

    deduped_candidates: list[dict[str, Any]] = []
    for candidate in screened:
        if candidate.get("screening_decision") not in _ALLOWED_DECISIONS:
            continue
        mapped = {field: candidate.get(field) for field in _PASSTHROUGH_FIELDS if field in candidate}
        mapped.setdefault("provenance", [])
        deduped_candidates.append(mapped)

    return {"deduped_candidates": deduped_candidates, "facets": facets}
