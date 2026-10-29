"""Regression guard for issue #103: streamed agent failures must end in done.error."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from types import SimpleNamespace

import pytest

from app.execution import agent_service
from app.models.agent_schemas import AgentChatIn
from app.modules.task_store import store
from app.routes import agent as agent_route


async def _collect_stream_events(response) -> list[dict]:
    events: list[dict] = []
    async for chunk in response.body_iterator:
        text = chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk
        for line in text.splitlines():
            if line.strip():
                events.append(json.loads(line))
    return events


def _terminal_done_events(events: list[dict]) -> list[dict]:
    return [event for event in events if event.get("type") == "done"]


def test_artifact_content_internal_like_terms_do_not_block_completed_reply() -> None:
    reply = (
        "```json\n"
        + json.dumps(
            {
                "type": "artifact",
                "title": "Validation artifact",
                "kind": "research_agent_scenario_artifact",
                "format": "json",
                "content": json.dumps(
                    {
                        "missing_inputs_or_assumptions": [
                            "method-selection.md predecessor artifact not found; refine once available"
                        ],
                        "traceability_note": "Traceability here is a research-audit field, not an internal stack trace.",
                    }
                ),
            }
        )
        + "\n```"
    )

    status, safe_reply = agent_service._classify_business_outcome(
        reply=reply,
        error=None,
        created_artifact_ids=["artifact_validation"],
        confirmation_requests=[],
        substage={"label": "变量定义"},
    )

    assert status == "completed"
    assert safe_reply == reply


@pytest.fixture()
def isolated_store():
    original = store._tasks.copy()
    store._tasks.clear()
    try:
        yield
    finally:
        store._tasks.clear()
        store._tasks.update(original)


@pytest.mark.asyncio
async def test_stalled_interrupted_stream_returns_exactly_one_done_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A stalled/interrupted agent stream must become a structured terminal event, not a network drop."""

    async def interrupted_stream(*_args, **_kwargs) -> AsyncIterator[dict]:
        yield {"type": "started", "runtime": "claude_code", "capabilityCallId": "call_stalled"}
        yield {"type": "progress", "text": "Tool call is still running", "capabilityCallId": "call_stalled"}
        raise TimeoutError("simulated stream silence timeout")

    monkeypatch.setattr(agent_route, "ensure_task_in_scope", lambda task, identity: {"taskId": "task_stream_001"})
    monkeypatch.setattr(agent_route.store, "get_task", lambda task_id: {"taskId": task_id})
    monkeypatch.setattr(agent_route, "agent_chat_stream", interrupted_stream)

    response = await agent_route.post_agent_chat_stream(
        "task_stream_001",
        AgentChatIn(message="run a long literature retrieval"),
        identity={"ownerId": "owner_stream_test", "workspaceId": "workspace_stream_test"},
    )
    events = await _collect_stream_events(response)

    done_events = _terminal_done_events(events)
    assert len(done_events) == 1
    assert done_events[0]["runtime"] == "error"
    assert done_events[0]["error"] == "chat_runtime_failed"
    assert "simulated stream silence timeout" not in done_events[0]["error"]
    assert events[-1] == done_events[0]
    assert all(event.get("type") != "error" for event in events)


@pytest.mark.asyncio
async def test_agent_execution_exception_after_partial_delta_returns_exactly_one_done_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A post-processing/agent execution exception after partial output must still terminate with done.error."""

    async def partial_then_failure(*_args, **_kwargs) -> AsyncIterator[dict]:
        yield {"type": "started", "runtime": "claude_code", "capabilityCallId": "call_partial"}
        yield {"type": "assistant_delta", "text": "partial answer before failure", "capabilityCallId": "call_partial"}
        raise RuntimeError("simulated post-processing failure")

    monkeypatch.setattr(agent_route, "ensure_task_in_scope", lambda task, identity: {"taskId": "task_stream_002"})
    monkeypatch.setattr(agent_route.store, "get_task", lambda task_id: {"taskId": task_id})
    monkeypatch.setattr(agent_route, "agent_chat_stream", partial_then_failure)

    response = await agent_route.post_agent_chat_stream(
        "task_stream_002",
        AgentChatIn(message="continue after partial output"),
        identity={"ownerId": "owner_stream_test", "workspaceId": "workspace_stream_test"},
    )
    events = await _collect_stream_events(response)

    done_events = _terminal_done_events(events)
    assert len(done_events) == 1
    assert done_events[0]["runtime"] == "error"
    assert done_events[0]["error"] == "chat_runtime_failed"
    assert "simulated post-processing failure" not in done_events[0]["error"]
    assert events[-1] == done_events[0]
    assert any(event.get("type") == "assistant_delta" for event in events)
    assert all(event.get("type") != "error" for event in events)


@pytest.mark.asyncio
async def test_cancelled_agent_stream_marks_running_call_failed(
    monkeypatch: pytest.MonkeyPatch,
    isolated_store,
) -> None:
    task = store.create_task(
        {
            "title": "cancelled stream task",
            "goalSummary": "verify interrupted streams do not leave calls running",
            "domain": "research_task",
            "currentStage": "problem_modeling",
            "status": "running",
            "skillPack": "sdr",
        }
    )

    async def cancelled_claude_stream(*_args, **_kwargs) -> AsyncIterator[dict]:
        if False:
            yield {}
        raise asyncio.CancelledError()

    monkeypatch.setattr(agent_service.settings, "skill_exec_mode", "live")
    monkeypatch.setattr(agent_service, "_stream_claude_chat", cancelled_claude_stream)

    with pytest.raises(asyncio.CancelledError):
        async for _event in agent_service.agent_chat_stream(
            task["taskId"],
            "continue current node",
            skill_id="constraints-criteria",
            substage_key="constraints_criteria",
        ):
            pass

    updated = store.get_task(task["taskId"])
    assert updated is not None
    calls = updated["capabilityCalls"]
    assert len(calls) == 1
    assert calls[0]["status"] == "failed"
    assert calls[0]["finishedAt"]
    assert calls[0]["resultSummary"] == "Agent stream was interrupted before completion."
    substage = updated["stageDetails"][0]["subStages"][0]
    assert substage["status"] == "failed"


@pytest.mark.asyncio
async def test_successful_agent_stream_marks_substage_completed(
    monkeypatch: pytest.MonkeyPatch,
    isolated_store,
) -> None:
    task = store.create_task(
        {
            "title": "successful stream task",
            "goalSummary": "verify completed calls close the current node",
            "domain": "research_task",
            "currentStage": "problem_modeling",
            "status": "running",
            "skillPack": "sdr",
        }
    )

    monkeypatch.setattr(agent_service.settings, "skill_exec_mode", "replay")

    events = []
    async for event in agent_service.agent_chat_stream(
        task["taskId"],
        "continue current node",
        skill_id="constraints-criteria",
        substage_key="constraints_criteria",
    ):
        events.append(event)

    updated = store.get_task(task["taskId"])
    assert updated is not None
    calls = updated["capabilityCalls"]
    assert len(calls) == 1
    assert calls[0]["status"] == "completed"
    substage = updated["stageDetails"][0]["subStages"][0]
    assert substage["status"] == "completed"
    assert substage["finishedAt"]
    assert events[-1]["type"] == "done"


@pytest.mark.asyncio
async def test_selected_skill_response_without_workspace_file_is_reviewable_artifact(
    monkeypatch: pytest.MonkeyPatch,
    isolated_store,
) -> None:
    task = store.create_task(
        {
            "title": "captured live response task",
            "goalSummary": "define a bounded research scope",
            "domain": "research_task",
            "currentStage": "problem_modeling",
            "status": "running",
            "skillPack": "sdr",
        }
    )

    async def response_only_stream(*_args, **_kwargs) -> AsyncIterator[dict]:
        yield {"type": "assistant_delta", "text": "## Scope\n\nAssess executable-code reliability and security separately."}
        yield {"type": "done", "runtime": "claude_code"}

    monkeypatch.setattr(agent_service.settings, "skill_exec_mode", "live")
    monkeypatch.setattr(agent_service, "_stream_claude_chat", response_only_stream)

    async for _event in agent_service.agent_chat_stream(
        task["taskId"],
        "Define the research scope without claiming a literature search.",
        skill_id="intent-understanding",
        substage_key="intent_understanding",
    ):
        pass

    updated = store.get_task(task["taskId"])
    assert updated is not None
    call = updated["capabilityCalls"][0]
    assert call["status"] == "completed"
    assert len(call["outputArtifactRefs"]) == 1
    artifact = next(item for item in updated["artifacts"] if item["id"] == call["outputArtifactRefs"][0])
    assert artifact["sourceCapabilityCallId"] == call["id"]
    assert artifact["outputCapture"] == "runtime_response"
    assert artifact["inlineContent"].startswith("## Scope")
    assert artifact["artifactState"] == "needs_review"


@pytest.mark.asyncio
async def test_literature_response_requires_verified_external_source_before_completion(
    monkeypatch: pytest.MonkeyPatch,
    isolated_store,
) -> None:
    task = store.create_task(
        {
            "title": "verified literature task",
            "goalSummary": "find representative research papers",
            "domain": "research_task",
            "currentStage": "problem_modeling",
            "status": "running",
            "skillPack": "sdr",
        }
    )

    async def response_only_stream(*_args, **_kwargs) -> AsyncIterator[dict]:
        yield {"type": "assistant_delta", "text": "Candidate: arXiv:2107.03374"}
        yield {"type": "done", "runtime": "claude_code"}

    source_attempt = SimpleNamespace(
        source_api="OpenAlex",
        status="ok",
        retrieved_at="2026-07-19T00:00:00Z",
        result_count=1,
        error=None,
    )
    source_row = SimpleNamespace(
        title="Evaluating Large Language Models Trained on Code",
        year=2021,
        doi=None,
        arxiv_id="2107.03374",
        external_id="2107.03374",
        url="https://arxiv.org/abs/2107.03374",
        source_api="OpenAlex",
        retrieved_at="2026-07-19T00:00:00Z",
        source_mode="abstract_available",
        is_relevant_enough=True,
    )

    from app.modules import literature_live

    monkeypatch.setattr(literature_live, "retrieve_arxiv_identifiers", lambda *_args, **_kwargs: ([], source_attempt))
    monkeypatch.setattr(
        literature_live,
        "retrieve_semantic_scholar_arxiv_identifiers",
        lambda *_args, **_kwargs: ([], source_attempt),
    )
    monkeypatch.setattr(
        literature_live,
        "run_literature_live",
        lambda *_args, **_kwargs: SimpleNamespace(attempted_apis=[source_attempt], sources=[source_row]),
    )

    monkeypatch.setattr(agent_service.settings, "skill_exec_mode", "live")
    monkeypatch.setattr(agent_service, "_stream_claude_chat", response_only_stream)

    async for _event in agent_service.agent_chat_stream(
        task["taskId"],
        "Find verifiable sources.",
        skill_id="literature-search",
        substage_key="literature_search",
    ):
        pass

    updated = store.get_task(task["taskId"])
    assert updated is not None
    call = updated["capabilityCalls"][0]
    assert call["status"] == "completed"
    assert call["outputArtifactRefs"]
    artifact = updated["artifacts"][0]
    assert "https://arxiv.org/abs/2107.03374" in artifact["inlineContent"]
    assert artifact["evidenceRefs"][0]["sourceUrl"] == "https://arxiv.org/abs/2107.03374"
    from app.models.schemas import TaskOut

    TaskOut.model_validate(updated)


@pytest.mark.asyncio
async def test_internal_preparation_detail_does_not_complete_or_leak_to_user(
    monkeypatch: pytest.MonkeyPatch,
    isolated_store,
) -> None:
    task = store.create_task(
        {
            "title": "paper dependent task",
            "goalSummary": "几篇 SEI 论文对界面稳定机制解释不一致，判断分歧来源",
            "domain": "research_task",
            "currentStage": "problem_modeling",
            "status": "running",
            "skillPack": "sdr",
        }
    )

    async def internal_detail_stream(*_args, **_kwargs) -> AsyncIterator[dict]:
        yield {
            "type": "assistant_delta",
            "text": "research-config.yaml 未找到，先搜索工作区目录结构",
        }
        yield {"type": "done", "runtime": "claude_code"}

    monkeypatch.setattr(agent_service.settings, "skill_exec_mode", "live")
    monkeypatch.setattr(agent_service, "_stream_claude_chat", internal_detail_stream)

    events = []
    async for event in agent_service.agent_chat_stream(
        task["taskId"],
        "继续推进当前节点",
        skill_id="workbench_chat",
        substage_key="literature_search",
    ):
        events.append(event)

    updated = store.get_task(task["taskId"])
    assert updated is not None
    assert updated["status"] == "awaiting_confirmation"
    call = updated["capabilityCalls"][0]
    assert call["status"] == "blocked_recoverable"
    assert "yaml" not in call["resultSummary"].lower()
    assert "工作区目录" not in call["resultSummary"]

    substage = next(
        ss
        for detail in updated["stageDetails"]
        for ss in detail["subStages"]
        if ss["subStage"] == "literature_search"
    )
    assert substage["status"] == "blocked_recoverable"
    assert substage["finishedAt"] is None

    assistant_messages = [m for m in store.get_agent_messages(task["taskId"]) if m["role"] == "assistant"]
    assert assistant_messages
    assert "research-config.yaml" not in assistant_messages[-1]["text"]
    assert "工作区目录" not in assistant_messages[-1]["text"]
    assistant_deltas = [event.get("text", "") for event in events if event.get("type") == "assistant_delta"]
    assert assistant_deltas
    assert all("research-config.yaml" not in text for text in assistant_deltas)
    assert all("工作区目录" not in text for text in assistant_deltas)
    assert events[-1]["type"] == "done"
    assert "research-config.yaml" not in events[-1]["reply"]


@pytest.mark.asyncio
async def test_internal_detail_with_artifact_does_not_short_circuit_as_completed(
    monkeypatch: pytest.MonkeyPatch,
    isolated_store,
) -> None:
    task = store.create_task(
        {
            "title": "artifact with unsafe preface",
            "goalSummary": "audit a research output",
            "domain": "research_task",
            "currentStage": "problem_modeling",
            "status": "running",
            "skillPack": "sdr",
        }
    )
    artifact_block = {
        "type": "artifact",
        "title": "Audit artifact",
        "kind": "research_agent_scenario_artifact",
        "format": "json",
        "summary": "Audit artifact",
        "content": json.dumps({"verdict": "needs_fix"}),
    }

    async def artifact_plus_internal_stream(*_args, **_kwargs) -> AsyncIterator[dict]:
        yield {
            "type": "assistant_delta",
            "text": "research-config.yaml missing; preparing workspace\n\n",
        }
        yield {
            "type": "assistant_delta",
            "text": "```json\n" + json.dumps(artifact_block) + "\n```",
        }
        yield {"type": "done", "runtime": "claude_code"}

    monkeypatch.setattr(agent_service.settings, "skill_exec_mode", "live")
    monkeypatch.setattr(agent_service, "_stream_claude_chat", artifact_plus_internal_stream)

    events = []
    async for event in agent_service.agent_chat_stream(
        task["taskId"],
        "continue",
        skill_id="workbench_chat",
        substage_key="result_acceptance",
    ):
        events.append(event)

    updated = store.get_task(task["taskId"])
    assert updated is not None
    assert updated["status"] == "awaiting_confirmation"
    call = updated["capabilityCalls"][0]
    assert call["status"] == "blocked_recoverable"
    assert call["artifactRefs"]
    assert "research-config.yaml" not in call["resultSummary"]

    assistant_messages = [m for m in store.get_agent_messages(task["taskId"]) if m["role"] == "assistant"]
    assert assistant_messages
    assert "research-config.yaml" not in assistant_messages[-1]["text"]
    assert events[-1]["type"] == "done"
    assert events[-1]["createdArtifactIds"]
    assert "research-config.yaml" not in events[-1]["reply"]


@pytest.mark.asyncio
async def test_live_stream_failure_does_not_fallback_to_replay(
    monkeypatch: pytest.MonkeyPatch,
    isolated_store,
) -> None:
    task = store.create_task(
        {
            "title": "fallback task",
            "goalSummary": "continue research task",
            "domain": "research_task",
            "currentStage": "problem_modeling",
            "status": "running",
            "skillPack": "sdr",
        }
    )

    async def failing_live_stream(*_args, **_kwargs) -> AsyncIterator[dict]:
        raise RuntimeError("session unavailable")
        yield  # pragma: no cover

    monkeypatch.setattr(agent_service.settings, "skill_exec_mode", "live")
    monkeypatch.setattr(agent_service, "_stream_claude_chat", failing_live_stream)

    events = []
    async for event in agent_service.agent_chat_stream(
        task["taskId"],
        "continue",
        skill_id="workbench_chat",
        substage_key="literature_search",
    ):
        events.append(event)

    updated = store.get_task(task["taskId"])
    assert updated is not None
    call = updated["capabilityCalls"][0]
    assert call["status"] == "failed"
    assert "research-config.yaml" not in call["resultSummary"]

    assistant_deltas = [event.get("text", "") for event in events if event.get("type") == "assistant_delta"]
    assert assistant_deltas
    assert all("research-config.yaml" not in text for text in assistant_deltas)
    assert events[-1]["type"] == "done"
    assert events[-1]["error"]
    assert "research-config.yaml" not in events[-1]["reply"]


def test_authentication_reply_is_not_classified_as_completed() -> None:
    status, reply = agent_service._classify_business_outcome(
        reply="Not logged in - Please run /login",
        error=None,
        created_artifact_ids=[],
        confirmation_requests=[],
        substage={"label": "意图理解"},
    )

    assert status == "failed"
    assert "未生成科研产物" in reply
