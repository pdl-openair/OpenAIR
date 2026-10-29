from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from pathlib import Path

import pytest
from app.models.research_orchestration import (
    ResearchTaskV12,
    SubstageRuntimeStatus,
    TaskExecutionMode,
    TaskStatus,
)
from app.modules.research_execution_decision_service import (
    plan_execution_continuation,
    promote_dependency_ready_substages,
)
from tests.test_research_execution_lifecycle import _cs_readiness_continuation_task

ROOT = Path(__file__).resolve().parents[4]
ADAPTER = (
    ROOT
    / "skills/cs_research/reproduction-execution-input-closure/adapter"
    / "reproduction_execution_input_closure_adapter.py"
)
spec = importlib.util.spec_from_file_location("action_scope_input_closure", ADAPTER)
assert spec is not None and spec.loader is not None
closure = importlib.util.module_from_spec(spec)
spec.loader.exec_module(closure)
NOW = datetime(2026, 10, 11, tzinfo=UTC)


@pytest.mark.parametrize("readiness", [None, "not_ready", "partially_ready"])
def test_scientific_gap_releases_preparation_without_authorizing_experiment(readiness):
    task = _cs_readiness_continuation_task(readiness)
    projected = promote_dependency_ready_substages(task, occurred_at=NOW)
    downstream = next(
        row
        for row in projected.substage_runtime_states
        if row.substage_instance_id == "sub_downstream"
    )
    assert downstream.status is SubstageRuntimeStatus.READY
    assert projected.scientific_states == task.scientific_states
    assert projected.execution_decisions == task.execution_decisions
    assert projected.work_units == task.work_units
    assert (
        plan_execution_continuation(
            projected,
            completed_execution_decision_id="decision_contract_full",
            execution_mode=TaskExecutionMode.AUTOMATIC_RESEARCH,
        ).action
        == "start"
    )
    result = closure.run_reproduction_execution_input_closure(
        {
            "reproduction_experiment_design": {"status": "ready"},
            "execution_plan_facts": {
                "schema_version": "openair-execution-plan/v1",
                "state": "succeeded",
                "plan_status": "complete",
            },
            "execution_readiness_record": {
                "status": readiness,
                "blocking_gaps": ["unresolved"],
                "executable_scope": [],
            },
            "code_identity": {},
            "environment_record": {},
        }
    )
    assert result == {"state": "failed", "failure_code": "execution_readiness_not_ready"}
    assert not {"executable", "argv", "frozen_input_sha256"}.intersection(result)


def test_unfinished_formal_dependency_still_orders_preparation():
    task = _cs_readiness_continuation_task("not_ready")
    payload = task.model_dump(mode="json", by_alias=True)
    downstream = next(
        row
        for row in payload["researchDesignVersions"][0]["instances"]
        if row["substageInstanceId"] == "sub_downstream"
    )
    downstream["dependencies"][0].update(
        {
            "conditionType": "instance_completed",
            "requiredArtifactTypes": [],
        }
    )
    task = ResearchTaskV12.model_validate(payload)
    upstream_id = (
        task.research_design_versions[0]
        .instances[-1]
        .dependencies[0]
        .upstream_instance_ref.substage_instance_id
    )
    task = task.model_copy(
        update={
            "substage_runtime_states": [
                row.model_copy(update={"status": SubstageRuntimeStatus.RUNNING})
                if row.substage_instance_id == upstream_id
                else row
                for row in task.substage_runtime_states
            ]
        }
    )
    projected = promote_dependency_ready_substages(task, occurred_at=NOW)
    assert (
        next(
            row
            for row in projected.substage_runtime_states
            if row.substage_instance_id == "sub_downstream"
        ).status
        is SubstageRuntimeStatus.PLANNED
    )


def test_stopped_task_cannot_acquire_continuation():
    task = _cs_readiness_continuation_task("not_ready").model_copy(
        update={"status": TaskStatus.CANCELLED}
    )
    assert (
        plan_execution_continuation(
            task,
            completed_execution_decision_id="decision_contract_full",
            execution_mode=TaskExecutionMode.AUTOMATIC_RESEARCH,
        ).action
        == "none"
    )
