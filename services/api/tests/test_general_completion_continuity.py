"""Isolated formal General completion and recovery handoff checks."""

from copy import deepcopy
from types import SimpleNamespace

import pytest
from app.models.research_orchestration import ResearchTaskV12
from app.modules import research_execution_decision_service as service_module

from tests.test_research_orchestration_resolver import _general_reasoning_fallback_selection


def _materialized(*, completion: bool = True):
    external = None if completion else {"status": "unknown", "requiredActionKinds": []}
    task, resolution, snapshot, prepared = _general_reasoning_fallback_selection(
        "opentrajectory:bounded_reasoning",
        external_contract=external,
    )
    initial = service_module.prepare_selection_execution(
        prepared, task, "agent:general_research_agent:1"
    )
    frozen = service_module.freeze_selection_execution(prepared, task, initial)
    return frozen, resolution, snapshot


def _running_completion():
    frozen, _resolution, _snapshot = _materialized()
    payload = frozen.task.model_dump(mode="json", by_alias=True)
    decision = payload["executionDecisions"][-1]
    work = payload["workUnits"][-1]
    decision["status"] = "dispatched"
    work["status"] = "running"
    runtime = payload["substageRuntimeStates"][0]
    runtime["status"] = "running"
    runtime["activeExecutionDecisionId"] = decision["executionDecisionId"]
    return ResearchTaskV12.model_validate(payload), decision, work


@pytest.mark.parametrize(
    "mutation",
    [
        None,
        "progress",
        "work_revision",
        "design_version",
        "inactive_runtime",
        "failed_check",
        "wrong_contract",
    ],
)
def test_real_general_result_acceptance_preserves_exact_completion_lineage(mutation):
    task, decision, work = _running_completion()
    decision, work = deepcopy(decision), deepcopy(work)
    check, contract = "passed", "general_research_reasoning_artifact_v1"
    if mutation == "progress":
        decision["purpose"] = "progress"
        decision["assignments"][0]["completionCoverage"] = "progress_capable"
    elif mutation == "work_revision":
        work["substageInstanceRevision"] += 1
    elif mutation == "design_version":
        decision["researchDesignVersion"] += 1
        work["researchDesignVersion"] += 1
    elif mutation == "inactive_runtime":
        task.substage_runtime_states[0].active_execution_decision_id = None
    elif mutation == "failed_check":
        check = "failed"
    elif mutation == "wrong_contract":
        contract = "another_artifact_contract"
    before = task.model_dump(mode="json", by_alias=True)
    accepted = service_module.general_completion_result_authorized(
        task,
        decision=decision,
        work=work,
        artifact_contract=contract,
        result_check_status=check,
        artifact_content='{"summary":"A bounded conclusion grounded in the available records."}',
    )
    assert accepted is (mutation is None)
    assert task.model_dump(mode="json", by_alias=True) == before


@pytest.mark.parametrize("mutation", [None, "old_design", "unknown_work", "active_decision"])
def test_exhausted_progress_recovery_uses_formal_materializer_without_rewriting_history(
    mutation,
    monkeypatch,
):
    frozen, _old_resolution, _old_snapshot = _materialized(completion=False)
    _new, resolution, snapshot = _materialized()
    payload = frozen.task.model_dump(mode="json", by_alias=True)
    decision = payload["executionDecisions"][-1]
    work = payload["workUnits"][-1]
    runtime = payload["substageRuntimeStates"][0]
    decision.update(status="closed", outcome="progressed", closedAt="2026-07-28T00:01:00Z")
    work.update(status="completed", finishedAt="2026-07-28T00:01:00Z")
    runtime.update(
        status="blocked_recoverable",
        activeExecutionDecisionId=None,
        statusReasonCode="progress_capability_exhausted_without_completion_contract",
    )
    task = ResearchTaskV12.model_validate(payload)
    if mutation == "old_design":
        task.execution_decisions[-1].research_design_version += 1
    elif mutation == "unknown_work":
        task.work_units[-1].status = service_module.WorkUnitStatus.EXTERNAL_RESULT_UNKNOWN
    elif mutation == "active_decision":
        task.substage_runtime_states[0].active_execution_decision_id = decision[
            "executionDecisionId"
        ]
    service = object.__new__(service_module.ResearchExecutionDecisionService)
    monkeypatch.setattr(
        service,
        "_execution_snapshot",
        lambda *a, **k: (snapshot.envelope, resolution.registry_snapshot_ref),
        raising=False,
    )
    monkeypatch.setattr(service, "_facts_source", lambda *a: (None, {}), raising=False)
    monkeypatch.setattr(
        service, "_resolver", SimpleNamespace(resolve=lambda **k: resolution), raising=False
    )
    requests = []
    monkeypatch.setattr(
        service,
        "materialize_recovery",
        lambda request, **k: requests.append(request) or "formal-recovery",
        raising=False,
    )
    before = task.model_dump(mode="json", by_alias=True)
    outcome = service._resume_completed_progress(
        task=task,
        design=task.research_design_versions[0],
        owner_id="owner_contract",
        workspace_id="workspace_contract",
        selection_request_id="continuity-recovery",
    )
    assert task.model_dump(mode="json", by_alias=True) == before
    assert len(requests) == (1 if mutation is None else 0)
    assert outcome == ("formal-recovery" if mutation is None else None)
    if requests:
        assert requests[0].original_execution_decision_id == decision["executionDecisionId"]
        assert requests[0].original_work_unit_id == work["workUnitId"]
        assert (
            requests[0].recovery_identity
            == f"completion-capability:{decision['executionDecisionId']}"
        )
        assert requests[0].expected_task_revision == task.revision


@pytest.mark.parametrize("completion", [True, False])
def test_general_dispatch_freezes_typed_output_only_for_completion(completion):
    from app.execution.research_dispatch_huiyan_provider import frozen_request_for_dispatch
    from app.execution.research_dispatch_provider import DispatchSubmissionRequest
    from app.modules.research_orchestration_resolver import build_registry_snapshot

    frozen, _resolution, snapshot = _materialized(completion=completion)
    task = frozen.task
    work, decision = frozen.work_units[0], frozen.decision
    production = build_registry_snapshot().envelope
    agent = next(
        row
        for row in production["definitions"]["agents"]
        if row["agentId"] == work.executor_ref.executor_id
    )
    provider_id = agent["allowedProviders"][0]
    snapshot_envelope = deepcopy(snapshot.envelope)
    snapshot_envelope["definitions"]["agents"] = [agent]
    repository = SimpleNamespace(
        load=lambda **k: task, load_registry_snapshot=lambda content_hash: snapshot_envelope
    )
    request = DispatchSubmissionRequest(
        dispatch_id="dispatch-frozen-general",
        task_id=task.task_id,
        execution_decision_id=decision.execution_decision_id,
        work_unit_id=work.work_unit_id,
        idempotency_key=work.idempotency_key,
        owner_id="owner_contract",
        workspace_id="workspace_contract",
        payload=work.model_dump(mode="json", by_alias=True),
    )
    request_frozen = frozen_request_for_dispatch(
        request,
        provider_id=provider_id,
        repository=repository,
    )
    expected_kind = "evidence_summary" if completion else agent["outputContract"]["artifactKind"]
    assert request_frozen.orchestrationPlan["artifactKind"] == expected_kind
    assert request_frozen.artifactContract == "general_research_reasoning_artifact_v1"
    assert request_frozen.frozenInputSnapshot["instruction"] == work.instruction
    assert (
        request_frozen.frozenInputSnapshot["researchDesignVersion"]
        == decision.research_design_version
    )


@pytest.mark.parametrize(
    "content,accepted",
    [
        ("{}", False),
        ("[]", False),
        ("null", False),
        ("", False),
        ('""', False),
        (
            '{"summary": "Evidence supports a bounded association; mechanism remains unknown.", '
            '"limitations": ["mechanism unknown"], "unresolved": null}',
            True,
        ),
    ],
)
def test_general_completion_requires_content_but_preserves_local_unknowns(content, accepted):
    task, decision, work = _running_completion()
    before = task.model_dump(mode="json", by_alias=True)
    assert (
        service_module.general_completion_result_authorized(
            task,
            decision=decision,
            work=work,
            artifact_contract="general_research_reasoning_artifact_v1",
            result_check_status="passed",
            artifact_content=content,
        )
        is accepted
    )
    assert task.model_dump(mode="json", by_alias=True) == before


@pytest.mark.parametrize("mutation", [None, "manual", "unknown_reason", "active"])
def test_automatic_continuation_reaches_exhausted_recovery_without_ready_instances(mutation):
    from tests.support.research_execution_decision import build_planned_continuation_task

    task = build_planned_continuation_task(next_stage_key="insight_generation")
    payload = task.model_dump(mode="json", by_alias=True)
    runtime = payload["substageRuntimeStates"][1]
    runtime.update(
        status="blocked_recoverable",
        statusReasonCode="progress_capability_exhausted_without_completion_contract",
        activeExecutionDecisionId=None,
    )
    if mutation == "unknown_reason":
        runtime["statusReasonCode"] = "external_result_unknown"
    elif mutation == "active":
        active_decision = deepcopy(payload["executionDecisions"][0])
        active_decision.update(
            executionDecisionId="decision-active-downstream",
            substageInstanceId=runtime["substageInstanceId"],
            substageInstanceRevision=runtime["instanceRevision"],
            decisionSequence=active_decision["decisionSequence"] + 1,
            status="dispatched",
            outcome=None,
            closedAt=None,
        )
        payload["executionDecisions"].append(active_decision)
        runtime["activeExecutionDecisionId"] = active_decision["executionDecisionId"]
    task = ResearchTaskV12.model_validate(payload)
    mode = (
        service_module.TaskExecutionMode.MANUAL_STEP
        if mutation == "manual"
        else service_module.TaskExecutionMode.AUTOMATIC_RESEARCH
    )
    before = task.model_dump(mode="json", by_alias=True)
    outcome = service_module.plan_execution_continuation(
        task,
        completed_execution_decision_id=task.execution_decisions[0].execution_decision_id,
        execution_mode=mode,
    )
    assert outcome.action == ("start" if mutation is None else "none")
    assert outcome.next_instance_id is None
    assert task.model_dump(mode="json", by_alias=True) == before
