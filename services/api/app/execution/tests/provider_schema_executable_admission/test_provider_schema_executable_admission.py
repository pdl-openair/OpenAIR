from __future__ import annotations

import hashlib
import json
from typing import Any

import pytest

from app.execution import durable_skill_runtime
from app.execution.provider_schema_projection import schema_for_provider_prompt


def _schema_sha256(schema: dict[str, Any]) -> str:
    payload = json.dumps(
        schema,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _skill(output_schema: dict[str, Any]) -> dict[str, Any]:
    input_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["source"],
        "properties": {"source": {"type": "string", "minLength": 1}},
    }
    return {
        "skillId": "synthetic_provider_schema_admission",
        "definitionVersion": 1,
        "status": "ready",
        "enabled": True,
        "durableExecutable": True,
        "runtime": "llm_structured",
        "skillType": "atomic",
        "skillPath": "skills/synthetic/provider-schema-admission",
        "sourceContract": {
            "runtime_contract": {
                "kind": "llm_reasoning",
                "requires_external_action": False,
                "adapter_id": "huiyan_chat_completions",
                "evidence_contract_version": "none",
            },
            "structured_output": {
                "contract_version": 1,
                "guard_version": durable_skill_runtime.OPENTRAJECTORY_STRUCTURED_GUARD_VERSION,
            },
            "frozenSchemas": {
                "input": {
                    "sha256": _schema_sha256(input_schema),
                    "schema": input_schema,
                },
                "output": {
                    "sha256": _schema_sha256(output_schema),
                    "schema": output_schema,
                },
            },
        },
    }


def _description_business_field_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "DescriptionBusinessField",
        "description": "document metadata",
        "type": "object",
        "additionalProperties": False,
        "required": ["description"],
        "properties": {
            "description": {
                "type": "string",
                "minLength": 1,
                "description": "field metadata",
            }
        },
    }


def _unique_items_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["items"],
        "properties": {
            "items": {
                "type": "array",
                "items": {"type": "string"},
                "uniqueItems": True,
            }
        },
    }


def _nested_unique_items_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["branch"],
        "properties": {
            "branch": {
                "type": "object",
                "additionalProperties": False,
                "required": ["tags"],
                "properties": {
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "uniqueItems": True,
                    }
                },
            }
        },
    }


def _unsupported_contains_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["items"],
        "properties": {
            "items": {
                "type": "array",
                "items": {"type": "string"},
                "contains": {"type": "string", "minLength": 1},
            }
        },
    }


def test_projection_preserves_business_property_named_description() -> None:
    projected = schema_for_provider_prompt(_description_business_field_schema())

    assert "description" not in projected
    assert projected["required"] == ["description"]
    assert "description" in projected["properties"]
    assert projected["properties"]["description"] == {
        "type": "string",
        "minLength": 1,
    }


def test_projection_preserves_business_names_and_literal_values() -> None:
    projected = schema_for_provider_prompt(
        {
            "type": "object",
            "dependencies": {
                "description": ["other"],
                "title": {
                    "type": "object",
                    "description": "dependency schema metadata",
                    "required": ["description"],
                    "properties": {
                        "description": {
                            "type": "string",
                            "description": "field metadata",
                        }
                    },
                },
            },
            "dependentRequired": {"description": ["other"]},
            "const": {"description": "literal business value"},
            "enum": [{"title": "literal title"}],
        }
    )

    assert projected["dependencies"] == {
        "description": ["other"],
        "title": {
            "type": "object",
            "required": ["description"],
            "properties": {"description": {"type": "string"}},
        },
    }
    assert projected["dependentRequired"] == {"description": ["other"]}
    assert projected["const"] == {"description": "literal business value"}
    assert projected["enum"] == [{"title": "literal title"}]


def test_admission_accepts_lossless_business_description_schema() -> None:
    assert durable_skill_runtime.registered_durable_skill_provider_schema_executable(
        _skill(_description_business_field_schema()),
        adapter_id="huiyan_chat_completions",
    ) is True


def test_admission_accepts_huiyan_unique_items_schema() -> None:
    assert durable_skill_runtime.registered_durable_skill_provider_schema_executable(
        _skill(_unique_items_schema()),
        adapter_id="huiyan_chat_completions",
    ) is True


def test_admission_accepts_nested_huiyan_unique_items_schema() -> None:
    assert durable_skill_runtime.registered_durable_skill_provider_schema_executable(
        _skill(_nested_unique_items_schema()),
        adapter_id="huiyan_chat_completions",
    ) is True


def test_huiyan_json_mode_accepts_schema_constraints_enforced_locally() -> None:
    assert durable_skill_runtime.registered_durable_skill_provider_schema_executable(
        _skill(_unsupported_contains_schema()),
        adapter_id="huiyan_chat_completions",
    ) is True


def test_ot_source_screening_unique_items_no_longer_blocks_admission() -> None:
    skill = durable_skill_runtime.resolve_registered_durable_skill(
        "opentrajectory_source_screening_selection"
    )
    assert skill is not None
    source = skill.get("sourceContract")
    assert isinstance(source, dict)
    frozen_finalization = source.get("frozenFinalization")
    assert isinstance(frozen_finalization, dict)
    draft_schema = frozen_finalization.get("draftSchema", {}).get("schema")
    assert isinstance(draft_schema, dict)

    encoded = json.dumps(draft_schema, ensure_ascii=False, sort_keys=True)
    assert "uniqueItems" in encoded
    judgments = draft_schema["properties"]["judgments"]["items"]["properties"]
    assert judgments["evidence_roles"]["uniqueItems"] is True
    assert judgments["relevant_target_ids"]["uniqueItems"] is True

    assert durable_skill_runtime.registered_durable_skill_provider_schema_executable(
        skill,
        adapter_id="huiyan_chat_completions",
    ) is True


def test_python_adapter_with_structured_output_is_not_killed_by_llm_transport() -> None:
    """Anti-landmine: non-LLM adapters must not be judged by LLM Provider transport.

    OT Fresh hit capability_unavailable because source_acquisition publishes
    structured_output while executing via python_adapter. Provider Schema
    admission belongs only to huiyan/openai_responses wire transports.
    """
    skill = _skill(_description_business_field_schema())
    skill["sourceContract"]["runtime_contract"] = {
        "kind": "external_adapter",
        "requires_external_action": True,
        "adapter_id": "python_adapter:synthetic_source_acquisition",
        "evidence_contract_version": "external-action-evidence/v1",
    }

    assert durable_skill_runtime.registered_durable_skill_provider_schema_executable(
        skill,
        adapter_id="python_adapter:synthetic_source_acquisition",
    ) is True
    # Even an otherwise-unsupported schema must not fail closed for non-LLM adapters.
    assert durable_skill_runtime.registered_durable_skill_provider_schema_executable(
        _skill(_unsupported_contains_schema()),
        adapter_id="python_adapter:synthetic_source_acquisition",
    ) is True


@pytest.mark.parametrize(
    "skill_id",
    [
        "opentrajectory_source_acquisition",
        "deepxiv_progressive_reading",
        "cs_paper_source_resolution",
        "pvd_literature_evidence_acquisition_closure",
    ],
)
def test_three_lane_python_adapters_are_not_blocked_by_provider_schema_admission(
    skill_id: str,
) -> None:
    skill = durable_skill_runtime.resolve_registered_durable_skill(skill_id)
    assert skill is not None, skill_id
    runtime = durable_skill_runtime.resolve_registered_durable_skill_runtime(
        skill_id,
        definition_version=int(skill.get("definitionVersion") or 1),
    )
    assert runtime is not None, skill_id
    adapter_id = str(runtime.get("adapterId") or "")
    assert adapter_id.startswith("python_adapter:"), adapter_id
    assert durable_skill_runtime.registered_durable_skill_provider_schema_executable(
        skill,
        adapter_id=adapter_id,
    ) is True


@pytest.mark.parametrize(
    "skill_id",
    [
        "execution_plan_interpretation",
        "reproduction_experiment_design",
    ],
)
def test_previously_blocked_three_lane_skills_pass_admission(skill_id: str) -> None:
    skill = durable_skill_runtime.resolve_registered_durable_skill(skill_id)
    assert skill is not None, skill_id
    assert skill["status"] == "ready"
    assert durable_skill_runtime.registered_durable_skill_provider_schema_executable(
        skill,
        adapter_id="huiyan_chat_completions",
    ) is True


def test_every_ready_huiyan_reasoning_skill_passes_provider_schema_preflight() -> None:
    failures: list[str] = []
    checked: list[str] = []
    for skill in durable_skill_runtime.list_registered_durable_skills():
        runtime_contract = skill.get("runtimeContract")
        if (
            skill.get("status") != "ready"
            or not skill.get("enabled")
            or not skill.get("durableExecutable")
            or not isinstance(runtime_contract, dict)
            or runtime_contract.get("kind") != "llm_reasoning"
            or runtime_contract.get("adapter_id") != "huiyan_chat_completions"
        ):
            continue
        skill_id = str(skill.get("skillId") or "")
        checked.append(skill_id)
        if not durable_skill_runtime.registered_durable_skill_provider_schema_executable(
            skill,
            adapter_id="huiyan_chat_completions",
        ):
            failures.append(skill_id)

    assert checked
    assert failures == []


@pytest.mark.parametrize(
    "skill_id",
    [
        "opentrajectory_source_screening_selection",
        "cs_reproduction_input_structuring",
        "ot_v3_evidence_assessment_synthesis",
        "evidence_node_extraction",
    ],
)
def test_unique_items_only_skills_become_executable_after_capability_truth_fix(
    skill_id: str,
) -> None:
    skill = durable_skill_runtime.resolve_registered_durable_skill(skill_id)
    assert skill is not None, skill_id
    assert skill["status"] == "ready"
    assert durable_skill_runtime.registered_durable_skill_provider_schema_executable(
        skill,
        adapter_id="huiyan_chat_completions",
    ) is True


def test_ot_evidence_schema_design_is_executable_after_projection_fix() -> None:
    skill = durable_skill_runtime.resolve_registered_durable_skill("evidence_schema_design")
    assert skill is not None
    assert durable_skill_runtime.registered_durable_skill_provider_schema_executable(
        skill,
        adapter_id="huiyan_chat_completions",
    ) is True


def test_retrieval_matrix_generation_is_executable_on_huiyan() -> None:
    skill = durable_skill_runtime.resolve_registered_durable_skill(
        "retrieval_matrix_generation"
    )
    assert skill is not None
    assert durable_skill_runtime.registered_durable_skill_provider_schema_executable(
        skill,
        adapter_id="huiyan_chat_completions",
    ) is True
