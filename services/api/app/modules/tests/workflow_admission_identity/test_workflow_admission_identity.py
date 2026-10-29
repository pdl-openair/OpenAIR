from __future__ import annotations

from types import SimpleNamespace

import pytest
from app.modules.workflow_planning_admission import WorkflowPlanningAdmission


def _selection(workflow_id: str) -> WorkflowPlanningAdmission:
    return WorkflowPlanningAdmission(
        workflow_id=workflow_id,
        reason="direct_workflow_test",
        user_facing_summary="ready",
        suggested_title=None,
    )


def test_workflow_admission_preserves_frozen_content_hash_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.modules.workflow_planning as planning

    canonical_hash = "sha256:" + "a" * 64

    class Frozen:
        content_hash = canonical_hash
        definition = SimpleNamespace(
            workflow_id="opentrajectory_research_workflow",
            workflow_version=1,
            skill_pack="opentrajectory",
        )

        def model_dump(self, *, mode="json", by_alias=True):
            assert mode == "json"
            assert by_alias is True
            return {"opaque": "snapshot"}

    class WorkflowSource:
        def __init__(self, *, definition_authority):
            self.definition_authority = definition_authority

        def resolve(self, requested):
            return object() if requested == "opentrajectory_research_workflow" else None

        def freeze(self, requested):
            assert requested == "opentrajectory_research_workflow"
            return Frozen()

    monkeypatch.setattr(planning, "ProductionWorkflowSource", WorkflowSource)
    result = planning.workflow_admission_for_selection(
        selection=_selection("opentrajectory_research_workflow"),
        definition_source=object(),
    )

    assert result is not None
    assert result["workflowContentHash"] == canonical_hash
