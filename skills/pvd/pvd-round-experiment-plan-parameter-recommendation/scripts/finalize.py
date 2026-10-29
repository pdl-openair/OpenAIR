"""Bind model-authored recommendations to existing parameter metadata."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class FinalizationResult:
    status: str
    output: dict[str, Any] | None
    errors: tuple[str, ...] = ()


def finalize(inputs: Mapping[str, Any], draft: Mapping[str, Any]) -> FinalizationResult:
    context = inputs.get("current_context") or {}
    specs = {row.get("parameter_key"): row for row in (context.get("recommendation_parameter_set") or {}).get("parameters") or []}
    directions = {row.get("factor"): row for row in (inputs.get("direction_analysis") or {}).get("factor_assessments") or []}
    feasibility = inputs.get("feasibility_validation") or {}
    admissions = {row.get("factor"): row for row in feasibility.get("direction_assessments") or []}
    experiments = deepcopy(draft.get("experiments") or [])
    for experiment in experiments:
        for item in experiment.get("parameters") or []:
            key = item.get("parameter_key")
            spec, direction, admission = specs.get(key) or {}, directions.get(key) or {}, admissions.get(key) or {}
            for field, value in {
                "display_name": spec.get("display_name"), "unit": spec.get("unit"),
                "candidate_direction": direction.get("candidate_direction"),
                "admission_decision": admission.get("decision"), "direction_ref": direction.get("candidate_ref"),
            }.items():
                item.setdefault(field, value)
    result = deepcopy(dict(draft))
    result.update({
        "experiment_count": len(experiments), "experiments": experiments,
        "blocked_experiments": [], "global_optimum_claimed": deepcopy(draft.get("global_optimum_claimed")),
        "first_run_ranking_status": draft.get("first_run_ranking_status") or ("ranked" if draft.get("limited_resource_first_to_run_experiment_ids") else "pending" if experiments else "not_applicable"),
        "shared_conditions": deepcopy(feasibility.get("shared_conditions") or []),
        "shared_risks": deepcopy(feasibility.get("shared_risks") or []),
        "audit": {"parameter_keys": list(specs), "direction_and_admission_joined_without_candidate_authority": True},
    })
    return FinalizationResult("succeeded", result)
