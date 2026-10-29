"""Counterexamples for evidence scope; constructed facts, no real execution."""

from types import SimpleNamespace as Row

import pytest
from app.modules.target_output_evidence import target_output_has_unqualified_evidence


def evidence_scope(action_status="failed"):
    action = Row(
        work_unit_id="action",
        status=Row(value=action_status),
        dependency_work_unit_ids=[],
        substage_instance_id="scope",
        substage_instance_revision=1,
        research_design_version=1,
        execution_decision_id="decision",
    )
    report = Row(
        work_unit_id="report",
        status=Row(value="completed"),
        dependency_work_unit_ids=["action"],
        substage_instance_id="scope",
        substage_instance_revision=1,
        research_design_version=1,
        execution_decision_id="decision",
    )
    artifact = Row(artifact_id="report-artifact", artifact_type="record", work_unit_id="report")
    task = Row(work_units=[action, report], execution_decisions=[], agent_interaction_records=[])
    return task, artifact


def unqualified(task, artifacts):
    return target_output_has_unqualified_evidence(
        task, artifacts, required_type="record", instance_ref=("scope", 1), design_version=1
    )


@pytest.mark.parametrize("status", ["failed", "blocked", "external_result_unknown", "running"])
def test_failed_or_unsettled_real_action_is_not_qualified_by_report_presence(status):
    task, artifact = evidence_scope(status)
    assert unqualified(task, [artifact])


def test_unrelated_failure_does_not_disqualify_successful_output():
    task, artifact = evidence_scope("completed")
    task.work_units.append(Row(work_unit_id="unrelated", status=Row(value="failed")))
    assert not unqualified(task, [artifact])


def test_explicit_acceptance_can_qualify_diagnosis_with_failed_action():
    task, artifact = evidence_scope()
    task.agent_interaction_records = [
        Row(
            interaction_type="acceptance_recorded",
            verdict=Row(decision=Row(value="approved"), rationale_code="review_approved"),
            input_artifact_refs=[artifact.artifact_id],
            output_artifact_refs=[],
        )
    ]
    assert not unqualified(task, [artifact])
    task.agent_interaction_records[0].input_artifact_refs = ["another-artifact"]
    assert unqualified(task, [artifact])


def test_superseded_or_other_scope_output_does_not_cover_current_goal():
    task, artifact = evidence_scope("completed")
    task.execution_decisions = [Row(supersedes_execution_decision_id="decision")]
    assert unqualified(task, [artifact])
    task.execution_decisions = []
    task.work_units[1].research_design_version = 2
    assert unqualified(task, [artifact])


def test_successful_later_output_can_cover_goal_without_erasing_old_failure():
    task, artifact = evidence_scope()
    recovered = Row(
        work_unit_id="recovered",
        status=Row(value="completed"),
        dependency_work_unit_ids=[],
        substage_instance_id="scope",
        substage_instance_revision=1,
        research_design_version=1,
        execution_decision_id="recovery",
    )
    task.work_units.append(recovered)
    output = Row(artifact_id="recovered-artifact", artifact_type="record", work_unit_id="recovered")
    assert not unqualified(task, [artifact, output])
    assert task.work_units[0].status.value == "failed"


def test_missing_dependency_is_unproven_and_diamond_graph_terminates():
    task, artifact = evidence_scope("completed")
    task.work_units[1].dependency_work_unit_ids = ["action", "action"]
    assert not unqualified(task, [artifact])
    task.work_units[0].dependency_work_unit_ids = ["missing"]
    assert unqualified(task, [artifact])


@pytest.mark.parametrize("action_status", ["failed", "external_result_unknown"])
def test_automatic_general_delivery_acceptance_does_not_qualify_failed_target(action_status):
    task, artifact = evidence_scope(action_status)
    task.agent_interaction_records = [
        Row(
            interaction_type="acceptance_recorded",
            verdict=Row(
                decision=Row(value="approved"),
                rationale_code="general_validated_completion_artifact",
            ),
            input_artifact_refs=[],
            output_artifact_refs=[artifact.artifact_id],
        )
    ]
    assert unqualified(task, [artifact])


def test_reuse_acceptance_is_not_unscoped_acceptance_of_current_target():
    task, artifact = evidence_scope()
    task.agent_interaction_records = [
        Row(
            interaction_type="target_output_reuse_accepted",
            verdict=Row(
                decision=Row(value="approved"), rationale_code="target_output_reuse_accepted"
            ),
            input_artifact_refs=[artifact.artifact_id],
            output_artifact_refs=[],
        )
    ]
    assert unqualified(task, [artifact])


def test_independent_review_of_same_artifact_remains_effective_after_auto_acceptance():
    task, artifact = evidence_scope()
    task.agent_interaction_records = [
        Row(
            interaction_type="acceptance_recorded",
            verdict=Row(
                decision=Row(value="approved"),
                rationale_code="general_validated_completion_artifact",
            ),
            input_artifact_refs=[],
            output_artifact_refs=[artifact.artifact_id],
        ),
        Row(
            interaction_type="acceptance_recorded",
            verdict=Row(decision=Row(value="approved"), rationale_code="review_approved"),
            input_artifact_refs=[artifact.artifact_id],
            output_artifact_refs=[],
        ),
    ]
    assert not unqualified(task, [artifact])
    task.agent_interaction_records[1].input_artifact_refs = ["another-artifact"]
    assert unqualified(task, [artifact])
