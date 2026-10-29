from __future__ import annotations

import sys
from pathlib import Path

import pytest

API_ROOT = Path(__file__).resolve().parents[3]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.modules.task_store import TaskStore, _infer_task_result_source


def _live_task() -> dict:
    return {
        "artifacts": [{"id": "art-live", "sourceCapabilityCallId": "call-live"}],
        "capabilityCalls": [{
            "id": "call-live",
            "runtime": "claude_code",
            "runMode": "live",
            "status": "completed",
            "runId": "run-live",
        }],
    }


@pytest.mark.parametrize(
    ("task", "expected"),
    [
        ({}, "none"),
        ({"status": "draft", "resultSource": "live"}, "none"),
        (_live_task(), "live"),
        ({
            "artifacts": [{"id": "a", "sourceCapabilityCallId": "c"}],
            "capabilityCalls": [{"id": "c", "runtime": "fixture", "status": "completed", "runId": "r"}],
        }, "demo"),
        ({
            "artifacts": [{"id": "a", "sourceCapabilityCallId": "c"}],
            "capabilityCalls": [{"id": "c", "runtime": "unknown_runtime", "status": "completed", "runId": "r"}],
        }, "none"),
        ({"resultSource": "replay", **_live_task()}, "replay"),
        ({"artifacts": [{"id": "a", "origin": "imported", "sourceMode": "replay"}]}, "replay"),
        ({"resultSource": "demo", "artifacts": [{"id": "a"}]}, "demo"),
        ({"artifacts": [{"id": "manual"}]}, "none"),
        ({"artifacts": [{"id": "provided", "origin": "provided_materials_only"}]}, "none"),
    ],
)
def test_positive_evidence_result_source_matrix(task: dict, expected: str) -> None:
    assert _infer_task_result_source(task) == expected


def test_unfinished_live_call_does_not_prove_live() -> None:
    task = _live_task()
    task["capabilityCalls"][0]["status"] = "running"
    assert _infer_task_result_source(task) == "none"


def test_add_artifact_keeps_replay_when_call_is_replay_fallback() -> None:
    store = TaskStore()
    task = store.create_task({"title": "Replay task", "goalSummary": "goal"})
    call = store.add_capability_call(
        task["taskId"], skill_id="literature_review", status="completed", runtime="replay_fallback"
    )
    assert call is not None
    artifact = store.add_artifact(
        task["taskId"], kind="stage_summary", title="Replay output", source_capability_call_id=call["id"]
    )
    assert artifact is not None
    assert store.get_task(task["taskId"])["resultSource"] == "replay"


def test_add_artifact_marks_live_only_for_completed_linked_call() -> None:
    store = TaskStore()
    task = store.create_task({"title": "Live task", "goalSummary": "goal"})
    call = store.add_capability_call(
        task["taskId"], skill_id="literature_review", status="completed", runtime="claude_code"
    )
    assert call is not None
    store.add_artifact(
        task["taskId"], kind="stage_summary", title="Live output", source_capability_call_id=call["id"]
    )
    assert store.get_task(task["taskId"])["resultSource"] == "live"


@pytest.mark.parametrize(
    ("runtime", "expected_mode"),
    [
        ("claude_code", "live"),
        ("replay_fallback", "replay"),
        ("fixture", "fixture"),
        ("openair_local_release_gate", "unknown"),
        (None, "unknown"),
    ],
)
def test_new_capability_call_records_run_mode_and_run_id(runtime: str, expected_mode: str) -> None:
    store = TaskStore()
    task = store.create_task({"title": "Audit task", "goalSummary": "goal"})
    call = store.add_capability_call(task["taskId"], skill_id="audit_skill", status="running", runtime=runtime)
    assert call is not None
    assert call["runId"].startswith("run_")
    assert call["runMode"] == expected_mode
    assert call["sourceMode"] == expected_mode
    assert call["sourceMetadata"] == {}


def test_canonical_succeeded_call_requires_exact_live_work_unit_linkage() -> None:
    task = {
        "workUnits": [{"workUnitId": "work-1", "sourceMode": "live", "status": "completed"}],
        "capabilityCalls": [{
            "capabilityCallId": "call-1",
            "workUnitId": "work-1",
            "runtimeBindingId": "provider-binding-1",
            "status": "succeeded",
            "outputArtifactRefs": ["artifact-1"],
        }],
        "artifacts": [{
            "artifactId": "artifact-1",
            "workUnitId": "work-1",
            "sourceCapabilityCallId": "call-1",
        }],
    }

    assert _infer_task_result_source(task) == "live"
    task["workUnits"][0]["sourceMode"] = "legacy_projection"
    assert _infer_task_result_source(task) == "none"



def test_canonical_fixture_or_unknown_runtime_cannot_be_promoted_to_live() -> None:
    task = {
        "workUnits": [{"workUnitId": "work-1", "sourceMode": "live", "status": "completed"}],
        "capabilityCalls": [{
            "capabilityCallId": "call-1",
            "workUnitId": "work-1",
            "runtime": "fixture",
            "status": "succeeded",
            "outputArtifactRefs": ["artifact-1"],
        }],
        "artifacts": [{
            "artifactId": "artifact-1",
            "workUnitId": "work-1",
            "sourceCapabilityCallId": "call-1",
        }],
    }

    assert _infer_task_result_source(task) == "demo"
    task["capabilityCalls"][0]["runtime"] = "unknown_runtime"
    assert _infer_task_result_source(task) == "none"

def test_canonical_unlinked_or_unfinished_call_is_not_live() -> None:
    task = {
        "resultSource": "live",
        "workUnits": [{"workUnitId": "work-1", "sourceMode": "live", "status": "completed"}],
        "capabilityCalls": [{
            "capabilityCallId": "call-1",
            "workUnitId": "work-1",
            "status": "running",
            "outputArtifactRefs": [],
        }],
        "artifacts": [{
            "artifactId": "artifact-1",
            "workUnitId": "work-1",
            "sourceCapabilityCallId": "call-1",
        }],
    }

    assert _infer_task_result_source(task) == "none"
