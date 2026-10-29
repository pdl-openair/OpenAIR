from __future__ import annotations

import json
from pathlib import Path
import sys

import jsonschema

ROOT = Path(__file__).resolve().parents[3]
for import_root in (ROOT / "services" / "api", ROOT / "packages" / "llm_core"):
    value = str(import_root)
    if value not in sys.path:
        sys.path.insert(0, value)

from app.execution.durable_skill_runtime import (
    resolve_registered_durable_skill,
    resolve_registered_durable_skill_runtime,
)


NODE4_OUTPUT_SCHEMA = (
    ROOT
    / "skills"
    / "opentrajectory"
    / "evidence-schema-design-skill"
    / "output.schema.json"
)


def test_node4_registry_publishes_structured_output_authority(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER_API_KEY", "test-key")
    monkeypatch.setenv("LLM_PROVIDER_BASE_URL", "https://example.invalid")
    monkeypatch.setenv("LLM_PROVIDER_MODEL", "gpt-5.6-terra")
    monkeypatch.setenv("LLM_PROVIDER_MODEL_TERRA", "gpt-5.6-terra")

    skill = resolve_registered_durable_skill("evidence_schema_design")
    assert skill is not None
    assert skill["sourceContract"]["structured_output"] == {
        "contract_version": 1,
        "guard_version": "p06c-v1",
    }

    runtime = resolve_registered_durable_skill_runtime("evidence_schema_design")
    assert runtime is not None
    assert runtime["runtimeKind"] == "llm_reasoning"
    assert runtime["artifactMimeType"] == "application/json"
    assert runtime["modelProfile"] == "terra"
    assert runtime["adapterId"] == "huiyan_chat_completions"


def test_node4_formal_schema_rejects_empty_object() -> None:
    schema = json.loads(NODE4_OUTPUT_SCHEMA.read_text(encoding="utf-8"))
    validator = jsonschema.Draft7Validator(schema)
    assert list(validator.iter_errors({}))


def test_node4_formal_schema_accepts_valid_blocked_result() -> None:
    schema = json.loads(NODE4_OUTPUT_SCHEMA.read_text(encoding="utf-8"))
    validator = jsonschema.Draft7Validator(schema)
    value = {
        "status": "blocked",
        "blocking_reason": "The minimum comparison basis is not uniquely defined.",
        "missing_minimum_information": ["comparable evaluation protocol"],
        "missing_comparison_dimensions": [],
        "design_warnings": [],
    }
    assert list(validator.iter_errors(value)) == []
