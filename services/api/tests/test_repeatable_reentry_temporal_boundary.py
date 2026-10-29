"""Unit tests for the temporal trigger boundary of repeatable reentry.

Covers the 2026-10-12 fix: ``_trigger_boundary`` no longer requires the
triggering decision's USABLE artifacts to appear in the latest
ScientificState.sourceRefs (only S1/S8/feedback-API materializations append
sourceRefs — S4-S7 declare no scientific_state_projection, so S5 completion
could never re-arm S6/S7/S8). The replacement guard is temporal: the
triggering decision must close strictly after the template's latest completed
work unit finished, preventing stale-decision-close re-arm storms.

The test builds on ``build_planned_continuation_task`` (a confirmed task whose
upstream decision is closed/succeeded with a completed work unit and USABLE
artifact), then grafts a second closed decision standing in for S5's close and
a repeatable downstream (S6) instance.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime

from app.models.research_orchestration import (
    ArtifactState,
    ExecutionDecisionOutcome,
    ExecutionDecisionStatus,
    ResearchTaskV12,
    SubstageRuntimeStatus,
    WorkUnitStatus,
)
from app.modules.repeatable_definition_reentry import (
    _trigger_boundary,
    reactivate_repeatable_definition_instances,
)

from tests.support.research_execution_decision import (
    build_planned_continuation_task,
)

_S4 = "pvd:evidence_applicability_comparability_analysis"
_S5 = "pvd:experimental_feedback_round_analysis"

_UPSTREAM_INSTANCE_ID = "sub_contract_full"  # fixture's completed instance
_S5_INSTANCE_ID = "sub_pvd_s5"
_S5_DECISION_ID = "dec_pvd_s5"
_S5_WORK_ID = "wu_pvd_s5"
_S5_ARTIFACT_ID = "artifact_pvd_s5"


def _build_task(
    *,
    s5_closed_at: datetime,
    downstream_last_finished_at: datetime | None,
) -> ResearchTaskV12:
    """Graft an S5-closing decision + USABLE artifact onto the continuation fixture.

    The downstream template instance (``sub_downstream``) gets a repeatable
    reentry authority triggered by S5; when ``downstream_last_finished_at`` is
    set, its prior work unit finished at that time (for the staleness case).
    """
    task = build_planned_continuation_task(next_stage_key="execution_validation")
    payload = task.model_dump(mode="json", by_alias=True)
    design = payload["researchDesignVersions"][0]

    # S5 instance in the confirmed design (dormant until feedback wakes it).
    s5_instance = deepcopy(design["instances"][0])
    s5_instance.update(
        {
            "substageInstanceId": _S5_INSTANCE_ID,
            "substageDefinitionId": _S5,
            "sequenceHint": 99,
        }
    )
    s5_instance.pop("repeatableReentryAuthority", None)
    design["instances"].append(s5_instance)

    # Downstream template declares S5 (and S4) as repeat triggers.
    downstream = next(
        row for row in design["instances"] if row["substageInstanceId"] == "sub_downstream"
    )
    downstream["repeatableReentryAuthority"] = {
        "policyVersion": "repeatable_definition_reentry_v1",
        "triggerDefinitionIds": [_S5, _S4],
    }

    # S5-closing decision: clone the fixture's closed decision and override
    # identity/closure fields. Its single assignment is re-keyed so the grafted
    # work unit below satisfies WorkUnit↔Assignment uniqueness.
    s5_decision = deepcopy(payload["executionDecisions"][0])
    s5_decision.update(
        {
            "executionDecisionId": _S5_DECISION_ID,
            "selectionRequestId": "sel-pvd-s5",
            "decisionSequence": 99,
            "substageInstanceId": _S5_INSTANCE_ID,
            "rationale": "pvd s5 feedback analysis completed",
            "decidedAt": s5_closed_at.isoformat(),
            "closedAt": s5_closed_at.isoformat(),
            "status": ExecutionDecisionStatus.CLOSED.value,
            "outcome": ExecutionDecisionOutcome.SUCCEEDED.value,
        }
    )
    for assignment in s5_decision["assignments"]:
        assignment["assignmentId"] = "assign_pvd_s5"
    s5_decision["completionPolicy"]["requiredAssignmentIds"] = ["assign_pvd_s5"]
    for req in s5_decision["resultAssemblyPolicy"]["finalArtifactRequirements"]:
        req["sourceAssignmentIds"] = ["assign_pvd_s5"]
    payload["executionDecisions"].append(s5_decision)

    # S5 completed work unit under that decision (reuse the fixture's
    # assignment shape so WorkUnit↔Assignment invariants hold).
    s5_work = deepcopy(payload["workUnits"][0])
    s5_work.update(
        {
            "workUnitId": _S5_WORK_ID,
            "idempotencyKey": "idem-wu-pvd-s5",
            "executionDecisionId": _S5_DECISION_ID,
            "assignmentId": "assign_pvd_s5",
            "substageInstanceId": _S5_INSTANCE_ID,
            "substageDefinitionId": _S5,
            "status": WorkUnitStatus.COMPLETED.value,
            "dispatchedAt": s5_closed_at.isoformat(),
            "frozenAt": s5_closed_at.isoformat(),
            "finishedAt": s5_closed_at.isoformat(),
        }
    )
    payload["workUnits"].append(s5_work)

    # S5 USABLE artifact — deliberately NOT in any ScientificState.sourceRefs.
    # Null out sourceCapabilityCallId (the fixture's call binds to wu_contract_full,
    # not wu_pvd_s5) and re-set lineageKind to match the direct_work_unit_output
    # lineage expected when no capability call backs the artifact.
    s5_artifact = deepcopy(payload["artifacts"][0])
    s5_artifact.update(
        {
            "artifactId": _S5_ARTIFACT_ID,
            "workUnitId": _S5_WORK_ID,
            "substageInstanceId": _S5_INSTANCE_ID,
            "sourceCapabilityCallId": None,
            "lineageKind": "direct_work_unit_output",
            "sourceResultId": None,
            "artifactState": ArtifactState.USABLE.value,
        }
    )
    payload["artifacts"].append(s5_artifact)

    # Downstream prior work: when set, the downstream instance completed at
    # that time and its runtime is terminal. The fixture already has a planned
    # sub_downstream runtime row; promote it to completed in place.
    if downstream_last_finished_at is not None:
        downstream_decision = deepcopy(payload["executionDecisions"][0])
        downstream_decision.update(
            {
                "executionDecisionId": "dec_downstream_prior",
                "selectionRequestId": "sel-downstream-prior",
                "decisionSequence": 50,
                "substageInstanceId": "sub_downstream",
                "decidedAt": downstream_last_finished_at.isoformat(),
                "closedAt": downstream_last_finished_at.isoformat(),
            }
        )
        for assignment in downstream_decision["assignments"]:
            assignment["assignmentId"] = "assign_downstream_prior"
        downstream_decision["completionPolicy"]["requiredAssignmentIds"] = [
            "assign_downstream_prior"
        ]
        for req in downstream_decision["resultAssemblyPolicy"]["finalArtifactRequirements"]:
            req["sourceAssignmentIds"] = ["assign_downstream_prior"]
        payload["executionDecisions"].append(downstream_decision)
        downstream_work = deepcopy(payload["workUnits"][0])
        downstream_work.update(
            {
                "workUnitId": "wu_downstream_prior",
                "idempotencyKey": "idem-wu-downstream-prior",
                "executionDecisionId": "dec_downstream_prior",
                "assignmentId": "assign_downstream_prior",
                "substageInstanceId": "sub_downstream",
                "substageDefinitionId": "sdr:downstream",
                "status": WorkUnitStatus.COMPLETED.value,
                "dispatchedAt": downstream_last_finished_at.isoformat(),
                "frozenAt": downstream_last_finished_at.isoformat(),
                "finishedAt": downstream_last_finished_at.isoformat(),
            }
        )
        payload["workUnits"].append(downstream_work)
        for runtime in payload["substageRuntimeStates"]:
            if runtime["substageInstanceId"] == "sub_downstream":
                runtime.update(
                    {
                        "status": SubstageRuntimeStatus.COMPLETED.value,
                        "activeExecutionDecisionId": "dec_downstream_prior",
                        "workUnitIds": ["wu_downstream_prior"],
                        "statusReasonCode": None,
                        "updatedAt": downstream_last_finished_at.isoformat(),
                    }
                )
                break

    # ScientificState: ``_trigger_boundary`` derives the triggering state from
    # the latest ScientificState whose ``research_design_version`` matches the
    # confirmed design. The continuation fixture ships none, so graft one with a
    # single sourceRef (the upstream material) so the boundary's state lookup
    # resolves. The state's sourceRefs need NOT contain the S5 artifact — that
    # is the whole point of this fix.
    if not payload.get("scientificStates"):
        payload["scientificStates"] = [
            {
                "scientificStateVersion": 1,
                "researchDesignVersion": design["version"],
                "scientificFacts": {},
                "executionState": {},
                "sourceRefs": ["material_contract"],
                "createdAt": s5_closed_at.isoformat(),
            }
        ]

    return ResearchTaskV12.model_validate(payload)


def test_trigger_boundary_accepts_usable_artifact_not_in_source_refs() -> None:
    """S5 closes with a USABLE artifact NOT in sourceRefs → boundary holds.

    Pre-fix this returned None (S5's artifact never enters sourceRefs because
    S5 declares no scientific_state_projection) and S6/S7/S8 were never re-armed.
    """
    closed = datetime(2026, 10, 12, 12, 0, 0, tzinfo=UTC)
    task = _build_task(s5_closed_at=closed, downstream_last_finished_at=None)
    boundary = _trigger_boundary(task, _S5_DECISION_ID)
    assert boundary is not None
    assert boundary.decision.execution_decision_id == _S5_DECISION_ID
    assert _S5_ARTIFACT_ID in boundary.validated_artifact_refs


def test_trigger_boundary_rejects_stale_decision_close() -> None:
    """A decision closing before the template's last finished work → None."""
    downstream_finished = datetime(2026, 10, 12, 12, 0, 0, tzinfo=UTC)
    stale_close = datetime(2026, 10, 12, 11, 0, 0, tzinfo=UTC)
    task = _build_task(
        s5_closed_at=stale_close,
        downstream_last_finished_at=downstream_finished,
    )
    assert _trigger_boundary(task, _S5_DECISION_ID) is None


def test_trigger_boundary_rejects_open_decision() -> None:
    closed = datetime(2026, 10, 12, 12, 0, 0, tzinfo=UTC)
    task = _build_task(s5_closed_at=closed, downstream_last_finished_at=None)
    payload = task.model_dump(mode="json", by_alias=True)
    for row in payload["executionDecisions"]:
        if row["executionDecisionId"] == _S5_DECISION_ID:
            row["closedAt"] = None
            row["outcome"] = None
            row["status"] = "dispatched"
    task = ResearchTaskV12.model_validate(payload)
    assert _trigger_boundary(task, _S5_DECISION_ID) is None


def test_trigger_boundary_requires_usable_artifact() -> None:
    closed = datetime(2026, 10, 12, 12, 0, 0, tzinfo=UTC)
    task = _build_task(s5_closed_at=closed, downstream_last_finished_at=None)
    payload = task.model_dump(mode="json", by_alias=True)
    for row in payload["artifacts"]:
        if row["artifactId"] == _S5_ARTIFACT_ID:
            row["artifactState"] = "rejected_or_needs_revision"
    task = ResearchTaskV12.model_validate(payload)
    assert _trigger_boundary(task, _S5_DECISION_ID) is None


def test_reactivate_resets_downstream_terminal_runtime_to_ready() -> None:
    """S5 close re-arms the repeatable downstream template runtime to READY."""
    closed = datetime(2026, 10, 12, 12, 0, 0, tzinfo=UTC)
    downstream_finished = datetime(2026, 10, 12, 10, 0, 0, tzinfo=UTC)
    task = _build_task(
        s5_closed_at=closed,
        downstream_last_finished_at=downstream_finished,
    )
    reactivated = reactivate_repeatable_definition_instances(
        task,
        completed_execution_decision_id=_S5_DECISION_ID,
        occurred_at=closed,
    )
    runtime = next(
        row
        for row in reactivated.substage_runtime_states
        if row.substage_instance_id == "sub_downstream"
    )
    assert runtime.status == SubstageRuntimeStatus.READY
    assert runtime.status_reason_code == "repeatable_definition_reentry"
    assert runtime.work_unit_ids == []


def test_reactivate_does_not_rearm_when_decision_is_stale() -> None:
    downstream_finished = datetime(2026, 10, 12, 12, 0, 0, tzinfo=UTC)
    stale_close = datetime(2026, 10, 12, 11, 0, 0, tzinfo=UTC)
    task = _build_task(
        s5_closed_at=stale_close,
        downstream_last_finished_at=downstream_finished,
    )
    reactivated = reactivate_repeatable_definition_instances(
        task,
        completed_execution_decision_id=_S5_DECISION_ID,
        occurred_at=stale_close,
    )
    runtime = next(
        row
        for row in reactivated.substage_runtime_states
        if row.substage_instance_id == "sub_downstream"
    )
    assert runtime.status == SubstageRuntimeStatus.COMPLETED
