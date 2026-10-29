from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from app.config import settings
from app.modules.recorded_run_adapter import load_recorded_run
from app.modules.task_store import (
    TaskStore,
    _infer_task_result_source,
    normalize_task_result_source,
)


def _artifact(call_id: str | None = None, artifact_id: str = "artifact_1") -> dict:
    artifact = {"id": artifact_id, "kind": "report"}
    if call_id is not None:
        artifact["sourceCapabilityCallId"] = call_id
    return artifact


def _call(
    call_id: str,
    *,
    runtime: str | None = None,
    run_mode: str | None = None,
    source_mode: str | None = None,
    status: str = "completed",
    artifact_refs: list[str] | None = None,
) -> dict:
    return {
        "id": call_id,
        "status": status,
        "runtime": runtime,
        "runMode": run_mode,
        "sourceMode": source_mode,
        "artifactRefs": list(artifact_refs or []),
        "outputArtifactRefs": list(artifact_refs or []),
    }


@pytest.mark.parametrize(
    ("task", "expected"),
    [
        pytest.param(
            {
                "resultSource": "replay",
                "artifacts": [_artifact("call_1")],
                "capabilityCalls": [
                    _call(
                        "call_1",
                        runtime="claude_code",
                        run_mode="replay",
                        source_mode="replay",
                    )
                ],
            },
            "replay",
            id="explicit-replay-recorded-original-live-runtime",
        ),
        pytest.param(
            {
                "resultSource": "demo",
                "artifacts": [_artifact("call_1")],
                "capabilityCalls": [_call("call_1", runtime="fixture")],
            },
            "demo",
            id="explicit-demo-fixture-runtime",
        ),
        pytest.param(
            {
                "resultSource": "none",
                "artifacts": [_artifact("call_1")],
                "capabilityCalls": [_call("call_1", runtime="fixture")],
            },
            "none",
            id="fixture-runtime-never-live",
        ),
        pytest.param(
            {
                "resultSource": "none",
                "artifacts": [_artifact("call_1")],
                "capabilityCalls": [_call("call_1", runtime="future_provider_v9")],
            },
            "none",
            id="unknown-runtime-never-live",
        ),
        pytest.param(
            {"resultSource": "live", "artifacts": [_artifact()], "capabilityCalls": []},
            "none",
            id="artifact-without-call-is-not-live",
        ),
        pytest.param(
            {
                "resultSource": "replay",
                "artifacts": [_artifact("call_live")],
                "capabilityCalls": [
                    _call(
                        "call_live",
                        runtime="claude_code",
                        run_mode="live",
                        source_mode="live",
                    )
                ],
            },
            "live",
            id="authoritative-live-upgrades-explicit-replay",
        ),
        pytest.param(
            {
                "resultSource": "none",
                "artifacts": [_artifact("call_replay")],
                "capabilityCalls": [
                    _call(
                        "call_replay",
                        runtime="claude_code",
                        run_mode="replay",
                        source_mode="replay",
                    )
                ],
            },
            "replay",
            id="linked-replay-call",
        ),
        pytest.param(
            {
                "resultSource": "none",
                "artifacts": [_artifact("call_replay")],
                "capabilityCalls": [_call("call_replay", runtime="replay_fallback")],
            },
            "replay",
            id="legacy-replay-runtime-one-way-compatibility",
        ),
        pytest.param(
            {
                "resultSource": "none",
                "artifacts": [_artifact(artifact_id="artifact_1")],
                "capabilityCalls": [
                    _call(
                        "call_live",
                        run_mode="live",
                        source_mode="live",
                        artifact_refs=["artifact_1"],
                    )
                ],
            },
            "live",
            id="reverse-call-artifact-linkage",
        ),
        pytest.param(
            {"resultSource": "live", "artifacts": [], "capabilityCalls": []},
            "none",
            id="explicit-live-without-artifact-is-none",
        ),
        pytest.param(
            {"resultSource": "replay", "artifacts": [], "capabilityCalls": []},
            "replay",
            id="explicit-replay-without-artifact-is-allowed",
        ),
    ],
)
def test_result_source_truth_matrix(task: dict, expected: str) -> None:
    assert _infer_task_result_source(task) == expected


def test_mixed_live_and_replay_uses_authoritative_live_deterministically() -> None:
    task = {
        "resultSource": "replay",
        "artifacts": [_artifact("call_replay", "artifact_replay"), _artifact("call_live", "artifact_live")],
        "capabilityCalls": [
            _call("call_replay", run_mode="replay", source_mode="replay"),
            _call("call_live", run_mode="live", source_mode="live"),
        ],
    }
    assert _infer_task_result_source(task) == "live"
    assert _infer_task_result_source({**task, "capabilityCalls": list(reversed(task["capabilityCalls"]))}) == "live"


def test_incomplete_live_call_cannot_upgrade_result_source() -> None:
    task = {
        "resultSource": "replay",
        "artifacts": [_artifact("call_live")],
        "capabilityCalls": [
            _call("call_live", run_mode="live", source_mode="live", status="running")
        ],
    }
    assert _infer_task_result_source(task) == "replay"


def test_task_store_list_and_detail_return_the_same_normalized_source(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "persistence_backend", "json")
    raw = {
        "taskId": "task_1",
        "resultSource": "live",
        "artifacts": [_artifact("call_1")],
        "capabilityCalls": [_call("call_1", runtime="fixture")],
    }
    task_store = TaskStore()
    task_store._tasks["task_1"] = deepcopy(raw)

    detail = task_store.get_task("task_1")
    listed = next(task for task in task_store.list_tasks() if task["taskId"] == "task_1")
    assert detail is not None
    assert detail["resultSource"] == listed["resultSource"] == "none"


def test_normalization_does_not_mutate_repository_payload() -> None:
    raw = {
        "taskId": "task_1",
        "resultSource": "live",
        "artifacts": [_artifact("call_1")],
        "capabilityCalls": [_call("call_1", runtime="fixture")],
    }
    normalized = normalize_task_result_source(raw)
    assert normalized["resultSource"] == "none"
    assert raw["resultSource"] == "live"


def test_recorded_run_stays_replay_after_import_persist_and_get(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "persistence_backend", "json")
    run_dir = tmp_path / "recorded_run"
    run_dir.mkdir()
    (run_dir / "task.json").write_text(
        json.dumps(
            {
                "task_id": "task_recorded",
                "title": "Recorded task",
                "status": "completed",
                "current_stage": "problem_modeling",
                "created_at": "2026-08-06T00:00:00Z",
                "updated_at": "2026-08-06T00:01:00Z",
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "stages.json").write_text("[]", encoding="utf-8")
    (run_dir / "artifacts.json").write_text(
        json.dumps(
            [
                {
                    "artifact_id": "artifact_1",
                    "title": "Recorded output",
                    "source_capability_call_id": "call_1",
                }
            ]
        ),
        encoding="utf-8",
    )
    (run_dir / "capability_calls.json").write_text(
        json.dumps(
            [
                {
                    "call_id": "call_1",
                    "skill_id": "recorded_skill",
                    "status": "completed",
                    "runtime": "claude_code",
                    "artifact_refs": ["artifact_1"],
                }
            ]
        ),
        encoding="utf-8",
    )

    recorded = load_recorded_run(run_dir)
    assert recorded["resultSource"] == "replay"
    assert recorded["capabilityCalls"][0]["runMode"] == "replay"
    assert recorded["capabilityCalls"][0]["sourceMode"] == "replay"
    assert recorded["capabilityCalls"][0]["sourceMetadata"]["recordedOriginalRuntime"] == "claude_code"

    persist_dir = tmp_path / "persisted"
    task_store = TaskStore()
    task_store.enable_persistence(persist_dir)
    task_store.import_persisted_task(recorded)

    reloaded_store = TaskStore()
    reloaded_store.enable_persistence(persist_dir)
    assert reloaded_store.load_persisted_tasks() == 1
    reloaded = reloaded_store.get_task("task_recorded")
    assert reloaded is not None
    assert reloaded["resultSource"] == "replay"
