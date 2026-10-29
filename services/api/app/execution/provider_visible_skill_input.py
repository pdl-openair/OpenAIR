"""Deterministic model-visible views for durable reasoning Skills.

Canonical Skill parameters remain unchanged.  This module only projects the
copy that is sent to the Provider when a Skill explicitly opts in.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

SCIENTIFIC_MINIMAL_V1 = "scientific_minimal_v1"


def _llm_configuration(metadata: dict[str, Any]) -> dict[str, Any]:
    value = metadata.get("llm")
    return value if isinstance(value, dict) else {}


def resolve_provider_message_contract(metadata: dict[str, Any]) -> str | None:
    llm_cfg = _llm_configuration(metadata)
    contract = llm_cfg.get("provider_message_contract")
    if contract is None:
        return None
    if contract != SCIENTIFIC_MINIMAL_V1:
        raise ValueError("provider_message_contract_invalid")
    return contract


def _field_list(value: Any, *, code: str) -> list[str]:
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(field, str) or not field.strip() for field in value)
        or len(set(value)) != len(value)
    ):
        raise ValueError(code)
    return value


def _project_object_value(value: Any, fields: list[str]) -> Any:
    if not isinstance(value, dict) or value.get("sourceStatus") == "unparsed":
        return deepcopy(value)
    if not any(field in value for field in fields):
        return deepcopy(value)
    return {field: deepcopy(value[field]) for field in fields if field in value}


def _project_array_value(value: Any, fields: list[str]) -> Any:
    if not isinstance(value, list):
        return deepcopy(value)
    return [_project_object_value(item, fields) for item in value]


def _projection_fields(parameter_name: Any, spec: Any) -> list[str]:
    if not isinstance(parameter_name, str) or not parameter_name.strip():
        raise ValueError("provider_input_projection_invalid")
    if not isinstance(spec, dict) or set(spec) != {"fields"}:
        raise ValueError("provider_input_projection_invalid")
    return _field_list(spec.get("fields"), code="provider_input_projection_invalid")


def project_provider_parameters(
    metadata: dict[str, Any], parameters: dict[str, Any]
) -> dict[str, Any]:
    """Return a deterministic Provider-visible copy of canonical parameters."""
    llm_cfg = _llm_configuration(metadata)
    projection = llm_cfg.get("provider_input_projection")
    if projection is None:
        return parameters
    if not isinstance(projection, dict) or not projection:
        raise ValueError("provider_input_projection_invalid")

    allowed = {"omit_fields", "object_fields", "array_fields"}
    if not set(projection).issubset(allowed):
        raise ValueError("provider_input_projection_invalid")

    projected = deepcopy(parameters)

    omit_fields = projection.get("omit_fields")
    if omit_fields is not None:
        for field in _field_list(omit_fields, code="provider_input_projection_invalid"):
            projected.pop(field, None)

    object_fields = projection.get("object_fields")
    if object_fields is not None:
        if not isinstance(object_fields, dict) or not object_fields:
            raise ValueError("provider_input_projection_invalid")
        for parameter_name, spec in object_fields.items():
            fields = _projection_fields(parameter_name, spec)
            projected[parameter_name] = _project_object_value(
                parameters.get(parameter_name), fields
            )

    array_fields = projection.get("array_fields")
    if array_fields is not None:
        if not isinstance(array_fields, dict) or not array_fields:
            raise ValueError("provider_input_projection_invalid")
        for parameter_name, spec in array_fields.items():
            fields = _projection_fields(parameter_name, spec)
            projected[parameter_name] = _project_array_value(parameters.get(parameter_name), fields)

    return projected


def scientific_instruction_body(instructions: str) -> str:
    """Remove SKILL.md registration frontmatter, preserving the instruction body."""
    text = instructions.strip()
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return text
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            body = "\n".join(lines[index + 1 :]).strip()
            if not body:
                raise ValueError("provider_instruction_body_invalid")
            return body
    raise ValueError("provider_instruction_body_invalid")


def build_scientific_minimal_user_payload(
    *,
    instructions: str,
    scientific_input: dict[str, Any],
    output_schema: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the model-visible payload for an opted-in structured Skill."""
    payload = {
        "instructions": scientific_instruction_body(instructions),
        "scientificInput": deepcopy(scientific_input),
    }
    if output_schema is not None:
        from app.execution.scientific_output_fields import scientific_output_fields

        payload["scientificOutputFields"] = scientific_output_fields(output_schema)
        payload["outputInstruction"] = (
            "Use these exact field names, nesting and allowed values for the requested "
            "scientific judgments. Preserve unknowns and limitations; do not invent missing "
            "facts. Array alternatives describe allowed representations, not additional fields."
        )
    return payload
