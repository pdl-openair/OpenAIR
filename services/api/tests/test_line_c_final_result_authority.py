from __future__ import annotations

from types import SimpleNamespace

from app.models.research_orchestration import (
    ArtifactState,
    CanonicalArtifactSnapshot,
    StageKey,
    SubstageRuntimeStatus,
    WorkUnitSourceMode,
    WorkUnitStatus,
)
from app.modules.task_public_projection import _active_stage_details


def _instance(instance_id: str, *, substage: str, stage: StageKey, dependencies=None):
    return SimpleNamespace(
        substage_instance_id=instance_id,
        instance_revision=1,
        substage_definition_id=f"sdr:{substage}",
        stage_key=stage,
        dependencies=list(dependencies or []),
        sequence_hint=None,
        objective=substage,
        expected_artifact_types=[f"{substage}_output"],
    )


def _artifact(artifact_id: str, work_unit_id: str, artifact_type: str, *, version: int = 1, state=ArtifactState.USABLE):
    row = SimpleNamespace(
        artifact_id=artifact_id,
        work_unit_id=work_unit_id,
        artifact_type=artifact_type,
        artifact_state=state,
        version=version,
        validator_result={"contentSha256": f"{version:x}" * 64 if version < 16 else "a" * 64},
    )
    row.model_dump = lambda **_kwargs: {"artifactType": row.artifact_type}
    return row


def _snapshot(artifact) -> CanonicalArtifactSnapshot:
    return CanonicalArtifactSnapshot.model_validate({
        "artifactId": artifact.artifact_id,
        "artifactType": artifact.artifact_type,
        "version": artifact.version,
        "contentSha256": artifact.validator_result["contentSha256"],
    })


def _work(
    work_id: str,
    instance_id: str,
    *,
    refs=None,
    snapshots=None,
    decision_id="decision-upstream",
    assignment_id="assign-upstream",
):
    row = SimpleNamespace(
        work_unit_id=work_id,
        execution_decision_id=decision_id,
        assignment_id=assignment_id,
        substage_instance_id=instance_id,
        substage_instance_revision=1,
        research_design_version=1,
        source_mode=WorkUnitSourceMode.LIVE,
        status=WorkUnitStatus.COMPLETED,
        input_artifact_refs=list(refs or []),
        input_artifact_snapshots=list(snapshots or []),
    )
    row.model_dump = lambda **_kwargs: {"assignmentId": row.assignment_id}
    return row


def test_completed_downstream_substage_is_affected_only_when_its_frozen_artifact_revision_changes() -> None:
    upstream = _instance("sub-a", substage="evidence_extraction", stage=StageKey.EXECUTION_VALIDATION)
    downstream = _instance("sub-b", substage="findings_synthesis", stage=StageKey.EXECUTION_VALIDATION)
    unrelated = _instance("sub-c", substage="quality_audit", stage=StageKey.EXECUTION_VALIDATION)
    design = SimpleNamespace(version=1, instances=[upstream, downstream, unrelated])
    source_v2 = _artifact("artifact-a", "work-a", "evidence", version=2)
    source_v2.validator_result = {"contentSha256": "2" * 64}
    unrelated_source = _artifact("artifact-unrelated", "work-a", "evidence", version=1)
    source_v2.substage_instance_id = "sub-a"
    unrelated_source.substage_instance_id = "sub-a"
    task = SimpleNamespace(
        artifacts=[source_v2, unrelated_source],
        work_units=[
            _work("work-a", "sub-a"),
            _work("work-b", "sub-b", refs=["artifact-a"], snapshots=[CanonicalArtifactSnapshot.model_validate({
                "artifactId": "artifact-a", "artifactType": "evidence", "version": 1, "contentSha256": "1" * 64,
            })]),
            _work("work-c", "sub-c", refs=["artifact-unrelated"], snapshots=[_snapshot(unrelated_source)]),
        ],
        substage_runtime_states=[
            SimpleNamespace(substage_instance_id="sub-a", instance_revision=1, status=SubstageRuntimeStatus.COMPLETED),
            SimpleNamespace(substage_instance_id="sub-b", instance_revision=1, status=SubstageRuntimeStatus.COMPLETED),
            SimpleNamespace(substage_instance_id="sub-c", instance_revision=1, status=SubstageRuntimeStatus.COMPLETED),
        ],
        capability_calls=[],
    )

    details = _active_stage_details(
        task,
        design,
        ("execution_validation",),
        public_artifact_ids={"artifact-a", "artifact-unrelated"},
    )
    substages = {row["subStageInstanceId"]: row for row in details[0]["subStages"]}

    assert source_v2.version == 2  # A v1 remains the frozen downstream snapshot.
    assert task.work_units[1].input_artifact_snapshots[0].version == 1
    assert substages["sub-b"]["affected"] is True
    assert substages["sub-c"]["affected"] is False
    assert len(task.work_units) == 3
