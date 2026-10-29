"""Schema utilities for the generic research-lead package contract."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

PACKAGE_VERSION = "research_lead_package_v0.1"

EVALUATION_MODES = {"offline_proposal", "traceable_package", "reproduced_result"}
CLAIMED_RESULT_STATUSES = {"planned", "observed", "reproduced"}
SOURCE_TYPES = {"recorded_run", "result_package", "live_output", "manual_fixture"}
MAPPING_CONFIDENCE = {"high", "medium", "low"}

REPRODUCIBILITY_KEYS = (
    "repo",
    "commit",
    "config",
    "command",
    "seed",
    "model_version",
    "data_split",
    "logs",
)

PROPOSAL_KEYS = (
    "problem_formulation",
    "input_output_setting",
    "failure_mode",
    "technical_intervention",
    "expected_effect",
    "mechanism_hypothesis",
    "baseline",
    "validation_target",
    "metrics",
    "control_or_ablation_plan",
    "reproducibility_protocol",
    "failure_or_error_taxonomy",
    "compute_or_cost_boundary",
    "statistical_robustness_plan",
    "falsification_criteria",
    "next_experiment_matrix",
    "evidence_refs",
    "artifact_refs",
    "gap_records",
    "claimed_result_status",
)

GENERIC_PACKAGE_KEYS = (
    "package_version",
    "evaluation_mode",
    "domain_profile",
    "task_domain",
    "task_subdomain",
    "proposal",
    "domain_normalized",
    "source_mapping",
)

DEFAULT_PROPOSAL: dict[str, Any] = {
    "problem_formulation": "",
    "input_output_setting": "",
    "failure_mode": "",
    "technical_intervention": "",
    "expected_effect": "",
    "mechanism_hypothesis": "",
    "baseline": [],
    "validation_target": [],
    "metrics": [],
    "control_or_ablation_plan": [],
    "reproducibility_protocol": {key: "" for key in REPRODUCIBILITY_KEYS},
    "failure_or_error_taxonomy": [],
    "compute_or_cost_boundary": "",
    "statistical_robustness_plan": "",
    "falsification_criteria": "",
    "next_experiment_matrix": [],
    "evidence_refs": [],
    "artifact_refs": [],
    "gap_records": [],
    "claimed_result_status": "planned",
}

DEFAULT_PACKAGE: dict[str, Any] = {
    "package_version": PACKAGE_VERSION,
    "evaluation_mode": "offline_proposal",
    "domain_profile": "",
    "task_domain": "",
    "task_subdomain": "",
    "proposal": deepcopy(DEFAULT_PROPOSAL),
    "domain_normalized": {"profile_id": "", "fields": {}},
    "source_mapping": {
        "source_type": "manual_fixture",
        "source_paths": [],
        "field_mapping_notes": [],
        "unmapped_source_fields": [],
        "mapping_confidence": "low",
    },
}

def empty_package() -> dict[str, Any]:
    """Return a fresh package with the generic contract shape."""

    return deepcopy(DEFAULT_PACKAGE)


def validate_research_lead_package(package: dict[str, Any]) -> list[str]:
    """Return structural validation errors for the generic package."""

    errors: list[str] = []
    for key in GENERIC_PACKAGE_KEYS:
        if key not in package:
            errors.append(f"missing package key: {key}")

    proposal = package.get("proposal") or {}
    for key in PROPOSAL_KEYS:
        if key not in proposal:
            errors.append(f"missing proposal key: {key}")

    return errors
