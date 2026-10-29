"""Focused WorkUnit materialization and explicit-start contract tests."""

from __future__ import annotations
from copy import deepcopy
from datetime import datetime
import pytest
from app.models.research_orchestration import ResearchTaskV12, RuntimeReadinessSnapshot
from app.modules.research_execution_decision_service import (
    ExecutionPreflightBlocked,
    PreparedSelection,
    ResearchExecutionDecisionService,
    SelectionCommitResult,
    evaluate_instance_preflight,
)
from app.modules import research_execution_decision_service as decision_service
from app.modules.research_orchestration_resolver import ResolutionFacts
from app.modules.research_progress_projection import project_task_progress
from tests.support.research_execution import (
    NoWorkflowLineageRepository as _NoWorkflowLineageRepository,
    execution_input_context_for_test as _execution_input_context_for_test,
    multi_artifact_selection_fixture as _multi_artifact_selection_fixture,
)
from tests.support.research_execution_decision import (
    freeze_prepared_selection_for_test as _freeze_prepared_selection_for_test,
    resolution_for_execution_decision as _resolution,
    structural_execution_decision_payload as _decision,
)
from tests.support.research_lifecycle import build_confirmed_research_task


def _fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_initial_materialization_does_not_read_unreleased_dependency_units() -> None:
    task = build_confirmed_research_task()
    decision = _decision()
    resolution = _resolution(decision)
    readiness = RuntimeReadinessSnapshot.model_validate(decision["runtimeReadiness"][0])
    reviewer_readiness = RuntimeReadinessSnapshot.model_validate(
        {
            "capabilityRef": {
                "agentId": "result_reliability_audit",
                "definitionVersion": 1,
            },
            "ready": True,
            "runtimeBindingId": "registry:review-contract:result_reliability_audit:1",
            "checkedAt": "2026-07-28T00:00:03Z",
            "expiresAt": "2026-07-28T00:05:03Z",
            "reason": None,
        }
    )
    selected_identity = "skill:evidence_synthesis_skill:1"
    prepared = PreparedSelection(
        task=task,
        instance=task.research_design_versions[0].instances[0],
        resolution=resolution,
        snapshot_envelope={
            "definitions": {
                "agents": [
                    {
                        "agentId": "result_reliability_audit",
                        "definitionVersion": 1,
                        "enabled": True,
                        "reviewContract": {
                            "frozenArtifactContract": {
                                "artifactContract": "independent_result_review_v1"
                            }
                        },
                    }
                ]
            },
            "bindings": {
                "skills": [
                    {
                        "bindingId": "bind:sdr:evidence_synthesis:skill:evidence_synthesis_skill",
                        "bindingVersion": 1,
                        "skillId": "evidence_synthesis_skill",
                        "skillDefinitionVersion": 1,
                        "enabled": True,
                    }
                ],
                "agents": [],
            },
        },
        runtime_readiness={
            selected_identity: readiness,
            "agent:result_reliability_audit:1": reviewer_readiness,
        },
        selection_request_id="selection-initial-materialization",
        selection_command_hash="command-hash",
        selection_payload_hash="payload-hash",
        expected_task_revision=task.revision,
        owner_id="owner",
        workspace_id="workspace",
        final_tie_candidate_ids=(selected_identity,),
        fairness=None,
        decided_at=datetime.fromisoformat("2026-07-28T00:00:04+00:00"),
    )

    result = _freeze_prepared_selection_for_test(prepared, task, selected_identity)

    assert result.task.status.value == "running"
    assert result.task.started_at is not None
    assert result.decision.status.value == "dispatched"
    assert len(result.work_units) == 1
    assert result.work_units[0].dependency_work_unit_ids == []
    assert result.work_units[0].input_artifact_refs == []
    assert result.work_units[0].policy_version == task.execution_policy_version


def test_explicit_start_materializes_running_truth_and_reload_projection() -> None:
    task, resolution, snapshot, facts, readiness = _multi_artifact_selection_fixture(
        completion_coverage="progress_capable"
    )
    payload = task.model_dump(mode="json", by_alias=True)
    payload["status"] = "not_started"
    payload["startedAt"] = None
    prestart = ResearchTaskV12.model_validate(payload)

    class Repository(_NoWorkflowLineageRepository):
        def __init__(self) -> None:
            self.current_task = prestart

        def lookup_selection_replay(self, **_kwargs):
            return None

        def load(self, **_kwargs):
            return self.current_task

        def load_registry_snapshot(self, _content_hash):
            return snapshot

        def load_catalog_execution_binding(self, **_kwargs):
            return None

        def load_execution_workflow_lineage(self, **_kwargs):
            return None

        def read_execution_input_context(self, **kwargs):
            return _execution_input_context_for_test(self.current_task, **kwargs)

        def record_needs_input(self, **_kwargs):
            raise AssertionError("executable explicit start must not record a blocker")

        def commit_prepared_selection(self, prepared, materialize):
            materialized = materialize(
                self.current_task,
                prepared.final_tie_candidate_ids[0],
            )
            self.current_task = project_task_progress(
                materialized.task,
                revision=self.current_task.revision + 1,
            )
            return SelectionCommitResult(
                task=self.current_task,
                decision=materialized.decision,
                replayed=False,
            )

    class Resolver:
        def resolve(self, **_kwargs):
            return resolution

    repository = Repository()
    service = ResearchExecutionDecisionService(
        repository=repository,
        resolver=Resolver(),
        facts_source=lambda *_args: (facts, readiness),
        now=lambda: datetime.fromisoformat("2026-07-28T00:00:04+00:00"),
    )

    result = service.start(
        task_id=prestart.task_id,
        instance_id=prestart.research_design_versions[0].instances[0].substage_instance_id,
        owner_id="owner",
        workspace_id="workspace",
        selection_request_id="selection-explicit-start",
        expected_task_revision=prestart.revision,
    )
    reloaded = repository.load(
        task_id=prestart.task_id, owner_id="owner", workspace_id="workspace"
    )

    assert result.task.status.value == "running"
    assert result.task.started_at is not None
    assert result.decision.status.value == "dispatched"
    assert reloaded.status.value == "running"
    assert reloaded.progress_projection["taskProgress"]["status"] == "running"


def test_known_missing_input_continues_and_records_the_gap() -> None:
    task = build_confirmed_research_task()
    facts = ResolutionFacts(
        runtimeReadiness={},
        providerReadiness={},
        authorizedCandidateRefs=set(),
        availableInputRefs=set(),
        applicableRuleRefs=set(),
        satisfiedHumanGateRefs=set(),
        riskAllowed=True,
        budgetAvailable=True,
        policyVersion="registry-policy.v1",
        activeWorkUnitCounts={},
        lastAssignedAt={},
    )
    preflight = evaluate_instance_preflight(
        task,
        instance_id="sub_contract_full",
        facts=facts,
        required_input_refs=("material_missing",),
    )
    assert preflight.executable
    assert preflight.missing_facts["researchGap"] == "required_input_missing"
    assert preflight.missing_facts["inputRefs"] == ["material_missing"]
    assert task.execution_decisions == []
    assert task.work_units == []
