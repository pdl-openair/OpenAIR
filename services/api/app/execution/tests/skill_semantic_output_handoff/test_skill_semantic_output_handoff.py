"""Registered message handoff for Skill output instructions.

Captures the production Huiyan body and stops before network or database I/O.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from app.execution import durable_skill_runtime as runtime
from app.execution.provider_visible_skill_input import (
    scientific_instruction_body,
)
from app.execution.structured_output_provider_adapter import (
    apply_provider_schema_transport,
)
from app.modules.provider_transport_evidence import ProviderAttemptEvidenceError


QUERY = "在硅片上用磁控溅射沉积 AlN，功率和氮气流量未知。"

CONFIRMED = {
    "pvd_research_request_understanding": "response_schema",
    "pvd_evidence_retrieval_design": "provider.schema.json",
    "pvd_evidence_schema_design": "provider.schema.json",
    "pvd_experimental_feedback_round_analysis": "output.schema.json",
    "pvd_process_factor_candidate_experiment_analysis": "draft.schema.json",
    "ot_v3_evidence_assessment_synthesis": "supplied draft schema",
    "pvd_evidence_applicability_global_planning": "output.schema.json",
    "pvd_evidence_record_deep_verification": "output.schema.json",
    "pvd_evidence_applicability_final_synthesis": "output.schema.json",
    "pvd_evidence_node_extraction": "model-facing safe subset",
    "pvd_research_record_structuring": "shapes verbatim",
}

CHILD_SKILLS = (
    "pvd_evidence_applicability_global_planning",
    "pvd_evidence_record_deep_verification",
    "pvd_evidence_applicability_final_synthesis",
    "pvd_evidence_node_extraction",
    "pvd_research_record_structuring",
)


def _required(schema: dict[str, Any]) -> list[str]:
    required = schema.get("required")
    assert isinstance(required, list)
    return [item for item in required if isinstance(item, str)]


def _stub(schema: Any, *, depth: int = 0) -> Any:
    if not isinstance(schema, dict) or depth > 4:
        return "probe"
    if "const" in schema:
        return schema["const"]
    if isinstance(schema.get("enum"), list) and schema["enum"]:
        return schema["enum"][0]
    kind = schema.get("type")
    if isinstance(kind, list):
        kind = next((item for item in kind if item != "null"), kind[0])
    props = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    if kind == "object" or props:
        keys = _required(schema) if schema.get("required") else list(props)[:4]
        return {key: _stub(props.get(key, {"type": "string"}), depth=depth + 1) for key in keys}
    if kind == "array":
        return []
    if kind == "integer":
        return 1
    if kind == "number":
        return 1
    if kind == "boolean":
        return False
    return "probe"


def _skill(skill_id: str) -> dict[str, Any]:
    skill = runtime.resolve_registered_durable_skill_runtime(skill_id)
    assert skill is not None
    assert skill["runtimeKind"] == "llm_reasoning"
    return skill


def _instruction_body(skill: dict[str, Any]) -> str:
    path = runtime.repo_root() / str(skill["skillPath"]) / "SKILL.md"
    return scientific_instruction_body(path.read_text(encoding="utf-8"))


def _model_schema(skill: dict[str, Any]) -> dict[str, Any]:
    source = skill["sourceContract"]
    finalization = source.get("frozenFinalization")
    if isinstance(finalization, dict):
        schema = finalization["draftSchema"]["schema"]
    else:
        schema = source["frozenSchemas"]["output"]["schema"]
    assert isinstance(schema, dict)
    return schema


def _capture(
    monkeypatch: pytest.MonkeyPatch,
    skill_id: str,
    parameters: dict[str, Any],
    *,
    validate: bool,
) -> dict[str, Any]:
    captured: dict[str, bytes] = {}

    class _Repository:
        def begin(self, **kwargs: Any) -> None:
            body = kwargs.get("request_body")
            assert isinstance(body, (bytes, bytearray))
            captured["body"] = bytes(body)
            raise ProviderAttemptEvidenceError("audit_capture_no_io")

        def begin_sub_attempt(self, **kwargs: Any) -> None:
            self.begin(**kwargs)

    monkeypatch.setattr(runtime, "ProviderHttpAttemptRepository", _Repository)
    if not validate:
        monkeypatch.setattr(
            runtime,
            "_validate_structured_json",
            lambda value, *_args, **_kwargs: value,
        )
    result = runtime.execute_new_durable_llm_child(
        skill_id=skill_id,
        parameters=parameters,
        parent_capability_call_id="audit-parent-call",
        adapter_name="prompt_semantic_handoff",
    )
    assert result["failureClassification"] == "provider_attempt_evidence"
    assert result["firstMismatch"] == "audit_capture_no_io"
    payload = json.loads(captured["body"].decode("utf-8"))
    assert payload["response_format"] == {"type": "json_object"}
    serialized = captured["body"].decode("utf-8")
    assert '"$schema"' not in serialized
    assert "additionalProperties" not in serialized
    return payload


def _user_object(payload: dict[str, Any]) -> dict[str, Any]:
    user = next(message for message in payload["messages"] if message["role"] == "user")
    value = json.loads(user["content"])
    assert isinstance(value, dict)
    return value


def test_pvd_request_message_keeps_query_and_names_draft_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skill = _skill("pvd_research_request_understanding")
    body = _instruction_body(skill)
    payload = _capture(
        monkeypatch,
        "pvd_research_request_understanding",
        {"query": QUERY},
        validate=True,
    )
    user = _user_object(payload)
    assert set(user) == {"instructions", "scientificInput"}
    assert user["scientificInput"] == {"query": QUERY}
    assert user["instructions"] == body
    assert "response_schema" not in body
    for field in _required(_model_schema(skill)):
        assert field in body
    assert "没有关键未知时为空数组" in body
    assert "不编造 quote" in body
    transported = apply_provider_schema_transport(
        "huiyan_chat_completions",
        payload=payload,
        response_format=payload["response_format"],
        provider_schema=_model_schema(skill),
    )
    assert transported["response_format"] == {"type": "json_object"}
    assert "schema" not in transported["response_format"]


def test_confirmed_instructions_name_current_receiver_fields_without_schema_file() -> None:
    for skill_id, retired_claim in CONFIRMED.items():
        skill = _skill(skill_id)
        body = _instruction_body(skill)
        assert retired_claim not in body
        for field in _required(_model_schema(skill)):
            assert field in body, skill_id


def test_ot_assessment_registered_message_has_no_draft_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skill = _skill("ot_v3_evidence_assessment_synthesis")
    parameters = _stub(skill["sourceContract"]["frozenSchemas"]["input"]["schema"])
    assert isinstance(parameters, dict)
    payload = _capture(
        monkeypatch,
        "ot_v3_evidence_assessment_synthesis",
        parameters,
        validate=False,
    )
    user = _user_object(payload)
    assert "outputContract" in user
    assert "schema" not in user["outputContract"]
    instructions = user["selectedSkill"]["instructions"]
    assert "direct_assessment" in instructions
    assert "evidence_judgments" in instructions
    assert "supplied draft schema" not in instructions
    assert "Do not invent records, measurements, or quotes." in instructions


def test_pvd_child_calls_use_registered_minimal_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    applicability = (
        runtime.repo_root()
        / "skills/pvd/pvd-evidence-applicability-comparability-analysis/adapter/evidence_applicability_comparability.py"
    ).read_text(encoding="utf-8")
    literature = (
        runtime.repo_root()
        / "skills/pvd/pvd-literature-evidence-acquisition-closure/adapter/literature_evidence_closure.py"
    ).read_text(encoding="utf-8")
    assert "execute_new_durable_llm_child" in applicability
    assert "execute_new_durable_llm_child" in literature
    for skill_id in (
        "pvd_evidence_applicability_global_planning",
        "pvd_evidence_record_deep_verification",
        "pvd_evidence_applicability_final_synthesis",
    ):
        assert skill_id in applicability
    assert "pvd_evidence_node_extraction" in literature
    assert "pvd_research_record_structuring" in literature

    for skill_id in CHILD_SKILLS:
        skill = _skill(skill_id)
        parameters = _stub(skill["sourceContract"]["frozenSchemas"]["input"]["schema"])
        assert isinstance(parameters, dict)
        payload = _capture(monkeypatch, skill_id, parameters, validate=False)
        user = _user_object(payload)
        assert set(user) == {"instructions", "scientificInput"}
        body = _instruction_body(skill)
        assert user["instructions"] == body
        if skill_id in CONFIRMED:
            assert "output.schema.json" not in body
            for field in _required(_model_schema(skill)):
                assert field in body

    synthesis = _instruction_body(_skill("pvd_evidence_applicability_final_synthesis"))
    assert "empty means no positive authorization" in synthesis
    assert "direct_numeric" in synthesis
    deep = _instruction_body(_skill("pvd_evidence_record_deep_verification"))
    assert "Do not emit or decide `allowed_uses`" in deep


def test_problem_framing_keeps_named_draft_and_verbatim_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skill = _skill("problem_framing_generation")
    query = "倍率未知，不要假设。"
    payload = _capture(
        monkeypatch,
        "problem_framing_generation",
        {"user_query": query},
        validate=True,
    )
    user = _user_object(payload)
    assert user["scientificInput"] == {"user_query": query}
    body = user["instructions"]
    assert "intent_understanding" in body
    assert "constraints_criteria" in body
    for field in _required(_model_schema(skill)):
        assert field in body
