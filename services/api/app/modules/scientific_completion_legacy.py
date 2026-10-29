"""Read-only compatibility for historical scientific-completion contracts.

This module is intentionally isolated from the current Final Result contract.
It preserves the exact v1/v2 projection and validation metadata needed to read
already-persisted historical results. New finalization must not call these
projections and must not add new domain semantics here.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from app.modules.research_completion_decision import (
    completion_row_coverage,
    scientific_state_artifact_refs,
    scientific_state_completion_rows,
)


V1_INTEGRITY_CONTRACT_VERSION = "scientific_completion_integrity_v1"
V2_INTEGRITY_CONTRACT_VERSION = "scientific_completion_integrity_v2"

V1_VALIDATOR_REF = "core:scientific_completion_integrity:v1"
V2_VALIDATOR_REF = "core:scientific_completion_integrity:v2"

V1_VALIDATION_CHECK_IDS = (
    "current_completion_decision",
    "disposition_coverage",
    "evidence_referential_integrity",
    "fact_only_claim_boundary",
    "gap_preservation",
)
V2_VALIDATION_CHECK_IDS = (
    *V1_VALIDATION_CHECK_IDS,
    "accepted_scientific_fact_projection",
)

LEGACY_VALIDATION_CONTRACTS = {
    V1_INTEGRITY_CONTRACT_VERSION: (V1_VALIDATOR_REF, V1_VALIDATION_CHECK_IDS),
    V2_INTEGRITY_CONTRACT_VERSION: (V2_VALIDATOR_REF, V2_VALIDATION_CHECK_IDS),
}

_NO_INFERENCE_UNCERTAINTY = (
    "No new comparison, explanation, recommendation, or priority was inferred during finalization."
)
_FACT_ONLY_LIMITATION = "Finalization only organizes already validated durable facts."
_NO_EVIDENCE_GAP = "No usable validated artifact was available."


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _list(value: object) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _unique_strings(values: object) -> list[str]:
    result: list[str] = []
    for value in _list(values):
        if isinstance(value, str) and value and value not in result:
            result.append(value)
    return result


def _accepted_text(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value


def _accepted_record_texts(values: object, *, field: str) -> list[str]:
    result: list[str] = []
    for value in _list(values):
        text = _accepted_text(value)
        if text is None and isinstance(value, Mapping):
            text = _accepted_text(value.get(field))
        if text is not None and text not in result:
            result.append(text)
    return result


def expected_v1_final_result_fields(
    state: Mapping[str, Any], *, disposition: str
) -> dict[str, Any]:
    """Reconstruct the historical v1 deterministic template exactly."""

    artifact_refs = scientific_state_artifact_refs(state)
    rows = scientific_state_completion_rows(state)
    gaps: list[str] = []
    uncertainty: list[str] = []
    for row in rows:
        _, _, unresolved_ids, unmet_ids = completion_row_coverage(row)
        for criterion_id in unmet_ids:
            value = f"criterion:{criterion_id}"
            if value not in gaps:
                gaps.append(value)
        for criterion_id in unresolved_ids:
            value = f"criterion:{criterion_id}"
            if value not in uncertainty:
                uncertainty.append(value)
        for value in _unique_strings(row.get("gaps")):
            if value not in gaps:
                gaps.append(value)
        for value in _unique_strings(row.get("uncertainty")):
            if value not in uncertainty:
                uncertainty.append(value)
    if not artifact_refs and _NO_EVIDENCE_GAP not in gaps:
        gaps.append(_NO_EVIDENCE_GAP)
    if disposition == "partial" and not gaps:
        gaps.append("Required completion criteria or execution scope remain incomplete.")
    if disposition == "degraded" and not gaps:
        gaps.append("Execution limitations degraded the confirmed scope.")
    if _NO_INFERENCE_UNCERTAINTY not in uncertainty:
        uncertainty.append(_NO_INFERENCE_UNCERTAINTY)

    direct_answer = {
        "full": "Validated research outputs are available for the confirmed scope.",
        "partial": (
            "Validated research outputs are available, but required completion criteria "
            "or execution gaps remain."
        ),
        "degraded": (
            "Validated research outputs are available, but execution limitations "
            "degraded the confirmed scope."
        ),
        "evidence_gap": "No validated research output is available for the confirmed scope.",
    }[disposition]
    key_findings = {
        "full": [
            "The persisted ScientificState records all required completion criteria as satisfied."
        ],
        "partial": [
            "The persisted ScientificState records validated outputs, but required criteria "
            "or execution gaps remain."
        ],
        "degraded": [
            "The persisted ScientificState records validated outputs with execution degradation."
        ],
        "evidence_gap": [
            "The persisted ScientificState records no validated output for the confirmed scope."
        ],
    }[disposition]
    return {
        "directAnswer": direct_answer,
        "keyFindings": key_findings,
        "evidenceRefs": artifact_refs,
        "uncertainty": uncertainty,
        "gaps": gaps,
        "limitations": [_FACT_ONLY_LIMITATION],
    }


def expected_v2_final_result_fields(
    state: Mapping[str, Any], *, disposition: str
) -> dict[str, Any]:
    """Reconstruct the historical OpenTrajectory-shaped v2 projection exactly."""

    fields = expected_v1_final_result_fields(state, disposition=disposition)
    scientific_facts = _mapping(state.get("scientificFacts"))
    accepted_direct_answer = _accepted_text(scientific_facts.get("direct_answer"))
    accepted_findings = _accepted_record_texts(
        scientific_facts.get("evidence_supported_conclusions"), field="statement"
    )
    accepted_gaps = _accepted_record_texts(
        scientific_facts.get("evidence_gaps"), field="gap"
    )
    accepted_uncertainty = _unique_strings(scientific_facts.get("uncertainties"))
    accepted_conflicts = _unique_strings(scientific_facts.get("evidence_conflicts"))

    if accepted_direct_answer is not None:
        fields["directAnswer"] = accepted_direct_answer
    if accepted_findings:
        fields["keyFindings"] = accepted_findings
    fields["gaps"] = list(dict.fromkeys([*accepted_gaps, *fields["gaps"]]))
    fields["uncertainty"] = list(
        dict.fromkeys([*accepted_uncertainty, *accepted_conflicts, *fields["uncertainty"]])
    )
    fields["acceptedScientificFacts"] = deepcopy(dict(scientific_facts))
    return fields


def legacy_validation_contract(
    version: object,
) -> tuple[str, tuple[str, ...]] | None:
    return LEGACY_VALIDATION_CONTRACTS.get(version)


def legacy_expected_result_projection(
    *, state: Mapping[str, Any], disposition: str, version: object
) -> tuple[dict[str, Any], tuple[str, ...]] | None:
    if version == V1_INTEGRITY_CONTRACT_VERSION:
        return expected_v1_final_result_fields(state, disposition=disposition), (
            "directAnswer",
            "keyFindings",
            "evidenceRefs",
            "uncertainty",
            "gaps",
            "limitations",
        )
    if version == V2_INTEGRITY_CONTRACT_VERSION:
        return expected_v2_final_result_fields(state, disposition=disposition), (
            "directAnswer",
            "keyFindings",
            "evidenceRefs",
            "uncertainty",
            "gaps",
            "limitations",
            "acceptedScientificFacts",
        )
    return None


def legacy_result_reference_errors(
    *, result: Mapping[str, Any], artifact_refs: list[str], version: object
) -> list[str]:
    if version not in LEGACY_VALIDATION_CONTRACTS:
        return []
    return (
        []
        if _unique_strings(result.get("evidenceRefs")) == artifact_refs
        else ["final_result_evidence_mismatch"]
    )


def legacy_lineage_error_codes(version: object) -> tuple[str, str]:
    if version in LEGACY_VALIDATION_CONTRACTS:
        return "evidence_lineage_invalid", "evidence_source_call_invalid"
    return "completion_artifact_lineage_invalid", "completion_artifact_source_call_invalid"


__all__ = [
    "V1_INTEGRITY_CONTRACT_VERSION",
    "V2_INTEGRITY_CONTRACT_VERSION",
    "V1_VALIDATION_CHECK_IDS",
    "V2_VALIDATION_CHECK_IDS",
    "expected_v1_final_result_fields",
    "expected_v2_final_result_fields",
    "legacy_expected_result_projection",
    "legacy_lineage_error_codes",
    "legacy_result_reference_errors",
    "legacy_validation_contract",
]
