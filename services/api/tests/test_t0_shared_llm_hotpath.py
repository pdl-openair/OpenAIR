from __future__ import annotations

from types import SimpleNamespace

from app.execution import durable_skill_runtime as runtime_module
from app.execution import research_dispatch_skill_provider as dispatch_module


def _boom(*_args, **_kwargs):
    raise AssertionError("registry/runtime resolver must not run on prepared hot path")


def test_execute_durable_skill_reuses_prepared_runtime(monkeypatch):
    prepared = {
        "skillId": "hotpath_skill",
        "definitionVersion": 1,
        "runtimeKind": runtime_module._RUNTIME_KIND_LLM_REASONING,
    }
    expected = {"state": "succeeded", "source": "prepared"}

    monkeypatch.setattr(runtime_module, "resolve_registered_durable_skill", _boom)
    monkeypatch.setattr(runtime_module, "resolve_registered_durable_skill_runtime", _boom)
    monkeypatch.setattr(
        runtime_module,
        "_execute_durable_huiyan_skill",
        lambda request, skill, *, runtime: expected,
    )

    actual = runtime_module.execute_durable_skill(
        {"skillId": "hotpath_skill"},
        prepared_runtime=prepared,
    )
    assert actual is expected


def test_production_entry_reuses_prepared_runtime(monkeypatch):
    from app.modules import skill_binding_source

    prepared = {
        "skillId": "hotpath_skill",
        "definitionVersion": 1,
        "runtimeKind": runtime_module._RUNTIME_KIND_LLM_REASONING,
    }
    captured = {}

    monkeypatch.setattr(runtime_module, "resolve_registered_durable_skill", _boom)
    monkeypatch.setattr(skill_binding_source, "skill_ids_with_binding_inputs", lambda: frozenset())

    def fake_execute(request, *, prepared_runtime=None):
        captured["prepared_runtime"] = prepared_runtime
        return {"state": "succeeded"}

    monkeypatch.setattr(runtime_module, "execute_durable_skill", fake_execute)

    actual = runtime_module.execute_production_durable_skill(
        {"skillId": "hotpath_skill"},
        prepared_runtime=prepared,
    )
    assert actual["state"] == "succeeded"
    assert captured["prepared_runtime"]["skillId"] == "hotpath_skill"


def test_prepared_dispatch_threads_runtime_to_freeze_and_execute(monkeypatch):
    prepared_runtime = {
        "skillId": "hotpath_skill",
        "definitionVersion": 1,
        "runtimeKind": runtime_module._RUNTIME_KIND_LLM_REASONING,
    }
    captured = {}
    frozen = SimpleNamespace(
        skill_id="hotpath_skill",
        logicalProviderId="huiyan",
        modelProfile=None,
        resolvedExecutionPolicy=None,
        orchestrationPlan={
            "instruction": "test",
            "stopConditions": [],
            "inputRefs": [],
            "resolvedParameters": {},
            "bindingInputEvidence": None,
        },
    )

    def fake_frozen(*_args, **kwargs):
        captured["freeze_runtime"] = kwargs.get("prepared_runtime")
        return frozen

    def fake_execute(request, *, prepared_runtime=None):
        captured["execute_runtime"] = prepared_runtime
        return {"state": "succeeded"}

    monkeypatch.setattr(dispatch_module, "frozen_skill_request_for_dispatch", fake_frozen)
    monkeypatch.setattr(dispatch_module, "execute_production_durable_skill", fake_execute)
    monkeypatch.setattr(dispatch_module, "_completed_submission", lambda result, *, frozen: result)

    prepared = dispatch_module.PreparedDirectSkillResearchDispatch(
        capabilities=dispatch_module._DIRECT_SKILL_CAPABILITIES,
        skill_id="hotpath_skill",
        definition_version=1,
        snapshot_definition={},
        source_binding_refs=(),
        repository=object(),
        runtime=prepared_runtime,
        execute=fake_execute,
    )
    request = SimpleNamespace(
        task_id="task-1",
        owner_id="owner-1",
        workspace_id="workspace-1",
        work_unit_id="wu-1",
        idempotency_key="idem-1",
        capability_call_id="call-1",
        execution_decision_id="decision-1",
        attempt_number=1,
    )

    actual = prepared.submit(request)
    assert actual["state"] == "succeeded"
    assert captured["freeze_runtime"] is prepared_runtime
    assert captured["execute_runtime"] is prepared_runtime
