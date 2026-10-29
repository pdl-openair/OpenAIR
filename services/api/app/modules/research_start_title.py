"""One optional short title after acceptance; no planning wait, retry or gate."""

from __future__ import annotations

import logging
from collections.abc import Callable

import httpx
import psycopg
from llm_core.openai_compatible_client import (
    ProviderEndpoint,
    extract_final_chat_content,
    post_chat_completions,
)
from llm_core.token_usage import record_observed_usage, usage_collection_scope

from app.modules.provider_registry import (
    ProviderRegistryError,
    load_provider_registry,
    resolve_provider_runtime_config,
)
from app.modules.research_start_display_context import ResearchStartDisplayRepository

_logger = logging.getLogger(__name__)
TITLE_PROMPT = (
    "将这条研究请求概括成一个简洁中文标题，只返回标题，不超过20字，"
    "不包含Workflow名称，不声称研究已完成。保留必要的英文专名。"
)
TITLE_FAILURES = (
    httpx.HTTPError,
    psycopg.Error,
    ProviderRegistryError,
    OSError,
    ValueError,
    TypeError,
    KeyError,
    RuntimeError,
)


def generate_research_title(query: str) -> str | None:
    registry = load_provider_registry()
    config = resolve_provider_runtime_config(registry.defaultProviderId, registry=registry)
    payload = {
        "model": config.model,
        "messages": [
            {"role": "system", "content": TITLE_PROMPT},
            {"role": "user", "content": query},
        ],
        "max_tokens": 256,
        "stream": False,
    }
    payload.update(thinking={"type": "disabled"}, reasoning_effort="none")
    endpoint = ProviderEndpoint(
        config.baseUrl,
        config.apiKey,
        timeout_seconds=min(config.timeoutSeconds, 10.0),
        read_timeout_seconds=min(config.timeoutSeconds, 10.0),
        allow_proxy_fallback=False,
    )
    body = post_chat_completions(endpoint, payload)
    record_observed_usage(
        provider=config.adapter, model=config.model, body=body, request_text=query
    )
    if not isinstance(body, dict):
        raise ValueError("invalid LLM response object")
    content, _ = extract_final_chat_content(body)
    title = content.strip().strip('"“”').strip()
    # A presentation-only response check, never a Query/goal/research gate.
    if not title or len(title.splitlines()) != 1 or title.startswith(("{", "[", "```", "@")):
        return None
    return title


def generate_session_title(
    session_id: str,
    owner_id: str,
    workspace_id: str,
    *,
    repository: ResearchStartDisplayRepository | None = None,
    generate: Callable[[str], str | None] = generate_research_title,
) -> None:
    repository = repository or ResearchStartDisplayRepository()
    try:
        claimed = repository.claim_title(session_id, owner_id, workspace_id)
    except TITLE_FAILURES as exc:  # Isolate optional title failures.
        _logger.warning("Optional title claim unavailable: %s", type(exc).__name__)
        return
    if claimed is None:
        return
    title = None
    with usage_collection_scope() as usage:
        try:
            if claimed["original_input"]:
                title = generate(claimed["original_input"])
        except TITLE_FAILURES as exc:  # No research gate on provider failure.
            _logger.warning("Optional title generation failed: %s", type(exc).__name__)
    if usage:
        try:
            from app.modules.token_ledger_repository import flush_observed_usage

            flush_observed_usage(
                user_id=owner_id,
                workspace_id=workspace_id,
                task_id=claimed["task_id"],
                source="research_title",
                bucket=usage,
            )
        except TITLE_FAILURES as exc:  # Account observation failure is local to this optional task.
            _logger.warning("Optional title usage persistence unavailable: %s", type(exc).__name__)
    try:
        repository.finish_title(session_id, owner_id, workspace_id, title)
    except TITLE_FAILURES as exc:  # No retry after an uncertain optional persistence outcome.
        _logger.warning("Optional title persistence unavailable: %s", type(exc).__name__)
