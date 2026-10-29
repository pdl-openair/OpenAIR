from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

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
