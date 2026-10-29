from __future__ import annotations

from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.modules import task_repository
from app.modules import task_store
from app.modules.task_store import TaskStore, store


client = TestClient(app)

STAGE_KEYS = [
    "problem_modeling",
    "solution_design",
    "execution_validation",
    "insight_generation",
]

EXECUTABLE_SUBSTAGE_BY_STAGE = {
    "problem_modeling": "question_refinement",
    "solution_design": "variable_definition",
    "execution_validation": "evidence_extraction",
    "insight_generation": "report_generation",
}


@pytest.fixture()
def isolated_store():
    original = deepcopy(store._tasks)
    original_persist_dir = store._persist_dir
    store._tasks.clear()
    store._persist_dir = None
    try:
        yield
    finally:
        store._tasks.clear()
        store._tasks.update(original)
        store._persist_dir = original_persist_dir


def _editable_plan(expected_revision: int = 0) -> dict:
    stages = []
    for index, stage_key in enumerate(STAGE_KEYS):
        stages.append(
            {
                "stageKey": stage_key,
                "title": f"阶段 {index + 1}",
                "description": f"阶段 {index + 1} 描述",
                "substages": [
                    {
                        "substage": EXECUTABLE_SUBSTAGE_BY_STAGE[stage_key],
                        "label": f"步骤 {index + 1}",
                        "action": "整理研究问题和边界" if index == 0 else f"执行步骤 {index + 1}",
                        "expectedArtifact": f"阶段 {index + 1} 产物",
                    }
                ],
            }
        )
    return {
        "expectedRevision": expected_revision,
        "goalSummary": "保存完整四阶段研究草案",
        "understanding": {"evidenceBoundaries": ["仅使用已提供材料"]},
        "clarifications": ["确认研究边界"],
        "stages": stages,
    }


def _draft_task(*, creation_status: str = "awaiting_confirmation") -> dict:
    task = store.create_task(
        {
            "title": "四阶段草案",
            "goalSummary": "旧目标",
            "status": "draft",
            "creationStatus": creation_status,
            "creationPlan": {
                "goalSummary": "旧目标",
                "domain": "research_task",
                "skillPack": "sdr",
                "planningSource": "planner",
                "planningAudit": {"internal": "keep"},
                "planningReason": "internal route",
                "orchestratorDecision": {"state": "draft"},
                "selectedCapabilities": [{"kind": "agent", "id": "internal-agent"}],
                "clarifications": [],
                "stages": [{
                    "stageKey": "problem_modeling",
                    "description": "old",
                    "subStages": ["question_refinement"],
                    "substages": [{
                        "substage": "question_refinement",
                        "skills": ["internal-skill"],
                        "rationale": "internal rationale",
                    }],
                }],
                "readyToConfirm": False,
            },
        }
    )
    task["revision"] = 0
    store._tasks[task["taskId"]] = deepcopy(task)
    return task


def test_update_plan_preserves_substage_action(isolated_store) -> None:
    task = _draft_task()

    response = client.post(f"/api/tasks/{task['taskId']}/update-plan", json=_editable_plan())

    assert response.status_code == 200, response.text
    assert response.json()["creationPlan"]["stages"][0]["substages"][0]["action"] == "整理研究问题和边界"


def test_complete_plan_round_trips_and_hides_internal_metadata(isolated_store) -> None:
    task = _draft_task()

    saved = client.post(f"/api/tasks/{task['taskId']}/update-plan", json=_editable_plan())
    loaded = client.get(f"/api/tasks/{task['taskId']}")

    assert saved.status_code == loaded.status_code == 200
    assert loaded.json()["creationPlan"] == saved.json()["creationPlan"]
    assert saved.json()["creationPlan"]["planningSource"] == "planner"
    for internal in ("planningAudit", "planningReason", "orchestratorDecision", "selectedCapabilities"):
        assert internal not in saved.json()["creationPlan"]
    authoritative = store.get_task(task["taskId"])["creationPlan"]
    assert authoritative["planningAudit"] == {"internal": "keep"}
    assert authoritative["planningReason"] == "internal route"
    assert authoritative["orchestratorDecision"]["state"] == "draft"
    assert {row["substages"][0] for row in authoritative["selectedCapabilities"]} == set(
        EXECUTABLE_SUBSTAGE_BY_STAGE.values()
    )
    assert authoritative["stages"][0]["substages"][0]["skills"] == ["internal-skill"]
    assert "skills" not in saved.json()["creationPlan"]["stages"][0]["substages"][0]


def test_internal_metadata_in_request_is_rejected_without_overwrite(isolated_store) -> None:
    task = _draft_task()
    payload = _editable_plan()
    payload["planningAudit"] = {"internal": "overwrite"}

    response = client.post(f"/api/tasks/{task['taskId']}/update-plan", json=payload)

    assert response.status_code == 422
    assert store.get_task(task["taskId"])["creationPlan"]["planningAudit"] == {"internal": "keep"}
    assert store.get_task(task["taskId"])["revision"] == 0


def test_json_plan_reload_from_second_store_is_identical(isolated_store, tmp_path) -> None:
    task = _draft_task()
    store.enable_persistence(tmp_path)

    saved = client.post(f"/api/tasks/{task['taskId']}/update-plan", json=_editable_plan())
    assert saved.status_code == 200, saved.text
    first = store.get_task(task["taskId"])
    reloaded_store = TaskStore()
    reloaded_store.enable_persistence(tmp_path)
    assert reloaded_store.load_persisted_tasks() == 1

    assert reloaded_store.get_task(task["taskId"]) == first


def test_two_updates_with_same_revision_only_one_succeeds(isolated_store) -> None:
    task = _draft_task()
    url = f"/api/tasks/{task['taskId']}/update-plan"

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(lambda _: client.post(url, json=_editable_plan()), range(2)))

    assert sorted(response.status_code for response in responses) == [200, 409]
    conflict = next(response for response in responses if response.status_code == 409)
    assert conflict.json()["detail"] == "task_revision_conflict"
    assert store.get_task(task["taskId"])["revision"] == 1


def test_legacy_json_task_without_revision_starts_at_zero(isolated_store) -> None:
    task = _draft_task()
    store._tasks[task["taskId"]].pop("revision")

    response = client.post(f"/api/tasks/{task['taskId']}/update-plan", json=_editable_plan(0))

    assert response.status_code == 200, response.text
    assert response.json()["revision"] == 1


@pytest.mark.parametrize("creation_status", ["awaiting_confirmation", "revision_requested"])
def test_editable_creation_statuses_are_saved(isolated_store, creation_status: str) -> None:
    task = _draft_task(creation_status=creation_status)
    response = client.post(f"/api/tasks/{task['taskId']}/update-plan", json=_editable_plan())
    assert response.status_code == 200, response.text


@pytest.mark.parametrize("creation_status", ["planning", "confirmed", "cancelled"])
def test_other_creation_statuses_are_rejected(isolated_store, creation_status: str) -> None:
    task = _draft_task(creation_status=creation_status)
    response = client.post(f"/api/tasks/{task['taskId']}/update-plan", json=_editable_plan())
    assert response.status_code == 409


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload["stages"].pop(),
        lambda payload: payload["stages"].reverse(),
        lambda payload: payload["stages"].__setitem__(1, deepcopy(payload["stages"][0])),
        lambda payload: payload["stages"][0]["substages"][0].__setitem__("action", " "),
        lambda payload: payload["stages"][0]["substages"][0].__setitem__("expectedArtifact", ""),
    ],
)
def test_incomplete_or_invalid_four_stage_plan_is_rejected(isolated_store, mutate) -> None:
    task = _draft_task()
    payload = _editable_plan()
    mutate(payload)
    response = client.post(f"/api/tasks/{task['taskId']}/update-plan", json=payload)
    assert response.status_code == 422
    assert store.get_task(task["taskId"])["revision"] == 0


def test_projection_is_consistent_and_revision_increments_once(isolated_store) -> None:
    task = _draft_task()
    existing = store._tasks[task["taskId"]]["stageDetails"][0]
    existing["artifactRefs"] = ["artifact-stage"]
    existing["subStages"] = [{
        "subStage": "question_refinement",
        "status": "not_started",
        "artifactRefs": ["artifact-substage"],
        "capabilityCallRefs": ["call-1"],
        "subStageInstanceId": "sub_existing",
        "parentStageKey": "problem_modeling",
        "label": "old",
        "sequence": 0,
    }]

    response = client.post(f"/api/tasks/{task['taskId']}/update-plan", json=_editable_plan())

    assert response.status_code == 200, response.text
    saved = store.get_task(task["taskId"])
    assert saved["revision"] == 1
    plan_keys = [row["stageKey"] for row in saved["creationPlan"]["stages"]]
    assert plan_keys == saved["stages"]
    assert plan_keys == [row["stageKey"] for row in saved["stageDetails"]]
    assert plan_keys == [row["stageKey"] for row in saved["stageCatalog"]]
    instance = saved["stageDetails"][0]["subStages"][0]
    assert instance["subStageInstanceId"] == "sub_existing"
    assert instance["artifactRefs"] == ["artifact-substage"]
    assert instance["capabilityCallRefs"] == ["call-1"]
    assert saved["stageDetails"][0]["artifactRefs"] == ["artifact-stage"]


def test_final_precompile_failure_does_not_publish_json_candidate(isolated_store, tmp_path, monkeypatch) -> None:
    task = _draft_task()
    store.enable_persistence(tmp_path)
    store._persist_task(task["taskId"])
    path = tmp_path / f"{task['taskId']}.json"
    before_bytes = path.read_bytes()
    before = store.get_task(task["taskId"])
    events: list[tuple] = []

    def fail_precompile(*, creation_plan: dict, goal_summary: str):
        raise RuntimeError("final precompile failed")

    monkeypatch.setattr(task_store, "precompile_creation_plan", fail_precompile)
    monkeypatch.setattr(store, "_emit_task_event", lambda *args: events.append(args))
    with pytest.raises(RuntimeError, match="final precompile failed"):
        store.update_task_plan(task["taskId"], _editable_plan())

    assert path.read_bytes() == before_bytes
    assert store.get_task(task["taskId"]) == before
    assert events == []


def test_legacy_research_start_draft_is_repaired_in_same_projection(isolated_store) -> None:
    task = _draft_task()
    store._tasks[task["taskId"]]["artifacts"] = [{
        "id": "artifact-research-start",
        "taskId": task["taskId"],
        "kind": "research_start_draft",
        "artifactType": "research_start_draft",
        "title": "Research start",
        "summary": "legacy",
    }]

    saved = store.update_task_plan(task["taskId"], _editable_plan())

    assert saved is not None
    artifact = saved["artifacts"][0]
    assert artifact["structuredResearchStart"]["research_goal"] == "保存完整四阶段研究草案"
    assert artifact["researchStartConfirmation"]["status"] == "unconfirmed"
    assert store.get_task(task["taskId"])["revision"] == 1


def test_saved_plan_refreshes_structured_research_start_goal_and_boundaries(isolated_store) -> None:
    task = _draft_task()
    store._tasks[task["taskId"]]["artifacts"] = [{
        "id": "artifact-research-start",
        "taskId": task["taskId"],
        "kind": "research_start_draft",
        "artifactType": "research_start_draft",
        "currentVersionId": "version-pr306",
        "structuredResearchStart": {
            "research_goal": "old goal",
            "non_citable_boundaries": ["old boundary"],
        },
        "downstreamInput": {"nonCitableBoundaries": ["old boundary"]},
    }]
    payload = _editable_plan()
    payload["goalSummary"] = "latest saved research goal"
    payload["understanding"]["evidenceBoundaries"] = ["latest evidence boundary"]

    saved = store.update_task_plan(task["taskId"], payload)

    assert saved is not None
    artifact = store.get_task(task["taskId"])["artifacts"][0]
    assert artifact["structuredResearchStart"]["research_goal"] == "latest saved research goal"
    assert artifact["structuredResearchStart"]["non_citable_boundaries"] == ["latest evidence boundary"]
    assert artifact["currentVersionId"] == "version-pr306"


def test_json_disk_error_does_not_publish_memory_state(isolated_store, monkeypatch) -> None:
    task = _draft_task()
    before = store.get_task(task["taskId"])

    def fail_write(_task_id: str, _task: dict) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(store, "_persist_task_projection", fail_write)
    with pytest.raises(OSError, match="disk full"):
        store.update_task_plan(task["taskId"], _editable_plan())

    assert store.get_task(task["taskId"]) == before


def test_unknown_substage_rejects_before_json_write_memory_or_event(isolated_store, tmp_path, monkeypatch) -> None:
    task = _draft_task()
    store.enable_persistence(tmp_path)
    store._persist_task(task["taskId"])
    path = tmp_path / f"{task['taskId']}.json"
    before_bytes = path.read_bytes()
    before = store.get_task(task["taskId"])
    events: list[tuple] = []
    monkeypatch.setattr(store, "_emit_task_event", lambda *args: events.append(args))
    payload = _editable_plan()
    payload["stages"][0]["substages"][0]["substage"] = "not_a_real_substage"

    response = client.post(f"/api/tasks/{task['taskId']}/update-plan", json=payload)

    assert response.status_code == 400
    assert path.read_bytes() == before_bytes
    assert store.get_task(task["taskId"]) == before
    assert events == []


def test_disabled_substage_rejects_before_publish(isolated_store, monkeypatch) -> None:
    task = _draft_task()
    before = store.get_task(task["taskId"])
    original = task_repository.pack_config.load_stage_substage_map

    def disabled_catalog(*, pack: str = "sdr") -> dict:
        catalog = deepcopy(original(pack=pack))
        for entry in catalog["problem_modeling"]:
            if entry["key"] == "question_refinement":
                entry["enabled"] = False
        return catalog

    monkeypatch.setattr(task_repository.pack_config, "load_stage_substage_map", disabled_catalog)
    response = client.post(f"/api/tasks/{task['taskId']}/update-plan", json=_editable_plan())

    assert response.status_code == 400
    assert "disabled" in response.json()["detail"]
    assert store.get_task(task["taskId"]) == before


def test_missing_specialist_route_rejects_without_ready_to_confirm_change(isolated_store, monkeypatch) -> None:
    task = _draft_task()
    before = store.get_task(task["taskId"])

    class EmptyRegistry:
        def by_sdr_substage(self, *_args):
            return []

        def list_agents(self):
            return []

    monkeypatch.setattr(task_repository, "load_agent_registry", EmptyRegistry)
    response = client.post(f"/api/tasks/{task['taskId']}/update-plan", json=_editable_plan())

    assert response.status_code == 409
    assert response.json()["detail"].startswith("required_capability_unavailable:")
    assert store.get_task(task["taskId"]) == before
    assert before["creationPlan"]["readyToConfirm"] is False


def test_non_draft_task_cannot_save_plan(isolated_store) -> None:
    task = _draft_task()
    store._tasks[task["taskId"]]["status"] = "not_started"
    response = client.post(f"/api/tasks/{task['taskId']}/update-plan", json=_editable_plan())
    assert response.status_code == 409
    assert store.get_task(task["taskId"])["revision"] == 0
