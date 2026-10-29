"""ResearchDesign-only orchestration for the v1.2 research-start path."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Annotated, Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.models.external_action import ExternalActionKind
from app.models.research_orchestration import (
    AcceptanceCriterion,
    AcceptanceCriterionKind,
    AdditionalActionType,
    BudgetBoundary,
    ConfirmationV12,
    CoverageClaim,
    CoverageEvaluationMode,
    CoverageMode,
    CoverageSourceType,
    CoverageStatus,
    CoverageTarget,
    DependencyConditionType,
    EvaluatorType,
    ExternalActionBoundary,
    InstanceRef,
    JudgmentRequirement,
    PlanningAttempt,
    PlanningAttemptStatus,
    PlanningComplexitySnapshot,
    PlanningMode,
    PlanningSourceUsage,
    ProjectCompletionRequirement,
    RegistrySnapshotRef,
    ResearchDesignCoverageReport,
    ResearchDesignRef,
    ResearchDesignStatus,
    ResearchDesignVersion,
    RepeatableDefinitionReentryAuthority,
    ResearchTaskV12,
    ResearchUnderstanding,
    RiskBoundary,
    ScopeBoundary,
    StageKey,
    SubstageDependency,
    SubstageInstance,
    TaskExecutionMode,
    TaskStatus,
    WorkflowProvenanceRef,
    WorkflowResponsibilityCoverage,
)
from app.modules.research_user_input import freeze_planning_user_input
from app.modules.research_design_activation import (
    approved_design_activation_updates,
    design_versions_after_confirmation,
)
from app.modules.research_design_recovery import (
    ResearchDesignRecoveryError,
    build_semantic_gap_recovery_draft,
    semantic_gap_recovery_id,
)
from app.modules.research_orchestration_repository import TaskRevisionConflictError
from app.modules.research_task_lifecycle import (
    ResearchDesignLifecycleFact,
    reconcile_task_lifecycle,
)


class ResearchDesignServiceError(RuntimeError):
    pass


RESEARCH_DESIGN_REVISION_USER_MESSAGE = "无法根据当前修改形成合法研究方案，请调整研究步骤后重试。"
RESEARCH_DESIGN_REVISION_RETRY_MESSAGE = "本次研究方案保存未完成，请重试。"
_RESEARCH_DESIGN_REVISION_USER_ERROR_CODES = {"research_design_revision_semantic_mismatch"}


class ResearchDesignRevisionUserError(ResearchDesignServiceError):
    pass


class ResearchDesignRevisionSaveUnavailable(ResearchDesignServiceError):
    pass


def classify_research_design_revision_failure(exc: Exception) -> ResearchDesignServiceError:
    safe_error_code = getattr(exc, "safe_error_code", None)
    if safe_error_code in _RESEARCH_DESIGN_REVISION_USER_ERROR_CODES:
        return ResearchDesignRevisionUserError(RESEARCH_DESIGN_REVISION_USER_MESSAGE)
    return ResearchDesignRevisionSaveUnavailable(RESEARCH_DESIGN_REVISION_RETRY_MESSAGE)


class ResearchDesignProposalError(ResearchDesignServiceError):
    def __init__(
        self,
        message: str,
        *,
        safe_error_code: str = "research_design_proposal_invalid",
        safe_field_paths: tuple[str, ...] = ("/",),
    ) -> None:
        super().__init__(message)
        self.safe_error_code = safe_error_code
        self.safe_field_paths = safe_field_paths


class _ProposalReferenceError(ValueError):
    def __init__(self, message: str, field_paths: list[str]) -> None:
        super().__init__(message)
        self.safe_field_paths = tuple(field_paths)


class _ProposalInvariantError(ValueError):
    def __init__(self, message: str, *, error_code: str, field_paths: tuple[str, ...]) -> None:
        super().__init__(message)
        self.safe_error_code = error_code
        self.safe_field_paths = field_paths


class DefinitionSourceUnavailableError(ResearchDesignServiceError):
    pass


class DefinitionResolutionError(ResearchDesignServiceError):
    pass


class ResearchDesignConfirmationError(ResearchDesignServiceError):
    pass


class ConfirmationIdentityConflictError(ResearchDesignConfirmationError):
    pass


class AdditionalActionConflictError(ResearchDesignServiceError):
    pass


class PlanningAttemptReplayError(ResearchDesignServiceError):
    pass


class PlannerTransportUnknown(ResearchDesignServiceError):
    def __init__(self, message: str, *, provider_call_ref: str | None = None) -> None:
        super().__init__(message)
        self.provider_call_ref = provider_call_ref


def _to_camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part.capitalize() for part in tail)


class _ProposalModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=_to_camel, extra="forbid",
        populate_by_name=True,
        strict=True,
    )


ProposalString = Annotated[str, Field(min_length=1)]
RiskLevel = Literal["low", "medium", "high"]


class ResearchUnderstandingProposal(_ProposalModel):
    research_type: ProposalString | None
    user_intent: ProposalString
    material_summary: ProposalString | None
    primary_research_questions: Annotated[list[ProposalString], Field(min_length=1)]
    auxiliary_research_dimensions: list[ProposalString]
    target_outputs: list[ProposalString]
    evidence_boundaries: list[ProposalString]
    missing_inputs: list[ProposalString]
    risk_notes: list[ProposalString]
    planning_rationale: ProposalString


class PlanningComplexityProposal(_ProposalModel):
    problem_clarity: Literal["high", "medium", "low"]
    expected_stage_span: Literal["one", "multiple", "unknown"]
    dependency_complexity: RiskLevel
    evidence_burden: RiskLevel
    external_action_risk: RiskLevel
    research_uncertainty: RiskLevel
    rationale: ProposalString


class ScopeBoundaryProposal(_ProposalModel):
    allowed_objective_kinds: list[ProposalString]
    excluded_actions: list[ProposalString]
    allowed_material_refs: list[ProposalString]
    max_substage_instances: Annotated[int, Field(ge=1)] | None


class BudgetBoundaryProposal(_ProposalModel):
    max_model_calls: Annotated[int, Field(ge=0)] | None
    max_tool_calls: Annotated[int, Field(ge=0)] | None
    max_wall_time_sec: Annotated[int, Field(ge=0)] | None
    max_cost_amount: Annotated[float, Field(ge=0)] | None
    currency: ProposalString | None

    @model_validator(mode="after")
    def validate_currency(self) -> "BudgetBoundaryProposal":
        if (self.max_cost_amount is None) != (self.currency is None):
            raise ValueError("maxCostAmount and currency must both be null or non-null")
        return self


class ExternalActionBoundaryProposal(_ProposalModel):
    allowed_action_kinds: list[ProposalString]
    confirmation_required_kinds: list[ProposalString]
    allowed_destinations: list[ProposalString]


class RiskBoundaryProposal(_ProposalModel):
    max_risk_class: RiskLevel
    prohibited_actions: list[ProposalString]
    human_gate_triggers: list[ProposalString]


class AcceptanceCriterionProposal(_ProposalModel):
    criterion_key: ProposalString
    description: ProposalString
    criterion_kind: Literal[
        "target_output",
        "artifact_quality",
        "constraint_compliance",
        "evidence_boundary_compliance",
        "external_action_guard",
        "risk_guard",
        "professional_judgment",
    ]
    coverage_source_paths: list[ProposalString]
    required_artifact_type: ProposalString | None
    judgment_requirement: Literal["deterministic", "research_synthesis", "domain_professional", "human_authority"]
    evaluator_type: Literal["deterministic", "agent", "human_gate"]
    evaluator_ref: ProposalString | None

    @model_validator(mode="after")
    def validate_evaluator(self) -> "AcceptanceCriterionProposal":
        if self.evaluator_type == "deterministic" and self.evaluator_ref is not None:
            raise ValueError("deterministic evaluator cannot have evaluatorRef")
        if self.evaluator_type != "deterministic" and self.evaluator_ref is None:
            raise ValueError("agent and human gate evaluators require evaluatorRef")
        return self


class DependencyProposal(_ProposalModel):
    dependency_key: ProposalString
    upstream_instance_key: ProposalString
    condition_type: Literal["instance_completed", "artifact_usable", "confirmation_granted"]
    required_artifact_types: list[ProposalString]
    confirmation_target_ref: ProposalString | None

    @model_validator(mode="after")
    def validate_condition_fields(self) -> "DependencyProposal":
        if self.condition_type == "artifact_usable":
            if not self.required_artifact_types or self.confirmation_target_ref is not None:
                raise ValueError("artifact_usable requires artifact types only")
        elif self.condition_type == "confirmation_granted":
            if self.required_artifact_types or self.confirmation_target_ref is None:
                raise ValueError("confirmation_granted requires confirmationTargetRef only")
        elif self.required_artifact_types or self.confirmation_target_ref is not None:
            raise ValueError("instance_completed cannot carry artifact or confirmation requirements")
        return self


class SubstageInstanceProposal(_ProposalModel):
    instance_key: ProposalString
    definition_id: ProposalString
    objective: ProposalString
    input_refs: list[ProposalString]
    expected_artifact_types: list[ProposalString]
    acceptance_criteria: list[AcceptanceCriterionProposal]
    dependencies: list[DependencyProposal]
    sequence_hint: Annotated[int, Field(ge=0)] | None

    @model_validator(mode="after")
    def validate_instance_content(self) -> "SubstageInstanceProposal":
        if not self.expected_artifact_types:
            raise _ProposalInvariantError(
                "a design instance requires expectedArtifactTypes",
                error_code="research_design_instance_outputs_missing",
                field_paths=("/expectedArtifactTypes",),
            )
        if not self.acceptance_criteria:
            raise _ProposalInvariantError(
                "a design instance requires acceptanceCriteria",
                error_code="research_design_instance_criteria_missing",
                field_paths=("/acceptanceCriteria",),
            )
        if len({row.criterion_key for row in self.acceptance_criteria}) != len(self.acceptance_criteria):
            raise _ProposalInvariantError(
                "duplicate criterionKey",
                error_code="research_design_instance_criteria_duplicate",
                field_paths=("/acceptanceCriteria",),
            )
        if len({row.dependency_key for row in self.dependencies}) != len(self.dependencies):
            raise _ProposalInvariantError(
                "duplicate dependencyKey",
                error_code="research_design_instance_dependencies_duplicate",
                field_paths=("/dependencies",),
            )
        for criterion in self.acceptance_criteria:
            if (
                criterion.required_artifact_type is not None
                and criterion.required_artifact_type not in self.expected_artifact_types
            ):
                raise _ProposalInvariantError(
                    "acceptance criterion requires an artifact the instance does not emit",
                    error_code="research_design_instance_artifact_mismatch",
                    field_paths=("/expectedArtifactTypes", "/acceptanceCriteria"),
                )
            if criterion.criterion_kind == "target_output" and (
                criterion.required_artifact_type is None or not criterion.coverage_source_paths
            ):
                raise _ProposalInvariantError(
                    "target_output criterion requires an emitted artifact and coverage target",
                    error_code="research_design_target_output_binding_invalid",
                    field_paths=("/acceptanceCriteria",),
                )
        return self


class WorkflowResponsibilityCoverageProposal(_ProposalModel):
    responsibility_key: ProposalString
    definition_id: ProposalString
    requirement: Literal["core", "conditional"]
    coverage_mode: Literal["planned_work", "reused_result", "not_selected"]
    instance_keys: list[ProposalString]
    reused_result_refs: list[ProposalString]
    rationale: ProposalString

    @model_validator(mode="after")
    def validate_coverage(self) -> "WorkflowResponsibilityCoverageProposal":
        if len(set(self.instance_keys)) != len(self.instance_keys):
            raise ValueError("duplicate Workflow responsibility instanceKey")
        if len(set(self.reused_result_refs)) != len(self.reused_result_refs):
            raise ValueError("duplicate Workflow responsibility reusedResultRef")
        if self.coverage_mode == "planned_work":
            if not self.instance_keys or self.reused_result_refs:
                raise ValueError("planned_work requires instanceKeys and no reusedResultRefs")
        elif self.coverage_mode == "reused_result":
            if self.instance_keys or not self.reused_result_refs:
                raise ValueError("reused_result requires reusedResultRefs and no instanceKeys")
        elif self.instance_keys or self.reused_result_refs:
            raise ValueError("not_selected cannot carry instanceKeys or reusedResultRefs")
        if self.requirement == "core" and self.coverage_mode == "not_selected":
            raise ValueError("core Workflow responsibility cannot be not_selected")
        return self


class CoverageClaimCandidate(_ProposalModel):
    source_path: ProposalString
    instance_keys: list[ProposalString]
    acceptance_criterion_keys: list[ProposalString]
    rationale: ProposalString


class ResearchDesignProposal(_ProposalModel):
    planning_mode: Literal["lightweight", "partial", "sdr"]
    planning_complexity: PlanningComplexityProposal
    goal: ProposalString
    understanding: ResearchUnderstandingProposal
    constraints: list[ProposalString]
    scope_boundary: ScopeBoundaryProposal
    budget_boundary: BudgetBoundaryProposal
    external_action_boundary: ExternalActionBoundaryProposal
    risk_boundary: RiskBoundaryProposal
    instances: list[SubstageInstanceProposal]
    workflow_responsibility_coverage: list[WorkflowResponsibilityCoverageProposal] = Field(default_factory=list)
    coverage_claim_candidates: list[CoverageClaimCandidate]
    target_output_owners: dict[str, str] = Field(default_factory=dict)
    target_output_contributors: dict[str, list[str]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_proposal_graph(self) -> "ResearchDesignProposal":
        if not self.instances:
            raise ValueError("a ResearchDesign proposal requires at least one instance")
        keys = [row.instance_key for row in self.instances]
        if len(set(keys)) != len(keys):
            raise ValueError("duplicate instanceKey")
        known = set(keys)
        responsibility_keys = [row.responsibility_key for row in self.workflow_responsibility_coverage]
        if len(set(responsibility_keys)) != len(responsibility_keys):
            raise ValueError("duplicate Workflow responsibilityKey")
        proposal_by_key = {row.instance_key: row for row in self.instances}
        owned_instance_keys: set[str] = set()
        for coverage in self.workflow_responsibility_coverage:
            if any(key not in known for key in coverage.instance_keys):
                raise _ProposalReferenceError(
                    "Workflow responsibility coverage references an unknown instanceKey",
                    ["/workflowResponsibilityCoverage"],
                )
            for instance_key in coverage.instance_keys:
                if proposal_by_key[instance_key].definition_id != coverage.definition_id:
                    raise ValueError("Workflow responsibility Definition does not match its project task")
                if instance_key in owned_instance_keys:
                    raise ValueError("a project task cannot satisfy multiple Workflow responsibilities")
                owned_instance_keys.add(instance_key)
        graph: dict[str, list[str]] = {}
        for row in self.instances:
            dependencies = [item.upstream_instance_key for item in row.dependencies]
            if any(item not in known for item in dependencies):
                raise ValueError("dependency references an unknown instanceKey")
            for dependency in row.dependencies:
                upstream = next(item for item in self.instances if item.instance_key == dependency.upstream_instance_key)
                if any(
                    artifact_type not in upstream.expected_artifact_types
                    for artifact_type in dependency.required_artifact_types
                ):
                    raise ValueError("dependency requires an artifact its upstream instance does not emit")
            graph[row.instance_key] = dependencies
        _topological_order(graph)

        source_paths = {
            *(f"understanding.targetOutputs[{index}]" for index, _ in enumerate(self.understanding.target_outputs)),
            *(f"constraints[{index}]" for index, _ in enumerate(self.constraints)),
            *(
                f"understanding.evidenceBoundaries[{index}]"
                for index, _ in enumerate(self.understanding.evidence_boundaries)
            ),
            *(
                f"externalActionBoundary.confirmationRequiredKinds[{index}]"
                for index, _ in enumerate(self.external_action_boundary.confirmation_required_kinds)
            ),
            *(
                f"riskBoundary.prohibitedActions[{index}]"
                for index, _ in enumerate(self.risk_boundary.prohibited_actions)
            ),
            *(
                f"riskBoundary.humanGateTriggers[{index}]"
                for index, _ in enumerate(self.risk_boundary.human_gate_triggers)
            ),
        }
        criterion_keys = {
            f"{instance.instance_key}:{criterion.criterion_key}"
            for instance in self.instances
            for criterion in instance.acceptance_criteria
        }
        reference_violations = [
            f"/instances/{instance_index}/acceptanceCriteria/{criterion_index}/coverageSourcePaths"
            for instance_index, instance in enumerate(self.instances)
            for criterion_index, criterion in enumerate(instance.acceptance_criteria)
            if any(path not in source_paths for path in criterion.coverage_source_paths)
        ]
        candidate_paths = [row.source_path for row in self.coverage_claim_candidates]
        if len(set(candidate_paths)) != len(candidate_paths):
            reference_violations.append("/coverageClaimCandidates")
        for candidate_index, candidate in enumerate(self.coverage_claim_candidates):
            if candidate.source_path not in source_paths:
                reference_violations.append(f"/coverageClaimCandidates/{candidate_index}/sourcePath")
            if any(key not in known for key in candidate.instance_keys):
                reference_violations.append(f"/coverageClaimCandidates/{candidate_index}/instanceKeys")
            if any(key not in criterion_keys for key in candidate.acceptance_criterion_keys):
                reference_violations.append(
                    f"/coverageClaimCandidates/{candidate_index}/acceptanceCriterionKeys"
                )
        if reference_violations:
            raise _ProposalReferenceError("invalid ResearchDesign cross-reference", reference_violations)
        return self


def validate_research_design_proposal(
    payload: Mapping[str, Any],
    *,
    allowed_definition_ids: frozenset[str] | None = None,
    definition_stage_by_id: Mapping[str, str] | None = None,
) -> ResearchDesignProposal:
    try:
        proposal = ResearchDesignProposal.model_validate(
            _normalize_coverage_reference_aliases(payload)
        )
    except (ValidationError, ValueError) as exc:
        field_paths: list[str] = []
        reference_error = False
        invariant_error_codes: list[str] = []
        if isinstance(exc, ValidationError):
            for item in exc.errors(include_url=False, include_context=True, include_input=False):
                source = item.get("ctx", {}).get("error") if isinstance(item.get("ctx"), Mapping) else None
                if isinstance(source, _ProposalReferenceError):
                    reference_error = True
                    field_paths.extend(source.safe_field_paths)
                    continue
                location = item.get("loc") or ()
                if isinstance(source, _ProposalInvariantError):
                    prefix = "/" + "/".join(str(part) for part in location)
                    invariant_error_codes.append(source.safe_error_code)
                    field_paths.extend(
                        prefix + path if prefix != "/" else path
                        for path in source.safe_field_paths
                    )
                    continue
                field_paths.append("/" + "/".join(str(part) for part in location))
        invariant_error_code = (
            invariant_error_codes[0]
            if invariant_error_codes and len(set(invariant_error_codes)) == 1
            else None
        )
        raise ResearchDesignProposalError(
            "invalid_research_design_proposal",
            safe_error_code="research_design_cross_reference_invalid"
            if reference_error
            else invariant_error_code or "research_design_proposal_invalid",
            safe_field_paths=tuple(dict.fromkeys(field_paths)) or ("/",),
        ) from exc
    if allowed_definition_ids is not None:
        unknown_paths = tuple(
            f"/instances/{index}/definitionId"
            for index, row in enumerate(proposal.instances)
            if row.definition_id not in allowed_definition_ids
        )
        if unknown_paths:
            raise ResearchDesignProposalError(
                "ResearchDesign proposal selected a Definition outside the frozen catalog",
                safe_error_code="research_design_definition_unknown",
                safe_field_paths=unknown_paths,
            )
    if definition_stage_by_id is not None:
        proposal = validate_research_design_planning_depth(
            proposal,
            definition_stage_by_id=definition_stage_by_id,
        )
    return proposal


def validate_research_design_planning_depth(
    proposal: ResearchDesignProposal,
    *,
    definition_stage_by_id: Mapping[str, str],
) -> ResearchDesignProposal:
    """Align planning-depth claims with trusted graph facts.

    Unknown Definition IDs remain hard failures. Inconsistent ``lightweight`` or
    over-ambitious ``sdr`` claims are normalized to ``partial`` (and
    ``expectedStageSpan`` is corrected to the trusted Stage graph) so weaker
    planners can still deliver a confirmable design instead of failing the
    session. ``partial`` remains the conservative middle ground.
    """

    missing_paths = tuple(
        f"/instances/{index}/definitionId"
        for index, row in enumerate(proposal.instances)
        if row.definition_id not in definition_stage_by_id
    )
    if missing_paths:
        raise ResearchDesignProposalError(
            "ResearchDesign proposal selected a Definition without a trusted Stage",
            safe_error_code="research_design_definition_unknown",
            safe_field_paths=missing_paths,
        )

    stages = {definition_stage_by_id[row.definition_id] for row in proposal.instances}
    actual_stage_span: Literal["one", "multiple"] = "one" if len(stages) == 1 else "multiple"
    complexity = proposal.planning_complexity
    updates: dict[str, Any] = {}

    if complexity.expected_stage_span != actual_stage_span:
        complexity = complexity.model_copy(update={"expected_stage_span": actual_stage_span})
        updates["planning_complexity"] = complexity

    factors = (
        complexity.dependency_complexity,
        complexity.evidence_burden,
        complexity.external_action_risk,
        complexity.research_uncertainty,
    )
    all_factors_low = all(value == "low" for value in factors)
    lightweight_eligible = (
        complexity.problem_clarity == "high"
        and actual_stage_span == "one"
        and all_factors_low
    )

    if proposal.planning_mode == "lightweight" and not lightweight_eligible:
        updates["planning_mode"] = "partial"
    elif proposal.planning_mode == "sdr" and lightweight_eligible:
        # Clear one-stage low-complexity work does not need a full SDR template.
        updates["planning_mode"] = "partial"

    if not updates:
        return proposal
    return proposal.model_copy(update=updates)

def _normalize_coverage_reference_aliases(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    """Normalize aliases and derive non-authoritative claim references from criteria."""
    normalized = deepcopy(dict(payload))
    instances = normalized.get("instances")
    candidates = normalized.get("coverageClaimCandidates")
    if not isinstance(instances, list) or not isinstance(candidates, list):
        return normalized
    valid_source_paths = _raw_coverage_source_paths(normalized)
    canonical_refs: set[str] = set()
    owners_by_criterion: dict[str, set[str]] = {}
    criteria_by_ref: dict[str, dict[str, Any]] = {}
    for instance in instances:
        if not isinstance(instance, Mapping) or not isinstance(instance.get("instanceKey"), str):
            continue
        instance_key = instance["instanceKey"]
        criteria = instance.get("acceptanceCriteria")
        if not isinstance(criteria, list):
            continue
        for criterion in criteria:
            if not isinstance(criterion, Mapping) or not isinstance(criterion.get("criterionKey"), str):
                continue
            criterion_key = criterion["criterionKey"]
            canonical_ref = f"{instance_key}:{criterion_key}"
            canonical_refs.add(canonical_ref)
            if isinstance(criterion, dict):
                criteria_by_ref[canonical_ref] = criterion
            owners_by_criterion.setdefault(criterion_key, set()).add(instance_key)
            paths = criterion.get("coverageSourcePaths")
            if isinstance(paths, list) and isinstance(criterion, dict):
                criterion["coverageSourcePaths"] = [
                    _normalize_coverage_source_path(path) if isinstance(path, str) else path
                    for path in paths
                ]
    claim_paths_by_criterion: dict[str, set[str]] = {}
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        instance_keys = candidate.get("instanceKeys")
        references = candidate.get("acceptanceCriterionKeys")
        if not isinstance(instance_keys, list) or not isinstance(references, list):
            continue
        if isinstance(candidate.get("sourcePath"), str):
            candidate["sourcePath"] = _normalize_coverage_source_path(candidate["sourcePath"])
        declared_instances = {value for value in instance_keys if isinstance(value, str)}
        rewritten: list[Any] = []
        for reference in references:
            if not isinstance(reference, str) or reference in canonical_refs:
                rewritten.append(reference)
                continue
            owners = owners_by_criterion.get(reference, set()) & declared_instances
            rewritten.append(f"{next(iter(owners))}:{reference}" if len(owners) == 1 else reference)
        candidate["acceptanceCriterionKeys"] = rewritten
        source_path = candidate.get("sourcePath")
        if isinstance(source_path, str) and source_path in valid_source_paths:
            for reference in rewritten:
                if isinstance(reference, str) and reference in canonical_refs:
                    claim_paths_by_criterion.setdefault(reference, set()).add(source_path)
    for reference, criterion in criteria_by_ref.items():
        paths = criterion.get("coverageSourcePaths")
        if not isinstance(paths, list):
            continue
        claimed_paths = claim_paths_by_criterion.get(reference, set())
        if claimed_paths:
            criterion["coverageSourcePaths"] = list(dict.fromkeys([
                *[path for path in paths if not isinstance(path, str) or path in valid_source_paths],
                *sorted(claimed_paths),
            ]))
    references_by_source_path: dict[str, list[str]] = {}
    for reference, criterion in criteria_by_ref.items():
        paths = criterion.get("coverageSourcePaths")
        if not isinstance(paths, list):
            continue
        for path in paths:
            if isinstance(path, str) and path in valid_source_paths:
                references_by_source_path.setdefault(path, []).append(reference)
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        source_path = candidate.get("sourcePath")
        declared_instances = candidate.get("instanceKeys")
        declared_references = candidate.get("acceptanceCriterionKeys")
        if (
            not isinstance(source_path, str)
            or not isinstance(declared_instances, list)
            or not declared_instances
            or not isinstance(declared_references, list)
            or not declared_references
        ):
            continue
        references = list(dict.fromkeys(references_by_source_path.get(source_path, ())))
        if not references:
            continue
        candidate["acceptanceCriterionKeys"] = references
        candidate["instanceKeys"] = list(dict.fromkeys(
            reference.split(":", 1)[0] for reference in references
        ))
    candidates[:] = [
        candidate
        for candidate in candidates
        if not isinstance(candidate, dict)
        or not isinstance(candidate.get("sourcePath"), str)
        or candidate["sourcePath"] not in valid_source_paths
        or not candidate.get("instanceKeys")
        or not candidate.get("acceptanceCriterionKeys")
        or bool(references_by_source_path.get(candidate["sourcePath"]))
    ]
    return normalized


def _normalize_coverage_source_path(value: str) -> str:
    aliases = {
        "understanding.target_outputs": "understanding.targetOutputs",
        "understanding.evidence_boundaries": "understanding.evidenceBoundaries",
        "external_action_boundary.confirmation_required_kinds": (
            "externalActionBoundary.confirmationRequiredKinds"
        ),
        "risk_boundary.prohibited_actions": "riskBoundary.prohibitedActions",
        "risk_boundary.human_gate_triggers": "riskBoundary.humanGateTriggers",
    }
    normalized = value.strip()
    for source, target in aliases.items():
        if normalized.startswith(source):
            normalized = target + normalized[len(source):]
            break
    pointer = re.fullmatch(
        r"/(understanding/targetOutputs|understanding/evidenceBoundaries|constraints|"
        r"externalActionBoundary/confirmationRequiredKinds|riskBoundary/prohibitedActions|"
        r"riskBoundary/humanGateTriggers)/(\d+)",
        normalized,
    )
    if pointer:
        return f"{pointer.group(1).replace('/', '.')}[{pointer.group(2)}]"
    dotted = re.fullmatch(
        r"(understanding\.targetOutputs|understanding\.evidenceBoundaries|constraints|"
        r"externalActionBoundary\.confirmationRequiredKinds|riskBoundary\.prohibitedActions|"
        r"riskBoundary\.humanGateTriggers)\.(\d+)",
        normalized,
    )
    if dotted:
        return f"{dotted.group(1)}[{dotted.group(2)}]"
    return normalized


def _raw_coverage_source_paths(payload: Mapping[str, Any]) -> set[str]:
    understanding = payload.get("understanding")
    external = payload.get("externalActionBoundary")
    risk = payload.get("riskBoundary")
    fields = (
        ("understanding.targetOutputs", understanding, "targetOutputs"),
        ("constraints", payload, "constraints"),
        ("understanding.evidenceBoundaries", understanding, "evidenceBoundaries"),
        ("externalActionBoundary.confirmationRequiredKinds", external, "confirmationRequiredKinds"),
        ("riskBoundary.prohibitedActions", risk, "prohibitedActions"),
        ("riskBoundary.humanGateTriggers", risk, "humanGateTriggers"),
    )
    return {
        f"{prefix}[{index}]"
        for prefix, container, key in fields
        if isinstance(container, Mapping) and isinstance(container.get(key), list)
        for index, _ in enumerate(container[key])
    }


@dataclass(frozen=True)
class ResolvedDefinition:
    definition_id: str
    definition_version: int
    stage_key: StageKey | str
    required_input_refs: tuple[str, ...] = ()
    artifact_types: tuple[str, ...] = ()
    criterion_refs: tuple[str, ...] = ()
    evaluator_refs: tuple[str, ...] = ()
    quality_rule_refs: tuple[str, ...] = ()
    judgment_requirement: str | None = None
    deterministic_validator_refs: tuple[str, ...] = ()
    substage_key: str | None = None
    required_external_action_kinds: tuple[ExternalActionKind, ...] = ()
    external_action_contract_known: bool = False
    repeatable_reentry_authority: RepeatableDefinitionReentryAuthority | None = None


class DefinitionSource(Protocol):
    """Read-only source for the frozen definitions used to form a ResearchDesign."""

    def snapshot_ref(self) -> RegistrySnapshotRef | Mapping[str, Any]: ...

    def resolve(self, definition_id: str) -> ResolvedDefinition | None: ...

    def definition_is_eligible_for_workflow_admission(
        self, definition_id: str, workflow_admission: Mapping[str, Any] | None
    ) -> bool: ...


class ResearchTaskRepository(Protocol):
    def load(self, *, task_id: str, owner_id: str, workspace_id: str) -> ResearchTaskV12: ...

    def load_read_only(
        self, *, task_id: str, owner_id: str, workspace_id: str
    ) -> ResearchTaskV12: ...

    def load_planning_workflow_admission(
        self, *, task_id: str, owner_id: str, workspace_id: str
    ) -> Mapping[str, Any] | None: ...

    def save(
        self,
        task: ResearchTaskV12,
        *,
        owner_id: str,
        workspace_id: str,
        expected_task_revision: int,
        **kwargs: object,
    ) -> ResearchTaskV12: ...


@dataclass(frozen=True)
class PlannerProviderResult:
    proposal: Mapping[str, Any]
    provider_call_ref: str
    planning_source_usages: tuple[PlanningSourceUsage, ...] = ()


PlannerProvider = Callable[[str], PlannerProviderResult]
Clock = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _stable_id(prefix: str, *parts: object) -> str:
    raw = json.dumps(parts, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return f"{prefix}_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:20]}"


def _workflow_provenance_ref(
    workflow_admission: Mapping[str, Any] | None,
) -> WorkflowProvenanceRef | None:
    if workflow_admission is None:
        return None
    workflow_id = str(workflow_admission.get("workflowId") or "").strip()
    if not workflow_id:
        return None
    workflow_version = workflow_admission.get("workflowVersion")
    raw_hash = str(workflow_admission.get("workflowContentHash") or "").strip().lower()
    if raw_hash.startswith("sha256:"):
        raw_hash = raw_hash[7:]
    if (
        isinstance(workflow_version, bool)
        or not isinstance(workflow_version, int)
        or workflow_version < 1
        or re.fullmatch(r"[0-9a-f]{64}", raw_hash) is None
    ):
        raise DefinitionResolutionError("Workflow provenance is incomplete or invalid")
    return WorkflowProvenanceRef(
        workflowId=workflow_id,
        workflowVersion=workflow_version,
        contentHash=f"sha256:{raw_hash}",
    )


def _topological_order(graph: Mapping[str, list[str]]) -> list[str]:
    visiting: set[str] = set()
    visited: set[str] = set()
    order: list[str] = []

    def visit(node: str) -> None:
        if node in visiting:
            raise ValueError("SubstageInstance proposal dependency graph contains a cycle")
        if node in visited:
            return
        visiting.add(node)
        for upstream in graph.get(node, []):
            visit(upstream)
        visiting.remove(node)
        visited.add(node)
        order.append(node)

    for key in graph:
        visit(key)
    return order


def new_research_task(
    *,
    task_id: str,
    registry_snapshot_ref: RegistrySnapshotRef | Mapping[str, Any],
) -> ResearchTaskV12:
    return ResearchTaskV12(
        taskId=task_id,
        revision=0,
        status=TaskStatus.DRAFT,
        executionMode=TaskExecutionMode.STAGE_CONFIRMATION,
        pendingExecutionMode=None,
        executionPolicyVersion="research-design-only.v1",
        activeResearchDesignRef=None,
        registrySnapshotRef=RegistrySnapshotRef.model_validate(registry_snapshot_ref),
        planningAttempts=[],
        researchDesignVersions=[],
        substageRuntimeStates=[],
        executionDecisions=[],
        workUnits=[],
        capabilityCalls=[],
        artifacts=[],
        confirmations=[],
        agentInteractionRecords=[],
        startedAt=None,
        finishedAt=None,
    )


class ResearchDesignService:
    def __init__(
        self,
        *,
        repository: ResearchTaskRepository,
        definition_source: DefinitionSource | None,
        clock: Clock = _utc_now,
    ) -> None:
        self._repository = repository
        self._definition_source = definition_source
        self._clock = clock

    def _source_snapshot(self) -> RegistrySnapshotRef:
        if self._definition_source is None:
            raise DefinitionSourceUnavailableError("trusted DefinitionSource is required")
        try:
            return RegistrySnapshotRef.model_validate(self._definition_source.snapshot_ref())
        except (ValidationError, ValueError, TypeError) as exc:
            raise DefinitionResolutionError("DefinitionSource returned an invalid snapshot ref") from exc

    def _preflight_source(self, task: ResearchTaskV12) -> None:
        if self._source_snapshot() != task.registry_snapshot_ref:
            raise DefinitionResolutionError("DefinitionSource snapshot does not match the Task snapshot")

    def _planning_workflow_admission(
        self,
        *,
        task: ResearchTaskV12,
        owner_id: str,
        workspace_id: str,
    ) -> Mapping[str, Any] | None:
        admission = self._repository.load_planning_workflow_admission(
            task_id=task.task_id,
            owner_id=owner_id,
            workspace_id=workspace_id,
        )
        if admission is not None and not isinstance(admission, Mapping):
            raise DefinitionResolutionError("Workflow admission is invalid")
        return admission

    def plan(
        self,
        *,
        task_id: str,
        owner_id: str,
        workspace_id: str,
        request_id: str,
        expected_task_revision: int,
        provider: PlannerProvider,
        user_input: str | None = None,
        user_input_source_ref: str | None = None,
        user_input_kind: Literal["user_request", "user_revision", "plan_revision"] = "user_request",
        input_base_design_ref: ResearchDesignRef | None = None,
    ) -> ResearchTaskV12:
        task = self._repository.load(task_id=task_id, owner_id=owner_id, workspace_id=workspace_id)
        self._preflight_source(task)
        if task.revision != expected_task_revision:
            raise TaskRevisionConflictError("task_revision_conflict")

        attempt_id = _stable_id("planning", task_id, request_id)
        existing = next(
            (row for row in task.planning_attempts if row.planning_attempt_id == attempt_id),
            None,
        )
        if existing is not None:
            if user_input is not None:
                frozen = existing.user_input_snapshot
                last_source = None if frozen is None else frozen.sources[-1]
                if (last_source is None or last_source.content != user_input
                        or last_source.source_kind != user_input_kind
                        or last_source.source_ref != (user_input_source_ref or request_id)):
                    raise PlanningAttemptReplayError("planning_request_input_conflict")
            if existing.status == PlanningAttemptStatus.COMPLETED:
                return task
            raise PlanningAttemptReplayError("a started, failed, or unknown Planner attempt cannot be replayed")

        user_input_snapshot = (
            freeze_planning_user_input(
                task=task, content=user_input,
                source_ref=user_input_source_ref or request_id,
                source_kind=user_input_kind, base_design_ref=input_base_design_ref,
            ) if user_input is not None else None
        )
        idempotency_key = f"planner:{request_id}"
        started = PlanningAttempt(
            planningAttemptId=attempt_id,
            taskId=task.task_id,
            requestId=request_id,
            plannerCallIdempotencyKey=idempotency_key,
            plannerProviderCallRef=None,
            userInputSnapshot=user_input_snapshot,
            additionalActionType=AdditionalActionType.NONE,
            additionalActionIdempotencyKey=None,
            additionalProviderCallRef=None,
            status=PlanningAttemptStatus.STARTED,
            startedAt=self._clock(),
            finishedAt=None,
        )
        started_task = self._replace_task(task, planningAttempts=[*task.planning_attempts, started])
        persisted_started = self._repository.save(
            started_task,
            owner_id=owner_id,
            workspace_id=workspace_id,
            expected_task_revision=task.revision,
        )

        provider_call_ref: str | None = None
        try:
            provider_result = provider(idempotency_key)
            if not isinstance(provider_result, PlannerProviderResult):
                raise ResearchDesignProposalError("Planner provider returned an invalid envelope")
            if not isinstance(provider_result.provider_call_ref, str) or not provider_result.provider_call_ref:
                raise ResearchDesignProposalError("Planner provider call ref is required")
            provider_call_ref = provider_result.provider_call_ref
            try:
                planning_source_usages = tuple(
                    row
                    if isinstance(row, PlanningSourceUsage)
                    else PlanningSourceUsage.model_validate(row)
                    for row in provider_result.planning_source_usages
                )
            except Exception as exc:
                raise ResearchDesignProposalError(
                    "Planner provider returned invalid planning source provenance"
                ) from exc
            source_identities = [
                (row.source_kind, row.source_id, row.source_version)
                for row in planning_source_usages
            ]
            if len(source_identities) != len(set(source_identities)):
                raise ResearchDesignProposalError(
                    "Planner provider returned duplicate planning source provenance"
                )
            proposal = validate_research_design_proposal(provider_result.proposal)
            design = self._build_design(
                persisted_started, proposal, attempt_id, owner_id=owner_id, workspace_id=workspace_id
            )
            completed = started.model_copy(
                update={
                    "planner_provider_call_ref": provider_result.provider_call_ref,
                    "planning_source_usages": list(planning_source_usages),
                    "status": PlanningAttemptStatus.COMPLETED,
                    "finished_at": self._clock(),
                }
            )
            prior_versions = [
                row.model_copy(update={"status": ResearchDesignStatus.SUPERSEDED})
                if row.status == ResearchDesignStatus.DRAFT
                else row
                for row in persisted_started.research_design_versions
            ]
            candidate = self._replace_task(
                persisted_started,
                planningAttempts=[*persisted_started.planning_attempts[:-1], completed],
                researchDesignVersions=[*prior_versions, design],
            )
            return self._repository.save(
                candidate,
                owner_id=owner_id,
                workspace_id=workspace_id,
                expected_task_revision=persisted_started.revision,
            )
        except Exception as exc:
            self._mark_attempt_failed(
                persisted_started,
                attempt_id=attempt_id,
                owner_id=owner_id,
                workspace_id=workspace_id,
                provider_call_ref=getattr(exc, "provider_call_ref", None) or provider_call_ref,
            )
            raise

    def revise(
        self,
        *,
        task_id: str,
        owner_id: str,
        workspace_id: str,
        request_id: str,
        expected_task_revision: int,
        provider: PlannerProvider,
        user_input: str | None = None,
        user_input_source_ref: str | None = None,
        user_input_kind: Literal["user_revision", "plan_revision"] = "user_revision",
    ) -> ResearchTaskV12:
        """Recompile an unconfirmed user-edited plan through the normal Planner boundary."""
        task = self._repository.load(task_id=task_id, owner_id=owner_id, workspace_id=workspace_id)
        self._preflight_source(task)
        if task.revision != expected_task_revision:
            raise TaskRevisionConflictError("task_revision_conflict")
        if not task.research_design_versions:
            raise ResearchDesignServiceError("research design not found")
        latest = max(task.research_design_versions, key=lambda row: row.version)
        if latest.status != ResearchDesignStatus.DRAFT:
            raise ResearchDesignServiceError("research design is not editable")
        if task.execution_decisions or task.work_units or task.capability_calls or task.substage_runtime_states:
            raise ResearchDesignServiceError("research design cannot be revised after execution preparation")
        return self.plan(
            task_id=task_id,
            owner_id=owner_id,
            workspace_id=workspace_id,
            request_id=request_id,
            expected_task_revision=expected_task_revision,
            provider=provider,
            user_input=user_input,
            user_input_source_ref=user_input_source_ref,
            user_input_kind=user_input_kind,
            input_base_design_ref=ResearchDesignRef(
                researchDesignId=latest.research_design_id, version=latest.version,
            ),
        )

    def recover_confirmed_semantic_material_gap(
        self,
        *,
        task_id: str,
        owner_id: str,
        workspace_id: str,
        blocked_instance_id: str,
        gap_reason_code: str,
        expected_task_revision: int,
    ) -> ResearchTaskV12:
        """Create a recovery Design draft without changing confirmed research authority."""

        task = self._repository.load(
            task_id=task_id,
            owner_id=owner_id,
            workspace_id=workspace_id,
        )
        self._preflight_source(task)
        if task.revision != expected_task_revision:
            raise TaskRevisionConflictError("task_revision_conflict")
        if task.execution_mode is not TaskExecutionMode.AUTOMATIC_RESEARCH:
            raise ResearchDesignServiceError("system_recovery_requires_automatic_research")
        if gap_reason_code != "semantic_material_insufficient":
            raise ResearchDesignServiceError("system_recovery_gap_not_authorized")

        active = self._active_confirmed_design(task)
        target, target_definition = self._semantic_gap_recovery_target(gap_reason_code)
        recovery_id = semantic_gap_recovery_id(
            blocked_instance_id=blocked_instance_id,
            gap_reason_code=gap_reason_code,
            recovery_definition_id=target_definition.definition_id,
            source_design_version=active.version,
        )
        existing_draft = next(
            (
                design
                for design in task.research_design_versions
                if design.status is ResearchDesignStatus.DRAFT
            ),
            None,
        )
        if existing_draft is not None:
            if any(
                row.substage_instance_id == recovery_id
                for row in existing_draft.instances
            ):
                return task
            raise ResearchDesignServiceError("research_design_confirmation_pending")

        blocked = next(
            (row for row in active.instances if row.substage_instance_id == blocked_instance_id),
            None,
        )
        runtime = next(
            (
                row
                for row in task.substage_runtime_states
                if blocked is not None
                and row.substage_instance_id == blocked.substage_instance_id
                and row.instance_revision == blocked.instance_revision
            ),
            None,
        )
        if (
            blocked is None
            or runtime is None
            or runtime.status_reason_code != gap_reason_code
        ):
            raise ResearchDesignServiceError("system_recovery_gap_not_durable")

        try:
            draft = build_semantic_gap_recovery_draft(
                active=active,
                blocked=blocked,
                recovery_id=recovery_id,
                recovery_definition_id=target_definition.definition_id,
                recovery_definition_version=target_definition.definition_version,
                recovery_stage_key=target_definition.stage_key,
                recovery_objective=str(
                    target.get("objective")
                    or "Recover material required by the blocked research task."
                ),
                recovery_artifact_types=[
                    str(value).strip()
                    for value in target.get("expectedArtifactTypes", ())
                    if str(value).strip()
                ],
                created_at=self._clock(),
            )
        except ResearchDesignRecoveryError as exc:
            raise ResearchDesignServiceError(str(exc)) from exc
        candidate = self._replace_task(
            task,
            researchDesignVersions=[*task.research_design_versions, draft],
        )
        candidate = self._apply_task_lifecycle_fact(
            task,
            candidate,
            fact=ResearchDesignLifecycleFact.SEMANTIC_MATERIAL_GAP_RECOVERY_DRAFTED,
        )
        return self._repository.save(
            candidate,
            owner_id=owner_id,
            workspace_id=workspace_id,
            expected_task_revision=task.revision,
            lifecycle_fact=ResearchDesignLifecycleFact.SEMANTIC_MATERIAL_GAP_RECOVERY_DRAFTED,
        )

    @staticmethod
    def _active_confirmed_design(task: ResearchTaskV12) -> ResearchDesignVersion:
        active_ref = task.active_research_design_ref
        active = next(
            (
                row
                for row in task.research_design_versions
                if active_ref is not None
                and row.research_design_id == active_ref.research_design_id
                and row.version == active_ref.version
            ),
            None,
        )
        if active is None or active.status is not ResearchDesignStatus.CONFIRMED:
            raise ResearchDesignServiceError("system_recovery_requires_confirmed_design")
        return active

    def _semantic_gap_recovery_target(
        self,
        gap_reason_code: str,
    ) -> tuple[Mapping[str, Any], ResolvedDefinition]:
        source = self._definition_source
        target_loader = getattr(source, "system_recovery_target", None)
        target = target_loader(gap_reason_code) if callable(target_loader) else None
        if not isinstance(target, Mapping):
            raise ResearchDesignServiceError("system_recovery_target_not_frozen")
        definition_id = str(target.get("definitionId") or "").strip()
        if not definition_id:
            raise ResearchDesignServiceError("system_recovery_target_invalid")
        definition = source.resolve(definition_id) if source is not None else None
        if definition is None:
            raise ResearchDesignServiceError("system_recovery_target_not_resolvable")
        outputs = [
            str(value).strip()
            for value in target.get("expectedArtifactTypes", ())
            if str(value).strip()
        ]
        if not outputs:
            raise ResearchDesignServiceError("system_recovery_target_invalid")
        return target, definition

    def _mark_attempt_failed(
        self,
        task: ResearchTaskV12,
        *,
        attempt_id: str,
        owner_id: str,
        workspace_id: str,
        provider_call_ref: str | None,
    ) -> None:
        current = task
        for save_attempt in range(2):
            attempts: list[PlanningAttempt] = []
            for row in current.planning_attempts:
                if row.planning_attempt_id == attempt_id:
                    if row.status != PlanningAttemptStatus.STARTED:
                        return
                    attempts.append(
                        row.model_copy(
                            update={
                                "status": PlanningAttemptStatus.FAILED,
                                "finished_at": self._clock(),
                                "planner_provider_call_ref": provider_call_ref,
                            }
                        )
                    )
                else:
                    attempts.append(row)
            failed = self._replace_task(current, planningAttempts=attempts)
            try:
                self._repository.save(
                    failed,
                    owner_id=owner_id,
                    workspace_id=workspace_id,
                    expected_task_revision=current.revision,
                )
                return
            except TaskRevisionConflictError:
                if save_attempt == 1:
                    raise
                current = self._repository.load(
                    task_id=task.task_id,
                    owner_id=owner_id,
                    workspace_id=workspace_id,
                )

    def claim_additional_action(
        self,
        *,
        task_id: str,
        owner_id: str,
        workspace_id: str,
        planning_attempt_id: str,
        action_type: str,
        expected_task_revision: int,
    ) -> ResearchTaskV12:
        task = self._repository.load(task_id=task_id, owner_id=owner_id, workspace_id=workspace_id)
        if task.revision != expected_task_revision:
            raise TaskRevisionConflictError("task_revision_conflict")
        try:
            action = AdditionalActionType(action_type)
        except ValueError as exc:
            raise AdditionalActionConflictError("unsupported additional action") from exc
        if action == AdditionalActionType.NONE:
            raise AdditionalActionConflictError("additional action cannot be none")
        updated: list[PlanningAttempt] = []
        found = False
        for row in task.planning_attempts:
            if row.planning_attempt_id != planning_attempt_id:
                updated.append(row)
                continue
            found = True
            if row.status != PlanningAttemptStatus.COMPLETED:
                raise AdditionalActionConflictError("additional action requires a completed PlanningAttempt")
            if row.additional_action_type != AdditionalActionType.NONE:
                raise AdditionalActionConflictError("additional action is already claimed")
            updated.append(
                row.model_copy(
                    update={
                        "additional_action_type": action,
                        "additional_action_idempotency_key": f"additional:{planning_attempt_id}:{action.value}",
                    }
                )
            )
        if not found:
            raise AdditionalActionConflictError("PlanningAttempt not found")
        candidate = self._replace_task(task, planningAttempts=updated)
        return self._repository.save(
            candidate,
            owner_id=owner_id,
            workspace_id=workspace_id,
            expected_task_revision=task.revision,
        )

    def read_design(
        self,
        *,
        task_id: str,
        owner_id: str,
        workspace_id: str,
    ) -> dict[str, Any]:
        loader = getattr(self._repository, "load_read_only", self._repository.load)
        task = loader(task_id=task_id, owner_id=owner_id, workspace_id=workspace_id)
        if not task.research_design_versions:
            raise ResearchDesignServiceError("research design not found")
        design = max(task.research_design_versions, key=lambda row: row.version)
        return {
            "designId": design.research_design_id,
            "version": design.version,
            "status": design.status.value,
            "goal": design.goal,
            "projectCompletionRequirements": [
                row.model_dump(mode="json", by_alias=True)
                for row in design.project_completion_requirements
            ],
            "workflowProvenanceRef": (
                design.workflow_provenance_ref.model_dump(mode="json", by_alias=True)
                if design.workflow_provenance_ref is not None
                else None
            ),
            "workflowResponsibilityCoverage": [
                row.model_dump(mode="json", by_alias=True)
                for row in design.workflow_responsibility_coverage
            ],
            "coverage": (
                design.coverage_report.model_dump(mode="json", by_alias=True)
                if design.coverage_report is not None
                else None
            ),
            "revision": task.revision,
        }

    @staticmethod
    def _replay_confirmation(
        task: ResearchTaskV12,
        *,
        confirmation_id: str,
        expected_identity: tuple[str, str, int, str, str],
    ) -> ResearchTaskV12 | None:
        existing = next(
            (row for row in task.confirmations if row.confirmation_id == confirmation_id),
            None,
        )
        if existing is None:
            return None
        actual_identity = (
            existing.target_type,
            existing.target_id,
            existing.target_version,
            existing.decision,
            existing.actor_id,
        )
        if actual_identity != expected_identity:
            raise ConfirmationIdentityConflictError(
                "confirmationId is already bound to another payload"
            )
        return task

    @staticmethod
    def _confirmation_target(
        task: ResearchTaskV12,
        *,
        design_id: str,
        version: int,
        decision: str,
    ) -> ResearchDesignVersion:
        target = next(
            (
                row
                for row in task.research_design_versions
                if row.research_design_id == design_id and row.version == version
            ),
            None,
        )
        if target is None:
            raise ResearchDesignConfirmationError(
                "Design ref does not match a persisted version"
            )
        latest_version = max((row.version for row in task.research_design_versions), default=0)
        if version != latest_version:
            raise ResearchDesignConfirmationError("only the latest Design version can be confirmed")
        if target.status != ResearchDesignStatus.DRAFT:
            raise ResearchDesignConfirmationError("only a draft Design can be confirmed")
        if decision not in {"approved", "rejected", "request_revision"}:
            raise ResearchDesignConfirmationError("unsupported Design confirmation decision")
        if decision == "approved":
            if not target.instances:
                raise ResearchDesignConfirmationError(
                    "ResearchDesign must contain at least one executable substage instance"
                )
            from app.modules.research_design_coverage_confirmation import (
                coverage_allows_confirmation,
            )

            if not coverage_allows_confirmation(target.coverage_report):
                raise ResearchDesignConfirmationError("Design coverage is incomplete")
        return target

    def confirm(
        self,
        *,
        task_id: str,
        owner_id: str,
        workspace_id: str,
        actor_id: str,
        design_id: str,
        version: int,
        confirmation_id: str,
        decision: str,
        expected_task_revision: int,
    ) -> ResearchTaskV12:
        expected_identity = ("research_design", design_id, version, decision, actor_id)
        task = self._repository.load(
            task_id=task_id, owner_id=owner_id, workspace_id=workspace_id
        )
        replay = self._replay_confirmation(
            task,
            confirmation_id=confirmation_id,
            expected_identity=expected_identity,
        )
        if replay is not None:
            return replay
        if task.revision != expected_task_revision:
            raise TaskRevisionConflictError("task_revision_conflict")

        target = self._confirmation_target(
            task,
            design_id=design_id,
            version=version,
            decision=decision,
        )
        now = self._clock()
        confirmation = ConfirmationV12(
            confirmationId=confirmation_id,
            targetType="research_design",
            targetId=design_id,
            targetVersion=version,
            decision=decision,
            actorId=actor_id,
            createdAt=now,
        )
        updates: dict[str, object] = {
            "researchDesignVersions": design_versions_after_confirmation(
                versions=task.research_design_versions,
                design_id=design_id,
                version=version,
                decision=decision,
                confirmed_at=now,
                confirmation_id=confirmation_id,
            ),
            "confirmations": [*task.confirmations, confirmation],
        }
        if decision == "approved":
            snapshot_loader = getattr(self._repository, "load_registry_snapshot", None)
            snapshot = (
                snapshot_loader(task.registry_snapshot_ref.content_hash)
                if callable(snapshot_loader)
                else None
            )
            updates.update(
                approved_design_activation_updates(
                    task=task, design=target, activated_at=now, snapshot=snapshot
                )
            )
        elif decision == "rejected" and task.active_research_design_ref is not None and (
            task.active_research_design_ref.research_design_id == design_id
            and task.active_research_design_ref.version == version
        ):
            updates["activeResearchDesignRef"] = None

        candidate = self._replace_task(task, **updates)
        lifecycle_fact = None
        if decision == "approved":
            lifecycle_fact = ResearchDesignLifecycleFact.DESIGN_APPROVED
            candidate = self._apply_task_lifecycle_fact(
                task,
                candidate,
                fact=lifecycle_fact,
            )
        try:
            return self._repository.save(
                candidate,
                owner_id=owner_id,
                workspace_id=workspace_id,
                expected_task_revision=task.revision,
                lifecycle_fact=lifecycle_fact,
            )
        except TaskRevisionConflictError:
            current = self._repository.load(
                task_id=task_id,
                owner_id=owner_id,
                workspace_id=workspace_id,
            )
            replay = self._replay_confirmation(
                current,
                confirmation_id=confirmation_id,
                expected_identity=expected_identity,
            )
            if replay is not None:
                return replay
            raise

    def _build_design(
        self,
        task: ResearchTaskV12,
        proposal: ResearchDesignProposal,
        planning_attempt_id: str,
        *,
        owner_id: str,
        workspace_id: str,
    ) -> ResearchDesignVersion:
        from app.modules.target_output_ownership import (
            project_completion_requirements,
            validate_target_owners,
        )

        validate_target_owners(
            proposal.target_output_owners,
            proposal.understanding.target_outputs,
            proposal.workflow_responsibility_coverage,
            proposal.target_output_contributors,
        )
        self._preflight_source(task)
        version = max((row.version for row in task.research_design_versions), default=0) + 1
        design_id = (
            task.research_design_versions[0].research_design_id
            if task.research_design_versions
            else _stable_id("design", task.task_id)
        )
        previous = max(task.research_design_versions, key=lambda row: row.version, default=None)
        targets = self._coverage_targets(design_id, proposal)
        target_ids_by_path = {row.source_path: row.target_id for row in targets}
        workflow_admission = self._planning_workflow_admission(
            task=task,
            owner_id=owner_id,
            workspace_id=workspace_id,
        )
        from app.modules.target_output_ownership import validate_frozen_target_owners

        validate_frozen_target_owners(proposal, workflow_admission)
        definitions = self._resolve_definitions(
            task,
            proposal,
            owner_id=owner_id,
            workspace_id=workspace_id,
            workflow_admission=workflow_admission,
        )
        proposal = validate_research_design_planning_depth(
            proposal,
            definition_stage_by_id={
                definition.definition_id: str(definition.stage_key)
                for definition in definitions.values()
            },
        )
        instances = self._build_instances(
            design_id=design_id,
            proposal=proposal,
            definitions=definitions,
            target_ids_by_path=target_ids_by_path,
            previous=previous,
        )
        workflow_responsibility_coverage = self._workflow_responsibility_coverage(
            proposal=proposal,
            instances=instances,
        )
        coverage = self._coverage_report(
            design_id=design_id,
            version=version,
            planning_attempt_id=planning_attempt_id,
            proposal=proposal,
            targets=targets,
            instances=instances,
        )
        try:
            return ResearchDesignVersion(
                researchDesignId=design_id,
                taskId=task.task_id,
                version=version,
                supersedesVersion=previous.version if previous is not None else None,
                status=ResearchDesignStatus.DRAFT,
                planningMode=PlanningMode(proposal.planning_mode),
                planningComplexity=PlanningComplexitySnapshot.model_validate(
                    proposal.planning_complexity.model_dump(by_alias=True)
                ),
                goal=proposal.goal,
                understanding=ResearchUnderstanding.model_validate(
                    proposal.understanding.model_dump(by_alias=True)
                ),
                constraints=proposal.constraints,
                scopeBoundary=ScopeBoundary.model_validate(proposal.scope_boundary.model_dump(by_alias=True)),
                budgetBoundary=BudgetBoundary.model_validate(proposal.budget_boundary.model_dump(by_alias=True)),
                externalActionBoundary=ExternalActionBoundary.model_validate(
                    proposal.external_action_boundary.model_dump(by_alias=True)
                ),
                riskBoundary=RiskBoundary.model_validate(proposal.risk_boundary.model_dump(by_alias=True)),
                workflowProvenanceRef=_workflow_provenance_ref(workflow_admission),
                workflowResponsibilityCoverage=workflow_responsibility_coverage,
                projectCompletionRequirements=project_completion_requirements(
                    design_id=design_id,
                    target_outputs=proposal.understanding.target_outputs,
                    target_output_owners=proposal.target_output_owners,
                    target_output_contributors=proposal.target_output_contributors,
                    id_factory=_stable_id,
                ),
                instances=instances,
                coverageReport=coverage,
                createdAt=self._clock(),
                confirmedAt=None,
                confirmationRef=None,
            )
        except (ValidationError, ValueError) as exc:
            raise ResearchDesignProposalError("proposal cannot form a valid ResearchDesignVersion") from exc

    @staticmethod
    def _workflow_responsibility_coverage(
        *,
        proposal: ResearchDesignProposal,
        instances: list[SubstageInstance],
    ) -> list[WorkflowResponsibilityCoverage]:
        instance_by_key = {
            proposal_row.instance_key: instance
            for proposal_row, instance in zip(proposal.instances, instances, strict=True)
        }
        return [
            WorkflowResponsibilityCoverage(
                responsibilityKey=row.responsibility_key,
                definitionId=row.definition_id,
                requirement=row.requirement,
                coverageMode=row.coverage_mode,
                instanceRefs=[
                    InstanceRef(
                        substageInstanceId=instance_by_key[key].substage_instance_id,
                        instanceRevision=instance_by_key[key].instance_revision,
                    )
                    for key in row.instance_keys
                ],
                reusedResultRefs=row.reused_result_refs,
                rationale=row.rationale,
            )
            for row in proposal.workflow_responsibility_coverage
        ]

    def _resolve_definitions(
        self,
        task: ResearchTaskV12,
        proposal: ResearchDesignProposal,
        *,
        owner_id: str,
        workspace_id: str,
        workflow_admission: Mapping[str, Any] | None,
    ) -> dict[str, ResolvedDefinition]:
        assert self._definition_source is not None
        resolved: dict[str, ResolvedDefinition] = {}
        for row in proposal.instances:
            if not self._definition_source.definition_is_eligible_for_workflow_admission(
                row.definition_id, workflow_admission
            ):
                raise DefinitionResolutionError(
                    "Definition is incompatible with the Task workflow admission"
                )
            definition = self._definition_source.resolve(row.definition_id)
            if definition is None:
                raise DefinitionResolutionError(f"unknown Definition: {row.definition_id}")
            if definition.definition_id != row.definition_id:
                raise DefinitionResolutionError("DefinitionSource returned the wrong definitionId")
            if isinstance(definition.definition_version, bool) or definition.definition_version < 1:
                raise DefinitionResolutionError("DefinitionSource returned an invalid definitionVersion")
            try:
                StageKey(definition.stage_key)
            except ValueError as exc:
                raise DefinitionResolutionError("DefinitionSource returned an invalid Stage") from exc
            resolved[row.instance_key] = definition
        return resolved

    @staticmethod
    def _revision_instance_id(
        *,
        design_id: str,
        instance_key: str,
        prior_by_id: Mapping[str, SubstageInstance],
    ) -> str:
        prefix = "existing_instance__"
        if instance_key.startswith(prefix):
            instance_id = instance_key[len(prefix):].strip()
            if not instance_id or instance_id not in prior_by_id:
                raise ResearchDesignProposalError("ResearchDesign revision references an unknown prior instance")
            return instance_id
        return _stable_id("instance", design_id, instance_key)

    def _build_instances(
        self,
        *,
        design_id: str,
        proposal: ResearchDesignProposal,
        definitions: Mapping[str, ResolvedDefinition],
        target_ids_by_path: Mapping[str, str],
        previous: ResearchDesignVersion | None,
    ) -> list[SubstageInstance]:
        proposals = {row.instance_key: row for row in proposal.instances}
        graph = {
            row.instance_key: [dependency.upstream_instance_key for dependency in row.dependencies]
            for row in proposal.instances
        }
        prior_by_id = (
            {row.substage_instance_id: row for row in previous.instances}
            if previous is not None
            else {}
        )
        built_by_key: dict[str, SubstageInstance] = {}
        for key in _topological_order(graph):
            row = proposals[key]
            instance_id = self._revision_instance_id(
                design_id=design_id,
                instance_key=key,
                prior_by_id=prior_by_id,
            )
            prior = prior_by_id.get(instance_id)
            tentative_revision = prior.instance_revision if prior is not None else 1
            candidate = self._instance_from_proposal(
                instance_id=instance_id,
                row=row,
                definition=definitions[key],
                revision=tentative_revision,
                built_by_key=built_by_key,
                target_ids_by_path=target_ids_by_path,
            )
            if prior is not None and self._instance_semantics(candidate) == self._instance_semantics(prior):
                built_by_key[key] = prior
            elif prior is not None:
                built_by_key[key] = self._instance_from_proposal(
                    instance_id=instance_id,
                    row=row,
                    definition=definitions[key],
                    revision=prior.instance_revision + 1,
                    built_by_key=built_by_key,
                    target_ids_by_path=target_ids_by_path,
                )
            else:
                built_by_key[key] = candidate
        return [built_by_key[row.instance_key] for row in proposal.instances]

    @staticmethod
    def _instance_semantics(instance: SubstageInstance) -> dict[str, Any]:
        payload = instance.model_dump(mode="json", by_alias=True)
        payload.pop("instanceRevision", None)
        return payload

    def _instance_from_proposal(
        self,
        *,
        instance_id: str,
        row: SubstageInstanceProposal,
        definition: ResolvedDefinition,
        revision: int,
        built_by_key: Mapping[str, SubstageInstance],
        target_ids_by_path: Mapping[str, str],
    ) -> SubstageInstance:
        if definition.required_input_refs and not set(definition.required_input_refs).issubset(row.input_refs):
            raise ResearchDesignProposalError(
                "SubstageInstance inputRefs do not satisfy the frozen Definition contract"
            )
        if definition.artifact_types and (
            not row.expected_artifact_types
            or not set(row.expected_artifact_types).issubset(definition.artifact_types)
        ):
            raise ResearchDesignProposalError(
                "SubstageInstance expectedArtifactTypes do not satisfy the frozen Definition contract"
            )
        for criterion in row.acceptance_criteria:
            if (
                criterion.required_artifact_type is not None
                and definition.artifact_types
                and criterion.required_artifact_type not in definition.artifact_types
            ):
                raise ResearchDesignProposalError(
                    "AcceptanceCriterion artifact type is outside the frozen Definition contract"
                )
            if (
                criterion.evaluator_ref is not None
                and (definition.evaluator_refs or definition.criterion_refs)
                and criterion.evaluator_ref not in (definition.evaluator_refs or definition.criterion_refs)
            ):
                raise ResearchDesignProposalError(
                    "AcceptanceCriterion evaluator is outside the frozen Definition contract"
                )
        criteria: list[AcceptanceCriterion] = []
        for criterion in row.acceptance_criteria:
            unknown_paths = [path for path in criterion.coverage_source_paths if path not in target_ids_by_path]
            if unknown_paths:
                raise ResearchDesignProposalError("AcceptanceCriterion references an unknown coverage source path")
            criteria.append(
                AcceptanceCriterion(
                    criterionId=_stable_id("criterion", instance_id, criterion.criterion_key),
                    description=criterion.description,
                    criterionKind=AcceptanceCriterionKind(criterion.criterion_kind),
                    coverageTargetIds=[target_ids_by_path[path] for path in criterion.coverage_source_paths],
                    requiredArtifactType=criterion.required_artifact_type,
                    judgmentRequirement=JudgmentRequirement(criterion.judgment_requirement),
                    evaluatorType=EvaluatorType(criterion.evaluator_type),
                    evaluatorRef=criterion.evaluator_ref,
                )
            )
        dependencies: list[SubstageDependency] = []
        for dependency in row.dependencies:
            upstream = built_by_key[dependency.upstream_instance_key]
            dependencies.append(
                SubstageDependency(
                    dependencyId=_stable_id("dependency", instance_id, dependency.dependency_key),
                    upstreamInstanceRef=InstanceRef(
                        substageInstanceId=upstream.substage_instance_id,
                        instanceRevision=upstream.instance_revision,
                    ),
                    conditionType=DependencyConditionType(dependency.condition_type),
                    requiredArtifactTypes=dependency.required_artifact_types,
                    confirmationTargetRef=dependency.confirmation_target_ref,
                )
            )
        try:
            return SubstageInstance(
                substageInstanceId=instance_id,
                instanceRevision=revision,
                substageDefinitionId=definition.definition_id,
                substageDefinitionVersion=definition.definition_version,
                stageKey=StageKey(definition.stage_key),
                objective=row.objective,
                inputRefs=row.input_refs,
                expectedArtifactTypes=row.expected_artifact_types,
                acceptanceCriteria=criteria,
                dependencies=dependencies,
                sequenceHint=row.sequence_hint,
                repeatable_reentry_authority=(
                    definition.repeatable_reentry_authority
                ),
            )
        except (ValidationError, ValueError) as exc:
            raise ResearchDesignProposalError("invalid SubstageInstance proposal") from exc

    def _coverage_targets(
        self,
        design_id: str,
        proposal: ResearchDesignProposal,
    ) -> list[CoverageTarget]:
        artifact_types_by_source_path: dict[str, list[str]] = {}
        for instance in proposal.instances:
            for criterion in instance.acceptance_criteria:
                artifact_type = criterion.required_artifact_type
                if artifact_type is None or artifact_type not in instance.expected_artifact_types:
                    continue
                for source_path in criterion.coverage_source_paths:
                    values = artifact_types_by_source_path.setdefault(source_path, [])
                    if artifact_type not in values:
                        values.append(artifact_type)
        rows: list[tuple[CoverageSourceType, str, str, list[str], str | None]] = []
        rows.extend(
            (
                CoverageSourceType.TARGET_OUTPUT,
                f"understanding.targetOutputs[{index}]",
                value,
                artifact_types_by_source_path.get(f"understanding.targetOutputs[{index}]", []),
                None,
            )
            for index, value in enumerate(proposal.understanding.target_outputs)
        )
        rows.extend(
            (
                CoverageSourceType.CONSTRAINT,
                f"constraints[{index}]",
                value,
                [],
                f"constraint:{value}",
            )
            for index, value in enumerate(proposal.constraints)
        )
        rows.extend(
            (
                CoverageSourceType.EVIDENCE_BOUNDARY,
                f"understanding.evidenceBoundaries[{index}]",
                value,
                [],
                f"evidence-boundary:{value}",
            )
            for index, value in enumerate(proposal.understanding.evidence_boundaries)
        )
        rows.extend(
            (
                CoverageSourceType.EXTERNAL_ACTION_BOUNDARY,
                f"externalActionBoundary.confirmationRequiredKinds[{index}]",
                value,
                [],
                f"external-action:{value}",
            )
            for index, value in enumerate(proposal.external_action_boundary.confirmation_required_kinds)
        )
        rows.extend(
            (
                CoverageSourceType.RISK_BOUNDARY,
                f"riskBoundary.prohibitedActions[{index}]",
                value,
                [],
                f"risk-boundary:{value}",
            )
            for index, value in enumerate(proposal.risk_boundary.prohibited_actions)
        )
        rows.extend(
            (
                CoverageSourceType.RISK_BOUNDARY,
                f"riskBoundary.humanGateTriggers[{index}]",
                value,
                [],
                f"human-gate:{value}",
            )
            for index, value in enumerate(proposal.risk_boundary.human_gate_triggers)
        )
        return [
            CoverageTarget(
                targetId=_stable_id("target", design_id, source_path),
                sourceType=source_type,
                sourcePath=source_path,
                description=description,
                requiredArtifactTypes=artifact_types,
                enforcementRef=enforcement_ref,
                required=True,
            )
            for source_type, source_path, description, artifact_types, enforcement_ref in rows
        ]

    def _coverage_report(
        self,
        *,
        design_id: str,
        version: int,
        planning_attempt_id: str,
        proposal: ResearchDesignProposal,
        targets: list[CoverageTarget],
        instances: list[SubstageInstance],
    ) -> ResearchDesignCoverageReport:
        proposals = {row.instance_key: row for row in proposal.instances}
        if len(instances) != len(proposal.instances):
            raise ResearchDesignProposalError("compiled instance count does not match ResearchDesign proposal")
        instances_by_key = {
            row.instance_key: instance
            for row, instance in zip(proposal.instances, instances, strict=True)
        }
        criteria_by_key = {
            f"{instance_key}:{criterion.criterion_key}": next(
                item
                for item in instances_by_key[instance_key].acceptance_criteria
                if item.criterion_id
                == _stable_id("criterion", instances_by_key[instance_key].substage_instance_id, criterion.criterion_key)
            )
            for instance_key, instance_proposal in proposals.items()
            for criterion in instance_proposal.acceptance_criteria
        }
        candidates = {row.source_path: row for row in proposal.coverage_claim_candidates}
        claims: list[CoverageClaim] = []
        uncovered: list[str] = []
        for target in targets:
            candidate = candidates.get(target.source_path)
            claimed_instances = (
                [instances_by_key[key] for key in candidate.instance_keys if key in instances_by_key]
                if candidate is not None
                else []
            )
            claimed_criteria = (
                [criteria_by_key[key] for key in candidate.acceptance_criterion_keys if key in criteria_by_key]
                if candidate is not None
                else []
            )
            full = bool(candidate and claimed_instances and claimed_criteria) and any(
                self._criterion_covers(target, criterion, claimed_instances)
                for criterion in claimed_criteria
            )
            from app.modules.target_output_ownership import non_work_target_paths
            full = full or target.source_path in non_work_target_paths(
                proposal.target_output_owners,
                proposal.workflow_responsibility_coverage,
                proposal.target_output_contributors,
            )
            mode = CoverageMode.FULL if full else (CoverageMode.PARTIAL if claimed_instances else CoverageMode.NONE)
            if not full:
                uncovered.append(target.target_id)
            derivation_refs = ["coverage-policy.v1:server-derived"]
            if target.enforcement_ref is not None:
                derivation_refs.append(target.enforcement_ref)
            claims.append(
                CoverageClaim(
                    targetId=target.target_id,
                    instanceRefs=[
                        InstanceRef(
                            substageInstanceId=row.substage_instance_id,
                            instanceRevision=row.instance_revision,
                        )
                        for row in claimed_instances
                    ],
                    coverageMode=mode,
                    acceptanceCriterionRefs=[row.criterion_id for row in claimed_criteria],
                    derivationRuleRefs=derivation_refs,
                    rationale=(candidate.rationale if candidate is not None else "No valid coverage candidate was supplied."),
                )
            )
        overloaded = []
        maximum = proposal.scope_boundary.max_substage_instances
        if maximum is not None and len(instances) > maximum:
            overloaded = [
                InstanceRef(
                    substageInstanceId=row.substage_instance_id,
                    instanceRevision=row.instance_revision,
                )
                for row in instances[maximum:]
            ]
        status = (
            CoverageStatus.PASSED_STRUCTURAL
            if not uncovered and not overloaded
            else CoverageStatus.NEEDS_REVISION
        )
        report = ResearchDesignCoverageReport(
            coverageReportId=_stable_id("coverage", design_id, version),
            designRef=ResearchDesignRef(researchDesignId=design_id, version=version),
            planningAttemptId=planning_attempt_id,
            targets=targets,
            claims=claims,
            uncoveredRequiredTargetIds=uncovered,
            overloadedInstanceRefs=overloaded,
            missingDependencyRefs=[],
            status=status,
            evaluationTier=PlanningMode(proposal.planning_mode),
            evaluationMode=CoverageEvaluationMode.DETERMINISTIC,
            semanticEvaluationRef=None,
            automaticRepairCount=0,
            policyVersion="coverage-policy.v1",
            evaluatedAt=self._clock(),
        )
        from app.modules.research_design_coverage_confirmation import (
            has_only_executable_target_gaps,
        )

        if status == CoverageStatus.NEEDS_REVISION and has_only_executable_target_gaps(report):
            report.status = CoverageStatus.EXECUTABLE_WITH_GAPS
        return report

    @staticmethod
    def _criterion_covers(
        target: CoverageTarget,
        criterion: AcceptanceCriterion,
        instances: list[SubstageInstance],
    ) -> bool:
        if target.target_id not in criterion.coverage_target_ids:
            return False
        expected_kind = {
            CoverageSourceType.TARGET_OUTPUT: AcceptanceCriterionKind.TARGET_OUTPUT,
            CoverageSourceType.CONSTRAINT: AcceptanceCriterionKind.CONSTRAINT_COMPLIANCE,
            CoverageSourceType.EVIDENCE_BOUNDARY: AcceptanceCriterionKind.EVIDENCE_BOUNDARY_COMPLIANCE,
            CoverageSourceType.EXTERNAL_ACTION_BOUNDARY: AcceptanceCriterionKind.EXTERNAL_ACTION_GUARD,
            CoverageSourceType.RISK_BOUNDARY: AcceptanceCriterionKind.RISK_GUARD,
        }[target.source_type]
        if criterion.criterion_kind != expected_kind and not (
            target.source_type == CoverageSourceType.TARGET_OUTPUT
            and criterion.criterion_kind == AcceptanceCriterionKind.PROFESSIONAL_JUDGMENT
        ):
            return False
        if target.source_type == CoverageSourceType.TARGET_OUTPUT:
            artifact = criterion.required_artifact_type
            if artifact is None or artifact not in target.required_artifact_types:
                return False
            if not any(artifact in row.expected_artifact_types for row in instances):
                return False
        return True

    @staticmethod
    def _replace_task(task: ResearchTaskV12, **updates: object) -> ResearchTaskV12:
        payload = task.model_dump(mode="json", by_alias=True)
        for key, value in updates.items():
            field = ResearchTaskV12.model_fields.get(key)
            alias = field.alias if field is not None and field.alias is not None else key
            if isinstance(value, BaseModel):
                payload[alias] = value.model_dump(mode="json", by_alias=True)
            elif isinstance(value, list):
                payload[alias] = [
                    item.model_dump(mode="json", by_alias=True) if isinstance(item, BaseModel) else item
                    for item in value
                ]
            else:
                payload[alias] = value
        return ResearchTaskV12.model_validate(payload)

    @staticmethod
    def _apply_task_lifecycle_fact(
        task: ResearchTaskV12,
        candidate: ResearchTaskV12,
        *,
        fact: ResearchDesignLifecycleFact,
    ) -> ResearchTaskV12:
        transition = reconcile_task_lifecycle(
            task.model_dump(mode="json", by_alias=True),
            fact=fact,
        )
        if transition is None:
            raise ResearchDesignServiceError("research_design_lifecycle_transition_rejected")
        return ResearchDesignService._replace_task(candidate, status=transition.task_status)


__all__ = [
    "AdditionalActionConflictError",
    "ConfirmationIdentityConflictError",
    "CoverageClaimCandidate",
    "DefinitionResolutionError",
    "DefinitionSource",
    "DefinitionSourceUnavailableError",
    "PlannerProviderResult",
    "PlannerTransportUnknown",
    "PlanningAttemptReplayError",
    "ResearchDesignConfirmationError",
    "ResearchDesignProposal",
    "ResearchDesignProposalError",
    "ResearchDesignService",
    "ResearchDesignServiceError",
    "ResolvedDefinition",
    "new_research_task",
    "validate_research_design_proposal",
]
