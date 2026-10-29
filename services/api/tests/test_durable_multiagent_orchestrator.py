from __future__ import annotations

from copy import deepcopy
import json

import pytest

from app.execution import durable_multiagent_orchestrator as orchestrator_module
from app.execution import durable_skill_runtime
from app.execution.durable_multiagent_orchestrator import DurableMultiAgentOrchestrator
from app.execution.durable_orchestrator_bridge import (
    _mandatory_review_contract_snapshot,
    compile_durable_work_units,
)
from app.execution.durable_orchestrator_runtime import load_frozen_artifact_contract
from app.modules.agent_registry import load_agent_registry
from app.modules.capability_resolver import resolve_creation_plan_capabilities
from app.modules import collaboration_repository as collaboration_repository_module
from app.modules.durable_execution import DurableExecutionError


class FakeCollaborationRepository:
    """Fake durable store; it never contacts a live provider."""

    def __init__(self, *, stop_before_unit_key: str | None = None, stop_before_creation_key: str | None = None,
                 pause_before_unit_key: str | None = None) -> None:
        self.units: list[dict] = []
        self.calls: dict[str, dict] = {}
        self.handoffs: list[dict] = []
        self.reviews: list[dict] = []
        self.review_material_requests: list[dict] = []
        self.events: list[str] = []
        self.stop_before_unit_key = stop_before_unit_key
        self.stop_before_creation_key = stop_before_creation_key
        self.pause_before_unit_key = pause_before_unit_key

    def create_work_unit(self, **kwargs):
        if kwargs["unit_key"] == self.stop_before_creation_key:
            raise DurableExecutionError(409, "stop_won")
        existing = next((unit for unit in self.units if unit["unit_key"] == kwargs["unit_key"]), None)
        if existing:
            existing["dispatch_paused"] = kwargs["unit_key"] == self.pause_before_unit_key
            self.events.append(f"reconcile:{existing['unit_key']}")
            return existing
        unit = {
            **kwargs,
            "work_unit_id": f"wu_{len(self.units) + 1}",
            "state": "queued",
            "output_artifact_refs": [],
            "dispatch_paused": kwargs["unit_key"] == self.pause_before_unit_key,
        }
        self.units.append(unit)
        self.events.append(f"create:{unit['unit_key']}:{','.join(unit['input_artifact_refs'])}")
        return unit

    def list_work_units(self, **_kwargs):
        return list(self.units)

    def transition_work_unit(self, **kwargs):
        unit = next(unit for unit in self.units if unit["work_unit_id"] == kwargs["work_unit_id"])
        if kwargs["state"] == "running" and unit["unit_key"] == self.stop_before_unit_key:
            raise DurableExecutionError(409, "stop_won")
        unit["state"] = kwargs["state"]
        if "output_artifact_refs" in kwargs:
            unit["output_artifact_refs"] = list(kwargs["output_artifact_refs"])
        self.events.append(f"unit:{unit['unit_key']}:{kwargs['state']}")
        return unit

    def create_capability_invocation(self, **kwargs):
        existing = next((call for call in self.calls.values() if call["idempotency_key"] == kwargs["idempotency_key"]), None)
        if existing:
            return existing
        call = {**kwargs, "call_id": f"call_{len(self.calls) + 1}", "state": "queued", "external_action_started": False, "output_artifact_refs": []}
        self.calls[call["call_id"]] = call
        return call

    def create_skill_call(self, **kwargs):
        return self.create_capability_invocation(invocation_kind="skill", **kwargs)

    def finish_agent_and_create_skill_call(self, **kwargs):
        normalized = orchestrator_module._normalize_persisted_skill_request(
            kwargs["skill_request"], require_frozen_authority=True,
        )
        assert normalized == kwargs["skill_request"]
        assert normalized["skillId"] == kwargs["skill_id"]
        assert normalized["inputMemoryRefs"] == kwargs["input_memory_refs"]
        assert normalized["inputArtifactRefs"] == kwargs["input_artifact_refs"]
        parent = self.calls[kwargs["call_id"]]
        parent.update(
            state="succeeded", result_summary=kwargs["summary"],
            output_artifact_refs=[], failure_classification=None,
        )
        existing = next(
            (call for call in self.calls.values()
             if call.get("idempotency_key") == kwargs["skill_idempotency_key"]),
            None,
        )
        skill_call = self.create_skill_call(
            run_id=parent["run_id"], owner_id=kwargs["owner_id"], workspace_id=kwargs["workspace_id"],
            worker_id=kwargs["worker_id"], fence_epoch=kwargs["fence_epoch"],
            work_unit_id=parent["work_unit_id"], requesting_agent_id=parent["requesting_agent_id"],
            skill_id=kwargs["skill_id"], idempotency_key=kwargs["skill_idempotency_key"],
            request_fingerprint=kwargs["skill_request_fingerprint"],
            input_memory_refs=kwargs["input_memory_refs"], input_artifact_refs=kwargs["input_artifact_refs"],
        )
        request_snapshot = "skill-request.v2:" + json.dumps(
            normalized, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
        )
        if existing is not None and skill_call.get("result_summary") != request_snapshot:
            raise DurableExecutionError(409, "capability_invocation_idempotency_conflict")
        if skill_call.get("result_summary") is None:
            skill_call["result_summary"] = request_snapshot
        return {"parentCall": parent, "skillCall": skill_call}

    def find_recoverable_requested_skill_call(self, **kwargs):
        calls = [
            call for call in self.calls.values()
            if call["run_id"] == kwargs["run_id"]
            and call["work_unit_id"] == kwargs["work_unit_id"]
            and call["requesting_agent_id"] == kwargs["requesting_agent_id"]
            and call["invocation_kind"] == "skill"
        ]
        assert len(calls) <= 1
        return calls[0] if calls else None

    def begin_capability_invocation(self, **kwargs):
        call = self.calls[kwargs["call_id"]]
        if call["external_action_started"]:
            return call, True
        call["external_action_started"] = True
        call["state"] = "running"
        self.events.append(f"begin:{call['call_id']}")
        return call, False

    def mark_capability_result_unknown(self, **kwargs):
        call = self.calls[kwargs["call_id"]]
        call["state"] = "unknown"
        next(unit for unit in self.units if unit["work_unit_id"] == call["work_unit_id"])["state"] = "unknown"
        return call

    def fail_unstarted_capability_invocation(self, **kwargs):
        call = self.calls[kwargs["call_id"]]
        if kwargs.get("failure_classification", "skill_runtime_unavailable") != "skill_runtime_unavailable":
            raise DurableExecutionError(422, "invalid_unstarted_failure_classification")
        if call["external_action_started"]:
            raise DurableExecutionError(409, "capability_invocation_already_started")
        if call["state"] == "failed":
            if (
                call.get("result_summary") != kwargs["summary"]
                or call.get("output_artifact_refs")
                or call.get("failure_classification") != "skill_runtime_unavailable"
            ):
                raise DurableExecutionError(409, "capability_invocation_result_conflict")
            return call
        if call["state"] != "queued":
            raise DurableExecutionError(409, "terminal_capability_invocation")
        call.update(
            state="failed", result_summary=kwargs["summary"], output_artifact_refs=[],
            failure_classification="skill_runtime_unavailable", ended_at="now",
        )
        self.events.append(f"fail-unstarted:{call['call_id']}")
        return call

    def finish_capability_invocation(self, **kwargs):
        call = self.calls[kwargs["call_id"]]
        call.update(
            state=kwargs["state"], result_summary=kwargs["summary"],
            output_artifact_refs=list(kwargs["output_artifact_refs"]),
            failure_classification=kwargs.get("failure_classification"),
        )
        self.events.append(f"finish:{call['call_id']}:{kwargs['state']}")
        return call

    def finish_artifact_review(self, **kwargs):
        call = self.calls[kwargs["call_id"]]
        unit = next(unit for unit in self.units if unit["work_unit_id"] == call["work_unit_id"])
        assert call["invocation_kind"] == "review"
        assert call["requesting_agent_id"] == "result_reliability_audit"
        assert unit["kind"] == "review"
        assert kwargs["artifact_refs"] == call["input_artifact_refs"]
        verdict = kwargs["verdict"]
        continue_allowed = kwargs.get("continue_allowed", False)
        call.update(
            state="succeeded" if verdict == "usable" or continue_allowed else "blocked",
            result_summary=kwargs["basis"], output_artifact_refs=[],
        )
        unit.update(
            state="succeeded" if verdict == "usable" or continue_allowed else "blocked",
            decision_summary=verdict,
            output_artifact_refs=list(kwargs["artifact_refs"]) if verdict == "usable" or continue_allowed else [],
        )
        review = {**kwargs, "reviewedAt": "2026-07-24T00:00:00Z", "reviewer": unit["agent_id"], "artifactVersion": "v1"}
        self.reviews.append(review)
        return {"call": call, "workUnit": unit, "verdict": verdict, "artifactState": "usable" if verdict == "usable" else verdict}

    def complete_specialist_after_review(self, **kwargs):
        unit = next(unit for unit in self.units if unit["work_unit_id"] == kwargs["work_unit_id"])
        assert unit["kind"] in {"specialist", "skill_route"}
        review = next(
            review for review in self.reviews
            if review["artifact_refs"] == kwargs["artifact_refs"]
            and (review["verdict"] == "usable" or review.get("continue_allowed") is True)
        )
        assert review["reviewer"] != unit["agent_id"]
        unit.update(state="succeeded", summary=kwargs["summary"], output_artifact_refs=list(kwargs["artifact_refs"]))
        self.events.append(f"unit:{unit['unit_key']}:succeeded")
        return unit

    def handoff(self, **kwargs):
        source = next(unit for unit in self.units if unit["work_unit_id"] == kwargs["from_work_unit_id"])
        target = next(unit for unit in self.units if unit["work_unit_id"] == kwargs["to_work_unit_id"])
        assert source["state"] == "succeeded"
        assert target["state"] == "queued"
        assert target["parent_work_unit_id"] == source["work_unit_id"]
        assert kwargs["input_artifact_refs"] == source["output_artifact_refs"]
        assert kwargs["output_artifact_refs"] == source["output_artifact_refs"]
        assert target["input_artifact_refs"] == source["output_artifact_refs"]
        self.handoffs.append(dict(kwargs))
        self.events.append(f"handoff:{source['unit_key']}:{target['unit_key']}")
        return self.handoffs[-1]

    def list_handoffs(self, **_kwargs):
        return list(self.handoffs)

    def read_review_materials(self, **kwargs):
        self.review_material_requests.append(deepcopy(kwargs))
        return [
            {
                "artifactId": artifact_id,
                "title": f"Artifact {artifact_id}",
                "mimeType": "text/markdown",
                "content": f"Verified research body for {artifact_id}.",
                "truncated": False,
                "contentSha256": f"sha256-{artifact_id}",
            }
            for artifact_id in kwargs["artifact_refs"]
        ]


class FakeControllerCommands:
    def __init__(self) -> None:
        self.received: list[str] = []
        self.applied: list[dict] = []

    def consume_at_work_unit_boundary(self, **kwargs):
        self.applied.extend({"text": text, **kwargs} for text in self.received)
        received, self.received = list(self.received), []
        return received


@pytest.fixture(autouse=True)
def fake_controller_command_repository(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(orchestrator_module, "ControllerCommandRepository", FakeControllerCommands)


def _run() -> dict:
    return {
        "run_id": "run_1", "task_id": "task_1", "owner_id": "owner_1", "workspace_id": "workspace_1",
        "input_snapshot": {
            "goalSummary": "check the research question",
            "creationPlan": {"clarifications": ["confirmed materials only"]},
            "memoryRecords": [{
                "memoryId": "memory_goal", "kind": "user_fact", "content": "Use confirmed materials only.",
                "scope": "task", "createdBy": "user_1", "createdAt": "2026-07-23T00:00:00Z", "confidence": "high",
            }],
        },
    }


def _frozen_work_unit_contract(agent_id: str) -> dict:
    registry = load_agent_registry()
    agent = registry.require(agent_id)
    artifact_contract = agent.artifact_contract
    artifact_schema = agent.outputContract.get("artifactSchema")
    if not artifact_contract or not isinstance(artifact_schema, str) or not artifact_schema.strip():
        raise AssertionError(f"Agent {agent_id} has no frozen artifact contract metadata")
    frozen_artifact_contract = load_frozen_artifact_contract(
        artifact_contract,
        artifact_schema,
    )
    artifact_contract = {
        "artifactContractSchemaVersion": registry.schemaVersion,
        "artifactRequiredFields": list(frozen_artifact_contract.schema["required"]),
        **frozen_artifact_contract.snapshot(),
    }
    return {
        **artifact_contract,
        "mandatoryReviewContract": _mandatory_review_contract_snapshot(registry),
    }


def _plan(*agents: str) -> dict:
    units: list[dict] = []
    parent: str | None = None
    registry = load_agent_registry()
    for index, agent_id in enumerate(agents, start=1):
        registry.require(agent_id)
        frozen_work_unit_contract = _frozen_work_unit_contract(agent_id)
        unit_key = f"unit_{index}"
        units.append({
            "unitKey": unit_key, "parentUnitKey": parent, "agentId": agent_id, "kind": "specialist", "state": "planned",
            "objective": f"{agent_id} work", "inputMemoryRefs": ["memory_goal"] if index == 1 else [],
            "inputArtifactRefs": [], "selectedSkillIds": [], "modelMode": "terra",
            **frozen_work_unit_contract,
        })
        parent = unit_key
    return {
        "context": {"runId": "run_1", "taskId": "task_1", "ownerId": "owner_1", "workspaceId": "workspace_1"},
        "selectedProvider": "openai_primary",
        "modelProfile": "terra",
        "workUnits": units,
    }


def _orchestrator(repository: FakeCollaborationRepository, requests: list[dict]) -> DurableMultiAgentOrchestrator:
    def execute(request: dict) -> dict:
        requests.append(deepcopy(request))
        if request.get("phase") == "review_artifact":
            return {"verdict": "usable", "basis": "The artifact supports the next step.", "issues": [], "missingInputs": []}
        return {"state": "succeeded", "summary": f"{request['agentId']} complete"}

    return DurableMultiAgentOrchestrator(
        collaboration=repository,
        execute_agent_unit=execute,
        persist_artifact=lambda call_id, _result: f"artifact_{call_id}",
        controller_commands=FakeControllerCommands(),
    )


@pytest.mark.parametrize(
    ("issues", "expected"),
    [
        ([{"severity": "warning"}], True),
        ([{"severity": "blocking"}], False),
        ([{"severity": "blocker"}], False),
    ],
)
def test_question_refinement_needs_input_classifies_by_existing_issue_severity(issues: list[dict], expected: bool) -> None:
    assert orchestrator_module._is_advisory_question_refinement_review(
        {"artifactContract": "research_question_refinement_artifact_v1"},
        {"verdict": "needs_input", "issues": issues},
        ["artifact_1"],
    ) is expected


def test_zero_specialist_plan_blocks_before_fake_provider() -> None:
    repository = FakeCollaborationRepository()
    requests: list[dict] = []

    result = _orchestrator(repository, requests).run(
        frozen_run=_run(), persisted_plan=_plan(), worker_id="worker_1", lease_epoch=1,
    )

    assert result == {"artifactIds": [], "terminal": True, "state": "blocked"}
    assert requests == []
    assert repository.units == []


def test_one_specialist_plan_calls_only_that_registered_agent_once() -> None:
    repository = FakeCollaborationRepository()
    requests: list[dict] = []

    result = _orchestrator(repository, requests).run(
        frozen_run=_run(), persisted_plan=_plan("paper_evidence_extraction"), worker_id="worker_1", lease_epoch=1,
    )

    assert [request["agentId"] for request in requests] == ["paper_evidence_extraction", "result_reliability_audit"]
    assert [fact["memoryId"] for fact in requests[0]["contextEnvelope"]["requiredFacts"]] == ["memory_goal"]
    assert "memoryRecords" not in requests[0]
    assert result["state"] == "succeeded"
    assert repository.handoffs == []


def test_three_specialists_execute_in_frozen_order_with_real_handoffs() -> None:
    repository = FakeCollaborationRepository()
    requests: list[dict] = []

    result = _orchestrator(repository, requests).run(
        frozen_run=_run(),
        persisted_plan=_plan("paper_evidence_extraction", "validation_design", "research_question_refinement"),
        worker_id="worker_1",
        lease_epoch=1,
    )

    assert [request["agentId"] for request in requests] == [
        "paper_evidence_extraction", "result_reliability_audit", "validation_design",
        "result_reliability_audit", "research_question_refinement", "result_reliability_audit",
    ]
    assert requests[2]["inputArtifactRefs"] == ["artifact_call_1"]
    assert requests[4]["inputArtifactRefs"] == ["artifact_call_3"]
    assert [(handoff["input_artifact_refs"], handoff["output_artifact_refs"]) for handoff in repository.handoffs] == [
        (["artifact_call_1"], ["artifact_call_1"]),
        (["artifact_call_3"], ["artifact_call_3"]),
    ]
    assert repository.events.index("unit:unit_1:succeeded") < repository.events.index("create:unit_2:artifact_call_1")
    assert repository.events.index("create:unit_2:artifact_call_1") < repository.events.index("handoff:unit_1:unit_2")
    assert repository.events.index("handoff:unit_1:unit_2") < repository.events.index("unit:unit_2:running")
    assert result == {"artifactIds": ["artifact_call_1", "artifact_call_3", "artifact_call_5"], "terminal": True, "state": "succeeded"}


def _review_question_refinement(
    monkeypatch: pytest.MonkeyPatch,
    *,
    review_result: dict,
) -> tuple[dict, FakeCollaborationRepository]:
    repository = FakeCollaborationRepository()
    source = repository.create_work_unit(
        run_id="run_1", owner_id="owner_1", workspace_id="workspace_1", worker_id="worker_1", fence_epoch=1,
        unit_key="question", parent_work_unit_id=None, agent_id="research_question_refinement", kind="specialist",
        title="Question refinement", stage_key=None, substage_key=None, model_profile="terra", objective="Refine question",
        visibility="main", input_memory_refs=[], input_artifact_refs=[], boundary_kind="initial",
    )
    monkeypatch.setattr(
        orchestrator_module,
        "_frozen_contract_spec",
        lambda raw: {"artifactContract": raw["artifactContract"]},
    )

    orchestrator = DurableMultiAgentOrchestrator(
        collaboration=repository,
        execute_agent_unit=lambda _request: review_result,
        persist_artifact=lambda _call_id, _result: "unused",
        controller_commands=FakeControllerCommands(),
    )
    result = orchestrator._review_specialist_artifacts(
        source_request={},
        source_spec={
            "unitKey": "question",
            "agentId": "research_question_refinement",
            "artifactContract": "research_question_refinement_artifact_v1",
            "inputMemoryRefs": [],
            "mandatoryReviewContract": {
                "reviewerAgentId": "result_reliability_audit",
                "artifactContract": "independent_result_review_v1",
            },
        },
        source_unit=source,
        source_summary="Question candidates created.",
        artifact_ids=["artifact_1"],
        run_id="run_1",
        owner_id="owner_1",
        workspace_id="workspace_1",
        worker_id="worker_1",
        lease_epoch=1,
    )
    return result, repository


def test_question_refinement_advisory_input_continues_with_supplement_preserved(monkeypatch: pytest.MonkeyPatch) -> None:
    result, repository = _review_question_refinement(
        monkeypatch,
        review_result={
            "verdict": "needs_input",
            "basis": "The candidate questions are usable; scope can be refined later.",
            "issues": [{"code": "scope_optional", "severity": "warning", "message": "Scope can be more specific."}],
            "missingInputs": ["preferred scope"],
        },
    )

    assert result == {"artifactIds": ["artifact_1"], "terminal": False, "state": "running"}
    assert repository.reviews[0]["verdict"] == "needs_input"
    assert repository.reviews[0]["continue_allowed"] is True
    assert repository.units[0]["state"] == "succeeded"


def test_question_refinement_missing_inputs_without_explicit_blocker_stays_advisory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, repository = _review_question_refinement(
        monkeypatch,
        review_result={
            "verdict": "needs_input",
            "basis": "The question is usable; extra scope detail is optional.",
            "issues": [],
            "missingInputs": ["preferred scope"],
        },
    )

    assert result == {"artifactIds": ["artifact_1"], "terminal": False, "state": "running"}
    assert repository.reviews[0]["continue_allowed"] is True
    assert repository.units[0]["state"] == "succeeded"


def test_question_refinement_low_severity_supplement_stays_advisory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, repository = _review_question_refinement(
        monkeypatch,
        review_result={
            "verdict": "needs_input",
            "basis": "A minor preference could improve focus but is not required.",
            "issues": [{"code": "preference_optional", "severity": "low", "message": "A preference may be added later."}],
            "missingInputs": ["preferred emphasis"],
        },
    )

    assert result == {"artifactIds": ["artifact_1"], "terminal": False, "state": "running"}
    assert repository.reviews[0]["continue_allowed"] is True
    assert repository.units[0]["state"] == "succeeded"


def test_question_refinement_required_input_is_non_blocking_after_user_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, repository = _review_question_refinement(
        monkeypatch,
        review_result={
            "verdict": "needs_input",
            "basis": "A required input is absent.",
            "issues": [{"code": "required_dataset", "severity": "blocking", "message": "Provide the dataset."}],
            "missingInputs": ["dataset"],
        },
    )

    assert result == {"artifactIds": ["artifact_1"], "terminal": False, "state": "running"}
    assert repository.reviews[0]["continue_allowed"] is True
    assert repository.units[0]["state"] == "succeeded"


@pytest.mark.parametrize(
    "verdict",
    ["needs_revision", "needs_input", "insufficient_materials", "human_decision_required"],
)
def test_post_confirmation_reviewer_verdicts_never_pause_durable_research(
    monkeypatch: pytest.MonkeyPatch, verdict: str,
) -> None:
    result, repository = _review_question_refinement(
        monkeypatch,
        review_result={
            "verdict": verdict,
            "basis": "Reviewer quality concern recorded after user confirmation.",
            "issues": [{"code": "quality_gap", "severity": "blocking", "message": "Internal quality gap."}],
            "missingInputs": ["internal_or_optional_detail"],
        },
    )

    if verdict == "human_decision_required":
        assert result == {"artifactIds": ["artifact_1"], "terminal": True, "state": "awaiting_human"}
        assert repository.reviews[0]["continue_allowed"] is False
    else:
        assert result == {"artifactIds": ["artifact_1"], "terminal": False, "state": "running"}
        assert repository.reviews[0]["continue_allowed"] is True
    assert repository.reviews[0]["verdict"] == verdict


def test_stop_before_next_unit_prevents_its_creation_and_fake_provider_callback() -> None:
    repository = FakeCollaborationRepository(stop_before_creation_key="unit_2")
    requests: list[dict] = []

    result = _orchestrator(repository, requests).run(
        frozen_run=_run(),
        persisted_plan=_plan("paper_evidence_extraction", "result_reliability_audit"),
        worker_id="worker_1",
        lease_epoch=1,
    )

    assert result["state"] == "stopped_by_user"
    assert [request["agentId"] for request in requests] == ["paper_evidence_extraction", "result_reliability_audit"]
    assert [unit["unit_key"] for unit in repository.units] == ["unit_1", "review:unit_1"]


def test_pending_policy_boundary_returns_awaiting_human_without_specialist_callback() -> None:
    repository = FakeCollaborationRepository(pause_before_unit_key="unit_2")
    requests: list[dict] = []

    result = _orchestrator(repository, requests).run(
        frozen_run=_run(),
        persisted_plan=_plan("paper_evidence_extraction", "validation_design"),
        worker_id="worker_1",
        lease_epoch=1,
    )

    assert result["state"] == "awaiting_human"
    assert [request["agentId"] for request in requests] == [
        "paper_evidence_extraction", "result_reliability_audit",
    ]


def test_existing_queued_handoff_reconciles_policy_before_specialist_replay() -> None:
    repository = FakeCollaborationRepository(stop_before_unit_key="unit_2")
    requests: list[dict] = []
    plan = _plan("paper_evidence_extraction", "validation_design")

    assert _orchestrator(repository, requests).run(
        frozen_run=_run(), persisted_plan=plan, worker_id="worker_1", lease_epoch=1,
    )["state"] == "stopped_by_user"
    assert next(unit for unit in repository.units if unit["unit_key"] == "unit_2")["state"] == "queued"

    repository.stop_before_unit_key = None
    repository.pause_before_unit_key = "unit_2"
    requests.clear()
    result = _orchestrator(repository, requests).run(
        frozen_run=_run(), persisted_plan=plan, worker_id="worker_1", lease_epoch=2,
    )

    assert result["state"] == "awaiting_human"
    assert requests == []


def test_unknown_external_result_never_replays_or_starts_next_agent() -> None:
    repository = FakeCollaborationRepository()
    calls = 0

    def unknown(_request: dict) -> dict:
        nonlocal calls
        calls += 1
        return {"state": "unknown", "summary": "transport interrupted"}

    orchestrator = DurableMultiAgentOrchestrator(
        collaboration=repository, execute_agent_unit=unknown, persist_artifact=lambda _call_id, _result: "unused",
    )
    plan = _plan("paper_evidence_extraction", "result_reliability_audit")

    assert orchestrator.run(frozen_run=_run(), persisted_plan=plan, worker_id="worker_1", lease_epoch=1)["state"] == "external_result_unknown"
    assert orchestrator.run(frozen_run=_run(), persisted_plan=plan, worker_id="worker_1", lease_epoch=1)["state"] == "external_result_unknown"
    assert calls == 1
    assert len(repository.calls) == 1


def test_unregistered_agent_blocks_before_fake_provider() -> None:
    repository = FakeCollaborationRepository()
    requests: list[dict] = []

    with pytest.raises(ValueError, match="not registered"):
        _orchestrator(repository, requests).run(
            frozen_run=_run(), persisted_plan=_plan("not_registered"), worker_id="worker_1", lease_epoch=1,
        )

    assert requests == []
    assert repository.units == []


@pytest.mark.parametrize("model_mode", ("", "unknown"))
def test_invalid_model_profile_blocks_before_external_action(model_mode: str) -> None:
    repository = FakeCollaborationRepository()
    requests: list[dict] = []
    plan = _plan("paper_evidence_extraction")
    plan["workUnits"][0]["modelMode"] = model_mode

    with pytest.raises(ValueError, match="modelMode"):
        _orchestrator(repository, requests).run(
            frozen_run=_run(), persisted_plan=plan, worker_id="worker_1", lease_epoch=1,
        )

    assert requests == []
    assert repository.calls == {}


def test_preflight_failure_does_not_start_external_action_or_call_provider() -> None:
    repository = FakeCollaborationRepository()
    provider_calls = 0

    def provider(_request: dict) -> dict:
        nonlocal provider_calls
        provider_calls += 1
        return {"state": "succeeded", "summary": "unused"}

    result = DurableMultiAgentOrchestrator(
        collaboration=repository,
        execute_agent_unit=provider,
        preflight_agent_unit=lambda _request: {"summary": "model configuration unavailable"},
        persist_artifact=lambda _call_id, _result: "unused",
    ).run(frozen_run=_run(), persisted_plan=_plan("paper_evidence_extraction"), worker_id="worker_1", lease_epoch=1)

    assert result["state"] == "failed"
    assert provider_calls == 0
    assert all(not call["external_action_started"] for call in repository.calls.values())


def test_strict_fake_rejects_handoff_when_downstream_input_does_not_match_output() -> None:
    repository = FakeCollaborationRepository()
    repository.units = [
        {"work_unit_id": "wu_1", "unit_key": "unit_1", "state": "succeeded", "output_artifact_refs": ["artifact_a"]},
        {"work_unit_id": "wu_2", "unit_key": "unit_2", "state": "queued", "parent_work_unit_id": "wu_1", "input_artifact_refs": ["artifact_wrong"]},
    ]

    with pytest.raises(AssertionError):
        repository.handoff(
            from_work_unit_id="wu_1", to_work_unit_id="wu_2",
            input_artifact_refs=["artifact_a"], output_artifact_refs=["artifact_a"],
        )


def _skill_plan(skill_id: str = "academic_source_retrieval_skill") -> dict:
    plan = _plan("paper_evidence_extraction")
    plan["workUnits"][0]["selectedSkillIds"] = [skill_id]
    definitions, bindings = _test_skill_authority(skill_id)
    plan["workUnits"][0]["selectedSkillDefinitions"] = definitions
    plan["workUnits"][0]["selectedSkillBindings"] = bindings
    return plan


def _test_skill_authority(*skill_ids: str) -> tuple[list[dict], list[dict]]:
    definitions = [{
        "skillId": skill_id,
        "definitionVersion": 1,
        "status": "ready",
        "enabled": True,
        "inputContract": {"status": "unknown", "acceptedInputRefs": []},
        "outputContract": {"status": "unknown", "artifactTypes": []},
        "judgmentSupport": {"deterministic": False},
    } for skill_id in skill_ids]
    bindings = [{
        "bindingId": f"bind:test:route:skill:{skill_id}",
        "bindingVersion": 1,
        "definitionId": "test:route",
        "definitionVersion": 1,
        "skillId": skill_id,
        "skillDefinitionVersion": 1,
        "enabled": True,
        "sourceAuthority": "skill_binding_source",
    } for skill_id in skill_ids]
    return definitions, bindings


def _frozen_skill_route_plan(*skill_ids: str) -> dict:
    plan = _plan("general_research_agent")
    plan["workUnits"][0].update(_frozen_work_unit_contract("general_research_agent"))
    definitions, bindings = _test_skill_authority(*skill_ids)
    plan["workUnits"][0].update({
        "kind": "skill_route",
        "objective": "按冻结顺序执行已授权的研究能力",
        "selectedSkillIds": list(skill_ids),
        "selectedSkillDefinitions": definitions,
        "selectedSkillBindings": bindings,
        "modelMode": "terra",
    })
    return plan


@pytest.fixture(scope="module")
def materials_skill_route_plan() -> dict:
    creation_plan = resolve_creation_plan_capabilities({
        "goalSummary": "search materials",
        "selectedCapabilities": [{
            "kind": "skill",
            "id": "materials_mp_constraint_search",
            "requestedCapability": "materials_mp_constraint_search",
            "skillIds": ["materials_mp_constraint_search"],
        }],
    })
    plan = _plan("general_research_agent")
    plan["workUnits"] = compile_durable_work_units({
        "input_snapshot": {
            "goalSummary": "search materials",
            "creationPlan": creation_plan,
        },
    })
    return plan


def _successful_skill_review(request: dict) -> dict:
    if request.get("phase") == "review_artifact":
        return {
            "verdict": "usable", "basis": "Frozen Skill output is usable.",
            "issues": [], "missingInputs": [],
        }
    return {
        "state": "succeeded", "summary": "reviewed frozen Skill output",
        "artifactPayload": {"reviewed": True},
    }


def test_work_specs_preserves_compiled_frozen_skill_authority(
    materials_skill_route_plan: dict,
) -> None:
    compiled = materials_skill_route_plan["workUnits"][0]

    projected = orchestrator_module._work_specs([deepcopy(compiled)])[0]

    assert projected["selectedSkillDefinitions"] == compiled["selectedSkillDefinitions"]
    assert projected["selectedSkillBindings"] == compiled["selectedSkillBindings"]
    assert projected["selectedSkillDefinitions"] is not compiled["selectedSkillDefinitions"]
    assert projected["selectedSkillBindings"] is not compiled["selectedSkillBindings"]


def test_real_materials_v2_request_round_trips_under_persistence_limit(
    materials_skill_route_plan: dict,
) -> None:
    unit = materials_skill_route_plan["workUnits"][0]
    definition = unit["selectedSkillDefinitions"][0]
    binding = unit["selectedSkillBindings"][0]
    request = {
        "skillId": definition["skillId"],
        "definitionVersion": definition["definitionVersion"],
        "snapshotDefinition": definition,
        "sourceBindingRefs": [{
            "bindingId": binding["bindingId"], "bindingVersion": binding["bindingVersion"],
        }],
        "selectedSkillBindings": [binding],
        "inputMemoryRefs": [],
        "inputArtifactRefs": [],
        "parameters": {"goal": "search materials"},
        "reason": "need material candidates",
        "idempotencyKey": "materials-1",
    }
    snapshot = "skill-request.v2:" + json.dumps(
        request, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
    )

    assert len(snapshot) < 25_000
    assert orchestrator_module._persisted_skill_request(snapshot) == request


@pytest.mark.parametrize("prefix", ("skill-request.v1:", "skill-request.v2:"))
def test_persisted_skill_request_snapshot_is_hidden_from_projection(prefix: str) -> None:
    assert collaboration_repository_module._safe_projection_text(
        prefix + '{"snapshotDefinition":{"secret":"internal"}}', "safe",
    ) == "safe"


def test_ordered_skill_route_rejects_frozen_adapter_drift_before_actual_adapter(
    materials_skill_route_plan: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = deepcopy(materials_skill_route_plan)
    definition = plan["workUnits"][0]["selectedSkillDefinitions"][0]
    definition["sourceContract"]["runtime_contract"]["adapter_id"] = (
        "python_adapter:retrieval_matrix_generation"
    )
    adapter_calls = 0

    def adapter(**_kwargs):
        nonlocal adapter_calls
        adapter_calls += 1
        return {"state": "succeeded"}

    monkeypatch.setattr(durable_skill_runtime, "run_python_skill_adapter", adapter)
    result = DurableMultiAgentOrchestrator(
        collaboration=FakeCollaborationRepository(),
        execute_agent_unit=_successful_skill_review,
        execute_skill_unit=durable_skill_runtime.execute_durable_skill,
        persist_artifact=lambda call_id, _result: f"artifact_{call_id}",
    ).run(
        frozen_run=_run(), persisted_plan=plan, worker_id="worker_1", lease_epoch=1,
    )

    assert result["state"] == "failed"
    assert adapter_calls == 0


def test_ordered_skill_route_rejects_frozen_binding_drift_without_reselection(
    materials_skill_route_plan: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = deepcopy(materials_skill_route_plan)
    plan["workUnits"][0]["selectedSkillBindings"][0]["bindingVersion"] += 1
    adapter_calls = 0

    def adapter(**_kwargs):
        nonlocal adapter_calls
        adapter_calls += 1
        return {"state": "succeeded"}

    monkeypatch.setattr(durable_skill_runtime, "run_python_skill_adapter", adapter)
    result = DurableMultiAgentOrchestrator(
        collaboration=FakeCollaborationRepository(),
        execute_agent_unit=_successful_skill_review,
        execute_skill_unit=durable_skill_runtime.execute_durable_skill,
        persist_artifact=lambda call_id, _result: f"artifact_{call_id}",
    ).run(
        frozen_run=_run(), persisted_plan=plan, worker_id="worker_1", lease_epoch=1,
    )

    assert result["state"] == "failed"
    assert adapter_calls == 0


def test_ordered_materials_route_delivers_exact_frozen_authority_to_consumer(
    materials_skill_route_plan: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = deepcopy(materials_skill_route_plan)
    adapter_requests: list[dict] = []

    def adapter(**kwargs):
        adapter_requests.append(deepcopy(kwargs))
        return {"state": "succeeded"}

    monkeypatch.setattr(durable_skill_runtime, "run_python_skill_adapter", adapter)
    monkeypatch.setattr(
        durable_skill_runtime, "_matches_external_adapter_formal_schema", lambda *_args: True,
    )
    monkeypatch.setattr(
        durable_skill_runtime, "_has_external_adapter_output_evidence", lambda *_args: True,
    )
    skill_payloads: list[dict] = []

    def execute(payload: dict) -> dict:
        skill_payloads.append(deepcopy(payload))
        return durable_skill_runtime.execute_durable_skill(payload)

    result = DurableMultiAgentOrchestrator(
        collaboration=FakeCollaborationRepository(),
        execute_agent_unit=_successful_skill_review,
        execute_skill_unit=execute,
        persist_artifact=lambda call_id, _result: f"artifact_{call_id}",
    ).run(
        frozen_run=_run(), persisted_plan=plan, worker_id="worker_1", lease_epoch=1,
    )

    definition = plan["workUnits"][0]["selectedSkillDefinitions"][0]
    binding = plan["workUnits"][0]["selectedSkillBindings"][0]
    assert result["state"] == "succeeded"
    assert len(adapter_requests) == 1
    assert skill_payloads[0]["snapshotDefinition"] == definition
    assert skill_payloads[0]["definitionVersion"] == definition["definitionVersion"]
    assert skill_payloads[0]["selectedSkillBindings"] == [binding]
    assert skill_payloads[0]["sourceBindingRefs"] == [{
        "bindingId": binding["bindingId"], "bindingVersion": binding["bindingVersion"],
    }]


def test_specialist_requested_skill_uses_frozen_authority_not_live_selection(
    materials_skill_route_plan: dict,
) -> None:
    plan = deepcopy(materials_skill_route_plan)
    plan["workUnits"][0]["kind"] = "specialist"
    skill_payloads: list[dict] = []

    def agent(request: dict) -> dict:
        if request.get("phase"):
            return _successful_skill_review(request)
        return {
            "state": "succeeded", "summary": "request frozen materials route",
            "skillRequest": {
                "skillId": "materials_mp_constraint_search",
                "inputMemoryRefs": [], "inputArtifactRefs": [],
                "parameters": {"goal": "search materials"},
                "reason": "need material candidates", "idempotencyKey": "materials-1",
            },
        }

    result = DurableMultiAgentOrchestrator(
        collaboration=FakeCollaborationRepository(),
        execute_agent_unit=agent,
        execute_skill_unit=lambda payload: skill_payloads.append(deepcopy(payload)) or {
            "state": "succeeded", "summary": "materials", "artifactPayload": {},
        },
        persist_artifact=lambda call_id, _result: f"artifact_{call_id}",
    ).run(
        frozen_run=_run(), persisted_plan=plan, worker_id="worker_1", lease_epoch=1,
    )

    assert result["state"] == "succeeded"
    assert skill_payloads[0]["snapshotDefinition"] == (
        plan["workUnits"][0]["selectedSkillDefinitions"][0]
    )
    assert skill_payloads[0]["selectedSkillBindings"] == [
        plan["workUnits"][0]["selectedSkillBindings"][0]
    ]


def _with_downstream_unit(plan: dict) -> dict:
    downstream = deepcopy(_plan("validation_design")["workUnits"][0])
    downstream.update(
        unitKey="unit_2", parentUnitKey="unit_1", inputMemoryRefs=[], inputArtifactRefs=[],
    )
    plan["workUnits"].append(downstream)
    return plan


def test_frozen_skill_route_runs_two_skills_in_order_then_one_review(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = FakeCollaborationRepository()
    skill_requests: list[dict] = []
    review_requests: list[dict] = []
    monkeypatch.setattr(
        "app.execution.durable_skill_runtime.resolve_registered_durable_skill",
        lambda skill_id: {"skillId": skill_id, "durableExecutable": True},
    )

    def skill(request: dict) -> dict:
        skill_requests.append(deepcopy(request))
        return {"state": "succeeded", "summary": request["skillId"], "artifactPayload": {}}

    def review(request: dict) -> dict:
        review_requests.append(deepcopy(request))
        if request.get("phase") == "review_artifact":
            return {"verdict": "usable", "basis": "Skill outputs are usable.", "issues": [], "missingInputs": []}
        return {"state": "succeeded", "summary": "reviewed", "artifactPayload": {}}

    result = DurableMultiAgentOrchestrator(
        collaboration=repository,
        execute_agent_unit=review,
        execute_skill_unit=skill,
        persist_artifact=lambda call_id, _result: f"artifact_{call_id}",
    ).run(
        frozen_run=_run(),
        persisted_plan=_frozen_skill_route_plan("skill_a", "skill_b"),
        worker_id="worker_1",
        lease_epoch=1,
    )

    assert [request["skillId"] for request in skill_requests] == ["skill_a", "skill_b"]
    assert all(request["logicalProviderId"] == "openai_primary" for request in skill_requests)
    assert all(request["modelProfile"] == "terra" for request in skill_requests)
    assert all(request["frozenGoal"] == "check the research question" for request in skill_requests)
    assert all(request["frozenConstraints"] == ["confirmed materials only"] for request in skill_requests)
    assert [item["phase"] for item in review_requests] == ["review_skill_result", "review_artifact"]
    assert review_requests[0]["phase"] == "review_skill_result"
    assert review_requests[0]["skillResult"]["artifactIds"] == ["artifact_call_1", "artifact_call_2"]
    assert review_requests[1]["agentId"] == "result_reliability_audit"
    assert review_requests[1]["inputArtifactRefs"] == ["artifact_call_1", "artifact_call_2", "artifact_call_3"]
    assert [call["invocation_kind"] for call in repository.calls.values()] == ["skill", "skill", "review", "review"]
    assert result == {
        "artifactIds": ["artifact_call_1", "artifact_call_2", "artifact_call_3"],
        "terminal": True,
        "state": "succeeded",
    }


def test_frozen_skill_route_failure_never_runs_review_or_returns_success_artifacts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = FakeCollaborationRepository()
    calls: list[str] = []
    persisted_states: list[str | None] = []
    monkeypatch.setattr(
        "app.execution.durable_skill_runtime.resolve_registered_durable_skill",
        lambda skill_id: {"skillId": skill_id, "durableExecutable": True},
    )

    def skill(request: dict) -> dict:
        calls.append(request["skillId"])
        if request["skillId"] == "skill_b":
            return {"state": "failed", "summary": "failed", "failureClassification": "provider_configuration"}
        return {"state": "succeeded", "summary": "first", "artifactPayload": {}}

    result = DurableMultiAgentOrchestrator(
        collaboration=repository,
        execute_agent_unit=lambda _request: pytest.fail("review must not run"),
        execute_skill_unit=skill,
        persist_artifact=lambda call_id, result: (
            persisted_states.append(result.get("artifactState")) or f"artifact_{call_id}"
        ),
    ).run(
        frozen_run=_run(),
        persisted_plan=_frozen_skill_route_plan("skill_a", "skill_b", "skill_c"),
        worker_id="worker_1",
        lease_epoch=1,
    )

    assert calls == ["skill_a", "skill_b"]
    assert persisted_states == ["partial_success"]
    assert repository.calls["call_2"]["failure_classification"] == "provider_configuration"
    assert result == {"artifactIds": [], "terminal": True, "state": "failed"}
    assert [call["invocation_kind"] for call in repository.calls.values()] == ["skill", "skill"]


def test_frozen_skill_route_unknown_is_not_replayed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = FakeCollaborationRepository()
    calls = 0
    monkeypatch.setattr(
        "app.execution.durable_skill_runtime.resolve_registered_durable_skill",
        lambda skill_id: {"skillId": skill_id, "durableExecutable": True},
    )

    def unknown(_request: dict) -> dict:
        nonlocal calls
        calls += 1
        return {"state": "unknown", "summary": "result unknown"}

    orchestrator = DurableMultiAgentOrchestrator(
        collaboration=repository,
        execute_agent_unit=lambda _request: pytest.fail("review must not run"),
        execute_skill_unit=unknown,
        persist_artifact=lambda _call_id, _result: pytest.fail("unknown result must not persist"),
    )
    plan = _frozen_skill_route_plan("skill_a", "skill_b")

    assert orchestrator.run(
        frozen_run=_run(), persisted_plan=plan, worker_id="worker_1", lease_epoch=1,
    )["state"] == "external_result_unknown"
    assert orchestrator.run(
        frozen_run=_run(), persisted_plan=plan, worker_id="worker_1", lease_epoch=1,
    )["state"] == "external_result_unknown"
    assert calls == 1
    assert len(repository.calls) == 1


def test_frozen_skill_route_recovers_persisted_artifact_without_second_skill_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RecoveryRepository(FakeCollaborationRepository):
        def create_skill_call(self, **kwargs):
            call = super().create_skill_call(**kwargs)
            call.update(
                state="running", external_action_started=True,
                output_artifact_refs=["artifact_recovered"], result_summary="persisted before interruption",
            )
            return call

    repository = RecoveryRepository()
    skill_calls = 0
    monkeypatch.setattr(
        "app.execution.durable_skill_runtime.resolve_registered_durable_skill",
        lambda skill_id: {"skillId": skill_id, "durableExecutable": True},
    )

    result = DurableMultiAgentOrchestrator(
        collaboration=repository,
        execute_agent_unit=lambda request: (
            {"verdict": "usable", "basis": "Recovered Skill output is usable.", "issues": [], "missingInputs": []}
            if request.get("phase") == "review_artifact"
            else {"state": "succeeded", "summary": request.get("phase", "reviewed"), "artifactPayload": {}}
        ),
        execute_skill_unit=lambda _request: pytest.fail("recovery must not call the Skill runner"),
        persist_artifact=lambda call_id, _result: f"artifact_{call_id}",
    ).run(
        frozen_run=_run(), persisted_plan=_frozen_skill_route_plan("skill_a"), worker_id="worker_1", lease_epoch=1,
    )

    assert skill_calls == 0
    assert result["state"] == "succeeded"
    assert "begin:call_1" not in repository.events
    assert repository.calls["call_1"]["state"] == "succeeded"


def test_requested_skill_recovers_persisted_artifact_without_second_skill_call() -> None:
    class RecoveryRepository(FakeCollaborationRepository):
        def create_skill_call(self, **kwargs):
            call = super().create_skill_call(**kwargs)
            call.update(
                state="running", external_action_started=True,
                output_artifact_refs=["artifact_recovered"], result_summary="persisted before interruption",
            )
            return call

    repository = RecoveryRepository()

    def agent(request: dict) -> dict:
        if request.get("phase") == "review_artifact":
            return {"verdict": "usable", "basis": "Recovered Skill output is usable.", "issues": [], "missingInputs": []}
        if request.get("phase") == "review_skill_result":
            assert request["skillResult"]["artifactIds"] == ["artifact_recovered"]
            return {"state": "succeeded", "summary": "reviewed"}
        return {
            "state": "succeeded", "summary": "need sources",
            "skillRequest": {
                "skillId": "academic_source_retrieval_skill", "inputMemoryRefs": ["memory_goal"],
                "inputArtifactRefs": [], "parameters": {}, "reason": "need sources", "idempotencyKey": "one",
            },
        }

    result = DurableMultiAgentOrchestrator(
        collaboration=repository, execute_agent_unit=agent,
        execute_skill_unit=lambda _request: pytest.fail("recovery must not call the Skill runner"),
        persist_artifact=lambda call_id, _result: f"artifact_{call_id}",
    ).run(frozen_run=_run(), persisted_plan=_skill_plan(), worker_id="worker_1", lease_epoch=1)

    assert result["state"] == "succeeded"
    assert "begin:call_2" not in repository.events
    assert repository.calls["call_2"]["state"] == "succeeded"


def test_recovery_of_succeeded_empty_parent_reuses_persisted_requested_skill_before_review() -> None:
    repository = FakeCollaborationRepository()
    plan = _skill_plan()
    spec = plan["workUnits"][0]
    unit = repository.create_work_unit(
        run_id="run_1", owner_id="owner_1", workspace_id="workspace_1", worker_id="T1-worker",
        fence_epoch=1, unit_key=spec["unitKey"], parent_work_unit_id=None, agent_id=spec["agentId"],
        kind=spec["kind"], title=spec["unitKey"], stage_key=None, substage_key=None,
        model_profile=spec["modelMode"], objective=spec["objective"], visibility="main",
        input_memory_refs=spec["inputMemoryRefs"], input_artifact_refs=[], boundary_kind="initial_dispatch",
    )
    unit["state"] = "running"
    request = DurableMultiAgentOrchestrator(
        collaboration=repository, execute_agent_unit=lambda _request: {}, persist_artifact=lambda _call_id, _result: "unused",
    )._request_spec(
        spec=spec, unit=unit, frozen_run=_run(), persisted_plan=plan,
        run_id="run_1", owner_id="owner_1", workspace_id="workspace_1", controller_inputs=[],
    )
    parent = repository.create_capability_invocation(
        run_id="run_1", owner_id="owner_1", workspace_id="workspace_1", worker_id="T1-worker",
        fence_epoch=1, work_unit_id=unit["work_unit_id"], requesting_agent_id=spec["agentId"],
        invocation_kind="agent_brain", idempotency_key=orchestrator_module._fingerprint({
            "runId": "run_1", "unitKey": spec["unitKey"], "kind": "agent_brain",
        }), request_fingerprint=orchestrator_module._fingerprint(request),
        input_memory_refs=request["inputMemoryRefs"], input_artifact_refs=request["inputArtifactRefs"],
    )
    parent.update(state="succeeded", external_action_started=True, result_summary="requested sources", output_artifact_refs=[])
    skill = repository.create_skill_call(
        run_id="run_1", owner_id="owner_1", workspace_id="workspace_1", worker_id="T1-worker",
        fence_epoch=1, work_unit_id=unit["work_unit_id"], requesting_agent_id=spec["agentId"],
        skill_id="academic_source_retrieval_skill", idempotency_key="persisted-requested-skill",
        request_fingerprint="persisted-requested-skill-fingerprint", input_memory_refs=["memory_goal"],
        input_artifact_refs=[],
    )
    skill.update(
        state="running", external_action_started=True,
        result_summary="skill-request.v1:" + json.dumps({
            "skillId": "academic_source_retrieval_skill",
            "inputMemoryRefs": ["memory_goal"],
            "inputArtifactRefs": [],
            "parameters": {},
            "reason": spec["objective"],
            "idempotencyKey": "legacy-completed-request",
        }, ensure_ascii=True, sort_keys=True, separators=(",", ":")),
        output_artifact_refs=["artifact_persisted_skill"],
    )
    review_requests: list[dict] = []

    def agent(request: dict) -> dict:
        if request.get("phase") == "review_artifact":
            review_requests.append(deepcopy(request))
            return {"verdict": "usable", "basis": "Persisted Skill output is usable.", "issues": [], "missingInputs": []}
        if request.get("phase") != "review_skill_result":
            pytest.fail("recovery must not call the succeeded parent Agent")
        review_requests.append(deepcopy(request))
        return {"state": "succeeded", "summary": "reviewed persisted Skill", "artifactPayload": {"reviewed": True}}

    result = DurableMultiAgentOrchestrator(
        collaboration=repository, execute_agent_unit=agent,
        execute_skill_unit=lambda _request: pytest.fail("recovery must not call the persisted Skill"),
        persist_artifact=lambda call_id, _result: f"artifact_{call_id}",
    ).run(frozen_run=_run(), persisted_plan=plan, worker_id="T2-worker", lease_epoch=2)

    assert result == {
        "artifactIds": ["artifact_persisted_skill", "artifact_call_3"],
        "terminal": True,
        "state": "succeeded",
    }
    assert [request["phase"] for request in review_requests] == ["review_skill_result", "review_artifact"]
    assert review_requests[0]["skillResult"] == {
        "skillId": "academic_source_retrieval_skill", "skillCallId": skill["call_id"],
        "artifactIds": ["artifact_persisted_skill"], "reason": spec["objective"],
    }
    assert [call["invocation_kind"] for call in repository.calls.values()] == ["agent_brain", "skill", "review", "review"]
    assert "begin:call_1" not in repository.events
    assert "begin:call_2" not in repository.events


def test_requested_skill_recovers_atomic_queued_request_after_crash_before_begin() -> None:
    class SimulatedCrash(RuntimeError):
        pass

    class CrashAfterAtomicPersistRepository(FakeCollaborationRepository):
        crash_after_persist = True

        def finish_agent_and_create_skill_call(self, **kwargs):
            persisted = super().finish_agent_and_create_skill_call(**kwargs)
            if self.crash_after_persist:
                self.crash_after_persist = False
                raise SimulatedCrash("process stopped after atomic commit")
            return persisted

    repository = CrashAfterAtomicPersistRepository()
    parent_calls = 0
    skill_calls = 0
    same_agent_reviews = 0

    def agent(request: dict) -> dict:
        nonlocal parent_calls, same_agent_reviews
        if request.get("phase") == "review_artifact":
            return {"verdict": "usable", "basis": "Recovered output is usable.", "issues": [], "missingInputs": []}
        if request.get("phase") == "review_skill_result":
            same_agent_reviews += 1
            return {"state": "succeeded", "summary": "synthesized", "artifactPayload": {}}
        parent_calls += 1
        return {
            "state": "succeeded", "summary": "need sources",
            "skillRequest": {
                "skillId": "academic_source_retrieval_skill", "inputMemoryRefs": ["memory_goal"],
                "inputArtifactRefs": [], "parameters": {"user_query": "bounded"},
                "reason": "need sources", "idempotencyKey": "one",
            },
        }

    def skill(_request: dict) -> dict:
        nonlocal skill_calls
        skill_calls += 1
        return {"state": "succeeded", "summary": "sources", "artifactPayload": {}}

    orchestrator = DurableMultiAgentOrchestrator(
        collaboration=repository, execute_agent_unit=agent, execute_skill_unit=skill,
        persist_artifact=lambda call_id, _result: f"artifact_{call_id}",
    )
    with pytest.raises(SimulatedCrash):
        orchestrator.run(frozen_run=_run(), persisted_plan=_skill_plan(), worker_id="T1-worker", lease_epoch=1)

    skill_call = next(call for call in repository.calls.values() if call["invocation_kind"] == "skill")
    assert skill_call["state"] == "queued"
    assert skill_call["external_action_started"] is False
    assert skill_calls == 0

    result = orchestrator.run(
        frozen_run=_run(), persisted_plan=_skill_plan(), worker_id="T2-worker", lease_epoch=2,
    )

    assert result["state"] == "succeeded"
    assert parent_calls == 1
    assert skill_calls == 1
    assert same_agent_reviews == 1


def test_cross_lease_v2_recovery_fails_closed_on_live_binding_drift(
    materials_skill_route_plan: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SimulatedCrash(RuntimeError):
        pass

    class CrashAfterAtomicPersistRepository(FakeCollaborationRepository):
        crash_after_persist = True

        def finish_agent_and_create_skill_call(self, **kwargs):
            persisted = super().finish_agent_and_create_skill_call(**kwargs)
            if self.crash_after_persist:
                self.crash_after_persist = False
                raise SimulatedCrash("process stopped after atomic commit")
            return persisted

    plan = deepcopy(materials_skill_route_plan)
    plan["workUnits"][0]["kind"] = "specialist"
    repository = CrashAfterAtomicPersistRepository()
    adapter_calls = 0

    def agent(request: dict) -> dict:
        if request.get("phase"):
            return _successful_skill_review(request)
        return {
            "state": "succeeded", "summary": "request frozen materials route",
            "skillRequest": {
                "skillId": "materials_mp_constraint_search",
                "inputMemoryRefs": [], "inputArtifactRefs": [],
                "parameters": {"goal": "search materials"},
                "reason": "need material candidates", "idempotencyKey": "materials-1",
            },
        }

    def adapter(**_kwargs):
        nonlocal adapter_calls
        adapter_calls += 1
        return {"state": "succeeded"}

    monkeypatch.setattr(durable_skill_runtime, "run_python_skill_adapter", adapter)
    orchestrator = DurableMultiAgentOrchestrator(
        collaboration=repository,
        execute_agent_unit=agent,
        execute_skill_unit=durable_skill_runtime.execute_durable_skill,
        persist_artifact=lambda call_id, _result: f"artifact_{call_id}",
    )
    with pytest.raises(SimulatedCrash):
        orchestrator.run(
            frozen_run=_run(), persisted_plan=plan, worker_id="T1-worker", lease_epoch=1,
        )

    skill_call = next(call for call in repository.calls.values() if call["invocation_kind"] == "skill")
    persisted = orchestrator_module._persisted_skill_request(skill_call["result_summary"])
    assert persisted["snapshotDefinition"] == plan["workUnits"][0]["selectedSkillDefinitions"][0]
    assert persisted["selectedSkillBindings"] == plan["workUnits"][0]["selectedSkillBindings"]

    monkeypatch.setattr(
        "app.modules.skill_binding_source.load_skill_binding_source_rows", lambda: [],
    )
    result = orchestrator.run(
        frozen_run=_run(), persisted_plan=plan, worker_id="T2-worker", lease_epoch=2,
    )

    assert result["state"] == "failed"
    assert adapter_calls == 0
    assert len([call for call in repository.calls.values() if call["invocation_kind"] == "skill"]) == 1


def test_legacy_v1_request_without_frozen_plan_authority_fails_safe() -> None:
    legacy = "skill-request.v1:" + json.dumps({
        "skillId": "materials_mp_constraint_search",
        "inputMemoryRefs": [], "inputArtifactRefs": [], "parameters": {},
        "reason": "legacy request", "idempotencyKey": "legacy-1",
    }, ensure_ascii=True, sort_keys=True, separators=(",", ":"))

    with pytest.raises(DurableExecutionError, match="persisted_skill_authority_unavailable"):
        orchestrator_module._persisted_skill_request(
            legacy,
            frozen_request={"selectedSkillIds": ["materials_mp_constraint_search"]},
            require_frozen_authority=True,
        )


def test_legacy_queued_recovery_durably_fails_and_crash_gap_converges() -> None:
    class CrashAfterQueuedFailureRepository(FakeCollaborationRepository):
        crash_once = True

        def fail_unstarted_capability_invocation(self, **kwargs):
            failed = super().fail_unstarted_capability_invocation(**kwargs)
            if self.crash_once:
                self.crash_once = False
                raise RuntimeError("process stopped after durable queued failure")
            return failed

    repository = CrashAfterQueuedFailureRepository()
    plan = _skill_plan()
    skill_calls = 0

    def agent(request: dict) -> dict:
        if request.get("phase"):
            return _successful_skill_review(request)
        return {
            "state": "succeeded", "summary": "need sources",
            "skillRequest": {
                "skillId": "academic_source_retrieval_skill",
                "inputMemoryRefs": ["memory_goal"], "inputArtifactRefs": [],
                "parameters": {}, "reason": "need sources", "idempotencyKey": "one",
            },
        }

    def skill(_request: dict) -> dict:
        nonlocal skill_calls
        skill_calls += 1
        return {"state": "succeeded", "summary": "unexpected", "artifactPayload": {}}

    orchestrator = DurableMultiAgentOrchestrator(
        collaboration=repository, execute_agent_unit=agent, execute_skill_unit=skill,
        persist_artifact=lambda call_id, _result: f"artifact_{call_id}",
    )
    original_finish = repository.finish_agent_and_create_skill_call

    def persist_then_stop(**kwargs):
        persisted = original_finish(**kwargs)
        repository.finish_agent_and_create_skill_call = original_finish
        raise RuntimeError("process stopped after request persistence")

    repository.finish_agent_and_create_skill_call = persist_then_stop
    with pytest.raises(RuntimeError, match="request persistence"):
        orchestrator.run(frozen_run=_run(), persisted_plan=plan, worker_id="T1-worker", lease_epoch=1)

    skill_call = next(call for call in repository.calls.values() if call["invocation_kind"] == "skill")
    skill_call["result_summary"] = "skill-request.v1:" + json.dumps({
        "skillId": "unselected_legacy_skill", "inputMemoryRefs": [], "inputArtifactRefs": [],
        "parameters": {}, "reason": "legacy request", "idempotencyKey": "legacy-1",
    }, ensure_ascii=True, sort_keys=True, separators=(",", ":"))

    with pytest.raises(RuntimeError, match="durable queued failure"):
        orchestrator.run(frozen_run=_run(), persisted_plan=plan, worker_id="T2-worker", lease_epoch=2)
    assert skill_call["state"] == "failed"
    assert skill_call["failure_classification"] == "skill_runtime_unavailable"
    assert skill_call["external_action_started"] is False
    assert skill_calls == 0
    assert repository.units[0]["state"] == "running"

    result = orchestrator.run(
        frozen_run=_run(), persisted_plan=plan, worker_id="T3-worker", lease_epoch=3,
    )

    assert result["state"] == "failed"
    assert repository.units[0]["state"] == "failed"
    assert repository.events.count(f"fail-unstarted:{skill_call['call_id']}") == 1
    assert skill_calls == 0


def test_v2_plan_authority_mismatch_durably_fails_before_dispatch() -> None:
    repository = FakeCollaborationRepository()
    plan = _skill_plan()
    orchestrator = DurableMultiAgentOrchestrator(
        collaboration=repository,
        execute_agent_unit=lambda _request: {},
        execute_skill_unit=lambda _request: pytest.fail("authority mismatch must not dispatch"),
        persist_artifact=lambda _call_id, _result: "unused",
    )
    spec = plan["workUnits"][0]
    unit = repository.create_work_unit(
        run_id="run_1", owner_id="owner_1", workspace_id="workspace_1", worker_id="T1-worker",
        fence_epoch=1, unit_key=spec["unitKey"], parent_work_unit_id=None, agent_id=spec["agentId"],
        kind=spec["kind"], title=spec["unitKey"], stage_key=None, substage_key=None,
        model_profile=spec["modelMode"], objective=spec["objective"], visibility="main",
        input_memory_refs=spec["inputMemoryRefs"], input_artifact_refs=[], boundary_kind="initial_dispatch",
    )
    unit["state"] = "running"
    request = orchestrator._request_spec(
        spec=spec, unit=unit, frozen_run=_run(), persisted_plan=plan,
        run_id="run_1", owner_id="owner_1", workspace_id="workspace_1", controller_inputs=[],
    )
    parent = repository.create_capability_invocation(
        run_id="run_1", owner_id="owner_1", workspace_id="workspace_1", worker_id="T1-worker",
        fence_epoch=1, work_unit_id=unit["work_unit_id"], requesting_agent_id=spec["agentId"],
        invocation_kind="agent_brain", idempotency_key="parent", request_fingerprint="parent-fp",
        input_memory_refs=request["inputMemoryRefs"], input_artifact_refs=[],
    )
    parent.update(state="succeeded", external_action_started=True, result_summary="requested", output_artifact_refs=[])
    definition = deepcopy(plan["workUnits"][0]["selectedSkillDefinitions"][0])
    binding = deepcopy(plan["workUnits"][0]["selectedSkillBindings"][0])
    binding["bindingVersion"] = 2
    persisted_request = {
        "skillId": definition["skillId"], "definitionVersion": definition["definitionVersion"],
        "snapshotDefinition": definition,
        "sourceBindingRefs": [{"bindingId": binding["bindingId"], "bindingVersion": 2}],
        "selectedSkillBindings": [binding], "inputMemoryRefs": ["memory_goal"],
        "inputArtifactRefs": [], "parameters": {}, "reason": "mismatch", "idempotencyKey": "one",
    }
    skill_call = repository.create_skill_call(
        run_id="run_1", owner_id="owner_1", workspace_id="workspace_1", worker_id="T1-worker",
        fence_epoch=1, work_unit_id=unit["work_unit_id"], requesting_agent_id=spec["agentId"],
        skill_id=definition["skillId"], idempotency_key="skill", request_fingerprint="skill-fp",
        input_memory_refs=["memory_goal"], input_artifact_refs=[],
    )
    skill_call["result_summary"] = "skill-request.v2:" + json.dumps(
        persisted_request, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
    )

    result = orchestrator._recover_requested_skill(
        request=request, unit_id=unit["work_unit_id"], run_id="run_1", owner_id="owner_1",
        workspace_id="workspace_1", worker_id="T2-worker", lease_epoch=2, skill_call=skill_call,
    )

    assert result["state"] == "failed"
    assert skill_call["state"] == "failed"
    assert skill_call["failure_classification"] == "skill_runtime_unavailable"
    assert skill_call["external_action_started"] is False


def test_adopted_confirmed_skill_review_artifact_is_closed_without_second_provider_call() -> None:
    class SimulatedCrash(RuntimeError):
        pass

    repository = FakeCollaborationRepository()
    parent_calls = 0
    skill_calls = 0
    same_agent_reviews = 0
    independent_reviews = 0
    crash_once = True

    def agent(request: dict) -> dict:
        nonlocal parent_calls, same_agent_reviews, independent_reviews
        if request.get("phase") == "review_artifact":
            independent_reviews += 1
            return {"verdict": "usable", "basis": "Persisted synthesis is usable.", "issues": [], "missingInputs": []}
        if request.get("phase") == "review_skill_result":
            same_agent_reviews += 1
            return {"state": "succeeded", "summary": "persisted synthesis", "artifactPayload": {}}
        parent_calls += 1
        return {
            "state": "succeeded", "summary": "need sources",
            "skillRequest": {
                "skillId": "academic_source_retrieval_skill", "inputMemoryRefs": ["memory_goal"],
                "inputArtifactRefs": [], "parameters": {}, "reason": "need sources", "idempotencyKey": "one",
            },
        }

    def skill(_request: dict) -> dict:
        nonlocal skill_calls
        skill_calls += 1
        return {"state": "succeeded", "summary": "sources", "artifactPayload": {}}

    def persist(call_id: str, result: dict) -> str:
        nonlocal crash_once
        artifact_id = f"artifact_{call_id}"
        call = repository.calls[call_id]
        if call["invocation_kind"] == "review" and call["requesting_agent_id"] != "result_reliability_audit" and crash_once:
            call.update(output_artifact_refs=[artifact_id], result_summary=result["summary"])
            crash_once = False
            raise SimulatedCrash("confirmed review artifact persisted before invocation finish")
        return artifact_id

    orchestrator = DurableMultiAgentOrchestrator(
        collaboration=repository, execute_agent_unit=agent, execute_skill_unit=skill, persist_artifact=persist,
    )
    with pytest.raises(SimulatedCrash):
        orchestrator.run(frozen_run=_run(), persisted_plan=_skill_plan(), worker_id="T1-worker", lease_epoch=1)

    result = orchestrator.run(
        frozen_run=_run(), persisted_plan=_skill_plan(), worker_id="T2-worker", lease_epoch=2,
    )

    assert result["state"] == "succeeded"
    assert parent_calls == 1
    assert skill_calls == 1
    assert same_agent_reviews == 1
    assert independent_reviews == 1


@pytest.mark.parametrize(
    "plan_factory",
    [
        lambda: _with_downstream_unit(_frozen_skill_route_plan("skill_a")),
        lambda: _with_downstream_unit(_skill_plan()),
    ],
    ids=("frozen_skill_route", "requested_skill"),
)
def test_skill_review_advisory_verdict_allows_confirmed_continuation(
    monkeypatch: pytest.MonkeyPatch, plan_factory,
) -> None:
    repository = FakeCollaborationRepository()
    monkeypatch.setattr(
        "app.execution.durable_skill_runtime.resolve_registered_durable_skill",
        lambda skill_id: {"skillId": skill_id, "durableExecutable": True},
    )

    def agent(request: dict) -> dict:
        if request.get("phase") == "review_artifact":
            return {"verdict": "needs_revision", "basis": "Evidence is incomplete.", "issues": [], "missingInputs": []}
        if request.get("phase") == "review_skill_result":
            return {"state": "succeeded", "summary": "synthesized", "artifactPayload": {}}
        if request["agentId"] == "validation_design":
            return {"state": "succeeded", "summary": "downstream complete"}
        return {
            "state": "succeeded", "summary": "need sources",
            "skillRequest": {
                "skillId": "academic_source_retrieval_skill", "inputMemoryRefs": ["memory_goal"],
                "inputArtifactRefs": [], "parameters": {}, "reason": "need sources", "idempotencyKey": "one",
            },
        }

    result = DurableMultiAgentOrchestrator(
        collaboration=repository, execute_agent_unit=agent,
        execute_skill_unit=lambda request: {"state": "succeeded", "summary": request["skillId"], "artifactPayload": {}},
        persist_artifact=lambda call_id, _result: f"artifact_{call_id}",
    ).run(frozen_run=_run(), persisted_plan=plan_factory(), worker_id="worker_1", lease_epoch=1)

    assert result["state"] == "succeeded"
    assert repository.units[0]["state"] == "succeeded"
    assert len(repository.handoffs) == 1
    assert repository.reviews
    assert all(review["verdict"] == "needs_revision" for review in repository.reviews)
    assert all(review["disposition"] == "advisory" for review in repository.reviews)
    assert all(review["continue_allowed"] is True for review in repository.reviews)


@pytest.mark.parametrize(
    "plan_factory",
    [lambda: _with_downstream_unit(_frozen_skill_route_plan("skill_a")), lambda: _with_downstream_unit(_skill_plan())],
    ids=("frozen_skill_route", "requested_skill"),
)
def test_unknown_independent_skill_review_is_absorbing_and_never_handoffs(
    monkeypatch: pytest.MonkeyPatch, plan_factory,
) -> None:
    repository = FakeCollaborationRepository()
    monkeypatch.setattr(
        "app.execution.durable_skill_runtime.resolve_registered_durable_skill",
        lambda skill_id: {"skillId": skill_id, "durableExecutable": True},
    )

    def agent(request: dict) -> dict:
        if request.get("phase") == "review_artifact":
            return {
                "verdict": "external_result_unknown", "basis": "Review result is uncertain.",
                "issues": [], "missingInputs": [],
            }
        if request.get("phase") == "review_skill_result":
            return {"state": "succeeded", "summary": "synthesized", "artifactPayload": {}}
        return {
            "state": "succeeded", "summary": "need sources",
            "skillRequest": {
                "skillId": "academic_source_retrieval_skill", "inputMemoryRefs": ["memory_goal"],
                "inputArtifactRefs": [], "parameters": {}, "reason": "need sources", "idempotencyKey": "one",
            },
        }

    result = DurableMultiAgentOrchestrator(
        collaboration=repository, execute_agent_unit=agent,
        execute_skill_unit=lambda request: {"state": "succeeded", "summary": request["skillId"], "artifactPayload": {}},
        persist_artifact=lambda call_id, _result: f"artifact_{call_id}",
    ).run(frozen_run=_run(), persisted_plan=plan_factory(), worker_id="worker_1", lease_epoch=1)

    assert result["state"] == "external_result_unknown"
    assert repository.units[0]["state"] != "succeeded"
    assert repository.handoffs == []


@pytest.mark.parametrize(
    "classification",
    ("provider_configuration", "provider_rejected_request", "provider_response_contract_invalid"),
)
def test_requested_skill_persists_known_failure_classification(classification: str) -> None:
    repository = FakeCollaborationRepository()

    def agent(_request: dict) -> dict:
        return {
            "state": "succeeded", "summary": "need sources",
            "skillRequest": {
                "skillId": "academic_source_retrieval_skill", "inputMemoryRefs": ["memory_goal"],
                "inputArtifactRefs": [], "parameters": {}, "reason": "need sources", "idempotencyKey": "one",
            },
        }

    result = DurableMultiAgentOrchestrator(
        collaboration=repository, execute_agent_unit=agent,
        execute_skill_unit=lambda _request: {
            "state": "failed", "summary": "known provider failure", "failureClassification": classification,
        },
        persist_artifact=lambda _call_id, _result: pytest.fail("failed Skill must not persist an artifact"),
    ).run(frozen_run=_run(), persisted_plan=_skill_plan(), worker_id="worker_1", lease_epoch=1)

    assert result["state"] == "failed"
    assert repository.calls["call_2"]["failure_classification"] == classification


@pytest.mark.parametrize("verdict", ("needs_revision", "needs_input"))
def test_advisory_review_verdict_preserves_evidence_and_continues(verdict: str) -> None:
    repository = FakeCollaborationRepository()
    requests: list[dict] = []

    def execute(request: dict) -> dict:
        requests.append(deepcopy(request))
        if request.get("phase") == "review_artifact":
            return {"verdict": verdict, "basis": "The artifact cannot safely continue.", "issues": [], "missingInputs": ["source_record"]}
        return {"state": "succeeded", "summary": "specialist complete"}

    result = DurableMultiAgentOrchestrator(
        collaboration=repository, execute_agent_unit=execute,
        persist_artifact=lambda call_id, _result: f"artifact_{call_id}",
    ).run(frozen_run=_run(), persisted_plan=_plan("paper_evidence_extraction", "validation_design"), worker_id="worker_1", lease_epoch=1)

    assert result["state"] == "succeeded"
    assert [request["agentId"] for request in requests] == [
        "paper_evidence_extraction", "result_reliability_audit",
        "validation_design", "result_reliability_audit",
    ]
    assert len(repository.handoffs) == 1
    assert len(repository.reviews) == 2
    assert all(review["verdict"] == verdict for review in repository.reviews)
    assert all(review["basis"] == "The artifact cannot safely continue." for review in repository.reviews)
    assert all(review["missing_inputs"] == ["source_record"] for review in repository.reviews)
    assert all(review["disposition"] == "advisory" for review in repository.reviews)
    assert all(review["continue_allowed"] is True for review in repository.reviews)


def test_unknown_review_never_retries_or_handoffs() -> None:
    repository = FakeCollaborationRepository()
    calls = 0

    def execute(request: dict) -> dict:
        nonlocal calls
        calls += 1
        if request.get("phase") == "review_artifact":
            return {"verdict": "external_result_unknown", "basis": "The reviewer outcome is unknown.", "issues": [], "missingInputs": []}
        return {"state": "succeeded", "summary": "specialist complete"}

    orchestrator = DurableMultiAgentOrchestrator(
        collaboration=repository, execute_agent_unit=execute,
        persist_artifact=lambda call_id, _result: f"artifact_{call_id}",
    )
    plan = _plan("paper_evidence_extraction", "validation_design")

    assert orchestrator.run(frozen_run=_run(), persisted_plan=plan, worker_id="worker_1", lease_epoch=1)["state"] == "external_result_unknown"
    assert orchestrator.run(frozen_run=_run(), persisted_plan=plan, worker_id="worker_1", lease_epoch=1)["state"] == "external_result_unknown"
    assert calls == 2
    assert len(repository.calls) == 2
    assert repository.handoffs == []


def test_reviewer_preflight_runs_before_its_provider_call() -> None:
    repository = FakeCollaborationRepository()
    preflight_requests: list[dict] = []
    provider_requests: list[dict] = []

    result = DurableMultiAgentOrchestrator(
        collaboration=repository,
        execute_agent_unit=lambda request: provider_requests.append(deepcopy(request)) or (
            {"verdict": "usable", "basis": "Reviewed.", "issues": [], "missingInputs": []}
            if request.get("phase") == "review_artifact"
            else {"state": "succeeded", "summary": "specialist complete"}
        ),
        persist_artifact=lambda call_id, _result: f"artifact_{call_id}",
        preflight_agent_unit=lambda request: preflight_requests.append(deepcopy(request)) or None,
    ).run(frozen_run=_run(), persisted_plan=_plan("paper_evidence_extraction"), worker_id="worker_1", lease_epoch=1)

    assert result["state"] == "succeeded"
    assert [request.get("phase") for request in preflight_requests] == [None, "review_artifact"]
    assert [request.get("phase") for request in provider_requests] == [None, "review_artifact"]
    assert preflight_requests[1]["selectedSkillIds"] == []


def test_reviewer_receives_verified_artifact_body_not_only_reference() -> None:
    repository = FakeCollaborationRepository()
    requests: list[dict] = []

    _orchestrator(repository, requests).run(
        frozen_run=_run(), persisted_plan=_plan("paper_evidence_extraction"), worker_id="worker_1", lease_epoch=1,
    )

    review_request = next(request for request in requests if request.get("phase") == "review_artifact")
    assert review_request["inputArtifactRefs"] == ["artifact_call_1"]
    assert review_request["reviewMaterials"] == [{
        "artifactId": "artifact_call_1",
        "title": "Artifact artifact_call_1",
        "mimeType": "text/markdown",
        "content": "Verified research body for artifact_call_1.",
        "truncated": False,
        "contentSha256": "sha256-artifact_call_1",
    }]
    assert repository.review_material_requests == [{
        "run_id": "run_1", "owner_id": "owner_1", "workspace_id": "workspace_1",
        "artifact_refs": ["artifact_call_1"],
    }]


def test_production_review_verdict_wrapper_is_accepted() -> None:
    repository = FakeCollaborationRepository()

    def execute(request: dict) -> dict:
        if request.get("phase") == "review_artifact":
            return {"reviewVerdict": {
                "verdict": "usable", "basis": "The verified body is usable.", "issues": [], "missingInputs": [],
            }}
        return {"state": "succeeded", "summary": "specialist complete"}

    result = DurableMultiAgentOrchestrator(
        collaboration=repository, execute_agent_unit=execute,
        persist_artifact=lambda call_id, _result: f"artifact_{call_id}",
    ).run(frozen_run=_run(), persisted_plan=_plan("paper_evidence_extraction"), worker_id="worker_1", lease_epoch=1)

    assert result["state"] == "succeeded"
    assert repository.reviews[0]["verdict"] == "usable"


def test_invalid_review_response_blocks_without_handoff_or_successful_source() -> None:
    repository = FakeCollaborationRepository()

    def execute(request: dict) -> dict:
        if request.get("phase") == "review_artifact":
            return {"state": "succeeded", "summary": "not a structured review verdict"}
        return {"state": "succeeded", "summary": "specialist complete"}

    result = DurableMultiAgentOrchestrator(
        collaboration=repository, execute_agent_unit=execute,
        persist_artifact=lambda call_id, _result: f"artifact_{call_id}",
    ).run(
        frozen_run=_run(), persisted_plan=_plan("paper_evidence_extraction", "validation_design"),
        worker_id="worker_1", lease_epoch=1,
    )

    review_call = next(call for call in repository.calls.values() if call["invocation_kind"] == "review")
    source = next(unit for unit in repository.units if unit["unit_key"] == "unit_1")
    assert result["state"] == "failed"
    assert review_call["state"] == "failed"
    assert source["state"] == "running"
    assert repository.handoffs == []


def test_recovery_does_not_repeat_a_completed_review_or_handoff() -> None:
    repository = FakeCollaborationRepository(stop_before_creation_key="unit_2")
    requests: list[dict] = []
    orchestrator = _orchestrator(repository, requests)
    plan = _plan("paper_evidence_extraction", "validation_design")

    assert orchestrator.run(frozen_run=_run(), persisted_plan=plan, worker_id="worker_1", lease_epoch=1)["state"] == "stopped_by_user"
    assert [request["agentId"] for request in requests] == ["paper_evidence_extraction", "result_reliability_audit"]
    repository.stop_before_creation_key = None
    assert orchestrator.run(frozen_run=_run(), persisted_plan=plan, worker_id="worker_1", lease_epoch=1)["state"] == "succeeded"
    assert [request["agentId"] for request in requests] == [
        "paper_evidence_extraction", "result_reliability_audit", "validation_design", "result_reliability_audit",
    ]
    assert len(repository.reviews) == 2
    assert len(repository.handoffs) == 1


def test_reviewer_cannot_review_its_own_business_artifact() -> None:
    repository = FakeCollaborationRepository()
    requests: list[dict] = []

    with pytest.raises(DurableExecutionError, match="independent_reviewer_required"):
        _orchestrator(repository, requests).run(
            frozen_run=_run(), persisted_plan=_plan("result_reliability_audit"), worker_id="worker_1", lease_epoch=1,
        )

    assert [request["agentId"] for request in requests] == ["result_reliability_audit"]
    assert repository.handoffs == []


def test_authorized_skill_is_run_once_then_reviewed_by_its_requesting_agent() -> None:
    repository = FakeCollaborationRepository()
    agent_requests: list[dict] = []
    skill_requests: list[dict] = []
    preflight_requests: list[dict] = []

    def agent(request: dict) -> dict:
        agent_requests.append(deepcopy(request))
        if request.get("phase") == "review_artifact":
            return {"verdict": "usable", "basis": "Skill outputs are usable.", "issues": [], "missingInputs": []}
        if request.get("phase") == "review_skill_result":
            assert request["agentId"] == "paper_evidence_extraction"
            assert request["skillResult"]["artifactIds"] == ["artifact_call_2"]
            return {"state": "succeeded", "summary": "reviewed", "artifactPayload": {"final": True}}
        return {
            "state": "succeeded", "summary": "need sources",
            "skillRequest": {
                "skillId": "academic_source_retrieval_skill", "inputMemoryRefs": ["memory_goal"],
                "inputArtifactRefs": [], "parameters": {"user_query": "bounded query"},
                "reason": "need registered sources", "idempotencyKey": "source-pass-1",
            },
        }

    def skill(request: dict) -> dict:
        skill_requests.append(deepcopy(request))
        return {"state": "succeeded", "summary": "sources", "relatedSkillId": request["skillId"], "artifactPayload": {"sources": []}}

    result = DurableMultiAgentOrchestrator(
        collaboration=repository, execute_agent_unit=agent,
        execute_skill_unit=skill, persist_artifact=lambda call_id, _result: f"artifact_{call_id}",
        preflight_agent_unit=lambda request: preflight_requests.append(deepcopy(request)) or None,
    ).run(frozen_run=_run(), persisted_plan=_skill_plan(), worker_id="worker_1", lease_epoch=1)

    assert result == {"artifactIds": ["artifact_call_2", "artifact_call_3"], "terminal": True, "state": "succeeded"}
    assert len(skill_requests) == 1
    assert skill_requests[0]["logicalProviderId"] == "openai_primary"
    assert skill_requests[0]["modelProfile"] == "terra"
    assert skill_requests[0]["frozenGoal"] == "check the research question"
    assert skill_requests[0]["frozenConstraints"] == ["confirmed materials only"]
    assert [request.get("phase") for request in agent_requests] == [None, "review_skill_result", "review_artifact"]
    assert [request.get("phase") for request in preflight_requests] == [None, "review_skill_result", "review_artifact"]
    assert [call["invocation_kind"] for call in repository.calls.values()] == ["agent_brain", "skill", "review", "review"]


def test_skill_review_configuration_failure_stays_before_its_external_action() -> None:
    repository = FakeCollaborationRepository()
    review_provider_calls = 0

    def agent(request: dict) -> dict:
        nonlocal review_provider_calls
        if request.get("phase") == "review_skill_result":
            review_provider_calls += 1
            return {"state": "succeeded", "summary": "unused"}
        return {
            "state": "succeeded", "summary": "need sources",
            "skillRequest": {
                "skillId": "academic_source_retrieval_skill", "inputMemoryRefs": ["memory_goal"],
                "inputArtifactRefs": [], "parameters": {"user_query": "bounded query"},
                "reason": "need registered sources", "idempotencyKey": "source-pass-1",
            },
        }

    result = DurableMultiAgentOrchestrator(
        collaboration=repository, execute_agent_unit=agent,
        execute_skill_unit=lambda _request: {"state": "succeeded", "summary": "sources", "artifactPayload": {}},
        persist_artifact=lambda call_id, _result: f"artifact_{call_id}",
        preflight_agent_unit=lambda request: (
            {"summary": "model configuration unavailable"}
            if request.get("phase") == "review_skill_result" else None
        ),
    ).run(frozen_run=_run(), persisted_plan=_skill_plan(), worker_id="worker_1", lease_epoch=1)

    review = next(call for call in repository.calls.values() if call["invocation_kind"] == "review")
    assert result["state"] == "failed"
    assert review_provider_calls == 0
    assert review["external_action_started"] is False


def test_unapproved_or_malformed_skill_request_never_reaches_skill_runner() -> None:
    for response in (
        {"state": "succeeded", "summary": "bad", "skillRequest": {"skillId": "not_registered", "inputMemoryRefs": [], "inputArtifactRefs": [], "parameters": {}, "reason": "x", "idempotencyKey": "x"}},
        {"state": "succeeded", "summary": "bad", "skillRequest": {"skillId": "academic_source_retrieval_skill"}},
    ):
        repository = FakeCollaborationRepository()
        skill_calls = 0

        def skill(_request: dict) -> dict:
            nonlocal skill_calls
            skill_calls += 1
            return {"state": "succeeded"}

        with pytest.raises(DurableExecutionError):
            DurableMultiAgentOrchestrator(
                collaboration=repository, execute_agent_unit=lambda _request, value=response: value,
                execute_skill_unit=skill, persist_artifact=lambda _call_id, _result: "unused",
            ).run(frozen_run=_run(), persisted_plan=_skill_plan(), worker_id="worker_1", lease_epoch=1)
        assert skill_calls == 0


def test_review_cannot_open_a_second_skill_round_without_a_frozen_budget() -> None:
    repository = FakeCollaborationRepository()
    runner_calls = 0

    def agent(request: dict) -> dict:
        skill_request = {"skillId": "academic_source_retrieval_skill", "inputMemoryRefs": ["memory_goal"], "inputArtifactRefs": [], "parameters": {}, "reason": "need sources", "idempotencyKey": "one"}
        if request.get("phase") == "review_skill_result":
            return {"state": "succeeded", "summary": "would loop", "skillRequest": skill_request}
        return {"state": "succeeded", "summary": "first", "skillRequest": skill_request}

    def skill(_request: dict) -> dict:
        nonlocal runner_calls
        runner_calls += 1
        return {"state": "succeeded", "summary": "sources", "artifactPayload": {}}

    result = DurableMultiAgentOrchestrator(
        collaboration=repository, execute_agent_unit=agent, execute_skill_unit=skill,
        persist_artifact=lambda call_id, _result: f"artifact_{call_id}",
    ).run(frozen_run=_run(), persisted_plan=_skill_plan(), worker_id="worker_1", lease_epoch=1)

    assert result["state"] == "failed"
    assert runner_calls == 1


def test_received_controller_input_is_consumed_once_at_dispatch_boundary() -> None:
    repository = FakeCollaborationRepository()
    commands = FakeControllerCommands()
    commands.received = ["Use only the supplied corpus."]
    requests: list[dict] = []
    orchestrator = _orchestrator(repository, requests)
    orchestrator._controller_commands = commands

    orchestrator.run(frozen_run=_run(), persisted_plan=_plan("general_research_agent"), worker_id="worker_1", lease_epoch=1)

    assert requests[0]["controllerInputContext"] == ["Use only the supplied corpus."]
    assert len(commands.applied) == 1
    assert commands.applied[0]["work_unit_id"] == "wu_1"
    assert commands.applied[0]["worker_id"] == "worker_1"
    assert commands.applied[0]["fence_epoch"] == 1


def test_frozen_controller_input_is_only_in_first_specialist_request_and_survives_rebuild() -> None:
    repository = FakeCollaborationRepository()
    commands = FakeControllerCommands()
    commands.received = ["Boundary input."]
    requests: list[dict] = []
    orchestrator = _orchestrator(repository, requests)
    orchestrator._controller_commands = commands
    run = _run()
    run["input_snapshot"]["controllerInputContext"] = ["Start input."]

    plan = _plan("paper_evidence_extraction", "validation_design")
    orchestrator.run(frozen_run=run, persisted_plan=plan, worker_id="worker_1", lease_epoch=1)

    specialist_requests = [request for request in requests if request["kind"] == "specialist"]
    assert specialist_requests[0]["controllerInputContext"] == ["Start input.", "Boundary input."]
    assert specialist_requests[1]["controllerInputContext"] == []

    first_spec = plan["workUnits"][0]
    first_unit = {"work_unit_id": "wu_rebuilt", "input_artifact_refs": []}
    rebuilt = orchestrator._request_spec(
        spec=first_spec, unit=first_unit, frozen_run=run, persisted_plan=plan,
        run_id="run_1", owner_id="owner_1", workspace_id="workspace_1", controller_inputs=["Boundary input."],
    )
    assert rebuilt["controllerInputContext"] == ["Start input.", "Boundary input."]


def test_frozen_controller_input_reaches_first_skill_route_request_and_not_later_work_units(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = FakeCollaborationRepository()
    skill_requests: list[dict] = []
    monkeypatch.setattr(
        "app.execution.durable_skill_runtime.resolve_registered_durable_skill",
        lambda skill_id: {"skillId": skill_id, "durableExecutable": True},
    )
    plan = _frozen_skill_route_plan("skill_a")
    plan["workUnits"].append({
        "unitKey": "unit_2", "parentUnitKey": "unit_1", "agentId": "validation_design", "kind": "specialist",
        "state": "planned", "objective": "validation work", "inputMemoryRefs": [], "inputArtifactRefs": [],
        "selectedSkillIds": [], "modelMode": "terra", **_frozen_work_unit_contract("validation_design"),
    })
    run = _run()
    run["input_snapshot"]["controllerInputContext"] = ["Frozen input."]
    orchestrator = DurableMultiAgentOrchestrator(
        collaboration=repository,
        execute_agent_unit=lambda request: {"state": "succeeded", "summary": request["kind"]},
        execute_skill_unit=lambda request: skill_requests.append(deepcopy(request)) or {"state": "succeeded", "summary": "skill"},
        persist_artifact=lambda call_id, _result: f"artifact_{call_id}",
        controller_commands=FakeControllerCommands(),
    )

    orchestrator.run(frozen_run=run, persisted_plan=plan, worker_id="worker_1", lease_epoch=1)

    assert skill_requests[0]["controllerInputContext"] == ["Frozen input."]
    later_request = orchestrator._request_spec(
        spec=plan["workUnits"][1], unit={"work_unit_id": "wu_2", "input_artifact_refs": []},
        frozen_run=run, persisted_plan=plan, run_id="run_1", owner_id="owner_1", workspace_id="workspace_1",
        controller_inputs=[],
    )
    assert later_request["controllerInputContext"] == []


def test_frozen_controller_input_survives_skill_route_request_rebuild(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = FakeCollaborationRepository()
    monkeypatch.setattr(
        "app.execution.durable_skill_runtime.resolve_registered_durable_skill",
        lambda skill_id: {"skillId": skill_id, "durableExecutable": True},
    )
    plan = _frozen_skill_route_plan("skill_a")
    run = _run()
    run["input_snapshot"]["controllerInputContext"] = ["Frozen input."]
    orchestrator = _orchestrator(repository, [])
    unit = {"work_unit_id": "wu_1", "input_artifact_refs": []}

    first = orchestrator._request_spec(
        spec=plan["workUnits"][0], unit=unit, frozen_run=run, persisted_plan=plan,
        run_id="run_1", owner_id="owner_1", workspace_id="workspace_1", controller_inputs=[],
    )
    rebuilt = orchestrator._request_spec(
        spec=plan["workUnits"][0], unit=unit, frozen_run=run, persisted_plan=plan,
        run_id="run_1", owner_id="owner_1", workspace_id="workspace_1", controller_inputs=[],
    )

    assert first["controllerInputContext"] == ["Frozen input."]
    assert rebuilt["controllerInputContext"] == ["Frozen input."]
