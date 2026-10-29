"""Regression contract for method-local failures before WorkUnit creation.

Repository IO is in memory. Definition selection, Binding/Artifact resolution,
the input gate, release/start/freeze, and closure are production functions.
The unchanged 407b sample is the adjacent-method case. Cascade/start tests use
an explicitly declared failure-policy variant of that sample role graph.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
from hashlib import sha256
import json
from unittest.mock import patch

import pytest

from app.models.research_orchestration import (
    AssignmentPreflightOutcome, ExecutionDecisionStatus, ExecutionDecisionOutcome, ResearchTaskV12,
)
from app.modules import research_execution_decision_service as decisions
from app.modules.research_orchestration_resolver import ResolutionFacts
from app.modules.tests.research_execution_method_runtime_control import test_execution_method_runtime_control as method_fixture
from tests.support.research_execution_decision import freeze_prepared_selection_for_test


_BASE_METHOD = method_fixture._pvd_407b_method
_INTERMEDIATE_ROLES = (
    "scientific_relevance_screening",
    "progressive_reading",
    "evidence_extraction",
    "research_record_closure",
    "coverage_assessment",
)


def _schema(value):
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return {"schema": value, "sha256": sha256(encoded).hexdigest()}


def _binding(snapshot, skill_id):
    return next(row for row in snapshot["bindings"]["skills"] if row["skillId"] == skill_id)


def _definition(snapshot, skill_id):
    return next(row for row in snapshot["definitions"]["skills"] if row["skillId"] == skill_id)


def _bind_artifact(snapshot, target, producer, *, field="$", required=True):
    producer_binding = _binding(snapshot, producer)
    _binding(snapshot, target)["inputSources"] = {
        "contractVersion": 2,
        "fields": [{
            "targetField": "packet",
            "sourcePresence": "required" if required else "optional",
            "source": {
                "kind": "artifact",
                "bindingId": producer_binding["bindingId"],
                "bindingVersion": 1,
                "field": field,
            },
        }],
    }


def _build_case(*, cascade=False, first_unavailable=False, producer_payload=None, with_result=False):
    method = deepcopy(_BASE_METHOD())
    if cascade:
        # A declared generic control variant: all intermediate capabilities may
        # be unavailable; the original formal completion role remains closed.
        for role in method["roles"]:
            if role["roleId"] in _INTERMEDIATE_ROLES or (
                first_unavailable and role["roleId"] == "candidate_acquisition"
            ):
                role["failurePolicy"] = "continue_degraded"
    with patch.object(method_fixture, "_pvd_407b_method", return_value=method):
        task, prepared, snapshot, _ = method_fixture._fixture()

    for definition in snapshot["definitions"]["skills"]:
        definition["sourceContract"] = {
            "frozenSchemas": {
                "input": _schema({"type": "object", "additionalProperties": False}),
                "output": _schema({"type": "object"}),
            },
        }
    producer = _definition(snapshot, "sample_source_acquisition")
    producer["sourceContract"]["frozenSchemas"]["output"] = _schema({
        "type": "object",
        "properties": {"value": {"type": ["string", "null"]}},
        "additionalProperties": False,
    })
    for screen_skill in ("sample_relevance_screen_a", "sample_relevance_screen_b"):
        _bind_artifact(snapshot, screen_skill, "sample_source_acquisition", field="value")
        _definition(snapshot, screen_skill)["sourceContract"]["frozenSchemas"]["input"] = _schema({
            "type": "object",
            "properties": {"packet": {"type": "string", "minLength": 1}},
            "required": ["packet"],
            "additionalProperties": False,
        })

    # The selected screening candidate is frozen by the existing resolver fixture.
    screening_skill = next(
        identity.split(":")[1]
        for role, identity in prepared.execution_method_role_candidates
        if role == "scientific_relevance_screening"
    )
    required_sources = {
        "sample_progressive_reading": (
            screening_skill if cascade else "sample_source_acquisition"
        ),
        "sample_evidence_extraction": "sample_progressive_reading",
        "sample_record_closure": "sample_evidence_extraction",
        "sample_coverage_assessment": "sample_record_closure",
    }
    for target, source in required_sources.items():
        _bind_artifact(snapshot, target, source)
        _definition(snapshot, target)["sourceContract"]["frozenSchemas"]["input"] = _schema({
            "type": "object",
            "properties": {"packet": {"type": "object"}},
            "required": ["packet"],
            "additionalProperties": False,
        })
    _bind_artifact(
        snapshot, "sample_evidence_closure", "sample_coverage_assessment", required=False
    )
    _definition(snapshot, "sample_evidence_closure")["sourceContract"]["frozenSchemas"]["input"] = _schema({
        "type": "object",
        "properties": {"packet": {"type": "object"}},
        "additionalProperties": False,
    })

    task = method_fixture._set_coverage(task, "satisfied")
    prepared = decisions.replace(prepared, task=task)
    if first_unavailable:
        _binding(snapshot, "sample_source_acquisition")["inputSources"] = {
            "contractVersion": 2,
            "fields": [{
                "targetField": "packet",
                "sourcePresence": "required",
                "source": {"kind": "scientificState", "field": "unavailable_seed"},
            }],
        }
        return task, prepared, snapshot, {}

    materialized = freeze_prepared_selection_for_test(
        prepared, task, prepared.final_tie_candidate_ids[0]
    )
    current = method_fixture._set_work_status(materialized.task, "candidate_acquisition", "completed")
    current = method_fixture._append_role_artifact(
        current,
        role_id="candidate_acquisition",
        iteration=1,
        artifact_id="artifact-input-candidates",
    )
    content = json.dumps(
        {"value": None} if producer_payload is None else producer_payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    payload = current.model_dump(mode="json", by_alias=True)
    payload["artifacts"][-1]["validatorResult"]["contentSha256"] = sha256(content).hexdigest()
    if with_result:
        artifact = payload["artifacts"][-1]
        work = next(row for row in payload["workUnits"] if row["workUnitId"] == artifact["workUnitId"])
        result_id = "result-fixture-candidate-acquisition"
        payload["results"].append({
            "resultId": result_id,
            "workUnitId": work["workUnitId"],
            "capabilityCallId": None,
            "storageType": "local",
            "storageScope": payload["taskId"],
            "storageRef": "fixture/results/candidate-acquisition.json",
            "contentSha256": sha256(content).hexdigest(),
            "mimeType": "application/json",
            "checkStatus": "passed",
            "checkResult": {"reasonCode": "fixture_contract_validation_passed"},
            "sourceRefs": [],
            "artifactRefs": [artifact["artifactId"]],
            "scientificStateVersions": [],
            "createdAt": work["finishedAt"],
        })
        artifact["sourceResultId"] = result_id
    current = ResearchTaskV12.model_validate(payload)
    return current, prepared, snapshot, {"artifact-input-candidates": content}


class _MemoryRepository:
    def __init__(self, task, snapshot, contents):
        self.current = task
        self.snapshot = snapshot
        self.contents = contents
        self.dispatches = []
        self.save_calls = 0
        self.commit_calls = 0
        self.material_transform = None
        self.replay = None

    def load(self, **kwargs):
        assert kwargs["task_id"] == self.current.task_id
        return self.current

    def load_registry_snapshot(self, _digest):
        return self.snapshot

    def read_execution_input_context(self, **kwargs):
        artifacts = []
        works = {row.work_unit_id: row for row in self.current.work_units}
        decisions_by_id = {row.execution_decision_id: row for row in self.current.execution_decisions}
        for artifact_id in kwargs["input_artifact_refs"]:
            artifact = next(row for row in self.current.artifacts if row.artifact_id == artifact_id)
            work = works[artifact.work_unit_id]
            decision = decisions_by_id[work.execution_decision_id]
            assignment = next(row for row in decision.assignments if row.assignment_id == work.assignment_id)
            content = self.contents[artifact_id]
            assert artifact.validator_result["contentSha256"] == sha256(content).hexdigest()
            material = {
                "artifactId": artifact_id,
                "contentSha256": sha256(content).hexdigest(),
                "sourceCapabilityType": work.executor_ref.executor_type,
                "sourceCapabilityId": work.executor_ref.executor_id,
                "sourceCapabilityDefinitionVersion": work.executor_ref.definition_version,
                "sourceBindingRefs": [row.model_dump(mode="json", by_alias=True) for row in assignment.source_binding_refs],
                "mimeType": "application/json",
                "content": content,
            }
            artifacts.append(material if self.material_transform is None else self.material_transform(material))
        state = self.current.scientific_states[-1]
        return {
            "artifacts": artifacts,
            "scientificState": dict(state.scientific_facts),
            "scientificStatePresent": True,
            "scientificStateVersion": state.scientific_state_version,
            "invocationContext": kwargs.get("invocation_context", {}),
        }

    def save(self, task, **kwargs):
        assert kwargs["expected_task_revision"] == self.current.revision == task.revision
        dispatches = kwargs.get("pending_dispatches", [])
        assert not kwargs.get("automatic_dispatch") or dispatches
        self.save_calls += 1
        self.dispatches.extend(dispatches)
        payload = task.model_dump(mode="json", by_alias=True)
        payload["revision"] += 1
        self.current = ResearchTaskV12.model_validate(payload)
        return self.current

    def lookup_selection_replay(self, **kwargs):
        if self.replay is None:
            return None
        command_hash, result = self.replay
        assert kwargs["selection_command_hash"] == command_hash
        return decisions.SelectionCommitResult(task=self.current, decision=result.decision, replayed=True)

    def load_catalog_execution_binding(self, **_kwargs):
        return None

    def load_execution_workflow_lineage(self, **_kwargs):
        return None

    def record_needs_input(self, **_kwargs):
        raise AssertionError("a declared local degradation must not request user input")

    def commit_prepared_selection(self, prepared, materialize):
        assert prepared.expected_task_revision == self.current.revision
        self.commit_calls += 1
        materialized = materialize(self.current, prepared.final_tie_candidate_ids[0])
        self.dispatches.extend(materialized.dispatches)
        payload = materialized.task.model_dump(mode="json", by_alias=True)
        payload["revision"] += 1
        self.current = ResearchTaskV12.model_validate(payload)
        result = decisions.SelectionCommitResult(task=self.current, decision=materialized.decision, replayed=False)
        self.replay = (prepared.selection_command_hash, result)
        return result


def _service(case):
    task, prepared, snapshot, contents = case
    repository = _MemoryRepository(task, snapshot, contents)
    facts = ResolutionFacts(
        runtimeReadiness={},
        providerReadiness={},
        authorizedCandidateRefs={decisions.candidate_identity(row) for row in prepared.resolution.candidate_evaluations},
        availableInputRefs=set(prepared.instance.input_refs),
        applicableRuleRefs=set(),
        satisfiedHumanGateRefs=set(),
        riskAllowed=True,
        budgetAvailable=True,
        policyVersion="registry-policy.v1",
        activeWorkUnitCounts={},
        lastAssignedAt={},
    )
    class Resolver:
        def resolve(self, **_kwargs):
            return prepared.resolution
    service = decisions.ResearchExecutionDecisionService(
        repository=repository,
        resolver=Resolver(),
        facts_source=lambda *_args: (facts, prepared.runtime_readiness),
        now=lambda: prepared.decided_at + timedelta(minutes=11),
    )
    return service, repository


def _release(service, repository):
    task = repository.current
    return service.release_ready(
        task_id=task.task_id,
        execution_decision_id=task.execution_decisions[-1].execution_decision_id,
        owner_id="owner",
        workspace_id="workspace",
        expected_task_revision=task.revision,
    )


def _outcomes_by_role(task):
    decision = task.execution_decisions[-1]
    roles = {row.assignment_id: row.execution_method_role_id for row in decision.assignments}
    return {roles[row.assignment_id]: row for row in decision.assignment_preflight_outcomes}


def _assert_no_dispatch_for_outcomes(task, repository):
    failed_ids = {row.assignment_id for row in task.execution_decisions[-1].assignment_preflight_outcomes}
    assert not failed_ids & {row.assignment_id for row in task.work_units}
    dispatched_work_ids = {row.work_unit_id for row in repository.dispatches}
    assert all(row.work_unit_id in {work.work_unit_id for work in task.work_units} for row in repository.dispatches)
    assert all(row.status.value == "queued" for row in task.work_units if row.work_unit_id in dispatched_work_ids)










@pytest.mark.parametrize(
    "corruption", ["lineage", "ambiguous"],
    ids=["wrong_binding", "same_lineage_ambiguous"],
)
def test_continue_degraded_does_not_swallow_authority_or_shape_corruption(corruption):
    case = _build_case(producer_payload={"value": []} if corruption == "source_shape" else {"value": None})
    service, repository = _service(case)
    if corruption == "lineage":
        def replace_lineage(material):
            return {**material, "sourceBindingRefs": [{"bindingId": "unrelated-binding", "bindingVersion": 1}]}
        repository.material_transform = replace_lineage
    if corruption == "ambiguous":
        repository.current = method_fixture._append_role_artifact(
            repository.current, role_id="candidate_acquisition", iteration=1,
            artifact_id="artifact-input-candidates-duplicate",
        )
        content = repository.contents["artifact-input-candidates"]
        repository.contents["artifact-input-candidates-duplicate"] = content
        payload = repository.current.model_dump(mode="json", by_alias=True)
        payload["artifacts"][-1]["validatorResult"]["contentSha256"] = sha256(content).hexdigest()
        repository.current = ResearchTaskV12.model_validate(payload)
    before_artifacts = repository.current.artifacts
    after = _release(service, repository)
    assert after.execution_decisions[-1].status.value == "closed"
    assert after.execution_decisions[-1].outcome.value == "blocked"
    assert repository.dispatches == []
    assert len(after.work_units) == 1
    outcomes = _outcomes_by_role(after)
    assert outcomes["scientific_relevance_screening"].failure_kind == "contract_violation"
    assert after.artifacts == before_artifacts




def test_known_failure_after_worker_exit_resumes_same_method_without_provider(monkeypatch):
    from app.modules.research_dispatch_service import ResearchDispatchService
    from worker import entrypoint
    case = _build_case(producer_payload={"value": "usable"}, with_result=True)
    coordinator, repository = _service(case)
    after_screen_dispatched = _release(coordinator, repository)
    failed = method_fixture._set_work_status(after_screen_dispatched, "scientific_relevance_screening", "blocked")
    repository.current = failed
    # This is the durable state left by a process exit after failure persistence.
    # Query IO supplies its original revision; all continuation logic is real.
    def candidates(**kwargs):
        assert kwargs["min_pending_age_seconds"] == 0
        return [{"task_id": failed.task_id, "execution_decision_id": failed.execution_decisions[-1].execution_decision_id,
                 "owner_id": "owner", "workspace_id": "workspace", "revision": failed.revision}]
    repository.list_pending_method_continuations = candidates
    dispatch_service = ResearchDispatchService(repository, decision_coordinator=coordinator)
    resolver_calls = 0
    provider_calls = 0

    class Resolver:
        def resolve(self):
            nonlocal resolver_calls
            resolver_calls += 1
            return None

    def provider_factory(*args, **kwargs):
        nonlocal provider_calls
        provider_calls += 1
        raise AssertionError("failed action must never be resubmitted")

    monkeypatch.setattr(entrypoint.settings, "workunit_execution_mode", "worker")
    monkeypatch.setattr(entrypoint.settings, "research_orchestration_v1", True)
    monkeypatch.setattr(entrypoint.settings, "persistence_backend", "postgres")
    dispatch_count = len(repository.dispatches)
    original_dispatches = tuple(repository.dispatches)
    original_work_unit_ids = {row.work_unit_id for row in failed.work_units}
    original_results = failed.results
    assert len(original_results) >= 1
    original_result_ids = tuple(row.result_id for row in original_results)
    original_results_by_id = {
        row.result_id: row.model_dump(mode="json", by_alias=True)
        for row in original_results
    }
    original_artifacts = failed.artifacts
    original_failed_work = next(
        row for row in failed.work_units
        if row.execution_method_role_id == "scientific_relevance_screening"
    )

    assert entrypoint.claim_and_run_research_dispatch(
        binding_resolver=Resolver(), service=dispatch_service, provider_factory=provider_factory,
    ) is False
    assert resolver_calls == 1
    assert provider_calls == 0
    assert repository.current is failed

    assert dispatch_service.resume_pending_method_continuation() is True
    resumed = repository.current
    assert resumed.work_units[:len(failed.work_units)] == failed.work_units
    assert len(resumed.work_units) == len(failed.work_units) + 1
    assert resumed.work_units[-1].execution_method_role_id == "progressive_reading"
    assert resumed.work_units[-1].work_unit_id not in original_work_unit_ids
    assert len(repository.dispatches) == dispatch_count + 1
    assert tuple(repository.dispatches[:dispatch_count]) == original_dispatches
    assert repository.dispatches[-1].work_unit_id == resumed.work_units[-1].work_unit_id
    resumed_dispatches = tuple(repository.dispatches)
    assert len(resumed.results) == len(original_results)
    assert tuple(row.result_id for row in resumed.results) == original_result_ids
    assert {
        row.result_id: row.model_dump(mode="json", by_alias=True)
        for row in resumed.results
    } == original_results_by_id
    assert len({row.result_id for row in resumed.results}) == len(resumed.results)
    assert resumed.artifacts == original_artifacts
    assert next(
        row for row in resumed.work_units
        if row.work_unit_id == original_failed_work.work_unit_id
    ) == original_failed_work
    # A competing/stale poll keeps the observed revision and cannot dispatch twice.
    assert dispatch_service.resume_pending_method_continuation() is False
    assert repository.current is resumed
    assert tuple(repository.dispatches) == resumed_dispatches
    assert len(repository.dispatches) == dispatch_count + 1
    assert provider_calls == 0


def test_hard_preflight_waits_for_inflight_sibling_before_closing():
    case = _build_case()
    service, repository = _service(case)
    # Reuse the already completed first real WU as a deliberately in-flight peer
    # after recording a checked hard blocker. No fabricated WU is introduced.
    from app.models.research_orchestration import AssignmentPreflightOutcome
    task = repository.current
    decision = task.execution_decisions[-1]
    assignment = next(row for row in decision.assignments if row.execution_method_role_id == "scientific_relevance_screening")
    outcome = AssignmentPreflightOutcome(
        assignmentId=assignment.assignment_id, failureKind="contract_violation",
        reasonCode="input_contract_invalid", fieldPaths=["/packet"],
        inputArtifactSnapshots=[], inputScientificStateVersion=None,
        checkedAt=decision.decided_at,
    )
    payload = task.model_dump(mode="json", by_alias=True)
    payload["executionDecisions"][-1]["assignmentPreflightOutcomes"] = [outcome.model_dump(mode="json", by_alias=True)]
    payload["workUnits"][0]["status"] = "running"
    payload["workUnits"][0]["finishedAt"] = None
    inflight = ResearchTaskV12.model_validate(payload)
    repository.current = inflight
    assert _release(service, repository) is inflight
    assert repository.dispatches == []
    assert repository.save_calls == 0
    repository.current = method_fixture._set_work_status(inflight, "candidate_acquisition", "completed")
    closed = _release(service, repository)
    assert closed.execution_decisions[-1].status.value == "closed"
    assert closed.execution_decisions[-1].outcome.value == "blocked"
    assert closed.work_units == repository.current.work_units


def _legacy_material_case(*, closed=False, kind="material_unavailable"):
    original = _build_case(producer_payload={"value": "useful source"})
    task, prepared, snapshot, contents = original
    decision = task.execution_decisions[-1]
    assignment = next(
        row
        for row in decision.assignments
        if row.execution_method_role_id == "scientific_relevance_screening"
    )
    outcome = AssignmentPreflightOutcome(
        assignmentId=assignment.assignment_id,
        failureKind=kind,
        reasonCode="input_condition_insufficient"
        if kind == "material_unavailable"
        else "input_binding_lineage_invalid",
        fieldPaths=[],
        inputArtifactSnapshots=[],
        inputScientificStateVersion=None,
        checkedAt=decision.decided_at,
    )
    decision = decision.model_copy(
        update={
            "assignment_preflight_outcomes": [outcome],
            "status": ExecutionDecisionStatus.CLOSED
            if closed
            else ExecutionDecisionStatus.DISPATCHED,
            "outcome": ExecutionDecisionOutcome.SUCCEEDED if closed else None,
        }
    )
    task = task.model_copy(update={"execution_decisions": [decision]})
    return (task, prepared, snapshot, contents), assignment, outcome


@pytest.mark.parametrize("closed", [False, True])
def test_legacy_material_history_releases_formal_work_without_rewriting_results(closed):
    inputs, assignment, outcome = _legacy_material_case(closed=closed)
    service, repo = _service(inputs)
    before = deepcopy(repo.current)
    after = _release(service, repo)
    work = next(row for row in after.work_units if row.assignment_id == assignment.assignment_id)
    assert work.status.value == "queued"
    assert work.resolved_parameters == {"packet": "useful source"}
    assert any(row.work_unit_id == work.work_unit_id for row in repo.dispatches)
    assert after.execution_decisions[-1].assignment_preflight_outcomes == [outcome]
    assert after.artifacts == before.artifacts
    assert after.results == before.results
    assert after.work_units[0] == before.work_units[0]
    calls = repo.save_calls
    repeated = _release(service, repo)
    assert repeated == after
    assert repo.save_calls == calls


def test_contract_violation_still_prevents_materialization():
    inputs, assignment, outcome = _legacy_material_case(kind="contract_violation")
    service, repo = _service(inputs)
    after = _release(service, repo)
    assert not any(row.assignment_id == assignment.assignment_id for row in after.work_units)
    assert not repo.dispatches
    assert after.execution_decisions[-1].outcome.value == "blocked"


@pytest.mark.parametrize(
    "invalid", ["newer_decision", "different_design", "active_work", "cancelled"]
)
def test_closed_legacy_recovery_retains_identity_and_no_duplicate_execution_guards(invalid):
    inputs, assignment, outcome = _legacy_material_case(closed=True)
    task, prepared, snapshot, contents = inputs
    decision = task.execution_decisions[-1]
    if invalid == "newer_decision":
        task = task.model_copy(
            update={
                "execution_decisions": [
                    decision,
                    decision.model_copy(
                        update={
                            "execution_decision_id": "newer",
                            "decision_sequence": decision.decision_sequence + 1,
                        }
                    ),
                ]
            }
        )
    if invalid == "different_design":
        task = task.model_copy(
            update={
                "active_research_design_ref": task.active_research_design_ref.model_copy(
                    update={"version": 99}
                )
            }
        )
    if invalid == "active_work":
        task = task.model_copy(
            update={
                "work_units": [
                    task.work_units[0].model_copy(
                        update={"status": type(task.work_units[0].status)("queued")}
                    )
                ]
            }
        )
    if invalid == "cancelled":
        task = task.model_copy(update={"status": type(task.status)("cancelled")})
    assert decisions._legacy_material_release_task(task, decision) is None


@pytest.mark.parametrize("producer_payload", [{"value": None}, {}, {"value": []}])
def test_scientific_gap_dispatches_research_role_and_preserves_source(producer_payload):
    inputs = _build_case(producer_payload=producer_payload)
    service, repository = _service(inputs)
    before = repository.current
    after = _release(service, repository)
    assert after.execution_decisions[-1].assignment_preflight_outcomes == []
    assert after.work_units[-1].execution_method_role_id == "scientific_relevance_screening"
    assert after.work_units[-1].status.value == "queued"
    assert len(repository.dispatches) == 1
    assert after.artifacts == before.artifacts
    assert after.work_units[0] == before.work_units[0]
    assert after.execution_decisions[-1].outcome is None
