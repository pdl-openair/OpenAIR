"""Instance-level research execution selection and frozen materialization.

This module is deliberately provider-free.  It consumes the complete Resolver
evidence, validates the frozen policy, and builds values that a PostgreSQL UoW
can commit atomically with Task CAS, fairness, and the dispatch outbox.
"""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Any, Callable, Mapping, Protocol, Sequence

from app.config import settings
from app.execution.research_input_contract_gate import (
    InputContractGateFailure,
    execution_input_candidate_from_assignment,
    resolve_and_validate_execution_input,
)
from app.models.research_orchestration import (
    ActorRef,
    AgentRef,
    ArtifactRequirement,
    ArtifactState,
    AssignmentPreflightOutcome,
    AssignmentRole,
    AuthorizationSnapshot,
    BudgetSnapshot,
    CandidateEvaluation,
    CandidateExecutionBlockerCode,
    CompletionCoverage,
    CompletionPolicy,
    DependencyConditionType,
    DispatchRecord,
    DispatchStatus,
    ExecutionAssignment,
    ExecutionDecision,
    ExecutionDecisionOutcome,
    ExecutionDecisionStatus,
    ExecutionPurpose,
    ExecutionStrategy,
    ExecutorRef,
    FallbackReasonCode,
    InstanceRef,
    JudgmentRequirement,
    MultiAgentRecipeDefinition,
    MultiAgentRecipeRef,
    ProgressPolicy,
    ProgressResultCode,
    RegistrySnapshotRef,
    ResearchDesignStatus,
    ResearchDesignVersion,
    ResearchTaskV12,
    ResultAssemblyMode,
    ResultAssemblyPolicy,
    ReviewerRejectAction,
    ReviewRequirement,
    RiskClass,
    RiskSnapshot,
    RuntimeReadinessSnapshot,
    SkillRef,
    StageKey,
    SubstageInstance,
    SubstageRuntimeStatus,
    SuccessMode,
    TaskExecutionMode,
    TaskStatus,
    WorkUnit,
    WorkUnitSourceMode,
    WorkUnitStatus,
    execution_instance_for_ref,
    execution_instances_for_design,
)
from app.modules.canonical_execution_lineage import CanonicalExecutionLineageError
from app.modules.capability_applicability_producer import (
    EXECUTION_INSTANCE_FACT_REF,
    RESEARCH_TASK_FACT_REF,
    SCIENTIFIC_STATE_FACT_REF,
    ApplicabilityEvaluatorResolver,
    produce_capability_applicability_results,
)
from app.modules.catalog_execution_binding import (
    binding_from_public,
    bound_definition_refs,
)
from app.modules.catalog_execution_binding import (
    candidate_identity as catalog_candidate_identity,
)
from app.modules.conditional_capability_binding import (
    CapabilityApplicabilityStatus,
    evaluate_skill_binding_eligibility,
)
from app.modules.registry_snapshot_repository import (
    execution_snapshot_matches_planning,
    registry_envelope_ref,
)
from app.modules.research_blocker_semantics import (
    needs_user_input,
    prohibited_external_action,
    research_gap_continues,
)
from app.modules.research_finalization import (
    finalize_from_validated_facts,
    record_scientific_state_from_durable_facts,
    settle_finalized_lifecycle,
)
from app.modules.research_orchestration_resolver import (
    ResolutionFacts,
    ResolutionRejectedError,
    ResolutionResult,
    state_outstanding_criterion_ids,
)
from app.modules.research_scientific_state_authority import current_scientific_state
from app.modules.research_user_input import canonical_user_query

DECISION_POLICY_VERSION = "execution-selection.v1"
FAIRNESS_POLICY_VERSION = "equivalent_candidate_fairness.v1"
_POST_CONFIRM_USER_CONFIRMATION_REASON_BY_MODE = {
    TaskExecutionMode.STAGE_CONFIRMATION: "stage_confirmation_required",
    TaskExecutionMode.MANUAL_STEP: "manual_step_confirmation_required",
}
_EXPLICIT_CONTINUATION_USER_GATE_REASONS = frozenset({
    *_POST_CONFIRM_USER_CONFIRMATION_REASON_BY_MODE.values(),
    "reviewer_human_gate_required",
})

def _explicit_execution_confirmation_pending(task: ResearchTaskV12) -> bool:
    """Keep only user-selected stage/manual execution pauses after intent confirmation."""
    expected_reason = _POST_CONFIRM_USER_CONFIRMATION_REASON_BY_MODE.get(task.execution_mode)
    if expected_reason is None:
        expected_reasons = {"reviewer_human_gate_required"}
    else:
        expected_reasons = {expected_reason, "reviewer_human_gate_required"}
    return any(
        runtime.status == SubstageRuntimeStatus.READY
        and runtime.status_reason_code in expected_reasons
        for runtime in task.substage_runtime_states
    )


def materialize_execution_continuation_pause(
    task: ResearchTaskV12,
    *,
    reason_code: str | None,
    next_instance_id: str | None,
    occurred_at: datetime,
) -> ResearchTaskV12:
    """Persist an explicit user-owned continuation gate as runtime facts.

    Stage/manual continuation intentionally does not preselect the scientifically
    next instance.  When no instance is selected, mark every currently READY
    candidate with the same gate reason instead of storing only a transient task
    status.  Resuming execution consumes this gate before the next Decision is
    materialized.
    """
    if reason_code not in _EXPLICIT_CONTINUATION_USER_GATE_REASONS:
        return task
    payload = task.model_dump(mode="json", by_alias=True)
    targets = []
    for runtime in payload["substageRuntimeStates"]:
        if runtime["status"] != SubstageRuntimeStatus.READY.value:
            continue
        if next_instance_id is not None and runtime["substageInstanceId"] != next_instance_id:
            continue
        targets.append(runtime)
    if not targets:
        return task
    timestamp = occurred_at.astimezone(timezone.utc).isoformat()
    for runtime in targets:
        runtime["statusReasonCode"] = reason_code
        runtime["updatedAt"] = timestamp
    return ResearchTaskV12.model_validate(payload)


DURABLE_START_BLOCKER_CODES = frozenset(
    {
        "authorization_required",
        "budget_expansion_required",
        "policy_prohibited",
        "llm_runtime_unavailable",
        "runtime_unavailable",
        "limited_coverage",
        "provided_materials_only",
        "canonical_workflow_lineage_mismatch",
        "canonical_skill_composition_not_frozen",
        "canonical_skill_composition_unavailable",
        "execution_selection_invocation_context_unsatisfied",
    }
)


class ResearchExecutionDecisionError(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}:{detail}" if detail else code)


class DecisionPolicyViolation(ResearchExecutionDecisionError):
    pass


class SelectionRequestConflictError(ResearchExecutionDecisionError):
    pass


class ExecutionPreflightBlocked(ResearchExecutionDecisionError):
    def __init__(self, code: str, *, facts: Mapping[str, Any] | None = None) -> None:
        self.facts = dict(facts or {})
        super().__init__(code)


class RuntimeReadinessInvalid(ResearchExecutionDecisionError):
    pass




def _load_canonical_workflow_lineage(
    repository: Any,
    *,
    task_id: str,
    owner_id: str,
    workspace_id: str,
) -> Mapping[str, Any] | None:
    try:
        return repository.load_execution_workflow_lineage(
            task_id=task_id,
            owner_id=owner_id,
            workspace_id=workspace_id,
        )
    except CanonicalExecutionLineageError as exc:
        raise ExecutionPreflightBlocked(exc.code) from exc


@dataclass(frozen=True)
class PreflightResult:
    instance: SubstageInstance
    missing_reason_code: str | None
    missing_facts: Mapping[str, Any]

    @property
    def executable(self) -> bool:
        return self.missing_reason_code is None


@dataclass(frozen=True)
class FairnessDescriptor:
    policy_version: str
    owner_id: str
    workspace_id: str
    registry_snapshot_content_hash: str
    substage_definition_ref: str
    equivalence_set_hash: str
    candidate_identities: tuple[str, ...]


@dataclass(frozen=True)
class PreparedSelection:
    task: ResearchTaskV12
    instance: SubstageInstance
    resolution: ResolutionResult
    snapshot_envelope: Mapping[str, Any]
    runtime_readiness: Mapping[str, RuntimeReadinessSnapshot]
    selection_request_id: str
    selection_command_hash: str
    selection_payload_hash: str
    expected_task_revision: int
    owner_id: str
    workspace_id: str
    final_tie_candidate_ids: tuple[str, ...]
    fairness: FairnessDescriptor | None
    decided_at: datetime
    invocation_context: Mapping[str, Any] = None  # normalized to a frozen dict by prepare_selection
    required_skill_sequence: tuple[SkillRef, ...] = ()
    composition_identity: str | None = None
    execution_method: Mapping[str, Any] | None = None
    execution_method_identity: str | None = None
    execution_method_role_candidates: tuple[tuple[str, str], ...] = ()
    effective_acceptance_criteria: tuple[Any, ...] | None = None
    effective_expected_artifact_types: tuple[str, ...] | None = None
    next_task_scientific_state_version: int | None = None
    next_task_rationale: str | None = None


@dataclass(frozen=True)
class PreparedWorkUnit:
    assignment: ExecutionAssignment
    work_unit_id: str
    idempotency_key: str
    input_artifact_refs: tuple[str, ...]
    input_artifact_snapshots: tuple[Mapping[str, Any], ...]
    input_memory_refs: tuple[str, ...]
    invocation_context: Mapping[str, Any]
    dependency_work_unit_ids: tuple[str, ...]
    risk_snapshot: RiskSnapshot
    resolved_parameters: Mapping[str, Any] | None = None
    resolved_execution_policy: Mapping[str, Any] | None = None
    input_scientific_state_version: int | None = None


@dataclass(frozen=True)
class ExecutionPreparation:
    task: ResearchTaskV12
    decision: ExecutionDecision
    work_units: tuple[PreparedWorkUnit, ...]
    selected_candidate_identity: str


@dataclass(frozen=True)
class ValidatedSelectionPreparation:
    initial: ExecutionPreparation
    validated: ExecutionPreparation


@dataclass(frozen=True)
class ExecutionMaterialization:
    task: ResearchTaskV12
    decision: ExecutionDecision
    work_units: tuple[WorkUnit, ...]
    dispatches: tuple[DispatchRecord, ...]
    selected_candidate_identity: str


@dataclass(frozen=True)
class SelectionCommitResult:
    task: ResearchTaskV12
    decision: ExecutionDecision
    replayed: bool


@dataclass(frozen=True)
class BlockedSelectionReplay:
    reason_code: str
    facts: Mapping[str, Any]


@dataclass(frozen=True)
class SelectionReplayEvidence:
    instance_id: str
    selection_command_hash: str
    replay: SelectionCommitResult | BlockedSelectionReplay


@dataclass(frozen=True)
class AssignmentRelease:
    task: ResearchTaskV12
    work_units: tuple[WorkUnit, ...]
    dispatches: tuple[DispatchRecord, ...]


@dataclass(frozen=True)
class ExecutionContinuationPlan:
    action: str
    next_instance_id: str | None
    reason_code: str | None


@dataclass(frozen=True)
class ExecutionOutcomeResolution:
    """Canonical semantic resolution for one durable execution fact.

    Producers report physical facts (accepted / known failed / unknown). They do
    not decide whether the research task is terminal. That decision belongs here.
    """

    system_action: str
    work_unit_status: WorkUnitStatus
    decision_outcome: ExecutionDecisionOutcome | None


def resolve_execution_outcome(outcome: str) -> ExecutionOutcomeResolution:
    """Map durable execution facts to one canonical semantic disposition.

    This deliberately does not inspect error codes. A newly introduced provider,
    Skill, schema, grounding, or reviewer error therefore cannot acquire terminal
    authority merely by returning a different code.
    """
    normalized = str(outcome or "").strip()
    if normalized == "accepted":
        return ExecutionOutcomeResolution(
            system_action="continue",
            work_unit_status=WorkUnitStatus.RUNNING,
            decision_outcome=None,
        )
    if normalized in {"known_rejected", "known_failed", "not_submitted"}:
        return ExecutionOutcomeResolution(
            system_action="recover",
            work_unit_status=WorkUnitStatus.BLOCKED,
            # One failed WorkUnit is not enough authority to choose the aggregate
            # Decision outcome. close_decision_from_durable_facts decides that
            # from all required durable facts, preserving partial/degraded progress.
            decision_outcome=None,
        )
    if normalized == "unknown":
        return ExecutionOutcomeResolution(
            system_action="reconcile",
            work_unit_status=WorkUnitStatus.EXTERNAL_RESULT_UNKNOWN,
            decision_outcome=None,
        )
    raise DecisionPolicyViolation("execution_outcome_invalid")


@dataclass(frozen=True)
class RecoveryMaterializationRequest:
    recovery_identity: str
    directive_kind: str
    task_id: str
    original_execution_decision_id: str
    original_work_unit_id: str
    selection_request_id: str
    expected_task_revision: int
    owner_id: str
    workspace_id: str


class RecoveryMaterializationPort(Protocol):
    def materialize_recovery(
        self,
        request: RecoveryMaterializationRequest,
        *,
        resolution: ResolutionResult,
        facts: ResolutionFacts,
        runtime_readiness: Mapping[str, RuntimeReadinessSnapshot],
    ) -> SelectionCommitResult: ...


def _completed_runtime_has_safe_recovery_target(task, original, work_unit_id, snapshot) -> bool:
    if (
        snapshot is None
        or original.status is not ExecutionDecisionStatus.CLOSED
        or original.outcome is not ExecutionDecisionOutcome.SUCCEEDED
    ):
        return False
    work = next(
        (
            row
            for row in task.work_units
            if row.work_unit_id == work_unit_id
            and row.execution_decision_id == original.execution_decision_id
        ),
        None,
    )
    if (
        work is None
        or work.status not in {WorkUnitStatus.BLOCKED, WorkUnitStatus.FAILED}
        or work.executor_ref.executor_type != "skill"
    ):
        return False
    calls = [row for row in task.capability_calls if row.work_unit_id == work_unit_id]
    if any(
        row.status
        in {
            WorkUnitStatus.QUEUED,
            WorkUnitStatus.RUNNING,
            WorkUnitStatus.REVIEWING,
            WorkUnitStatus.EXTERNAL_RESULT_UNKNOWN,
        }
        for row in task.work_units
    ):
        return False
    active = task.active_research_design_ref
    design = next(
        (
            row
            for row in task.research_design_versions
            if active is not None
            and row.research_design_id == active.research_design_id
            and row.version == active.version
        ),
        None,
    )
    if (
        design is None
        or design.status is not ResearchDesignStatus.CONFIRMED
        or design.version != original.research_design_version
        or not any(
            row.substage_instance_id == original.substage_instance_id
            and row.instance_revision == original.substage_instance_revision
            for row in design.instances
        )
    ):
        return False
    latest = max(
        (
            row
            for row in task.execution_decisions
            if row.substage_instance_id == original.substage_instance_id
            and row.substage_instance_revision == original.substage_instance_revision
            and row.research_design_version == original.research_design_version
        ),
        key=lambda row: (row.decision_sequence, row.decided_at, row.execution_decision_id),
        default=None,
    )
    if latest is None or latest.execution_decision_id != original.execution_decision_id:
        return False
    from app.execution.durable_skill_runtime import (
        _frozen_execution_contract_matches,
        resolve_registered_durable_skill,
        resolve_registered_durable_skill_runtime,
    )

    frozen = next(
        (
            row
            for row in snapshot.get("definitions", {}).get("skills", [])
            if row.get("skillId") == work.executor_ref.executor_id
            and row.get("definitionVersion") == work.executor_ref.definition_version
        ),
        None,
    )
    live = resolve_registered_durable_skill(work.executor_ref.executor_id)
    if (
        frozen is None
        or live is None
        or resolve_registered_durable_skill_runtime(
            work.executor_ref.executor_id,
            definition_version=work.executor_ref.definition_version,
            snapshot_definition=live,
        )
        is None
    ):
        return False
    if calls:
        if any(str(row.status) != "failed" or row.finished_at is None for row in calls):
            return False
        contract = live.get("runtimeContract") or {}
        if contract.get("requires_external_action"):
            from app.modules.external_execution_checkpoint import (
                ExternalExecutionCheckpointRepository,
            )

            for call in calls:
                checkpoint = ExternalExecutionCheckpointRepository().get(capability_call_id=call.capability_call_id)
                if checkpoint is None or checkpoint.phase not in {"response_received", "canonical_completed"}:
                    return False
                try:
                    response = json.loads(checkpoint.response_payload or b"null")
                except (UnicodeDecodeError, json.JSONDecodeError):
                    return False
                if not isinstance(response, dict) or response.get("state") != "failed":
                    return False
                if response.get("failed_stage") not in {"input_validation", "workspace", "git_preflight", "provider_preflight"}:
                    return False
    elif work.terminal_reason_code != "dispatch_runtime_unavailable":
        return False
    old_schemas = (frozen.get("sourceContract") or {}).get("frozenSchemas") or {}
    new_schemas = (live.get("sourceContract") or {}).get("frozenSchemas") or {}
    if (
        not old_schemas.get("output")
        or not new_schemas.get("output")
        or (not calls and old_schemas["output"].get("sha256") == new_schemas["output"].get("sha256"))
    ):
        return False
    compatible = deepcopy(frozen)
    compatible["sourceContract"]["frozenSchemas"]["output"] = deepcopy(new_schemas["output"])
    return _frozen_execution_contract_matches(live, compatible)


def _recovery_preflight_task(
    task: ResearchTaskV12,
    original: ExecutionDecision,
    *, recovery_work_unit_id: str | None = None, frozen_snapshot: Mapping[str, Any] | None = None,
) -> ResearchTaskV12:
    """Temporarily reopen only the original recoverable runtime for policy preflight."""
    payload = task.model_dump(mode="json", by_alias=True)
    runtime = next(
        (
            row
            for row in payload["substageRuntimeStates"]
            if row["substageInstanceId"] == original.substage_instance_id
            and row["instanceRevision"] == original.substage_instance_revision
        ),
        None,
    )
    if runtime is None:
        raise DecisionPolicyViolation("recovery_runtime_missing")
    if runtime.get("activeExecutionDecisionId") is not None:
        raise DecisionPolicyViolation("recovery_runtime_still_active")
    if runtime.get("status") == "completed" and _completed_runtime_has_safe_recovery_target(
        task, original, recovery_work_unit_id, frozen_snapshot,
    ):
        runtime["status"] = "ready"
    if runtime.get("status") not in {
        "ready",
        "needs_input",
        "blocked_recoverable",
        "insufficient_evidence",
    }:
        raise DecisionPolicyViolation("recovery_runtime_not_recoverable")
    runtime["status"] = "ready"
    return ResearchTaskV12.model_validate(payload)


class ExecutionSelectionRepositoryPort(Protocol):
    def load(self, *, task_id: str, owner_id: str, workspace_id: str) -> ResearchTaskV12: ...

    def load_execution_workflow_lineage(
        self, *, task_id: str, owner_id: str, workspace_id: str
    ) -> Mapping[str, Any] | None: ...

    def lookup_selection_replay(
        self,
        *,
        task_id: str,
        owner_id: str,
        workspace_id: str,
        selection_request_id: str,
        selection_command_hash: str,
    ) -> SelectionCommitResult | BlockedSelectionReplay | None: ...

    def lookup_selection_replay_evidence(
        self,
        *,
        task_id: str,
        owner_id: str,
        workspace_id: str,
        selection_request_id: str,
    ) -> SelectionReplayEvidence | None: ...

    def record_needs_input(
        self,
        *,
        task_id: str,
        owner_id: str,
        workspace_id: str,
        expected_task_revision: int,
        substage_instance_id: str,
        reason_code: str,
        selection_request_id: str,
        selection_command_hash: str,
        blocker_facts: Mapping[str, Any] | None = None,
    ) -> ResearchTaskV12 | SelectionCommitResult | BlockedSelectionReplay: ...

    def commit_prepared_selection(
        self,
        prepared: PreparedSelection,
        freeze_selection: Callable[[ResearchTaskV12, str], ExecutionMaterialization],
    ) -> SelectionCommitResult: ...

    def load_registry_snapshot(self, content_hash: str) -> Mapping[str, Any]: ...

    def prepare_execution_registry_snapshot(
        self,
        *,
        planning_snapshot_ref: Mapping[str, Any],
        required_definition_refs: tuple[tuple[str, int], ...],
    ) -> Mapping[str, Any]: ...

    def read_execution_input_context(
        self,
        *,
        task_id: str,
        owner_id: str,
        workspace_id: str,
        research_design_version: int,
        input_artifact_refs: list[str] | tuple[str, ...],
        input_artifact_snapshots: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...],
        invocation_context: Mapping[str, Any] | None = None,
        max_total_bytes: int = 1_000_000,
    ) -> Mapping[str, Any]: ...

    def read_reviewer_execution_input_context(
        self,
        *,
        task_id: str,
        owner_id: str,
        workspace_id: str,
        research_design_version: int,
        input_artifact_refs: list[str] | tuple[str, ...],
        input_artifact_snapshots: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...],
        invocation_context: Mapping[str, Any] | None = None,
        max_total_bytes: int = 1_000_000,
    ) -> Mapping[str, Any]: ...

    def save(
        self,
        task: ResearchTaskV12,
        *,
        owner_id: str,
        workspace_id: str,
        expected_task_revision: int,
        pending_dispatches: list[DispatchRecord] | None = None,
        automatic_dispatch: bool = False,
    ) -> ResearchTaskV12: ...




def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _hash(value: Any) -> str:
    return "sha256:" + sha256(_canonical_json(value)).hexdigest()


def _selection_command_hash(
    *,
    task_id: str,
    instance_id: str,
    owner_id: str,
    workspace_id: str,
    selection_request_id: str,
    expected_task_revision: int,
    recovery_identity: str | None,
    invocation_context: Mapping[str, Any] | None = None,
) -> str:
    return _hash(
        {
            "taskId": task_id,
            "instanceId": instance_id,
            "ownerId": owner_id,
            "workspaceId": workspace_id,
            "selectionRequestId": selection_request_id,
            "expectedTaskRevision": expected_task_revision,
            "recoveryIdentity": recovery_identity,
            "invocationContext": dict(invocation_context or {}),
        }
    )


def _stable_id(prefix: str, *parts: str) -> str:
    digest = sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:32]
    return f"{prefix}:{digest}"


def candidate_identity(candidate: CandidateEvaluation) -> str:
    ref = candidate.candidate_ref
    if isinstance(ref, SkillRef):
        return f"skill:{ref.skill_id}:{ref.definition_version}"
    if isinstance(ref, AgentRef):
        return f"agent:{ref.agent_id}:{ref.definition_version}"
    if isinstance(ref, MultiAgentRecipeRef):
        return f"recipe:{ref.recipe_id}:{ref.recipe_version}"
    raise DecisionPolicyViolation("candidate_identity_unsupported")


def _system_blocked_selection_can_be_reevaluated(task, design, instance, runtime) -> bool:
    """Reevaluate current inputs for a terminal selection that never made work."""
    from app.modules.continuation_responsibility import user_responsibility_contract

    reason = runtime.status_reason_code
    if (
        runtime.status not in {SubstageRuntimeStatus.NEEDS_INPUT, SubstageRuntimeStatus.BLOCKED_RECOVERABLE}
        or runtime.active_execution_decision_id is not None
        or runtime.work_unit_ids
        or needs_user_input(reason)
        or prohibited_external_action(reason)
        or user_responsibility_contract(str(reason or "")) is not None
        or reason == "review_human_decision_required"
        or not (reason == "input_condition_insufficient" or research_gap_continues(reason))
    ):
        return False
    if any(
        row.substage_instance_id == instance.substage_instance_id
        and row.substage_instance_revision == instance.instance_revision
        and row.research_design_version == design.version
        for row in task.work_units
    ) or any(
        row.status in {WorkUnitStatus.QUEUED, WorkUnitStatus.RUNNING,
                       WorkUnitStatus.REVIEWING, WorkUnitStatus.EXTERNAL_RESULT_UNKNOWN}
        for row in task.work_units
    ):
        return False
    latest = max(
        (row for row in task.execution_decisions
         if row.substage_instance_id == instance.substage_instance_id
         and row.substage_instance_revision == instance.instance_revision
         and row.research_design_version == design.version),
        key=lambda row: (row.decision_sequence, row.decided_at, row.execution_decision_id),
        default=None,
    )
    return (latest is not None and latest.status is ExecutionDecisionStatus.CLOSED
            and latest.outcome is ExecutionDecisionOutcome.BLOCKED)


def evaluate_instance_preflight(
    task: ResearchTaskV12,
    *,
    instance_id: str,
    facts: ResolutionFacts | None = None,
    required_input_refs: Sequence[str] = (),
    recovery_instance_ref: InstanceRef | None = None,
) -> PreflightResult:
    if task.active_research_design_ref is None:
        raise ExecutionPreflightBlocked("active_design_missing")
    design = next(
        (
            row
            for row in task.research_design_versions
            if row.research_design_id == task.active_research_design_ref.research_design_id
            and row.version == task.active_research_design_ref.version
        ),
        None,
    )
    if design is None or design.status != ResearchDesignStatus.CONFIRMED:
        raise ExecutionPreflightBlocked("active_design_not_confirmed")
    instance = (
        execution_instance_for_ref(task, design, recovery_instance_ref)
        if recovery_instance_ref is not None
        and recovery_instance_ref.substage_instance_id == instance_id
        else next(
            (
                row
                for row in execution_instances_for_design(task, design)
                if row.substage_instance_id == instance_id
            ),
            None,
        )
    )
    runtime = next(
        (row for row in task.substage_runtime_states if row.substage_instance_id == instance_id),
        None,
    )
    if instance is None or runtime is None or runtime.instance_revision != instance.instance_revision:
        raise ExecutionPreflightBlocked("instance_revision_not_current")
    if runtime.status != SubstageRuntimeStatus.READY and not _system_blocked_selection_can_be_reevaluated(
        task, design, instance, runtime,
    ):
        raise ExecutionPreflightBlocked("instance_not_ready", facts={"status": runtime.status.value})
    if task.status in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}:
        raise ExecutionPreflightBlocked("task_not_executable", facts={"status": task.status.value})
    if any(
        row.substage_instance_id == instance_id
        and row.substage_instance_revision == instance.instance_revision
        and row.status == ExecutionDecisionStatus.DISPATCHED
        and any(
            work.execution_decision_id == row.execution_decision_id
            and work.status in {WorkUnitStatus.QUEUED, WorkUnitStatus.RUNNING, WorkUnitStatus.REVIEWING}
            for work in task.work_units
        )
        for row in task.execution_decisions
    ):
        raise ExecutionPreflightBlocked("active_dispatched_decision_exists")

    missing_reason, missing_facts = _dependency_preflight_block(task, instance)
    if missing_reason is not None:
        return PreflightResult(instance, missing_reason, missing_facts)

    available = facts.available_input_refs if facts is not None else set(instance.input_refs)
    missing_inputs = sorted(set(required_input_refs) - set(available))
    gap_facts = dict(missing_facts)
    if missing_reason and research_gap_continues(missing_reason):
        gap_facts = {**gap_facts, "researchGap": missing_reason}
    if missing_inputs:
        gap_facts = {
            **gap_facts,
            "inputRefs": missing_inputs,
            "researchGap": gap_facts.get("researchGap") or "required_input_missing",
        }
    if facts is not None:
        if not facts.budget_available:
            return PreflightResult(instance, "budget_expansion_required", {})
        if not facts.risk_allowed:
            return PreflightResult(
                instance,
                "policy_prohibited",
                {**gap_facts, "researchGap": "policy_prohibited"},
            )
    return PreflightResult(instance, None, gap_facts)


def _agent_definition(snapshot: Mapping[str, Any], ref: AgentRef) -> Mapping[str, Any]:
    rows = snapshot.get("definitions", {}).get("agents", [])
    matches = [
        row
        for row in rows
        if row.get("agentId") == ref.agent_id and row.get("definitionVersion") == ref.definition_version
    ]
    if len(matches) != 1 or not matches[0].get("enabled"):
        raise DecisionPolicyViolation("selected_agent_definition_invalid")
    return matches[0]


def _is_general(snapshot: Mapping[str, Any], candidate: CandidateEvaluation) -> bool:
    ref = candidate.candidate_ref
    return isinstance(ref, AgentRef) and _agent_definition(snapshot, ref).get("agentKind") == "general_research"


def _preference_key(candidate: CandidateEvaluation) -> tuple[Any, ...]:
    preference = candidate.preference
    semantic = {"exact": 0, "compatible": 1, "weak": 2}[preference.semantic_fit]
    risk = {"low": 0, "medium": 1, "high": 2, "critical": 3}[preference.risk_class.value]
    permission = {"narrow": 0, "standard": 1, "broad": 2}[preference.permission_breadth]
    cost = (1, 0.0) if preference.estimated_cost_amount is None else (0, preference.estimated_cost_amount)
    last_assigned = (
        (0, "")
        if preference.last_assigned_at is None
        else (1, preference.last_assigned_at.astimezone(timezone.utc).isoformat())
    )
    return (
        semantic,
        risk,
        permission,
        cost,
        preference.active_work_unit_count,
        last_assigned,
    )


def _fairness_ring(candidate_ids: Sequence[str], descriptor_seed: Mapping[str, Any]) -> tuple[str, ...]:
    seed = _canonical_json(descriptor_seed)
    return tuple(
        sorted(
            candidate_ids,
            key=lambda identity: sha256(seed + b"\x00" + identity.encode("utf-8")).digest(),
        )
    )


def _required_input_refs(snapshot: Mapping[str, Any], instance: SubstageInstance) -> tuple[str, ...]:
    definitions = snapshot.get("definitions", {}).get("substages", [])
    row = next(
        (
            item
            for item in definitions
            if item.get("definitionId") == instance.substage_definition_id
            and item.get("definitionVersion") == instance.substage_definition_version
        ),
        None,
    )
    if row is None:
        raise ExecutionPreflightBlocked("substage_definition_missing")
    values = (row.get("inputContract") or {}).get("requiredInputRefs") or {}
    if values.get("status") != "known":
        return ()
    return tuple(values.get("values") or ())


def _satisfied_human_gate_refs(
    task: ResearchTaskV12,
    instance: SubstageInstance,
) -> set[str]:
    declared_refs = {
        criterion.evaluator_ref
        for criterion in instance.acceptance_criteria
        if criterion.judgment_requirement == JudgmentRequirement.HUMAN_AUTHORITY
        and criterion.evaluator_ref is not None
    }
    return declared_refs & {
        row.target_id for row in task.confirmations if row.decision == "approved"
    }


def build_production_resolution_facts(
    task: ResearchTaskV12,
    instance: SubstageInstance,
    snapshot: Mapping[str, Any],
    *,
    applicability_evaluator_resolver: ApplicabilityEvaluatorResolver | None = None,
) -> tuple[ResolutionFacts, Mapping[str, RuntimeReadinessSnapshot]]:
    """Build readiness facts with deterministic Provider-schema admission and no Provider I/O."""
    from app.execution.durable_skill_runtime import (
        registered_durable_skill_provider_schema_executable,
        resolve_registered_durable_skill_runtime,
    )
    from app.modules.provider_registry import (
        load_provider_registry,
        resolve_provider_runtime_config,
    )

    now = datetime.now(timezone.utc)
    definition_identity = (
        instance.substage_definition_id,
        instance.substage_definition_version,
    )
    latest_state = current_scientific_state(task)
    authoritative_runtime_facts: dict[str, Any] = {
        RESEARCH_TASK_FACT_REF: task.model_dump(mode="json", by_alias=True),
        EXECUTION_INSTANCE_FACT_REF: instance.model_dump(mode="json", by_alias=True),
    }
    if latest_state is not None:
        authoritative_runtime_facts[SCIENTIFIC_STATE_FACT_REF] = latest_state.model_dump(
            mode="json", by_alias=True
        )
    applicability_results = produce_capability_applicability_results(
        snapshot=snapshot,
        definition_identity=definition_identity,
        authoritative_runtime_facts=authoritative_runtime_facts,
        evaluator_resolver=applicability_evaluator_resolver,
    )
    authoritative_fact_refs = set(authoritative_runtime_facts)
    definitions = snapshot.get("definitions", {})
    skill_definitions = {
        (row.get("skillId"), row.get("definitionVersion")): row
        for row in definitions.get("skills", [])
    }
    agent_definitions = {
        (row.get("agentId"), row.get("definitionVersion")): row
        for row in definitions.get("agents", [])
    }
    provider_registry = load_provider_registry()
    provider_readiness: dict[str, bool] = {}
    for agent in agent_definitions.values():
        for provider_id in agent.get("allowedProviders", []):
            if provider_id in provider_readiness:
                continue
            try:
                definition = provider_registry.require(provider_id)
                resolve_provider_runtime_config(provider_id, registry=provider_registry)
                safety = definition.submissionSafety
                provider_readiness[provider_id] = safety.supports_safe_submission
            except Exception:
                provider_readiness[provider_id] = False

    runtime_facts: dict[str, bool] = {}
    authorized: set[str] = set()
    readiness: dict[str, RuntimeReadinessSnapshot] = {}
    reviewer_definition = agent_definitions.get(("result_reliability_audit", 1)) or {}
    reviewer_provider_id = reviewer_definition.get("defaultProvider")
    reviewer_ready = bool(
        reviewer_definition.get("enabled")
        and isinstance(reviewer_definition.get("reviewContract"), Mapping)
        and reviewer_definition.get("reviewContract", {}).get("frozenArtifactContract")
        and provider_readiness.get(reviewer_provider_id, False)
    )
    reviewer_ref = AgentRef(agentId="result_reliability_audit", definitionVersion=1)
    readiness["agent:result_reliability_audit:1"] = RuntimeReadinessSnapshot(
        capabilityRef=reviewer_ref,
        ready=reviewer_ready,
        runtimeBindingId=(
            "registry:review-contract:result_reliability_audit:1" if reviewer_ready else None
        ),
        checkedAt=now,
        expiresAt=now + timedelta(minutes=5),
        reason=None if reviewer_ready else "review_runtime_unavailable",
    )
    for binding in snapshot.get("bindings", {}).get("skills", []):
        if (
            (binding.get("definitionId"), binding.get("definitionVersion")) != definition_identity
            or not binding.get("enabled")
        ):
            continue
        ref = SkillRef(
            skillId=binding["skillId"],
            definitionVersion=binding["skillDefinitionVersion"],
        )
        identity = f"skill:{ref.skill_id}:{ref.definition_version}"
        definition = skill_definitions.get((ref.skill_id, ref.definition_version)) or {}
        live_runtime = resolve_registered_durable_skill_runtime(
            ref.skill_id,
            definition_version=ref.definition_version,
            snapshot_definition=definition,
        )
        schema_executable = bool(
            live_runtime is not None
            and registered_durable_skill_provider_schema_executable(
                definition,
                adapter_id=str(live_runtime.get("adapterId") or ""),
            )
        )
        ready = bool(
            definition.get("enabled")
            and definition.get("status") == "ready"
            and live_runtime is not None
            and schema_executable
        )
        eligibility = evaluate_skill_binding_eligibility(
            binding,
            capability_ref=identity,
            authoritative_runtime_fact_refs=authoritative_fact_refs,
            applicability_results=applicability_results,
        )
        if eligibility is CapabilityApplicabilityStatus.ELIGIBLE:
            authorized.add(identity)
        runtime_facts[identity] = ready
        readiness[identity] = RuntimeReadinessSnapshot(
            capabilityRef=ref,
            ready=ready,
            runtimeBindingId=(
                f"registry:{binding['bindingId']}:{binding['bindingVersion']}" if ready else None
            ),
            checkedAt=now,
            expiresAt=now + timedelta(minutes=5),
            reason=(
                None
                if ready
                else "skill_provider_schema_incompatible"
                if live_runtime is not None and not schema_executable
                else "skill_runtime_or_result_check_unavailable"
            ),
        )
    for binding in snapshot.get("bindings", {}).get("agents", []):
        if (
            (binding.get("definitionId"), binding.get("definitionVersion")) != definition_identity
            or not binding.get("enabled")
        ):
            continue
        ref = AgentRef(
            agentId=binding["agentId"],
            definitionVersion=binding["agentDefinitionVersion"],
        )
        identity = f"agent:{ref.agent_id}:{ref.definition_version}"
        definition = agent_definitions.get((ref.agent_id, ref.definition_version)) or {}
        provider_id = definition.get("defaultProvider")
        ready = bool(definition.get("enabled") and provider_readiness.get(provider_id, False))
        authorized.add(identity)
        runtime_facts[identity] = ready
        readiness[identity] = RuntimeReadinessSnapshot(
            capabilityRef=ref,
            ready=ready,
            runtimeBindingId=(
                f"registry:{binding['bindingId']}:{binding['bindingVersion']}" if ready else None
            ),
            checkedAt=now,
            expiresAt=now + timedelta(minutes=5),
            reason=None if ready else "agent_provider_or_runtime_unavailable",
        )
    applicable_rules: set[str] = set()
    for recipe in snapshot.get("recipes", []):
        if (
            (recipe.get("substageDefinitionId"), recipe.get("substageDefinitionVersion"))
            != definition_identity
            or not recipe.get("enabled")
        ):
            continue
        authorized.add(f"recipe:{recipe['recipeId']}:{recipe['recipeVersion']}")
        applicable_rules.update(recipe.get("applicabilityRuleRefs", []))
    design = next(
        row
        for row in task.research_design_versions
        if task.active_research_design_ref is not None
        and row.research_design_id == task.active_research_design_ref.research_design_id
        and row.version == task.active_research_design_ref.version
    )
    boundary = design.budget_boundary
    budget_available = any(
        value is None or value > 0
        for value in (boundary.max_model_calls, boundary.max_tool_calls, boundary.max_cost_amount)
    )
    available_inputs = set(instance.input_refs) | {
        row.artifact_id for row in task.artifacts if row.artifact_state == ArtifactState.USABLE
    }
    facts = ResolutionFacts(
        runtimeReadiness=runtime_facts,
        providerReadiness=provider_readiness,
        authorizedCandidateRefs=authorized,
        availableInputRefs=available_inputs,
        applicableRuleRefs=applicable_rules,
        satisfiedHumanGateRefs=_satisfied_human_gate_refs(task, instance),
        riskAllowed=True,
        budgetAvailable=budget_available,
        policyVersion=snapshot.get("staticPolicies", {}).get("policyVersion", ""),
        activeWorkUnitCounts={},
        lastAssignedAt={},
        currentScientificState=(
            None
            if latest_state is None
            else latest_state.model_dump(mode="json", by_alias=True)
        ),
        authoritativeRuntimeFactRefs=authoritative_fact_refs,
        capabilityApplicabilityResults=applicability_results,
    )
    return facts, readiness


def _state_effective_requirements(
    instance: SubstageInstance,
    facts: ResolutionFacts,
) -> tuple[tuple[Any, ...], tuple[str, ...]]:
    """Project State-outstanding Design criteria into one continuation action.

    No runtime criterion is invented here: the confirmed instance remains the
    authority.  A current ScientificState narrows intentional re-entry to
    confirmed criteria that remain unresolved or unmet, so satisfied work is not
    replayed while recoverable failed requirements are not silently dropped.
    """
    outstanding_ids = state_outstanding_criterion_ids(instance, facts)
    if outstanding_ids is None:
        return tuple(instance.acceptance_criteria), tuple(instance.expected_artifact_types)
    criteria = tuple(
        criterion for criterion in instance.acceptance_criteria
        if criterion.criterion_id in outstanding_ids
    )
    # Expected artifact types are the instance's full output contract and must
    # NOT be narrowed by State outstanding-criteria.  A re-entered instance
    # (chain-2 round) may have every criterion already satisfied from round 1
    # (outstanding == empty set), yet it still must produce the same artifact
    # types to feed its downstream successors.  Narrowing types to the empty
    # set starves input composition for the re-entry dispatch (S7' failure).
    return criteria, tuple(instance.expected_artifact_types)


def constrain_resolution_facts_to_catalog_binding(
    facts: ResolutionFacts,
    *,
    snapshot: Mapping[str, Any],
    instance: SubstageInstance,
    binding_payload: Mapping[str, Any] | None,
) -> ResolutionFacts:
    """Make a promoted Catalog capability the sole execution authority.

    A missing binding keeps ordinary Research Start behavior unchanged.  A
    present but invalid/unbound binding authorizes no candidate, so the existing
    resolver/selection policy fails closed instead of falling back to a general
    Agent or another Skill.
    """
    if binding_payload is None:
        return facts
    try:
        binding = binding_from_public(binding_payload)
    except ValueError:
        return facts.model_copy(update={"authorized_candidate_refs": set()})
    if binding is None:
        return facts
    identity = catalog_candidate_identity(snapshot, binding)
    allowed_definitions = bound_definition_refs(snapshot, binding)
    current_definition = (
        instance.substage_definition_id,
        instance.substage_definition_version,
    )
    authorized = {identity} if identity is not None and current_definition in allowed_definitions else set()
    return facts.model_copy(update={"authorized_candidate_refs": authorized})


def _canonical_required_skill_sequence(
    snapshot: Mapping[str, Any],
    instance: SubstageInstance,
    design_ref: ResearchDesignRef,
    workflow_lineage: Mapping[str, Any] | None,
) -> tuple[SkillRef, ...]:
    rows = [
        row
        for row in snapshot.get("definitions", {}).get("substages", [])
        if row.get("definitionId") == instance.substage_definition_id
        and row.get("definitionVersion") == instance.substage_definition_version
    ]
    if len(rows) != 1:
        raise DecisionPolicyViolation("substage_definition_snapshot_mismatch")
    frozen_definition = rows[0]
    if frozen_definition.get("executionMethod") is not None:
        return ()
    composition = frozen_definition.get("executionComposition")
    if composition is None:
        canonical_workflow_lineage = bool(
            workflow_lineage is not None
            and workflow_lineage.get("workflowId")
            and workflow_lineage.get("skillPack")
            and workflow_lineage.get("planningAttemptId")
            and frozen_definition.get("pack") == workflow_lineage.get("skillPack")
        )
        if not canonical_workflow_lineage:
            return ()
        if (
            workflow_lineage.get("researchDesignId") != design_ref.research_design_id
            or workflow_lineage.get("researchDesignVersion") != design_ref.version
        ):
            raise ExecutionPreflightBlocked("canonical_workflow_lineage_mismatch")
        scope = snapshot.get("scope")
        if (
            snapshot.get("schemaVersion") == 2
            and isinstance(scope, Mapping)
            and scope.get("kind") == "execution"
        ):
            # v2 freezes research requirements during planning and resolves the
            # concrete capability only after confirmation. A Definition without
            # an explicit execution method/composition therefore uses the normal
            # Registry candidate-selection path. Historical v1 snapshots keep
            # the old fail-closed contract below.
            return ()
        raise ExecutionPreflightBlocked(
            "canonical_skill_composition_not_frozen",
            facts={
                "workflowId": workflow_lineage.get("workflowId"),
                "skillPack": workflow_lineage.get("skillPack"),
                "planningAttemptId": workflow_lineage.get("planningAttemptId"),
                "substageDefinitionId": instance.substage_definition_id,
                "substageDefinitionVersion": instance.substage_definition_version,
            },
        )
    if not isinstance(composition, Mapping) or composition.get("semantics") != "ordered_required":
        raise DecisionPolicyViolation("canonical_skill_composition_invalid")
    if (
        workflow_lineage is None
        or workflow_lineage.get("workflowId") != composition.get("workflowId")
        or workflow_lineage.get("skillPack") != composition.get("skillPack")
        or workflow_lineage.get("researchDesignId") != design_ref.research_design_id
        or workflow_lineage.get("researchDesignVersion") != design_ref.version
    ):
        raise ExecutionPreflightBlocked("canonical_workflow_lineage_mismatch")
    refs = tuple(SkillRef.model_validate(row) for row in composition.get("requiredSkillRefs") or [])
    if not refs:
        raise DecisionPolicyViolation("canonical_skill_composition_empty")
    return refs


def _frozen_execution_method(
    snapshot: Mapping[str, Any],
    instance: SubstageInstance,
) -> Mapping[str, Any] | None:
    rows = [
        row
        for row in snapshot.get("definitions", {}).get("substages", [])
        if row.get("definitionId") == instance.substage_definition_id
        and row.get("definitionVersion") == instance.substage_definition_version
    ]
    if len(rows) != 1:
        raise DecisionPolicyViolation("substage_definition_snapshot_mismatch")
    method = rows[0].get("executionMethod")
    if method is None:
        return None
    if not isinstance(method, Mapping):
        raise DecisionPolicyViolation("execution_method_invalid")
    return method


def _candidate_execution_method_roles(
    snapshot: Mapping[str, Any], candidate: CandidateEvaluation
) -> frozenset[str]:
    ref = candidate.candidate_ref
    if isinstance(ref, SkillRef):
        rows = [
            row for row in snapshot.get("definitions", {}).get("skills", [])
            if row.get("skillId") == ref.skill_id
            and row.get("definitionVersion") == ref.definition_version
        ]
    elif isinstance(ref, AgentRef):
        rows = [
            row for row in snapshot.get("definitions", {}).get("agents", [])
            if row.get("agentId") == ref.agent_id
            and row.get("definitionVersion") == ref.definition_version
        ]
    else:
        return frozenset()
    if len(rows) != 1:
        return frozenset()
    roles = rows[0].get("executionMethodRoles") or []
    if not isinstance(roles, list):
        return frozenset()
    return frozenset(str(value) for value in roles if isinstance(value, str) and value)


def _candidate_execution_method_role_evaluation(
    candidate: CandidateEvaluation, role_ref: str
):
    matches = [
        row for row in candidate.execution_method_role_evaluations
        if row.role_ref == role_ref
    ]
    if len(matches) != 1:
        return None
    return matches[0]


def _select_execution_method_roles(
    prepared_resolution: ResolutionResult,
    *,
    snapshot: Mapping[str, Any],
    method: Mapping[str, Any],
    selection_request_id: str,
) -> tuple[tuple[str, str], ...]:
    method_id = str(method.get("methodId") or "")
    selections: list[tuple[str, str]] = []
    for role in method.get("roles") or []:
        role_id = str(role.get("roleId") or "")
        role_ref = f"{method_id}:{role_id}"
        allowed_kinds = set(role.get("capabilityKinds") or [])
        qualified = []
        for row in prepared_resolution.candidate_evaluations:
            candidate_kind = (
                "skill" if isinstance(row.candidate_ref, SkillRef)
                else "agent" if isinstance(row.candidate_ref, AgentRef)
                else None
            )
            role_evaluation = _candidate_execution_method_role_evaluation(row, role_ref)
            if (
                candidate_kind in allowed_kinds
                and row.eligible
                and role_evaluation is not None
                and role_evaluation.eligible
                and role_ref in _candidate_execution_method_roles(snapshot, row)
            ):
                qualified.append(row)
        if not qualified:
            if role.get("requirement") == "optional":
                continue
            raise ExecutionPreflightBlocked(
                "no_qualified_capability",
                facts={"methodId": method_id, "roleId": role_id},
            )
        pool = [
            row
            for row in qualified
            if (
                (evaluation := _candidate_execution_method_role_evaluation(row, role_ref))
                is not None
                and evaluation.executable_now
            )
        ]
        if not pool:
            if role.get("requirement") == "optional":
                continue
            raise ExecutionPreflightBlocked(
                "capability_unavailable",
                facts={
                    "methodId": method_id,
                    "roleId": role_id,
                    "qualifiedCapabilityRefs": [
                        candidate_identity(row) for row in qualified
                    ],
                },
            )
        best = min(_preference_key(row) for row in pool)
        equivalent = [row for row in pool if _preference_key(row) == best]
        chosen = min(
            equivalent,
            key=lambda row: sha256(
                f"{DECISION_POLICY_VERSION}\x1f{selection_request_id}\x1f{method_id}\x1f{role_id}\x1f{candidate_identity(row)}".encode()
            ).digest(),
        )
        selections.append((role_id, candidate_identity(chosen)))
    return tuple(selections)


def _execution_method_identity(
    instance: SubstageInstance,
    method: Mapping[str, Any],
    role_candidates: Sequence[tuple[str, str]],
    snapshot_hash: str,
) -> str:
    seed = "|".join(
        [
            instance.substage_definition_id,
            str(instance.substage_definition_version),
            snapshot_hash,
            str(method.get("methodId")),
            str(method.get("methodVersion")),
            *[f"{role_id}={candidate_id}" for role_id, candidate_id in role_candidates],
        ]
    )
    return f"execution-method:{sha256(seed.encode('utf-8')).hexdigest()[:32]}"


def _method_role_by_id(method: Mapping[str, Any], role_id: str) -> Mapping[str, Any]:
    rows = [row for row in method.get("roles") or [] if row.get("roleId") == role_id]
    if len(rows) != 1:
        raise DecisionPolicyViolation("execution_method_role_invalid", role_id)
    return rows[0]


def _method_assignment_iteration(assignment: ExecutionAssignment) -> int:
    return assignment.execution_method_iteration or 1


def _method_assignments_for_role(
    assignments: Sequence[ExecutionAssignment], role_id: str
) -> list[ExecutionAssignment]:
    return sorted(
        [row for row in assignments if row.execution_method_role_id == role_id],
        key=_method_assignment_iteration,
    )


def _method_latest_assignment(
    assignments: Sequence[ExecutionAssignment],
    role_id: str,
    *,
    at_or_before: int | None = None,
) -> ExecutionAssignment | None:
    rows = _method_assignments_for_role(assignments, role_id)
    if at_or_before is not None:
        rows = [row for row in rows if _method_assignment_iteration(row) <= at_or_before]
    return rows[-1] if rows else None


def _method_reentry_transition_for_role(
    method: Mapping[str, Any], role_id: str
) -> Mapping[str, Any] | None:
    rows = [
        row
        for row in method.get("reentryTransitions") or []
        if role_id in (row.get("repeatRoleIds") or [])
    ]
    if len(rows) > 1:
        raise DecisionPolicyViolation("execution_method_reentry_role_ambiguous", role_id)
    return rows[0] if rows else None


def _method_assignment_is_current_iteration(
    decision: ExecutionDecision,
    method: Mapping[str, Any],
    assignment: ExecutionAssignment,
) -> bool:
    role_id = assignment.execution_method_role_id
    if role_id is None:
        return True
    transition = _method_reentry_transition_for_role(method, role_id)
    if transition is None:
        return True
    repeat_roles = set(transition.get("repeatRoleIds") or [])
    latest_iteration = max(
        (
            _method_assignment_iteration(row)
            for row in decision.assignments
            if row.execution_method_role_id in repeat_roles
        ),
        default=1,
    )
    return _method_assignment_iteration(assignment) == latest_iteration


def _method_reentry_continuation_pending(
    task: ResearchTaskV12, decision: ExecutionDecision
) -> bool:
    if decision.strategy is not ExecutionStrategy.EXECUTION_METHOD:
        return False
    work_by_assignment = {
        row.assignment_id: row
        for row in task.work_units
        if row.execution_decision_id == decision.execution_decision_id
    }
    assignments = list(decision.assignments)
    for assignment in assignments:
        work = work_by_assignment.get(assignment.assignment_id)
        if work is None or work.status is not WorkUnitStatus.COMPLETED:
            continue
        control = assignment.execution_method_role_control or {}
        for transition in control.get("reentryTransitions") or []:
            source = transition.get("source") if isinstance(transition, Mapping) else None
            if not isinstance(source, Mapping) or not _formal_state_condition_matches(task, source):
                continue
            next_iteration = _method_assignment_iteration(assignment) + 1
            restart_role = str(transition.get("restartRoleId") or "")
            if not any(
                row.execution_method_role_id == restart_role
                and _method_assignment_iteration(row) == next_iteration
                for row in assignments
            ):
                return True
    return False


def _formal_state_condition_matches(task: ResearchTaskV12, condition: Mapping[str, Any]) -> bool:
    if condition.get("kind") != "scientific_state_equals":
        return False
    state = current_scientific_state(task)
    if state is None:
        return False
    root: Any = {
        "scientificFacts": state.scientific_facts,
        "executionState": state.execution_state,
    }
    for part in condition.get("path") or []:
        if not isinstance(root, Mapping) or part not in root:
            return False
        root = root[part]
    return root == condition.get("equals")


def _execution_method_stop_triggered(
    task: ResearchTaskV12,
    *,
    decision: ExecutionDecision,
    method: Mapping[str, Any],
) -> bool:
    method_ref = decision.execution_method_ref
    if decision.strategy is not ExecutionStrategy.EXECUTION_METHOD or not isinstance(method_ref, Mapping):
        return False
    if (
        method_ref.get("methodId") != method.get("methodId")
        or method_ref.get("methodVersion") != method.get("methodVersion")
    ):
        raise DecisionPolicyViolation("execution_method_snapshot_mismatch")
    work_by_assignment = {
        row.assignment_id: row
        for row in task.work_units
        if row.execution_decision_id == decision.execution_decision_id
    }
    for condition in method.get("stopConditions") or []:
        after_role = str(condition.get("afterRoleId") or "")
        completed = any(
            (work := work_by_assignment.get(assignment.assignment_id)) is not None
            and work.status is WorkUnitStatus.COMPLETED
            for assignment in _method_assignments_for_role(decision.assignments, after_role)
        )
        if not completed:
            continue
        source = condition.get("source")
        if isinstance(source, Mapping) and _formal_state_condition_matches(task, source):
            return True
    return False


def _method_assignment_release_allowed(
    task: ResearchTaskV12, assignment: ExecutionAssignment
) -> bool:
    control = assignment.execution_method_role_control
    if control is None:
        return True
    release = control.get("releasePolicy")
    if not isinstance(release, Mapping):
        raise DecisionPolicyViolation("execution_method_release_policy_missing")
    if release.get("kind") == "always":
        return True
    return _formal_state_condition_matches(task, release)


def _preflight_blocker(decision: ExecutionDecision) -> AssignmentPreflightOutcome | None:
    return next((row for row in decision.assignment_preflight_outcomes
                 if row.failure_kind == "contract_violation"), None)


def _legacy_material_release_task(
    task: ResearchTaskV12, decision: ExecutionDecision
) -> ResearchTaskV12 | None:
    """Restore only an unfinished frozen method, preserving its historical checks."""
    if (
        decision.status is not ExecutionDecisionStatus.CLOSED
        or decision.outcome is not ExecutionDecisionOutcome.SUCCEEDED
    ):
        return None
    existing = {
        row.assignment_id
        for row in task.work_units
        if row.execution_decision_id == decision.execution_decision_id
    }
    if not any(
        row.failure_kind == "material_unavailable" and row.assignment_id not in existing
        for row in decision.assignment_preflight_outcomes
    ):
        return None
    active = task.active_research_design_ref
    design = next(
        (
            row
            for row in task.research_design_versions
            if active is not None
            and row.research_design_id == active.research_design_id
            and row.version == active.version
        ),
        None,
    )
    if (
        task.status is not TaskStatus.RUNNING
        or design is None
        or design.status is not ResearchDesignStatus.CONFIRMED
        or design.version != decision.research_design_version
        or not any(
            row.substage_instance_id == decision.substage_instance_id
            and row.instance_revision == decision.substage_instance_revision
            for row in design.instances
        )
    ):
        return None
    latest = max(
        (
            row
            for row in task.execution_decisions
            if row.research_design_version == decision.research_design_version
            and row.substage_instance_id == decision.substage_instance_id
            and row.substage_instance_revision == decision.substage_instance_revision
        ),
        key=lambda row: (row.decision_sequence, row.decided_at, row.execution_decision_id),
        default=None,
    )
    if latest is None or latest.execution_decision_id != decision.execution_decision_id:
        return None
    if any(
        row.status in {WorkUnitStatus.QUEUED, WorkUnitStatus.RUNNING, WorkUnitStatus.REVIEWING}
        for row in task.work_units
    ):
        return None
    reopened = decision.model_copy(
        update={"status": ExecutionDecisionStatus.DISPATCHED, "outcome": None, "closed_at": None}
    )
    staged = _task_with_prepared_decision(task, reopened)
    return staged.model_copy(
        update={
            "substage_runtime_states": [
                row.model_copy(
                    update={
                        "status": type(row.status)("queued"),
                        "active_execution_decision_id": decision.execution_decision_id,
                        "status_reason_code": None,
                    }
                )
                if row.substage_instance_id == decision.substage_instance_id
                and row.instance_revision == decision.substage_instance_revision
                else row
                for row in staged.substage_runtime_states
            ]
        }
    )


def _method_terminal_blocker(task: ResearchTaskV12, decision: ExecutionDecision) -> str | None:
    if decision.strategy is not ExecutionStrategy.EXECUTION_METHOD:
        return None
    preflight = _preflight_blocker(decision)
    if preflight is not None:
        return preflight.reason_code
    assignments = {row.assignment_id: row for row in decision.assignments}
    for work in task.work_units:
        if work.execution_decision_id != decision.execution_decision_id:
            continue
        if work.status not in {WorkUnitStatus.FAILED, WorkUnitStatus.BLOCKED}:
            continue
        control = assignments[work.assignment_id].execution_method_role_control or {}
        if control.get("failurePolicy") != "continue_degraded":
            return work.terminal_reason_code or "execution_method_role_failed"
    return None


def _task_with_prepared_decision(
    task: ResearchTaskV12, decision: ExecutionDecision,
) -> ResearchTaskV12:
    # Preparation only: no WorkUnit, Provider side effect or persistence.
    return task.model_copy(update={"execution_decisions": [
        *[row for row in task.execution_decisions
          if row.execution_decision_id != decision.execution_decision_id], decision,
    ]})


def _method_dependency_allows_release(
    decision: ExecutionDecision,
    dependency_assignment: ExecutionAssignment,
    work_unit: WorkUnit | None,
) -> bool:
    if work_unit is None:
        return False
    if work_unit.status is WorkUnitStatus.COMPLETED:
        return True
    if decision.strategy is not ExecutionStrategy.EXECUTION_METHOD:
        return False
    control = dependency_assignment.execution_method_role_control or {}
    return (
        control.get("failurePolicy") == "continue_degraded"
        and work_unit.status in {WorkUnitStatus.FAILED, WorkUnitStatus.BLOCKED}
    )


def _expand_execution_method_reentry(
    task: ResearchTaskV12,
    decision: ExecutionDecision,
    method: Mapping[str, Any],
) -> ExecutionDecision:
    """Append the next immutable method iteration when a frozen transition fires.

    Static role dependencies remain acyclic.  Reentry is represented by new
    assignment identities whose restart role also depends on the completed
    transition trigger from the previous iteration.
    """
    if decision.strategy is not ExecutionStrategy.EXECUTION_METHOD:
        return decision
    assignments = list(decision.assignments)
    work_by_assignment = {
        row.assignment_id: row
        for row in task.work_units
        if row.execution_decision_id == decision.execution_decision_id
    }
    changed = False
    method_roles = [str(row.get("roleId") or "") for row in method.get("roles") or []]
    for transition in method.get("reentryTransitions") or []:
        source = transition.get("source")
        if not isinstance(source, Mapping) or not _formal_state_condition_matches(task, source):
            continue
        after_role = str(transition.get("afterRoleId") or "")
        completed_triggers = [
            assignment
            for assignment in _method_assignments_for_role(assignments, after_role)
            if (
                (work := work_by_assignment.get(assignment.assignment_id)) is not None
                and work.status is WorkUnitStatus.COMPLETED
            )
        ]
        if not completed_triggers:
            continue
        trigger = completed_triggers[-1]
        next_iteration = _method_assignment_iteration(trigger) + 1
        repeat_roles = [str(role_id) for role_id in transition.get("repeatRoleIds") or []]
        if any(
            assignment.execution_method_role_id in repeat_roles
            and _method_assignment_iteration(assignment) == next_iteration
            for assignment in assignments
        ):
            continue

        new_ids: dict[str, str] = {}
        templates: dict[str, ExecutionAssignment] = {}
        for role_id in repeat_roles:
            template = _method_latest_assignment(
                assignments, role_id, at_or_before=_method_assignment_iteration(trigger)
            )
            if template is None:
                raise DecisionPolicyViolation(
                    "execution_method_reentry_template_missing", role_id
                )
            templates[role_id] = template
            new_ids[role_id] = _stable_id(
                "assignment",
                decision.execution_decision_id,
                (
                    f"execution-method:{role_id}:iteration:{next_iteration}:"
                    f"{template.executor_ref.executor_type}:"
                    f"{template.executor_ref.executor_id}:"
                    f"{template.executor_ref.definition_version}"
                ),
            )

        new_assignments: list[ExecutionAssignment] = []
        repeat_set = set(repeat_roles)
        for role_id in method_roles:
            if role_id not in repeat_set:
                continue
            template = templates[role_id]
            role = _method_role_by_id(method, role_id)
            dependencies: list[str] = []
            for dependency_role in role.get("dependsOn") or []:
                if dependency_role in repeat_set:
                    dependencies.append(new_ids[dependency_role])
                else:
                    dependency = _method_latest_assignment(
                        assignments,
                        dependency_role,
                        at_or_before=_method_assignment_iteration(trigger),
                    )
                    if dependency is not None:
                        dependencies.append(dependency.assignment_id)
            if role_id == transition.get("restartRoleId"):
                if trigger.assignment_id not in dependencies:
                    dependencies.append(trigger.assignment_id)
            payload = template.model_dump(mode="json", by_alias=True)
            payload.update(
                {
                    "assignmentId": new_ids[role_id],
                    "executionMethodIteration": next_iteration,
                    "dependencyAssignmentIds": dependencies,
                }
            )
            new_assignments.append(ExecutionAssignment.model_validate(payload))
        assignments.extend(new_assignments)
        changed = True

    if not changed:
        return decision

    payload = decision.model_dump(mode="json", by_alias=True)
    payload["assignments"] = [
        row.model_dump(mode="json", by_alias=True) for row in assignments
    ]
    completion_roles = [str(role_id) for role_id in method.get("completionRoleIds") or []]
    latest_completion_assignments = [
        assignment
        for role_id in completion_roles
        if (assignment := _method_latest_assignment(assignments, role_id)) is not None
    ]
    if not latest_completion_assignments:
        raise DecisionPolicyViolation("execution_method_completion_assignments_missing")
    required_ids = [row.assignment_id for row in latest_completion_assignments]
    if payload.get("completionPolicy") is not None:
        payload["completionPolicy"]["requiredAssignmentIds"] = required_ids
    result_policy = payload.get("resultAssemblyPolicy") or {}
    for requirement in result_policy.get("finalArtifactRequirements") or []:
        requirement["sourceAssignmentIds"] = list(required_ids)
    return ExecutionDecision.model_validate(payload)


def _composition_candidate_rows(
    resolution: ResolutionResult, required: Sequence[SkillRef]
) -> list[CandidateEvaluation]:
    rows: list[CandidateEvaluation] = []
    for ref in required:
        matches = [
            row
            for row in resolution.candidate_evaluations
            if isinstance(row.candidate_ref, SkillRef)
            and row.candidate_ref.skill_id == ref.skill_id
            and row.candidate_ref.definition_version == ref.definition_version
        ]
        if len(matches) != 1:
            raise ExecutionPreflightBlocked(
                "canonical_skill_composition_unavailable",
                facts={"skillId": ref.skill_id, "definitionVersion": ref.definition_version},
            )
        rows.append(matches[0])
    return rows


def _require_canonical_composition_available(
    rows: Sequence[CandidateEvaluation],
    *,
    snapshot: Mapping[str, Any],
    facts: ResolutionFacts,
    runtime_readiness: Mapping[str, RuntimeReadinessSnapshot],
) -> None:
    """Gate canonical OT Skills by durable availability, not generic semantic scoring."""
    for row in rows:
        identity = candidate_identity(row)
        readiness = runtime_readiness.get(identity)
        try:
            bindings = _binding_rows(snapshot, row)
        except DecisionPolicyViolation as exc:
            raise ExecutionPreflightBlocked(
                "canonical_skill_composition_unavailable",
                facts={"candidate": identity, "reason": exc.code},
            ) from exc
        if (
            identity not in facts.authorized_candidate_refs
            or not facts.risk_allowed
            or not facts.budget_available
            or not bindings
            or readiness is None
            or not readiness.ready
            or readiness.runtime_binding_id is None
        ):
            raise ExecutionPreflightBlocked(
                "canonical_skill_composition_unavailable",
                facts={
                    "candidate": identity,
                    "authorized": identity in facts.authorized_candidate_refs,
                    "riskAllowed": facts.risk_allowed,
                    "budgetAvailable": facts.budget_available,
                    "runtimeReady": bool(readiness and readiness.ready),
                },
            )


def _skill_composition_identity(
    instance: SubstageInstance, required: Sequence[SkillRef], snapshot_hash: str
) -> str:
    seed = "|".join(
        [
            instance.substage_definition_id,
            str(instance.substage_definition_version),
            snapshot_hash,
            *[f"{row.skill_id}:{row.definition_version}" for row in required],
        ]
    )
    return f"skill-composition:{sha256(seed.encode('utf-8')).hexdigest()[:32]}"


def prepare_selection(
    task: ResearchTaskV12,
    *,
    instance_id: str,
    resolution: ResolutionResult,
    snapshot_envelope: Mapping[str, Any],
    facts: ResolutionFacts,
    runtime_readiness: Mapping[str, RuntimeReadinessSnapshot],
    selection_request_id: str,
    selection_command_hash: str,
    expected_task_revision: int,
    owner_id: str,
    workspace_id: str,
    decided_at: datetime,
    recovery_identity: str | None = None,
    workflow_lineage: Mapping[str, Any] | None = None,
    invocation_context: Mapping[str, Any] | None = None,
    next_task_scientific_state_version: int | None = None,
    next_task_rationale: str | None = None,
    recovery_instance_ref: InstanceRef | None = None,
) -> PreparedSelection:
    if task.revision != expected_task_revision:
        raise ExecutionPreflightBlocked("task_revision_conflict")
    if recovery_instance_ref is not None and recovery_identity is None:
        raise DecisionPolicyViolation("recovery_instance_requires_identity")
    if not execution_snapshot_matches_planning(
        task.registry_snapshot_ref.model_dump(mode="json", by_alias=True),
        resolution.registry_snapshot_ref.model_dump(mode="json", by_alias=True),
        snapshot_envelope,
    ):
        raise DecisionPolicyViolation("resolver_snapshot_mismatch")
    active_design = next(
        (
            row
            for row in task.research_design_versions
            if task.active_research_design_ref is not None
            and row.research_design_id
            == task.active_research_design_ref.research_design_id
            and row.version == task.active_research_design_ref.version
        ),
        None,
    )
    required_input_instance = (
        execution_instance_for_ref(task, active_design, recovery_instance_ref)
        if active_design is not None and recovery_instance_ref is not None
        else next(
            row
            for design in task.research_design_versions
            if task.active_research_design_ref is not None
            and design.research_design_id
            == task.active_research_design_ref.research_design_id
            and design.version == task.active_research_design_ref.version
            for row in execution_instances_for_design(task, design)
            if row.substage_instance_id == instance_id
        )
    )
    if required_input_instance is None:
        raise ExecutionPreflightBlocked("instance_revision_not_current")
    preflight = evaluate_instance_preflight(
        task,
        instance_id=instance_id,
        facts=facts,
        required_input_refs=_required_input_refs(
            snapshot_envelope,
            required_input_instance,
        ),
        recovery_instance_ref=recovery_instance_ref,
    )
    if not preflight.executable:
        raise ExecutionPreflightBlocked(preflight.missing_reason_code or "preflight_blocked", facts=preflight.missing_facts)
    instance = preflight.instance
    effective_criteria, effective_expected_types = _state_effective_requirements(
        instance, facts
    )
    if (
        resolution.instance_ref.substage_instance_id != instance.substage_instance_id
        or resolution.instance_ref.instance_revision != instance.instance_revision
    ):
        raise DecisionPolicyViolation("resolver_instance_mismatch")

    evaluations = list(resolution.candidate_evaluations)
    if not evaluations:
        raise DecisionPolicyViolation("candidate_evaluation_empty")
    execution_method = _frozen_execution_method(snapshot_envelope, instance)
    if execution_method is not None:
        role_candidates = _select_execution_method_roles(
            resolution,
            snapshot=snapshot_envelope,
            method=execution_method,
            selection_request_id=selection_request_id,
        )
        method_identity = _execution_method_identity(
            instance,
            execution_method,
            role_candidates,
            resolution.registry_snapshot_ref.content_hash,
        )
        payload = {
            "taskId": task.task_id,
            "expectedTaskRevision": expected_task_revision,
            "designRef": resolution.design_ref.model_dump(mode="json", by_alias=True),
            "instanceRef": resolution.instance_ref.model_dump(mode="json", by_alias=True),
            "registrySnapshotRef": resolution.registry_snapshot_ref.model_dump(mode="json", by_alias=True),
            "candidateEvaluations": [row.model_dump(mode="json", by_alias=True) for row in evaluations],
            "runtimeReadiness": {
                key: value.model_dump(mode="json", by_alias=True)
                for key, value in sorted(runtime_readiness.items())
            },
            "executionMethod": dict(execution_method),
            "executionMethodRoleCandidates": [
                {"roleId": role_id, "candidateIdentity": candidate_id}
                for role_id, candidate_id in role_candidates
            ],
            "policyVersion": DECISION_POLICY_VERSION,
            "recoveryIdentity": recovery_identity,
        }
        return PreparedSelection(
            task=task,
            instance=instance,
            resolution=resolution,
            snapshot_envelope=snapshot_envelope,
            runtime_readiness=dict(runtime_readiness),
            selection_request_id=selection_request_id,
            selection_command_hash=selection_command_hash,
            selection_payload_hash=_hash(payload),
            expected_task_revision=expected_task_revision,
            owner_id=owner_id,
            workspace_id=workspace_id,
            final_tie_candidate_ids=(method_identity,),
            fairness=None,
            decided_at=decided_at.astimezone(timezone.utc),
            invocation_context=dict(invocation_context or {}),
            execution_method=dict(execution_method),
            execution_method_identity=method_identity,
            execution_method_role_candidates=role_candidates,
            effective_acceptance_criteria=effective_criteria,
            effective_expected_artifact_types=effective_expected_types,
            next_task_scientific_state_version=next_task_scientific_state_version,
            next_task_rationale=next_task_rationale,
        )
    required_skill_sequence = _canonical_required_skill_sequence(
        snapshot_envelope, instance, resolution.design_ref, workflow_lineage
    )
    composition_identity: str | None = None
    if required_skill_sequence:
        # One formal research responsibility may declare multiple expected
        # Artifact types for a single primary Skill (or ordered composition).
        # Multi-type expected contracts are no longer treated as limited
        # coverage; genuine lack of an executable candidate still fail-closes
        # below via composition availability / candidate pool checks.
        composition_rows = _composition_candidate_rows(resolution, required_skill_sequence)
        _require_canonical_composition_available(
            composition_rows,
            snapshot=snapshot_envelope,
            facts=facts,
            runtime_readiness=runtime_readiness,
        )
        if len(required_skill_sequence) == 1:
            final_ids = (candidate_identity(composition_rows[0]),)
        else:
            composition_identity = _skill_composition_identity(
                instance, required_skill_sequence, resolution.registry_snapshot_ref.content_hash
            )
            final_ids = (composition_identity,)
        payload = {
            "taskId": task.task_id,
            "expectedTaskRevision": expected_task_revision,
            "designRef": resolution.design_ref.model_dump(mode="json", by_alias=True),
            "instanceRef": resolution.instance_ref.model_dump(mode="json", by_alias=True),
            "registrySnapshotRef": resolution.registry_snapshot_ref.model_dump(mode="json", by_alias=True),
            "candidateEvaluations": [row.model_dump(mode="json", by_alias=True) for row in evaluations],
            "runtimeReadiness": {
                key: value.model_dump(mode="json", by_alias=True)
                for key, value in sorted(runtime_readiness.items())
            },
            "requiredSkillSequence": [row.model_dump(mode="json", by_alias=True) for row in required_skill_sequence],
            "workflowLineage": dict(workflow_lineage or {}),
            "policyVersion": DECISION_POLICY_VERSION,
            "recoveryIdentity": recovery_identity,
        }
        return PreparedSelection(
            task=task,
            instance=instance,
            resolution=resolution,
            snapshot_envelope=snapshot_envelope,
            runtime_readiness=dict(runtime_readiness),
            selection_request_id=selection_request_id,
            selection_command_hash=selection_command_hash,
            selection_payload_hash=_hash(payload),
            expected_task_revision=expected_task_revision,
            owner_id=owner_id,
            workspace_id=workspace_id,
            final_tie_candidate_ids=final_ids,
            fairness=None,
            decided_at=decided_at.astimezone(timezone.utc),
            invocation_context=dict(invocation_context or {}),
            required_skill_sequence=required_skill_sequence,
            composition_identity=composition_identity,
            effective_acceptance_criteria=effective_criteria,
            effective_expected_artifact_types=effective_expected_types,
        )
    executable = [row for row in evaluations if row.eligible and row.preference.executable_now]
    preferred_completion = [
        row
        for row in executable
        if not _is_general(snapshot_envelope, row)
        and row.preference.completion_coverage == CompletionCoverage.COMPLETION_CAPABLE
    ]
    # Multi expected Artifact types remain completion-capable when a single
    # producer Skill can satisfy them. limited_coverage is reserved for true
    # absence of an executable candidate (handled when pool is empty below).
    if preferred_completion:
        pool = preferred_completion
    else:
        non_fallback_progress = [row for row in executable if not _is_general(snapshot_envelope, row)]
        pool = non_fallback_progress or [row for row in executable if _is_general(snapshot_envelope, row)]
    if not pool:
        blockers = {
            code.value
            for row in evaluations
            for code in row.preference.execution_blocker_codes
        }
        if CandidateExecutionBlockerCode.PROVIDER_UNAVAILABLE.value in blockers:
            raise ExecutionPreflightBlocked("llm_runtime_unavailable")
        if CandidateExecutionBlockerCode.AUTHORIZATION_DENIED.value in blockers:
            raise ExecutionPreflightBlocked("authorization_required")
        if CandidateExecutionBlockerCode.BUDGET_EXCEEDED.value in blockers:
            raise ExecutionPreflightBlocked("budget_expansion_required")
        if CandidateExecutionBlockerCode.RISK_EXCEEDED.value in blockers:
            raise ExecutionPreflightBlocked("policy_prohibited")
        raise ExecutionPreflightBlocked("runtime_unavailable")

    best_key = min(_preference_key(row) for row in pool)
    final_tie = [row for row in pool if _preference_key(row) == best_key]
    final_ids = tuple(candidate_identity(row) for row in final_tie)
    descriptor_seed = {
        "policyVersion": FAIRNESS_POLICY_VERSION,
        "ownerId": owner_id,
        "workspaceId": workspace_id,
        "registrySnapshotContentHash": resolution.registry_snapshot_ref.content_hash,
        "substageDefinitionRef": f"{instance.substage_definition_id}:{instance.substage_definition_version}",
        "equivalenceSetHash": _hash(sorted(final_ids)),
    }
    ring = _fairness_ring(final_ids, descriptor_seed)
    fairness = None
    if len(ring) > 1:
        fairness = FairnessDescriptor(
            policy_version=FAIRNESS_POLICY_VERSION,
            owner_id=owner_id,
            workspace_id=workspace_id,
            registry_snapshot_content_hash=resolution.registry_snapshot_ref.content_hash,
            substage_definition_ref=descriptor_seed["substageDefinitionRef"],
            equivalence_set_hash=descriptor_seed["equivalenceSetHash"],
            candidate_identities=ring,
        )
    payload = {
        "taskId": task.task_id,
        "expectedTaskRevision": expected_task_revision,
        "designRef": resolution.design_ref.model_dump(mode="json", by_alias=True),
        "instanceRef": resolution.instance_ref.model_dump(mode="json", by_alias=True),
        "registrySnapshotRef": resolution.registry_snapshot_ref.model_dump(mode="json", by_alias=True),
        "candidateEvaluations": [row.model_dump(mode="json", by_alias=True) for row in evaluations],
        "runtimeReadiness": {
            key: value.model_dump(mode="json", by_alias=True)
            for key, value in sorted(runtime_readiness.items())
        },
        "policyVersion": DECISION_POLICY_VERSION,
        "recoveryIdentity": recovery_identity,
    }
    return PreparedSelection(
        task=task,
        instance=instance,
        resolution=resolution,
        snapshot_envelope=snapshot_envelope,
        runtime_readiness=dict(runtime_readiness),
        selection_request_id=selection_request_id,
        selection_command_hash=selection_command_hash,
        selection_payload_hash=_hash(payload),
        expected_task_revision=expected_task_revision,
        owner_id=owner_id,
        workspace_id=workspace_id,
        final_tie_candidate_ids=ring,
        fairness=fairness,
        decided_at=decided_at.astimezone(timezone.utc),
        invocation_context=dict(invocation_context or {}),
        effective_acceptance_criteria=effective_criteria,
        effective_expected_artifact_types=effective_expected_types,
        next_task_scientific_state_version=next_task_scientific_state_version,
        next_task_rationale=next_task_rationale,
    )


def _binding_rows(
    snapshot: Mapping[str, Any], candidate: CandidateEvaluation
) -> list[Mapping[str, Any]]:
    ref_ids = {(row.binding_id, row.binding_version) for row in candidate.source_binding_refs}
    bindings = snapshot.get("bindings", {})
    rows = list(bindings.get("skills", [])) + list(bindings.get("agents", []))
    matches = [
        row
        for row in rows
        if (row.get("bindingId"), row.get("bindingVersion")) in ref_ids and row.get("enabled")
    ]
    if {(row.get("bindingId"), row.get("bindingVersion")) for row in matches} != ref_ids:
        raise DecisionPolicyViolation("source_binding_snapshot_mismatch")
    return matches


def _allowed_skills(snapshot: Mapping[str, Any], ref: AgentRef) -> list[SkillRef]:
    grants = snapshot.get("bindings", {}).get("agentSkills", [])
    return [
        SkillRef(skillId=row["skillId"], definitionVersion=row["skillDefinitionVersion"])
        for row in grants
        if row.get("agentId") == ref.agent_id
        and row.get("agentDefinitionVersion") == ref.definition_version
        and row.get("enabled")
    ]


def _frozen_schema_validated_completion_without_reviewer(
    prepared: PreparedSelection,
    ref: SkillRef,
) -> bool:
    """Use frozen schema authority for an explicitly self-validating workflow Skill.

    This is intentionally opt-in and snapshot-bound. It does not infer from
    titles/stages or current mutable registry state: the exact frozen Skill
    definition must publish the review policy together with structured-output
    schemas. Historical snapshots without that policy keep the reviewer.
    """
    if len(prepared.required_skill_sequence) != 1 or prepared.required_skill_sequence[0] != ref:
        return False
    matches = [
        row
        for row in prepared.snapshot_envelope.get("definitions", {}).get("skills", [])
        if row.get("skillId") == ref.skill_id
        and row.get("definitionVersion") == ref.definition_version
    ]
    if len(matches) != 1:
        return False
    source = matches[0].get("sourceContract")
    if not isinstance(source, Mapping):
        return False
    if source.get("review_policy") != {
        "contract_version": 1,
        "mode": "schema_validated_only",
    }:
        return False
    frozen = source.get("frozenSchemas")
    if not (
        isinstance(frozen, Mapping)
        and all(
            isinstance(frozen.get(name), Mapping)
            and isinstance(frozen[name].get("schema"), Mapping)
            and isinstance(frozen[name].get("sha256"), str)
            and len(frozen[name]["sha256"]) == 64
            for name in ("input", "output")
        )
    ):
        return False

    structured_output = source.get("structured_output")
    if (
        isinstance(structured_output, Mapping)
        and structured_output.get("contract_version") == 1
    ):
        return True

    composition = next(
        (
            row.get("executionComposition")
            for row in prepared.snapshot_envelope.get("definitions", {}).get("substages", [])
            if row.get("definitionId") == prepared.instance.substage_definition_id
            and row.get("definitionVersion") == prepared.instance.substage_definition_version
        ),
        None,
    )
    # The short Materials chain is explicitly schema-validated at adapter ingress
    # and artifact egress, and is restricted to these workflow-owned stages.
    if isinstance(composition, Mapping) and composition.get("workflowId") == "materials_candidate_discovery_workflow":
        return ref.skill_id in {
            "materials_mp_constraint_search", "crystal_design_structure_crossover_py",
            "crystal_design_structure_crossover_agent",
        }

    # Other LLM Skills may self-validate only when the frozen Registry
    # definition contains the registered finalizer contract. Historical
    # structured_output.guard_version metadata alone is not execution authority.
    finalization = source.get("finalization")
    frozen_finalization = source.get("frozenFinalization")
    return bool(
        isinstance(finalization, Mapping)
        and finalization.get("contract_version") == 1
        and isinstance(frozen_finalization, Mapping)
        and frozen_finalization.get("contractVersion") == 1
    )


def _single_assignment(
    prepared: PreparedSelection,
    selected: CandidateEvaluation,
    *,
    purpose: ExecutionPurpose,
    decision_id: str,
    leaf_final_report: bool = False,
    general_completion_authorized: bool = False,
) -> list[ExecutionAssignment]:
    ref = selected.candidate_ref
    assignment_id = _stable_id("assignment", decision_id, candidate_identity(selected))
    # Assignment authority is projected from the resolver candidate only.
    completion = selected.preference.completion_coverage
    if purpose == ExecutionPurpose.PROGRESS:
        completion = CompletionCoverage.PROGRESS_CAPABLE
    elif general_completion_authorized:
        completion = CompletionCoverage.COMPLETION_CAPABLE
    if (
        purpose == ExecutionPurpose.COMPLETION
        and isinstance(ref, SkillRef)
        and _frozen_schema_validated_completion_without_reviewer(prepared, ref)
    ):
        completion = CompletionCoverage.COMPLETION_CAPABLE
    criteria = list(
        prepared.instance.acceptance_criteria
        if prepared.effective_acceptance_criteria is None
        else prepared.effective_acceptance_criteria
    )
    expected_types = list(
        prepared.instance.expected_artifact_types
        if prepared.effective_expected_artifact_types is None
        else prepared.effective_expected_artifact_types
    )
    if purpose == ExecutionPurpose.PROGRESS:
        criteria = [
            row for row in criteria if row.judgment_requirement == JudgmentRequirement.DETERMINISTIC
        ]
        selected_bindings = _binding_rows(prepared.snapshot_envelope, selected)
        declares_scientific_state_projection = bool(
            isinstance(ref, SkillRef)
            and any(
                isinstance(row.get("scientificStateProjection"), Mapping)
                for row in selected_bindings
            )
        )
        if not declares_scientific_state_projection and isinstance(ref, SkillRef):
            # Fallback for snapshots pinned before projection moved to the
            # binding level: the skill sourceContract still carries it there.
            declares_scientific_state_projection = bool(
                any(
                    row.get("skillId") == ref.skill_id
                    and row.get("definitionVersion") == ref.definition_version
                    and isinstance(
                        (row.get("sourceContract") or {}).get(
                            "scientific_state_projection"
                        ),
                        Mapping,
                    )
                    for row in prepared.snapshot_envelope.get("definitions", {}).get(
                        "skills", []
                    )
                )
            )
        if not declares_scientific_state_projection:
            expected_types = []
    else:
        selected_bindings = _binding_rows(prepared.snapshot_envelope, selected)
    if isinstance(ref, SkillRef):
        actor = ActorRef(actorType="orchestrator", actorId="openair_orchestrator", definitionVersion=None)
        executor = ExecutorRef(
            executorType="skill", executorId=ref.skill_id, definitionVersion=ref.definition_version
        )
        allowed_skills: list[SkillRef] = []
    elif isinstance(ref, AgentRef):
        actor = ActorRef(actorType="agent", actorId=ref.agent_id, definitionVersion=ref.definition_version)
        executor = ExecutorRef(
            executorType="agent", executorId=ref.agent_id, definitionVersion=ref.definition_version
        )
        allowed_skills = _allowed_skills(prepared.snapshot_envelope, ref)
    else:
        raise DecisionPolicyViolation("single_assignment_candidate_invalid")
    primary = ExecutionAssignment(
        assignmentId=assignment_id,
        recipeTemplateKey=None,
        role=AssignmentRole.PRIMARY,
        completionCoverage=completion,
        objective=(
            prepared.instance.objective
            if purpose == ExecutionPurpose.COMPLETION
            else f"为以下目标形成可审计的阶段性进展：{prepared.instance.objective}"
        ),
        sourceBindingRefs=selected.source_binding_refs,
        responsibleActorRef=actor,
        executorRef=executor,
        allowedSkillRefs=allowed_skills,
        inputRefs=prepared.instance.input_refs,
        expectedArtifactTypes=expected_types,
        acceptanceCriteria=criteria,
        dependencyAssignmentIds=[],
    )
    return [primary]


def _skill_composition_assignments(
    prepared: PreparedSelection,
    *,
    decision_id: str,
) -> list[ExecutionAssignment]:
    rows = _composition_candidate_rows(prepared.resolution, prepared.required_skill_sequence)
    assignments: list[ExecutionAssignment] = []
    previous_assignment_id: str | None = None
    for index, (ref, candidate) in enumerate(zip(prepared.required_skill_sequence, rows, strict=True)):
        _binding_rows(prepared.snapshot_envelope, candidate)
        assignment_id = _stable_id(
            "assignment", decision_id, f"skill-composition:{index}:{ref.skill_id}:{ref.definition_version}"
        )
        assignments.append(
            ExecutionAssignment(
                assignmentId=assignment_id,
                recipeTemplateKey=None,
                role=AssignmentRole.PRIMARY,
                completionCoverage=CompletionCoverage.COMPLETION_CAPABLE,
                objective=prepared.instance.objective,
                sourceBindingRefs=candidate.source_binding_refs,
                responsibleActorRef=ActorRef(
                    actorType="orchestrator", actorId="openair_orchestrator", definitionVersion=None
                ),
                executorRef=ExecutorRef(
                    executorType="skill", executorId=ref.skill_id, definitionVersion=ref.definition_version
                ),
                allowedSkillRefs=[],
                inputRefs=prepared.instance.input_refs,
                expectedArtifactTypes=prepared.instance.expected_artifact_types,
                acceptanceCriteria=prepared.instance.acceptance_criteria,
                dependencyAssignmentIds=([] if previous_assignment_id is None else [previous_assignment_id]),
            )
        )
        previous_assignment_id = assignment_id
    return assignments


def _execution_method_role_result_contract(
    role: Mapping[str, Any],
    *,
    is_completion: bool,
    expected_artifact_types: list[str],
    acceptance_criteria: list[Any],
) -> tuple[list[str], list[Any]]:
    """Project the Definition result contract onto one method role.

    Every producer role retains its declared artifact outputs so progress
    results can enter the shared Artifact chain.  Only completion roles inherit
    Definition acceptance criteria, projected onto the artifacts they own.
    Legacy completion roles without a qualification contract retain the
    previous all-output behavior.
    """
    qualification = role.get("qualificationContract")
    if not isinstance(qualification, Mapping):
        qualification = {}
    declared_output_values = qualification.get("outputArtifactTypes")
    if declared_output_values is not None and (
        not isinstance(declared_output_values, list)
        or any(
            not isinstance(artifact_type, str) or not artifact_type
            for artifact_type in declared_output_values
        )
    ):
        raise DecisionPolicyViolation(
            "execution_method_role_output_contract_invalid",
            str(role.get("roleId") or ""),
        )
    declared_outputs = list(declared_output_values or [])
    role_expected_types = (
        declared_outputs
        if declared_outputs
        else list(expected_artifact_types) if is_completion else []
    )
    if not is_completion:
        return role_expected_types, []
    role_acceptance_criteria = [
        criterion
        for criterion in acceptance_criteria
        if criterion.required_artifact_type is None
        or criterion.required_artifact_type in role_expected_types
    ]
    return role_expected_types, role_acceptance_criteria


def _execution_method_assignments(
    prepared: PreparedSelection,
    *,
    decision_id: str,
) -> list[ExecutionAssignment]:
    method = prepared.execution_method
    if method is None:
        raise DecisionPolicyViolation("execution_method_not_prepared")
    selected_by_role = dict(prepared.execution_method_role_candidates)
    evaluations = {candidate_identity(row): row for row in prepared.resolution.candidate_evaluations}
    completion_roles = set(method.get("completionRoleIds") or [])
    stop_by_role: dict[str, list[str]] = {}
    for condition in method.get("stopConditions") or []:
        stop_by_role.setdefault(str(condition.get("afterRoleId") or ""), []).append(
            str(condition.get("conditionId") or "")
        )
    assignment_ids = {
        role_id: _stable_id("assignment", decision_id, f"execution-method:{role_id}:{candidate_id}")
        for role_id, candidate_id in prepared.execution_method_role_candidates
    }
    assignments: list[ExecutionAssignment] = []
    effective_expected_types = list(
        prepared.instance.expected_artifact_types
        if prepared.effective_expected_artifact_types is None
        else prepared.effective_expected_artifact_types
    )
    effective_acceptance_criteria = list(
        prepared.instance.acceptance_criteria
        if prepared.effective_acceptance_criteria is None
        else prepared.effective_acceptance_criteria
    )
    for role in method.get("roles") or []:
        role_id = str(role.get("roleId") or "")
        candidate_id = selected_by_role.get(role_id)
        if candidate_id is None:
            if role.get("requirement") == "optional":
                continue
            raise DecisionPolicyViolation("execution_method_role_selection_missing", role_id)
        candidate = evaluations.get(candidate_id)
        if candidate is None:
            raise DecisionPolicyViolation("execution_method_candidate_missing", candidate_id)
        _binding_rows(prepared.snapshot_envelope, candidate)
        ref = candidate.candidate_ref
        if isinstance(ref, SkillRef):
            actor = ActorRef(
                actorType="orchestrator", actorId="openair_orchestrator", definitionVersion=None
            )
            executor = ExecutorRef(
                executorType="skill", executorId=ref.skill_id, definitionVersion=ref.definition_version
            )
            allowed_skills: list[SkillRef] = []
        elif isinstance(ref, AgentRef):
            actor = ActorRef(
                actorType="agent", actorId=ref.agent_id, definitionVersion=ref.definition_version
            )
            executor = ExecutorRef(
                executorType="agent", executorId=ref.agent_id, definitionVersion=ref.definition_version
            )
            allowed_skills = _allowed_skills(prepared.snapshot_envelope, ref)
        else:
            raise DecisionPolicyViolation("execution_method_candidate_kind_invalid", candidate_id)
        dependency_ids = [
            assignment_ids[dependency_role]
            for dependency_role in role.get("dependsOn") or []
            if dependency_role in assignment_ids
        ]
        role_control = {
            "requirement": role.get("requirement"),
            "releasePolicy": dict(role.get("releasePolicy") or {}),
            "failurePolicy": role.get("failurePolicy"),
            "parallelism": role.get("parallelism"),
            "stopConditionIds": stop_by_role.get(role_id, []),
            "stopConditions": [
                dict(condition)
                for condition in method.get("stopConditions") or []
                if condition.get("afterRoleId") == role_id
            ],
            "qualityConstraints": list(method.get("qualityConstraints") or []),
        }
        reentry_transitions = [
            dict(transition)
            for transition in method.get("reentryTransitions") or []
            if transition.get("afterRoleId") == role_id
        ]
        if reentry_transitions:
            role_control["reentryTransitions"] = reentry_transitions
        is_completion = role_id in completion_roles
        qualification = role.get("qualificationContract")
        if not isinstance(qualification, Mapping):
            qualification = {}
        role_expected_types, role_acceptance_criteria = (
            _execution_method_role_result_contract(
                role,
                is_completion=is_completion,
                expected_artifact_types=effective_expected_types,
                acceptance_criteria=effective_acceptance_criteria,
            )
        )
        assignments.append(
            ExecutionAssignment(
                assignmentId=assignment_ids[role_id],
                recipeTemplateKey=None,
                executionMethodRoleId=role_id,
                executionMethodIteration=1,
                executionMethodRoleControl=role_control,
                role=AssignmentRole.PRIMARY,
                completionCoverage=(
                    CompletionCoverage.COMPLETION_CAPABLE
                    if is_completion
                    else CompletionCoverage.PROGRESS_CAPABLE
                ),
                objective=str(qualification.get("objective") or prepared.instance.objective),
                sourceBindingRefs=candidate.source_binding_refs,
                responsibleActorRef=actor,
                executorRef=executor,
                allowedSkillRefs=allowed_skills,
                inputRefs=prepared.instance.input_refs,
                expectedArtifactTypes=role_expected_types,
                acceptanceCriteria=role_acceptance_criteria,
                dependencyAssignmentIds=dependency_ids,
            )
        )
    if not assignments:
        raise DecisionPolicyViolation("execution_method_assignments_empty")
    return assignments


def _recipe_definition(
    snapshot: Mapping[str, Any], ref: MultiAgentRecipeRef
) -> MultiAgentRecipeDefinition:
    matches = [
        row
        for row in snapshot.get("recipes", [])
        if row.get("recipeId") == ref.recipe_id and row.get("recipeVersion") == ref.recipe_version
    ]
    if len(matches) != 1:
        raise DecisionPolicyViolation("recipe_snapshot_mismatch")
    recipe = MultiAgentRecipeDefinition.model_validate(matches[0])
    if not recipe.enabled:
        raise DecisionPolicyViolation("recipe_disabled")
    return recipe


def _recipe_assignments(
    prepared: PreparedSelection,
    selected: CandidateEvaluation,
    *,
    purpose: ExecutionPurpose,
    decision_id: str,
) -> tuple[list[ExecutionAssignment], MultiAgentRecipeDefinition]:
    ref = selected.candidate_ref
    if not isinstance(ref, MultiAgentRecipeRef):
        raise DecisionPolicyViolation("recipe_candidate_required")
    recipe = _recipe_definition(prepared.snapshot_envelope, ref)
    direct_agents = [
        row
        for row in prepared.resolution.candidate_evaluations
        if isinstance(row.candidate_ref, AgentRef)
        and row.eligible
        and row.preference.executable_now
        and not _is_general(prepared.snapshot_envelope, row)
    ]
    assignment_ids = {
        template.template_key: _stable_id("assignment", decision_id, template.template_key)
        for template in recipe.assignment_templates
    }
    assignments: list[ExecutionAssignment] = []
    for template in recipe.assignment_templates:
        pool = [row for row in direct_agents if row.supported_role == template.required_supported_role.value]
        if not pool:
            raise DecisionPolicyViolation("recipe_template_executor_missing", template.template_key)
        best = min(_preference_key(row) for row in pool)
        equivalent = [row for row in pool if _preference_key(row) == best]
        chosen = min(
            equivalent,
            key=lambda row: sha256(
                f"{DECISION_POLICY_VERSION}\x1f{prepared.selection_request_id}\x1f{template.template_key}\x1f{candidate_identity(row)}".encode()
            ).digest(),
        )
        agent_ref = chosen.candidate_ref
        assert isinstance(agent_ref, AgentRef)
        binding_rows = _binding_rows(prepared.snapshot_envelope, chosen)
        if not any(row.get("supportedRole") == template.required_supported_role.value for row in binding_rows):
            raise DecisionPolicyViolation("recipe_template_split_state", template.template_key)
        assignments.append(
            ExecutionAssignment(
                assignmentId=assignment_ids[template.template_key],
                recipeTemplateKey=template.template_key,
                role=template.role,
                completionCoverage=(
                    CompletionCoverage.COMPLETION_CAPABLE
                    if purpose == ExecutionPurpose.COMPLETION
                    else CompletionCoverage.PROGRESS_CAPABLE
                ),
                objective=template.objective_template.replace("{objective}", prepared.instance.objective),
                sourceBindingRefs=chosen.source_binding_refs,
                responsibleActorRef=ActorRef(
                    actorType="agent", actorId=agent_ref.agent_id, definitionVersion=agent_ref.definition_version
                ),
                executorRef=ExecutorRef(
                    executorType="agent", executorId=agent_ref.agent_id, definitionVersion=agent_ref.definition_version
                ),
                allowedSkillRefs=_allowed_skills(prepared.snapshot_envelope, agent_ref),
                inputRefs=prepared.instance.input_refs,
                expectedArtifactTypes=(
                    prepared.instance.expected_artifact_types
                    if purpose == ExecutionPurpose.COMPLETION and template.role != AssignmentRole.REVIEWER
                    else []
                ),
                acceptanceCriteria=(
                    prepared.instance.acceptance_criteria
                    if purpose == ExecutionPurpose.COMPLETION and template.role != AssignmentRole.REVIEWER
                    else []
                ),
                dependencyAssignmentIds=[assignment_ids[key] for key in template.dependency_template_keys],
            )
        )
    return assignments, recipe


def validate_execution_decision(
    decision: ExecutionDecision,
    *,
    resolution: ResolutionResult,
    snapshot_envelope: Mapping[str, Any],
    recipe: MultiAgentRecipeDefinition | None = None,
    leaf_final_report: bool = False,
    general_completion_authorized: bool = False,
) -> None:
    actual_candidates = [row.model_dump(mode="json", by_alias=True) for row in decision.candidate_evaluations]
    expected_candidates = [row.model_dump(mode="json", by_alias=True) for row in resolution.candidate_evaluations]
    if actual_candidates != expected_candidates:
        raise DecisionPolicyViolation("candidate_evaluations_not_preserved")
    if decision.strategy == ExecutionStrategy.GENERAL_RESEARCH_AGENT:
        preferred = [row for row in resolution.candidate_evaluations if not _is_general(snapshot_envelope, row)]
        if any(
            row.eligible
            and row.preference.completion_coverage == CompletionCoverage.COMPLETION_CAPABLE
            and row.preference.executable_now
            for row in preferred
        ):
            raise DecisionPolicyViolation("fallback_completion_candidate_exists")
        if not decision.preferred_candidate_evaluation_refs and preferred:
            raise DecisionPolicyViolation("fallback_preferred_evidence_missing")
        professional = any(
            criterion.judgment_requirement == JudgmentRequirement.DOMAIN_PROFESSIONAL
            for assignment in decision.assignments
            for criterion in assignment.acceptance_criteria
        )
        if (
            professional
            and decision.purpose != ExecutionPurpose.PROGRESS
            and not general_completion_authorized
        ):
            raise DecisionPolicyViolation("professional_fallback_requires_progress")
    if decision.strategy == ExecutionStrategy.MULTI_AGENT:
        if recipe is None:
            raise DecisionPolicyViolation("recipe_authority_missing")
        expected_keys = {row.template_key for row in recipe.assignment_templates}
        actual_keys = {row.recipe_template_key for row in decision.assignments}
        if actual_keys != expected_keys or len(decision.assignments) != len(recipe.assignment_templates):
            raise DecisionPolicyViolation("recipe_template_one_to_one_violation")
        assignment_by_key = {row.recipe_template_key: row for row in decision.assignments}
        for template in recipe.assignment_templates:
            expected_dependencies = {assignment_by_key[key].assignment_id for key in template.dependency_template_keys}
            if set(assignment_by_key[template.template_key].dependency_assignment_ids) != expected_dependencies:
                raise DecisionPolicyViolation("recipe_dependency_materialization_invalid")


def _active_design_for_instance(
    task: ResearchTaskV12,
    instance: SubstageInstance,
    *,
    research_design_version: int,
):
    return next(
        (
            row
            for row in getattr(task, "research_design_versions", [])
            if row.version == research_design_version
            and execution_instance_for_ref(
                task,
                row,
                InstanceRef(
                    substageInstanceId=instance.substage_instance_id,
                    instanceRevision=instance.instance_revision,
                ),
            )
            is not None
        ),
        None,
    )


def _is_leaf_report_generation_instance(design: Any, instance: SubstageInstance) -> bool:
    if (
        instance.stage_key is not StageKey.INSIGHT_GENERATION
        or instance.substage_definition_id.split(":", 1)[-1] != "report_generation"
    ):
        return False
    ref = (instance.substage_instance_id, instance.instance_revision)
    return not any(
        (
            dependency.upstream_instance_ref.substage_instance_id,
            dependency.upstream_instance_ref.instance_revision,
        ) == ref
        for candidate in design.instances
        if candidate.substage_instance_id != instance.substage_instance_id
        for dependency in candidate.dependencies
    )


def _general_completion_authorized(
    task: ResearchTaskV12,
    prepared: PreparedSelection,
    selected: CandidateEvaluation,
    *,
    design: Any | None,
    general: bool,
    professional_required: bool,
) -> bool:
    """Use the Resolver's frozen reasoning authority and current dependency facts."""
    return bool(
        general
        and selected.preference.completion_coverage == CompletionCoverage.COMPLETION_CAPABLE
        and design is not None
        and design.status is ResearchDesignStatus.CONFIRMED
        and _dependency_preflight_block(task, prepared.instance)[0] is None
    )


def general_completion_result_authorized(
    task: ResearchTaskV12,
    *,
    decision: Mapping[str, Any],
    work: Mapping[str, Any],
    artifact_contract: str,
    result_check_status: str,
    artifact_content: str | None,
) -> bool:
    """Accept a real completed reasoning action on its exact frozen lineage."""
    if (
        not _has_materializable_completion_content(artifact_content)
        or result_check_status != "passed"
        or artifact_contract != "general_research_reasoning_artifact_v1"
        or decision.get("purpose") != ExecutionPurpose.COMPLETION.value
        or decision.get("strategy") != ExecutionStrategy.GENERAL_RESEARCH_AGENT.value
        or decision.get("status") != ExecutionDecisionStatus.DISPATCHED.value
        or task.active_research_design_ref is None
        or decision.get("researchDesignVersion") != task.active_research_design_ref.version
        or any(decision.get(key) != work.get(key) for key in (
            "substageInstanceId", "substageInstanceRevision", "researchDesignVersion",
        ))
    ):
        return False
    assignment = next((row for row in decision.get("assignments", [])
                       if row.get("assignmentId") == work.get("assignmentId")), None)
    if not assignment or assignment.get("completionCoverage") != "completion_capable":
        return False
    design = next((row for row in task.research_design_versions
                   if row.research_design_id == task.active_research_design_ref.research_design_id
                   and row.version == task.active_research_design_ref.version
                   and row.status is ResearchDesignStatus.CONFIRMED), None)
    instance = next((row for row in execution_instances_for_design(task, design)
                     if row.substage_instance_id == work.get("substageInstanceId")
                     and row.instance_revision == work.get("substageInstanceRevision")), None) if design else None
    if instance is None or _dependency_preflight_block(task, instance)[0] is not None:
        return False
    runtime = next((row for row in task.substage_runtime_states
                    if row.substage_instance_id == work.get("substageInstanceId")
                    and row.instance_revision == work.get("substageInstanceRevision")), None)
    return bool(
        runtime is not None
        and runtime.active_execution_decision_id == decision.get("executionDecisionId")
        and runtime.status is SubstageRuntimeStatus.RUNNING
    )


_FINAL_FALLBACK_UPSTREAM_KEYS = frozenset(
    {
        "key_literature",
        "evidence_extraction",
        "findings_synthesis",
    }
)


def _substage_key_from_definition_id(definition_id: str | None) -> str:
    return str(definition_id or "").split(":", 1)[-1].strip().lower()


def _is_final_fallback_upstream_key(value: str | None) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return False
    if text in _FINAL_FALLBACK_UPSTREAM_KEYS:
        return True
    return any(key in text for key in _FINAL_FALLBACK_UPSTREAM_KEYS)


def _has_materializable_completion_content(content: str | None) -> bool:
    text = str(content or "").strip()
    if not text:
        return False
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return True
    if parsed in ({}, [], None, ""):
        return False
    if isinstance(parsed, Mapping):
        readable = (
            "result",
            "summary",
            "answer",
            "reasoning_summary",
            "content",
            "report",
            "title",
        )
        if any(
            isinstance(parsed.get(key), str) and str(parsed.get(key) or "").strip()
            for key in readable
        ):
            return True
        # Structured but empty of report prose: treat as non-materializable final output.
        return bool(parsed) and any(
            value not in (None, "", [], {}) for value in parsed.values()
        )
    return True


def assemble_degraded_final_report_markdown(
    upstream_artifacts: Sequence[Mapping[str, Any]],
) -> str:
    """Deterministically assemble a minimum-sufficient final report from upstream facts.

    Truthful only: references existing artifact identities/types. Does not invent
    DOI, numeric claims, or research conclusions beyond what upstream already holds.
    """
    lines = [
        "# Research report (degraded / limited evidence)",
        "",
        "This final report was assembled automatically because the normal final-stage",
        "output could not be materialized. Confidence is low; content is limited to",
        "already-usable upstream artifacts.",
        "",
        "## Supported conclusions",
        "",
        "No new scientific conclusions are asserted by this fallback assembler.",
        "Only prior usable upstream artifacts are listed below.",
        "",
        "## Existing evidence",
        "",
    ]
    if not upstream_artifacts:
        lines.append("- (no usable upstream artifacts were available)")
    else:
        for row in upstream_artifacts:
            artifact_id = str(row.get("artifactId") or row.get("artifact_id") or "").strip() or "(unknown)"
            artifact_type = str(row.get("artifactType") or row.get("artifact_type") or "").strip() or "(unknown)"
            source_substage = str(
                row.get("sourceSubstageKey") or row.get("source_substage_key") or ""
            ).strip()
            digest = str(
                (row.get("validatorResult") or {}).get("contentSha256")
                if isinstance(row.get("validatorResult"), Mapping)
                else row.get("contentSha256") or ""
            ).strip()
            detail = f"- `{artifact_type}` (`{artifact_id}`)"
            if source_substage:
                detail += f" from `{source_substage}`"
            if digest:
                detail += f"; contentSha256=`{digest}`"
            lines.append(detail)
    lines.extend(
        [
            "",
            "## Evidence limitations",
            "",
            "- Final-stage normal formatting/generation did not produce a durable artifact.",
            "- This document is a degraded, system-owned assembly of prior usable artifacts.",
            "- Do not treat this as a peer-reviewed or high-confidence research report.",
            "",
            "## Currently unconfirmed",
            "",
            "- Any claim, DOI, numeric result, or mechanism not already present in the",
            "  listed upstream artifacts remains unconfirmed by this fallback.",
            "",
            "Quality markers: `degraded`, `limited evidence`, `low confidence`.",
            "",
        ]
    )
    return "\n".join(lines)


def collect_final_fallback_upstream_artifacts(
    task_payload: Mapping[str, Any],
    *,
    work: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Select usable upstream artifacts eligible for final-report degraded assembly."""
    work_by_id = {
        str(row.get("workUnitId") or ""): row
        for row in task_payload.get("workUnits") or []
        if isinstance(row, Mapping)
    }
    selected: list[dict[str, Any]] = []
    input_refs = [
        str(ref)
        for ref in (work.get("inputArtifactRefs") or [])
        if str(ref or "").strip()
    ]
    allowed_ids = set(input_refs)
    for artifact in task_payload.get("artifacts") or []:
        if not isinstance(artifact, Mapping):
            continue
        if str(artifact.get("artifactState") or "") != ArtifactState.USABLE.value:
            continue
        artifact_id = str(artifact.get("artifactId") or "").strip()
        if allowed_ids and artifact_id not in allowed_ids:
            continue
        producer = work_by_id.get(str(artifact.get("workUnitId") or ""))
        producer_key = _substage_key_from_definition_id(
            None if producer is None else producer.get("substageDefinitionId")
        )
        artifact_type = str(artifact.get("artifactType") or "")
        if not (
            _is_final_fallback_upstream_key(producer_key)
            or _is_final_fallback_upstream_key(artifact_type)
        ):
            continue
        selected.append(
            {
                "artifactId": artifact_id,
                "artifactType": artifact_type,
                "sourceSubstageKey": producer_key,
                "validatorResult": dict(artifact.get("validatorResult") or {}),
                "workUnitId": artifact.get("workUnitId"),
            }
        )
    return selected


def _attach_degraded_final_report_artifact(
    payload: dict[str, Any],
    *,
    decision: Mapping[str, Any],
    closed_at: datetime,
) -> dict[str, Any]:
    """System-owned recovery: ensure leaf report completion has a usable final Artifact."""
    if decision.get("purpose") != ExecutionPurpose.COMPLETION.value:
        return payload
    work_units = [
        row
        for row in payload.get("workUnits") or []
        if isinstance(row, Mapping)
        and row.get("executionDecisionId") == decision.get("executionDecisionId")
        and row.get("status") == WorkUnitStatus.COMPLETED.value
    ]
    if not work_units:
        return payload
    leaf_works = [
        row
        for row in work_units
        if _substage_key_from_definition_id(row.get("substageDefinitionId")) == "report_generation"
    ]
    if not leaf_works:
        return payload
    assignments = {
        row["assignmentId"]: row
        for row in decision.get("assignments") or []
        if isinstance(row, Mapping)
    }
    requirements = list(
        (decision.get("resultAssemblyPolicy") or {}).get("finalArtifactRequirements") or []
    )
    artifacts_by_id = {
        str(row.get("artifactId") or ""): row
        for row in payload.get("artifacts") or []
        if isinstance(row, Mapping)
    }
    for work in leaf_works:
        assignment = assignments.get(work.get("assignmentId"))
        if assignment is None or assignment.get("role") == AssignmentRole.REVIEWER.value:
            continue
        expected_types = list(
            work.get("expectedArtifactTypes") or assignment.get("expectedArtifactTypes") or []
        )
        if not expected_types:
            continue
        artifact_type = expected_types[0]
        existing_ok = False
        for artifact in artifacts_by_id.values():
            if artifact.get("workUnitId") != work.get("workUnitId"):
                continue
            if artifact.get("artifactType") != artifact_type:
                continue
            if artifact.get("artifactState") != ArtifactState.USABLE.value:
                continue
            if not _artifact_has_completion_authority(
                artifact, work, assignment, decision
            ):
                continue
            if requirements and not any(
                requirement.get("artifactType") == artifact_type
                and work.get("assignmentId") in (requirement.get("sourceAssignmentIds") or [])
                for requirement in requirements
                if isinstance(requirement, Mapping)
            ):
                continue
            existing_ok = True
            break
        if existing_ok:
            continue
        upstream = collect_final_fallback_upstream_artifacts(payload, work=work)
        if not upstream:
            raise DecisionPolicyViolation("final_report_fallback_upstream_missing")
        markdown = assemble_degraded_final_report_markdown(upstream)
        digest = sha256(markdown.encode("utf-8")).hexdigest()
        artifact_id = f"artifact:degraded-final:{work.get('workUnitId')}"
        call_id = next(
            (
                str(row.get("capabilityCallId") or "")
                for row in payload.get("capabilityCalls") or []
                if isinstance(row, Mapping) and row.get("workUnitId") == work.get("workUnitId")
            ),
            None,
        )
        artifact = {
            "artifactId": artifact_id,
            "workUnitId": work["workUnitId"],
            "substageInstanceId": work["substageInstanceId"],
            "sourceCapabilityCallId": call_id,
            "artifactType": artifact_type,
            "artifactState": ArtifactState.USABLE.value,
            "version": 1,
            "sourceRefs": [
                *[str(ref) for ref in work.get("inputArtifactRefs") or []],
                *[str(ref) for ref in work.get("inputMemoryRefs") or []],
            ],
            "validatorResult": {
                "status": "passed",
                "artifactContract": "degraded_final_report_v1",
                "artifactSchemaDigest": digest,
                "contentSha256": digest,
                "artifactQuality": "degraded",
                "qualityMarkers": ["degraded", "limited_evidence", "low_confidence"],
                "assemblyMode": "upstream_deterministic_markdown",
            },
            "lineageKind": "capability_call_output",
        }
        payload.setdefault("artifacts", []).append(artifact)
        artifacts_by_id[artifact_id] = artifact
        for call in payload.get("capabilityCalls") or []:
            if not isinstance(call, Mapping):
                continue
            if call.get("workUnitId") != work.get("workUnitId"):
                continue
            refs = list(call.get("outputArtifactRefs") or [])
            if artifact_id not in refs:
                refs.append(artifact_id)
            call["outputArtifactRefs"] = refs
        # Keep WorkUnit output refs coherent with the capability call.
        work_refs = list(work.get("outputArtifactRefs") or [])
        if artifact_id not in work_refs:
            # WorkUnit schema may not carry outputArtifactRefs; capability refs are authoritative.
            pass
        _ = closed_at  # timestamp reserved for future catalog binding
    return payload


def _final_result_artifact_snapshot(artifact: Any) -> dict[str, Any]:
    digest = str((artifact.validator_result or {}).get("contentSha256") or "").strip().lower()
    if digest and (len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest)):
        raise DecisionPolicyViolation("final_result_artifact_hash_invalid")
    return {
        "artifactId": artifact.artifact_id,
        "artifactType": artifact.artifact_type,
        "version": artifact.version,
        "contentSha256": digest or None,
    }


def _artifact_snapshots_for_refs(task: ResearchTaskV12, refs: list[str]) -> list[dict[str, Any]]:
    by_id = {row.artifact_id: row for row in task.artifacts}
    snapshots: list[dict[str, Any]] = []
    for ref in refs:
        artifact = by_id.get(ref)
        if artifact is None:
            raise DecisionPolicyViolation("input_artifact_snapshot_missing")
        snapshots.append(_final_result_artifact_snapshot(artifact))
    return snapshots



def _artifact_has_explicit_semantic_gap(artifact: Any) -> bool:
    validator = getattr(artifact, "validator_result", None) or {}
    readiness = validator.get("semanticReadiness") if isinstance(validator, Mapping) else None
    return bool(
        getattr(artifact, "artifact_state", None) is ArtifactState.INSUFFICIENT_MATERIALS
        and isinstance(readiness, Mapping)
        and readiness.get("explicit") is True
        and readiness.get("downstreamReady") is False
    )


def _semantic_gap_final_result_authority(
    task: ResearchTaskV12,
    design: Any,
    *,
    assembled_at: datetime,
) -> dict[str, Any] | None:
    """Authorize a truthful degraded final result from an explicit material gap.

    The result Artifact is the producer's already persisted diagnostic output.
    This deliberately does not upgrade it to USABLE/downstream-ready and does
    not synthesize scientific claims.
    """

    work_by_id = {row.work_unit_id: row for row in task.work_units}
    instance_by_ref = {
        (row.substage_instance_id, row.instance_revision): row
        for row in design.instances
    }
    candidates: list[tuple[int, str, Any, Any]] = []
    for artifact in task.artifacts:
        producer = work_by_id.get(artifact.work_unit_id)
        if (
            producer is None
            or producer.source_mode is not WorkUnitSourceMode.LIVE
            or producer.research_design_version != design.version
            or producer.status is not WorkUnitStatus.BLOCKED
            or not _artifact_has_explicit_semantic_gap(artifact)
        ):
            continue
        instance = instance_by_ref.get(
            (producer.substage_instance_id, producer.substage_instance_revision)
        )
        sequence = (
            int(instance.sequence_hint)
            if instance is not None and instance.sequence_hint is not None
            else -1
        )
        candidates.append((sequence, artifact.artifact_id, artifact, producer))
    if not candidates:
        return None

    _, _, result_artifact, producer = max(candidates, key=lambda row: (row[0], row[1]))
    by_id = {row.artifact_id: row for row in task.artifacts}
    source_snapshots = []
    for ref in producer.input_artifact_refs:
        source = by_id.get(ref)
        if source is None or source.artifact_state is not ArtifactState.USABLE:
            continue
        source_work = work_by_id.get(source.work_unit_id)
        if (
            source_work is None
            or source_work.status is not WorkUnitStatus.COMPLETED
            or source_work.research_design_version != design.version
        ):
            continue
        source_snapshots.append(_final_result_artifact_snapshot(source))

    return {
        "mode": "artifact_set",
        "researchDesignVersion": design.version,
        "resultArtifactRefs": [result_artifact.artifact_id],
        "sourceArtifacts": source_snapshots,
        "assembledAt": assembled_at.astimezone(timezone.utc).isoformat(),
        "integrityContractVersion": "legacy_v0",
    }


_OPTIONAL_ENHANCEMENT_UNAVAILABLE_REASON = "optional_enhancement_unavailable"
_OT_WORKFLOW_ID = "opentrajectory_research_workflow"
_OT_SKILL_PACK = "opentrajectory"
_OT_EVIDENCE_DEFINITION_ID = "opentrajectory:evidence_extraction"
_OT_MECHANISM_DEFINITION_ID = "opentrajectory:mechanism_explanation"
_OT_FINAL_DEFINITION_ID = "opentrajectory:findings_synthesis"


def _workflow_definition_node(
    workflow_definition: Mapping[str, Any],
    node_key: str,
) -> Mapping[str, Any] | None:
    nodes = workflow_definition.get("nodes")
    if not isinstance(nodes, list):
        return None
    matches = [
        row for row in nodes
        if isinstance(row, Mapping) and row.get("nodeKey") == node_key
    ]
    return matches[0] if len(matches) == 1 else None


def _workflow_node_for_instance(
    workflow_definition: Mapping[str, Any],
    instance: SubstageInstance,
) -> tuple[str, Mapping[str, Any]] | None:
    nodes = workflow_definition.get("nodes")
    if not isinstance(nodes, list):
        return None
    matches = [
        row
        for row in nodes
        if isinstance(row, Mapping)
        and row.get("definitionId") == instance.substage_definition_id
        and row.get("definitionVersion") == instance.substage_definition_version
        and isinstance(row.get("nodeKey"), str)
    ]
    if len(matches) != 1:
        return None
    return str(matches[0]["nodeKey"]), matches[0]


def _workflow_dependency(
    workflow_definition: Mapping[str, Any],
    *,
    upstream_node_key: str,
    downstream_node_key: str,
) -> Mapping[str, Any] | None:
    dependencies = workflow_definition.get("dependencies")
    if not isinstance(dependencies, list):
        return None
    matches = [
        row
        for row in dependencies
        if isinstance(row, Mapping)
        and row.get("upstreamNodeKey") == upstream_node_key
        and row.get("downstreamNodeKey") == downstream_node_key
    ]
    return matches[0] if len(matches) == 1 else None


def _workflow_dependency_pairs(
    workflow_definition: Mapping[str, Any],
) -> set[tuple[str, str]]:
    dependencies = workflow_definition.get("dependencies")
    if not isinstance(dependencies, list):
        return set()
    return {
        (str(row.get("upstreamNodeKey") or ""), str(row.get("downstreamNodeKey") or ""))
        for row in dependencies
        if isinstance(row, Mapping)
    }


def _generic_optional_enhancement_failure_authorized(
    task: ResearchTaskV12,
    *,
    design: Any,
    origin_instance: SubstageInstance,
    workflow_definition: Mapping[str, Any],
) -> bool:
    """Authorize degradation only from an explicit frozen optional edge.

    The frozen Workflow owns the product meaning: an optional upstream may be
    attempted before its consumer, but a known terminal failure may release the
    consumer only when every actual dependency from that upstream is declared
    ``optional_enhancement``.  Artifact and confirmation dependencies remain
    hard gates.
    """

    origin_match = _workflow_node_for_instance(workflow_definition, origin_instance)
    if origin_match is None:
        return False
    origin_node_key, origin_node = origin_match
    if origin_node.get("requirement") != "optional":
        return False

    origin_ref = (
        origin_instance.substage_instance_id,
        origin_instance.instance_revision,
    )
    dependents: list[tuple[SubstageInstance, list[Any]]] = []
    for candidate in execution_instances_for_design(task, design):
        matching_dependencies = [
            dependency
            for dependency in candidate.dependencies
            if (
                dependency.upstream_instance_ref.substage_instance_id,
                dependency.upstream_instance_ref.instance_revision,
            )
            == origin_ref
        ]
        if matching_dependencies:
            dependents.append((candidate, matching_dependencies))
    if not dependents:
        return False

    runtime_by_ref = {
        (row.substage_instance_id, row.instance_revision): row
        for row in task.substage_runtime_states
    }
    for dependent, instance_dependencies in dependents:
        if any(
            dependency.condition_type is not DependencyConditionType.INSTANCE_COMPLETED
            for dependency in instance_dependencies
        ):
            return False
        dependent_match = _workflow_node_for_instance(workflow_definition, dependent)
        if dependent_match is None:
            return False
        dependent_node_key, _ = dependent_match
        frozen_dependency = _workflow_dependency(
            workflow_definition,
            upstream_node_key=origin_node_key,
            downstream_node_key=dependent_node_key,
        )
        if (
            frozen_dependency is None
            or frozen_dependency.get("dependencyType") != "optional_enhancement"
        ):
            return False
        dependent_runtime = runtime_by_ref.get(
            (dependent.substage_instance_id, dependent.instance_revision)
        )
        if (
            dependent_runtime is None
            or dependent_runtime.status
            not in {SubstageRuntimeStatus.PLANNED, SubstageRuntimeStatus.READY}
        ):
            return False
    return True


def _legacy_ot_optional_mechanism_failure_authorized(
    task: ResearchTaskV12,
    *,
    design: Any,
    decision: ExecutionDecision,
    origin_instance: SubstageInstance,
    workflow_lineage: Mapping[str, Any],
    workflow_definition: Mapping[str, Any],
) -> bool:
    """Preserve the already-shipped OT v1 Node9 degradation contract."""

    if (
        workflow_lineage.get("workflowId") != _OT_WORKFLOW_ID
        or workflow_lineage.get("skillPack") != _OT_SKILL_PACK
        or workflow_definition.get("workflowId") != _OT_WORKFLOW_ID
        or workflow_definition.get("skillPack") != _OT_SKILL_PACK
    ):
        return False

    evidence_node = _workflow_definition_node(workflow_definition, "evidence_extraction")
    mechanism_node = _workflow_definition_node(workflow_definition, "mechanism_explanation")
    final_node = _workflow_definition_node(workflow_definition, "findings_synthesis")
    if not (
        evidence_node is not None
        and evidence_node.get("definitionId") == _OT_EVIDENCE_DEFINITION_ID
        and evidence_node.get("requirement") == "required"
        and mechanism_node is not None
        and mechanism_node.get("definitionId") == _OT_MECHANISM_DEFINITION_ID
        and mechanism_node.get("requirement") == "optional"
        and final_node is not None
        and final_node.get("definitionId") == _OT_FINAL_DEFINITION_ID
        and final_node.get("requirement") == "required"
    ):
        return False
    dependency_pairs = _workflow_dependency_pairs(workflow_definition)
    if not (
        ("evidence_extraction", "mechanism_explanation") in dependency_pairs
        and ("evidence_extraction", "findings_synthesis") in dependency_pairs
        and ("mechanism_explanation", "findings_synthesis") not in dependency_pairs
    ):
        return False

    instances = list(execution_instances_for_design(task, design))
    evidence_instances = [
        row for row in instances
        if row.substage_definition_id == _OT_EVIDENCE_DEFINITION_ID
    ]
    mechanism_instances = [
        row for row in instances
        if row.substage_definition_id == _OT_MECHANISM_DEFINITION_ID
    ]
    final_instances = [
        row for row in instances
        if row.substage_definition_id == _OT_FINAL_DEFINITION_ID
    ]
    if not (len(evidence_instances) == len(mechanism_instances) == len(final_instances) == 1):
        return False
    evidence_instance = evidence_instances[0]
    mechanism_instance = mechanism_instances[0]
    final_instance = final_instances[0]
    if origin_instance != mechanism_instance:
        return False

    def dependency_refs(instance: SubstageInstance) -> set[tuple[str, int]]:
        return {
            (
                row.upstream_instance_ref.substage_instance_id,
                row.upstream_instance_ref.instance_revision,
            )
            for row in instance.dependencies
        }

    evidence_ref = (
        evidence_instance.substage_instance_id,
        evidence_instance.instance_revision,
    )
    mechanism_ref = (
        mechanism_instance.substage_instance_id,
        mechanism_instance.instance_revision,
    )
    if evidence_ref not in dependency_refs(mechanism_instance):
        return False
    if (
        evidence_ref not in dependency_refs(final_instance)
        or mechanism_ref in dependency_refs(final_instance)
    ):
        return False

    runtime_by_ref = {
        (row.substage_instance_id, row.instance_revision): row
        for row in task.substage_runtime_states
    }
    evidence_runtime = runtime_by_ref.get(evidence_ref)
    final_runtime = runtime_by_ref.get(
        (final_instance.substage_instance_id, final_instance.instance_revision)
    )
    if (
        evidence_runtime is None
        or evidence_runtime.status is not SubstageRuntimeStatus.COMPLETED
        or final_runtime is None
        or final_runtime.status
        not in {SubstageRuntimeStatus.PLANNED, SubstageRuntimeStatus.READY}
    ):
        return False

    work_by_id = {row.work_unit_id: row for row in task.work_units}
    return any(
        artifact.artifact_state is ArtifactState.USABLE
        and artifact.artifact_type == "research_records"
        and (producer := work_by_id.get(artifact.work_unit_id)) is not None
        and producer.source_mode is WorkUnitSourceMode.LIVE
        and producer.status is WorkUnitStatus.COMPLETED
        and producer.research_design_version == design.version
        and producer.substage_definition_id == _OT_EVIDENCE_DEFINITION_ID
        and producer.substage_instance_id == evidence_instance.substage_instance_id
        and producer.substage_instance_revision == evidence_instance.instance_revision
        for artifact in task.artifacts
    )


def project_system_owned_optional_enhancement_failure(
    task: ResearchTaskV12,
    *,
    execution_decision_id: str,
    occurred_at: datetime,
    workflow_lineage: Mapping[str, Any],
    workflow_definition: Mapping[str, Any],
) -> ResearchTaskV12:
    """Project a proven optional enhancement failure to a truthful system skip.

    New Workflow versions authorize the skip with an explicit frozen
    ``optional_enhancement`` dependency.  The historical OT v1 contract remains
    supported unchanged.  Provider/error strings never choose the policy;
    cancelled and externally-unknown executions therefore remain blocking.
    """

    if (
        task.status in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}
        or task.final_result_authority is not None
    ):
        return task
    active_ref = task.active_research_design_ref
    if active_ref is None:
        return task
    if (
        workflow_definition.get("workflowId") != workflow_lineage.get("workflowId")
        or workflow_definition.get("skillPack") != workflow_lineage.get("skillPack")
        or workflow_definition.get("workflowVersion")
        != workflow_lineage.get("workflowVersion")
        or workflow_lineage.get("researchDesignId") != active_ref.research_design_id
        or workflow_lineage.get("researchDesignVersion") != active_ref.version
    ):
        return task

    design = next(
        (
            row for row in task.research_design_versions
            if row.research_design_id == active_ref.research_design_id
            and row.version == active_ref.version
        ),
        None,
    )
    if design is None or design.status is not ResearchDesignStatus.CONFIRMED:
        return task

    decision = next(
        (
            row for row in task.execution_decisions
            if row.execution_decision_id == execution_decision_id
        ),
        None,
    )
    if (
        decision is None
        or decision.status is not ExecutionDecisionStatus.CLOSED
        or decision.outcome is not ExecutionDecisionOutcome.BLOCKED
        or decision.purpose is not ExecutionPurpose.COMPLETION
        or decision.research_design_version != design.version
    ):
        return task

    origin_instance = next(
        (
            row for row in execution_instances_for_design(task, design)
            if row.substage_instance_id == decision.substage_instance_id
            and row.instance_revision == decision.substage_instance_revision
        ),
        None,
    )
    if origin_instance is None:
        return task
    origin_runtime = next(
        (
            row for row in task.substage_runtime_states
            if row.substage_instance_id == origin_instance.substage_instance_id
            and row.instance_revision == origin_instance.instance_revision
        ),
        None,
    )
    if (
        origin_runtime is None
        or origin_runtime.status is not SubstageRuntimeStatus.BLOCKED_RECOVERABLE
    ):
        return task

    decision_works = [
        row for row in task.work_units
        if row.execution_decision_id == execution_decision_id
        and row.research_design_version == design.version
        and row.substage_instance_id == origin_instance.substage_instance_id
        and row.substage_instance_revision == origin_instance.instance_revision
    ]
    if (
        not decision_works
        or any(
            row.status in {
                WorkUnitStatus.CANCELLED,
                WorkUnitStatus.EXTERNAL_RESULT_UNKNOWN,
            }
            for row in decision_works
        )
        or any(
            row.status
            not in {
                WorkUnitStatus.COMPLETED,
                WorkUnitStatus.FAILED,
                WorkUnitStatus.BLOCKED,
            }
            for row in decision_works
        )
        or not any(row.status is WorkUnitStatus.BLOCKED for row in decision_works)
    ):
        return task

    authorized = _generic_optional_enhancement_failure_authorized(
        task,
        design=design,
        origin_instance=origin_instance,
        workflow_definition=workflow_definition,
    ) or _legacy_ot_optional_mechanism_failure_authorized(
        task,
        design=design,
        decision=decision,
        origin_instance=origin_instance,
        workflow_lineage=workflow_lineage,
        workflow_definition=workflow_definition,
    )
    if not authorized:
        return task

    payload = task.model_dump(mode="json", by_alias=True)
    runtime = next(
        row for row in payload["substageRuntimeStates"]
        if row["substageInstanceId"] == origin_instance.substage_instance_id
        and row["instanceRevision"] == origin_instance.instance_revision
    )
    runtime.update(
        {
            "status": SubstageRuntimeStatus.SKIPPED.value,
            "activeExecutionDecisionId": None,
            "statusReasonCode": _OPTIONAL_ENHANCEMENT_UNAVAILABLE_REASON,
            "updatedAt": occurred_at.astimezone(timezone.utc).isoformat(),
        }
    )
    payload.update(
        {
            "status": TaskStatus.RUNNING.value,
            "finishedAt": None,
        }
    )
    return ResearchTaskV12.model_validate(payload)


def _system_owned_nonblocking_skip_allows_continuation(
    task: ResearchTaskV12,
    *,
    decision: ExecutionDecision,
    origin_runtime: Any,
    execution_mode: TaskExecutionMode,
) -> bool:
    if (
        execution_mode is not TaskExecutionMode.AUTOMATIC_RESEARCH
        or decision.outcome is not ExecutionDecisionOutcome.BLOCKED
        or origin_runtime is None
        or origin_runtime.status is not SubstageRuntimeStatus.SKIPPED
    ):
        return False
    if research_gap_continues(origin_runtime.status_reason_code):
        return True
    if origin_runtime.status_reason_code != _OPTIONAL_ENHANCEMENT_UNAVAILABLE_REASON:
        return False
    active_ref = task.active_research_design_ref
    if active_ref is None:
        return False
    design = next(
        (
            row for row in task.research_design_versions
            if row.research_design_id == active_ref.research_design_id
            and row.version == active_ref.version
            and row.status is ResearchDesignStatus.CONFIRMED
        ),
        None,
    )
    if design is None:
        return False
    return any(
        row.substage_instance_id == decision.substage_instance_id
        and row.instance_revision == decision.substage_instance_revision
        for row in execution_instances_for_design(task, design)
    )



def _build_final_result_authority(
    task: ResearchTaskV12,
    design: Any,
    *,
    assembled_at: datetime,
) -> dict[str, Any] | None:
    work_by_id = {row.work_unit_id: row for row in task.work_units}
    semantic_gap_authority = _semantic_gap_final_result_authority(
        task,
        design,
        assembled_at=assembled_at,
    )
    usable = [
        artifact
        for artifact in task.artifacts
        if artifact.artifact_state is ArtifactState.USABLE
        and (producer := work_by_id.get(artifact.work_unit_id)) is not None
        and producer.source_mode is WorkUnitSourceMode.LIVE
        and producer.status is WorkUnitStatus.COMPLETED
        and producer.research_design_version == design.version
    ]
    if not usable:
        return semantic_gap_authority
    artifacts_by_instance: dict[tuple[str, int], list[Any]] = {}
    for artifact in usable:
        producer = work_by_id[artifact.work_unit_id]
        artifacts_by_instance.setdefault(
            (producer.substage_instance_id, producer.substage_instance_revision), []
        ).append(artifact)

    report_instances = [
        instance for instance in design.instances
        if _is_leaf_report_generation_instance(design, instance)
    ]
    if report_instances:
        report_refs = {
            (row.substage_instance_id, row.instance_revision) for row in report_instances
        }
        decision_by_id = {row.execution_decision_id: row for row in task.execution_decisions}
        result_artifacts: list[Any] = []
        for ref in report_refs:
            for artifact in artifacts_by_instance.get(ref, []):
                work = work_by_id[artifact.work_unit_id]
                decision = decision_by_id.get(work.execution_decision_id)
                if decision is None or decision.status is not ExecutionDecisionStatus.CLOSED:
                    continue
                assignment = next(
                    (row for row in decision.assignments if row.assignment_id == work.assignment_id),
                    None,
                )
                if assignment is None or not _artifact_has_completion_authority(
                    artifact.model_dump(mode="json", by_alias=True),
                    work.model_dump(mode="json", by_alias=True),
                    assignment.model_dump(mode="json", by_alias=True),
                    decision.model_dump(mode="json", by_alias=True),
                ):
                    continue
                if any(
                    artifact.artifact_type == requirement.artifact_type
                    and work.assignment_id in requirement.source_assignment_ids
                    for requirement in decision.result_assembly_policy.final_artifact_requirements
                ):
                    result_artifacts.append(artifact)
        if not result_artifacts:
            return semantic_gap_authority
        result_ids = {row.artifact_id for row in result_artifacts}
        report_works = [
            work
            for work in task.work_units
            if work.research_design_version == design.version
            and (work.substage_instance_id, work.substage_instance_revision) in report_refs
            and work.status is WorkUnitStatus.COMPLETED
        ]
        if any(work.input_artifact_refs and not work.input_artifact_snapshots for work in report_works):
            return None
        source_snapshots_by_id: dict[str, Any] = {}
        for work in report_works:
            for snapshot in work.input_artifact_snapshots:
                if snapshot.artifact_id not in result_ids:
                    source_snapshots_by_id.setdefault(snapshot.artifact_id, snapshot)
        return {
            "mode": "assembled_artifact",
            "researchDesignVersion": design.version,
            "resultArtifactRefs": [row.artifact_id for row in result_artifacts],
            "sourceArtifacts": [
                row.model_dump(mode="json", by_alias=True)
                for row in source_snapshots_by_id.values()
            ],
            "assembledAt": assembled_at.astimezone(timezone.utc).isoformat(),
            "integrityContractVersion": "legacy_v0",
        }

    # A normal set of leaf artifacts is intermediate research evidence, not a
    # canonical final result. An explicit semantic-material gap is the only
    # degraded artifact-set exception, and remains visibly non-usable downstream.
    return semantic_gap_authority


def _latest_successful_decision_ids_per_instance(
    task: ResearchTaskV12,
    *,
    research_design_version: int,
    instance_refs: set[tuple[str, int]],
) -> set[str]:
    """For each upstream instance, the latest CLOSED successful decision id.

    Repeatable re-entry rounds append new successful decisions for the same
    instance; downstream consumers must freeze only the latest round's
    artifacts, or every historical round's same-typed outputs flow into the
    same Binding slot and fail closed as ambiguous.
    """
    latest: dict[tuple[str, int], ExecutionDecision] = {}
    for decision in task.execution_decisions:
        instance_ref = (decision.substage_instance_id, decision.substage_instance_revision)
        if (
            decision.research_design_version != research_design_version
            or instance_ref not in instance_refs
            or decision.status is not ExecutionDecisionStatus.CLOSED
            or decision.outcome not in _BINDING_SOURCE_SUCCESSFUL_OUTCOMES
        ):
            continue
        previous = latest.get(instance_ref)
        if previous is None or (decision.decision_sequence, decision.decided_at) > (
            previous.decision_sequence, previous.decided_at
        ):
            latest[instance_ref] = decision
    return {row.execution_decision_id for row in latest.values()}


def _upstream_dependency_materials(
    task: ResearchTaskV12,
    instance: SubstageInstance,
    *,
    research_design_version: int,
) -> tuple[list[str], list[str]]:
    """Freeze usable outputs from the exact persisted upstream instance lineage.

    Cross-L2 structured handoff must never rediscover outputs by title, Skill
    name, or current workflow files. The SubstageDependency already freezes the
    authoritative upstream instance id/revision, so root WorkUnits inherit only
    USABLE artifacts produced by completed LIVE WorkUnits on those exact refs.
    The dispatch provider later narrows these refs again by frozen Skill
    identity/version before materializing structured parameters.
    """
    design = _active_design_for_instance(
        task, instance, research_design_version=research_design_version
    )
    if design is not None and _is_leaf_report_generation_instance(design, instance):
        upstream_refs = {
            (candidate.substage_instance_id, candidate.instance_revision)
            for candidate in design.instances
            if candidate.substage_instance_id != instance.substage_instance_id
        }
    else:
        upstream_refs = {
            (
                dependency.upstream_instance_ref.substage_instance_id,
                dependency.upstream_instance_ref.instance_revision,
            )
            for dependency in instance.dependencies
        }
    if not upstream_refs:
        return [], []

    work_units = {row.work_unit_id: row for row in task.work_units}
    latest_round_decisions = _latest_successful_decision_ids_per_instance(
        task,
        research_design_version=research_design_version,
        instance_refs=upstream_refs,
    )
    artifact_refs: list[str] = []
    dependency_work_unit_ids: list[str] = []
    for artifact in task.artifacts:
        if artifact.artifact_state is not ArtifactState.USABLE:
            continue
        # Declared workspace files are presentation supplements of the
        # producer's render output; they are not bindable downstream inputs
        # (see the dependency release filter for the same rule).
        if str(artifact.artifact_type).endswith(".declared_file"):
            continue
        producer = work_units.get(artifact.work_unit_id)
        if (
            producer is None
            or producer.source_mode is not WorkUnitSourceMode.LIVE
            or producer.status is not WorkUnitStatus.COMPLETED
            or producer.research_design_version != research_design_version
            or (producer.substage_instance_id, producer.substage_instance_revision)
            not in upstream_refs
            or producer.execution_decision_id not in latest_round_decisions
        ):
            continue
        artifact_refs.append(artifact.artifact_id)
        dependency_work_unit_ids.append(producer.work_unit_id)
    return (
        list(dict.fromkeys(artifact_refs)),
        list(dict.fromkeys(dependency_work_unit_ids)),
    )


def _should_materialize_upstream_dependencies(
    design: Any | None,
    instance: SubstageInstance,
    *,
    required_skill_sequence: Sequence[SkillRef],
) -> bool:
    """Freeze every declared cross-substage handoff into the new root WorkUnit."""
    return bool(instance.dependencies) or bool(required_skill_sequence) or bool(
        design is not None and _is_leaf_report_generation_instance(design, instance)
    )


def _resolved_execution_policy_for_assignment(
    snapshot: Mapping[str, Any],
    assignment: ExecutionAssignment,
) -> dict[str, Any] | None:
    """Resolve one declared durable LLM policy before WorkUnit materialization."""
    if assignment.executor_ref.executor_type != "skill":
        return None
    definitions = [
        row
        for row in snapshot.get("definitions", {}).get("skills", [])
        if row.get("skillId") == assignment.executor_ref.executor_id
        and row.get("definitionVersion") == assignment.executor_ref.definition_version
    ]
    if len(definitions) != 1:
        return None
    source_contract = definitions[0].get("sourceContract")
    if not isinstance(source_contract, Mapping):
        return None
    from app.execution.durable_llm_execution_policy import (
        DECLARATION_KEY,
        resolve_durable_llm_execution_policy,
    )

    if source_contract.get(DECLARATION_KEY) is None:
        return None
    try:
        return resolve_durable_llm_execution_policy(definitions[0])
    except (OSError, ValueError) as exc:
        raise DecisionPolicyViolation("durable_llm_execution_policy_unavailable") from exc


def _declared_invocation_fields_for_assignment(
    snapshot: Mapping[str, Any], assignment: ExecutionAssignment
) -> frozenset[str]:
    refs = {(ref.binding_id, ref.binding_version) for ref in assignment.source_binding_refs}
    if assignment.executor_ref.executor_type != "skill":
        return frozenset()
    rows = snapshot.get("bindings", {}).get("skills", [])
    bindings = [
        row for row in rows
        if isinstance(row, Mapping)
        and (row.get("bindingId"), row.get("bindingVersion")) in refs
        and row.get("enabled") is True
    ]
    if len(bindings) != len(refs) or not bindings:
        raise DecisionPolicyViolation("invocation_context_binding_authority_missing")
    result: set[str] = set()
    for binding in bindings:
        runtime_inputs = binding.get("runtimeInputs")
        if runtime_inputs is None:
            continue
        if not isinstance(runtime_inputs, Mapping) or runtime_inputs.get("contractVersion") != 2:
            raise DecisionPolicyViolation("invocation_context_binding_contract_invalid")
        fields = runtime_inputs.get("fields")
        if not isinstance(fields, list):
            raise DecisionPolicyViolation("invocation_context_binding_contract_invalid")
        for entry in fields:
            source = entry.get("source") if isinstance(entry, Mapping) else None
            if isinstance(source, Mapping) and source.get("kind") == "invocationContext":
                field = source.get("field")
                if not isinstance(field, str) or not field:
                    raise DecisionPolicyViolation("invocation_context_binding_contract_invalid")
                result.add(field)
    return frozenset(result)


def _freeze_declared_invocation_context(
    *, snapshot: Mapping[str, Any], assignments: Sequence[ExecutionAssignment],
    task: ResearchTaskV12, design: Any, caller_context: Mapping[str, Any] | None,
) -> dict[str, Any]:
    declared: set[str] = set()
    for assignment in assignments:
        declared.update(_declared_invocation_fields_for_assignment(snapshot, assignment))
    caller = dict(caller_context or {})
    if {"canonical_user_query", "confirmed_research_goal"} & set(caller):
        raise DecisionPolicyViolation("invocation_context_reserved_field")
    undeclared = sorted(set(caller) - declared)
    if undeclared:
        raise DecisionPolicyViolation("invocation_context_field_not_declared:" + ",".join(undeclared))
    frozen = dict(caller)
    if "canonical_user_query" in declared:
        source = canonical_user_query(task, design)
        if source is not None:
            frozen["canonical_user_query"] = source
    if "confirmed_research_goal" in declared:
        frozen["confirmed_research_goal"] = design.goal
    # A binding may declare caller-designated invocationContext fields (e.g.
    # experiment_ref) that only an explicitly triggered selection supplies.
    # Freezing without them would dispatch a WorkUnit whose input composition
    # deterministically fails later; block at selection time instead.
    unsatisfied = sorted(declared - frozen.keys())
    if unsatisfied:
        raise ExecutionPreflightBlocked(
            "execution_selection_invocation_context_unsatisfied",
            facts={"missingInvocationFields": unsatisfied},
        )
    return frozen


def prepare_selection_execution(
    prepared: PreparedSelection,
    task: ResearchTaskV12,
    selected_candidate_identity: str,
) -> ExecutionPreparation:
    if task.revision != prepared.expected_task_revision:
        raise ExecutionPreflightBlocked("task_revision_conflict")
    is_method = (
        prepared.execution_method is not None
        and prepared.execution_method_identity is not None
        and selected_candidate_identity == prepared.execution_method_identity
    )
    is_composition = (
        len(prepared.required_skill_sequence) > 1
        and prepared.composition_identity is not None
        and selected_candidate_identity == prepared.composition_identity
    )
    if selected_candidate_identity not in prepared.final_tie_candidate_ids:
        raise DecisionPolicyViolation("fairness_selected_candidate_invalid")
    composition_rows = (
        _composition_candidate_rows(prepared.resolution, prepared.required_skill_sequence)
        if is_composition
        else []
    )
    method_candidate_ids = {candidate_id for _, candidate_id in prepared.execution_method_role_candidates}
    method_rows = [
        row
        for row in prepared.resolution.candidate_evaluations
        if candidate_identity(row) in method_candidate_ids
    ] if is_method else []
    selected = (
        method_rows[0]
        if is_method and method_rows
        else composition_rows[0]
        if is_composition
        else next(
            (
                row
                for row in prepared.resolution.candidate_evaluations
                if candidate_identity(row) == selected_candidate_identity
            ),
            None,
        )
    )
    if selected is None:
        raise DecisionPolicyViolation("fairness_selected_candidate_invalid")
    general = False if is_method or is_composition else _is_general(prepared.snapshot_envelope, selected)
    effective_expected_types = (
        prepared.instance.expected_artifact_types
        if prepared.effective_expected_artifact_types is None
        else prepared.effective_expected_artifact_types
    )
    professional_required = any(
        row.judgment_requirement == JudgmentRequirement.DOMAIN_PROFESSIONAL
        for row in (
            prepared.instance.acceptance_criteria
            if prepared.effective_acceptance_criteria is None
            else prepared.effective_acceptance_criteria
        )
    )
    canonical_single_skill_completion = bool(
        not is_method
        and not is_composition
        and isinstance(selected.candidate_ref, SkillRef)
        and _frozen_schema_validated_completion_without_reviewer(
            prepared, selected.candidate_ref
        )
    )
    report_design = _active_design_for_instance(
        task,
        prepared.instance,
        research_design_version=prepared.resolution.design_ref.version,
    )
    leaf_final_report = bool(
        report_design is not None
        and _is_leaf_report_generation_instance(report_design, prepared.instance)
    )
    general_completion = _general_completion_authorized(
        task,
        prepared,
        selected,
        design=report_design,
        general=general,
        professional_required=professional_required,
    )
    if (
        leaf_final_report
        and not is_method
        and not is_composition
        and selected.preference.completion_coverage != CompletionCoverage.COMPLETION_CAPABLE
    ):
        raise ExecutionPreflightBlocked(
            "completion_producer_authority_missing",
            facts={"candidateEvaluationId": selected.candidate_evaluation_id},
        )
    purpose = (
        ExecutionPurpose.COMPLETION
        if (
            is_method
            or is_composition
            or canonical_single_skill_completion
            or general_completion
        )
        else ExecutionPurpose.PROGRESS
        if selected.preference.completion_coverage == CompletionCoverage.PROGRESS_CAPABLE
        or (general and professional_required)
        else ExecutionPurpose.COMPLETION
    )
    if general and any(
        row.eligible
        and row.preference.completion_coverage == CompletionCoverage.COMPLETION_CAPABLE
        and row.preference.executable_now
        and not _is_general(prepared.snapshot_envelope, row)
        for row in prepared.resolution.candidate_evaluations
    ):
        raise DecisionPolicyViolation("fallback_completion_candidate_exists")

    decision_id = _stable_id(
        "decision", task.task_id, prepared.selection_request_id, prepared.selection_payload_hash
    )
    if is_method:
        strategy = ExecutionStrategy.EXECUTION_METHOD
        assignments = _execution_method_assignments(prepared, decision_id=decision_id)
        recipe = None
        recipe_ref = None
    elif is_composition:
        strategy = ExecutionStrategy.SKILL_COMPOSITION
        assignments = _skill_composition_assignments(prepared, decision_id=decision_id)
        recipe = None
        recipe_ref = None
    elif isinstance(selected.candidate_ref, MultiAgentRecipeRef):
        strategy = ExecutionStrategy.MULTI_AGENT
        assignments, recipe = _recipe_assignments(
            prepared, selected, purpose=purpose, decision_id=decision_id
        )
        recipe_ref = selected.candidate_ref
    else:
        strategy = (
            ExecutionStrategy.DIRECT_SKILL
            if isinstance(selected.candidate_ref, SkillRef)
            else ExecutionStrategy.GENERAL_RESEARCH_AGENT
            if general
            else ExecutionStrategy.SPECIALIST_AGENT
        )
        assignments = _single_assignment(
            prepared,
            selected,
            purpose=purpose,
            decision_id=decision_id,
            leaf_final_report=leaf_final_report,
            general_completion_authorized=general_completion,
        )
        recipe = None
        recipe_ref = None

    assignment_by_key = {row.recipe_template_key: row for row in assignments}
    if is_method:
        method = prepared.execution_method or {}
        completion_roles = set(method.get("completionRoleIds") or [])
        required_ids = [
            row.assignment_id
            for row in assignments
            if row.execution_method_role_id in completion_roles
        ]
        if not required_ids:
            raise DecisionPolicyViolation("execution_method_completion_assignments_missing")
        reviewer_ids = []
        completion_template = None
        result_policy = ResultAssemblyPolicy(
            mode=(
                ResultAssemblyMode.PRIMARY_OUTPUT
                if len(required_ids) == 1
                else ResultAssemblyMode.ARTIFACT_SET
            ),
            assemblerAssignmentId=None,
            finalArtifactRequirements=[
                ArtifactRequirement(
                    artifactType=artifact_type,
                    minimumCount=1,
                    sourceAssignmentIds=list(required_ids),
                )
                for artifact_type in effective_expected_types
            ],
        )
    elif recipe is not None:
        completion_template = recipe.completion_policy_template
        required_ids = [assignment_by_key[key].assignment_id for key in completion_template.required_template_keys]
        reviewer_ids = [assignment_by_key[key].assignment_id for key in completion_template.reviewer_template_keys]
        assembly_template = recipe.result_assembly_policy_template
        result_policy = ResultAssemblyPolicy(
            mode=assembly_template.mode,
            assemblerAssignmentId=(
                assignment_by_key[assembly_template.assembler_template_key].assignment_id
                if assembly_template.assembler_template_key is not None
                else None
            ),
            finalArtifactRequirements=[
                ArtifactRequirement(
                    artifactType=row.artifact_type,
                    minimumCount=row.minimum_count,
                    sourceAssignmentIds=[assignment_by_key[key].assignment_id for key in row.source_template_keys],
                )
                for row in assembly_template.final_artifact_requirements
            ],
        )
    else:
        required_ids = [row.assignment_id for row in assignments]
        reviewer_ids = [
            row.assignment_id for row in assignments if row.role == AssignmentRole.REVIEWER
        ]
        completion_template = None
        final_primary = next(
            row for row in reversed(assignments) if row.role == AssignmentRole.PRIMARY
        )
        result_policy = ResultAssemblyPolicy(
            mode=(
                ResultAssemblyMode.ARTIFACT_SET
                if purpose == ExecutionPurpose.COMPLETION and len(effective_expected_types) > 1
                else ResultAssemblyMode.PRIMARY_OUTPUT
            ),
            assemblerAssignmentId=None,
            finalArtifactRequirements=[
                ArtifactRequirement(
                    artifactType=artifact_type,
                    minimumCount=1,
                    sourceAssignmentIds=[final_primary.assignment_id],
                )
                for artifact_type in effective_expected_types
            ] if purpose == ExecutionPurpose.COMPLETION else [],
        )
    completion_policy = None
    progress_policy = None
    if purpose == ExecutionPurpose.COMPLETION:
        completion_policy = CompletionPolicy(
            requiredAssignmentIds=required_ids,
            successMode=(completion_template.success_mode if completion_template else SuccessMode.ALL_REQUIRED),
            reviewerAssignmentIds=reviewer_ids,
            reviewRequirement=(
                completion_template.review_requirement
                if completion_template
                else ReviewRequirement.ALL_REVIEWERS_APPROVE
                if reviewer_ids
                else ReviewRequirement.NONE
            ),
            partialSuccessAllowed=(completion_template.partial_success_allowed if completion_template else False),
            onReviewerReject=(
                completion_template.on_reviewer_reject
                if completion_template
                else ReviewerRejectAction.NEW_REVISION_DECISION
            ),
        )
    else:
        progress_policy = ProgressPolicy(
            requiredAssignmentIds=required_ids,
            requiredArtifactTypes=[],
            acceptedResultCodes=ProgressResultCode.PROGRESSED,
        )

    preferred_evaluations = [
        row
        for row in prepared.resolution.candidate_evaluations
        if not _is_general(prepared.snapshot_envelope, row)
    ]
    preferred_nonfallback = [
        row.candidate_evaluation_id for row in preferred_evaluations
    ]
    completion_preferred = [
        row
        for row in preferred_evaluations
        if row.eligible
        and row.preference.completion_coverage == CompletionCoverage.COMPLETION_CAPABLE
    ]
    fallback_reason = None
    if general:
        blockers = {
            code
            for row in completion_preferred
            for code in row.preference.execution_blocker_codes
        }
        mapping = (
            (CandidateExecutionBlockerCode.REQUIRED_INPUT_MISSING, FallbackReasonCode.PREFERRED_NOT_EXECUTABLE_INPUTS),
            (CandidateExecutionBlockerCode.AUTHORIZATION_DENIED, FallbackReasonCode.PREFERRED_NOT_EXECUTABLE_AUTHORIZATION),
            (CandidateExecutionBlockerCode.BUDGET_EXCEEDED, FallbackReasonCode.PREFERRED_NOT_EXECUTABLE_BUDGET),
            (CandidateExecutionBlockerCode.RISK_EXCEEDED, FallbackReasonCode.PREFERRED_NOT_EXECUTABLE_RISK),
            (CandidateExecutionBlockerCode.PROVIDER_UNAVAILABLE, FallbackReasonCode.PREFERRED_NOT_EXECUTABLE_PROVIDER),
            (CandidateExecutionBlockerCode.RUNTIME_UNAVAILABLE, FallbackReasonCode.PREFERRED_NOT_EXECUTABLE_RUNTIME),
        )
        fallback_reason = next(
            (reason for blocker, reason in mapping if blocker in blockers),
            FallbackReasonCode.NO_COMPLETION_COVERAGE_PREFERRED_CANDIDATE,
        )

    readiness_values = list(prepared.runtime_readiness.values())
    selected_executor_ids = {row.executor_ref.executor_id for row in assignments}
    for executor_id in selected_executor_ids:
        snapshots = [
            row
            for row in readiness_values
            if (
                isinstance(row.capability_ref, AgentRef) and row.capability_ref.agent_id == executor_id
            )
            or (
                isinstance(row.capability_ref, SkillRef) and row.capability_ref.skill_id == executor_id
            )
        ]
        if len(snapshots) != 1 or not snapshots[0].ready or snapshots[0].expires_at <= prepared.decided_at:
            raise RuntimeReadinessInvalid("selected_runtime_readiness_invalid", executor_id)
        if snapshots[0].runtime_binding_id is None:
            raise RuntimeReadinessInvalid("selected_runtime_binding_missing", executor_id)

    design_for_invocation = next(
        row for row in task.research_design_versions
        if row.version == prepared.resolution.design_ref.version
    )
    frozen_invocation_context = _freeze_declared_invocation_context(
        snapshot=prepared.snapshot_envelope,
        assignments=assignments,
        task=task,
        design=design_for_invocation,
        caller_context=prepared.invocation_context,
    )

    sequence = 1 + max(
        (
            row.decision_sequence
            for row in task.execution_decisions
            if row.substage_instance_id == prepared.instance.substage_instance_id
            and row.substage_instance_revision == prepared.instance.instance_revision
        ),
        default=0,
    )
    decision = ExecutionDecision(
        executionDecisionId=decision_id,
        selectionRequestId=prepared.selection_request_id,
        decisionSequence=sequence,
        supersedesExecutionDecisionId=None,
        revisionReasonCode=None,
        taskId=task.task_id,
        researchDesignVersion=prepared.resolution.design_ref.version,
        substageInstanceId=prepared.instance.substage_instance_id,
        substageInstanceRevision=prepared.instance.instance_revision,
        registrySnapshotRef=prepared.resolution.registry_snapshot_ref,
        purpose=purpose,
        strategy=strategy,
        multiAgentRecipeRef=recipe_ref,
        executionMethodRef=(
            {
                "methodId": prepared.execution_method.get("methodId"),
                "methodVersion": prepared.execution_method.get("methodVersion"),
            }
            if is_method and prepared.execution_method is not None
            else None
        ),
        candidateEvaluations=prepared.resolution.candidate_evaluations,
        fallbackInvoked=general,
        fallbackReasonCode=fallback_reason,
        preferredCandidateEvaluationRefs=(
            [row.candidate_evaluation_id for row in method_rows]
            if is_method
            else preferred_nonfallback
            if general
            else [row.candidate_evaluation_id for row in composition_rows]
            if is_composition
            else [selected.candidate_evaluation_id]
        ),
        assignments=assignments,
        invocationContext=frozen_invocation_context,
        completionPolicy=completion_policy,
        progressPolicy=progress_policy,
        resultAssemblyPolicy=result_policy,
        runtimeReadiness=readiness_values,
        budgetSnapshot=BudgetSnapshot(
            boundary=next(
                design.budget_boundary
                for design in task.research_design_versions
                if design.version == prepared.resolution.design_ref.version
            ),
            consumedModelCalls=0,
            consumedToolCalls=0,
            consumedCostAmount=0,
        ),
        policyVersion=DECISION_POLICY_VERSION,
        rationale=(
            (
                "Next research task selected from confirmed ResearchDesign against "
                f"ScientificState v{prepared.next_task_scientific_state_version}: "
                f"{prepared.next_task_rationale} Capability selection: "
            )
            if prepared.next_task_scientific_state_version is not None
            and prepared.next_task_rationale is not None
            else ""
        )
        + (
            "Materialized execution method roles with concrete capabilities selected by ExecutionDecision."
            if is_method
            else "Materialized canonical ordered required Skill composition from frozen Registry authority."
            if is_composition
            else f"Selected {selected.candidate_evaluation_id} by frozen policy; fallback={str(general).lower()}."
        ),
        status=ExecutionDecisionStatus.DISPATCHED,
        outcome=None,
        decidedAt=prepared.decided_at,
        closedAt=None,
    )
    validate_execution_decision(
        decision,
        resolution=prepared.resolution,
        snapshot_envelope=prepared.snapshot_envelope,
        recipe=recipe,
        leaf_final_report=leaf_final_report,
        general_completion_authorized=general_completion,
    )

    design = next(
        row
        for row in task.research_design_versions
        if row.version == decision.research_design_version
    )
    work_units: list[PreparedWorkUnit] = []
    artifact_ids = {row.artifact_id for row in task.artifacts}
    should_materialize_upstream = _should_materialize_upstream_dependencies(
        report_design,
        prepared.instance,
        required_skill_sequence=prepared.required_skill_sequence,
    )
    upstream_artifact_refs, upstream_work_unit_ids = (
        _upstream_dependency_materials(
            task,
            prepared.instance,
            research_design_version=decision.research_design_version,
        )
        if should_materialize_upstream
        else ([], [])
    )
    for assignment in assignments:
        if assignment.dependency_assignment_ids:
            continue
        if is_method and not _method_assignment_release_allowed(task, assignment):
            continue
        work_unit_id = _stable_id(
            "workunit", decision_id, assignment.assignment_id
        )
        idempotency_key = (
            f"workunit:{decision_id}:{assignment.assignment_id}"
        )
        binding_artifact_refs = _binding_declared_artifact_refs(
            task,
            snapshot=prepared.snapshot_envelope,
            assignment=assignment,
            research_design_version=decision.research_design_version,
            same_decision_id=decision_id,
        )
        input_artifact_refs = list(
            dict.fromkeys(
                [
                    *[
                        ref
                        for ref in assignment.input_refs
                        if ref in artifact_ids
                    ],
                    *upstream_artifact_refs,
                    *binding_artifact_refs,
                ]
            )
        )
        evaluated_risk = next(
            (
                row.preference.risk_class
                for row in prepared.resolution.candidate_evaluations
                if (
                    getattr(row.candidate_ref, "skill_id", None)
                    == assignment.executor_ref.executor_id
                    or getattr(row.candidate_ref, "agent_id", None)
                    == assignment.executor_ref.executor_id
                )
            ),
            selected.preference.risk_class,
        )
        work_units.append(
            PreparedWorkUnit(
                assignment=assignment,
                work_unit_id=work_unit_id,
                idempotency_key=idempotency_key,
                input_artifact_refs=tuple(input_artifact_refs),
                input_artifact_snapshots=tuple(
                    _artifact_snapshots_for_refs(task, input_artifact_refs)
                ),
                input_memory_refs=tuple(
                    ref
                    for ref in assignment.input_refs
                    if ref not in artifact_ids
                ),
                invocation_context={
                    key: value for key, value in frozen_invocation_context.items()
                    if key in _declared_invocation_fields_for_assignment(prepared.snapshot_envelope, assignment)
                },
                dependency_work_unit_ids=tuple(upstream_work_unit_ids),
                risk_snapshot=RiskSnapshot(
                    boundary=design.risk_boundary,
                    evaluatedRiskClass=evaluated_risk,
                    triggeredHumanGates=[],
                ),
                resolved_execution_policy=_resolved_execution_policy_for_assignment(
                    prepared.snapshot_envelope, assignment
                ),
            )
        )
    if not work_units:
        raise DecisionPolicyViolation("first_dispatch_empty")
    return ExecutionPreparation(
        task=task,
        decision=decision,
        work_units=tuple(work_units),
        selected_candidate_identity=selected_candidate_identity,
    )


def _freeze_prepared_work_unit(
    *,
    task: ResearchTaskV12,
    decision: ExecutionDecision,
    prepared_work_unit: PreparedWorkUnit,
    frozen_at: datetime,
) -> tuple[WorkUnit, DispatchRecord]:
    """Freeze one already-validated input preparation into durable models."""
    assignment = prepared_work_unit.assignment
    design = next(
        row
        for row in task.research_design_versions
        if row.version == decision.research_design_version
    )
    instance = execution_instance_for_ref(
        task,
        design,
        InstanceRef(
            substageInstanceId=decision.substage_instance_id,
            instanceRevision=decision.substage_instance_revision,
        ),
    )
    if instance is None:
        raise DecisionPolicyViolation("decision_instance_missing")
    work_unit = WorkUnit(
        workUnitId=prepared_work_unit.work_unit_id,
        idempotencyKey=prepared_work_unit.idempotency_key,
        taskId=task.task_id,
        executionDecisionId=decision.execution_decision_id,
        assignmentId=assignment.assignment_id,
        researchDesignVersion=decision.research_design_version,
        substageInstanceId=instance.substage_instance_id,
        substageInstanceRevision=instance.instance_revision,
        substageDefinitionId=instance.substage_definition_id,
        substageDefinitionVersion=instance.substage_definition_version,
        registrySnapshotRef=decision.registry_snapshot_ref,
        responsibleActorRef=assignment.responsible_actor_ref,
        executorRef=assignment.executor_ref,
        instruction=assignment.objective,
        inputArtifactRefs=list(prepared_work_unit.input_artifact_refs),
        inputArtifactSnapshots=list(prepared_work_unit.input_artifact_snapshots),
        inputMemoryRefs=list(prepared_work_unit.input_memory_refs),
        invocationContext=dict(prepared_work_unit.invocation_context),
        resolvedParameters=(
            None
            if prepared_work_unit.resolved_parameters is None
            else dict(prepared_work_unit.resolved_parameters)
        ),
        resolvedExecutionPolicy=(
            None
            if prepared_work_unit.resolved_execution_policy is None
            else dict(prepared_work_unit.resolved_execution_policy)
        ),
        inputScientificStateVersion=prepared_work_unit.input_scientific_state_version,
        executionMethodRef=(
            None if decision.execution_method_ref is None else dict(decision.execution_method_ref)
        ),
        executionMethodRoleId=assignment.execution_method_role_id,
        executionMethodIteration=assignment.execution_method_iteration,
        executionMethodRoleControl=(
            None
            if assignment.execution_method_role_control is None
            else dict(assignment.execution_method_role_control)
        ),
        allowedSkillRefs=assignment.allowed_skill_refs,
        authorizationSnapshot=AuthorizationSnapshot(
            policyVersion="authorization.v1",
            allowedMemoryScopes=["task"],
            allowedExternalActionKinds=design.external_action_boundary.allowed_action_kinds,
            confirmationRefs=[design.confirmation_ref] if design.confirmation_ref else [],
        ),
        budgetSnapshot=decision.budget_snapshot,
        riskSnapshot=prepared_work_unit.risk_snapshot,
        expectedArtifactTypes=assignment.expected_artifact_types,
        acceptanceCriteria=assignment.acceptance_criteria,
        stopConditions=[
            "acceptance_policy_terminal",
            *[
                f"execution_method:{condition_id}"
                for condition_id in (
                    (assignment.execution_method_role_control or {}).get("stopConditionIds") or []
                )
            ],
        ],
        dependencyWorkUnitIds=list(prepared_work_unit.dependency_work_unit_ids),
        executionGroupId=(
            decision.execution_decision_id
            if len(decision.assignments) > 1
            else None
        ),
        policyVersion=task.execution_policy_version,
        sourceMode=WorkUnitSourceMode.LIVE,
        dispatchable=True,
        status=WorkUnitStatus.QUEUED,
        terminalReasonCode=None,
        dispatchedAt=frozen_at,
        frozenAt=frozen_at,
        finishedAt=None,
    )
    dispatch = DispatchRecord(
        dispatchId=_stable_id("dispatch", prepared_work_unit.work_unit_id),
        taskId=task.task_id,
        executionDecisionId=decision.execution_decision_id,
        workUnitId=prepared_work_unit.work_unit_id,
        idempotencyKey=prepared_work_unit.idempotency_key,
        executionNamespace=settings.execution_namespace,
        status=DispatchStatus.PENDING,
        attemptCount=0,
        leaseOwner=None,
        leaseExpiresAt=None,
        authorizationTaskRevision=None,
        remoteRunId=None,
        nextAttemptAt=None,
        lastErrorCode=None,
        createdAt=frozen_at,
        updatedAt=frozen_at,
    )
    return work_unit, dispatch


def freeze_selection_execution(
    prepared: PreparedSelection,
    task: ResearchTaskV12,
    preparation: ExecutionPreparation,
) -> ExecutionMaterialization:
    """Create WorkUnits only after the preparation passed the input gate."""
    if task.revision != prepared.expected_task_revision:
        raise ExecutionPreflightBlocked("task_revision_conflict")
    if preparation.task.revision != task.revision:
        raise DecisionPolicyViolation("execution_preparation_revision_mismatch")

    work_units: list[WorkUnit] = []
    dispatches: list[DispatchRecord] = []
    preflight_ids = {row.assignment_id for row in preparation.decision.assignment_preflight_outcomes
                     if row.failure_kind == "contract_violation"}
    for prepared_work_unit in preparation.work_units:
        if (_preflight_blocker(preparation.decision) is not None
                or prepared_work_unit.assignment.assignment_id in preflight_ids):
            continue
        work_unit, dispatch = _freeze_prepared_work_unit(
            task=task,
            decision=preparation.decision,
            prepared_work_unit=prepared_work_unit,
            frozen_at=prepared.decided_at,
        )
        work_units.append(work_unit)
        dispatches.append(dispatch)

    payload = task.model_dump(mode="json", by_alias=True)
    # A user-authorized continuation consumes the durable stage/manual gate as a
    # whole.  Clear it from every READY candidate before queuing the selected
    # instance so stale pause facts cannot flip the task back after this write.
    progression_pause_reasons = set(_POST_CONFIRM_USER_CONFIRMATION_REASON_BY_MODE.values())
    if task.status is TaskStatus.AWAITING_CONFIRMATION:
        for runtime_row in payload["substageRuntimeStates"]:
            if (
                runtime_row["status"] == SubstageRuntimeStatus.READY.value
                and runtime_row.get("statusReasonCode") in progression_pause_reasons
            ):
                runtime_row["statusReasonCode"] = None
    payload["executionDecisions"].append(
        preparation.decision.model_dump(mode="json", by_alias=True)
    )
    payload["workUnits"].extend(
        row.model_dump(mode="json", by_alias=True) for row in work_units
    )
    runtime = next(
        row
        for row in payload["substageRuntimeStates"]
        if row["substageInstanceId"] == prepared.instance.substage_instance_id
        and row["instanceRevision"] == prepared.instance.instance_revision
    )
    runtime.update(
        {
            "status": "queued",
            "activeExecutionDecisionId": preparation.decision.execution_decision_id,
            "workUnitIds": [row.work_unit_id for row in work_units],
            "statusReasonCode": None,
            "updatedAt": prepared.decided_at.isoformat(),
        }
    )
    if payload["startedAt"] is None:
        payload["startedAt"] = prepared.decided_at.isoformat()
    materialized_task = _settle_method_frontier(
        ResearchTaskV12.model_validate(payload), decision=preparation.decision,
        occurred_at=prepared.decided_at,
    )
    materialized_task = reconcile_task_lifecycle_from_runtime(
        materialized_task,
        occurred_at=prepared.decided_at,
    )
    final_decision = next(row for row in materialized_task.execution_decisions
                          if row.execution_decision_id == preparation.decision.execution_decision_id)
    return ExecutionMaterialization(
        task=materialized_task,
        decision=final_decision,
        work_units=tuple(work_units),
        dispatches=tuple(dispatches),
        selected_candidate_identity=preparation.selected_candidate_identity,
    )



def _completion_authority_candidate(
    decision: Mapping[str, Any],
    assignment: Mapping[str, Any],
) -> Mapping[str, Any] | None:
    if assignment.get("completionCoverage") != "completion_capable":
        return None
    executor = assignment.get("executorRef") or {}
    binding_refs = assignment.get("sourceBindingRefs") or []

    def matches(row: Mapping[str, Any]) -> bool:
        ref = row.get("candidateRef") or {}
        if executor.get("executorType") == "agent":
            identity_matches = (
                ref.get("agentId") == executor.get("executorId")
                and ref.get("definitionVersion") == executor.get("definitionVersion")
            )
        elif executor.get("executorType") == "skill":
            identity_matches = (
                ref.get("skillId") == executor.get("executorId")
                and ref.get("definitionVersion") == executor.get("definitionVersion")
            )
        else:
            return False
        evidence = (row.get("preference") or {}).get("semanticFitEvidence") or {}
        return bool(
            identity_matches
            and row.get("sourceBindingRefs") == binding_refs
            and (row.get("preference") or {}).get("completionCoverage") == "completion_capable"
            and any(
                str(rule).endswith(":executor-capability")
                for rule in evidence.get("positiveRuleRefs") or []
            )
        )

    candidates = [
        row for row in decision.get("candidateEvaluations") or []
        if isinstance(row, Mapping) and matches(row)
    ]
    return candidates[0] if len(candidates) == 1 else None


def _artifact_has_completion_authority(
    artifact: Mapping[str, Any],
    work: Mapping[str, Any],
    assignment: Mapping[str, Any],
    decision: Mapping[str, Any],
) -> bool:
    """Require resolver-backed producer provenance for synthetic planned outputs."""
    if not str(artifact.get("artifactType") or "").endswith(".planned_output"):
        return True
    validator = artifact.get("validatorResult") or {}
    authority = validator.get("producerCompletionAuthority") if isinstance(validator, Mapping) else None
    candidate = _completion_authority_candidate(decision, assignment)
    snapshot_ref = decision.get("registrySnapshotRef") or {}
    return bool(
        str(validator.get("artifactContract") or "").strip()
        and isinstance(authority, Mapping)
        and candidate is not None
        and work.get("assignmentId") == assignment.get("assignmentId")
        and authority.get("candidateEvaluationId") == candidate.get("candidateEvaluationId")
        and authority.get("executorRef") == assignment.get("executorRef")
        and authority.get("sourceBindingRefs") == assignment.get("sourceBindingRefs")
        and authority.get("registrySnapshotContentHash") == snapshot_ref.get("contentHash")
    )


def close_decision_from_durable_facts(
    task: ResearchTaskV12,
    *,
    execution_decision_id: str,
    closed_at: datetime,
    recoverable_terminal_outcome: ExecutionDecisionOutcome | None = None,
) -> ResearchTaskV12:
    payload = task.model_dump(mode="json", by_alias=True)
    decision = next(
        (row for row in payload["executionDecisions"] if row["executionDecisionId"] == execution_decision_id),
        None,
    )
    if decision is None or decision["status"] != "dispatched":
        raise DecisionPolicyViolation("decision_not_closable")
    assignments = {row["assignmentId"]: row for row in decision["assignments"]}
    work_units = {
        row["assignmentId"]: row
        for row in payload["workUnits"]
        if row["executionDecisionId"] == execution_decision_id
    }
    decision_model = next(row for row in task.execution_decisions
                          if row.execution_decision_id == execution_decision_id)
    if _method_terminal_blocker(task, decision_model) is not None:
        settled = _settle_method_frontier(task, decision=decision_model, occurred_at=closed_at)
        if settled is task:
            raise DecisionPolicyViolation("required_assignment_not_terminal")
        return settled
    preflight_ids = {row.assignment_id for row in decision_model.assignment_preflight_outcomes
                     if row.failure_kind == "contract_violation"}
    status_by_assignment = {key: row["status"] for key, row in work_units.items()}
    status_by_assignment.update({key: "blocked" for key in preflight_ids})
    required = (
        decision["completionPolicy"]["requiredAssignmentIds"]
        if decision["purpose"] == "completion"
        else decision["progressPolicy"]["requiredAssignmentIds"]
    )
    method_degraded_accepted: set[str] = set(preflight_ids)
    if decision.get("strategy") == ExecutionStrategy.EXECUTION_METHOD.value:
        decision_model = next(
            row
            for row in task.execution_decisions
            if row.execution_decision_id == execution_decision_id
        )
        if _method_reentry_continuation_pending(task, decision_model):
            raise DecisionPolicyViolation("execution_method_continuation_pending")
        required = []
        stop_triggered = False
        for assignment in decision["assignments"]:
            work = work_units.get(assignment["assignmentId"])
            if work is None or work.get("status") != WorkUnitStatus.COMPLETED.value:
                continue
            control = assignment.get("executionMethodRoleControl") or {}
            for condition in control.get("stopConditions") or []:
                source = condition.get("source") if isinstance(condition, Mapping) else None
                if isinstance(source, Mapping) and _formal_state_condition_matches(task, source):
                    stop_triggered = True
                    break
            if stop_triggered:
                break
        frozen_transitions: dict[str, Mapping[str, Any]] = {}
        for frozen_assignment in decision["assignments"]:
            control = frozen_assignment.get("executionMethodRoleControl") or {}
            for transition in control.get("reentryTransitions") or []:
                if isinstance(transition, Mapping):
                    transition_id = str(transition.get("transitionId") or "")
                    if transition_id:
                        frozen_transitions[transition_id] = transition
        latest_iteration_by_role: dict[str, int] = {}
        for transition in frozen_transitions.values():
            repeat_roles = set(transition.get("repeatRoleIds") or [])
            latest_iteration = max(
                (
                    int(row.get("executionMethodIteration") or 1)
                    for row in decision["assignments"]
                    if row.get("executionMethodRoleId") in repeat_roles
                ),
                default=1,
            )
            for role_id in repeat_roles:
                latest_iteration_by_role[str(role_id)] = latest_iteration

        active_ids: list[str] = []
        for assignment in decision["assignments"]:
            assignment_id = assignment["assignmentId"]
            role_id = assignment.get("executionMethodRoleId")
            iteration = int(assignment.get("executionMethodIteration") or 1)
            control = assignment.get("executionMethodRoleControl") or {}
            requirement = control.get("requirement")
            work = work_units.get(assignment_id)
            if (
                work is None
                and role_id in latest_iteration_by_role
                and iteration < latest_iteration_by_role[role_id]
            ):
                continue
            release = control.get("releasePolicy")
            condition_active = (
                isinstance(release, Mapping)
                and release.get("kind") == "scientific_state_equals"
                and _formal_state_condition_matches(task, release)
            )
            unconditional_release = (
                isinstance(release, Mapping) and release.get("kind") == "always"
            )
            required_active = (
                requirement == "required"
                and (unconditional_release or condition_active or work is not None)
            )
            conditional_active = (
                requirement == "conditional"
                and condition_active
                and not stop_triggered
            )
            if required_active or work is not None or conditional_active or assignment_id in preflight_ids:
                active_ids.append(assignment_id)
            if (
                work is not None
                and work.get("status") in {"failed", "blocked"}
                and control.get("failurePolicy") == "continue_degraded"
            ):
                method_degraded_accepted.add(assignment_id)
        required = list(dict.fromkeys([*required, *active_ids]))
    terminal_required = list(required)
    if recoverable_terminal_outcome is not None:
        # An explicit producer-side recoverable close ends this malformed or
        # failed attempt before review. A reviewer that was never materialized
        # cannot be required to become terminal merely to preserve the already
        # happened producer result.
        terminal_required = [
            assignment_id
            for assignment_id in terminal_required
            if assignments.get(assignment_id, {}).get("role")
            != AssignmentRole.REVIEWER.value
        ]
    if any(
        status_by_assignment.get(assignment_id) not in {"completed", "failed", "blocked", "cancelled"}
        for assignment_id in terminal_required
    ):
        raise DecisionPolicyViolation("required_assignment_not_terminal")
    if recoverable_terminal_outcome is not None:
        if recoverable_terminal_outcome not in {
            ExecutionDecisionOutcome.BLOCKED,
            ExecutionDecisionOutcome.REJECTED,
        }:
            raise DecisionPolicyViolation("recoverable_terminal_outcome_invalid")
        outcome = recoverable_terminal_outcome.value
    elif decision["purpose"] == "progress":
        # Preserve usable partial progress: one blocked WorkUnit must not erase a
        # completed required result when progress policy accepts any completion.
        outcome = "progressed" if any(status_by_assignment[row] == "completed" for row in required) else "blocked"
    elif any(
        status_by_assignment[row] == "blocked" and row not in method_degraded_accepted
        for row in required
    ):
        # For completion Decisions, a blocked WorkUnit is recoverable execution
        # evidence, not proof that the research task is irrecoverable.
        outcome = ExecutionDecisionOutcome.BLOCKED.value
    else:
        # Leaf final report: if normal final Artifact is missing but upstream
        # usable evidence exists, attach a degraded Markdown Artifact before
        # evaluating completion requirements. Never bounce this to the user.
        payload = _attach_degraded_final_report_artifact(
            payload,
            decision=decision,
            closed_at=closed_at,
        )
        decision = next(
            row
            for row in payload["executionDecisions"]
            if row["executionDecisionId"] == execution_decision_id
        )
        assignments = {row["assignmentId"]: row for row in decision["assignments"]}
        work_units = {
            row["assignmentId"]: row
            for row in payload["workUnits"]
            if row["executionDecisionId"] == execution_decision_id
        }
        reviewer_ids = decision["completionPolicy"]["reviewerAssignmentIds"]
        reviewer_verdicts = {
            row["sourceAssignmentId"]: (row.get("verdict") or {}).get("decision")
            for row in payload["agentInteractionRecords"]
            if row["executionDecisionId"] == execution_decision_id and row.get("verdict")
        }
        if reviewer_ids and any(reviewer_verdicts.get(row) != "approved" for row in reviewer_ids):
            raise DecisionPolicyViolation("reviewer_verdict_not_approved")
        work_unit_by_id = {row["workUnitId"]: row for row in payload["workUnits"]}
        artifact_counts: dict[tuple[str, str], int] = {}
        for artifact in payload["artifacts"]:
            work = work_unit_by_id.get(artifact["workUnitId"])
            assignment = None if work is None else assignments.get(work["assignmentId"])
            if (
                work
                and assignment
                and artifact["artifactState"] == "usable"
                and _artifact_has_completion_authority(artifact, work, assignment, decision)
            ):
                artifact_counts[(work["assignmentId"], artifact["artifactType"])] = (
                    artifact_counts.get((work["assignmentId"], artifact["artifactType"]), 0) + 1
                )
        for requirement in decision["resultAssemblyPolicy"]["finalArtifactRequirements"]:
            count = sum(
                artifact_counts.get((assignment_id, requirement["artifactType"]), 0)
                for assignment_id in requirement["sourceAssignmentIds"]
            )
            if count < requirement["minimumCount"]:
                raise DecisionPolicyViolation("final_artifact_requirement_unsatisfied")
        success_count = sum(
            status_by_assignment[row] == "completed"
            or (
                row in method_degraded_accepted
                and status_by_assignment[row] in {"failed", "blocked"}
            )
            for row in required
        )
        if success_count == len(required):
            outcome = "succeeded"
        elif decision["completionPolicy"]["partialSuccessAllowed"] and success_count:
            outcome = "partial"
        else:
            outcome = "failed"
    decision.update(
        {"status": "closed", "outcome": outcome, "closedAt": closed_at.astimezone(timezone.utc).isoformat()}
    )
    design = next(
        (
            row for row in task.research_design_versions
            if row.version == decision["researchDesignVersion"]
            and any(
                instance.substage_instance_id == decision["substageInstanceId"]
                and instance.instance_revision == decision["substageInstanceRevision"]
                for instance in execution_instances_for_design(task, row)
            )
        ),
        None,
    )
    if design is None:
        raise DecisionPolicyViolation("decision_design_authority_missing")
    # A successful action contributes a new immutable scientific snapshot before
    # it changes the instance runtime.  Completion is determined from the
    # snapshot's existing acceptance-artifact facts, not from Decision success.
    state_task, state = record_scientific_state_from_durable_facts(
        ResearchTaskV12.model_validate(payload), design, occurred_at=closed_at
    )
    payload = state_task.model_dump(mode="json", by_alias=True)
    decision = next(
        row for row in payload["executionDecisions"]
        if row["executionDecisionId"] == execution_decision_id
    )
    runtime = next(
        row
        for row in payload["substageRuntimeStates"]
        if row["substageInstanceId"] == decision["substageInstanceId"]
        and row["instanceRevision"] == decision["substageInstanceRevision"]
    )
    runtime["activeExecutionDecisionId"] = None
    runtime["updatedAt"] = closed_at.astimezone(timezone.utc).isoformat()
    substage_state = next(
        (
            row for row in state["executionState"]["substageCompletion"]
            if row["substageInstanceId"] == decision["substageInstanceId"]
            and row["instanceRevision"] == decision["substageInstanceRevision"]
        ),
        None,
    )
    if substage_state is None:
        raise DecisionPolicyViolation("scientific_state_instance_missing")
    successful_outcome = outcome in {"succeeded", "partial", "progressed"}
    contributed_artifact = any(
        artifact["artifactState"] == ArtifactState.USABLE.value
        and any(
            work["workUnitId"] == artifact["workUnitId"]
            and work["executionDecisionId"] == execution_decision_id
            and work["status"] == WorkUnitStatus.COMPLETED.value
            for work in payload["workUnits"]
        )
        for artifact in payload["artifacts"]
    )
    superseded_decision_id = str(decision.get("supersedesExecutionDecisionId") or "").strip()
    superseded_decision = next(
        (
            row
            for row in payload["executionDecisions"]
            if row["executionDecisionId"] == superseded_decision_id
        ),
        None,
    )
    accepted_revision_progress = bool(
        decision.get("revisionReasonCode") == "research_revision"
        and superseded_decision is not None
        and superseded_decision["status"]
        in {
            ExecutionDecisionStatus.CLOSED.value,
            ExecutionDecisionStatus.SUPERSEDED.value,
            ExecutionDecisionStatus.CANCELLED.value,
        }
        and superseded_decision["taskId"] == decision["taskId"]
        and superseded_decision["researchDesignVersion"] == decision["researchDesignVersion"]
        and superseded_decision["substageInstanceId"] == decision["substageInstanceId"]
        and superseded_decision["substageInstanceRevision"]
        == decision["substageInstanceRevision"]
        and contributed_artifact
        and substage_state["needsAction"]
    )
    fallback_progress_without_completion_contract = bool(
        decision["purpose"] == "progress"
        and decision.get("fallbackInvoked") is True
    )
    if successful_outcome and substage_state["completionCriteriaSatisfied"]:
        runtime["status"] = "completed"
        runtime["statusReasonCode"] = None
    elif successful_outcome and contributed_artifact and decision["purpose"] == "completion":
        # A settled delivery is independent of the original scientific goal.
        # Keep the assessments and raw limitations; do not redispatch merely
        # because a professional criterion remains unresolved.
        runtime["status"] = "completed"
        runtime["statusReasonCode"] = (
            "acceptance_criteria_unresolved"
            if substage_state["needsAction"]
            else "acceptance_criteria_unmet"
        )
    elif fallback_progress_without_completion_contract:
        # A general fallback may preserve a bounded handoff, but it cannot gain
        # completion authority from repeating against the same unresolved
        # Definition.  Keep its usable artifact and stop automatic redispatch
        # until new, formally eligible work becomes available.
        runtime["status"] = "blocked_recoverable"
        runtime["statusReasonCode"] = (
            "progress_capability_exhausted_without_completion_contract"
        )
    elif (
        (successful_outcome or accepted_revision_progress)
        and contributed_artifact
        and substage_state["needsAction"]
    ):
        # READY is deliberately narrow: ScientificState must identify an
        # unresolved confirmed acceptance criterion, while this close must have
        # added usable evidence. Artifact type/count is input evidence only;
        # the normal Resolver still decides whether a legal next action exists.
        runtime["status"] = "ready"
        runtime["statusReasonCode"] = "acceptance_criteria_unresolved"
    elif decision["purpose"] == "progress":
        runtime["status"] = "blocked_recoverable"
        runtime["statusReasonCode"] = outcome
    else:
        runtime["status"] = "blocked_recoverable"
        runtime["statusReasonCode"] = outcome
    return ResearchTaskV12.model_validate(payload)


def _dependency_preflight_block(
    task: ResearchTaskV12,
    instance: SubstageInstance,
) -> tuple[str | None, Mapping[str, Any]]:
    """Evaluate dependency facts against the exact durable instance revision lineage."""
    runtime_by_ref = {
        (row.substage_instance_id, row.instance_revision): row
        for row in task.substage_runtime_states
    }
    work_units_by_id = {row.work_unit_id: row for row in task.work_units}
    artifacts_by_ref: dict[tuple[str, int], list[Any]] = {}
    for artifact in task.artifacts:
        work_unit = work_units_by_id.get(artifact.work_unit_id)
        if work_unit is None:
            continue
        ref = (work_unit.substage_instance_id, work_unit.substage_instance_revision)
        artifacts_by_ref.setdefault(ref, []).append(artifact)
    confirmations = sorted(task.confirmations, key=lambda row: row.created_at)

    design = _active_design_for_instance(
        task, instance, research_design_version=(
            task.active_research_design_ref.version
            if task.active_research_design_ref is not None
            else 0
        ),
    )
    if design is not None and _is_leaf_report_generation_instance(design, instance):
        for candidate in design.instances:
            if candidate.substage_instance_id == instance.substage_instance_id:
                continue
            upstream = runtime_by_ref.get(
                (candidate.substage_instance_id, candidate.instance_revision)
            )
            if upstream is None:
                return "final_assembly_upstream_missing", {
                    "substageInstanceId": candidate.substage_instance_id
                }
            if upstream.status not in {
                SubstageRuntimeStatus.COMPLETED,
                SubstageRuntimeStatus.SKIPPED,
            }:
                return "final_assembly_upstream_pending", {
                    "substageInstanceId": candidate.substage_instance_id
                }

    for dependency in instance.dependencies:
        upstream_ref = (
            dependency.upstream_instance_ref.substage_instance_id,
            dependency.upstream_instance_ref.instance_revision,
        )
        upstream = runtime_by_ref.get(upstream_ref)
        facts = {"dependencyId": dependency.dependency_id}
        if upstream is None:
            return "dependency_instance_missing", facts
        if upstream.status in {
            SubstageRuntimeStatus.FAILED,
            SubstageRuntimeStatus.CANCELLED,
        }:
            return "dependency_terminal_unsatisfied", facts
        if upstream.status is SubstageRuntimeStatus.SKIPPED:
            if not (
                dependency.condition_type is DependencyConditionType.INSTANCE_COMPLETED
                and upstream.status_reason_code
                == _OPTIONAL_ENHANCEMENT_UNAVAILABLE_REASON
            ):
                return "dependency_terminal_unsatisfied", facts
            continue
        if dependency.condition_type == DependencyConditionType.INSTANCE_COMPLETED:
            satisfied = upstream.status == SubstageRuntimeStatus.COMPLETED
        elif dependency.condition_type == DependencyConditionType.ARTIFACT_USABLE:
            upstream_artifacts = artifacts_by_ref.get(upstream_ref, [])
            satisfied = all(
                any(
                    artifact.artifact_type == artifact_type
                    and artifact.artifact_state == ArtifactState.USABLE
                    for artifact in upstream_artifacts
                )
                for artifact_type in dependency.required_artifact_types
            )
        else:
            latest = next(
                (
                    row
                    for row in reversed(confirmations)
                    if row.target_id == dependency.confirmation_target_ref
                ),
                None,
            )
            satisfied = latest is not None and latest.decision == "approved"
        if not satisfied:
            return "dependency_not_satisfied", facts
    return None, {}


def _snapshot_definition_enabled(
    snapshot: Mapping[str, Any] | None,
    instance: SubstageInstance,
) -> bool | None:
    if snapshot is None:
        return None
    definitions = snapshot.get("definitions")
    substages = definitions.get("substages") if isinstance(definitions, Mapping) else None
    if not isinstance(substages, list):
        return None
    definition = next(
        (
            row
            for row in substages
            if isinstance(row, Mapping)
            and row.get("definitionId") == instance.substage_definition_id
            and row.get("definitionVersion") == instance.substage_definition_version
        ),
        None,
    )
    if definition is None:
        return None
    return bool(definition.get("enabled"))


def promote_dependency_ready_substages(
    task: ResearchTaskV12,
    *,
    occurred_at: datetime,
    snapshot: Mapping[str, Any] | None = None,
) -> ResearchTaskV12:
    """Promote planned active-design instances only from persisted dependency facts."""
    if task.active_research_design_ref is None:
        return task
    design = next(
        (
            row
            for row in task.research_design_versions
            if row.research_design_id == task.active_research_design_ref.research_design_id
            and row.version == task.active_research_design_ref.version
        ),
        None,
    )
    if design is None or design.status != ResearchDesignStatus.CONFIRMED:
        return task

    payload = task.model_dump(mode="json", by_alias=True)
    runtime_payload_by_ref = {
        (row["substageInstanceId"], row["instanceRevision"]): row
        for row in payload["substageRuntimeStates"]
    }
    changed = False
    timestamp = occurred_at.astimezone(timezone.utc).isoformat()
    for instance in execution_instances_for_design(task, design):
        runtime = runtime_payload_by_ref.get(
            (instance.substage_instance_id, instance.instance_revision)
        )
        if runtime is None or runtime["status"] != SubstageRuntimeStatus.PLANNED.value:
            continue
        enabled = _snapshot_definition_enabled(snapshot, instance)
        if enabled is False or runtime.get("statusReasonCode") == "definition_disabled":
            if runtime.get("statusReasonCode") != "definition_disabled":
                runtime.update(
                    {
                        "statusReasonCode": "definition_disabled",
                        "updatedAt": timestamp,
                    }
                )
                changed = True
            continue
        missing_reason, _ = _dependency_preflight_block(task, instance)
        if missing_reason is not None:
            continue
        if prohibited_external_action(missing_reason):
            continue
        runtime.update(
            {
                "status": SubstageRuntimeStatus.READY.value,
                "statusReasonCode": missing_reason if research_gap_continues(missing_reason) else None,
                "updatedAt": timestamp,
            }
        )
        changed = True
    return ResearchTaskV12.model_validate(payload) if changed else task


def _reviewer_rejection_continuation(
    task: ResearchTaskV12,
    *,
    decision: Any,
    origin_runtime: Any,
    execution_mode: TaskExecutionMode,
) -> ExecutionContinuationPlan | None:
    """Apply the frozen reviewer-reject policy without changing reviewer truth."""
    completion_policy = getattr(decision, "completion_policy", None)
    if (
        completion_policy is None
        or execution_mode is not TaskExecutionMode.AUTOMATIC_RESEARCH
        or getattr(decision, "purpose", None) is not ExecutionPurpose.COMPLETION
        or decision.outcome
        not in {
            ExecutionDecisionOutcome.BLOCKED,
            ExecutionDecisionOutcome.NEEDS_INPUT,
            ExecutionDecisionOutcome.REJECTED,
        }
    ):
        return None

    reviewer_ids = set(completion_policy.reviewer_assignment_ids)
    review_rejections = [
        row
        for row in getattr(task, "agent_interaction_records", ())
        if row.execution_decision_id == decision.execution_decision_id
        and row.source_assignment_id in reviewer_ids
        and row.verdict is not None
        and row.verdict.decision.value != "approved"
    ]
    if not reviewer_ids or not review_rejections:
        return None

    requires_human_gate = (
        completion_policy.on_reviewer_reject is ReviewerRejectAction.HUMAN_GATE
        or any(
            row.verdict is not None
            and row.verdict.rationale_code == "review_human_decision_required"
            for row in review_rejections
        )
    )
    if requires_human_gate:
        return ExecutionContinuationPlan(
            "pause",
            decision.substage_instance_id,
            "reviewer_human_gate_required",
        )
    if (
        completion_policy.on_reviewer_reject
        is ReviewerRejectAction.NEW_REVISION_DECISION
        and origin_runtime is not None
        and origin_runtime.status is SubstageRuntimeStatus.READY
    ):
        return ExecutionContinuationPlan("start", decision.substage_instance_id, None)
    return None

def _accepted_revision_progress_allows_continuation(
    task: ResearchTaskV12,
    *,
    decision: ExecutionDecision,
    origin_runtime: Any,
) -> bool:
    """Return to next-task authority after accepted recovery progress.

    A historical research-revision Decision can remain durably BLOCKED when its
    frozen completion contract was malformed, while a recovered WorkUnit still
    completed and contributed a usable Artifact accepted by current ScientificState.
    That history must not be rewritten as success, but it also must not strand the
    project after the recovered knowledge has become current.
    """
    if not (
        decision.outcome is ExecutionDecisionOutcome.BLOCKED
        and getattr(decision, "revision_reason_code", None) == "research_revision"
        and getattr(decision, "supersedes_execution_decision_id", None) is not None
        and origin_runtime is not None
        and origin_runtime.status is SubstageRuntimeStatus.READY
    ):
        return False
    superseded_decision = next(
        (
            row
            for row in task.execution_decisions
            if row.execution_decision_id == decision.supersedes_execution_decision_id
        ),
        None,
    )
    if not (
        superseded_decision is not None
        and superseded_decision.status
        in {
            ExecutionDecisionStatus.CLOSED,
            ExecutionDecisionStatus.SUPERSEDED,
            ExecutionDecisionStatus.CANCELLED,
        }
        and superseded_decision.task_id == decision.task_id
        and superseded_decision.research_design_version == decision.research_design_version
        and superseded_decision.substage_instance_id == decision.substage_instance_id
        and superseded_decision.substage_instance_revision
        == decision.substage_instance_revision
    ):
        return False
    state = current_scientific_state(task)
    if state is None:
        return False
    substage_state = next(
        (
            row
            for row in state.execution_state.get("substageCompletion", [])
            if row.get("substageInstanceId") == decision.substage_instance_id
            and row.get("instanceRevision") == decision.substage_instance_revision
        ),
        None,
    )
    if not isinstance(substage_state, Mapping) or not substage_state.get("needsAction"):
        return False
    validated_refs = set(state.execution_state.get("validatedArtifactRefs", []))
    completed_work_ids = {
        row.work_unit_id
        for row in task.work_units
        if row.execution_decision_id == decision.execution_decision_id
        and row.status is WorkUnitStatus.COMPLETED
    }
    return any(
        artifact.artifact_id in validated_refs
        and artifact.work_unit_id in completed_work_ids
        and artifact.artifact_state is ArtifactState.USABLE
        for artifact in task.artifacts
    )


def reconcile_persisted_accepted_revision_progress(
    task: ResearchTaskV12,
    *,
    completed_execution_decision_id: str,
    occurred_at: datetime,
) -> ResearchTaskV12:
    """Re-derive accepted recovery progress from durable facts after an upgrade.

    Older binaries could persist a research-revision recovery Decision as CLOSED /
    BLOCKED while also accepting a usable recovered Artifact, yet leave the
    substage BLOCKED_RECOVERABLE and the latest ScientificState carrying the
    superseded attempt's terminal failure. Preserve that history, derive a new
    immutable ScientificState from the same facts, and reopen normal next-task
    authority only when the canonical accepted-revision predicate proves progress.
    """
    decision = next(
        (
            row
            for row in task.execution_decisions
            if row.execution_decision_id == completed_execution_decision_id
        ),
        None,
    )
    if not (
        decision is not None
        and decision.status is ExecutionDecisionStatus.CLOSED
        and decision.outcome is ExecutionDecisionOutcome.BLOCKED
        and decision.revision_reason_code == "research_revision"
        and decision.supersedes_execution_decision_id is not None
    ):
        return task
    runtime = next(
        (
            row
            for row in task.substage_runtime_states
            if row.substage_instance_id == decision.substage_instance_id
            and row.instance_revision == decision.substage_instance_revision
        ),
        None,
    )
    if runtime is None or runtime.status is not SubstageRuntimeStatus.BLOCKED_RECOVERABLE:
        return task
    superseded_decision = next(
        (
            row
            for row in task.execution_decisions
            if row.execution_decision_id == decision.supersedes_execution_decision_id
        ),
        None,
    )
    if not (
        superseded_decision is not None
        and superseded_decision.status
        in {
            ExecutionDecisionStatus.CLOSED,
            ExecutionDecisionStatus.SUPERSEDED,
            ExecutionDecisionStatus.CANCELLED,
        }
        and superseded_decision.task_id == decision.task_id
        and superseded_decision.research_design_version == decision.research_design_version
        and superseded_decision.substage_instance_id == decision.substage_instance_id
        and superseded_decision.substage_instance_revision
        == decision.substage_instance_revision
    ):
        return task

    completed_work_ids = {
        row.work_unit_id
        for row in task.work_units
        if row.execution_decision_id == decision.execution_decision_id
        and row.status is WorkUnitStatus.COMPLETED
    }
    if not completed_work_ids or not any(
        artifact.work_unit_id in completed_work_ids
        and artifact.artifact_state is ArtifactState.USABLE
        for artifact in task.artifacts
    ):
        return task

    payload = task.model_dump(mode="json", by_alias=True)
    runtime_payload = next(
        row
        for row in payload["substageRuntimeStates"]
        if row["substageInstanceId"] == decision.substage_instance_id
        and row["instanceRevision"] == decision.substage_instance_revision
    )
    runtime_payload.update(
        {
            "status": SubstageRuntimeStatus.READY.value,
            "statusReasonCode": "acceptance_criteria_unresolved",
            "activeExecutionDecisionId": None,
            "updatedAt": occurred_at.astimezone(timezone.utc).isoformat(),
        }
    )
    projected = ResearchTaskV12.model_validate(payload)
    design = next(
        (
            row
            for row in projected.research_design_versions
            if row.version == decision.research_design_version
            and any(
                instance.substage_instance_id == decision.substage_instance_id
                and instance.instance_revision == decision.substage_instance_revision
                for instance in execution_instances_for_design(projected, row)
            )
        ),
        None,
    )
    if design is None:
        return task
    projected, _state = record_scientific_state_from_durable_facts(
        projected,
        design,
        occurred_at=occurred_at,
    )
    projected_decision = next(
        row
        for row in projected.execution_decisions
        if row.execution_decision_id == decision.execution_decision_id
    )
    projected_runtime = next(
        row
        for row in projected.substage_runtime_states
        if row.substage_instance_id == decision.substage_instance_id
        and row.instance_revision == decision.substage_instance_revision
    )
    if not _accepted_revision_progress_allows_continuation(
        projected,
        decision=projected_decision,
        origin_runtime=projected_runtime,
    ):
        return task
    return projected


def reconcile_closed_successful_result_progress(
    task: ResearchTaskV12,
    *,
    completed_execution_decision_id: str,
    occurred_at: datetime,
    snapshot: Mapping[str, Any] | None = None,
) -> ResearchTaskV12:
    """Repair a stale recoverable runtime from already-accepted durable facts.

    A crash can occur after a Decision has closed successfully but before its
    runtime projection is persisted.  Recovery must not reopen that Decision or
    replay its provider call: it can only align a recoverable runtime when the
    closed Decision, completed WorkUnit, usable Artifact, and current
    ScientificState all independently prove completion.
    """
    decision = next(
        (
            row
            for row in task.execution_decisions
            if row.execution_decision_id == completed_execution_decision_id
        ),
        None,
    )
    if not (
        decision is not None
        and decision.status is ExecutionDecisionStatus.CLOSED
        and decision.outcome
        in {
            ExecutionDecisionOutcome.SUCCEEDED,
            ExecutionDecisionOutcome.PARTIAL,
            ExecutionDecisionOutcome.PROGRESSED,
        }
    ):
        return task
    runtime = next(
        (
            row
            for row in task.substage_runtime_states
            if row.substage_instance_id == decision.substage_instance_id
            and row.instance_revision == decision.substage_instance_revision
        ),
        None,
    )
    if runtime is None or runtime.status is not SubstageRuntimeStatus.BLOCKED_RECOVERABLE:
        return task
    completed_work_ids = {
        row.work_unit_id
        for row in task.work_units
        if row.execution_decision_id == decision.execution_decision_id
        and row.status is WorkUnitStatus.COMPLETED
    }
    if not completed_work_ids or not any(
        artifact.work_unit_id in completed_work_ids
        and artifact.artifact_state is ArtifactState.USABLE
        for artifact in task.artifacts
    ):
        return task
    design = next(
        (
            row
            for row in task.research_design_versions
            if row.version == decision.research_design_version
            and any(
                instance.substage_instance_id == decision.substage_instance_id
                and instance.instance_revision == decision.substage_instance_revision
                for instance in execution_instances_for_design(task, row)
            )
        ),
        None,
    )
    if design is None:
        return task
    projected, state = record_scientific_state_from_durable_facts(
        task,
        design,
        occurred_at=occurred_at,
    )
    substage_state = next(
        (
            row
            for row in state["executionState"]["substageCompletion"]
            if row["substageInstanceId"] == decision.substage_instance_id
            and row["instanceRevision"] == decision.substage_instance_revision
        ),
        None,
    )
    if substage_state is None or not substage_state["completionCriteriaSatisfied"]:
        return task
    payload = projected.model_dump(mode="json", by_alias=True)
    runtime_payload = next(
        row
        for row in payload["substageRuntimeStates"]
        if row["substageInstanceId"] == decision.substage_instance_id
        and row["instanceRevision"] == decision.substage_instance_revision
    )
    runtime_payload.update(
        {
            "status": SubstageRuntimeStatus.COMPLETED.value,
            "statusReasonCode": None,
            "activeExecutionDecisionId": None,
            "updatedAt": occurred_at.astimezone(timezone.utc).isoformat(),
        }
    )
    return reconcile_task_lifecycle_from_runtime(
        ResearchTaskV12.model_validate(payload),
        occurred_at=occurred_at,
        snapshot=snapshot,
    )


def plan_execution_continuation(
    task: ResearchTaskV12,
    *,
    completed_execution_decision_id: str,
    execution_mode: TaskExecutionMode,
) -> ExecutionContinuationPlan:
    """Choose the next safe action from durable Task facts only."""
    if task.active_research_design_ref is None or task.status in {
        TaskStatus.COMPLETED,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
    }:
        return ExecutionContinuationPlan("none", None, None)
    decision = next(
        (
            row
            for row in task.execution_decisions
            if row.execution_decision_id == completed_execution_decision_id
        ),
        None,
    )
    if decision is None or decision.status != ExecutionDecisionStatus.CLOSED:
        return ExecutionContinuationPlan("none", None, None)
    origin_runtime = next(
        (
            row for row in task.substage_runtime_states
            if row.substage_instance_id == decision.substage_instance_id
            and row.instance_revision == decision.substage_instance_revision
        ),
        None,
    )
    review_recovery = _reviewer_rejection_continuation(
        task,
        decision=decision,
        origin_runtime=origin_runtime,
        execution_mode=execution_mode,
    )
    if review_recovery is not None:
        return review_recovery

    continuation_from_success = decision.outcome in {
        ExecutionDecisionOutcome.SUCCEEDED,
        ExecutionDecisionOutcome.PARTIAL,
        ExecutionDecisionOutcome.PROGRESSED,
    }
    if (
        decision.outcome in {ExecutionDecisionOutcome.PARTIAL, ExecutionDecisionOutcome.PROGRESSED}
        and execution_mode is not TaskExecutionMode.AUTOMATIC_RESEARCH
        and (
            origin_runtime is None
            or origin_runtime.status
            not in {SubstageRuntimeStatus.READY, SubstageRuntimeStatus.COMPLETED}
        )
    ):
        continuation_from_success = False
    continuation_from_system_skip = _system_owned_nonblocking_skip_allows_continuation(
        task,
        decision=decision,
        origin_runtime=origin_runtime,
        execution_mode=execution_mode,
    )
    continuation_from_accepted_revision_progress = bool(
        execution_mode is TaskExecutionMode.AUTOMATIC_RESEARCH
        and _accepted_revision_progress_allows_continuation(
            task,
            decision=decision,
            origin_runtime=origin_runtime,
        )
    )
    # A local failed execution does not choose the next research work.
    # In automatic research, if another confirmed ResearchDesign task is READY,
    # return to the normal start_next() path so ResearchDesign + the latest
    # ScientificState remain the sole authority for the next ExecutionDecision.
    # The failed origin stays durable and is never rewritten as success.
    continuation_after_local_failure = bool(
        execution_mode is TaskExecutionMode.AUTOMATIC_RESEARCH
        and decision.outcome is ExecutionDecisionOutcome.FAILED
        and origin_runtime is not None
        and origin_runtime.status is SubstageRuntimeStatus.FAILED
    )
    if not (
        continuation_from_success
        or continuation_from_system_skip
        or continuation_from_accepted_revision_progress
        or continuation_after_local_failure
    ):
        return ExecutionContinuationPlan("none", None, None)
    design = next(
        (
            row
            for row in task.research_design_versions
            if row.research_design_id
            == task.active_research_design_ref.research_design_id
            and row.version == task.active_research_design_ref.version
        ),
        None,
    )
    if design is None or design.status != ResearchDesignStatus.CONFIRMED:
        raise DecisionPolicyViolation("active_design_not_confirmed")
    runtime_by_instance = {
        (row.substage_instance_id, row.instance_revision): row
        for row in task.substage_runtime_states
    }
    ready = [
        instance
        for instance in execution_instances_for_design(task, design)
        if (runtime := runtime_by_instance.get(
            (instance.substage_instance_id, instance.instance_revision)
        ))
        is not None
        and runtime.status == SubstageRuntimeStatus.READY
    ]
    if not ready:
        if execution_mode is TaskExecutionMode.AUTOMATIC_RESEARCH and any(
            runtime.status is SubstageRuntimeStatus.BLOCKED_RECOVERABLE
            and runtime.status_reason_code
            == "progress_capability_exhausted_without_completion_contract"
            and runtime.active_execution_decision_id is None
            and _dependency_preflight_block(task, instance)[0] is None
            for instance in execution_instances_for_design(task, design)
            if (runtime := runtime_by_instance.get(
                (instance.substage_instance_id, instance.instance_revision)
            )) is not None
        ):
            return ExecutionContinuationPlan("start", None, None)
        return ExecutionContinuationPlan("none", None, None)
    if execution_mode == TaskExecutionMode.AUTOMATIC_RESEARCH:
        return ExecutionContinuationPlan("start", None, None)
    completed_instance = next(
        (
            row
            for row in execution_instances_for_design(task, design)
            if row.substage_instance_id == decision.substage_instance_id
            and row.instance_revision == decision.substage_instance_revision
        ),
        None,
    )
    if completed_instance is None:
        raise DecisionPolicyViolation("completed_decision_instance_missing")
    if execution_mode == TaskExecutionMode.STAGE_CONFIRMATION:
        # The policy may decide whether to pause, but it must not choose which
        # ready research task is scientifically next. If every currently ready
        # option stays inside the current stage, forming the next Decision is safe.
        # Mixed/new-stage candidate sets pause conservatively without preselection.
        if all(row.stage_key == completed_instance.stage_key for row in ready):
            return ExecutionContinuationPlan("start", None, None)
        return ExecutionContinuationPlan(
            "pause",
            None,
            "stage_confirmation_required",
        )
    if execution_mode == TaskExecutionMode.MANUAL_STEP:
        return ExecutionContinuationPlan(
            "pause",
            None,
            "manual_step_confirmation_required",
        )
    raise DecisionPolicyViolation("execution_mode_invalid")


def reconcile_task_lifecycle_from_runtime(
    task: ResearchTaskV12,
    *,
    occurred_at: datetime,
    snapshot: Mapping[str, Any] | None = None,
    lifecycle_control_fact: Mapping[str, Any] | None = None,
) -> ResearchTaskV12:
    """Reconcile Task lifecycle from runtime and durable control facts."""

    if (
        task.final_result_authority is not None
        and task.final_result_authority.integrity_contract_version == "legacy_v0"
    ):
        raise DecisionPolicyViolation("legacy_final_result_authority_read_only")
    control_action: str | None = None
    if lifecycle_control_fact is not None:
        control_action = str(lifecycle_control_fact.get("controlAction") or "")
        generation = lifecycle_control_fact.get("controlGeneration")
        if control_action not in {"stop", "resume"} or type(generation) is not int or generation < 1:
            raise DecisionPolicyViolation("lifecycle_control_fact_invalid")
        if control_action == "resume":
            superseded = lifecycle_control_fact.get("supersedesControlGeneration")
            if type(superseded) is not int or generation != superseded + 1:
                raise DecisionPolicyViolation("lifecycle_control_generation_invalid")

    # Completed/failed aggregate truth is terminal. A control request cannot
    # rewrite it, and a resume only supersedes the latest stop generation.
    if task.status in {TaskStatus.COMPLETED, TaskStatus.FAILED}:
        return task
    if control_action == "stop":
        payload = task.model_dump(mode="json", by_alias=True)
        payload.update({
            "status": TaskStatus.CANCELLED.value,
            "finishedAt": occurred_at.astimezone(timezone.utc).isoformat(),
        })
        return ResearchTaskV12.model_validate(payload)
    if task.status == TaskStatus.CANCELLED and control_action != "resume":
        return task
    if task.active_research_design_ref is None:
        return task

    task = promote_dependency_ready_substages(
        task,
        occurred_at=occurred_at,
        snapshot=snapshot,
    )
    design = next(
        (
            row
            for row in task.research_design_versions
            if row.research_design_id == task.active_research_design_ref.research_design_id
            and row.version == task.active_research_design_ref.version
        ),
        None,
    )
    if design is None:
        raise DecisionPolicyViolation("active_design_not_found")

    runtime_by_instance = {
        (row.substage_instance_id, row.instance_revision): row
        for row in task.substage_runtime_states
    }
    runtimes = [
        runtime_by_instance.get((instance.substage_instance_id, instance.instance_revision))
        for instance in execution_instances_for_design(task, design)
    ]
    if not runtimes or any(row is None for row in runtimes):
        raise DecisionPolicyViolation("active_design_runtime_missing")

    statuses = {row.status for row in runtimes if row is not None}
    occurred_at = occurred_at.astimezone(timezone.utc)
    if statuses <= {SubstageRuntimeStatus.COMPLETED, SubstageRuntimeStatus.SKIPPED}:
        finalized = finalize_from_validated_facts(task, design, occurred_at=occurred_at)
        current_state = current_scientific_state(finalized)
        if current_state is None:
            raise DecisionPolicyViolation("current_scientific_state_missing")
        return settle_finalized_lifecycle(finalized, design, current_state, occurred_at=occurred_at)

    payload = task.model_dump(mode="json", by_alias=True)
    # Substage failure remains a durable local fact. It does not become
    # ResearchTask termination authority: a confirmed research lifecycle can
    # recover, degrade, or form a new ExecutionDecision from ResearchDesign +
    # the latest ScientificState. Only an aggregate terminal authority may close it.
    if SubstageRuntimeStatus.NEEDS_INPUT in statuses:
        payload.update({
            "status": "awaiting_confirmation",
            "finishedAt": None,
        })
    elif statuses & {
        SubstageRuntimeStatus.BLOCKED_RECOVERABLE,
        SubstageRuntimeStatus.INSUFFICIENT_EVIDENCE,
    }:
        payload.update({
            "status": "running",
            "finishedAt": None,
        })
    else:
        payload.update({
            "status": (
                "awaiting_confirmation"
                if _explicit_execution_confirmation_pending(task)
                else "running"
            ),
            "finishedAt": None,
        })
    return ResearchTaskV12.model_validate(payload)


def close_and_reconcile_decision_from_durable_facts(
    task: ResearchTaskV12,
    *,
    execution_decision_id: str,
    closed_at: datetime,
) -> ResearchTaskV12:
    closed = close_decision_from_durable_facts(
        task,
        execution_decision_id=execution_decision_id,
        closed_at=closed_at,
    )
    return reconcile_task_lifecycle_from_runtime(closed, occurred_at=closed_at)


def prepare_ready_assignments(
    task: ResearchTaskV12,
    *,
    execution_decision_id: str,
    snapshot: Mapping[str, Any],
) -> ExecutionPreparation | None:
    """Prepare dependency-ready inputs without creating a WorkUnit."""
    if task.status == TaskStatus.CANCELLED:
        return None
    decision = next(
        (
            row
            for row in task.execution_decisions
            if row.execution_decision_id == execution_decision_id
        ),
        None,
    )
    if decision is None or decision.status != ExecutionDecisionStatus.DISPATCHED:
        raise DecisionPolicyViolation("decision_not_releasable")
    if _preflight_blocker(decision) is not None:
        return None
    design = next(
        (
            row
            for row in task.research_design_versions
            if row.version == decision.research_design_version
        ),
        None,
    )
    instance = (
        None
        if design is None
        else next(
            (
                row
                for row in execution_instances_for_design(task, design)
                if row.substage_instance_id == decision.substage_instance_id
                and row.instance_revision == decision.substage_instance_revision
            ),
            None,
        )
    )
    if design is None or instance is None:
        raise DecisionPolicyViolation("decision_design_authority_missing")
    execution_method = (
        _frozen_execution_method(snapshot, instance)
        if decision.strategy is ExecutionStrategy.EXECUTION_METHOD
        else None
    )
    if decision.strategy is ExecutionStrategy.EXECUTION_METHOD:
        if execution_method is None:
            raise DecisionPolicyViolation("execution_method_snapshot_missing")
        if _execution_method_stop_triggered(
            task, decision=decision, method=execution_method
        ):
            return None
        decision = _expand_execution_method_reentry(task, decision, execution_method)

    existing = {
        row.assignment_id: row
        for row in task.work_units
        if row.execution_decision_id == execution_decision_id
    }
    artifacts_by_work_unit: dict[str, list[Any]] = {}
    for artifact in task.artifacts:
        artifacts_by_work_unit.setdefault(artifact.work_unit_id, []).append(artifact)
    artifact_ids = {row.artifact_id for row in task.artifacts}
    work_units: list[PreparedWorkUnit] = []
    assignment_by_id = {row.assignment_id: row for row in decision.assignments}

    preflight_ids = {row.assignment_id for row in decision.assignment_preflight_outcomes
                     if row.failure_kind == "contract_violation"}
    for assignment in decision.assignments:
        if assignment.assignment_id in existing or assignment.assignment_id in preflight_ids:
            continue
        if decision.strategy is ExecutionStrategy.EXECUTION_METHOD:
            if execution_method is None:
                raise DecisionPolicyViolation("execution_method_snapshot_missing")
            if not _method_assignment_is_current_iteration(
                decision, execution_method, assignment
            ):
                continue
            if not _method_assignment_release_allowed(task, assignment):
                continue
        dependency_pairs = [
            (assignment_by_id.get(ref), existing.get(ref))
            for ref in assignment.dependency_assignment_ids
        ]
        if any(
            dependency_assignment is None
            or not _method_dependency_allows_release(
                decision, dependency_assignment, dependency_work
            )
            for dependency_assignment, dependency_work in dependency_pairs
        ):
            continue
        dependency_units = [
            dependency_work
            for _, dependency_work in dependency_pairs
            if dependency_work is not None
        ]
        dependency_artifact_states = (
            {ArtifactState.NEEDS_REVIEW, ArtifactState.USABLE}
            if assignment.role == AssignmentRole.REVIEWER
            else {ArtifactState.USABLE}
        )
        dependencies_usable = all(
            all(
                any(
                    artifact.artifact_type == artifact_type
                    and artifact.artifact_state in dependency_artifact_states
                    for artifact in artifacts_by_work_unit.get(
                        dependency.work_unit_id, []
                    )
                )
                for artifact_type in dependency.expected_artifact_types
            )
            for dependency in dependency_units
            if dependency is not None
            and dependency.status is WorkUnitStatus.COMPLETED
        )
        if not dependencies_usable:
            continue

        readiness = [
            row
            for row in decision.runtime_readiness
            if getattr(row.capability_ref, "definition_version", None)
            == assignment.executor_ref.definition_version
            and (
                getattr(row.capability_ref, "agent_id", None)
                == assignment.executor_ref.executor_id
                or getattr(row.capability_ref, "skill_id", None)
                == assignment.executor_ref.executor_id
            )
        ]
        if (
            len(readiness) != 1
            or not readiness[0].ready
            or readiness[0].runtime_binding_id is None
        ):
            raise RuntimeReadinessInvalid(
                "released_assignment_runtime_invalid",
                assignment.assignment_id,
            )

        dependency_artifact_refs = [
            artifact.artifact_id
            for dependency in dependency_units
            if dependency is not None
            for artifact in artifacts_by_work_unit.get(
                dependency.work_unit_id, []
            )
            if artifact.artifact_state in dependency_artifact_states
            # Declared workspace files (*.declared_file) are presentation
            # supplements of the producer's render output; they must never
            # flow into downstream input bindings (compose_source_document
            # would see them as ambiguous extra sources).
            and not str(artifact.artifact_type).endswith(".declared_file")
        ]
        binding_artifact_refs = _binding_declared_artifact_refs(
            task,
            snapshot=snapshot,
            assignment=assignment,
            research_design_version=decision.research_design_version,
            same_decision_id=execution_decision_id,
        )
        input_artifact_refs = list(
            dict.fromkeys(
                [
                    *[
                        ref
                        for ref in assignment.input_refs
                        if ref in artifact_ids
                    ],
                    *dependency_artifact_refs,
                    *binding_artifact_refs,
                ]
            )
        )
        evaluated_risk = next(
            (
                row.preference.risk_class
                for row in decision.candidate_evaluations
                if (
                    getattr(row.candidate_ref, "agent_id", None)
                    == assignment.executor_ref.executor_id
                    or getattr(row.candidate_ref, "skill_id", None)
                    == assignment.executor_ref.executor_id
                    or isinstance(row.candidate_ref, MultiAgentRecipeRef)
                )
            ),
            RiskClass.HIGH,
        )
        work_unit_id = _stable_id(
            "workunit",
            decision.execution_decision_id,
            assignment.assignment_id,
        )
        work_units.append(
            PreparedWorkUnit(
                assignment=assignment,
                work_unit_id=work_unit_id,
                idempotency_key=(
                    f"workunit:{decision.execution_decision_id}:"
                    f"{assignment.assignment_id}"
                ),
                input_artifact_refs=tuple(input_artifact_refs),
                input_artifact_snapshots=tuple(
                    _artifact_snapshots_for_refs(task, input_artifact_refs)
                ),
                input_memory_refs=tuple(
                    ref
                    for ref in assignment.input_refs
                    if ref not in artifact_ids
                ),
                invocation_context={
                    key: value for key, value in decision.invocation_context.items()
                    if key in _declared_invocation_fields_for_assignment(snapshot, assignment)
                },
                dependency_work_unit_ids=tuple(
                    row.work_unit_id
                    for row in dependency_units
                    if row is not None
                ),
                risk_snapshot=RiskSnapshot(
                    boundary=design.risk_boundary,
                    evaluatedRiskClass=evaluated_risk,
                    triggeredHumanGates=[],
                ),
                resolved_execution_policy=_resolved_execution_policy_for_assignment(
                    snapshot, assignment
                ),
            )
        )

    if not work_units:
        return None
    return ExecutionPreparation(
        task=task,
        decision=decision,
        work_units=tuple(work_units),
        selected_candidate_identity="release",
    )


def freeze_ready_assignments(
    preparation: ExecutionPreparation,
    *,
    released_at: datetime,
) -> AssignmentRelease:
    """Freeze a validated downstream preparation and its pending dispatches."""
    task = preparation.task
    decision = preparation.decision
    released_at = released_at.astimezone(timezone.utc)
    work_units: list[WorkUnit] = []
    dispatches: list[DispatchRecord] = []
    preflight_ids = {row.assignment_id for row in preparation.decision.assignment_preflight_outcomes
                     if row.failure_kind == "contract_violation"}
    for prepared_work_unit in preparation.work_units:
        if (_preflight_blocker(preparation.decision) is not None
                or prepared_work_unit.assignment.assignment_id in preflight_ids):
            continue
        work_unit, dispatch = _freeze_prepared_work_unit(
            task=task,
            decision=decision,
            prepared_work_unit=prepared_work_unit,
            frozen_at=released_at,
        )
        work_units.append(work_unit)
        dispatches.append(dispatch)

    payload = task.model_dump(mode="json", by_alias=True)
    for index, frozen_decision in enumerate(payload["executionDecisions"]):
        if frozen_decision["executionDecisionId"] == decision.execution_decision_id:
            payload["executionDecisions"][index] = decision.model_dump(
                mode="json", by_alias=True
            )
            break
    payload["workUnits"].extend(
        row.model_dump(mode="json", by_alias=True) for row in work_units
    )
    runtime = next(
        row
        for row in payload["substageRuntimeStates"]
        if row["substageInstanceId"] == decision.substage_instance_id
        and row["instanceRevision"] == decision.substage_instance_revision
    )
    runtime["workUnitIds"].extend(row.work_unit_id for row in work_units)
    runtime["updatedAt"] = released_at.isoformat()
    return AssignmentRelease(
        task=_settle_method_frontier(
            ResearchTaskV12.model_validate(payload), decision=decision, occurred_at=released_at,
        ),
        work_units=tuple(work_units),
        dispatches=tuple(dispatches),
    )



def block_decision_for_input_contract_failure(
    task: ResearchTaskV12,
    *,
    execution_decision_id: str,
    reason_code: str,
    occurred_at: datetime,
) -> ResearchTaskV12:
    """Close a Decision when the next Assignment cannot accept its resolved input."""
    payload = task.model_dump(mode="json", by_alias=True)
    decision = next(
        (
            row
            for row in payload["executionDecisions"]
            if row["executionDecisionId"] == execution_decision_id
        ),
        None,
    )
    if decision is None or decision.get("status") != ExecutionDecisionStatus.DISPATCHED.value:
        raise DecisionPolicyViolation("decision_not_releasable")
    decision.update(
        {
            "status": ExecutionDecisionStatus.CLOSED.value,
            "outcome": ExecutionDecisionOutcome.BLOCKED.value,
            "closedAt": occurred_at.isoformat(),
        }
    )
    runtime = next(
        (
            row
            for row in payload["substageRuntimeStates"]
            if row["substageInstanceId"] == decision["substageInstanceId"]
            and row["instanceRevision"] == decision["substageInstanceRevision"]
        ),
        None,
    )
    if runtime is None:
        raise DecisionPolicyViolation("decision_runtime_missing")
    runtime.update(
        {
            "status": (
                SubstageRuntimeStatus.SKIPPED.value
                if research_gap_continues(reason_code)
                else SubstageRuntimeStatus.BLOCKED_RECOVERABLE.value
            ),
            "activeExecutionDecisionId": None,
            "statusReasonCode": reason_code,
            "updatedAt": occurred_at.isoformat(),
        }
    )
    return reconcile_task_lifecycle_from_runtime(
        ResearchTaskV12.model_validate(payload),
        occurred_at=occurred_at,
    )



def _settle_method_frontier(
    task: ResearchTaskV12, *, decision: ExecutionDecision, occurred_at: datetime,
) -> ResearchTaskV12:
    blocker = _method_terminal_blocker(task, decision)
    if blocker is not None:
        active = any(
            row.execution_decision_id == decision.execution_decision_id
            and row.status not in {WorkUnitStatus.COMPLETED, WorkUnitStatus.FAILED,
                                   WorkUnitStatus.BLOCKED, WorkUnitStatus.CANCELLED}
            for row in task.work_units
        )
        if active:
            # Preserve in-flight execution. The durable blocker prevents new
            # releases; the normal result/reconciliation path will settle it.
            return task
        return block_decision_for_input_contract_failure(
            task, execution_decision_id=decision.execution_decision_id,
            reason_code=blocker, occurred_at=occurred_at,
        )
    try:
        return close_decision_from_durable_facts(
            task, execution_decision_id=decision.execution_decision_id, closed_at=occurred_at,
        )
    except DecisionPolicyViolation as exc:
        if exc.code in {"required_assignment_not_terminal", "execution_method_continuation_pending"}:
            return task
        if exc.code == "final_artifact_requirement_unsatisfied":
            return block_decision_for_input_contract_failure(
                task, execution_decision_id=decision.execution_decision_id,
                reason_code=exc.code, occurred_at=occurred_at,
            )
        raise


def _frozen_target_skill_binding(
    snapshot: Mapping[str, Any], assignment: ExecutionAssignment
) -> Mapping[str, Any] | None:
    if assignment.executor_ref.executor_type != "skill":
        return None
    refs = {(ref.binding_id, ref.binding_version) for ref in assignment.source_binding_refs}
    rows = snapshot.get("bindings", {}).get("skills", [])
    matches = [
        row for row in rows
        if isinstance(row, Mapping)
        and (row.get("bindingId"), row.get("bindingVersion")) in refs
        and row.get("skillId") == assignment.executor_ref.executor_id
        and row.get("skillDefinitionVersion") == assignment.executor_ref.definition_version
        and row.get("enabled") is True
    ]
    if len(matches) != 1:
        raise DecisionPolicyViolation("skill_binding_authority_missing")
    return matches[0]


def _binding_artifact_source_identities(
    snapshot: Mapping[str, Any],
    assignment: ExecutionAssignment,
) -> tuple[tuple[str, int, str, int], ...]:
    # Resolve only artifact sources explicitly named by the target frozen Binding.
    target_binding = _frozen_target_skill_binding(snapshot, assignment)
    if target_binding is None:
        return ()

    input_sources = target_binding.get("inputSources")
    if input_sources is None:
        return ()
    if (
        not isinstance(input_sources, Mapping)
        or input_sources.get("contractVersion") != 2
        or not isinstance(input_sources.get("fields"), list)
    ):
        raise DecisionPolicyViolation("skill_binding_input_sources_invalid")

    requested_refs: list[tuple[str, int]] = []
    for field in input_sources["fields"]:
        source = field.get("source") if isinstance(field, Mapping) else None
        if not isinstance(source, Mapping) or source.get("kind") != "artifact":
            continue
        binding_id = source.get("bindingId")
        binding_version = source.get("bindingVersion")
        if (
            not isinstance(binding_id, str)
            or not binding_id
            or not isinstance(binding_version, int)
            or isinstance(binding_version, bool)
            or binding_version < 1
        ):
            raise DecisionPolicyViolation("skill_binding_artifact_source_invalid")
        identity = (binding_id, binding_version)
        if identity not in requested_refs:
            requested_refs.append(identity)

    if not requested_refs:
        return ()

    binding_rows = snapshot.get("bindings", {}).get("skills", [])
    if not isinstance(binding_rows, list):
        raise DecisionPolicyViolation("skill_binding_artifact_source_authority_missing")

    resolved: list[tuple[str, int, str, int]] = []
    for binding_id, binding_version in requested_refs:
        matches = [
            row
            for row in binding_rows
            if isinstance(row, Mapping)
            and row.get("bindingId") == binding_id
            and row.get("bindingVersion") == binding_version
            and row.get("enabled") is True
        ]
        if len(matches) != 1:
            raise DecisionPolicyViolation("skill_binding_artifact_source_authority_missing")
        row = matches[0]
        skill_id = row.get("skillId")
        definition_version = row.get("skillDefinitionVersion")
        if (
            not isinstance(skill_id, str)
            or not skill_id
            or not isinstance(definition_version, int)
            or isinstance(definition_version, bool)
            or definition_version < 1
        ):
            raise DecisionPolicyViolation("skill_binding_artifact_source_authority_missing")
        resolved.append(
            (binding_id, binding_version, skill_id, definition_version)
        )
    return tuple(resolved)


def _execution_method_artifact_lineage_scope(
    task: ResearchTaskV12,
    *,
    assignment: ExecutionAssignment,
    research_design_version: int,
) -> tuple[str, frozenset[str], int] | None:
    """Return the frozen method dependency lineage for one consumer assignment.

    Static assignment dependencies are the execution authority for method-local
    data flow.  Reentry adds new immutable assignments per iteration, so walking
    those dependencies provides a deterministic lineage without introducing a
    second Artifact/Binding authority.
    """
    if assignment.execution_method_role_id is None:
        return None
    matches: list[tuple[ExecutionDecision, ExecutionAssignment]] = []
    for decision in task.execution_decisions:
        if (
            decision.research_design_version != research_design_version
            or decision.strategy is not ExecutionStrategy.EXECUTION_METHOD
        ):
            continue
        authoritative = next(
            (
                row
                for row in decision.assignments
                if row.assignment_id == assignment.assignment_id
            ),
            None,
        )
        if authoritative is not None:
            matches.append((decision, authoritative))
    if not matches:
        return None
    if len(matches) != 1:
        raise DecisionPolicyViolation("execution_method_assignment_authority_ambiguous")

    decision, authoritative = matches[0]
    assignment_by_id = {row.assignment_id: row for row in decision.assignments}
    ancestors: set[str] = set()
    pending = list(authoritative.dependency_assignment_ids)
    while pending:
        dependency_id = pending.pop()
        if dependency_id in ancestors:
            continue
        dependency = assignment_by_id.get(dependency_id)
        if dependency is None:
            raise DecisionPolicyViolation("execution_method_dependency_authority_missing")
        ancestors.add(dependency_id)
        pending.extend(dependency.dependency_assignment_ids)
    return (
        decision.execution_decision_id,
        frozenset(ancestors),
        _method_assignment_iteration(authoritative),
    )



# A decision whose artifacts may serve as Binding sources must have closed on
# the platform's success family. `progressed` (substage completed and the next
# one started) and `partial` are success outcomes everywhere else — e.g.
# repeatable reentry authority — and a closed success-family decision's USABLE
# artifacts are durable facts.
_BINDING_SOURCE_SUCCESSFUL_OUTCOMES = frozenset({
    ExecutionDecisionOutcome.SUCCEEDED,
    ExecutionDecisionOutcome.PARTIAL,
    ExecutionDecisionOutcome.PROGRESSED,
})


def _prefer_same_decision_source_round(
    candidates: list[tuple[str, WorkUnit, ExecutionAssignment]],
    *,
    same_decision_id: str | None,
    source_decision_ids: set[str],
) -> list[tuple[str, WorkUnit, ExecutionAssignment]]:
    """Keep Binding-source artifacts from the latest producing round.

    Completed producers inside the decision being prepared are already durable
    handoff material, and prior successful decisions remain eligible sources.
    When this decision itself re-produced the Binding's source (repeatable
    re-entry round), the fresh in-decision artifact supersedes the prior
    round's same-source artifact: keeping both feeds compose_source_document a
    duplicate artifactType and fails closed as ambiguous.
    """
    filtered = [
        row for row in candidates
        if row[1].execution_decision_id == same_decision_id
        or row[1].execution_decision_id in source_decision_ids
    ]
    if same_decision_id is not None and any(
        row[1].execution_decision_id == same_decision_id for row in filtered
    ):
        filtered = [
            row for row in filtered
            if row[1].execution_decision_id == same_decision_id
        ]
    return filtered


def _current_successful_source_decision_ids(
    task: ResearchTaskV12, *, research_design_version: int,
    source_ref: tuple[str, int], skill_id: str, definition_version: int,
) -> set[str]:
    active = task.active_research_design_ref
    design = next((row for row in task.research_design_versions
                   if active is not None
                   and row.research_design_id == active.research_design_id
                   and row.version == active.version == research_design_version
                   and row.status is ResearchDesignStatus.CONFIRMED), None)
    if design is None:
        return set()
    current_instances = {(row.substage_instance_id, row.instance_revision)
                         for row in design.instances}
    latest: dict[tuple[str, int], ExecutionDecision] = {}
    for decision in task.execution_decisions:
        instance_ref = (decision.substage_instance_id, decision.substage_instance_revision)
        if (decision.research_design_version != research_design_version
                or instance_ref not in current_instances
                or decision.status is not ExecutionDecisionStatus.CLOSED
                or decision.outcome not in _BINDING_SOURCE_SUCCESSFUL_OUTCOMES):
            continue
        if not any(row.executor_ref.executor_type == "skill"
                   and row.executor_ref.executor_id == skill_id
                   and row.executor_ref.definition_version == definition_version
                   and source_ref in {(ref.binding_id, ref.binding_version)
                                      for ref in row.source_binding_refs}
                   for row in decision.assignments):
            continue
        previous = latest.get(instance_ref)
        if previous is None or (decision.decision_sequence, decision.decided_at) > (
                previous.decision_sequence, previous.decided_at):
            latest[instance_ref] = decision
    return {row.execution_decision_id for row in latest.values()}


def _binding_declared_artifact_refs(
    task: ResearchTaskV12,
    *,
    snapshot: Mapping[str, Any],
    assignment: ExecutionAssignment,
    research_design_version: int,
    same_decision_id: str | None = None,
) -> list[str]:
    # Binding decides data inputs; persisted producer lineage decides which Artifact.
    # This does not expand the Workflow/WorkUnit execution dependency graph.
    sources = _binding_artifact_source_identities(snapshot, assignment)
    if not sources:
        return []

    source_by_ref = {
        (binding_id, binding_version): (skill_id, definition_version)
        for binding_id, binding_version, skill_id, definition_version in sources
    }
    work_units = {
        row.work_unit_id: row
        for row in task.work_units
        if row.source_mode is WorkUnitSourceMode.LIVE
        and row.status is WorkUnitStatus.COMPLETED
        and row.research_design_version == research_design_version
    }
    decisions = {
        row.execution_decision_id: row
        for row in task.execution_decisions
        if row.research_design_version == research_design_version
    }
    method_scope = _execution_method_artifact_lineage_scope(
        task,
        assignment=assignment,
        research_design_version=research_design_version,
    )

    matches_by_source: dict[
        tuple[str, int],
        list[tuple[str, WorkUnit, ExecutionAssignment]],
    ] = {source_ref: [] for source_ref in source_by_ref}
    for artifact in task.artifacts:
        if artifact.artifact_state is not ArtifactState.USABLE:
            continue
        # Declared workspace files are presentation supplements, never bindable
        # data sources (same rule as the dependency release filter).
        if str(artifact.artifact_type).endswith(".declared_file"):
            continue
        producer = work_units.get(artifact.work_unit_id)
        if producer is None:
            continue
        decision = decisions.get(producer.execution_decision_id)
        if decision is None or decision.registry_snapshot_ref != producer.registry_snapshot_ref:
            continue
        producer_assignment = next(
            (
                row
                for row in decision.assignments
                if row.assignment_id == producer.assignment_id
            ),
            None,
        )
        if (
            producer_assignment is None
            or producer_assignment.executor_ref != producer.executor_ref
            or producer.executor_ref.executor_type != "skill"
        ):
            continue

        producer_binding_refs = {
            (ref.binding_id, ref.binding_version)
            for ref in producer_assignment.source_binding_refs
        }
        for source_ref, (skill_id, definition_version) in source_by_ref.items():
            if (
                source_ref in producer_binding_refs
                and producer.executor_ref.executor_id == skill_id
                and producer.executor_ref.definition_version == definition_version
            ):
                matches_by_source[source_ref].append(
                    (artifact.artifact_id, producer, producer_assignment)
                )

    selected: list[str] = []
    for source_ref, (skill_id, definition_version) in source_by_ref.items():
        candidates = matches_by_source[source_ref]
        method_local_source = False
        if method_scope is not None:
            method_decision_id, ancestor_ids, consumer_iteration = method_scope
            method_decision = decisions.get(method_decision_id)
            if method_decision is None:
                raise DecisionPolicyViolation("execution_method_assignment_authority_missing")
            method_source_assignments = [
                row
                for row in method_decision.assignments
                if row.executor_ref.executor_type == "skill"
                and row.executor_ref.executor_id == skill_id
                and row.executor_ref.definition_version == definition_version
                and source_ref
                in {
                    (ref.binding_id, ref.binding_version)
                    for ref in row.source_binding_refs
                }
            ]
            if method_source_assignments:
                method_local_source = True
                lineage_source_assignments = [
                    row
                    for row in method_source_assignments
                    if row.assignment_id in ancestor_ids
                    and _method_assignment_iteration(row) <= consumer_iteration
                ]
                if not lineage_source_assignments:
                    # A method-local producer is frozen for this Binding, but it
                    # is outside the consumer's dependency lineage.  Never fall
                    # back to another iteration or sibling branch.
                    continue
                lineage_iteration = max(
                    _method_assignment_iteration(row)
                    for row in lineage_source_assignments
                )
                lineage_assignment_ids = {
                    row.assignment_id
                    for row in lineage_source_assignments
                    if _method_assignment_iteration(row) == lineage_iteration
                }
                candidates = [
                    row
                    for row in candidates
                    if row[1].execution_decision_id == method_decision_id
                    and row[2].assignment_id in lineage_assignment_ids
                ]
        if not method_local_source:
            source_decision_ids = _current_successful_source_decision_ids(
                task, research_design_version=research_design_version,
                source_ref=source_ref, skill_id=skill_id,
                definition_version=definition_version,
            )
            # Completed producers inside the decision being prepared are already
            # durable handoff material: the loop above owns their validity
            # (LIVE + COMPLETED WorkUnit, USABLE artifact, matching Binding
            # lineage), and that decision cannot be CLOSED+SUCCEEDED while it
            # is still freezing WorkUnits.
            candidates = _prefer_same_decision_source_round(
                candidates,
                same_decision_id=same_decision_id,
                source_decision_ids=source_decision_ids,
            )
        selected.extend(row[0] for row in candidates)

    # Keep every Artifact from the selected producer assignment/iteration.  The
    # existing Binding v2 cardinality contract still owns one-vs-many handling
    # and must continue to fail closed on genuine same-lineage ambiguity.
    return list(dict.fromkeys(selected))


class ResearchExecutionDecisionService(RecoveryMaterializationPort):
    def __init__(
        self,
        *,
        repository: ExecutionSelectionRepositoryPort,
        resolver: Any,
        facts_source: Callable[[ResearchTaskV12, SubstageInstance, Mapping[str, Any]], tuple[ResolutionFacts, Mapping[str, RuntimeReadinessSnapshot]]],
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._resolver = resolver
        self._facts_source = facts_source
        self._now = now or (lambda: datetime.now(timezone.utc))

    def _validate_execution_preparation(
        self,
        *, snapshot: Mapping[str, Any], preparation: ExecutionPreparation,
        owner_id: str, workspace_id: str,
    ) -> ExecutionPreparation:
        if preparation.decision.strategy is not ExecutionStrategy.EXECUTION_METHOD:
            return self._validate_execution_preparation_once(
                snapshot=snapshot, preparation=preparation,
                owner_id=owner_id, workspace_id=workspace_id,
            )
        decision = preparation.decision
        pending = list(preparation.work_units)
        checked: list[PreparedWorkUnit] = []
        seen: set[str] = set()
        while pending:
            for work in pending:
                seen.add(work.assignment.assignment_id)
                one = replace(preparation, decision=decision, work_units=(work,))
                try:
                    validated = self._validate_execution_preparation_once(
                        snapshot=snapshot, preparation=one,
                        owner_id=owner_id, workspace_id=workspace_id,
                    )
                    checked.extend(validated.work_units)
                except ExecutionPreflightBlocked as exc:
                    # Only input gate failures have capability-scoped facts.
                    # Authorization, budget, readiness and revision guards remain
                    # outside this local material-unavailability mechanism.
                    facts = exc.facts or {}
                    if "failureKind" not in facts:
                        raise
                    outcome = AssignmentPreflightOutcome(
                        assignmentId=work.assignment.assignment_id,
                        failureKind=facts["failureKind"], reasonCode=exc.code,
                        fieldPaths=facts.get("fieldPaths", []),
                        inputArtifactSnapshots=list(work.input_artifact_snapshots),
                        inputScientificStateVersion=facts.get("inputScientificStateVersion"),
                        checkedAt=self._now(),
                    )
                    existing_outcome = any(
                        row.assignment_id == outcome.assignment_id
                        for row in decision.assignment_preflight_outcomes
                    )
                    if existing_outcome and outcome.failure_kind == "contract_violation":
                        raise
                    if not existing_outcome:
                        decision = decision.model_copy(update={
                            "assignment_preflight_outcomes": [
                                *decision.assignment_preflight_outcomes, outcome,
                            ],
                        })
                    checked.append(work)
                    if outcome.failure_kind == "contract_violation":
                        return replace(preparation, decision=decision, work_units=tuple(checked))
            staged = _task_with_prepared_decision(preparation.task, decision)
            frontier = prepare_ready_assignments(
                staged, execution_decision_id=decision.execution_decision_id, snapshot=snapshot,
            )
            if frontier is None:
                break
            decision = frontier.decision
            pending = [row for row in frontier.work_units if row.assignment.assignment_id not in seen]
        return replace(preparation, decision=decision, work_units=tuple(checked))


    def _validate_execution_preparation_once(
        self,
        *, snapshot: Mapping[str, Any], preparation: ExecutionPreparation,
        owner_id: str, workspace_id: str,
    ) -> ExecutionPreparation:
        validated_work_units: list[PreparedWorkUnit] = []
        for work_unit in preparation.work_units:
            try:
                candidate = execution_input_candidate_from_assignment(
                    task=preparation.task, decision=preparation.decision,
                    assignment=work_unit.assignment, instruction=work_unit.assignment.objective,
                    input_artifact_refs=work_unit.input_artifact_refs,
                    input_artifact_snapshots=work_unit.input_artifact_snapshots,
                    input_memory_refs=work_unit.input_memory_refs,
                )
            except ValueError as exc:
                raise DecisionPolicyViolation(str(exc)) from exc
            scientific_state_version: int | None = None
            try:
                reader = (
                    self._repository.read_reviewer_execution_input_context
                    if work_unit.assignment.role is AssignmentRole.REVIEWER
                    else self._repository.read_execution_input_context
                )
                context = reader(
                    task_id=preparation.task.task_id, owner_id=owner_id, workspace_id=workspace_id,
                    research_design_version=preparation.decision.research_design_version,
                    input_artifact_refs=work_unit.input_artifact_refs,
                    input_artifact_snapshots=work_unit.input_artifact_snapshots,
                    invocation_context=work_unit.invocation_context,
                )
                skill_binding = _frozen_target_skill_binding(snapshot, work_unit.assignment)
                if isinstance(skill_binding, Mapping):
                    sources = skill_binding.get("inputSources")
                    fields = sources.get("fields") if isinstance(sources, Mapping) else None
                    uses_state = isinstance(fields, list) and any(
                        isinstance(field, Mapping) and isinstance(field.get("source"), Mapping)
                        and field["source"].get("kind") == "scientificState" for field in fields
                    )
                    if uses_state:
                        scientific_state_version = context.get("scientificStateVersion")
                        if scientific_state_version is not None and (
                            not isinstance(scientific_state_version, int)
                            or isinstance(scientific_state_version, bool)
                            or scientific_state_version < 1
                        ):
                            raise ExecutionPreflightBlocked("input_composition_scientific_state_version_invalid")
                resolved = resolve_and_validate_execution_input(
                    snapshot=snapshot, candidate=candidate, input_context=context,
                    skill_binding=skill_binding,
                )
                validated_work_units.append(
                    replace(work_unit, resolved_parameters=dict(resolved),
                            input_scientific_state_version=scientific_state_version)
                    if candidate.capability_kind == "skill" else work_unit
                )
            except InputContractGateFailure as exc:
                facts = {
                    **exc.facts,
                    "inputContractFailureCode": exc.code,
                }
                if scientific_state_version is not None:
                    facts["inputScientificStateVersion"] = scientific_state_version
                raise ExecutionPreflightBlocked(
                    (
                        "input_condition_insufficient"
                        if exc.failure_kind == "material_unavailable"
                        else exc.code
                    ),
                    facts=facts,
                ) from exc
        return replace(preparation, work_units=tuple(validated_work_units))

    @staticmethod
    def _assert_same_prepared_inputs(
        expected: ExecutionPreparation,
        actual: ExecutionPreparation,
    ) -> None:
        def signature(preparation: ExecutionPreparation) -> tuple[Any, ...]:
            return (
                preparation.selected_candidate_identity,
                preparation.decision.execution_decision_id,
                preparation.decision.strategy.value,
                tuple(
                    (
                        row.assignment.assignment_id,
                        row.assignment.executor_ref.model_dump(
                            mode="json", by_alias=True
                        ),
                        row.assignment.objective,
                        row.input_artifact_refs,
                        row.input_artifact_snapshots,
                        row.input_memory_refs,
                        dict(row.invocation_context),
                        row.dependency_work_unit_ids,
                    )
                    for row in preparation.work_units
                ),
            )

        if signature(expected) != signature(actual):
            raise DecisionPolicyViolation(
                "execution_input_preparation_changed_before_freeze"
            )

    def _validate_prepared_selection_inputs(
        self,
        prepared: PreparedSelection,
        task: ResearchTaskV12,
    ) -> dict[str, ValidatedSelectionPreparation]:
        # Fairness may select any member of the final tie set inside the atomic
        # commit. Prepare and validate every possible winner before any WorkUnit
        # can be frozen.
        validated: dict[str, ValidatedSelectionPreparation] = {}
        for candidate_id in prepared.final_tie_candidate_ids:
            preparation = prepare_selection_execution(
                prepared, task, candidate_id
            )
            initial = preparation
            preparation = self._validate_execution_preparation(
                snapshot=prepared.snapshot_envelope,
                preparation=preparation,
                owner_id=prepared.owner_id,
                workspace_id=prepared.workspace_id,
            )
            validated[candidate_id] = ValidatedSelectionPreparation(initial, preparation)
        return validated

    def _freeze_validated_selection(
        self,
        *,
        prepared: PreparedSelection,
        locked_task: ResearchTaskV12,
        winner: str,
        validated: Mapping[str, ValidatedSelectionPreparation],
    ) -> ExecutionMaterialization:
        expected = validated.get(winner)
        if expected is None:
            raise DecisionPolicyViolation(
                "fairness_selected_candidate_not_prevalidated"
            )
        actual = prepare_selection_execution(prepared, locked_task, winner)
        self._assert_same_prepared_inputs(expected.initial, actual)
        if expected.validated.decision.model_copy(update={
            "assignment_preflight_outcomes": expected.initial.decision.assignment_preflight_outcomes,
        }) != expected.initial.decision:
            raise DecisionPolicyViolation("execution_preflight_changed_frozen_decision")
        frozen_preparation = replace(
            actual, decision=expected.validated.decision, work_units=expected.validated.work_units,
        )
        return freeze_selection_execution(prepared, locked_task, frozen_preparation)

    def replay_before_instance_resolution(
        self,
        *,
        task_id: str,
        owner_id: str,
        workspace_id: str,
        selection_request_id: str,
        expected_task_revision: int,
        recovery_identity: str | None = None,
    ) -> SelectionCommitResult | None:
        """Replay a prior request before deriving an instance from mutable Task state."""
        evidence = self._repository.lookup_selection_replay_evidence(
            task_id=task_id,
            owner_id=owner_id,
            workspace_id=workspace_id,
            selection_request_id=selection_request_id,
        )
        if evidence is None:
            return None
        selection_command_hash = _selection_command_hash(
            task_id=task_id,
            instance_id=evidence.instance_id,
            owner_id=owner_id,
            workspace_id=workspace_id,
            selection_request_id=selection_request_id,
            expected_task_revision=expected_task_revision,
            recovery_identity=recovery_identity,
        )
        if evidence.selection_command_hash != selection_command_hash:
            raise SelectionRequestConflictError("selection_request_command_conflict")
        if isinstance(evidence.replay, BlockedSelectionReplay):
            if needs_user_input(evidence.replay.reason_code):
                raise ExecutionPreflightBlocked(
                    evidence.replay.reason_code,
                    facts=evidence.replay.facts,
                )
            return None
        return evidence.replay

    @staticmethod
    def _current_design_and_scientific_state(
        task: ResearchTaskV12,
    ) -> tuple[Any, Any]:
        active_ref = task.active_research_design_ref
        if active_ref is None:
            raise ExecutionPreflightBlocked("active_design_missing")
        design = next(
            (
                row
                for row in task.research_design_versions
                if row.research_design_id == active_ref.research_design_id
                and row.version == active_ref.version
            ),
            None,
        )
        if design is None or design.status != ResearchDesignStatus.CONFIRMED:
            raise ExecutionPreflightBlocked("active_design_not_confirmed")
        state = current_scientific_state(task)
        if state is None:
            raise DecisionPolicyViolation("current_scientific_state_missing")
        return design, state

    def _legal_next_task_candidates(
        self,
        *,
        task: ResearchTaskV12,
        design: Any,
        snapshot: Mapping[str, Any],
        owner_id: str,
        workspace_id: str,
        selection_request_id: str,
        execution_ref: RegistrySnapshotRef,
        invocation_context: Mapping[str, Any] | None,
        invocation_context_definition_id: str | None,
    ) -> tuple[SubstageInstance, ...]:
        runtime_by_ref = {
            (row.substage_instance_id, row.instance_revision): row
            for row in task.substage_runtime_states
        }
        ready_instances = [
            instance
            for instance in execution_instances_for_design(task, design)
            if (runtime := runtime_by_ref.get(
                (instance.substage_instance_id, instance.instance_revision)
            )) is not None
            and runtime.status == SubstageRuntimeStatus.READY
        ]
        if not ready_instances:
            raise ExecutionPreflightBlocked("ready_instance_missing")
        binding_loader = getattr(
            self._repository, "load_catalog_execution_binding", None
        )
        binding_payload = (
            binding_loader(
                task_id=task.task_id,
                owner_id=owner_id,
                workspace_id=workspace_id,
            )
            if callable(binding_loader)
            else None
        )
        rejections: dict[str, str] = {}
        for instance in ready_instances:
            dependency_reason, _ = _dependency_preflight_block(task, instance)
            if dependency_reason is not None:
                rejections[instance.substage_instance_id] = dependency_reason
                continue
            enabled = _snapshot_definition_enabled(snapshot, instance)
            if enabled is False:
                rejections[instance.substage_instance_id] = "definition_disabled"
                continue
            try:
                facts, readiness = self._facts_source(task, instance, snapshot)
                facts = constrain_resolution_facts_to_catalog_binding(
                    facts, snapshot=snapshot, instance=instance,
                    binding_payload=binding_payload,
                )
                resolution = self._resolver.resolve(
                    designRef=task.active_research_design_ref,
                    instanceRef=InstanceRef(
                        substageInstanceId=instance.substage_instance_id,
                        instanceRevision=instance.instance_revision,
                    ),
                    registrySnapshotRef=execution_ref, design=design, facts=facts,
                )
                context = (
                    invocation_context
                    if invocation_context_definition_id is None
                    or instance.substage_definition_id == invocation_context_definition_id
                    else None
                )
                prepared = prepare_selection(
                    task, instance_id=instance.substage_instance_id,
                    resolution=resolution, snapshot_envelope=snapshot,
                    facts=facts, runtime_readiness=readiness,
                    selection_request_id=selection_request_id,
                    selection_command_hash=_selection_command_hash(
                        task_id=task.task_id, instance_id=instance.substage_instance_id,
                        owner_id=owner_id, workspace_id=workspace_id,
                        selection_request_id=selection_request_id,
                        expected_task_revision=task.revision, invocation_context=context,
                        recovery_identity=None,
                    ),
                    expected_task_revision=task.revision, owner_id=owner_id,
                    workspace_id=workspace_id, decided_at=self._now(),
                    workflow_lineage=_load_canonical_workflow_lineage(
                        self._repository, task_id=task.task_id,
                        owner_id=owner_id, workspace_id=workspace_id,
                    ),
                    invocation_context=context,
                )
                validated = self._validate_prepared_selection_inputs(prepared, task)
                blockers = [
                    _preflight_blocker(row.validated.decision)
                    for row in validated.values()
                ]
                if any(blockers):
                    rejections[instance.substage_instance_id] = next(
                        row.reason_code for row in blockers if row is not None
                    )
                    continue
                return (instance,)
            except (ExecutionPreflightBlocked, ResolutionRejectedError) as exc:
                rejections[instance.substage_instance_id] = exc.code
        raise ExecutionPreflightBlocked(
            "next_task_legal_candidate_missing",
            facts={"candidateRejections": rejections},
        )

    def _start_unique_blocked_next_task(
        self,
        *,
        blocked: ExecutionPreflightBlocked,
        task_id: str,
        owner_id: str,
        workspace_id: str,
        selection_request_id: str,
        expected_task_revision: int,
        scientific_state_version: int,
        invocation_context: Mapping[str, Any] | None,
        invocation_context_definition_id: str | None,
        design: ResearchDesignVersion,
    ) -> SelectionCommitResult | None:
        """Preserve canonical blocker semantics when only one READY task exists."""
        if blocked.code != "next_task_legal_candidate_missing":
            return None
        rejections = blocked.facts.get("candidateRejections")
        if not isinstance(rejections, Mapping) or len(rejections) != 1:
            return None
        selected_instance_id = next(iter(rejections))
        # Mirror the per-instance invocation-context resolution used by the
        # legal-candidate path (see start_next): when the caller designates an
        # invocation_context_definition_id, the context is only valid for the
        # instance whose definition matches. The single READY candidate here is
        # the would-be selected instance; if its definition matches, preserve
        # the caller context so canonical preflight can adjudicate it (e.g. an
        # invocation-context-guarded substage honestly lands in needs_input
        # rather than being silently stripped of its caller-designated fields).
        selected_definition_id = next(
            (
                instance.substage_definition_id
                for instance in design.instances
                if instance.substage_instance_id == selected_instance_id
            ),
            None,
        )
        resolved_invocation_context = (
            invocation_context
            if invocation_context_definition_id is None
            or selected_definition_id == invocation_context_definition_id
            else None
        )
        return self.start(
            task_id=task_id,
            instance_id=selected_instance_id,
            owner_id=owner_id,
            workspace_id=workspace_id,
            selection_request_id=selection_request_id,
            expected_task_revision=expected_task_revision,
            invocation_context=resolved_invocation_context,
            next_task_scientific_state_version=scientific_state_version,
            next_task_rationale=(
                "Only one structurally READY ResearchDesign task is available under "
                f"ScientificState v{scientific_state_version}; canonical execution "
                "preflight determines whether it can run now."
            ),
        )

    def _execution_snapshot(
        self,
        task: ResearchTaskV12,
        *,
        required_definition_refs: tuple[tuple[str, int], ...] = (),
    ) -> tuple[Mapping[str, Any], RegistrySnapshotRef]:
        """Legacy execution remains pinned; new execution derives from frozen research."""
        snapshot = self._repository.load_registry_snapshot(task.registry_snapshot_ref.content_hash)
        if snapshot.get("scope") != {"kind": "research_planning"}:
            return snapshot, task.registry_snapshot_ref
        active = task.active_research_design_ref
        if active is None or not any(
            row.research_design_id == active.research_design_id
            and row.version == active.version
            and row.status == ResearchDesignStatus.CONFIRMED
            for row in task.research_design_versions
        ):
            raise ExecutionPreflightBlocked("active_design_not_confirmed")
        snapshot = self._repository.prepare_execution_registry_snapshot(
            planning_snapshot_ref=task.registry_snapshot_ref.model_dump(mode="json", by_alias=True),
            required_definition_refs=required_definition_refs,
        )
        ref = RegistrySnapshotRef.model_validate(registry_envelope_ref(snapshot))
        if not execution_snapshot_matches_planning(
            task.registry_snapshot_ref.model_dump(mode="json", by_alias=True),
            ref.model_dump(mode="json", by_alias=True),
            snapshot,
        ):
            raise DecisionPolicyViolation("resolver_snapshot_mismatch")
        return snapshot, ref

    def start_next(
        self,
        *,
        task_id: str,
        owner_id: str,
        workspace_id: str,
        selection_request_id: str,
        expected_task_revision: int,
        invocation_context: Mapping[str, Any] | None = None,
        invocation_context_definition_id: str | None = None,
    ) -> SelectionCommitResult:
        """Form the next ExecutionDecision from Design + current ScientificState."""
        replay = self.replay_before_instance_resolution(
            task_id=task_id,
            owner_id=owner_id,
            workspace_id=workspace_id,
            selection_request_id=selection_request_id,
            expected_task_revision=expected_task_revision,
        )
        if replay is not None:
            return replay
        task = self._repository.load(
            task_id=task_id, owner_id=owner_id, workspace_id=workspace_id
        )
        if task.revision != expected_task_revision:
            raise ExecutionPreflightBlocked("task_revision_conflict")
        design, scientific_state = self._current_design_and_scientific_state(task)
        runtime_by_ref = {
            (row.substage_instance_id, row.instance_revision): row
            for row in task.substage_runtime_states
        }
        # Next-task selection must evaluate the whole currently READY choice set
        # against one execution snapshot.  Freezing an empty capability set here
        # makes every method-role candidate appear unavailable before the
        # selector can apply the confirmed ResearchDesign.
        ready_definition_refs = tuple(sorted({
            (
                instance.substage_definition_id,
                instance.substage_definition_version,
            )
            for instance in execution_instances_for_design(task, design)
            if (
                runtime_by_ref.get(
                    (instance.substage_instance_id, instance.instance_revision)
                ) is not None
                and runtime_by_ref[
                    (instance.substage_instance_id, instance.instance_revision)
                ].status
                == SubstageRuntimeStatus.READY
            )
        }))
        snapshot, execution_ref = self._execution_snapshot(
            task,
            required_definition_refs=ready_definition_refs,
        )
        try:
            candidates = self._legal_next_task_candidates(
                task=task,
                design=design,
                snapshot=snapshot,
                owner_id=owner_id,
                workspace_id=workspace_id,
                selection_request_id=selection_request_id,
                execution_ref=execution_ref,
                invocation_context=invocation_context,
                invocation_context_definition_id=invocation_context_definition_id,
            )
        except ExecutionPreflightBlocked as exc:
            if exc.code in {"ready_instance_missing", "next_task_legal_candidate_missing"}:
                recovered = self._resume_completed_progress(
                    task=task, design=design, owner_id=owner_id, workspace_id=workspace_id,
                    selection_request_id=selection_request_id,
                )
                if recovered is not None:
                    return recovered
            blocked_result = self._start_unique_blocked_next_task(
                blocked=exc,
                task_id=task_id,
                owner_id=owner_id,
                workspace_id=workspace_id,
                selection_request_id=selection_request_id,
                expected_task_revision=expected_task_revision,
                scientific_state_version=scientific_state.scientific_state_version,
                invocation_context=(
                    invocation_context
                    if invocation_context_definition_id is None
                    else None
                ),
                invocation_context_definition_id=invocation_context_definition_id,
                design=design,
            )
            if blocked_result is None:
                raise
            return blocked_result
        # The confirmed instance order is the dispatch authority. Candidate
        # eligibility has already consumed current dependency/input facts.
        candidate_ids = {row.substage_instance_id for row in candidates}
        selected = next(
            instance
            for instance in execution_instances_for_design(task, design)
            if instance.substage_instance_id in candidate_ids
        )
        selected_instance_id = selected.substage_instance_id
        rationale = (
            "First executable task in the confirmed ResearchDesign order under "
            f"ScientificState v{scientific_state.scientific_state_version}."
        )
        selected_invocation_context = (
            invocation_context
            if invocation_context_definition_id is None
            or selected.substage_definition_id == invocation_context_definition_id
            else None
        )
        return self.start(
            task_id=task_id,
            instance_id=selected_instance_id,
            owner_id=owner_id,
            workspace_id=workspace_id,
            selection_request_id=selection_request_id,
            expected_task_revision=expected_task_revision,
            invocation_context=selected_invocation_context,
            next_task_scientific_state_version=(
                scientific_state.scientific_state_version
            ),
            next_task_rationale=rationale,
        )

    def _resume_completed_progress(
        self, *, task: ResearchTaskV12, design: Any, owner_id: str,
        workspace_id: str, selection_request_id: str,
    ) -> SelectionCommitResult | None:
        """Reuse formal recovery when a bounded handoff now has completion capability."""
        for instance in execution_instances_for_design(task, design):
            runtime = next((row for row in task.substage_runtime_states
                            if row.substage_instance_id == instance.substage_instance_id
                            and row.instance_revision == instance.instance_revision), None)
            if (
                runtime is None
                or runtime.status is not SubstageRuntimeStatus.BLOCKED_RECOVERABLE
                or runtime.status_reason_code
                != "progress_capability_exhausted_without_completion_contract"
                or runtime.active_execution_decision_id is not None
                or _dependency_preflight_block(task, instance)[0] is not None
            ):
                continue
            decisions = [row for row in task.execution_decisions
                         if row.substage_instance_id == instance.substage_instance_id
                         and row.substage_instance_revision == instance.instance_revision
                         and row.research_design_version == design.version]
            original = max(decisions, key=lambda row: row.decision_sequence) if decisions else None
            if (
                original is None or original.status is not ExecutionDecisionStatus.CLOSED
                or original.purpose is not ExecutionPurpose.PROGRESS
            ):
                continue
            works = [row for row in task.work_units
                     if row.execution_decision_id == original.execution_decision_id]
            if not works or any(row.status is not WorkUnitStatus.COMPLETED for row in works):
                continue
            selection_task = _recovery_preflight_task(task, original)
            snapshot, ref = self._execution_snapshot(
                selection_task,
                required_definition_refs=((instance.substage_definition_id,
                                           instance.substage_definition_version),),
            )
            facts, readiness = self._facts_source(selection_task, instance, snapshot)
            resolution = self._resolver.resolve(
                designRef=selection_task.active_research_design_ref,
                instanceRef=InstanceRef(substageInstanceId=instance.substage_instance_id,
                                       instanceRevision=instance.instance_revision),
                registrySnapshotRef=ref, design=design, facts=facts,
            )
            if not any(row.eligible and row.preference.executable_now
                       and row.preference.completion_coverage is CompletionCoverage.COMPLETION_CAPABLE
                       for row in resolution.candidate_evaluations):
                continue
            return self.materialize_recovery(
                RecoveryMaterializationRequest(
                    recovery_identity=f"completion-capability:{original.execution_decision_id}",
                    directive_kind="research_revision", task_id=task.task_id,
                    original_execution_decision_id=original.execution_decision_id,
                    original_work_unit_id=works[0].work_unit_id,
                    selection_request_id=selection_request_id,
                    expected_task_revision=task.revision, owner_id=owner_id,
                    workspace_id=workspace_id,
                ), resolution=resolution, facts=facts, runtime_readiness=readiness,
            )
        return None

    def _skip_prohibited_action(
        self,
        *,
        task_id: str,
        owner_id: str,
        workspace_id: str,
        expected_task_revision: int,
        instance_id: str,
        reason_code: str,
    ) -> None:
        """Record a hard-boundary skip. Other research steps stay runnable."""
        recorder = getattr(self._repository, "record_execution_continuation_blocked", None)
        if not callable(recorder):
            return
        recorder(
            task_id=task_id,
            owner_id=owner_id,
            workspace_id=workspace_id,
            expected_task_revision=expected_task_revision,
            next_instance_id=instance_id,
            reason_code=reason_code,
        )

    def start(
        self,
        *,
        task_id: str,
        instance_id: str,
        owner_id: str,
        workspace_id: str,
        selection_request_id: str,
        expected_task_revision: int,
        recovery_identity: str | None = None,
        invocation_context: Mapping[str, Any] | None = None,
        next_task_scientific_state_version: int | None = None,
        next_task_rationale: str | None = None,
    ) -> SelectionCommitResult:
        selection_command_hash = _selection_command_hash(
            task_id=task_id,
            instance_id=instance_id,
            owner_id=owner_id,
            workspace_id=workspace_id,
            selection_request_id=selection_request_id,
            expected_task_revision=expected_task_revision,
            recovery_identity=recovery_identity,
            invocation_context=invocation_context,
        )
        replay = self._repository.lookup_selection_replay(
            task_id=task_id,
            owner_id=owner_id,
            workspace_id=workspace_id,
            selection_request_id=selection_request_id,
            selection_command_hash=selection_command_hash,
        )
        if isinstance(replay, BlockedSelectionReplay):
            if needs_user_input(replay.reason_code):
                raise ExecutionPreflightBlocked(replay.reason_code, facts=replay.facts)
            replay = None
        if replay is not None:
            return replay
        task = self._repository.load(task_id=task_id, owner_id=owner_id, workspace_id=workspace_id)
        stop_reader = getattr(self._repository, "execution_stop_active", None)
        if callable(stop_reader) and stop_reader(task_id=task_id, owner_id=owner_id, workspace_id=workspace_id):
            raise ExecutionPreflightBlocked("task_stopped")
        provisional = evaluate_instance_preflight(task, instance_id=instance_id)
        snapshot, execution_ref = self._execution_snapshot(
            task,
            required_definition_refs=((
                provisional.instance.substage_definition_id,
                provisional.instance.substage_definition_version,
            ),),
        )
        facts, readiness = self._facts_source(task, provisional.instance, snapshot)
        binding_loader = getattr(self._repository, "load_catalog_execution_binding", None)
        binding_payload = (
            binding_loader(task_id=task_id, owner_id=owner_id, workspace_id=workspace_id)
            if callable(binding_loader)
            else None
        )
        facts = constrain_resolution_facts_to_catalog_binding(
            facts,
            snapshot=snapshot,
            instance=provisional.instance,
            binding_payload=binding_payload,
        )
        preflight = evaluate_instance_preflight(
            task,
            instance_id=instance_id,
            facts=facts,
            required_input_refs=_required_input_refs(snapshot, provisional.instance),
        )
        if not preflight.executable:
            reason_code = preflight.missing_reason_code or "preflight_blocked"
            if prohibited_external_action(reason_code):
                self._skip_prohibited_action(
                    task_id=task_id,
                    owner_id=owner_id,
                    workspace_id=workspace_id,
                    expected_task_revision=expected_task_revision,
                    instance_id=instance_id,
                    reason_code=reason_code,
                )
                raise ExecutionPreflightBlocked(reason_code, facts=preflight.missing_facts)
            if not needs_user_input(reason_code):
                raise ExecutionPreflightBlocked(reason_code, facts=preflight.missing_facts)
            blocked = self._repository.record_needs_input(
                task_id=task_id,
                owner_id=owner_id,
                workspace_id=workspace_id,
                expected_task_revision=expected_task_revision,
                substage_instance_id=instance_id,
                reason_code=reason_code,
                selection_request_id=selection_request_id,
                selection_command_hash=selection_command_hash,
                blocker_facts=preflight.missing_facts,
            )
            if isinstance(blocked, SelectionCommitResult):
                return blocked
            if isinstance(blocked, BlockedSelectionReplay):
                raise ExecutionPreflightBlocked(blocked.reason_code, facts=blocked.facts)
            raise ExecutionPreflightBlocked(
                reason_code,
                facts={"taskRevision": blocked.revision, **dict(preflight.missing_facts)},
            )
        design = next(
            row
            for row in task.research_design_versions
            if task.active_research_design_ref is not None
            and row.research_design_id == task.active_research_design_ref.research_design_id
            and row.version == task.active_research_design_ref.version
        )
        try:
            workflow_lineage = _load_canonical_workflow_lineage(
                self._repository,
                task_id=task_id,
                owner_id=owner_id,
                workspace_id=workspace_id,
            )
            resolver_kwargs: dict[str, Any] = {}
            if not any(
                row.substage_instance_id == preflight.instance.substage_instance_id
                and row.instance_revision == preflight.instance.instance_revision
                for row in design.instances
            ):
                resolver_kwargs["executionInstance"] = preflight.instance
            resolution = self._resolver.resolve(
                designRef=task.active_research_design_ref,
                instanceRef=InstanceRef(
                    substageInstanceId=preflight.instance.substage_instance_id,
                    instanceRevision=preflight.instance.instance_revision,
                ),
                registrySnapshotRef=execution_ref,
                design=design,
                facts=facts,
                **resolver_kwargs,
            )
            prepared = prepare_selection(
                task,
                instance_id=instance_id,
                resolution=resolution,
                snapshot_envelope=snapshot,
                facts=facts,
                runtime_readiness=readiness,
                selection_request_id=selection_request_id,
                selection_command_hash=selection_command_hash,
                expected_task_revision=expected_task_revision,
                owner_id=owner_id,
                workspace_id=workspace_id,
                decided_at=self._now(),
                recovery_identity=recovery_identity,
                workflow_lineage=workflow_lineage,
                invocation_context=invocation_context,
                next_task_scientific_state_version=next_task_scientific_state_version,
                next_task_rationale=next_task_rationale,
            )
            # Validate inside the same guarded block: an unsatisfiable frozen
            # invocation contract (see _freeze_declared_invocation_context)
            # must persist as needs_input, not escape as a raw job failure.
            validated = self._validate_prepared_selection_inputs(prepared, task)
        except ExecutionPreflightBlocked as exc:
            if prohibited_external_action(exc.code):
                self._skip_prohibited_action(
                    task_id=task_id,
                    owner_id=owner_id,
                    workspace_id=workspace_id,
                    expected_task_revision=expected_task_revision,
                    instance_id=instance_id,
                    reason_code=exc.code,
                )
                raise
            if exc.code not in DURABLE_START_BLOCKER_CODES:
                raise
            if not needs_user_input(exc.code):
                raise
            blocked = self._repository.record_needs_input(
                task_id=task_id,
                owner_id=owner_id,
                workspace_id=workspace_id,
                expected_task_revision=expected_task_revision,
                substage_instance_id=instance_id,
                reason_code=exc.code,
                selection_request_id=selection_request_id,
                selection_command_hash=selection_command_hash,
                blocker_facts=exc.facts,
            )
            if isinstance(blocked, SelectionCommitResult):
                return blocked
            if isinstance(blocked, BlockedSelectionReplay):
                raise ExecutionPreflightBlocked(blocked.reason_code, facts=blocked.facts) from exc
            raise ExecutionPreflightBlocked(
                exc.code,
                facts={"taskRevision": blocked.revision, **dict(exc.facts)},
            ) from exc
        return self._repository.commit_prepared_selection(
            prepared,
            lambda locked_task, winner: self._freeze_validated_selection(
                prepared=prepared,
                locked_task=locked_task,
                winner=winner,
                validated=validated,
            ),
        )

    def materialize_controller_progression(
        self,
        *,
        task_id: str,
        original_execution_decision_id: str,
        original_work_unit_id: str,
        owner_id: str,
        workspace_id: str,
        selection_request_id: str,
        expected_task_revision: int,
        recovery_identity: str,
        directive_kind: str = "research_revision",
    ) -> SelectionCommitResult:
        """Resolve and atomically materialize a canonical user-supplement revision."""
        task = self._repository.load(
            task_id=task_id,
            owner_id=owner_id,
            workspace_id=workspace_id,
        )
        if task.revision != expected_task_revision:
            raise ExecutionPreflightBlocked("task_revision_conflict")
        original = next(
            (
                row
                for row in task.execution_decisions
                if row.execution_decision_id == original_execution_decision_id
            ),
            None,
        )
        if original is None:
            raise DecisionPolicyViolation("recovery_original_decision_missing")
        if original.status not in {
            ExecutionDecisionStatus.CLOSED,
            ExecutionDecisionStatus.SUPERSEDED,
            ExecutionDecisionStatus.CANCELLED,
        }:
            raise DecisionPolicyViolation("recovery_original_decision_not_terminal")
        selection_task = _recovery_preflight_task(
            task, original, recovery_work_unit_id=original_work_unit_id,
            frozen_snapshot=self._repository.load_registry_snapshot(original.registry_snapshot_ref.content_hash),
        )
        active_design = next(
            (
                row
                for row in selection_task.research_design_versions
                if selection_task.active_research_design_ref is not None
                and row.research_design_id
                == selection_task.active_research_design_ref.research_design_id
                and row.version == selection_task.active_research_design_ref.version
            ),
            None,
        )
        recovery_instance_ref = InstanceRef(
            substageInstanceId=original.substage_instance_id,
            instanceRevision=original.substage_instance_revision,
        )
        instance = (
            execution_instance_for_ref(
                selection_task,
                active_design,
                recovery_instance_ref,
            )
            if active_design is not None
            else None
        )
        if instance is None:
            raise DecisionPolicyViolation("recovery_instance_not_current")
        snapshot, execution_ref = self._execution_snapshot(
            selection_task,
            required_definition_refs=((instance.substage_definition_id, instance.substage_definition_version),),
        )
        facts, readiness = self._facts_source(selection_task, instance, snapshot)
        resolution = self._resolver.resolve(
            designRef=selection_task.active_research_design_ref,
            instanceRef=InstanceRef(
                substageInstanceId=instance.substage_instance_id,
                instanceRevision=instance.instance_revision,
            ),
            registrySnapshotRef=execution_ref,
            design=next(
                row
                for row in selection_task.research_design_versions
                if selection_task.active_research_design_ref is not None
                and row.research_design_id
                == selection_task.active_research_design_ref.research_design_id
                and row.version == selection_task.active_research_design_ref.version
            ),
            facts=facts,
        )
        return self.materialize_recovery(
            RecoveryMaterializationRequest(
                recovery_identity=recovery_identity,
                directive_kind=directive_kind,
                task_id=task_id,
                original_execution_decision_id=original_execution_decision_id,
                original_work_unit_id=original_work_unit_id,
                selection_request_id=selection_request_id,
                expected_task_revision=expected_task_revision,
                owner_id=owner_id,
                workspace_id=workspace_id,
            ),
            resolution=resolution,
            facts=facts,
            runtime_readiness=readiness,
        )

    def materialize_recovery(
        self,
        request: RecoveryMaterializationRequest,
        *,
        resolution: ResolutionResult,
        facts: ResolutionFacts,
        runtime_readiness: Mapping[str, RuntimeReadinessSnapshot],
    ) -> SelectionCommitResult:
        if request.directive_kind not in {"research_revision", "unknown_confirmed_never_started"}:
            raise DecisionPolicyViolation("recovery_directive_not_materializable")
        task = self._repository.load(
            task_id=request.task_id,
            owner_id=request.owner_id,
            workspace_id=request.workspace_id,
        )
        original = next(
            (
                row
                for row in task.execution_decisions
                if row.execution_decision_id == request.original_execution_decision_id
            ),
            None,
        )
        if original is None or not any(
            row.work_unit_id == request.original_work_unit_id
            and row.execution_decision_id == original.execution_decision_id
            for row in task.work_units
        ):
            raise DecisionPolicyViolation("recovery_lineage_invalid")
        if original.status not in {
            ExecutionDecisionStatus.CLOSED,
            ExecutionDecisionStatus.SUPERSEDED,
            ExecutionDecisionStatus.CANCELLED,
        }:
            raise DecisionPolicyViolation("recovery_original_decision_not_terminal")
        if request.selection_request_id == original.selection_request_id:
            raise DecisionPolicyViolation("recovery_requires_new_selection_request")
        selection_task = _recovery_preflight_task(
            task, original, recovery_work_unit_id=request.original_work_unit_id,
            frozen_snapshot=self._repository.load_registry_snapshot(original.registry_snapshot_ref.content_hash),
        )
        recovery_instance_ref = InstanceRef(
            substageInstanceId=original.substage_instance_id,
            instanceRevision=original.substage_instance_revision,
        )
        snapshot = self._repository.load_registry_snapshot(resolution.registry_snapshot_ref.content_hash)
        workflow_lineage = _load_canonical_workflow_lineage(
            self._repository,
            task_id=request.task_id,
            owner_id=request.owner_id,
            workspace_id=request.workspace_id,
        )
        binding_loader = getattr(self._repository, "load_catalog_execution_binding", None)
        binding_payload = (
            binding_loader(
                task_id=request.task_id,
                owner_id=request.owner_id,
                workspace_id=request.workspace_id,
            )
            if callable(binding_loader)
            else None
        )
        if binding_payload is not None:
            active_design = next(
                row
                for row in selection_task.research_design_versions
                if selection_task.active_research_design_ref is not None
                and row.research_design_id == selection_task.active_research_design_ref.research_design_id
                and row.version == selection_task.active_research_design_ref.version
            )
            instance = execution_instance_for_ref(
                selection_task,
                active_design,
                recovery_instance_ref,
            )
            if instance is None:
                raise DecisionPolicyViolation("recovery_instance_not_current")
            facts = constrain_resolution_facts_to_catalog_binding(
                facts,
                snapshot=snapshot,
                instance=instance,
                binding_payload=binding_payload,
            )
            resolution = self._resolver.resolve(
                designRef=selection_task.active_research_design_ref,
                instanceRef=InstanceRef(
                    substageInstanceId=instance.substage_instance_id,
                    instanceRevision=instance.instance_revision,
                ),
                registrySnapshotRef=resolution.registry_snapshot_ref,
                design=active_design,
                facts=facts,
            )
        prepared = prepare_selection(
            selection_task,
            instance_id=original.substage_instance_id,
            resolution=resolution,
            snapshot_envelope=snapshot,
            facts=facts,
            runtime_readiness=runtime_readiness,
            selection_request_id=request.selection_request_id,
            selection_command_hash=_selection_command_hash(
                task_id=request.task_id,
                instance_id=original.substage_instance_id,
                owner_id=request.owner_id,
                workspace_id=request.workspace_id,
                selection_request_id=request.selection_request_id,
                expected_task_revision=request.expected_task_revision,
                recovery_identity=request.recovery_identity,
            ),
            expected_task_revision=request.expected_task_revision,
            owner_id=request.owner_id,
            workspace_id=request.workspace_id,
            decided_at=self._now(),
            recovery_identity=request.recovery_identity,
            workflow_lineage=workflow_lineage,
            recovery_instance_ref=recovery_instance_ref,
        )
        validated = self._validate_prepared_selection_inputs(
            prepared, selection_task
        )

        def materialize_recovery_lineage(
            locked_task: ResearchTaskV12,
            winner: str,
        ) -> ExecutionMaterialization:
            expected = validated.get(winner)
            if expected is None:
                raise DecisionPolicyViolation(
                    "fairness_selected_candidate_not_prevalidated"
                )
            actual = prepare_selection_execution(prepared, locked_task, winner)
            self._assert_same_prepared_inputs(expected.initial, actual)
            if expected.validated.decision.model_copy(update={
                "assignment_preflight_outcomes": expected.initial.decision.assignment_preflight_outcomes,
            }) != expected.initial.decision:
                raise DecisionPolicyViolation("execution_preflight_changed_frozen_decision")
            decision_payload = expected.validated.decision.model_dump(mode="json", by_alias=True)
            decision_payload.update(
                {
                    "supersedesExecutionDecisionId": original.execution_decision_id,
                    "revisionReasonCode": request.directive_kind,
                }
            )
            revised = ExecutionPreparation(
                task=actual.task,
                decision=ExecutionDecision.model_validate(decision_payload),
                work_units=expected.validated.work_units,
                selected_candidate_identity=actual.selected_candidate_identity,
            )
            return freeze_selection_execution(prepared, locked_task, revised)

        return self._repository.commit_prepared_selection(
            prepared,
            materialize_recovery_lineage,
        )

    def release_ready(
        self,
        *,
        task_id: str,
        execution_decision_id: str,
        owner_id: str,
        workspace_id: str,
        expected_task_revision: int,
    ) -> ResearchTaskV12:
        task = self._repository.load(
            task_id=task_id,
            owner_id=owner_id,
            workspace_id=workspace_id,
        )
        if task.revision != expected_task_revision:
            raise ExecutionPreflightBlocked("task_revision_conflict")
        decision = next(
            (row for row in task.execution_decisions if row.execution_decision_id == execution_decision_id),
            None,
        )
        if decision is None:
            raise DecisionPolicyViolation("execution_decision_missing")
        original_task = task
        if decision.status is not ExecutionDecisionStatus.DISPATCHED:
            restored = _legacy_material_release_task(task, decision)
            if restored is None:
                return task
            task = restored
            decision = next(row for row in task.execution_decisions
                            if row.execution_decision_id == execution_decision_id)
        if _method_terminal_blocker(task, decision) is not None:
            settled = _settle_method_frontier(task, decision=decision, occurred_at=self._now())
            if settled is task:
                return task
            return self._repository.save(
                settled, owner_id=owner_id, workspace_id=workspace_id,
                expected_task_revision=expected_task_revision,
            )
        snapshot = self._repository.load_registry_snapshot(decision.registry_snapshot_ref.content_hash)
        if not execution_snapshot_matches_planning(
            task.registry_snapshot_ref.model_dump(mode="json", by_alias=True),
            decision.registry_snapshot_ref.model_dump(mode="json", by_alias=True),
            snapshot,
        ):
            raise DecisionPolicyViolation("resolver_snapshot_mismatch")
        preparation = prepare_ready_assignments(
            task,
            execution_decision_id=execution_decision_id,
            snapshot=snapshot,
        )
        if preparation is None:
            if original_task is not task:
                return original_task
            settled = _settle_method_frontier(task, decision=decision, occurred_at=self._now())
            if settled is task:
                return task
            return self._repository.save(
                settled, owner_id=owner_id, workspace_id=workspace_id,
                expected_task_revision=expected_task_revision,
            )
        try:
            preparation = self._validate_execution_preparation(
                snapshot=snapshot,
                preparation=preparation,
                owner_id=owner_id,
                workspace_id=workspace_id,
            )
        except ExecutionPreflightBlocked as exc:
            blocked = block_decision_for_input_contract_failure(
                task,
                execution_decision_id=execution_decision_id,
                reason_code=exc.code,
                occurred_at=self._now(),
            )
            return self._repository.save(
                blocked,
                owner_id=owner_id,
                workspace_id=workspace_id,
                expected_task_revision=expected_task_revision,
            )
        release = freeze_ready_assignments(
            preparation, released_at=self._now()
        )
        return self._repository.save(
            release.task,
            owner_id=owner_id,
            workspace_id=workspace_id,
            expected_task_revision=expected_task_revision,
            pending_dispatches=list(release.dispatches),
            automatic_dispatch=bool(release.dispatches),
        )

    def close_from_facts(
        self,
        *,
        task_id: str,
        execution_decision_id: str,
        owner_id: str,
        workspace_id: str,
        expected_task_revision: int,
    ) -> ResearchTaskV12:
        task = self._repository.load(
            task_id=task_id,
            owner_id=owner_id,
            workspace_id=workspace_id,
        )
        if task.revision != expected_task_revision:
            raise ExecutionPreflightBlocked("task_revision_conflict")
        closed = close_decision_from_durable_facts(
            task,
            execution_decision_id=execution_decision_id,
            closed_at=self._now(),
        )
        return self._repository.save(
            closed,
            owner_id=owner_id,
            workspace_id=workspace_id,
            expected_task_revision=expected_task_revision,
        )


__all__ = [
    "DECISION_POLICY_VERSION",
    "FAIRNESS_POLICY_VERSION",
    "DecisionPolicyViolation",
    "AssignmentRelease",
    "BlockedSelectionReplay",
    "ExecutionMaterialization",
    "ExecutionOutcomeResolution",
    "ExecutionPreflightBlocked",
    "FairnessDescriptor",
    "PreparedSelection",
    "RecoveryMaterializationPort",
    "RecoveryMaterializationRequest",
    "ResearchExecutionDecisionService",
    "RuntimeReadinessInvalid",
    "SelectionCommitResult",
    "SelectionReplayEvidence",
    "SelectionRequestConflictError",
    "candidate_identity",
    "close_and_reconcile_decision_from_durable_facts",
    "plan_execution_continuation",
    "close_decision_from_durable_facts",
    "assemble_degraded_final_report_markdown",
    "collect_final_fallback_upstream_artifacts",
    "evaluate_instance_preflight",
    "prepare_selection",
    "prepare_selection_execution",
    "freeze_selection_execution",
    "prepare_ready_assignments",
    "freeze_ready_assignments",
    "resolve_execution_outcome",
    "validate_execution_decision",
]
