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
