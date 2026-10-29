"""Report scientific input gaps without inventing or deleting scientific values."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
from typing import Any

import jsonschema


def with_input_limits(instructions: str, warnings: list[str]) -> str:
    """Expose input gaps without sending a Schema or authorizing a new fact."""
    if not warnings:
        return instructions
    return instructions + (
        " Input contract diagnostics follow. Preserve these gaps as limitations; "
        "do not replace invalid or missing judgments with valid choices. Unrecognized "
        "fields are context only, not established facts or execution authorization. "
        + " ".join(warnings)
    )


def validate_cli_schema(
    instance: Any,
    schema: dict[str, Any],
    label: str,
    *,
    role: str,
    schema_validator: Callable[[Any], None] | None,
    input_diagnostics: dict[str, Any] | None,
) -> Any:
    """Preserve the CLI exception/output behavior and expose its input gaps."""
    from app.execution.skill_contract_policy import apply_semantic_contract

    normalized, warnings = apply_semantic_contract(
        deepcopy(instance) if role == "input" else instance,
        schema,
        label=label,
        role=role,  # type: ignore[arg-type]
        schema_validator=schema_validator,
    )
    if role == "input":
        if input_diagnostics is not None:
            input_diagnostics.update(
                {
                    "schemaPassed": not any(w.startswith("input_contract_gap:") for w in warnings),
                    "warnings": readable_input_warnings(warnings),
                }
            )
        return normalized
    if isinstance(instance, dict) and isinstance(normalized, dict):
        if normalized is not instance:
            instance.clear()
            instance.update(normalized)
        return instance
    return normalized


def validate_runtime_json(
    value: Any,
    schema: dict[str, Any],
    label: str,
    *,
    role: str,
    input_warnings: list[str] | None = None,
) -> Any:
    """Keep scientific output losslessly; report structure gaps instead of rejecting it."""
    from app.execution.skill_contract_policy import apply_semantic_contract

    if role == "output":
        validator = jsonschema.validators.validator_for(schema)(schema)
        gaps = [error for error in validator.iter_errors(value) if error.validator == "required"]
        if input_warnings is not None:
            input_warnings.extend(
                f"output_contract_gap:{error.json_path}:{error.validator}"
                for error in gaps
            )
        # Never normalize, trim, drop extras or manufacture missing judgments.
        # This is usable output, not a claim that Formal Schema is satisfied.
        return value
    normalized, warnings = apply_semantic_contract(
        deepcopy(value) if role == "input" else value,
        schema,
        label=label,
        role=role,  # type: ignore[arg-type]
    )
    if role == "input":
        if input_warnings is not None:
            input_warnings.extend(readable_input_warnings(warnings))
        return normalized
    if isinstance(value, dict) and isinstance(normalized, dict):
        if normalized is not value:
            value.clear()
            value.update(normalized)
        return value
    return normalized


def validate_scientific_input(
    value: Any,
    schema: Mapping[str, Any],
    *,
    label: str,
    warnings: list[str],
    validate_payload: Callable[[Any], None],
    collect_errors: bool = True,
) -> tuple[Any, list[str]]:
    """Preserve input unchanged and report only existing missing-key errors."""
    try:
        validate_payload(value)
    except jsonschema.ValidationError as first:
        # Custom validators own reference resolution; do not revalidate their
        # errors using a different resolver and turn a local gap into failure.
        errors = [first]
        owner = getattr(validate_payload, "__self__", None)
        validator = getattr(owner, "validator", owner)
        if collect_errors:
            validator = jsonschema.validators.validator_for(schema)(schema)
        iterator = getattr(validator, "iter_errors", None)
        if callable(iterator):
            errors = list(iterator(value)) or errors
        errors = [error for error in errors if error.validator == "required"]
        warnings.extend(
            f"input_contract_gap:{error.json_path}:{error.validator}" for error in errors
        )
    return value, list(dict.fromkeys(warnings))


def readable_input_warnings(warnings: list[str]) -> list[str]:
    """Project value-free diagnostics through the existing Artifact warnings."""
    output: list[str] = []
    for warning in warnings:
        code, _, detail = warning.partition(":")
        if code == "input_contract_gap":
            path, _, check = detail.rpartition(":")
            output.append(
                f"输入 {path} 存在缺口（{check}）；原始内容和缺失已保留，"
                "不能据此推断科研结论或执行许可。"
            )
        elif code == "preserved_unrecognized_input_field":
            output.append(
                f"输入 {detail} 不属于已声明字段；原文保留为上下文，不构成正式事实或执行许可。"
            )
        else:
            output.append(f"输入 {detail} 仅做含义唯一的表示归一；冻结原始输入未改写。")
    return list(dict.fromkeys(output))
