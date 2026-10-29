"""Materialize retrieval fields without filtering model-owned research content."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class FinalizationResult:
    status: str
    output: dict[str, Any] | None
    errors: tuple[Any, ...] = ()


def finalize(input_payload: Mapping[str, Any], model_output: Mapping[str, Any]) -> FinalizationResult:
    strategies = deepcopy(model_output.get("strategies") or [])
    queries = deepcopy(model_output.get("expanded_query_variants") or [])
    facets = deepcopy(model_output.get("facets") or {})
    intents = list(dict.fromkeys(row["search_intent"] for row in strategies))
    refs = list(dict.fromkeys(ref for row in [*strategies, *queries] for ref in row["source_constraints"]))
    fallback_used = any(row["variant_type"] == "fallback" for row in queries)
    output = {
        "search_matrix": strategies,
        "expanded_query_variants": queries,
        "quality_report": {
            "search_matrix_count": len(strategies),
            "expanded_query_variants_count": len(queries),
            "search_intent_coverage": intents,
            "constraints_utilized": refs,
            "fallback_used": fallback_used,
            "validation_flags": [],
        },
        "assembled_facets": {
            **facets,
            "query_facets": {
                "object": deepcopy(facets.get("research_object_en") or facets.get("core_entities") or []),
                "mechanism": deepcopy(facets.get("mechanisms") or []),
                "metric": deepcopy(facets.get("target_metrics_en") or []),
                "context": deepcopy(facets.get("task_context") or []),
                "evaluation": deepcopy(facets.get("evaluation_terms") or []),
            },
        },
        "result_protocol": {
            "status": "ok",
            "accepted_items": [{"path": "search_matrix", "count": len(strategies)}, {"path": "expanded_query_variants", "count": len(queries)}],
            "quarantined_items": [],
            "repaired_items": [],
            "warnings": [],
            "coverage": {"search_intent_count": len(intents), "search_intent_total": len(intents), "accepted_query_count": len(queries), "quarantined_item_count": 0},
        },
    }
    return FinalizationResult("succeeded", output)
