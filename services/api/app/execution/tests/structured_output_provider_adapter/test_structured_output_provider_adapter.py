from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from app.execution.structured_output_provider_adapter import (
    ProviderSchemaCapabilityError,
    apply_provider_schema_transport,
    structured_response_format,
)


def _closed_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Scientific choice",
        "type": "object",
        "additionalProperties": False,
        "required": ["selectedId", "rationale"],
        "properties": {
            "selectedId": {"type": "string", "enum": ["a", "b"]},
            "rationale": {"type": "string"},
        },
    }


def test_structured_response_format_selects_provider_native_mode() -> None:
    assert structured_response_format(
        "huiyan_chat_completions",
        schema_name="ignored",
    ) == {"type": "json_object"}
    assert structured_response_format(
        "openai_responses",
        schema_name="scientific_choice_v1",
    ) == {"type": "json_schema", "name": "scientific_choice_v1"}


def test_huiyan_keeps_json_mode_without_serializing_schema_to_provider() -> None:
    schema = _closed_schema()
    schema["properties"]["unresolved_inputs"] = {
        "type": "array",
        "items": {"type": "string"},
        "uniqueItems": True,
    }
    original_schema = deepcopy(schema)
    payload = {
        "model": "test-model",
        "messages": [
            {"role": "system", "content": "Return the requested JSON object."},
            {"role": "user", "content": "The complete schema is here."},
        ],
        "response_format": {"type": "json_object"},
    }

    adapted = apply_provider_schema_transport(
        "huiyan_chat_completions",
        payload=payload,
        response_format={"type": "json_object"},
        provider_schema=schema,
    )

    assert schema == original_schema
    assert adapted == payload
    assert "json_schema" not in adapted["response_format"]


def test_huiyan_accepts_open_nested_object_schema() -> None:
    schema = _closed_schema()
    schema["properties"]["comparison_criteria"] = {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    }

    adapted = apply_provider_schema_transport(
        "huiyan_chat_completions",
        payload={},
        response_format={"type": "json_object"},
        provider_schema=schema,
    )

    assert adapted["response_format"] == {"type": "json_object"}


def test_openai_responses_keeps_native_strict_schema_envelope() -> None:
    payload = {
        "model": "test-model",
        "input": [{"role": "user", "content": "SCIENTIFIC INPUT"}],
    }

    adapted = apply_provider_schema_transport(
        "openai_responses",
        payload=payload,
        response_format={"type": "json_schema", "name": "scientific_choice_v1"},
        provider_schema=_closed_schema(),
    )

    assert payload == {
        "model": "test-model",
        "input": [{"role": "user", "content": "SCIENTIFIC INPUT"}],
    }
    assert adapted["text"]["format"] == {
        "type": "json_schema",
        "name": "scientific_choice_v1",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["selectedId", "rationale"],
            "properties": {
                "selectedId": {"type": "string", "enum": ["a", "b"]},
                "rationale": {"type": "string"},
            },
        },
    }


@pytest.mark.parametrize("keyword", ["pattern", "uniqueItems"])
def test_openai_rejects_unproven_strict_constraints(keyword: str) -> None:
    schema = _closed_schema()
    if keyword == "pattern":
        schema["properties"]["rationale"][keyword] = "^[A-Z]+$"
    else:
        schema["properties"]["rationale"] = {
            "type": "array",
            "items": {"type": "string"},
            keyword: True,
        }

    with pytest.raises(ProviderSchemaCapabilityError, match=keyword):
        apply_provider_schema_transport(
            "openai_responses",
            payload={},
            response_format={"type": "json_schema", "name": "unsupported_v1"},
            provider_schema=schema,
        )


def test_openai_losslessly_lowers_disjoint_oneof_and_typed_const() -> None:
    schema = _closed_schema()
    schema["properties"]["selectedId"] = {"const": "a"}
    schema["properties"]["rationale"] = {
        "oneOf": [{"type": "string"}, {"type": "boolean"}],
    }

    adapted = apply_provider_schema_transport(
        "openai_responses",
        payload={},
        response_format={"type": "json_schema", "name": "lowered_v1"},
        provider_schema=schema,
    )
    transported = adapted["text"]["format"]["schema"]

    assert transported["properties"]["selectedId"] == {
        "const": "a",
        "type": "string",
    }
    assert transported["properties"]["rationale"] == {
        "anyOf": [{"type": "string"}, {"type": "boolean"}],
    }


def test_structured_response_format_admits_all_three_protocols() -> None:
    assert structured_response_format(
        "openai_chat_completions",
        schema_name="ignored",
    ) == {"type": "json_object"}
    assert structured_response_format(
        "anthropic_messages",
        schema_name="emit_v1",
    ) == {"type": "tool_use", "name": "emit_v1"}


def test_openai_chat_completions_keeps_json_mode_without_serializing_schema() -> None:
    payload = {
        "model": "test-model",
        "messages": [
            {"role": "system", "content": "SYS"},
            {"role": "user", "content": "USER"},
        ],
    }

    adapted = apply_provider_schema_transport(
        "openai_chat_completions",
        payload=payload,
        response_format={"type": "json_object"},
        provider_schema=_closed_schema(),
    )

    assert adapted["response_format"] == {"type": "json_object"}
    assert "schema" not in adapted["response_format"]
    assert payload["messages"][1]["content"] == "USER"


def test_anthropic_messages_uses_forced_tool_with_projected_schema() -> None:
    payload = {
        "model": "claude-sonnet-4-5",
        "system": "SYS",
        "max_tokens": 512,
        "messages": [{"role": "user", "content": "USER"}],
    }

    adapted = apply_provider_schema_transport(
        "anthropic_messages",
        payload=payload,
        response_format={"type": "tool_use", "name": "emit_v1"},
        provider_schema=_closed_schema(),
    )

    assert adapted["tool_choice"] == {"type": "tool", "name": "emit_v1"}
    assert len(adapted["tools"]) == 1
    tool = adapted["tools"][0]
    assert tool["name"] == "emit_v1"
    assert tool["input_schema"]["type"] == "object"
    assert tool["input_schema"]["required"] == ["selectedId", "rationale"]
    assert tool["input_schema"]["properties"]["selectedId"] == {
        "type": "string",
        "enum": ["a", "b"],
    }
    assert "response_format" not in adapted
    assert payload["messages"][0]["content"] == "USER"


def test_anthropic_messages_accepts_pattern_constraint_without_openai_strict_gate() -> None:
    schema = _closed_schema()
    schema["properties"]["rationale"] = {"type": "string", "pattern": "^[A-Za-z]+$"}

    adapted = apply_provider_schema_transport(
        "anthropic_messages",
        payload={"model": "m", "max_tokens": 8, "messages": [{"role": "user", "content": "u"}]},
        response_format={"type": "tool_use", "name": "emit_v1"},
        provider_schema=schema,
    )

    assert (
        adapted["tools"][0]["input_schema"]["properties"]["rationale"]["pattern"]
        == "^[A-Za-z]+$"
    )


def test_anthropic_messages_requires_tool_use_envelope() -> None:
    with pytest.raises(ValueError, match="tool_use"):
        apply_provider_schema_transport(
            "anthropic_messages",
            payload={"model": "m", "max_tokens": 8, "messages": []},
            response_format={"type": "json_object"},
            provider_schema=_closed_schema(),
        )
