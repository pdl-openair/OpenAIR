"""Skill-owned deterministic finalization for Constraint Interpretation."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class FinalizationResult:
    status: str
    output: dict[str, Any] | None
    errors: tuple[Any, ...] = ()


def _failed(code: str) -> FinalizationResult:
    return FinalizationResult("failed", None, (code,))


def _input_phrase(input_payload: Mapping[str, Any], source_index: Any) -> str:
    if isinstance(source_index, bool) or not isinstance(source_index, int):
        raise ValueError("source_constraint_index_invalid")
    constraints = input_payload.get("raw_constraints")
    if not isinstance(constraints, list) or not 0 <= source_index < len(constraints):
        raise ValueError("source_constraint_index_invalid")
    source = constraints[source_index]
    if not isinstance(source, Mapping):
        raise ValueError("source_constraint_invalid")
    phrase = source.get("phrase")
    if not isinstance(phrase, str) or not phrase.strip():
        phrase = source.get("original_phrase")
    if not isinstance(phrase, str) or not phrase.strip():
        raise ValueError("source_constraint_phrase_missing")
    return phrase


def finalize(
    input_payload: Mapping[str, Any],
    model_output: Mapping[str, Any],
) -> FinalizationResult:
    """Preserve model science and restore only exact input-owned source phrases."""
    if not isinstance(input_payload, Mapping) or not isinstance(model_output, Mapping):
        return _failed("constraint_interpretation_finalization_input_invalid")

    output = deepcopy(dict(model_output))
    constraints = output.get("interpreted_constraints")
    if not isinstance(constraints, list):
        return _failed("interpreted_constraints_invalid")

    deprecated_field_ignored = False
    try:
        for row in constraints:
            if not isinstance(row, dict):
                raise ValueError("interpreted_constraint_invalid")
            if "downstream_filter_rules" in row:
                del row["downstream_filter_rules"]
                deprecated_field_ignored = True
            source_index = row.pop("source_constraint_index", None)
            supplied_phrase = row.get("original_phrase")
            if source_index is None:
                if "original_phrase" not in row:
                    raise ValueError("original_phrase_missing")
                continue
            if supplied_phrase is None:
                try:
                    row["original_phrase"] = _input_phrase(input_payload, source_index)
                except ValueError:
                    row["original_phrase"] = None
    except ValueError as exc:
        return _failed(str(exc))

    if deprecated_field_ignored:
        return FinalizationResult("degraded", output, ("deprecated_field_ignored",))
    return FinalizationResult("succeeded", output, ())
