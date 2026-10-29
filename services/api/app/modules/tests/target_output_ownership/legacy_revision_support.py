"""Actual historical producers and an isolated CAS repository for revision tests."""

from __future__ import annotations

import subprocess
import sys
import types
from functools import lru_cache
from pathlib import Path

from app.models.research_orchestration import ConfirmationV12, ResearchTaskV12
from app.modules.research_design_activation import (
    approved_design_activation_updates,
    design_versions_after_confirmation,
)
from app.modules.research_design_agent_revision import revise_research_design_with_agent
from app.modules.research_design_service import new_research_task
from app.modules.tests.target_output_ownership.audit_support import NOW, QUERY, brief_for


@lru_cache
def baseline_module(filename):
    path = f"services/api/app/modules/{filename}.py"
    code = subprocess.check_output(["git", "show", f"b14b45e7:{path}"], encoding="utf-8")
    name = f"ownership_legacy_{filename}"
    module = types.ModuleType(name)
    module.__file__ = str(Path.cwd() / path)
    sys.modules[name] = module
    current_service = sys.modules.get("app.modules.research_design_service")
    if filename == "research_design_compiler":
        # Resolve the compiler's DTO import against the actual historical service.
        sys.modules["app.modules.research_design_service"] = baseline_module(
            "research_design_service"
        )
    try:
        exec(compile(code, module.__file__, "exec"), module.__dict__)
    finally:
        if current_service is not None:
            sys.modules["app.modules.research_design_service"] = current_service
    return module


class RevisionRepository:
    def __init__(self, task, admission):
        self.task = task
        self.admission = admission
        self.writes = []

    def load(self, **_kwargs):
        return self.task

    def load_planning_workflow_admission(self, **_kwargs):
        return self.admission

    def save(self, candidate, *, expected_task_revision, **_kwargs):
        assert expected_task_revision == self.task.revision
        payload = candidate.model_dump(mode="json", by_alias=True)
        payload["revision"] = self.task.revision + 1
        self.task = ResearchTaskV12.model_validate(payload)
        self.writes.append(self.task)
        return self.task


def legacy_task(source, bundle, **brief_kwargs):
    brief, sources = brief_for(bundle, **brief_kwargs)
    semantic = baseline_module(
        "research_design_planner_assembly"
    ).build_adaptive_workflow_semantics(
        raw_input=QUERY,
        definition_catalog=bundle.definition_catalog,
        workflow_planning_context=bundle.planning_context,
        brief=brief,
        planning_sources=sources,
    )
    proposal = baseline_module("research_design_compiler").compile_research_design_proposal(
        semantic,
        definition_catalog=bundle.definition_catalog,
        raw_input=QUERY,
        workflow_planning_context=bundle.planning_context,
    )
    task = new_research_task(
        task_id="legacy_ownership_revision", registry_snapshot_ref=source.snapshot_ref()
    )
    repository = RevisionRepository(task, bundle.admission)
    service_module = baseline_module("research_design_service")
    historical_task = service_module.ResearchDesignService(
        repository=repository,
        definition_source=source,
        clock=lambda: NOW,
    ).plan(
        task_id=task.task_id,
        owner_id="audit",
        workspace_id="audit",
        request_id="isolated_historical_plan",
        expected_task_revision=0,
        user_input=QUERY,
        provider=lambda _: service_module.PlannerProviderResult(
            proposal=proposal.model_dump(mode="json", by_alias=True),
            provider_call_ref="isolated-historical-planner",
        ),
    )
    repository.writes.clear()
    return repository, historical_task.research_design_versions[0]


def revise(repository, source):
    def patch_provider(_instruction, design):
        return {
            "goal": design.goal + "（保留原问题，明确证据限制）",
            "workItems": [
                {"substageInstanceId": row.substage_instance_id, "objective": row.objective}
                for row in design.instances
            ],
        }, "isolated-targeted-patch"

    task = revise_research_design_with_agent(
        task_id=repository.task.task_id,
        owner_id="audit",
        workspace_id="audit",
        instruction="明确证据限制，保留已有研究工作。",
        expected_task_revision=repository.task.revision,
        repository=repository,
        definition_source=source,
        model_patch_provider=patch_provider,
    )
    return task, task.research_design_versions[-1]


def activate_revision(task, design):
    versions = design_versions_after_confirmation(
        versions=task.research_design_versions,
        design_id=design.research_design_id,
        version=design.version,
        decision="approved",
        confirmed_at=NOW,
        confirmation_id="confirmation_legacy_revision",
    )
    confirmed = versions[-1]
    payload = task.model_dump(mode="json", by_alias=True)
    payload["researchDesignVersions"] = [
        row.model_dump(mode="json", by_alias=True) for row in versions
    ]
    payload["confirmations"] = [
        ConfirmationV12(
            confirmationId="confirmation_legacy_revision",
            targetType="research_design",
            targetId=design.research_design_id,
            targetVersion=design.version,
            decision="approved",
            actorId="audit",
            createdAt=NOW,
        ).model_dump(mode="json", by_alias=True)
    ]
    for key, value in approved_design_activation_updates(
        task=task, design=confirmed, activated_at=NOW
    ).items():
        if isinstance(value, list):
            value = [
                row.model_dump(mode="json", by_alias=True) if hasattr(row, "model_dump") else row
                for row in value
            ]
        elif hasattr(value, "model_dump"):
            value = value.model_dump(mode="json", by_alias=True)
        payload[key] = value
    payload["status"] = "running"
    return ResearchTaskV12.model_validate(payload), confirmed
