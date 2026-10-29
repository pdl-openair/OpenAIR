from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from app.execution.python_adapter_skills import (
    PYTHON_ADAPTER_ENTRYPOINTS,
    resolve_python_adapter_skill,
)
from app.execution.research_input_contract_gate import (
    InputContractGateFailure,
    SkillInputRequest,
    resolve_registered_skill_input,
    validate_registered_skill_input_snapshot,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
SKILL_ROOT = REPO_ROOT / "skills" / "cs_research" / "resource-identity-acquisition"


def _registry_row() -> dict:
    rows = yaml.safe_load((REPO_ROOT / "skills" / "registry.yaml").read_text(encoding="utf-8"))["skills"]
    return next(row for row in rows if row["skill_id"] == "resource_identity_acquisition")


def _binding_row() -> dict:
    rows = yaml.safe_load((REPO_ROOT / "skills" / "skill-bindings.yaml").read_text(encoding="utf-8"))["bindings"]
    return next(
        row
        for row in rows
        if row["bindingId"] == "bind:sdr:reproducibility:skill:resource_identity_acquisition"
    )


def test_registry_binding_and_python_adapter_use_formal_contract() -> None:
    registry = _registry_row()
    assert registry["stages"] == ["solution_design"]
    assert registry["substages"] == ["reproducibility"]
    assert registry["packs"] == ["sdr"]
    assert registry["runtime"] == "python_adapter"
    assert registry["runtime_contract"] == {
        "kind": "external_adapter",
        "requires_external_action": True,
        "adapter_id": "python_adapter:resource_identity_acquisition",
        "evidence_contract_version": "external-action-evidence/v1",
    }
    assert resolve_python_adapter_skill("resource_identity_acquisition") == (
        "resource_identity_acquisition",
        "skills/cs_research/resource-identity-acquisition",
    )
    assert PYTHON_ADAPTER_ENTRYPOINTS["python_adapter:resource_identity_acquisition"] == (
        "adapter.resource_identity_adapter",
        "run_resource_identity_acquisition",
        "payload_v1",
    )

    binding = _binding_row()["inputSources"]
    fields = {row["targetField"]: row for row in binding["fields"]}
    assert binding["contractVersion"] == 2
    assert set(fields) == {
        "resource_kind",
        "repo_id",
        "repo_type",
        "revision",
        "filename",
        "expected_commit_sha",
        "expected_sha256",
    }
    required = {"resource_kind", "repo_id", "repo_type", "revision"}
    for name in required:
        assert fields[name]["sourcePresence"] == "required"
    for name in set(fields) - required:
        assert fields[name]["sourcePresence"] == "optional"
    assert fields["revision"]["source"] == {
        "kind": "scientificState",
        "field": "resource_revision",
    }
    assert fields["repo_id"]["source"] == {
        "kind": "scientificState",
        "field": "resource_repo_id",
    }


def _minimal_frozen_snapshot() -> tuple[dict, dict]:
    input_schema = json.loads((SKILL_ROOT / "input.schema.json").read_text(encoding="utf-8"))
    registry = _registry_row()
    binding = _binding_row()
    frozen_binding = {**binding, "enabled": True}
    snapshot = {
        "definitions": {
            "skills": [{
                "skillId": "resource_identity_acquisition",
                "definitionVersion": 1,
                "sourceContract": {
                    **registry,
                    "frozenSchemas": {"input": {"sha256": "0" * 64, "schema": input_schema}},
                },
            }],
            "agents": [],
        },
        "bindings": {"skills": [frozen_binding]},
    }
    return snapshot, frozen_binding


def test_formal_inputs_freeze_resource_identity_without_reusing_code_revision() -> None:
    snapshot, binding = _minimal_frozen_snapshot()
    request = SkillInputRequest(
        task_id="task-resource",
        strategy="direct_skill",
        skill_id="resource_identity_acquisition",
        definition_version=1,
    )
    state = {
        "repository": "https://github.com/example/project.git",
        "revision": "code-v1",
        "resource_kind": "huggingface_file",
        "resource_repo_id": "example/model",
        "resource_repo_type": "model",
        "resource_revision": "resource-v2",
        "resource_filename": "config.json",
    }
    resolved = resolve_registered_skill_input(
        snapshot=snapshot,
        request=request,
        input_context={"scientificStatePresent": True, "scientificState": state},
        skill_binding=binding,
    )
    assert resolved == {
        "resource_kind": "huggingface_file",
        "repo_id": "example/model",
        "repo_type": "model",
        "revision": "resource-v2",
        "filename": "config.json",
    }
    assert resolved["revision"] != state["revision"]
    assert validate_registered_skill_input_snapshot(
        snapshot=snapshot,
        request=request,
        resolved_input=resolved,
        skill_binding=binding,
    ) == resolved

    with pytest.raises(InputContractGateFailure) as caught:
        resolve_registered_skill_input(
            snapshot=snapshot,
            request=request,
            input_context={
                "scientificStatePresent": True,
                "scientificState": {key: value for key, value in state.items() if key != "resource_revision"},
            },
            skill_binding=binding,
        )
    assert caught.value.code == "input_composition_source_field_missing"
    assert caught.value.field_paths == ("resource_revision",)
