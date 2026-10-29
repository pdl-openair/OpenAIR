from __future__ import annotations

from copy import deepcopy

from app.execution.durable_skill_runtime import (
    registered_skill_capability_contract,
    resolve_registered_durable_skill,
    resolve_registered_durable_skill_runtime,
)

from app.modules.registry_snapshot_repository import canonicalize_registry_value

def _frozen_definition(skill: dict) -> dict:
    input_contract, output_contract, judgment_support = (
        registered_skill_capability_contract(skill["sourceContract"])
    )
    definition = {
        "skillId": skill["skillId"],
        "definitionVersion": skill["definitionVersion"],
        "status": "ready",
        "enabled": skill["enabled"],
        "runtime": skill["runtime"],
        "skillPath": skill["skillPath"],
        "skillType": skill["skillType"],
        "riskPolicy": {"riskClass": None, "source": "canonical_registry_unspecified"},
        "sourceContract": deepcopy(skill["sourceContract"]),
        "inputContract": input_contract,
        "outputContract": output_contract,
        "judgmentSupport": judgment_support,
    }
    execution_method_roles = (
        skill["sourceContract"].get("execution_method_roles") or []
    )
    if execution_method_roles:
        definition["executionMethodRoles"] = list(execution_method_roles)
    return definition

def test_retrieval_matrix_runtime_accepts_set_like_substage_order() -> None:
    skill = resolve_registered_durable_skill("retrieval_matrix_generation")
    assert skill is not None
    frozen = _frozen_definition(skill)

    assert resolve_registered_durable_skill_runtime(
        "retrieval_matrix_generation", snapshot_definition=frozen
    ) is not None

    reversed_frozen = deepcopy(frozen)
    reversed_frozen["sourceContract"]["substages"] = list(
        reversed(reversed_frozen["sourceContract"]["substages"])
    )
    assert resolve_registered_durable_skill_runtime(
        "retrieval_matrix_generation", snapshot_definition=reversed_frozen
    ) is not None


def test_runtime_snapshot_preserves_execution_method_roles(
    monkeypatch,
) -> None:
    monkeypatch.setenv("LLM_PROVIDER_API_KEY", "test-key")
    monkeypatch.setenv("LLM_PROVIDER_BASE_URL", "https://provider.test")
    monkeypatch.setenv("LLM_PROVIDER_MODEL_TERRA", "test-terra")

    skill = resolve_registered_durable_skill("problem_framing_generation")
    assert skill is not None
    frozen = _frozen_definition(skill)
    assert frozen["executionMethodRoles"] == [
        "opentrajectory_research_record_establishment_v1:research_context_modeling"
    ]
    assert resolve_registered_durable_skill_runtime(
        "problem_framing_generation", snapshot_definition=frozen
    ) is not None

    mismatched = deepcopy(frozen)
    mismatched["executionMethodRoles"] = [
        "opentrajectory_research_record_establishment_v1:retrieval_strategy"
    ]
    assert resolve_registered_durable_skill_runtime(
        "problem_framing_generation", snapshot_definition=mismatched
    ) is None


def test_runtime_treats_execution_method_role_order_as_set_semantics() -> None:
    skill = resolve_registered_durable_skill("repository_revision_acquisition")
    assert skill is not None
    frozen = _frozen_definition(skill)
    assert len(frozen["executionMethodRoles"]) == 2
    reversed_roles = deepcopy(frozen)
    reversed_roles["executionMethodRoles"] = list(
        reversed(reversed_roles["executionMethodRoles"])
    )

    assert resolve_registered_durable_skill_runtime(
        "repository_revision_acquisition", snapshot_definition=reversed_roles
    ) is not None


def test_runtime_ignores_non_authority_metadata_but_rejects_schema_identity_change() -> None:
    skill = resolve_registered_durable_skill("retrieval_matrix_generation")
    assert skill is not None
    frozen = _frozen_definition(skill)
    metadata_only = deepcopy(frozen)
    metadata_only["displayName"] = "non-authority display metadata"
    metadata_only["sourceContract"]["derived_display"] = ["second", "first"]

    assert resolve_registered_durable_skill_runtime(
        "retrieval_matrix_generation", snapshot_definition=metadata_only
    ) is not None

    schema_conflict = deepcopy(frozen)
    schema_conflict["sourceContract"]["frozenSchemas"]["output"]["sha256"] = "f" * 64
    assert resolve_registered_durable_skill_runtime(
        "retrieval_matrix_generation", snapshot_definition=schema_conflict
    ) is None


def test_runtime_rejects_frozen_adapter_identity_conflict() -> None:
    skill = resolve_registered_durable_skill("retrieval_matrix_generation")
    assert skill is not None
    frozen = _frozen_definition(skill)
    frozen["sourceContract"]["runtime_contract"]["adapter_id"] = (
        "python_adapter:other_skill"
    )

    assert resolve_registered_durable_skill_runtime(
        "retrieval_matrix_generation", snapshot_definition=frozen
    ) is None

def test_registry_canonicalization_keeps_execution_composition_order_strict() -> None:
    first = {
        "sourceContract": {"substages": ["b", "a", "a"]},
        "executionComposition": {"requiredSkillRefs": ["one", "two"]},
    }
    set_like_equivalent = {
        "sourceContract": {"substages": ["a", "b"]},
        "executionComposition": {"requiredSkillRefs": ["one", "two"]},
    }
    reversed_composition = {
        "sourceContract": {"substages": ["a", "b"]},
        "executionComposition": {"requiredSkillRefs": ["two", "one"]},
    }

    assert canonicalize_registry_value(first) == canonicalize_registry_value(
        set_like_equivalent
    )
    assert canonicalize_registry_value(first) != canonicalize_registry_value(
        reversed_composition
    )
