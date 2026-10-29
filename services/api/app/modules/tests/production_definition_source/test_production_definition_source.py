from __future__ import annotations

import subprocess

from app.modules import research_orchestration_resolver
from app.modules.production_definition_source import ProductionDefinitionSource
from app.modules.registry_snapshot_repository import InMemoryRegistrySnapshotRepository


def test_freeform_research_design_catalog_keeps_specialized_work_available_with_formal_scope() -> None:
    source = ProductionDefinitionSource(archive=InMemoryRegistrySnapshotRepository())

    catalog = source.planner_catalog_for_research_design(None)
    pvd_row = next(
        row for row in catalog if row["definitionId"] == "pvd:research_goal_equipment_constraints"
    )
    ope_row = next(
        row for row in catalog if row["definitionId"] == "ope:experiment_design"
    )

    assert pvd_row["planningApplicability"]["scopeKind"] == "specialized"
    assert "PVD" in pvd_row["planningApplicability"]["scopeLabel"]
    assert pvd_row["planningApplicability"]["whenToUse"]
    assert ope_row["planningApplicability"]["scopeKind"] == "specialized"
    assert source.definition_is_eligible_for_workflow_admission(
        "pvd:research_goal_equipment_constraints",
        None,
    ) is True


def test_production_definition_source_can_reopen_the_task_frozen_snapshot_without_creating_a_new_one() -> None:
    archive = InMemoryRegistrySnapshotRepository()
    original = ProductionDefinitionSource(archive=archive)
    ref = original.snapshot_ref()

    reopened = ProductionDefinitionSource(archive=archive, snapshot_ref=ref)

    assert reopened.snapshot_ref() == ref
    assert reopened.planner_catalog_for_research_design(None) == original.planner_catalog_for_research_design(None)


def test_production_definition_source_uses_packaged_schemas_without_git_metadata(
    monkeypatch,
) -> None:
    expected = ProductionDefinitionSource(
        archive=InMemoryRegistrySnapshotRepository()
    ).snapshot_ref()

    def no_git_metadata(*_args, **_kwargs):
        raise subprocess.CalledProcessError(128, ["git", "ls-files"])

    monkeypatch.setattr(research_orchestration_resolver.subprocess, "run", no_git_metadata)
    source = ProductionDefinitionSource(archive=InMemoryRegistrySnapshotRepository())

    assert source.snapshot_ref() == expected
    assert source.resolve("sdr:question_refinement") is not None


def _source(
    *,
    definitions: list[dict],
    agents: list[dict] | None = None,
    bindings: list[dict] | None = None,
):
    source = ProductionDefinitionSource.__new__(ProductionDefinitionSource)
    source._definitions = {str(row["definitionId"]): row for row in definitions}
    source._packs = {
        "sdr": {
            "key": "sdr",
            "planning_scope": "general",
            "label": "SDR",
            "description": "General research work",
            "when_to_use": "Use across domains when scientifically appropriate.",
        },
        "pvd": {
            "key": "pvd",
            "planning_scope": "specialized",
            "label": "PVD",
            "description": "PVD thin-film process research",
            "when_to_use": "Use for PVD process research.",
        },
    }
    source._agents = {
        (str(row["agentId"]), int(row["definitionVersion"])): row
        for row in (agents or [])
    }
    source._agent_bindings = tuple(bindings or ())
    return source


def _complete_definition() -> dict:
    return {
        "definitionId": "sdr:research_route_design",
        "definitionVersion": 1,
        "pack": "sdr",
        "stage": "solution_design",
        "substageKey": "research_route_design",
        "label": "研究路线设计",
        "description": "根据研究目标形成步骤、依赖、产物和验收边界。",
        "enabled": True,
        "instancePolicy": {"mode": "repeatable"},
        "inputContract": {
            "status": "known",
            "requiredInputRefs": {"status": "known", "values": ["research_understanding"]},
        },
        "outputContract": {
            "status": "known",
            "artifactTypes": {"status": "known", "values": ["research_route"]},
        },
        "acceptanceContract": {
            "status": "known",
            "criterionRefs": {"status": "known", "values": ["research_route_reviewer"]},
        },
        "externalActionContract": {
            "status": "known",
            "requiredActionKinds": ["external_compute"],
        },
    }


def test_resolve_exposes_substage_identity_for_workflow_validation() -> None:
    definition = _complete_definition()
    source = _source(definitions=[definition])

    resolved = source.resolve(definition["definitionId"])

    assert resolved is not None
    assert resolved.substage_key == "research_route_design"
    assert tuple(value.value for value in resolved.required_external_action_kinds) == (
        "external_compute",
    )
    assert resolved.external_action_contract_known is True


def test_resolve_keeps_missing_external_action_contract_unknown() -> None:
    definition = _complete_definition()
    definition.pop("externalActionContract")
    source = _source(definitions=[definition])

    resolved = source.resolve(definition["definitionId"])

    assert resolved is not None
    assert resolved.required_external_action_kinds == ()
    assert resolved.external_action_contract_known is False


def test_planner_catalog_does_not_depend_on_specialist_agent_availability() -> None:
    definition = _complete_definition()
    without_specialist = _source(definitions=[definition])
    with_specialist = _source(
        definitions=[definition],
        agents=[{
            "agentId": "route_specialist",
            "definitionVersion": 1,
            "enabled": True,
        }],
        bindings=[{
            "definitionId": definition["definitionId"],
            "definitionVersion": definition["definitionVersion"],
            "agentId": "route_specialist",
            "agentDefinitionVersion": 1,
            "supportedRole": "primary",
            "sourceAuthority": "agent_registry_mapping",
            "enabled": True,
        }],
    )

    assert without_specialist.planner_catalog_for_research_design(None) == with_specialist.planner_catalog_for_research_design(None)
    assert without_specialist.resolve(definition["definitionId"]) is not None


def test_planner_catalog_keeps_enabled_semantic_definition_without_inventing_contract_truth() -> None:
    incomplete = _complete_definition()
    incomplete["outputContract"] = {
        "status": "unknown",
        "artifactTypes": {"status": "unknown", "values": []},
    }
    source = _source(definitions=[incomplete])

    resolved = source.resolve(incomplete["definitionId"])
    assert resolved is not None
    assert resolved.required_input_refs == ("research_understanding",)
    assert resolved.artifact_types == ()
    assert resolved.criterion_refs == ("research_route_reviewer",)

def test_definition_contract_compatibility_alias_uses_evaluators_not_quality_rules() -> None:
    row = {
        "inputContract": {
            "status": "known",
            "requiredInputRefs": {"status": "known", "values": ["input_a"]},
        },
        "outputContract": {
            "status": "known",
            "artifactTypes": {"status": "known", "values": ["artifact_a"]},
        },
        "acceptanceContract": {
            "status": "known",
            "qualityRuleRefs": {"status": "known", "values": ["quality_rule_a"]},
            "judgmentRequirement": {"status": "known", "value": "domain_professional"},
            "evaluatorRefs": {"status": "known", "values": ["domain_reviewer"]},
            "deterministicValidatorRefs": {"status": "known", "values": []},
        },
    }
    source = object.__new__(ProductionDefinitionSource)

    contract, complete = source._definition_contract_state(row)

    assert contract["qualityRuleRefs"] == ("quality_rule_a",)
    assert contract["evaluatorRefs"] == ("domain_reviewer",)
    assert contract["criterionRefs"] == ("domain_reviewer",)
    assert complete is True
