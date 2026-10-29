"""Child-call and skill-script agent token metering (observability gap fix).

Two ledger write points are covered:

- ``execute_new_durable_llm_child`` sub-calls: identity crosses the adapter
  subprocess boundary through the environment, the child request adopts it,
  and ``_record_durable_skill_usage`` records a ``durable_child`` row
  attributed to the parent capability call.
- ``skills/shared/durable_llm_agent``: each agent-loop LLM response is
  metered from its real Anthropic usage under ``durable_skill_agent``.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.execution import durable_skill_runtime as runtime
from app.modules.repo_paths import repo_root


@pytest.fixture(autouse=True)
def isolated_provider_env(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER_API_KEY", "offline-test-key")
    monkeypatch.setenv("LLM_PROVIDER_BASE_URL", "https://blocked.invalid")
    monkeypatch.setenv("LLM_PROVIDER_MODEL", "test-model")
    monkeypatch.setenv("LLM_PROVIDER_MODEL_TERRA", "frozen-terra-A")
    monkeypatch.setenv("LLM_PROVIDER_MODEL_SOL", "frozen-sol-A")
    monkeypatch.setenv("OPENAIR_PARENT_CAPABILITY_CALL_ID", "parent-contract-test")
    for env_key in ("OPENAIR_TASK_ID", "OPENAIR_OWNER_ID", "OPENAIR_WORKSPACE_ID"):
        monkeypatch.delenv(env_key, raising=False)


def _call_child(monkeypatch, **env):
    seen = {}

    def execute(request, *, prepared_runtime):
        seen.update(request=request)
        return {"state": "unknown"}

    monkeypatch.setattr(runtime, "execute_durable_skill", execute)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    result = runtime.execute_new_durable_llm_child(
        skill_id="opentrajectory_source_screening_selection",
        parameters={"candidates": []},
        parent_capability_call_id="parent-contract-test",
        adapter_name="child_metering_test",
    )
    assert result == {"state": "unknown"}
    return seen["request"]


def test_child_request_adopts_metering_identity_from_environment(monkeypatch):
    request = _call_child(
        monkeypatch,
        OPENAIR_TASK_ID="task_meter_1",
        OPENAIR_OWNER_ID="user_meter",
        OPENAIR_WORKSPACE_ID="ws_meter",
    )
    assert request["taskId"] == "task_meter_1"
    assert request["ownerId"] == "user_meter"
    assert request["workspaceId"] == "ws_meter"
    # identity never overrides the child's own evidence lineage
    assert request["providerEvidence"]["evidenceKind"] == runtime.EVIDENCE_KIND_SUB_CALL
    assert request["providerEvidence"]["capabilityCallId"] == "parent-contract-test"


def test_child_request_without_environment_identity_stays_unattributed(monkeypatch):
    request = _call_child(monkeypatch)
    assert "taskId" not in request
    assert "ownerId" not in request
    assert "workspaceId" not in request


def test_child_metering_env_skips_empty_values():
    env = runtime._child_metering_env({
        "taskId": "task_meter_1",
        "ownerId": "   ",
        "workspaceId": None,
    })
    assert env == {"OPENAIR_TASK_ID": "task_meter_1"}


def test_adapter_subprocess_receives_metering_identity(monkeypatch):
    captured = {}

    def fake_runner(**kwargs):
        captured.update(kwargs)
        return {"state": "succeeded"}

    monkeypatch.setattr(runtime, "run_python_skill_adapter", fake_runner)
    fake_runtime = {
        "skillId": "pvd_literature_evidence_acquisition_closure",
        "skillPath": "skills/pvd/pvd-literature-evidence-acquisition-closure",
        "runtimeKind": runtime._RUNTIME_KIND_REASONING_ADAPTER,
        "adapterId": "python_adapter:pvd_literature_evidence_acquisition_closure",
    }
    result = runtime.execute_durable_skill(
        {
            "skillId": fake_runtime["skillId"],
            "parameters": {"user_query": "q"},
            "capabilityCallId": "cap_parent_1",
            "taskId": "task_meter_1",
            "ownerId": "user_meter",
            "workspaceId": "ws_meter",
        },
        prepared_runtime=fake_runtime,
    )
    assert result["state"] == "succeeded"
    assert captured["environment_overrides"] == {
        "OPENAIR_PARENT_CAPABILITY_CALL_ID": "cap_parent_1",
        "OPENAIR_TASK_ID": "task_meter_1",
        "OPENAIR_OWNER_ID": "user_meter",
        "OPENAIR_WORKSPACE_ID": "ws_meter",
    }


def test_record_durable_skill_usage_separates_child_rows_from_dispatch_rows(monkeypatch):
    import app.modules.token_ledger_repository as ledger_repo

    calls = []
    monkeypatch.setattr(ledger_repo, "meter_call", lambda **kwargs: calls.append(kwargs))

    runtime._record_durable_skill_usage(
        request={
            "ownerId": "user_meter",
            "workspaceId": "ws_meter",
            "taskId": "task_meter_1",
            "providerEvidence": {
                "evidenceKind": runtime.EVIDENCE_KIND_SUB_CALL,
                "capabilityCallId": "cap_parent_1",
            },
        },
        provider_id="huiyan",
        model="frozen-terra-A",
        usage_wire={"inputTokens": 10, "outputTokens": 5},
    )
    runtime._record_durable_skill_usage(
        request={
            "ownerId": "user_meter",
            "taskId": "task_meter_1",
            "providerEvidence": {"capabilityCallId": "cap_dispatch_1", "attemptNumber": 1},
        },
        provider_id="huiyan",
        model="frozen-terra-A",
        usage_wire={"inputTokens": 7, "outputTokens": 3},
    )
    runtime._record_durable_skill_usage(
        request={"taskId": "task_meter_1"},
        provider_id="huiyan",
        model="frozen-terra-A",
        usage_wire={"inputTokens": 1},
    )
    assert len(calls) == 2
    child, dispatch = calls
    assert child["source"] == "durable_child"
    assert child["call_id"] == "cap_parent_1"
    assert child["user_id"] == "user_meter"
    assert dispatch["source"] == "durable_dispatch"
    assert dispatch["call_id"] is None


def _load_agent_module():
    agent_path = repo_root() / "skills" / "shared" / "durable_llm_agent.py"
    spec = importlib.util.spec_from_file_location("durable_llm_agent_under_test", str(agent_path))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_agent_meters_real_usage_with_cache_detail(monkeypatch):
    import app.modules.token_ledger_repository as ledger_repo

    agent = _load_agent_module()
    calls = []
    monkeypatch.setattr(ledger_repo, "meter_call", lambda **kwargs: calls.append(kwargs))
    response = SimpleNamespace(
        usage=SimpleNamespace(
            input_tokens=1200, output_tokens=350,
            cache_creation_input_tokens=64, cache_read_input_tokens=0,
        ),
        content=[],
    )
    agent._meter_llm_call(
        response,
        meter_context={"user_id": "user_meter", "task_id": "task_meter_1", "workspace_id": "ws_meter"},
        model="gpt-5.6-terra",
    )
    assert len(calls) == 1
    entry = calls[0]
    assert entry["source"] == "durable_skill_agent"
    assert entry["user_id"] == "user_meter"
    assert entry["task_id"] == "task_meter_1"
    assert entry["provider"] == "anthropic"
    assert entry["model"] == "gpt-5.6-terra"
    assert entry["usage_wire"]["inputTokens"] == 1200
    assert entry["usage_wire"]["outputTokens"] == 350
    assert entry["usage_wire"]["totalTokens"] == 1550
    assert entry["usage_wire"]["estimated"] is False
    # cache tokens stay traceable but outside the billed total (S03-14-01 §5)
    assert entry["usage_wire"]["raw"] == {"cache_creation_input_tokens": 64}


def test_agent_meter_skips_without_context_or_usage(monkeypatch):
    import app.modules.token_ledger_repository as ledger_repo

    agent = _load_agent_module()
    calls = []
    monkeypatch.setattr(ledger_repo, "meter_call", lambda **kwargs: calls.append(kwargs))
    response = SimpleNamespace(
        usage=SimpleNamespace(input_tokens=10, output_tokens=4), content=[]
    )
    agent._meter_llm_call(response, meter_context=None, model="m")
    agent._meter_llm_call(response, meter_context={}, model="m")
    agent._meter_llm_call(
        SimpleNamespace(usage=SimpleNamespace(input_tokens=0, output_tokens=0), content=[]),
        meter_context={"user_id": "user_meter"},
        model="m",
    )
    assert calls == []


def test_agent_meter_never_breaks_the_loop(monkeypatch):
    import app.modules.token_ledger_repository as ledger_repo

    agent = _load_agent_module()
    def exploding(**_kwargs):
        raise RuntimeError("ledger unavailable")
    monkeypatch.setattr(ledger_repo, "meter_call", exploding)
    response = SimpleNamespace(usage=SimpleNamespace(input_tokens=10, output_tokens=4), content=[])
    agent._meter_llm_call(response, meter_context={"user_id": "user_meter"}, model="m")  # must not raise


def _load_formula_adapter_module():
    adapter_path = (
        repo_root() / "skills" / "materials" / "crystal-design-formula-crossover-py"
        / "adapter" / "crossover_adapter.py"
    )
    spec = importlib.util.spec_from_file_location("formula_adapter_under_test", str(adapter_path))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_formula_crossover_meters_internal_llm_usage(monkeypatch):
    import app.modules.token_ledger_repository as ledger_repo

    monkeypatch.setenv("OPENAIR_OWNER_ID", "user_meter")
    monkeypatch.setenv("OPENAIR_TASK_ID", "task_meter_1")
    monkeypatch.setenv("OPENAIR_WORKSPACE_ID", "ws_meter")
    adapter = _load_formula_adapter_module()
    calls = []
    monkeypatch.setattr(ledger_repo, "meter_call", lambda **kwargs: calls.append(kwargs))
    adapter._meter_llm_call(
        model="gpt-5.6-terra",
        body={"model": "gpt-5.6-terra", "messages": []},
        resp={
            "choices": [{"message": {"content": "{\"formulas\": []}"}}],
            "usage": {"prompt_tokens": 321, "completion_tokens": 45},
        },
    )
    assert len(calls) == 1
    entry = calls[0]
    assert entry["source"] == "durable_adapter_llm"
    assert entry["user_id"] == "user_meter"
    assert entry["task_id"] == "task_meter_1"
    assert entry["provider"] == "huiyan"
    assert entry["usage_wire"]["inputTokens"] == 321
    assert entry["usage_wire"]["outputTokens"] == 45
    assert entry["usage_wire"]["totalTokens"] == 366
    assert entry["usage_wire"]["estimated"] is False


def test_formula_crossover_estimates_when_gateway_omits_usage(monkeypatch):
    import app.modules.token_ledger_repository as ledger_repo

    monkeypatch.setenv("OPENAIR_OWNER_ID", "user_meter")
    monkeypatch.delenv("OPENAIR_TASK_ID", raising=False)
    adapter = _load_formula_adapter_module()
    calls = []
    monkeypatch.setattr(ledger_repo, "meter_call", lambda **kwargs: calls.append(kwargs))
    adapter._meter_llm_call(
        model="m",
        body={"model": "m", "messages": [{"role": "user", "content": "x" * 400}]},
        resp={"choices": [{"message": {"content": "y" * 80}}]},
    )
    assert len(calls) == 1
    # a paid HTTP success must never become a free call (S03-14-01 §5)
    assert calls[0]["usage_wire"]["estimated"] is True
    assert calls[0]["usage_wire"]["totalTokens"] > 0
    assert calls[0]["task_id"] is None


def test_formula_crossover_meter_skips_without_owner(monkeypatch):
    import app.modules.token_ledger_repository as ledger_repo

    monkeypatch.delenv("OPENAIR_OWNER_ID", raising=False)
    adapter = _load_formula_adapter_module()
    calls = []
    monkeypatch.setattr(ledger_repo, "meter_call", lambda **kwargs: calls.append(kwargs))
    adapter._meter_llm_call(
        model="m",
        body={"model": "m", "messages": []},
        resp={"usage": {"prompt_tokens": 5, "completion_tokens": 2}},
    )
    assert calls == []


def test_external_adapter_subprocess_receives_metering_identity(monkeypatch):
    captured = {}

    def fake_runner(**kwargs):
        captured.update(kwargs)
        return {"state": "succeeded"}

    monkeypatch.setattr(runtime, "run_python_skill_adapter", fake_runner)
    fake_runtime = {
        "skillId": "crystal_design_formula_crossover_py",
        "skillPath": "skills/materials/crystal-design-formula-crossover-py",
        "runtimeKind": runtime._RUNTIME_KIND_EXTERNAL_ADAPTER,
        "adapterId": "python_adapter:crystal_design_formula_crossover_py",
    }
    runtime.execute_durable_skill(
        {
            "skillId": fake_runtime["skillId"],
            "parameters": {},
            "taskId": "task_meter_1",
            "ownerId": "user_meter",
            "workspaceId": "ws_meter",
        },
        prepared_runtime=fake_runtime,
    )
    assert captured["environment_overrides"] == {
        "OPENAIR_TASK_ID": "task_meter_1",
        "OPENAIR_OWNER_ID": "user_meter",
        "OPENAIR_WORKSPACE_ID": "ws_meter",
    }
