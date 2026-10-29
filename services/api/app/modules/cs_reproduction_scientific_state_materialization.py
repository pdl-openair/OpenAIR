"""Deterministic materialization for CS reproduction execution-input facts.

The LLM may interpret the confirmed ResearchDesign goal.  This materializer
only enforces deterministic source/provenance consistency before those values
become canonical ScientificState facts.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime
from typing import Any

from app.modules.research_scientific_state_authority import (
    append_scientific_state_if_changed,
    existing_scientific_facts,
    prepare_scientific_state_update,
)

CS_REPRODUCTION_INPUT_SOURCE_MATERIALIZER = "cs:reproduction-input-source:v1"


def _frozen_research_goal(
    task_payload: Mapping[str, Any],
    *,
    source_artifact_id: str,
    research_design_version: int,
) -> str:
    artifacts = task_payload.get("artifacts")
    work_units = task_payload.get("workUnits")
    if not isinstance(artifacts, list) or not isinstance(work_units, list):
        raise ValueError("cs_reproduction_source_lineage_missing")

    matches = [
        row
        for row in artifacts
        if isinstance(row, Mapping) and row.get("artifactId") == source_artifact_id
    ]
    if len(matches) != 1:
        raise ValueError("cs_reproduction_source_artifact_invalid")
    work_unit_id = matches[0].get("workUnitId")
    work_matches = [
        row
        for row in work_units
        if isinstance(row, Mapping) and row.get("workUnitId") == work_unit_id
    ]
    if len(work_matches) != 1:
        raise ValueError("cs_reproduction_source_work_unit_invalid")
    work = work_matches[0]
    if work.get("researchDesignVersion") != research_design_version:
        raise ValueError("cs_reproduction_source_design_mismatch")
    invocation = work.get("invocationContext")
    goal = invocation.get("canonical_user_query") if isinstance(invocation, Mapping) else None
    if not isinstance(goal, str) or not goal.strip():
        raise ValueError("cs_reproduction_confirmed_goal_missing")
    return goal


def _validate_source_consistency(
    projected_facts: Mapping[str, Any],
    *,
    confirmed_goal: str,
) -> dict[str, Any]:
    values = {
        "repository": projected_facts.get("reproduction_repository"),
        "revision": projected_facts.get("reproduction_revision"),
    }
    expected_unresolved: list[str] = []
    for field, value in values.items():
        if value is None or not isinstance(value, str) or not value.strip():
            expected_unresolved.append(field)
            values[field] = None
            continue
        # A valid execution identity still must be traceable to the frozen goal.
        if value not in confirmed_goal:
            raise ValueError(f"cs_reproduction_{field}_source_mismatch")
    unresolved = projected_facts.get("reproduction_unresolved_inputs")
    if (not isinstance(unresolved, list)
            or any(not isinstance(field, str) for field in unresolved)
            or set(unresolved) != set(expected_unresolved)
            or len(unresolved) != len(expected_unresolved)):
        logging.getLogger(__name__).warning(
            "cs_reproduction_projection_gap: unresolved_metadata_mismatch"
        )
    # Missingness of code-owned execution fields is deterministic; no scientific
    # conclusion or execution identity is supplied by this reconciliation.
    unresolved = expected_unresolved
    accepted: dict[str, Any] = {
        "reproduction_unresolved_inputs": list(unresolved),
    }
    if values["repository"] is not None:
        accepted["reproduction_repository"] = deepcopy(values["repository"])
    if values["revision"] is not None:
        accepted["reproduction_revision"] = deepcopy(values["revision"])
    return accepted


def materialize_cs_reproduction_inputs(
    *,
    task_payload: Mapping[str, Any],
    projected_facts: Mapping[str, Any],
    source_artifact_id: str,
    research_design_version: int,
    occurred_at: datetime,
) -> dict[str, Any]:
    """Append accepted CS reproduction inputs without creating another authority."""

    confirmed_goal = _frozen_research_goal(
        task_payload,
        source_artifact_id=source_artifact_id,
        research_design_version=research_design_version,
    )
    accepted = _validate_source_consistency(
        projected_facts,
        confirmed_goal=confirmed_goal,
    )
    update = prepare_scientific_state_update(
        task_payload,
        source_research_design_version=research_design_version,
    )
    facts = existing_scientific_facts(update)
    facts.update({key: value for key, value in accepted.items()
                  if key != "reproduction_unresolved_inputs"})
    facts["reproduction_unresolved_inputs"] = [
        field for field in ("repository", "revision")
        if not facts.get(f"reproduction_{field}")
    ]
    return append_scientific_state_if_changed(
        update,
        scientific_facts=facts,
        source_artifact_id=source_artifact_id,
        occurred_at=occurred_at,
    )


__all__ = [
    "CS_REPRODUCTION_INPUT_SOURCE_MATERIALIZER",
    "materialize_cs_reproduction_inputs",
]
