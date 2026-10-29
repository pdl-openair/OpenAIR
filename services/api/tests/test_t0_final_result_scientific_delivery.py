from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.research_orchestration import FinalResultValidationOutcome
from app.modules.scientific_completion_integrity import (
    evaluate_final_result_integrity,
    expected_final_result_fields,
    validation_outcome_payload,
)
from app.modules.scientific_completion_legacy import (
    V2_INTEGRITY_CONTRACT_VERSION,
    expected_v1_final_result_fields,
    expected_v2_final_result_fields,
    legacy_validation_contract,
)


def _state() -> dict:
    return {
        "scientificFacts": {
            "direct_answer": "Evidence supports route A for the confirmed scope.",
            "evidence_supported_conclusions": [
                {
                    "statement": "Route A is supported by the accepted evidence.",
                    "confidence": 0.9,
                }
            ],
            "research_leads": [
                {"question": "Test long-duration stability next."}
            ],
            "pvd_recommendation": {
                "power_w": 240,
                "n2_sccm": 18,
                "ar_sccm": 22,
                "deposition_time_min": 30,
            },
        },
        "executionState": {
            "validatedArtifactRefs": ["artifact-final-synthesis"],
            "substageCompletion": [],
        },
        "sourceRefs": ["artifact-accepted-scientific-result"],
    }


def _decision() -> dict:
    return {
        "gaps": ["completion-gap"],
        "uncertainty": ["completion-uncertainty"],
        "limitations": ["execution-limitation"],
    }


def test_v3_final_result_is_exact_domain_neutral_scientific_state_delivery() -> None:
    state = _state()

    result = expected_final_result_fields(state, decision=_decision())

    assert result["scientificResult"] == state["scientificFacts"]
    assert result["scientificSourceRefs"] == state["sourceRefs"]
    assert result["uncertainty"] == ["completion-uncertainty"]
    assert result["gaps"] == ["completion-gap"]
    assert result["limitations"] == ["execution-limitation"]


def test_v3_core_does_not_reinterpret_domain_fact_names() -> None:
    result = expected_final_result_fields(_state(), decision=_decision())

    assert result["directAnswer"] is None
    assert result["keyFindings"] == []
    assert result["evidenceRefs"] == []
    assert result["acceptedScientificFacts"] == {}
    assert result["scientificResult"]["direct_answer"] == (
        "Evidence supports route A for the confirmed scope."
    )
    assert result["scientificResult"]["research_leads"] == [
        {"question": "Test long-duration stability next."}
    ]
    assert result["scientificResult"]["pvd_recommendation"]["power_w"] == 240


def test_v3_delivery_is_an_immutable_snapshot_not_a_live_alias() -> None:
    state = _state()

    result = expected_final_result_fields(state, decision=_decision())
    state["scientificFacts"]["pvd_recommendation"]["power_w"] = 999
    state["sourceRefs"].append("artifact-later-source")

    assert result["scientificResult"]["pvd_recommendation"]["power_w"] == 240
    assert result["scientificSourceRefs"] == ["artifact-accepted-scientific-result"]


def test_v3_keeps_artifact_provenance_distinct_from_evidence() -> None:
    state = _state()

    result = expected_final_result_fields(state, decision=_decision())

    assert result["scientificSourceRefs"] == ["artifact-accepted-scientific-result"]
    assert result["evidenceRefs"] == []
    assert state["executionState"]["validatedArtifactRefs"] == [
        "artifact-final-synthesis"
    ]


def test_v2_contract_remains_readable_under_its_original_projection() -> None:
    state = _state()

    result = expected_v2_final_result_fields(state, disposition="full")

    assert result["directAnswer"] == state["scientificFacts"]["direct_answer"]
    assert result["keyFindings"] == [
        "Route A is supported by the accepted evidence."
    ]
    assert result["evidenceRefs"] == ["artifact-final-synthesis"]
    assert result["acceptedScientificFacts"] == state["scientificFacts"]


def test_v1_contract_keeps_historical_template_semantics() -> None:
    result = expected_v1_final_result_fields(_state(), disposition="full")

    assert result["directAnswer"] == (
        "Validated research outputs are available for the confirmed scope."
    )
    assert result["keyFindings"] == [
        "The persisted ScientificState records all required completion criteria as satisfied."
    ]
    assert "acceptedScientificFacts" not in result


def test_v1_v2_and_v3_validation_outcome_versions_remain_distinct() -> None:
    for version in (1, 2):
        FinalResultValidationOutcome.model_validate(
            {
                "contractVersion": f"scientific_completion_integrity_v{version}",
                "validatorRef": f"core:scientific_completion_integrity:v{version}",
                "status": "passed",
                "checkIds": ["current_completion_decision"],
                "validatedAt": "2026-09-14T09:00:00+00:00",
            }
        )
    v3 = FinalResultValidationOutcome.model_validate(
        validation_outcome_payload(validated_at="2026-09-14T09:00:00+00:00")
    )
    assert v3.contract_version == "scientific_completion_integrity_v3"

    with pytest.raises(ValidationError, match="contract and validator versions must match"):
        FinalResultValidationOutcome.model_validate(
            {
                "contractVersion": "scientific_completion_integrity_v3",
                "validatorRef": "core:scientific_completion_integrity:v2",
                "status": "passed",
                "checkIds": ["current_completion_decision"],
                "validatedAt": "2026-09-14T09:00:00+00:00",
            }
        )


def _integrity_task(*, version: str = "scientific_completion_integrity_v3") -> dict:
    design = {
        "researchDesignId": "design-final-delivery",
        "version": 1,
        "projectCompletionRequirements": [
            {
                "completionRequirementId": "requirement-final-delivery",
                "targetOutputRef": "final_delivery",
            }
        ],
        "coverageReport": {
            "targets": [
                {
                    "targetId": "target-final-delivery",
                    "sourceType": "target_output",
                    "sourcePath": "final_delivery",
                }
            ],
            "claims": [
                {
                    "targetId": "target-final-delivery",
                    "coverageMode": "full",
                    "acceptanceCriterionRefs": ["criterion-final-delivery"],
                }
            ],
        },
    }
    state = {
        "scientificStateVersion": 1,
        "researchDesignVersion": 1,
        "scientificFacts": _state()["scientificFacts"],
        "executionState": {
            "validatedArtifactRefs": ["artifact-final-synthesis"],
            "substageCompletion": [
                {
                    "substageInstanceId": "substage-final-delivery",
                    "criteria": [
                        {
                            "criterionId": "criterion-final-delivery",
                            "status": "satisfied",
                        }
                    ],
                    "completionCriteriaSatisfied": True,
                    "unresolvedCriterionIds": [],
                    "unmetCriterionIds": [],
                    "gaps": [],
                    "uncertainty": [],
                    "runtimeStatus": "completed",
                    "hasExecutionDegradation": False,
                }
            ],
        },
        "sourceRefs": ["artifact-accepted-scientific-result"],
    }
    decision = {
        "completionDecisionId": "completion-final-delivery",
        "completionAuthorityVersion": "project_completion_v1",
        "researchDesignRef": {
            "researchDesignId": "design-final-delivery",
            "version": 1,
        },
        "scientificStateVersion": 1,
        "canComplete": True,
        "requirementAssessments": [
            {
                "completionRequirementId": "requirement-final-delivery",
                "targetOutputRef": "final_delivery",
                "status": "satisfied",
                "coverageTargetId": "target-final-delivery",
                "acceptanceCriterionRefs": ["criterion-final-delivery"],
                "gaps": [],
                "uncertainty": [],
            }
        ],
        "disposition": "full",
        "gaps": [],
        "uncertainty": [],
        "limitations": [],
        "rationale": (
            "All confirmed project completion requirements are satisfied by the current ScientificState."
        ),
        "sourceRefs": ["artifact-final-synthesis"],
        "decidedAt": "2026-09-14T09:00:00+00:00",
    }
    if version == "scientific_completion_integrity_v3":
        result_fields = expected_final_result_fields(state, decision=decision)
        outcome = validation_outcome_payload(validated_at="2026-09-14T09:00:00+00:00")
    else:
        result_fields = expected_v2_final_result_fields(state, disposition="full")
        contract = legacy_validation_contract(version)
        assert contract is not None
        validator_ref, check_ids = contract
        outcome = {
            "contractVersion": version,
            "validatorRef": validator_ref,
            "status": "passed",
            "checkIds": list(check_ids),
            "validatedAt": "2026-09-14T09:00:00+00:00",
        }
    return {
        "activeResearchDesignRef": {
            "researchDesignId": "design-final-delivery",
            "version": 1,
        },
        "researchDesignVersions": [design],
        "scientificStates": [state],
        "completionDecisions": [decision],
        "workUnits": [
            {
                "workUnitId": "work-final-delivery",
                "sourceMode": "live",
                "status": "completed",
                "researchDesignVersion": 1,
            }
        ],
        "capabilityCalls": [],
        "artifacts": [
            {
                "artifactId": "artifact-final-synthesis",
                "artifactState": "usable",
                "workUnitId": "work-final-delivery",
                "sourceCapabilityCallId": None,
            }
        ],
        "finalResults": [
            {
                "finalResultId": "final:completion-final-delivery",
                "completionDecisionId": "completion-final-delivery",
                "scientificStateVersion": 1,
                "disposition": "full",
                **result_fields,
                "validatedAt": "2026-09-14T09:00:00+00:00",
                "validationOutcome": outcome,
            }
        ],
        "finalResultAuthority": {
            "researchDesignVersion": 1,
            "resultArtifactRefs": ["artifact-final-synthesis"],
            "finalResultId": "final:completion-final-delivery",
            "integrityContractVersion": version,
        },
    }


def test_v3_integrity_accepts_only_exact_current_state_projection() -> None:
    task = _integrity_task()

    evaluation = evaluate_final_result_integrity(task, require_persisted_outcome=True)

    assert evaluation["passed"] is True
    assert evaluation["errorCodes"] == []


def test_v3_integrity_rejects_scientific_rewrite_and_artifact_as_evidence() -> None:
    task = _integrity_task()
    result = task["finalResults"][0]
    result["scientificResult"] = {"invented": "claim"}
    result["evidenceRefs"] = ["artifact-final-synthesis"]

    evaluation = evaluate_final_result_integrity(task, require_persisted_outcome=True)

    assert evaluation["passed"] is False
    assert "scientific_state_projection_failed" in evaluation["errorCodes"]
    assert "artifact_mislabeled_as_evidence" in evaluation["errorCodes"]


def test_v2_integrity_remains_readable_only_through_legacy_contract() -> None:
    task = _integrity_task(version=V2_INTEGRITY_CONTRACT_VERSION)

    evaluation = evaluate_final_result_integrity(task, require_persisted_outcome=True)

    assert evaluation["passed"] is True
    assert evaluation["contractVersion"] == V2_INTEGRITY_CONTRACT_VERSION
