from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from app.execution import skill_dependency_runtime as runtime


def _snapshot():
    return {
        "bindings": {"agentSkills": [{
            "agentId": "research-agent", "skillId": "registered-skill",
            "skillDefinitionVersion": 2, "enabled": True,
        }]},
        "definitions": {"skills": [{
            "skillId": "registered-skill", "definitionVersion": 2,
        }]},
    }


def _request():
    return SimpleNamespace(
        task_id="offline", owner_id="owner", workspace_id="workspace",
        work_unit_id="work", idempotency_key="call", capability_call_id="capability",
        execution_decision_id="decision", attempt_number=1,
    )


def _call(snapshot, **overrides):
    args = {
        "snapshot": snapshot, "target_skill_ref": ("registered-skill", 2),
        "parameters": {"user_query": "Exact research scope"},
        "canonical_user_query": "Original confirmed goal", "request": _request(),
        "requesting_actor_id": "research-agent",
    }
    return runtime.execute_frozen_agent_skill(**{**args, **overrides})


@pytest.mark.parametrize("change", ["disabled", "wrong_actor", "wrong_version"])
def test_invalid_frozen_grant_stops_before_execution(change):
    snapshot = _snapshot()
    grant = snapshot["bindings"]["agentSkills"][0]
    if change == "disabled":
        grant["enabled"] = False
    elif change == "wrong_actor":
        grant["agentId"] = "another-agent"
    else:
        grant["skillDefinitionVersion"] = 1
    execute = Mock()
    resolver = Mock()
    with pytest.raises(runtime.SkillDependencyContractError, match="frozen_grant_missing"):
        _call(snapshot, execute=execute, runtime_resolver=resolver)
    execute.assert_not_called()
    resolver.assert_not_called()


def test_production_entrypoint_receives_frozen_runtime_and_exact_input(monkeypatch):
    frozen_runtime = {"runtimeKind": "external_adapter", "skillId": "registered-skill"}
    resolver = Mock(return_value=frozen_runtime)
    execute = Mock(return_value={"state": "unknown", "externalResultUnknown": True})
    monkeypatch.setattr(runtime, "execute_production_durable_skill", execute)
    parameters = {"user_query": "Exact scope", "optional": []}
    original = deepcopy(parameters)
    result = _call(
        _snapshot(), parameters=parameters, execute=execute, runtime_resolver=resolver,
    )
    assert result == {"state": "unknown", "externalResultUnknown": True}
    execute.assert_called_once()
    outgoing = execute.call_args.args[0]
    parameters["optional"].append("later mutation")
    assert outgoing["parameters"] == original
    assert outgoing["frozenGoal"] == "Original confirmed goal"
    assert outgoing["executionDecisionId"] == "decision"
    assert execute.call_args.kwargs == {"prepared_runtime": frozen_runtime}
    assert resolver.call_args.kwargs["definition_version"] == 2
    assert resolver.call_args.kwargs["snapshot_definition"]["definitionVersion"] == 2
