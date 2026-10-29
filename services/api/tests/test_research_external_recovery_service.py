from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from app.execution.research_dispatch_provider import DispatchSubmissionQueryResult
from app.models.research_external_result import (
    ExplicitRetryCommand,
    ExternalExecutionState,
    FrozenExternalQuery,
    ReconcileExternalResultCommand,
    ReconcileResolution,
)
from app.modules.research_external_recovery_service import (
    ExternalRecoveryConflict,
    ExternalRecoveryNotAllowed,
    ExternalRecoveryService,
    production_external_recovery_capabilities,
)
from app.modules.research_orchestration_repository import (
    ExternalRecoveryStateError,
    ResearchOrchestrationRepository,
)

from tests.support.r0c_interruption import r0c_canonical


class Repository:
    def __init__(self, frozen: FrozenExternalQuery) -> None:
        self.frozen = frozen
        self.prepared = 0
        self.commits = []
        self.retry_commands = []
        self.result = {"status": "recorded", "directiveId": None}

    def prepare_reconcile(self, command):
        self.prepared += 1
        return self.frozen

    def commit_reconcile(self, command, frozen, query):
        self.commits.append((command, frozen, query))
        return self.result

    def create_retry_attempt(self, command):
        self.retry_commands.append(command)
        return {"status": "created", "capabilityCallId": "call-2", "attemptNumber": 2}

    def prepare_retry(self, command):
        return self.frozen


class PreparedProvider:
    def __init__(self, query_result, *, query_capable=True) -> None:
        self.query_result = query_result
        self.query_capable = query_capable
        self.query_calls = []
        self.submit_calls = 0

    def query_external_result(self, *, idempotency_key, remote_run_id):
        self.query_calls.append((idempotency_key, remote_run_id))
        return self.query_result


class Provider:
    def __init__(self, prepared) -> None:
        self.prepared = prepared

    def preflight_query(self):
        return self.prepared


class RecoveryPort:
    def __init__(self) -> None:
        self.calls = []

    def materialize_recovery(self, directive):
        self.calls.append(directive)
        return {"status": "materialized", "decisionId": "decision-new"}


FROZEN = FrozenExternalQuery(
    task_id="task-1",
    work_unit_id="work-1",
    dispatch_id="dispatch-1",
    execution_decision_id="decision-1",
    capability_call_id="call-1",
    attempt_number=1,
    expected_task_revision=9,
    authorization_task_revision=8,
    owner_id="owner-1",
    workspace_id="workspace-1",
    provider_id="fake-provider",
    adapter="fake-adapter",
    provider_idempotency_key="provider-key-frozen",
    remote_run_id="remote-1",
    external_state=ExternalExecutionState.UNKNOWN,
    frozen_work_unit_fingerprint="a" * 64,
)


def _reconcile(**updates) -> ReconcileExternalResultCommand:
    values = dict(
        task_id="task-1",
        work_unit_id="work-1",
        dispatch_id="dispatch-1",
        execution_decision_id="decision-1",
        capability_call_id="call-1",
        attempt_number=1,
        resolution=ReconcileResolution.QUERY_PROVIDER,
        evidence_refs=("evidence-1",),
        command_request_id="reconcile-request-1",
        canonical_payload_fingerprint="b" * 64,
        expected_task_revision=9,
        owner_id="owner-1",
        workspace_id="workspace-1",
        actor_id="actor-1",
    )
    values.update(updates)
    return ReconcileExternalResultCommand(**values)


@pytest.mark.parametrize(
    ("query", "expected"),
    (
        (DispatchSubmissionQueryResult(status="submitted", remote_run_id="remote-1"), ExternalExecutionState.SUBMITTED),
        (DispatchSubmissionQueryResult(status="running", remote_run_id="remote-1"), ExternalExecutionState.RUNNING),
        (DispatchSubmissionQueryResult(status="completed", remote_run_id="remote-1", result_evidence_refs=("evidence-result",)), ExternalExecutionState.COMPLETED),
        (DispatchSubmissionQueryResult(status="known_failed", remote_run_id="remote-1", error_code="provider_failed"), ExternalExecutionState.KNOWN_FAILED),
        (DispatchSubmissionQueryResult(status="not_submitted"), ExternalExecutionState.NEVER_SUBMITTED),
        (DispatchSubmissionQueryResult(status="unknown", error_code="query_uncertain"), ExternalExecutionState.UNKNOWN),
    ),
)
def test_reconcile_query_distinguishes_all_six_states_and_never_submits(query, expected) -> None:
    repository = Repository(FROZEN)
    prepared = PreparedProvider(query)

    result = ExternalRecoveryService(repository=repository, provider=Provider(prepared)).reconcile(_reconcile())

    assert result["status"] == "recorded"
    assert repository.prepared == 1
    assert len(prepared.query_calls) == 1
    assert prepared.query_calls == [("provider-key-frozen", "remote-1")]
    assert repository.commits[0][2].external_state is expected
    assert prepared.submit_calls == 0


def test_capability_projection_is_read_only_and_fail_closed() -> None:
    query_capable = ExternalRecoveryService(
        repository=Repository(FROZEN),
        provider=Provider(PreparedProvider(None, query_capable=True)),
    ).capabilities()
    no_query = ExternalRecoveryService(
        repository=Repository(FROZEN),
        provider=Provider(PreparedProvider(None, query_capable=False)),
    ).capabilities()

    assert query_capable.query_supported is True
    assert query_capable.abandon_supported is False
    assert no_query.query_supported is False
    assert no_query.abandon_supported is False
    assert production_external_recovery_capabilities().query_supported is False
    assert production_external_recovery_capabilities().abandon_supported is False


def test_no_query_capability_fails_closed_before_query_or_write() -> None:
    repository = Repository(FROZEN)
    prepared = PreparedProvider(None, query_capable=False)

    with pytest.raises(ExternalRecoveryNotAllowed, match="provider_external_query_unsupported"):
        ExternalRecoveryService(repository=repository, provider=Provider(prepared)).reconcile(_reconcile())

    assert repository.prepared == 0
    assert prepared.query_calls == []
    assert repository.commits == []


def test_query_failure_stays_unknown_and_is_never_interpreted_as_not_submitted() -> None:
    repository = Repository(FROZEN)

    class Failing(PreparedProvider):
        def query_external_result(self, **kwargs):
            raise TimeoutError("fictional-sensitive-canary")

    result = ExternalRecoveryService(repository=repository, provider=Provider(Failing(None))).reconcile(_reconcile())

    assert result["status"] == "recorded"
    assert repository.commits[0][2].external_state is ExternalExecutionState.UNKNOWN
    assert repository.commits[0][2].error_code == "provider_query_result_unknown"


def test_query_provider_is_the_only_reconciliation_resolution() -> None:
    assert tuple(ReconcileResolution) == (ReconcileResolution.QUERY_PROVIDER,)


def _retry(**updates) -> ExplicitRetryCommand:
    values = dict(
        task_id="task-1",
        work_unit_id="work-1",
        dispatch_id="dispatch-1",
        execution_decision_id="decision-1",
        prior_capability_call_id="call-1",
        prior_attempt_number=1,
        command_request_id="retry-request-1",
        canonical_payload_fingerprint="c" * 64,
        expected_task_revision=9,
        owner_id="owner-1",
        workspace_id="workspace-1",
        actor_id="actor-1",
        reason_code="transport_never_started_transient",
    )
    values.update(updates)
    return ExplicitRetryCommand(**values)


def test_explicit_retry_rejects_stale_initial_command_but_replays_committed_command() -> None:
    repository = Repository(FROZEN.model_copy(update={"external_state": ExternalExecutionState.NEVER_SUBMITTED, "remote_run_id": None}))
    service = ExternalRecoveryService(repository=repository, provider=None)

    with pytest.raises(ExternalRecoveryConflict, match="retry_frozen_fact_conflict"):
        service.retry(_retry(expected_task_revision=1))
    assert repository.retry_commands == []
    command = _retry()
    first = service.retry(command)
    repository.frozen = repository.frozen.model_copy(update={"expected_task_revision": 10})
    second = service.retry(command)

    assert first == second
    assert first == {"status": "created", "capabilityCallId": "call-2", "attemptNumber": 2}
    assert len(repository.retry_commands) == 1
    assert FROZEN.provider_idempotency_key == "provider-key-frozen"


@pytest.mark.parametrize(
    "state",
    (
        ExternalExecutionState.UNKNOWN,
        ExternalExecutionState.SUBMITTED,
        ExternalExecutionState.RUNNING,
        ExternalExecutionState.COMPLETED,
    ),
)
def test_retry_rejects_external_states_that_may_still_be_running(state) -> None:
    repository = Repository(FROZEN.model_copy(update={"external_state": state}))
    with pytest.raises(ExternalRecoveryNotAllowed):
        ExternalRecoveryService(repository=repository, provider=None).retry(_retry())
    assert repository.retry_commands == []


def test_known_failure_retry_uses_repository_as_single_authority() -> None:
    repository = Repository(FROZEN.model_copy(update={
        "external_state": ExternalExecutionState.KNOWN_FAILED,
        "remote_run_id": None,
    }))
    service = ExternalRecoveryService(repository=repository, provider=None)
    command = _retry(reason_code="user_confirmed_explicit_retry")

    result = service.retry(command)

    assert result == {"status": "created", "capabilityCallId": "call-2", "attemptNumber": 2}
    assert len(repository.retry_commands) == 1
    durable = repository.retry_commands[0]
    assert durable.task_id == "task-1"
    assert durable.work_unit_id == "work-1"
    assert durable.dispatch_id == "dispatch-1"
    assert durable.prior_attempt_number == 1


def test_unknown_submission_never_gains_structured_output_retry_permission() -> None:
    repository = Repository(FROZEN.model_copy(update={
        "external_state": ExternalExecutionState.UNKNOWN,
        "remote_run_id": None,
    }))

    with pytest.raises(ExternalRecoveryNotAllowed, match="retry_requires_proven_safe_outcome"):
        ExternalRecoveryService(repository=repository, provider=None).retry(
            _retry(reason_code="user_confirmed_structured_output_retry")
        )

    assert repository.retry_commands == []


def test_proven_no_action_unknown_creates_one_independent_retry_attempt() -> None:
    frozen = FROZEN.model_copy(update={
        "remote_run_id": None,
        "proven_no_external_action_retry": True,
    })
    repository = Repository(frozen)
    service = ExternalRecoveryService(repository=repository, provider=None)
    command = _retry(reason_code="system_pure_reasoning_result_recovery")

    first = service.retry(command)
    assert service.retry(command) == first
    assert len(repository.retry_commands) == 1
    assert first["capabilityCallId"] != command.prior_capability_call_id
    assert first["attemptNumber"] == command.prior_attempt_number + 1
    assert frozen.external_state is ExternalExecutionState.UNKNOWN


def test_no_action_proof_does_not_authorize_known_active_remote_run() -> None:
    repository = Repository(FROZEN.model_copy(update={
        "proven_no_external_action_retry": True,
    }))
    with pytest.raises(ExternalRecoveryNotAllowed):
        ExternalRecoveryService(repository=repository, provider=None).retry(_retry())
    assert repository.retry_commands == []


@pytest.mark.parametrize("other_blocker", [None, "other_unknown", "user_input", "no_proof"])
def test_proven_reasoning_retry_consumes_only_its_own_real_unknown_blocker(
    monkeypatch, other_blocker,
) -> None:
    from app.modules import workbench_user_actions
    from app.modules.research_blocker_semantics import ResearchBlocker, current_blocker_facts

    task = r0c_canonical(
        work_status="external_result_unknown", reason="external_result_unknown",
    )
    work = task.work_units[0]
    decision = task.execution_decisions[0]
    call = task.capability_calls[0]
    facts = current_blocker_facts(task)
    assert len(facts) == 1
    assert facts[0].kind == "external_result_unknown"
    assert facts[0].work_unit_id == work.work_unit_id
    assert facts[0].blocking_work_unit_ids == ()
    if other_blocker == "other_unknown":
        facts += (ResearchBlocker("external_result_unknown", "unknown", "other-work"),)
    elif other_blocker == "user_input":
        facts += (ResearchBlocker("user_input", "authorization_required", "other-work"),)
    monkeypatch.setattr(workbench_user_actions, "current_blocker_facts", lambda _: facts)

    reason = workbench_user_actions.explicit_retry_reason(
        canonical=task, work_unit_id=work.work_unit_id,
        execution_decision_id=decision.execution_decision_id,
        prior_capability_call_id=call.capability_call_id,
        prior_attempt_number=call.attempt_number,
        dispatch_status="submission_unknown", external_state="unknown",
        dispatch_remote_run_id=None, external_remote_run_id=None,
        last_error_code="external_action_outcome_pending",
        proven_no_external_action_retry=other_blocker != "no_proof",
    )
    assert reason == ("system_pure_reasoning_result_recovery" if other_blocker is None else None)
    assert work.status.value == "external_result_unknown"
    assert call.status.value == "external_result_unknown"


def _frozen_reasoning_retry_case():
    work = SimpleNamespace(
        work_unit_id="work-1", execution_decision_id="decision-1",
        substage_definition_id="research:judgment", substage_definition_version=3,
        executor_ref=SimpleNamespace(
            executor_type="agent", executor_id="agent-1", definition_version=2,
        ),
        allowed_skill_refs=[
            SimpleNamespace(skill_id=skill, definition_version=1)
            for skill in ("intent_extraction", "question_formulation", "task_decomposition")
        ],
    )
    decision = SimpleNamespace(
        execution_decision_id="decision-1",
        registry_snapshot_ref=SimpleNamespace(content_hash="sha256:" + "a" * 64),
    )
    task = SimpleNamespace(work_units=[work], execution_decisions=[decision])
    snapshot = {"definitions": {
        "substages": [{"definitionId": "research:judgment", "definitionVersion": 3,
                       "externalActionContract": {"status": "known", "requiredActionKinds": []}}],
        "agents": [{"agentId": "agent-1", "definitionVersion": 2, "agentKind": "general_research",
                    "budgetPolicy": {"maxToolCalls": 0}}],
        "skills": [
            {"skillId": ref.skill_id, "definitionVersion": 1,
             "sourceContract": {"runtime_contract": {
                 "kind": "llm_reasoning", "requires_external_action": False,
             }}}
            for ref in work.allowed_skill_refs
        ],
    }}
    row = _retry_validation_row(
        status="submission_unknown", external_state="unknown", result_storage_ref=None,
        lease_expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    return task, snapshot, row


@pytest.mark.parametrize("invalid", [
    None, "unknown_contract", "external_action", "tools", "wrong_version",
    "active_lease", "saved_result", "remote_run", "permission_action", "permission_unknown",
])
def test_unknown_reasoning_retry_requires_frozen_no_action_facts(invalid) -> None:
    from app.modules.research_external_recovery_persistence import no_external_action_retry_proven

    task, snapshot, row = _frozen_reasoning_retry_case()
    snapshot = deepcopy(snapshot)
    definition = snapshot["definitions"]["substages"][0]
    if invalid == "unknown_contract":
        definition["externalActionContract"] = {"status": "unknown", "requiredActionKinds": []}
    elif invalid == "external_action":
        definition["externalActionContract"]["requiredActionKinds"] = ["code_execution"]
    elif invalid == "tools":
        snapshot["definitions"]["agents"][0]["budgetPolicy"]["maxToolCalls"] = 1
    elif invalid == "wrong_version":
        definition["definitionVersion"] = 2
    elif invalid == "active_lease":
        row["lease_expires_at"] = datetime.now(UTC) + timedelta(minutes=1)
    elif invalid == "saved_result":
        row["result_storage_ref"] = "durable-result"
    elif invalid == "remote_run":
        row["external_remote_run_id"] = "remote-existing"
    elif invalid == "permission_action":
        snapshot["definitions"]["skills"][0]["sourceContract"]["runtime_contract"] = {
            "kind": "external_adapter", "requires_external_action": True,
        }
    elif invalid == "permission_unknown":
        snapshot["definitions"]["skills"][0]["sourceContract"] = {}
    repository = SimpleNamespace(load_registry_snapshot=lambda digest: snapshot)

    assert no_external_action_retry_proven(repository, task, row) is (invalid is None)


def _retry_validation_row(**updates):
    row = {
        "execution_decision_id": "decision-1",
        "work_unit_id": "work-1",
        "capability_call_id": "call-1",
        "attempt_number": 1,
        "owner_id": "owner-1",
        "workspace_id": "workspace-1",
        "provider_idempotency_key": "provider-key",
        "idempotency_key": "provider-key",
        "external_state": ExternalExecutionState.KNOWN_FAILED.value,
        "external_remote_run_id": None,
        "remote_run_id": None,
        "status": "failed",
        "last_error_code": "provider_structured_output_correction_exhausted",
    }
    row.update(updates)
    return row


def test_repository_allows_only_exact_structured_known_failure_for_retry() -> None:
    source = r0c_canonical(reason="provider_structured_output_correction_exhausted")
    decision = source.execution_decisions[0].model_copy(
        update={"execution_decision_id": "decision-1"}
    )
    work = source.work_units[0].model_copy(
        update={
            "work_unit_id": "work-1",
            "execution_decision_id": "decision-1",
            "idempotency_key": "provider-key",
        }
    )
    call = source.capability_calls[0].model_copy(
        update={"capability_call_id": "call-1", "work_unit_id": "work-1"}
    )
    task = source.model_copy(
        update={
            "task_id": "task-1",
            "execution_decisions": [decision],
            "work_units": [work],
            "capability_calls": [call],
        }
    )
    command = _retry(
        reason_code="user_confirmed_structured_output_retry", expected_task_revision=task.revision,
    )

    ResearchOrchestrationRepository._validate_retry_row(task, command, _retry_validation_row())

    with pytest.raises(ExternalRecoveryStateError, match="retry_requires_proven_safe_outcome"):
        ResearchOrchestrationRepository._validate_retry_row(
            task,
            command,
            _retry_validation_row(last_error_code="provider_execution_invalid"),
        )

    with pytest.raises(ExternalRecoveryStateError, match="retry_requires_proven_safe_outcome"):
        ResearchOrchestrationRepository._validate_retry_row(
            task,
            command,
            _retry_validation_row(
                external_state=ExternalExecutionState.UNKNOWN.value,
                last_error_code="provider_structured_output_correction_exhausted",
            ),
        )

def test_recovery_directive_is_durable_before_injected_materialization_and_missing_port_is_pending() -> None:
    repository = Repository(FROZEN)
    repository.result = {"status": "directive_pending", "directiveId": "directive-1"}
    query = DispatchSubmissionQueryResult(status="not_submitted")

    pending = ExternalRecoveryService(repository=repository, provider=Provider(PreparedProvider(query))).reconcile(_reconcile())

    assert pending == {"status": "directive_pending", "directiveId": "directive-1"}
    port = RecoveryPort()
    materialized = ExternalRecoveryService(
        repository=repository, provider=Provider(PreparedProvider(query)), recovery_materialization_port=port
    ).reconcile(_reconcile())
    assert materialized["status"] == "materialized"
    assert len(port.calls) == 1


def test_command_payload_fingerprint_is_required_and_not_derived_from_request_id() -> None:
    with pytest.raises(ValueError):
        _reconcile(canonical_payload_fingerprint="reconcile-request-1")
    with pytest.raises(ValueError):
        _retry(canonical_payload_fingerprint="retry-request-1")


def proof_case(monkeypatch):
    work = SimpleNamespace(
        work_unit_id="w",
        execution_decision_id="d",
        substage_instance_id="i",
        substage_instance_revision=1,
        research_design_version=1,
        status="failed",
        executor_ref=SimpleNamespace(executor_type="skill", executor_id="s", definition_version=1),
    )
    decision = SimpleNamespace(
        execution_decision_id="d",
        substage_instance_id="i",
        substage_instance_revision=1,
        research_design_version=1,
        decision_sequence=1,
        decided_at=datetime.now(UTC),
        registry_snapshot_ref=SimpleNamespace(content_hash="sha256:" + "a" * 64),
    )
    task = SimpleNamespace(work_units=[work], execution_decisions=[decision])
    snapshot = {
        "definitions": {
            "skills": [
                {
                    "skillId": "s",
                    "definitionVersion": 1,
                    "sourceContract": {
                        "runtime_contract": {
                            "kind": "deterministic",
                            "requires_external_action": False,
                        }
                    },
                }
            ]
        }
    }
    row = dict(
        status="failed",
        external_state="known_failed",
        work_unit_id="w",
        execution_decision_id="d",
        capability_call_id="c",
        dispatch_id="x",
    )
    cp = SimpleNamespace(
        capability_call_id="c",
        execution_kind="direct_skill_runtime",
        phase="prepared",
        dispatch_id="x",
        response_payload=None,
    )
    from app.modules import external_execution_checkpoint

    monkeypatch.setattr(
        external_execution_checkpoint,
        "ExternalExecutionCheckpointRepository",
        lambda: SimpleNamespace(get=lambda **kwargs: cp),
    )
    return task, snapshot, row, cp


@pytest.mark.parametrize(
    "bad",
    [
        None,
        "reasoning",
        "external",
        "unfrozen",
        "active",
        "remote",
        "saved",
        "wrong_checkpoint",
        "response",
        "checkpoint_phase",
        "same_instance_newer",
        "work_identity",
        "no_checkpoint",
        "wrong_call",
    ],
)
def test_frozen_known_failed_proof(monkeypatch, bad):
    from app.modules import research_external_recovery_persistence as persistence

    task, snapshot, row, cp = proof_case(monkeypatch)
    if bad == "reasoning":
        snapshot["definitions"]["skills"][0]["sourceContract"]["runtime_contract"]["kind"] = (
            "reasoning_adapter"
        )
    if bad == "external":
        snapshot["definitions"]["skills"][0]["sourceContract"]["runtime_contract"][
            "requires_external_action"
        ] = True
    if bad == "unfrozen":
        snapshot["definitions"]["skills"] = []
    if bad == "active":
        row["lease_expires_at"] = datetime.now(UTC) + timedelta(minutes=1)
    if bad == "remote":
        row["external_remote_run_id"] = "remote"
    if bad == "saved":
        row["result_storage_ref"] = "saved"
    if bad == "wrong_checkpoint":
        cp.dispatch_id = "other"
    if bad == "response":
        cp.response_payload = {"result": "saved"}
    if bad == "checkpoint_phase":
        cp.phase = "completed"
    if bad == "wrong_call":
        cp.capability_call_id = "other"
    if bad == "no_checkpoint":
        from app.modules import external_execution_checkpoint

        monkeypatch.setattr(
            external_execution_checkpoint,
            "ExternalExecutionCheckpointRepository",
            lambda: SimpleNamespace(get=lambda **kwargs: None),
        )
    if bad == "work_identity":
        task.work_units[0].execution_decision_id = "other"
    if bad == "same_instance_newer":
        newer = deepcopy(task.execution_decisions[0])
        newer.execution_decision_id = "newer"
        newer.decision_sequence = 2
        task.execution_decisions.append(newer)
    repository = SimpleNamespace(load_registry_snapshot=lambda _: snapshot)
    assert persistence.no_external_action_retry_proven(repository, task, row) is (
        bad in {None, "reasoning"}
    )


def authorization_case():
    from app.models.research_orchestration import ExecutionDecisionOutcome

    task = r0c_canonical(reason="provider_structured_output_correction_exhausted")
    work = task.work_units[0].model_copy(
        update={
            "status": type(task.work_units[0].status)("queued"),
            "finished_at": None,
            "terminal_reason_code": None,
        }
    )
    decision = task.execution_decisions[0].model_copy(
        update={
            "status": type(task.execution_decisions[0].status)("closed"),
            "outcome": ExecutionDecisionOutcome.SUCCEEDED,
        }
    )
    newer = decision.model_copy(
        update={
            "execution_decision_id": "newer",
            "decision_sequence": decision.decision_sequence + 1,
            "substage_instance_id": "another-instance",
        }
    )
    task = task.model_copy(update={"work_units": [work], "execution_decisions": [decision, newer]})
    dispatch = dict(
        execution_decision_id=decision.execution_decision_id,
        work_unit_id=work.work_unit_id,
        idempotency_key=work.idempotency_key,
    )
    return task, work, decision, newer, dispatch


@pytest.mark.parametrize(
    "bad", [None, "no_proof", "same_instance_newer", "different_design", "wrong_binding"]
)
def test_only_current_confirmed_instance_can_restore_older_decision(bad):
    from app.modules.research_orchestration_repository import DispatchAuthorizationError

    task, work, decision, newer, dispatch = authorization_case()
    if bad == "same_instance_newer":
        task.execution_decisions[1] = newer.model_copy(
            update={"substage_instance_id": decision.substage_instance_id}
        )
    if bad == "different_design":
        task.execution_decisions[0] = decision.model_copy(update={"research_design_version": 99})
    if bad == "wrong_binding":
        dispatch["idempotency_key"] = "different"
    if bad is None:
        assert (
            ResearchOrchestrationRepository._validate_known_failed_safe_retry_authorization(
                task, dispatch, proven_no_external_action_retry=True
            )
            == work
        )
    else:
        with pytest.raises(DispatchAuthorizationError):
            ResearchOrchestrationRepository._validate_known_failed_safe_retry_authorization(
                task, dispatch, proven_no_external_action_retry=bad != "no_proof"
            )


def test_reopen_preserves_failed_call_and_other_artifacts():
    task, work, decision, newer, dispatch = authorization_case()
    payload = task.model_dump(mode="json", by_alias=True)
    before_calls = deepcopy(payload["capabilityCalls"])
    before_artifacts = deepcopy(payload["artifacts"])
    target = payload["executionDecisions"][0]
    command = SimpleNamespace(
        execution_decision_id=decision.execution_decision_id, work_unit_id=work.work_unit_id
    )
    assert (
        ResearchOrchestrationRepository._reopen_closed_blocked_decision_for_known_failed_safe_retry(
            payload,
            command=command,
            occurred_at=datetime.now(UTC),
            proven_no_external_action_retry=True,
        )
    )
    assert target["status"] == "dispatched"
    assert payload["capabilityCalls"] == before_calls
    assert payload["artifacts"] == before_artifacts
    assert payload["executionDecisions"][1]["executionDecisionId"] == "newer"
