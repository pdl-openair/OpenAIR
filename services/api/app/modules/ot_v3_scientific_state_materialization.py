"""Narrow deterministic ScientificState projection for OT v3/v4 agent results.

The general research agent has one broad artifact contract.  Only the three
frozen OpenTrajectory v3 responsibilities below are accepted here.  The
validated artifact is copied without semantic interpretation and the existing
ScientificState authority owns current-design selection and snapshot append.
"""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime
from typing import Any, Mapping

from app.modules.research_scientific_state_authority import (
    append_scientific_state_if_changed,
    existing_scientific_facts,
    prepare_scientific_state_update,
)


_OT_V3_WORKFLOW_ID = "opentrajectory_research_workflow"
_OT_SUPPORTED_WORKFLOW_VERSIONS = frozenset({3, 4})
_GENERAL_RESEARCH_AGENT_ID = "general_research_agent"
_GENERAL_RESEARCH_ARTIFACT_CONTRACT = "general_research_reasoning_artifact_v1"
_FACT_KEY_BY_SUBSTAGE = {
    "opentrajectory:current_research_conclusion": (
        "ot_v3_current_research_conclusion"
    ),
    "opentrajectory:key_unknowns_candidate_explanations": (
        "ot_v3_key_unknowns_candidate_explanations"
    ),
    "opentrajectory:next_research_recommendation": (
        "ot_v3_next_research_recommendation"
    ),
}


def _one(rows: Any, *, key: str, value: Any) -> dict[str, Any] | None:
    if not isinstance(rows, list):
        return None
    matches = [
        row
        for row in rows
        if isinstance(row, dict) and row.get(key) == value
    ]
    return matches[0] if len(matches) == 1 else None


def _validated_artifact_object(content: str) -> dict[str, Any] | None:
    if not isinstance(content, str):
        return None
    try:
        value = json.loads(content)
    except (TypeError, ValueError):
        return None
    return deepcopy(dict(value)) if isinstance(value, Mapping) else None


def materialize_ot_v3_general_agent_result(
    *,
    task_payload: Mapping[str, Any],
    result_id: str,
    validated_artifact_content: str,
    occurred_at: datetime,
) -> dict[str, Any]:
    """Project one passed, exact OT v3/v4 general-agent Result when eligible.

    Ineligible workflow, substage, contract, failed Result, and superseded
    ResearchDesign inputs are intentionally no-ops.  This keeps the hook safe
    for every production Result while opening no global general-agent path.
    """

    payload = deepcopy(dict(task_payload))
    result = _one(payload.get("results"), key="resultId", value=result_id)
    if result is None or result.get("checkStatus") != "passed":
        return payload

    work = _one(
        payload.get("workUnits"),
        key="workUnitId",
        value=result.get("workUnitId"),
    )
    if work is None:
        return payload
    substage_definition_id = work.get("substageDefinitionId")
    fact_key = _FACT_KEY_BY_SUBSTAGE.get(substage_definition_id)
    if fact_key is None:
        return payload

    executor = work.get("executorRef")
    if not (
        isinstance(executor, Mapping)
        and executor.get("executorType") == "agent"
        and executor.get("executorId") == _GENERAL_RESEARCH_AGENT_ID
    ):
        return payload

    active_ref = payload.get("activeResearchDesignRef")
    if not isinstance(active_ref, Mapping):
        return payload
    design_version = work.get("researchDesignVersion")
    if active_ref.get("version") != design_version:
        return payload
    design = _one(
        payload.get("researchDesignVersions"),
        key="version",
        value=design_version,
    )
    provenance = design.get("workflowProvenanceRef") if design is not None else None
    if not (
        isinstance(provenance, Mapping)
        and provenance.get("workflowId") == _OT_V3_WORKFLOW_ID
        and provenance.get("workflowVersion") in _OT_SUPPORTED_WORKFLOW_VERSIONS
    ):
        return payload

    artifact_refs = result.get("artifactRefs")
    if not isinstance(artifact_refs, list) or len(artifact_refs) != 1:
        return payload
    source_artifact_id = artifact_refs[0]
    artifact = _one(
        payload.get("artifacts"),
        key="artifactId",
        value=source_artifact_id,
    )
    validator = artifact.get("validatorResult") if artifact is not None else None
    if not (
        artifact is not None
        and artifact.get("workUnitId") == work.get("workUnitId")
        and artifact.get("sourceResultId") == result_id
        and isinstance(validator, Mapping)
        and validator.get("status") == "passed"
        and validator.get("artifactContract")
        == _GENERAL_RESEARCH_ARTIFACT_CONTRACT
    ):
        return payload

    validated_artifact = _validated_artifact_object(validated_artifact_content)
    if validated_artifact is None:
        return payload

    before_versions = {
        int(row["scientificStateVersion"])
        for row in payload.get("scientificStates", [])
        if isinstance(row, Mapping)
    }
    update = prepare_scientific_state_update(
        payload,
        source_research_design_version=int(design_version),
    )
    facts = existing_scientific_facts(update)
    facts[fact_key] = {
        "validatedArtifact": validated_artifact,
        "provenance": {
            "sourceArtifactId": source_artifact_id,
            "sourceResultId": result_id,
            "workUnitId": work["workUnitId"],
            "substageInstanceId": work["substageInstanceId"],
            "substageDefinitionId": substage_definition_id,
            "researchDesignVersion": design_version,
            "artifactContract": _GENERAL_RESEARCH_ARTIFACT_CONTRACT,
        },
    }
    materialized = append_scientific_state_if_changed(
        update,
        scientific_facts=facts,
        source_artifact_id=str(source_artifact_id),
        occurred_at=occurred_at,
    )
    new_versions = [
        int(row["scientificStateVersion"])
        for row in materialized.get("scientificStates", [])
        if int(row["scientificStateVersion"]) not in before_versions
    ]
    if new_versions:
        persisted_result = _one(
            materialized.get("results"), key="resultId", value=result_id
        )
        if persisted_result is not None:
            persisted_versions = list(
                persisted_result.get("scientificStateVersions") or []
            )
            persisted_result["scientificStateVersions"] = [
                *persisted_versions,
                *(version for version in new_versions if version not in persisted_versions),
            ]
    return materialized


__all__ = ["materialize_ot_v3_general_agent_result"]
