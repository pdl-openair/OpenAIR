from __future__ import annotations

import json

import pytest
from app.execution.research_input_contract_gate import (
    ExecutionInputCandidate,
    InputContractGateFailure,
    SkillInputRequest,
    resolve_and_validate_execution_input,
    resolve_registered_skill_input,
)


def _frozen(schema: dict) -> dict:
    return {"sha256": "0" * 64, "schema": schema}


def _snapshot(*, target_input: dict, source_output: dict | None = None) -> dict:
    definitions = [
        {
            "skillId": "target_skill",
            "definitionVersion": 1,
            "sourceContract": {"frozenSchemas": {"input": _frozen(target_input)}},
        }
    ]
    bindings = []
    if source_output is not None:
        definitions.append(
            {
                "skillId": "source_skill",
                "definitionVersion": 1,
                "sourceContract": {"frozenSchemas": {"output": _frozen(source_output)}},
            }
        )
        bindings.append(
            {
                "bindingId": "bind:test:source:skill:source_skill",
                "bindingVersion": 1,
                "definitionId": "test:source",
                "definitionVersion": 1,
                "skillId": "source_skill",
                "skillDefinitionVersion": 1,
                "enabled": True,
            }
        )
    return {"definitions": {"skills": definitions, "agents": []}, "bindings": {"skills": bindings}}


def _target_binding(
    *, presence: str = "required", runtime_fields: list[dict] | None = None
) -> dict:
    row = {
        "bindingId": "bind:test:target:skill:target_skill",
        "bindingVersion": 1,
        "definitionId": "test:target",
        "definitionVersion": 1,
        "skillId": "target_skill",
        "skillDefinitionVersion": 1,
        "enabled": True,
        "inputSources": {
            "contractVersion": 2,
            "fields": [
                {
                    "targetField": "source_payload",
                    "sourcePresence": presence,
                    "source": {
                        "kind": "artifact",
                        "bindingId": "bind:test:source:skill:source_skill",
                        "bindingVersion": 1,
                        "field": "$",
                    },
                }
            ],
        },
    }
    if runtime_fields is not None:
        row["runtimeInputs"] = {"contractVersion": 2, "fields": runtime_fields}
    return row


def _request() -> SkillInputRequest:
    return SkillInputRequest(
        task_id="task-1", strategy="direct_skill", skill_id="target_skill", definition_version=1
    )


def _artifact(payload: dict, *, binding_id: str = "bind:test:source:skill:source_skill") -> dict:
    return {
        "sourceCapabilityType": "skill",
        "sourceCapabilityId": "source_skill",
        "sourceCapabilityDefinitionVersion": 1,
        "sourceBindingRefs": [{"bindingId": binding_id, "bindingVersion": 1}],
        "mimeType": "application/json",
        "content": json.dumps(payload).encode("utf-8"),
    }


def test_required_artifact_exact_delivery_and_missing_stays_unknown() -> None:
    source_schema = {
        "type": "object",
        "required": ["value"],
        "properties": {"value": {"type": "string"}},
        "additionalProperties": False,
    }
    target_schema = {
        "type": "object",
        "required": ["source_payload"],
        "properties": {"source_payload": source_schema},
        "additionalProperties": False,
    }
    snapshot = _snapshot(target_input=target_schema, source_output=source_schema)
    binding = _target_binding(presence="required")

    resolved = resolve_registered_skill_input(
        snapshot=snapshot,
        request=_request(),
        input_context={"artifacts": [_artifact({"value": "exact"})]},
        skill_binding=binding,
    )
    assert resolved == {"source_payload": {"value": "exact"}}

    assert resolve_registered_skill_input(
        snapshot=snapshot,
        request=_request(),
        input_context={"artifacts": []},
        skill_binding=binding,
    ) == {"source_payload": None}


def test_optional_artifact_absent_omits_field_but_wrong_lineage_still_fails() -> None:
    source_schema = {
        "type": "object",
        "required": ["value"],
        "properties": {"value": {"type": "string"}},
        "additionalProperties": False,
    }
    target_schema = {
        "type": "object",
        "properties": {"source_payload": source_schema},
        "additionalProperties": False,
    }
    snapshot = _snapshot(target_input=target_schema, source_output=source_schema)
    binding = _target_binding(presence="optional")

    resolved = resolve_registered_skill_input(
        snapshot=snapshot,
        request=_request(),
        input_context={"artifacts": []},
        skill_binding=binding,
    )
    assert resolved == {}

    with pytest.raises(InputContractGateFailure, match="input_composition_source_lineage_mismatch"):
        resolve_registered_skill_input(
            snapshot=snapshot,
            request=_request(),
            input_context={
                "artifacts": [
                    _artifact({"value": "wrong"}, binding_id="bind:test:other:skill:source_skill")
                ]
            },
            skill_binding=binding,
        )


def test_optional_artifact_applies_only_an_explicit_default() -> None:
    source_schema = {"type": "object"}
    target_schema = {
        "type": "object",
        "properties": {"source_payload": {"type": "array"}},
        "additionalProperties": False,
    }
    snapshot = _snapshot(target_input=target_schema, source_output=source_schema)
    binding = _target_binding(presence="optional")
    binding["inputSources"]["fields"][0]["defaultValue"] = []

    resolved = resolve_registered_skill_input(
        snapshot=snapshot,
        request=_request(),
        input_context={"artifacts": []},
        skill_binding=binding,
    )

    assert resolved == {"source_payload": []}


def test_complex_schema_default_is_decided_by_runtime_validation() -> None:
    source_schema = {"type": "object"}
    target_schema = {
        "type": "object",
        "required": ["source_payload"],
        "properties": {
            "source_payload": {
                "allOf": [
                    {"type": "object"},
                    {
                        "required": ["mode"],
                        "properties": {"mode": {"const": "core"}},
                    },
                ]
            }
        },
        "additionalProperties": False,
    }
    snapshot = _snapshot(target_input=target_schema, source_output=source_schema)
    binding = _target_binding(presence="optional")
    field = binding["inputSources"]["fields"][0]
    field["defaultValue"] = {"mode": "core"}

    assert resolve_registered_skill_input(
        snapshot=snapshot,
        request=_request(),
        input_context={"artifacts": []},
        skill_binding=binding,
    ) == {"source_payload": {"mode": "core"}}

    field["defaultValue"] = {"mode": "supplemental"}
    assert resolve_registered_skill_input(
        snapshot=snapshot,
        request=_request(),
        input_context={"artifacts": []},
        skill_binding=binding,
    ) == {"source_payload": {"mode": "supplemental"}}


def test_optional_artifact_ambiguity_stays_rejected_and_scientific_gap_is_preserved() -> None:
    source_schema = {
        "type": "object",
        "required": ["value"],
        "properties": {"value": {"type": "string"}},
        "additionalProperties": False,
    }
    target_schema = {
        "type": "object",
        "properties": {"source_payload": source_schema},
        "additionalProperties": False,
    }
    snapshot = _snapshot(target_input=target_schema, source_output=source_schema)
    binding = _target_binding(presence="optional")

    with pytest.raises(
        InputContractGateFailure, match="input_composition_source_artifact_ambiguous"
    ):
        resolve_registered_skill_input(
            snapshot=snapshot,
            request=_request(),
            input_context={"artifacts": [_artifact({"value": "a"}), _artifact({"value": "b"})]},
            skill_binding=binding,
        )

    resolved = resolve_registered_skill_input(
        snapshot=snapshot,
        request=_request(),
        input_context={"artifacts": [_artifact({"unexpected": True})]},
        skill_binding=binding,
    )
    assert resolved == {"source_payload": {"unexpected": True}}


def test_artifact_source_paths_apply_identical_one_and_many_semantics() -> None:
    source_schema = {
        "type": "object",
        "required": ["structured"],
        "properties": {
            "structured": {
                "type": "object",
                "required": ["value"],
                "properties": {"value": {"type": "string"}},
            },
        },
    }
    one_schema = {
        "type": "object",
        "required": ["source_payload"],
        "properties": {"source_payload": {"type": "string"}},
    }
    one_binding = _target_binding()
    one_binding["inputSources"]["fields"][0]["source"]["field"] = "structured.value"
    one_snapshot = _snapshot(target_input=one_schema, source_output=source_schema)

    assert resolve_registered_skill_input(
        snapshot=one_snapshot,
        request=_request(),
        input_context={"artifacts": [_artifact({"structured": {"value": "one"}})]},
        skill_binding=one_binding,
    ) == {"source_payload": "one"}

    many_schema = {
        "type": "object",
        "required": ["source_payloads"],
        "properties": {
            "source_payloads": {"type": "array", "items": {"type": "string"}},
        },
    }
    many_binding = _target_binding()
    field = many_binding["inputSources"]["fields"][0]
    field["targetField"] = "source_payloads"
    field["source"] = {
        **field["source"],
        "field": "structured.value",
        "cardinality": "many",
    }
    many_snapshot = _snapshot(target_input=many_schema, source_output=source_schema)

    assert resolve_registered_skill_input(
        snapshot=many_snapshot,
        request=_request(),
        input_context={
            "artifacts": [
                _artifact({"structured": {"value": "first"}}),
                _artifact({"structured": {"value": "second"}}),
            ]
        },
        skill_binding=many_binding,
    ) == {"source_payloads": ["first", "second"]}


def test_nested_artifact_source_path_preserves_an_empty_array() -> None:
    source_schema = {
        "type": "object",
        "required": ["structured_context"],
        "properties": {
            "structured_context": {
                "type": "object",
                "required": ["parameter_admission_judgments"],
                "properties": {
                    "parameter_admission_judgments": {
                        "type": "array",
                        "items": {"type": "object"},
                    }
                },
            }
        },
    }
    target_schema = {
        "type": "object",
        "required": ["source_payload"],
        "properties": {"source_payload": {"type": "array"}},
    }
    binding = _target_binding()
    binding["inputSources"]["fields"][0]["source"]["field"] = (
        "structured_context.parameter_admission_judgments"
    )

    resolved = resolve_registered_skill_input(
        snapshot=_snapshot(target_input=target_schema, source_output=source_schema),
        request=_request(),
        input_context={
            "artifacts": [_artifact({"structured_context": {"parameter_admission_judgments": []}})]
        },
        skill_binding=binding,
    )

    assert resolved == {"source_payload": []}


def test_runtime_inputs_are_exact_and_missing_invocation_context_fails() -> None:
    target_schema = {
        "type": "object",
        "required": ["experiment_ref", "task_id", "mode"],
        "properties": {
            "experiment_ref": {"type": "string"},
            "task_id": {"type": "string"},
            "mode": {"const": "initial"},
        },
        "additionalProperties": False,
    }
    snapshot = _snapshot(target_input=target_schema)
    binding = {
        "bindingId": "bind:test:target:skill:target_skill",
        "bindingVersion": 1,
        "definitionId": "test:target",
        "definitionVersion": 1,
        "skillId": "target_skill",
        "skillDefinitionVersion": 1,
        "enabled": True,
        "runtimeInputs": {
            "contractVersion": 2,
            "fields": [
                {
                    "targetField": "experiment_ref",
                    "source": {"kind": "invocationContext", "field": "experiment_ref"},
                },
                {
                    "targetField": "task_id",
                    "source": {"kind": "executionIdentity", "field": "task_id"},
                },
                {"targetField": "mode", "source": {"kind": "literal", "value": "initial"}},
            ],
        },
    }
    resolved = resolve_registered_skill_input(
        snapshot=snapshot,
        request=_request(),
        input_context={
            "artifacts": [],
            "executionIdentity": {"task_id": "task-1"},
            "invocationContext": {"experiment_ref": "exp-1"},
        },
        skill_binding=binding,
    )
    assert resolved == {"experiment_ref": "exp-1", "task_id": "task-1", "mode": "initial"}

    with pytest.raises(
        InputContractGateFailure, match="input_composition_invocation_context_missing"
    ):
        resolve_registered_skill_input(
            snapshot=snapshot,
            request=_request(),
            input_context={
                "artifacts": [],
                "executionIdentity": {"task_id": "task-1"},
                "invocationContext": {},
            },
            skill_binding=binding,
        )


def test_scientific_state_optional_absence_and_required_presence() -> None:
    target_schema = {
        "type": "object",
        "properties": {"facts": {"type": "object"}},
        "additionalProperties": False,
    }
    snapshot = _snapshot(target_input=target_schema)
    base = {
        "bindingId": "bind:test:target:skill:target_skill",
        "bindingVersion": 1,
        "definitionId": "test:target",
        "definitionVersion": 1,
        "skillId": "target_skill",
        "skillDefinitionVersion": 1,
        "enabled": True,
    }
    optional = {
        **base,
        "inputSources": {
            "contractVersion": 2,
            "fields": [
                {
                    "targetField": "facts",
                    "sourcePresence": "optional",
                    "source": {"kind": "scientificState", "field": "$"},
                }
            ],
        },
    }
    assert (
        resolve_registered_skill_input(
            snapshot=snapshot,
            request=_request(),
            input_context={"artifacts": [], "scientificStatePresent": False},
            skill_binding=optional,
        )
        == {}
    )

    required = {
        **base,
        "inputSources": {
            "contractVersion": 2,
            "fields": [
                {
                    "targetField": "facts",
                    "sourcePresence": "required",
                    "source": {"kind": "scientificState", "field": "$"},
                }
            ],
        },
    }
    assert resolve_registered_skill_input(
        snapshot=snapshot,
        request=_request(),
        input_context={"artifacts": [], "scientificStatePresent": False},
        skill_binding=required,
    ) == {"facts": None}
    required["inputSources"]["fields"][0]["source"]["field"] = "absent_fact"
    assert resolve_registered_skill_input(
        snapshot=snapshot,
        request=_request(),
        input_context={
            "artifacts": [],
            "scientificStatePresent": True,
            "scientificState": {"valid_fact": "kept"},
        },
        skill_binding=required,
    ) == {"facts": None}


def _agent_snapshot() -> dict:
    return {
        "definitions": {
            "skills": [],
            "agents": [
                {
                    "agentId": "general_research_agent",
                    "definitionVersion": 1,
                    "inputContract": {
                        "required": ["user_goal"],
                        "optional": ["input_memory_refs"],
                    },
                }
            ],
        },
        "bindings": {"skills": []},
    }


def _agent_candidate(*, input_memory_refs: tuple[str, ...]) -> ExecutionInputCandidate:
    return ExecutionInputCandidate(
        task_id="task-agent-goal",
        research_design_version=1,
        research_goal="Reproduce the reported result with traceable evidence.",
        execution_decision_id="decision-agent-goal",
        assignment_id="assignment-agent-goal",
        assignment_role="primary",
        strategy="general_research_agent",
        capability_kind="agent",
        capability_id="general_research_agent",
        definition_version=1,
        instruction="Establish the reproduction baseline.",
        input_artifact_refs=(),
        input_artifact_snapshots=(),
        input_memory_refs=input_memory_refs,
    )


def test_agent_maps_authorized_research_goal_to_user_goal() -> None:
    resolved = resolve_and_validate_execution_input(
        snapshot=_agent_snapshot(),
        candidate=_agent_candidate(input_memory_refs=("research_goal",)),
        input_context={},
    )

    assert resolved["inputMemoryRefs"] == ["research_goal"]
    assert resolved["resolvedInputMemory"] == {
        "user_goal": "Reproduce the reported result with traceable evidence."
    }


def test_agent_goal_mapping_preserves_legacy_user_goal_ref() -> None:
    resolved = resolve_and_validate_execution_input(
        snapshot=_agent_snapshot(),
        candidate=_agent_candidate(input_memory_refs=("user_goal",)),
        input_context={},
    )
    assert resolved["resolvedInputMemory"]["user_goal"] == (
        "Reproduce the reported result with traceable evidence."
    )


def test_agent_goal_mapping_still_fails_closed_without_research_goal_authority() -> None:
    with pytest.raises(InputContractGateFailure) as exc_info:
        resolve_and_validate_execution_input(
            snapshot=_agent_snapshot(),
            candidate=_agent_candidate(input_memory_refs=()),
            input_context={},
        )

    assert exc_info.value.code == "input_contract_invalid"
    assert exc_info.value.field_paths == ("/user_goal",)


def _reviewer_snapshot() -> dict:
    return {
        "definitions": {
            "skills": [],
            "agents": [
                {
                    "agentId": "result_reliability_audit",
                    "definitionVersion": 1,
                    "inputContract": {
                        "required": ["user_goal", "claim_or_result", "material_manifest"],
                    },
                    "reviewContract": {
                        "frozenArtifactContract": {
                            "sha256": "1" * 64,
                            "schema": {"type": "object"},
                        },
                    },
                }
            ],
        },
        "bindings": {"skills": []},
    }


def _reviewer_candidate() -> ExecutionInputCandidate:
    return ExecutionInputCandidate(
        task_id="task-review",
        research_design_version=1,
        research_goal="Review the produced result.",
        execution_decision_id="decision-review",
        assignment_id="assignment-review",
        assignment_role="reviewer",
        strategy="skill_composition",
        capability_kind="agent",
        capability_id="result_reliability_audit",
        definition_version=1,
        instruction="Review the supplied artifact against its frozen contract.",
        input_artifact_refs=("artifact-under-review",),
        input_artifact_snapshots=(),
        input_memory_refs=(),
    )


def test_reviewer_uses_review_contract_and_verified_artifact_materials() -> None:
    material = {"artifactId": "artifact-under-review", "content": b"{}"}

    resolved = resolve_and_validate_execution_input(
        snapshot=_reviewer_snapshot(),
        candidate=_reviewer_candidate(),
        input_context={"artifacts": [material]},
    )

    assert resolved["inputArtifactRefs"] == ["artifact-under-review"]
    assert resolved["inputArtifactMaterials"] == [material]
    assert resolved["resolvedInputMemory"] == {}


def test_reviewer_fails_closed_without_exact_verified_artifact_materials() -> None:
    with pytest.raises(InputContractGateFailure) as exc_info:
        resolve_and_validate_execution_input(
            snapshot=_reviewer_snapshot(),
            candidate=_reviewer_candidate(),
            input_context={"artifacts": []},
        )

    assert exc_info.value.code == "input_contract_invalid"
    assert exc_info.value.field_paths == ("/inputArtifactMaterials",)


def test_reviewer_agent_still_uses_research_input_contract_for_primary_work() -> None:
    reviewer = _reviewer_candidate()
    primary = ExecutionInputCandidate(**{**reviewer.__dict__, "assignment_role": "primary"})

    with pytest.raises(InputContractGateFailure) as exc_info:
        resolve_and_validate_execution_input(
            snapshot=_reviewer_snapshot(),
            candidate=primary,
            input_context={"artifacts": [{"artifactId": "artifact-under-review"}]},
        )

    assert exc_info.value.code == "input_contract_invalid"
    assert exc_info.value.field_paths == (
        "/user_goal",
        "/claim_or_result",
        "/material_manifest",
    )


@pytest.mark.parametrize(
    "kind", ["source_null", "wrong_type", "mixed_error", "runtime_null", "frozen_null"]
)
def test_only_validated_scientific_unknowns_are_material_unavailable(kind):
    from app.execution.research_input_contract_gate import validate_registered_skill_input_snapshot

    snapshot = _snapshot(
        target_input={
            "type": "object",
            "required": ["source_payload"],
            "properties": {"source_payload": {"type": "string"}},
            "additionalProperties": False,
        },
        source_output={"type": "object", "required": ["value"], "properties": {"value": {}}},
    )
    binding = _target_binding()
    binding["inputSources"]["fields"][0]["source"]["field"] = "value"
    if kind == "mixed_error":
        binding["runtimeInputs"] = {
            "contractVersion": 2,
            "fields": [
                {"targetField": "extra", "source": {"kind": "literal", "value": "unexpected"}},
            ],
        }
    if kind == "runtime_null":
        binding.pop("inputSources")
        binding["runtimeInputs"] = {
            "contractVersion": 2,
            "fields": [
                {
                    "targetField": "source_payload",
                    "source": {"kind": "invocationContext", "field": "seed"},
                },
            ],
        }
    value = [] if kind == "wrong_type" else None

    def resolve():
        if kind == "frozen_null":
            return validate_registered_skill_input_snapshot(
                snapshot=snapshot,
                request=_request(),
                resolved_input={"source_payload": None},
                skill_binding=binding,
            )
        return resolve_registered_skill_input(
            snapshot=snapshot,
            request=_request(),
            skill_binding=binding,
            input_context={
                "artifacts": [_artifact({"value": value})],
                "invocationContext": {"seed": None},
            },
        )

    if kind == "mixed_error":
        with pytest.raises(InputContractGateFailure) as caught:
            resolve()
        assert caught.value.code == "input_contract_invalid"
    else:
        assert resolve() == {"source_payload": value}


def test_many_source_arrays_keep_all_contributions_without_nested_arrays():
    source_schema = {"type": "object", "properties": {"records": {"type": "array"}}}
    target_schema = {"type": "object", "properties": {"source_payload": {"type": "array"}}}
    binding = _target_binding()
    source = binding["inputSources"]["fields"][0]["source"]
    source.update({"cardinality": "many", "field": "records"})
    assert resolve_registered_skill_input(
        snapshot=_snapshot(target_input=target_schema, source_output=source_schema),
        request=_request(),
        skill_binding=binding,
        input_context={
            "artifacts": [
                _artifact({"records": [{"id": "a"}]}),
                _artifact({"records": [{"id": "b"}]}),
            ]
        },
    ) == {"source_payload": [{"id": "a"}, {"id": "b"}]}
