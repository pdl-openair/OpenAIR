from __future__ import annotations

import pytest

from app.modules.skill_binding_source import (
    BINDING_CONTRACT_VERSION,
    BINDING_SOURCE_VERSION,
    SkillBindingSourceError,
    binding_handoff_evidence_matches,
    canonical_binding_handoff_evidence,
    load_skill_binding_source_rows,
    skill_ids_requiring_frozen_handoff_schemas,
    skill_ids_with_binding_inputs,
)


def test_binding_v2_source_is_single_version_and_separates_scientific_and_runtime_inputs() -> None:
    rows = load_skill_binding_source_rows()
    assert rows
    assert BINDING_SOURCE_VERSION == 2
    assert BINDING_CONTRACT_VERSION == 2
    for row in rows:
        scientific = row.get("inputSources")
        if scientific:
            assert scientific["contractVersion"] == 2
            for field in scientific["fields"]:
                assert field["sourcePresence"] in {"required", "optional", "conditional"}
                assert field["source"]["kind"] in {"artifact", "scientificState"}
        runtime = row.get("runtimeInputs")
        if runtime:
            assert runtime["contractVersion"] == 2
            for field in runtime["fields"]:
                assert field["source"]["kind"] in {"executionIdentity", "invocationContext", "literal"}


def test_binding_v2_can_freeze_usage_identity_without_handoff_inputs() -> None:
    rows = load_skill_binding_source_rows()
    identity_only = next(
        row
        for row in rows
        if row["definitionId"] == "sdr:ablation_design"
        and row["skillId"] == "ablation_planning"
    )
    assert "inputSources" not in identity_only
    assert "runtimeInputs" not in identity_only
    assert "ablation_planning" not in skill_ids_with_binding_inputs()


def test_schema_freeze_participants_cover_every_target_and_artifact_producer() -> None:
    rows = load_skill_binding_source_rows()
    participants = skill_ids_requiring_frozen_handoff_schemas()
    assert skill_ids_with_binding_inputs() <= participants
    for row in rows:
        for field in (row.get("inputSources") or {}).get("fields", []):
            source = field["source"]
            if source["kind"] == "artifact":
                producer_skill = source["bindingId"].rsplit(":skill:", 1)[1]
                assert producer_skill in participants


def test_binding_evidence_requires_exact_v2_declaration() -> None:
    row = next(
        row
        for row in load_skill_binding_source_rows()
        if row.get("inputSources")
    )
    skill_id = row["skillId"]
    evidence = {
        "bindingId": row["bindingId"],
        "bindingVersion": row["bindingVersion"],
        "bindingContractVersion": 2,
        "inputSources": row.get("inputSources"),
        "runtimeInputs": row.get("runtimeInputs"),
    }
    assert binding_handoff_evidence_matches(skill_id, evidence) is True
    assert binding_handoff_evidence_matches(
        skill_id,
        {**evidence, "bindingContractVersion": 1},
    ) is False
    assert binding_handoff_evidence_matches(
        skill_id,
        {**evidence, "inputSources": None},
    ) is False


def test_canonical_binding_handoff_evidence_is_the_single_identity_projection() -> None:
    row = next(
        row for row in load_skill_binding_source_rows()
        if row["bindingId"]
        == "bind:opentrajectory:intent_understanding:skill:structured_requirement_generation"
    )

    evidence = canonical_binding_handoff_evidence({**row, "enabled": True})

    assert evidence == {
        "bindingId": row["bindingId"],
        "bindingVersion": row["bindingVersion"],
        "bindingContractVersion": BINDING_CONTRACT_VERSION,
        "inputSources": row.get("inputSources"),
        "runtimeInputs": row.get("runtimeInputs"),
    }
    assert "scientificStateVersion" not in evidence
    assert binding_handoff_evidence_matches("structured_requirement_generation", evidence) is True
    assert binding_handoff_evidence_matches(
        "structured_requirement_generation",
        {**evidence, "scientificStateVersion": 7},
    ) is False


def test_canonical_binding_handoff_evidence_rejects_missing_required_identity() -> None:
    row = next(
        row for row in load_skill_binding_source_rows()
        if row["bindingId"]
        == "bind:pvd:research_goal_equipment_constraints:skill:pvd_research_request_understanding"
    )

    with pytest.raises(SkillBindingSourceError, match="invalid_binding_id"):
        canonical_binding_handoff_evidence({key: value for key, value in row.items() if key != "bindingId"})

    with pytest.raises(SkillBindingSourceError, match="invalid_binding_version"):
        canonical_binding_handoff_evidence({key: value for key, value in row.items() if key != "bindingVersion"})
