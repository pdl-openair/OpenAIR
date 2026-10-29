"""Canonical-shaped R0-C fixtures for deterministic projection/command owners.

These are real contract model objects. model_construct deliberately bypasses
aggregate admission: no Provider, registry, database, or lifecycle admission is
being simulated. Full admission/transaction fixtures remain their own tests.
"""

from datetime import datetime, timezone

from app.models.research_orchestration import (
    ActorRef, ArtifactV12, AssignmentRole, CapabilityCallStatus, CapabilityCallV12, ExecutionAssignment, ExecutionDecision,
    ExecutionDecisionOutcome, ExecutionDecisionStatus, ExecutionPurpose,
    ResearchDesignRef, ResearchDesignStatus, ResearchDesignVersion, ResearchTaskV12,
    StageKey, SubstageInstance, SubstageRuntimeState, SubstageRuntimeStatus,
    TaskExecutionMode, TaskStatus, WorkUnit, WorkUnitSourceMode, WorkUnitStatus,
)


NOW = datetime(2026, 10, 5, tzinfo=timezone.utc)


def r0c_canonical(
    *, lane="ot", task_status="running", runtime_status="blocked_recoverable",
    reason="provider_configuration", work_status="failed", decision_status="dispatched",
    decision_outcome=None,
):
    """One current research step, with exact decision/instance/work identities."""
    instance = SubstageInstance.model_construct(
        substage_instance_id="sub-1", instance_revision=1,
        substage_definition_id=f"{lane}:research_step", substage_definition_version=1,
        stage_key=StageKey.EXECUTION_VALIDATION,
        objective="核对当前证据是否支持研究判断", input_refs=[],
        expected_artifact_types=["research_evidence"], acceptance_criteria=[], dependencies=[],
        sequence_hint=0,
    )
    design = ResearchDesignVersion.model_construct(
        research_design_id="design-1", task_id="task_1", version=1,
        status=ResearchDesignStatus.CONFIRMED, instances=[instance],
        workflow_provenance_ref=None,
    )
    decision = ExecutionDecision.model_construct(
        execution_decision_id="decision_1", selection_request_id="selection-1",
        decision_sequence=1, task_id="task_1", research_design_version=1,
        substage_instance_id="sub-1", substage_instance_revision=1,
        status=ExecutionDecisionStatus(decision_status), purpose=ExecutionPurpose.PROGRESS,
        outcome=ExecutionDecisionOutcome(decision_outcome) if decision_outcome else None,
        assignments=[ExecutionAssignment.model_construct(
            assignment_id="assignment-1", role=AssignmentRole.PRIMARY,
            objective="核对当前证据是否支持研究判断",
        )], assignment_preflight_outcomes=[], decided_at=NOW, closed_at=None,
    )
    work = WorkUnit.model_construct(
        work_unit_id="wu_1", task_id="task_1", idempotency_key="work:decision-1:assignment-1",
        execution_decision_id="decision_1", assignment_id="assignment-1",
        research_design_version=1, substage_instance_id="sub-1", substage_instance_revision=1,
        substage_definition_id=instance.substage_definition_id, substage_definition_version=1,
        status=WorkUnitStatus(work_status), terminal_reason_code=reason,
        source_mode=WorkUnitSourceMode.LIVE, dispatchable=True,
        responsible_actor_ref=ActorRef(actor_type="agent", actor_id="research-agent", definition_version=1),
        instruction="核对当前证据是否支持研究判断", execution_group_id=None,
        input_artifact_refs=[], input_memory_refs=[],
        dependency_work_unit_ids=[], dispatched_at=NOW, frozen_at=NOW,
        finished_at=NOW if work_status in {"completed", "failed", "cancelled"} else None,
    )
    runtime = SubstageRuntimeState.model_construct(
        substage_instance_id="sub-1", instance_revision=1,
        status=SubstageRuntimeStatus(runtime_status), status_reason_code=reason,
        active_execution_decision_id=None if decision_status == "closed" else "decision_1",
        work_unit_ids=["wu_1"], artifact_refs=[], updated_at=NOW,
    )
    return ResearchTaskV12.model_construct(
        task_id="task_1", revision=8, status=TaskStatus(task_status),
        execution_mode=TaskExecutionMode.AUTOMATIC_RESEARCH,
        pending_execution_mode=None, execution_policy_version="execution-policy-v1",
        active_research_design_ref=ResearchDesignRef(research_design_id="design-1", version=1),
        planning_attempts=[], research_design_versions=[design], reentry_execution_instances=[],
        substage_runtime_states=[runtime], execution_decisions=[decision], work_units=[work],
        capability_calls=[CapabilityCallV12.model_construct(
            capability_call_id="call_1", work_unit_id="wu_1", attempt_number=1,
            substage_instance_id="sub-1",
            status=CapabilityCallStatus.EXTERNAL_RESULT_UNKNOWN if work_status == "external_result_unknown" else CapabilityCallStatus.FAILED,
        )], results=[], artifacts=[], confirmations=[],
        agent_interaction_records=[], started_at=NOW, finished_at=None,
    )


def retained_artifact(*, state="usable", artifact_id="artifact-before-failure", version=1):
    return ArtifactV12.model_validate({
        "artifactId": artifact_id, "workUnitId": "wu-completed",
        "substageInstanceId": "sub-completed", "sourceCapabilityCallId": None,
        "artifactType": "research_evidence", "artifactState": state, "version": version,
        "sourceRefs": ["source-evidence-1"],
        "validatorResult": {"contentSha256": "a" * 64},
        "lineageKind": "direct_work_unit_output",
    })
