"""Registry-frozen, zero-reshape projection into ScientificState facts.

The shared runtime only copies available declared top-level fields from an accepted
structured Skill result. It never interprets field meaning
or chooses a projection from capability identity.

When a declaration explicitly opts into a deterministic materializer, the
shared runtime dispatches the already projected facts to that materializer.
The domain materializer owns only state-shape construction, not scientific
reasoning.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
import logging
import re
from typing import Any, Mapping


PROJECTION_CONTRACT_VERSION = 1
MATERIALIZED_PROJECTION_CONTRACT_VERSION = 2
SUPPORTED_PROJECTION_CONTRACT_VERSIONS = {
    PROJECTION_CONTRACT_VERSION,
    MATERIALIZED_PROJECTION_CONTRACT_VERSION,
}
_FACT_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,119}$")
_MATERIALIZER_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$")
_SUPPORTED_MATERIALIZER_REFS = {
    "cs:reproduction-input-source:v1",
    "pvd:experimental-system-context:v1",
    "pvd:research-request-understanding:v1",
    "pvd:round-experiment-recommendation:v1",
    "research:declared-scientific-facts:v1",
}


def validate_scientific_state_projection_declaration(
    value: Any,
    *,
    output_schema: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate one frozen declaration against the frozen output contract."""

    if not isinstance(value, Mapping):
        raise ValueError("scientific_state_projection_contract_invalid")
    version = value.get("contract_version")
    if version not in SUPPORTED_PROJECTION_CONTRACT_VERSIONS:
        raise ValueError("scientific_state_projection_version_invalid")
    expected_keys = (
        {"contract_version", "facts"}
        if version == PROJECTION_CONTRACT_VERSION
        else {"contract_version", "facts", "materializer_ref"}
    )
    if set(value) != expected_keys:
        raise ValueError("scientific_state_projection_contract_invalid")

    materializer_ref = None
    if version == MATERIALIZED_PROJECTION_CONTRACT_VERSION:
        materializer_ref = str(value.get("materializer_ref") or "").strip()
        if (
            _MATERIALIZER_REF.fullmatch(materializer_ref) is None
            or materializer_ref not in _SUPPORTED_MATERIALIZER_REFS
        ):
            raise ValueError("scientific_state_projection_materializer_invalid")

    facts = value.get("facts")
    properties = output_schema.get("properties")
    required = output_schema.get("required")
    if (
        not isinstance(facts, list)
        or not facts
        or not isinstance(properties, Mapping)
        or not isinstance(required, list)
    ):
        raise ValueError("scientific_state_projection_contract_invalid")

    normalized: list[dict[str, str]] = []
    seen_fact_keys: set[str] = set()
    seen_source_fields: set[str] = set()
    for row in facts:
        if not isinstance(row, Mapping) or set(row) != {"fact_key", "source_field"}:
            raise ValueError("scientific_state_projection_fact_invalid")
        fact_key = str(row.get("fact_key") or "").strip()
        source_field = str(row.get("source_field") or "").strip()
        if (
            _FACT_KEY.fullmatch(fact_key) is None
            or not source_field
            or source_field not in properties
            or source_field not in required
            or fact_key in seen_fact_keys
            or source_field in seen_source_fields
        ):
            raise ValueError("scientific_state_projection_fact_invalid")
        seen_fact_keys.add(fact_key)
        seen_source_fields.add(source_field)
        normalized.append({"fact_key": fact_key, "source_field": source_field})

    normalized_declaration: dict[str, Any] = {
        "contract_version": version,
        "facts": normalized,
    }
    if materializer_ref is not None:
        normalized_declaration["materializer_ref"] = materializer_ref
    return normalized_declaration


def project_validated_structured_result(
    content: str,
    declaration: Mapping[str, Any] | None,
    *,
    result_protocol: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Copy declared values only when the whole structure is not quarantined."""

    from app.models.result_protocol import result_structure_quarantined

    if result_structure_quarantined(result_protocol):
        return {}
    if declaration is None:
        return {}
    try:
        document = json.loads(content)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("scientific_state_projection_result_invalid") from exc
    if not isinstance(document, Mapping):
        raise ValueError("scientific_state_projection_result_invalid")
    facts = declaration.get("facts")
    if (
        declaration.get("contract_version")
        not in SUPPORTED_PROJECTION_CONTRACT_VERSIONS
        or not isinstance(facts, list)
    ):
        raise ValueError("scientific_state_projection_contract_invalid")
    projected: dict[str, Any] = {}
    for row in facts:
        if not isinstance(row, Mapping):
            raise ValueError("scientific_state_projection_contract_invalid")
        fact_key = str(row.get("fact_key") or "")
        source_field = str(row.get("source_field") or "")
        if not fact_key or not source_field:
            raise ValueError("scientific_state_projection_contract_invalid")
        if source_field not in document:
            logging.getLogger(__name__).warning(
                "scientific_state_projection_source_missing fact=%s field=%s",
                fact_key, source_field,
            )
            continue
        projected[fact_key] = deepcopy(document[source_field])
    return projected


def materialize_scientific_state_projection(
    *,
    task_payload: Mapping[str, Any],
    declaration: Mapping[str, Any] | None,
    projected_facts: Mapping[str, Any],
    source_artifact_id: str,
    research_design_version: int,
    occurred_at: datetime,
) -> dict[str, Any]:
    """Apply an explicitly frozen deterministic state materializer, if declared."""

    if not projected_facts:
        return deepcopy(dict(task_payload))
    if declaration is None:
        raise ValueError("scientific_state_projection_declaration_missing")
    version = declaration.get("contract_version")
    if version == PROJECTION_CONTRACT_VERSION:
        # v1 projections remain durable Artifact provenance only.  They do not
        # silently mutate ScientificState.
        return deepcopy(dict(task_payload))
    if version != MATERIALIZED_PROJECTION_CONTRACT_VERSION:
        raise ValueError("scientific_state_projection_version_invalid")

    materializer_ref = str(declaration.get("materializer_ref") or "").strip()
    if materializer_ref == "cs:reproduction-input-source:v1":
        from app.modules.cs_reproduction_scientific_state_materialization import (
            materialize_cs_reproduction_inputs,
        )

        return materialize_cs_reproduction_inputs(
            task_payload=task_payload,
            projected_facts=projected_facts,
            source_artifact_id=source_artifact_id,
            research_design_version=research_design_version,
            occurred_at=occurred_at,
        )
    if materializer_ref == "pvd:experimental-system-context:v1":
        from app.modules.pvd_scientific_state_materialization import (
            materialize_experimental_system_context,
        )

        return materialize_experimental_system_context(
            task_payload=task_payload,
            projected_facts=projected_facts,
            source_artifact_id=source_artifact_id,
            research_design_version=research_design_version,
            occurred_at=occurred_at,
        )
    if materializer_ref == "pvd:research-request-understanding:v1":
        from app.modules.pvd_scientific_state_materialization import (
            materialize_research_request_understanding,
        )

        return materialize_research_request_understanding(
            task_payload=task_payload,
            projected_facts=projected_facts,
            source_artifact_id=source_artifact_id,
            research_design_version=research_design_version,
            occurred_at=occurred_at,
        )
    if materializer_ref == "pvd:round-experiment-recommendation:v1":
        from app.modules.pvd_scientific_state_materialization import (
            materialize_round_experiment_recommendation,
        )

        return materialize_round_experiment_recommendation(
            task_payload=task_payload,
            projected_facts=projected_facts,
            source_artifact_id=source_artifact_id,
            research_design_version=research_design_version,
            occurred_at=occurred_at,
        )
    if materializer_ref == "research:declared-scientific-facts:v1":
        from app.modules.research_scientific_state_materialization import (
            materialize_declared_scientific_facts,
        )

        return materialize_declared_scientific_facts(
            task_payload=task_payload,
            projected_facts=projected_facts,
            source_artifact_id=source_artifact_id,
            research_design_version=research_design_version,
            occurred_at=occurred_at,
        )
    raise ValueError("scientific_state_projection_materializer_invalid")


def authoritative_projected_facts(
    *,
    artifacts: list[Any],
    work_by_id: Mapping[str, Any],
    call_by_id: Mapping[str, Any],
    decision_by_id: Mapping[str, Any],
) -> dict[str, Any]:
    """Admit persisted projections only when their complete lineage matches."""

    admitted: dict[str, Any] = {}
    for artifact in artifacts:
        validator = artifact.validator_result
        rows = validator.get("scientificStateFactProjections") if isinstance(validator, Mapping) else None
        if rows is None:
            continue
        if not isinstance(rows, list):
            raise ValueError("scientific_state_projection_provenance_invalid")
        work = work_by_id.get(artifact.work_unit_id)
        call = call_by_id.get(artifact.source_capability_call_id)
        decision = None if work is None else decision_by_id.get(work.execution_decision_id)
        if work is None or call is None or decision is None:
            raise ValueError("scientific_state_projection_provenance_invalid")
        expected_capability_ref = {
            "capabilityType": call.capability_type.value,
            "capabilityId": call.capability_id,
        }
        for row in rows:
            if not isinstance(row, Mapping) or set(row) != {
                "factKey",
                "value",
                "sourceArtifactId",
                "sourceContentSha256",
                "producerCapabilityRef",
                "registrySnapshotContentHash",
            }:
                raise ValueError("scientific_state_projection_provenance_invalid")
            fact_key = str(row.get("factKey") or "")
            if (
                _FACT_KEY.fullmatch(fact_key) is None
                or row.get("sourceArtifactId") != artifact.artifact_id
                or row.get("sourceContentSha256") != validator.get("contentSha256")
                or row.get("producerCapabilityRef") != expected_capability_ref
                or row.get("registrySnapshotContentHash")
                != decision.registry_snapshot_ref.content_hash
            ):
                raise ValueError("scientific_state_projection_provenance_invalid")
            value = deepcopy(row.get("value"))
            if fact_key in admitted and admitted[fact_key] != value:
                raise ValueError("scientific_state_projection_conflict")
            admitted[fact_key] = value
    return admitted


__all__ = [
    "MATERIALIZED_PROJECTION_CONTRACT_VERSION",
    "PROJECTION_CONTRACT_VERSION",
    "SUPPORTED_PROJECTION_CONTRACT_VERSIONS",
    "authoritative_projected_facts",
    "materialize_scientific_state_projection",
    "project_validated_structured_result",
    "validate_scientific_state_projection_declaration",
]
