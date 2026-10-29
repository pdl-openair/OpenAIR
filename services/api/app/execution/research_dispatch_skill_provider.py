"""Canonical direct-Skill adapter for one frozen research dispatch.

The durable dispatch repository owns claim, Attempt, Submission authority,
external-action fencing and result persistence.  This adapter performs no
transport during preflight and delegates the one physical execution only to
the existing durable Skill runtime after authorization has been marked started.
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from app.execution.durable_llm_execution_policy import (
    DECLARATION_KEY as DURABLE_LLM_EXECUTION_POLICY_KEY,
)
from app.execution.durable_llm_execution_policy import (
    normalize_resolved_durable_llm_execution_policy,
)
from app.execution.durable_skill_runtime import (
    execute_production_durable_skill,
    normalize_external_action_evidence,
    registered_skill_capability_contract,
    resolve_registered_durable_skill_runtime,
)
from app.execution.research_dispatch_context import build_direct_skill_execution_request
from app.execution.research_dispatch_provider import (
    DispatchProviderKnownRejected,
    DispatchProviderPermanentPreflightError,
    DispatchProviderResultUnknown,
    DispatchSubmissionCompleted,
    DispatchSubmissionQueryResult,
    DispatchSubmissionRequest,
)
from app.execution.research_input_contract_gate import (
    InputContractGateFailure,
    SkillInputRequest,
    execution_input_candidate_from_authority,
    resolve_and_validate_execution_input,
    validate_registered_skill_input_snapshot,
)
from app.execution.skill_contract_policy import (
    safe_skill_failure_detail as _safe_failure_detail,
)
from app.execution.skill_contract_policy import (
    validate_action_input,
)
from app.models.research_orchestration import ExecutionPurpose, ExecutionStrategy
from app.models.result_protocol import merge_result_protocols, result_protocol_usable
from app.modules.provider_registry import ProviderSubmissionSafety
from app.modules.registry_snapshot_repository import execution_snapshot_matches_planning
from app.modules.research_orchestration_repository import (
    ResearchOrchestrationPersistenceError,
    ResearchOrchestrationRepository,
)
from app.modules.skill_binding_source import (
    SkillBindingSourceError,
    canonical_binding_handoff_evidence,
)


def _execution_method_skill_assignment_is_valid(
    decision: Any, assignment: Any, work_unit: Any, snapshot: Mapping[str, Any]
) -> bool:
    if decision.strategy != ExecutionStrategy.EXECUTION_METHOD:
        return True
    method_ref = decision.execution_method_ref
    if not isinstance(method_ref, Mapping):
        return False
    substage_rows = [
        row
        for row in snapshot.get("definitions", {}).get("substages", [])
        if row.get("definitionId") == work_unit.substage_definition_id
        and row.get("definitionVersion") == work_unit.substage_definition_version
    ]
    method = substage_rows[0].get("executionMethod") if len(substage_rows) == 1 else None
    if not isinstance(method, Mapping) or method_ref != {
        "methodId": method.get("methodId"),
        "methodVersion": method.get("methodVersion"),
    }:
        return False
    role_id = assignment.execution_method_role_id
    roles = [row for row in method.get("roles") or [] if row.get("roleId") == role_id]
    if len(roles) != 1 or "skill" not in (roles[0].get("capabilityKinds") or []):
        return False
    role_ref = f"{method.get('methodId')}:{role_id}"
    skill_rows = [
        row
        for row in snapshot.get("definitions", {}).get("skills", [])
        if row.get("skillId") == assignment.executor_ref.executor_id
        and row.get("definitionVersion") == assignment.executor_ref.definition_version
    ]
    if len(skill_rows) != 1 or role_ref not in (skill_rows[0].get("executionMethodRoles") or []):
        return False
    role = roles[0]
    iteration = getattr(assignment, "execution_method_iteration", None) or 1
    assignments_by_role: dict[str, list[Any]] = {}
    for candidate_assignment in decision.assignments:
        candidate_role = getattr(candidate_assignment, "execution_method_role_id", None)
        if candidate_role is not None:
            assignments_by_role.setdefault(candidate_role, []).append(candidate_assignment)
    for rows in assignments_by_role.values():
        rows.sort(key=lambda row: getattr(row, "execution_method_iteration", None) or 1)
    transitions = [
        row
        for row in method.get("reentryTransitions") or []
        if role_id in (row.get("repeatRoleIds") or [])
    ]
    if len(transitions) > 1:
        return False
    transition = transitions[0] if transitions else None
    repeat_roles = set(transition.get("repeatRoleIds") or []) if transition else set()
    expected_dependencies: list[str] = []
    for dependency in role.get("dependsOn") or []:
        rows = assignments_by_role.get(dependency, [])
        if dependency in repeat_roles:
            resolved = next(
                (
                    row
                    for row in rows
                    if (getattr(row, "execution_method_iteration", None) or 1) == iteration
                ),
                None,
            )
        else:
            eligible = [
                row
                for row in rows
                if (getattr(row, "execution_method_iteration", None) or 1) <= iteration
            ]
            resolved = eligible[-1] if eligible else None
        if resolved is not None:
            expected_dependencies.append(resolved.assignment_id)
    if (
        transition is not None
        and iteration > 1
        and transition.get("restartRoleId") == role_id
    ):
        prior = next(
            (
                row
                for row in assignments_by_role.get(
                    str(transition.get("afterRoleId") or ""), []
                )
                if (getattr(row, "execution_method_iteration", None) or 1)
                == iteration - 1
            ),
            None,
        )
        if prior is None:
            return False
        if prior.assignment_id not in expected_dependencies:
            expected_dependencies.append(prior.assignment_id)
    stop_conditions = [
        dict(condition)
        for condition in method.get("stopConditions") or []
        if condition.get("afterRoleId") == role_id
    ]
    expected_control = {
        "requirement": role.get("requirement"),
        "releasePolicy": dict(role.get("releasePolicy") or {}),
        "failurePolicy": role.get("failurePolicy"),
        "parallelism": role.get("parallelism"),
        "stopConditionIds": [str(row.get("conditionId") or "") for row in stop_conditions],
        "stopConditions": stop_conditions,
        "qualityConstraints": list(method.get("qualityConstraints") or []),
    }
    outgoing_reentry = [
        dict(row)
        for row in method.get("reentryTransitions") or []
        if row.get("afterRoleId") == role_id
    ]
    if outgoing_reentry:
        expected_control["reentryTransitions"] = outgoing_reentry
    work_iteration = getattr(work_unit, "execution_method_iteration", None) or 1
    return bool(
        assignment.dependency_assignment_ids == expected_dependencies
        and assignment.execution_method_role_control == expected_control
        and work_unit.execution_method_ref == decision.execution_method_ref
        and work_unit.execution_method_role_id == role_id
        and work_iteration == iteration
        and work_unit.execution_method_role_control == expected_control
    )


_DIRECT_SKILL_CAPABILITIES = ProviderSubmissionSafety(
    acceptsIdempotencyKey=False,
    returnsReceiptOrRemoteRunId=False,
    queryByIdempotencyKey=False,
    queryByReceiptOrRemoteRunId=False,
    submissionMode="single_attempt_no_replay",
)


@dataclass(frozen=True)
class FrozenDirectSkillDispatch:
    skill_id: str
    definition_version: int
    artifactContract: str
    artifactSchemaDigest: str
    artifactMimeType: str
    logicalProviderId: str | None
    modelProfile: str | None
    resolvedExecutionPolicy: Mapping[str, Any] | None
    runtimeKind: str
    requiresExternalAction: bool
    adapterId: str
    evidenceContractVersion: str
    orchestrationPlan: Mapping[str, Any]


@dataclass(frozen=True)
class PreparedDirectSkillResearchDispatch:
    capabilities: ProviderSubmissionSafety
    skill_id: str
    definition_version: int
    snapshot_definition: Mapping[str, Any]
    source_binding_refs: tuple[tuple[str, int], ...]
    repository: ResearchOrchestrationRepository
    runtime: Mapping[str, Any]
    execute: Callable[[dict[str, Any]], dict[str, Any]]
    provider_id: str = "direct_skill_runtime"
    adapter: str = "durable_skill_runtime"

    def submit(self, request: DispatchSubmissionRequest) -> DispatchSubmissionCompleted:
        frozen = frozen_skill_request_for_dispatch(
            request,
            skill_id=self.skill_id,
            definition_version=self.definition_version,
            snapshot_definition=self.snapshot_definition,
            source_binding_refs=self.source_binding_refs,
            repository=self.repository,
            prepared_runtime=self.runtime,
        )
        parameters = frozen.orchestrationPlan.get("resolvedParameters")
        if not isinstance(parameters, Mapping):
            raise DispatchProviderKnownRejected("input_contract_not_prepared")
        if frozen.requiresExternalAction:
            try:
                validate_action_input(self.snapshot_definition, parameters)
            except ValueError as exc:
                raise DispatchProviderKnownRejected(str(exc)) from exc
        binding_input_evidence = frozen.orchestrationPlan.get("bindingInputEvidence")
        try:
            execution_request = build_direct_skill_execution_request(
                request,
                frozen,
                parameters,
                binding_input_evidence,
                self.snapshot_definition,
            )
        except ValueError as exc:
            raise DispatchProviderKnownRejected(str(exc)) from exc
        if self.execute is execute_production_durable_skill:
            result = self.execute(execution_request, prepared_runtime=self.runtime)
        else:
            result = self.execute(execution_request)
        from app.execution.research_internal_loop_pending import validate_pending_submission

        pending = validate_pending_submission(result, request=request, repository=self.repository)
        if pending is not None:
            raise pending
        try:
            return _completed_submission(result, frozen=frozen)
        except (DispatchProviderKnownRejected, DispatchProviderResultUnknown):
            raise
        except Exception as exc:
            # External uncertainty must be expressed explicitly by the runtime.
            # DTO, serialization and local result-wrapping failures are local
            # contract failures and must never become external_result_unknown.
            raise DispatchProviderKnownRejected(
                "skill_result_local_processing_failed",
                failure_detail={"failedStage": "dispatch_result_wrapping"},
            ) from exc

    def query_by_idempotency_key(self, _idempotency_key: str) -> DispatchSubmissionQueryResult:
        raise DispatchProviderResultUnknown("single_attempt_result_unknown")


class DirectSkillResearchDispatchProvider:
    def __init__(
        self,
        *,
        skill_id: str,
        definition_version: int,
        snapshot_definition: Mapping[str, Any],
        source_binding_refs: tuple[tuple[str, int], ...],
        repository: ResearchOrchestrationRepository | None = None,
        runtime_resolver: Callable[..., dict[str, Any] | None] = resolve_registered_durable_skill_runtime,
        execute: Callable[[dict[str, Any]], dict[str, Any]] = execute_production_durable_skill,
    ) -> None:
        self._skill_id = skill_id
        self._definition_version = definition_version
        self._snapshot_definition = dict(snapshot_definition)
        self._source_binding_refs = tuple(source_binding_refs)
        self._repository = repository or ResearchOrchestrationRepository()
        self._runtime_resolver = runtime_resolver
        self._execute = execute

    def preflight(self) -> PreparedDirectSkillResearchDispatch:
        runtime = self._runtime_resolver(
            self._skill_id,
            definition_version=self._definition_version,
            snapshot_definition=self._snapshot_definition,
        )
        if runtime is None:
            raise DispatchProviderPermanentPreflightError("skill_runtime_unavailable")
        return PreparedDirectSkillResearchDispatch(
            capabilities=_DIRECT_SKILL_CAPABILITIES,
            skill_id=self._skill_id,
            definition_version=self._definition_version,
            snapshot_definition=self._snapshot_definition,
            source_binding_refs=self._source_binding_refs,
            repository=self._repository,
            runtime=runtime,
            execute=self._execute,
        )


def frozen_skill_request_for_dispatch(
    request: DispatchSubmissionRequest,
    *,
    skill_id: str,
    definition_version: int,
    snapshot_definition: Mapping[str, Any],
    source_binding_refs: tuple[tuple[str, int], ...],
    repository: ResearchOrchestrationRepository,
    prepared_runtime: Mapping[str, Any] | None = None,
    allow_closed_decision_for_result_recovery: bool = False,
) -> FrozenDirectSkillDispatch:
    task = repository.load(
        task_id=request.task_id,
        owner_id=request.owner_id,
        workspace_id=request.workspace_id,
    )
    work_unit = next((row for row in task.work_units if row.work_unit_id == request.work_unit_id), None)
    if work_unit is None or work_unit.model_dump(mode="json", by_alias=True) != dict(request.payload):
        raise DispatchProviderKnownRejected("dispatch_work_unit_snapshot_mismatch")
    decision = next(
        (row for row in task.execution_decisions if row.execution_decision_id == request.execution_decision_id),
        None,
    )
    assignment = None if decision is None else next(
        (row for row in decision.assignments if row.assignment_id == work_unit.assignment_id),
        None,
    )
    settled_result_recovery = bool(
        allow_closed_decision_for_result_recovery
        and decision is not None
        and decision.status.value == "closed"
        and getattr(getattr(decision, "outcome", None), "value", None)
        in {"succeeded", "partial"}
    )
    if (
        decision is None
        or decision.strategy not in {
            ExecutionStrategy.DIRECT_SKILL,
            ExecutionStrategy.SKILL_COMPOSITION,
            ExecutionStrategy.EXECUTION_METHOD,
        }
        or (decision.status.value != "dispatched" and not settled_result_recovery)
        or work_unit.execution_decision_id != decision.execution_decision_id
        or work_unit.registry_snapshot_ref != decision.registry_snapshot_ref
        or assignment is None
        or assignment.executor_ref != work_unit.executor_ref
        or assignment.responsible_actor_ref != work_unit.responsible_actor_ref
        or assignment.role.value != "primary"
        or assignment.allowed_skill_refs != work_unit.allowed_skill_refs
        or assignment.executor_ref.executor_type != "skill"
        or assignment.executor_ref.executor_id != skill_id
        or assignment.executor_ref.definition_version != definition_version
        or assignment.responsible_actor_ref.actor_type != "orchestrator"
        or assignment.allowed_skill_refs
    ):
        raise DispatchProviderKnownRejected("dispatch_assignment_snapshot_mismatch")
    if decision.purpose == ExecutionPurpose.COMPLETION:
        reviewers = [row for row in decision.assignments if row.role.value == "reviewer"]
        primaries = [row for row in decision.assignments if row.role.value == "primary"]
        if decision.completion_policy is None:
            raise DispatchProviderKnownRejected("dispatch_review_contract_missing")
        if decision.completion_policy.review_requirement.value == "none":
            if reviewers or decision.completion_policy.reviewer_assignment_ids:
                raise DispatchProviderKnownRejected("dispatch_review_contract_missing")
        else:
            reviewed_assignment_id = (
                primaries[-1].assignment_id
                if decision.strategy == ExecutionStrategy.SKILL_COMPOSITION and primaries
                else assignment.assignment_id
            )
            if (
                len(reviewers) != 1
                or decision.completion_policy.reviewer_assignment_ids != [reviewers[0].assignment_id]
                or reviewers[0].dependency_assignment_ids != [reviewed_assignment_id]
                or reviewers[0].executor_ref.executor_type != "agent"
                or reviewers[0].executor_ref.executor_id != "result_reliability_audit"
                or reviewers[0].executor_ref.definition_version != 1
                or reviewers[0].responsible_actor_ref.actor_type != "agent"
                or reviewers[0].responsible_actor_ref.actor_id
                != reviewers[0].executor_ref.executor_id
                or reviewers[0].responsible_actor_ref.definition_version
                != reviewers[0].executor_ref.definition_version
            ):
                raise DispatchProviderKnownRejected("dispatch_review_contract_missing")
    snapshot = repository.load_registry_snapshot(work_unit.registry_snapshot_ref.content_hash)
    if not execution_snapshot_matches_planning(
        task.registry_snapshot_ref.model_dump(mode="json", by_alias=True),
        decision.registry_snapshot_ref.model_dump(mode="json", by_alias=True),
        snapshot,
    ):
        raise DispatchProviderKnownRejected("dispatch_assignment_snapshot_mismatch")
    if decision.strategy == ExecutionStrategy.EXECUTION_METHOD:
        if not _execution_method_skill_assignment_is_valid(
            decision, assignment, work_unit, snapshot
        ):
            raise DispatchProviderKnownRejected("dispatch_execution_method_mismatch")
    if decision.strategy == ExecutionStrategy.SKILL_COMPOSITION:
        substage_definitions = [
            row
            for row in snapshot.get("definitions", {}).get("substages", [])
            if row.get("definitionId") == work_unit.substage_definition_id
            and row.get("definitionVersion") == work_unit.substage_definition_version
        ]
        composition = (
            substage_definitions[0].get("executionComposition")
            if len(substage_definitions) == 1
            else None
        )
        expected_sequence = [
            (row.get("skillId"), row.get("definitionVersion"))
            for row in (composition or {}).get("requiredSkillRefs", [])
        ]
        actual_sequence = [
            (row.executor_ref.executor_id, row.executor_ref.definition_version)
            for row in decision.assignments
            if row.role.value == "primary"
        ]
        if (
            not isinstance(composition, Mapping)
            or composition.get("semantics") != "ordered_required"
            or expected_sequence != actual_sequence
        ):
            raise DispatchProviderKnownRejected("dispatch_skill_composition_mismatch")
    definitions = [
        row
        for row in snapshot.get("definitions", {}).get("skills", [])
        if row.get("skillId") == skill_id and row.get("definitionVersion") == definition_version
    ]
    if len(definitions) != 1 or definitions[0] != dict(snapshot_definition):
        raise DispatchProviderKnownRejected("dispatch_skill_definition_mismatch")
    output_contract = definitions[0].get("outputContract")
    artifact_types = (
        [str(value).strip() for value in output_contract.get("artifactTypes") or [] if str(value).strip()]
        if isinstance(output_contract, Mapping)
        else []
    )
    produced_artifact_type = artifact_types[0] if len(artifact_types) == 1 else None
    if produced_artifact_type is None and not artifact_types:
        # Historical execution snapshots may predate the public outputContract
        # projection while still freezing the formal Skill capability contract.
        # Recover only a single producer type declared by that same immutable
        # snapshot; never consult the mutable live registry and never guess.
        try:
            _input_contract, capability_output, _judgment_support = (
                registered_skill_capability_contract(definitions[0].get("sourceContract"))
            )
        except ValueError:
            capability_output = {}
        capability_types = [
            str(value).strip()
            for value in capability_output.get("artifactTypes") or []
            if str(value).strip()
        ]
        if capability_output.get("status") == "known" and len(capability_types) == 1:
            produced_artifact_type = capability_types[0]
    refs = {(row.binding_id, row.binding_version) for row in assignment.source_binding_refs}
    if refs != set(source_binding_refs) or not refs:
        raise DispatchProviderKnownRejected("dispatch_skill_binding_mismatch")
    bindings = [
        row
        for row in snapshot.get("bindings", {}).get("skills", [])
        if (row.get("bindingId"), row.get("bindingVersion")) in refs
        and row.get("skillId") == skill_id
        and row.get("skillDefinitionVersion") == definition_version
        and row.get("definitionId") == work_unit.substage_definition_id
        and row.get("definitionVersion") == work_unit.substage_definition_version
        and row.get("enabled")
    ]
    if {(row.get("bindingId"), row.get("bindingVersion")) for row in bindings} != refs or len(bindings) != 1:
        raise DispatchProviderKnownRejected("dispatch_skill_binding_mismatch")
    skill_binding = bindings[0]
    if isinstance(prepared_runtime, Mapping):
        runtime = dict(prepared_runtime)
    else:
        runtime = resolve_registered_durable_skill_runtime(
            skill_id,
            definition_version=definition_version,
            snapshot_definition=dict(snapshot_definition),
        )
    if runtime is None:
        raise DispatchProviderKnownRejected("skill_runtime_unavailable")
    try:
        candidate = execution_input_candidate_from_authority(
            task=task,
            decision=decision,
            work_unit=work_unit,
        )
        frozen_parameters = getattr(work_unit, "resolved_parameters", None)
        if frozen_parameters is not None:
            resolved_parameters = validate_registered_skill_input_snapshot(
                snapshot=snapshot,
                request=SkillInputRequest(
                    task_id=candidate.task_id,
                    strategy=candidate.strategy,
                    skill_id=candidate.capability_id,
                    definition_version=candidate.definition_version,
                ),
                resolved_input=frozen_parameters,
                skill_binding=skill_binding,
            )
        else:
            # Backward compatibility for WorkUnits persisted before input snapshots
            # were introduced. New WorkUnits always freeze direct-Skill input.
            input_context = repository.read_direct_skill_input_context_for_dispatch(
                task_id=request.task_id,
                owner_id=request.owner_id,
                workspace_id=request.workspace_id,
                execution_decision_id=request.execution_decision_id,
                work_unit_id=request.work_unit_id,
            )
            resolved_parameters = resolve_and_validate_execution_input(
                snapshot=snapshot,
                candidate=candidate,
                input_context=input_context,
                skill_binding=skill_binding,
            )
    except InputContractGateFailure as exc:
        raise DispatchProviderKnownRejected(exc.code) from exc
    except (ValueError, ResearchOrchestrationPersistenceError) as exc:
        raise DispatchProviderKnownRejected("input_contract_context_invalid") from exc

    source_contract = definitions[0].get("sourceContract")
    declared_execution_policy = (
        source_contract.get(DURABLE_LLM_EXECUTION_POLICY_KEY)
        if isinstance(source_contract, Mapping)
        else None
    )
    persisted_execution_policy = getattr(work_unit, "resolved_execution_policy", None)
    if declared_execution_policy is None:
        if persisted_execution_policy is not None:
            raise DispatchProviderKnownRejected("dispatch_execution_policy_snapshot_mismatch")
        resolved_execution_policy = None
    else:
        if persisted_execution_policy is None:
            raise DispatchProviderKnownRejected("dispatch_execution_policy_snapshot_missing")
        try:
            resolved_execution_policy = normalize_resolved_durable_llm_execution_policy(
                persisted_execution_policy
            )
        except ValueError as exc:
            raise DispatchProviderKnownRejected("dispatch_execution_policy_invalid") from exc
        if resolved_execution_policy["adapterId"] != str(runtime["adapterId"]):
            raise DispatchProviderKnownRejected("dispatch_execution_policy_adapter_mismatch")
    try:
        binding_input_evidence = canonical_binding_handoff_evidence(skill_binding)
    except SkillBindingSourceError as exc:
        raise DispatchProviderKnownRejected("dispatch_skill_binding_mismatch") from exc
    scientific_state_projection = (
        skill_binding.get("scientificStateProjection")
        if isinstance(skill_binding, Mapping)
        else None
    )
    if scientific_state_projection is None and isinstance(source_contract, Mapping):
        # Old snapshots pinned before projection moved to the binding carry it
        # at the skill level. Keep them functional; new snapshots read the
        # binding exclusively (the skill sourceContract no longer declares it).
        scientific_state_projection = source_contract.get("scientific_state_projection")
    if (
        assignment.role.value != "reviewer"
        and isinstance(scientific_state_projection, Mapping)
        and not work_unit.expected_artifact_types
    ):
        raise DispatchProviderKnownRejected(
            "scientific_state_result_identity_missing"
        )

    return FrozenDirectSkillDispatch(
        skill_id=skill_id,
        definition_version=definition_version,
        artifactContract=str(runtime["artifactContract"]),
        artifactSchemaDigest=str(runtime["artifactSchemaDigest"]),
        artifactMimeType=str(runtime["artifactMimeType"]),
        logicalProviderId=(
            str(resolved_execution_policy["providerId"])
            if resolved_execution_policy is not None
            else (str(runtime["logicalProviderId"]) if runtime.get("logicalProviderId") else None)
        ),
        modelProfile=(
            str(resolved_execution_policy["modelProfile"])
            if resolved_execution_policy is not None
            and resolved_execution_policy.get("modelProfile")
            else (str(runtime["modelProfile"]) if runtime.get("modelProfile") else None)
        ),
        resolvedExecutionPolicy=resolved_execution_policy,
        runtimeKind=str(runtime["runtimeKind"]),
        requiresExternalAction=bool(runtime["requiresExternalAction"]),
        adapterId=str(runtime["adapterId"]),
        evidenceContractVersion=str(runtime["evidenceContractVersion"]),
        orchestrationPlan={
            "assignmentRole": assignment.role.value,
            **(
                {"artifactKind": produced_artifact_type}
                if assignment.role.value != "reviewer" and produced_artifact_type is not None
                else {}
            ),
            "instruction": work_unit.instruction,
            "inputRefs": list(assignment.input_refs),
            "inputArtifactRefs": list(work_unit.input_artifact_refs),
            "inputMemoryRefs": list(work_unit.input_memory_refs),
            "stopConditions": list(work_unit.stop_conditions),
            "skillId": skill_id,
            "definitionVersion": definition_version,
            "resolvedParameters": resolved_parameters,
            **(
                {"scientificStateProjection": scientific_state_projection}
                if isinstance(scientific_state_projection, Mapping)
                else {}
            ),
            "bindingInputEvidence": binding_input_evidence,
            **(
                {"externalSourceRecords": source_contract["external_source_records"]}
                if isinstance(source_contract, Mapping)
                and source_contract.get("external_source_records") is not None
                else {}
            ),
        },
    )


def validate_completed_skill_dispatch_result(
    request: DispatchSubmissionRequest,
    result: DispatchSubmissionCompleted,
    *,
    repository: ResearchOrchestrationRepository,
    allow_closed_decision_for_result_recovery: bool = False,
) -> FrozenDirectSkillDispatch:
    task = repository.load(
        task_id=request.task_id,
        owner_id=request.owner_id,
        workspace_id=request.workspace_id,
    )
    work_unit = next((row for row in task.work_units if row.work_unit_id == request.work_unit_id), None)
    if work_unit is None or work_unit.executor_ref.executor_type != "skill":
        raise DispatchProviderKnownRejected("dispatch_skill_work_unit_missing")
    snapshot = repository.load_registry_snapshot(work_unit.registry_snapshot_ref.content_hash)
    definition = next(
        (
            row
            for row in snapshot.get("definitions", {}).get("skills", [])
            if row.get("skillId") == work_unit.executor_ref.executor_id
            and row.get("definitionVersion") == work_unit.executor_ref.definition_version
        ),
        None,
    )
    decision = next(
        (row for row in task.execution_decisions if row.execution_decision_id == request.execution_decision_id),
        None,
    )
    assignment = None if decision is None else next(
        (row for row in decision.assignments if row.assignment_id == work_unit.assignment_id),
        None,
    )
    if definition is None or assignment is None:
        raise DispatchProviderKnownRejected("dispatch_skill_snapshot_missing")
    frozen = frozen_skill_request_for_dispatch(
        request,
        skill_id=work_unit.executor_ref.executor_id,
        definition_version=int(work_unit.executor_ref.definition_version or 0),
        snapshot_definition=definition,
        source_binding_refs=tuple(
            (row.binding_id, row.binding_version) for row in assignment.source_binding_refs
        ),
        repository=repository,
        allow_closed_decision_for_result_recovery=(
            allow_closed_decision_for_result_recovery
        ),
    )
    if result.mime_type != frozen.artifactMimeType:
        raise DispatchProviderKnownRejected("skill_result_mime_type_mismatch")
    if not result.content.strip():
        raise DispatchProviderKnownRejected("skill_result_content_empty")
    _validate_result_evidence(
        frozen,
        result.external_action_evidence,
        content=result.content,
    )
    return frozen


def _completed_submission(
    result: Mapping[str, Any],
    *,
    frozen: FrozenDirectSkillDispatch,
) -> DispatchSubmissionCompleted:
    state = str(result.get("state") or "").strip()
    if state == "unknown" and result.get("externalResultUnknown") is True:
        raise DispatchProviderResultUnknown(
            "single_attempt_result_unknown",
            remote_run_id=str(result.get("providerExecutionId") or "").strip() or None,
        )
    if state == "failed":
        code = str(result.get("failureClassification") or "skill_execution_failed").strip()
        raise DispatchProviderKnownRejected(code, failure_detail=_safe_failure_detail(result))
    if state != "succeeded" or result.get("relatedSkillId") != frozen.skill_id:
        raise DispatchProviderKnownRejected("skill_result_contract_invalid")
    artifact = result.get("artifactPayload")
    presentation = artifact.get("presentation") if isinstance(artifact, Mapping) else None
    if not isinstance(artifact, Mapping) or not isinstance(presentation, Mapping):
        raise DispatchProviderKnownRejected("skill_result_contract_invalid")
    presentation_metadata = presentation.get("metadata")
    if presentation_metadata is None:
        presentation_metadata = {}
    if not isinstance(presentation_metadata, Mapping):
        raise DispatchProviderKnownRejected("skill_result_metadata_invalid")
    try:
        content_bytes = base64.b64decode(str(artifact.get("contentBase64") or ""), validate=True)
        content = content_bytes.decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        raise DispatchProviderKnownRejected("skill_result_content_invalid") from None
    mime_type = str(artifact.get("contentType") or "").strip()
    title = str(presentation.get("title") or "").strip()
    if (
        not content.strip()
        or not title
        or mime_type != frozen.artifactMimeType
        or str(presentation.get("mimeType") or "").strip() != mime_type
        or presentation.get("contentSize") != len(content_bytes)
        or str(presentation.get("contentSha256") or "").strip().lower()
        != hashlib.sha256(content_bytes).hexdigest()
    ):
        raise DispatchProviderKnownRejected("skill_result_contract_invalid")
    try:
        evidence = normalize_external_action_evidence(result.get("externalActionEvidence", []))
    except ValueError:
        raise DispatchProviderKnownRejected("skill_result_external_evidence_invalid") from None
    _validate_result_evidence(frozen, evidence, content=content)
    provider_model: str | None = None
    if frozen.runtimeKind == "llm_reasoning":
        provider_model = str(result.get("providerModel") or "").strip() or None
        if provider_model is None:
            raise DispatchProviderKnownRejected("skill_result_provider_model_missing")
        if (
            isinstance(frozen.resolvedExecutionPolicy, Mapping)
            and provider_model != str(frozen.resolvedExecutionPolicy.get("model") or "").strip()
        ):
            raise DispatchProviderKnownRejected("skill_result_provider_model_mismatch")
    metadata_warnings = presentation_metadata.get("warnings") or []
    if not isinstance(metadata_warnings, list):
        metadata_warnings = ["skill_result_metadata_gap:warnings"]
    elif any(not isinstance(value, str) for value in metadata_warnings):
        metadata_warnings = [
            *(value for value in metadata_warnings if isinstance(value, str)),
            "skill_result_metadata_gap:warnings",
        ]
    protocol_value = presentation_metadata.get("resultProtocol")
    try:
        if protocol_value is not None and not isinstance(protocol_value, Mapping):
            raise ValueError("result protocol is not an object")
        result_protocol = merge_result_protocols(protocol_value, warnings=metadata_warnings)
    except ValueError:
        result_protocol = merge_result_protocols(
            None, warnings=[*metadata_warnings, "skill_result_metadata_gap:resultProtocol"],
        )
    if not result_protocol_usable(result_protocol):
        raise DispatchProviderKnownRejected("skill_result_protocol_unusable")
    try:
        return DispatchSubmissionCompleted(
            title=title,
            mime_type=mime_type,
            content=content,
            provider_id="direct_skill_runtime",
            adapter="durable_skill_runtime",
            usage=None,
            external_action_evidence=evidence,
            model=provider_model,
            result_protocol=result_protocol,
            warnings=tuple(result_protocol.get("warnings") or ()),
        )
    except ValueError:
        raise DispatchProviderKnownRejected("skill_result_dispatch_contract_invalid") from None


def _validate_result_evidence(
    frozen: FrozenDirectSkillDispatch,
    evidence: tuple[Mapping[str, str], ...],
    *,
    content: str,
) -> None:
    if not frozen.requiresExternalAction:
        if evidence:
            raise DispatchProviderKnownRejected("skill_result_unexpected_external_evidence")
        return
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    if (
        frozen.runtimeKind not in {"external_adapter", "skill_script"}
        or frozen.evidenceContractVersion not in {
            "external-action-evidence/v1",
            "skill-execution-evidence/v1",
        }
        or len(evidence) != 1
        or evidence[0].get("adapter") != frozen.adapterId
        or evidence[0].get("outcome") not in {"completed", "partial"}
        or evidence[0].get("contentSha256") != digest
        or evidence[0].get("evidenceRef")
        != f"skill-result:{frozen.skill_id}:sha256:{digest}"
    ):
        raise DispatchProviderKnownRejected("skill_result_external_evidence_missing")



__all__ = [
    "DirectSkillResearchDispatchProvider",
    "FrozenDirectSkillDispatch",
    "PreparedDirectSkillResearchDispatch",
    "frozen_skill_request_for_dispatch",
    "validate_completed_skill_dispatch_result",
]
