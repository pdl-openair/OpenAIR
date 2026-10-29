"""Lightweight structured-output runner for OpenTrajectory skills.

Supports two provider modes:
- ``huiyan_chat_completions`` for the existing OpenAI-compatible chat path.
- ``openai_responses`` for official OpenAI Responses API Structured Outputs.
"""
from __future__ import annotations

import copy
import json
import os
import time
from pathlib import Path
from typing import Any, Callable, Mapping

import httpx

from app.execution.structured_skill_schema_runtime import (
    find_openair_repository_root,
    load_file_backed_schema,
)

from app.execution.opentrajectory_structured_runtime_guard import (
    RuntimeGuardRejected,
    apply_opentrajectory_runtime_guard,
)
from app.execution.skill_finalization_runtime import (
    finalize_and_validate_registered_output,
    validate_frozen_finalization_contract,
)
from app.execution.structured_output_provider_adapter import (
    apply_provider_schema_transport,
    structured_response_format,
)

# U-02: the pure content extractors now live in openai_compatible_client (to
# break the import cycle once this module adopts the shared transport). Re-export
# here so existing `from llm_structured_runner import _extract_chat_content`
# callers such as research_start_planner keep working.
from llm_core.openai_compatible_client import (  # noqa: E402,F401
    _extract_chat_content,
    _extract_responses_output_text,
    apply_openai_compatible_thinking,
    extract_final_chat_content,
)

# Load .env from repo root (best-effort; config.py also loads .env robustly).
# Guard the parents index: in the container the module sits at /app/app/execution/
# (only 3 parents), so parents[4] would IndexError. In that case skip — config.py
# + the OPENAIR_REPO_ROOT env var already handle .env discovery.
try:
    from dotenv import load_dotenv

    _parents = Path(__file__).resolve().parents
    _repo_root = _parents[4] if len(_parents) > 4 else None
    if _repo_root is not None:
        _env_file = _repo_root / ".env"
        if _env_file.is_file():
            load_dotenv(_env_file, override=False)
except ImportError:
    pass

_DEFAULT_STRUCTURED_OUTPUT_MAX_ATTEMPTS = 2
_MAX_STRUCTURED_OUTPUT_MAX_ATTEMPTS = 3
_RETRY_EXCERPT_CHARS = 1200


def _structured_output_max_attempts(llm_cfg: dict[str, Any]) -> int:
    """Return a bounded attempt count for syntax/schema-only correction retries."""
    raw_value = llm_cfg.get("structured_output_max_attempts")
    if raw_value is None:
        raw_value = os.environ.get("OPENTRAJECTORY_STRUCTURED_OUTPUT_MAX_ATTEMPTS")
    if raw_value is None:
        return _DEFAULT_STRUCTURED_OUTPUT_MAX_ATTEMPTS
    try:
        parsed = int(raw_value)
    except (TypeError, ValueError):
        return _DEFAULT_STRUCTURED_OUTPUT_MAX_ATTEMPTS
    return max(1, min(parsed, _MAX_STRUCTURED_OUTPUT_MAX_ATTEMPTS))


def _sum_optional(current: int | None, value: Any) -> int | None:
    if isinstance(value, int):
        return (current or 0) + value
    return current


def _billable_token_counts(
    body: Any,
    *,
    request_text: str = "",
    response_text: str = "",
) -> tuple[int, int, int]:
    """Provider usage or character-based estimate (no silent zero when body exists)."""
    from llm_core.token_usage import billable_usage_from_openai_body

    usage = billable_usage_from_openai_body(
        body if isinstance(body, dict) else {},
        request_text=request_text,
        response_text=response_text,
    )
    return (
        int(usage.get("input_tokens") or 0),
        int(usage.get("output_tokens") or 0),
        int(usage.get("total_tokens") or 0),
    )


def _retry_prompt(*, failure_type: str, detail: str, previous_content: str) -> str:
    excerpt = previous_content[:_RETRY_EXCERPT_CHARS]
    return f"""Your previous output failed machine validation.
Failure type: {failure_type}
Failure detail: {detail}

Return a corrected raw JSON object only.
- Do not add markdown fences or commentary.
- Follow the supplied output schema exactly.
- Use valid JSON literals and numeric syntax. Do not spell out numbers inside numeric fields.
- Preserve the intended scientific meaning; do not invent new facts.

Previous output excerpt:
{excerpt}"""


_P08A_SKILL_DIR_NAME = "research-lead-hypothesis-generation-skill"
_P08A_HYPOTHESIS_NOVELTY_BOUNDARY = "derived_from_mechanism_not_established_by_sources"


def _clean_string(value: Any) -> str:
    return " ".join(str(value or "").split())


def _p08a_mechanism_refs(input_payload: dict[str, Any]) -> tuple[set[str], dict[str, set[str]]]:
    packet = input_payload.get("mechanism_packet") if isinstance(input_payload, dict) else {}
    edges = packet.get("mechanism_edges") if isinstance(packet, dict) else []
    edge_ids: set[str] = set()
    evidence_by_edge: dict[str, set[str]] = {}
    for row in edges or []:
        if not isinstance(row, dict):
            continue
        edge_id = _clean_string(row.get("edge_id"))
        if not edge_id:
            continue
        edge_ids.add(edge_id)
        evidence_by_edge[edge_id] = {
            _clean_string(item)
            for item in row.get("evidence_ids") or []
            if _clean_string(item)
        }
    return edge_ids, evidence_by_edge


def _p08a_ref_error(
    item: dict[str, Any],
    edge_ids: set[str],
    evidence_by_edge: dict[str, set[str]],
) -> str | None:
    evidence_ids = {
        _clean_string(value)
        for value in item.get("supporting_evidence_ids") or []
        if _clean_string(value)
    }
    cited_edges = {
        _clean_string(value)
        for value in item.get("supporting_mechanism_edge_ids") or []
        if _clean_string(value)
    }
    if not evidence_ids:
        return "requires_supporting_evidence_ids"
    if not cited_edges:
        return "requires_supporting_mechanism_edge_ids"
    unknown_edges = sorted(edge for edge in cited_edges if edge not in edge_ids)
    if unknown_edges:
        return "unknown_mechanism_edge_id:" + ",".join(unknown_edges)
    grounded = set().union(*(evidence_by_edge[edge] for edge in cited_edges))
    unknown_evidence = sorted(eid for eid in evidence_ids if eid not in grounded)
    if unknown_evidence:
        return "evidence_id_not_grounded_by_selected_edges:" + ",".join(unknown_evidence)
    return None


def _p08a_strings(value: Any) -> list[str]:
    return [_clean_string(item) for item in (value or []) if _clean_string(item)]


def _p08a_valid_confidence(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= float(value) <= 1


def _shape_p08a_output_for_guard(
    *,
    skill_dir_name: str,
    input_payload: dict[str, Any],
    parsed: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Drop invalid P08A rows before the strict guard validates the survivors.

    This is a conservative output-shaping step for live LLM variability. It
    never invents references, never upgrades gap edges, and never changes claim
    strength; rows that cannot satisfy R04/R05 are removed and recorded.
    """
    if skill_dir_name != _P08A_SKILL_DIR_NAME or not isinstance(parsed, dict):
        return parsed, []
    output = copy.deepcopy(parsed)
    edge_ids, evidence_by_edge = _p08a_mechanism_refs(input_payload)
    actions: list[dict[str, Any]] = []

    leads = output.get("research_leads")
    if isinstance(leads, list):
        valid_leads: list[dict[str, Any]] = []
        seen_local_keys: set[str] = set()
        dropped: list[dict[str, Any]] = []
        for index, row in enumerate(leads):
            reason: str | None = None
            if not isinstance(row, dict):
                reason = "lead_not_object"
                local_key = ""
            else:
                local_key = _clean_string(row.get("local_lead_key"))
                required_text = [
                    "title",
                    "statement",
                    "intervention_or_variable",
                    "expected_effect",
                    "scientific_significance",
                    "mechanism_reasoning",
                    "novelty_boundary",
                ]
                if row.get("claim_type") != "mechanism_derived_research_lead":
                    reason = "invalid_claim_type"
                elif not local_key or local_key in seen_local_keys:
                    reason = "missing_or_duplicate_local_lead_key"
                elif not all(_clean_string(row.get(name)) for name in required_text):
                    reason = "missing_specificity_fields"
                elif not _p08a_strings(row.get("boundary_conditions")) or not _p08a_strings(row.get("uncertainties")):
                    reason = "missing_boundary_or_uncertainty_fields"
                else:
                    reason = _p08a_ref_error(row, edge_ids, evidence_by_edge)
                    if reason is None and not _p08a_valid_confidence(row.get("confidence")):
                        reason = "invalid_confidence"
            if reason:
                dropped.append({"index": index, "local_lead_key": local_key, "reason": reason})
                continue
            seen_local_keys.add(local_key)
            valid_leads.append(row)
        if dropped:
            output["research_leads"] = valid_leads
            actions.append({"action": "drop_invalid_p08a_research_leads", "dropped": dropped})

    valid_local_lead_keys = {
        _clean_string(row.get("local_lead_key"))
        for row in output.get("research_leads") or []
        if isinstance(row, dict) and _clean_string(row.get("local_lead_key"))
    }
    conclusion_statements = {
        _clean_string(row.get("statement")).lower()
        for row in output.get("evidence_supported_conclusions") or []
        if isinstance(row, dict) and _clean_string(row.get("statement"))
    }
    hypotheses = output.get("hypotheses")
    if isinstance(hypotheses, list):
        valid_hypotheses: list[dict[str, Any]] = []
        seen_hypothesis_keys: set[str] = set()
        dropped: list[dict[str, Any]] = []
        for index, row in enumerate(hypotheses):
            reason: str | None = None
            if not isinstance(row, dict):
                reason = "hypothesis_not_object"
                local_key = ""
                local_lead_key = ""
            else:
                local_key = _clean_string(row.get("local_hypothesis_key"))
                local_lead_key = _clean_string(row.get("local_lead_key"))
                required_text = [
                    "title",
                    "statement",
                    "independent_variable",
                    "dependent_variable",
                    "expected_direction",
                    "scientific_significance",
                    "evidence_basis",
                    "mechanism_reasoning",
                    "falsifiable_prediction",
                ]
                if row.get("claim_type") != "unvalidated_hypothesis":
                    reason = "invalid_claim_type"
                elif row.get("novelty_boundary") != _P08A_HYPOTHESIS_NOVELTY_BOUNDARY:
                    reason = "invalid_novelty_boundary"
                elif not local_key or local_key in seen_hypothesis_keys:
                    reason = "missing_or_duplicate_local_hypothesis_key"
                elif not local_lead_key or local_lead_key not in valid_local_lead_keys:
                    reason = "unknown_or_missing_local_lead_key"
                elif not all(_clean_string(row.get(name)) for name in required_text):
                    reason = "missing_testable_prediction_fields"
                elif not _p08a_strings(row.get("boundary_conditions")) or not _p08a_strings(row.get("uncertainties")):
                    reason = "missing_boundary_or_uncertainty_fields"
                elif _clean_string(row.get("statement")).lower() in conclusion_statements:
                    reason = "hypothesis_copies_evidence_supported_conclusion"
                else:
                    reason = _p08a_ref_error(row, edge_ids, evidence_by_edge)
                    if reason is None and not _p08a_valid_confidence(row.get("confidence")):
                        reason = "invalid_confidence"
            if reason:
                dropped.append(
                    {
                        "index": index,
                        "local_hypothesis_key": local_key,
                        "local_lead_key": local_lead_key,
                        "reason": reason,
                    }
                )
                continue
            seen_hypothesis_keys.add(local_key)
            valid_hypotheses.append(row)
        if dropped:
            output["hypotheses"] = valid_hypotheses
            actions.append({"action": "drop_invalid_p08a_hypotheses", "dropped": dropped})

    if actions:
        warnings = [str(item) for item in (output.get("generation_warnings") or []) if str(item).strip()]
        for action in actions:
            warnings.append(
                f"{action['action']}: dropped {len(action['dropped'])} row(s) before runtime guard; "
                "no evidence coverage or support status was changed."
            )
        if edge_ids and not output.get("hypotheses"):
            warnings.append("No guard-compliant P08A hypothesis survived shaping for the supplied mechanism packet.")
        output["generation_warnings"] = warnings
    return output, actions


def _persist_structured_guard_failure(
    *,
    workspace_dir: Path,
    task_id: str,
    content: str,
    parsed: Any,
    guard_diagnostics: dict[str, Any],
) -> None:
    task_workspace = workspace_dir / task_id / "diagnostics"
    task_workspace.mkdir(parents=True, exist_ok=True)
    (task_workspace / "raw_output.txt").write_text(_strip_json_markdown(content), encoding="utf-8")
    (task_workspace / "parsed_output.json").write_text(
        json.dumps(parsed, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (task_workspace / "runtime_guard_rejection.json").write_text(
        json.dumps(guard_diagnostics, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


class LocalPayloadPreflightError(RuntimeError):
    """Fail closed before HTTP when a JSON request cannot be emitted as strict UTF-8."""

    failure_class = "local_payload_preflight_failed"
    failure_stage = "payload_utf8_preflight"
    retryable = False

    def __init__(self, message: str, *, diagnostics: dict[str, Any] | None = None):
        super().__init__(message)
        self.diagnostics = diagnostics or {}


def _json_path(parent: str, key: Any) -> str:
    if isinstance(key, int):
        return f"{parent}[{key}]"
    return f"{parent}.{key}" if parent else str(key)


def _sanitize_unicode_text(value: str) -> tuple[str, int]:
    out: list[str] = []
    replacements = 0
    index = 0
    while index < len(value):
        code = ord(value[index])
        if 0xD800 <= code <= 0xDBFF:
            if index + 1 < len(value):
                next_code = ord(value[index + 1])
                if 0xDC00 <= next_code <= 0xDFFF:
                    scalar = 0x10000 + ((code - 0xD800) << 10) + (next_code - 0xDC00)
                    out.append(chr(scalar))
                    index += 2
                    continue
            out.append("\uFFFD")
            replacements += 1
        elif 0xDC00 <= code <= 0xDFFF:
            out.append("\uFFFD")
            replacements += 1
        else:
            out.append(value[index])
        index += 1
    return "".join(out), replacements


def _sanitize_unicode_deep(value: Any, path: str = "$") -> tuple[Any, list[str]]:
    if isinstance(value, str):
        sanitized, replacements = _sanitize_unicode_text(value)
        return sanitized, [path] * replacements
    if isinstance(value, list):
        out: list[Any] = []
        paths: list[str] = []
        for index, item in enumerate(value):
            sanitized, nested_paths = _sanitize_unicode_deep(item, _json_path(path, index))
            out.append(sanitized)
            paths.extend(nested_paths)
        return out, paths
    if isinstance(value, tuple):
        out, paths = _sanitize_unicode_deep(list(value), path)
        return tuple(out), paths
    if isinstance(value, dict):
        out: dict[Any, Any] = {}
        paths: list[str] = []
        for raw_key, raw_item in value.items():
            key, key_paths = _sanitize_unicode_deep(raw_key, _json_path(path, "<key>"))
            if key in out:
                raise LocalPayloadPreflightError(
                    f"payload key collision after Unicode sanitization at {path}",
                    diagnostics={"payload_unicode_replacement_paths": key_paths},
                )
            item, item_paths = _sanitize_unicode_deep(raw_item, _json_path(path, key))
            out[key] = item
            paths.extend(key_paths)
            paths.extend(item_paths)
        return out, paths
    return value, []


def _prepare_payload_for_http(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    sanitized, replacement_paths = _sanitize_unicode_deep(payload)
    diagnostics = {
        "payload_unicode_replacement_count": len(replacement_paths),
        "payload_unicode_replacement_paths": sorted(set(replacement_paths)),
        "payload_utf8_preflight_passed": False,
    }
    _assert_schema_unchanged_by_payload_preflight(diagnostics)
    try:
        json.dumps(sanitized, ensure_ascii=False, separators=(",", ":")).encode("utf-8", "strict")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise LocalPayloadPreflightError(
            f"LLM payload failed local UTF-8 preflight: {exc}",
            diagnostics=diagnostics,
        ) from exc
    diagnostics["payload_utf8_preflight_passed"] = True
    return sanitized, diagnostics


def _failure_payload(exc: Exception, *, default_stage: str = "runtime_execution") -> dict[str, Any]:
    failure_class = str(getattr(exc, "failure_class", "runtime_execution_failed"))
    failure_stage = str(getattr(exc, "failure_stage", default_stage))
    retryable = bool(getattr(exc, "retryable", False))
    diagnostics = getattr(exc, "diagnostics", {})
    if not isinstance(diagnostics, dict):
        diagnostics = {}
    return {
        "failure_class": failure_class,
        "failure_stage": failure_stage,
        "retryable": retryable,
        "diagnostics": diagnostics,
    }


def _dedupe_models(models: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for model in models:
        model = str(model or "").strip()
        if not model or model in seen:
            continue
        seen.add(model)
        out.append(model)
    return out


def _supported_model_fallback_chain(primary_model: str) -> list[str]:
    supported_raw = os.environ.get("LLM_PROVIDER_SUPPORTED_MODELS", "")
    supported_models = [m.strip() for m in supported_raw.split(",") if m.strip()]
    return _dedupe_models([primary_model, *supported_models])


def _openai_model_fallback_chain(primary_model: str) -> list[str]:
    supported_raw = os.environ.get("OPENAI_SUPPORTED_MODELS", "")
    supported_models = [m.strip() for m in supported_raw.split(",") if m.strip()]
    return _dedupe_models([primary_model, *supported_models])


def _is_reasoning_model(model: str) -> bool:
    return str(model).startswith(("gpt-5", "deepseek-r1"))


def _request_timeout_seconds(model: str) -> float:
    timeout_env = os.environ.get("OPENTRAJECTORY_LLM_TIMEOUT_SECONDS")
    if timeout_env:
        try:
            return float(timeout_env)
        except ValueError:
            pass
    return 180.0 if _is_reasoning_model(model) else 90.0


def _extract_error_fields(result: Any) -> tuple[str, str, str] | None:
    if not isinstance(result, dict):
        return None
    error = result.get("error")
    if not isinstance(error, dict):
        return None
    code = str(error.get("code") or "").lower()
    error_type = str(error.get("type") or "").lower()
    message = str(error.get("message") or "")
    return code, error_type, message


def _extract_non_retryable_error(result: Any) -> str | None:
    fields = _extract_error_fields(result)
    if fields is None:
        return None
    code, error_type, message = fields
    message_lower = message.lower()
    context_markers = (
        "context_length_exceeded",
        "context length",
        "context window",
        "maximum context",
        "max context",
        "too many tokens",
        "input is too long",
        "prompt is too long",
        "request too large",
        "token limit",
        "tokens exceed",
        "超过上下文",
        "上下文长度",
        "输入过长",
        "请求过大",
        "token过多",
        "令牌过多",
    )
    if code in {"context_length_exceeded", "invalid_request_error", "request_too_large"} and any(
        marker in message_lower or marker in message for marker in context_markers
    ):
        return message or code
    if error_type in {"invalid_request_error", "bad_request", "validation_error"} and any(
        marker in message_lower or marker in message for marker in context_markers
    ):
        return message or error_type
    if any(marker in message_lower or marker in message for marker in context_markers):
        return message or "context length exceeded"
    return None


def _extract_retryable_error(result: Any) -> str | None:
    if _extract_non_retryable_error(result):
        return None
    fields = _extract_error_fields(result)
    if fields is None:
        return None
    code, error_type, message = fields
    message_lower = message.lower()
    retryable_markers = (
        "busy",
        "overloaded",
        "rate limit",
        "temporarily unavailable",
        "timeout",
        "try again",
        "稍后重试",
        "模型繁忙",
        "当前模型繁忙",
        "上游",
    )
    if code in {"upstream_error", "rate_limit_exceeded", "server_error", "timeout"}:
        return message or code
    if error_type in {"server_error", "rate_limit_error", "timeout_error"}:
        return message or error_type
    if error_type == "api_error" and any(marker in message_lower or marker in message for marker in retryable_markers):
        return message or error_type
    if any(marker in message_lower or marker in message for marker in retryable_markers):
        return message or "retryable provider error"
    return None


def _http_error_is_retryable(status_code: int, response_body: str) -> bool:
    if status_code in {408, 409, 425, 429, 500, 502, 503, 504}:
        return True
    if status_code in {400, 413, 422}:
        try:
            parsed = json.loads(response_body)
        except json.JSONDecodeError:
            return False
        return _extract_retryable_error(parsed) is not None
    return False


def _strip_json_markdown(content: str) -> str:
    content = content.strip()
    if content.startswith("```json"):
        content = content[7:]
    if content.startswith("```"):
        content = content[3:]
    if content.endswith("```"):
        content = content[:-3]
    return content.strip()


def _parse_llm_json_content(content: str) -> dict[str, Any]:
    stripped = _strip_json_markdown(content)
    if not stripped:
        raise ValueError("empty content")
    parsed = json.loads(stripped)
    if not isinstance(parsed, dict):
        raise ValueError(f"expected JSON object, got {type(parsed).__name__}")
    return parsed


def _require_jsonschema():
    try:
        import jsonschema
    except ImportError as exc:
        raise RuntimeError("jsonschema dependency is required for structured skill execution") from exc
    return jsonschema


def _validate_schema(
    instance: Any,
    schema: dict[str, Any],
    label: str,
    *,
    role: str | None = None,
    schema_validator: Callable[[Any], None] | None = None,
    input_diagnostics: dict[str, Any] | None = None,
) -> Any:
    from app.execution.scientific_input_fidelity import validate_cli_schema

    inferred = role if role in {"input", "output"} else ("input" if "input" in label.lower() else "output")
    return validate_cli_schema(
        instance, schema, label, role=inferred, schema_validator=schema_validator,
        input_diagnostics=input_diagnostics,
    )


def _apply_registered_output_authority(
    *,
    skill_dir: Path,
    repo_root: Path,
    input_payload: Mapping[str, Any],
    draft_payload: Mapping[str, Any],
    final_schema: Mapping[str, Any],
    final_schema_validator: Callable[[Any], None],
    frozen_finalization: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Apply exactly one registered output authority."""
    if frozen_finalization is not None:
        relative_skill_path = skill_dir.resolve().relative_to(repo_root.resolve()).as_posix()
        finalized = finalize_and_validate_registered_output(
            repo_root=repo_root,
            skill_path=relative_skill_path,
            frozen_contract=frozen_finalization,
            input_payload=input_payload,
            draft_payload=draft_payload,
            final_schema=final_schema,
        )
        return (
            finalized.output,
            {},
            {
                "managed": True,
                "contract_version": frozen_finalization.get("contractVersion"),
                "status": finalized.status,
                "errors": list(finalized.errors),
            },
        )

    guarded, runtime_guard = apply_opentrajectory_runtime_guard(
        skill_id=skill_dir.name,
        input_payload=dict(input_payload),
        output_payload=dict(draft_payload),
    )
    output = _validate_schema(
        guarded,
        dict(final_schema),
        "Output",
        schema_validator=final_schema_validator,
    )
    return output, runtime_guard, {}


def _schema_name(skill_dir: Path) -> str:
    name = skill_dir.name.replace("-", "_")
    return f"{name}_output"[:64]


def _build_responses_payload(
    *,
    model: str,
    system_prompt: str,
    user_prompt: str,
    output_schema: dict[str, Any],
    schema_name: str,
    max_completion_tokens: int,
    reasoning_effort: str | None,
    temperature: float | int | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "input": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_output_tokens": max_completion_tokens,
        "store": False,
    }
    if reasoning_effort is not None and _is_reasoning_model(model):
        payload["reasoning"] = {"effort": reasoning_effort}
    elif temperature is not None:
        payload["temperature"] = temperature
    response_format = structured_response_format(
        "openai_responses",
        schema_name=schema_name,
    )
    return apply_provider_schema_transport(
        "openai_responses",
        payload=payload,
        response_format=response_format,
        provider_schema=output_schema,
    )


def _assert_schema_unchanged_by_payload_preflight(
    diagnostics: Mapping[str, Any],
) -> None:
    paths = diagnostics.get("payload_unicode_replacement_paths")
    if not isinstance(paths, list):
        return
    schema_prefixes = (
        "$.text.format.schema",
        "$.response_format.json_schema.schema",
    )
    if any(
        isinstance(path, str) and path.startswith(schema_prefixes)
        for path in paths
    ):
        raise LocalPayloadPreflightError(
            "LLM payload preflight would mutate an admitted Provider Schema",
            diagnostics=dict(diagnostics),
        )


def run_skill_with_llm(
    skill_dir: Path,
    input_payload: dict[str, Any],
    workspace_dir: Path,
    task_id: str,
    *,
    source_contract: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], float, dict[str, Any]]:
    """Execute a structured skill and persist its validated JSON artifact.

    Internal-trial execution permits one Provider submission. Provider,
    transport, JSON, schema, and runtime-guard failures are all fail-closed.
    """
    start_time = time.time()
    _require_jsonschema()

    provider = os.environ.get("OPENTRAJECTORY_LLM_PROVIDER", "huiyan_chat_completions").strip().lower()
    if provider == "openai_responses":
        api_key = os.environ.get("OPENAI_API_KEY")
        api_base = os.environ.get("OPENAI_API_BASE_URL", "https://api.openai.com")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY environment variable not set")
    elif provider == "huiyan_chat_completions":
        api_key = os.environ.get("LLM_PROVIDER_API_KEY")
        api_base = os.environ.get("LLM_PROVIDER_BASE_URL")
        if not api_key:
            raise RuntimeError("LLM_PROVIDER_API_KEY environment variable not set")
        if not api_base:
            raise RuntimeError("LLM_PROVIDER_BASE_URL environment variable not set")
    else:
        raise RuntimeError(f"Unsupported OPENTRAJECTORY_LLM_PROVIDER: {provider}")

    skill_md_path = skill_dir / "SKILL.md"
    input_schema_path = skill_dir / "input.schema.json"
    output_schema_path = skill_dir / "output.schema.json"
    skill_yaml_path = skill_dir / "skill.yaml"

    if not skill_md_path.exists():
        raise RuntimeError(f"SKILL.md not found: {skill_md_path}")
    if not input_schema_path.exists():
        raise RuntimeError(f"input.schema.json not found: {input_schema_path}")
    if not output_schema_path.exists():
        raise RuntimeError(f"output.schema.json not found: {output_schema_path}")
    if not skill_yaml_path.exists():
        raise RuntimeError(f"skill.yaml not found: {skill_yaml_path}")

    skill_md = skill_md_path.read_text(encoding="utf-8")
    repo_root = find_openair_repository_root(skill_dir)
    input_schema_document = load_file_backed_schema(
        input_schema_path, allowed_root=repo_root
    )
    output_schema_document = load_file_backed_schema(
        output_schema_path, allowed_root=repo_root
    )
    input_schema = input_schema_document.schema
    output_schema = output_schema_document.schema
    frozen_finalization: dict[str, Any] | None = None
    if isinstance(source_contract, Mapping):
        raw_finalization = source_contract.get("frozenFinalization")
        if raw_finalization is not None:
            frozen_finalization = validate_frozen_finalization_contract(raw_finalization)
    generation_schema = (
        frozen_finalization["draftSchema"]["schema"]
        if frozen_finalization is not None
        else output_schema
    )
    input_diagnostics: dict[str, Any] = {}
    input_payload = _validate_schema(
        input_payload,
        input_schema,
        "Input",
        schema_validator=input_schema_document.validate,
        input_diagnostics=input_diagnostics,
    )

    import yaml

    skill_meta = yaml.safe_load(skill_yaml_path.read_text(encoding="utf-8")) or {}
    output_file = skill_meta.get("output_file")
    if not output_file:
        raise RuntimeError(f"output_file not configured in skill.yaml: {skill_yaml_path}")

    llm_cfg = skill_meta.get("llm") or {}
    if not isinstance(llm_cfg, dict):
        llm_cfg = {}

    if provider == "openai_responses":
        model = os.environ.get("OPENTRAJECTORY_LIVE_LLM_MODEL") or llm_cfg.get("model") or "gpt-5-mini"
    else:
        model = (
            os.environ.get("OPENTRAJECTORY_LIVE_LLM_MODEL")
            or llm_cfg.get("model")
            or os.environ.get("OPENTRAJECTORY_REVIEW_LLM_MODEL")
            or os.environ.get("VITE_TEXT_LLM_MODEL")
            or os.environ.get("LLM_PROVIDER_MODEL")
            or "gpt-4o-mini"
        )
    # Internal trial execution owns one physical submission only. Compatibility
    # lists remain diagnostic helpers, but cannot authorize model resubmission.
    model_fallback_chain = [str(model)]

    temperature = llm_cfg.get("temperature", 0.3)
    max_completion_tokens = int(llm_cfg.get("max_completion_tokens", 8000))
    reasoning_effort = llm_cfg.get("reasoning_effort")
    structured_output_max_attempts = _structured_output_max_attempts(llm_cfg)

    system_prompt = f"""You are an executor for this skill. Your task is to transform the input JSON into the JSON output described by the Skill instructions.

{skill_md}

CRITICAL REQUIREMENTS:
1. Output ONLY valid JSON following the Skill's output semantics
2. Do NOT wrap JSON in markdown code blocks
3. Do NOT add explanations or commentary
4. Ensure all required fields are present
5. Follow the output fields and meanings declared by the Skill exactly
6. Use valid JSON numeric syntax; never spell out numbers inside numeric fields"""

    user_prompt = f"""Input data:
```json
{json.dumps(input_payload, ensure_ascii=False, indent=2)}
```

Generate the output JSON now (raw JSON only, no markdown):"""

    from app.execution.scientific_input_fidelity import with_input_limits

    system_prompt = with_input_limits(system_prompt, input_diagnostics.get("warnings") or [])

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    fallback_attempts: list[dict[str, Any]] = []
    retry_reasons: list[dict[str, Any]] = []
    result: dict[str, Any] | None = None
    content = ""
    output_dict: dict[str, Any] | None = None
    choice: dict[str, Any] = {"finish_reason": None}
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    request_timeout_seconds = _request_timeout_seconds(str(model))
    runtime_guard: dict[str, Any] = {}
    finalization: dict[str, Any] = {}
    p08a_shaping_actions: list[dict[str, Any]] = []
    payload_preflight: dict[str, Any] = {
        "payload_unicode_replacement_count": 0,
        "payload_unicode_replacement_paths": [],
        "payload_utf8_preflight_passed": False,
    }

    for attempt_index, candidate_model in enumerate(model_fallback_chain, start=1):
        request_timeout_seconds = _request_timeout_seconds(candidate_model)
        model_succeeded = False

        if provider == "openai_responses":
            payload = _build_responses_payload(
                model=candidate_model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                output_schema=generation_schema,
                schema_name=_schema_name(skill_dir),
                max_completion_tokens=max_completion_tokens,
                reasoning_effort=reasoning_effort,
                temperature=temperature,
            )
            endpoint = f"{api_base.rstrip('/')}/v1/responses"
            payload, payload_preflight = _prepare_payload_for_http(payload)

            try:
                from llm_core.openai_compatible_client import ProviderEndpoint, post_responses

                result = post_responses(
                    ProviderEndpoint(
                        base_url=api_base,
                        api_key=api_key,
                        timeout_seconds=request_timeout_seconds,
                    ),
                    payload,
                )
            except httpx.HTTPStatusError as exc:
                status_code = exc.response.status_code
                response_text = exc.response.text
                fallback_attempts.append(
                    {"model": candidate_model, "attempt": attempt_index, "status": status_code, "error": response_text[:500]}
                )
                if _http_error_is_retryable(status_code, response_text) and attempt_index < len(model_fallback_chain):
                    continue
                raise RuntimeError(
                    f"LLM API call failed after {attempt_index} model attempt(s): {status_code}; "
                    f"provider={provider}; model={candidate_model}; fallback_attempts={fallback_attempts}; "
                    f"response={response_text[:1000]}"
                ) from exc
            except UnicodeEncodeError as exc:
                raise LocalPayloadPreflightError(
                    f"LLM payload failed local UTF-8 preflight: {exc}",
                    diagnostics=payload_preflight,
                ) from exc
            except httpx.RequestError as exc:
                fallback_attempts.append({"model": candidate_model, "attempt": attempt_index, "error": str(exc)[:500]})
                if attempt_index < len(model_fallback_chain):
                    continue
                raise RuntimeError(
                    f"LLM API transport failed after {attempt_index} model attempt(s): {exc}; "
                    f"provider={provider}; model={candidate_model}; fallback_attempts={fallback_attempts}"
                ) from exc

            retryable_error = _extract_retryable_error(result)
            if retryable_error:
                fallback_attempts.append({"model": candidate_model, "attempt": attempt_index, "error": retryable_error[:500]})
                if attempt_index < len(model_fallback_chain):
                    continue
                raise RuntimeError(f"LLM API returned retryable provider error; fallback_attempts={fallback_attempts}")
            non_retryable_error = _extract_non_retryable_error(result)
            if non_retryable_error:
                raise RuntimeError(f"LLM API returned non-retryable provider error: {non_retryable_error}")

            try:
                content = _extract_responses_output_text(result)
                choice = {"finish_reason": result.get("status") if isinstance(result, dict) else None}
                prompt_tokens, completion_tokens, total_tokens = _billable_token_counts(
                    result,
                    response_text=content,
                )
                parsed = _parse_llm_json_content(content)
                parsed, actions = _shape_p08a_output_for_guard(
                    skill_dir_name=skill_dir.name,
                    input_payload=input_payload,
                    parsed=parsed,
                )
                p08a_shaping_actions.extend(actions)
                try:
                    output_dict, runtime_guard, finalization = _apply_registered_output_authority(
                        skill_dir=skill_dir,
                        repo_root=repo_root,
                        input_payload=input_payload,
                        draft_payload=parsed,
                        final_schema=output_schema,
                        final_schema_validator=output_schema_document.validate,
                        frozen_finalization=frozen_finalization,
                    )
                except RuntimeGuardRejected as exc:
                    _persist_structured_guard_failure(
                        workspace_dir=workspace_dir,
                        task_id=task_id,
                        content=content,
                        parsed=parsed,
                        guard_diagnostics=exc.diagnostics,
                    )
                    raise
                model = candidate_model
                content = _strip_json_markdown(content)
                model_succeeded = True
            except (json.JSONDecodeError, ValueError, RuntimeError) as exc:
                fallback_attempts.append(
                    {
                        "model": candidate_model,
                        "attempt": attempt_index,
                        "finish_reason": choice.get("finish_reason"),
                        "error": str(exc)[:500],
                    }
                )
                if attempt_index < len(model_fallback_chain):
                    continue
                raise RuntimeError(
                    f"Failed to parse validated LLM output: {exc}; fallback_attempts={fallback_attempts}"
                ) from exc
        else:
            messages: list[dict[str, str]] = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]

            for correction_attempt in range(1, structured_output_max_attempts + 1):
                payload = {
                    "model": candidate_model,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_completion_tokens,
                }
                if (
                    provider != "huiyan_chat_completions"
                    and reasoning_effort is not None
                    and _is_reasoning_model(candidate_model)
                ):
                    payload["reasoning_effort"] = reasoning_effort
                endpoint = f"{api_base.rstrip('/')}/v1/chat/completions"
                payload, payload_preflight = _prepare_payload_for_http(payload)
                response_format = structured_response_format(
                    provider,
                    schema_name=_schema_name(skill_dir),
                )
                payload = apply_provider_schema_transport(
                    provider,
                    payload=payload,
                    response_format=response_format,
                    provider_schema=generation_schema,
                )

                try:
                    from llm_core.openai_compatible_client import ProviderEndpoint, post_chat_completions

                    result = post_chat_completions(
                        ProviderEndpoint(
                            base_url=api_base,
                            api_key=api_key,
                            timeout_seconds=request_timeout_seconds,
                        ),
                        payload,
                    )
                except httpx.HTTPStatusError as exc:
                    status_code = exc.response.status_code
                    response_text = exc.response.text
                    fallback_attempts.append(
                        {
                            "model": candidate_model,
                            "attempt": attempt_index,
                            "correction_attempt": correction_attempt,
                            "status": status_code,
                            "error": response_text[:500],
                        }
                    )
                    if _http_error_is_retryable(status_code, response_text) and attempt_index < len(model_fallback_chain):
                        break
                    raise RuntimeError(
                        f"LLM API call failed after {attempt_index} model attempt(s): {status_code}; "
                        f"provider={provider}; model={candidate_model}; fallback_attempts={fallback_attempts}; "
                        f"response={response_text[:1000]}"
                    ) from exc
                except UnicodeEncodeError as exc:
                    raise LocalPayloadPreflightError(
                        f"LLM payload failed local UTF-8 preflight: {exc}",
                        diagnostics=payload_preflight,
                    ) from exc
                except httpx.RequestError as exc:
                    fallback_attempts.append(
                        {
                            "model": candidate_model,
                            "attempt": attempt_index,
                            "correction_attempt": correction_attempt,
                            "error": str(exc)[:500],
                        }
                    )
                    if attempt_index < len(model_fallback_chain):
                        break
                    raise RuntimeError(
                        f"LLM API transport failed after {attempt_index} model attempt(s): {exc}; "
                        f"provider={provider}; model={candidate_model}; fallback_attempts={fallback_attempts}"
                    ) from exc

                retryable_error = _extract_retryable_error(result)
                if retryable_error:
                    fallback_attempts.append(
                        {
                            "model": candidate_model,
                            "attempt": attempt_index,
                            "correction_attempt": correction_attempt,
                            "error": retryable_error[:500],
                        }
                    )
                    if attempt_index < len(model_fallback_chain):
                        break
                    raise RuntimeError(f"LLM API returned retryable provider error; fallback_attempts={fallback_attempts}")
                non_retryable_error = _extract_non_retryable_error(result)
                if non_retryable_error:
                    raise RuntimeError(f"LLM API returned non-retryable provider error: {non_retryable_error}")

                try:
                    content, choice = extract_final_chat_content(result)
                except ValueError as exc:
                    # Empty/truncated provider replies are transient; route them
                    # through the existing correction-retry path instead of
                    # hard-failing the whole Skill call on first emptiness.
                    message = str(exc)
                    if "empty LLM response" in message or "without final content" in message:
                        content = ""
                        first_choice = (
                            (result.get("choices") or [None])[0]
                            if isinstance(result, dict)
                            else None
                        )
                        choice = first_choice if isinstance(first_choice, dict) else {}
                    else:
                        fallback_attempts.append(
                            {
                                "model": candidate_model,
                                "attempt": attempt_index,
                                "correction_attempt": correction_attempt,
                                "error": message[:500],
                            }
                        )
                        if attempt_index < len(model_fallback_chain):
                            break
                        raise
                except RuntimeError as exc:
                    fallback_attempts.append(
                        {
                            "model": candidate_model,
                            "attempt": attempt_index,
                            "correction_attempt": correction_attempt,
                            "error": str(exc)[:500],
                        }
                    )
                    if attempt_index < len(model_fallback_chain):
                        break
                    raise

                in_tokens, out_tokens, tot_tokens = _billable_token_counts(
                    result,
                    request_text=f"{system_prompt}\n{user_prompt}",
                    response_text=content or "",
                )
                prompt_tokens = _sum_optional(prompt_tokens, in_tokens)
                completion_tokens = _sum_optional(completion_tokens, out_tokens)
                total_tokens = _sum_optional(total_tokens, tot_tokens)

                failure_type: str | None = None
                failure_detail: str | None = None
                if not content:
                    failure_type = "json_parse_error"
                    failure_detail = (
                        "empty content; "
                        f"choices_count={len(result.get('choices') or []) if isinstance(result, dict) else 0}; "
                        f"finish_reason={choice.get('finish_reason')}; "
                        f"message_has_content={bool(content)}"
                    )
                else:
                    try:
                        parsed = json.loads(_strip_json_markdown(content))
                    except json.JSONDecodeError as exc:
                        failure_type = "json_parse_error"
                        failure_detail = str(exc)
                    else:
                        parsed, actions = _shape_p08a_output_for_guard(
                            skill_dir_name=skill_dir.name,
                            input_payload=input_payload,
                            parsed=parsed,
                        )
                        p08a_shaping_actions.extend(actions)
                        try:
                            output_dict, runtime_guard, finalization = _apply_registered_output_authority(
                                skill_dir=skill_dir,
                                repo_root=repo_root,
                                input_payload=input_payload,
                                draft_payload=parsed,
                                final_schema=output_schema,
                                final_schema_validator=output_schema_document.validate,
                                frozen_finalization=frozen_finalization,
                            )
                        except RuntimeGuardRejected as exc:
                            _persist_structured_guard_failure(
                                workspace_dir=workspace_dir,
                                task_id=task_id,
                                content=content,
                                parsed=parsed,
                                guard_diagnostics=exc.diagnostics,
                            )
                            raise
                        try:
                            if output_dict is None:
                                raise RuntimeError("registered output authority returned no output")
                        except RuntimeError as exc:
                            output_dict = None
                            failure_type = "schema_validation_error"
                            failure_detail = str(exc)
                        else:
                            model = candidate_model
                            content = _strip_json_markdown(content)
                            model_succeeded = True
                            break

                assert failure_type is not None and failure_detail is not None
                retry_reasons.append(
                    {
                        "model": candidate_model,
                        "attempt": correction_attempt,
                        "failure_type": failure_type,
                        "detail": failure_detail,
                    }
                )
                if correction_attempt >= structured_output_max_attempts:
                    break
                messages = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                    {"role": "assistant", "content": content[:_RETRY_EXCERPT_CHARS]},
                    {
                        "role": "user",
                        "content": _retry_prompt(
                            failure_type=failure_type,
                            detail=failure_detail,
                            previous_content=content,
                        ),
                    },
                ]

        if model_succeeded:
            break

    if result is None or output_dict is None:
        if retry_reasons:
            last = retry_reasons[-1]
            raise RuntimeError(
                f"Structured output failed after {len(retry_reasons)} correction attempt(s): "
                f"{last['failure_type']}: {last['detail']}; fallback_attempts={fallback_attempts}"
            )
        raise RuntimeError(f"LLM API call failed: no model produced valid JSON; fallback_attempts={fallback_attempts}")

    sanitizer_actions: list[Any] = []
    if skill_dir.name == "retrieval-matrix-generation":
        try:
            from app.execution.retrieval_matrix_sanitizer import sanitize_retrieval_matrix_output

            output_dict, sanitizer_actions = sanitize_retrieval_matrix_output(output_dict, input_payload)
        except Exception as exc:
            raise RuntimeError(f"retrieval matrix sanitizer failed: {exc}") from exc
        output_dict = _validate_schema(
            output_dict,
            output_schema,
            "Output",
            schema_validator=output_schema_document.validate,
        )

    task_workspace = workspace_dir / task_id
    task_workspace.mkdir(parents=True, exist_ok=True)
    output_path = task_workspace / output_file
    output_path.write_text(json.dumps(output_dict, ensure_ascii=False, indent=2), encoding="utf-8")

    elapsed = time.time() - start_time
    diagnostics = {
        "provider": provider,
        "model": model,
        "model_fallback_chain": model_fallback_chain,
        "model_fallback_attempts": fallback_attempts,
        "model_fallback_used": bool(fallback_attempts),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "response_chars": len(content),
        "choice_finish_reason": choice.get("finish_reason"),
        "effective_max_tokens": max_completion_tokens,
        "request_timeout_seconds": request_timeout_seconds,
        "skill_llm_config_applied": bool(llm_cfg),
        "input_schema_passed": input_diagnostics["schemaPassed"],
        "input_contract_warnings": input_diagnostics["warnings"],
        "output_schema_passed": True,
        "structured_output_attempts": 1 + len(retry_reasons),
        "structured_output_retries": len(retry_reasons),
        "structured_output_max_attempts": structured_output_max_attempts,
        "structured_output_retry_reasons": retry_reasons,
        "runtime_guard": runtime_guard,
        "finalization": finalization,
        "sanitizer_actions": sanitizer_actions,
        "p08a_shaping_actions": p08a_shaping_actions,
        **payload_preflight,
    }
    return output_dict, elapsed, diagnostics
