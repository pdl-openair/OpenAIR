from __future__ import annotations

import pytest
from app.execution.research_input_contract_gate import (
    InputContractGateFailure,
    SkillInputRequest,
    validate_registered_skill_input_snapshot,
)
from app.execution.research_source_document import compose_source_document
from test_output_preservation import _machine_consume


def _snapshot_contract():
    schema = {
        "type": "object",
        "required": ["source_payload", "runtime_id"],
        "properties": {
            "source_payload": {
                "type": "object",
                "required": ["value"],
                "properties": {"value": {"type": "string"}},
            },
            "runtime_id": {"type": "string"},
        },
    }
    request = SkillInputRequest(
        task_id="isolated-task", strategy="direct_skill", skill_id="target", definition_version=1
    )
    binding = {
        "skillId": "target",
        "skillDefinitionVersion": 1,
        "enabled": True,
        "bindingId": "target-binding",
        "bindingVersion": 1,
        "inputSources": {"fields": [{"targetField": "source_payload"}]},
        "runtimeInputs": {"fields": [{"targetField": "runtime_id"}]},
    }
    snapshot = {
        "definitions": {
            "skills": [
                {
                    "skillId": "target",
                    "definitionVersion": 1,
                    "sourceContract": {
                        "frozenSchemas": {"input": {"sha256": "0" * 64, "schema": schema}}
                    },
                }
            ]
        }
    }
    return snapshot, request, binding


def _validate(payload, *, wrong_version=False):
    snapshot, request, binding = _snapshot_contract()
    if wrong_version:
        binding["skillDefinitionVersion"] = 2
    return validate_registered_skill_input_snapshot(
        snapshot=snapshot, request=request, resolved_input=payload, skill_binding=binding
    )


def test_source_json_gap_preserves_original_fields_through_composition():
    assert _machine_consume("{}") == {"source_payload": {}}


@pytest.mark.parametrize("source", [{}, None, {"value": None}])
def test_frozen_scientific_gap_stays_uninvented(source):
    payload = {"source_payload": source, "runtime_id": "physical-call-1"}
    assert _validate(payload) == payload


def test_absent_scientific_field_does_not_reappear_during_snapshot_validation():
    payload = {"runtime_id": "physical-call-1"}
    assert _validate(payload) == payload
    assert "source_payload" not in payload


@pytest.mark.parametrize(
    "payload", [{}, {"source_payload": {}}, {"source_payload": {"value": "real"}}]
)
def test_required_runtime_field_still_rejects_missing_identity(payload):
    with pytest.raises(InputContractGateFailure, match="input_contract_invalid"):
        _validate(payload)


def test_binding_identity_is_not_part_of_scientific_relaxation():
    with pytest.raises(InputContractGateFailure):
        _validate({"source_payload": {}, "runtime_id": "physical-call-1"}, wrong_version=True)


def test_runtime_field_takes_precedence_over_scientific_label():
    snapshot, request, binding = _snapshot_contract()
    binding["inputSources"]["fields"].append({"targetField": "runtime_id"})
    with pytest.raises(InputContractGateFailure, match="input_contract_invalid"):
        validate_registered_skill_input_snapshot(
            snapshot=snapshot,
            request=request,
            resolved_input={"source_payload": {}},
            skill_binding=binding,
        )


def test_multi_output_document_preserves_partial_fields_and_shared_call_identity():
    _, request, _ = _snapshot_contract()
    sources = [
        {
            "artifactType": kind,
            "sourceCapabilityCallId": "original-call",
            "mimeType": "application/json",
            "content": content,
        }
        for kind, content in (("first", b"{}"), ("second", b'{"value": null}'))
    ]
    schema = {"required": ["first", "second"], "properties": {"first": {}, "second": {}}}
    assert compose_source_document(sources, schema, request.identity) == {
        "first": {},
        "second": {"value": None},
    }
    sources[1]["sourceCapabilityCallId"] = "another-call"
    with pytest.raises(InputContractGateFailure, match="source_artifact_ambiguous"):
        compose_source_document(sources, schema, request.identity)
