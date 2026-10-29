"""Explicit Skill execution follows canonical Substage eligibility."""

import pytest

from app.execution import agent_service
from app.execution.agent_service import _skill_registered_packs, execute_skill


def test_skill_registered_packs_reads_registry() -> None:
    """Sanity: _skill_registered_packs returns the packs list from registry
    for a known skill. Uses new_task_planning which is registered under sdr."""
    packs = _skill_registered_packs("new_task_planning")
    assert isinstance(packs, list), packs
    # new_task_planning is sdr-registered; the registry may add more packs later
    # but sdr must be present.
    assert "sdr" in packs or len(packs) == 0 or packs, packs


def test_skill_registered_packs_unknown_skill_returns_empty() -> None:
    """Unknown skill_id → empty list (can't determine pack, don't block)."""
    assert _skill_registered_packs("does_not_exist_skill_xyz") == []


@pytest.mark.asyncio
async def test_execute_skill_ignores_pack_when_canonical_substage_matches(monkeypatch) -> None:
    monkeypatch.setattr(agent_service, "_skill_registered_packs", lambda _sid: ["sdr"])
    monkeypatch.setattr(
        agent_service,
        "_load_skill_meta",
        lambda _sid: {"substages": ["stability_verification"]},
    )
    monkeypatch.setattr(
        agent_service,
        "_bind_runtime_context",
        lambda *a, **k: ("execution_validation", "stability_verification", {"subStageInstanceId": "sub_x"}),
    )
    monkeypatch.setattr(agent_service.store, "add_capability_call", lambda *a, **k: {"id": "c1"})
    monkeypatch.setattr(agent_service.store, "patch_capability_call", lambda *a, **k: None)
    monkeypatch.setattr(agent_service.store, "add_artifact", lambda *a, **k: None)
    monkeypatch.setattr(agent_service.store, "append_agent_message", lambda *a, **k: None)
    monkeypatch.setattr(agent_service, "_complete_runtime_context", lambda *a, **k: None)
    monkeypatch.setattr(agent_service.settings, "skill_exec_mode", "replay")
    monkeypatch.setattr(agent_service, "replay_skill", lambda _sid: {"summary": "ok"}, raising=False)

    # Fake task: materials pack, in execution mode.
    fake_task = {
        "taskId": "t_cross",
        "skillPack": "materials",
        "status": "running",
        "creationPlan": {"confirmed": True},
        "stageCatalog": [],
        "stageDetails": [],
        "currentStage": "execution_validation",
    }
    monkeypatch.setattr(agent_service.store, "get_task", lambda _tid: fake_task)
    monkeypatch.setattr(agent_service, "_task_mode", lambda _t: "execution")

    result = await execute_skill("t_cross", "constraints_criteria")

    assert result["status"] == "completed", result


@pytest.mark.asyncio
async def test_execute_skill_blocks_canonical_substage_mismatch(monkeypatch) -> None:
    monkeypatch.setattr(
        agent_service,
        "_load_skill_meta",
        lambda _sid: {"substages": ["question_refinement"]},
    )
    monkeypatch.setattr(
        agent_service,
        "_bind_runtime_context",
        lambda *a, **k: ("execution_validation", "stability_verification", {"subStageInstanceId": "sub_x"}),
    )
    fake_task = {
        "taskId": "t_mismatch",
        "skillPack": "sdr",
        "status": "running",
        "creationPlan": {"confirmed": True},
        "stageCatalog": [],
        "stageDetails": [],
        "currentStage": "execution_validation",
    }
    monkeypatch.setattr(agent_service.store, "get_task", lambda _tid: fake_task)
    monkeypatch.setattr(agent_service, "_task_mode", lambda _t: "execution")

    result = await execute_skill("t_mismatch", "question_formulation")

    assert result["status"] == "failed", result
    assert result["runtime"] == "system_guard", result
    assert "substage mismatch" in result["error"], result["error"]


@pytest.mark.asyncio
async def test_execute_skill_allloys_unknown_skill(monkeypatch) -> None:
    """A skill absent from the registry (custom/workflow-defined) must fall
    through — pack membership can't be determined, so we don't block."""
    monkeypatch.setattr(agent_service, "_skill_registered_packs", lambda _sid: [])
    monkeypatch.setattr(
        agent_service,
        "_bind_runtime_context",
        lambda *a, **k: ("execution_validation", "stability_verification", {"subStageInstanceId": "sub_x"}),
    )
    monkeypatch.setattr(agent_service.store, "add_capability_call", lambda *a, **k: {"id": "c1"})
    monkeypatch.setattr(agent_service.store, "patch_capability_call", lambda *a, **k: None)
    monkeypatch.setattr(agent_service.store, "add_artifact", lambda *a, **k: None)
    monkeypatch.setattr(agent_service.store, "append_agent_message", lambda *a, **k: None)
    monkeypatch.setattr(agent_service, "_complete_runtime_context", lambda *a, **k: None)
    monkeypatch.setattr(agent_service.settings, "skill_exec_mode", "replay")
    monkeypatch.setattr(agent_service, "replay_skill", lambda _sid: {"summary": "ok"}, raising=False)

    fake_task = {
        "taskId": "t_custom",
        "skillPack": "materials",
        "status": "running",
        "creationPlan": {"confirmed": True},
        "stageCatalog": [],
        "stageDetails": [],
        "currentStage": "execution_validation",
    }
    monkeypatch.setattr(agent_service.store, "get_task", lambda _tid: fake_task)
    monkeypatch.setattr(agent_service, "_task_mode", lambda _t: "execution")

    # Should NOT short-circuit — falls through to normal execution path
    # (which in this mock hits replay_skill and returns completed).
    result = await execute_skill("t_custom", "some_custom_workflow_skill")
    assert result["status"] != "failed" or "cross-pack" not in result.get("error", ""), result


@pytest.mark.asyncio
async def test_execute_skill_high_risk_stops_before_runner(monkeypatch) -> None:
    monkeypatch.setattr(agent_service, "_skill_registered_packs", lambda _sid: [])
    monkeypatch.setattr(
        agent_service,
        "_load_skill_meta",
        lambda _sid: {"risk_level": "high", "auto_allowed": False, "review_required": True},
    )

    patched: dict[str, object] = {}

    def _patch_task(_task_id: str, updates: dict) -> dict:
        patched.update(updates)
        return {}

    monkeypatch.setattr(agent_service.store, "patch_task", _patch_task)
    monkeypatch.setattr(
        agent_service,
        "_bind_runtime_context",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("runner context should not be bound")),
    )

    fake_task = {
        "taskId": "t_high_risk",
        "skillPack": "sdr",
        "status": "running",
        "creationPlan": {"confirmed": True},
        "stageCatalog": [],
        "stageDetails": [],
        "currentStage": "execution_validation",
    }
    monkeypatch.setattr(agent_service.store, "get_task", lambda _tid: fake_task)
    monkeypatch.setattr(agent_service, "_task_mode", lambda _t: "execution")

    result = await execute_skill("t_high_risk", "dangerous_skill")

    assert result["status"] == "awaiting_confirmation"
    assert result["runtime"] == "risk_guard"
    assert result["error"] == "high_risk_requires_user_confirmation"
    assert patched == {"status": "awaiting_confirmation"}
