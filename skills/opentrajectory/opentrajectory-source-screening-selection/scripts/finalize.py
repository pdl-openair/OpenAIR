from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Mapping

from jsonschema import validators


ROOT = Path(__file__).resolve().parents[1]
SKILL_ID = "opentrajectory_source_screening_selection"


@dataclass(frozen=True)
class FinalizationResult:
    status: str
    output: dict[str, Any] | None
    errors: tuple[Any, ...] = ()


def _load_schema(name: str) -> dict[str, Any]:
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


INPUT_SCHEMA = _load_schema("input.schema.json")
MODEL_SCHEMA = _load_schema("model_output.schema.json")
OUTPUT_SCHEMA = _load_schema("output.schema.json")
INPUT_VALIDATOR = validators.validator_for(INPUT_SCHEMA)(INPUT_SCHEMA)
MODEL_VALIDATOR = validators.validator_for(MODEL_SCHEMA)(MODEL_SCHEMA)
OUTPUT_VALIDATOR = validators.validator_for(OUTPUT_SCHEMA)(OUTPUT_SCHEMA)


def _errors(validator, value: Any, prefix: str) -> list[str]:
    rows = sorted(
        (error for error in validator.iter_errors(value) if error.validator == "required"),
        key=lambda error: [str(part) for part in error.absolute_path],
    )
    return [
        f"{prefix}:/{'/'.join(map(str, row.absolute_path))}:{row.message}"
        for row in rows[:40]
    ]


def _known_target_ids(evidence_schema: Any) -> set[str]:
    if not isinstance(evidence_schema, Mapping):
        return set()
    return {
        str(row["target_id"])
        for row in evidence_schema.get("evidence_targets") or []
        if isinstance(row, Mapping)
        and isinstance(row.get("target_id"), str)
        and row["target_id"].strip()
    }


def _candidate_index(candidates: list[Mapping[str, Any]]):
    by_id = {str(row["candidate_id"]): row for row in candidates}
    order = {str(row["candidate_id"]): index for index, row in enumerate(candidates)}
    return by_id, order, []


def _judgment_index(judgments: list[Mapping[str, Any]], *, candidates: Mapping[str, Mapping[str, Any]], target_ids: set[str]):
    return {str(row["candidate_id"]): deepcopy(dict(row)) for row in judgments}, []


def _priority_for_sort(value: Any) -> float:
    try:
        return float(value) if value is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _materialize(candidate: Mapping[str, Any], decision: Mapping[str, Any]) -> dict[str, Any]:
    row = deepcopy(dict(candidate))
    row.update(
        {
            "selection_decision": decision["decision"],
            "selection_reason": decision["reason"],
            "priority_rank": decision["priority_rank"],
            "evidence_roles": list(decision.get("evidence_roles") or []),
            "relevant_target_ids": list(decision.get("relevant_target_ids") or []),
        }
    )
    return row


def finalize(
    input_payload: Mapping[str, Any],
    model_output: Mapping[str, Any],
) -> FinalizationResult:
    input_errors = _errors(INPUT_VALIDATOR, input_payload, "input")
    model_errors = _errors(MODEL_VALIDATOR, model_output, "model")
    if input_errors or model_errors:
        return FinalizationResult("failed", None, tuple([*input_errors, *model_errors]))

    candidates = list(input_payload["candidates"])
    candidate_by_id, order, candidate_errors = _candidate_index(candidates)
    target_ids = _known_target_ids(input_payload.get("evidence_schema"))
    judgment_by_id, judgment_diagnostics = _judgment_index(
        list(model_output["judgments"]),
        candidates=candidate_by_id,
        target_ids=target_ids,
    )
    if candidate_errors:
        return FinalizationResult("failed", None, tuple(candidate_errors))

    decisions: list[dict[str, Any]] = []
    selected: list[dict[str, Any]] = []
    selected_priority: dict[str, int | None] = {}
    deferred: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    unreviewed_count = 0

    for candidate_id, candidate in candidate_by_id.items():
        judgment = judgment_by_id.get(candidate_id)
        if judgment is None:
            unreviewed_count += 1
            decision = {
                "candidate_id": candidate_id,
                "decision": "unreviewed",
                "priority_rank": None,
                "evidence_roles": [],
                "relevant_target_ids": [],
                "reason": "The model did not return a judgment for this candidate.",
            }
        else:
            decision = {
                "candidate_id": candidate_id,
                "decision": judgment["decision"],
                "priority_rank": None,
                "evidence_roles": list(judgment.get("evidence_roles") or []),
                "relevant_target_ids": list(judgment.get("relevant_target_ids") or []),
                "reason": judgment["reason"],
            }

        decisions.append(decision)
        materialized = _materialize(candidate, decision)
        if decision["decision"] in {"priority", "supporting"}:
            selected_priority[candidate_id] = (
                judgment.get("reading_priority") if judgment is not None else None
            )
            selected.append(materialized)
        elif decision["decision"] == "exclude":
            excluded.append(materialized)
        else:
            deferred.append(materialized)

    selected.sort(
        key=lambda row: (
            0 if row["selection_decision"] == "priority" else 1,
            -_priority_for_sort(selected_priority.get(row["candidate_id"])),
            order[row["candidate_id"]],
        )
    )

    # Convert the model-owned scientific priority into the public deterministic rank.
    # Equal model priority keeps an equal rank; source order only stabilizes display.
    next_rank = 1
    previous_key: tuple[str, str] | None = None
    previous_rank: int | None = None
    decision_by_id = {row["candidate_id"]: row for row in decisions}
    for row in selected:
        reading_priority = selected_priority.get(row["candidate_id"])
        if reading_priority is None:
            continue
        rank_key = (row["selection_decision"], str(reading_priority))
        if rank_key == previous_key and previous_rank is not None:
            rank = previous_rank
        else:
            rank = next_rank
            previous_key = rank_key
            previous_rank = rank
        row["priority_rank"] = rank
        decision_by_id[row["candidate_id"]]["priority_rank"] = rank
        next_rank += 1

    for candidate_id, judgment in judgment_by_id.items():
        if candidate_id not in candidate_by_id:
            decisions.append({**deepcopy(dict(judgment)), "priority_rank": None})

    supporting = [
        row for row in selected
        if row["selection_decision"] == "supporting"
    ]
    method = [
        row for row in selected
        if "method" in row["evidence_roles"]
    ]

    risk_flags: list[str] = []
    degradation_reasons: list[str] = []
    if unreviewed_count:
        risk_flags.append("unreviewed_candidates_present")
        degradation_reasons.append("model_did_not_judge_every_candidate")
    if judgment_diagnostics:
        risk_flags.append("model_judgment_adjustments_present")
        degradation_reasons.append("model_judgment_adjustments_present")

    topk = {
        "selected_for_p03": selected,
        "extended_selected": [],
        "supporting_papers": supporting,
        "method_inspiration_papers": method,
        "deferred_candidates": deferred,
        "excluded_examples": excluded,
        "selection_stats": {
            "candidate_count": len(candidates),
            "model_judgment_count": len(judgment_by_id),
            "selected_total": len(selected),
            "priority_count": sum(
                row["selection_decision"] == "priority" for row in selected
            ),
            "supporting_count": sum(
                row["selection_decision"] == "supporting" for row in selected
            ),
            "deferred_count": len(deferred),
            "excluded_count": len(excluded),
            "unreviewed_count": unreviewed_count,
        },
        "p03_acquisition_plan": {
            "total_selected": len(selected),
            "selected_candidate_ids": [
                row["candidate_id"] for row in selected
            ],
        },
        "risk_summary": {
            "risk_flags": risk_flags,
            "unreviewed_candidates_present": bool(unreviewed_count),
        },
        "degraded": bool(degradation_reasons),
        "degradation_reasons": degradation_reasons,
    }

    output = {
        "topk_selection": topk,
        "screening_decisions": decisions,
        "summary": {
            "selected_count": len(selected),
            "unreviewed_count": unreviewed_count,
        },
        "audit": {
            "skill_id": SKILL_ID,
            "task_id": input_payload["task_id"],
            "traceability_mode": "model_judgment_candidate_identity_finalizer",
            "candidate_count": len(candidates),
            "model_judgment_count": len(judgment_by_id),
        },
    }

    output_errors = _errors(OUTPUT_VALIDATOR, output, "output")
    if output_errors:
        return FinalizationResult("failed", None, tuple(output_errors))

    result_diagnostics = list(judgment_diagnostics)
    if unreviewed_count:
        result_diagnostics.append("model_did_not_judge_every_candidate")
    return FinalizationResult(
        "degraded" if topk["degraded"] else "succeeded",
        output,
        tuple(result_diagnostics),
    )
