from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[6]
MODULE_PATH = ROOT / "services/api/app/modules/registry_snapshot_repository.py"


def _load_module(monkeypatch):
    external_action = types.ModuleType("app.models.external_action")
    external_action.EXTERNAL_ACTION_KIND_VALUES = set()
    models = types.ModuleType("app.models")
    models.__path__ = []  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "app.models", models)
    monkeypatch.setitem(sys.modules, "app.models.external_action", external_action)

    finalization = types.ModuleType("app.execution.skill_finalization_runtime")
    finalization.validate_finalization_declaration = lambda value: value
    finalization.validate_frozen_finalization_contract = lambda value: value
    monkeypatch.setitem(sys.modules, "app.execution.skill_finalization_runtime", finalization)

    conditional = types.ModuleType("app.modules.conditional_capability_binding")
    conditional.validate_conditional_policy = lambda value: value
    monkeypatch.setitem(sys.modules, "app.modules.conditional_capability_binding", conditional)
    spec = importlib.util.spec_from_file_location("registry_snapshot_repository_under_test", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_all_declared_binding_v2_sources_and_runtime_inputs_validate(monkeypatch) -> None:
    module = _load_module(monkeypatch)
    payload = yaml.safe_load((ROOT / "skills/skill-bindings.yaml").read_text(encoding="utf-8"))
    assert payload["version"] == 2
    for binding in payload["bindings"]:
        if "inputSources" in binding:
            module._validate_skill_binding_input_sources(binding["inputSources"])
        if "runtimeInputs" in binding:
            module._validate_skill_binding_runtime_inputs(binding["runtimeInputs"], scientific_targets={field["targetField"] for field in binding.get("inputSources", {}).get("fields", [])})


def test_scientific_sources_require_source_presence_and_reject_mapper_fields(monkeypatch) -> None:
    module = _load_module(monkeypatch)
    invalid = {
        "contractVersion": 2,
        "fields": [{
            "targetField": "candidates",
            "sourcePresence": "optional",
            "source": {
                "kind": "artifact",
                "bindingId": "bind:opentrajectory:source:skill:producer",
                "bindingVersion": 1,
                "field": "items",
                "normalizer": "semantic_projection_v1",
            },
        }],
    }
    with pytest.raises(module.RegistrySnapshotIntegrityError):
        module._validate_skill_binding_input_sources(invalid)


def test_scientific_source_contract_rejects_runtime_kinds(monkeypatch) -> None:
    module = _load_module(monkeypatch)
    with pytest.raises(module.RegistrySnapshotIntegrityError):
        module._validate_skill_binding_input_sources({
            "contractVersion": 2,
            "fields": [{
                "targetField": "experiment_ref",
                "sourcePresence": "required",
                "source": {"kind": "invocationContext", "field": "experiment_ref"},
            }],
        })


def test_runtime_inputs_are_separate_and_domain_neutral(monkeypatch) -> None:
    module = _load_module(monkeypatch)
    module._validate_skill_binding_runtime_inputs({
        "contractVersion": 2,
        "fields": [
            {"targetField": "task_id", "source": {"kind": "executionIdentity", "field": "task_id"}},
            {"targetField": "experiment_ref", "source": {"kind": "invocationContext", "field": "experiment_ref"}},
            {"targetField": "mode", "source": {"kind": "literal", "value": "initial"}},
        ],
    }, scientific_targets=set())
