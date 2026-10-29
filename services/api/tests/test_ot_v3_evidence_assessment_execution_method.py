from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from app.models.research_orchestration import InstanceRef, ResearchTaskV12
from app.modules.registry_snapshot_repository import InMemoryRegistrySnapshotRepository
from app.modules.research_orchestration_resolver import (
    ResearchOrchestrationResolver,
    ResolutionFacts,
    build_registry_snapshot,
)
from tests.support.research_lifecycle import build_confirmed_research_task


ROOT = Path(__file__).resolve().parents[3]
METHOD_ID = "opentrajectory_evidence_assessment_synthesis_v1"
SKILL_ID = "ot_v3_evidence_assessment_synthesis"
RECORD_ID = "rr:0123456789abcdef0123456789abcdef"


def _task(snapshot_hash: str) -> ResearchTaskV12:
    payload = build_confirmed_research_task().model_dump(mode="json", by_alias=True)
    payload["registrySnapshotRef"] = {"schemaVersion": 1, "contentHash": snapshot_hash}
    instance = payload["researchDesignVersions"][0]["instances"][0]
    instance.update(
        {
            "substageDefinitionId": "opentrajectory:evidence_assessment_synthesis",
            "substageDefinitionVersion": 1,
            "stageKey": "execution_validation",
            "objective": "基于研究记录评估证据。",
            "inputRefs": ["current_judgment_target", "research_records"],
            "expectedArtifactTypes": ["evidence_assessment_and_research_synthesis"],
        }
    )
    criterion = instance["acceptanceCriteria"][0]
    criterion.update(
        {
            "requiredArtifactType": "evidence_assessment_and_research_synthesis",
            "judgmentRequirement": "domain_professional",
            "evaluatorType": "agent",
            "evaluatorRef": "evidence_relationships_traceable_to_records_and_sources",
        }
    )
    instance["acceptanceCriteria"] = [criterion]
    return ResearchTaskV12.model_validate(payload)


def test_v3_evidence_assessment_method_selects_only_its_formal_skill() -> None:
    snapshot = build_registry_snapshot()
    task = _task(snapshot.content_hash)
    design = task.research_design_versions[0]
    instance = design.instances[0]
    facts = ResolutionFacts(
        runtimeReadiness={f"skill:{SKILL_ID}:1": True, "agent:general_research_agent:1": True},
        providerReadiness={"openai_primary": True},
        authorizedCandidateRefs={f"skill:{SKILL_ID}:1", "agent:general_research_agent:1"},
        availableInputRefs=set(instance.input_refs),
        applicableRuleRefs=set(),
        satisfiedHumanGateRefs=set(),
        riskAllowed=True,
        budgetAvailable=True,
        policyVersion="registry-policy.v1",
    )
    repository = InMemoryRegistrySnapshotRepository()
    repository.put(snapshot)
    resolution = ResearchOrchestrationResolver(repository).resolve(
        design=design,
        designRef=task.active_research_design_ref,
        instanceRef=InstanceRef(substageInstanceId=instance.substage_instance_id, instanceRevision=instance.instance_revision),
        registrySnapshotRef=task.registry_snapshot_ref,
        facts=facts,
    )

    definition = next(
        row for row in snapshot.envelope["definitions"]["substages"]
        if row["definitionId"] == "opentrajectory:evidence_assessment_synthesis"
    )
    assert definition["executionMethod"]["methodId"] == METHOD_ID
    assert definition["executionMethod"]["completionRoleIds"] == ["evidence_assessment"]
    eligible = {
        role.role_ref: getattr(candidate.candidate_ref, "skill_id", None)
        for candidate in resolution.candidate_evaluations
        for role in candidate.execution_method_role_evaluations
        if role.eligible and role.executable_now
    }
    assert eligible == {f"{METHOD_ID}:evidence_assessment": SKILL_ID}


def _guard_module():
    path = ROOT / "skills" / "opentrajectory" / "ot-v3-evidence-assessment-synthesis-skill" / "scripts" / "runtime_guard.py"
    spec = importlib.util.spec_from_file_location("ot_v3_evidence_assessment_guard", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _draft(record_id: str = RECORD_ID) -> dict:
    return {
        "direct_assessment": "The supplied record supports the bounded reported observation only.",
        "evidence_judgments": [{"statement": "A bounded observation is reported.", "disposition": "supported", "record_ids": [record_id], "scope_boundary": "Only under the record's reported conditions.", "rationale": "The record is the supplied evidence basis.", "confidence": 0.5}],
        "conflicts": [],
        "limitations": ["The supplied record set is narrow."],
        "evidence_gaps": [{"gap": "Independent replication is absent.", "related_record_ids": [record_id], "impact": "A broader conclusion is not established."}],
        "synthesis_warnings": [],
    }


def test_v3_evidence_assessment_preserves_model_record_references() -> None:
    guard = _guard_module()
    payload = {"task_id": "task-ot-v3", "research_context": {"research_object": "bounded question"}, "research_records": [{"record_id": RECORD_ID}]}
    output = guard.finalize(payload, _draft())
    assert output["evidence_assessment_and_research_synthesis"]["evidence_judgments"][0]["record_ids"] == [RECORD_ID]
    assert output["audit"]["traceability_mode"] == "research_record_source_binding"

    draft = _draft("rr:ffffffffffffffffffffffffffffffff")
    draft["evidence_judgments"][0]["confidence"] = "模型信心说明"
    output = guard.finalize(payload, draft)
    assert output["evidence_assessment_and_research_synthesis"] == draft
