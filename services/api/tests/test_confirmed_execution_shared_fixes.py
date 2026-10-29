import os

import pytest
from app.execution.provider_visible_skill_input import build_scientific_minimal_user_payload
from app.execution.scientific_input_fidelity import validate_runtime_json
from app.modules.skill_binding_source import SkillBindingSourceError, load_skill_binding_source_rows
from app.modules.yaml_source_cache import read_yaml_source


def test_yaml_source_refreshes_same_size_same_mtime_and_does_not_share_mutations(tmp_path):
    path = tmp_path / "source.yaml"
    path.write_text("value: old\n", encoding="utf-8")
    stamp = path.stat()
    first = read_yaml_source(path)
    first["value"] = "mutated"
    assert read_yaml_source(path) == {"value": "old"}
    path.write_text("value: new\n", encoding="utf-8")
    os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    assert read_yaml_source(path) == {"value": "new"}


def test_binding_fast_loader_keeps_duplicate_key_rejection(tmp_path):
    path = tmp_path / "bindings.yaml"
    path.write_text("version: 2\nversion: 2\nbindings: []\n", encoding="utf-8")
    with pytest.raises(SkillBindingSourceError, match="duplicate_yaml_key"):
        load_skill_binding_source_rows(path)


def test_minimal_scientific_request_explains_nested_fields_and_preserves_inputs():
    schema = {
        "type": "object",
        "required": ["risks"],
        "properties": {
            "risks": {
                "type": "array",
                "maxItems": 6,
                "items": {
                    "$ref": "#/$defs/risk",
                },
            },
        },
        "$defs": {
            "risk": {
                "type": "object",
                "required": ["state"],
                "properties": {"state": {"enum": ["unknown", "confirmed"]}},
            }
        },
    }
    inputs = {"measured_value": None, "conditions": "unknown"}
    payload = build_scientific_minimal_user_payload(
        instructions="Preserve unknowns.",
        scientific_input=inputs,
        output_schema=schema,
    )
    fields = {row["field"]: row for row in payload["scientificOutputFields"]}
    assert fields["risks"]["maximumItems"] == 6
    assert fields["risks[].state"]["allowedValues"] == ["unknown", "confirmed"]
    assert payload["scientificInput"] == inputs
    assert "$defs" not in str(payload)


def test_output_structure_gaps_keep_all_science_and_unknowns():
    original = {"conclusion": "unknown", "valuable_extra": ["condition A", "condition B"]}
    warnings = []
    result = validate_runtime_json(
        original,
        {
            "type": "object",
            "required": ["missing_judgment"],
            "properties": {"conclusion": {"enum": ["supported"]}},
            "additionalProperties": False,
        },
        "scientific output",
        role="output",
        input_warnings=warnings,
    )
    assert result is original
    assert result == {"conclusion": "unknown", "valuable_extra": ["condition A", "condition B"]}
    assert len(warnings) == 3
    assert "missing_judgment" not in result


def test_schema_admission_refreshes_after_content_changes():
    from jsonschema.exceptions import SchemaError
    from app.modules.schema_source_cache import validate_schema_document

    schema = {"type": "object"}
    validate_schema_document(schema)
    schema["type"] = "unknown_type"
    with pytest.raises(SchemaError):
        validate_schema_document(schema)
    validate_schema_document({"type": "object"})


def test_cross_instance_continuation_uses_code_dispatch_without_model_parent(monkeypatch):
    from types import SimpleNamespace

    from app.modules import research_dispatch_service as module
    from app.modules import provider_transport_evidence
    from app.modules.research_orchestration_repository import DispatchContinuationFence

    calls = []
    commit = object()
    fence = DispatchContinuationFence("dispatch", "task", "decision", "work", "owner", "workspace", "worker", 1)
    directive = SimpleNamespace(action="start", next_instance_id=None, selection_request_id="next", expected_task_revision=9)
    service = module.ResearchDispatchService(
        SimpleNamespace(prepare_execution_continuation=lambda **kwargs: directive),
        decision_coordinator=SimpleNamespace(start_next=lambda **kwargs: calls.append(kwargs) or commit),
    )
    monkeypatch.setattr(module, "prepare_continuation_preconditions", lambda *args, **kwargs: (False, False))
    monkeypatch.setattr(service, "_release_ready_assignments_after_result", lambda *args, **kwargs: None)
    monkeypatch.setattr(service, "_system_reconcile_external_result", lambda *args, **kwargs: False)
    monkeypatch.setattr(service, "_successor_execution_proof_from_commit", lambda *args, **kwargs: kwargs["commit"])

    def forbidden(**kwargs):
        raise AssertionError("code dispatch must not construct a model invocation")

    monkeypatch.setattr(provider_transport_evidence, "provider_execution_parent", forbidden)
    assert service.process_continuation(fence) is commit
    assert calls == [{"task_id": "task", "owner_id": "owner", "workspace_id": "workspace", "selection_request_id": "next", "expected_task_revision": 9}]
