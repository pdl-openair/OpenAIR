from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import warnings

import pytest

from app.modules import registry_snapshot_repository as registry
from app.modules import research_orchestration_resolver as resolver
from app.modules.research_orchestration_resolver import (
    build_execution_registry_snapshot,
    build_planning_registry_snapshot,
    build_registry_snapshot,
)
from app.modules.yaml_contract_loader import load_unique_key_yaml


ROOT = Path(__file__).resolve().parents[3]
WORKFLOW_DIR = ROOT / "schemas" / "workflows"


def _workflow_definition_refs(version: int) -> tuple[tuple[str, int], ...]:
    suffix = "" if version == 1 else f"_v{version}"
    payload = load_unique_key_yaml(
        (WORKFLOW_DIR / f"cs_paper_reproduction_workflow{suffix}.yaml").read_text(
            encoding="utf-8"
        )
    )
    return tuple(
        (str(row["definitionId"]), int(row["definitionVersion"]))
        for row in payload["nodes"]
    )


def _binding_source_fields(binding_version: int, skill_id: str) -> dict[str, dict]:
    payload = load_unique_key_yaml(
        (ROOT / "skills" / "skill-bindings.yaml").read_text(encoding="utf-8")
    )
    row = next(
        row
        for row in payload["bindings"]
        if row["bindingVersion"] == binding_version and row["skillId"] == skill_id
    )
    return {
        str(field["targetField"]): field
        for field in row["inputSources"]["fields"]
    }


def _skill(skill_id: str, *, input_schema: dict | None = None, output_schema: dict | None = None) -> dict:
    frozen: dict[str, dict] = {}
    if input_schema is not None:
        frozen["input"] = {"sha256": "0" * 64, "schema": input_schema}
    if output_schema is not None:
        frozen["output"] = {"sha256": "1" * 64, "schema": output_schema}
    return {"skillId": skill_id, "sourceContract": {"frozenSchemas": frozen}}


def _validate(
    *,
    producer_output: dict,
    consumer_input: dict,
    source_path: str = "payload",
    target_path: str = "payload",
    source_presence: str = "required",
    cardinality: str = "one",
    producer_id: str = "pvd_producer_v2",
    consumer_id: str = "pvd_consumer_v1",
    admission: dict | list | None = None,
) -> None:
    source = {
        "kind": "artifact",
        "bindingId": f"bind:test:producer:skill:{producer_id}",
        "bindingVersion": 2,
        "field": source_path,
        **({"cardinality": cardinality} if cardinality != "one" else {}),
        **({"admission": admission} if admission is not None else {}),
    }
    registry._validate_artifact_handoff(
        consumer_binding={"bindingId": f"bind:test:consumer:skill:{consumer_id}"},
        consumer_skill=_skill(consumer_id, input_schema=consumer_input),
        source_binding={"bindingId": f"bind:test:producer:skill:{producer_id}"},
        producer_skill=_skill(producer_id, output_schema=producer_output),
        entry={
            "targetField": target_path,
            "sourcePresence": source_presence,
            "source": source,
        },
    )


def test_fresh_v6_execution_snapshot_admits_exact_reachable_binding_closure() -> None:
    refs = _workflow_definition_refs(6)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", registry.RegistrySnapshotAdmissionWarning)
        snapshot = build_execution_registry_snapshot(
            build_planning_registry_snapshot().envelope,
            required_definition_refs=refs,
        )

    selected = set(refs)
    bindings = snapshot.envelope["bindings"]["skills"]
    admitted_definitions = {
        (row["definitionId"], row["definitionVersion"])
        for row in bindings
    }
    admitted_binding_refs = {
        (row["bindingId"], row["bindingVersion"])
        for row in bindings
    }
    assert selected <= admitted_definitions
    assert admitted_definitions - selected == {
        ("cs_reproduction:deviation_diagnosis_validation", 3)
    }
    assert all(row["bindingVersion"] in {3, 4} for row in bindings)
    assert (
        "bind:cs_reproduction:deviation_diagnosis_validation:skill:"
        "reproduction_deviation_diagnosis_v2",
        3,
    ) in admitted_binding_refs
    assert all(
        (source["bindingId"], source["bindingVersion"])
        in admitted_binding_refs
        for row in bindings
        for field in (row.get("inputSources") or {}).get("fields", [])
        for source in [field.get("source") or {}]
        if source.get("kind") == "artifact"
    )

    active = _binding_source_fields(3, "exploratory_run")
    assert active["argv"]["sourcePresence"] == "conditional"
    assert active["resource_bindings"]["sourcePresence"] == "conditional"


def test_reachable_handoff_gap_is_diagnostic_without_blocking_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = resolver._build_registry_envelope()
    invalid = deepcopy(current)
    target = next(
        row
        for row in invalid["bindings"]["skills"]
        if row["bindingVersion"] == 3
        and row["skillId"] == "reproduction_deviation_diagnosis_v2"
    )
    source_field = next(
        row
        for row in target["inputSources"]["fields"]
        if row["targetField"] == "reproduction_baseline_and_criteria"
    )
    source_field["source"]["field"] = "missing_from_reachable_dependency"
    monkeypatch.setattr(resolver, "_build_registry_envelope", lambda: invalid)

    with pytest.warns(
        registry.RegistrySnapshotAdmissionWarning,
        match=(
            "consumerSkill=reproduction_deviation_diagnosis_v2.*"
            "sourcePath=missing_from_reachable_dependency.*"
            "conflict=source_path_missing"
        ),
    ):
        build_execution_registry_snapshot(
            build_planning_registry_snapshot().envelope,
            required_definition_refs=_workflow_definition_refs(6),
        )


def test_unrelated_legacy_v1_does_not_block_fresh_but_global_check_finds_it() -> None:
    legacy = _binding_source_fields(1, "exploratory_run")
    assert legacy["argv"]["sourcePresence"] == "required"
    assert legacy["resource_bindings"]["sourcePresence"] == "optional"

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", registry.RegistrySnapshotAdmissionWarning)
        fresh = build_execution_registry_snapshot(
            build_planning_registry_snapshot().envelope,
            required_definition_refs=_workflow_definition_refs(6),
        )
        global_snapshot = build_registry_snapshot()

    assert fresh.content_hash.startswith("sha256:")
    assert global_snapshot.content_hash.startswith("sha256:")


def test_handoff_content_difference_is_reported_with_ranges() -> None:
    producer = {
        "type": "object",
        "required": ["mode"],
        "properties": {"mode": {"enum": ["core", "supplemental"]}},
        "additionalProperties": False,
    }
    consumer = {
        "type": "object",
        "required": ["mode"],
        "properties": {"mode": {"const": "core"}},
        "additionalProperties": False,
    }

    with pytest.warns(registry.RegistrySnapshotAdmissionWarning) as caught:
        _validate(
            producer_output=producer,
            consumer_input=consumer,
            source_path="mode",
            target_path="mode",
        )

    message = str(caught[0].message)
    assert "producerSkill=pvd_producer_v2" in message
    assert "consumerSkill=pvd_consumer_v1" in message
    assert "conflict=const" in message
    assert "producerAllowed=values=[\"core\",\"supplemental\"]" in message
    assert "consumerAccepted=values=[\"core\"]" in message


def test_ready_only_binding_admission_narrows_enum_to_const() -> None:
    producer = {
        "type": "object",
        "required": ["status"],
        "properties": {"status": {"type": "string", "enum": ["ready", "blocked"]}},
        "additionalProperties": False,
    }
    consumer = {
        "type": "object",
        "required": ["design"],
        "properties": {
            "design": {
                "type": "object",
                "required": ["status"],
                "properties": {"status": {"const": "ready"}},
                "additionalProperties": True,
            }
        },
    }

    with pytest.warns(registry.RegistrySnapshotAdmissionWarning, match="conflict=property.status.const"):
        _validate(
            producer_output=producer,
            consumer_input=consumer,
            source_path="$",
            target_path="design",
        )

    _validate(
        producer_output=producer,
        consumer_input=consumer,
        source_path="$",
        target_path="design",
        admission={
            "kind": "json_field_equals",
            "path": ["status"],
            "equals": "ready",
        },
    )


def test_not_null_binding_admission_narrows_nullable_type() -> None:
    producer = {
        "type": "object",
        "required": [
            "plan_status",
            "working_directory",
            "timeout_seconds",
            "actual_metric_result_source",
        ],
        "properties": {
            "plan_status": {"type": "string", "enum": ["complete", "incomplete"]},
            "working_directory": {"type": ["string", "null"], "minLength": 1},
            "timeout_seconds": {"type": ["integer", "null"], "minimum": 1},
            "actual_metric_result_source": {"type": ["object", "null"]},
        },
        "additionalProperties": False,
    }
    consumer = {
        "type": "object",
        "required": ["facts"],
        "properties": {
            "facts": {
                "type": "object",
                "required": [
                    "plan_status",
                    "working_directory",
                    "timeout_seconds",
                    "actual_metric_result_source",
                ],
                "properties": {
                    "plan_status": {"const": "complete"},
                    "working_directory": {"type": "string", "minLength": 1},
                    "timeout_seconds": {"type": "integer", "minimum": 1},
                    "actual_metric_result_source": {"type": "object"},
                },
                "additionalProperties": True,
            }
        },
    }

    with pytest.warns(registry.RegistrySnapshotAdmissionWarning) as caught:
        _validate(
            producer_output=producer,
            consumer_input=consumer,
            source_path="$",
            target_path="facts",
        )
    assert "conflict=property." in str(caught[0].message)
    assert any(
        token in str(caught[0].message)
        for token in (
            "plan_status",
            "working_directory",
            "timeout_seconds",
            "actual_metric_result_source",
        )
    )

    _validate(
        producer_output=producer,
        consumer_input=consumer,
        source_path="$",
        target_path="facts",
        admission=[
            {"kind": "json_field_equals", "path": ["plan_status"], "equals": "complete"},
            {"kind": "json_field_not_null", "path": ["working_directory"]},
            {"kind": "json_field_not_null", "path": ["timeout_seconds"]},
            {"kind": "json_field_not_null", "path": ["actual_metric_result_source"]},
        ],
    )


def test_local_ref_and_many_cardinality_wrap_the_actual_handoff_type() -> None:
    producer = {
        "type": "object",
        "required": ["payload"],
        "properties": {"payload": {"$ref": "#/$defs/value"}},
        "$defs": {"value": {"type": "string"}},
    }
    consumer = {
        "type": "object",
        "required": ["payloads"],
        "properties": {"payloads": {"type": "array", "items": {"type": "string"}}},
    }

    _validate(
        producer_output=producer,
        consumer_input=consumer,
        source_path="payload",
        target_path="payloads",
        cardinality="many",
    )


def test_optional_source_for_required_target_is_diagnostic() -> None:
    producer = {"type": "object", "properties": {"payload": {"type": "string"}}}
    consumer = {
        "type": "object",
        "required": ["payload"],
        "properties": {"payload": {"type": "string"}},
    }

    with pytest.warns(registry.RegistrySnapshotAdmissionWarning, match="conflict=required"):
        _validate(producer_output=producer, consumer_input=consumer)


@pytest.mark.parametrize(
    ("producer", "consumer", "source_path", "target_path", "conflict"),
    [
        (
            {"type": "object", "properties": {}, "additionalProperties": False},
            {"type": "object", "properties": {"payload": {"type": "string"}}},
            "missing",
            "payload",
            "source_path_missing",
        ),
        (
            {"type": "object", "required": ["payload"], "properties": {"payload": {"type": "string"}}},
            {"type": "object", "properties": {}, "additionalProperties": False},
            "payload",
            "missing",
            "target_path_missing",
        ),
    ],
)
def test_missing_handoff_paths_are_diagnosed(
    producer: dict,
    consumer: dict,
    source_path: str,
    target_path: str,
    conflict: str,
) -> None:
    with pytest.warns(registry.RegistrySnapshotAdmissionWarning, match=f"conflict={conflict}"):
        _validate(
            producer_output=producer,
            consumer_input=consumer,
            source_path=source_path,
            target_path=target_path,
        )


def test_unreachable_private_defs_do_not_affect_admission() -> None:
    producer = {
        "type": "object",
        "required": ["payload"],
        "properties": {"payload": {"type": "string"}},
        "$defs": {"private": {"oneOf": [{"type": "string"}, {"type": "number"}]}},
    }
    consumer = {
        "type": "object",
        "required": ["payload"],
        "properties": {"payload": {"type": "string"}},
    }

    _validate(producer_output=producer, consumer_input=consumer)


def test_unsupported_combination_is_diagnostic_but_non_blocking() -> None:
    producer = {
        "type": "object",
        "required": ["payload"],
        "properties": {"payload": {"oneOf": [{"type": "string"}, {"type": "number"}]}},
    }
    consumer = {
        "type": "object",
        "required": ["payload"],
        "properties": {"payload": {"type": "string"}},
    }

    with pytest.warns(registry.RegistrySnapshotAdmissionWarning, match="unsupported_schema"):
        _validate(producer_output=producer, consumer_input=consumer)


def test_optional_default_gap_is_reported_without_blocking_runtime() -> None:
    consumer = {
        "type": "object",
        "properties": {"payload": {"type": "object"}},
        "allOf": [
            {
                "if": {"required": ["payload"]},
                "then": {"properties": {"payload": {"required": ["mode"]}}},
            }
        ],
    }
    entry = {
        "targetField": "payload",
        "sourcePresence": "optional",
        "defaultValue": None,
        "source": {
            "kind": "artifact",
            "bindingId": "bind:test:producer:skill:pvd_producer_v2",
            "bindingVersion": 2,
            "field": "payload",
        },
    }

    with pytest.warns(
        registry.RegistrySnapshotAdmissionWarning,
        match=r"conflict=default.type.*defaultValue=null",
    ):
        registry._validate_binding_default(
            consumer_binding={"bindingId": "bind:test:consumer:skill:pvd_consumer_v1"},
            consumer_skill=_skill("pvd_consumer_v1", input_schema=consumer),
            entry=entry,
        )


def test_complex_consumer_default_remains_unknown_and_non_blocking() -> None:
    consumer = {
        "type": "object",
        "required": ["payload"],
        "properties": {
            "payload": {
                "allOf": [
                    {"type": "object"},
                    {"required": ["mode"], "properties": {"mode": {"const": "core"}}},
                ]
            }
        },
    }
    entry = {
        "targetField": "payload",
        "sourcePresence": "optional",
        "defaultValue": {"mode": "core"},
        "source": {
            "kind": "artifact",
            "bindingId": "bind:test:producer:skill:pvd_producer_v2",
            "bindingVersion": 2,
            "field": "payload",
        },
    }

    with pytest.warns(
        registry.RegistrySnapshotAdmissionWarning,
        match="runtime payload validation required",
    ):
        result = registry._validate_binding_default(
            consumer_binding={"bindingId": "bind:test:consumer:skill:pvd_consumer_v1"},
            consumer_skill=_skill("pvd_consumer_v1", input_schema=consumer),
            entry=entry,
        )

    assert result == registry._DEFAULT_UNKNOWN


def test_many_array_sources_match_flat_runtime_projection() -> None:
    schema = {"type": "object", "required": ["payload"], "properties": {
        "payload": {"type": "array", "items": {"type": "string"}},
    }}
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", registry.RegistrySnapshotAdmissionWarning)
        _validate(producer_output=schema, consumer_input=schema, cardinality="many")
    assert not caught
