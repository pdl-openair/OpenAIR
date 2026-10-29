from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.models.research_orchestration import ResearchTaskV12
from app.modules.canonical_progress_reducer import CanonicalProgressReducer
from app.modules.research_finalization import finalize_from_validated_facts


def _task_payload() -> dict:
    path = Path(__file__).parents[3] / "packages" / "shared" / "test-fixtures" / "research-orchestration-v1.2-valid.json"
    return json.loads(path.read_text(encoding="utf-8"))["researchTask"]


def test_completed_task_without_final_authority_is_rejected() -> None:
    payload = _task_payload()
    payload["status"] = "completed"
    with pytest.raises(ValidationError, match="validated Final Result authority"):
        ResearchTaskV12.model_validate(payload)


def test_completed_substages_do_not_complete_without_final_authority() -> None:
    progress = CanonicalProgressReducer().reduce(
        revision=1,
        active_design={"status": "confirmed", "instances": [{"substageInstanceId": "s", "instanceRevision": 1, "stageKey": "insight_generation"}]},
        substage_runtime_states=[{"substageInstanceId": "s", "instanceRevision": 1, "status": "completed"}],
        work_units=[], capability_calls=[], artifacts=[], confirmations=[], task_status="running",
    )
    assert progress["taskProgress"]["status"] == "running"


def test_fact_only_finalization_is_idempotent_without_premature_completion() -> None:
    payload = _task_payload()
    payload["status"] = "running"
    task = ResearchTaskV12.model_validate(payload)
    design = task.research_design_versions[0]

    finalized = finalize_from_validated_facts(
        task,
        design,
        occurred_at=datetime(2026, 8, 19, tzinfo=timezone.utc),
    )
    replayed = finalize_from_validated_facts(
        finalized,
        design,
        occurred_at=datetime(2026, 8, 19, tzinfo=timezone.utc),
    )

    assert len(replayed.scientific_states) == 1
    assert replayed.completion_decisions == []
    assert replayed.final_results == []
    assert replayed.final_result_authority is None
    assert replayed.status.value == "running"
    assert replayed.finished_at is None


def test_finalization_uses_a_new_state_version_when_durable_sources_change() -> None:
    payload = _task_payload()
    payload["workUnits"][0].update(
        {"status": "completed", "finishedAt": "2026-08-19T00:00:01Z"}
    )
    payload["artifacts"][0].update(
        {
            "artifactState": "usable",
            "sourceCapabilityCallId": None,
            "lineageKind": "direct_work_unit_output",
        }
    )
    task = ResearchTaskV12.model_validate(payload)
    design = task.research_design_versions[0]
    first = finalize_from_validated_facts(
        task, design, occurred_at=datetime(2026, 8, 19, tzinfo=timezone.utc)
    )
    changed = first.model_dump(mode="json", by_alias=True)
    extra = dict(changed["artifacts"][0])
    extra["artifactId"] = "artifact-finalization-new-source"
    changed["artifacts"].append(extra)

    second = finalize_from_validated_facts(
        ResearchTaskV12.model_validate(changed),
        design,
        occurred_at=datetime(2026, 8, 19, 0, 1, tzinfo=timezone.utc),
    )

    assert [row.scientific_state_version for row in second.scientific_states] == [1, 2]
    assert second.completion_decisions[-1].scientific_state_version == 2
    assert second.final_results[-1].scientific_state_version == 2
    assert second.final_result_authority is not None
    assert second.final_result_authority.final_result_id == second.final_results[-1].final_result_id
