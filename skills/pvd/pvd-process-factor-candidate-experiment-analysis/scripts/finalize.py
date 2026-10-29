from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class FinalizationResult:
    status: str
    output: dict[str, Any] | None
    errors: tuple[str, ...] = ()






def finalize(
    input_payload: Mapping[str, Any],
    draft_payload: Mapping[str, Any],
) -> FinalizationResult:
    current_context = input_payload.get("current_context") or {}
    specs = {row.get("parameter_key"): row for row in (current_context.get("recommendation_parameter_set") or {}).get("parameters") or []}
    final_rows = []
    for original in draft_payload.get("factor_assessments") or []:
        row = copy.deepcopy(dict(original))
        key = row.get("factor")
        spec = specs.get(key) or {}
        row["candidate_ref"] = f"node6:factor_assessment:{key}"
        row["display_name"] = spec.get("display_name") or key
        row["is_recommendation_parameter"] = key in specs
        final_rows.append(row)
    order = list(specs)
    focus = draft_payload.get("recommended_next_experiment_focus") or {}
    has_feedback = input_payload.get("feedback_analysis") is not None
    degraded = draft_payload.get("analysis_status") == "degraded"
    output = {
        "analysis_status": "degraded" if degraded else "complete",
        "analysis_basis": {
            "parameter_scope": "current_context.recommendation_parameter_set",
            "history_mode": "feedback" if has_feedback else "R0",
            "diagnosis_status": "present" if has_feedback else "absent",
        },
        "factor_assessments": final_rows,
        "recommended_next_experiment_focus": copy.deepcopy(dict(focus)),
        "limitations": list(draft_payload.get("limitations") or []),
        "audit": {"parameter_keys": order, "scope_exact": {row.get("factor") for row in final_rows} == set(order)},
    }
    return FinalizationResult("degraded" if degraded else "succeeded", output)
