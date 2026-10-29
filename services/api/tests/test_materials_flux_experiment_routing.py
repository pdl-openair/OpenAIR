"""Routing tests for the flux_experiment durable skill_script bridge.

The durable chain (S2 -> S3 -> S7 -> S8 -> S9) dispatches S8 cloud-experiment
delivery to ``execute_materials_skill_script`` with skill_id=flux_experiment.
These tests pin the routing and parameter passthrough only — the agent loop
itself talks to the cloud platform and is covered by live verification.
"""
from __future__ import annotations

import sys
import types

import pytest

from app.execution.materials_skill_script_runtime import (
    execute_materials_skill_script,
)


def test_flux_experiment_routes_to_durable_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []

    fake = types.ModuleType("run_dispatch")

    def _fake_execute(**kwargs: object) -> dict[str, object]:
        calls.append(dict(kwargs))
        return {"state": "succeeded", "dispatch_report_path": "x"}

    fake.execute_flux_experiment_durable = _fake_execute
    monkeypatch.setitem(sys.modules, "run_dispatch", fake)

    result = execute_materials_skill_script(
        skill_id="flux_experiment",
        task_id="task_rt",
        work_unit_id="wu_rt",
        parameters={"goal": "宽带隙氧化物", "candidate_summary": "top1"},
        owner_id="owner_1",
        workspace_id="ws_1",
    )

    assert result == {"state": "succeeded", "dispatch_report_path": "x"}
    assert len(calls) == 1
    assert calls[0]["task_id"] == "task_rt"
    assert calls[0]["work_unit_id"] == "wu_rt"
    assert calls[0]["parameters"] == {"goal": "宽带隙氧化物", "candidate_summary": "top1"}
    assert calls[0]["owner_id"] == "owner_1"
    assert calls[0]["workspace_id"] == "ws_1"


def test_unknown_skill_id_is_rejected() -> None:
    with pytest.raises(ValueError, match="unsupported_materials_skill_script"):
        execute_materials_skill_script(
            skill_id="not_a_materials_skill",
            task_id="t",
            work_unit_id="w",
            parameters={},
        )
