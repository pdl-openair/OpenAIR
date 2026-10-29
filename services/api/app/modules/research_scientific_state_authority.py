"""Single deterministic authority for selecting and appending current ScientificState.

ScientificState is the project's current accepted scientific knowledge.  Historical
snapshots may exist for traceability, but callers that read or mutate current state
must scope that operation to the current confirmed ResearchDesign (or to the
explicit Design version when operating on a frozen execution input).

This module owns no scientific interpretation.  It only resolves state identity,
versioning, provenance carry-forward, and immutable snapshot append semantics.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, TypeVar


TState = TypeVar("TState")


def _field(value: Any, snake_name: str, camel_name: str) -> Any:
    if isinstance(value, Mapping):
        if camel_name in value:
            return value.get(camel_name)
        return value.get(snake_name)
    return getattr(value, snake_name, None)


def _positive_int(value: Any, *, error_code: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(error_code)
    return value


def scientific_state_version(state: Any) -> int:
    return _positive_int(
        _field(state, "scientific_state_version", "scientificStateVersion"),
        error_code="scientific_state_version_invalid",
    )


def scientific_state_design_version(state: Any) -> int:
    return _positive_int(
        _field(state, "research_design_version", "researchDesignVersion"),
        error_code="scientific_state_design_version_invalid",
    )


def latest_scientific_state_for_design(
    states: Iterable[TState],
    *,
    research_design_version: int,
) -> TState | None:
    """Return the newest immutable snapshot for one ResearchDesign version."""

    design_version = _positive_int(
        research_design_version,
        error_code="scientific_state_design_version_invalid",
    )
    matching = [
        state
        for state in states
        if scientific_state_design_version(state) == design_version
    ]
    return max(matching, key=scientific_state_version, default=None)


def scientific_state_at_version_for_design(
    states: Iterable[TState],
    *,
    research_design_version: int,
    scientific_state_version_value: int,
) -> TState | None:
    """Return one frozen ScientificState snapshot within one Design scope."""

    design_version = _positive_int(
        research_design_version,
        error_code="scientific_state_design_version_invalid",
    )
    state_version = _positive_int(
        scientific_state_version_value,
        error_code="scientific_state_version_invalid",
    )
    matches = [
        state
        for state in states
        if scientific_state_design_version(state) == design_version
        and scientific_state_version(state) == state_version
    ]
    if len(matches) > 1:
        raise ValueError("scientific_state_snapshot_identity_ambiguous")
    return matches[0] if matches else None


def active_research_design_version(task: Any) -> int | None:
    """Return the current confirmed Design version referenced by the task."""

    active_ref = _field(task, "active_research_design_ref", "activeResearchDesignRef")
    if active_ref is None:
        return None
    return _positive_int(
        _field(active_ref, "version", "version"),
        error_code="active_research_design_version_invalid",
    )


def current_scientific_state(task: Any) -> Any | None:
    """Return current ScientificState according to active ResearchDesign authority."""

    design_version = active_research_design_version(task)
    if design_version is None:
        return None
    states = _field(task, "scientific_states", "scientificStates") or []
    if not isinstance(states, list):
        raise ValueError("scientific_state_collection_invalid")
    return latest_scientific_state_for_design(
        states,
        research_design_version=design_version,
    )


@dataclass
class ScientificStateUpdateContext:
    """Prepared mutable copy for one deterministic ScientificState append."""

    payload: dict[str, Any]
    states: list[Any]
    current_state: Mapping[str, Any] | None
    research_design_version: int
    next_scientific_state_version: int


def prepare_scientific_state_update(
    task_payload: Mapping[str, Any],
    *,
    source_research_design_version: int,
) -> ScientificStateUpdateContext:
    """Resolve the only current state that a Result projection may update.

    A Result produced under a superseded Design remains a durable historical Result,
    but it cannot silently make that superseded Design current again by appending a
    newer ScientificState.  Any such knowledge must be accepted through the current
    Design authority before it can change current project state.
    """

    source_version = _positive_int(
        source_research_design_version,
        error_code="scientific_state_projection_design_version_invalid",
    )
    payload = deepcopy(dict(task_payload))
    states = payload.setdefault("scientificStates", [])
    if not isinstance(states, list):
        raise ValueError("scientific_state_collection_invalid")
    if any(not isinstance(row, Mapping) for row in states):
        raise ValueError("scientific_state_collection_invalid")

    active_version = active_research_design_version(payload)
    if active_version is not None and source_version != active_version:
        raise ValueError("scientific_state_projection_design_not_current")

    target_version = active_version or source_version
    current = latest_scientific_state_for_design(
        states,
        research_design_version=target_version,
    )

    if active_version is not None and states and current is None:
        raise ValueError("current_scientific_state_missing")

    if active_version is None and states:
        latest_overall = max(states, key=scientific_state_version)
        if scientific_state_design_version(latest_overall) != target_version:
            raise ValueError("scientific_state_projection_design_not_current")

    next_version = max(
        (scientific_state_version(row) for row in states),
        default=0,
    ) + 1
    return ScientificStateUpdateContext(
        payload=payload,
        states=states,
        current_state=current,
        research_design_version=target_version,
        next_scientific_state_version=next_version,
    )


def existing_scientific_facts(
    context: ScientificStateUpdateContext,
) -> dict[str, Any]:
    if context.current_state is None:
        return {}
    facts = context.current_state.get("scientificFacts")
    if not isinstance(facts, Mapping):
        raise ValueError("scientific_state_existing_facts_invalid")
    return deepcopy(dict(facts))


def append_scientific_state_if_changed(
    context: ScientificStateUpdateContext,
    *,
    scientific_facts: Mapping[str, Any],
    source_artifact_id: str,
    occurred_at: datetime,
) -> dict[str, Any]:
    """Append one immutable current-state snapshot only when knowledge changed."""

    if not isinstance(scientific_facts, Mapping):
        raise ValueError("scientific_state_facts_invalid")
    if not isinstance(source_artifact_id, str) or not source_artifact_id.strip():
        raise ValueError("scientific_state_source_identity_missing")
    if occurred_at.tzinfo is None:
        raise ValueError("scientific_state_timestamp_invalid")

    facts = deepcopy(dict(scientific_facts))
    current = context.current_state
    if current is not None and current.get("scientificFacts") == facts:
        return context.payload

    current_refs = list(current.get("sourceRefs") or []) if current is not None else []
    source_refs: list[str] = []
    for ref in [*current_refs, source_artifact_id]:
        if not isinstance(ref, str) or not ref.strip():
            raise ValueError("scientific_state_source_ref_invalid")
        if ref not in source_refs:
            source_refs.append(ref)

    execution_state = (
        deepcopy(dict(current.get("executionState") or {}))
        if current is not None
        else {}
    )
    context.states.append(
        {
            "scientificStateVersion": context.next_scientific_state_version,
            "researchDesignVersion": context.research_design_version,
            "scientificFacts": facts,
            "executionState": execution_state,
            "sourceRefs": source_refs,
            "createdAt": occurred_at.astimezone(timezone.utc).isoformat(),
        }
    )

    # New accepted knowledge invalidates only the current completion authority.
    # Historical decisions/results remain durable and traceable.
    context.payload["finalResultAuthority"] = None
    if context.payload.get("status") == "completed":
        context.payload["status"] = "running"
        context.payload["finishedAt"] = None
    return context.payload


__all__ = [
    "ScientificStateUpdateContext",
    "active_research_design_version",
    "append_scientific_state_if_changed",
    "current_scientific_state",
    "existing_scientific_facts",
    "latest_scientific_state_for_design",
    "prepare_scientific_state_update",
    "scientific_state_at_version_for_design",
    "scientific_state_design_version",
    "scientific_state_version",
]
