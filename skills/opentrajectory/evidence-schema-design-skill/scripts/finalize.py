from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Mapping


SCHEMA_VERSION = "0.1.0"


@dataclass(frozen=True)
class FinalizationResult:
    status: str
    output: dict[str, Any] | None
    errors: tuple[Any, ...] = ()


def _required_text(value: Any, *, label: str, preserve: bool = False) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label}_missing")
    return value if preserve else value.strip()


def _failed(code: str) -> FinalizationResult:
    return FinalizationResult("failed", None, (code,))


def finalize(
    input_payload: Mapping[str, Any],
    model_output: Mapping[str, Any],
) -> FinalizationResult:
    """Materialize the formal EvidenceSchema without rewriting model science.

    The model owns the semantic task scope, targets, gaps, uncertainty rules,
    warnings, or the semantic blocked reason. Code owns only deterministic
    identity/version fields and the formal ready/blocked wrapper.
    """
    if not isinstance(input_payload, Mapping) or not isinstance(model_output, Mapping):
        return _failed("evidence_schema_finalization_input_invalid")

    try:
        task_id = _required_text(input_payload.get("task_id"), label="task_id")
        intent = input_payload.get("intent_understanding")
        if not isinstance(intent, Mapping):
            return _failed("intent_understanding_missing")
        research_question = _required_text(
            intent.get("original_user_query"),
            label="research_question",
            preserve=True,
        )
    except ValueError as exc:
        return _failed(str(exc))

    ready = model_output.get("ready")
    blocked = model_output.get("blocked")
    if not isinstance(ready, list) or not isinstance(blocked, list):
        return _failed("evidence_schema_branch_shape_invalid")

    if blocked and not ready:
        row = blocked[0]
        if not isinstance(row, Mapping):
            return _failed("evidence_schema_blocked_payload_invalid")
        output = {"status": "blocked", **deepcopy(dict(row))}
        return FinalizationResult("succeeded", output, ())

    if not ready:
        return _failed("evidence_schema_ready_payload_missing")
    row = ready[0]
    if not isinstance(row, Mapping):
        return _failed("evidence_schema_ready_payload_invalid")
    semantic = deepcopy(dict(row))
    targets = semantic.pop("evidence_targets", None)
    uncertainty_rules = semantic.pop("uncertainty_rules", None)
    design_warnings = semantic.pop("design_warnings", None)
    if not isinstance(targets, list) or not isinstance(uncertainty_rules, list):
        return _failed("evidence_schema_semantic_collections_invalid")

    schema_id = f"{task_id}:evidence"

    materialized_targets: list[dict[str, Any]] = []
    for index, target in enumerate(targets, start=1):
        if not isinstance(target, Mapping):
            return _failed("evidence_schema_target_invalid")
        materialized_targets.append(
            {"target_id": f"target_{index:03d}", **deepcopy(dict(target))}
        )

    materialized_rules: list[dict[str, Any]] = []
    for index, rule in enumerate(uncertainty_rules, start=1):
        if not isinstance(rule, Mapping):
            return _failed("evidence_schema_uncertainty_rule_invalid")
        materialized_rules.append(
            {"rule_id": f"u{index:02d}", **deepcopy(dict(rule))}
        )

    try:
        task_scope = semantic.pop("task_scope")
        domain_hints = semantic.pop("domain_hints")
        design_assumptions = semantic.pop("design_assumptions")
        gap_taxonomy = semantic.pop("gap_taxonomy")
    except KeyError:
        return _failed("evidence_schema_ready_semantics_incomplete")

    output = {
        "status": "ready",
        "evidence_schema": {
            "schema_id": schema_id,
            "schema_version": SCHEMA_VERSION,
            "task_context": {
                "research_question": research_question,
                "task_scope": task_scope,
                "domain_hints": domain_hints,
                "design_assumptions": design_assumptions,
            },
            "evidence_targets": materialized_targets,
            "gap_taxonomy": gap_taxonomy,
            "uncertainty_rules": materialized_rules,
            **semantic,
        },
        "design_warnings": design_warnings,
    }
    return FinalizationResult("succeeded", output, ())
