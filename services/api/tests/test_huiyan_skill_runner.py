from __future__ import annotations

import asyncio
import hashlib
import json
from types import SimpleNamespace

import pytest
import httpx

from app.execution import huiyan_skill_runner
from llm_core import openai_compatible_client
from app.execution import runtime_boundary
from app.execution.explicit_chat_runtime import ExplicitChatRuntimeError, run_explicit_chat_runtime
from app.execution.runtime_boundary import (
    ExecutionRequest,
    InlineRuntime,
    UnobservableProviderTransportError,
)
from app.modules.specialist_runtime_gateway import RuntimeGatewayDecision, execution_subject, gateway_metadata
from app.modules.provider_transport_evidence import ProviderAttemptEvidenceError, ProviderHttpAttempt


class _ReservedAttemptRepository:
    def begin(self, **kwargs) -> ProviderHttpAttempt:
        call_id = str(kwargs["capability_call_id"] or "").strip()
        if not call_id or kwargs["attempt_number"] != 1:
            raise ProviderAttemptEvidenceError("provider_attempt_identity_invalid")
        return ProviderHttpAttempt(
            attempt_id=f"attempt-{call_id}",
            capability_call_id=call_id,
            attempt_number=1,
            request_hash=hashlib.sha256(kwargs["request_body"]).hexdigest(),
            provider_endpoint_identity="test:endpoint",
        )

    def mark_io_started(self, _attempt: ProviderHttpAttempt) -> None:
        return None

    def finish(self, _attempt: ProviderHttpAttempt, **_kwargs) -> None:
        return None


def test_selected_skill_provider_timeout_keeps_response_observation_open(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAIR_LLM_PROVIDER_TIMEOUT_SECONDS", "242")

    timeout = huiyan_skill_runner._provider_http_timeout()

    assert timeout.read is None
    assert timeout.connect == 5.0
    assert timeout.pool == 5.0
    assert timeout.write == 10.0


@pytest.fixture(autouse=True)
def reserved_provider_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(huiyan_skill_runner, "ProviderHttpAttemptRepository", _ReservedAttemptRepository)


class _Response:
    status_code = 200
    headers: dict[str, str] = {}

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {
            "id": "huiyan-run-001",
            "model": "huiyan-research-model",
            "choices": [{"message": {"content": '{"summary":"已生成意图理解草案。","output_content":"research_goal: compare MapReduce and Spark\\n"}'}}],
        }

    @property
    def content(self) -> bytes:
        return json.dumps(self.json(), ensure_ascii=True, separators=(",", ":")).encode("utf-8")


class _Client:
    def __init__(self, **_kwargs) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args) -> None:
        return None

    async def post(self, *_args, **_kwargs) -> _Response:
        return _Response()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "expected_outcome"),
    [
        (httpx.ConnectError("connect", request=httpx.Request("POST", "https://provider.invalid")), "transport_not_submitted"),
        (httpx.ReadError("read", request=httpx.Request("POST", "https://provider.invalid")), "transport_uncertain"),
    ],
)
async def test_selected_skill_transport_evidence_distinguishes_submission_certainty(
    monkeypatch: pytest.MonkeyPatch, failure: Exception, expected_outcome: str
) -> None:
    finishes: list[str] = []

    class Repository(_ReservedAttemptRepository):
        def finish(self, _attempt: ProviderHttpAttempt, **kwargs) -> None:
            finishes.append(str(kwargs["outcome"]))

    class Client:
        def __init__(self, *, timeout) -> None:
            assert timeout.read is None
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_args) -> None:
            return None
        async def post(self, *_args, **_kwargs):
            raise failure

    async def controlled_input(**_kwargs):
        return {"goal": "bounded"}, {"inputMode": "test"}

    monkeypatch.setenv("LLM_PROVIDER_API_KEY", "test-key")
    monkeypatch.setenv("LLM_PROVIDER_BASE_URL", "https://provider.invalid")
    monkeypatch.setattr(huiyan_skill_runner, "ProviderHttpAttemptRepository", Repository)
    monkeypatch.setattr(huiyan_skill_runner.httpx, "AsyncClient", Client)
    monkeypatch.setattr(huiyan_skill_runner, "_controlled_skill_input", controlled_input)
    request = SimpleNamespace(
        task_id="task-transport",
        requested_skill_id="intent_understanding",
        skill_id="intent_understanding",
        message="bounded",
        task={"goalSummary": "bounded", "materials": [], "artifacts": []},
        capability_call_id="call-transport",
    )

    with pytest.raises(huiyan_skill_runner.HuiyanSkillExecutionError):
        await huiyan_skill_runner.run_huiyan_selected_skill(request)

    assert finishes == [expected_outcome]


@pytest.mark.asyncio
async def test_selected_huiyan_skill_without_provider_evidence_submits_zero_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client_constructions = 0
    submissions = 0

    class NoHttpClient:
        def __init__(self, **_kwargs) -> None:
            nonlocal client_constructions
            client_constructions += 1

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args) -> None:
            return None

        async def post(self, *_args, **_kwargs):
            nonlocal submissions
            submissions += 1
            raise AssertionError("HTTP submission must not be reached")

    monkeypatch.setenv("LLM_PROVIDER_API_KEY", "test-key")
    monkeypatch.setenv("LLM_PROVIDER_BASE_URL", "https://huiyan.example.test")
    monkeypatch.setattr(huiyan_skill_runner.httpx, "AsyncClient", NoHttpClient)
    request = SimpleNamespace(
        task_id="task-huiyan-no-evidence",
        requested_skill_id="intent_understanding",
        skill_id="intent_understanding",
        message="Create a bounded draft.",
        task={"goalSummary": "Bounded research", "materials": [], "artifacts": []},
    )

    with pytest.raises(ProviderAttemptEvidenceError, match="identity_invalid"):
        await huiyan_skill_runner.run_huiyan_selected_skill(request)

    assert client_constructions == 0
    assert submissions == 0


@pytest.mark.asyncio
async def test_selected_skill_writes_declared_output_and_returns_audit(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("LLM_PROVIDER_API_KEY", "test-key")
    monkeypatch.setenv("LLM_PROVIDER_BASE_URL", "https://huiyan.example.test")
    monkeypatch.setattr(openai_compatible_client.httpx, "AsyncClient", _Client)
    monkeypatch.setattr(huiyan_skill_runner, "prepare_task_workspace", lambda _task_id: tmp_path)

    async def controlled_input(**_kwargs):
        return {"goal": "Compare MapReduce and Spark/RDD"}, {"inputMode": "huiyan_controller", "controllerRunId": "controller-run-001"}

    monkeypatch.setattr(huiyan_skill_runner, "_controlled_skill_input", controlled_input)
    request = SimpleNamespace(
        task_id="task_huiyan_live",
        requested_skill_id="intent_understanding",
        skill_id="intent_understanding",
        message="Create a bounded MapReduce and Spark comparison draft.",
        task={"goalSummary": "Compare MapReduce and Spark/RDD", "materials": [], "artifacts": []},
        capability_call_id="call-huiyan-live",
    )

    result = await huiyan_skill_runner.run_huiyan_selected_skill(request)

    assert result["reply"] == "已生成意图理解草案。"
    assert result["audit"]["provider"] == "huiyan_chat_completions"
    assert result["audit"]["model"] == "huiyan-research-model"
    assert result["audit"]["sourceMetadata"]["providerRunId"] == "huiyan-run-001"
    assert result["audit"]["sourceMetadata"]["inputMode"] == "huiyan_controller"
    assert (tmp_path / "research-config.yaml").read_text(encoding="utf-8") == "research_goal: compare MapReduce and Spark\n"


@pytest.mark.asyncio
async def test_selected_skill_rejects_missing_declared_artifact(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER_API_KEY", "test-key")
    monkeypatch.setenv("LLM_PROVIDER_BASE_URL", "https://huiyan.example.test")
    monkeypatch.setattr(openai_compatible_client.httpx, "AsyncClient", _Client)

    class MissingArtifactResponse(_Response):
        def json(self) -> dict:
            return {"id": "huiyan-run-002", "choices": [{"message": {"content": '{"summary":"仅摘要"}'}}]}

    class MissingArtifactClient(_Client):
        async def post(self, *_args, **_kwargs) -> MissingArtifactResponse:
            return MissingArtifactResponse()

    monkeypatch.setattr(openai_compatible_client.httpx, "AsyncClient", MissingArtifactClient)

    async def controlled_input(**_kwargs):
        return {"goal": "Compare MapReduce and Spark/RDD"}, {"inputMode": "huiyan_controller"}

    monkeypatch.setattr(huiyan_skill_runner, "_controlled_skill_input", controlled_input)
    request = SimpleNamespace(
        task_id="task_huiyan_live",
        requested_skill_id="intent_understanding",
        skill_id="intent_understanding",
        message="Create a draft.",
        task={"goalSummary": "Compare MapReduce and Spark/RDD", "materials": [], "artifacts": []},
        capability_call_id="call-huiyan-missing-artifact",
    )

    with pytest.raises(huiyan_skill_runner.HuiyanSkillExecutionError, match="可审阅产物"):
        await huiyan_skill_runner.run_huiyan_selected_skill(request)


def test_internal_runtime_metadata_is_removed_from_user_artifacts() -> None:
    content = "trace_id: hidden-run\nresearch_goal: bounded comparison\nprovider: internal\nmodel: internal-model\n"

    assert huiyan_skill_runner._strip_internal_output_metadata(content) == "research_goal: bounded comparison"


def test_task_context_lists_artifacts_for_controller_selection() -> None:
    context = huiyan_skill_runner._task_context({
        "taskId": "task_context",
        "goalSummary": "Bound the research question.",
        "artifacts": [{
            "id": "artifact_real",
            "title": "Full upstream result",
            "kind": "research_question",
            "inlineContent": "Complete upstream artifact, not a preview.",
        }],
    })

    assert context["priorArtifacts"] == [{
        "id": "artifact_real",
        "title": "Full upstream result",
        "kind": "research_question",
        "summary": None,
    }]


def test_internal_runtime_metadata_is_removed_when_mixed_with_research_text() -> None:
    content = "Draft scope is design only. trace_id: hidden-run\nresearch_goal: bounded comparison\n"

    assert huiyan_skill_runner._strip_internal_output_metadata(content) == "Draft scope is design only.\nresearch_goal: bounded comparison"


def test_internal_runtime_metadata_is_removed_from_escaped_tool_text() -> None:
    content = 'Preparation complete. {\\"command\\":\\"pwd\\"}\\n"trace_id": "TBD"\\nresearch_goal: bounded comparison'

    assert huiyan_skill_runner._strip_internal_output_metadata(content) == 'Preparation complete. {\\"command\\":\\"pwd\\"}\\nresearch_goal: bounded comparison'


def test_internal_runtime_metadata_is_removed_when_attached_to_tool_text() -> None:
    content = 'Tool output: {"command":"pwd"}trace_id: "TBD"\nresearch_goal: bounded comparison'

    assert huiyan_skill_runner._strip_internal_output_metadata(content) == 'Tool output: {"command":"pwd"}\nresearch_goal: bounded comparison'


@pytest.mark.asyncio
async def test_selected_skill_writes_raw_declared_artifact_without_json_wrapper(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("LLM_PROVIDER_API_KEY", "test-key")
    monkeypatch.setenv("LLM_PROVIDER_BASE_URL", "https://huiyan.example.test")
    monkeypatch.setattr(huiyan_skill_runner, "prepare_task_workspace", lambda _task_id: tmp_path)

    class RawArtifactResponse(_Response):
        def json(self) -> dict:
            return {"id": "huiyan-run-raw", "model": "huiyan-research-model", "choices": [{"message": {"content": "```yaml\nresearch_goal: bounded MapReduce comparison\n```"}}]}

    class RawArtifactClient(_Client):
        async def post(self, *_args, **_kwargs) -> RawArtifactResponse:
            return RawArtifactResponse()

    monkeypatch.setattr(openai_compatible_client.httpx, "AsyncClient", RawArtifactClient)

    async def controlled_input(**_kwargs):
        return {"goal": "Compare MapReduce and Spark/RDD"}, {"inputMode": "huiyan_controller"}

    monkeypatch.setattr(huiyan_skill_runner, "_controlled_skill_input", controlled_input)
    request = SimpleNamespace(
        task_id="task_huiyan_live",
        requested_skill_id="intent_understanding",
        skill_id="intent_understanding",
        message="Create a bounded draft.",
        task={"goalSummary": "Compare MapReduce and Spark/RDD", "materials": [], "artifacts": []},
        capability_call_id="call-huiyan-raw-artifact",
    )

    result = await huiyan_skill_runner.run_huiyan_selected_skill(request)

    assert result["reply"] == "已生成「intent_understanding」研究产物，等待你确认后继续。"
    assert (tmp_path / "research-config.yaml").read_text(encoding="utf-8") == "research_goal: bounded MapReduce comparison\n"


@pytest.mark.asyncio
async def test_selected_skill_uses_one_provider_turn_without_input_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER_API_KEY", "test-key")
    monkeypatch.setenv("LLM_PROVIDER_BASE_URL", "https://huiyan.example.test")
    requests: list[dict] = []

    class ControllerClient(_Client):
        async def post(self, *_args, **kwargs):
            requests.append(json.loads(kwargs["content"]))
            return _Response()

    monkeypatch.setattr(openai_compatible_client.httpx, "AsyncClient", ControllerClient)
    request = SimpleNamespace(
        task_id="task_huiyan_live",
        requested_skill_id="intent_understanding",
        skill_id="intent_understanding",
        message="Create a bounded draft.",
        task={"goalSummary": "Compare MapReduce and Spark/RDD", "creationPlan": {"stages": [{"stageKey": "problem_modeling"}]}, "materials": [], "artifacts": []},
        capability_call_id="call-huiyan-single-turn",
    )

    result = await huiyan_skill_runner.run_huiyan_selected_skill(request)

    assert len(requests) == 1
    assert "creationPlan" not in requests[0]["messages"][1]["content"]
    assert "controllerTaskContext" not in requests[0]["messages"][1]["content"]
    assert "controllerSelectedInput" in requests[0]["messages"][1]["content"]
    assert result["audit"]["sourceMetadata"]["inputMode"] == "single_attempt_frozen_context"
    assert "controllerRunId" not in result["audit"]["sourceMetadata"]


@pytest.mark.asyncio
async def test_declared_input_contract_does_not_request_controller(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER_API_KEY", "test-key")
    monkeypatch.setenv("LLM_PROVIDER_BASE_URL", "https://huiyan.example.test")
    metadata = {"input_contract": {"required": ["research_goal"]}}
    request = SimpleNamespace(
        task={"goalSummary": "Bounded research goal"},
        message="Run the selected Skill.",
    )

    controlled_input, audit = await huiyan_skill_runner._controlled_skill_input(
        request=request,
        skill_id="declared-skill",
        metadata=metadata,
        api_base="https://huiyan.example.test",
        api_key="test-key",
        model="huiyan-model",
    )

    assert controlled_input["declaredInputContract"] == metadata["input_contract"]
    assert audit == {"inputMode": "declared_contract"}


@pytest.mark.asyncio
async def test_inline_runtime_offloads_selected_huiyan_skill(monkeypatch: pytest.MonkeyPatch) -> None:
    request = SimpleNamespace()

    async def runner(received_request):
        assert received_request is request
        return {"reply": "ok"}

    monkeypatch.setattr(huiyan_skill_runner, "run_huiyan_selected_skill", runner)

    result = await InlineRuntime()._run_huiyan_selected_skill(request)

    assert result == {"reply": "ok"}


@pytest.mark.asyncio
async def test_inline_runtime_reports_live_progress_while_huiyan_skill_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    request = SimpleNamespace(mode="execution", requested_skill_id="intent_understanding")
    runtime = InlineRuntime()

    async def runner(_request):
        await asyncio.sleep(0.01)
        return {"reply": "Intent artifact is ready.", "audit": {"provider": "huiyan_chat_completions"}}

    monkeypatch.setattr(runtime, "_run_huiyan_selected_skill", runner)
    monkeypatch.setattr(runtime_boundary, "_selected_skill_uses_huiyan", lambda _request: True)
    monkeypatch.setattr(runtime_boundary, "_LIVE_PROGRESS_INTERVAL_SECONDS", 0.001)

    events = [event async for event in runtime._stream_live(request)]

    progress = [event for event in events if event.get("type") == "progress"]
    assert progress
    assert "模型" in progress[0]["text"]
    assert any("已等待" in event["text"] for event in progress[1:])
    assert events[-1]["type"] == "done"
    assert events[-1]["runtime"] == "huiyan_chat_completions"


@pytest.mark.asyncio
async def test_inline_runtime_blocks_unobservable_claude_provider_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    request = ExecutionRequest(
        task_id="task_claude_live",
        message="Define the research scope.",
        mode="execution",
        requested_skill_id="intent_understanding",
        task={},
        capability_call_id="call_claude_live",
    )

    monkeypatch.setattr(runtime_boundary, "_selected_skill_uses_huiyan", lambda _request: False)

    with pytest.raises(UnobservableProviderTransportError, match="attempts_unobservable"):
        _ = [event async for event in InlineRuntime()._stream_live(request)]


@pytest.mark.asyncio
async def test_explicit_live_chat_uses_observable_collaboration_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAIR_IB01_TEST_LIVE_CHAT_SEAM", raising=False)

    async def fake_agent(*, task, message, actions_snapshot):
        from app.execution.workbench_collaboration_agent import CollaborationAgentResult

        assert task["taskId"] == "task_explicit_live"
        assert message == "Continue the task."
        return CollaborationAgentResult(
            reply="好的，我理解你的需求。",
            runtime="huiyan_chat_completions",
            proposed_action=None,
            source_metadata={"collaboration": True},
        )

    monkeypatch.setattr(
        "app.execution.explicit_chat_runtime.run_workbench_collaboration_agent",
        fake_agent,
    )
    result = await run_explicit_chat_runtime(
        task={"taskId": "task_explicit_live"},
        message="Continue the task.",
        skill_id="workbench_chat",
        source_mode="live",
    )
    assert result.reply == "好的，我理解你的需求。"
    assert result.runtime == "huiyan_chat_completions"


def test_declared_runtime_uses_huiyan_only_when_skill_explicitly_requires_it(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.execution import agent_service

    assert agent_service._declared_live_runtime("intent-understanding", {"runtime": "claude_code"}) == "claude_code"
    assert agent_service._declared_live_runtime("specialist-skill", {"runtime": "huiyan"}) == "huiyan_chat_completions"
    assert agent_service._declared_live_runtime("workbench_chat", {"runtime": "huiyan"}) == "claude_code"


def test_live_runtime_override_routes_explicit_skill_to_huiyan(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.execution import agent_service

    monkeypatch.setenv("OPENAIR_LIVE_SKILL_RUNTIME", "huiyan_chat_completions")

    request = ExecutionRequest(
        task_id="task_huiyan_override",
        message="Define the research scope.",
        mode="execution",
        requested_skill_id="intent_understanding",
        task={},
    )

    assert agent_service._declared_live_runtime("intent_understanding", {"runtime": "claude_code"}) == "huiyan_chat_completions"
    assert runtime_boundary._selected_skill_uses_huiyan(request) is True


def test_unmapped_runtime_call_is_durably_owned_by_openair_controller() -> None:
    decision = RuntimeGatewayDecision(
        action="proceed",
        taskId="task_unmapped",
        targetAgent=None,
        executionMode="full_execution",
        userInputPolicy="queue",
        reason="No registered specialist target was inferred.",
    )

    assert execution_subject(decision) == "openair_orchestrator"
    assert gateway_metadata(decision)["targetAgent"] == "openair_orchestrator"
