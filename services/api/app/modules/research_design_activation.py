"""Activation rules for a user-confirmed ResearchDesign.

This module deliberately owns only the deterministic transition from an approved
Design version to runtime state. It does not decide research content, modify the
Design, or choose the next research task.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any, Mapping

from app.models.research_orchestration import (
    ResearchDesignRef,
    ResearchDesignStatus,
    ResearchDesignVersion,
    ResearchTaskV12,
    ScientificStateV1,
    SubstageInstance,
    SubstageRuntimeState,
    SubstageRuntimeStatus,
)
from app.modules.research_scientific_state_authority import current_scientific_state


def _definition_enabled(
    snapshot: Mapping[str, Any] | None,
    instance: SubstageInstance,
) -> bool:
    if snapshot is None:
        return True
    definitions = snapshot.get("definitions")
    substages = definitions.get("substages") if isinstance(definitions, Mapping) else None
    if not isinstance(substages, list):
        return True
    definition = next(
        (
            row
            for row in substages
            if isinstance(row, Mapping)
            and row.get("definitionId") == instance.substage_definition_id
            and row.get("definitionVersion") == instance.substage_definition_version
        ),
        None,
    )
    if definition is None:
        return True
    return bool(definition.get("enabled"))


def _initial_runtime_state(
    instance: SubstageInstance,
    *,
    activated_at: datetime,
    snapshot: Mapping[str, Any] | None = None,
) -> SubstageRuntimeState:
    enabled = _definition_enabled(snapshot, instance)
    return SubstageRuntimeState(
        substageInstanceId=instance.substage_instance_id,
        instanceRevision=instance.instance_revision,
        status=(
            SubstageRuntimeStatus.READY
            if enabled and not instance.dependencies
            else SubstageRuntimeStatus.PLANNED
        ),
        activeExecutionDecisionId=None,
        workUnitIds=[],
        artifactRefs=[],
        statusReasonCode=None if enabled else "definition_disabled",
        updatedAt=activated_at,
    )


def runtime_states_for_confirmed_design(
    *,
    design: ResearchDesignVersion,
    existing_states: list[SubstageRuntimeState],
    activated_at: datetime,
    snapshot: Mapping[str, Any] | None = None,
) -> list[SubstageRuntimeState]:
    """Preserve historical execution facts and initialize only new revisions.

    A new confirmed ResearchDesign may add tasks or increment an instance
    revision. Existing runtime rows are historical execution facts and are kept
    unchanged; only Design instance revisions with no runtime row are initialized.
    """

    existing_keys = {
        (state.substage_instance_id, state.instance_revision)
        for state in existing_states
    }
    initialized = [*existing_states]
    for instance in design.instances:
        key = (instance.substage_instance_id, instance.instance_revision)
        if key in existing_keys:
            continue
        initialized.append(
            _initial_runtime_state(
                instance, activated_at=activated_at, snapshot=snapshot
            )
        )
    return initialized


def scientific_states_for_confirmed_design(
    *,
    task: ResearchTaskV12,
    design: ResearchDesignVersion,
    activated_at: datetime,
) -> list[ScientificStateV1]:
    """Append the current-state baseline for one newly confirmed Design.

    Accepted scientific facts and their provenance remain current across a Design
    revision. Execution progress is Design-scoped and therefore starts empty.
    """

    latest = current_scientific_state(task)
    next_version = (
        max(
            (state.scientific_state_version for state in task.scientific_states),
            default=0,
        )
        + 1
    )
    return [
        *task.scientific_states,
        ScientificStateV1(
            scientificStateVersion=next_version,
            researchDesignVersion=design.version,
            scientificFacts=(
                deepcopy(latest.scientific_facts) if latest is not None else {}
            ),
            executionState={},
            sourceRefs=(list(latest.source_refs) if latest is not None else []),
            createdAt=activated_at,
        ),
    ]




def design_versions_after_confirmation(
    *,
    versions: list[ResearchDesignVersion],
    design_id: str,
    version: int,
    decision: str,
    confirmed_at: datetime,
    confirmation_id: str,
) -> list[ResearchDesignVersion]:
    """Apply one confirmation decision without touching runtime or persistence."""

    updated: list[ResearchDesignVersion] = []
    for design in versions:
        is_target = (
            design.research_design_id == design_id and design.version == version
        )
        if is_target and decision == "approved":
            updated.append(
                design.model_copy(
                    update={
                        "status": ResearchDesignStatus.CONFIRMED,
                        "confirmed_at": confirmed_at,
                        "confirmation_ref": confirmation_id,
                    }
                )
            )
        elif is_target and decision == "rejected":
            updated.append(
                design.model_copy(update={"status": ResearchDesignStatus.CANCELLED})
            )
        elif decision == "approved" and design.status is ResearchDesignStatus.CONFIRMED:
            updated.append(
                design.model_copy(update={"status": ResearchDesignStatus.SUPERSEDED})
            )
        else:
            updated.append(design)
    return updated


def approved_design_activation_updates(
    *,
    task: ResearchTaskV12,
    design: ResearchDesignVersion,
    activated_at: datetime,
    snapshot: Mapping[str, Any] | None = None,
) -> dict[str, object]:
    """Return only lifecycle fields changed by activating an approved Design."""

    updates: dict[str, object] = {
        "activeResearchDesignRef": ResearchDesignRef(
            researchDesignId=design.research_design_id,
            version=design.version,
        ),
        "substageRuntimeStates": runtime_states_for_confirmed_design(
            design=design,
            existing_states=task.substage_runtime_states,
            activated_at=activated_at,
            snapshot=snapshot,
        ),
        "scientificStates": scientific_states_for_confirmed_design(
            task=task,
            design=design,
            activated_at=activated_at,
        ),
        "finishedAt": None,
    }
    if task.active_research_design_ref is not None:
        updates["finalResultAuthority"] = None
    return updates


__all__ = [
    "approved_design_activation_updates",
    "design_versions_after_confirmation",
    "runtime_states_for_confirmed_design",
    "scientific_states_for_confirmed_design",
]
