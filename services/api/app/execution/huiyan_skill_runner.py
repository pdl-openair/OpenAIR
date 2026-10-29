"""HUIYAN-native executor for explicitly selected registered Skills."""
from __future__ import annotations

import json
import logging
import math
import os
import re
from pathlib import Path, PurePosixPath
from typing import Any

import httpx
import yaml

from app.execution.execution_context import prepare_task_workspace
from app.modules.provider_transport_evidence import ProviderHttpAttemptRepository
from app.modules.repo_paths import repo_root

RUNTIME = "huiyan_chat_completions"
PROVIDER = "huiyan_chat_completions"
logger = logging.getLogger(__name__)
_MODEL_UNAVAILABLE = "模型暂时不可用，未使用回放或模拟数据，请稍后重试当前步骤。"
_INTERNAL_OUTPUT_METADATA_SEGMENT = re.compile(
    r"^\s*(?:\\?['\"])?(?:trace|run|session|provider|runtime|model)(?:[_-]?id)?(?:\\?['\"])?\s*:\s*.*$|^\s*(?:\\?['\"])?(?:api[_-]?key|token|password)(?:\\?['\"])?\s*:\s*.*$",
    re.IGNORECASE,
)
_INLINE_INTERNAL_OUTPUT_METADATA = re.compile(
    r"(?<![A-Za-z0-9_])(?:\\?['\"])?(?:trace|run|session|provider|runtime|model)(?:[_-]?id)?(?:\\?['\"])?\s*:\s*.*$|(?<![A-Za-z0-9_])(?:\\?['\"])?(?:api[_-]?key|token|password)(?:\\?['\"])?\s*:\s*.*$",
    re.IGNORECASE,
)


class HuiyanSkillExecutionError(RuntimeError):
    """A selected Skill did not produce a valid live result."""


def _usage_from_body(body: dict[str, Any] | None, *, request_text: str = "") -> dict[str, Any] | None:
    """Extract normalized wire-usage from a chat-completions response body.

    When the provider omits ``usage``, estimate from request/response text so
    the call is never silently free.
    """
    from llm_core.token_usage import billable_usage_from_openai_body, to_wire_usage

    if not isinstance(body, dict):
        return None
    usage = billable_usage_from_openai_body(body, request_text=request_text)
    usage_wire = to_wire_usage(usage)
    if usage_wire and usage.get("estimated"):
        logger.warning(
            "provider usage missing; estimated huiyan_skill tokens total=%s",
            usage_wire.get("totalTokens"),
        )
    return usage_wire


def _combined_usage(
    main_body: dict[str, Any] | None,
    controller_audit: dict[str, Any],
    *,
    request_text: str = "",
) -> dict[str, Any] | None:
    """Sum main skill + controller call token usage (both are real provider calls)."""
    from llm_core.token_usage import from_wire_usage, to_wire_usage

    parts = [from_wire_usage(_usage_from_body(main_body, request_text=request_text))]
    controller_wire = controller_audit.get("controllerUsage")
    if controller_wire:
        parts.append(from_wire_usage(controller_wire))
    parts = [p for p in parts if p]
    if not parts:
        return None
    total_in = sum(int(p.get("input_tokens") or 0) for p in parts)
    total_out = sum(int(p.get("output_tokens") or 0) for p in parts)
    return to_wire_usage(
        {
            "input_tokens": total_in,
            "output_tokens": total_out,
            "total_tokens": total_in + total_out,
            "estimated": any(p.get("estimated") for p in parts),
            "raw": None,
        }
    )


def _timeout_seconds() -> float:
    try:
        requested = float(os.environ.get("OPENAIR_LLM_PROVIDER_TIMEOUT_SECONDS", "90"))
    except ValueError:
        return 90.0
    return requested if math.isfinite(requested) and requested > 0 else 90.0


def _provider_http_timeout() -> httpx.Timeout:
    guard = max(float(_timeout_seconds()), 0.1)
    return httpx.Timeout(
        connect=min(5.0, guard),
        pool=min(5.0, guard),
        write=min(10.0, guard),
        read=None,
    )


def _skill_dir(skill_id: str) -> Path:
    try:
        registry = yaml.safe_load((repo_root() / "skills" / "registry.yaml").read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise HuiyanSkillExecutionError("Skill registry is unavailable.") from exc
    normalized = skill_id.replace("-", "_")
    for entry in registry.get("skills") or []:
        if str(entry.get("skill_id", "")).replace("-", "_") == normalized:
            path = entry.get("skill_path")
            if isinstance(path, str) and path:
                return repo_root() / path
    raise HuiyanSkillExecutionError(f"Selected Skill is not registered: {skill_id}")


def _skill_contract(skill_id: str) -> tuple[dict[str, Any], str]:
    directory = _skill_dir(skill_id)
    try:
        metadata = yaml.safe_load((directory / "skill.yaml").read_text(encoding="utf-8")) or {}
        instructions = (directory / "SKILL.md").read_text(encoding="utf-8")
    except (OSError, yaml.YAMLError) as exc:
        raise HuiyanSkillExecutionError(f"Selected Skill contract is unreadable: {skill_id}") from exc
    if not isinstance(metadata, dict) or not instructions.strip():
        raise HuiyanSkillExecutionError(f"Selected Skill contract is invalid: {skill_id}")
    return metadata, instructions


def _json_object(content: str) -> dict[str, Any]:
    """Accept one complete JSON object, including a safely fenced response."""
    text = content.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    try:
        result = json.loads(text.strip())
    except json.JSONDecodeError as exc:
        raise HuiyanSkillExecutionError(_MODEL_UNAVAILABLE) from exc
    if not isinstance(result, dict):
        raise HuiyanSkillExecutionError(_MODEL_UNAVAILABLE)
    return result


def _unfence_content(content: str) -> str:
    text = content.strip()
    if not text.startswith("```"):
        return text
    text = text.split("\n", 1)[1] if "\n" in text else ""
    return text.rstrip()[:-3].strip() if text.rstrip().endswith("```") else text.strip()


def _safe_output_file(raw: str) -> PurePosixPath:
    path = PurePosixPath(raw)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise HuiyanSkillExecutionError("Selected Skill declares an unsafe output path.")
    return path


def _strip_internal_output_metadata(content: str) -> str:
    """Keep runtime identifiers out of user-visible Skill artifacts."""
    parts = re.split(r"(\r\n|\n|\\r\\n|\\n)", content)
    kept: list[str] = []
    for index in range(0, len(parts), 2):
        segment = parts[index]
        delimiter = parts[index + 1] if index + 1 < len(parts) else ""
        if not _INTERNAL_OUTPUT_METADATA_SEGMENT.match(segment):
            kept.extend((_INLINE_INTERNAL_OUTPUT_METADATA.sub("", segment).rstrip(), delimiter))
    return "".join(kept).strip()


def _task_context(task: dict[str, Any]) -> dict[str, Any]:
    """Return task memory that lets the controller select the needed inputs."""
    plan = task.get("creationPlan") if isinstance(task.get("creationPlan"), dict) else {}
    understanding = plan.get("understanding") if isinstance(plan.get("understanding"), dict) else {}
    research_input = task.get("researchStartInput") if isinstance(task.get("researchStartInput"), dict) else {}
    prior_artifacts = [
        {"id": item.get("id"), "title": item.get("title"), "kind": item.get("kind"), "summary": item.get("summary")}
        for item in task.get("artifacts") or []
        if isinstance(item, dict) and item.get("id")
    ]
    return {
        "goal": task.get("goalSummary") or task.get("title") or "",
        "researchType": task.get("researchType") or plan.get("researchType") or "",
        "currentStage": task.get("currentStage") or "",
        "understanding": {
            key: understanding.get(key)
            for key in ("userIntent", "materialSummary", "evidenceBoundaries", "missingInputs", "riskNotes")
            if understanding.get(key)
        },
        "confirmedInputs": {
            key: research_input.get(key)
            for key in ("research_goal", "existing_materials", "missing_materials", "non_citable_boundaries", "accepted_limitations")
            if research_input.get(key)
        },
        "materials": [item.get("contentPreview") for item in task.get("materials") or [] if isinstance(item, dict) and item.get("contentPreview")],
        "priorArtifacts": prior_artifacts,
    }


def _selected_artifact_inputs(task: dict[str, Any], artifact_ids: list[str]) -> list[dict[str, Any]]:
    """Load exactly the real artifacts selected by the total controller."""
    if not artifact_ids:
        return []
    task_id = str(task.get("taskId") or "")
    by_id = {str(item.get("id")): item for item in task.get("artifacts") or [] if isinstance(item, dict) and item.get("id")}
    selected: list[dict[str, Any]] = []
    for artifact_id in dict.fromkeys(artifact_ids):
        artifact = by_id.get(artifact_id)
        if artifact is None:
            continue
        content = artifact.get("inlineContent")
        source_mode = "inline" if isinstance(content, str) and content.strip() else None
        if source_mode is None and task_id:
            try:
                from app.modules.artifact_resolver import get_resolver

                content, source_mode = get_resolver().resolve_content(task_id, artifact)
            except Exception:
                content = None
                source_mode = None
        if source_mode == "replay" or not isinstance(content, str) or not content.strip():
            continue
        selected.append({"id": artifact_id, "title": artifact.get("title"), "kind": artifact.get("kind"), "content": content})
    return selected


async def _controlled_skill_input(
    *,
    request: Any,
    skill_id: str,
    metadata: dict[str, Any],
    api_base: str,
    api_key: str,
    model: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build bounded input locally so one CapabilityCall needs only one Provider POST."""
    _ = (api_base, api_key, model)
    declared_contract = metadata.get("input_contract")
    if isinstance(declared_contract, dict):
        return {
            "goal": request.task.get("goalSummary") or request.task.get("title") or "",
            "userMessage": request.message,
            "declaredInputContract": declared_contract,
        }, {"inputMode": "declared_contract"}

    context = _task_context(request.task)
    return {
        "goal": context.get("goalSummary") or "",
        "constraints": context.get("constraints") or [],
        "known_evidence": context.get("priorArtifacts") or [],
        "missing_inputs": [],
        "task_instruction": request.message,
    }, {
        "inputMode": "single_attempt_frozen_context",
        "selectedArtifactIds": [],
    }


async def run_huiyan_selected_skill(request: Any) -> dict[str, Any]:
    """Execute one explicit Skill and write its declared artifact to the task workspace."""
    skill_id = str(request.requested_skill_id or request.skill_id or "").strip()
    api_key = (os.environ.get("LLM_PROVIDER_API_KEY") or "").strip()
    api_base = (os.environ.get("LLM_PROVIDER_BASE_URL") or "").strip()
    if not skill_id:
        raise HuiyanSkillExecutionError("Live Skill execution requires an explicit selected Skill.")
    if not api_key or not api_base:
        raise HuiyanSkillExecutionError(_MODEL_UNAVAILABLE)
    metadata, instructions = _skill_contract(skill_id)
    output_file = metadata.get("output_file")
    if output_file is not None and (not isinstance(output_file, str) or not output_file.strip()):
        raise HuiyanSkillExecutionError("Selected Skill declares an invalid output contract.")
    model = (os.environ.get("LLM_PROVIDER_MODEL") or "gpt-4o-mini").strip()
    declared_contract = metadata.get("input_contract")
    controller_input, controller_audit = await _controlled_skill_input(
        request=request,
        skill_id=skill_id,
        metadata=metadata,
        api_base=api_base,
        api_key=api_key,
        model=model,
    )
    selected_artifact_inputs = _selected_artifact_inputs(
        request.task,
        list(controller_audit.get("selectedArtifactIds") or []),
    )
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are executing exactly one user-selected research Skill with inputs selected by the OpenAIR total controller. Use only controllerSelectedInput and selectedArtifactInputs; preserve unresolved inputs and research boundaries. Do not create a task plan, workflow, runtime metadata, or use unselected sources. Do not claim completed retrieval, benchmarks, experiments, or citations absent from these inputs. The artifact is user-visible: never include trace_id, run_id, session_id, provider names, API keys, tokens, passwords, debug logs, internal paths, or implementation metadata. If outputFile is declared, return only its complete artifact text, without an outer JSON wrapper. If outputFile is absent, return JSON only with summary (Chinese, user-facing)."},
            {"role": "user", "content": json.dumps({
                "selectedSkill": {"skillId": skill_id, "outputFile": output_file, "spec": metadata.get("spec"), "instructions": instructions},
                "controllerSelectedInput": controller_input,
                "selectedArtifactInputs": selected_artifact_inputs,
                "userMessage": request.message,
                "declaredInputContract": declared_contract if isinstance(declared_contract, dict) else None,
            }, ensure_ascii=False)},
        ],
        "temperature": 0.2,
    }
    if not output_file:
        payload["response_format"] = {"type": "json_object"}
    body: dict[str, Any] | None = None
    executed_model = model
    endpoint = f"{api_base.rstrip('/')}/v1/chat/completions"
    request_body = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    call_id = str(getattr(request, "capability_call_id", None) or "").strip()
    attempt_repository = ProviderHttpAttemptRepository()
    attempt = attempt_repository.begin(
        capability_call_id=call_id,
        attempt_number=1,
        request_body=request_body,
        endpoint=endpoint,
        adapter=RUNTIME,
        operation_identity=f"selected_skill:{skill_id}",
        provider=PROVIDER,
        model=model,
    )
    headers.update(attempt.headers)
    try:
        if attempt.recovered_response_body is not None:
            response_body = attempt.recovered_response_body
            response = httpx.Response(
                status_code=attempt.recovered_http_status or 200,
                content=response_body,
                headers=attempt.recovered_response_headers or {},
                request=httpx.Request("POST", endpoint),
            )
        else:
            attempt_repository.mark_io_started(attempt)
            async with httpx.AsyncClient(timeout=_provider_http_timeout()) as client:
                response = await client.post(endpoint, headers=headers, content=request_body)
                response_body = response.content
                attempt_repository.finish(
                    attempt,
                    request_body=request_body,
                    outcome="response_received" if response.status_code < 400 else "provider_http_error",
                    http_status=response.status_code,
                    response_body=response_body,
                    response_headers=response.headers,
                )
        response.raise_for_status()
        body = json.loads(response_body.decode("utf-8")) if response_body is not None else response.json()
        if not isinstance(body, dict):
            raise ValueError("non-object provider response")
    except httpx.HTTPStatusError as exc:
        logger.warning("HUIYAN Skill request failed skill=%s model=%s status=%s", skill_id, model, exc.response.status_code)
        raise HuiyanSkillExecutionError(_MODEL_UNAVAILABLE) from exc
    except (httpx.HTTPError, OSError) as exc:
        definitely_not_submitted = isinstance(
            exc,
            (httpx.ConnectTimeout, httpx.ConnectError, httpx.PoolTimeout),
        )
        attempt_repository.finish(
            attempt,
            request_body=request_body,
            outcome="transport_not_submitted" if definitely_not_submitted else "transport_uncertain",
            http_status=None,
            response_body=None,
            response_headers=None,
        )
        logger.warning("HUIYAN Skill request failed skill=%s model=%s error=%s", skill_id, model, type(exc).__name__)
        raise HuiyanSkillExecutionError(_MODEL_UNAVAILABLE) from exc
    except ValueError as exc:
        raise HuiyanSkillExecutionError(_MODEL_UNAVAILABLE) from exc
    try:
        assert body is not None
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError, AssertionError) as exc:
        raise HuiyanSkillExecutionError(_MODEL_UNAVAILABLE) from exc
    raw_content = str(content or "")
    result: dict[str, Any] | None = None
    try:
        result = _json_object(raw_content)
    except HuiyanSkillExecutionError:
        if not output_file:
            raise
    if output_file:
        output_content = result.get("output_content") if result else _unfence_content(raw_content)
        if not isinstance(output_content, str) or not output_content.strip():
            raise HuiyanSkillExecutionError("模型未生成当前步骤需要的可审阅产物，未使用回放或模拟数据，请稍后重试当前步骤。")
        target = prepare_task_workspace(request.task_id) / Path(_safe_output_file(output_file))
        target.parent.mkdir(parents=True, exist_ok=True)
        sanitized_output = _strip_internal_output_metadata(output_content)
        if not sanitized_output:
            raise HuiyanSkillExecutionError("模型未生成可展示产物，未使用回放或模拟数据，请稍后重试当前步骤。")
        target.write_text(sanitized_output + "\n", encoding="utf-8")
        summary = str(result.get("summary") or "").strip() if result else ""
        if not summary:
            summary = f"已生成「{skill_id}」研究产物，等待你确认后继续。"
    else:
        assert result is not None
        summary = str(result.get("summary") or "").strip()
        if not summary:
            raise HuiyanSkillExecutionError("模型未返回可展示结果，未使用回放或模拟数据，请稍后重试当前步骤。")
    return {
        "reply": summary,
        "audit": {
            "provider": PROVIDER,
            "model": str(body.get("model") or executed_model),
            "usage": _combined_usage(
                body,
                controller_audit,
                request_text=request_body.decode("utf-8", errors="replace"),
            ),
            "sourceMetadata": {"providerRunId": str(body.get("id") or "") or None, "requestedModel": model, "executedModel": str(body.get("model") or executed_model), "selectedSkill": skill_id, **controller_audit},
        },
    }
