"""R0-C deterministic ownership, interruption, and preservation contracts.

Primary evidence for KB1-6/8/10. These cases execute the shared canonical
projection; they do not claim Provider, HTTP, transaction, or browser coverage.
"""
from copy import deepcopy
from types import SimpleNamespace
import unittest

from app.models.research_orchestration import (
    ArtifactState, ExecutionDecisionStatus, SubstageRuntimeStatus, WorkUnitStatus,
)
from app.modules.research_blocker_semantics import (
    active_user_stop_receipt, current_blocker_facts,
)
from tests.support.r0c_interruption import r0c_canonical, retained_artifact


def stop_receipt(*, task_id="task_1", active=True):
    return {
        "taskId": task_id, "status": "stopped", "active": active,
        "controlAction": "stop", "stoppedAt": "2026-10-05T00:02:00Z",
    }


class CurrentBlockerContractTests(unittest.TestCase):
    def test_same_provider_configuration_fact_has_same_owner_across_three_lanes(self):
        views = []
        for lane in ("cs", "ot", "pvd"):
            with self.subTest(lane=lane):
                task = r0c_canonical(lane=lane)
                facts = current_blocker_facts(task)
                self.assertEqual(len(facts), 1)
                fact = facts[0]
                self.assertEqual(fact.kind, "system_failure")
                self.assertEqual(fact.reason_code, "provider_configuration")
                self.assertEqual(fact.work_unit_id, "wu_1")
                self.assertEqual(fact.substage_instance_id, "sub-1")
                views.append(fact)
        self.assertEqual(views[0], views[1])
        self.assertEqual(views[1], views[2])

    def test_system_reason_dominates_coarse_needs_input_and_stale_artifact_gap(self):
        task = r0c_canonical(runtime_status="needs_input")
        task.artifacts[:] = [retained_artifact(state="needs_input").model_copy(update={
            "work_unit_id": "wu_1", "substage_instance_id": "sub-1",
            "validator_result": {"missingInputs": ["source_records"]},
        })]
        self.assertTrue(current_blocker_facts(task))
        self.assertEqual({fact.kind for fact in current_blocker_facts(task)}, {"system_failure"})

    def test_neighboring_user_dependency_and_unknown_have_distinct_ownership(self):
        for reason, runtime, work, expected in (
            ("required_input_missing", "needs_input", "blocked", None),
            ("authorization_required", "needs_input", "blocked", "user_input"),
            ("external_dependency_pending", "blocked_recoverable", "blocked", "external_dependency"),
            ("external_result_unknown", "blocked_recoverable", "external_result_unknown", "external_result_unknown"),
            ("a_future_unclassified_failure", "needs_input", "blocked", "system_failure"),
        ):
            with self.subTest(reason=reason):
                task = r0c_canonical(reason=reason, runtime_status=runtime, work_status=work)
                kinds = {fact.kind for fact in current_blocker_facts(task)}
                self.assertEqual(kinds, set() if expected is None else {expected})

    def test_failure_remains_after_cancelled_control_state(self):
        task = r0c_canonical(task_status="cancelled", runtime_status="cancelled")
        self.assertEqual([fact.kind for fact in current_blocker_facts(task)], ["system_failure"])
        self.assertEqual(current_blocker_facts(task)[0].reason_code, "provider_configuration")

    def test_only_matching_committed_stop_establishes_user_control(self):
        self.assertIsNotNone(active_user_stop_receipt("task_1", stop_receipt()))
        for receipt in (None, {"status": "cancelled"}, stop_receipt(task_id="other-task"),
                        stop_receipt(active=False), {"status": "stopped", "active": True}):
            with self.subTest(receipt=receipt):
                self.assertIsNone(active_user_stop_receipt("task_1", receipt))
        task = r0c_canonical(
            task_status="cancelled", runtime_status="cancelled", work_status="cancelled", reason=None,
        )
        self.assertEqual(current_blocker_facts(task), ())

    def test_completed_and_optional_skipped_steps_do_not_revive_old_failure(self):
        for runtime in ("completed", "skipped"):
            with self.subTest(runtime=runtime):
                task = r0c_canonical(runtime_status=runtime)
                if runtime == "skipped":
                    task.substage_runtime_states[0].status_reason_code = "optional_enhancement_unavailable"
                self.assertEqual(current_blocker_facts(task), ())

    def test_superseded_attempt_cannot_mask_current_real_user_input(self):
        task = r0c_canonical(
            reason="required_input_missing", runtime_status="needs_input", work_status="blocked",
            decision_status="closed", decision_outcome="needs_input",
        )
        current = task.execution_decisions[0].model_copy(update={"decision_sequence": 2})
        task.execution_decisions[0] = current
        old = current.model_copy(update={
            "execution_decision_id": "decision-old", "decision_sequence": 1,
            "status": ExecutionDecisionStatus.SUPERSEDED,
        })
        task.execution_decisions.insert(0, old)
        task.work_units.insert(0, task.work_units[0].model_copy(update={
            "work_unit_id": "work-old", "execution_decision_id": "decision-old",
            "status": WorkUnitStatus.FAILED, "terminal_reason_code": "provider_configuration",
        }))
        self.assertEqual(current_blocker_facts(task), ())

    def test_review_input_requires_its_real_missing_input_evidence(self):
        task = r0c_canonical(reason="review_needs_input", runtime_status="needs_input", work_status="blocked")
        task.artifacts[:] = [retained_artifact(state="needs_input").model_copy(update={
            "work_unit_id": "wu_1", "substage_instance_id": "sub-1",
            "validator_result": {
                "reviewVerdict": "needs_input", "reviewMissingInputs": ["原始实验记录"],
                "reviewBasis": "需要核对原始测量条件", "continueAllowed": False,
            },
        })]
        self.assertEqual(current_blocker_facts(task), ())
        task.artifacts[0].validator_result = {"reviewVerdict": "needs_input", "continueAllowed": False}
        self.assertEqual(current_blocker_facts(task), ())

    def test_projection_never_mutates_scientific_or_result_facts(self):
        task = r0c_canonical()
        task.artifacts[:] = [retained_artifact()]
        task.scientific_states[:] = [SimpleNamespace(scientific_state_version=3, current_facts={"accepted": "evidence"})]
        task.results[:] = [SimpleNamespace(result_id="result-before-failure", work_unit_id="wu-completed")]
        before = deepcopy(task)
        current_blocker_facts(task)
        self.assertEqual(task, before)

    def test_generic_blocked_runtime_cannot_override_specific_work_ownership(self):
        for reason in ("required_input_missing", "dependency_pending"):
            with self.subTest(reason=reason):
                task = r0c_canonical(reason=reason, work_status="blocked")
                task.substage_runtime_states[0].status_reason_code = "blocked"
                facts = current_blocker_facts(task)
                self.assertFalse(any(fact.reason_code == reason for fact in facts))
                self.assertNotIn("user_input", {fact.kind for fact in facts})

    def test_review_evidence_cannot_reclassify_a_provider_configuration_failure(self):
        task = r0c_canonical(runtime_status="needs_input", reason="provider_configuration")
        task.substage_runtime_states[0].status_reason_code = "review_needs_input"
        task.artifacts[:] = [retained_artifact(state="needs_input").model_copy(update={
            "work_unit_id": "wu_1", "substage_instance_id": "sub-1",
            "validator_result": {
                "reviewVerdict": "needs_input", "reviewMissingInputs": ["原始实验记录"],
                "continueAllowed": False,
            },
        })]
        work_fact = next(fact for fact in current_blocker_facts(task) if fact.work_unit_id == "wu_1")
        self.assertEqual(work_fact.kind, "system_failure")
        self.assertEqual(work_fact.reason_code, "provider_configuration")

    def test_no_active_design_does_not_reactivate_historical_failure(self):
        task = r0c_canonical().model_copy(update={"active_research_design_ref": None})
        self.assertEqual(current_blocker_facts(task), ())

    def test_legacy_reentry_outside_confirmed_design_is_not_current_research(self):
        task = r0c_canonical(reason=None, runtime_status="ready", work_status="queued")
        extra_instance = task.research_design_versions[0].instances[0].model_copy(update={"substage_instance_id": "legacy-step"})
        task.reentry_execution_instances.append(SimpleNamespace(research_design_version=1, instance=extra_instance))
        task.substage_runtime_states.append(task.substage_runtime_states[0].model_copy(update={
            "substage_instance_id": "legacy-step", "status": SubstageRuntimeStatus.BLOCKED_RECOVERABLE,
            "status_reason_code": "provider_configuration", "work_unit_ids": ["legacy-work"],
        }))
        task.work_units.append(task.work_units[0].model_copy(update={
            "substage_instance_id": "legacy-step", "work_unit_id": "legacy-work",
            "status": WorkUnitStatus.FAILED, "terminal_reason_code": "provider_configuration",
        }))
        self.assertEqual(current_blocker_facts(task), ())

    def test_collaboration_keeps_prior_artifact_and_does_not_reopen_optional_failure(self):
        from app.modules.canonical_collaboration import project_canonical_collaboration

        task = r0c_canonical(runtime_status="skipped", work_status="blocked")
        task.substage_runtime_states[0].status_reason_code = "optional_enhancement_unavailable"
        task.artifacts.append(retained_artifact())
        view = project_canonical_collaboration(
            task,
            visible_artifact_ids={row.artifact_id for row in task.artifacts},
        )
        self.assertIsNone(view["workUnits"][0]["waiting"])
        self.assertEqual({row["artifactId"] for row in view["artifacts"]}, {"artifact-before-failure"})
        self.assertEqual(view["allowedActions"], [])




class PublicInterruptionContractTests(unittest.TestCase):
    def project(self, task, *, receipt=None, final_status=None, visible_artifact_ids=None):
        from app.modules.task_public_projection import _research_interruption_projection

        source = {"taskId": "task_1", "revision": 8, "skillPack": "sdr"}
        if receipt is not None:
            source["stopReceipt"] = receipt
        authority = None if final_status is None else {
            "status": final_status, "finalResultId": "final-1", "resultArtifactRefs": ["final-artifact"],
        }
        visible_ids = (
            {row.artifact_id for row in task.artifacts}
            if visible_artifact_ids is None
            else visible_artifact_ids
        )
        return _research_interruption_projection(
            task,
            source=source,
            final_result_authority=authority,
            visible_artifact_ids=visible_ids,
        )

    def test_failure_before_stop_is_primary_and_existing_artifacts_remain_distinct(self):
        task = r0c_canonical(task_status="cancelled", runtime_status="cancelled")
        task.artifacts[:] = [retained_artifact(), retained_artifact(state="needs_review", artifact_id="artifact-review")]
        task.results[:] = [SimpleNamespace(result_id="result-before-failure")]
        before = deepcopy(task)
        view = self.project(task, receipt=stop_receipt(), final_status="needs_review")
        self.assertEqual(view["kind"], "system_failure")
        self.assertEqual(view["responsibility"], "system")
        self.assertEqual(view["controlFact"]["kind"], "user_stop")
        self.assertTrue(view["impact"])
        retained = view["retainedResults"]
        self.assertEqual({row["artifactId"] for row in retained["artifacts"]}, {"artifact-before-failure", "artifact-review"})
        self.assertEqual({row["artifactState"] for row in retained["artifacts"]}, {"usable", "needs_review"})
        self.assertIn("已保留 2 项成果，其中 1 项可用、1 项待审核", retained["summary"])
        self.assertEqual(retained["resultCount"], 1)
        self.assertEqual(retained["finalResultStatus"], "needs_review")
        self.assertEqual(task, before)
        default_copy = " ".join(str(view[key]) for key in ("summary", "impact", "responsibilityLabel", "userAction"))
        self.assertNotIn("provider_configuration", default_copy)
        self.assertEqual(view["diagnostics"][0]["reasonCode"], "provider_configuration")

    def test_only_stop_and_unattributed_cancel_are_different(self):
        task = r0c_canonical(task_status="cancelled", runtime_status="cancelled", work_status="cancelled", reason=None)
        user_stop = self.project(task, receipt=stop_receipt())
        self.assertEqual(user_stop["kind"], "user_stop")
        self.assertEqual(user_stop["responsibility"], "user")
        unattributed = self.project(task)
        self.assertEqual(unattributed["kind"], "execution_cancelled")
        self.assertNotEqual(unattributed["responsibility"], "user")
        self.assertIsNone(unattributed["controlFact"])

    def test_final_result_status_is_forwarded_without_becoming_new_authority(self):
        task = r0c_canonical()
        task.artifacts[:] = [retained_artifact()]
        for status in (None, "current", "stale", "needs_review"):
            with self.subTest(status=status):
                view = self.project(task, final_status=status)
                self.assertEqual(view["retainedResults"]["finalResultStatus"], status)
                self.assertEqual(view["retainedResults"]["artifacts"][0]["artifactState"], "usable")
                self.assertEqual(view["kind"], "system_failure")

    def test_retained_results_include_only_catalog_visible_artifacts(self):
        task = r0c_canonical(task_status="cancelled", runtime_status="cancelled")
        task.artifacts[:] = [
            retained_artifact(artifact_id="visible-usable"),
            retained_artifact(state="needs_review", artifact_id="hidden-artifact"),
            retained_artifact(artifact_id="internal-artifact"),
            retained_artifact(artifact_id="diagnostic-artifact"),
            retained_artifact(artifact_id="audit-artifact"),
            retained_artifact(artifact_id="developer-artifact"),
        ]
        authority = {
            "status": "needs_review",
            "finalResultId": "final-1",
            "resultArtifactRefs": ["hidden-artifact"],
        }
        before = deepcopy(task)

        view = self.project(
            task,
            receipt=stop_receipt(),
            final_status=authority["status"],
            visible_artifact_ids={"visible-usable"},
        )

        retained = view["retainedResults"]
        self.assertEqual(retained["artifacts"], [{
            "artifactId": "visible-usable",
            "artifactState": "usable",
            "version": 1,
        }])
        self.assertIn("已保留 1 项成果，其中 1 项可用、0 项待审核", retained["summary"])
        for hidden_id in (
            "hidden-artifact",
            "internal-artifact",
            "diagnostic-artifact",
            "audit-artifact",
            "developer-artifact",
        ):
            self.assertNotIn(hidden_id, str(retained))
        self.assertEqual(retained["finalResultStatus"], "needs_review")
        self.assertEqual(view["kind"], "system_failure")
        self.assertEqual(view["controlFact"]["kind"], "user_stop")
        self.assertEqual(task, before)

    def test_same_failure_is_projected_consistently_for_every_lane(self):
        projected = []
        for lane in ("cs", "ot", "pvd"):
            view = self.project(r0c_canonical(lane=lane))
            projected.append({key: view[key] for key in ("kind", "summary", "responsibility", "userAction")})
        self.assertEqual(projected[0], projected[1])
        self.assertEqual(projected[1], projected[2])

    def test_failed_task_without_a_research_design_is_not_an_execution_interruption(self):
        task = r0c_canonical(task_status="failed")
        task.research_design_versions.clear()
        self.assertIsNone(self.project(task))

    def test_optional_failure_does_not_interrupt_completed_or_skipped_work(self):
        for status in ("completed", "skipped"):
            task = r0c_canonical(runtime_status=status)
            task.substage_runtime_states[0].status_reason_code = "optional_enhancement_unavailable"
            self.assertIsNone(self.project(task))


if __name__ == "__main__":
    unittest.main()
