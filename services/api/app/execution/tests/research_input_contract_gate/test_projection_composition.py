from __future__ import annotations

import copy
import json

import pytest
from app.execution.research_input_contract_gate import (
    CapabilityContractIdentity,
    InputContractGateFailure,
)
from app.execution.research_source_document import compose_source_document

IDENTITY = CapabilityContractIdentity("skill", "consumer", 1)
SCHEMA = {
    "type": "object",
    "required": ["context", "parameters", "count", "unknown"],
    "properties": {
        "context": {"type": "object"},
        "parameters": {"type": "array"},
        "count": {"type": "integer"},
        "unknown": {"type": "null"},
    },
    "additionalProperties": False,
}
VALUES = {"context": {"substrate": "Mo"}, "parameters": ["power"], "count": 10, "unknown": None}


def rows():
    return [
        {
            "artifactType": key,
            "sourceCapabilityCallId": "call-1",
            "mimeType": "application/json",
            "content": json.dumps(value).encode(),
        }
        for key, value in VALUES.items()
    ]


def test_complete_projection_preserves_object_array_scalar_and_null():
    assert compose_source_document(rows(), SCHEMA, IDENTITY) == VALUES


def test_whole_document_is_not_rewrapped():
    whole = {
        "mimeType": "application/json",
        "content": json.dumps(VALUES).encode(),
        "artifactType": "context",
    }
    assert compose_source_document([whole], SCHEMA, IDENTITY) == VALUES


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "duplicate",
        "different_call",
        "missing_call",
        "foreign_type",
        "mixed_whole",
        "invalid_bytes",
    ],
)
def test_bad_projection_fails_closed(mutation):
    sources = copy.deepcopy(rows())
    if mutation == "missing":
        sources.pop()
    elif mutation == "duplicate":
        sources[-1] = sources[0]
    elif mutation == "different_call":
        sources[-1]["sourceCapabilityCallId"] = "call-2"
    elif mutation == "missing_call":
        sources[-1].pop("sourceCapabilityCallId")
    elif mutation == "foreign_type":
        sources[-1]["artifactType"] = "foreign"
    elif mutation == "mixed_whole":
        sources.append({"mimeType": "application/json", "content": json.dumps(VALUES).encode()})
    elif mutation == "invalid_bytes":
        sources[0]["content"] = b"invalid"
    with pytest.raises(InputContractGateFailure):
        compose_source_document(sources, SCHEMA, IDENTITY)


def test_registered_entry_reassembles_and_validates_frozen_schemas():
    from app.execution.research_input_contract_gate import (
        SkillInputRequest,
        resolve_registered_skill_input,
    )

    source_schema = copy.deepcopy(SCHEMA)
    source_schema["properties"]["count"]["x-execution-critical"] = True
    target_schema = {
        "type": "object",
        "required": ["project"],
        "properties": {"project": source_schema},
    }
    snapshot = {
        "definitions": {
            "skills": [
                {
                    "skillId": "source",
                    "definitionVersion": 1,
                    "sourceContract": {
                        "frozenSchemas": {"output": {"schema": source_schema, "sha256": "0" * 64}}
                    },
                },
                {
                    "skillId": "consumer",
                    "definitionVersion": 1,
                    "sourceContract": {
                        "frozenSchemas": {"input": {"schema": target_schema, "sha256": "0" * 64}}
                    },
                },
            ]
        },
        "bindings": {
            "skills": [
                {
                    "bindingId": "source-binding",
                    "bindingVersion": 1,
                    "skillId": "source",
                    "skillDefinitionVersion": 1,
                    "enabled": True,
                }
            ]
        },
    }
    binding = {
        "bindingId": "consumer-binding",
        "bindingVersion": 1,
        "skillId": "consumer",
        "skillDefinitionVersion": 1,
        "enabled": True,
        "inputSources": {
            "contractVersion": 2,
            "fields": [
                {
                    "targetField": "project",
                    "sourcePresence": "required",
                    "source": {
                        "kind": "artifact",
                        "bindingId": "source-binding",
                        "bindingVersion": 1,
                        "field": "$",
                    },
                }
            ],
        },
    }
    materials = [
        {
            **row,
            "sourceCapabilityType": "skill",
            "sourceCapabilityId": "source",
            "sourceCapabilityDefinitionVersion": 1,
            "sourceBindingRefs": [{"bindingId": "source-binding", "bindingVersion": 1}],
        }
        for row in rows()
    ]
    request = SkillInputRequest(
        task_id="task", strategy="direct_skill", skill_id="consumer", definition_version=1
    )
    assert resolve_registered_skill_input(
        snapshot=snapshot,
        request=request,
        input_context={"artifacts": materials},
        skill_binding=binding,
    ) == {"project": VALUES}


def test_typed_single_whole_document_keeps_scientific_gap_at_registered_entry():
    from app.execution.research_input_contract_gate import resolve_registered_skill_input
    from app.execution.tests.research_input_contract_gate.test_research_input_contract_gate import (
        _artifact,
        _request,
        _snapshot,
        _target_binding,
    )

    source_schema = {
        "type": "object",
        "required": ["value"],
        "properties": {"value": {"type": "string"}},
    }
    target_schema = {"type": "object", "properties": {"source_payload": source_schema}}
    snapshot = _snapshot(target_input=target_schema, source_output=source_schema)
    body = {"value": None}
    source = {**_artifact(body), "artifactType": "value"}
    assert resolve_registered_skill_input(
        snapshot=snapshot,
        request=_request(),
        input_context={"artifacts": [source]},
        skill_binding=_target_binding(),
    ) == {"source_payload": body}


@pytest.mark.parametrize("body", [[], None, 10, "scalar"])
def test_single_non_object_keeps_original_invalid_source_rejection(body):
    from app.execution.research_input_contract_gate import resolve_registered_skill_input
    from app.execution.tests.research_input_contract_gate.test_research_input_contract_gate import (
        _artifact,
        _request,
        _snapshot,
        _target_binding,
    )

    snapshot = _snapshot(target_input={"type": "object"}, source_output={"type": "object"})
    with pytest.raises(InputContractGateFailure, match="input_composition_source_artifact_invalid"):
        resolve_registered_skill_input(
            snapshot=snapshot,
            request=_request(),
            input_context={"artifacts": [_artifact(body)]},
            skill_binding=_target_binding(),
        )
