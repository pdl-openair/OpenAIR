from app.modules.artifact_gate import artifact_state_for_call_status, normalize_artifact_state
from app.modules.research_blocker_semantics import (
    needs_user_input,
    runtime_blocker_status,
)


def test_needs_input_is_reserved_for_explicit_user_owned_reasons() -> None:
    assert needs_user_input("required_input_missing") is False
    assert needs_user_input("authorization_required") is True
    assert needs_user_input("budget_expansion_required") is True
    assert needs_user_input("active_design_not_confirmed") is True


def test_ambiguous_and_system_reasons_default_to_system_recovery() -> None:
    for reason in (
        "external_result_unknown",
        "runtime_unavailable",
        "canonical_skill_composition_not_frozen",
        "dispatch_work_unit_missing_after_action_started",
        "provider_result_unknown",
        "unclassified_future_blocker",
        None,
    ):
        assert needs_user_input(reason) is False
        assert runtime_blocker_status(reason) == "blocked_recoverable"
    assert needs_user_input("policy_prohibited") is False
    assert runtime_blocker_status("policy_prohibited") == "skipped"
    assert runtime_blocker_status("required_input_missing") == "skipped"


def test_budget_blocker_keeps_its_specific_user_facing_reason() -> None:
    from app.modules.research_blocker_semantics import (
        ResearchBlocker,
        blocker_user_explanation,
    )

    explanation = blocker_user_explanation(
        ResearchBlocker("system_failure", "budget_exceeded", "work-1", "substage-1")
    )

    assert explanation == {
        "kind": "runtime_unavailable",
        "reason": "当前资源预算不足，执行已安全暂停。",
        "reasonCode": "system_execution_interrupted",
    }
    assert "budget_exceeded" not in explanation["reason"]


def test_system_runtime_blocker_does_not_become_artifact_input_request() -> None:
    assert normalize_artifact_state("blocked_recoverable") == "failed"
    assert artifact_state_for_call_status("blocked_recoverable") == "needs_review"
    assert artifact_state_for_call_status("needs_input") == "needs_review"
    assert (
        artifact_state_for_call_status(
            "needs_input", reason_code="required_input_missing"
        )
        == "needs_review"
    )
    assert (
        artifact_state_for_call_status(
            "needs_input", reason_code="authorization_required"
        )
        == "needs_input"
    )


def test_artifact_gate_cannot_create_task_recovery_authority() -> None:
    """KB9: an artifact state alone cannot make a task operation effective."""
    from app.modules.artifact_gate import human_gate_for_state

    task_recovery = {
        "retry", "return", "supplement_materials", "allow_retrieval",
        "supplement_input", "continue_research", "explicit_retry", "resume",
    }
    for state in (
        "usable", "needs_review", "needs_input", "insufficient_materials",
        "failed", "partial_success", "rejected_or_needs_revision",
    ):
        gate = human_gate_for_state(state, missing_inputs=["source_records"])
        assert not task_recovery.intersection(gate["allowedActions"]), state
        assert gate["primaryAction"] not in task_recovery, state

    assert human_gate_for_state("usable")["canUseDownstream"] is True
    assert "use_downstream" in human_gate_for_state("usable")["allowedActions"]
    assert human_gate_for_state("needs_review")["canConfirmPass"] is True
    assert "confirm_pass" in human_gate_for_state("needs_review")["allowedActions"]
    assert "confirm_scope_expansion" in human_gate_for_state("partial_success")["allowedActions"]
    assert human_gate_for_state("needs_input")["canUseDownstream"] is False


def test_artifact_gate_preserves_existing_usable_result_without_a_task_status() -> None:
    """KB8: gate input is the artifact fact; later task failure has no veto."""
    from app.modules.artifact_gate import apply_artifact_gate

    artifact = {
        "id": "artifact-before-failure", "artifactState": "usable",
        "content": "已经形成并接受的研究结果", "version": 3,
        "validatorResult": {"artifactState": "usable"},
    }
    apply_artifact_gate(artifact)
    assert artifact["artifactState"] == "usable"
    assert artifact["content"] == "已经形成并接受的研究结果"
    assert artifact["version"] == 3
    assert artifact["humanGate"]["canUseDownstream"] is True
