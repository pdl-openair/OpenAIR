from __future__ import annotations


def test_single_skill_resolution_freezes_only_requested_registry_row(monkeypatch):
    from app.execution import durable_skill_runtime as runtime

    target = "opentrajectory_source_screening_selection"
    seen: list[str] = []
    original = runtime.freeze_registered_source_contract

    def counted(source_contract, **kwargs):
        seen.append(str(source_contract.get("skill_id") or ""))
        return original(source_contract, **kwargs)

    monkeypatch.setattr(runtime, "freeze_registered_source_contract", counted)

    resolved = runtime.resolve_registered_durable_skill(target)
    assert resolved is not None
    assert resolved["skillId"] == target
    assert seen == [target]


def test_single_skill_resolution_alias_uses_same_targeted_authority(monkeypatch):
    from app.execution import durable_skill_runtime as runtime

    target = "opentrajectory_source_screening_selection"
    seen: list[str] = []
    original = runtime.freeze_registered_source_contract

    def counted(source_contract, **kwargs):
        seen.append(str(source_contract.get("skill_id") or ""))
        return original(source_contract, **kwargs)

    monkeypatch.setattr(runtime, "freeze_registered_source_contract", counted)

    resolved = runtime.resolve_registered_durable_skill(
        "opentrajectory-source-screening-selection"
    )
    assert resolved is not None
    assert resolved["skillId"] == target
    assert seen == [target]
