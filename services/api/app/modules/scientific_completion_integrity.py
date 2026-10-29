"""Deterministic integrity validation for truthful scientific completion.

The validator is intentionally limited to persisted orchestration facts. It
never judges scientific quality, calls a provider, or manufactures claims.
For new project-completion decisions it verifies that the Final Result is
reachable only from the single ResearchDesign + ScientificState completion
authority. Historical v1/v2 results remain readable under their original
contracts while new finalization uses domain-neutral v3.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from app.modules.research_completion_decision import (
    COMPLETION_AUTHORITY_VERSION,
    ROUND_COMPLETION_AUTHORITY_VERSION,
    ProjectCompletionAuthorityError,
    expected_completion_disposition,
    expected_project_completion_evaluation,
    scientific_state_artifact_refs,
    scientific_state_completion_rows,
)
from app.modules.research_completion_decision import (
    completion_row_coverage as _completion_row_coverage,
)
from app.modules.research_round_end import derive_round_end
from app.modules.scientific_completion_legacy import (
    V1_INTEGRITY_CONTRACT_VERSION,
    V2_INTEGRITY_CONTRACT_VERSION,
    legacy_expected_result_projection,
    legacy_lineage_error_codes,
    legacy_result_reference_errors,
    legacy_validation_contract,
)

LEGACY_INTEGRITY_CONTRACT_VERSION = "legacy_v0"
INTEGRITY_CONTRACT_VERSION = "scientific_completion_integrity_v3"

VALIDATOR_REF = "core:scientific_completion_integrity:v3"
VALIDATION_CHECK_IDS = (
    "current_completion_decision",
    "disposition_coverage",
    "completion_artifact_referential_integrity",
    "scientific_state_exact_projection",
    "scientific_source_exact_projection",
    "completion_diagnostic_preservation",
    "legacy_semantic_projection_absent",
)
SUPPORTED_INTEGRITY_CONTRACT_VERSIONS = {
    V1_INTEGRITY_CONTRACT_VERSION,
    V2_INTEGRITY_CONTRACT_VERSION,
    INTEGRITY_CONTRACT_VERSION,
}



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


def expected_final_result_fields(
    state: Mapping[str, Any], *, decision: Mapping[str, Any]
) -> dict[str, Any]:
    """Build the domain-neutral v3 FinalResult without scientific reinterpretation.

    ScientificState remains the only Authority for accepted scientific meaning.
    The FinalResult copies its facts and provenance exactly, and separately
    projects CompletionDecision diagnostics. Artifact ids are never relabeled
    as Evidence ids. Domain-specific answer/findings/next-step structures remain
    inside ``scientificResult`` exactly as accepted upstream.
    """

    scientific_facts = _mapping(state.get("scientificFacts"))
    return {
        "directAnswer": None,
        "keyFindings": [],
        "evidenceRefs": [],
        "scientificResult": deepcopy(dict(scientific_facts)),
        "scientificSourceRefs": deepcopy(_list(state.get("sourceRefs"))),
        "uncertainty": deepcopy(_list(decision.get("uncertainty"))),
        "gaps": deepcopy(_list(decision.get("gaps"))),
        "limitations": deepcopy(_list(decision.get("limitations"))),
        "acceptedScientificFacts": {},
    }


def validation_outcome_payload(*, validated_at: str) -> dict[str, Any]:
    return {
        "contractVersion": INTEGRITY_CONTRACT_VERSION,
        "validatorRef": VALIDATOR_REF,
        "status": "passed",
        "checkIds": list(VALIDATION_CHECK_IDS),
        "validatedAt": validated_at,
    }


def _validation_contract(version: object) -> tuple[str, tuple[str, ...]] | None:
    if version == INTEGRITY_CONTRACT_VERSION:
        return VALIDATOR_REF, VALIDATION_CHECK_IDS
    return legacy_validation_contract(version)


def _active_design(
    task: Mapping[str, Any], *, design_version: object
) -> Mapping[str, Any] | None:
    active_ref = _mapping(task.get("activeResearchDesignRef"))
    design_id = active_ref.get("researchDesignId")
    if active_ref.get("version") != design_version:
        return None
    return next(
        (
            design
            for value in _list(task.get("researchDesignVersions"))
            if (design := _mapping(value))
            and design.get("researchDesignId") == design_id
            and design.get("version") == design_version
        ),
        None,
    )


def _versioned_completion_decision(
    decisions: list[Mapping[str, Any]],
    *,
    design: Mapping[str, Any] | None,
) -> tuple[Mapping[str, Any] | None, bool]:
    if design is None:
        return None, False
    expected_ref = {
        "researchDesignId": design.get("researchDesignId"),
        "version": design.get("version"),
    }
    matches = [
        row
        for row in decisions
        if row.get("completionAuthorityVersion")
        in {COMPLETION_AUTHORITY_VERSION, ROUND_COMPLETION_AUTHORITY_VERSION}
        and _mapping(row.get("researchDesignRef")) == expected_ref
    ]
    if len(matches) == 1:
        return matches[0], False
    return None, len(matches) > 1


def _validate_project_completion_decision(
    *,
    task: Mapping[str, Any],
    design: Mapping[str, Any],
    state: Mapping[str, Any],
    decision: Mapping[str, Any],
) -> list[str]:
    errors: list[str] = []
    try:
        expected = expected_project_completion_evaluation(design, state)
    except ProjectCompletionAuthorityError:
        return ["project_completion_authority_unresolved"]

    expected_ref = {
        "researchDesignId": design.get("researchDesignId"),
        "version": design.get("version"),
    }
    if _mapping(decision.get("researchDesignRef")) != expected_ref:
        errors.append("completion_design_binding_mismatch")
    if decision.get("completionAuthorityVersion") == ROUND_COMPLETION_AUTHORITY_VERSION:
        round_end = derive_round_end(task, design, state)
        if (
            decision.get("canComplete") is not False
            or round_end is None
            or decision.get("roundEnd") != round_end
        ):
            errors.append("completion_not_authorized")
    elif decision.get("canComplete") is not True:
        errors.append("completion_not_authorized")
    for field in (
        "canComplete",
        "requirementAssessments",
        "disposition",
        "gaps",
        "uncertainty",
        "limitations",
        "rationale",
        "sourceRefs",
    ):
        if decision.get(field) != expected[field]:
            errors.append("project_completion_decision_mismatch")
            break
    return errors



def _artifact_lineage_errors(
    task: Mapping[str, Any],
    *,
    artifact_refs: list[str],
    design_version: object,
    integrity_version: object,
) -> list[str]:
    """Validate completion Artifact lineage without relabeling it as Evidence."""

    artifacts = {
        row.get("artifactId"): row
        for value in _list(task.get("artifacts"))
        if (row := _mapping(value)) and isinstance(row.get("artifactId"), str)
    }
    works = {
        row.get("workUnitId"): row
        for value in _list(task.get("workUnits"))
        if (row := _mapping(value)) and isinstance(row.get("workUnitId"), str)
    }
    calls = {
        row.get("capabilityCallId"): row
        for value in _list(task.get("capabilityCalls"))
        if (row := _mapping(value)) and isinstance(row.get("capabilityCallId"), str)
    }
    lineage_error, source_call_error = legacy_lineage_error_codes(integrity_version)

    errors: list[str] = []
    for ref in artifact_refs:
        artifact = artifacts.get(ref)
        work = works.get(artifact.get("workUnitId")) if artifact is not None else None
        if (
            artifact is None
            or artifact.get("artifactState") != "usable"
            or work is None
            or work.get("sourceMode") != "live"
            or work.get("status") != "completed"
            or work.get("researchDesignVersion") != design_version
        ):
            errors.append(lineage_error)
            continue
        call_id = artifact.get("sourceCapabilityCallId")
        if call_id is None:
            continue
        call = calls.get(call_id)
        if (
            call is None
            or call.get("status") != "succeeded"
            or call.get("workUnitId") != work.get("workUnitId")
            or ref not in _unique_strings(call.get("outputArtifactRefs"))
        ):
            errors.append(source_call_error)
    return errors


def _expected_result_projection(
    *,
    state: Mapping[str, Any],
    decision: Mapping[str, Any] | None,
    disposition: str,
    integrity_version: object,
) -> tuple[dict[str, Any], tuple[str, ...]]:
    legacy_projection = legacy_expected_result_projection(
        state=state,
        disposition=disposition,
        version=integrity_version,
    )
    if legacy_projection is not None:
        return legacy_projection
    if integrity_version == INTEGRITY_CONTRACT_VERSION and decision is not None:
        return expected_final_result_fields(state, decision=decision), (
            "directAnswer",
            "keyFindings",
            "evidenceRefs",
            "scientificResult",
            "scientificSourceRefs",
            "uncertainty",
            "gaps",
            "limitations",
            "acceptedScientificFacts",
        )
    return {}, ()


def _projection_error_code(*, integrity_version: object, field: str) -> str:
    if integrity_version == INTEGRITY_CONTRACT_VERSION:
        if field == "scientificResult":
            return "scientific_state_projection_failed"
        if field == "scientificSourceRefs":
            return "scientific_source_projection_failed"
        if field in {"uncertainty", "gaps", "limitations"}:
            return "completion_diagnostic_projection_failed"
        return "legacy_semantic_projection_present"
    if field == "acceptedScientificFacts":
        return "accepted_scientific_fact_projection_failed"
    if field in {"uncertainty", "gaps", "limitations"}:
        return "gap_preservation_failed"
    return "fact_only_claim_boundary_failed"

def _authority_target_result(
    task: Mapping[str, Any], authority: Mapping[str, Any]
) -> tuple[Mapping[str, Any] | None, list[str]]:
    result_id = authority.get("finalResultId")
    results = [
        row for value in _list(task.get("finalResults")) if (row := _mapping(value))
    ]
    result = next((row for row in results if row.get("finalResultId") == result_id), None)
    if not isinstance(result_id, str) or not result_id or result is None:
        return result, ["final_result_missing"]
    return result, []


def _authority_design(
    task: Mapping[str, Any], authority: Mapping[str, Any]
) -> tuple[Mapping[str, Any] | None, object, list[str]]:
    errors: list[str] = []
    design_version = authority.get("researchDesignVersion")
    active_ref = _mapping(task.get("activeResearchDesignRef"))
    if active_ref.get("version") != design_version:
        errors.append("authority_design_not_current")
    design = _active_design(task, design_version=design_version)
    if design is None:
        errors.append("current_research_design_missing")
    return design, design_version, errors


def _current_completion_context(
    task: Mapping[str, Any],
    *,
    design: Mapping[str, Any] | None,
    design_version: object,
    result: Mapping[str, Any] | None,
) -> tuple[
    Mapping[str, Any] | None,
    Mapping[str, Any] | None,
    str | None,
    list[str],
    list[str],
]:
    errors: list[str] = []
    states = [
        row
        for value in _list(task.get("scientificStates"))
        if (row := _mapping(value))
        and row.get("researchDesignVersion") == design_version
    ]
    state = max(
        states,
        key=lambda row: int(row.get("scientificStateVersion") or 0),
        default=None,
    )
    if state is None:
        return None, None, None, [], ["current_scientific_state_missing"]

    state_version = state.get("scientificStateVersion")
    completion_rows = scientific_state_completion_rows(state)
    if any(not _completion_row_coverage(row)[1] for row in completion_rows):
        errors.append("completion_coverage_mismatch")

    decisions = [
        row
        for value in _list(task.get("completionDecisions"))
        if (row := _mapping(value))
        and row.get("scientificStateVersion") == state_version
    ]
    versioned, ambiguous = _versioned_completion_decision(decisions, design=design)
    if ambiguous:
        errors.append("current_completion_decision_ambiguous")
    if versioned is not None:
        decision = versioned
        errors.extend(
            _validate_project_completion_decision(
                task=task,
                design=design or {},
                state=state,
                decision=decision,
            )
        )
    else:
        historical = [
            row
            for row in decisions
            if row.get("completionAuthorityVersion") is None
        ]
        decision = max(
            historical,
            key=lambda row: str(row.get("decidedAt") or ""),
            default=None,
        )
    if decision is None:
        errors.append("current_completion_decision_missing")

    if result is not None and (
        decision is None
        or result.get("completionDecisionId") != decision.get("completionDecisionId")
        or result.get("scientificStateVersion") != state_version
    ):
        errors.append("final_result_not_current")

    expected_disposition = expected_completion_disposition(state)
    if decision is not None and decision.get("disposition") != expected_disposition:
        errors.append("completion_disposition_mismatch")
    if result is not None and result.get("disposition") != expected_disposition:
        errors.append("final_result_disposition_mismatch")

    return (
        state,
        decision,
        expected_disposition,
        scientific_state_artifact_refs(state),
        errors,
    )


def _completion_projection_errors(
    task: Mapping[str, Any],
    *,
    authority: Mapping[str, Any],
    result: Mapping[str, Any] | None,
    state: Mapping[str, Any],
    decision: Mapping[str, Any] | None,
    design_version: object,
    expected_disposition: str,
    artifact_refs: list[str],
) -> list[str]:
    errors: list[str] = []
    integrity_version = authority.get("integrityContractVersion")
    if decision is not None and _unique_strings(decision.get("sourceRefs")) != artifact_refs:
        errors.append("completion_sources_mismatch")
    if _unique_strings(authority.get("resultArtifactRefs")) != artifact_refs:
        errors.append("authority_artifact_refs_mismatch")

    if result is not None:
        errors.extend(
            legacy_result_reference_errors(
                result=result,
                artifact_refs=artifact_refs,
                version=integrity_version,
            )
        )
        if (
            integrity_version == INTEGRITY_CONTRACT_VERSION
            and _unique_strings(result.get("evidenceRefs"))
        ):
            errors.append("artifact_mislabeled_as_evidence")

    errors.extend(
        _artifact_lineage_errors(
            task,
            artifact_refs=artifact_refs,
            design_version=design_version,
            integrity_version=integrity_version,
        )
    )
    if result is None:
        return errors

    expected_fields, checked_fields = _expected_result_projection(
        state=state,
        decision=decision,
        disposition=expected_disposition,
        integrity_version=integrity_version,
    )
    for field in checked_fields:
        if result.get(field) != expected_fields[field]:
            errors.append(
                _projection_error_code(
                    integrity_version=integrity_version,
                    field=field,
                )
            )
    return errors


def _validation_outcome_errors(
    *,
    result: Mapping[str, Any] | None,
    integrity_version: object,
    require_persisted_outcome: bool,
) -> tuple[str, tuple[str, ...], list[str]]:
    contract = _validation_contract(integrity_version)
    if contract is None:
        return VALIDATOR_REF, (), ["integrity_contract_unsupported"]

    expected_validator_ref, expected_check_ids = contract
    if result is None or not require_persisted_outcome:
        return expected_validator_ref, expected_check_ids, []

    outcome = _mapping(result.get("validationOutcome"))
    if (
        outcome.get("contractVersion") != integrity_version
        or outcome.get("validatorRef") != expected_validator_ref
        or outcome.get("status") != "passed"
        or _unique_strings(outcome.get("checkIds")) != list(expected_check_ids)
        or outcome.get("validatedAt") != result.get("validatedAt")
    ):
        return expected_validator_ref, expected_check_ids, ["validator_outcome_not_passed"]
    return expected_validator_ref, expected_check_ids, []


def evaluate_final_result_integrity(
    task: Mapping[str, Any],
    *,
    require_persisted_outcome: bool,
) -> dict[str, Any]:
    """Evaluate the current authority-targeted Final Result from persisted facts."""

    authority = _mapping(task.get("finalResultAuthority"))
    result, result_errors = _authority_target_result(task, authority)
    design, design_version, design_errors = _authority_design(task, authority)
    state, decision, expected_disposition, artifact_refs, context_errors = (
        _current_completion_context(
            task,
            design=design,
            design_version=design_version,
            result=result,
        )
    )

    errors = [*result_errors, *design_errors, *context_errors]
    if state is not None and expected_disposition is not None:
        errors.extend(
            _completion_projection_errors(
                task,
                authority=authority,
                result=result,
                state=state,
                decision=decision,
                design_version=design_version,
                expected_disposition=expected_disposition,
                artifact_refs=artifact_refs,
            )
        )

    integrity_version = authority.get("integrityContractVersion")
    expected_validator_ref, _, outcome_errors = _validation_outcome_errors(
        result=result,
        integrity_version=integrity_version,
        require_persisted_outcome=require_persisted_outcome,
    )
    errors.extend(outcome_errors)
    errors = list(dict.fromkeys(errors))
    return {
        "passed": not errors,
        "expectedDisposition": expected_disposition,
        "errorCodes": errors,
        "validatorRef": expected_validator_ref,
        "contractVersion": integrity_version,
    }
