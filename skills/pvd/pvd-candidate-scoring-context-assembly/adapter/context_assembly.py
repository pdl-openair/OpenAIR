"""Deterministic PVD bridge from canonical retrieval artifacts to candidate scoring context."""
from __future__ import annotations

from typing import Any, Mapping


def _ordered_strings(*values: Any) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if isinstance(value, str):
            items = [value]
        elif isinstance(value, (list, tuple)):
            items = list(value)
        else:
            continue
        for item in items:
            text = str(item or "").strip()
            key = text.lower()
            if not text or key in seen:
                continue
            seen.add(key)
            result.append(text)
    return result


def run_pvd_candidate_scoring_context_assembly(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ValueError("payload must be an object")
    query = str(payload.get("user_query") or "").strip()
    retrieval_design = payload.get("retrieval_strategy_design")
    evidence_schema = payload.get("evidence_schema")
    if not query:
        raise ValueError("user_query is required")
    if not isinstance(retrieval_design, Mapping):
        raise ValueError("retrieval_strategy_design is required")
    if not isinstance(evidence_schema, Mapping):
        raise ValueError("evidence_schema is required")

    task_context = evidence_schema.get("task_context") if isinstance(evidence_schema.get("task_context"), Mapping) else {}
    assembled = retrieval_design.get("assembled_facets") if isinstance(retrieval_design.get("assembled_facets"), Mapping) else {}
    query_facets = assembled.get("query_facets") if isinstance(assembled.get("query_facets"), Mapping) else {}
    research_question = str(task_context.get("research_question") or query).strip()
    if not research_question:
        raise ValueError("research question is required for candidate scoring")

    context = {
        "research_question": research_question,
        "research_objects": _ordered_strings(query_facets.get("object"), assembled.get("research_object_en"), assembled.get("core_entities")),
        "conditions": _ordered_strings(query_facets.get("context"), assembled.get("conditions")),
        "results_or_metrics": _ordered_strings(query_facets.get("metric"), assembled.get("target_metrics_en")),
        "mechanisms": _ordered_strings(query_facets.get("mechanism"), assembled.get("mechanisms")),
        "comparison_dimensions": _ordered_strings(query_facets.get("evaluation"), assembled.get("task_context")),
        "critical_evidence_requirements": _ordered_strings(
            assembled.get("must_have_terms"),
            assembled.get("nice_to_have_terms"),
            [
                str(target.get("description") or target.get("evidence_need") or target.get("name") or "").strip()
                for target in (evidence_schema.get("evidence_targets") or [])
                if isinstance(target, Mapping)
            ],
        ),
        "negative_signals": _ordered_strings(assembled.get("negative_contexts")),
        "off_domain_signals": _ordered_strings(assembled.get("off_domain_signals")),
    }
    return {"research_context": context}
