"""Definition-owned repeatable execution policy.

ScientificState creation, executor selection, and recovery remain in their
existing authorities. Repeat execution reactivates the confirmed project task;
it never creates another SubstageInstance identity.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256

from app.models.research_orchestration import (
    ArtifactState,
    ExecutionDecision,
    ExecutionDecisionOutcome,
    ExecutionDecisionStatus,
    InstanceRef,
    ReentryExecutionInstance,
    ResearchDesignStatus,
    ResearchDesignVersion,
    ResearchTaskV12,
    ScientificStateV1,
    SubstageInstance,
    SubstageRuntimeStatus,
    TaskStatus,
    WorkUnitStatus,
    execution_instance_for_ref,
    repeatable_reentry_fingerprint,
)

_TERMINAL_RUNTIME_STATUSES = frozenset(
    {
        SubstageRuntimeStatus.COMPLETED,
        SubstageRuntimeStatus.FAILED,
        SubstageRuntimeStatus.CANCELLED,
        SubstageRuntimeStatus.SKIPPED,
    }
)


@dataclass(frozen=True)
class _TriggerBoundary:
    design: ResearchDesignVersion
    decision: ExecutionDecision
    instance: SubstageInstance
    state: ScientificStateV1
    validated_artifact_refs: tuple[str, ...]


def _stable_id(prefix: str, *parts: str) -> str:
    digest = sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:32]
    return f"{prefix}:{digest}"


def _trigger_boundary(
    task: ResearchTaskV12,
    completed_execution_decision_id: str,
) -> _TriggerBoundary | None:
    active_ref = task.active_research_design_ref
    if active_ref is None:
        return None
    design = next(
        (
            row
            for row in task.research_design_versions
            if row.research_design_id == active_ref.research_design_id
            and row.version == active_ref.version
            and row.status == ResearchDesignStatus.CONFIRMED
        ),
        None,
    )
    decision = next(
        (
            row
            for row in task.execution_decisions
            if row.execution_decision_id == completed_execution_decision_id
        ),
        None,
    )
    if (
        design is None
        or decision is None
        or decision.status != ExecutionDecisionStatus.CLOSED
        or decision.research_design_version != design.version
        or decision.outcome
        not in {
            ExecutionDecisionOutcome.SUCCEEDED,
            ExecutionDecisionOutcome.PARTIAL,
            ExecutionDecisionOutcome.PROGRESSED,
        }
    ):
        return None
    instance = execution_instance_for_ref(
        task,
        design,
        InstanceRef(
            substageInstanceId=decision.substage_instance_id,
            instanceRevision=decision.substage_instance_revision,
        ),
    )
    state = max(
        (
            row
            for row in task.scientific_states
            if row.research_design_version == design.version
        ),
        key=lambda row: row.scientific_state_version,
        default=None,
    )
    if instance is None or state is None:
        return None
    completed_work_ids = {
        row.work_unit_id
        for row in task.work_units
        if row.execution_decision_id == decision.execution_decision_id
        and row.status == WorkUnitStatus.COMPLETED
    }
    validated_refs = tuple(
        sorted(
            {
                row.artifact_id
                for row in task.artifacts
                if row.work_unit_id in completed_work_ids
                and row.artifact_state == ArtifactState.USABLE
            }
        )
    )
    if not validated_refs:
        return None
    if not _is_new_triggering_decision(task, design, decision, instance):
        return None
    return _TriggerBoundary(design, decision, instance, state, validated_refs)


def _is_new_triggering_decision(
    task: ResearchTaskV12,
    design: ResearchDesignVersion,
    decision: ExecutionDecision,
    instance: SubstageInstance,
) -> bool:
    """Guard against re-arming from a stale decision close.

    The triggering decision must close strictly after the latest completed
    work of every downstream template it would re-arm (templates whose
    ``repeatableReentryAuthority.triggerDefinitionIds`` contains the triggering
    instance's definition id). This replaces the former ``sourceRefs``
    membership check (which required the triggering artifact to appear in the
    latest ScientificState's sourceRefs — a property only S1/S8/feedback-API
    materializations satisfy, leaving S4-S7 unable to re-arm their downstream
    repeatables). The temporal guard preserves the anti-loop property (an old
    closed decision cannot re-arm a dependent whose latest work is newer) using
    only existing persisted timestamps.
    """
    decision_close = decision.closed_at
    if decision_close is None:
        return False
    triggering_definition_id = instance.substage_definition_id
    downstream_definition_ids = {
        template.substage_definition_id
        for template in design.instances
        if template.repeatable_reentry_authority is not None
        and triggering_definition_id
        in template.repeatable_reentry_authority.trigger_definition_ids
    }
    if not downstream_definition_ids:
        return True
    for work in task.work_units:
        if work.research_design_version != design.version:
            continue
        work_instance = execution_instance_for_ref(
            task,
            design,
            InstanceRef(
                substageInstanceId=work.substage_instance_id,
                instanceRevision=work.substage_instance_revision,
            ),
        )
        if (
            work_instance is not None
            and work_instance.substage_definition_id in downstream_definition_ids
            and work.status == WorkUnitStatus.COMPLETED
            and work.finished_at is not None
            and work.finished_at >= decision_close
        ):
            return False
    return True


def _previous_relevant_instance(
    task: ResearchTaskV12,
    *,
    design_version: int,
    template: SubstageInstance,
) -> tuple[SubstageInstance, int]:
    prior = [
        row
        for row in task.reentry_execution_instances
        if row.research_design_version == design_version
        and row.instance.substage_definition_id == template.substage_definition_id
        and row.instance.substage_definition_version
        == template.substage_definition_version
        and row.template_instance_ref.substage_instance_id
        == template.substage_instance_id
        and row.template_instance_ref.instance_revision == template.instance_revision
    ]
    if not prior:
        return template, 0
    latest = max(prior, key=lambda row: row.triggering_scientific_state_version)
    return latest.instance, latest.triggering_scientific_state_version


def _legacy_materialization_for_template(
    task: ResearchTaskV12,
    boundary: _TriggerBoundary,
    template: SubstageInstance,
    *,
    occurred_at: datetime,
    existing_fingerprints: set[str],
) -> ReentryExecutionInstance | None:
    authority = template.repeatable_reentry_authority
    if (
        authority is None
        or boundary.instance.substage_definition_id
        not in authority.trigger_definition_ids
    ):
        return None
    previous, previous_state_version = _previous_relevant_instance(
        task,
        design_version=boundary.design.version,
        template=template,
    )
    previous_runtime = next(
        (
            row
            for row in task.substage_runtime_states
            if row.substage_instance_id == previous.substage_instance_id
            and row.instance_revision == previous.instance_revision
        ),
        None,
    )
    if (
        previous_runtime is None
        or previous_runtime.status not in _TERMINAL_RUNTIME_STATUSES
        or boundary.state.scientific_state_version <= previous_state_version
    ):
        return None
    fingerprint = repeatable_reentry_fingerprint(
        design=boundary.design,
        template=template,
        scientific_state_version=boundary.state.scientific_state_version,
    )
    if fingerprint in existing_fingerprints:
        return None
    instance_payload = template.model_dump(mode="json", by_alias=True)
    instance_payload.update(
        {
            "substageInstanceId": _stable_id("instance-reentry", fingerprint),
            "instanceRevision": 1,
            "inputRefs": [
                *template.input_refs,
                f"scientific-state:{boundary.state.scientific_state_version}",
            ],
        }
    )
    return ReentryExecutionInstance(
        researchDesignVersion=boundary.design.version,
        instance=SubstageInstance.model_validate(instance_payload),
        templateInstanceRef=InstanceRef(
            substageInstanceId=template.substage_instance_id,
            instanceRevision=template.instance_revision,
        ),
        triggeringScientificStateVersion=boundary.state.scientific_state_version,
        triggeringExecutionDecisionId=boundary.decision.execution_decision_id,
        previousRelevantInstanceRef=InstanceRef(
            substageInstanceId=previous.substage_instance_id,
            instanceRevision=previous.instance_revision,
        ),
        triggeringStateSourceRefs=list(boundary.validated_artifact_refs),
        reentryFingerprint=fingerprint,
        createdAt=occurred_at.astimezone(UTC),
    )


def _repeatable_runtime_for_template(
    task: ResearchTaskV12,
    boundary: _TriggerBoundary,
    template: SubstageInstance,
) -> tuple[str, int] | None:
    authority = template.repeatable_reentry_authority
    if (
        authority is None
        or boundary.instance.substage_definition_id
        not in authority.trigger_definition_ids
    ):
        return None
    runtime = next(
        (
            row
            for row in task.substage_runtime_states
            if row.substage_instance_id == template.substage_instance_id
            and row.instance_revision == template.instance_revision
        ),
        None,
    )
    if runtime is None or runtime.status not in _TERMINAL_RUNTIME_STATUSES:
        return None
    return template.substage_instance_id, template.instance_revision


def materialize_repeatable_definition_reentries(
    task: ResearchTaskV12,
    *,
    completed_execution_decision_id: str,
    occurred_at: datetime,
) -> ResearchTaskV12:
    """Preserve the legacy re-entry record shape for compatibility reads.

    Current continuation never calls this function. Legacy records are excluded
    from the canonical execution-instance projection and cannot authorize a new
    project research task.
    """

    boundary = _trigger_boundary(task, completed_execution_decision_id)
    if boundary is None:
        return task
    fingerprints = {
        row.reentry_fingerprint for row in task.reentry_execution_instances
    }
    materializations: list[ReentryExecutionInstance] = []
    for template in boundary.design.instances:
        materialization = _legacy_materialization_for_template(
            task,
            boundary,
            template,
            occurred_at=occurred_at,
            existing_fingerprints=fingerprints,
        )
        if materialization is None:
            continue
        materializations.append(materialization)
        fingerprints.add(materialization.reentry_fingerprint)
    if not materializations:
        return task

    timestamp = occurred_at.astimezone(UTC).isoformat()
    payload = task.model_dump(mode="json", by_alias=True)
    payload["reentryExecutionInstances"] = [
        *payload.get("reentryExecutionInstances", []),
        *(row.model_dump(mode="json", by_alias=True) for row in materializations),
    ]
    payload["substageRuntimeStates"] = [
        *payload["substageRuntimeStates"],
        *(
            {
                "substageInstanceId": row.instance.substage_instance_id,
                "instanceRevision": row.instance.instance_revision,
                "status": SubstageRuntimeStatus.READY.value,
                "activeExecutionDecisionId": None,
                "workUnitIds": [],
                "artifactRefs": [],
                "statusReasonCode": "repeatable_definition_reentry",
                "updatedAt": timestamp,
            }
            for row in materializations
        ),
    ]
    payload.update(
        {
            "status": TaskStatus.RUNNING.value,
            "finishedAt": None,
            "finalResultAuthority": None,
        }
    )
    return ResearchTaskV12.model_validate(payload)


def reactivate_repeatable_definition_instances(
    task: ResearchTaskV12,
    *,
    completed_execution_decision_id: str,
    occurred_at: datetime,
) -> ResearchTaskV12:
    """Reactivate eligible confirmed tasks without minting project-task IDs."""

    boundary = _trigger_boundary(task, completed_execution_decision_id)
    if boundary is None:
        return task
    repeatable_refs: list[tuple[str, int]] = []
    for template in boundary.design.instances:
        runtime_ref = _repeatable_runtime_for_template(
            task,
            boundary,
            template,
        )
        if runtime_ref is not None:
            repeatable_refs.append(runtime_ref)
    if not repeatable_refs:
        return task

    timestamp = occurred_at.astimezone(UTC).isoformat()
    payload = task.model_dump(mode="json", by_alias=True)
    repeatable_ref_set = set(repeatable_refs)
    for runtime in payload["substageRuntimeStates"]:
        runtime_ref = (
            runtime["substageInstanceId"],
            runtime["instanceRevision"],
        )
        if runtime_ref not in repeatable_ref_set:
            continue
        runtime.update(
            {
                "status": SubstageRuntimeStatus.READY.value,
                "activeExecutionDecisionId": None,
                "workUnitIds": [],
                "artifactRefs": [],
                "statusReasonCode": "repeatable_definition_reentry",
                "updatedAt": timestamp,
            }
        )
    payload.update(
        {
            "status": TaskStatus.RUNNING.value,
            "finishedAt": None,
            "finalResultAuthority": None,
        }
    )
    return ResearchTaskV12.model_validate(payload)


def reactivate_confirmed_definition_for_scientific_state(
    task: ResearchTaskV12,
    *,
    substage_definition_id: str,
    scientific_state_version: int,
    occurred_at: datetime,
    reason_code: str,
) -> ResearchTaskV12:
    '''Converge one confirmed conditional responsibility to READY for new facts.'''

    active_ref = task.active_research_design_ref
    if active_ref is None:
        raise ValueError('active_research_design_missing')
    designs = [
        row
        for row in task.research_design_versions
        if row.research_design_id == active_ref.research_design_id
        and row.version == active_ref.version
        and row.status == ResearchDesignStatus.CONFIRMED
    ]
    if len(designs) != 1:
        raise ValueError('active_research_design_missing')
    states = [
        row
        for row in task.scientific_states
        if row.research_design_version == active_ref.version
    ]
    if (
        not states
        or max(states, key=lambda row: row.scientific_state_version)
        .scientific_state_version
        != scientific_state_version
    ):
        raise ValueError('current_scientific_state_missing')
    instances = [
        row
        for row in designs[0].instances
        if row.substage_definition_id == substage_definition_id
    ]
    if len(instances) != 1:
        raise ValueError('conditional_definition_not_in_research_design')
    instance = instances[0]
    runtimes = [
        row
        for row in task.substage_runtime_states
        if row.substage_instance_id == instance.substage_instance_id
        and row.instance_revision == instance.instance_revision
    ]
    if len(runtimes) != 1:
        raise ValueError('conditional_definition_runtime_missing')
    if runtimes[0].status in {
        SubstageRuntimeStatus.QUEUED,
        SubstageRuntimeStatus.RUNNING,
        SubstageRuntimeStatus.REVIEWING,
    }:
        raise ValueError('conditional_definition_execution_in_flight')

    payload = task.model_dump(mode='json', by_alias=True)
    runtime = next(
        row
        for row in payload['substageRuntimeStates']
        if row['substageInstanceId'] == instance.substage_instance_id
        and row['instanceRevision'] == instance.instance_revision
    )
    runtime.update(
        {
            'status': SubstageRuntimeStatus.READY.value,
            'activeExecutionDecisionId': None,
            'workUnitIds': [],
            'artifactRefs': [],
            'statusReasonCode': reason_code,
            'updatedAt': occurred_at.astimezone(UTC).isoformat(),
        }
    )
    payload.update(
        {
            'status': TaskStatus.RUNNING.value,
            'finishedAt': None,
            'finalResultAuthority': None,
        }
    )
    return ResearchTaskV12.model_validate(payload)


__all__ = [
    'reactivate_confirmed_definition_for_scientific_state',
    "materialize_repeatable_definition_reentries",
    "reactivate_repeatable_definition_instances",
]
