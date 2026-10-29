from __future__ import annotations

import json

import pytest

from app.execution import durable_skill_runtime
from app.execution.skill_contract_policy import (
    apply_semantic_contract,
    materialize_semantic_output,
    normalize_semantic_input,
)


_MARKDOWN_CONTRACT = {
    "artifact": "skills/evidence_extraction/result.md",
    "mimeType": "text/markdown",
    "template": "# Result\n\n## Findings\n\n## Evidence\n\n## Next Steps\n",
}

_SEMANTIC_INPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["query"],
    "properties": {
        "query": {"type": "string", "minLength": 1},
        "description": {"type": "string"},
        "rationale": {"type": "string"},
        "priority": {
            "type": "string",
            "enum": ["high", "medium", "low"],
            "default": "medium",
        },
        "notes": {"type": "string"},
    },
}

_EXECUTION_INPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["artifact_id", "timeout_seconds"],
    "properties": {
        "artifact_id": {
            "type": "string",
            "minLength": 1,
            "x-execution-critical": True,
        },
        "timeout_seconds": {
            "type": "integer",
            "minimum": 1,
            "x-execution-critical": True,
        },
        "query": {"type": "string"},
    },
}


def _parse_markdown(raw: str | dict) -> dict:
    if isinstance(raw, dict):
        content = json.dumps(raw, ensure_ascii=False)
    else:
        content = raw
    return durable_skill_runtime._parse_huiyan_artifact(
        {"choices": [{"message": {"content": content}}]},
        skill_id="evidence_extraction",
        max_output_chars=20_000,
        output_contract=_MARKDOWN_CONTRACT,
        forbidden_values=[],
    )






def test_markdown_non_template_headings_are_accepted() -> None:
    artifact = _parse_markdown(
        "# 证据摘录\n\n## 主要发现\n\n硫化物固态电解质仍是核心体系。\n"
    )
    assert "硫化物固态电解质仍是核心体系" in artifact["content"]
    assert artifact["content"].strip()


def test_fenced_markdown_is_normalized() -> None:
    artifact = _parse_markdown("```markdown\n# Evidence\n\nReadable scientific summary.\n```")
    assert artifact["content"].startswith("# Evidence")
    assert "```" not in artifact["content"]
    assert "Readable scientific summary" in artifact["content"]


def test_html_comments_are_stripped_without_rejecting() -> None:
    artifact = _parse_markdown(
        "<!-- template: do not copy -->\n# Findings\n\nInterface stability remains the bottleneck.\n"
    )
    assert "<!--" not in artifact["content"]
    assert "Interface stability remains the bottleneck" in artifact["content"]


def test_json_extra_keys_are_accepted() -> None:
    artifact = _parse_markdown(
        {
            "title": "Evidence extraction",
            "content": "Ion conductivity comparison is preserved.",
            "extra": "llm-metadata",
            "model_notes": "ignore me",
        }
    )
    assert artifact["title"] == "Evidence extraction"
    assert "Ion conductivity comparison is preserved." in artifact["content"]
    assert "ignored_extra_semantic_fields" in artifact["warnings"]


def test_missing_title_with_non_empty_content_uses_fallback_title() -> None:
    artifact = _parse_markdown({"content": "Non-empty scientific body."})
    assert artifact["title"]
    assert artifact["title"] != "content"
    assert "Non-empty scientific body." in artifact["content"]
    assert "fallback_title" in artifact["warnings"]


def test_plain_non_empty_text_materializes_artifact() -> None:
    artifact = _parse_markdown("Plain readable research notes about sulfide electrolytes.")
    assert artifact["content"].strip()
    assert "sulfide electrolytes" in artifact["content"]


def test_partial_semantic_json_is_normalized() -> None:
    artifact = materialize_semantic_output(
        {
            "findings": "A remains more conductive than B.",
            "confidence": 0.4,
        },
        fallback_title="evidence extraction",
        mime_type="text/markdown",
        max_output_chars=10_000,
    )
    assert "A remains more conductive than B." in artifact["content"]
    assert artifact["content"].strip()
    assert artifact["title"] == "evidence extraction"






def test_optional_field_missing_stays_missing() -> None:
    normalized, warnings = normalize_semantic_input(
        {"query": "sulfide solid electrolyte"},
        _SEMANTIC_INPUT_SCHEMA,
    )
    assert normalized["query"] == "sulfide solid electrolyte"
    assert "description" not in normalized or normalized.get("description") in {"", None}
    assert "priority" not in normalized


def test_extra_field_is_preserved() -> None:
    normalized, warnings = apply_semantic_contract(
        {
            "query": "sulfide solid electrolyte",
            "llm_trace": "drop-me",
        },
        _SEMANTIC_INPUT_SCHEMA,
        role="input",
    )
    assert normalized["query"] == "sulfide solid electrolyte"
    assert normalized["llm_trace"] == "drop-me"
    assert not warnings


def test_enum_value_is_preserved() -> None:
    normalized, warnings = apply_semantic_contract(
        {"query": "sulfide solid electrolyte", "priority": "HIGH"},
        _SEMANTIC_INPUT_SCHEMA,
        role="input",
    )
    assert normalized["priority"] == "HIGH"
    assert not warnings


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        ("abc", "abc"),
    ],
)
def test_anyof_valid_branch_value_is_preserved_exactly(value: object, expected: object) -> None:
    normalized, _warnings = normalize_semantic_input(
        value,
        {"anyOf": [{"type": "string", "minLength": 1}, {"type": "null"}]},
    )
    assert normalized == expected


def test_anyof_non_first_valid_branch_is_preserved_exactly() -> None:
    normalized, _warnings = normalize_semantic_input(
        42,
        {"anyOf": [{"type": "string"}, {"type": "integer"}]},
    )
    assert normalized == 42


def test_oneof_non_first_valid_branch_is_preserved_exactly() -> None:
    normalized, _warnings = normalize_semantic_input(
        42,
        {"oneOf": [{"type": "string", "minLength": 1}, {"type": "integer"}]},
    )
    assert normalized == 42




def test_multi_type_union_valid_null_is_preserved_exactly() -> None:
    normalized, _warnings = normalize_semantic_input(
        None,
        {"type": ["object", "null"]},
    )
    assert normalized is None


def test_union_value_is_preserved_without_normalization() -> None:
    normalized, warnings = normalize_semantic_input(
        "42",
        {"anyOf": [{"type": "integer"}, {"type": "string", "enum": ["high"]}]},
    )
    assert normalized == "42"
    assert not warnings
