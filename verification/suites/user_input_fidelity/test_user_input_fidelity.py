"""Focused production-boundary tests; Planner responses are controlled I/O fixtures.

Run with the target checkout's API and tests packages on PYTHONPATH. No direct
call or patch of the canonical-input freezing function is used.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from threading import Lock
from types import SimpleNamespace
from typing import Any

import pytest

from app.models.research_orchestration import ResearchTaskV12
from app.modules import research_design_service, research_execution_decision_service as decisions
from app.modules.research_orchestration_repository import TaskRevisionConflictError
from app.modules.research_orchestration_resolver import ResolutionResult
from tests.support.research_execution import (
    execution_input_context_for_test,
    multi_artifact_selection_fixture,
)


FAILURE = json.loads(
    (Path(__file__).parent / "fixtures" / "cs_preflight_production_failure_minimal.json").read_text(encoding="utf-8")
)
SIMPLE_PROPOSAL = json.loads(
    (Path(__file__).parent / "fixtures" / "research_design_simple.json").read_text(encoding="utf-8")
)
CS_INPUT = FAILURE["originalRequestExcerpt"]
CS_SUMMARY = FAILURE["persistedDesignGoal"]
PVD_INPUT = "  40% AlSc 靶材、Mo 衬底。\r\nPower=0.30 kW；N₂=0.012 slm；Ar=25 sccm。\r\n温度/压力/靶基距未知，均保留为 -。  "
PVD_SUMMARY = "为给定靶材和衬底设计首轮可追溯的 PVD 实验。"
NOW = datetime(2026, 7, 29, 1, 2, 4, tzinfo=timezone.utc)
SNAPSHOT = {
    "schemaVersion": 1,
    "contentHash": "sha256:" + "b" * 64,
}


class FakeDefinitionSource:
    def __init__(self) -> None:
        self.definitions = {
            "sdr:evidence_synthesis": (1, "solution_design"),
            "opentrajectory:progressive_reading": (1, "execution_validation"),
        }

    def snapshot_ref(self) -> dict[str, Any]:
        return deepcopy(SNAPSHOT)

    def resolve(self, definition_id: str):
        if definition_id not in self.definitions:
            return None
        version, stage = self.definitions[definition_id]
        return research_design_service.ResolvedDefinition(
            definition_id=definition_id,
            definition_version=version,
            stage_key=stage,
        )

    def definition_is_eligible_for_workflow_admission(self, definition_id, admission):
        return definition_id in self.definitions

    def system_recovery_target(self, reason_code: str):
        if reason_code != "semantic_material_insufficient":
            return None
        return {
            "reasonCode": reason_code,
            "definitionId": "opentrajectory:progressive_reading",
            "objective": "Recover source material needed by the blocked research step.",
            "expectedArtifactTypes": ["source_materials"],
        }


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


def _service_and_repo():
    task = research_design_service.new_research_task(
        task_id="task_design",
        registry_snapshot_ref=SNAPSHOT,
    )
    repository = FakeRepository(task)
    service = research_design_service.ResearchDesignService(
        repository=repository,
        definition_source=FakeDefinitionSource(),
        clock=lambda: datetime(2026, 7, 29, 1, 2, 3, tzinfo=timezone.utc),
    )
    return research_design_service, service, repository


@pytest.fixture(autouse=True)
def forbid_external_io(monkeypatch):
    import socket
    import subprocess
    import psycopg
    def blocked(*_args, **_kwargs):
        raise AssertionError("focused regression must not call DB, Provider or subprocess")
    monkeypatch.setattr(psycopg, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(subprocess, "Popen", blocked)


def _proposal(goal):
    proposal = deepcopy(SIMPLE_PROPOSAL)
    proposal["goal"] = goal
    return proposal


def _plan(service, repo, module, *, text, goal, request_id="initial", source_ref=None):
    observed = []
    def provider(_key):
        attempt = repo.task.planning_attempts[-1]
        assert attempt.status.value == "started"
        observed.append(deepcopy(attempt.model_dump(mode="json", by_alias=True)))
        return module.PlannerProviderResult(proposal=_proposal(goal), provider_call_ref=f"provider:{request_id}")
    task = service.plan(
        task_id=repo.task.task_id, owner_id="owner", workspace_id="workspace",
        request_id=request_id, expected_task_revision=repo.task.revision,
        provider=provider, user_input=text, user_input_source_ref=source_ref,
    )
    assert len(observed) == 1
    return task, observed[0]


def _confirm(service, repo):
    design = repo.task.research_design_versions[-1]
    return service.confirm(
        task_id=repo.task.task_id, owner_id="owner", workspace_id="workspace", actor_id="researcher",
        design_id=design.research_design_id, version=design.version,
        confirmation_id=f"confirm-v{design.version}", decision="approved",
        expected_task_revision=repo.task.revision,
    )


def _execute_to_frozen_skill(task, *, max_input_length=None, prepared_inputs=None):
    """Use production selection, input gate and WorkUnit freeze with fixture ports."""
    _, prototype, snapshot, facts, readiness = multi_artifact_selection_fixture(completion_coverage="progress_capable")
    readiness = {key: row.model_copy(update={"checked_at": NOW, "expires_at": NOW + timedelta(hours=1)}) for key, row in readiness.items()}
    design = next(d for d in task.research_design_versions if d.version == task.active_research_design_ref.version)
    instance = design.instances[0]
    snapshot = deepcopy(snapshot)
    snapshot["definitions"]["skills"][0]["sourceContract"]["frozenSchemas"]["input"]["schema"] = {
        "type": "object", "required": ["research_goal"],
        "properties": {"research_goal": {"type": "string", "minLength": 1}},
        "additionalProperties": False,
    }
    if max_input_length is not None:
        snapshot["definitions"]["skills"][0]["sourceContract"]["frozenSchemas"]["input"]["schema"]["properties"]["research_goal"]["maxLength"] = max_input_length
    snapshot["bindings"]["skills"][0]["runtimeInputs"] = {
        "contractVersion": 2,
        "fields": [{"targetField": "research_goal", "source": {"kind": "invocationContext", "field": "canonical_user_query"}}],
    }
    resolution = ResolutionResult(
        designRef=task.active_research_design_ref,
        instanceRef={"substageInstanceId": instance.substage_instance_id, "instanceRevision": instance.instance_revision},
        registrySnapshotRef=task.registry_snapshot_ref,
        candidateEvaluations=prototype.candidate_evaluations,
        preferredCandidateEvaluationRefs=[],
    )
    prepared = decisions.prepare_selection(
        task, instance_id=instance.substage_instance_id,
        resolution=resolution, snapshot_envelope=snapshot, facts=facts,
        runtime_readiness=readiness, selection_request_id="selection-fidelity",
        selection_command_hash="command-fidelity", expected_task_revision=task.revision,
        owner_id="owner", workspace_id="workspace", decided_at=NOW,
    )
    execution = decisions.prepare_selection_execution(prepared, task, "skill:evidence_synthesis_skill:1")
    if prepared_inputs is not None:
        prepared_inputs.append(deepcopy(dict(execution.work_units[0].invocation_context)))
    ports = SimpleNamespace(read_execution_input_context=lambda **kw: execution_input_context_for_test(task, **kw))
    service = decisions.ResearchExecutionDecisionService(repository=ports, resolver=None, facts_source=None, now=lambda: NOW)
    validated = service._validate_execution_preparation(
        snapshot=snapshot, preparation=execution, owner_id="owner", workspace_id="workspace",
    )
    frozen = decisions.freeze_selection_execution(prepared, task, validated)
    return frozen.task.work_units[-1]


@pytest.mark.parametrize(("text", "summary"), [(CS_INPUT, CS_SUMMARY), (PVD_INPUT, PVD_SUMMARY)], ids=["actual_cs_failure", "adjacent_pvd_units"])
def test_original_input_survives_summary_into_actual_frozen_skill(text, summary):
    module, service, repo = _service_and_repo()
    planned, before_provider = _plan(service, repo, module, text=text, goal=summary)
    frozen_before_call = before_provider["userInputSnapshot"]
    assert frozen_before_call == {
        "sources": [{"sourceKind": "user_request", "sourceRef": "initial", "content": text}],
        "baseResearchDesignRef": None,
    }
    assert planned.research_design_versions[-1].goal == summary
    confirmed = _confirm(service, repo)
    work = _execute_to_frozen_skill(confirmed)
    assert work.resolved_parameters == {"research_goal": text}
    assert work.invocation_context["canonical_user_query"] == text


@pytest.mark.parametrize("kind", ["user_revision", "plan_revision"])
def test_draft_revision_appends_exact_sources_and_preserves_initial_snapshot(kind):
    module, service, repo = _service_and_repo()
    original, _ = _plan(service, repo, module, text=CS_INPUT, goal=CS_SUMMARY, source_ref="submit-raw")
    first = original.research_design_versions[-1]
    initial_snapshot = original.planning_attempts[-1].model_dump(mode="json", by_alias=True)["userInputSnapshot"]
    changes = "  revision 改为 release/v2；仓库地址不变。\r\n保留原始比较边界。  "
    observed = []
    def provider(_key):
        observed.append(repo.task.planning_attempts[-1].model_dump(mode="json", by_alias=True))
        return module.PlannerProviderResult(proposal=_proposal("按用户更改后的代码版本形成可追溯比较。"), provider_call_ref="provider:revision")
    revised = service.revise(
        task_id=repo.task.task_id, owner_id="owner", workspace_id="workspace",
        request_id="revision-1", expected_task_revision=repo.task.revision,
        provider=provider, user_input=changes, user_input_source_ref="user-edit-1", user_input_kind=kind,
    )
    expected_sources = [
        {"sourceKind": "user_request", "sourceRef": "submit-raw", "content": CS_INPUT},
        {"sourceKind": kind, "sourceRef": "user-edit-1", "content": changes},
    ]
    snapshot = observed[0]["userInputSnapshot"]
    assert snapshot["sources"] == expected_sources
    assert snapshot["baseResearchDesignRef"] == {"researchDesignId": first.research_design_id, "version": first.version}
    assert revised.planning_attempts[0].model_dump(mode="json", by_alias=True)["userInputSnapshot"] == initial_snapshot
    persisted_revision = revised.model_dump(mode="json", by_alias=True)
    replay = service.revise(
        task_id=repo.task.task_id, owner_id="owner", workspace_id="workspace",
        request_id="revision-1", expected_task_revision=repo.task.revision,
        provider=lambda _key: pytest.fail("same revision request must replay without a new Planner call"),
        user_input=changes, user_input_source_ref="user-edit-1", user_input_kind=kind,
    )
    assert replay.model_dump(mode="json", by_alias=True) == persisted_revision
    with pytest.raises(module.ResearchDesignServiceError):
        service.revise(
            task_id=repo.task.task_id, owner_id="owner", workspace_id="workspace",
            request_id="revision-1", expected_task_revision=repo.task.revision,
            provider=lambda _key: pytest.fail("conflicting replay must be rejected before Provider"),
            user_input="将相同请求暗改为另一个未确认 revision。", user_input_source_ref="user-edit-1", user_input_kind=kind,
        )
    assert repo.task.model_dump(mode="json", by_alias=True) == persisted_revision
    confirmed = _confirm(service, repo)
    work = _execute_to_frozen_skill(confirmed)
    visible = work.resolved_parameters["research_goal"]
    assert visible == "\n\n".join(row["content"] for row in expected_sources)
    assert confirmed.research_design_versions[-1].goal not in visible


def test_second_revision_and_serialization_preserve_all_source_texts():
    module, service, repo = _service_and_repo()
    _plan(service, repo, module, text=PVD_INPUT, goal=PVD_SUMMARY)
    corrections = ["N₂ 设为 12 sccm。\nAr 保持 25 sccm。", "只保留 3 组实验；不补温度与压力。"]
    for i, text in enumerate(corrections, 1):
        service.revise(
            task_id=repo.task.task_id, owner_id="owner", workspace_id="workspace",
            request_id=f"revision-{i}", expected_task_revision=repo.task.revision,
            user_input=text,
            provider=lambda _key, i=i: module.PlannerProviderResult(proposal=_proposal(f"第 {i} 次确认后的 PVD 实验安排。"), provider_call_ref=f"provider:{i}"),
        )
    confirmed = _confirm(service, repo)
    restored = ResearchTaskV12.model_validate_json(confirmed.model_dump_json(by_alias=True))
    work = _execute_to_frozen_skill(restored)
    assert work.resolved_parameters["research_goal"] == "\n\n".join([PVD_INPUT, *corrections])
    assert _execute_to_frozen_skill(confirmed).resolved_parameters == work.resolved_parameters


def test_agent_revision_entrypoint_preserves_raw_instruction_into_frozen_work():
    from app.modules.research_design_agent_revision import revise_research_design_with_agent
    module, service, repo = _service_and_repo()
    _plan(service, repo, module, text=PVD_INPUT, goal=PVD_SUMMARY)
    original = "  只调整 N₂ 为 12 sccm。\r\n功率继续 0.30 kW；不填未知值。  "
    model_inputs = []
    def patch_provider(instruction, latest):
        model_inputs.append(instruction)
        return {
            "goal": "按已更正气体流量形成 PVD 方案。",
            "workItems": [{"substageInstanceId": row.substage_instance_id, "objective": row.objective} for row in latest.instances],
        }, "provider:targeted-revision"
    revised = revise_research_design_with_agent(
        task_id=repo.task.task_id, owner_id="owner", workspace_id="workspace",
        instruction=original, expected_task_revision=repo.task.revision,
        repository=repo, definition_source=FakeDefinitionSource(),
        request_id="agent-edit", model_patch_provider=patch_provider,
    )
    assert len(model_inputs) == 1
    source = revised.planning_attempts[-1].model_dump(mode="json", by_alias=True)["userInputSnapshot"]["sources"][-1]
    assert source == {"sourceKind": "user_revision", "sourceRef": "agent-edit", "content": original}
    work = _execute_to_frozen_skill(_confirm(service, repo))
    assert work.resolved_parameters["research_goal"].endswith(source["content"])


def test_completed_planning_replay_cannot_append_or_replace_original_input():
    module, service, repo = _service_and_repo()
    first, _ = _plan(service, repo, module, text=CS_INPUT, goal=CS_SUMMARY)
    before = first.model_dump(mode="json", by_alias=True)
    def forbidden_provider(_key):
        raise AssertionError("completed planning must not invoke provider")
    replay = service.plan(
        task_id=repo.task.task_id, owner_id="owner", workspace_id="workspace",
        request_id="initial", expected_task_revision=repo.task.revision,
        provider=forbidden_provider, user_input=CS_INPUT,
    )
    assert replay.model_dump(mode="json", by_alias=True) == before
    with pytest.raises(module.ResearchDesignServiceError):
        service.plan(
            task_id=repo.task.task_id, owner_id="owner", workspace_id="workspace",
            request_id="initial", expected_task_revision=repo.task.revision,
            provider=forbidden_provider, user_input="改用另一个未经确认的仓库。",
        )
    assert repo.task.model_dump(mode="json", by_alias=True) == before


def test_missing_source_never_silently_substitutes_design_summary():
    module, service, repo = _service_and_repo()
    _plan(service, repo, module, text=None, goal=CS_SUMMARY)
    confirmed = _confirm(service, repo)
    with pytest.raises(decisions.ExecutionPreflightBlocked) as blocked:
        _execute_to_frozen_skill(confirmed)
    assert blocked.value.code == "input_composition_invocation_context_missing"
    assert not confirmed.work_units and not confirmed.execution_decisions


def test_revision_from_missing_original_source_cannot_promote_summary_to_raw_input():
    module, service, repo = _service_and_repo()
    _plan(service, repo, module, text=None, goal=CS_SUMMARY)
    before = repo.task.model_dump(mode="json", by_alias=True)
    with pytest.raises((module.ResearchDesignServiceError, ValueError)):
        service.revise(
            task_id=repo.task.task_id, owner_id="owner", workspace_id="workspace",
            request_id="revision-legacy", expected_task_revision=repo.task.revision,
            user_input="revision 改为 v2。", provider=lambda _key: pytest.fail("missing original source must be detected before planning"),
        )
    assert repo.task.model_dump(mode="json", by_alias=True) == before


def test_multi_source_over_limit_preserves_complete_sources_and_blocks_dispatch():
    module, service, repo = _service_and_repo()
    original = CS_INPUT + "\n" + "保留初始原文。" * 280
    amendments = ["补充条件不改变仓库和 revision。\n" * 120, "最终补充：" + "未知值保持未知。" * 200]
    _plan(service, repo, module, text=original, goal=CS_SUMMARY)
    for i, amendment in enumerate(amendments, 1):
        service.revise(
            task_id=repo.task.task_id, owner_id="owner", workspace_id="workspace",
            request_id=f"long-revision-{i}", expected_task_revision=repo.task.revision,
            user_input=amendment,
            provider=lambda _key: module.PlannerProviderResult(proposal=_proposal(CS_SUMMARY), provider_call_ref="provider:long-revision"),
        )
    confirmed = _confirm(service, repo)
    frozen_sources = confirmed.planning_attempts[-1].model_dump(mode="json", by_alias=True)["userInputSnapshot"]["sources"]
    assert [row["content"] for row in frozen_sources] == [original, *amendments]
    trace = []
    with pytest.raises(decisions.ExecutionPreflightBlocked) as blocked:
        _execute_to_frozen_skill(confirmed, max_input_length=4000, prepared_inputs=trace)
    assert blocked.value.code == "input_contract_invalid"
    assert blocked.value.facts["fieldPaths"] == ["/research_goal"]
    invocation = trace[0]["canonical_user_query"]
    assert len(invocation) > 4000
    assert invocation == "\n\n".join(row["content"] for row in frozen_sources)
    assert not confirmed.work_units and not confirmed.execution_decisions


def test_fork_and_recovery_preserve_self_contained_sources_without_dereferencing_base():
    from app.modules.research_orchestration_repository import _extract_contract_payload
    from app.modules.research_user_input import canonical_user_query
    from app.routes.tasks import _fork_v12_contract_into
    module, service, repo = _service_and_repo()
    _plan(service, repo, module, text=CS_INPUT, goal=CS_SUMMARY)
    for i in (1, 2):
        service.revise(
            task_id=repo.task.task_id, owner_id="owner", workspace_id="workspace",
            request_id=f"fork-source-revision-{i}", expected_task_revision=repo.task.revision,
            user_input=f"  第 {i} 次修改仅缩小比较范围。\r\n仓库与代码版本保持。  ",
            provider=lambda _key: module.PlannerProviderResult(proposal=_proposal(CS_SUMMARY), provider_call_ref="provider:fork-source-revision"),
        )
    original = _confirm(service, repo)
    original_snapshot = original.planning_attempts[-1].model_dump(mode="json", by_alias=True)["userInputSnapshot"]
    assert original_snapshot["baseResearchDesignRef"]["version"] == 2
    target = {"taskId": "forked-input-fidelity"}
    assert _fork_v12_contract_into(original.model_dump(mode="json", by_alias=True), target)
    cloned = ResearchTaskV12.model_validate(_extract_contract_payload(target))
    assert [row.version for row in cloned.research_design_versions] == [1]
    assert cloned.research_design_versions[0].status.value == "draft"
    assert cloned.active_research_design_ref is None and not cloned.confirmations
    cloned_snapshot = cloned.planning_attempts[-1].model_dump(mode="json", by_alias=True)["userInputSnapshot"]
    assert cloned_snapshot == original_snapshot
    cloned_repo = FakeRepository(cloned)
    cloned_service = module.ResearchDesignService(repository=cloned_repo, definition_source=FakeDefinitionSource(), clock=lambda: NOW)
    work = _execute_to_frozen_skill(_confirm(cloned_service, cloned_repo))
    assert work.resolved_parameters["research_goal"] == "\n\n".join(row["content"] for row in original_snapshot["sources"])
    payload = cloned_repo.task.model_dump(mode="json", by_alias=True)
    payload["executionMode"] = "automatic_research"
    payload["startedAt"] = NOW.isoformat()
    payload["substageRuntimeStates"][0].update({"status": "skipped", "statusReasonCode": "semantic_material_insufficient"})
    cloned_repo.task = ResearchTaskV12.model_validate(payload)
    recovered = cloned_service.recover_confirmed_semantic_material_gap(
        task_id=cloned_repo.task.task_id, owner_id="owner", workspace_id="workspace",
        blocked_instance_id=cloned.research_design_versions[0].instances[0].substage_instance_id,
        gap_reason_code="semantic_material_insufficient", expected_task_revision=cloned_repo.task.revision,
    )
    draft = recovered.research_design_versions[-1]
    assert recovered.status.value == "awaiting_confirmation" and draft.status.value == "draft"
    assert len(recovered.planning_attempts) == len(cloned.planning_attempts)
    assert draft.coverage_report.planning_attempt_id == cloned.research_design_versions[0].coverage_report.planning_attempt_id
    assert canonical_user_query(recovered, draft) == "\n\n".join(row["content"] for row in original_snapshot["sources"])
    assert not recovered.work_units and not recovered.execution_decisions
