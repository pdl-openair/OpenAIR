from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest


from app.modules import workbench_user_actions as module
from app.models.research_orchestration import AssignmentRole, SubstageRuntimeStatus, ExecutionDecisionOutcome


def canonical(
    *, task_status: str = "running", work_status: str = "external_result_unknown",
    execution_mode: str = "automatic_research", runtime_status: str = "completed",
    runtime_reason: str | None = None,
) -> SimpleNamespace:
    """Mutable projection fixture with the same safety facts as canonical models."""
    from copy import deepcopy
    from app.models.research_orchestration import TaskExecutionMode
    from tests.support.r0c_interruption import r0c_canonical

    class Facts(SimpleNamespace):
        def model_copy(self, *, update):
            copied = deepcopy(self)
            copied.__dict__.update(update)
            return copied

    def mutable(value):
        if isinstance(value, list):
            return [mutable(row) for row in value]
        if hasattr(value, "model_dump"):
            return Facts(**{key: mutable(item) for key, item in value.__dict__.items()})
        return value

    user_gap = runtime_status == "needs_input" and runtime_reason == "required_input_missing"
    task = mutable(r0c_canonical(
        task_status=task_status, work_status="completed" if work_status == "succeeded" else work_status,
        runtime_status=runtime_status, reason=runtime_reason,
        decision_status="closed" if user_gap else "dispatched",
        decision_outcome="needs_input" if user_gap else None,
    ))
    task.execution_mode = TaskExecutionMode(execution_mode)
    task.execution_decisions[0].substage_instance_id = "sub_1"
    task.work_units[0].substage_instance_id = "sub_1"
    task.substage_runtime_states[0].substage_instance_id = "sub_1"
    task.research_design_versions[0].instances[0].substage_instance_id = "sub_1"
    return task


def payload(status: str = "running") -> dict:
    return {
        "taskId": "task_1",
        "ownerId": "owner_1",
        "workspaceId": "workspace_1",
        "revision": 8,
        "status": status,
    }


def external_row(**overrides) -> dict:
    row = {
        "dispatch_id": "dispatch_1",
        "execution_decision_id": "decision_1",
        "work_unit_id": "wu_1",
        "dispatch_status": "submission_unknown",
        "dispatch_remote_run_id": None,
        "last_error_code": None,
        "capability_call_id": "call_1",
        "attempt_number": 1,
        "owner_id": "owner_1",
        "workspace_id": "workspace_1",
        "external_state": "unknown",
        "external_remote_run_id": None,
        "canonical_payload_fingerprint": "fp_1",
        "evidence_refs": ["evidence_1"],
    }
    row.update(overrides)
    return row


def project(monkeypatch: pytest.MonkeyPatch, *, task_status="running", work_status="external_result_unknown", rows=None):
    monkeypatch.setattr(module, "_canonical_task", lambda _payload: canonical(task_status=task_status, work_status=work_status))
    monkeypatch.setattr(module, "_external_context_rows", lambda **_kwargs: rows if rows is not None else [external_row()])
    monkeypatch.setattr(module, "_pending_controller_command_rows", lambda **_kwargs: [])
    return module.project_workbench_user_actions(
        task_payload=payload(task_status),
        owner_id="owner_1",
        workspace_id="workspace_1",
        actor_id="actor_1",
    )


def test_external_unknown_never_requires_user_abandon(monkeypatch: pytest.MonkeyPatch) -> None:
    result = project(monkeypatch)
    assert result["lifecycleKind"] == "canonical_v1_2"
    item = result["items"][0]
    assert item["kind"] == "external_result_unknown"
    assert item["allowedActions"] == []
    assert result["unrecoverable"] is False


def test_external_unknown_never_becomes_user_query_protocol(monkeypatch: pytest.MonkeyPatch) -> None:
    result = project(monkeypatch)
    assert result["items"][0]["kind"] == "external_result_unknown"
    assert result["items"][0]["allowedActions"] == []
    assert all(item.get("kind") != "provide_input" for item in result["items"])
    assert not any("explicit_retry" in item.get("allowedActions", []) for item in result["items"])


def test_submission_unknown_with_external_execution_result_unavailable_projects_recovery_item(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = project(
        monkeypatch,
        rows=[
            external_row(
                dispatch_status="submission_unknown",
                external_state="unknown",
                last_error_code="external_execution_result_unavailable",
            )
        ],
    )
    item = result["items"][0]
    assert item["kind"] == "external_result_unknown"
    assert item["workUnitId"] == "wu_1"
    assert item["allowedActions"] == []
    assert "external_execution_result_unavailable" not in item["reason"]


def test_non_unknown_work_does_not_project_user_recovery_and_can_continue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(module, "_canonical_supplement_progression_available", lambda: True)

    result = project(monkeypatch, work_status="cancelled")

    assert all(item.get("kind") != "external_result_unknown" for item in result["items"])
    assert "supplement_input" in result["allowedActions"]
    assert "explicit_retry" not in result["allowedActions"]


def test_retry_is_exposed_only_after_authority_reports_never_submitted(monkeypatch: pytest.MonkeyPatch) -> None:
    result = project(
        monkeypatch,
        task_status="running",
        work_status="blocked",
        rows=[external_row(external_state="never_submitted", dispatch_status="failed", evidence_refs=[])],
    )
    assert result["items"][0]["allowedActions"] == ["explicit_retry"]
    assert result["items"][0]["commandContexts"]["explicit_retry"]["priorAttemptNumber"] == 1
    assert result["unrecoverable"] is False


def test_structured_output_exhaustion_projects_real_explicit_retry_authority(monkeypatch: pytest.MonkeyPatch) -> None:
    result = project(
        monkeypatch,
        task_status="running",
        work_status="failed",
        rows=[external_row(
            external_state="known_failed",
            dispatch_status="failed",
            last_error_code="provider_structured_output_correction_exhausted",
            evidence_refs=[],
        )],
    )

    item = result["items"][0]
    assert item["kind"] == "structured_output_retry_ready"
    assert item["allowedActions"] == ["explicit_retry"]
    assert item["commandContexts"]["explicit_retry"]["reasonCode"] == "user_confirmed_structured_output_retry"
    assert result["unrecoverable"] is False


def test_running_known_failed_exposes_manual_retry_without_error_whitelist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """User stop exists ⇒ user retry must exist for failed attempts not in flight."""
    result = project(
        monkeypatch,
        task_status="running",
        work_status="blocked",
        rows=[external_row(
            external_state="known_failed",
            dispatch_status="failed",
            last_error_code="provider_rejected_request",
            evidence_refs=[],
        )],
    )

    item = result["items"][0]
    assert item["allowedActions"] == ["explicit_retry"]
    assert item["commandContexts"]["explicit_retry"]["reasonCode"] == "user_confirmed_explicit_retry"
    assert result["unrecoverable"] is False


def test_terminal_failed_task_still_unrecoverable_for_known_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = project(
        monkeypatch,
        task_status="failed",
        work_status="failed",
        rows=[external_row(
            external_state="known_failed",
            dispatch_status="failed",
            last_error_code="provider_execution_invalid",
            evidence_refs=[],
        )],
    )

    assert result["items"] == []
    assert result["unrecoverable"] is True

def test_failed_without_safe_server_action_is_explicitly_unrecoverable(monkeypatch: pytest.MonkeyPatch) -> None:
    result = project(monkeypatch, task_status="failed", work_status="failed", rows=[])
    assert result["items"] == []
    assert result["unrecoverable"] is True
    assert result["unrecoverableMessage"] == "这一步没有完成，系统暂时无法自动继续。\n已完成的研究内容和产物不会丢失。"
    assert "stop" not in result["allowedActions"]


@pytest.mark.parametrize(
    ("execution_mode", "runtime_reason"),
    [
        ("stage_confirmation", "stage_confirmation_required"),
        ("manual_step", "manual_step_confirmation_required"),
        ("automatic_research", "stage_confirmation_required"),
    ],
)
def test_awaiting_confirmation_projects_server_authorized_continue_research(
    monkeypatch: pytest.MonkeyPatch,
    execution_mode: str,
    runtime_reason: str,
) -> None:
    canonical_task = canonical(
        task_status="awaiting_confirmation",
        work_status="completed",
        execution_mode=execution_mode,
        runtime_status="ready",
        runtime_reason=runtime_reason,
    )
    monkeypatch.setattr(module, "_canonical_task", lambda _payload: canonical_task)
    monkeypatch.setattr(module, "_external_context_rows", lambda **_kwargs: [])
    monkeypatch.setattr(module, "_pending_controller_command_rows", lambda **_kwargs: [])

    result = module.project_workbench_user_actions(
        task_payload=payload("awaiting_confirmation"),
        owner_id="owner_1",
        workspace_id="workspace_1",
        actor_id="actor_1",
    )

    assert "continue_research" in result["allowedActions"]


@pytest.mark.parametrize(
    ("execution_mode", "runtime_reason"),
    [
        ("stage_confirmation", "stage_confirmation_required"),
        ("manual_step", "manual_step_confirmation_required"),
    ],
)
def test_external_result_unknown_blocks_continue_research(
    monkeypatch: pytest.MonkeyPatch,
    execution_mode: str,
    runtime_reason: str,
) -> None:
    canonical_task = canonical(
        task_status="awaiting_confirmation",
        work_status="external_result_unknown",
        execution_mode=execution_mode,
        runtime_status="ready",
        runtime_reason=runtime_reason,
    )
    monkeypatch.setattr(module, "_canonical_task", lambda _payload: canonical_task)
    monkeypatch.setattr(module, "_external_context_rows", lambda **_kwargs: [])
    monkeypatch.setattr(module, "_pending_controller_command_rows", lambda **_kwargs: [])

    result = module.project_workbench_user_actions(
        task_payload=payload("awaiting_confirmation"),
        owner_id="owner_1",
        workspace_id="workspace_1",
        actor_id="actor_1",
    )

    assert "continue_research" not in result["allowedActions"]


def test_continue_research_is_not_invented_for_automatic_or_unready_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for canonical_task in (
        canonical(
            task_status="awaiting_confirmation",
            work_status="completed",
            execution_mode="automatic_research",
            runtime_status="ready",
        ),
        canonical(
            task_status="awaiting_confirmation",
            work_status="completed",
            execution_mode="manual_step",
            runtime_status="planned",
            runtime_reason="manual_step_confirmation_required",
        ),
    ):
        monkeypatch.setattr(module, "_canonical_task", lambda _payload, task=canonical_task: task)
        monkeypatch.setattr(module, "_external_context_rows", lambda **_kwargs: [])
        monkeypatch.setattr(module, "_pending_controller_command_rows", lambda **_kwargs: [])
        result = module.project_workbench_user_actions(
            task_payload=payload("awaiting_confirmation"),
            owner_id="owner_1",
            workspace_id="workspace_1",
            actor_id="actor_1",
        )
        assert "continue_research" not in result["allowedActions"]

    blocked = canonical(
        task_status="awaiting_confirmation",
        work_status="completed",
        execution_mode="manual_step",
        runtime_status="ready",
        runtime_reason="manual_step_confirmation_required",
    )
    blocked.research_design_versions[0].instances.append(
        SimpleNamespace(substage_instance_id="sub_2", instance_revision=1)
    )
    blocked.substage_runtime_states.append(
        SimpleNamespace(
            status="needs_input", status_reason_code="required_input_missing",
            substage_instance_id="sub_2", instance_revision=1,
        )
    )
    monkeypatch.setattr(module, "_canonical_task", lambda _payload: blocked)
    result = module.project_workbench_user_actions(
        task_payload=payload("awaiting_confirmation"),
        owner_id="owner_1",
        workspace_id="workspace_1",
        actor_id="actor_1",
    )
    assert "continue_research" in result["allowedActions"]


def test_legacy_lifecycle_never_queries_canonical_external_authority(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "_canonical_task", lambda _payload: None)
    monkeypatch.setattr(module, "_external_context_rows", lambda **_kwargs: pytest.fail("canonical tables must not be read"))
    result = module.project_workbench_user_actions(
        task_payload=payload("failed"),
        owner_id="owner_1",
        workspace_id="workspace_1",
        actor_id="actor_1",
    )
    assert result["lifecycleKind"] == "legacy_durable_run"
    assert result["items"] == []
    assert result["unrecoverable"] is False


def test_needs_input_exposes_only_server_authorized_supplement(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "_canonical_supplement_progression_available", lambda: True)
    canonical_task = canonical(task_status="running", work_status="blocked", runtime_status="needs_input", runtime_reason="required_input_missing")
    canonical_task.artifacts = [
        SimpleNamespace(work_unit_id="wu_1", artifact_state="needs_input")
    ]
    monkeypatch.setattr(module, "_canonical_task", lambda _payload: canonical_task)
    monkeypatch.setattr(module, "_external_context_rows", lambda **_kwargs: [])
    monkeypatch.setattr(module, "_pending_controller_command_rows", lambda **_kwargs: [])

    result = module.project_workbench_user_actions(
        task_payload=payload("running"),
        owner_id="owner_1",
        workspace_id="workspace_1",
        actor_id="actor_1",
    )

    item = result["items"][0]
    assert item["kind"] == "provide_input"
    assert item["allowedActions"] == ["supplement_input"]
    assert item["workUnitId"] == "wu_1"


def test_advisory_needs_input_is_labeled_as_optional_supplement(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "_canonical_supplement_progression_available", lambda: True)
    canonical_task = canonical(task_status="running", work_status="succeeded")
    canonical_task.artifacts = [
        SimpleNamespace(
            work_unit_id="wu_1",
            artifact_state="needs_input",
            validator_result={"continueAllowed": True},
        )
    ]
    monkeypatch.setattr(module, "_canonical_task", lambda _payload: canonical_task)
    monkeypatch.setattr(module, "_external_context_rows", lambda **_kwargs: [])
    monkeypatch.setattr(module, "_pending_controller_command_rows", lambda **_kwargs: [])

    result = module.project_workbench_user_actions(
        task_payload=payload("running"),
        owner_id="owner_1",
        workspace_id="workspace_1",
        actor_id="actor_1",
    )

    item = result["items"][0]
    assert item["title"] == "可补充研究信息"
    assert item["allowedActions"] == ["supplement_input"]


def test_action_item_projects_canonical_objective_and_artifact_refs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "_canonical_supplement_progression_available", lambda: True)
    canonical_task = canonical(task_status="running", work_status="blocked", runtime_status="needs_input", runtime_reason="required_input_missing")
    canonical_task.execution_decisions[0].assignments = [
        SimpleNamespace(assignment_id="assignment_1", role=AssignmentRole.PRIMARY, objective="比较 GraphRAG 与混合检索 RAG 的适用范围")
    ]
    canonical_task.work_units[0].assignment_id = "assignment_1"
    canonical_task.artifacts = [
        SimpleNamespace(
            work_unit_id="wu_1",
            artifact_id="artifact_1",
            artifact_state="needs_input",
            validator_result={},
        ),
        SimpleNamespace(
            work_unit_id="wu_1",
            artifact_id="artifact_completed",
            artifact_state="completed",
            validator_result={},
        ),
    ]
    monkeypatch.setattr(module, "_canonical_task", lambda _payload: canonical_task)
    monkeypatch.setattr(module, "_external_context_rows", lambda **_kwargs: [])
    monkeypatch.setattr(module, "_pending_controller_command_rows", lambda **_kwargs: [])

    result = module.project_workbench_user_actions(
        task_payload=payload("running"),
        owner_id="owner_1",
        workspace_id="workspace_1",
        actor_id="actor_1",
    )

    item = result["items"][0]
    assert item["kind"] == "provide_input"
    assert item["objective"] == "比较 GraphRAG 与混合检索 RAG 的适用范围"
    assert item["artifactRefs"] == ["artifact_1"]


def test_canonical_supplement_is_hidden_without_safe_progression_interface(monkeypatch: pytest.MonkeyPatch) -> None:
    canonical_task = canonical(task_status="running", work_status="blocked", runtime_status="needs_input", runtime_reason="required_input_missing")
    canonical_task.artifacts = [SimpleNamespace(work_unit_id="wu_1", artifact_state="needs_input")]
    monkeypatch.setattr(module, "_canonical_supplement_progression_available", lambda: False)
    monkeypatch.setattr(module, "_canonical_task", lambda _payload: canonical_task)
    monkeypatch.setattr(module, "_external_context_rows", lambda **_kwargs: [])
    monkeypatch.setattr(module, "_pending_controller_command_rows", lambda **_kwargs: [])

    result = module.project_workbench_user_actions(
        task_payload=payload("running"),
        owner_id="owner_1",
        workspace_id="workspace_1",
        actor_id="actor_1",
    )

    assert "supplement_input" not in result["allowedActions"]
    item = result["items"][0]
    assert item["kind"] == "provide_input"
    assert item["allowedActions"] == []
    assert "暂不能接收并应用" in item["reason"]
    assert "无需操作" in item["reason"]
    assert "接口" not in item["reason"]
    assert "接入" not in item["reason"]

def test_received_supplement_is_waiting_not_applied(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "_canonical_supplement_progression_available", lambda: True)
    monkeypatch.setattr(module, "_canonical_task", lambda _payload: canonical())
    monkeypatch.setattr(module, "_external_context_rows", lambda **_kwargs: [])
    monkeypatch.setattr(
        module,
        "_pending_controller_command_rows",
        lambda **kwargs: [
            {
                "submitted_task_revision": 8,
                "created_at": "2026-08-06T00:00:00Z",
            }
        ],
    )

    result = module.project_workbench_user_actions(
        task_payload=payload("running"),
        owner_id="owner_1",
        workspace_id="workspace_1",
        actor_id="actor_1",
    )

    item = result["items"][0]
    assert item["kind"] == "supplement_pending"
    assert item["allowedActions"] == []
    assert item["reason"] == "系统已收到补充内容，正在等待下一个安全工作节点应用。"
    assert "已生效" not in item["reason"]


def test_unprogressable_received_supplement_is_projected_as_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(module, "_canonical_supplement_progression_available", lambda: False)
    monkeypatch.setattr(module, "_canonical_task", lambda _payload: canonical())
    monkeypatch.setattr(module, "_external_context_rows", lambda **_kwargs: [])
    monkeypatch.setattr(
        module,
        "_pending_controller_command_rows",
        lambda **_kwargs: [{
            "submitted_task_revision": 8,
            "created_at": "2026-08-06T00:00:00Z",
            "state": "rejected",
            "terminal_reason_code": "supplement_progression_service_unavailable",
        }],
    )

    result = module.project_workbench_user_actions(
        task_payload=payload("running"),
        owner_id="owner_1",
        workspace_id="workspace_1",
        actor_id="actor_1",
    )

    item = result["items"][0]
    assert item["kind"] == "supplement_rejected"
    assert item["title"] == "补充未生效"
    assert item["reason"] == "这次补充没有生效。"

def test_terminal_task_does_not_project_received_supplement(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "_canonical_task", lambda _payload: canonical(task_status="cancelled", work_status="cancelled"))
    monkeypatch.setattr(module, "_external_context_rows", lambda **_kwargs: [])
    monkeypatch.setattr(
        module,
        "_pending_controller_command_rows",
        lambda **_kwargs: pytest.fail("terminal tasks must not read pending supplements"),
    )

    result = module.project_workbench_user_actions(
        task_payload=payload("cancelled"),
        owner_id="owner_1",
        workspace_id="workspace_1",
        actor_id="actor_1",
    )

    assert result["items"] == []
    assert result["allowedActions"] == []


def test_completed_decision_continuation_reconciles_before_planning() -> None:
    source = Path(module.__file__).with_name("research_orchestration_repository.py").read_text(encoding="utf-8")
    method = source[
        source.index("    def prepare_execution_continuation("):
        source.index("    def record_execution_continuation_blocked(")
    ]
    reconcile_call = method.index(
        "projected = self._reconcile_task_lifecycle_with_frozen_snapshot("
    )
    plan_call = method.index("plan = plan_execution_continuation(")
    assert reconcile_call < plan_call
    assert "occurred_at=boundary_time" in method


def test_pending_command_query_is_owner_workspace_task_scoped() -> None:
    source = Path(module.__file__).read_text(encoding="utf-8")
    query = source[source.index("def _pending_controller_command_rows"):source.index("def _string_value")]
    assert "task_id=%s AND owner_id=%s AND workspace_id=%s" in query
    assert "state='received'" in query
    assert "state='rejected'" in query
    assert "supplement_progression_service_unavailable" in query
    assert "lifecycle_kind='canonical_v1_2'" in query


def test_preflight_needs_input_projects_user_facing_gap_without_artifact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(module, "_canonical_supplement_progression_available", lambda: True)
    canonical_task = canonical(task_status="awaiting_confirmation", work_status="completed")
    canonical_task.execution_decisions = []
    canonical_task.work_units = []
    canonical_task.substage_runtime_states = [
        SimpleNamespace(
            status="needs_input",
            status_reason_code="required_input_missing",
            substage_instance_id="sub_1",
        )
    ]
    canonical_task.research_design_versions = [
        SimpleNamespace(
            research_design_id="design-1", version=1, status="confirmed",
            instances=[SimpleNamespace(substage_instance_id="sub_1", instance_revision=1, objective="界定过程监督训练信号")],
        )
    ]
    monkeypatch.setattr(module, "_canonical_task", lambda _payload: canonical_task)
    monkeypatch.setattr(module, "_external_context_rows", lambda **_kwargs: [])
    monkeypatch.setattr(module, "_pending_controller_command_rows", lambda **_kwargs: [])

    result = module.project_workbench_user_actions(
        task_payload=payload("awaiting_confirmation"),
        owner_id="owner_1",
        workspace_id="workspace_1",
        actor_id="actor_1",
    )

    item = result["items"][0]
    assert item["kind"] == "provide_input"
    assert item["title"] == "当前研究信息缺口"
    assert "暂不能接收并应用" in item["reason"]
    assert "无需操作" in item["reason"]
    assert item["objective"] == "界定过程监督训练信号"
    assert item["allowedActions"] == []
    assert "supplement_input" not in result["allowedActions"]


def test_system_contract_failure_does_not_project_supplement_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(module, "_canonical_supplement_progression_available", lambda: True)
    canonical_task = canonical(
        task_status="awaiting_confirmation",
        work_status="blocked",
        runtime_status="blocked_recoverable",
        runtime_reason="input_contract_invalid",
    )
    canonical_task.execution_decisions[0].assignment_preflight_outcomes = [
        SimpleNamespace(
            failure_kind="contract_violation",
            reason_code="input_contract_invalid",
        )
    ]
    monkeypatch.setattr(module, "_canonical_task", lambda _payload: canonical_task)
    monkeypatch.setattr(module, "_external_context_rows", lambda **_kwargs: [])
    monkeypatch.setattr(module, "_pending_controller_command_rows", lambda **_kwargs: [])

    result = module.project_workbench_user_actions(
        task_payload=payload("awaiting_confirmation"),
        owner_id="owner_1",
        workspace_id="workspace_1",
        actor_id="actor_1",
    )

    assert "supplement_input" not in result["allowedActions"]
    assert all(item["kind"] != "provide_input" for item in result["items"])


def test_historical_contract_failure_does_not_hide_current_user_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(module, "_canonical_supplement_progression_available", lambda: True)
    canonical_task = canonical(
        task_status="awaiting_confirmation",
        work_status="blocked", runtime_status="needs_input", runtime_reason="required_input_missing")
    historical = canonical_task.execution_decisions[0].model_copy(update={
        "execution_decision_id": "decision_0",
        "decision_sequence": 1,
        "substage_instance_id": "sub_0",
        "outcome": ExecutionDecisionOutcome.BLOCKED,
        "assignments": [],
        "assignment_preflight_outcomes": [
            SimpleNamespace(
                failure_kind="contract_violation",
                reason_code="input_contract_invalid",
            )
        ],
    })
    current = canonical_task.execution_decisions[0]
    current.decision_sequence = 2
    current.substage_instance_id = "sub_1"
    current.outcome = ExecutionDecisionOutcome.NEEDS_INPUT
    current.assignment_preflight_outcomes = []
    canonical_task.execution_decisions = [historical, current]
    canonical_task.work_units[0].substage_instance_id = "sub_1"
    canonical_task.substage_runtime_states = [
        SimpleNamespace(
            status="blocked_recoverable",
            status_reason_code="input_contract_invalid",
            substage_instance_id="sub_0",
        ),
        SimpleNamespace(
            status=SubstageRuntimeStatus.NEEDS_INPUT,
            active_execution_decision_id=None, instance_revision=1,
            status_reason_code="required_input_missing",
            substage_instance_id="sub_1",
        ),
    ]
    monkeypatch.setattr(module, "_canonical_task", lambda _payload: canonical_task)
    monkeypatch.setattr(module, "_external_context_rows", lambda **_kwargs: [])
    monkeypatch.setattr(module, "_pending_controller_command_rows", lambda **_kwargs: [])

    result = module.project_workbench_user_actions(
        task_payload=payload("awaiting_confirmation"),
        owner_id="owner_1",
        workspace_id="workspace_1",
        actor_id="actor_1",
    )

    assert "supplement_input" in result["allowedActions"]
    assert any(
        item["kind"] == "provide_input"
        and item["allowedActions"] == ["supplement_input"]
        for item in result["items"]
    )


def test_needs_review_artifact_projects_gap_when_runtime_needs_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(module, "_canonical_supplement_progression_available", lambda: True)
    canonical_task = canonical(task_status="awaiting_confirmation", work_status="completed", runtime_status="needs_input", runtime_reason="required_input_missing")
    canonical_task.work_units[0].substage_instance_id = "sub_1"
    canonical_task.substage_runtime_states = [
        SimpleNamespace(
            status=SubstageRuntimeStatus.NEEDS_INPUT,
            active_execution_decision_id=None, instance_revision=1,
            status_reason_code="required_input_missing",
            substage_instance_id="sub_1",
        )
    ]
    canonical_task.artifacts = [
        SimpleNamespace(
            work_unit_id="wu_1",
            artifact_id="artifact_1",
            artifact_state="needs_review",
            validator_result={
                "continueAllowed": False,
                "issues": [
                    {
                        "code": "insufficient_for_next_step",
                        "severity": "blocker",
                        "message": "还不能判断这些方向是否可行或该优先做哪一个。",
                    }
                ],
            },
        )
    ]
    monkeypatch.setattr(module, "_canonical_task", lambda _payload: canonical_task)
    monkeypatch.setattr(module, "_external_context_rows", lambda **_kwargs: [])
    monkeypatch.setattr(module, "_pending_controller_command_rows", lambda **_kwargs: [])

    result = module.project_workbench_user_actions(
        task_payload=payload("awaiting_confirmation"),
        owner_id="owner_1",
        workspace_id="workspace_1",
        actor_id="actor_1",
    )

    item = next(row for row in result["items"] if row["kind"] == "provide_input")
    assert "还不能判断这些方向是否可行或该优先做哪一个" in item["reason"]
    assert item["artifactRefs"] == ["artifact_1"]
    assert item["workUnitId"] == "wu_1"
    assert item["allowedActions"] == ["supplement_input"]


def test_usable_artifact_projects_gap_when_runtime_needs_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(module, "_canonical_supplement_progression_available", lambda: True)
    canonical_task = canonical(task_status="awaiting_confirmation", work_status="completed", runtime_status="needs_input", runtime_reason="required_input_missing")
    canonical_task.work_units[0].substage_instance_id = "sub_1"
    canonical_task.substage_runtime_states = [
        SimpleNamespace(
            status=SubstageRuntimeStatus.NEEDS_INPUT,
            active_execution_decision_id=None, instance_revision=1,
            status_reason_code="required_input_missing",
            substage_instance_id="sub_1",
        )
    ]
    canonical_task.artifacts = [
        SimpleNamespace(
            work_unit_id="wu_1",
            artifact_id="artifact_1",
            artifact_state="usable",
            validator_result=None,
        )
    ]
    monkeypatch.setattr(module, "_canonical_task", lambda _payload: canonical_task)
    monkeypatch.setattr(module, "_external_context_rows", lambda **_kwargs: [])
    monkeypatch.setattr(module, "_pending_controller_command_rows", lambda **_kwargs: [])

    result = module.project_workbench_user_actions(
        task_payload=payload("awaiting_confirmation"),
        owner_id="owner_1",
        workspace_id="workspace_1",
        actor_id="actor_1",
    )

    item = next(row for row in result["items"] if row["kind"] == "provide_input")
    assert item["workUnitId"] == "wu_1"
    assert item["artifactRefs"] == ["artifact_1"]
    assert "缺少继续研究所需的具体条件或材料" in item["reason"]
    assert item["allowedActions"] == ["supplement_input"]


def test_usable_artifact_on_completed_runtime_is_not_a_gap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(module, "_canonical_supplement_progression_available", lambda: True)
    canonical_task = canonical(task_status="running", work_status="completed", runtime_status="completed")
    canonical_task.work_units[0].substage_instance_id = "sub_1"
    canonical_task.substage_runtime_states = [
        SimpleNamespace(status="completed", status_reason_code=None, substage_instance_id="sub_1")
    ]
    canonical_task.artifacts = [
        SimpleNamespace(
            work_unit_id="wu_1",
            artifact_id="artifact_1",
            artifact_state="usable",
            validator_result=None,
        )
    ]
    monkeypatch.setattr(module, "_canonical_task", lambda _payload: canonical_task)
    monkeypatch.setattr(module, "_external_context_rows", lambda **_kwargs: [])
    monkeypatch.setattr(module, "_pending_controller_command_rows", lambda **_kwargs: [])

    result = module.project_workbench_user_actions(
        task_payload=payload("running"),
        owner_id="owner_1",
        workspace_id="workspace_1",
        actor_id="actor_1",
    )

    assert all(row["kind"] != "provide_input" for row in result["items"])


def test_blocking_validator_reason_uses_user_facing_missing_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(module, "_canonical_supplement_progression_available", lambda: True)
    canonical_task = canonical(task_status="running", work_status="blocked", runtime_status="needs_input", runtime_reason="required_input_missing")
    canonical_task.artifacts = [
        SimpleNamespace(
            work_unit_id="wu_1",
            artifact_id="artifact_1",
            artifact_state="needs_input",
            validator_result={
                "continueAllowed": False,
                "missingInputs": ["user_materials_or_scope", "source_records"],
                "issues": [
                    {
                        "code": "missing_inputs",
                        "severity": "blocker",
                        "message": "还没有足够材料判断信息获取与整合是否已经解耦。",
                    }
                ],
            },
        )
    ]
    monkeypatch.setattr(module, "_canonical_task", lambda _payload: canonical_task)
    monkeypatch.setattr(module, "_external_context_rows", lambda **_kwargs: [])
    monkeypatch.setattr(module, "_pending_controller_command_rows", lambda **_kwargs: [])

    result = module.project_workbench_user_actions(
        task_payload=payload("running"),
        owner_id="owner_1",
        workspace_id="workspace_1",
        actor_id="actor_1",
    )

    item = result["items"][0]
    assert "还没有足够材料判断信息获取与整合是否已经解耦" in item["reason"]
    assert "研究材料或范围说明" in item["reason"]
    assert "可追溯的来源记录" in item["reason"]
    assert "source_records" not in item["reason"]


def test_post_confirmation_human_authority_projects_a_real_input_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "_canonical_supplement_progression_available", lambda: True)
    monkeypatch.setattr(module, "_research_intent_is_confirmed", lambda _task: True)
    canonical_task = canonical(
        task_status="awaiting_confirmation",
        work_status="blocked",
        runtime_status="needs_input",
        runtime_reason="required_input_missing",
    )
    canonical_task.execution_decisions[0].outcome = ExecutionDecisionOutcome.NEEDS_INPUT
    canonical_task.artifacts = [
        SimpleNamespace(
            work_unit_id="wu_1",
            artifact_id="artifact_1",
            artifact_state="needs_input",
            validator_result={"continueAllowed": False, "disposition": "human_authority"},
        )
    ]
    monkeypatch.setattr(module, "_canonical_task", lambda _payload: canonical_task)
    monkeypatch.setattr(module, "_external_context_rows", lambda **_kwargs: [])
    monkeypatch.setattr(module, "_pending_controller_command_rows", lambda **_kwargs: [])

    result = module.project_workbench_user_actions(
        task_payload=payload("awaiting_confirmation"),
        owner_id="owner_1",
        workspace_id="workspace_1",
        actor_id="actor_1",
    )

    item = next(item for item in result["items"] if item.get("kind") == "provide_input")
    assert item["workUnitId"] == "wu_1"
    assert item["allowedActions"] == ["supplement_input"]


# R0-C owner cases use coherent current instance / decision identities. They
# protect action effects and their canonical preconditions, not coarse status.
def _r0c_action_task(
    *, task_status="running", runtime_status="blocked_recoverable",
    runtime_reason="provider_configuration", work_status="failed",
):
    from tests.support.r0c_interruption import r0c_canonical

    user_gap = runtime_status == "needs_input" and runtime_reason == "required_input_missing"
    return r0c_canonical(
        task_status=task_status, runtime_status=runtime_status, reason=runtime_reason,
        work_status=work_status, decision_status="closed" if user_gap else "dispatched",
        decision_outcome="needs_input" if user_gap else None,
    )


def _r0c_action_snapshot(monkeypatch, task, *, rows=(), stopped=False):
    source = payload(task.status)
    if stopped:
        source["stopReceipt"] = {
            "taskId": "task_1", "controlAction": "stop",
            "status": "stopped", "active": True,
            "stoppedAt": "2026-10-05T00:02:00Z",
        }
    monkeypatch.setattr(module, "_canonical_task", lambda _source: task)
    monkeypatch.setattr(module, "_canonical_supplement_progression_available", lambda: True)
    monkeypatch.setattr(module, "_external_context_rows", lambda **_scope: list(rows))
    monkeypatch.setattr(module, "_pending_controller_command_rows", lambda **_scope: [])
    snapshot = module.project_workbench_user_actions(
        task_payload=source, owner_id="owner_1", workspace_id="workspace_1", actor_id="actor_1",
    )
    return source, snapshot


def test_r0c_failure_then_stop_cannot_gain_resume_and_hide_the_blocker(monkeypatch):
    task = _r0c_action_task(task_status="cancelled", runtime_status="cancelled")
    source, snapshot = _r0c_action_snapshot(monkeypatch, task, stopped=True)
    assert snapshot["executionStopped"] is True
    assert "continue_research" not in snapshot["allowedActions"]
    assert module.project_continue_research_action(source)["allowed"] is False


def test_r0c_stop_only_keeps_same_task_continue_eligible(monkeypatch):
    task = _r0c_action_task(
        task_status="cancelled", runtime_status="ready", runtime_reason=None,
        work_status="queued",
    )
    source, snapshot = _r0c_action_snapshot(monkeypatch, task, stopped=True)
    assert "continue_research" in snapshot["allowedActions"]
    authority = module.project_continue_research_action(source)
    assert authority["allowed"] is True
    assert authority["revision"] == source["revision"]
    assert source["taskId"] == "task_1"


def test_stop_can_resume_pending_work_without_erasing_a_local_gap(monkeypatch):
    task = _r0c_action_task(
        task_status="cancelled", runtime_status="ready", runtime_reason=None,
        work_status="queued",
    )
    before = task.model_dump(mode="json")
    source, _snapshot = _r0c_action_snapshot(monkeypatch, task, stopped=True)
    monkeypatch.setattr(module, "current_blocker_facts", lambda canonical: (object(),))
    assert module.project_continue_research_action(source)["allowed"] is True
    assert source["status"] == "cancelled"
    assert task.model_dump(mode="json") == before


@pytest.mark.parametrize("task_status", ["completed", "failed"])
def test_r0c_final_task_cannot_be_reopened_by_stale_stop_or_retry_proof(monkeypatch, task_status):
    task = _r0c_action_task(task_status=task_status)
    source, snapshot = _r0c_action_snapshot(
        monkeypatch, task, stopped=True,
        rows=[external_row(external_state="never_submitted", dispatch_status="failed")],
    )
    assert module.project_continue_research_action(source)["allowed"] is False
    assert not {"continue_research", "supplement_input"}.intersection(snapshot["allowedActions"])
    assert not any("explicit_retry" in item["allowedActions"] for item in snapshot["items"])


@pytest.mark.parametrize("external_state", ["unknown", "never_submitted"])
def test_r0c_unknown_cannot_gain_retry_from_an_older_or_conflicting_row(monkeypatch, external_state):
    task = _r0c_action_task(
        runtime_reason="external_result_unknown", work_status="external_result_unknown",
    )
    _source, snapshot = _r0c_action_snapshot(
        monkeypatch, task,
        rows=[external_row(external_state=external_state, dispatch_status="failed")],
    )
    assert "continue_research" not in snapshot["allowedActions"]
    assert not any("explicit_retry" in item["allowedActions"] for item in snapshot["items"])
    assert not any(item["kind"] == "external_result_unknown" for item in snapshot["items"])


@pytest.mark.parametrize("reason", ["provider_configuration", "skill_input_invalid", "future_system_failure"])
def test_r0c_never_submitted_manual_retry_not_gated_by_error_code(monkeypatch, reason):
    """User may retry a failed never_submitted attempt regardless of error code."""
    task = _r0c_action_task(runtime_reason=reason, work_status="blocked")
    _source, snapshot = _r0c_action_snapshot(
        monkeypatch, task,
        rows=[external_row(external_state="never_submitted", dispatch_status="failed", evidence_refs=[])],
    )
    assert any("explicit_retry" in item["allowedActions"] for item in snapshot["items"])


def test_r0c_safe_retry_keeps_the_prior_attempt_identity(monkeypatch):
    task = _r0c_action_task(runtime_reason="provider_transport_not_submitted", work_status="blocked")
    _source, snapshot = _r0c_action_snapshot(
        monkeypatch, task,
        rows=[external_row(external_state="never_submitted", dispatch_status="failed", evidence_refs=[])],
    )
    retries = [item for item in snapshot["items"] if "explicit_retry" in item["allowedActions"]]
    assert len(retries) == 1
    context = retries[0]["commandContexts"]["explicit_retry"]
    assert context["workUnitId"] == "wu_1"
    assert context["priorCapabilityCallId"] == "call_1"
    assert context["priorAttemptNumber"] == 1
    assert context["reasonCode"] == "user_confirmed_explicit_retry"


def test_r0c_genuine_input_has_a_concrete_gap_and_shared_server_eligibility(monkeypatch):
    task = _r0c_action_task(
        task_status="awaiting_confirmation", runtime_status="needs_input",
        runtime_reason="required_input_missing", work_status="blocked",
    )
    task.artifacts[:] = [SimpleNamespace(
        work_unit_id="wu_1", artifact_id="input-gap", artifact_state="needs_input",
        validator_result={
            "continueAllowed": False,
            "missingInputs": ["source_records"],
            "issues": [{"severity": "blocker", "message": "需要原始实验记录以核对测量结果。"}],
        },
    )]
    source, snapshot = _r0c_action_snapshot(monkeypatch, task)
    assert "supplement_input" in snapshot["allowedActions"]
    assert "continue_research" not in snapshot["allowedActions"]
    item = next(row for row in snapshot["items"] if row["kind"] == "provide_input")
    assert item["allowedActions"] == ["supplement_input"]
    assert "原始实验记录" in item["reason"]
    assert item["artifactRefs"] == ["input-gap"]
    authority = module.project_supplement_input_action(source, progression_available=True)
    assert authority["allowed"] is True
    assert authority["revision"] == snapshot["revision"]
    assert module.project_supplement_input_action(source, progression_available=False)["allowed"] is False


def test_r0c_external_dependency_cannot_be_cleared_by_user_supplement(monkeypatch):
    task = _r0c_action_task(runtime_reason="external_dependency_pending", work_status="blocked")
    source, snapshot = _r0c_action_snapshot(monkeypatch, task)
    assert "supplement_input" not in snapshot["allowedActions"]
    assert "continue_research" not in snapshot["allowedActions"]
    assert not any(row["kind"] == "provide_input" for row in snapshot["items"])
    assert module.project_supplement_input_action(source, progression_available=True)["allowed"] is False


@pytest.mark.parametrize("dependency_ids,allowed", [(["wu_1"], True), (["wu_1", "other-work"], False), ([], False)])
def test_r0c_retry_can_release_only_its_exact_proven_dependent_wait(monkeypatch, dependency_ids, allowed):
    from app.models.research_orchestration import SubstageRuntimeStatus, WorkUnitStatus

    task = _r0c_action_task(runtime_reason="provider_transport_not_submitted", work_status="blocked")
    task.execution_decisions[0] = task.execution_decisions[0].model_copy(update={"decision_sequence": 2})
    instance = task.research_design_versions[0].instances[0].model_copy(update={"substage_instance_id": "dependent-step"})
    task.research_design_versions[0].instances.append(instance)
    task.execution_decisions.append(task.execution_decisions[0].model_copy(update={
        "execution_decision_id": "dependent-decision", "selection_request_id": "dependent-selection",
        "decision_sequence": 1, "substage_instance_id": "dependent-step",
    }))
    task.substage_runtime_states.append(task.substage_runtime_states[0].model_copy(update={
        "substage_instance_id": "dependent-step", "status": SubstageRuntimeStatus.BLOCKED_RECOVERABLE,
        "status_reason_code": "dependency_pending", "work_unit_ids": ["dependent-work"],
    }))
    task.work_units.append(task.work_units[0].model_copy(update={
        "work_unit_id": "dependent-work", "execution_decision_id": "dependent-decision",
        "substage_instance_id": "dependent-step", "terminal_reason_code": "dependency_pending",
        "dependency_work_unit_ids": dependency_ids,
    }))
    # This durable unrelated work is unresolved; a retry of wu_1 cannot change it.
    task.work_units.append(task.work_units[0].model_copy(update={
        "work_unit_id": "other-work", "substage_instance_id": "other-step",
        "execution_decision_id": "other-decision", "status": WorkUnitStatus.RUNNING,
        "terminal_reason_code": None,
    }))
    _source, snapshot = _r0c_action_snapshot(monkeypatch, task, rows=[external_row(
        external_state="never_submitted", dispatch_status="failed", last_error_code="provider_transport_not_submitted",
    )])
    exposed = any("explicit_retry" in item["allowedActions"] for item in snapshot["items"])
    assert exposed is allowed


def test_r0c_actual_review_missing_inputs_are_visible_and_accepted(monkeypatch):
    from tests.support.r0c_interruption import r0c_canonical, retained_artifact

    task = r0c_canonical(
        reason="review_needs_input", runtime_status="needs_input", work_status="blocked",
        decision_status="closed", decision_outcome="needs_input",
    )
    task.artifacts.append(retained_artifact(state="needs_input", artifact_id="review-input").model_copy(update={
        "work_unit_id": "wu_1", "substage_instance_id": "sub-1",
        "validator_result": {
            "reviewVerdict": "needs_input", "reviewMissingInputs": ["原始测量条件与实验记录"],
            "reviewBasis": "需要核对测量条件", "continueAllowed": False,
        },
    }))
    source, snapshot = _r0c_action_snapshot(monkeypatch, task)
    item = next(row for row in snapshot["items"] if row["kind"] == "provide_input")
    assert "原始测量条件与实验记录" in item["reason"]
    assert item["allowedActions"] == ["supplement_input"]
    assert module.project_supplement_input_action(source, progression_available=True)["allowed"] is True


def test_r0c_old_review_gap_never_transfers_current_provider_failure_to_user(monkeypatch):
    from tests.support.r0c_interruption import retained_artifact

    task = _r0c_action_task(runtime_reason="provider_configuration", runtime_status="needs_input")
    task.substage_runtime_states[0].status_reason_code = "review_needs_input"
    task.artifacts.append(retained_artifact(state="needs_input").model_copy(update={
        "work_unit_id": "wu_1", "substage_instance_id": "sub-1",
        "validator_result": {"reviewVerdict": "needs_input", "reviewMissingInputs": ["原始记录"], "continueAllowed": False},
    }))
    _source, snapshot = _r0c_action_snapshot(monkeypatch, task)
    assert "supplement_input" not in snapshot["allowedActions"]
    assert not any(item["kind"] == "provide_input" for item in snapshot["items"])


def test_r0c_stop_without_unfinished_work_has_no_empty_resume_action(monkeypatch):
    task = _r0c_action_task(task_status="cancelled", runtime_status="completed", work_status="completed", runtime_reason=None)
    source, snapshot = _r0c_action_snapshot(monkeypatch, task, stopped=True)
    assert "continue_research" not in snapshot["allowedActions"]
    assert module.project_continue_research_action(source)["allowed"] is False


def _r0c_two_input_instances():
    task = _r0c_action_task(
        runtime_status="needs_input", runtime_reason="required_input_missing", work_status="blocked",
    )
    task.research_design_versions[0].instances.append(
        task.research_design_versions[0].instances[0].model_copy(update={
            "substage_instance_id": "sub-2", "objective": "核对第二组实验的原始记录",
        })
    )
    task.execution_decisions.append(task.execution_decisions[0].model_copy(update={
        "execution_decision_id": "decision_2", "decision_sequence": 2,
        "selection_request_id": "selection-2", "substage_instance_id": "sub-2",
        "outcome": ExecutionDecisionOutcome.BLOCKED,
    }))
    task.work_units.append(task.work_units[0].model_copy(update={
        "work_unit_id": "wu_2", "execution_decision_id": "decision_2", "substage_instance_id": "sub-2",
    }))
    task.substage_runtime_states.append(task.substage_runtime_states[0].model_copy(update={
        "substage_instance_id": "sub-2", "work_unit_ids": ["wu_2"],
    }))
    return task


def test_r0c_supplement_item_only_targets_the_instance_that_will_receive_it(monkeypatch):
    task = _r0c_two_input_instances()
    source, snapshot = _r0c_action_snapshot(monkeypatch, task)
    authority = module.project_supplement_input_action(source, progression_available=True)
    assert authority["allowed"] is True
    assert authority["targetSubstageInstanceId"] == "sub-2"
    items = {row["itemId"]: row for row in snapshot["items"] if row["kind"] == "provide_input"}
    assert items["provide-input:runtime:sub-1"]["allowedActions"] == []
    assert items["provide-input:runtime:sub-2"]["allowedActions"] == ["supplement_input"]
    assert "supplement_input" in snapshot["allowedActions"]


def test_r0c_latest_recovery_target_outside_current_design_cannot_accept_input(monkeypatch):
    task = _r0c_two_input_instances()
    task.research_design_versions[0].instances[:] = task.research_design_versions[0].instances[:1]
    source, snapshot = _r0c_action_snapshot(monkeypatch, task)
    assert module.project_supplement_input_action(source, progression_available=True)["allowed"] is False
    assert "supplement_input" not in snapshot["allowedActions"]
    assert not any("supplement_input" in item["allowedActions"] for item in snapshot["items"])


def test_r0c_runtime_configuration_and_work_transport_failure_remain_distinct(monkeypatch):
    from app.modules.research_blocker_semantics import current_blocker_facts

    task = _r0c_action_task(runtime_reason="provider_transport_not_submitted", work_status="blocked")
    task.substage_runtime_states[0].status_reason_code = "provider_configuration"
    facts = current_blocker_facts(task)
    assert {(fact.kind, fact.reason_code) for fact in facts} == {
        ("system_failure", "provider_transport_not_submitted"),
        ("system_failure", "provider_configuration"),
    }
    _source, snapshot = _r0c_action_snapshot(monkeypatch, task, rows=[external_row(
        external_state="never_submitted", dispatch_status="failed",
        last_error_code="provider_transport_not_submitted",
        evidence_refs=[],
    )])
    # Distinct system_failure facts still describe the same WorkUnit; user
    # manual retry remains available (not error-code gated).
    assert any("explicit_retry" in item["allowedActions"] for item in snapshot["items"])
