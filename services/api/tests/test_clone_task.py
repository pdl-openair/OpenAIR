"""Tests for POST /api/tasks/{task_id}/clone (whole-project clone + template)."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

from app.config import settings
from app.models.schemas import CloneTaskIn
from app.routes import tasks as tasks_route
from app.routes.tasks import _fork_v12_contract_into


_SOURCE = {
    "taskId": "src1",
    "ownerId": "owner1",
    "workspaceId": "ws1",
    "createdBy": "owner1",
    "title": "Original",
    "goalSummary": "study X",
    "domain": "research_task",
    "skillPack": "sdr",
    "creationPlan": {"goalSummary": "study X", "stages": []},
    "stages": ["problem_modeling", "solution_design"],
    "executionMode": "stage_confirmation",
    "capabilityCalls": [],
}

_ARTIFACTS = [
    {"id": "a1", "storageType": "inline", "inlineContent": "x", "kind": "report",
     "title": "A1", "artifactState": "usable"},
    {"id": "a2", "storageType": "inline", "inlineContent": "y", "kind": "report",
     "title": "A2", "artifactState": "usable"},
]


def _wire_clone_mocks(monkeypatch, reused: list):
    monkeypatch.setattr(settings, "persistence_backend", "postgres")

    class FakeTaskRepo:
        def get_task(self, tid):
            if tid == "src1":
                return dict(_SOURCE)
            return {"taskId": tid, "ownerId": "owner1", "workspaceId": "ws1",
                    "title": "New", "creationPlan": _SOURCE["creationPlan"]}

        def insert_task(self, task):
            return None

    monkeypatch.setattr(tasks_route, "TaskRepository", FakeTaskRepo)

    captured: dict = {}

    def fake_build(payload):
        # postgres clone builds via build_task_payload + insert_task (create_task
        # is fenced off in postgres mode), so mock the builder that captures the
        # seeded payload.
        captured["payload"] = payload
        return {"taskId": "new1", **payload, "ownerId": "owner1",
                "workspaceId": "ws1", "createdBy": "owner1",
                "status": payload.get("status", "not_started"),
                "artifacts": []}

    monkeypatch.setattr(tasks_route.store, "build_task_payload", fake_build)
    monkeypatch.setattr(
        tasks_route.CollaborationRepository,
        "initialize_task_execution_policy",
        lambda self, **kw: captured.setdefault("policy", kw),
    )

    class FakeArtRepo:
        def list_by_scope(self, *a, **k):
            return (list(_ARTIFACTS), len(_ARTIFACTS))

    monkeypatch.setattr(tasks_route, "ArtifactRepository", FakeArtRepo)

    def fake_reuse(src_art, src_task, target, *, identity=None):
        reused.append((src_art["id"], target))
        return {"id": "copy_" + src_art["id"], "ownerId": "owner1",
                "workspaceId": "ws1", "storageType": "object",
                "contentSha256": "z" * 64, "createdAt": "now"}

    monkeypatch.setattr(tasks_route, "reuse_artifact", fake_reuse)
    monkeypatch.setattr(tasks_route, "project_public_task", lambda t: t)
    return captured


def test_clone_task_seeds_from_source_spec_and_reuses_all_artifacts(monkeypatch):
    reused: list = []
    captured = _wire_clone_mocks(monkeypatch, reused)

    result = tasks_route.clone_task(
        "src1",
        CloneTaskIn(title="Cloned", includeArtifacts=True),
        identity={"ownerId": "owner1", "workspaceId": "ws1", "createdBy": "owner1"},
    )

    # new task seeded from source spec, fresh execution state
    assert captured["payload"]["creationPlan"] == _SOURCE["creationPlan"]
    assert captured["payload"]["skillPack"] == "sdr"
    assert captured["payload"]["goalSummary"] == "study X"
    assert captured["payload"]["status"] == "not_started"
    assert captured["payload"]["title"] == "Cloned"
    assert captured["policy"]["task_id"] == "new1"

    # every source artifact reused into the new task (content-addressed)
    assert len(reused) == 2
    assert {r[0] for r in reused} == {"a1", "a2"}
    assert all(r[1] == "new1" for r in reused)


def test_clone_task_template_skips_artifacts(monkeypatch):
    reused: list = []
    _wire_clone_mocks(monkeypatch, reused)

    tasks_route.clone_task(
        "src1",
        CloneTaskIn(title="Template only", includeArtifacts=False),
        identity={"ownerId": "owner1", "workspaceId": "ws1", "createdBy": "owner1"},
    )

    assert reused == []  # template carries no artifacts


def test_clone_task_default_title_includes_copy_marker(monkeypatch):
    reused: list = []
    captured = _wire_clone_mocks(monkeypatch, reused)

    tasks_route.clone_task(
        "src1",
        CloneTaskIn(),  # no title -> default "Original（副本）"
        identity={"ownerId": "owner1", "workspaceId": "ws1", "createdBy": "owner1"},
    )

    assert captured["payload"]["title"].startswith("Original")
    assert "副本" in captured["payload"]["title"]


def test_fork_v12_contract_requires_new_design_confirmation():
    """A cloned Project reuses Design content but never inherits user approval."""
    fixture = (
        Path(__file__).resolve().parents[3]
        / "packages"
        / "shared"
        / "test-fixtures"
        / "research-orchestration-v1.2-valid.json"
    )
    source = deepcopy(json.loads(fixture.read_text(encoding="utf-8"))["researchTask"])
    source["taskId"] = "src1"
    target = {"taskId": "new1", "status": "completed"}

    assert _fork_v12_contract_into(source, target) is True

    # Design content and planning provenance are reusable, but the new Project
    # must own a fresh confirmation before execution can start.
    cloned_design = target["researchDesignVersions"][0]
    assert cloned_design["taskId"] == "new1"
    assert cloned_design["version"] == 1
    assert cloned_design["supersedesVersion"] is None
    assert cloned_design["status"] == "draft"
    assert cloned_design["confirmedAt"] is None
    assert cloned_design["confirmationRef"] is None
    assert target["planningAttempts"][0]["taskId"] == "new1"
    assert target["registrySnapshotRef"] == source["registrySnapshotRef"]
    assert target["progressProjection"] is not None
    assert target["confirmations"] == []
    assert target["activeResearchDesignRef"] is None
    assert target["substageRuntimeStates"] == []
    assert target["creationStatus"] == "awaiting_confirmation"
    assert target["status"] == "awaiting_confirmation"

    # Execution history remains empty until the copied draft is confirmed.
    for key in (
        "executionDecisions",
        "workUnits",
        "capabilityCalls",
        "agentInteractionRecords",
        "artifacts",
    ):
        assert target[key] == []
    assert target["revision"] == 0


def test_fork_v12_contract_is_noop_for_legacy_source():
    """A pre-v1.2 source (no researchDesignVersions) keeps the legacy clone path."""
    target = {"taskId": "new1"}
    assert _fork_v12_contract_into({"taskId": "src1"}, target) is False
    assert "researchDesignVersions" not in target
