from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

import pytest


REGISTRY_REF = {"schemaVersion": 1, "contentHash": "sha256:" + "b" * 64}


def _frozen_workflow():
    from app.models.workflow_definition import WorkflowDefinitionV1, freeze_workflow_definition

    definition = WorkflowDefinitionV1.model_validate({
        "schemaVersion": "workflow-definition.v1",
        "workflowId": "test_workflow",
        "workflowVersion": 1,
        "nameZh": "测试流程",
        "nameEn": "Test Workflow",
        "description": "A frozen research skeleton used by the Planner.",
        "skillPack": "test_pack",
        "nodes": [
            {
                "nodeKey": "findings",
                "definitionId": "test:findings_synthesis",
                "definitionVersion": 1,
                "requirement": "required",
                "displayOrder": 10,
            }
        ],
        "dependencies": [],
        "boundaryPolicy": {
            "allowedExternalActions": [],
            "confirmationRequiredActions": [],
            "prohibitedActions": [],
        },
    })
    return freeze_workflow_definition(definition, registry_snapshot_ref=REGISTRY_REF)


def _admission() -> dict:
    frozen = _frozen_workflow()
    return {
        "workflowId": frozen.workflow_id,
        "workflowVersion": frozen.workflow_version,
        "workflowContentHash": frozen.content_hash,
        "workflowDefinitionSnapshot": frozen.model_dump(mode="json", by_alias=True),
        "skillPack": frozen.definition.skill_pack,
        "decisionReason": "test",
        "routeType": "workflow_planning",
    }


class _Cursor:
    def __init__(self, payload: dict):
        self.payload = payload

    def execute(self, *_args, **_kwargs):
        return None

    def fetchone(self):
        return {"payload": deepcopy(self.payload)}


class _Connection:
    def __init__(self, payload: dict):
        self.payload = payload

    def cursor(self):
        cursor = _Cursor(self.payload)

        class _Context:
            def __enter__(self):
                return cursor

            def __exit__(self, *_args):
                return False

        return _Context()


class _Transaction:
    def __init__(self, payload: dict):
        self.payload = payload

    def __enter__(self):
        return _Connection(self.payload)

    def __exit__(self, *_args):
        return False


class _Uow:
    def __init__(self, payload: dict):
        self.payload = payload

    def transaction(self):
        return _Transaction(self.payload)


def test_planning_workflow_admission_preserves_complete_frozen_identity() -> None:
    from app.modules.research_orchestration_repository import ResearchOrchestrationRepository

    admission = _admission()
    payload = {
        "skillPack": admission["skillPack"],
        "registrySnapshotRef": deepcopy(REGISTRY_REF),
        "workflowAdmission": deepcopy(admission),
    }
    repository = object.__new__(ResearchOrchestrationRepository)
    repository._uow = _Uow(payload)
    repository._require_postgres = lambda: None

    loaded = repository.load_planning_workflow_admission(
        task_id="task",
        owner_id="owner",
        workspace_id="workspace",
    )

    assert loaded == admission
    assert loaded["workflowVersion"] == 1
    assert loaded["workflowContentHash"] == admission["workflowContentHash"]
    assert loaded["workflowDefinitionSnapshot"] == admission["workflowDefinitionSnapshot"]


def test_planning_workflow_admission_rejects_truncated_identity() -> None:
    from app.modules.research_orchestration_repository import (
        ResearchOrchestrationPersistenceError,
        ResearchOrchestrationRepository,
    )

    admission = _admission()
    admission.pop("workflowContentHash")
    payload = {
        "skillPack": admission["skillPack"],
        "registrySnapshotRef": deepcopy(REGISTRY_REF),
        "workflowAdmission": admission,
    }
    repository = object.__new__(ResearchOrchestrationRepository)
    repository._uow = _Uow(payload)
    repository._require_postgres = lambda: None

    with pytest.raises(ResearchOrchestrationPersistenceError, match="workflow_admission_lineage_mismatch"):
        repository.load_planning_workflow_admission(
            task_id="task",
            owner_id="owner",
            workspace_id="workspace",
        )


def test_confirmed_workflow_design_keeps_provenance_without_inferred_generic_final_definition() -> None:
    from app.models.research_orchestration import ResearchTaskV12
    from app.modules.canonical_execution_lineage import validate_canonical_execution_lineage
    from app.modules.research_design_compiler import compile_research_design_proposal
    from app.modules.research_design_planner_assembly import build_deterministic_workflow_semantics
    from app.modules.research_design_workflow_semantics import (
        WorkflowProjectSemanticBriefV2,
        apply_workflow_semantic_brief,
    )
    from app.modules.research_design_service import (
        PlannerProviderResult,
        ResearchDesignService,
        ResolvedDefinition,
        new_research_task,
    )

    admission = _admission()
    catalog = [
        {
            "definitionId": "test:findings_synthesis",
            "definitionVersion": 1,
            "stage": "insight_generation",
            "label": "Findings",
            "description": "Synthesize findings for the current research goal.",
            "instancePolicy": {"mode": "repeatable"},
            "requiredInputRefs": ["research_context"],
            "artifactTypes": ["findings"],
            "criterionRefs": ["crit:findings"],
            "contractComplete": True,
        },
        {
            "definitionId": "sdr:report_generation",
            "definitionVersion": 1,
            "stage": "insight_generation",
            "label": "Report",
            "description": "Produce the final user-facing research result.",
            "instancePolicy": {"mode": "repeatable"},
            "requiredInputRefs": ["findings"],
            "artifactTypes": ["research_report"],
            "criterionRefs": ["crit:report"],
            "contractComplete": True,
        },
    ]
    workflow_context = {
        "schemaVersion": "workflow-planning-context.v1",
        "workflowId": admission["workflowId"],
        "workflowVersion": admission["workflowVersion"],
        "workflowContentHash": admission["workflowContentHash"],
        "requiredNodes": [
            {
                "nodeKey": "findings",
                "definitionId": "test:findings_synthesis",
                "definitionVersion": 1,
                "displayOrder": 10,
            }
        ],
        "optionalNodes": [],
        "dependencies": [],
        "boundaryPolicy": {
            "allowedExternalActions": [],
            "confirmationRequiredActions": [],
            "prohibitedActions": [],
        },
    }
    semantic = build_deterministic_workflow_semantics(
        raw_input="Form a complete research result.",
        definition_catalog=catalog,
        workflow_planning_context=workflow_context,
    )
    semantic = apply_workflow_semantic_brief(
        semantic,
        WorkflowProjectSemanticBriefV2.model_validate({
            "researchGoal": "Form a complete evidence-grounded research result.",
            "primaryResearchQuestions": ["What conclusion is supported by the current research evidence?"],
            "auxiliaryResearchDimensions": [],
            "targetOutputs": ["A complete evidence-grounded research result"],
            "constraints": [],
            "scientificUnknowns": ["Which findings remain uncertain?"],
            "risksAndTradeoffs": ["Evidence limitations may constrain the final conclusion."],
            "workItemScientificContexts": [
                "Synthesize the evidence that directly answers the main research question.",
            ],
        }),
    )
    proposal = compile_research_design_proposal(
        semantic,
        definition_catalog=catalog,
        raw_input="Form a complete research result.",
        workflow_planning_context=workflow_context,
    ).model_dump(mode="json", by_alias=True)

    class _DefinitionSource:
        def snapshot_ref(self):
            return deepcopy(REGISTRY_REF)

        def resolve(self, definition_id: str):
            row = next((row for row in catalog if row["definitionId"] == definition_id), None)
            if row is None:
                return None
            return ResolvedDefinition(
                definition_id=row["definitionId"],
                definition_version=row["definitionVersion"],
                stage_key=row["stage"],
                required_input_refs=tuple(row["requiredInputRefs"]),
                artifact_types=tuple(row["artifactTypes"]),
                criterion_refs=tuple(row["criterionRefs"]),
            )

        def definition_is_eligible_for_workflow_admission(self, definition_id, workflow_admission):
            return definition_id in {"test:findings_synthesis", "sdr:report_generation"}

    class _Repository:
        def __init__(self):
            self.task = new_research_task(task_id="task", registry_snapshot_ref=REGISTRY_REF)

        def load(self, **_kwargs):
            return self.task.model_copy(deep=True)

        def load_planning_workflow_admission(self, **_kwargs):
            return deepcopy(admission)

        def save(self, task, *, expected_task_revision: int, **_kwargs):
            assert self.task.revision == expected_task_revision
            payload = task.model_dump(mode="json", by_alias=True)
            payload["revision"] = expected_task_revision + 1
            self.task = ResearchTaskV12.model_validate(payload)
            return self.task.model_copy(deep=True)

    repository = _Repository()
    service = ResearchDesignService(
        repository=repository,
        definition_source=_DefinitionSource(),
        clock=lambda: datetime(2026, 9, 10, tzinfo=timezone.utc),
    )
    planned = service.plan(
        task_id="task",
        owner_id="owner",
        workspace_id="workspace",
        request_id="request",
        expected_task_revision=repository.task.revision,
        provider=lambda _key: PlannerProviderResult(
            proposal=proposal,
            provider_call_ref="planner:test",
        ),
    )
    design = planned.research_design_versions[-1]
    assert design.workflow_provenance_ref is not None
    assert design.workflow_provenance_ref.workflow_id == admission["workflowId"]
    assert design.workflow_provenance_ref.workflow_version == admission["workflowVersion"]
    assert design.workflow_provenance_ref.content_hash == "sha256:" + admission["workflowContentHash"]
    assert {row.substage_definition_id for row in design.instances} == {
        "test:findings_synthesis",
    }
    assert all(row.substage_definition_id != "sdr:report_generation" for row in design.instances)

    confirmed = service.confirm(
        task_id="task",
        owner_id="owner",
        workspace_id="workspace",
        actor_id="user",
        design_id=design.research_design_id,
        version=design.version,
        confirmation_id="confirmation",
        decision="approved",
        expected_task_revision=planned.revision,
    )
    lineage = validate_canonical_execution_lineage(
        task=confirmed,
        workflow_admission=admission,
        persisted_skill_pack=admission["skillPack"],
    )
    assert lineage.workflow_id == admission["workflowId"]
    assert lineage.workflow_version == admission["workflowVersion"]
    assert lineage.workflow_content_hash == admission["workflowContentHash"]
