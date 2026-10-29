import hashlib
import json
from copy import deepcopy
from pathlib import Path

from app.models.research_orchestration import ResearchTaskV12
from app.modules.research_progress_projection import (
    project_research_process_history,
    project_task_progress,
)


FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "packages"
    / "shared"
    / "test-fixtures"
    / "research-orchestration-v1.2-valid.json"
)


def test_history_ignores_missing_call_status_and_keeps_valid_tool_result():
    payload = {
        "capabilityCalls": [
            {"capabilityCallId": "invalid-call", "workUnitId": "invalid-work"},
            {
                "capabilityCallId": "valid-call",
                "workUnitId": "valid-work",
                "status": "succeeded",
                "outputArtifactRefs": ["valid-artifact"],
            },
        ],
    }
    history = project_research_process_history(payload, visible_artifact_ids={"valid-artifact"})
    tool_events = [row for row in history if row["eventType"] == "research_tool_summary"]
    assert len(tool_events) == 1
    assert tool_events[0]["phase"] == "completed"
    assert tool_events[0]["artifactRefs"] == ["valid-artifact"]


def test_project_task_progress_does_not_promote_local_failure_to_canonical_state():
    payload = deepcopy(json.loads(FIXTURE.read_text(encoding="utf-8"))["researchTask"])
    work_unit = payload["workUnits"][0]
    work_unit.update(
        {
            "status": "failed",
            "terminalReasonCode": "dispatch_failed",
            "finishedAt": "2026-07-30T00:00:00Z",
        }
    )
    runtime = next(
        row
        for row in payload["substageRuntimeStates"]
        if row["substageInstanceId"] == work_unit["substageInstanceId"]
    )
    runtime["status"] = "planned"

    projected = project_task_progress(ResearchTaskV12.model_validate(payload), revision=5)

    assert projected.revision == 5
    assert projected.status.value == "running"
    assert projected.substage_runtime_states[0].status.value == "planned"
    assert projected.progress_projection["revision"] == 5
    assert projected.progress_projection["taskProgress"]["status"] == "running"
    assert projected.progress_projection["substageProgress"][0]["status"] == "planned"


def test_project_task_progress_does_not_reopen_completed_runtime():
    payload = deepcopy(json.loads(FIXTURE.read_text(encoding="utf-8"))["researchTask"])
    payload["status"] = "running"
    payload["substageRuntimeStates"][0].update(
        {"status": "completed", "statusReasonCode": None}
    )
    payload["workUnits"][0].update(
        {"status": "failed", "terminalReasonCode": "historical_failure"}
    )

    projected = project_task_progress(
        ResearchTaskV12.model_validate(payload), revision=5
    )

    assert projected.substage_runtime_states[0].status.value == "completed"
    assert projected.progress_projection["substageProgress"][0]["status"] == "completed"


def _confirmation_pause_payload(*, execution_mode="stage_confirmation"):
    payload = deepcopy(json.loads(FIXTURE.read_text(encoding="utf-8"))["researchTask"])
    current = payload["researchDesignVersions"][0]["instances"][0]
    current.update({"stageKey": "problem_modeling", "sequenceHint": 0})
    next_instance = deepcopy(current)
    next_instance.update(
        {
            "substageInstanceId": "sub_solution_design",
            "stageKey": "solution_design",
            "sequenceHint": 1,
        }
    )
    payload["researchDesignVersions"][0]["instances"].append(next_instance)

    current_runtime = payload["substageRuntimeStates"][0]
    current_runtime.update({"status": "completed", "statusReasonCode": None})
    next_runtime = deepcopy(current_runtime)
    next_runtime.update(
        {
            "substageInstanceId": "sub_solution_design",
            "status": "ready",
            "activeExecutionDecisionId": None,
            "workUnitIds": [],
            "artifactRefs": [],
            "statusReasonCode": {
                "stage_confirmation": "stage_confirmation_required",
                "manual_step": "manual_step_confirmation_required",
            }[execution_mode],
        }
    )
    payload["substageRuntimeStates"].append(next_runtime)
    payload["workUnits"][0].update({"status": "completed", "finishedAt": "2026-07-30T00:00:00Z"})
    payload["status"] = "awaiting_confirmation"
    payload["executionMode"] = execution_mode
    return payload


def test_project_task_progress_preserves_stage_confirmation_pause():
    payload = _confirmation_pause_payload()

    projected = project_task_progress(ResearchTaskV12.model_validate(payload), revision=5)

    assert projected.status.value == "awaiting_confirmation"


def test_project_task_progress_preserves_manual_step_confirmation_pause():
    projected = project_task_progress(
        ResearchTaskV12.model_validate(_confirmation_pause_payload(execution_mode="manual_step")),
        revision=5,
    )

    assert projected.status.value == "awaiting_confirmation"


def test_project_task_progress_confirmation_pause_is_idempotent():
    first = project_task_progress(
        ResearchTaskV12.model_validate(_confirmation_pause_payload()), revision=5
    )
    second = project_task_progress(first, revision=6)

    assert first.status.value == second.status.value == "awaiting_confirmation"




def test_process_history_requires_a_persisted_scientific_state_link():
    history = project_research_process_history(
        {
            "researchDesignVersions": [],
            "workUnits": [],
            "capabilityCalls": [],
            "results": [
                {
                    "resultId": "result-structure-only",
                    "workUnitId": "wu-1",
                    "checkStatus": "passed",
                    "artifactRefs": ["artifact-1"],
                    "scientificStateVersions": [],
                    "createdAt": "2026-10-06T02:20:00Z",
                },
                {
                    "resultId": "result-missing-state",
                    "workUnitId": "wu-1",
                    "checkStatus": "passed",
                    "artifactRefs": ["artifact-1"],
                    "scientificStateVersions": [99],
                    "createdAt": "2026-10-06T02:21:00Z",
                },
            ],
            "scientificStates": [],
            "substageRuntimeStates": [],
            "status": "running",
        }
    )

    assert history == []


def test_process_history_fails_closed_when_artifact_visibility_is_not_supplied():
    history = project_research_process_history(
        {
            "researchDesignVersions": [],
            "workUnits": [
                {
                    "workUnitId": "wu-1",
                    "status": "completed",
                    "startedAt": "2026-10-06T02:10:00Z",
                    "finishedAt": "2026-10-06T02:20:00Z",
                    "outputArtifactRefs": ["artifact-private"],
                }
            ],
            "capabilityCalls": [],
            "results": [],
            "scientificStates": [],
            "substageRuntimeStates": [],
            "status": "running",
        }
    )

    assert len(history) == 1
    assert history[0]["artifactRefs"] == []


def test_plan_generation_failure_does_not_narrate_a_finished_research_task():
    history = project_research_process_history(
        {
            "researchDesignVersions": [],
            "workUnits": [],
            "capabilityCalls": [],
            "results": [],
            "scientificStates": [],
            "substageRuntimeStates": [],
            "status": "failed",
            "finishedAt": "2026-10-06T03:00:00Z",
        }
    )

    assert history == []
    assert not any(row["headline"] == "研究任务未能完成" for row in history)


def test_process_history_legacy_terminal_does_not_invent_missing_research_steps():
    history = project_research_process_history(
        {
            "researchDesignVersions": [],
            "workUnits": [],
            "capabilityCalls": [],
            "results": [],
            "scientificStates": [],
            "substageRuntimeStates": [],
            "status": "completed",
            "finishedAt": "2026-10-06T03:00:00Z",
        }
    )

    assert history == [
        {
            "eventId": "durable:task:terminal",
            "eventType": "meaningful_progress",
            "presentationLevel": "primary",
            "workUnitId": None,
            "headline": "研究任务已完成",
            "summary": None,
            "sequence": -1,
            "occurredAt": "2026-10-06T03:00:00Z",
            "startedAt": None,
            "aggregateCount": None,
            "activityKey": "task-terminal",
            "phase": "completed",
            "artifactRefs": [],
            "resultRefs": [],
            "scientificStateVersions": [],
        }
    ]


def test_process_history_rebuilds_only_durable_user_facts():
    payload = {
        "taskId": "task-history",
        "status": "running",
        "finishedAt": None,
        "researchDesignVersions": [
            {
                "researchDesignId": "design-history",
                "version": 1,
                "status": "superseded",
                "confirmedAt": "2026-10-06T01:00:00Z",
            },
            {
                "researchDesignId": "design-history",
                "version": 2,
                "status": "confirmed",
                "confirmedAt": "2026-10-06T02:00:00Z",
            },
        ],
        "workUnits": [
            {
                "workUnitId": "wu-completed",
                "status": "completed",
                "startedAt": "2026-10-06T02:10:00Z",
                "finishedAt": "2026-10-06T02:20:00Z",
                "terminalReasonCode": "internal_reason_must_not_leak",
            },
            {
                "workUnitId": "wu-blocked",
                "status": "blocked",
                "startedAt": "2026-10-06T02:30:00Z",
                "finishedAt": "2026-10-06T02:31:00Z",
                "terminalReasonCode": "provider_internal_failure",
            },
        ],
        "capabilityCalls": [
            {
                "capabilityCallId": "call-1",
                "workUnitId": "wu-completed",
                "capabilityId": "internal-capability-id",
                "status": "succeeded",
                "startedAt": "2026-10-06T02:11:00Z",
                "finishedAt": "2026-10-06T02:19:00Z",
                "outputArtifactRefs": ["artifact-1", "artifact-hidden"],
            }
        ],
        "results": [
            {
                "resultId": "result-accepted",
                "workUnitId": "wu-completed",
                "checkStatus": "passed",
                "artifactRefs": ["artifact-1", "artifact-hidden"],
                "scientificStateVersions": [3],
                "createdAt": "2026-10-06T02:20:00Z",
                "content": "raw result text must never enter the process projection",
            },
            {
                "resultId": "result-not-accepted",
                "workUnitId": "wu-blocked",
                "checkStatus": "passed",
                "artifactRefs": ["artifact-2"],
                "scientificStateVersions": [],
                "createdAt": "2026-10-06T02:31:00Z",
            },
        ],
        "scientificStates": [
            {
                "scientificStateVersion": 3,
                "createdAt": "2026-10-06T02:21:00Z",
                "scientificFacts": {"unknown": "semantic content belongs to T5"},
            }
        ],
        "substageRuntimeStates": [
            {
                "substageInstanceId": "sub-2",
                "instanceRevision": 1,
                "status": "insufficient_evidence",
                "statusReasonCode": "internal_gap_code",
                "updatedAt": "2026-10-06T02:32:00Z",
                "artifactRefs": ["artifact-1"],
            }
        ],
    }

    history = project_research_process_history(
        payload,
        visible_artifact_ids={"artifact-1"},
    )

    assert history == project_research_process_history(
        deepcopy(payload),
        visible_artifact_ids={"artifact-1"},
    )
    assert [row["sequence"] for row in history] == list(range(-len(history), 0))
    assert sum(row["activityKey"] == "research-design:design-history:v1" for row in history) == 1
    assert sum(row["activityKey"] == "research-design:design-history:v2" for row in history) == 1
    capability = next(row for row in history if row["eventType"] == "research_tool_summary")
    assert capability["activityKey"] == hashlib.sha256(b"call-1").hexdigest()[:24]
    accepted = next(
        row for row in history if row["activityKey"] == "accepted-result:result-accepted"
    )
    assert accepted["scientificStateVersions"] == [3]
    assert accepted["resultRefs"] == ["result-accepted"]
    assert accepted["artifactRefs"] == ["artifact-1"]
    assert accepted["occurredAt"] == "2026-10-06T02:21:00Z"
    assert not any("result-not-accepted" in row["eventId"] for row in history)
    assert any(row["headline"] == "研究步骤当前受阻" for row in history)
    assert any(row["headline"].startswith("当前证据仍不足") for row in history)
    serialized = json.dumps(history, ensure_ascii=False)
    assert "internal_reason_must_not_leak" not in serialized
    assert "provider_internal_failure" not in serialized
    assert "internal-capability-id" not in serialized
    assert "artifact-hidden" not in serialized
    assert "raw result text" not in serialized
    assert "semantic content belongs to T5" not in serialized
    assert "ETA" not in serialized
    assert "%" not in serialized
