from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from app.models.research_orchestration import ResearchTaskV12
from app.modules.pvd_scientific_state_materialization import (
    materialize_experimental_system_context,
    materialize_round_experiment_recommendation,
)
from app.modules.research_execution_decision_service import (
    ExecutionPreflightBlocked,
    ResearchExecutionDecisionService,
    SelectionCommitResult,
    SelectionReplayEvidence,
)
from app.modules.research_orchestration_resolver import ResolutionFacts, ResolutionResult
from app.routes import pvd_feedback as feedback_route
from app.routes.pvd_feedback import PvdExperimentFeedbackIn

from tests.support.research_execution import (
    NoWorkflowLineageRepository as _NoWorkflowLineageRepository,
)
from tests.support.research_execution import (
    execution_input_context_for_test as _execution_input_context_for_test,
)
from tests.support.research_execution import (
    multi_artifact_selection_fixture as _multi_artifact_selection_fixture,
)
from tests.test_pvd_scientific_state_materialization import (
    _context as _pvd_context,
)
from tests.test_pvd_scientific_state_materialization import (
    _recommendation as _pvd_recommendation,
)


def _task_with_scientific_state(
    *,
    accepted_fact: str,
    priority_gap: str,
) -> tuple[ResearchTaskV12, dict, ResolutionResult, ResolutionFacts, dict]:
    task, resolution, snapshot, facts, readiness = _multi_artifact_selection_fixture(
        completion_coverage="progress_capable"
    )
    payload = task.model_dump(mode="json", by_alias=True)
    first = payload["researchDesignVersions"][0]["instances"][0]
    first["objective"] = "Quantify comparative robustness under the confirmed evidence boundary."
    second = deepcopy(first)
    second.update(
        {
            "substageInstanceId": "sub_mechanism_followup",
            "objective": (
                "Investigate the unresolved mechanism explanation under the "
                "confirmed evidence boundary."
            ),
            "sequenceHint": 99,
        }
    )
    second["acceptanceCriteria"][0]["criterionId"] = "criterion_mechanism_followup"
    payload["researchDesignVersions"][0]["instances"].append(second)
    runtime = deepcopy(payload["substageRuntimeStates"][0])
    runtime.update(
        {
            "substageInstanceId": "sub_mechanism_followup",
            "status": "ready",
            "activeExecutionDecisionId": None,
            "workUnitIds": [],
            "artifactRefs": [],
        }
    )
    payload["substageRuntimeStates"].append(runtime)
    payload["scientificStates"] = [
        {
            "scientificStateVersion": 1,
            "researchDesignVersion": 1,
            "scientificFacts": {
                "acceptedConclusion": accepted_fact,
                "priorityResearchGap": priority_gap,
            },
            "executionState": {"completedResearchTasks": []},
            "sourceRefs": ["accepted-scientific-state"],
            "createdAt": "2026-07-28T00:00:04Z",
        }
    ]
    return ResearchTaskV12.model_validate(payload), snapshot, resolution, facts, readiness


class _Repository(_NoWorkflowLineageRepository):
    def __init__(self, task: ResearchTaskV12, snapshot: dict) -> None:
        self.task = task
        self.snapshot = snapshot
        self.replays: dict[tuple[str, str], SelectionCommitResult] = {}
        self.commit_count = 0

    def load(self, **_kwargs):
        return self.task

    def load_registry_snapshot(self, _content_hash):
        return self.snapshot

    def load_catalog_execution_binding(self, **_kwargs):
        return None

    def lookup_selection_replay_evidence(self, **kwargs):
        for (request_id, command_hash), replay in self.replays.items():
            if request_id == kwargs["selection_request_id"]:
                return SelectionReplayEvidence(
                    instance_id=replay.decision.substage_instance_id,
                    selection_command_hash=command_hash,
                    replay=replay,
                )
        return None

    def lookup_selection_replay(self, **kwargs):
        return self.replays.get((kwargs["selection_request_id"], kwargs["selection_command_hash"]))

    def read_execution_input_context(self, **kwargs):
        return _execution_input_context_for_test(self.task, **kwargs)

    def record_needs_input(self, **_kwargs):
        raise AssertionError("legal next-task candidates must not become needs-input")

    def commit_prepared_selection(self, prepared, materialize):
        self.commit_count += 1
        materialized = materialize(self.task, prepared.final_tie_candidate_ids[0])
        self.task = materialized.task
        result = SelectionCommitResult(
            task=self.task,
            decision=materialized.decision,
            replayed=False,
        )
        self.replays[(prepared.selection_request_id, prepared.selection_command_hash)] = result
        return result


class _Resolver:
    def __init__(self, template: ResolutionResult) -> None:
        self.template = template

    def resolve(self, **kwargs):
        return ResolutionResult(
            designRef=self.template.design_ref,
            instanceRef=kwargs["instanceRef"],
            registrySnapshotRef=self.template.registry_snapshot_ref,
            candidateEvaluations=self.template.candidate_evaluations,
            preferredCandidateEvaluationRefs=self.template.preferred_candidate_evaluation_refs,
        )


def _service(
    *,
    repository: _Repository,
    resolution: ResolutionResult,
    facts: ResolutionFacts,
    readiness: dict,
) -> ResearchExecutionDecisionService:
    return ResearchExecutionDecisionService(
        repository=repository,
        resolver=_Resolver(resolution),
        facts_source=lambda *_args: (facts, readiness),
        now=lambda: datetime.fromisoformat("2026-07-28T00:00:05+00:00"),
    )


def test_single_legal_candidate_does_not_require_semantic_provider() -> None:
    task, snapshot, resolution, facts, readiness = _task_with_scientific_state(
        accepted_fact="The accepted state leaves exactly one executable research task.",
        priority_gap="Advance the remaining legal task.",
    )
    payload = task.model_dump(mode="json", by_alias=True)
    next(
        row
        for row in payload["substageRuntimeStates"]
        if row["substageInstanceId"] == "sub_mechanism_followup"
    )["status"] = "planned"
    task = ResearchTaskV12.model_validate(payload)
    repository = _Repository(task, snapshot)

    result = _service(
        repository=repository,
        resolution=resolution,
        facts=facts,
        readiness=readiness,
    ).start_next(
        task_id=task.task_id,
        owner_id="owner",
        workspace_id="workspace",
        selection_request_id="scientific-next-single-candidate",
        expected_task_revision=task.revision,
    )

    assert result.decision.substage_instance_id == "sub_contract_full"
    assert "ScientificState v1:" in result.decision.rationale
    assert repository.commit_count == 1


def test_next_task_selection_prepares_ready_definition_scope_before_evaluation() -> None:
    """A planning snapshot must expose the READY choice set to role preflight."""
    task, snapshot, resolution, facts, readiness = _task_with_scientific_state(
        accepted_fact="The accepted state leaves one next research task.",
        priority_gap="Advance the remaining legal task.",
    )
    payload = task.model_dump(mode="json", by_alias=True)
    next(
        row
        for row in payload["substageRuntimeStates"]
        if row["substageInstanceId"] == "sub_mechanism_followup"
    )["status"] = "planned"
    task = ResearchTaskV12.model_validate(payload)
    repository = _Repository(task, snapshot)

    service = _service(
        repository=repository,
        resolution=resolution,
        facts=facts,
        readiness=readiness,
    )
    captured: list[tuple[tuple[str, int], ...]] = []
    original = service._execution_snapshot

    def capture_execution_snapshot(task, *, required_definition_refs=()):
        captured.append(required_definition_refs)
        return original(task, required_definition_refs=required_definition_refs)

    service._execution_snapshot = capture_execution_snapshot  # type: ignore[method-assign]
    service.start_next(
        task_id=task.task_id,
        owner_id="owner",
        workspace_id="workspace",
        selection_request_id="scientific-next-ready-definition-scope",
        expected_task_revision=task.revision,
    )

    instance = next(
        row
        for row in task.research_design_versions[0].instances
        if row.substage_instance_id == "sub_contract_full"
    )
    expected = ((instance.substage_definition_id, instance.substage_definition_version),)
    # First call evaluates the READY candidate set; the second freezes the
    # selected instance for decision materialization.
    assert captured == [expected, expected]


def test_next_execution_decision_reads_latest_real_pvd_feedback() -> None:
    task, snapshot, resolution, facts, readiness = _task_with_scientific_state(
        accepted_fact="Initial recommendation accepted.",
        priority_gap="Await real experiment feedback.",
    )
    payload = task.model_dump(mode="json", by_alias=True)
    payload["scientificStates"].append(
        {
            "scientificStateVersion": 2,
            "researchDesignVersion": 1,
            "scientificFacts": {
                "experimental_system_contexts": [
                    {
                        "experimental_system_context_ref": "pvd-context-1",
                        "experimental_system_context": {"contract": "test-context"},
                    }
                ],
                "current_experimental_system_context_ref": "pvd-context-1",
                "experiment_history": [
                    {
                        "experiment_ref": "experiment-feedback-1",
                        "round_ref": "R1",
                        "experimental_system_context_ref": "pvd-context-1",
                        "system_recommendation": {
                            "parameters": [
                                {
                                    "parameter_key": "substrate_bias",
                                    "recommended_value": -50,
                                }
                            ]
                        },
                        "planned_execution": {
                            "parameters": [{"parameter_key": "substrate_bias", "value": -45}]
                        },
                        "actual_execution": {
                            "execution_ref": "execution-feedback-1",
                            "parameters": [{"parameter_key": "substrate_bias", "value": -42}],
                        },
                        "measurements": [
                            {
                                "measurement_ref": "measurement-feedback-1",
                                "metric_key": "residual_stress",
                                "value": 1.7,
                                "unit": "GPa",
                            }
                        ],
                        "process_events": [],
                    }
                ],
            },
            "executionState": {},
            "sourceRefs": ["user-result-1"],
            "createdAt": "2026-09-26T00:02:00Z",
        }
    )
    latest_task = ResearchTaskV12.model_validate(payload)
    snapshot["definitions"]["substages"][0]["enabled"] = True
    repository = _Repository(latest_task, snapshot)

    result = _service(
        repository=repository,
        resolution=resolution,
        facts=facts,
        readiness=readiness,
    ).start_next(
        task_id=latest_task.task_id,
        owner_id="owner",
        workspace_id="workspace",
        selection_request_id="pvd-feedback-next",
        expected_task_revision=latest_task.revision,
    )

    consumed = repository.task.scientific_states[-1]
    record = consumed.scientific_facts["experiment_history"][0]
    assert consumed.scientific_state_version == 2
    assert "ScientificState v2:" in result.decision.rationale
    assert record["planned_execution"]["parameters"][0]["value"] == -45
    assert record["actual_execution"]["parameters"][0]["value"] == -42
    assert record["measurements"][0]["value"] == 1.7
    assert result.decision.substage_instance_id == "sub_contract_full"


def test_pvd_feedback_api_persists_state_and_enqueues_next_decision(monkeypatch) -> None:
    task, _snapshot, _resolution, _facts, _readiness = _task_with_scientific_state(
        accepted_fact="Initial recommendation accepted.",
        priority_gap="Await real experiment feedback.",
    )
    payload = task.model_dump(mode="json", by_alias=True)
    design = payload["researchDesignVersions"][0]
    feedback_instance = design["instances"][0]
    feedback_instance["substageDefinitionId"] = "pvd:experimental_feedback_round_analysis"
    design["instances"] = [feedback_instance]
    payload["substageRuntimeStates"] = [payload["substageRuntimeStates"][0]]
    payload["substageRuntimeStates"][0].update(
        {
            "status": "completed",
            "activeExecutionDecisionId": None,
            "workUnitIds": [],
            "artifactRefs": [],
        }
    )
    payload = materialize_experimental_system_context(
        task_payload=payload,
        projected_facts={"experimental_system_context": _pvd_context("AlSc")},
        source_artifact_id="artifact-context-feedback",
        research_design_version=1,
        occurred_at=datetime(2026, 9, 26, tzinfo=timezone.utc),
    )
    payload = materialize_round_experiment_recommendation(
        task_payload=payload,
        projected_facts=_pvd_recommendation(
            experiment_id="experiment-feedback-1",
            bias=-50,
        ),
        source_artifact_id="artifact-recommendation-feedback",
        research_design_version=1,
        occurred_at=datetime(2026, 9, 26, 0, 1, tzinfo=timezone.utc),
    )
    stored = ResearchTaskV12.model_validate(payload)
    queued: list[dict] = []

    class Repository:
        def load(self, **_kwargs):
            return stored

        def save(self, candidate, **kwargs):
            assert kwargs["expected_task_revision"] == stored.revision
            saved = candidate.model_dump(mode="json", by_alias=True)
            saved["revision"] = stored.revision + 1
            self.saved = ResearchTaskV12.model_validate(saved)
            return self.saved

    repository = Repository()

    class Queue:
        def enqueue(self, **kwargs):
            queued.append(kwargs)
            return {"state": "queued"}

    monkeypatch.setattr(feedback_route, "_require_api", lambda: None)
    monkeypatch.setattr(
        feedback_route,
        "ResearchOrchestrationRepository",
        lambda: repository,
    )
    monkeypatch.setattr(feedback_route, "ModelJobQueueRepository", Queue)
    body = PvdExperimentFeedbackIn(
        expectedRevision=stored.revision,
        experimentRef="experiment-feedback-1",
        sourceRef="user-feedback-submission-1",
        sourceOrigin="user",
        plannedExecution={
            "parameters": [
                {
                    "parameter_key": "substrate_bias",
                    "value_type": "integer",
                    "value": -45,
                    "unit": "V",
                    "unit_identity": "voltage:V",
                    "unit_dimension": "voltage",
                }
            ],
            "other_process_conditions": [],
        },
        actualExecution={
            "execution_ref": "execution-feedback-1",
            "status": "executed",
            "validity": "valid",
            "parameters": [
                {
                    "parameter_key": "substrate_bias",
                    "value_type": "integer",
                    "value": -42,
                    "unit": "V",
                    "unit_identity": "voltage:V",
                    "unit_dimension": "voltage",
                }
            ],
            "other_process_conditions": [],
        },
        measurements=[
            {
                "measurement_ref": "measurement-feedback-1",
                "metric_key": "residual_stress",
                "value": 1.7,
                "unit": "GPa",
                "status": "final",
                "validity": "valid",
                "method": "wafer_curvature",
            }
        ],
    )

    response = feedback_route.submit_pvd_experiment_feedback(
        task_id=stored.task_id,
        body=body,
        idempotency_key="pvd-feedback-selection-1",
        identity={"ownerId": "owner", "workspaceId": "workspace", "actorId": "user"},
    )

    latest = repository.saved.scientific_states[-1]
    record = latest.scientific_facts["experiment_history"][0]
    assert response.scientificStateVersion == latest.scientific_state_version
    assert record["system_recommendation"]["parameters"][0]["recommended_value"] == -50
    assert record["planned_execution"]["parameters"][0]["value"] == -45
    assert record["actual_execution"]["parameters"][0]["value"] == -42
    assert record["measurements"][0]["value"] == 1.7
    assert record["measurements"][0]["provenance"] == {
        "origin": "user",
        "source_ref": "user-feedback-submission-1",
    }
    assert repository.saved.substage_runtime_states[0].status.value == "ready"
    assert queued[0]["expected_revision"] == repository.saved.revision
    assert queued[0]["command_payload"]["invocationContext"] == {
        "experiment_ref": "experiment-feedback-1"
    }


@pytest.mark.parametrize("first_ready", [True, False])
def test_confirmed_work_order_dispatches_without_model_and_replays(
    first_ready, monkeypatch
) -> None:
    def forbidden_model_call(*_args, **_kwargs):
        raise AssertionError("confirmed work must reach dispatch without an LLM call")

    monkeypatch.setattr(
        "app.execution.research_start_provider_adapter.HttpxResearchStartTransport.post",
        forbidden_model_call,
    )
    monkeypatch.setattr(
        "app.execution.llm_structured_runner.run_skill_with_llm",
        forbidden_model_call,
    )
    task, snapshot, resolution, facts, readiness = _task_with_scientific_state(
        accepted_fact="Existing accepted facts.", priority_gap="Preserve this unknown."
    )
    if not first_ready:
        payload = task.model_dump(mode="json", by_alias=True)
        payload["substageRuntimeStates"][0]["status"] = "planned"
        task = ResearchTaskV12.model_validate(payload)
    repository = _Repository(task, snapshot)
    service = _service(
        repository=repository, resolution=resolution, facts=facts, readiness=readiness
    )
    command = dict(
        task_id=task.task_id,
        owner_id="owner",
        workspace_id="workspace",
        selection_request_id="confirmed-order",
        expected_task_revision=task.revision,
    )
    result = service.start_next(**command)
    expected_id = "sub_contract_full" if first_ready else "sub_mechanism_followup"
    assert result.decision.substage_instance_id == expected_id
    assert result.task.research_design_versions == task.research_design_versions
    assert result.task.work_units
    assert (
        result.task.scientific_states[-1].scientific_facts
        == task.scientific_states[-1].scientific_facts
    )
    replay = service.start_next(**command)
    assert replay.decision.execution_decision_id == result.decision.execution_decision_id
    assert repository.commit_count == 1


@pytest.mark.parametrize("blocked_by", ["executor_unavailable", "input_missing"])
def test_real_resolver_skips_unexecutable_work_despite_ready_reviewer(blocked_by) -> None:
    from app.models.research_orchestration import (
        InstanceRef,
        RegistrySnapshotRef,
        RuntimeReadinessSnapshot,
    )
    from app.modules.research_orchestration_resolver import ResearchOrchestrationResolver
    from tests.test_research_orchestration_resolver import _archived, _envelope, _facts

    task, _, _, _, original_readiness = _task_with_scientific_state(
        accepted_fact="Existing facts.", priority_gap="Keep the unresolved gap."
    )
    envelope = _envelope()
    for skill in envelope["definitions"]["skills"]:
        skill["sourceContract"]["frozenSchemas"] = {
            "input": {
                "sha256": "a" * 64,
                "schema": {"type": "object", "additionalProperties": False},
            }
        }
    archive, frozen = _archived(envelope)
    snapshot_ref = RegistrySnapshotRef(schemaVersion=1, contentHash=frozen.content_hash)
    payload = task.model_dump(mode="json", by_alias=True)
    payload["registrySnapshotRef"] = snapshot_ref.model_dump(mode="json", by_alias=True)
    for instance in payload["researchDesignVersions"][0]["instances"]:
        instance["expectedArtifactTypes"] = ["evidence_summary"]
    task = ResearchTaskV12.model_validate(payload)
    repository = _Repository(task, envelope)
    resolver = ResearchOrchestrationResolver(archive)
    template = next(iter(original_readiness.values())).model_dump(mode="json", by_alias=True)

    def facts_source(_task, instance, _snapshot):
        first = instance.substage_instance_id == "sub_contract_full"
        facts = _facts(provider=not (first and blocked_by == "executor_unavailable"))
        facts = facts.model_copy(
            update={
                "runtime_readiness": {
                    key: key == "skill:skill-a:1"
                    and not (first and blocked_by == "executor_unavailable")
                    for key in facts.runtime_readiness
                },
                "available_input_refs": set()
                if first and blocked_by == "input_missing"
                else {"material_contract"},
            }
        )
        executor = RuntimeReadinessSnapshot.model_validate(
            {
                **template,
                "capabilityRef": {"skillId": "skill-a", "definitionVersion": 1},
                "ready": not (first and blocked_by == "executor_unavailable"),
                "reason": "runtime_unavailable"
                if first and blocked_by == "executor_unavailable"
                else None,
            }
        )
        reviewer = RuntimeReadinessSnapshot.model_validate(
            {
                **template,
                "capabilityRef": {"agentId": "result_reliability_audit", "definitionVersion": 1},
                "ready": True,
            }
        )
        return facts, {"skill:skill-a:1": executor, "agent:result_reliability_audit:1": reviewer}

    service = ResearchExecutionDecisionService(
        repository=repository,
        resolver=resolver,
        facts_source=facts_source,
        now=lambda: datetime(2026, 7, 28, 0, 0, 4, tzinfo=timezone.utc),
    )
    design = task.research_design_versions[0]
    counts = []
    for instance in design.instances:
        facts, _ = facts_source(task, instance, envelope)
        resolution = resolver.resolve(
            designRef=task.active_research_design_ref,
            instanceRef=InstanceRef(
                substageInstanceId=instance.substage_instance_id, instanceRevision=1
            ),
            registrySnapshotRef=snapshot_ref,
            design=design,
            facts=facts,
        )
        counts.append(
            sum(
                row.eligible and row.preference.executable_now
                for row in resolution.candidate_evaluations
            )
        )
    assert counts == [0, 1], [
        (str(row.candidate_ref), row.eligible, row.rejection_codes, row.preference.executable_now)
        for row in resolution.candidate_evaluations
    ]
    result = service.start_next(
        task_id=task.task_id,
        owner_id="owner",
        workspace_id="workspace",
        selection_request_id="real-resolver-skip",
        expected_task_revision=task.revision,
    )
    assert result.decision.substage_instance_id == "sub_mechanism_followup"
    assert repository.commit_count == 1
    assert result.task.research_design_versions == task.research_design_versions


def _task_with_designated_invocation_binding(
    *,
    accepted_fact: str,
    priority_gap: str,
) -> tuple[ResearchTaskV12, dict, ResolutionResult, ResolutionFacts, dict]:
    """One READY instance whose skill binding declares a caller-designated field."""
    task, snapshot, resolution, facts, readiness = _task_with_scientific_state(
        accepted_fact=accepted_fact,
        priority_gap=priority_gap,
    )
    payload = task.model_dump(mode="json", by_alias=True)
    payload["researchDesignVersions"][0]["instances"] = [
        payload["researchDesignVersions"][0]["instances"][0]
    ]
    payload["substageRuntimeStates"] = [payload["substageRuntimeStates"][0]]
    snapshot["bindings"]["skills"][0]["runtimeInputs"] = {
        "contractVersion": 2,
        "fields": [
            {
                "targetField": "experiment_ref",
                "source": {"kind": "invocationContext", "field": "experiment_ref"},
            }
        ],
    }
    # The frozen skill input schema must admit the caller-designated field.
    input_schema = snapshot["definitions"]["skills"][0]["sourceContract"][
        "frozenSchemas"
    ]["input"]["schema"]
    input_schema["properties"] = {"experiment_ref": {"type": "string"}}
    return (
        ResearchTaskV12.model_validate(payload),
        snapshot,
        resolution,
        facts,
        readiness,
    )


def test_generic_start_blocks_on_unsupplied_designated_invocation_field() -> None:
    task, snapshot, resolution, facts, readiness = (
        _task_with_designated_invocation_binding(
            accepted_fact="Recommendation accepted.",
            priority_gap="Await designated experiment feedback.",
        )
    )

    class Repository(_Repository):
        def __init__(self, task: ResearchTaskV12, snapshot: dict) -> None:
            super().__init__(task, snapshot)
            self.needs_input_kwargs: dict | None = None

        def record_needs_input(self, **kwargs):
            self.needs_input_kwargs = kwargs
            return SimpleNamespace(revision=task.revision)

    repository = Repository(task, snapshot)
    service = _service(
        repository=repository,
        resolution=resolution,
        facts=facts,
        readiness=readiness,
    )

    with pytest.raises(
        ExecutionPreflightBlocked,
        match="execution_selection_invocation_context_unsatisfied",
    ):
        service.start_next(
            task_id=task.task_id,
            owner_id="owner",
            workspace_id="workspace",
            selection_request_id="generic-auto-start",
            expected_task_revision=task.revision,
        )

    assert repository.needs_input_kwargs is not None
    assert (
        repository.needs_input_kwargs["reason_code"]
        == "execution_selection_invocation_context_unsatisfied"
    )
    assert repository.needs_input_kwargs["blocker_facts"] == {
        "missingInvocationFields": ["experiment_ref"]
    }
    assert repository.commit_count == 0


def test_designated_invocation_field_scoped_selection_still_commits() -> None:
    task, snapshot, resolution, facts, readiness = (
        _task_with_designated_invocation_binding(
            accepted_fact="Recommendation accepted.",
            priority_gap="Designated experiment feedback submitted.",
        )
    )
    repository = _Repository(task, snapshot)

    result = _service(
        repository=repository,
        resolution=resolution,
        facts=facts,
        readiness=readiness,
    ).start_next(
        task_id=task.task_id,
        owner_id="owner",
        workspace_id="workspace",
        selection_request_id="feedback-triggered",
        expected_task_revision=task.revision,
        invocation_context={"experiment_ref": "pvd-experiment:1"},
        invocation_context_definition_id="sdr:evidence_synthesis",
    )

    assert repository.commit_count == 1
    assert result.decision.substage_instance_id == "sub_contract_full"
    frozen = repository.task.work_units[0].invocation_context
    assert frozen == {"experiment_ref": "pvd-experiment:1"}
