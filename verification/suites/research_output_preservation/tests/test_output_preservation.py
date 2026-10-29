from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from app.execution import skill_contract_policy
from app.execution.research_input_contract_gate import (
    SkillInputRequest,
    resolve_registered_skill_input,
)


@pytest.fixture
def materialize():
    candidate_path = os.environ.get("T2_POLICY_CANDIDATE")
    if not candidate_path:
        return skill_contract_policy.materialize_semantic_output
    spec = importlib.util.spec_from_file_location("t2_candidate", Path(candidate_path))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.materialize_semantic_output


def _materialize(materialize, raw, *, limit=10000, mime="application/json", secrets=()):
    return materialize(
        raw,
        fallback_title="Research result",
        mime_type=mime,
        max_output_chars=limit,
        secret_values=secrets,
    )


@pytest.mark.parametrize("as_mapping", [False, True])
def test_preserves_body_records_limits_and_bad_items(materialize, as_mapping):
    document = {
        "title": "Research",
        "content": "Valid body",
        "measurements": [{"value": 1.2}],
        "limitations": ["temperature unknown"],
        "broken_item": {"value": None},
    }
    raw = document if as_mapping else json.dumps(document, indent=2)
    result = _materialize(materialize, raw)
    assert json.loads(result["content"]) == document
    if not as_mapping:
        assert result["content"] == raw
    assert "ignored_extra_semantic_fields" not in result["warnings"]


def test_preserves_unrecognized_fields_beside_summary(materialize):
    document = {"summary": "Evidence is incomplete", "observed_revision": None}
    result = _materialize(materialize, json.dumps(document))
    assert json.loads(result["content"]) == document


def test_long_json_remains_complete_and_parseable(materialize):
    document = {"records": [{"note": "x" * 300}], "unknown": None}
    result = _materialize(materialize, json.dumps(document), limit=80)
    assert json.loads(result["content"]) == document
    assert "output_contract_gap:$:max_output_chars" in result["warnings"]


@pytest.mark.parametrize("raw", [' {"records": [ ', '```json\n{"x": null}\n```', '"a scalar"'])
def test_retains_raw_content_without_repair_or_reinterpretation(materialize, raw):
    assert _materialize(materialize, raw)["content"] == raw


def test_redacts_supplied_secret_without_losing_other_fields(materialize):
    secret = "test-only-redaction-value-0123456789"
    document = {"content": secret, "limitations": ["pressure unknown"]}
    result = _materialize(materialize, json.dumps(document), secrets=(secret,))
    assert secret not in result["content"]
    assert json.loads(result["content"])["limitations"] == document["limitations"]


@pytest.mark.parametrize("mime", ["text/plain", "text/markdown", "text/yaml"])
def test_non_json_body_remains_readable_with_existing_title(materialize, mime):
    raw = json.dumps({"title": "Evidence", "content": "## Findings\n\nUnknown"})
    actual = _materialize(materialize, raw, mime=mime, limit=25)
    assert actual == {
        "title": "Evidence",
        "content": "## Findings\n\nUnknown",
        "warnings": [],
    }


def _machine_consume(content):
    source_schema = {
        "type": "object",
        "required": ["value"],
        "properties": {"value": {"type": "string"}},
        "additionalProperties": False,
    }
    source_binding = {
        "bindingId": "source-binding",
        "bindingVersion": 1,
        "skillId": "source",
        "skillDefinitionVersion": 1,
        "enabled": True,
    }
    snapshot = {
        "definitions": {
            "skills": [
                {
                    "skillId": "source",
                    "definitionVersion": 1,
                    "sourceContract": {
                        "frozenSchemas": {"output": {"sha256": "0" * 64, "schema": source_schema}},
                    },
                },
                {
                    "skillId": "target",
                    "definitionVersion": 1,
                    "sourceContract": {
                        "frozenSchemas": {
                            "input": {
                                "sha256": "0" * 64,
                                "schema": {
                                    "type": "object",
                                    "required": ["source_payload"],
                                    "properties": {"source_payload": source_schema},
                                },
                            }
                        },
                    },
                },
            ]
        },
        "bindings": {"skills": [source_binding]},
    }
    binding = {
        "skillId": "target",
        "skillDefinitionVersion": 1,
        "bindingId": "target-binding",
        "bindingVersion": 1,
        "enabled": True,
        "inputSources": {
            "contractVersion": 2,
            "fields": [
                {
                    "targetField": "source_payload",
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
    artifact = {
        "sourceCapabilityType": "skill",
        "sourceCapabilityId": "source",
        "sourceCapabilityDefinitionVersion": 1,
        "sourceBindingRefs": [{"bindingId": "source-binding", "bindingVersion": 1}],
        "mimeType": "application/json",
        "content": content.encode("utf-8"),
    }
    return resolve_registered_skill_input(
        snapshot=snapshot,
        request=SkillInputRequest(
            task_id="isolated-test",
            strategy="direct_skill",
            skill_id="target",
            definition_version=1,
        ),
        input_context={"artifacts": [artifact]},
        skill_binding=binding,
    )


@pytest.mark.parametrize("document", [{}])
def test_preserved_bad_json_cannot_become_machine_input(materialize, document):
    result = _materialize(materialize, json.dumps(document))
    assert json.loads(result["content"]) == document
    resolved = _machine_consume(result["content"])
    assert resolved == {"source_payload": document}
    from app.execution.skill_contract_policy import validate_action_input

    schema = {
        "type": "object",
        "required": ["source_payload"],
        "properties": {"source_payload": {"type": "object", "required": ["value"]}},
    }
    definition = {
        "sourceContract": {"frozenSchemas": {"input": {"sha256": "0" * 64, "schema": schema}}}
    }
    with pytest.raises(ValueError, match="input_contract_invalid"):
        validate_action_input(definition, resolved)


def test_valid_json_reaches_machine_consumer_without_changed_values(materialize):
    result = _materialize(materialize, '{"value": "verified input"}')
    assert _machine_consume(result["content"]) == {"source_payload": {"value": "verified input"}}


def test_legacy_composition_preserves_bad_type_as_research_material(materialize):
    result = _materialize(materialize, '{"value": 123}')
    assert _machine_consume(result["content"]) == {"source_payload": {"value": 123}}


@pytest.fixture
def action_provider(monkeypatch):
    from app.execution import research_dispatch_skill_provider as provider

    directory = os.environ.get("T2_DISPATCH_CANDIDATE")
    if not directory:
        return provider
    policy_spec = importlib.util.spec_from_file_location(
        "t2_action_policy", os.environ["T2_POLICY_CANDIDATE"]
    )
    assert policy_spec is not None and policy_spec.loader is not None
    policy = importlib.util.module_from_spec(policy_spec)
    policy_spec.loader.exec_module(policy)
    monkeypatch.setitem(sys.modules, "app.execution.skill_contract_policy", policy)
    spec = importlib.util.spec_from_file_location("t2_action_provider", directory)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


class _ReachedExecution(RuntimeError):
    pass


def _submit(action_provider, monkeypatch, *, parameters, external, schema):
    calls = []
    definition = {
        "sourceContract": {
            "frozenSchemas": {
                "input": {"schema": schema, "sha256": "0" * 64},
            }
        }
    }
    frozen = SimpleNamespace(
        requiresExternalAction=external,
        skill_id="isolated_action",
        logicalProviderId=None,
        modelProfile=None,
        resolvedExecutionPolicy=None,
        orchestrationPlan={
            "resolvedParameters": parameters,
            "instruction": "inspect",
            "stopConditions": [],
            "inputRefs": [],
        },
    )
    monkeypatch.setattr(
        action_provider, "frozen_skill_request_for_dispatch", lambda *a, **kw: frozen
    )

    def execute(request):
        calls.append(request["parameters"])
        raise _ReachedExecution

    prepared = action_provider.PreparedDirectSkillResearchDispatch(
        capabilities=None,
        skill_id="isolated_action",
        definition_version=1,
        snapshot_definition=definition,
        source_binding_refs=(),
        repository=None,
        runtime={},
        execute=execute,
    )
    request = SimpleNamespace(
        task_id="isolated",
        owner_id="owner",
        workspace_id="workspace",
        work_unit_id="work",
        idempotency_key="isolated",
        capability_call_id="call",
        execution_decision_id="decision",
        attempt_number=1,
    )
    return prepared, request, calls


_ACTION_SCHEMA = {
    "type": "object",
    "required": ["value"],
    "properties": {"value": {"type": ["string", "null"]}},
    "additionalProperties": False,
}


@pytest.mark.parametrize("parameters", [{}, {"value": 123}, {"value": "ok", "extra": True}])
def test_real_action_rejects_invalid_final_parameters_before_execution(
    action_provider, monkeypatch, parameters
):
    prepared, request, calls = _submit(
        action_provider, monkeypatch, parameters=parameters, external=True, schema=_ACTION_SCHEMA
    )
    with pytest.raises(
        action_provider.DispatchProviderKnownRejected, match="input_contract_invalid"
    ):
        prepared.submit(request)
    assert calls == []
    assert (
        prepared.snapshot_definition["sourceContract"]["frozenSchemas"]["input"]["schema"]
        == _ACTION_SCHEMA
    )


@pytest.mark.parametrize("parameters", [{"value": "verified"}, {"value": None}])
def test_real_action_passes_valid_frozen_parameters_without_inventing_values(
    action_provider, monkeypatch, parameters
):
    prepared, request, calls = _submit(
        action_provider, monkeypatch, parameters=parameters, external=True, schema=_ACTION_SCHEMA
    )
    with pytest.raises(_ReachedExecution):
        prepared.submit(request)
    assert calls == [parameters]


def test_pure_research_keeps_incomplete_and_wrong_type_material(action_provider, monkeypatch):
    parameters = {"value": 123, "unknown": None}
    prepared, request, calls = _submit(
        action_provider, monkeypatch, parameters=parameters, external=False, schema=_ACTION_SCHEMA
    )
    with pytest.raises(_ReachedExecution):
        prepared.submit(request)
    assert calls == [parameters]


@pytest.mark.parametrize("schema", [None, {"type": "invalid-schema-type"}])
def test_real_action_requires_a_valid_frozen_input_contract(action_provider, monkeypatch, schema):
    prepared, request, calls = _submit(
        action_provider, monkeypatch, parameters={"value": "ok"}, external=True, schema=schema
    )
    with pytest.raises(action_provider.DispatchProviderKnownRejected, match="input_contract_.*"):
        prepared.submit(request)
    assert calls == []
