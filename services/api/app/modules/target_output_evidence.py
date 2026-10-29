"""A delivered report does not settle failed dependencies' target claims."""

from collections.abc import Iterable
from typing import Any


def _accepted_current_artifact_refs(task: Any, candidate_ids: set[str]) -> set[str]:
    accepted: set[str] = set()
    for record in task.agent_interaction_records:
        if record.interaction_type != "acceptance_recorded" or record.verdict is None:
            continue
        if (
            record.verdict.decision.value != "approved"
            or record.verdict.rationale_code == "general_validated_completion_artifact"
        ):
            # This existing automatic record accepts readable delivery, not goal fulfillment.
            continue
        accepted.update(
            candidate_ids.intersection(record.input_artifact_refs + record.output_artifact_refs)
        )
    return accepted


def target_output_has_unqualified_evidence(
    task: Any,
    artifacts: Iterable[Any],
    *,
    required_type: str | None,
    instance_ref: tuple[str, int],
    design_version: int,
) -> bool:
    """Use exact producer ancestry; never inspect prose or guess report status.

    Explicit acceptance of the current Artifact can qualify a diagnosis whose
    failed dependencies are intentionally reported. Other instances, superseded
    attempts and acceptance of unrelated Artifacts cannot qualify this output.
    Automatic delivery acceptance and another target's reuse acceptance cannot
    override known failed or unsettled dependencies.
    """
    superseded = {
        row.supersedes_execution_decision_id
        for row in task.execution_decisions
        if row.supersedes_execution_decision_id is not None
    }
    works = {row.work_unit_id: row for row in task.work_units}
    candidates = [
        artifact
        for artifact in artifacts
        if (required_type is None or artifact.artifact_type == required_type)
        and (work := works.get(artifact.work_unit_id)) is not None
        and (work.substage_instance_id, work.substage_instance_revision) == instance_ref
        and work.research_design_version == design_version
        and work.execution_decision_id not in superseded
    ]
    accepted = _accepted_current_artifact_refs(task, {row.artifact_id for row in candidates})
    for artifact in candidates:
        if artifact.artifact_id in accepted:
            return False
        pending = [artifact.work_unit_id]
        visited: set[str] = set()
        while pending:
            work_id = pending.pop()
            if work_id in visited:
                continue
            visited.add(work_id)
            work = works.get(work_id)
            if work is None or work.status.value != "completed":
                break
            pending.extend(work.dependency_work_unit_ids)
        else:
            return False
    return True
