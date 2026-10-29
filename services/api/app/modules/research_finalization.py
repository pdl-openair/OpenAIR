"""Minimal, deterministic P0 finalization from already validated facts.

This module is intentionally not a report generator.  It never invokes a
provider, changes a ResearchDesign, or derives new scientific claims.  Runtime
comparisons/recommendations must be executed before this function is called.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from app.models.research_orchestration import (
    READ_ONLY_LEGACY_FINAL_RESULT_AUTHORITY,
    AcceptanceCriterionKind,
    ArtifactState,
    CapabilityCallStatus,
    JudgmentRequirement,
    ResearchTaskV12,
    TaskStatus,
    WorkUnitSourceMode,
    WorkUnitStatus,
)
from app.modules.research_completion_decision import (
    current_project_completion_decision,
    expected_project_completion_evaluation,
    record_project_completion_decision,
)
from app.modules.research_round_end import derive_round_end
from app.modules.research_scientific_state_authority import (
    latest_scientific_state_for_design,
)
from app.modules.scientific_completion_integrity import (
    INTEGRITY_CONTRACT_VERSION,
    LEGACY_INTEGRITY_CONTRACT_VERSION,
    expected_final_result_fields,
    validation_outcome_payload,
)
from app.modules.target_output_evidence import target_output_has_unqualified_evidence


def record_scientific_state_from_durable_facts(
    task: ResearchTaskV12,
    design: Any,
    *,
    occurred_at: datetime,
) -> tuple[ResearchTaskV12, dict[str, Any]]:
    """Return the immutable snapshot represented by the current durable facts.

    A ResearchDesign can legitimately accumulate many ScientificState versions.
    The snapshot is replayed only when its fact content is identical; it is never
    overwritten merely because it belongs to the same design version.
    """

    payload = task.model_dump(mode="json", by_alias=True)
    timestamp = occurred_at.astimezone(timezone.utc).isoformat()
    work_by_id = {row.work_unit_id: row for row in task.work_units}
    call_by_id = {row.capability_call_id: row for row in task.capability_calls}
    superseded_execution_decision_ids = {
        row.supersedes_execution_decision_id
        for row in task.execution_decisions
        if row.supersedes_execution_decision_id is not None
    }
    usable = [
        artifact for artifact in task.artifacts
        if artifact.artifact_state is ArtifactState.USABLE
        and (work := work_by_id.get(artifact.work_unit_id)) is not None
        and work.source_mode is WorkUnitSourceMode.LIVE
        and work.status is WorkUnitStatus.COMPLETED
        and work.research_design_version == design.version
        and (
            artifact.source_capability_call_id is None
            or (
                (call := call_by_id.get(artifact.source_capability_call_id)) is not None
                and call.status is CapabilityCallStatus.SUCCEEDED
                and call.work_unit_id == work.work_unit_id
                and artifact.artifact_id in call.output_artifact_refs
            )
        )
    ]
    artifact_refs = sorted(row.artifact_id for row in usable)
    usable_by_instance: dict[tuple[str, int], set[str]] = {}
    for artifact in usable:
        work = work_by_id[artifact.work_unit_id]
        usable_by_instance.setdefault(
            (work.substage_instance_id, work.substage_instance_revision), set()
        ).add(artifact.artifact_type)
    runtime_by_instance = {
        (row.substage_instance_id, row.instance_revision): row
        for row in task.substage_runtime_states
    }
    substage_completion: list[dict[str, Any]] = []
    for instance in design.instances:
        instance_ref = (instance.substage_instance_id, instance.instance_revision)
        available_types = usable_by_instance.get(
            instance_ref, set()
        )
        instance_works = [
            work for work in task.work_units
            if (work.substage_instance_id, work.substage_instance_revision) == instance_ref
            and work.research_design_version == design.version
        ]
        current_instance_works = [
            work
            for work in instance_works
            if work.execution_decision_id not in superseded_execution_decision_ids
        ]
        terminal_failure = any(
            work.status in {WorkUnitStatus.FAILED, WorkUnitStatus.BLOCKED, WorkUnitStatus.CANCELLED}
            for work in current_instance_works
        )
        external_unknown = any(
            work.status is WorkUnitStatus.EXTERNAL_RESULT_UNKNOWN
            for work in current_instance_works
        )
        approved_acceptance = any(
            row.interaction_type == "acceptance_recorded"
            and row.verdict is not None
            and row.verdict.decision.value == "approved"
            and row.execution_decision_id in {
                work.execution_decision_id for work in instance_works
            }
            for row in task.agent_interaction_records
        )
        criteria: list[dict[str, Any]] = []
        unresolved_ids: list[str] = []
        unmet_ids: list[str] = []
        for criterion in instance.acceptance_criteria:
            required_type = criterion.required_artifact_type
            artifact_present = required_type is None or required_type in available_types
            if criterion.judgment_requirement is JudgmentRequirement.DETERMINISTIC:
                status = "satisfied" if artifact_present else (
                    "unmet" if terminal_failure else "unresolved"
                )
                if (
                    status == "satisfied"
                    and criterion.criterion_kind is AcceptanceCriterionKind.TARGET_OUTPUT
                    and target_output_has_unqualified_evidence(
                        task,
                        usable,
                        required_type=required_type,
                        instance_ref=instance_ref,
                        design_version=design.version,
                    )
                ):
                    # Delivery still settles; missing goal proof remains visible.
                    status = "unresolved"
            elif criterion.judgment_requirement is JudgmentRequirement.RESEARCH_SYNTHESIS:
                instance_work_ids = {work.work_unit_id for work in instance_works}
                instance_artifacts = [
                    artifact for artifact in usable
                    if artifact.work_unit_id in instance_work_ids
                ]
                instance_call_ids = {
                    call.capability_call_id
                    for call in task.capability_calls
                    if call.work_unit_id in instance_work_ids
                    and call.status is CapabilityCallStatus.SUCCEEDED
                }
                result_kinds = {
                    str((artifact.validator_result or {}).get("resultKind") or "").strip()
                    for artifact in instance_artifacts
                }
                synthesis_evidence = bool(
                    artifact_present
                    and len(instance_artifacts) >= 2
                    and instance_call_ids
                    and any(
                        artifact.source_capability_call_id in instance_call_ids
                        and artifact.source_refs
                        for artifact in instance_artifacts
                    )
                    and result_kinds & {"research_result", "comparison", "route_recommendation"}
                    and "evidence" in result_kinds
                )
                status = "satisfied" if synthesis_evidence and approved_acceptance else (
                    "unmet" if terminal_failure else "unresolved"
                )
            else:
                # A professional/human criterion remains unresolved until an
                # explicit durable acceptance fact exists; an Artifact alone is
                # evidence, not authority for that judgment.
                status = "satisfied" if artifact_present and approved_acceptance else (
                    "unmet" if terminal_failure else "unresolved"
                )
            criteria.append({
                "criterionId": criterion.criterion_id,
                "status": status,
                "requiredArtifactType": required_type,
                "judgmentRequirement": criterion.judgment_requirement.value,
                "evaluatorType": criterion.evaluator_type.value,
                "evaluatorRef": criterion.evaluator_ref,
            })
            if status == "unresolved":
                unresolved_ids.append(criterion.criterion_id)
            elif status == "unmet":
                unmet_ids.append(criterion.criterion_id)
        runtime = runtime_by_instance.get(instance_ref)
        execution_issue_codes: list[str] = []
        if runtime is None:
            execution_issue_codes.append(
                f"runtime:{instance.substage_instance_id}:missing"
            )
        elif runtime.status.value != "completed":
            execution_issue_codes.append(
                f"runtime:{instance.substage_instance_id}:{runtime.status.value}"
            )
        for work in current_instance_works:
            if work.status not in {WorkUnitStatus.COMPLETED}:
                execution_issue_codes.append(
                    f"work_unit:{work.work_unit_id}:{work.status.value}"
                )
        degradation_statuses = {
            "failed",
            "blocked_recoverable",
            "insufficient_evidence",
        }
        has_execution_degradation = (
            terminal_failure
            or external_unknown
            or (
                runtime is not None
                and runtime.status.value in degradation_statuses
            )
        )
        substage_completion.append(
            {
                "substageInstanceId": instance.substage_instance_id,
                "instanceRevision": instance.instance_revision,
                "criteria": criteria,
                "completionCriteriaSatisfied": bool(criteria)
                and not unresolved_ids
                and not unmet_ids,
                "unresolvedCriterionIds": unresolved_ids,
                "unmetCriterionIds": unmet_ids,
                "gaps": [
                    *[f"criterion:{criterion_id}" for criterion_id in unmet_ids],
                    *execution_issue_codes,
                ],
                "uncertainty": [
                    *[f"criterion:{criterion_id}" for criterion_id in unresolved_ids],
                    *(
                        [f"runtime:{instance.substage_instance_id}:external_result_unknown"]
                        if external_unknown
                        else []
                    ),
                ],
                "needsAction": bool(unresolved_ids),
                "runtimeStatus": None if runtime is None else runtime.status.value,
                "runtimeStatusReasonCode": None if runtime is None else runtime.status_reason_code,
                "hasExecutionDegradation": has_execution_degradation,
            }
        )
    states = payload.setdefault("scientificStates", [])
    latest_state = latest_scientific_state_for_design(
        states,
        research_design_version=design.version,
    )
    scientific_facts = (
        deepcopy(latest_state.get("scientificFacts") or {})
        if latest_state is not None
        else {}
    )
    scientific_source_refs = (
        list(latest_state.get("sourceRefs") or [])
        if latest_state is not None
        else []
    )
    from app.modules.target_output_completion import approved_artifact_refs

    execution_state = {
        "validatedArtifactRefs": artifact_refs,
        "approvedArtifactRefs": approved_artifact_refs(task, artifact_refs),
        "terminalSubstages": bool(substage_completion) and all(
            row["completionCriteriaSatisfied"] for row in substage_completion
        ),
        "substageCompletion": substage_completion,
    }
    state = next(
        (
            row for row in reversed(states)
            if row.get("researchDesignVersion") == design.version
            and row.get("scientificFacts") == scientific_facts
            and row.get("executionState") == execution_state
            and row.get("sourceRefs") == scientific_source_refs
        ),
        None,
    )
    if state is None:
        next_version = max((int(row["scientificStateVersion"]) for row in states), default=0) + 1
        state = {
            "scientificStateVersion": next_version,
            "researchDesignVersion": design.version,
            "scientificFacts": scientific_facts,
            "executionState": execution_state,
            "sourceRefs": scientific_source_refs,
            "createdAt": timestamp,
        }
        states.append(state)
        # A new immutable state makes any prior semantic authority historical.
        # Clear it only inside this finalization transaction; the caller builds
        # the new current decision/result/authority before completing the Task.
        payload["finalResultAuthority"] = None
        if payload.get("status") == "completed":
            payload.update({"status": "running", "finishedAt": None})
    return ResearchTaskV12.model_validate(payload), state


def finalize_from_validated_facts(
    task: ResearchTaskV12,
    design: Any,
    *,
    occurred_at: datetime,
) -> ResearchTaskV12:
    """Assemble a Final Result only after the project CompletionDecision permits it."""

    if (
        task.final_result_authority is not None
        and task.final_result_authority.integrity_contract_version
        == LEGACY_INTEGRITY_CONTRACT_VERSION
    ):
        raise ValueError(
            f"{READ_ONLY_LEGACY_FINAL_RESULT_AUTHORITY} cannot be finalized"
        )
    snapshotted, state = record_scientific_state_from_durable_facts(
        task, design, occurred_at=occurred_at
    )
    evaluation = expected_project_completion_evaluation(design, state)
    if (
        evaluation["canComplete"] is not True
        and derive_round_end(snapshotted, design, state) is None
    ):
        payload = snapshotted.model_dump(mode="json", by_alias=True)
        payload["finalResultAuthority"] = None
        if payload.get("status") == "completed":
            payload.update({"status": "running", "finishedAt": None})
        return ResearchTaskV12.model_validate(payload)

    decided, decision = record_project_completion_decision(
        snapshotted,
        design,
        state,
        occurred_at=occurred_at,
    )
    payload = decided.model_dump(mode="json", by_alias=True)
    timestamp = occurred_at.astimezone(timezone.utc).isoformat()
    artifact_refs = list(state["executionState"]["validatedArtifactRefs"])
    state_version = int(state["scientificStateVersion"])
    decision_id = decision.completion_decision_id
    disposition = decision.disposition.value
    results = payload.setdefault("finalResults", [])
    result_id = f"final:{decision_id}"
    result = next((row for row in results if row["finalResultId"] == result_id), None)
    if result is None:
        result_fields = expected_final_result_fields(
            state, decision=decision.model_dump(mode="json", by_alias=True)
        )
        result = {
            "finalResultId": result_id,
            "completionDecisionId": decision_id,
            "scientificStateVersion": state_version,
            "disposition": disposition,
            **result_fields,
            "validatedAt": timestamp,
            "validationOutcome": validation_outcome_payload(validated_at=timestamp),
        }
        results.append(result)
    payload["finalResultAuthority"] = {
        "mode": "artifact_set",
        "researchDesignVersion": design.version,
        "resultArtifactRefs": artifact_refs,
        "sourceArtifacts": [],
        "assembledAt": timestamp,
        "finalResultId": result_id,
        "integrityContractVersion": INTEGRITY_CONTRACT_VERSION,
    }
    return ResearchTaskV12.model_validate(payload)


def settle_finalized_lifecycle(
    task: ResearchTaskV12, design: Any, state: Any, *, occurred_at: datetime
) -> ResearchTaskV12:
    """The existing lifecycle consumer, with goal achievement kept independent."""
    completion = current_project_completion_decision(task, design, state)
    payload = task.model_dump(mode="json", by_alias=True)
    if completion is not None and (
        completion.can_complete is True or completion.round_end is not None
    ):
        payload.update(status="completed", finishedAt=occurred_at.isoformat())
    else:
        payload.update(status="running", finishedAt=None, finalResultAuthority=None)
    return ResearchTaskV12.model_validate(payload)
