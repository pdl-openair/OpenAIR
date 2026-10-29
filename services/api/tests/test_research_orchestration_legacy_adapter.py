from __future__ import annotations

from copy import deepcopy
from hashlib import sha256

from app.models.research_orchestration import LegacyTaskProjection
from app.modules.research_orchestration_repository import adapt_legacy_task


def _legacy_task(selected_capabilities: list[dict]) -> dict:
    return {
        "taskId": "task_legacy_contract",
        "creationPlan": {"selectedCapabilities": selected_capabilities},
        "stageDetails": [
            {
                "stageKey": "problem_modeling",
                "subStages": [
                    {"substage": "question_refinement", "sequence": 0},
                    {
                        "substage": "intent_understanding",
                        "sequence": 1,
                        "subStageInstanceId": "sub_existing",
                    },
                ],
            }
        ],
        "capabilityCalls": [
            {
                "id": "call_legacy_1",
                "subStageInstanceId": "sub_existing",
                "status": "completed",
            }
        ],
        "artifacts": [
            {
                "id": "artifact_legacy_1",
                "sourceCapabilityCallId": "call_legacy_1",
                "artifactState": "usable",
            },
            {"id": "artifact_legacy_2", "artifactState": "usable"},
        ],
    }


def test_legacy_adapter_is_read_only_deterministic_and_non_executable() -> None:
    source = _legacy_task([{"id": "agent_z"}, {"id": "agent_a"}])
    before = deepcopy(source)

    first = adapt_legacy_task(source)
    second = adapt_legacy_task(source)

    assert source == before
    assert first == second
    assert first.research_design_ref.research_design_id == "rd_legacy:task_legacy_contract"
    assert [row.substage_instance_id for row in first.substage_instances] == [
        "sub_legacy:492a809a8127b6a0",
        "sub_existing",
    ]
    assert all(unit.source_mode == "legacy_projection" for unit in first.work_units)
    assert all(unit.dispatchable is False for unit in first.work_units)
    assert all(unit.executor_ref.executor_type == "legacy_unknown" for unit in first.work_units)
    assert all(unit.responsible_actor_ref.actor_id == "legacy_unknown" for unit in first.work_units)
    assert first.collaboration_nodes == []
    projection_dump = first.model_dump(mode="json", by_alias=True)
    assert LegacyTaskProjection.model_validate(projection_dump) == first
    assert set(projection_dump) == {
        "taskId",
        "researchDesignRef",
        "substageInstances",
        "workUnits",
        "collaborationNodes",
    }
    assert "dispatchRecords" not in projection_dump
    assert "executionDecisions" not in projection_dump


def test_selected_capabilities_content_and_order_never_change_responsibility() -> None:
    left = adapt_legacy_task(_legacy_task([{"id": "agent_a"}, {"id": "agent_z"}]))
    right = adapt_legacy_task(_legacy_task([{"id": "agent_z"}, {"id": "agent_a"}, {"id": "skill_x"}]))

    assert left == right
    assert [unit.responsible_actor_ref for unit in left.work_units] == [
        unit.responsible_actor_ref for unit in right.work_units
    ]
    assert [unit.executor_ref for unit in left.work_units] == [unit.executor_ref for unit in right.work_units]


def test_adapter_uses_only_explicit_historical_actor_and_executor_facts() -> None:
    source = _legacy_task([{"id": "agent_must_be_ignored"}])
    source["capabilityCalls"][0]["responsibleActorRef"] = {
        "actorType": "agent",
        "actorId": "historical_reviewer",
        "definitionVersion": 3,
    }
    source["capabilityCalls"][0]["executorRef"] = {
        "executorType": "agent",
        "executorId": "historical_reviewer",
        "definitionVersion": 3,
    }

    projection = adapt_legacy_task(source)
    call_unit = next(unit for unit in projection.work_units if unit.source_capability_call_id == "call_legacy_1")
    assert call_unit.responsible_actor_ref.actor_id == "historical_reviewer"
    assert call_unit.executor_ref.executor_id == "historical_reviewer"


def test_missing_artifact_id_with_existing_capability_call_derives_valid_stable_id() -> None:
    source = _legacy_task([{"id": "agent_must_be_ignored"}])
    source["artifacts"][0].pop("id")
    before = deepcopy(source)

    first = adapt_legacy_task(source)
    second = adapt_legacy_task(source)
    call_unit = next(unit for unit in first.work_units if unit.source_capability_call_id == "call_legacy_1")
    expected_artifact_id = "artifact_legacy:" + sha256(
        b"task_legacy_contract:call_legacy_1"
    ).hexdigest()[:16]

    assert source == before
    assert call_unit.artifact_id == expected_artifact_id
    assert call_unit.artifact_id
    assert first == second
    assert LegacyTaskProjection.model_validate(first.model_dump(mode="json", by_alias=True)) == first
    assert call_unit.source_mode == "legacy_projection"
    assert call_unit.dispatchable is False
    assert first.collaboration_nodes == []


def test_legacy_records_with_historical_work_unit_ids_still_project_to_synthetic_non_dispatchable_units() -> None:
    source = _legacy_task([])
    source["capabilityCalls"][0]["workUnitId"] = "historical-live-looking-work-unit"
    source["artifacts"][0]["workUnitId"] = "historical-live-looking-work-unit"

    projection = adapt_legacy_task(source)

    call_unit = next(unit for unit in projection.work_units if unit.source_capability_call_id == "call_legacy_1")
    assert call_unit.work_unit_id == "wu_legacy:call_legacy_1"
    assert call_unit.source_mode == "legacy_projection"
    assert call_unit.dispatchable is False
    assert projection.collaboration_nodes == []
