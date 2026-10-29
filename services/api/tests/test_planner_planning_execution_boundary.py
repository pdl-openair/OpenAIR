"""Planner slice 1: scientific planning and execution freeze handoff.

No live Provider is called. Core snapshot tests are deterministic; the production
entry-point tests require the API's normal locked dependencies.
"""
from __future__ import annotations

import ast
from copy import deepcopy
from datetime import datetime, timezone
import importlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from app.modules.registry_snapshot_repository import (
    InMemoryRegistrySnapshotRepository,
    RegistrySnapshotIntegrityError,
    execution_snapshot_matches_planning,
    parse_spec_document,
    planning_envelope_from_execution,
    registry_envelope_ref,
    snapshot_from_envelope,
)
from app.modules.research_design_freeform_semantics import (
    freeform_scientific_brief_prompts,
    freeform_scientific_brief_schema,
)
from app.modules.research_design_workflow_semantics import (
    workflow_semantic_brief_prompts,
    workflow_semantic_delta_prompts,
)
from app.modules.research_planning_stage_framework import research_stage_framework

ROOT = Path(__file__).resolve().parents[3]


def _full_envelope() -> dict:
    contract = parse_spec_document(
        "sdr", "specs/example.md",
        "# Evidence synthesis\n\n## Input\n\n- user_goal\n\n"
        "## Output\n\n- evidence_summary\n\n## Acceptance\n\n- deterministic_contract\n",
    )
    return {
        "schemaVersion": 1,
        "definitions": {
            "packs": [{"pack": "sdr", "definitionVersion": 1, "source": {"planning_scope": "general"}}],
            "substages": [{
                "definitionId": "sdr:evidence_synthesis", "definitionVersion": 1,
                "pack": "sdr", "stage": "insight_generation", "substageKey": "evidence_synthesis",
                "enabled": True, "label": "Evidence synthesis", "description": "Answer the research question.",
                **contract,
            }],
            "skills": [{
                "skillId": "evidence_synthesis_skill", "definitionVersion": 1, "status": "ready", "enabled": True,
                "runtime": "python_adapter", "skillPath": "skills/test/evidence-synthesis", "skillType": "atomic",
                "riskPolicy": {"riskClass": "low", "source": "test"}, "sourceContract": {},
                "inputContract": {"status": "known", "acceptedInputRefs": ["user_goal"]},
                "outputContract": {"status": "known", "artifactTypes": ["evidence_summary"]},
                "judgmentSupport": {"deterministic": True, "source": "test_contract"},
            }],
            "agents": [{
                "agentId": "general-research", "definitionVersion": 1,
                "agentKind": "general_research", "candidateScope": "all_enabled_substages",
                "enabled": True, "visibility": "internal", "aliases": [],
                "capability": ["evidence_synthesis"], "notFor": [],
                "inputContract": {"required": ["user_goal"]},
                "outputContract": {"artifactKind": "progress_note", "artifactContract": "test_artifact_v1", "requiredFields": ["summary"]},
                "failClosedRules": ["contract_mismatch"],
                "budgetPolicy": {"defaultMode": "draft_only", "maxWallTimeSec": 30, "maxToolCalls": 0},
                "evaluator": {"id": "test", "artifactValidator": "test", "requiredMetadata": ["source"], "qualitySignals": ["traceable"]},
                "allowedProviders": ["provider-a"], "defaultProvider": "provider-a",
                "toolRequirements": {}, "judgmentSupport": {"domainProfessional": False, "humanAuthority": False},
            }],
        },
        "bindings": {"skills": [{
            "bindingId": "bind:sdr:evidence_synthesis:skill:evidence_synthesis_skill", "bindingVersion": 1,
            "definitionId": "sdr:evidence_synthesis", "definitionVersion": 1,
            "skillId": "evidence_synthesis_skill", "skillDefinitionVersion": 1,
            "supportedRole": "tool", "enabled": True, "sourceAuthority": "skill_binding_source",
        }], "agents": [{
            "bindingId": "bind:sdr:evidence_synthesis:agent:general-research:primary", "bindingVersion": 1,
            "definitionId": "sdr:evidence_synthesis", "definitionVersion": 1,
            "agentId": "general-research", "agentDefinitionVersion": 1,
            "supportedRole": "primary", "enabled": True, "sourceAuthority": "general_fallback_policy",
        }], "agentSkills": []},
        "recipes": [],
        "staticPolicies": {
            "policyVersion": "registry-policy.v1", "unknownCanonicalRiskClass": "high",
            "unboundSkillPolicy": "not_applicable", "providerReadinessSource": "resolution_facts_only",
            "systemRecoveryAuthorities": [],
        },
    }


def _pair():
    full = _full_envelope()
    planning = snapshot_from_envelope(planning_envelope_from_execution(full))
    full["schemaVersion"] = 2
    full["scope"] = {"kind": "execution", "planningSnapshotRef": registry_envelope_ref(planning.envelope)}
    execution = snapshot_from_envelope(full)
    return planning, execution


def test_planning_snapshot_has_only_frozen_research_requirements() -> None:
    planning, execution = _pair()
    envelope = planning.envelope
    assert envelope["definitions"]["skills"] == []
    assert envelope["definitions"]["agents"] == []
    assert all(rows == [] for rows in envelope["bindings"].values())
    assert envelope["recipes"] == []
    assert envelope["definitions"]["substages"] == execution.envelope["definitions"]["substages"]


def test_execution_snapshot_proves_the_exact_planning_contract() -> None:
    planning, execution = _pair()
    assert execution_snapshot_matches_planning(
        registry_envelope_ref(planning.envelope), registry_envelope_ref(execution.envelope), execution.envelope,
    )
    archive = InMemoryRegistrySnapshotRepository()
    archive.put(planning)
    archive.put(execution)
    assert archive.get(planning.content_hash).canonical_payload == planning.canonical_payload
    assert archive.get(execution.content_hash).canonical_payload == execution.canonical_payload
    assert planning.content_hash != execution.content_hash


def test_planning_snapshot_rejects_injected_capabilities() -> None:
    planning, _ = _pair()
    envelope = planning.envelope
    envelope["definitions"]["skills"] = _full_envelope()["definitions"]["skills"]
    with pytest.raises(RegistrySnapshotIntegrityError, match="planning snapshot contains"):
        snapshot_from_envelope(envelope)


@pytest.mark.parametrize("mutation", ["description", "version", "parent"])
def test_execution_snapshot_rejects_changed_research_or_parent(mutation: str) -> None:
    _, execution = _pair()
    envelope = execution.envelope
    if mutation == "description":
        envelope["definitions"]["substages"][0]["description"] = "A different research requirement"
    elif mutation == "version":
        envelope["definitions"]["substages"][0]["definitionVersion"] = 2
    else:
        envelope["scope"]["planningSnapshotRef"]["contentHash"] = "sha256:" + "f" * 64
    with pytest.raises(RegistrySnapshotIntegrityError):
        snapshot_from_envelope(envelope)


def test_old_snapshot_bytes_and_freeze_identity_remain_unchanged() -> None:
    snapshot = snapshot_from_envelope(_full_envelope())
    assert snapshot.schema_version == 1 and "scope" not in snapshot.envelope
    ref = registry_envelope_ref(snapshot.envelope)
    assert execution_snapshot_matches_planning(ref, ref, snapshot.envelope)
    planning, execution = _pair()
    assert not execution_snapshot_matches_planning(ref, registry_envelope_ref(execution.envelope), execution.envelope)
    assert not execution_snapshot_matches_planning(registry_envelope_ref(planning.envelope), ref, snapshot.envelope)


def test_planning_snapshot_cannot_be_used_as_execution_authority() -> None:
    planning, _ = _pair()
    ref = registry_envelope_ref(planning.envelope)
    assert not execution_snapshot_matches_planning(ref, ref, planning.envelope)


def _catalog():
    return [{
        "definitionId": "test:" + stage["stage"], "definitionVersion": 1,
        "stage": stage["stage"], "label": stage["label"], "description": stage["responsibility"],
        "planningApplicability": {"scopeKind": "general", "scopeLabel": "General research", "scopeDescription": "Research responsibilities", "whenToUse": "When needed by the research question."},
    } for stage in research_stage_framework()]


def test_freeform_input_contains_four_stages_without_execution_choices() -> None:
    catalog = _catalog()
    schema = freeform_scientific_brief_schema(catalog)
    system, user = freeform_scientific_brief_prompts(
        raw_input="Compare two teaching feedback methods without revealing answers.", source_page="home",
        selected_hint=None, planning_selection={"userOutcome": "research_task"}, definition_catalog=catalog,
    )
    payload = json.loads(user)
    assert payload["researchStageFramework"] == research_stage_framework()
    assert [row["stage"] for row in payload["availableResearchWorkTypes"]] == [row["stage"] for row in catalog]
    assert "never from available Agents, Skills" in system
    assert "manufacture repetitive tasks" in system
    assert not any(key in payload for key in ("agentCatalog", "allowedSkillIds", "selectedCapabilities"))
    assert "outputSchema" not in payload
    assert "test:problem_modeling" not in user
    assert "execution" not in schema["properties"]


@pytest.mark.parametrize("adaptive", [False, True])
def test_workflow_input_uses_responsibilities_without_generic_stage_framework(adaptive: bool) -> None:
    context = {"description": "Answer the current research question using valid evidence."}
    if adaptive:
        system, user = workflow_semantic_delta_prompts(
            raw_input="Evaluate a reproduction result.", workflow_planning_context=context,
            responsibility_descriptions=[{
                "responsibilityChoice": "responsibility_1",
                "requirement": "core",
                "responsibility": "Interpret actual evidence.",
                "currentProjectAnswerRequired": True,
            }],
        )
        assert "responsibilityDeltas" in system
        assert "modes, rationales, empty placeholder arrays" in system
    else:
        system, user = workflow_semantic_brief_prompts(
            raw_input="Evaluate a reproduction result.", workflow_planning_context=context,
            work_item_responsibilities=["Interpret actual evidence."],
        )
    payload = json.loads(user)
    assert "researchStageFramework" not in payload
    assert "outputSchema" not in payload
    assert "Workflow" in system


def test_production_planning_prepares_without_touching_capability_loaders(monkeypatch) -> None:
    from app.modules import research_orchestration_resolver as resolver
    from app.modules.production_definition_source import ProductionDefinitionSource
    from app.modules.workflow_planning import workflow_planning_bundle
    from app.modules.cs_reproduction_workflow_admission import cs_reproduction_workflow_planning_admission

    def forbidden(*_args, **_kwargs):
        pytest.fail("planning must not load or validate execution capabilities")
    for name in ("_skill_rows", "_agent_rows", "load_multi_agent_recipe_registry",
                 "_attach_opentrajectory_skill_compositions", "_attach_pvd_workflow_skill_compositions",
                 "_attach_cs_reproduction_skill_compositions", "_attach_materials_workflow_skill_compositions"):
        monkeypatch.setattr(resolver, name, forbidden)
    source = ProductionDefinitionSource(archive=InMemoryRegistrySnapshotRepository(), planning_only=True)
    assert source.resolve("sdr:question_refinement") is not None
    assert source.planner_catalog_for_research_design(None)
    selection = cs_reproduction_workflow_planning_admission()
    bundle = workflow_planning_bundle(selection=selection, raw_goal="Reproduce the specified paper experiment.", definition_source=source)
    assert bundle is not None
    assert bundle.admission["workflowVersion"] == 7
    assert bundle.planning_context["schemaVersion"] == "workflow-planning-context.v2"


def test_planner_import_does_not_enumerate_agents() -> None:
    command = """
import app.modules.agent_registry as registry
def forbidden(*args, **kwargs):
    raise AssertionError('Planner import enumerated Agents')
registry.load_agent_registry = forbidden
import app.modules.research_start_planner
"""
    result = subprocess.run([sys.executable, "-c", command], cwd=ROOT / "services/api", capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_current_execution_capabilities_are_bound_only_after_planning(monkeypatch) -> None:
    from app.modules import research_orchestration_resolver as resolver
    planning, _ = _pair()
    calls = []
    def build_current():
        calls.append("execution")
        return _full_envelope()
    monkeypatch.setattr(resolver, "_build_registry_envelope", build_current)
    execution = resolver.build_execution_registry_snapshot(
        planning.envelope, required_definition_refs=(("sdr:evidence_synthesis", 1),),
    )
    assert calls == ["execution"]
    assert execution.envelope["bindings"]["skills"]
    assert execution_snapshot_matches_planning(registry_envelope_ref(planning.envelope), registry_envelope_ref(execution.envelope), execution.envelope)
    changed = _full_envelope()
    changed["definitions"]["substages"][0]["description"] = "Changed contract under the same ID"
    monkeypatch.setattr(resolver, "_build_registry_envelope", lambda: changed)
    with pytest.raises(resolver.RegistryAuthorityError, match="contract_changed_since_planning"):
        resolver.build_execution_registry_snapshot(planning.envelope, required_definition_refs=(("sdr:evidence_synthesis", 1),))


def test_execution_snapshot_keeps_only_dependency_closed_skill_bindings(monkeypatch) -> None:
    from app.modules import research_orchestration_resolver as resolver

    def definition(substage_key: str, label: str) -> dict:
        parsed = parse_spec_document(
            "opentrajectory",
            f"specs/{substage_key}.md",
            f"# {label}\n\n{label}.\n\n"
            "## Input\n\n- user_goal\n\n"
            f"## Output\n\n- {substage_key}_artifact\n\n"
            "## Acceptance\n\n- deterministic_contract\n",
        )
        return {
            "definitionId": f"opentrajectory:{substage_key}",
            "definitionVersion": 1,
            "pack": "opentrajectory",
            "stage": "insight_generation",
            "substageKey": substage_key,
            "enabled": True,
            "label": label,
            "description": f"{label}.",
            **parsed,
        }

    def skill(skill_id: str) -> dict:
        row = deepcopy(_full_envelope()["definitions"]["skills"][0])
        input_schema = {
            "type": "object",
            "properties": {"upstream_artifact": {"type": "object"}},
            "additionalProperties": True,
        }
        output_schema = {"type": "object", "additionalProperties": True}
        row.update({
            "skillId": skill_id,
            "skillPath": f"skills/test/{skill_id}",
            "sourceContract": {
                "frozenSchemas": {
                    "input": {"sha256": "0" * 64, "schema": input_schema},
                    "output": {"sha256": "1" * 64, "schema": output_schema},
                }
            },
        })
        return row

    definition_specs = (
        ("source", "Source", "source_skill", None),
        ("middle", "Middle", "middle_skill", "bind:opentrajectory:source:skill:source_skill"),
        ("leaf", "Leaf", "leaf_skill", "bind:opentrajectory:middle:skill:middle_skill"),
    )
    full = _full_envelope()
    full["definitions"]["packs"] = [{
        "pack": "opentrajectory", "definitionVersion": 1,
        "source": {"planning_scope": "test"},
    }]
    full["definitions"]["substages"] = []
    full["definitions"]["skills"] = []
    full["bindings"]["skills"] = []
    full["bindings"]["agents"] = []
    for substage_key, label, skill_id, source_binding_id in definition_specs:
        definition_id = f"opentrajectory:{substage_key}"
        full["definitions"]["substages"].append(definition(substage_key, label))
        full["definitions"]["skills"].append(skill(skill_id))
        binding = {
            "bindingId": f"bind:{definition_id}:skill:{skill_id}",
            "bindingVersion": 1,
            "definitionId": definition_id,
            "definitionVersion": 1,
            "skillId": skill_id,
            "skillDefinitionVersion": 1,
            "supportedRole": "tool",
            "enabled": True,
            "sourceAuthority": "skill_binding_source",
        }
        if source_binding_id is not None:
            binding["inputSources"] = {
                "contractVersion": 2,
                "fields": [{
                    "targetField": "upstream_artifact",
                    "sourcePresence": "required",
                    "source": {
                        "kind": "artifact", "bindingId": source_binding_id,
                        "bindingVersion": 1, "field": "$",
                    },
                }],
            }
        full["bindings"]["skills"].append(binding)
        full["bindings"]["agents"].append({
            "bindingId": f"bind:{definition_id}:agent:general-research:primary",
            "bindingVersion": 1,
            "definitionId": definition_id,
            "definitionVersion": 1,
            "agentId": "general-research",
            "agentDefinitionVersion": 1,
            "supportedRole": "primary",
            "enabled": True,
            "sourceAuthority": "general_fallback_policy",
        })
    full["definitions"]["substages"][-1]["executionComposition"] = {
        "workflowId": "opentrajectory_research_workflow",
        "skillPack": "opentrajectory",
        "compositionVersion": 1,
        "semantics": "ordered_required",
        "sourceAuthority": "test_fixture",
        "requiredSkillRefs": [{"skillId": "leaf_skill", "definitionVersion": 1}],
    }

    current = snapshot_from_envelope(full)
    planning = snapshot_from_envelope(planning_envelope_from_execution(full))
    changed_current = deepcopy(current.envelope)
    source_definition = next(
        row for row in changed_current["definitions"]["substages"]
        if row["definitionId"] == "opentrajectory:source"
    )
    source_definition["label"] = "Changed source contract"
    monkeypatch.setattr(
        resolver, "_build_registry_envelope",
        lambda: changed_current,
    )

    execution = resolver.build_execution_registry_snapshot(
        planning.envelope,
        required_definition_refs=(("opentrajectory:leaf", 1),),
    )

    assert {
        row["bindingId"] for row in execution.envelope["bindings"]["skills"]
    } == {
        "bind:opentrajectory:source:skill:source_skill",
        "bind:opentrajectory:middle:skill:middle_skill",
        "bind:opentrajectory:leaf:skill:leaf_skill",
    }
    leaf_definition = next(
        row for row in execution.envelope["definitions"]["substages"]
        if row["definitionId"] == "opentrajectory:leaf"
    )
    assert leaf_definition["executionComposition"]["requiredSkillRefs"] == [
        {"skillId": "leaf_skill", "definitionVersion": 1}
    ]
    assert execution_snapshot_matches_planning(
        registry_envelope_ref(planning.envelope),
        registry_envelope_ref(execution.envelope),
        execution.envelope,
    )

def test_v2_experiment_design_uses_registered_execution_method_roles() -> None:
    from app.modules import research_orchestration_resolver as resolver

    definition_id = "cs_reproduction:reproduction_experiment_design"
    planning = resolver.build_planning_registry_snapshot()
    execution = resolver.build_execution_registry_snapshot(
        planning.envelope,
        required_definition_refs=((definition_id, 1),),
    )
    definition = next(
        row
        for row in execution.envelope["definitions"]["substages"]
        if row["definitionId"] == definition_id and row["definitionVersion"] == 1
    )
    method = definition["executionMethod"]
    assert method["methodId"] == "cs_reproduction_experiment_design_grounding"
    assert [row["roleId"] for row in method["roles"]] == [
        "execution_source_interpretation",
        "execution_fact_assembly",
        "experiment_design_closure",
    ]
    assert method["completionRoleIds"] == ["experiment_design_closure"]
    assert "executionComposition" not in definition
    bound_skills = {
        row["skillId"]
        for row in execution.envelope["bindings"]["skills"]
        if row["definitionId"] == definition_id
        and row["definitionVersion"] == 1
        and row["enabled"] is True
        and row["sourceAuthority"] == "skill_binding_source"
    }
    assert bound_skills == {
        "execution_plan_interpretation",
        "execution_plan_fact_assembly",
        "reproduction_experiment_design",
    }
    assert any(
        row["definitionId"] == definition_id
        and row["definitionVersion"] == 1
        and row["enabled"] is True
        and row["sourceAuthority"] == "general_fallback_policy"
        for row in execution.envelope["bindings"]["agents"]
    )


def test_confirmed_design_selection_freezes_execution_without_changing_planning() -> None:
    from app.models.research_orchestration import ResearchTaskV12, RegistrySnapshotRef
    from app.modules import research_execution_decision_service as service
    from tests.support.research_execution import multi_artifact_selection_fixture
    from tests.support.research_execution_decision import freeze_prepared_selection_for_test

    task, resolution, old_snapshot, facts, readiness = multi_artifact_selection_fixture(completion_coverage="progress_capable")
    full = _full_envelope()
    # Use the existing actual frozen input contract from the execution fixture.
    full["definitions"]["skills"][0]["sourceContract"] = old_snapshot["definitions"]["skills"][0]["sourceContract"]
    planning = snapshot_from_envelope(planning_envelope_from_execution(full))
    full["schemaVersion"] = 2
    full["scope"] = {"kind": "execution", "planningSnapshotRef": registry_envelope_ref(planning.envelope)}
    execution = snapshot_from_envelope(full)
    task_payload = task.model_dump(mode="json", by_alias=True)
    task_payload["registrySnapshotRef"] = registry_envelope_ref(planning.envelope)
    task = ResearchTaskV12.model_validate(task_payload)
    resolution = resolution.model_copy(update={"registry_snapshot_ref": RegistrySnapshotRef.model_validate(registry_envelope_ref(execution.envelope))})
    # The test's confirmed design has already satisfied this formal input.
    facts = facts.model_copy(update={"available_input_refs": {"user_goal"}})
    prepared = service.prepare_selection(
        task, instance_id="sub_contract_full", resolution=resolution, snapshot_envelope=execution.envelope,
        facts=facts, runtime_readiness=readiness, selection_request_id="planning-execution-bridge",
        selection_command_hash="planning-execution-bridge", expected_task_revision=task.revision,
        owner_id="owner", workspace_id="workspace", decided_at=datetime(2026, 7, 28, 0, 0, 4, tzinfo=timezone.utc),
    )
    result = freeze_prepared_selection_for_test(prepared, task, "skill:evidence_synthesis_skill:1")
    assert result.task.registry_snapshot_ref == task.registry_snapshot_ref
    assert result.decision.registry_snapshot_ref == resolution.registry_snapshot_ref
    assert result.task.work_units[-1].registry_snapshot_ref == resolution.registry_snapshot_ref
    assert result.task.research_design_versions == task.research_design_versions


def _s1_workflow_membership_source(workflow_version: int):
    """Small planning-only fixture with valid, hash-checked Workflow snapshots."""
    from app.models.workflow_definition import (
        WorkflowDefinitionV1,
        WorkflowDefinitionV2,
        freeze_workflow_definition,
    )
    from app.modules.production_definition_source import ProductionDefinitionSource

    source = ProductionDefinitionSource.__new__(ProductionDefinitionSource)
    prototype = _full_envelope()["definitions"]["substages"][0]
    source._definitions = {}
    for definition_id, enabled in (
        ("specialized:required_research", False),
        ("specialized:optional_research", False),
        ("sdr:general_research", True),
        ("sdr:disabled_general", False),
        ("specialized:outside_workflow", True),
        ("specialized:disabled_outside", False),
    ):
        pack, key = definition_id.split(":", 1)
        source._definitions[definition_id] = {
            **deepcopy(prototype),
            "definitionId": definition_id,
            "pack": pack,
            "substageKey": key,
            "enabled": enabled,
        }
    source._packs = {
        "sdr": {"planning_scope": "general", "label": "General research"},
        "specialized": {"planning_scope": "specialized", "label": "Specialized research"},
    }
    payload = {
        "schemaVersion": f"workflow-definition.v{workflow_version}",
        "workflowId": "membership_contract",
        "workflowVersion": workflow_version,
        "nameZh": "Research membership",
        "nameEn": "Research membership",
        "description": "Freeze research duties independently of execution enablement.",
        "skillPack": "specialized",
        "nodes": [
            {"nodeKey": "required_research", "definitionId": "specialized:required_research",
             "definitionVersion": 1, "requirement": "required", "displayOrder": 10},
            {"nodeKey": "optional_research", "definitionId": "specialized:optional_research",
             "definitionVersion": 1, "requirement": "optional", "displayOrder": 20},
        ],
        "dependencies": [],
        "boundaryPolicy": {
            "allowedExternalActions": [], "confirmationRequiredActions": [], "prohibitedActions": [],
        },
    }
    if workflow_version == 2:
        payload["planningPolicy"] = {
            "responsibilityPlanning": "project_adaptive",
            "existingResultReuse": "validated_existing_results",
            "conditionalResponsibilities": "planner_selected",
        }
    model = WorkflowDefinitionV1 if workflow_version == 1 else WorkflowDefinitionV2
    frozen = freeze_workflow_definition(
        model.model_validate(payload),
        registry_snapshot_ref={"schemaVersion": 1, "contentHash": "sha256:" + "a" * 64},
    )
    return source, {"workflowDefinitionSnapshot": frozen.model_dump(mode="json", by_alias=True)}

@pytest.mark.parametrize("workflow_version", [1, 2])
def test_s1_frozen_workflow_members_survive_enablement_without_broadening_scope(workflow_version: int) -> None:
    source, admission = _s1_workflow_membership_source(workflow_version)
    before = deepcopy(source._definitions)
    expected = {
        "specialized:required_research", "specialized:optional_research", "sdr:general_research",
    }
    catalog = source.planner_catalog_for_research_design(admission)
    assert {row["definitionId"] for row in catalog} == expected
    for definition_id in source._definitions:
        assert source.definition_is_eligible_for_workflow_admission(definition_id, admission) is (
            definition_id in expected
        )
    freeform_ids = {row["definitionId"] for row in source.planner_catalog_for_research_design(None)}
    assert freeform_ids == {"sdr:general_research", "specialized:outside_workflow"}
    for definition_id in source._definitions:
        assert source.definition_is_eligible_for_workflow_admission(definition_id, None) is (
            definition_id in freeform_ids
        )
    assert source._definitions == before

@pytest.mark.parametrize("workflow_version", [1, 2])
@pytest.mark.parametrize("invalid_source", ["missing", "wrong_version"])
def test_s1_workflow_membership_cannot_invent_definition_or_replace_version(
    workflow_version: int, invalid_source: str,
) -> None:
    source, admission = _s1_workflow_membership_source(workflow_version)
    definition_id = "specialized:required_research"
    if invalid_source == "missing":
        del source._definitions[definition_id]
    else:
        source._definitions[definition_id]["definitionVersion"] = 2
    assert definition_id not in {
        row["definitionId"] for row in source.planner_catalog_for_research_design(admission)
    }
    assert source.definition_is_eligible_for_workflow_admission(definition_id, admission) is False

@pytest.mark.parametrize("workflow_version", [1, 2])
def test_s1_workflow_membership_still_requires_valid_frozen_snapshot(workflow_version: int) -> None:
    source, admission = _s1_workflow_membership_source(workflow_version)
    admission["workflowDefinitionSnapshot"]["contentHash"] = "0" * 64
    with pytest.raises(ValueError, match="Workflow admission snapshot is invalid"):
        source.planner_catalog_for_research_design(admission)
    with pytest.raises(ValueError, match="Workflow admission snapshot is invalid"):
        source.definition_is_eligible_for_workflow_admission("specialized:required_research", admission)
