"""Deterministic materialization of Registry-declared accepted research facts.

The caller has already validated the structured Skill result and projected only
Registry-declared fields.  This module does not infer scientific meaning or
parse prose; it copies those accepted values into the current ScientificState.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any, Mapping

from app.modules.research_scientific_state_authority import (
    append_scientific_state_if_changed,
    existing_scientific_facts,
    prepare_scientific_state_update,
)


DECLARED_SCIENTIFIC_FACTS_MATERIALIZER = "research:declared-scientific-facts:v1"


def materialize_declared_scientific_facts(
    *,
    task_payload: Mapping[str, Any],
    projected_facts: Mapping[str, Any],
    source_artifact_id: str,
    research_design_version: int,
    occurred_at: datetime,
) -> dict[str, Any]:
    """Append one immutable current ScientificState when declared facts changed."""

    if not isinstance(projected_facts, Mapping) or not projected_facts:
        raise ValueError("declared_scientific_state_projection_invalid")
    if any(not isinstance(key, str) or not key.strip() for key in projected_facts):
        raise ValueError("declared_scientific_state_fact_key_invalid")

    from app.modules.target_output_completion import (
        merge_reuse_acceptances,
        validate_reuse_acceptance_projection,
    )

    validate_reuse_acceptance_projection(
        projected_facts, task_payload, source_artifact_id, research_design_version
    )
    context = prepare_scientific_state_update(
        task_payload,
        source_research_design_version=research_design_version,
    )
    scientific_facts = existing_scientific_facts(context)
    for fact_key, value in projected_facts.items():
        scientific_facts[fact_key] = (
            merge_reuse_acceptances(scientific_facts.get(fact_key), value)
            if fact_key == "targetOutputReuseAcceptances"
            else deepcopy(value)
        )

    return append_scientific_state_if_changed(
        context,
        scientific_facts=scientific_facts,
        source_artifact_id=source_artifact_id,
        occurred_at=occurred_at,
    )


__all__ = [
    "DECLARED_SCIENTIFIC_FACTS_MATERIALIZER",
    "materialize_declared_scientific_facts",
]
