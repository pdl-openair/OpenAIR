"""Exact target-set contract, shared by ResearchDesign persistence validation."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol


class _Requirement(Protocol):
    completion_requirement_id: str
    target_output_ref: str
    description: str


def validate_project_completion_requirements(
    requirements: Sequence[_Requirement],
    target_outputs: Sequence[str],
) -> None:
    ids = [row.completion_requirement_id for row in requirements]
    if len(set(ids)) != len(ids):
        raise ValueError("completionRequirementId values must be unique")
    expected = {
        f"understanding.targetOutputs[{index}]": value for index, value in enumerate(target_outputs)
    }
    if not expected:
        raise ValueError("ResearchDesign requires at least one project completion target")
    by_ref = {row.target_output_ref: row for row in requirements}
    if len(by_ref) != len(requirements):
        raise ValueError("project completion requirement targetOutputRef values must be unique")
    if set(by_ref) != set(expected):
        raise ValueError(
            "project completion requirements must cover every target output exactly once"
        )
    for ref, output in expected.items():
        if by_ref[ref].description != output:
            raise ValueError(
                "project completion requirement description must match its target output"
            )


def validate_workflow_target_coverage(design: Any) -> None:
    responsibilities = {
        row.responsibility_key: row for row in design.workflow_responsibility_coverage
    }
    report = design.coverage_report
    for requirement in design.project_completion_requirements:
        owner = requirement.workflow_responsibility_key
        contributors = requirement.contributing_responsibility_keys
        if contributors is not None:
            _validate_target_contributions(design, requirement, responsibilities)
        if owner is None:
            continue
        coverage = responsibilities.get(owner)
        if coverage is None or report is None:
            raise ValueError("project completion target has no Workflow owner coverage")
        targets = [
            row for row in report.targets if row.source_path == requirement.target_output_ref
        ]
        if len(targets) != 1:
            raise ValueError("Workflow target has no unique coverage target")
        claims = [row for row in report.claims if row.target_id == targets[0].target_id]
        if len(claims) != 1:
            raise ValueError("Workflow target has no unique coverage claim")
        expected = {row.substage_instance_id for row in coverage.instance_refs}
        actual = {row.substage_instance_id for row in claims[0].instance_refs}
        if expected != actual:
            raise ValueError("Workflow target claim conflicts with its formal owner")


def _validate_target_contributions(
    design: Any, requirement: Any, responsibilities: dict[str, Any]
) -> None:
    contributors = requirement.contributing_responsibility_keys
    owner = requirement.workflow_responsibility_key
    report = design.coverage_report
    if len(set(contributors)) != len(contributors) or any(
        key not in responsibilities for key in contributors
    ):
        raise ValueError(
            "target contributions must name distinct current Workflow responsibilities"
        )
    if owner is not None and contributors != [owner]:
        raise ValueError("target contributions cannot change its frozen owner")
    if report is None:
        raise ValueError("target contributions require coverage")
    targets = [row for row in report.targets if row.source_path == requirement.target_output_ref]
    claims = [
        row for row in report.claims if len(targets) == 1 and row.target_id == targets[0].target_id
    ]
    expected = {
        ref.substage_instance_id
        for key in contributors
        for ref in responsibilities[key].instance_refs
    }
    if (
        len(claims) != 1
        or {ref.substage_instance_id for ref in claims[0].instance_refs} != expected
    ):
        raise ValueError("target claim must cover every current contribution")
    criterion_refs = set(claims[0].acceptance_criterion_refs)
    for instance in design.instances:
        if instance.substage_instance_id not in expected:
            continue
        if not any(
            criterion.criterion_id in criterion_refs
            and targets[0].target_id in criterion.coverage_target_ids
            for criterion in instance.acceptance_criteria
        ):
            raise ValueError("each current contribution needs a target acceptance criterion")
