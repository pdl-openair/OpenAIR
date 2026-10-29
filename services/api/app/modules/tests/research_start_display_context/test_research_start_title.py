"""Deterministic provider and HTTP-entry evidence; no live model or business DB."""

import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from app.modules import research_start_title as titles
from app.routes import research_start as routes
from fastapi import BackgroundTasks


class OneShotRepository:
    def __init__(self):
        self.claimed = False
        self.finished = []
        self.original = "@Workflow 研究 LLM judge 风格偏差\n保留第二行"

    def claim_title(self, *identity):
        if self.claimed:
            return None
        self.claimed = True
        return {"task_id": "task-one", "original_input": self.original}

    def finish_title(self, *identity_and_title):
        self.finished.append(identity_and_title)


@pytest.mark.parametrize(
    "error",
    [
        httpx.ReadTimeout("slow"),
        RuntimeError("configuration missing"),
        ValueError("empty"),
        TypeError("bad response"),
    ],
)
def test_title_failures_finish_locally_and_duplicate_submit_never_calls_again(error):
    repository = OneShotRepository()
    generate = Mock(side_effect=error)
    titles.generate_session_title(
        "session", "owner", "workspace", repository=repository, generate=generate
    )
    titles.generate_session_title(
        "session", "owner", "workspace", repository=repository, generate=generate
    )
    generate.assert_called_once_with(repository.original)
    assert repository.finished == [("session", "owner", "workspace", None)]
    assert repository.original.endswith("保留第二行")


def test_persistence_failure_does_not_escape_or_repeat_generation():
    repository = OneShotRepository()
    repository.finish_title = Mock(side_effect=RuntimeError("save failed"))
    generate = Mock(return_value="LLM评审风格偏差")
    for _ in range(2):
        titles.generate_session_title(
            "session", "owner", "workspace", repository=repository, generate=generate
        )
    generate.assert_called_once()


def test_existing_name_or_already_claimed_title_skips_provider():
    repository = OneShotRepository()
    repository.claimed = True
    generate = Mock()
    titles.generate_session_title(
        "session", "owner", "workspace", repository=repository, generate=generate
    )
    generate.assert_not_called()


@pytest.mark.parametrize(
    "content, expected",
    [
        ("LLM评审风格偏差", "LLM评审风格偏差"),
        (
            "OpenTrajectory agent evaluation under answer style changes",
            "OpenTrajectory agent evaluation under answer style changes",
        ),
        ("“材料工艺参数研究”", "材料工艺参数研究"),
        ("", None),
        ("标题\n解释如下", None),
        ('{"title":"不使用JSON"}', None),
        ("@Workflow", None),
        (["invalid response object"], None),
    ],
)
def test_plain_title_uses_shared_provider_config_and_bounded_transport(
    monkeypatch, content, expected
):
    registry = SimpleNamespace(defaultProviderId="test-provider")
    config = SimpleNamespace(
        model="test-model",
        baseUrl="https://provider.invalid",
        apiKey="test-key",
        adapter="huiyan_chat_completions",
        timeoutSeconds=300,
    )
    monkeypatch.setattr(titles, "load_provider_registry", lambda: registry)
    resolver = Mock(return_value=config)
    monkeypatch.setattr(titles, "resolve_provider_runtime_config", resolver)
    post = Mock(
        return_value=content
        if isinstance(content, list)
        else {"choices": [{"message": {"content": content}}]}
    )
    monkeypatch.setattr(titles, "post_chat_completions", post)
    if content == "" or isinstance(content, list):
        with pytest.raises(ValueError):
            titles.generate_research_title("原始Query")
    else:
        assert titles.generate_research_title("原始Query") == expected
    resolver.assert_called_once_with("test-provider", registry=registry)
    endpoint, payload = post.call_args.args
    assert endpoint.read_timeout_seconds == 10
    assert endpoint.allow_proxy_fallback is False
    assert payload["messages"][-1]["content"] == "原始Query"
    assert payload["max_tokens"] == 256
    assert payload["thinking"]["type"] == "disabled"
    assert "response_format" not in payload


def accepted():
    return routes.ResearchStartAcceptedOut(
        sessionId="intent_123456abcdef",
        requestId="rsr-test",
        status="queued",
        reservedTaskId="task-test",
        reservedArtifactId="artifact-test",
    )


@pytest.mark.parametrize("stream", [False, True])
def test_public_submit_returns_acceptance_before_optional_title_task(monkeypatch, stream):
    monkeypatch.setattr(routes, "_reserve_public_research_start", lambda *_: accepted())
    generate = Mock()
    monkeypatch.setattr(titles, "generate_session_title", generate)
    tasks = BackgroundTasks()
    body = routes.ResearchStartSubmitIn(input="研究请求", clientRequestId="test-request")
    identity = {"ownerId": "owner", "workspaceId": "workspace", "createdBy": "actor"}
    if stream:
        response = asyncio.run(routes.submit_research_start_stream(body, tasks, identity))
        assert response.status_code == 202

        async def lines():
            return [part async for part in response.body_iterator]

        assert '"type": "accepted"' in asyncio.run(lines())[0]
    else:
        response = routes.submit_research_start(body, tasks, identity)
        assert response.reservedTaskId == "task-test"
    generate.assert_not_called()
    asyncio.run(tasks())
    generate.assert_called_once_with("intent_123456abcdef", "owner", "workspace")


def test_display_context_scopes_task_and_requested_session(monkeypatch):
    read = Mock(
        return_value={
            "taskId": "task",
            "sessionId": "session",
            "originalInput": "原文",
            "displayTitle": "已有名称",
            "titlePending": False,
        }
    )
    monkeypatch.setattr(
        "app.modules.research_start_display_context.ResearchStartDisplayRepository.read", read
    )
    response = routes.get_research_start_display_context(
        "task", {"ownerId": "owner", "workspaceId": "workspace"}, "session"
    )
    assert response.originalInput == "原文"
    read.assert_called_once_with("task", "owner", "workspace", "session")


def test_title_deadline_does_not_change_planner_shared_default():
    from llm_core.openai_compatible_client import ProviderEndpoint, _provider_transport_timeout

    assert _provider_transport_timeout(ProviderEndpoint("https://provider.invalid")).read is None
    assert (
        _provider_transport_timeout(
            ProviderEndpoint("https://provider.invalid", read_timeout_seconds=10)
        ).read
        == 10
    )


def test_optional_title_transport_never_retries_even_connection_failure(monkeypatch):
    from llm_core import openai_compatible_client as transport

    client = Mock()
    client.__enter__ = Mock(return_value=client)
    client.__exit__ = Mock(return_value=False)
    client.post.side_effect = httpx.ConnectError("unavailable")
    constructor = Mock(return_value=client)
    monkeypatch.setattr(transport.httpx, "Client", constructor)
    with pytest.raises(httpx.ConnectError):
        transport.post_chat_completions(
            transport.ProviderEndpoint("https://provider.invalid", allow_proxy_fallback=False),
            {"messages": []},
        )
    client.post.assert_called_once()
    constructor.assert_called_once()
