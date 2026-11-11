from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from typing import Any

import pytest

API_ROOT = Path(__file__).resolve().parents[3]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.execution import llm_structured_runner as runner
from app.execution import run_single_structured_skill as structured_skill_cli
from llm_core import openai_compatible_client
from app.execution.llm_structured_runner import (
    LocalPayloadPreflightError,
    _extract_retryable_error,
    _parse_llm_json_content,
    _prepare_payload_for_http,
    _supported_model_fallback_chain,
)
from app.execution.skill_finalization_runtime import (
    SkillFinalizationError,
    build_frozen_finalization_contract,
)


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]):
        self._payload = payload
        self.status_code = 200
        self.text = json.dumps(payload)

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeClient:
    def __init__(self, responses: list[dict[str, Any]], calls: list[dict[str, Any]]):
        self._responses = responses
        self._calls = calls

    def __enter__(self) -> "_FakeClient":
        return self

    def __exit__(self, *args: Any) -> None:
        return None

    def post(self, url: str, *, headers: dict[str, str], json: dict[str, Any]) -> _FakeResponse:
        self._calls.append({"url": url, "headers": headers, "json": json})
        if not self._responses:
            raise AssertionError("unexpected extra LLM request")
        return _FakeResponse(self._responses.pop(0))


def _completion(content: str, *, prompt_tokens: int = 10, completion_tokens: int = 5) -> dict[str, Any]:
    return {
        "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


def _skill(tmp_path: Path, *, skill_name: str = "synthetic-skill") -> Path:
    repo_root = tmp_path / "repo"
    (repo_root / "schemas" / "pvd").mkdir(parents=True, exist_ok=True)
    skill_dir = repo_root / "skills" / skill_name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("Return a JSON object with numeric field value.", encoding="utf-8")
    (skill_dir / "input.schema.json").write_text(
        json.dumps({
            "type": "object",
            "additionalProperties": False,
            "required": [],
            "properties": {},
        }),
        encoding="utf-8",
    )
    (skill_dir / "output.schema.json").write_text(
        json.dumps({
            "type": "object",
            "additionalProperties": False,
            "required": ["value"],
            "properties": {"value": {"type": "number"}},
        }),
        encoding="utf-8",
    )
    (skill_dir / "skill.yaml").write_text("output_file: output.json\n", encoding="utf-8")
    return skill_dir


def _skill_with_registered_finalizer(
    tmp_path: Path,
    *,
    invalid_final_output: bool = False,
) -> tuple[Path, dict[str, Any]]:
    skill_dir = _skill(tmp_path, skill_name="finalized-skill")
    (skill_dir / "draft.schema.json").write_text(
        json.dumps({
            "type": "object",
            "additionalProperties": False,
            "required": ["raw"],
            "properties": {"raw": {"type": "number"}},
        }),
        encoding="utf-8",
    )
    scripts = skill_dir / "scripts"
    scripts.mkdir()
    (scripts / "__init__.py").write_text("", encoding="utf-8")
    final_value = "'invalid'" if invalid_final_output else "draft_payload['raw']"
    (scripts / "finalize.py").write_text(
        "def finalize(input_payload, draft_payload):\n"
        f"    return {{'value': {final_value}}}\n",
        encoding="utf-8",
    )
    (skill_dir / "skill.yaml").write_text(
        "output_file: output.json\n"
        "llm:\n"
        "  structured_output_max_attempts: 1\n",
        encoding="utf-8",
    )
    repo_root = skill_dir.parents[1]
    frozen = build_frozen_finalization_contract(
        "skills/finalized-skill",
        {
            "contract_version": 1,
            "entrypoint": "scripts.finalize:finalize",
            "draft_schema": "draft.schema.json",
        },
        repo_root=repo_root,
    )
    return skill_dir, {"frozenFinalization": frozen}


def _install_fake_client(monkeypatch: pytest.MonkeyPatch, responses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        openai_compatible_client.httpx, "Client",
        lambda timeout, trust_env=False: _FakeClient(responses, calls),
    )
    monkeypatch.setenv("LLM_PROVIDER_API_KEY", "test-key")
    monkeypatch.setenv("LLM_PROVIDER_BASE_URL", "https://example.test")
    monkeypatch.setenv("OPENTRAJECTORY_LLM_PROVIDER", "huiyan_chat_completions")
    monkeypatch.delenv("OPENTRAJECTORY_STRUCTURED_OUTPUT_MAX_ATTEMPTS", raising=False)
    return calls


def test_supported_model_fallback_chain_deduplicates_primary_and_supported(monkeypatch):
    monkeypatch.setenv(
        "LLM_PROVIDER_SUPPORTED_MODELS",
        "gpt-5-mini,gpt-5.3-codex,gpt-5-mini,claude-sonnet-4-6",
    )

    assert _supported_model_fallback_chain("gpt-5-mini") == [
        "gpt-5-mini",
        "gpt-5.3-codex",
        "claude-sonnet-4-6",
    ]


def test_retryable_error_detects_huiyan_busy_response():
    result = {
        "error": {
            "message": "当前模型繁忙，请切换其他模型或稍后重试。如多次重试仍不可用，请联系客服处理。",
            "type": "api_error",
            "param": "",
            "code": "upstream_error",
        }
    }

    assert _extract_retryable_error(result) is not None


def test_retryable_error_ignores_non_error_openai_response():
    result = {
        "choices": [
            {
                "message": {"content": "{}"},
                "finish_reason": "stop",
            }
        ]
    }

    assert _extract_retryable_error(result) is None


def test_parse_llm_json_content_strips_markdown():
    assert _parse_llm_json_content('```json\n{"ok": true}\n```') == {"ok": True}


def test_parse_llm_json_content_raises_for_truncated_json():
    with pytest.raises(json.JSONDecodeError):
        _parse_llm_json_content('{"interpreted_constraints": [{"downstream_search_terms": ["fast charging",')


def test_extract_responses_output_text_from_message_content():
    from app.execution.llm_structured_runner import _extract_responses_output_text

    result = {"output": [{"type": "message", "content": [{"type": "output_text", "text": '{"ok": true}'}]}]}
    assert _extract_responses_output_text(result) == '{"ok": true}'


def test_extract_responses_output_text_raises_for_refusal():
    from app.execution.llm_structured_runner import _extract_responses_output_text

    result = {"output": [{"type": "message", "content": [{"type": "refusal", "refusal": "not allowed"}]}]}
    with pytest.raises(RuntimeError, match="refusal"):
        _extract_responses_output_text(result)


def test_payload_preflight_recursively_replaces_lone_surrogate():
    payload, diagnostics = _prepare_payload_for_http({"messages": [{"content": "before\udcad-after"}]})
    assert payload["messages"][0]["content"] == "before\ufffd-after"
    assert diagnostics["payload_unicode_replacement_count"] == 1
    assert diagnostics["payload_unicode_replacement_paths"] == ["$.messages[0].content"]
    assert diagnostics["payload_utf8_preflight_passed"] is True
    json.dumps(payload, ensure_ascii=False).encode("utf-8", "strict")


def test_payload_preflight_normalizes_valid_surrogate_pair_without_replacement():
    payload, diagnostics = _prepare_payload_for_http({"content": "\ud83d\ude80"})
    assert payload["content"] == "🚀"
    assert diagnostics["payload_unicode_replacement_count"] == 0


def test_payload_preflight_rejects_non_json_value_before_http():
    with pytest.raises(LocalPayloadPreflightError, match="local UTF-8 preflight"):
        _prepare_payload_for_http({"invalid": object()})


def test_payload_preflight_cannot_mutate_an_admitted_schema() -> None:
    with pytest.raises(
        LocalPayloadPreflightError,
        match="would mutate an admitted Provider Schema",
    ):
        _prepare_payload_for_http({
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "schema": {
                        "type": "object",
                        "properties": {"value": {"type": "string", "enum": ["bad\ud800"]}},
                    }
                },
            }
        })


def test_malformed_json_uses_one_format_correction_then_succeeds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Unparseable JSON is a representation mismatch: one correction retry may recover."""
    calls = _install_fake_client(monkeypatch, [
        _completion('{"value": 0. nine}'),
        _completion('{"value": 0.9}'),
    ])

    output, _elapsed, meta = runner.run_skill_with_llm(
        _skill(tmp_path), {}, tmp_path / "workspace", "task",
    )

    assert output == {"value": 0.9}
    assert len(calls) == 2
    assert calls[0]["json"]["response_format"] == {"type": "json_object"}  # noqa: S101 - test assertion
    assert calls[1]["json"]["response_format"] == {"type": "json_object"}  # noqa: S101 - test assertion
    assert meta["structured_output_retries"] == 1
    assert meta["structured_output_retry_reasons"][0]["failure_type"] == "json_parse_error"



def test_runner_resolves_relative_input_ref_before_provider(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    calls = _install_fake_client(monkeypatch, [_completion('{"value": 0.9}')])
    skill_dir = _skill(tmp_path, skill_name="downstream-skill")
    upstream_dir = skill_dir.parent / "upstream-skill"
    upstream_dir.mkdir()
    (upstream_dir / "output.schema.json").write_text(
        json.dumps({
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "additionalProperties": False,
            "required": ["payload"],
            "properties": {"payload": {"type": "string", "minLength": 1}},
        }),
        encoding="utf-8",
    )
    (skill_dir / "input.schema.json").write_text(
        json.dumps({
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$ref": "../upstream-skill/output.schema.json",
        }),
        encoding="utf-8",
    )

    output, _elapsed, meta = runner.run_skill_with_llm(
        skill_dir, {"payload": "ready"}, tmp_path / "workspace", "task",
    )

    assert output == {"value": 0.9}
    assert len(calls) == 1
    assert meta["input_schema_passed"] is True


def test_schema_type_drift_is_normalized_without_hard_fail(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Semantic JSON drift (e.g. numeric string) must normalize, not hard-fail."""
    calls = _install_fake_client(monkeypatch, [
        _completion('{"value": "0.9"}'),
        _completion('{"value": 0.9}'),
    ])

    output, _elapsed, meta = runner.run_skill_with_llm(
        _skill(tmp_path), {}, tmp_path / "workspace", "task",
    )

    assert output == {"value": 0.9}
    assert len(calls) == 1
    assert meta["output_schema_passed"] is True
    assert meta["structured_output_retries"] == 0


def test_runtime_guard_failure_is_not_retried(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls = _install_fake_client(monkeypatch, [_completion('{"value": 0.9}')])

    def _reject(**_: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        raise RuntimeError("semantic runtime guard rejection")

    monkeypatch.setattr(runner, "apply_opentrajectory_runtime_guard", _reject)

    with pytest.raises(RuntimeError, match="semantic runtime guard rejection"):
        runner.run_skill_with_llm(_skill(tmp_path), {}, tmp_path / "workspace", "task")

    assert len(calls) == 1


def test_registered_finalizer_replaces_centralized_guard(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    calls = _install_fake_client(monkeypatch, [_completion('{"raw": 0.9}')])
    skill_dir, source_contract = _skill_with_registered_finalizer(tmp_path)

    def _old_guard_must_not_run(**_: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        raise AssertionError("centralized guard must not run for registered finalization")

    monkeypatch.setattr(runner, "apply_opentrajectory_runtime_guard", _old_guard_must_not_run)
    output, _elapsed, meta = runner.run_skill_with_llm(
        skill_dir,
        {},
        tmp_path / "workspace",
        "task",
        source_contract=source_contract,
    )

    assert output == {"value": 0.9}
    assert len(calls) == 1
    assert meta["runtime_guard"] == {}
    assert meta["finalization"] == {
        "managed": True,
        "contract_version": 1,
        "status": "succeeded",
        "errors": [],
    }


def test_registered_finalizer_fails_closed_on_invalid_final_schema(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    calls = _install_fake_client(monkeypatch, [_completion('{"raw": 0.9}')])
    skill_dir, source_contract = _skill_with_registered_finalizer(
        tmp_path,
        invalid_final_output=True,
    )

    with pytest.raises(RuntimeError, match="final_schema_validation_failed"):
        runner.run_skill_with_llm(
            skill_dir,
            {},
            tmp_path / "workspace",
            "task",
            source_contract=source_contract,
        )

    assert len(calls) == 1


def test_cli_preserves_finalizer_rejection_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    finalizer_error = "/research_records/0/source_quote: exact quote missing"
    structured_error = {
        "row_index": 0,
        "code": "structured_field_not_grounded",
        "field_path": "result/comparison/baseline",
        "field_name": "baseline",
        "structured_value": "Cell C",
        "grounding_text": "Cell A retained 91%, compared with 83% for Cell B.",
        "reason": "literal_text_not_found_in_source_quote",
    }

    def _reject(**_: Any) -> tuple[dict[str, Any], float, dict[str, Any]]:
        raise SkillFinalizationError(
            "finalizer_rejected",
            errors=(finalizer_error, structured_error),
        )

    monkeypatch.setattr(structured_skill_cli, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        structured_skill_cli,
        "_frozen_registry_source_contract",
        lambda _relative_skill_path: {},
    )
    monkeypatch.setattr(structured_skill_cli, "run_skill_with_llm", _reject)
    monkeypatch.setattr(sys, "stdin", io.StringIO("{}"))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_single_structured_skill.py",
            "--skill-id",
            "evidence_node_extraction",
            "--workspace-dir",
            str(tmp_path / "workspace"),
            "--task-id",
            "diagnostics-case",
        ],
    )

    assert structured_skill_cli.main() == 1
    payload = json.loads(capsys.readouterr().out)

    assert payload["error_type"] == "SkillFinalizationError"
    assert payload["error"] == "finalizer_rejected"
    assert payload["diagnostics"]["finalization"] == {
        "code": "finalizer_rejected",
        "errors": [finalizer_error, structured_error],
    }


# ---------------------------------------------------------------------------
# Protocol-dimension provider support (openai_chat_completions alias,
# openai_responses via provider config, anthropic_messages tool use).
# ---------------------------------------------------------------------------


def _install_protocol_fake_client(
    monkeypatch: pytest.MonkeyPatch,
    responses: list[dict[str, Any]],
    provider: str,
) -> list[dict[str, Any]]:
    from llm_core import anthropic_client

    calls: list[dict[str, Any]] = []
    factory = lambda timeout, trust_env=False: _FakeClient(responses, calls)  # noqa: E731
    monkeypatch.setattr(openai_compatible_client.httpx, "Client", factory)
    monkeypatch.setattr(anthropic_client.httpx, "Client", factory)
    monkeypatch.setenv("LLM_PROVIDER_API_KEY", "test-key")
    monkeypatch.setenv("LLM_PROVIDER_BASE_URL", "https://example.test")
    monkeypatch.setenv("OPENTRAJECTORY_LLM_PROVIDER", provider)
    # These tests pin the wire protocol through the selector, so they run on the
    # legacy credential path: without a registry-resolvable model variable the
    # registry adapter is unknown and the selector is the sole protocol
    # authority (a real .env in a developer checkout must not change that).
    monkeypatch.delenv("LLM_PROVIDER_MODEL", raising=False)
    return calls


def test_openai_chat_completions_alias_sends_chat_request(monkeypatch, tmp_path) -> None:
    calls = _install_protocol_fake_client(
        monkeypatch, [_completion('{"value": 0.9}')], "openai_chat_completions"
    )

    output, _elapsed, meta = runner.run_skill_with_llm(
        _skill(tmp_path), {}, tmp_path / "workspace", "task",
    )

    assert output == {"value": 0.9}  # noqa: S101 - test assertion
    assert calls[0]["url"].endswith("/v1/chat/completions")  # noqa: S101 - test assertion
    assert calls[0]["headers"]["Authorization"] == "Bearer test-key"  # noqa: S101 - test assertion
    assert calls[0]["json"]["response_format"] == {"type": "json_object"}  # noqa: S101 - test assertion
    assert meta["provider"] == "openai_chat_completions"  # noqa: S101 - test assertion


def test_openai_responses_reads_provider_configured_credentials(monkeypatch, tmp_path) -> None:
    responses_body = {
        "output": [
            {"content": [{"type": "output_text", "text": '{"value": 0.9}'}]},
        ],
        "status": "completed",
        "usage": {"input_tokens": 11, "output_tokens": 7},
    }
    calls = _install_protocol_fake_client(monkeypatch, [responses_body], "openai_responses")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_BASE_URL", raising=False)

    output, _elapsed, meta = runner.run_skill_with_llm(
        _skill(tmp_path), {}, tmp_path / "workspace", "task",
    )

    assert output == {"value": 0.9}  # noqa: S101 - test assertion
    assert calls[0]["url"].endswith("/v1/responses")  # noqa: S101 - test assertion
    assert calls[0]["headers"]["Authorization"] == "Bearer test-key"  # noqa: S101 - test assertion
    assert calls[0]["json"]["input"][0]["role"] == "system"  # noqa: S101 - test assertion
    assert calls[0]["json"]["text"]["format"]["type"] == "json_schema"  # noqa: S101 - test assertion
    assert meta["provider"] == "openai_responses"  # noqa: S101 - test assertion
    assert meta["prompt_tokens"] == 11  # noqa: S101 - test assertion
    assert meta["completion_tokens"] == 7  # noqa: S101 - test assertion


def _anthropic_tool_response(*, input_tokens: int = 9, output_tokens: int = 4) -> dict[str, Any]:
    return {
        "stop_reason": "tool_use",
        "content": [
            {
                "type": "tool_use",
                "id": "t1",
                "name": "synthetic_skill_output",
                "input": {"value": 0.9},
            },
        ],
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
    }


def test_anthropic_messages_sends_native_request_and_reads_tool_input(
    monkeypatch, tmp_path
) -> None:
    calls = _install_protocol_fake_client(
        monkeypatch, [_anthropic_tool_response()], "anthropic_messages"
    )

    output, _elapsed, meta = runner.run_skill_with_llm(
        _skill(tmp_path), {}, tmp_path / "workspace", "task",
    )

    assert output == {"value": 0.9}  # noqa: S101 - test assertion
    request = calls[0]
    assert request["url"].endswith("/v1/messages")  # noqa: S101 - test assertion
    assert request["headers"]["x-api-key"] == "test-key"  # noqa: S101 - test assertion
    assert request["headers"]["anthropic-version"] == "2023-06-01"  # noqa: S101 - test assertion
    payload = request["json"]
    assert payload["system"].startswith("You are an executor")  # noqa: S101 - test assertion
    assert payload["max_tokens"] >= 1  # noqa: S101 - test assertion
    assert payload["tool_choice"] == {"type": "tool", "name": "synthetic_skill_output"}  # noqa: S101 - test assertion
    assert payload["tools"][0]["input_schema"]["properties"] == {"value": {"type": "number"}}  # noqa: S101 - test assertion
    assert meta["provider"] == "anthropic_messages"  # noqa: S101 - test assertion
    assert meta["prompt_tokens"] == 9  # noqa: S101 - test assertion
    assert meta["completion_tokens"] == 4  # noqa: S101 - test assertion
    assert meta["choice_finish_reason"] == "tool_use"  # noqa: S101 - test assertion


def test_anthropic_messages_correction_retry_keeps_assistant_turn(monkeypatch, tmp_path) -> None:
    non_json_reply = {"stop_reason": "end_turn", "content": [{"type": "text", "text": "不是 JSON"}]}
    calls = _install_protocol_fake_client(
        monkeypatch,
        [non_json_reply, _anthropic_tool_response()],
        "anthropic_messages",
    )
    skill_dir = _skill(tmp_path)
    (skill_dir / "skill.yaml").write_text(
        "output_file: output.json\nllm:\n  structured_output_max_attempts: 2\n",
        encoding="utf-8",
    )

    output, _elapsed, meta = runner.run_skill_with_llm(
        skill_dir, {}, tmp_path / "workspace", "task",
    )

    assert output == {"value": 0.9}  # noqa: S101 - test assertion
    assert len(calls) == 2  # noqa: S101 - test assertion
    assert calls[0]["json"]["messages"][0]["role"] == "user"  # noqa: S101 - test assertion
    assert calls[1]["json"]["messages"][1]["role"] == "assistant"  # noqa: S101 - test assertion
    assert meta["structured_output_retries"] == 1  # noqa: S101 - test assertion


def test_anthropic_messages_empty_reply_routes_to_correction_retry(monkeypatch, tmp_path) -> None:
    truncated_reply = {"stop_reason": "max_tokens", "content": []}
    calls = _install_protocol_fake_client(
        monkeypatch,
        [truncated_reply, _anthropic_tool_response()],
        "anthropic_messages",
    )
    skill_dir = _skill(tmp_path)
    (skill_dir / "skill.yaml").write_text(
        "output_file: output.json\nllm:\n  structured_output_max_attempts: 2\n",
        encoding="utf-8",
    )

    output, _elapsed, meta = runner.run_skill_with_llm(
        skill_dir, {}, tmp_path / "workspace", "task",
    )

    assert output == {"value": 0.9}  # noqa: S101 - test assertion
    assert len(calls) == 2  # noqa: S101 - test assertion
    assert meta["structured_output_retries"] == 1  # noqa: S101 - test assertion


# ---------------------------------------------------------------------------
# Selector/registry protocol consistency (fail closed before Provider I/O).
# ---------------------------------------------------------------------------


def test_selector_conflicting_with_registry_adapter_fails_before_provider_io(
    monkeypatch, tmp_path
) -> None:
    # A resolvable registry supplies the credentials from the default provider
    # (schemas/providers/provider-registry.yaml: huiyan_chat_completions), while
    # the selector asks for the Anthropic wire protocol. Previously the request
    # would be sent with the chat gateway's key; the path must fail closed.
    calls = _install_protocol_fake_client(
        monkeypatch, [_completion('{"value": 0.9}')], "anthropic_messages"
    )
    monkeypatch.setenv("LLM_PROVIDER_MODEL", "registry-model")

    with pytest.raises(RuntimeError) as raised:
        runner.run_skill_with_llm(
            _skill(tmp_path), {}, tmp_path / "workspace", "task",
        )

    message = str(raised.value)
    assert "OPENTRAJECTORY_LLM_PROVIDER=anthropic_messages" in message  # noqa: S101 - test assertion
    assert "registry adapter huiyan_chat_completions" in message  # noqa: S101 - test assertion
    assert calls == []  # noqa: S101 - test assertion


def test_selector_matching_registry_adapter_protocol_still_executes(
    monkeypatch, tmp_path
) -> None:
    # The same protocol under two names (the huiyan alias and the canonical
    # protocol name) is not a mismatch.
    calls = _install_protocol_fake_client(
        monkeypatch, [_completion('{"value": 0.9}')], "openai_chat_completions"
    )
    monkeypatch.setenv("LLM_PROVIDER_MODEL", "registry-model")

    output, _elapsed, meta = runner.run_skill_with_llm(
        _skill(tmp_path), {}, tmp_path / "workspace", "task",
    )

    assert output == {"value": 0.9}  # noqa: S101 - test assertion
    assert len(calls) == 1  # noqa: S101 - test assertion
    assert calls[0]["url"].endswith("/v1/chat/completions")  # noqa: S101 - test assertion
    assert meta["provider"] == "openai_chat_completions"  # noqa: S101 - test assertion
