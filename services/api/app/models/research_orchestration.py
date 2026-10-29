"""Research orchestration v1.2 persistence contracts.

These models are intentionally independent from the public legacy TaskOut DTO.
They describe persisted control-plane facts and reject unknown command fields.
"""

from __future__ import annotations

import math
from datetime import datetime
from enum import StrEnum
from hashlib import sha256
from typing import Annotated, Literal, TypeAlias

from pydantic import (
    AwareDatetime,
    BeforeValidator,
    Field,
    JsonValue,
    ValidationInfo,
    model_validator,
)

from app.models.project_completion_targets import (
    validate_project_completion_requirements as _validate_project_completion_requirements,
)
from app.models.project_completion_targets import (
    validate_workflow_target_coverage,
)
from app.models.research_capability_call import (
    CapabilityCallStatus,
    CapabilityType,
    InternalCapabilityCallInput,
    validate_capability_call_lifecycle,
    validate_capability_call_work_units,
)
from app.models.research_contract_base import ContractModel, NonEmptyStr
from app.models.research_round_end import RoundEndEvidence, validate_completion_decision_authority
from app.models.research_understanding import (
    READ_ONLY_LEGACY_RESEARCH_DESIGN,
    ResearchUnderstanding,
)

PositiveInt = Annotated[int, Field(ge=1, strict=True)]
NonNegativeInt = Annotated[int, Field(ge=0, strict=True)]
NonNegativeFloat = Annotated[float, Field(ge=0, strict=True, allow_inf_nan=False)]
StrictBool = Annotated[bool, Field(strict=True)]
JsonObject: TypeAlias = dict[str, JsonValue]


def _strict_timestamp_input(value: object) -> object:
    # JSON timestamps remain strings, but numeric/bool coercion is never a contract input.
    if isinstance(value, (str, datetime)) and not isinstance(value, bool):
        return value
    raise ValueError("timestamp must be an RFC 3339 string or aware datetime")


JsonAwareDatetime = Annotated[AwareDatetime, BeforeValidator(_strict_timestamp_input)]


# This context is reserved for the public GET-only projection adapter. Domain
# commands, repositories, reconciliation, and lifecycle code never supply it.
READ_ONLY_LEGACY_FINAL_RESULT_AUTHORITY = "read_only_legacy_final_result_authority"
class StageKey(StrEnum):
    PROBLEM_MODELING = "problem_modeling"
    SOLUTION_DESIGN = "solution_design"
    EXECUTION_VALIDATION = "execution_validation"
    INSIGHT_GENERATION = "insight_generation"


class TaskStatus(StrEnum):
    DRAFT = "draft"
    NOT_STARTED = "not_started"
    RUNNING = "running"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskExecutionMode(StrEnum):
    AUTOMATIC_RESEARCH = "automatic_research"
    STAGE_CONFIRMATION = "stage_confirmation"
    MANUAL_STEP = "manual_step"


class SubstageRuntimeStatus(StrEnum):
    PLANNED = "planned"
    READY = "ready"
    QUEUED = "queued"
    RUNNING = "running"
    REVIEWING = "reviewing"
    NEEDS_INPUT = "needs_input"
    BLOCKED_RECOVERABLE = "blocked_recoverable"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


class ArtifactState(StrEnum):
    USABLE = "usable"
    NEEDS_REVIEW = "needs_review"
    NEEDS_INPUT = "needs_input"
    INSUFFICIENT_MATERIALS = "insufficient_materials"
    FAILED = "failed"
    PARTIAL_SUCCESS = "partial_success"
    REJECTED_OR_NEEDS_REVISION = "rejected_or_needs_revision"


class ArtifactLineageKind(StrEnum):
    DIRECT_WORK_UNIT_OUTPUT = "direct_work_unit_output"
    CAPABILITY_CALL_OUTPUT = "capability_call_output"
    PLANNING = "planning"


class RiskClass(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class PlanningMode(StrEnum):
    LIGHTWEIGHT = "lightweight"
    PARTIAL = "partial"
    SDR = "sdr"


class PlanningAttemptStatus(StrEnum):
    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"


class AdditionalActionType(StrEnum):
    NONE = "none"
    SEMANTIC_ADEQUACY = "semantic_adequacy"
    PLANNER_REPAIR = "planner_repair"


class ResearchDesignStatus(StrEnum):
    DRAFT = "draft"
    CONFIRMED = "confirmed"
    SUPERSEDED = "superseded"
    CANCELLED = "cancelled"


class AcceptanceCriterionKind(StrEnum):
    TARGET_OUTPUT = "target_output"
    ARTIFACT_QUALITY = "artifact_quality"
    CONSTRAINT_COMPLIANCE = "constraint_compliance"
    EVIDENCE_BOUNDARY_COMPLIANCE = "evidence_boundary_compliance"
    EXTERNAL_ACTION_GUARD = "external_action_guard"
    RISK_GUARD = "risk_guard"
    PROFESSIONAL_JUDGMENT = "professional_judgment"


class JudgmentRequirement(StrEnum):
    DETERMINISTIC = "deterministic"
    RESEARCH_SYNTHESIS = "research_synthesis"
    DOMAIN_PROFESSIONAL = "domain_professional"
    HUMAN_AUTHORITY = "human_authority"


class EvaluatorType(StrEnum):
    DETERMINISTIC = "deterministic"
    AGENT = "agent"
    HUMAN_GATE = "human_gate"


class DependencyConditionType(StrEnum):
    INSTANCE_COMPLETED = "instance_completed"
    ARTIFACT_USABLE = "artifact_usable"
    CONFIRMATION_GRANTED = "confirmation_granted"


class CoverageSourceType(StrEnum):
    TARGET_OUTPUT = "target_output"
    CONSTRAINT = "constraint"
    EVIDENCE_BOUNDARY = "evidence_boundary"
    EXTERNAL_ACTION_BOUNDARY = "external_action_boundary"
    RISK_BOUNDARY = "risk_boundary"


class CoverageMode(StrEnum):
    FULL = "full"
    PARTIAL = "partial"
    NONE = "none"


class CoverageStatus(StrEnum):
    PASSED_STRUCTURAL = "passed_structural"
    PASSED_SEMANTIC = "passed_semantic"
    EXECUTABLE_WITH_GAPS = "executable_with_gaps"
    NEEDS_REVISION = "needs_revision"


class CoverageEvaluationMode(StrEnum):
    DETERMINISTIC = "deterministic"
    SEMANTIC_ASSISTED = "semantic_assisted"


class AssignmentRole(StrEnum):
    PRIMARY = "primary"
    REVIEWER = "reviewer"
    SUPPORT = "support"


class CompletionCoverage(StrEnum):
    COMPLETION_CAPABLE = "completion_capable"
    PROGRESS_CAPABLE = "progress_capable"


class SuccessMode(StrEnum):
    ALL_REQUIRED = "all_required"
    ANY_REQUIRED = "any_required"


class ReviewRequirement(StrEnum):
    NONE = "none"
    ALL_REVIEWERS_APPROVE = "all_reviewers_approve"
    ANY_REVIEWER_APPROVES = "any_reviewer_approves"


class ReviewerRejectAction(StrEnum):
    NEW_REVISION_DECISION = "new_revision_decision"
    HUMAN_GATE = "human_gate"


class ProgressResultCode(StrEnum):
    PROGRESSED = "progressed"
    NEEDS_INPUT = "needs_input"
    BLOCKED_RECOVERABLE = "blocked_recoverable"


class ResultAssemblyMode(StrEnum):
    PRIMARY_OUTPUT = "primary_output"
    DESIGNATED_ASSEMBLER = "designated_assembler"
    ARTIFACT_SET = "artifact_set"


class CandidateRejectionCode(StrEnum):
    DISABLED = "disabled"
    ROLE_MISMATCH = "role_mismatch"
    INPUT_INCOMPATIBLE = "input_incompatible"
    OUTPUT_INCOMPATIBLE = "output_incompatible"
    AGENT_SKILL_NOT_GRANTED = "agent_skill_not_granted"
    EXPLICITLY_NOT_FOR = "explicitly_not_for"
    SEMANTIC_FIT_INSUFFICIENT = "semantic_fit_insufficient"


class CandidateExecutionBlockerCode(StrEnum):
    REQUIRED_INPUT_MISSING = "required_input_missing"
    AUTHORIZATION_DENIED = "authorization_denied"
    RISK_EXCEEDED = "risk_exceeded"
    BUDGET_EXCEEDED = "budget_exceeded"
    RUNTIME_UNAVAILABLE = "runtime_unavailable"
    PROVIDER_UNAVAILABLE = "provider_unavailable"


class ExecutionStrategy(StrEnum):
    DIRECT_SKILL = "direct_skill"
    SKILL_COMPOSITION = "skill_composition"
    EXECUTION_METHOD = "execution_method"
    SPECIALIST_AGENT = "specialist_agent"
    GENERAL_RESEARCH_AGENT = "general_research_agent"
    MULTI_AGENT = "multi_agent"


class ExecutionPurpose(StrEnum):
    COMPLETION = "completion"
    PROGRESS = "progress"


class ExecutionDecisionStatus(StrEnum):
    PROPOSED = "proposed"
    SELECTED = "selected"
    DISPATCHED = "dispatched"
    CLOSED = "closed"
    SUPERSEDED = "superseded"
    CANCELLED = "cancelled"


class ExecutionDecisionOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    PROGRESSED = "progressed"
    NEEDS_INPUT = "needs_input"
    REJECTED = "rejected"
    BLOCKED = "blocked"
    FAILED = "failed"
    CANCELLED = "cancelled"


class FallbackReasonCode(StrEnum):
    NO_COMPLETION_COVERAGE_PREFERRED_CANDIDATE = "no_completion_coverage_preferred_candidate"
    PREFERRED_NOT_EXECUTABLE_INPUTS = "preferred_not_executable_inputs"
    PREFERRED_NOT_EXECUTABLE_AUTHORIZATION = "preferred_not_executable_authorization"
    PREFERRED_NOT_EXECUTABLE_BUDGET = "preferred_not_executable_budget"
    PREFERRED_NOT_EXECUTABLE_RISK = "preferred_not_executable_risk"
    PREFERRED_NOT_EXECUTABLE_PROVIDER = "preferred_not_executable_provider"
    PREFERRED_NOT_EXECUTABLE_RUNTIME = "preferred_not_executable_runtime"


class WorkUnitSourceMode(StrEnum):
    LIVE = "live"
    LEGACY_PROJECTION = "legacy_projection"


class WorkUnitStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    REVIEWING = "reviewing"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"
    EXTERNAL_RESULT_UNKNOWN = "external_result_unknown"


_TERMINAL_DECISION_STATUSES = {
    ExecutionDecisionStatus.CLOSED,
    ExecutionDecisionStatus.SUPERSEDED,
    ExecutionDecisionStatus.CANCELLED,
}
_ACTIVE_WORK_UNIT_STATUSES = {
    WorkUnitStatus.QUEUED,
    WorkUnitStatus.RUNNING,
    WorkUnitStatus.REVIEWING,
}


def _has_current_execution_authority(
    decision_status: ExecutionDecisionStatus,
    design_status: ResearchDesignStatus,
) -> bool:
    return decision_status not in _TERMINAL_DECISION_STATUSES and design_status == ResearchDesignStatus.CONFIRMED


def _is_terminal_decision_history(
    decision_status: ExecutionDecisionStatus,
    design_status: ResearchDesignStatus,
) -> bool:
    return (
        decision_status in _TERMINAL_DECISION_STATUSES
        and design_status in {ResearchDesignStatus.CONFIRMED, ResearchDesignStatus.SUPERSEDED}
    )






class DispatchStatus(StrEnum):
    PENDING = "pending"
    CLAIMED = "claimed"
    SUBMISSION_AUTHORIZED = "submission_authorized"
    SUBMITTED = "submitted"
    SUPPRESSED = "suppressed"
    SUBMISSION_UNKNOWN = "submission_unknown"
    FAILED = "failed"


class InstanceRef(ContractModel):
    substage_instance_id: NonEmptyStr
    instance_revision: PositiveInt


class ResearchDesignRef(ContractModel):
    research_design_id: NonEmptyStr
    version: PositiveInt


class WorkflowResponsibilityCoverage(ContractModel):
    responsibility_key: NonEmptyStr
    definition_id: NonEmptyStr
    requirement: Literal["core", "conditional"]
    coverage_mode: Literal["planned_work", "reused_result", "not_selected"]
    instance_refs: list[InstanceRef]
    reused_result_refs: list[NonEmptyStr]
    rationale: NonEmptyStr

    @model_validator(mode="after")
    def validate_coverage(self) -> "WorkflowResponsibilityCoverage":
        _unique_values(
            (f"{row.substage_instance_id}:{row.instance_revision}" for row in self.instance_refs),
            "WorkflowResponsibilityCoverage.instanceRef",
        )
        _unique_values(self.reused_result_refs, "WorkflowResponsibilityCoverage.reusedResultRef")
        if self.coverage_mode == "planned_work":
            if not self.instance_refs or self.reused_result_refs:
                raise ValueError("planned_work requires instanceRefs and no reusedResultRefs")
        elif self.coverage_mode == "reused_result":
            if self.instance_refs or not self.reused_result_refs:
                raise ValueError("reused_result requires reusedResultRefs and no instanceRefs")
        elif self.instance_refs or self.reused_result_refs:
            raise ValueError("not_selected cannot carry instanceRefs or reusedResultRefs")
        if self.requirement == "core" and self.coverage_mode == "not_selected":
            raise ValueError("core Workflow responsibility cannot be not_selected")
        return self


class WorkflowProvenanceRef(ContractModel):
    workflow_id: NonEmptyStr
    workflow_version: PositiveInt
    content_hash: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$", strict=True)]


class RegistrySnapshotRef(ContractModel):
    schema_version: PositiveInt
    content_hash: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$", strict=True)]


class MultiAgentRecipeRef(ContractModel):
    recipe_id: NonEmptyStr
    recipe_version: PositiveInt


class AgentRef(ContractModel):
    agent_id: NonEmptyStr
    definition_version: PositiveInt


class SkillRef(ContractModel):
    skill_id: NonEmptyStr
    definition_version: PositiveInt


CapabilityRef: TypeAlias = AgentRef | SkillRef
ExecutionCandidateRef: TypeAlias = AgentRef | SkillRef | MultiAgentRecipeRef


class BindingRef(ContractModel):
    binding_id: NonEmptyStr
    binding_version: PositiveInt


class PlanningComplexitySnapshot(ContractModel):
    problem_clarity: Annotated[str, Field(pattern=r"^(high|medium|low)$", strict=True)]
    expected_stage_span: Annotated[str, Field(pattern=r"^(one|multiple|unknown)$", strict=True)]
    dependency_complexity: RiskClass
    evidence_burden: RiskClass
    external_action_risk: RiskClass
    research_uncertainty: RiskClass
    rationale: NonEmptyStr


class PlanningSourceUsage(ContractModel):
    source_kind: NonEmptyStr
    source_id: NonEmptyStr
    source_version: NonEmptyStr
    source_artifact_ref: NonEmptyStr
    source_content_hash: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$", strict=True)]
    adopted_source_paths: list[NonEmptyStr]
    not_adopted_source_paths: list[NonEmptyStr]
    revalidation_source_paths: list[NonEmptyStr]

    @model_validator(mode="after")
    def validate_path_partition(self) -> "PlanningSourceUsage":
        groups = (
            self.adopted_source_paths,
            self.not_adopted_source_paths,
            self.revalidation_source_paths,
        )
        for values in groups:
            if len(values) != len(set(values)):
                raise ValueError("planning source usage contains duplicate source paths")
        flattened = [value for values in groups for value in values]
        if not flattened:
            raise ValueError("planning source usage must classify at least one source path")
        if len(flattened) != len(set(flattened)):
            raise ValueError("planning source usage categories must be disjoint")
        return self


class PlanningInputSource(ContractModel):
    source_kind: Literal["user_request", "user_revision", "plan_revision"]
    source_ref: NonEmptyStr
    content: NonEmptyStr


class PlanningInputSnapshot(ContractModel):
    # Code freezes the exact source chain. The base ref is historical provenance,
    # not a pointer that execution must resolve after a fork or design recovery.
    sources: Annotated[list[PlanningInputSource], Field(min_length=1)]
    base_research_design_ref: ResearchDesignRef | None

    @model_validator(mode="after")
    def validate_sources(self) -> "PlanningInputSnapshot":
        _unique_values((row.source_ref for row in self.sources), "planning input sourceRef")
        if self.sources[0].source_kind != "user_request":
            raise ValueError("planning input lineage requires its original user request")
        if any(row.source_kind == "user_request" for row in self.sources[1:]):
            raise ValueError("planning input lineage cannot contain another root request")
        return self


class PlanningAttempt(ContractModel):
    planning_attempt_id: NonEmptyStr
    task_id: NonEmptyStr
    request_id: NonEmptyStr
    planner_call_idempotency_key: NonEmptyStr
    planner_provider_call_ref: NonEmptyStr | None
    user_input_snapshot: PlanningInputSnapshot | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    planning_source_usages: list[PlanningSourceUsage] = Field(default_factory=list)
    additional_action_type: AdditionalActionType
    additional_action_idempotency_key: NonEmptyStr | None
    additional_provider_call_ref: NonEmptyStr | None
    status: PlanningAttemptStatus
    started_at: JsonAwareDatetime
    finished_at: JsonAwareDatetime | None

    @model_validator(mode="after")
    def validate_action_and_status(self) -> "PlanningAttempt":
        source_identities = [
            (row.source_kind, row.source_id, row.source_version)
            for row in self.planning_source_usages
        ]
        if len(source_identities) != len(set(source_identities)):
            raise ValueError("planning attempt contains duplicate planning source usages")
        has_action = self.additional_action_type != AdditionalActionType.NONE
        if has_action != (self.additional_action_idempotency_key is not None):
            raise ValueError("additional action type and idempotency key must agree")
        if not has_action and self.additional_provider_call_ref is not None:
            raise ValueError("additional provider call requires an additional action")
        if self.status == PlanningAttemptStatus.STARTED and self.finished_at is not None:
            raise ValueError("started planning attempt cannot be finished")
        if self.status != PlanningAttemptStatus.STARTED and self.finished_at is None:
            raise ValueError("terminal planning attempt requires finishedAt")
        return self


class AcceptanceCriterion(ContractModel):
    criterion_id: NonEmptyStr
    description: NonEmptyStr
    criterion_kind: AcceptanceCriterionKind
    coverage_target_ids: list[NonEmptyStr]
    required_artifact_type: NonEmptyStr | None
    judgment_requirement: JudgmentRequirement
    evaluator_type: EvaluatorType
    evaluator_ref: NonEmptyStr | None

    @model_validator(mode="after")
    def validate_evaluator(self) -> "AcceptanceCriterion":
        if self.evaluator_type == EvaluatorType.DETERMINISTIC and self.evaluator_ref is not None:
            raise ValueError("deterministic evaluator cannot have evaluatorRef")
        if self.evaluator_type != EvaluatorType.DETERMINISTIC and self.evaluator_ref is None:
            raise ValueError("agent and human gate evaluators require evaluatorRef")
        if self.judgment_requirement == JudgmentRequirement.RESEARCH_SYNTHESIS:
            if self.criterion_kind != AcceptanceCriterionKind.PROFESSIONAL_JUDGMENT:
                raise ValueError("research_synthesis requires professional_judgment criterion")
            if self.evaluator_type != EvaluatorType.DETERMINISTIC or self.evaluator_ref is not None:
                raise ValueError("research_synthesis cannot bind a concrete evaluator")
        elif self.judgment_requirement == JudgmentRequirement.DOMAIN_PROFESSIONAL:
            if self.evaluator_type != EvaluatorType.AGENT or self.evaluator_ref is None:
                raise ValueError("domain_professional requires an agent evaluator")
        return self


class SubstageDependency(ContractModel):
    dependency_id: NonEmptyStr
    upstream_instance_ref: InstanceRef
    condition_type: DependencyConditionType
    required_artifact_types: list[NonEmptyStr]
    confirmation_target_ref: NonEmptyStr | None

    @model_validator(mode="after")
    def validate_condition_fields(self) -> "SubstageDependency":
        if self.condition_type == DependencyConditionType.ARTIFACT_USABLE:
            if not self.required_artifact_types or self.confirmation_target_ref is not None:
                raise ValueError("artifact_usable requires artifact types only")
        elif self.condition_type == DependencyConditionType.CONFIRMATION_GRANTED:
            if self.required_artifact_types or self.confirmation_target_ref is None:
                raise ValueError("confirmation_granted requires confirmationTargetRef only")
        elif self.required_artifact_types or self.confirmation_target_ref is not None:
            raise ValueError("instance_completed cannot carry artifact or confirmation requirements")
        return self


class ScopeBoundary(ContractModel):
    allowed_objective_kinds: list[NonEmptyStr]
    excluded_actions: list[NonEmptyStr]
    allowed_material_refs: list[NonEmptyStr]
    max_substage_instances: PositiveInt | None


class BudgetBoundary(ContractModel):
    max_model_calls: NonNegativeInt | None
    max_tool_calls: NonNegativeInt | None
    max_wall_time_sec: NonNegativeInt | None
    max_cost_amount: NonNegativeFloat | None
    currency: NonEmptyStr | None

    @model_validator(mode="after")
    def validate_currency(self) -> "BudgetBoundary":
        if (self.max_cost_amount is None) != (self.currency is None):
            raise ValueError("maxCostAmount and currency must both be null or non-null")
        return self


class ExternalActionBoundary(ContractModel):
    allowed_action_kinds: list[NonEmptyStr]
    confirmation_required_kinds: list[NonEmptyStr]
    allowed_destinations: list[NonEmptyStr]


class RiskBoundary(ContractModel):
    max_risk_class: RiskClass
    prohibited_actions: list[NonEmptyStr]
    human_gate_triggers: list[NonEmptyStr]


class AuthorizationSnapshot(ContractModel):
    policy_version: NonEmptyStr
    allowed_memory_scopes: list[NonEmptyStr]
    allowed_external_action_kinds: list[NonEmptyStr]
    confirmation_refs: list[NonEmptyStr]


class BudgetSnapshot(ContractModel):
    boundary: BudgetBoundary
    consumed_model_calls: NonNegativeInt
    consumed_tool_calls: NonNegativeInt
    consumed_cost_amount: NonNegativeFloat


class RiskSnapshot(ContractModel):
    boundary: RiskBoundary
    evaluated_risk_class: RiskClass
    triggered_human_gates: list[NonEmptyStr]


class CompletionPolicy(ContractModel):
    required_assignment_ids: list[NonEmptyStr]
    success_mode: SuccessMode
    reviewer_assignment_ids: list[NonEmptyStr]
    review_requirement: ReviewRequirement
    partial_success_allowed: StrictBool
    on_reviewer_reject: ReviewerRejectAction


class ProgressPolicy(ContractModel):
    required_assignment_ids: list[NonEmptyStr]
    required_artifact_types: list[NonEmptyStr]
    accepted_result_codes: ProgressResultCode


class CompletionPolicyTemplate(ContractModel):
    required_template_keys: list[NonEmptyStr]
    success_mode: SuccessMode
    reviewer_template_keys: list[NonEmptyStr]
    review_requirement: ReviewRequirement
    partial_success_allowed: StrictBool
    on_reviewer_reject: ReviewerRejectAction


class ArtifactRequirementTemplate(ContractModel):
    artifact_type: NonEmptyStr
    minimum_count: NonNegativeInt
    source_template_keys: list[NonEmptyStr]


class ResultAssemblyPolicyTemplate(ContractModel):
    mode: ResultAssemblyMode
    assembler_template_key: NonEmptyStr | None
    final_artifact_requirements: list[ArtifactRequirementTemplate]


class ArtifactRequirement(ContractModel):
    artifact_type: NonEmptyStr
    minimum_count: NonNegativeInt
    source_assignment_ids: list[NonEmptyStr]


class ResultAssemblyPolicy(ContractModel):
    mode: ResultAssemblyMode
    assembler_assignment_id: NonEmptyStr | None
    final_artifact_requirements: list[ArtifactRequirement]


class CoverageTarget(ContractModel):
    target_id: NonEmptyStr
    source_type: CoverageSourceType
    source_path: NonEmptyStr
    description: NonEmptyStr
    required_artifact_types: list[NonEmptyStr]
    enforcement_ref: NonEmptyStr | None
    required: StrictBool


class CoverageClaim(ContractModel):
    target_id: NonEmptyStr
    instance_refs: list[InstanceRef]
    coverage_mode: CoverageMode
    acceptance_criterion_refs: list[NonEmptyStr]
    derivation_rule_refs: list[NonEmptyStr]
    rationale: NonEmptyStr


class ResearchDesignCoverageReport(ContractModel):
    coverage_report_id: NonEmptyStr
    design_ref: ResearchDesignRef
    planning_attempt_id: NonEmptyStr
    targets: list[CoverageTarget]
    claims: list[CoverageClaim]
    uncovered_required_target_ids: list[NonEmptyStr]
    overloaded_instance_refs: list[InstanceRef]
    missing_dependency_refs: list[NonEmptyStr]
    status: CoverageStatus
    evaluation_tier: PlanningMode
    evaluation_mode: CoverageEvaluationMode
    semantic_evaluation_ref: NonEmptyStr | None
    automatic_repair_count: NonNegativeInt
    policy_version: NonEmptyStr
    evaluated_at: JsonAwareDatetime

    @model_validator(mode="after")
    def validate_evaluation_evidence(self) -> "ResearchDesignCoverageReport":
        if self.evaluation_mode == CoverageEvaluationMode.DETERMINISTIC:
            if self.semantic_evaluation_ref is not None or self.automatic_repair_count not in {0, 1}:
                raise ValueError("deterministic coverage cannot claim semantic evidence")
        elif self.semantic_evaluation_ref is None or self.automatic_repair_count != 0:
            raise ValueError("semantic assisted coverage requires one semantic evidence ref")
        target_ids = _unique_values((target.target_id for target in self.targets), "CoverageTarget.targetId")
        _unique_values((claim.target_id for claim in self.claims), "CoverageClaim.targetId")
        if any(claim.target_id not in target_ids for claim in self.claims):
            raise ValueError("CoverageClaim references an unknown target")
        if any(target_id not in target_ids for target_id in self.uncovered_required_target_ids):
            raise ValueError("uncoveredRequiredTargetIds contains an unknown target")
        if self.status == CoverageStatus.EXECUTABLE_WITH_GAPS:
            from app.modules.research_design_coverage_confirmation import (
                has_only_executable_target_gaps,
            )

            if not has_only_executable_target_gaps(self):
                raise ValueError("executable coverage gaps must be limited to target outputs")
        return self


class RepeatableDefinitionReentryAuthority(ContractModel):
    """Definition-owned, fail-closed authority for post-confirmation re-entry."""

    policy_version: Annotated[
        str, Field(pattern=r"^repeatable_definition_reentry_v1$", strict=True)
    ]
    trigger_definition_ids: Annotated[list[NonEmptyStr], Field(min_length=1)]

    @model_validator(mode="after")
    def validate_trigger_definitions(self) -> "RepeatableDefinitionReentryAuthority":
        _unique_values(self.trigger_definition_ids, "triggerDefinitionId")
        return self


class SubstageInstance(ContractModel):
    substage_instance_id: NonEmptyStr
    instance_revision: PositiveInt
    substage_definition_id: NonEmptyStr
    substage_definition_version: PositiveInt
    stage_key: StageKey
    objective: NonEmptyStr
    input_refs: list[NonEmptyStr]
    expected_artifact_types: list[NonEmptyStr]
    acceptance_criteria: list[AcceptanceCriterion]
    dependencies: list[SubstageDependency]
    sequence_hint: NonNegativeInt | None
    repeatable_reentry_authority: RepeatableDefinitionReentryAuthority | None = None

    @model_validator(mode="after")
    def validate_identities(self) -> "SubstageInstance":
        _unique_values((item.criterion_id for item in self.acceptance_criteria), "criterionId")
        _unique_values((item.dependency_id for item in self.dependencies), "dependencyId")
        return self


class ReentryExecutionInstance(ContractModel):
    """Immutable execution fact materialized under one confirmed Design authority."""

    research_design_version: PositiveInt
    instance: SubstageInstance
    template_instance_ref: InstanceRef
    triggering_scientific_state_version: PositiveInt
    triggering_execution_decision_id: NonEmptyStr
    previous_relevant_instance_ref: InstanceRef
    triggering_state_source_refs: Annotated[list[NonEmptyStr], Field(min_length=1)]
    reentry_fingerprint: NonEmptyStr
    created_at: JsonAwareDatetime

    @model_validator(mode="after")
    def validate_lineage_shape(self) -> "ReentryExecutionInstance":
        if self.instance.repeatable_reentry_authority is None:
            raise ValueError("re-entry instance requires frozen Definition authority")
        _unique_values(self.triggering_state_source_refs, "triggeringStateSourceRef")
        new_ref = (self.instance.substage_instance_id, self.instance.instance_revision)
        if new_ref in {
            (
                self.template_instance_ref.substage_instance_id,
                self.template_instance_ref.instance_revision,
            ),
            (
                self.previous_relevant_instance_ref.substage_instance_id,
                self.previous_relevant_instance_ref.instance_revision,
            ),
        }:
            raise ValueError("re-entry instance requires a new identity")
        return self


class ProjectCompletionRequirement(ContractModel):
    completion_requirement_id: NonEmptyStr
    target_output_ref: NonEmptyStr
    description: NonEmptyStr
    workflow_responsibility_key: NonEmptyStr | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    contributing_responsibility_keys: (
        Annotated[list[NonEmptyStr], Field(min_length=1)] | None
    ) = Field(
        default=None, exclude_if=lambda value: value is None
    )


class ResearchDesignVersion(ContractModel):
    research_design_id: NonEmptyStr
    task_id: NonEmptyStr
    version: PositiveInt
    supersedes_version: PositiveInt | None
    status: ResearchDesignStatus
    planning_mode: PlanningMode
    planning_complexity: PlanningComplexitySnapshot
    goal: NonEmptyStr
    understanding: ResearchUnderstanding
    constraints: list[NonEmptyStr]
    scope_boundary: ScopeBoundary
    budget_boundary: BudgetBoundary
    external_action_boundary: ExternalActionBoundary
    risk_boundary: RiskBoundary
    workflow_provenance_ref: WorkflowProvenanceRef | None
    workflow_responsibility_coverage: list[WorkflowResponsibilityCoverage] = Field(default_factory=list)
    project_completion_requirements: Annotated[
        list[ProjectCompletionRequirement], Field(min_length=1)
    ]
    # A ResearchDesign is executable only when it owns at least one real
    # substage instance.  Keep this invariant at the persisted contract
    # boundary so malformed/legacy payloads cannot become confirmed designs
    # that later dead-end during execution materialization.
    instances: Annotated[list[SubstageInstance], Field(min_length=1)]
    coverage_report: ResearchDesignCoverageReport | None
    created_at: JsonAwareDatetime
    confirmed_at: JsonAwareDatetime | None
    confirmation_ref: NonEmptyStr | None

    @model_validator(mode="before")
    @classmethod
    def project_read_only_legacy_provenance(
        cls, value: object, info: ValidationInfo
    ) -> object:
        if not bool((info.context or {}).get(READ_ONLY_LEGACY_RESEARCH_DESIGN)):
            return value
        if not isinstance(value, dict):
            return value
        if (
            "workflowProvenanceRef" in value
            or "workflow_provenance_ref" in value
        ):
            return value
        projected = dict(value)
        projected["workflowProvenanceRef"] = None
        return projected

    @model_validator(mode="after")
    def validate_design(self) -> "ResearchDesignVersion":
        _validate_project_completion_requirements(
            self.project_completion_requirements,
            self.understanding.target_outputs,
        )
        validate_workflow_target_coverage(self)
        _unique_values((row.substage_instance_id for row in self.instances), "substageInstanceId")
        instance_refs = {(row.substage_instance_id, row.instance_revision) for row in self.instances}
        definition_by_instance = {
            (row.substage_instance_id, row.instance_revision): row.substage_definition_id
            for row in self.instances
        }
        if self.workflow_responsibility_coverage:
            if self.workflow_provenance_ref is None:
                raise ValueError("Workflow responsibility coverage requires workflow provenance")
            _unique_values(
                (row.responsibility_key for row in self.workflow_responsibility_coverage),
                "WorkflowResponsibilityCoverage.responsibilityKey",
            )
            owned_instance_refs: set[tuple[str, int]] = set()
            for coverage in self.workflow_responsibility_coverage:
                for ref in coverage.instance_refs:
                    identity = (ref.substage_instance_id, ref.instance_revision)
                    if identity not in instance_refs:
                        raise ValueError("Workflow responsibility coverage references an unknown instance revision")
                    if definition_by_instance[identity] != coverage.definition_id:
                        raise ValueError("Workflow responsibility coverage Definition does not match its instance")
                    if identity in owned_instance_refs:
                        raise ValueError("a Workflow project instance cannot satisfy multiple responsibilities")
                    owned_instance_refs.add(identity)
        criteria_by_instance = {
            (row.substage_instance_id, row.instance_revision): {
                criterion.criterion_id for criterion in row.acceptance_criteria
            }
            for row in self.instances
        }
        for row in self.instances:
            for dependency in row.dependencies:
                ref = dependency.upstream_instance_ref
                if (ref.substage_instance_id, ref.instance_revision) not in instance_refs:
                    raise ValueError("dependency references an unknown instance revision")
        _assert_acyclic(
            {row.substage_instance_id: [dep.upstream_instance_ref.substage_instance_id for dep in row.dependencies]
             for row in self.instances},
            "SubstageInstance dependency",
        )
        if self.coverage_report is not None:
            expected = ResearchDesignRef(researchDesignId=self.research_design_id, version=self.version)
            if self.coverage_report.design_ref != expected:
                raise ValueError("coverage report designRef does not match its design")
            if self.coverage_report.evaluation_tier != self.planning_mode:
                raise ValueError("coverage evaluationTier must match planningMode")
            target_ids = {target.target_id for target in self.coverage_report.targets}
            all_criterion_ids = {
                criterion.criterion_id
                for instance in self.instances
                for criterion in instance.acceptance_criteria
            }
            for instance in self.instances:
                for criterion in instance.acceptance_criteria:
                    if any(target_id not in target_ids for target_id in criterion.coverage_target_ids):
                        raise ValueError("AcceptanceCriterion references an unknown coverage target")
            for claim in self.coverage_report.claims:
                claim_refs = {
                    (ref.substage_instance_id, ref.instance_revision) for ref in claim.instance_refs
                }
                if any(ref not in instance_refs for ref in claim_refs):
                    raise ValueError("CoverageClaim references an unknown instance revision")
                claim_criterion_ids = {
                    criterion_id
                    for ref in claim_refs
                    for criterion_id in criteria_by_instance[ref]
                }
                if any(ref not in all_criterion_ids for ref in claim.acceptance_criterion_refs):
                    raise ValueError("CoverageClaim references an unknown acceptance criterion")
                if any(ref not in claim_criterion_ids for ref in claim.acceptance_criterion_refs):
                    raise ValueError("CoverageClaim criterion must belong to a claimed instance")
        if self.status == ResearchDesignStatus.CONFIRMED:
            if self.confirmed_at is None or self.confirmation_ref is None or self.coverage_report is None:
                raise ValueError("confirmed design requires confirmation and coverage")
            from app.modules.research_design_coverage_confirmation import (
                coverage_allows_confirmation,
            )

            if not coverage_allows_confirmation(self.coverage_report):
                raise ValueError("confirmed design requires executable coverage")
        return self


class SubstageRuntimeState(ContractModel):
    substage_instance_id: NonEmptyStr
    instance_revision: PositiveInt
    status: SubstageRuntimeStatus
    active_execution_decision_id: NonEmptyStr | None
    work_unit_ids: list[NonEmptyStr]
    artifact_refs: list[NonEmptyStr]
    status_reason_code: NonEmptyStr | None
    updated_at: JsonAwareDatetime


class RecipeAssignmentTemplate(ContractModel):
    template_key: NonEmptyStr
    role: AssignmentRole
    required_supported_role: AssignmentRole
    objective_template: NonEmptyStr
    dependency_template_keys: list[NonEmptyStr]


class MultiAgentRecipeDefinition(ContractModel):
    recipe_id: NonEmptyStr
    recipe_version: PositiveInt
    substage_definition_id: NonEmptyStr
    substage_definition_version: PositiveInt
    applicability_rule_refs: list[NonEmptyStr]
    assignment_templates: list[RecipeAssignmentTemplate]
    completion_policy_template: CompletionPolicyTemplate
    result_assembly_policy_template: ResultAssemblyPolicyTemplate
    enabled: StrictBool

    @model_validator(mode="after")
    def validate_topology(self) -> "MultiAgentRecipeDefinition":
        keys = _unique_values((row.template_key for row in self.assignment_templates), "templateKey")
        if not keys:
            raise ValueError("recipe requires assignment templates")
        graph: dict[str, list[str]] = {}
        for row in self.assignment_templates:
            if any(ref not in keys for ref in row.dependency_template_keys):
                raise ValueError("recipe dependency references an unknown template")
            graph[row.template_key] = row.dependency_template_keys
        refs = (
            self.completion_policy_template.required_template_keys
            + self.completion_policy_template.reviewer_template_keys
        )
        if not self.completion_policy_template.required_template_keys:
            raise ValueError("recipe completion policy requires templates")
        if any(ref not in keys for ref in refs):
            raise ValueError("completion policy references an unknown template")
        reviewer_keys = {
            row.template_key for row in self.assignment_templates if row.role == AssignmentRole.REVIEWER
        }
        if set(self.completion_policy_template.reviewer_template_keys) != reviewer_keys:
            raise ValueError("reviewerTemplateKeys must list every reviewer template")
        assembly = self.result_assembly_policy_template
        if assembly.mode == ResultAssemblyMode.DESIGNATED_ASSEMBLER:
            if assembly.assembler_template_key not in keys:
                raise ValueError("designated assembler must reference a recipe template")
        elif assembly.assembler_template_key is not None:
            raise ValueError("assemblerTemplateKey is only valid for designated_assembler")
        if any(ref not in keys for req in assembly.final_artifact_requirements for ref in req.source_template_keys):
            raise ValueError("artifact requirement references an unknown template")
        _assert_acyclic(graph, "RecipeAssignmentTemplate dependency")
        return self


class ActorRef(ContractModel):
    actor_type: Annotated[str, Field(pattern=r"^(orchestrator|agent)$", strict=True)]
    actor_id: NonEmptyStr
    definition_version: PositiveInt | None

    @model_validator(mode="after")
    def validate_version(self) -> "ActorRef":
        if self.actor_type == "agent" and self.definition_version is None:
            raise ValueError("agent actor requires definitionVersion")
        if self.actor_type == "orchestrator" and self.definition_version is not None:
            raise ValueError("orchestrator actor cannot have definitionVersion")
        return self


class ExecutorRef(ContractModel):
    executor_type: Annotated[str, Field(pattern=r"^(agent|skill|legacy_unknown)$", strict=True)]
    executor_id: NonEmptyStr
    definition_version: PositiveInt | None

    @model_validator(mode="after")
    def validate_version(self) -> "ExecutorRef":
        if self.executor_type == "legacy_unknown":
            if self.executor_id != "legacy_unknown" or self.definition_version is not None:
                raise ValueError("legacy_unknown executor must use the legacy_unknown identity")
        elif self.definition_version is None:
            raise ValueError("live executor requires definitionVersion")
        return self


class RuntimeReadinessSnapshot(ContractModel):
    capability_ref: CapabilityRef
    ready: StrictBool
    runtime_binding_id: NonEmptyStr | None
    checked_at: JsonAwareDatetime
    expires_at: JsonAwareDatetime
    reason: NonEmptyStr | None

    @model_validator(mode="after")
    def validate_window(self) -> "RuntimeReadinessSnapshot":
        if self.expires_at <= self.checked_at:
            raise ValueError("runtime readiness expiresAt must be after checkedAt")
        if self.ready and self.reason is not None:
            raise ValueError("ready runtime snapshot cannot carry a failure reason")
        if not self.ready and self.reason is None:
            raise ValueError("unready runtime snapshot requires a reason")
        return self


class SemanticFitEvidence(ContractModel):
    objective_match: Annotated[str, Field(pattern=r"^(exact|compatible|none)$", strict=True)]
    input_contract_match: Annotated[str, Field(pattern=r"^(exact|adapter_compatible|none)$", strict=True)]
    output_contract_match: Annotated[str, Field(pattern=r"^(exact|adapter_compatible|partial|none)$", strict=True)]
    professional_judgment_fit: Annotated[
        str, Field(pattern=r"^(required_supported|not_required|unsupported)$", strict=True)
    ]
    positive_rule_refs: list[NonEmptyStr]
    negative_rule_refs: list[NonEmptyStr]
    adapter_refs: list[NonEmptyStr]
    policy_version: NonEmptyStr
    rationale: NonEmptyStr


class CandidatePreference(ContractModel):
    semantic_fit: Annotated[str, Field(pattern=r"^(exact|compatible|weak)$", strict=True)]
    semantic_fit_evidence: SemanticFitEvidence
    completion_coverage: CompletionCoverage
    executable_now: StrictBool
    execution_blocker_codes: list[CandidateExecutionBlockerCode]
    risk_class: RiskClass
    estimated_cost_amount: NonNegativeFloat | None
    permission_breadth: Annotated[str, Field(pattern=r"^(narrow|standard|broad)$", strict=True)]
    supports_professional_judgment: StrictBool
    active_work_unit_count: NonNegativeInt
    last_assigned_at: JsonAwareDatetime | None


class ExecutionMethodRoleEvaluation(ContractModel):
    role_ref: NonEmptyStr
    eligible: StrictBool
    rejection_codes: list[CandidateRejectionCode]
    executable_now: StrictBool
    execution_blocker_codes: list[CandidateExecutionBlockerCode]
    semantic_fit_evidence: SemanticFitEvidence

    @model_validator(mode="after")
    def validate_role_evidence(self) -> "ExecutionMethodRoleEvaluation":
        if self.eligible == bool(self.rejection_codes):
            raise ValueError(
                "eligible execution-method roles cannot carry rejection codes and rejected roles require them"
            )
        if self.executable_now == bool(self.execution_blocker_codes):
            raise ValueError(
                "execution-method role executableNow must agree with executionBlockerCodes"
            )
        if not self.eligible and self.executable_now:
            raise ValueError("rejected execution-method roles cannot be executable")
        return self


class CandidateEvaluation(ContractModel):
    candidate_evaluation_id: NonEmptyStr
    candidate_ref: ExecutionCandidateRef
    source_binding_refs: list[BindingRef]
    supported_role: NonEmptyStr
    eligible: StrictBool
    rejection_codes: list[CandidateRejectionCode]
    preference: CandidatePreference
    rationale: NonEmptyStr
    execution_method_role_evaluations: list[ExecutionMethodRoleEvaluation] = Field(
        default_factory=list, exclude_if=lambda value: not value
    )

    @model_validator(mode="after")
    def validate_eligibility_evidence(self) -> "CandidateEvaluation":
        if self.eligible == bool(self.rejection_codes):
            raise ValueError("eligible candidates cannot carry rejection codes and rejected candidates require them")
        if self.preference.executable_now == bool(self.preference.execution_blocker_codes):
            raise ValueError("executableNow must agree with executionBlockerCodes")
        _unique_values(
            (row.role_ref for row in self.execution_method_role_evaluations),
            "executionMethodRoleEvaluations.roleRef",
        )
        return self


class ExecutionAssignment(ContractModel):
    assignment_id: NonEmptyStr
    recipe_template_key: NonEmptyStr | None
    execution_method_role_id: NonEmptyStr | None = None
    execution_method_iteration: PositiveInt | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    execution_method_role_control: JsonObject | None = None
    role: AssignmentRole
    completion_coverage: CompletionCoverage
    objective: NonEmptyStr
    source_binding_refs: list[BindingRef]
    responsible_actor_ref: ActorRef
    executor_ref: ExecutorRef
    allowed_skill_refs: list[SkillRef]
    input_refs: list[NonEmptyStr]
    expected_artifact_types: list[NonEmptyStr]
    acceptance_criteria: list[AcceptanceCriterion]
    dependency_assignment_ids: list[NonEmptyStr]


class AssignmentPreflightOutcome(ContractModel):
    """A checked role that was never dispatched; not execution evidence."""
    assignment_id: NonEmptyStr
    failure_kind: Literal["material_unavailable", "contract_violation"]
    reason_code: NonEmptyStr
    field_paths: list[NonEmptyStr]
    input_artifact_snapshots: list[CanonicalArtifactSnapshot]
    input_scientific_state_version: PositiveInt | None
    checked_at: JsonAwareDatetime


class ExecutionDecision(ContractModel):
    execution_decision_id: NonEmptyStr
    selection_request_id: NonEmptyStr
    decision_sequence: PositiveInt
    supersedes_execution_decision_id: NonEmptyStr | None
    revision_reason_code: NonEmptyStr | None
    task_id: NonEmptyStr
    research_design_version: PositiveInt
    substage_instance_id: NonEmptyStr
    substage_instance_revision: PositiveInt
    registry_snapshot_ref: RegistrySnapshotRef
    purpose: ExecutionPurpose
    strategy: ExecutionStrategy
    multi_agent_recipe_ref: MultiAgentRecipeRef | None
    execution_method_ref: JsonObject | None = None
    candidate_evaluations: list[CandidateEvaluation]
    fallback_invoked: StrictBool
    fallback_reason_code: FallbackReasonCode | None
    preferred_candidate_evaluation_refs: list[NonEmptyStr]
    assignments: list[ExecutionAssignment]
    assignment_preflight_outcomes: list[AssignmentPreflightOutcome] = Field(
        default_factory=list, exclude_if=lambda value: not value
    )
    invocation_context: JsonObject = Field(default_factory=dict)
    completion_policy: CompletionPolicy | None
    progress_policy: ProgressPolicy | None
    result_assembly_policy: ResultAssemblyPolicy
    runtime_readiness: list[RuntimeReadinessSnapshot]
    budget_snapshot: BudgetSnapshot
    policy_version: NonEmptyStr
    rationale: NonEmptyStr
    status: ExecutionDecisionStatus
    outcome: ExecutionDecisionOutcome | None
    decided_at: JsonAwareDatetime
    closed_at: JsonAwareDatetime | None

    @model_validator(mode="after")
    def validate_decision(self) -> "ExecutionDecision":
        assignment_ids = _unique_values((row.assignment_id for row in self.assignments), "assignmentId")
        preflight_ids = _unique_values(
            (row.assignment_id for row in self.assignment_preflight_outcomes),
            "assignmentPreflightOutcomes.assignmentId",
        )
        if preflight_ids and (
            self.strategy != ExecutionStrategy.EXECUTION_METHOD
            or not preflight_ids <= assignment_ids
        ):
            raise ValueError("preflight outcomes require this method's frozen assignments")
        candidate_ids = _unique_values(
            (row.candidate_evaluation_id for row in self.candidate_evaluations),
            "candidateEvaluationId",
        )
        graph: dict[str, list[str]] = {}
        for row in self.assignments:
            if any(ref not in assignment_ids for ref in row.dependency_assignment_ids):
                raise ValueError("assignment dependency references an unknown assignment")
            graph[row.assignment_id] = row.dependency_assignment_ids
        _assert_acyclic(graph, "ExecutionAssignment dependency")
        if any(ref not in candidate_ids for ref in self.preferred_candidate_evaluation_refs):
            raise ValueError("preferred candidate reference is unknown")
        if self.purpose == ExecutionPurpose.COMPLETION:
            if self.completion_policy is None or self.progress_policy is not None:
                raise ValueError("completion purpose requires completionPolicy only")
            required = self.completion_policy.required_assignment_ids
            if not required or any(ref not in assignment_ids for ref in required):
                raise ValueError("completion required assignments must belong to the decision")
            if any(
                row.completion_coverage != CompletionCoverage.COMPLETION_CAPABLE
                for row in self.assignments
                if row.assignment_id in required
            ):
                raise ValueError("completion required assignments must be completion capable")
            reviewer_ids = {
                row.assignment_id for row in self.assignments if row.role == AssignmentRole.REVIEWER
            }
            if set(self.completion_policy.reviewer_assignment_ids) != reviewer_ids:
                raise ValueError("reviewerAssignmentIds must list every reviewer assignment")
            if (
                self.completion_policy.review_requirement == ReviewRequirement.NONE
                and reviewer_ids
            ):
                raise ValueError("reviewRequirement none cannot include reviewer assignments")
            if (
                self.completion_policy.review_requirement != ReviewRequirement.NONE
                and not reviewer_ids
            ):
                raise ValueError("reviewRequirement requires a frozen reviewer assignment")
        else:
            if self.progress_policy is None or self.completion_policy is not None:
                raise ValueError("progress purpose requires progressPolicy only")
            required = self.progress_policy.required_assignment_ids
            if not required or any(ref not in assignment_ids for ref in required):
                raise ValueError("progress required assignments must belong to the decision")
            if not any(
                row.completion_coverage == CompletionCoverage.PROGRESS_CAPABLE
                for row in self.assignments
                if row.assignment_id in required
            ):
                raise ValueError("progress requires a progress-capable required assignment")
            if self.outcome in {ExecutionDecisionOutcome.SUCCEEDED, ExecutionDecisionOutcome.PARTIAL}:
                raise ValueError("progress decision cannot succeed or partially succeed")
        if self.strategy == ExecutionStrategy.DIRECT_SKILL:
            primaries = [row for row in self.assignments if row.role == AssignmentRole.PRIMARY]
            reviewers = [row for row in self.assignments if row.role == AssignmentRole.REVIEWER]
            if len(primaries) != 1 or len(reviewers) > 1 or len(self.assignments) != 1 + len(reviewers):
                raise ValueError("direct_skill requires one primary Skill and at most one reviewer")
            assignment = primaries[0]
            if (
                assignment.recipe_template_key is not None
                or assignment.responsible_actor_ref.actor_type != "orchestrator"
                or assignment.executor_ref.executor_type != "skill"
                or assignment.allowed_skill_refs
                or assignment.dependency_assignment_ids
            ):
                raise ValueError("direct_skill assignment has an invalid actor or executor")
            if self.purpose == ExecutionPurpose.COMPLETION and reviewers:
                reviewer = reviewers[0]
                if (
                    reviewer.recipe_template_key is not None
                    or reviewer.responsible_actor_ref.actor_type != "agent"
                    or reviewer.executor_ref.executor_type != "agent"
                    or reviewer.dependency_assignment_ids != [assignment.assignment_id]
                ):
                    raise ValueError("direct_skill reviewer is invalid")
            elif self.purpose != ExecutionPurpose.COMPLETION and reviewers:
                raise ValueError("progress direct_skill cannot include a reviewer")
        elif self.strategy == ExecutionStrategy.SKILL_COMPOSITION:
            primaries = [row for row in self.assignments if row.role == AssignmentRole.PRIMARY]
            reviewers = [row for row in self.assignments if row.role == AssignmentRole.REVIEWER]
            if len(primaries) < 2 or len(reviewers) > 1 or len(self.assignments) != len(primaries) + len(reviewers):
                raise ValueError("skill_composition requires ordered primary Skills and at most one reviewer")
            previous_id: str | None = None
            for assignment in primaries:
                expected_dependencies = [] if previous_id is None else [previous_id]
                if (
                    assignment.recipe_template_key is not None
                    or assignment.responsible_actor_ref.actor_type != "orchestrator"
                    or assignment.executor_ref.executor_type != "skill"
                    or assignment.allowed_skill_refs
                    or assignment.dependency_assignment_ids != expected_dependencies
                ):
                    raise ValueError("skill_composition assignment topology is invalid")
                previous_id = assignment.assignment_id
            if self.purpose == ExecutionPurpose.COMPLETION and reviewers:
                reviewer = reviewers[0]
                if (
                    reviewer.recipe_template_key is not None
                    or reviewer.responsible_actor_ref.actor_type != "agent"
                    or reviewer.executor_ref.executor_type != "agent"
                    or reviewer.dependency_assignment_ids != [primaries[-1].assignment_id]
                ):
                    raise ValueError("skill_composition reviewer is invalid")
            elif self.purpose != ExecutionPurpose.COMPLETION and reviewers:
                raise ValueError("progress skill_composition cannot include a reviewer")
        elif self.strategy == ExecutionStrategy.EXECUTION_METHOD:
            if (
                self.execution_method_ref is None
                or len(self.assignments) < 1
                or self.purpose != ExecutionPurpose.COMPLETION
            ):
                raise ValueError("execution_method requires completion purpose, a frozen method ref, and assignments")
            method_id = self.execution_method_ref.get("methodId")
            method_version = self.execution_method_ref.get("methodVersion")
            if (
                not isinstance(method_id, str)
                or not method_id
                or not isinstance(method_version, int)
                or isinstance(method_version, bool)
                or method_version < 1
            ):
                raise ValueError("executionMethodRef is invalid")
            method_roles = [row.execution_method_role_id for row in self.assignments]
            method_role_iterations = [
                (row.execution_method_role_id, row.execution_method_iteration or 1)
                for row in self.assignments
            ]
            if (
                any(role is None for role in method_roles)
                or len(set(method_role_iterations)) != len(method_role_iterations)
                or any(row.execution_method_role_control is None for row in self.assignments)
            ):
                raise ValueError(
                    "execution_method assignments require unique role iterations and frozen role control"
                )
            for assignment in self.assignments:
                if assignment.recipe_template_key is not None:
                    raise ValueError("execution_method assignment cannot use recipeTemplateKey")
                if assignment.executor_ref.executor_type == "skill":
                    if (
                        assignment.responsible_actor_ref.actor_type != "orchestrator"
                        or assignment.allowed_skill_refs
                    ):
                        raise ValueError("execution_method Skill assignment has invalid responsibility")
                elif assignment.executor_ref.executor_type == "agent":
                    if assignment.responsible_actor_ref.actor_type != "agent":
                        raise ValueError("execution_method Agent assignment has invalid responsibility")
                else:
                    raise ValueError("execution_method assignment executor is invalid")
        elif self.strategy in {
            ExecutionStrategy.SPECIALIST_AGENT,
            ExecutionStrategy.GENERAL_RESEARCH_AGENT,
        }:
            if len(self.assignments) != 1 or self.assignments[0].role != AssignmentRole.PRIMARY:
                raise ValueError("single Agent strategy requires one primary assignment")
            if self.assignments[0].recipe_template_key is not None:
                raise ValueError("single Agent assignment cannot have recipeTemplateKey")
            assignment = self.assignments[0]
            if (
                assignment.responsible_actor_ref.actor_type != "agent"
                or assignment.executor_ref.executor_type != "agent"
            ):
                raise ValueError("single Agent strategy requires Agent responsibility and execution")
        else:
            if self.multi_agent_recipe_ref is None or len(self.assignments) < 2:
                raise ValueError("multi_agent requires a recipe and at least two assignments")
            template_keys = [row.recipe_template_key for row in self.assignments]
            if any(key is None for key in template_keys) or len(set(template_keys)) != len(template_keys):
                raise ValueError("multi_agent assignments require unique recipeTemplateKey values")
        if self.strategy != ExecutionStrategy.MULTI_AGENT and self.multi_agent_recipe_ref is not None:
            raise ValueError("non-multi-Agent strategy cannot reference a multi-Agent recipe")
        if self.strategy != ExecutionStrategy.EXECUTION_METHOD and self.execution_method_ref is not None:
            raise ValueError("non-execution-method strategy cannot reference an execution method")
        is_fallback = self.strategy == ExecutionStrategy.GENERAL_RESEARCH_AGENT
        if is_fallback != self.fallback_invoked:
            raise ValueError("fallbackInvoked must match general_research_agent strategy")
        if is_fallback != (self.fallback_reason_code is not None):
            raise ValueError("fallbackReasonCode must be present only for fallback")
        policy = self.result_assembly_policy
        if policy.mode == ResultAssemblyMode.DESIGNATED_ASSEMBLER:
            if policy.assembler_assignment_id not in assignment_ids:
                raise ValueError("designated assembler must belong to the decision")
        elif policy.assembler_assignment_id is not None:
            raise ValueError("assemblerAssignmentId is only valid for designated_assembler")
        if any(
            ref not in assignment_ids
            for requirement in policy.final_artifact_requirements
            for ref in requirement.source_assignment_ids
        ):
            raise ValueError("artifact requirement references an unknown assignment")
        if self.status == ExecutionDecisionStatus.CLOSED:
            if self.outcome is None or self.closed_at is None:
                raise ValueError("closed Decision requires outcome and closedAt")
        elif self.outcome is not None or self.closed_at is not None:
            raise ValueError("only a closed Decision can carry outcome or closedAt")
        return self


class CanonicalArtifactSnapshot(ContractModel):
    artifact_id: NonEmptyStr
    artifact_type: NonEmptyStr
    version: PositiveInt
    content_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$", strict=True)] | None = None


class WorkUnit(ContractModel):
    work_unit_id: NonEmptyStr
    idempotency_key: NonEmptyStr
    task_id: NonEmptyStr
    execution_decision_id: NonEmptyStr
    assignment_id: NonEmptyStr
    research_design_version: PositiveInt
    substage_instance_id: NonEmptyStr
    substage_instance_revision: PositiveInt
    substage_definition_id: NonEmptyStr
    substage_definition_version: PositiveInt
    registry_snapshot_ref: RegistrySnapshotRef
    responsible_actor_ref: ActorRef
    executor_ref: ExecutorRef
    instruction: NonEmptyStr
    input_artifact_refs: list[NonEmptyStr]
    input_artifact_snapshots: list[CanonicalArtifactSnapshot] = Field(default_factory=list)
    input_memory_refs: list[NonEmptyStr]
    invocation_context: JsonObject = Field(default_factory=dict)
    resolved_parameters: JsonObject | None = None
    resolved_execution_policy: JsonObject | None = None
    input_scientific_state_version: PositiveInt | None = None
    execution_method_ref: JsonObject | None = None
    execution_method_role_id: NonEmptyStr | None = None
    execution_method_iteration: PositiveInt | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    execution_method_role_control: JsonObject | None = None
    allowed_skill_refs: list[SkillRef]
    authorization_snapshot: AuthorizationSnapshot
    budget_snapshot: BudgetSnapshot
    risk_snapshot: RiskSnapshot
    expected_artifact_types: list[NonEmptyStr]
    acceptance_criteria: list[AcceptanceCriterion]
    stop_conditions: list[NonEmptyStr]
    dependency_work_unit_ids: list[NonEmptyStr]
    execution_group_id: NonEmptyStr | None
    policy_version: NonEmptyStr
    source_mode: WorkUnitSourceMode
    dispatchable: StrictBool
    status: WorkUnitStatus
    terminal_reason_code: NonEmptyStr | None
    dispatched_at: JsonAwareDatetime
    frozen_at: JsonAwareDatetime
    finished_at: JsonAwareDatetime | None

    @model_validator(mode="after")
    def validate_source_and_executor(self) -> "WorkUnit":
        if self.source_mode == WorkUnitSourceMode.LIVE:
            if not self.dispatchable:
                raise ValueError("live WorkUnit must be dispatchable")
            if self.executor_ref.executor_type == "legacy_unknown" or self.responsible_actor_ref.actor_id == "legacy_unknown":
                raise ValueError("live WorkUnit cannot use legacy_unknown responsibility")
        elif self.dispatchable:
            raise ValueError("legacy_projection WorkUnit cannot be dispatchable")
        if self.executor_ref.executor_type == "skill" and self.allowed_skill_refs:
            raise ValueError("direct Skill WorkUnit cannot carry allowedSkillRefs")
        method_fields = (
            self.execution_method_ref,
            self.execution_method_role_id,
            self.execution_method_role_control,
        )
        if any(value is not None for value in method_fields) and any(
            value is None for value in method_fields
        ):
            raise ValueError("execution method WorkUnit freeze must be complete")
        if self.execution_method_ref is None and self.execution_method_iteration is not None:
            raise ValueError("non-method WorkUnit cannot carry executionMethodIteration")
        if self.execution_method_ref is not None:
            method_id = self.execution_method_ref.get("methodId")
            method_version = self.execution_method_ref.get("methodVersion")
            if (
                not isinstance(method_id, str)
                or not method_id
                or not isinstance(method_version, int)
                or isinstance(method_version, bool)
                or method_version < 1
            ):
                raise ValueError("WorkUnit executionMethodRef is invalid")
        if self.input_artifact_snapshots:
            snapshot_ids = [row.artifact_id for row in self.input_artifact_snapshots]
            if len(set(snapshot_ids)) != len(snapshot_ids):
                raise ValueError("WorkUnit input Artifact snapshots must be unique")
            if set(snapshot_ids) != set(self.input_artifact_refs):
                raise ValueError("WorkUnit input Artifact snapshots must match inputArtifactRefs")
        active = self.status in {WorkUnitStatus.QUEUED, WorkUnitStatus.RUNNING, WorkUnitStatus.REVIEWING}
        if active and self.finished_at is not None:
            raise ValueError("non-terminal WorkUnit cannot have finishedAt")
        return self


class CapabilityCallV12(ContractModel):
    internal_call_input_snapshot: InternalCapabilityCallInput | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    capability_call_id: NonEmptyStr
    idempotency_key: NonEmptyStr
    attempt_number: PositiveInt
    work_unit_id: NonEmptyStr
    substage_instance_id: NonEmptyStr
    requesting_actor_ref: ActorRef
    capability_type: CapabilityType
    capability_id: NonEmptyStr
    runtime_binding_id: NonEmptyStr | None
    status: CapabilityCallStatus
    input_refs: list[NonEmptyStr]
    output_artifact_refs: list[NonEmptyStr]
    started_at: JsonAwareDatetime
    finished_at: JsonAwareDatetime | None

    @model_validator(mode="after")
    def validate_lifecycle(self) -> "CapabilityCallV12":
        return validate_capability_call_lifecycle(self)


class ResultCheckStatus(StrEnum):
    PENDING = "pending"
    PASSED = "passed"
    FAILED = "failed"


class ResultV1(ContractModel):
    """Durable truth for what one live WorkUnit execution produced."""

    result_id: NonEmptyStr
    work_unit_id: NonEmptyStr
    capability_call_id: NonEmptyStr | None
    storage_type: NonEmptyStr
    storage_scope: NonEmptyStr | None
    storage_ref: NonEmptyStr
    content_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$", strict=True)]
    mime_type: NonEmptyStr
    check_status: ResultCheckStatus
    check_result: JsonObject
    source_refs: list[NonEmptyStr]
    artifact_refs: list[NonEmptyStr]
    scientific_state_versions: list[PositiveInt]
    created_at: JsonAwareDatetime

    @model_validator(mode="after")
    def validate_lineage(self) -> "ResultV1":
        if len(set(self.source_refs)) != len(self.source_refs):
            raise ValueError("Result source refs must be unique")
        if len(set(self.artifact_refs)) != len(self.artifact_refs):
            raise ValueError("Result Artifact refs must be unique")
        if len(set(self.scientific_state_versions)) != len(self.scientific_state_versions):
            raise ValueError("Result ScientificState refs must be unique")
        if self.check_status != ResultCheckStatus.PASSED and self.scientific_state_versions:
            raise ValueError("only a passed Result can update ScientificState")
        return self


class ArtifactV12(ContractModel):
    artifact_id: NonEmptyStr
    work_unit_id: NonEmptyStr
    substage_instance_id: NonEmptyStr
    source_capability_call_id: NonEmptyStr | None
    artifact_type: NonEmptyStr
    artifact_state: ArtifactState
    version: PositiveInt
    source_refs: list[NonEmptyStr]
    validator_result: JsonObject
    lineage_kind: ArtifactLineageKind | None = None
    # Historical Artifacts may predate formal execution Results. New execution
    # Artifacts bind this field to the Result that was inspected and preserved.
    source_result_id: NonEmptyStr | None = None

    @model_validator(mode="after")
    def validate_json_numbers(self) -> "ArtifactV12":
        def require_finite_json(value: JsonValue) -> None:
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError("validatorResult JSON numbers must be finite")
            if isinstance(value, list):
                for item in value:
                    require_finite_json(item)
            elif isinstance(value, dict):
                for item in value.values():
                    require_finite_json(item)

        require_finite_json(self.validator_result)
        expected_kind = (
            ArtifactLineageKind.DIRECT_WORK_UNIT_OUTPUT
            if self.source_capability_call_id is None
            else ArtifactLineageKind.CAPABILITY_CALL_OUTPUT
        )
        if self.lineage_kind is None:
            self.lineage_kind = expected_kind
        elif self.lineage_kind == ArtifactLineageKind.PLANNING:
            raise ValueError("planning Artifact cannot become a live research Artifact")
        elif self.lineage_kind != expected_kind:
            raise ValueError("Artifact lineageKind must match sourceCapabilityCallId")
        return self


class ConfirmationV12(ContractModel):
    confirmation_id: NonEmptyStr
    target_type: Annotated[
        str,
        Field(
            pattern=r"^(research_design|stage_gate|substage_gate|artifact|scope_expansion|budget_expansion)$",
            strict=True,
        ),
    ]
    target_id: NonEmptyStr
    target_version: PositiveInt
    decision: Annotated[str, Field(pattern=r"^(approved|rejected|request_revision)$", strict=True)]
    actor_id: NonEmptyStr
    created_at: JsonAwareDatetime


class InteractionVerdictType(StrEnum):
    HANDOFF = "handoff"
    REVIEW = "review"
    ACCEPTANCE = "acceptance"
    REJECTION = "rejection"


class InteractionVerdictDecision(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class InteractionVerdict(ContractModel):
    verdict_type: InteractionVerdictType
    decision: InteractionVerdictDecision
    rationale_code: NonEmptyStr


class AgentInteractionRecord(ContractModel):
    interaction_id: NonEmptyStr
    idempotency_key: NonEmptyStr
    task_id: NonEmptyStr
    execution_decision_id: NonEmptyStr
    source_assignment_id: NonEmptyStr
    source_work_unit_id: NonEmptyStr
    target_assignment_id: NonEmptyStr | None
    target_work_unit_id: NonEmptyStr | None
    interaction_type: Annotated[
        str,
        Field(
            pattern=(
                r"^(handoff_requested|handoff_completed|challenge_raised|response_provided|"
                r"review_submitted|acceptance_recorded|target_output_reuse_accepted|rejection_recorded)$"
            ),
            strict=True,
        ),
    ]
    input_artifact_refs: list[NonEmptyStr]
    output_artifact_refs: list[NonEmptyStr]
    verdict: InteractionVerdict | None
    summary: NonEmptyStr
    created_at: JsonAwareDatetime

    @model_validator(mode="after")
    def validate_target_pair(self) -> "AgentInteractionRecord":
        if (self.target_assignment_id is None) != (self.target_work_unit_id is None):
            raise ValueError("target assignment and WorkUnit refs must be present together")
        evidence_matrix = {
            "handoff_requested": (
                InteractionVerdictType.HANDOFF,
                {InteractionVerdictDecision.APPROVED, InteractionVerdictDecision.INSUFFICIENT_EVIDENCE},
            ),
            "handoff_completed": (
                InteractionVerdictType.HANDOFF,
                {InteractionVerdictDecision.APPROVED, InteractionVerdictDecision.INSUFFICIENT_EVIDENCE},
            ),
            "review_submitted": (
                InteractionVerdictType.REVIEW,
                set(InteractionVerdictDecision),
            ),
            "acceptance_recorded": (
                InteractionVerdictType.ACCEPTANCE,
                {InteractionVerdictDecision.APPROVED},
            ),
            "target_output_reuse_accepted": (
                InteractionVerdictType.ACCEPTANCE,
                {InteractionVerdictDecision.APPROVED},
            ),
            "rejection_recorded": (
                InteractionVerdictType.REJECTION,
                {InteractionVerdictDecision.REJECTED, InteractionVerdictDecision.INSUFFICIENT_EVIDENCE},
            ),
        }
        requirement = evidence_matrix.get(self.interaction_type)
        if requirement is None:
            return self
        has_artifact_evidence = bool(self.input_artifact_refs or self.output_artifact_refs)
        if not has_artifact_evidence and self.verdict is None:
            raise ValueError("interaction requires Artifact evidence or a structured verdict")
        if self.verdict is not None:
            expected_type, allowed_decisions = requirement
            if (
                self.verdict.verdict_type != expected_type
                or self.verdict.decision not in allowed_decisions
            ):
                raise ValueError(f"{self.interaction_type} has an incompatible structured verdict")
        return self


class DispatchRecord(ContractModel):
    dispatch_id: NonEmptyStr
    task_id: NonEmptyStr
    execution_decision_id: NonEmptyStr
    work_unit_id: NonEmptyStr
    idempotency_key: NonEmptyStr
    execution_namespace: NonEmptyStr | None = None
    status: DispatchStatus
    attempt_count: NonNegativeInt
    lease_owner: NonEmptyStr | None
    lease_expires_at: JsonAwareDatetime | None
    authorization_task_revision: NonNegativeInt | None
    remote_run_id: NonEmptyStr | None
    next_attempt_at: JsonAwareDatetime | None
    last_error_code: NonEmptyStr | None
    last_failure_at: JsonAwareDatetime | None = None
    created_at: JsonAwareDatetime
    updated_at: JsonAwareDatetime

    @model_validator(mode="after")
    def validate_physical_shape(self) -> "DispatchRecord":
        if (self.lease_owner is None) != (self.lease_expires_at is None):
            raise ValueError("leaseOwner and leaseExpiresAt must be present together")
        if self.updated_at < self.created_at:
            raise ValueError("updatedAt cannot precede createdAt")
        return self

    def assert_pending_insert(self) -> None:
        if (
            self.execution_namespace is None
            or self.status != DispatchStatus.PENDING
            or self.attempt_count != 0
            or self.lease_owner is not None
            or self.lease_expires_at is not None
            or self.authorization_task_revision is not None
            or self.remote_run_id is not None
            or self.next_attempt_at is not None
            or self.last_error_code is not None
            or self.last_failure_at is not None
        ):
            raise ValueError("T01 only permits namespaced pristine pending DispatchRecord inserts")


class LegacySubstageProjection(ContractModel):
    substage_instance_id: NonEmptyStr
    stage_key: StageKey
    substage_key: NonEmptyStr
    sequence: NonNegativeInt


class LegacyWorkUnitProjection(ContractModel):
    work_unit_id: NonEmptyStr
    source_capability_call_id: NonEmptyStr | None
    artifact_id: NonEmptyStr | None
    substage_instance_id: NonEmptyStr | None
    responsible_actor_ref: ActorRef
    executor_ref: ExecutorRef
    source_mode: WorkUnitSourceMode
    dispatchable: StrictBool

    @model_validator(mode="after")
    def validate_projection_only(self) -> "LegacyWorkUnitProjection":
        if self.source_mode != WorkUnitSourceMode.LEGACY_PROJECTION or self.dispatchable:
            raise ValueError("legacy WorkUnit projection can never be executable")
        return self


class LegacyTaskProjection(ContractModel):
    task_id: NonEmptyStr
    research_design_ref: ResearchDesignRef
    substage_instances: list[LegacySubstageProjection]
    work_units: list[LegacyWorkUnitProjection]
    collaboration_nodes: list[JsonObject]

    @model_validator(mode="after")
    def validate_read_only_projection(self) -> "LegacyTaskProjection":
        if self.collaboration_nodes:
            raise ValueError("legacy projection cannot create live collaboration nodes")
        if any(
            row.source_mode != WorkUnitSourceMode.LEGACY_PROJECTION or row.dispatchable
            for row in self.work_units
        ):
            raise ValueError("legacy projection WorkUnits can never be dispatchable")
        return self


class FinalResultAuthority(ContractModel):
    mode: Annotated[str, Field(pattern=r"^(assembled_artifact|artifact_set)$", strict=True)]
    research_design_version: PositiveInt
    result_artifact_refs: list[NonEmptyStr]
    source_artifacts: list[CanonicalArtifactSnapshot]
    assembled_at: JsonAwareDatetime
    # The authority is only complete when it names the validated user-facing
    # FinalResult.  ``None`` is retained for historical, non-terminal records.
    final_result_id: NonEmptyStr | None = None
    integrity_contract_version: Annotated[
        str,
        Field(
            pattern=r"^(legacy_v0|scientific_completion_integrity_v[123])$",
            strict=True,
        ),
    ]

    @model_validator(mode="after")
    def validate_identity(self, info: ValidationInfo) -> "FinalResultAuthority":
        if not self.result_artifact_refs and self.final_result_id is None:
            raise ValueError("final Result requires at least one result Artifact")
        if len(set(self.result_artifact_refs)) != len(self.result_artifact_refs):
            raise ValueError("final Result Artifact refs must be unique")
        source_ids = [row.artifact_id for row in self.source_artifacts]
        if len(set(source_ids)) != len(source_ids):
            raise ValueError("final Result source Artifact snapshots must be unique")
        if set(self.result_artifact_refs) & set(source_ids):
            raise ValueError("final Result source snapshots cannot duplicate result Artifacts")
        if (
            self.integrity_contract_version in {
                "scientific_completion_integrity_v1",
                "scientific_completion_integrity_v2",
                "scientific_completion_integrity_v3",
            }
            and self.final_result_id is None
        ):
            raise ValueError("scientific completion integrity requires a FinalResult")
        if (
            self.integrity_contract_version == "legacy_v0"
            and not bool((info.context or {}).get(READ_ONLY_LEGACY_FINAL_RESULT_AUTHORITY))
        ):
            raise ValueError("legacy_v0 Final Result authority is read-only")
        return self


class CompletionDisposition(StrEnum):
    FULL = "full"
    PARTIAL = "partial"
    DEGRADED = "degraded"
    EVIDENCE_GAP = "evidence_gap"


class ScientificStateV1(ContractModel):
    """Immutable, minimal state snapshot used by finalization.

    This deliberately contains no planner topology or executor selection.  A
    changed fact is represented by a new ``scientificStateVersion``.
    """

    scientific_state_version: PositiveInt
    research_design_version: PositiveInt
    scientific_facts: JsonObject
    execution_state: JsonObject
    source_refs: list[NonEmptyStr]
    created_at: JsonAwareDatetime


class CompletionRequirementAssessment(ContractModel):
    fulfillment_status: Literal[
        "planned_work", "not_applicable", "reuse_pending", "reuse_accepted"
    ] | None = Field(default=None, exclude_if=lambda value: value is None)
    completion_requirement_id: NonEmptyStr
    target_output_ref: NonEmptyStr
    status: Annotated[
        str, Field(pattern=r"^(satisfied|unmet|unresolved)$", strict=True)
    ]
    coverage_target_id: NonEmptyStr | None = None
    acceptance_criterion_refs: list[NonEmptyStr] = Field(default_factory=list)
    gaps: list[NonEmptyStr] = Field(default_factory=list)
    uncertainty: list[NonEmptyStr] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_assessment_identity(self) -> "CompletionRequirementAssessment":
        if len(set(self.acceptance_criterion_refs)) != len(self.acceptance_criterion_refs):
            raise ValueError("CompletionRequirementAssessment criterion refs must be unique")
        return self


class CompletionDecision(ContractModel):
    completion_decision_id: NonEmptyStr
    scientific_state_version: PositiveInt
    disposition: CompletionDisposition
    source_refs: list[NonEmptyStr]
    decided_at: JsonAwareDatetime
    completion_authority_version: (
        Annotated[str, Field(pattern=r"^project_completion_v[12]$", strict=True)] | None
    ) = None
    research_design_ref: ResearchDesignRef | None = None
    can_complete: bool | None = None
    requirement_assessments: list[CompletionRequirementAssessment] = Field(default_factory=list)
    gaps: list[NonEmptyStr] = Field(default_factory=list)
    uncertainty: list[NonEmptyStr] = Field(default_factory=list)
    limitations: list[NonEmptyStr] = Field(default_factory=list)
    rationale: NonEmptyStr | None = None

    round_end: RoundEndEvidence | None = Field(default=None, exclude_if=lambda value: value is None)

    @model_validator(mode="after")
    def validate_completion_authority(self) -> "CompletionDecision":
        return validate_completion_decision_authority(self)


class FinalResultValidationOutcome(ContractModel):
    contract_version: Annotated[
        str, Field(pattern=r"^scientific_completion_integrity_v[123]$", strict=True)
    ]
    validator_ref: Annotated[
        str, Field(pattern=r"^core:scientific_completion_integrity:v[123]$", strict=True)
    ]
    status: Annotated[str, Field(pattern=r"^(passed|failed|unknown)$", strict=True)]
    check_ids: list[NonEmptyStr]
    validated_at: JsonAwareDatetime

    @model_validator(mode="after")
    def validate_checks(self) -> "FinalResultValidationOutcome":
        if len(set(self.check_ids)) != len(self.check_ids):
            raise ValueError("Final Result validation checks must be unique")
        expected_validator_version = f"v{self.contract_version.rsplit('_v', 1)[-1]}"
        if self.validator_ref.rsplit(":", 1)[-1] != expected_validator_version:
            raise ValueError("Final Result validation contract and validator versions must match")
        return self


class FinalResult(ContractModel):
    final_result_id: NonEmptyStr
    completion_decision_id: NonEmptyStr
    scientific_state_version: PositiveInt
    disposition: CompletionDisposition

    # Historical v1/v2 presentation fields. New v3 finalization deliberately
    # leaves them empty instead of teaching core code how to interpret
    # domain-specific ScientificState fact names.
    direct_answer: NonEmptyStr | None = None
    key_findings: list[NonEmptyStr] = Field(default_factory=list)
    evidence_refs: list[NonEmptyStr] = Field(default_factory=list)

    # v3 canonical delivery. ``scientificResult`` is an exact copy of the
    # accepted ScientificState scientificFacts. ``scientificSourceRefs`` keeps
    # its provenance separate from completion Artifacts and from logical
    # Evidence. No field here becomes a second ScientificState authority.
    scientific_result: JsonObject = Field(default_factory=dict)
    scientific_source_refs: list[NonEmptyStr] = Field(default_factory=list)

    uncertainty: list[NonEmptyStr]
    gaps: list[NonEmptyStr]
    limitations: list[NonEmptyStr]

    # Historical v2 snapshot retained for read compatibility only.
    accepted_scientific_facts: JsonObject = Field(default_factory=dict)
    validated_at: JsonAwareDatetime
    validation_outcome: FinalResultValidationOutcome | None = None


class ResearchTaskV12(ContractModel):
    task_id: NonEmptyStr
    revision: NonNegativeInt
    status: TaskStatus
    execution_mode: TaskExecutionMode
    pending_execution_mode: TaskExecutionMode | None
    execution_policy_version: NonEmptyStr
    active_research_design_ref: ResearchDesignRef | None
    registry_snapshot_ref: RegistrySnapshotRef
    planning_attempts: list[PlanningAttempt]
    research_design_versions: list[ResearchDesignVersion]
    reentry_execution_instances: list[ReentryExecutionInstance] = Field(default_factory=list)
    substage_runtime_states: list[SubstageRuntimeState]
    execution_decisions: list[ExecutionDecision]
    work_units: list[WorkUnit]
    capability_calls: list[CapabilityCallV12]
    results: list[ResultV1] = Field(default_factory=list)
    artifacts: list[ArtifactV12]
    confirmations: list[ConfirmationV12]
    agent_interaction_records: list[AgentInteractionRecord]
    progress_projection: JsonObject | None = None
    final_result_authority: FinalResultAuthority | None = None
    scientific_states: list[ScientificStateV1] = Field(default_factory=list)
    completion_decisions: list[CompletionDecision] = Field(default_factory=list)
    final_results: list[FinalResult] = Field(default_factory=list)
    started_at: JsonAwareDatetime | None
    finished_at: JsonAwareDatetime | None

    @model_validator(mode="after")
    def validate_aggregate_identity(self, info: ValidationInfo) -> "ResearchTaskV12":
        planning_attempt_ids = _unique_values(
            (row.planning_attempt_id for row in self.planning_attempts), "planningAttemptId"
        )
        design_refs = _unique_values(
            ((row.research_design_id, row.version) for row in self.research_design_versions),
            "ResearchDesignVersion identity",
        )
        _unique_values(
            ((row.substage_instance_id, row.instance_revision) for row in self.substage_runtime_states),
            "SubstageRuntimeState identity",
        )
        _unique_values(
            (row.reentry_fingerprint for row in self.reentry_execution_instances),
            "ReentryExecutionInstance fingerprint",
        )
        _unique_values((row.execution_decision_id for row in self.execution_decisions), "executionDecisionId")
        _unique_values((row.work_unit_id for row in self.work_units), "workUnitId")
        _unique_values((row.idempotency_key for row in self.work_units), "WorkUnit.idempotencyKey")
        _unique_values((row.assignment_id for row in self.work_units), "WorkUnit.assignmentId")
        _unique_values((row.capability_call_id for row in self.capability_calls), "capabilityCallId")
        _unique_values((row.idempotency_key for row in self.capability_calls), "CapabilityCall.idempotencyKey")
        _unique_values((row.result_id for row in self.results), "resultId")
        _unique_values((row.artifact_id for row in self.artifacts), "artifactId")
        _unique_values((row.confirmation_id for row in self.confirmations), "confirmationId")
        _unique_values((row.interaction_id for row in self.agent_interaction_records), "interactionId")
        _unique_values((row.scientific_state_version for row in self.scientific_states), "scientificStateVersion")
        _unique_values((row.completion_decision_id for row in self.completion_decisions), "completionDecisionId")
        _unique_values((row.final_result_id for row in self.final_results), "finalResultId")
        _unique_values(
            (row.idempotency_key for row in self.agent_interaction_records),
            "AgentInteractionRecord.idempotencyKey",
        )
        if self.active_research_design_ref is not None:
            key = (self.active_research_design_ref.research_design_id, self.active_research_design_ref.version)
            if key not in design_refs:
                raise ValueError("activeResearchDesignRef must reference a persisted design version")
        lineage_ids = {row.research_design_id for row in self.research_design_versions}
        if len(lineage_ids) > 1:
            raise ValueError("ResearchTaskV12 can contain only one ResearchDesign lineage")
        designs_by_version = {row.version: row for row in self.research_design_versions}
        confirmed_designs = [
            row for row in self.research_design_versions if row.status == ResearchDesignStatus.CONFIRMED
        ]
        if len(confirmed_designs) > 1:
            raise ValueError("ResearchTaskV12 can contain only one confirmed Design")
        if self.active_research_design_ref is not None:
            active_design = next(
                row
                for row in self.research_design_versions
                if (row.research_design_id, row.version)
                == (
                    self.active_research_design_ref.research_design_id,
                    self.active_research_design_ref.version,
                )
            )
            if active_design.status != ResearchDesignStatus.CONFIRMED:
                raise ValueError("activeResearchDesignRef must reference a confirmed Design")
        instances_by_design_ref: dict[tuple[int, str, int], SubstageInstance] = {}
        frozen_instance_snapshots: dict[tuple[str, int], dict[str, object]] = {}
        for design in self.research_design_versions:
            for instance in design.instances:
                instance_key = (instance.substage_instance_id, instance.instance_revision)
                snapshot = instance.model_dump(mode="json", by_alias=True)
                prior_snapshot = frozen_instance_snapshots.setdefault(instance_key, snapshot)
                if prior_snapshot != snapshot:
                    raise ValueError(
                        "a reused instanceId and instanceRevision must retain its immutable snapshot; "
                        "increment instanceRevision for semantic changes"
                    )
                instances_by_design_ref[(design.version, *instance_key)] = instance
        reentry_by_ref: dict[tuple[int, str, int], ReentryExecutionInstance] = {}
        for materialization in self.reentry_execution_instances:
            design = designs_by_version.get(materialization.research_design_version)
            if design is None:
                raise ValueError("re-entry instance references an unknown Design version")
            template = next(
                (
                    row
                    for row in design.instances
                    if row.substage_instance_id
                    == materialization.template_instance_ref.substage_instance_id
                    and row.instance_revision
                    == materialization.template_instance_ref.instance_revision
                ),
                None,
            )
            if template is None:
                raise ValueError("re-entry instance template is outside the confirmed Design")
            instance = materialization.instance
            if (
                instance.substage_definition_id != template.substage_definition_id
                or instance.substage_definition_version != template.substage_definition_version
                or instance.stage_key != template.stage_key
                or instance.repeatable_reentry_authority != template.repeatable_reentry_authority
            ):
                raise ValueError("re-entry instance does not preserve its Definition template")
            instance_key = (instance.substage_instance_id, instance.instance_revision)
            if instance_key in frozen_instance_snapshots:
                raise ValueError("re-entry instance identity collides with existing history")
            frozen_instance_snapshots[instance_key] = instance.model_dump(
                mode="json", by_alias=True
            )
            design_key = (materialization.research_design_version, *instance_key)
            instances_by_design_ref[design_key] = instance
            reentry_by_ref[design_key] = materialization
        all_instance_refs = set(instances_by_design_ref)
        for materialization in self.reentry_execution_instances:
            design = designs_by_version[materialization.research_design_version]
            template = next(
                row
                for row in design.instances
                if row.substage_instance_id
                == materialization.template_instance_ref.substage_instance_id
                and row.instance_revision
                == materialization.template_instance_ref.instance_revision
            )
            previous_key = (
                materialization.research_design_version,
                materialization.previous_relevant_instance_ref.substage_instance_id,
                materialization.previous_relevant_instance_ref.instance_revision,
            )
            if previous_key not in all_instance_refs:
                raise ValueError("re-entry lineage references an unknown previous instance")
            previous_runtime = next(
                (
                    row
                    for row in self.substage_runtime_states
                    if row.substage_instance_id == previous_key[1]
                    and row.instance_revision == previous_key[2]
                ),
                None,
            )
            if previous_runtime is None or previous_runtime.status not in {
                SubstageRuntimeStatus.COMPLETED,
                SubstageRuntimeStatus.FAILED,
                SubstageRuntimeStatus.CANCELLED,
                SubstageRuntimeStatus.SKIPPED,
            }:
                raise ValueError("re-entry requires a terminal previous instance")
            state = next(
                (
                    row
                    for row in self.scientific_states
                    if row.scientific_state_version
                    == materialization.triggering_scientific_state_version
                    and row.research_design_version
                    == materialization.research_design_version
                ),
                None,
            )
            if state is None or not set(materialization.triggering_state_source_refs) <= set(
                state.source_refs
            ):
                raise ValueError("re-entry lineage references an invalid ScientificState")
            decision = next(
                (
                    row
                    for row in self.execution_decisions
                    if row.execution_decision_id
                    == materialization.triggering_execution_decision_id
                ),
                None,
            )
            if decision is None or decision.status != ExecutionDecisionStatus.CLOSED:
                raise ValueError("re-entry lineage requires a closed triggering Decision")
            trigger_instance = instances_by_design_ref.get(
                (
                    materialization.research_design_version,
                    decision.substage_instance_id,
                    decision.substage_instance_revision,
                )
            )
            authority = materialization.instance.repeatable_reentry_authority
            if (
                trigger_instance is None
                or decision.outcome
                not in {
                    ExecutionDecisionOutcome.SUCCEEDED,
                    ExecutionDecisionOutcome.PARTIAL,
                    ExecutionDecisionOutcome.PROGRESSED,
                }
                or authority is None
                or trigger_instance.substage_definition_id
                not in authority.trigger_definition_ids
            ):
                raise ValueError("re-entry lineage is not authorized by its Definition")
            expected_fingerprint = repeatable_reentry_fingerprint(
                design=design,
                template=template,
                scientific_state_version=materialization.triggering_scientific_state_version,
            )
            if materialization.reentry_fingerprint != expected_fingerprint:
                raise ValueError("re-entry fingerprint does not match its authority inputs")
            trigger_work_ids = {
                row.work_unit_id
                for row in self.work_units
                if row.execution_decision_id == decision.execution_decision_id
                and row.status == WorkUnitStatus.COMPLETED
            }
            validated_trigger_refs = {
                row.artifact_id
                for row in self.artifacts
                if row.work_unit_id in trigger_work_ids
                and row.artifact_state == ArtifactState.USABLE
                and row.artifact_id in state.source_refs
            }
            if not set(materialization.triggering_state_source_refs) <= validated_trigger_refs:
                raise ValueError("re-entry trigger is not a validated result in ScientificState")
            previous_materialization = reentry_by_ref.get(previous_key)
            previous_state_version = (
                previous_materialization.triggering_scientific_state_version
                if previous_materialization is not None
                else 0
            )
            if (
                materialization.triggering_scientific_state_version
                <= previous_state_version
            ):
                raise ValueError("re-entry requires a newer ScientificState")
        for design in self.research_design_versions:
            if design.coverage_report is not None:
                if design.coverage_report.planning_attempt_id not in planning_attempt_ids:
                    raise ValueError("coverage report references an unknown PlanningAttempt")
            if design.status == ResearchDesignStatus.CONFIRMED:
                confirmation = next(
                    (
                        row
                        for row in self.confirmations
                        if row.confirmation_id == design.confirmation_ref
                    ),
                    None,
                )
                direct_confirmation = (
                    confirmation is not None
                    and confirmation.target_type == "research_design"
                    and confirmation.target_id == design.research_design_id
                    and confirmation.target_version == design.version
                    and confirmation.decision == "approved"
                )
                inherited_confirmation = (
                    bool((info.context or {}).get(READ_ONLY_LEGACY_RESEARCH_DESIGN))
                    and confirmation is not None
                    and design.supersedes_version is not None
                    and confirmation.target_type == "research_design"
                    and confirmation.target_id == design.research_design_id
                    and confirmation.target_version == design.supersedes_version
                    and confirmation.decision == "approved"
                    and (parent := designs_by_version.get(design.supersedes_version)) is not None
                    and parent.confirmation_ref == design.confirmation_ref
                )
                if not (direct_confirmation or inherited_confirmation):
                    raise ValueError("confirmed Design requires its matching approved Confirmation")
        for state in self.substage_runtime_states:
            if (state.substage_instance_id, state.instance_revision) not in frozen_instance_snapshots:
                raise ValueError("SubstageRuntimeState references an unknown instance revision")
        decisions = {row.execution_decision_id: row for row in self.execution_decisions}
        active_decision_keys = (
            (row.selection_request_id, row.decision_sequence)
            for row in self.execution_decisions
            if row.status in {ExecutionDecisionStatus.SELECTED, ExecutionDecisionStatus.DISPATCHED}
        )
        _unique_values(active_decision_keys, "active ExecutionDecision selectionRequestId and decisionSequence")
        for decision in self.execution_decisions:
            design = designs_by_version.get(decision.research_design_version)
            if design is None:
                raise ValueError("ExecutionDecision references an unknown Design version")
            if not (
                _has_current_execution_authority(decision.status, design.status)
                or _is_terminal_decision_history(decision.status, design.status)
            ):
                raise ValueError(
                    "non-terminal ExecutionDecision requires the current confirmed Design; "
                    "terminal history may reference only a confirmed or superseded Design"
                )
            if (
                decision.research_design_version,
                decision.substage_instance_id,
                decision.substage_instance_revision,
            ) not in instances_by_design_ref:
                raise ValueError("ExecutionDecision references an unknown Design instance revision")
        work_units = {row.work_unit_id: row for row in self.work_units}
        for work_unit in self.work_units:
            decision = decisions.get(work_unit.execution_decision_id)
            if decision is None:
                raise ValueError("WorkUnit references an unknown ExecutionDecision")
            assignment = next(
                (row for row in decision.assignments if row.assignment_id == work_unit.assignment_id),
                None,
            )
            if assignment is None:
                raise ValueError("WorkUnit references an unknown ExecutionAssignment")
            if any(row.assignment_id == work_unit.assignment_id
                   and row.failure_kind == "contract_violation"
                   for row in decision.assignment_preflight_outcomes):
                raise ValueError("a preflight outcome cannot have a WorkUnit")
            if (
                work_unit.research_design_version != decision.research_design_version
                or work_unit.substage_instance_id != decision.substage_instance_id
                or work_unit.substage_instance_revision != decision.substage_instance_revision
            ):
                raise ValueError("WorkUnit identity does not match its ExecutionDecision")
            design = designs_by_version[decision.research_design_version]
            if decision.status in {ExecutionDecisionStatus.PROPOSED, ExecutionDecisionStatus.SELECTED}:
                raise ValueError("a proposed or selected Decision cannot own a WorkUnit")
            if work_unit.status in _ACTIVE_WORK_UNIT_STATUSES and not (
                decision.status == ExecutionDecisionStatus.DISPATCHED
                and _has_current_execution_authority(decision.status, design.status)
            ):
                raise ValueError(
                    "an active WorkUnit requires a dispatched Decision with the current confirmed Design"
                )
            instance = instances_by_design_ref[
                (
                    decision.research_design_version,
                    work_unit.substage_instance_id,
                    work_unit.substage_instance_revision,
                )
            ]
            if (
                work_unit.substage_definition_id != instance.substage_definition_id
                or work_unit.substage_definition_version != instance.substage_definition_version
            ):
                raise ValueError("WorkUnit Definition does not match its SubstageInstance")
            if (
                work_unit.responsible_actor_ref != assignment.responsible_actor_ref
                or work_unit.executor_ref != assignment.executor_ref
                or work_unit.allowed_skill_refs != assignment.allowed_skill_refs
            ):
                raise ValueError("WorkUnit responsibility must match its frozen Assignment")
            if any(ref not in work_units for ref in work_unit.dependency_work_unit_ids):
                raise ValueError("WorkUnit dependency references an unknown WorkUnit")
        _assert_acyclic(
            {row.work_unit_id: row.dependency_work_unit_ids for row in self.work_units},
            "WorkUnit dependency",
        )
        active_work_unit_statuses = {
            WorkUnitStatus.QUEUED,
            WorkUnitStatus.RUNNING,
            WorkUnitStatus.REVIEWING,
        }
        active_decision_instance_refs = (
            (
                decision.research_design_version,
                decision.substage_instance_id,
                decision.substage_instance_revision,
            )
            for decision in self.execution_decisions
            if decision.status == ExecutionDecisionStatus.SELECTED
            or (
                decision.status == ExecutionDecisionStatus.DISPATCHED
                and any(
                    work_unit.execution_decision_id == decision.execution_decision_id
                    and work_unit.status in active_work_unit_statuses
                    for work_unit in self.work_units
                )
            )
        )
        _unique_values(active_decision_instance_refs, "active ExecutionDecision instance")
        capability_calls = {row.capability_call_id: row for row in self.capability_calls}
        validate_capability_call_work_units(self.capability_calls, work_units)
        results = {row.result_id: row for row in self.results}
        state_versions = {row.scientific_state_version for row in self.scientific_states}
        for result in self.results:
            work_unit = work_units.get(result.work_unit_id)
            if work_unit is None:
                raise ValueError("Result references an unknown WorkUnit")
            if work_unit.source_mode != WorkUnitSourceMode.LIVE:
                raise ValueError("legacy Result cannot enter a live research aggregate")
            if result.capability_call_id is not None:
                call = capability_calls.get(result.capability_call_id)
                if call is None or call.work_unit_id != result.work_unit_id:
                    raise ValueError("Result source CapabilityCall must belong to its WorkUnit")
            if any(version not in state_versions for version in result.scientific_state_versions):
                raise ValueError("Result references an unknown ScientificState")
        artifacts = {row.artifact_id: row for row in self.artifacts}
        for artifact in self.artifacts:
            work_unit = work_units.get(artifact.work_unit_id)
            if work_unit is None:
                raise ValueError("Artifact references an unknown WorkUnit")
            if work_unit.source_mode != WorkUnitSourceMode.LIVE:
                raise ValueError("legacy Artifact cannot enter a live research aggregate")
            if artifact.substage_instance_id != work_unit.substage_instance_id:
                raise ValueError("Artifact Substage does not match its WorkUnit")
            if artifact.source_capability_call_id is not None:
                call = capability_calls.get(artifact.source_capability_call_id)
                if (
                    call is None
                    or call.work_unit_id != artifact.work_unit_id
                    or call.substage_instance_id != artifact.substage_instance_id
                ):
                    raise ValueError("Artifact source CapabilityCall must belong to its WorkUnit")
            if artifact.source_result_id is not None:
                source_result = results.get(artifact.source_result_id)
                if (
                    source_result is None
                    or source_result.work_unit_id != artifact.work_unit_id
                    or source_result.capability_call_id != artifact.source_capability_call_id
                    or artifact.artifact_id not in source_result.artifact_refs
                ):
                    raise ValueError("Artifact source Result must belong to its WorkUnit and CapabilityCall")
        for result in self.results:
            for artifact_ref in result.artifact_refs:
                artifact = artifacts.get(artifact_ref)
                if artifact is None or artifact.source_result_id != result.result_id:
                    raise ValueError("Result references an unknown or unrelated Artifact")
        for call in self.capability_calls:
            if any(ref not in artifacts for ref in call.output_artifact_refs):
                raise ValueError("CapabilityCall references an unknown output Artifact")
        if self.final_result_authority is not None:
            authority = self.final_result_authority
            if authority.research_design_version not in designs_by_version:
                raise ValueError("final Result references an unknown Design version")
            if any(ref not in artifacts for ref in authority.result_artifact_refs):
                raise ValueError("final Result references an unknown result Artifact")
            for snapshot in authority.source_artifacts:
                artifact = artifacts.get(snapshot.artifact_id)
                if artifact is None:
                    raise ValueError("final Result references an unknown source Artifact")
                if artifact.artifact_type != snapshot.artifact_type:
                    raise ValueError("final Result source Artifact type is immutable")
            if authority.final_result_id is not None and authority.final_result_id not in {
                row.final_result_id for row in self.final_results
            }:
                raise ValueError("final Result authority references an unknown FinalResult")
        completion_ids = {row.completion_decision_id for row in self.completion_decisions}
        for decision in self.completion_decisions:
            if decision.scientific_state_version not in state_versions:
                raise ValueError("CompletionDecision references an unknown ScientificState")
            if decision.completion_authority_version is None:
                continue
            if decision.research_design_ref is None:
                raise ValueError("project CompletionDecision requires a ResearchDesignRef")
            decision_design = next(
                (
                    row
                    for row in self.research_design_versions
                    if row.research_design_id == decision.research_design_ref.research_design_id
                    and row.version == decision.research_design_ref.version
                ),
                None,
            )
            if decision_design is None:
                raise ValueError("CompletionDecision references an unknown ResearchDesign")
            decision_state = next(
                (
                    row
                    for row in self.scientific_states
                    if row.scientific_state_version == decision.scientific_state_version
                ),
                None,
            )
            if (
                decision_state is None
                or decision_state.research_design_version != decision_design.version
            ):
                raise ValueError("CompletionDecision Design and ScientificState do not match")
            from app.modules.research_round_end import validate_round_end_identity

            validate_round_end_identity(self, decision, decision_design, decision_state)
            requirement_ids = {
                row.completion_requirement_id
                for row in decision_design.project_completion_requirements
            }
            assessment_ids = {
                row.completion_requirement_id
                for row in decision.requirement_assessments
            }
            if assessment_ids != requirement_ids:
                raise ValueError(
                    "CompletionDecision requirement assessments must cover the ResearchDesign requirements exactly"
                )
        for result in self.final_results:
            if result.completion_decision_id not in completion_ids:
                raise ValueError("FinalResult references an unknown CompletionDecision")
            if result.scientific_state_version not in state_versions:
                raise ValueError("FinalResult references an unknown ScientificState")
        if (
            self.final_result_authority is not None
            and self.final_result_authority.integrity_contract_version
            in {
                "scientific_completion_integrity_v1",
                "scientific_completion_integrity_v2",
                "scientific_completion_integrity_v3",
            }
        ):
            from app.modules.scientific_completion_integrity import (
                evaluate_final_result_integrity,
            )

            evaluation = evaluate_final_result_integrity(
                self.model_dump(mode="json", by_alias=True),
                require_persisted_outcome=True,
            )
            if not evaluation["passed"]:
                raise ValueError(
                    "Final Result integrity validation failed: "
                    + ",".join(evaluation["errorCodes"])
                )
        if self.status is TaskStatus.COMPLETED:
            authority = self.final_result_authority
            if authority is None or authority.final_result_id is None:
                raise ValueError("completed Task requires a validated Final Result authority")
            if (
                authority.integrity_contract_version not in {
                    "scientific_completion_integrity_v1",
                    "scientific_completion_integrity_v2",
                    "scientific_completion_integrity_v3",
                }
                and not bool((info.context or {}).get(READ_ONLY_LEGACY_FINAL_RESULT_AUTHORITY))
            ):
                raise ValueError(
                    "completed Task requires a validated scientific completion Final Result authority"
                )
            result = next(
                (row for row in self.final_results if row.final_result_id == authority.final_result_id),
                None,
            )
            if result is None or result.validated_at is None:
                raise ValueError("completed Task requires a validated Final Result")
        for state in self.substage_runtime_states:
            if state.active_execution_decision_id is not None:
                decision = decisions.get(state.active_execution_decision_id)
                if (
                    decision is None
                    or decision.substage_instance_id != state.substage_instance_id
                    or decision.substage_instance_revision != state.instance_revision
                ):
                    raise ValueError(
                        "SubstageRuntimeState active ExecutionDecision must target the same instance revision"
                    )
            for work_unit_id in state.work_unit_ids:
                work_unit = work_units.get(work_unit_id)
                if (
                    work_unit is None
                    or work_unit.substage_instance_id != state.substage_instance_id
                    or work_unit.substage_instance_revision != state.instance_revision
                ):
                    raise ValueError(
                        "SubstageRuntimeState WorkUnit must target the same instance revision"
                    )
            for artifact_ref in state.artifact_refs:
                artifact = artifacts.get(artifact_ref)
                work_unit = work_units.get(artifact.work_unit_id) if artifact is not None else None
                if (
                    artifact is None
                    or work_unit is None
                    or artifact.substage_instance_id != state.substage_instance_id
                    or work_unit.substage_instance_revision != state.instance_revision
                ):
                    raise ValueError(
                        "SubstageRuntimeState Artifact must target the same instance revision"
                    )
        for interaction in self.agent_interaction_records:
            decision = decisions.get(interaction.execution_decision_id)
            source_work_unit = work_units.get(interaction.source_work_unit_id)
            if decision is None or source_work_unit is None:
                raise ValueError("AgentInteractionRecord references an unknown Decision or WorkUnit")
            if (
                source_work_unit.execution_decision_id != interaction.execution_decision_id
                or source_work_unit.assignment_id != interaction.source_assignment_id
            ):
                raise ValueError("AgentInteractionRecord source refs must belong to its Decision")
            if interaction.target_work_unit_id is not None:
                target_work_unit = work_units.get(interaction.target_work_unit_id)
                if (
                    target_work_unit is None
                    or target_work_unit.execution_decision_id != interaction.execution_decision_id
                    or target_work_unit.assignment_id != interaction.target_assignment_id
                ):
                    raise ValueError("AgentInteractionRecord target refs must belong to its Decision")
            if any(ref not in artifacts for ref in interaction.input_artifact_refs + interaction.output_artifact_refs):
                raise ValueError("AgentInteractionRecord references an unknown Artifact")
        children = [
            *self.planning_attempts,
            *self.research_design_versions,
            *self.execution_decisions,
            *self.work_units,
            *self.agent_interaction_records,
        ]
        if any(row.task_id != self.task_id for row in children):
            raise ValueError("all aggregate child taskId values must match ResearchTaskV12.taskId")
        return self


def execution_instances_for_design(
    task: ResearchTaskV12,
    design: ResearchDesignVersion,
) -> tuple[SubstageInstance, ...]:
    """Return only the project-task identities confirmed by ResearchDesign.

    Re-entry execution instances are retained as legacy compatibility data.
    They cannot enlarge the confirmed project-task set or become next-task
    selection authority.
    """

    return tuple(design.instances)


def repeatable_reentry_fingerprint(
    *,
    design: ResearchDesignVersion,
    template: SubstageInstance,
    scientific_state_version: int,
) -> str:
    raw = "\x1f".join(
        (
            design.research_design_id,
            str(design.version),
            template.substage_definition_id,
            str(template.substage_definition_version),
            str(scientific_state_version),
        )
    )
    return f"reentry-fingerprint:{sha256(raw.encode('utf-8')).hexdigest()[:32]}"


def execution_instance_for_ref(
    task: ResearchTaskV12,
    design: ResearchDesignVersion,
    instance_ref: InstanceRef,
) -> SubstageInstance | None:
    candidates = (
        *design.instances,
        *(
            row.instance
            for row in task.reentry_execution_instances
            if row.research_design_version == design.version
        ),
    )
    return next(
        (
            row
            for row in candidates
            if row.substage_instance_id == instance_ref.substage_instance_id
            and row.instance_revision == instance_ref.instance_revision
        ),
        None,
    )


def _unique_values(values: object, label: str) -> set[object]:
    result: set[object] = set()
    for value in values:  # type: ignore[union-attr]
        if value in result:
            raise ValueError(f"duplicate {label}")
        result.add(value)
    return result


def _assert_acyclic(graph: dict[str, list[str]], label: str) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> None:
        if node in visiting:
            raise ValueError(f"{label} graph contains a cycle")
        if node in visited:
            return
        visiting.add(node)
        for dependency in graph.get(node, []):
            visit(dependency)
        visiting.remove(node)
        visited.add(node)

    for node in graph:
        visit(node)


__all__ = [name for name in globals() if not name.startswith("_")]
