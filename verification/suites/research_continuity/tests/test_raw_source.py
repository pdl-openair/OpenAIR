"""Formal binding and actual submission entry; isolated software controls."""

import importlib.util
import json
from pathlib import Path

import pytest
from app.execution.research_input_contract_gate import (
    InputContractGateFailure,
    resolve_registered_skill_input,
    validate_registered_skill_input_snapshot,
)
from app.execution.tests.research_input_contract_gate.test_research_input_contract_gate import (
    _artifact,
    _request,
    _snapshot,
    _target_binding,
)

SOURCE = {
    "type": "object",
    "required": ["value"],
    "properties": {"value": {"type": "string"}},
    "additionalProperties": False,
}
TARGET = {
    "type": "object",
    "required": ["source_payload"],
    "properties": {"source_payload": SOURCE},
    "additionalProperties": False,
}


def raw_input(text, mime, *, field="$", many=False):
    snapshot = _snapshot(target_input=TARGET, source_output=SOURCE)
    binding = _target_binding()
    source = binding["inputSources"]["fields"][0]["source"]
    source["field"] = field
    if many:
        source["cardinality"] = "many"
    artifact = _artifact({})
    artifact.update(mimeType=mime, content=text.encode("utf-8"), artifactId="software-raw")
    resolved = resolve_registered_skill_input(
        snapshot=snapshot,
        request=_request(),
        skill_binding=binding,
        input_context={"artifacts": [artifact]},
    )
    frozen = json.loads(json.dumps(resolved))
    assert (
        validate_registered_skill_input_snapshot(
            snapshot=snapshot, request=_request(), skill_binding=binding, resolved_input=frozen
        )
        == frozen
    )
    return frozen


@pytest.mark.parametrize(
    "text,mime",
    [
        ("## Research\n\nUnknown temperature; no experiment happened.", "text/markdown"),
        ("Incomplete evidence remains useful.", "text/plain"),
        (' {"value": [ ', "application/json"),
        ('["observed", null]', "application/json"),
    ],
)
@pytest.mark.parametrize("field", ["$", "value"])
def test_readable_unparsed_source_reaches_frozen_research_input(text, mime, field):
    document = raw_input(text, mime, field=field)["source_payload"]
    assert document["sourceStatus"] == "unparsed"
    assert document["requestedField"] == field
    assert document["sourceMaterials"][0]["content"] == text
    assert "value" not in document


def test_many_source_preserves_unparsed_text_and_valid_document_together():
    snapshot = _snapshot(target_input=TARGET, source_output=SOURCE)
    binding = _target_binding()
    binding["inputSources"]["fields"][0]["source"]["cardinality"] = "many"
    text = _artifact({})
    text.update(mimeType="text/plain", content=b"Not measured.", artifactId="a")
    valid = _artifact({"value": "actual evidence"})
    valid["artifactId"] = "b"
    resolved = resolve_registered_skill_input(
        snapshot=snapshot,
        request=_request(),
        skill_binding=binding,
        input_context={"artifacts": [valid, text]},
    )
    assert resolved["source_payload"][0]["sourceMaterials"][0]["content"] == "Not measured."
    assert resolved["source_payload"][1] == {"value": "actual evidence"}


@pytest.mark.parametrize("change", ["lineage", "binary", "bad_utf8"])
def test_identity_or_unreadable_source_still_refuses(change):
    artifact = _artifact({})
    artifact.update(mimeType="text/plain", content=b"raw report")
    if change == "lineage":
        artifact["sourceBindingRefs"][0]["bindingVersion"] = 2
    elif change == "binary":
        artifact["mimeType"] = "application/pdf"
    else:
        artifact["content"] = b"\xff"
    with pytest.raises(InputContractGateFailure):
        resolve_registered_skill_input(
            snapshot=_snapshot(target_input=TARGET, source_output=SOURCE),
            request=_request(),
            skill_binding=_target_binding(),
            input_context={"artifacts": [artifact]},
        )


def test_actual_submit_delivers_text_to_research_but_refuses_machine_action(monkeypatch):
    from app.execution import research_dispatch_skill_provider as provider

    path = Path(
        "verification/suites/research_output_preservation/tests/test_output_preservation.py"
    )
    spec = importlib.util.spec_from_file_location("raw_source_submission_control", path)
    control = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(control)
    parameters = raw_input("No machine value was obtained.", "text/plain")
    prepared, request, calls = control._submit(
        provider, monkeypatch, parameters=parameters, external=True, schema=TARGET
    )
    with pytest.raises(provider.DispatchProviderKnownRejected, match="input_contract_invalid"):
        prepared.submit(request)
    assert calls == []
    prepared, request, calls = control._submit(
        provider, monkeypatch, parameters=parameters, external=False, schema=TARGET
    )
    with pytest.raises(control._ReachedExecution):
        prepared.submit(request)
    assert calls == [parameters]


@pytest.mark.parametrize(
    "projection",
    [
        {"object_fields": {"source_payload": {"fields": ["value"]}}},
        {"array_fields": {"source_payload": {"fields": ["value"]}}},
    ],
)
def test_final_model_visible_payload_keeps_actual_raw_material(projection):
    from app.execution.provider_visible_skill_input import (
        build_scientific_minimal_user_payload,
        project_provider_parameters,
    )

    parameters = raw_input("Temperature unknown; no training occurred.", "text/plain")
    visible = project_provider_parameters(
        {"llm": {"provider_input_projection": projection}}, parameters
    )
    message = build_scientific_minimal_user_payload(
        instructions="Read actual evidence.", scientific_input=visible
    )
    assert message["scientificInput"] == parameters
    assert parameters["source_payload"]["sourceStatus"] == "unparsed"


def test_projection_preserves_unknown_and_mixed_records_without_changing_valid_projection():
    from app.execution.provider_visible_skill_input import project_provider_parameters

    opaque = raw_input("Not measured.", "text/plain")["source_payload"]
    metadata = {
        "llm": {
            "provider_input_projection": {
                "object_fields": {"unknown": {"fields": ["value"]}},
                "array_fields": {"records": {"fields": ["value"]}},
            }
        }
    }
    parameters = {"unknown": None, "records": [opaque, {"value": 7, "unused": 9}, None]}
    assert project_provider_parameters(metadata, parameters) == {
        "unknown": None,
        "records": [opaque, {"value": 7}, None],
    }
    assert parameters["records"][1] == {"value": 7, "unused": 9}


def test_same_production_multioutput_preserves_nested_raw_material_and_identity_boundary():
    source_schema = {
        "type": "object",
        "required": ["a", "b"],
        "properties": {"a": {"type": "object"}, "b": {"type": "object"}},
    }
    snapshot = _snapshot(target_input=TARGET, source_output=source_schema)
    binding = _target_binding()
    binding["inputSources"]["fields"][0]["source"]["field"] = "b.value"
    first = _artifact({"value": "actual evidence"})
    second = _artifact({})
    first.update(artifactType="a", sourceCapabilityCallId="same-production")
    second.update(
        artifactType="b",
        sourceCapabilityCallId="same-production",
        mimeType="text/plain",
        content=b"Still unknown.",
    )
    resolved = resolve_registered_skill_input(
        snapshot=snapshot,
        request=_request(),
        skill_binding=binding,
        input_context={"artifacts": [first, second]},
    )
    assert resolved["source_payload"]["requestedField"] == "b.value"
    assert resolved["source_payload"]["sourceMaterials"][0]["content"] == "Still unknown."
    second["sourceCapabilityCallId"] = "another-production"
    with pytest.raises(InputContractGateFailure, match="source_artifact_ambiguous"):
        resolve_registered_skill_input(
            snapshot=snapshot,
            request=_request(),
            skill_binding=binding,
            input_context={"artifacts": [first, second]},
        )


def test_no_matching_projection_field_does_not_erase_readable_json_context():
    from app.execution.provider_visible_skill_input import project_provider_parameters

    metadata = {
        "llm": {
            "provider_input_projection": {
                "object_fields": {"source_payload": {"fields": ["value"]}},
            }
        }
    }
    parameters = {
        "source_payload": {"content": "Measurements unavailable.", "unknowns": ["temperature"]}
    }
    assert project_provider_parameters(metadata, parameters) == parameters
