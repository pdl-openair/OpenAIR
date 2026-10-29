from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.modules.agent_orchestrator import build_orchestrator_decision
from app.modules.specialist_runtime_gateway import prepare_specialist_agent_call
from app.modules.task_store import store
from app.routes import agent as agent_route


client = TestClient(app)


@pytest.fixture()
def isolated_store():
    original = store._tasks.copy()
    store._tasks.clear()
    try:
        yield
    finally:
        store._tasks.clear()
        store._tasks.update(original)


def _evidence_plan() -> dict:
    return {
        "goalSummary": "Extract evidence from provided materials.",
        "domain": "research_task",
        "skillPack": "sdr",
        "researchType": "evidence_extraction_start_draft",
        "selectedCapabilities": [
            {
                "kind": "agent",
                "id": "paper_evidence_extraction",
                "skillIds": ["intent_understanding", "evidence_extraction"],
                "rationale": "Evidence extraction needs a registered specialist.",
                "expectedArtifactTypes": ["claim_evidence_table"],
                "confirmationRequired": True,
            }
        ],
        "clarifications": [],
        "stages": [
            {
                "stageKey": "execution_validation",
                "description": "Extract bounded evidence.",
                "subStages": ["evidence_extraction"],
                "substages": [{"substage": "evidence_extraction", "skills": ["evidence_extraction"]}],
            }
        ],
        "readyToConfirm": True,
    }


def _validation_plan() -> dict:
    return {
        "goalSummary": "Draft a validation design for a hypothesis.",
        "domain": "research_task",
        "skillPack": "sdr",
        "researchType": "validation_design_start_draft",
        "selectedCapabilities": [
            {
                "kind": "agent",
                "id": "validation_design",
                "skillIds": ["variable_definition", "baseline_selection", "analysis_planning"],
                "rationale": "Validation design must remain draft-only until confirmed.",
                "expectedArtifactTypes": ["validation_plan"],
                "confirmationRequired": True,
            }
        ],
        "clarifications": [],
        "stages": [
            {
                "stageKey": "solution_design",
                "description": "Design validation protocol.",
                "subStages": ["variable_definition"],
                "substages": [{"substage": "variable_definition", "skills": ["variable_definition"]}],
            }
        ],
        "readyToConfirm": True,
    }


def _create_task(*, status: str = "running", plan: dict | None = None, goal: str | None = None) -> dict:
    return store.create_task(
        {
            "title": "orchestrated specialist task",
            "goalSummary": goal or (plan or _evidence_plan())["goalSummary"],
            "domain": "research_task",
            "skillPack": "sdr",
            "currentStage": "execution_validation",
            "status": status,
            "creationStatus": "confirmed" if status != "draft" else "awaiting_confirmation",
            "creationPlan": plan or _evidence_plan(),
        }
    )


def _first_substage(task: dict, substage_key: str = "evidence_extraction") -> dict:
    return next(
        sub
        for detail in task["stageDetails"]
        for sub in detail["subStages"]
        if sub["subStage"] == substage_key
    )


def test_orchestrator_decision_distinguishes_entry_agent_supporting_agents_and_sdr_plan() -> None:
    decision = build_orchestrator_decision(
        user_goal="Review this area and propose a validation design.",
        selected_hint="agent_literature_research_v0_3",
        creation_plan=_validation_plan(),
        task_status="draft",
    )

    assert decision.selectedEntryAgent == "key_literature_positioning"
    assert decision.primaryAgent == "validation_design"
    assert decision.executionMode == "draft_only"
    assert decision.requiresUserConfirmation is True
    assert decision.scopeExpansion is True
    assert decision.sdrPlan[0].stage == "solution_design"
    assert decision.modeByAgent["validation_design"] == "draft_only"


def test_direct_draft_endpoint_is_idempotent_by_client_request_id(isolated_store) -> None:
    client_request_id = f"draft-timeout-{uuid4().hex}"
    payload = {
        "goalSummary": "Extract claim evidence from provided materials after confirmation.",
        "clientRequestId": client_request_id,
    }

    first = client.post("/api/tasks/draft", json=payload)
    second = client.post("/api/tasks/draft", json=payload)

    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    assert first.json()["taskId"] == second.json()["taskId"]
    assert first.json()["draftClientRequestId"] == client_request_id
    assert len(store.list_tasks()) == 1


def test_confirmed_json_task_fails_closed_without_durable_backend(isolated_store) -> None:
    draft = client.post(
        "/api/tasks/draft",
        json={
            "goalSummary": "Design validation variables and baseline before any execution.",
            "clientRequestId": f"confirm-start-{uuid4().hex}",
        },
    ).json()
    task_id = draft["taskId"]

    confirmed = client.post(f"/api/tasks/{task_id}/confirm-plan", json=_research_start_confirmation_payload())
    started = client.post(f"/api/tasks/{task_id}/start")

    assert confirmed.status_code == 200, confirmed.text
    assert "orchestratorDecision" not in confirmed.json()["creationPlan"]
    draft_artifact = next(a for a in confirmed.json()["artifacts"] if a["kind"] == "research_start_draft")
    assert draft_artifact["structuredResearchStart"]["research_goal"]
    assert draft_artifact["structuredResearchStart"]["user_confirmation_status"]["status"] in {
        "confirmed_with_limitations",
        "confirmed_complete",
    }
    assert draft_artifact["structuredResearchStart"]["non_citable_boundaries"]
    assert draft_artifact["downstreamInput"]["requiresVerification"] is True
    assert started.status_code == 503, started.text
    assert started.json()["detail"] == "durable_execution_requires_postgres"
    persisted = store.get_task(task_id)
    assert persisted is not None
    assert persisted["status"] == "not_started"
    assert persisted["creationStatus"] == "confirmed"
    assert len(store.list_tasks()) == 1


def _research_start_confirmation_payload() -> dict:
    return {
        "researchGoalConfirmed": True,
        "existingMaterialsConfirmed": True,
        "missingMaterialsDecision": "accept_limitations",
        "nonCitableBoundariesAccepted": True,
        "nextStepLimitationsConfirmed": True,
        "acceptedLimitations": ["当前尚未读取外部论文、数据或代码，不能把本草案作为可引用科研结论。"],
    }


def test_legacy_research_start_drafts_are_repaired_by_current_revision_endpoint(isolated_store) -> None:
    draft = client.post(
        "/api/tasks/draft",
        json={
            "goalSummary": "Compare MapReduce and Spark RDD, then produce a structured table.",
            "clientRequestId": f"legacy-repair-{uuid4().hex}",
        },
    ).json()
    task_id = draft["taskId"]
    artifacts = draft["artifacts"]
    legacy_artifact = next(artifact for artifact in artifacts if artifact["kind"] == "research_start_draft")
    for field in (
        "structuredResearchStart",
        "researchStartConfirmation",
        "missingInputs",
        "nonCitableBoundaries",
        "downstreamInput",
    ):
        legacy_artifact.pop(field, None)
    store.patch_task(task_id, {"artifacts": artifacts})

    revised = client.post(f"/api/tasks/{task_id}/revise-plan")
    assert revised.status_code == 200, revised.text
    revised_artifact = next(artifact for artifact in revised.json()["artifacts"] if artifact["kind"] == "research_start_draft")
    assert revised_artifact["structuredResearchStart"]["research_goal"]

    assert revised_artifact["researchStartConfirmation"]["status"] == "unconfirmed"

    assert revised.json()["creationStatus"] == "revision_requested"


def test_research_start_confirm_plan_rejects_empty_confirmation(isolated_store) -> None:
    draft = client.post(
        "/api/tasks/draft",
        json={
            "goalSummary": "Compare MapReduce and Spark RDD, then produce a structured table.",
            "clientRequestId": f"empty-confirm-{uuid4().hex}",
        },
    ).json()

    confirmed = client.post(f"/api/tasks/{draft['taskId']}/confirm-plan")

    assert confirmed.status_code == 409
    assert "structured research start confirmation is required" in confirmed.text


def test_research_start_advance_rejects_unconfirmed_structured_draft(isolated_store) -> None:
    draft = client.post(
        "/api/tasks/draft",
        json={
            "goalSummary": "Compare MapReduce and Spark RDD, then produce a structured table.",
            "clientRequestId": f"advance-unconfirmed-{uuid4().hex}",
        },
    ).json()
    task_id = draft["taskId"]
    task = store.get_task(task_id)
    assert task is not None
    store.patch_task(task_id, {"status": "running", "creationStatus": "confirmed"})
    store.initialize_from_workflow(task_id, task["creationPlan"])
    initialized = store.get_task(task_id)
    assert initialized is not None
    first_substage = initialized["stageDetails"][0]["subStages"][0]
    store.patch_substage_instance(task_id, first_substage["subStageInstanceId"], status="running")

    advanced = client.post(f"/api/tasks/{task_id}/advance-substage")

    assert advanced.status_code == 409
    assert "research start structured confirmation is required before advancing" in advanced.text


def test_research_start_decision_keeps_draft_blocked_for_supplement(isolated_store) -> None:
    draft = client.post(
        "/api/tasks/draft",
        json={
            "goalSummary": "Compare MapReduce and Spark RDD, then produce a structured table.",
            "clientRequestId": f"research-start-decision-{uuid4().hex}",
        },
    ).json()

    decided = client.post(
        f"/api/tasks/{draft['taskId']}/research-start-decision",
        json={**_research_start_confirmation_payload(), "missingMaterialsDecision": "supplement"},
    )

    assert decided.status_code == 200, decided.text
    body = decided.json()
    assert body["status"] == "draft"
    artifact = next(row for row in body["artifacts"] if row["kind"] == "research_start_draft")
    assert artifact["researchStartConfirmation"]["status"] == "blocked_for_supplement"
    assert artifact["downstreamInput"]["status"] == "blocked_by_user_decision"


def test_capability_calls_preserve_runtime_and_source_mode_metadata(isolated_store) -> None:
    task = _create_task()

    inline_call = store.add_capability_call(
        task["taskId"],
        skill_id="local_adapter",
        status="completed",
        runtime="python_adapter",
    )
    replay_call = store.add_capability_call(
        task["taskId"],
        skill_id="replayed_agent",
        status="completed",
        runtime="replay",
    )

    assert inline_call is not None
    assert inline_call["runId"].startswith("run_")
    assert inline_call["runMode"] == "live"
    assert inline_call["sourceMode"] == "inline"
    assert inline_call["adapter"] == "python_adapter"
    assert replay_call is not None
    assert replay_call["runMode"] == "replay"
    assert replay_call["sourceMode"] == "replay"


def test_gateway_fails_closed_when_specialist_has_no_context_pack_materials(isolated_store) -> None:
    task = _create_task(status="running", plan=_evidence_plan(), goal="Extract evidence about GraphRAG.")

    decision = prepare_specialist_agent_call(
        task=task,
        message="Extract evidence.",
        substage_key="evidence_extraction",
    )

    assert decision.action == "block"
    assert decision.targetAgent == "paper_evidence_extraction"
    assert decision.executionMode == "evidence_bound"
    assert decision.materialReadinessReport is not None
    assert decision.contextPackId is None


def test_gateway_builds_draft_only_context_for_validation_design(isolated_store) -> None:
    task = _create_task(status="draft", plan=_validation_plan())

    decision = prepare_specialist_agent_call(
        task=task,
        message="Hypothesis: method A reduces hallucination rate. Metric: hallucination rate. Baseline: method without retrieval. Dataset: sample corpus.",
        substage_key="variable_definition",
    )

    assert decision.action == "proceed"
    assert decision.targetAgent == "validation_design"
    assert decision.executionMode == "draft_only"
    assert decision.contextPackId
    assert decision.budget["mode"] == "draft_only"


def test_same_task_substage_running_call_queues_new_user_input(isolated_store) -> None:
    task = _create_task(status="running", plan=_evidence_plan())
    substage = _first_substage(task)
    running = store.add_capability_call(
        task["taskId"],
        skill_id="paper_evidence_extraction",
        status="running",
        stage_key="execution_validation",
        substage_instance_id=substage["subStageInstanceId"],
    )
    assert running is not None
    task = store.get_task(task["taskId"])

    decision = prepare_specialist_agent_call(
        task=task,
        message="Add this extra note to the same extraction.",
        substage_key="evidence_extraction",
        user_input_policy="queue",
    )

    assert decision.action == "queue"
    assert decision.userInputPolicy == "queue"
    updated = store.get_task(task["taskId"])
    queued = [call for call in updated["capabilityCalls"] if call["runtime"] == "specialist_agent_runtime_gateway"]
    assert queued
    assert queued[-1]["status"] == "not_started"
    assert queued[-1]["userInputPolicy"] == "queue"


def test_agent_chat_route_uses_gateway_before_specialist_runtime(monkeypatch: pytest.MonkeyPatch, isolated_store) -> None:
    task = _create_task(status="running", plan=_evidence_plan(), goal="Extract evidence about GraphRAG.")

    async def fail_if_called(*_args, **_kwargs):
        raise AssertionError("specialist runtime should not be called when gateway fails closed")

    monkeypatch.setattr(agent_route, "_collect_agent_chat_stream", fail_if_called)

    response = client.post(
        f"/api/tasks/{task['taskId']}/agent/chat",
        json={"message": "Extract evidence.", "skillId": "paper-evidence-extraction", "substageKey": "evidence_extraction"},
    )

    body = response.json()
    assert response.status_code == 410, response.text
    assert body["detail"]["code"] == "synchronous_model_route_retired"


def test_agent_chat_route_rejects_execution_without_an_explicit_skill(isolated_store) -> None:
    task = _create_task(status="running", plan=_evidence_plan(), goal="Extract evidence about GraphRAG.")

    response = client.post(
        f"/api/tasks/{task['taskId']}/agent/chat",
        json={"message": "Extract evidence.", "substageKey": "evidence_extraction"},
    )

    assert response.status_code == 410
    assert response.json()["detail"]["code"] == "synchronous_model_route_retired"


def test_agent_chat_route_passes_context_pack_as_runtime_input(monkeypatch: pytest.MonkeyPatch, isolated_store) -> None:
    original_message = "Extract bounded evidence from the supplied figure caption."
    task = store.create_task(
        {
            "title": "gateway bounded prompt task",
            "goalSummary": "Extract evidence from provided materials.",
            "domain": "research_task",
            "skillPack": "sdr",
            "currentStage": "execution_validation",
            "status": "running",
            "creationStatus": "confirmed",
            "creationPlan": _evidence_plan(),
        }
    )
    store.add_artifact(
        task["taskId"],
        kind="source_material",
        title="paper-a fig.2",
        summary="source excerpt",
        format="text",
        inline_content="Figure 2 reports GraphRAG reduced hallucination rate from 18% to 11%.",
    )

    async def capture_runtime_message(task_id, message, skill_id=None, substage_key=None, *, runtime_message=None):
        assert task_id == task["taskId"]
        assert message == original_message
        assert substage_key == "evidence_extraction"
        assert runtime_message is not None
        assert "specialist_agent_runtime_gateway.v1" in runtime_message
        assert '"targetAgent": "paper_evidence_extraction"' in runtime_message
        assert "research_agent_context_pack.v1" in runtime_message
        assert "Figure 2 reports GraphRAG" in runtime_message
        assert store.get_agent_messages(task_id) == []
        store.append_agent_message(task_id, role="user", text=message)
        return {
            "reply": "captured",
            "runtime": "test_runtime",
            "messages": store.get_agent_messages(task_id),
            "capabilityCallId": None,
            "createdArtifactIds": [],
            "error": None,
        }

    monkeypatch.setattr(agent_route, "_collect_agent_chat_stream", capture_runtime_message)

    response = client.post(
        f"/api/tasks/{task['taskId']}/agent/chat",
        json={"message": original_message, "skillId": "paper-evidence-extraction", "substageKey": "evidence_extraction"},
    )

    assert response.status_code == 410, response.text
    assert response.json()["detail"]["code"] == "synchronous_model_route_retired"
