# Formal adapter for academic-source-retrieval-skill P0
from .query_adapter import adapt_query_for_source
from .metadata_mapper import arxiv_hit_to_candidate, lit_candidate_to_candidate
from .retrieval_adapter import run_retrieval
from .content_form_access import (
    candidate_from_arxiv_hit,
    collect_readable_content_forms,
    plan_content_forms,
)

__all__ = [
    "adapt_query_for_source",
    "arxiv_hit_to_candidate",
    "lit_candidate_to_candidate",
    "run_retrieval",
    "candidate_from_arxiv_hit",
    "collect_readable_content_forms",
    "plan_content_forms",
]
