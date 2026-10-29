"""Deterministically close the final CS reproduction conclusion."""
from __future__ import annotations

from copy import deepcopy
from typing import Any


_VERDICT_MAP = {
    "meets_standard": "reproduced",
    "does_not_meet_standard": "not_reproduced",
    "indeterminate": "indeterminate",
}
_COMPARISON_SOURCE = "reproduction_comparison_judgment"


def _nonempty(value: Any) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else ""


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _declared_refs(value: Any) -> list[str]:
    """Read one explicit evidence-ref field; never discover refs recursively."""

    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        return []
    refs = [ref for item in value if (ref := _nonempty(item))]
    return _dedupe(refs)


def _known_comparison_refs(comparison: dict[str, Any]) -> list[str]:
    refs = _declared_refs(comparison.get("evidence_refs"))
    comparability = comparison.get("comparability")
    conditions = comparability.get("conditions") if isinstance(comparability, dict) else None
    if isinstance(conditions, dict):
        for item in conditions.values():
            if isinstance(item, dict) and "evidence_refs" in item:
                refs.extend(_declared_refs(item.get("evidence_refs")))
    return _dedupe(refs)


def _is_material(value: Any) -> bool:
    if isinstance(value, (list, dict, str)):
        return bool(value)
    return value is not None


def _evidence_sufficiency(
    verdict: str,
    comparison: dict[str, Any],
    actual: dict[str, Any],
    execution: dict[str, Any],
) -> str:
    if verdict != "indeterminate":
        return "sufficient"
    material_evidence = bool(
        _known_comparison_refs(comparison)
        or _is_material(comparison.get("metric_judgments"))
        or _nonempty(actual.get("execution_record_ref"))
        or _is_material(actual.get("metrics"))
        or _is_material(execution.get("raw_output_trace"))
    )
    return "partial" if material_evidence else "insufficient"


def _isolate(
    isolated: list[dict[str, Any]],
    segment_type: str,
    reason_code: str,
    index: int | None = None,
) -> None:
    item: dict[str, Any] = {
        "segment_type": segment_type,
        "reason_code": reason_code,
    }
    if index is not None:
        item["index"] = index
    isolated.append(item)


def _accept_segment(value: Any, *, segment_type: str, isolated: list[dict[str, Any]], index: int | None = None) -> dict[str, Any] | None:
    if not isinstance(value, dict) or not {"text", "evidence_refs"}.issubset(value):
        _isolate(isolated, segment_type, "segment_required_keys_missing", index)
        return None
    return deepcopy(value)


def _accept_segment_list(
    value: Any,
    *,
    segment_type: str,
    isolated: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        _isolate(isolated, segment_type, "segment_collection_invalid")
        return []
    accepted: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        segment = _accept_segment(
            item,
            segment_type=segment_type,
            isolated=isolated,
            index=index,
        )
        if segment is not None:
            accepted.append(segment)
    return accepted


def _accept_next_steps(value: Any, diagnosis: Any, isolated: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        _isolate(isolated, "next_step", "segment_collection_invalid")
        return []
    required = {"step_id", "action", "reason", "expected_judgment_effect", "target_definition_id", "requires_design_change"}
    result = []
    for index, item in enumerate(value):
        if not isinstance(item, dict) or not required.issubset(item):
            _isolate(isolated, "next_step", "next_step_required_keys_missing", index)
        else:
            result.append(deepcopy(item))
    return result


def _upstream_unknowns(
    comparison: dict[str, Any],
    execution: dict[str, Any],
    actual: dict[str, Any],
    diagnosis: Any,
) -> list[dict[str, str]]:
    unknowns: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def add(source: str, value: Any) -> None:
        text = _nonempty(value)
        key = (source, text)
        if text and key not in seen:
            seen.add(key)
            unknowns.append({"text": text, "source": source})

    for source_name, source in (
        ("reproduction_comparison_judgment", comparison),
        ("reproduction_execution_record", execution),
        ("actual_metric_records", actual),
    ):
        values = source.get("unknowns")
        if isinstance(values, list):
            for value in values:
                add(source_name, value)

    if isinstance(diagnosis, dict):
        values = diagnosis.get("remaining_unknowns")
        if isinstance(values, list):
            for value in values:
                if isinstance(value, dict):
                    add("reproduction_deviation_diagnosis", value.get("statement"))
                else:
                    add("reproduction_deviation_diagnosis", value)
    return unknowns


def run_reproduction_conclusion_closure(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")
    execution = payload.get("reproduction_execution_record")
    actual = payload.get("actual_metric_records")
    comparison = payload.get("reproduction_comparison_judgment")
    diagnosis = payload.get("reproduction_deviation_diagnosis")
    if not all(isinstance(value, dict) for value in (execution, actual, comparison)):
        raise ValueError("formal conclusion authority inputs must be objects")

    raw_verdict = comparison.get("overall_verdict")
    verdict = _VERDICT_MAP.get(raw_verdict, raw_verdict)
    execution_status = execution.get("status")

    comparison_refs = _known_comparison_refs(comparison)
    execution_ref = _nonempty(actual.get("execution_record_ref"))
    draft = payload.get("reproduction_conclusion_draft")
    isolated: list[dict[str, Any]] = []
    gaps: list[str] = []
    answer: dict[str, Any] | None = None
    scope: dict[str, Any] | None = None
    limitations: list[dict[str, Any]] = []
    draft_unknowns: list[dict[str, Any]] = []
    next_steps: list[dict[str, Any]] = []

    if isinstance(draft, dict):
        answer = _accept_segment(
            draft.get("answer"),
            segment_type="answer",
            isolated=isolated,
        )
        scope = _accept_segment(
            draft.get("scope"),
            segment_type="scope",
            isolated=isolated,
        )
        limitations = _accept_segment_list(
            draft.get("limitations"),
            segment_type="limitation",
            isolated=isolated,
        )
        draft_unknowns = _accept_segment_list(
            draft.get("unknowns"),
            segment_type="unknown",
            isolated=isolated,
        )
        next_steps = _accept_next_steps(draft.get("next_steps"), diagnosis, isolated)
        if answer is None:
            gaps.append("answer_missing_or_ungrounded")
        if not any((answer, scope, limitations, draft_unknowns, next_steps)):
            gaps.append("no_valid_synthesis_segments")
    else:
        gaps.extend(["synthesis_draft_missing_or_invalid", "answer_missing_or_ungrounded"])

    accepted_segment_refs: list[str] = []
    for segment in [answer, scope, *limitations, *draft_unknowns]:
        if isinstance(segment, dict):
            accepted_segment_refs.extend(segment["evidence_refs"])
    refs = list(comparison_refs)
    if execution_ref:
        refs.append(execution_ref)
    refs.extend(accepted_segment_refs)

    return {
        "verdict": verdict,
        "comparison": deepcopy(comparison),
        "comparison_source": _COMPARISON_SOURCE,
        "answer": deepcopy(answer),
        "scope": deepcopy(scope),
        "execution_status": execution_status,
        "evidence_sufficiency": _evidence_sufficiency(
            verdict, comparison, actual, execution
        ),
        "evidence_refs": _dedupe(refs),
        "limitations": deepcopy(limitations),
        "unknowns": deepcopy(draft_unknowns),
        "upstream_unknowns": _upstream_unknowns(
            comparison, execution, actual, diagnosis
        ),
        "next_steps": deepcopy(next_steps),
        "synthesis_gaps": _dedupe(gaps),
        "isolated_synthesis_segments": deepcopy(isolated),
    }
