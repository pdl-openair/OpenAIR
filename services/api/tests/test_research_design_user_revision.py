from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from threading import Lock
from typing import Any

import pytest

from app.models.research_orchestration import PlanningAttemptStatus, ResearchTaskV12
from app.models.research_planner_semantic import PlannerSemanticProposalV2
from app.modules.research_design_compiler import compile_research_design_proposal
from app.modules.research_design_service import (
    PlannerProviderResult,
    ResearchDesignConfirmationError,
    ResearchDesignService,
    ResolvedDefinition,
    new_research_task,
)
from app.modules.research_orchestration_repository import TaskRevisionConflictError


SNAPSHOT = {"schemaVersion": 1, "contentHash": "sha256:" + "c" * 64}
CATALOG = [
    {
        "definitionId": "sdr:source_collection",
        "definitionVersion": 1,
        "stage": "problem_modeling",
        "label": "Source collection",
        "description": "Collect traceable research sources.",
        "instancePolicy": {"mode": "repeatable"},
        "requiredInputRefs": ["user:request"],
        "artifactTypes": ["source_set"],
        "criterionRefs": ["source_validator"],
    },
    {
        "definitionId": "sdr:variable_definition",
        "definitionVersion": 1,
        "stage": "solution_design",
        "label": "Variable definition",
        "description": "Define variables and comparison dimensions.",
        "instancePolicy": {"mode": "repeatable"},
        "requiredInputRefs": ["user:request"],
        "artifactTypes": ["variable_spec"],
        "criterionRefs": ["variable_validator"],
    },
    {
        "definitionId": "sdr:ablation_design",
        "definitionVersion": 1,
        "stage": "solution_design",
        "label": "Ablation design",
        "description": "Design an ablation experiment.",
        "instancePolicy": {"mode": "repeatable"},
        "requiredInputRefs": ["user:request"],
        "artifactTypes": ["ablation_plan"],
        "criterionRefs": ["ablation_validator"],
    },
    {
        "definitionId": "sdr:execution_check",
        "definitionVersion": 1,
        "stage": "execution_validation",
        "label": "Execution validation",
        "description": "Validate the planned experiment or evaluation.",
        "instancePolicy": {"mode": "repeatable"},
        "requiredInputRefs": ["user:request"],
        "artifactTypes": ["validation_report"],
        "criterionRefs": ["validation_validator"],
    },
    {
        "definitionId": "sdr:result_summary",
        "definitionVersion": 1,
        "stage": "insight_generation",
        "label": "Result summary",
        "description": "Synthesize research findings.",
        "instancePolicy": {"mode": "repeatable"},
        "requiredInputRefs": ["user:request"],
        "artifactTypes": ["result_summary"],
        "criterionRefs": ["summary_validator"],
    },
]
CONTRACTS = {row["definitionId"]: row for row in CATALOG}


class FakeDefinitionSource:
    def snapshot_ref(self) -> dict[str, Any]:
        return deepcopy(SNAPSHOT)

    def resolve(self, definition_id: str) -> ResolvedDefinition | None:
        row = CONTRACTS.get(definition_id)
        if row is None:
            return None
        return ResolvedDefinition(
            definition_id=definition_id,
            definition_version=row["definitionVersion"],
            stage_key=row["stage"],
            required_input_refs=tuple(row["requiredInputRefs"]),
            artifact_types=tuple(row["artifactTypes"]),
            criterion_refs=tuple(row["criterionRefs"]),
        )

    def definition_is_eligible_for_workflow_admission(self, definition_id, workflow_admission):
        return definition_id in CONTRACTS


class FakeRepository:
    def __init__(self, task: ResearchTaskV12) -> None:
        self.task = task
        self.lock = Lock()

    def load(self, *, task_id: str, owner_id: str, workspace_id: str) -> ResearchTaskV12:
        assert task_id == self.task.task_id
        return self.task.model_copy(deep=True)

    def load_planning_workflow_admission(
        self, *, task_id: str, owner_id: str, workspace_id: str
    ):
        assert task_id == self.task.task_id
        return None

    def save(
        self,
        task: ResearchTaskV12,
        *,
        owner_id: str,
        workspace_id: str,
        expected_task_revision: int,
        **_kwargs: object,
    ) -> ResearchTaskV12:
        with self.lock:
            if self.task.revision != expected_task_revision or task.revision != expected_task_revision:
                raise TaskRevisionConflictError("task_revision_conflict")
            payload = task.model_dump(mode="json", by_alias=True)
            payload["revision"] = expected_task_revision + 1
            self.task = ResearchTaskV12.model_validate(payload)
            return self.task.model_copy(deep=True)


def _clock() -> datetime:
    return datetime(2026, 8, 10, 10, 0, 0, tzinfo=timezone.utc)


def _service() -> tuple[ResearchDesignService, FakeRepository]:
    repository = FakeRepository(new_research_task(task_id="task_u4", registry_snapshot_ref=SNAPSHOT))
    service = ResearchDesignService(
        repository=repository,
        definition_source=FakeDefinitionSource(),
        clock=_clock,
    )
    return service, repository


def _semantic(
    steps: list[dict[str, Any]],
    *,
    goal: str = "Compare GraphRAG methods",
    target_output: str = "comparison_report",
) -> PlannerSemanticProposalV2:
    work_items = []
    for index, step in enumerate(steps):
        definition = CONTRACTS[step["definitionId"]]
        work_items.append(
            {
                "localKey": step["localKey"],
                "objective": step["objective"],
                "definitionId": step["definitionId"],
                "inputRefs": ["user:request"],
                "expectedArtifactTypes": [definition["artifactTypes"][0]],
                "targetOutputRefs": [target_output] if index == len(steps) - 1 else [],
                "dependsOnLocalKeys": list(step.get("dependsOnLocalKeys") or []),
            }
        )
    return PlannerSemanticProposalV2.model_validate(
        {
            "schemaVersion": "planner-semantic.v2",
            "goal": goal,
            "understanding": {
                "userIntent": goal,
                "materialSummary": "No supplied materials",
                "primaryResearchQuestions": [goal],
                "auxiliaryResearchDimensions": [],
                "targetOutputs": [target_output],
                "evidenceBoundaries": ["Use traceable sources"],
                "missingInputs": [],
                "riskNotes": [],
            },
            "constraints": ["Use evidence from the requested scope"],
            "requestedExternalActions": [],
            "workItems": work_items,
        }
    )


def _compiled(steps: list[dict[str, Any]], *, goal: str = "Compare GraphRAG methods") -> dict[str, Any]:
    proposal = compile_research_design_proposal(
        _semantic(steps, goal=goal),
        definition_catalog=CATALOG,
        raw_input=goal,
    )
    return proposal.model_dump(mode="json", by_alias=True)


def _plan(service: ResearchDesignService, repository: FakeRepository, steps: list[dict[str, Any]], request_id: str):
    proposal = _compiled(steps)
    return service.plan(
        task_id="task_u4",
        owner_id="owner",
        workspace_id="workspace",
        request_id=request_id,
        expected_task_revision=repository.task.revision,
        provider=lambda _key: PlannerProviderResult(
            proposal=deepcopy(proposal),
            provider_call_ref=f"provider:{request_id}",
        ),
    )


def _revise(
    service: ResearchDesignService,
    repository: FakeRepository,
    steps: list[dict[str, Any]],
    request_id: str,
    *,
    goal: str = "Compare GraphRAG methods",
):
    proposal = _compiled(steps, goal=goal)
    return service.revise(
        task_id="task_u4",
        owner_id="owner",
        workspace_id="workspace",
        request_id=request_id,
        expected_task_revision=repository.task.revision,
        provider=lambda _key: PlannerProviderResult(
            proposal=deepcopy(proposal),
            provider_call_ref=f"provider:{request_id}",
        ),
    )


def test_real_compiler_add_recompiles_structural_limit_and_supports_empty_stage() -> None:
    service, repository = _service()
    initial_steps = [
        {"localKey": "scan", "objective": "Collect GraphRAG papers", "definitionId": "sdr:source_collection"},
        {
            "localKey": "variables",
            "objective": "Define comparison variables",
            "definitionId": "sdr:variable_definition",
            "dependsOnLocalKeys": ["scan"],
        },
    ]
    initial = _plan(service, repository, initial_steps, "initial")
    first = initial.research_design_versions[-1]
    assert first.scope_boundary.max_substage_instances == len(first.instances) == 2
    assert all(row.stage_key.value != "execution_validation" for row in first.instances)

    revised_steps = [
        *initial_steps,
        {
            "localKey": "validate",
            "objective": "Validate the comparison experiment",
            "definitionId": "sdr:execution_check",
            "dependsOnLocalKeys": ["variables"],
        },
    ]
    revised = _revise(service, repository, revised_steps, "add-empty-stage")
    latest = revised.research_design_versions[-1]

    assert latest.version == 2
    assert latest.scope_boundary.max_substage_instances == len(latest.instances) == 3
    assert any(row.stage_key.value == "execution_validation" for row in latest.instances)
    assert latest.coverage_report is not None
    assert latest.coverage_report.design_ref.version == 2


def test_semantic_add_and_edit_take_definition_owned_contracts_from_recompiled_semantics() -> None:
    service, repository = _service()
    _plan(
        service,
        repository,
        [
            {"localKey": "scan", "objective": "Collect GraphRAG papers", "definitionId": "sdr:source_collection"},
            {
                "localKey": "variables",
                "objective": "Define comparison variables",
                "definitionId": "sdr:variable_definition",
                "dependsOnLocalKeys": ["scan"],
            },
        ],
        "initial",
    )

    added = _revise(
        service,
        repository,
        [
            {"localKey": "scan", "objective": "Collect GraphRAG papers", "definitionId": "sdr:source_collection"},
            {
                "localKey": "variables",
                "objective": "Define comparison variables",
                "definitionId": "sdr:variable_definition",
                "dependsOnLocalKeys": ["scan"],
            },
            {
                "localKey": "ablation",
                "objective": "Design an ablation experiment",
                "definitionId": "sdr:ablation_design",
                "dependsOnLocalKeys": ["variables"],
            },
        ],
        "semantic-add",
    )
    added_design = added.research_design_versions[-1]
    ablation = next(row for row in added_design.instances if row.objective == "Design an ablation experiment")
    assert ablation.stage_key.value == "solution_design"
    assert ablation.substage_definition_id == "sdr:ablation_design"
    assert ablation.expected_artifact_types == ["ablation_plan"]
    assert {criterion.evaluator_ref for criterion in ablation.acceptance_criteria if criterion.evaluator_ref} == {
        "ablation_validator"
    }

    prior_scan = next(row for row in added_design.instances if row.objective == "Collect GraphRAG papers")
    prior_variables = next(row for row in added_design.instances if row.objective == "Define comparison variables")
    edited = _revise(
        service,
        repository,
        [
            {
                "localKey": f"existing_instance__{prior_scan.substage_instance_id}",
                "objective": "Collect GraphRAG papers",
                "definitionId": "sdr:source_collection",
            },
            {
                "localKey": f"existing_instance__{prior_variables.substage_instance_id}",
                "objective": "Design an ablation experiment for retrieval components",
                "definitionId": "sdr:ablation_design",
                "dependsOnLocalKeys": [f"existing_instance__{prior_scan.substage_instance_id}"],
            },
        ],
        "semantic-edit",
    )
    edited_design = edited.research_design_versions[-1]
    changed = next(row for row in edited_design.instances if row.objective.startswith("Design an ablation experiment"))
    assert changed.substage_instance_id == prior_variables.substage_instance_id
    assert changed.instance_revision == prior_variables.instance_revision + 1
    assert changed.substage_definition_id == "sdr:ablation_design"
    assert changed.expected_artifact_types == ["ablation_plan"]
    assert "variable_spec" not in changed.expected_artifact_types
    assert {criterion.evaluator_ref for criterion in changed.acceptance_criteria if criterion.evaluator_ref} == {
        "ablation_validator"
    }
    assert edited_design.coverage_report is not None
    assert edited_design.coverage_report.design_ref.version == edited_design.version
    assert edited_design.coverage_report.coverage_report_id != added_design.coverage_report.coverage_report_id


def test_delete_real_planner_node_rebuilds_dependencies_and_coverage_without_dangling_refs() -> None:
    service, repository = _service()
    _plan(
        service,
        repository,
        [
            {"localKey": "scan", "objective": "Collect GraphRAG papers", "definitionId": "sdr:source_collection"},
            {
                "localKey": "variables",
                "objective": "Define comparison variables",
                "definitionId": "sdr:variable_definition",
                "dependsOnLocalKeys": ["scan"],
            },
            {
                "localKey": "validate",
                "objective": "Validate the comparison experiment",
                "definitionId": "sdr:execution_check",
                "dependsOnLocalKeys": ["variables"],
            },
        ],
        "initial",
    )

    revised = _revise(
        service,
        repository,
        [
            {"localKey": "scan", "objective": "Collect GraphRAG papers", "definitionId": "sdr:source_collection"},
            {
                "localKey": "validate",
                "objective": "Validate the comparison experiment directly from collected evidence",
                "definitionId": "sdr:execution_check",
                "dependsOnLocalKeys": ["scan"],
            },
        ],
        "delete-middle-node",
    )
    latest = revised.research_design_versions[-1]
    assert len(latest.instances) == 2
    assert not any(row.substage_definition_id == "sdr:variable_definition" for row in latest.instances)
    known_refs = {(row.substage_instance_id, row.instance_revision) for row in latest.instances}
    assert all(
        (dependency.upstream_instance_ref.substage_instance_id, dependency.upstream_instance_ref.instance_revision)
        in known_refs
        for row in latest.instances
        for dependency in row.dependencies
    )
    assert latest.coverage_report is not None
    assert latest.coverage_report.design_ref.version == latest.version
    assert latest.coverage_report.status.value.startswith("passed_")


def test_revision_is_durable_server_fact_and_cannot_create_execution_before_confirm() -> None:
    service, repository = _service()
    _plan(
        service,
        repository,
        [{"localKey": "scan", "objective": "Collect GraphRAG papers", "definitionId": "sdr:source_collection"}],
        "initial",
    )
    revised = _revise(
        service,
        repository,
        [
            {"localKey": "scan", "objective": "Collect 2024-2026 GraphRAG papers", "definitionId": "sdr:source_collection"},
            {
                "localKey": "summary",
                "objective": "Synthesize the comparison findings",
                "definitionId": "sdr:result_summary",
                "dependsOnLocalKeys": ["scan"],
            },
        ],
        "durable",
        goal="Compare 2024-2026 GraphRAG methods",
    )

    reloaded = repository.load(task_id="task_u4", owner_id="owner", workspace_id="workspace")
    assert reloaded.model_dump(mode="json", by_alias=True) == revised.model_dump(mode="json", by_alias=True)
    assert reloaded.research_design_versions[-1].goal == "Compare 2024-2026 GraphRAG methods"
    assert reloaded.active_research_design_ref is None
    assert reloaded.execution_decisions == []
    assert reloaded.work_units == []
    assert reloaded.capability_calls == []
    assert reloaded.substage_runtime_states == []
    assert reloaded.artifacts == []


def test_only_latest_successor_can_be_confirmed_and_it_becomes_active() -> None:
    service, repository = _service()
    first_task = _plan(
        service,
        repository,
        [{"localKey": "scan", "objective": "Collect GraphRAG papers", "definitionId": "sdr:source_collection"}],
        "initial",
    )
    first = first_task.research_design_versions[-1]
    revised = _revise(
        service,
        repository,
        [
            {"localKey": "scan", "objective": "Collect 2024-2026 GraphRAG papers", "definitionId": "sdr:source_collection"},
            {
                "localKey": "summary",
                "objective": "Synthesize the comparison findings",
                "definitionId": "sdr:result_summary",
                "dependsOnLocalKeys": ["scan"],
            },
        ],
        "revision",
    )
    latest = revised.research_design_versions[-1]

    with pytest.raises(ResearchDesignConfirmationError):
        service.confirm(
            task_id="task_u4",
            owner_id="owner",
            workspace_id="workspace",
            actor_id="researcher",
            design_id=first.research_design_id,
            version=first.version,
            confirmation_id="confirm-old",
            decision="approved",
            expected_task_revision=repository.task.revision,
        )

    confirmed = service.confirm(
        task_id="task_u4",
        owner_id="owner",
        workspace_id="workspace",
        actor_id="researcher",
        design_id=latest.research_design_id,
        version=latest.version,
        confirmation_id="confirm-latest",
        decision="approved",
        expected_task_revision=repository.task.revision,
    )
    assert confirmed.active_research_design_ref is not None
    assert confirmed.active_research_design_ref.research_design_id == latest.research_design_id
    assert confirmed.active_research_design_ref.version == latest.version

def test_failed_revision_attempt_advances_revision_and_retry_preserves_user_semantics_without_execution() -> None:
    service, repository = _service()
    _plan(
        service,
        repository,
        [{"localKey": "scan", "objective": "Collect GraphRAG papers", "definitionId": "sdr:source_collection"}],
        "initial",
    )
    user_steps = [
        {
            "localKey": "scan",
            "objective": "Collect 2024-2026 GraphRAG papers",
            "definitionId": "sdr:source_collection",
        },
        {
            "localKey": "summary",
            "objective": "Synthesize the comparison findings",
            "definitionId": "sdr:result_summary",
            "dependsOnLocalKeys": ["scan"],
        },
    ]
    user_goal = "Compare 2024-2026 GraphRAG methods"
    failed_from_revision = repository.task.revision

    with pytest.raises(RuntimeError, match="temporary planning failure"):
        service.revise(
            task_id="task_u4",
            owner_id="owner",
            workspace_id="workspace",
            request_id="revision-failed-save",
            expected_task_revision=failed_from_revision,
            provider=lambda _key: (_ for _ in ()).throw(RuntimeError("temporary planning failure")),
        )

    after_failure = repository.load(task_id="task_u4", owner_id="owner", workspace_id="workspace")
    assert after_failure.revision == failed_from_revision + 2
    assert after_failure.planning_attempts[-1].status == PlanningAttemptStatus.FAILED
    assert after_failure.active_research_design_ref is None
    assert after_failure.execution_decisions == []
    assert after_failure.work_units == []
    assert after_failure.capability_calls == []
    assert after_failure.substage_runtime_states == []
    assert after_failure.artifacts == []

    retry_proposal = _compiled(user_steps, goal=user_goal)
    retried = service.revise(
        task_id="task_u4",
        owner_id="owner",
        workspace_id="workspace",
        request_id="revision-retry-save",
        expected_task_revision=after_failure.revision,
        provider=lambda _key: PlannerProviderResult(
            proposal=deepcopy(retry_proposal),
            provider_call_ref="provider:revision-retry-save",
        ),
    )

    latest = retried.research_design_versions[-1]
    assert latest.goal == user_goal
    assert [row.objective for row in latest.instances] == [
        "Collect 2024-2026 GraphRAG papers",
        "Synthesize the comparison findings",
    ]
    assert retried.active_research_design_ref is None
    assert retried.execution_decisions == []
    assert retried.work_units == []
    assert retried.capability_calls == []
    assert retried.substage_runtime_states == []
    assert retried.artifacts == []
