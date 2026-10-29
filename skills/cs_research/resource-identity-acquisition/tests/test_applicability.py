from __future__ import annotations

from resource_identity_acquisition.applicability import (
    RESEARCH_TASK_FACT_REF,
    evaluate_capability_applicability,
)


def _request(projection):
    task_artifacts = []
    if projection is not None:
        task_artifacts = [
            {
                "artifactId": "artifact-design",
                "artifactType": "reproduction_experiment_design",
                "artifactState": "usable",
                "validatorResult": {"artifactFieldProjection": projection},
            }
        ]
    return {
        "capabilityRef": "skill:resource_identity_acquisition:1",
        "evaluatorRef": (
            "python:resource_identity_acquisition.applicability:"
            "evaluate_capability_applicability"
        ),
        "runtimeFacts": {RESEARCH_TASK_FACT_REF: {"artifacts": task_artifacts}},
    }


def test_empty_resource_requests_are_ineligible():
    result = evaluate_capability_applicability(
        _request({"resource_acquisition_requests": 0, "status": "blocked"})
    )
    assert result["status"] == "ineligible"


def test_non_empty_resource_requests_are_eligible():
    result = evaluate_capability_applicability(
        _request({"resource_acquisition_requests": 2, "status": "ready"})
    )
    assert result["status"] == "eligible"


def test_missing_design_stays_eligible_for_material_unavailable():
    result = evaluate_capability_applicability(_request(None))
    assert result["status"] == "eligible"


def test_ambiguous_design_projections_fail_closed():
    request = {
        "capabilityRef": "skill:resource_identity_acquisition:1",
        "evaluatorRef": (
            "python:resource_identity_acquisition.applicability:"
            "evaluate_capability_applicability"
        ),
        "runtimeFacts": {
            RESEARCH_TASK_FACT_REF: {
                "artifacts": [
                    {
                        "artifactId": "artifact-design-a",
                        "artifactType": "reproduction_experiment_design",
                        "artifactState": "usable",
                        "validatorResult": {
                            "artifactFieldProjection": {
                                "resource_acquisition_requests": 0
                            }
                        },
                    },
                    {
                        "artifactId": "artifact-design-b",
                        "artifactType": "reproduction_experiment_design",
                        "artifactState": "usable",
                        "validatorResult": {
                            "artifactFieldProjection": {
                                "resource_acquisition_requests": 1
                            }
                        },
                    },
                ]
            }
        },
    }
    result = evaluate_capability_applicability(request)
    assert result["status"] == "evaluation_failed"