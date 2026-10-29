"""Deterministic construction of ResearchDesign recovery drafts.

This module changes only draft structure. It never confirms a Design, mutates
runtime history, chooses research work, or persists state.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Iterable

from app.models.research_orchestration import (
    AcceptanceCriterion,
    AcceptanceCriterionKind,
    CoverageEvaluationMode,
    CoverageStatus,
    DependencyConditionType,
    EvaluatorType,
    InstanceRef,
    JudgmentRequirement,
    ResearchDesignRef,
    ResearchDesignStatus,
    ResearchDesignVersion,
    StageKey,
    SubstageDependency,
    SubstageInstance,
)


class ResearchDesignRecoveryError(ValueError):
    """A recovery draft cannot be formed without violating the Design contract."""


def _stable_id(prefix: str, *parts: object) -> str:
    raw = json.dumps(parts, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return f"{prefix}_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:20]}"


def semantic_gap_recovery_id(
    *,
    blocked_instance_id: str,
    gap_reason_code: str,
    recovery_definition_id: str,
    source_design_version: int,
) -> str:
    return _stable_id(
        "system-recovery",
        blocked_instance_id,
        gap_reason_code,
        recovery_definition_id,
        source_design_version,
    )


def _rebase_dependent_instance_revisions(
    instances: list[SubstageInstance],
    *,
    revised_instance: SubstageInstance,
) -> tuple[list[SubstageInstance], dict[tuple[str, int], InstanceRef]]:
    """Create new immutable revisions for every task whose dependency graph changes."""

    by_id = {row.substage_instance_id: row for row in instances}
    affected_ids = {revised_instance.substage_instance_id}

    while True:
        newly_affected = {
            instance.substage_instance_id
            for instance in instances
            if instance.substage_instance_id not in affected_ids
            and any(
                dependency.upstream_instance_ref.substage_instance_id in affected_ids
                for dependency in instance.dependencies
            )
        }
        if not newly_affected:
            break
        affected_ids.update(newly_affected)

    revised_refs: dict[tuple[str, int], InstanceRef] = {}
    for instance_id in affected_ids:
        previous = by_id[instance_id]
        next_revision = (
            revised_instance.instance_revision
            if instance_id == revised_instance.substage_instance_id
            else previous.instance_revision + 1
        )
        revised_refs[(instance_id, previous.instance_revision)] = InstanceRef(
            substageInstanceId=instance_id,
            instanceRevision=next_revision,
        )

    rebased: list[SubstageInstance] = []
    for instance in instances:
        if instance.substage_instance_id not in affected_ids:
            rebased.append(instance)
            continue
        base = (
            revised_instance
            if instance.substage_instance_id == revised_instance.substage_instance_id
            else instance.model_copy(
                update={"instance_revision": instance.instance_revision + 1}
            )
        )
        dependencies = [
            dependency.model_copy(
                update={
                    "upstream_instance_ref": revised_refs.get(
                        (
                            dependency.upstream_instance_ref.substage_instance_id,
                            dependency.upstream_instance_ref.instance_revision,
                        ),
                        dependency.upstream_instance_ref,
                    )
                }
            )
            for dependency in base.dependencies
        ]
        rebased.append(base.model_copy(update={"dependencies": dependencies}))

    return rebased, revised_refs


def _missing_dependency_refs(instances: Iterable[SubstageInstance]) -> list[InstanceRef]:
    instance_refs = {
        (row.substage_instance_id, row.instance_revision)
        for row in instances
    }
    missing: dict[tuple[str, int], InstanceRef] = {}
    for instance in instances:
        for dependency in instance.dependencies:
            ref = dependency.upstream_instance_ref
            key = (ref.substage_instance_id, ref.instance_revision)
            if key not in instance_refs:
                missing[key] = ref
    return list(missing.values())


def _overloaded_instance_refs(
    design: ResearchDesignVersion,
    instances: list[SubstageInstance],
) -> list[InstanceRef]:
    maximum = design.scope_boundary.max_substage_instances
    if maximum is None or len(instances) <= maximum:
        return []
    return [
        InstanceRef(
            substageInstanceId=row.substage_instance_id,
            instanceRevision=row.instance_revision,
        )
        for row in instances[maximum:]
    ]


def _coverage_target_ids(blocked: SubstageInstance) -> list[str]:
    return list(
        dict.fromkeys(
            target_id
            for criterion in blocked.acceptance_criteria
            for target_id in criterion.coverage_target_ids
        )
    )


def _recovery_instance(
    *,
    recovery_id: str,
    recovery_definition_id: str,
    recovery_definition_version: int,
    recovery_stage_key: StageKey | str,
    recovery_objective: str,
    recovery_artifact_types: list[str],
    coverage_target_ids: list[str],
    sequence_hint: int,
) -> SubstageInstance:
    return SubstageInstance(
        substageInstanceId=recovery_id,
        instanceRevision=1,
        substageDefinitionId=recovery_definition_id,
        substageDefinitionVersion=recovery_definition_version,
        stageKey=StageKey(recovery_stage_key),
        objective=recovery_objective,
        inputRefs=[],
        expectedArtifactTypes=recovery_artifact_types,
        acceptanceCriteria=[
            AcceptanceCriterion(
                criterionId=_stable_id("criterion", recovery_id, "material_ready"),
                description="Produce material usable by the blocked research task.",
                criterionKind=AcceptanceCriterionKind.ARTIFACT_QUALITY,
                coverageTargetIds=coverage_target_ids,
                requiredArtifactType=recovery_artifact_types[0],
                judgmentRequirement=JudgmentRequirement.DETERMINISTIC,
                evaluatorType=EvaluatorType.DETERMINISTIC,
                evaluatorRef=None,
            )
        ],
        dependencies=[],
        sequenceHint=sequence_hint,
    )


def _recovery_coverage(
    *,
    active: ResearchDesignVersion,
    next_design_ref: ResearchDesignRef,
    instances: list[SubstageInstance],
    revised_refs: dict[tuple[str, int], InstanceRef],
    created_at: datetime,
):
    assert active.coverage_report is not None
    missing_dependencies = _missing_dependency_refs(instances)
    overloaded = _overloaded_instance_refs(active, instances)
    uncovered = list(active.coverage_report.uncovered_required_target_ids)
    coverage_status = (
        CoverageStatus.PASSED_STRUCTURAL
        if not uncovered and not overloaded and not missing_dependencies
        else CoverageStatus.NEEDS_REVISION
    )
    report = active.coverage_report.model_copy(
        update={
            "design_ref": next_design_ref,
            "coverage_report_id": _stable_id(
                "coverage", active.research_design_id, next_design_ref.version
            ),
            "claims": [
                claim.model_copy(
                    update={
                        "instance_refs": [
                            revised_refs.get(
                                (ref.substage_instance_id, ref.instance_revision), ref
                            )
                            for ref in claim.instance_refs
                        ]
                    }
                )
                for claim in active.coverage_report.claims
            ],
            "overloaded_instance_refs": overloaded,
            "missing_dependency_refs": missing_dependencies,
            "status": coverage_status,
            "evaluation_mode": CoverageEvaluationMode.DETERMINISTIC,
            "semantic_evaluation_ref": None,
            "automatic_repair_count": 0,
            "evaluated_at": created_at,
        }
    )
    from app.modules.research_design_coverage_confirmation import (
        has_only_executable_target_gaps,
    )

    if coverage_status == CoverageStatus.NEEDS_REVISION and has_only_executable_target_gaps(report):
        report.status = CoverageStatus.EXECUTABLE_WITH_GAPS
    return report


def build_semantic_gap_recovery_draft(
    *,
    active: ResearchDesignVersion,
    blocked: SubstageInstance,
    recovery_id: str,
    recovery_definition_id: str,
    recovery_definition_version: int,
    recovery_stage_key: StageKey | str,
    recovery_objective: str,
    recovery_artifact_types: list[str],
    created_at: datetime,
) -> ResearchDesignVersion:
    """Return a structurally rechecked draft; never inherit a semantic pass."""

    if active.coverage_report is None:
        raise ResearchDesignRecoveryError("system_recovery_requires_coverage")
    if not recovery_artifact_types:
        raise ResearchDesignRecoveryError("system_recovery_target_invalid")

    coverage_target_ids = _coverage_target_ids(blocked)
    if not coverage_target_ids:
        raise ResearchDesignRecoveryError("system_recovery_target_invalid")

    recovery = _recovery_instance(
        recovery_id=recovery_id,
        recovery_definition_id=recovery_definition_id,
        recovery_definition_version=recovery_definition_version,
        recovery_stage_key=recovery_stage_key,
        recovery_objective=recovery_objective,
        recovery_artifact_types=recovery_artifact_types,
        coverage_target_ids=coverage_target_ids,
        sequence_hint=blocked.sequence_hint,
    )
    recovery_dependency = SubstageDependency(
        dependencyId=_stable_id(
            "dependency", blocked.substage_instance_id, recovery_id
        ),
        upstreamInstanceRef=InstanceRef(
            substageInstanceId=recovery_id, instanceRevision=1
        ),
        conditionType=DependencyConditionType.ARTIFACT_USABLE,
        requiredArtifactTypes=recovery_artifact_types,
        confirmationTargetRef=None,
    )
    revised_blocked = blocked.model_copy(
        update={
            "instance_revision": blocked.instance_revision + 1,
            "dependencies": [*blocked.dependencies, recovery_dependency],
        }
    )
    instances, revised_refs = _rebase_dependent_instance_revisions(
        active.instances, revised_instance=revised_blocked
    )
    instances.append(recovery)

    next_version = active.version + 1
    next_design_ref = ResearchDesignRef(
        researchDesignId=active.research_design_id, version=next_version
    )
    coverage = _recovery_coverage(
        active=active,
        next_design_ref=next_design_ref,
        instances=instances,
        revised_refs=revised_refs,
        created_at=created_at,
    )
    return active.model_copy(
        update={
            "version": next_version,
            "supersedes_version": active.version,
            "status": ResearchDesignStatus.DRAFT,
            "instances": instances,
            "workflow_responsibility_coverage": [
                row.model_copy(
                    update={
                        "instance_refs": [
                            revised_refs.get(
                                (ref.substage_instance_id, ref.instance_revision), ref
                            )
                            for ref in row.instance_refs
                        ]
                    }
                )
                for row in active.workflow_responsibility_coverage
            ],
            "coverage_report": coverage,
            "created_at": created_at,
            "confirmed_at": None,
            "confirmation_ref": None,
        }
    )


__all__ = [
    "ResearchDesignRecoveryError",
    "build_semantic_gap_recovery_draft",
    "semantic_gap_recovery_id",
]
