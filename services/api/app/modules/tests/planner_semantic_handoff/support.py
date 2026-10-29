"""Suite-local inputs and dependency boundaries, never a parallel compiler."""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import httpx
from app.models.research_orchestration import ResearchTaskV12
from app.modules.research_design_freeform_semantic_provider import (
    FreeformSemanticProviderConfig,
    prepare_freeform_research_design_attempt,
)
from app.modules.research_design_service import (
    ResearchDesignService,
    new_research_task,
    validate_research_design_proposal,
)
from app.modules.research_design_workflow_semantic_provider import (
    WorkflowSemanticProviderConfig,
    prepare_hybrid_workflow_planner_attempt,
)
from app.modules.workflow_planning import workflow_planning_bundle
from app.modules.workflow_planning_admission import WorkflowPlanningAdmission

from .evidence import record

ROOT = Path(__file__).resolve().parents[6]
HERE = Path(__file__).parent
NOW = datetime(2026, 10, 9, tzinfo=UTC)
QUERY = (
    "我最近在做 agent 评测，发现 LLM judge 有时候很容易被回答风格带偏："
    "任务本身做得差不多，但如果回答更长、解释得更多，或者写得更像已经完成了，"
    "分数反而会更高。我想知道这个现象现在有没有比较可靠的研究证据，"
    "通常在什么情况下最明显，我们目前到底能下什么结论，还有哪些关键问题没搞清楚，"
    "下一步最值得验证什么？"
)
CONSTRAINTS = ["不将未知条件视为已确认", "保留所有可追溯来源"]


def real_payload():
    return json.loads((HERE / "real-response.json").read_text(encoding="utf-8"))["payload"]


def bundle_for(source, workflow_id="opentrajectory_research_workflow"):
    return workflow_planning_bundle(
        selection=WorkflowPlanningAdmission(
            workflow_id=workflow_id,
            reason="PV2 isolated software contract input",
            user_facing_summary="研究方案交接验证",
            suggested_title="研究方案交接验证",
        ),
        raw_goal=QUERY,
        definition_source=source,
    )


def workflow_payload(bundle):
    rows = sorted(
        [
            *bundle.planning_context["coreResponsibilities"],
            *bundle.planning_context["conditionalResponsibilities"],
        ],
        key=lambda row: row["displayOrder"],
    )
    core_keys = {row["nodeKey"] for row in bundle.planning_context["coreResponsibilities"]}
    return {
        "researchGoal": QUERY,
        "targetOutputs": ["当前问题的证据与结论"],
        "constraints": CONSTRAINTS,
        "responsibilityDeltas": [
            {
                "responsibilityChoice": f"responsibility_{index}",
                "currentProjectWorkContext": f"核查当前问题的{row['nodeKey']}与适用边界。",
                "targetOutputNumbers": [1],
            }
            for index, row in enumerate(rows, 1)
            if row["nodeKey"] in core_keys
        ],
    }


class RecordedTransport:
    """Supplies the exact scientific JSON at the HTTP boundary, without network."""

    def __init__(self, payload):
        self.content = json.dumps(payload, ensure_ascii=False)

    def post(self, endpoint, **_kwargs):
        return httpx.Response(
            200,
            request=httpx.Request("POST", endpoint),
            json={"id": "pv2-offline-input", "choices": [{"message": {"content": self.content}}]},
        )


def decode(payload, bundle, *, freeform=False, planning_sources=()):
    record("input", payload)
    if freeform:
        prepared = prepare_freeform_research_design_attempt(
            raw_input=QUERY,
            source_page="pv2",
            selected_hint=None,
            ordinal=1,
            definition_catalog=bundle.definition_catalog,
            planning_sources=planning_sources,
            provider_config=FreeformSemanticProviderConfig(
                provider="huiyan_chat_completions",
                model="offline-control",
                endpoint="https://pv2.invalid",
                timeout_seconds=1,
                max_tokens=4000,
                temperature=0,
            ),
        )
    else:
        prepared = prepare_hybrid_workflow_planner_attempt(
            raw_input=QUERY,
            ordinal=1,
            definition_catalog=bundle.definition_catalog,
            workflow_planning_context=bundle.planning_context,
            planning_sources=planning_sources,
            provider_config=WorkflowSemanticProviderConfig(
                provider="huiyan_chat_completions",
                model="offline-control",
                endpoint="https://pv2.invalid",
                timeout_seconds=1,
            ),
        )
    result = prepared.execute(transport=RecordedTransport(payload)) if not freeform else None
    if freeform:
        from app.execution.research_start_provider_adapter import execute_prepared_attempt

        result = execute_prepared_attempt(prepared, transport=RecordedTransport(payload))
    record("prepared_decode_receiver_assembly_compiler", result.proposal)
    return validate_research_design_proposal(result.proposal)


class MemoryTaskRepository:
    """Explicit ephemeral task storage; does not claim PostgreSQL persistence."""

    def __init__(self, task, admission):
        self.task = task
        self.admission = admission

    def load(self, **_kwargs):
        return self.task.model_copy(deep=True)

    def load_planning_workflow_admission(self, **_kwargs):
        return self.admission

    def save(self, task, **_kwargs):
        self.task = task.model_copy(deep=True)
        return self.load()


def build(proposal, bundle, source, *, freeform=False):
    task = new_research_task(task_id="task_pv2", registry_snapshot_ref=source.snapshot_ref())
    repository = MemoryTaskRepository(task, None if freeform else bundle.admission)
    service = ResearchDesignService(
        repository=repository, definition_source=source, clock=lambda: NOW
    )
    design = service._build_design(
        task, proposal, "pv2-planning", owner_id="pv2", workspace_id="pv2"
    )
    record("formal_build", design.model_dump(mode="json", by_alias=True))
    template = json.loads(
        (ROOT / "packages/shared/test-fixtures/research-orchestration-v1.2-valid.json").read_text(
            encoding="utf-8"
        )
    )["researchTask"]
    attempt = deepcopy(template["planningAttempts"][0])
    attempt.update(taskId=task.task_id, planningAttemptId="pv2-planning")
    payload = task.model_dump(mode="json", by_alias=True)
    payload.update(
        researchDesignVersions=[design.model_dump(mode="json", by_alias=True)],
        planningAttempts=[attempt],
        status="awaiting_confirmation",
    )
    repository.task = ResearchTaskV12.model_validate(payload)
    return service, repository, design


def confirm(service, repository, design):
    task = service.confirm(
        task_id=repository.task.task_id,
        owner_id="pv2",
        workspace_id="pv2",
        actor_id="pv2",
        design_id=design.research_design_id,
        version=design.version,
        confirmation_id="pv2-confirmation",
        decision="approved",
        expected_task_revision=repository.task.revision,
    )
    record("formal_confirm", task.model_dump(mode="json", by_alias=True))
    return task, task.research_design_versions[-1]


def evaluate(design, state):
    from app.modules.research_completion_decision import expected_project_completion_evaluation

    result = expected_project_completion_evaluation(design, state)
    record("completion_consumer", {"state": state, "result": result})
    return result


def formal_text(design):
    return json.dumps(design.model_dump(mode="json", by_alias=True), ensure_ascii=False)


def freeform_payload(bundle, outputs, numbers):
    return {
        "researchGoal": QUERY,
        "targetOutputs": outputs,
        "constraints": CONSTRAINTS,
        "workItems": [
            {
                "workTypeChoice": f"work_type_{index}",
                "scientificObjective": f"核查当前证据的贡献{index}。",
                "targetOutputNumbers": refs,
            }
            for index, refs in enumerate(numbers, 1)
        ],
    }
