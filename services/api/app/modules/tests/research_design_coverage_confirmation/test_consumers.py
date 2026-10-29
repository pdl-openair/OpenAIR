"""These production consumers must agree on the approved confirmation rule."""

import json

import pytest
from app.models.research_orchestration import ResearchDesignVersion, ResearchTaskV12
from app.modules.research_design_service import new_research_task
from app.modules.research_orchestration_repository import _research_design_compatibility_projection
from app.modules.research_start_repository import latest_usable_research_design_for_start_completion
from app.modules.task_public_projection import _active_research_design_projection

from .test_confirmation import ROOT, gap_design


def task_with_gap():
    payload = gap_design()
    payload.update(status="draft", confirmedAt=None, confirmationRef=None)
    design = ResearchDesignVersion.model_validate(payload)
    template = json.loads(
        (ROOT / "packages/shared/test-fixtures/research-orchestration-v1.2-valid.json").read_text(
            encoding="utf-8"
        )
    )["researchTask"]
    task = new_research_task(
        task_id=design.task_id, registry_snapshot_ref=template["registrySnapshotRef"]
    )
    stored = task.model_dump(mode="json", by_alias=True)
    stored.update(
        status="awaiting_confirmation",
        planningAttempts=template["planningAttempts"],
        researchDesignVersions=[design.model_dump(mode="json", by_alias=True)],
    )
    return ResearchTaskV12.model_validate(stored)


def test_research_start_receipt_accepts_executable_target_gaps():
    task = task_with_gap()
    assert (
        latest_usable_research_design_for_start_completion(task.research_design_versions)
        is not None
    )


@pytest.mark.parametrize("consumer", ["public", "compatibility"])
def test_workbench_projects_gap_design_as_confirmable(consumer):
    task = task_with_gap()
    if consumer == "public":
        projection = _active_research_design_projection(task, public_artifact_ids=[])
    else:
        projection = _research_design_compatibility_projection(task)
    assert projection["creationStatus"] == "awaiting_confirmation"
    assert projection["creationPlan"]["readyToConfirm"] is True


@pytest.mark.parametrize("status", ["cancelled", "superseded"])
def test_latest_unusable_design_never_falls_back_to_older_usable_design(status):
    older = task_with_gap().research_design_versions[0]
    payload = older.model_dump(mode="json", by_alias=True)
    payload.update(version=2, supersedesVersion=1, status=status)
    payload["coverageReport"]["designRef"]["version"] = 2
    latest = ResearchDesignVersion.model_validate(payload)
    assert latest_usable_research_design_for_start_completion([older, latest]) is None


@pytest.mark.parametrize("consumer", ["receipt", "public", "compatibility"])
def test_necessary_boundary_gap_remains_unconfirmable(consumer):
    task = task_with_gap()
    stored = task.model_dump(mode="json", by_alias=True)
    report = stored["researchDesignVersions"][0]["coverageReport"]
    report["status"] = "needs_revision"
    report["targets"][-1]["sourceType"] = "constraint"
    task = ResearchTaskV12.model_validate(stored)
    if consumer == "receipt":
        older = task_with_gap().research_design_versions[0]
        payload = task.research_design_versions[0].model_dump(mode="json", by_alias=True)
        payload.update(version=2, supersedesVersion=1)
        payload["coverageReport"]["designRef"]["version"] = 2
        latest = ResearchDesignVersion.model_validate(payload)
        assert latest_usable_research_design_for_start_completion(
            [older, latest]
        ) is None
    else:
        projection = (
            _active_research_design_projection(task, public_artifact_ids=[])
            if consumer == "public" else _research_design_compatibility_projection(task)
        )
        assert projection["creationStatus"] == "revision_requested"
        assert projection["creationPlan"]["readyToConfirm"] is False
