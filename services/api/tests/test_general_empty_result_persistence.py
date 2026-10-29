"""Empty General output remains durable output, never completion evidence."""

from datetime import UTC, datetime
from hashlib import sha256

import pytest
from app.execution.research_dispatch_provider import DispatchSubmissionCompleted
from app.models.research_orchestration import (
    CapabilityCallV12,
    JudgmentRequirement,
    ResearchTaskV12,
)
from app.modules.research_finalization import record_scientific_state_from_durable_facts
from app.modules.research_orchestration_repository import (
    AuthorizedDispatch,
    ExternalAttemptIdentity,
    ResearchOrchestrationRepository,
)

from tests.test_general_completion_continuity import _running_completion


@pytest.mark.parametrize(
    "content,usable",
    [
        ("{}", False),
        ('{"reasoning_summary":"", "assumptions":[], "uncertainty":null}', False),
        ('{"reasoning_summary":"Evidence supports a bounded association",'
         '"uncertainty":["mechanism unknown"]}', True),
    ],
)
def test_general_result_materialization_preserves_empty_output_without_counting_completion(
    content, usable, monkeypatch
):
    task, decision, work = _running_completion()
    now = datetime(2026, 10, 10, tzinfo=UTC)
    payload = task.model_dump(mode="json", by_alias=True)
    call = CapabilityCallV12.model_validate({
        "capabilityCallId": "call-general-output",
        "idempotencyKey": "call-general-output",
        "attemptNumber": 1,
        "workUnitId": work["workUnitId"],
        "substageInstanceId": work["substageInstanceId"],
        "requestingActorRef": work["responsibleActorRef"],
        "capabilityType": "agent_runtime",
        "capabilityId": work["executorRef"]["executorId"],
        "runtimeBindingId": None,
        "status": "running",
        "inputRefs": [],
        "outputArtifactRefs": [],
        "startedAt": now.isoformat(),
        "finishedAt": None,
    })
    payload["capabilityCalls"] = [call.model_dump(mode="json", by_alias=True)]
    task = ResearchTaskV12.model_validate(payload)
    fence = AuthorizedDispatch(
        dispatch_id="dispatch-general-output", task_id=task.task_id,
        execution_decision_id=decision["executionDecisionId"],
        work_unit_id=work["workUnitId"], idempotency_key=work["idempotencyKey"],
        lease_owner="isolated-test", attempt_count=1,
        authorization_task_revision=task.revision, work_unit_payload=work,
        external_attempt=ExternalAttemptIdentity(call.capability_call_id, 1),
    )
    repository = object.__new__(ResearchOrchestrationRepository)
    # The production mutation/decision close runs; only outer DB snapshot/lifecycle
    # I/O is excluded. This is an aggregate materialization test, not DB acceptance.
    monkeypatch.setattr(
        repository, "_reconcile_task_lifecycle_with_frozen_snapshot",
        lambda current, **kwargs: current,
    )
    updated, catalog, _dispatches, _message = repository._task_with_completed_result(
        task, fence=fence,
        result=DispatchSubmissionCompleted(
            title="General result", mime_type="application/json", content=content,
            provider_id="openai_primary", adapter="huiyan_chat_completions",
        ),
        artifact_contract="general_research_reasoning_artifact_v1",
        artifact_schema_digest="a" * 64,
        content_sha256=sha256(content.encode()).hexdigest(),
        storage_type="local", storage_scope=task.task_id,
        storage_ref="isolated/general-result.json", now=now,
        produced_artifact_type=work["expectedArtifactTypes"][0],
    )
    artifact = updated.artifacts[-1]
    assert updated.results[-1].content_sha256 == sha256(content.encode()).hexdigest()
    assert catalog["metadata"]["validatorResult"]["contentSha256"] == sha256(
        content.encode()
    ).hexdigest()
    assert updated.work_units[-1].status == "completed"
    assert updated.capability_calls[-1].status == "succeeded"
    assert (artifact.artifact_state == "usable") is usable
    design = updated.research_design_versions[0].model_copy(deep=True)
    # Also exercise deterministic completion's artifact-presence path, which
    # does not depend on an approved professional acceptance interaction.
    design.instances[0].acceptance_criteria[0].judgment_requirement = (
        JudgmentRequirement.DETERMINISTIC
    )
    _projected, state = record_scientific_state_from_durable_facts(
        updated, design, occurred_at=now,
    )
    criterion = state["executionState"]["substageCompletion"][0]["criteria"][0]
    assert (criterion["status"] == "satisfied") is usable
    assert (artifact.artifact_id in state["executionState"]["validatedArtifactRefs"]) is usable
    if not usable:
        assert artifact.artifact_state == "partial_success"
        assert updated.substage_runtime_states[0].status != "completed"
        assert not any(
            row.interaction_type == "acceptance_recorded"
            for row in updated.agent_interaction_records
        )
