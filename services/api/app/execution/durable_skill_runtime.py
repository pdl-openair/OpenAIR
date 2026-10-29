"""Fail-closed runtime adapter for Skills requested by durable specialist work.

The durable orchestrator owns authorization, call persistence, fencing and
recovery.  This module only resolves a registered Skill and invokes its
declared supported runner.  It never imports replay fixtures.
"""

from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import logging
import os
from pathlib import Path
from pathlib import PurePosixPath
import re
from typing import Any, Mapping

import httpx
from httpx import (
    ConnectError as HttpxConnectError,
    ConnectTimeout as HttpxConnectTimeout,
    PoolTimeout as HttpxPoolTimeout,
    ReadError as HttpxReadError,
    ReadTimeout as HttpxReadTimeout,
    RemoteProtocolError as HttpxRemoteProtocolError,
    LocalProtocolError as HttpxLocalProtocolError,
    WriteError as HttpxWriteError,
    WriteTimeout as HttpxWriteTimeout,
)
import yaml

from app.execution.durable_skill_transport_guards import (
    _llm_http_timeout,
    _llm_task_deadline,
    mark_skill_provider_io_started,
)

from app.modules.yaml_source_cache import SAFE_LOADER, read_yaml_source

logger = logging.getLogger(__name__)


def _record_durable_skill_usage(
    *,
    request: dict[str, Any],
    provider_id: str,
    model: str,
    usage_wire: dict[str, Any] | None,
    status: str = "success",
) -> None:
    """Best-effort metering for one durable HUIYAN skill call (U-04, E path).

    Sub-calls prepared by ``execute_new_durable_llm_child`` are attributed to
    their parent capability call and recorded under the ``durable_child``
    source, so per-sub-call tokens are distinguishable from dispatch-level
    rows instead of riding on time-window alignment.
    """
    owner_id = str(request.get("ownerId") or "").strip()
    if not owner_id:
        return
    from app.modules.token_ledger_repository import meter_call

    evidence = request.get("providerEvidence")
    source = "durable_dispatch"
    call_id = None
    if (
        isinstance(evidence, Mapping)
        and str(evidence.get("evidenceKind") or "") == EVIDENCE_KIND_SUB_CALL
    ):
        source = "durable_child"
        call_id = str(evidence.get("capabilityCallId") or "").strip() or None
    meter_call(
        user_id=owner_id,
        source=source,
        usage_wire=usage_wire,
        workspace_id=str(request.get("workspaceId") or "").strip() or None,
        task_id=str(request.get("taskId") or "").strip() or None,
        call_id=call_id,
        provider=provider_id,
        model=model,
        status=status,
    )


def _child_metering_env(request: Mapping[str, Any]) -> dict[str, str]:
    """Identity the adapter subprocess needs to meter its LLM sub-calls.

    ``execute_new_durable_llm_child`` runs inside the adapter's isolated
    process, where only the environment crosses the boundary.  Forward the
    dispatch's task/owner/workspace so each child LLM call can write a ledger
    row attributed to the same dimensions as its parent dispatch.
    """
    env: dict[str, str] = {}
    for env_key, request_key in (
        ("OPENAIR_TASK_ID", "taskId"),
        ("OPENAIR_OWNER_ID", "ownerId"),
        ("OPENAIR_WORKSPACE_ID", "workspaceId"),
    ):
        value = str(request.get(request_key) or "").strip()
        if value:
            env[env_key] = value
    return env


def _billable_usage_wire_from_body(
    body: Any,
    *,
    request_text: str = "",
    response_text: str = "",
    provider_id: str,
    model: str,
) -> dict[str, Any] | None:
    """Normalize provider usage, estimating when the gateway omits ``usage``."""
    from llm_core.token_usage import billable_usage_from_openai_body, to_wire_usage

    usage = billable_usage_from_openai_body(
        body,
        request_text=request_text,
        response_text=response_text,
    )
    usage_wire = to_wire_usage(usage)
    if usage_wire and usage.get("estimated"):
        logger.warning(
            "provider usage missing; estimated durable_skill tokens provider=%s model=%s total=%s",
            provider_id,
            model,
            usage_wire.get("totalTokens"),
        )
    return usage_wire

from app.execution.durable_llm_batch_policy import (
    DECLARATION_KEY as DURABLE_LLM_BATCH_POLICY_KEY,
    normalize_durable_llm_batch_policy_declaration,
)
from app.execution.durable_llm_execution_policy import DECLARATION_KEY as DURABLE_LLM_EXECUTION_POLICY_KEY, normalize_durable_llm_execution_policy_declaration, normalize_resolved_durable_llm_execution_policy, resolve_durable_llm_execution_policy
from app.execution.opentrajectory_structured_runtime_guard import (
    GUARD_VERSION as OPENTRAJECTORY_STRUCTURED_GUARD_VERSION,
    RuntimeGuardRejected,
    apply_opentrajectory_runtime_guard,
)
from app.models.result_protocol import (
    artifact_quality_for_protocol,
    merge_result_protocols,
)
from app.execution.execution_context import prepare_task_workspace
from app.execution.evidence_batch_identity import evidence_paper_operation_identity
from app.execution.python_adapter_runner import run_python_skill_adapter
from app.execution.python_adapter_skills import resolve_python_adapter_skill
from app.execution.provider_visible_skill_input import (
    SCIENTIFIC_MINIMAL_V1,
    build_scientific_minimal_user_payload,
    project_provider_parameters,
    resolve_provider_message_contract,
)
from app.execution.structured_output_provider_adapter import (
    PROVIDER_SCHEMA_UNSUPPORTED,
    ProviderSchemaCapabilityError,
    apply_provider_schema_transport,
    structured_response_format,
)
from app.execution.research_dispatch_provider import (
    STRUCTURED_OUTPUT_CORRECTION_EXHAUSTED,
)
from app.modules.provider_registry import ProviderRegistryError, load_provider_registry, resolve_provider_runtime_config
from app.modules.provider_transport_evidence import (
    EVIDENCE_KIND_SUB_CALL,
    ProviderAttemptEvidenceError,
    ProviderHttpAttemptRepository,
    request_sha256,
)
from app.modules.repo_paths import repo_root
from datetime import datetime
from datetime import timezone


_HUIYAN_DURABLE_ADAPTER = "huiyan_chat_completions"
_STRUCTURED_OUTPUT_PROVIDER_ADAPTERS = frozenset({
    "huiyan_chat_completions",
    "openai_responses",
})
_DURABLE_LLM_MODEL_PROFILES = frozenset({"terra", "sol"})
_DEFAULT_DURABLE_LLM_MODEL_PROFILE = "terra"
_RUNTIME_KIND_LLM_REASONING = "llm_reasoning"
_RUNTIME_KIND_REASONING_ADAPTER = "reasoning_adapter"
_RUNTIME_KIND_DETERMINISTIC = "deterministic"
_RUNTIME_KIND_EXTERNAL_ADAPTER = "external_adapter"
_RUNTIME_KIND_SKILL_SCRIPT = "skill_script"
_SKILL_FINALIZATION_RUNTIME_FAILED = "skill_finalization_runtime_failed"
_RUNTIME_CONTRACT_KEYS = frozenset({
    "kind", "requires_external_action", "adapter_id", "evidence_contract_version",
})
# 可选键：artifact_files 为声明式产物注册（glob 相对
# <workspace>/<task_id>/<work_unit_id>/，由 durable_execution.
# _persist_declared_skill_files 消费）；internal_skill_calls 仅限
# reasoning_adapter。
_RUNTIME_CONTRACT_OPTIONAL_KEYS = frozenset({"artifact_files", "internal_skill_calls"})
_EXTERNAL_EVIDENCE_CONTRACT_V1 = "external-action-evidence/v1"
_SKILL_EXECUTION_EVIDENCE_CONTRACT_V1 = "skill-execution-evidence/v1"
_DETERMINISTIC_EVIDENCE_CONTRACT_V1 = "deterministic-output/v1"
_NO_EXTERNAL_EVIDENCE_CONTRACT = "none"
_EVIDENCE_BATCH_DRAFT_FIELDS = {
    "evidence_node_extraction": "research_records",
    "pvd_evidence_node_extraction": "evidence_items",
}
_EVIDENCE_BATCH_MAX_PARALLEL = 4
_EVIDENCE_BATCH_MAX_ATTEMPTS = 2
# Adapters that may claim an external action only when their output carries
# adapter-specific verifiable evidence. Keep this list aligned with registry
# `runtime_contract.kind: external_adapter` entries that have a real runner.
_EXTERNAL_ADAPTER_EVIDENCE_VALIDATORS = frozenset({
    "python_adapter:materials_magnet_design_mvp",
    "python_adapter:crystal_design_formula_crossover",
    "python_adapter:crystal_design_formula_crossover_py",
    "python_adapter:crystal_design_structure_crossover_py",
    "python_adapter:materials_mp_constraint_search",
    "python_adapter:academic_source_retrieval_skill",
    "python_adapter:opentrajectory_source_acquisition",
    "python_adapter:source_access_readiness_resolver",
    "python_adapter:deepxiv_progressive_reading",
    "python_adapter:pvd_literature_evidence_acquisition_closure",
    "python_adapter:environment_setup",
    "python_adapter:exploratory_run",
    "python_adapter:repository_revision_acquisition",
    "python_adapter:cs_paper_source_resolution",
    "python_adapter:resource_identity_acquisition",
})
_EXTERNAL_EVIDENCE_OUTCOMES = frozenset({"completed", "partial"})
_FORMAL_SCHEMA_GATED_EXTERNAL_ADAPTERS = {
    "python_adapter:cs_paper_source_resolution": (
        "cs_research", "cs-paper-source-resolution", "output.schema.json",
    ),
    "python_adapter:repository_revision_acquisition": (
        "cs_research", "repository-revision-acquisition", "output.schema.json",
    ),
    "python_adapter:resource_identity_acquisition": (
        "cs_research", "resource-identity-acquisition", "output.schema.json",
    ),
    "python_adapter:exploratory_run": (
        "cs_research", "exploratory-run", "output.schema.json",
    ),
}
_GENERATED_DURABLE_TEMPLATE = """# Result

## Findings

## Evidence

## Next Steps
"""
_RESEARCH_CONFIG_KEYS = frozenset({
    "project", "schema", "research_type", "ai_mode", "domain", "subdomain", "target_venue",
    "resources", "checkpoint_stages", "minimum_success_criteria", "modification_log",
})
_TARGET_VENUE_KEYS = frozenset({"primary", "tier"})
_RESOURCE_KEYS = frozenset({"compute", "data_status"})
_SUCCESS_CRITERIA_KEYS = frozenset({
    "min_baselines", "min_ablation_runs", "require_proposed", "required_metrics",
})
_MODIFICATION_LOG_KEYS = frozenset({"date", "field", "old", "new", "reason"})
_INTERNAL_EXECUTION_KEYS = frozenset({
    "api_key", "authorization", "access_token", "session_id", "run_id", "task_id", "trace_id",
    "workspace", "workspace_id", "workspace_path", "provider", "provider_id", "model", "model_id",
    "runtime", "secret", "worker", "worker_id", "call", "call_id", "owner", "owner_id",
    "requesting_agent_id", "capability_call_id",
})


def _registered_durable_model_profile(skill: dict[str, Any] | None) -> str | None:
    """Resolve the bounded model profile declared by the frozen Registry Skill."""
    if not isinstance(skill, dict):
        return None
    source_contract = skill.get("sourceContract")
    if not isinstance(source_contract, dict):
        return None
    raw = source_contract.get("durable_model_profile")
    if raw is None:
        return _DEFAULT_DURABLE_LLM_MODEL_PROFILE
    if not isinstance(raw, str):
        return None
    profile = raw.strip()
    if profile not in _DURABLE_LLM_MODEL_PROFILES:
        return None
    return profile


def build_frozen_skill_schema_contract(skill_path: str) -> dict[str, dict[str, Any]]:
    """Capture self-contained Skill schemas for durable snapshot authority."""
    from app.execution.skill_schema_contract import build_frozen_skill_schema_contract as freeze

    return freeze(skill_path)


def registration_requires_frozen_schemas(source_contract: dict[str, Any]) -> bool:
    """Return whether one Registry row publishes schema-governed execution."""
    return any(
        source_contract.get(key) is not None
        for key in (
            "external_source_records",
            "structured_output",
            "finalization",
            "scientific_state_projection",
            "capability_contract",
            DURABLE_LLM_BATCH_POLICY_KEY,
        )
    )


def freeze_registered_source_contract(
    source_contract: dict[str, Any],
    *,
    skill_path: str,
    require_frozen_schemas: bool = False,
) -> dict[str, Any]:
    """Freeze one Registry-owned source contract through a single authority.

    ``require_frozen_schemas`` is asserted by canonical Binding v2 handoff
    authority. This keeps live durable runtime resolution and frozen Registry
    snapshots byte-for-byte aligned without inventing a second freezer.
    """
    from app.execution.skill_finalization_runtime import (
        build_frozen_finalization_contract,
        validate_finalization_declaration,
    )

    frozen = dict(source_contract)
    execution_policy = frozen.get(DURABLE_LLM_EXECUTION_POLICY_KEY)
    if execution_policy is not None:
        frozen[DURABLE_LLM_EXECUTION_POLICY_KEY] = (
            normalize_durable_llm_execution_policy_declaration(execution_policy)
        )
    batch_policy = frozen.get(DURABLE_LLM_BATCH_POLICY_KEY)
    if batch_policy is not None:
        frozen[DURABLE_LLM_BATCH_POLICY_KEY] = (
            normalize_durable_llm_batch_policy_declaration(batch_policy)
        )
    if registration_requires_frozen_schemas(frozen) or require_frozen_schemas:
        frozen["frozenSchemas"] = build_frozen_skill_schema_contract(skill_path)
    external_sources = frozen.get("external_source_records")
    if external_sources is not None:
        from app.execution.external_source_identity import (
            validate_external_source_records_declaration,
        )

        frozen["external_source_records"] = validate_external_source_records_declaration(
            external_sources,
            output_schema=frozen["frozenSchemas"]["output"]["schema"],
        )
    projection = frozen.get("scientific_state_projection")
    if projection is not None:
        from app.modules.scientific_state_fact_projection import (
            validate_scientific_state_projection_declaration,
        )

        output_schema = frozen["frozenSchemas"]["output"]["schema"]
        frozen["scientific_state_projection"] = (
            validate_scientific_state_projection_declaration(
                projection,
                output_schema=output_schema,
            )
        )
    declaration = frozen.get("finalization")
    if declaration is not None:
        normalized = validate_finalization_declaration(declaration)
        frozen["finalization"] = normalized
        frozen["frozenFinalization"] = build_frozen_finalization_contract(
            skill_path,
            normalized,
            repo_root=repo_root(),
        )
    elif frozen.get("frozenFinalization") is not None:
        raise ValueError("frozen_finalization_without_declaration")
    return frozen


def _frozen_schema_contract(skill: dict[str, Any] | None) -> dict[str, dict[str, Any]] | None:
    """Return the Registry-frozen input/output schemas without inferring authority."""
    if not isinstance(skill, dict):
        return None
    source = skill.get("sourceContract")
    frozen = source.get("frozenSchemas") if isinstance(source, dict) else None
    if not isinstance(frozen, dict):
        return None
    normalized: dict[str, dict[str, Any]] = {}
    for name in ("input", "output"):
        entry = frozen.get(name)
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("schema"), dict)
            or re.fullmatch(r"[0-9a-f]{64}", str(entry.get("sha256") or "").lower()) is None
        ):
            return None
        normalized[name] = {
            "sha256": str(entry["sha256"]).lower(),
            "schema": entry["schema"],
        }
    return normalized


def _structured_json_contract(skill: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return one frozen structured-JSON contract for an explicitly published Skill.

    The registry opt-in is intentionally separate from ``runtime_contract``: the
    latter proves the transport/runtime class, while this contract proves that
    the reasoning Skill must emit schema-validated JSON suitable for canonical
    downstream handoff.  Historical snapshots without this frozen contract stay
    non-executable rather than being reconstructed from mutable files at runtime.
    """
    if not isinstance(skill, dict):
        return None
    source = skill.get("sourceContract")
    raw = source.get("structured_output") if isinstance(source, dict) else None
    if not isinstance(raw, dict) or raw != {
        "contract_version": 1,
        "guard_version": OPENTRAJECTORY_STRUCTURED_GUARD_VERSION,
    }:
        return None
    normalized = _frozen_schema_contract(skill)
    if normalized is None:
        return None
    return {
        "contractVersion": 1,
        "guardVersion": OPENTRAJECTORY_STRUCTURED_GUARD_VERSION,
        "input": normalized["input"],
        "output": normalized["output"],
    }


def _runtime_guard_recovery_warnings(diagnostics: Mapping[str, Any]) -> list[str]:
    """Return user-readable warnings for recoverable guard actions only."""
    recoverable = [
        str(value).strip()
        for value in [
            *(diagnostics.get("recoverable_failures") or []),
            *(diagnostics.get("manual_review_triggers") or []),
        ]
        if str(value).strip()
    ]
    if not recoverable:
        return []

    checks_by_id = {
        str(row.get("check_id") or "").strip(): row
        for row in diagnostics.get("checks") or []
        if isinstance(row, Mapping) and str(row.get("check_id") or "").strip()
    }
    warnings: list[str] = []
    for check_id in recoverable:
        row = checks_by_id.get(check_id) or {}
        message = str(row.get("message") or "A recoverable model-output issue was isolated.").strip()
        warnings.append(message)
    return list(dict.fromkeys(warnings))[:24]


def _frozen_finalization_contract(skill: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return only the finalizer authority already frozen in the source contract."""
    if not isinstance(skill, dict):
        return None
    source = skill.get("sourceContract")
    raw = source.get("frozenFinalization") if isinstance(source, dict) else None
    if raw is None:
        return None
    from app.execution.skill_finalization_runtime import validate_frozen_finalization_contract

    return validate_frozen_finalization_contract(raw)


def registered_durable_skill_provider_schema_executable(
    skill: Mapping[str, Any],
    *,
    adapter_id: str,
) -> bool:
    """Return whether a Skill may pass durable readiness for schema reasons.

    LLM structured-output Skills must prove their frozen Provider Schema can
    pass the exact production projection/lowering/transport path without I/O.

    Non-LLM adapters (``python_adapter:*``, deterministic/external runtimes)
    execute outside that transport.  Treating their adapter id as a Provider
    would fail-closed every honest Python Skill that publishes
    ``structured_output``, blocking confirmed research from starting.
    """
    normalized_adapter = str(adapter_id or "").strip().lower()
    if normalized_adapter == "huiyan_chat_completions":
        # Huiyan receives JSON object mode only. Formal Schema completeness is
        # not a Provider capability gate and must never block readiness.
        return True
    if normalized_adapter not in _STRUCTURED_OUTPUT_PROVIDER_ADAPTERS:
        return True
    try:
        normalized_skill = dict(skill)
        structured_contract = _structured_json_contract(normalized_skill)
        frozen_finalization = _frozen_finalization_contract(normalized_skill)
        json_contract = (
            _frozen_schema_contract(normalized_skill)
            if frozen_finalization is not None
            else structured_contract
        )
        if json_contract is None:
            return True
        provider_schema = (
            frozen_finalization["draftSchema"]["schema"]
            if frozen_finalization is not None
            else json_contract["output"]["schema"]
        )
        response_format = {"type": "json_object"}
        apply_provider_schema_transport(
            normalized_adapter,
            payload={
                "model": "openair-admission-check",
                "messages": [],
                "response_format": dict(response_format),
            },
            response_format=response_format,
            provider_schema=deepcopy(provider_schema),
        )
    except (OSError, yaml.YAMLError, ValueError, ProviderSchemaCapabilityError):
        return False
    return True


def _validate_structured_json(
    value: Any, schema: dict[str, Any], label: str, *, role: str = "output",
    input_warnings: list[str] | None = None,
) -> Any:
    from app.execution.scientific_input_fidelity import validate_runtime_json

    return validate_runtime_json(value, schema, label, role=role, input_warnings=input_warnings)


_JSON_FENCE_RE = re.compile(
    r"\A\s*```(?:json)?\s*(?P<body>[\s\S]*?)\s*```\s*\Z",
    re.IGNORECASE,
)
_STRUCTURED_CORRECTION_SOURCE_LIMIT = 32 * 1024
_STRUCTURED_WRAPPER_LIMIT = 2_000


def _safe_structured_wrapper(value: str) -> bool:
    """Accept only bounded non-JSON wrapper text around one complete object."""
    text = str(value or "").strip()
    return len(text) <= _STRUCTURED_WRAPPER_LIMIT and not any(char in text for char in "{}[]")


def _parse_structured_output(raw: Any) -> Any:
    """Parse one structured object, recovering only representation-level wrappers.

    The fallback is deliberately lossless: it accepts exactly one complete JSON
    object surrounded only by bounded non-JSON commentary. It never repairs JSON,
    chooses between multiple objects, or manufactures missing fields.
    """
    text = str(raw or "").strip()
    fenced = _JSON_FENCE_RE.fullmatch(text)
    if fenced is not None:
        text = fenced.group("body").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as original:
        decoder = json.JSONDecoder()
        candidates: list[Any] = []
        for match in re.finditer(r"{", text):
            try:
                candidate, end = decoder.raw_decode(text, match.start())
            except json.JSONDecodeError:
                continue
            if not isinstance(candidate, dict):
                continue
            if not _safe_structured_wrapper(text[: match.start()]):
                continue
            if not _safe_structured_wrapper(text[end:]):
                continue
            candidates.append(candidate)
            if len(candidates) > 1:
                break
        if len(candidates) == 1:
            return candidates[0]
        raise original





def _structured_correction_payload(
    base_payload: dict[str, Any],
    previous_output: str,
    *,
    correction_hint: str = "json_parse_failed",
) -> dict[str, Any]:
    """Build one bounded structured-contract correction request.

    The correction is still the same frozen scientific task.  Code supplies only
    a deterministic failure label; the model remains responsible for scientific
    meaning and may use only the original frozen inputs.
    """
    previous = str(previous_output or "")
    if not previous.strip() or len(previous.encode("utf-8")) > _STRUCTURED_CORRECTION_SOURCE_LIMIT:
        raise ValueError("structured correction source is unavailable")
    hint = str(correction_hint or "structured_contract_invalid").strip()
    if re.fullmatch(r"[A-Za-z0-9._:/-]{1,160}", hint) is None:
        hint = "structured_contract_invalid"
    messages = [dict(item) for item in base_payload.get("messages", []) if isinstance(item, dict)]
    if len(messages) != 2:
        raise ValueError("structured correction base messages are invalid")
    messages.extend([
        {"role": "assistant", "content": previous},
        {
            "role": "user",
            "content": (
                "The previous response did not satisfy the frozen structured output contract "
                f"(deterministic_failure={hint}). "
                "Return exactly one corrected raw JSON object following the original Skill's "
                "output semantics. "
                "This is one bounded correction of the same frozen research task, not a new "
                "research task. Use only the original frozen inputs and original instructions. "
                "Preserve only scientific meaning that is supported by those inputs; remove or "
                "correct unsupported structure, references, or choices rather than inventing facts. "
                "Do not add citations, DOI, URLs, measurements, observations, evidence, claims, "
                "conclusions, or other scientific facts that are not supported by the frozen inputs. "
                "Do not guess missing scientific content. Do not use markdown fences or prose outside "
                "the JSON object. The deterministic finalizer, runtime guard, and frozen Formal Schema "
                "will still reject unsupported or invalid content."
            ),
        },
    ])
    return {**base_payload, "messages": messages}


def _request_structured_correction(
    *,
    request: dict[str, Any],
    base_payload: dict[str, Any],
    previous_output: str,
    runtime_config: Any,
    endpoint: str,
    headers: dict[str, str],
    attempt_repository: ProviderHttpAttemptRepository,
    provider_id: str,
    resolved_execution_policy: dict[str, Any] | None,
    task_deadline: float | None = None,
    correction_hint: str = "json_parse_failed",
) -> tuple[Any, dict[str, Any] | None]:
    """Issue one side-effect-free correction sub-call after a known response."""
    payload = _structured_correction_payload(
        base_payload, previous_output, correction_hint=correction_hint
    )
    request_body = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    attempt = attempt_repository.begin_sub_attempt(
        capability_call_id=str((request.get("providerEvidence") or {}).get("capabilityCallId") or ""),
        evidence_kind=EVIDENCE_KIND_SUB_CALL,
        adapter_name="structured_output_correction",
        request_body=request_body,
        endpoint=endpoint,
        adapter=runtime_config.adapter,
        operation_identity=f"structured_output_correction:{str(request.get('skillId') or 'skill')}",
        provider=provider_id,
        model=runtime_config.model,
        resolved_execution_policy=resolved_execution_policy,
    )
    correction_headers = dict(headers)
    correction_headers.update(attempt.headers)
    try:
        mark_skill_provider_io_started(request, attempt_repository, attempt)
        with httpx.Client(timeout=_llm_http_timeout(
            runtime_config=runtime_config,
            execution_policy=resolved_execution_policy,
            deadline=task_deadline,
        )) as client:
            response = client.post(endpoint, headers=correction_headers, content=request_body)
    except (httpx.HTTPError, OSError) as exc:
        submission_known_not_sent = isinstance(
            exc, (HttpxPoolTimeout, HttpxConnectTimeout, HttpxConnectError)
        )
        attempt_repository.finish(
            attempt,
            request_body=request_body,
            outcome=(
                "transport_not_submitted"
                if submission_known_not_sent
                else "transport_uncertain"
            ),
            http_status=None,
            response_body=None,
            response_headers=None,
        )
        try:
            setattr(exc, "openair_provider_execution_id", attempt.attempt_id)
            setattr(exc, "openair_submission_known_not_sent", submission_known_not_sent)
        except Exception:
            pass
        raise
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
    try:
        response_text = response_body.decode("utf-8")
        body = json.loads(response_text)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        _record_durable_skill_usage(
            request=request,
            provider_id=provider_id,
            model=runtime_config.model,
            usage_wire=_billable_usage_wire_from_body(
                {},
                request_text=request_body.decode("utf-8", errors="replace"),
                response_text=response_body.decode("utf-8", errors="replace"),
                provider_id=provider_id,
                model=runtime_config.model,
            ),
            status="failed",
        )
        raise ValueError("structured correction response is not valid provider JSON") from exc
    usage_wire = _billable_usage_wire_from_body(
        body,
        request_text=request_body.decode("utf-8", errors="replace"),
        provider_id=provider_id,
        model=runtime_config.model,
    )
    return body, usage_wire


def _json_type(value: Any) -> str:
    """Return JSON's type name without ever serializing a provider value."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    return type(value).__name__


def _json_pointer(parts: Any) -> str:
    escaped = [str(part).replace("~", "~0").replace("/", "~1") for part in parts]
    return "/" + "/".join(escaped) if escaped else "/"


def _schema_failure_diagnostics(exc: BaseException, instance: Any) -> dict[str, Any]:
    """Project the first jsonschema failure into value-free durable diagnostics."""
    current: BaseException | None = exc
    validation_error: Any = None
    while current is not None:
        if hasattr(current, "absolute_path") and hasattr(current, "validator"):
            validation_error = current
            break
        current = current.__cause__
    if validation_error is None:
        return {"failed_stage": "json_schema_failed", "first_mismatch": "schema_validation_failed"}
    diagnostic: dict[str, Any] = {
        "failed_stage": "json_schema_failed",
        "first_mismatch": str(validation_error.validator or "schema_validation_failed"),
        "instancePath": _json_pointer(validation_error.absolute_path),
        "schemaPath": _json_pointer(validation_error.absolute_schema_path),
        "validator": str(validation_error.validator or ""),
        "actualJsonType": _json_type(validation_error.instance),
    }
    if validation_error.validator == "type":
        expected = validation_error.validator_value
        diagnostic["expectedType"] = expected if isinstance(expected, str) else list(expected)
    return diagnostic


def _structured_output_contract(skill: dict[str, Any], contract: dict[str, Any]) -> dict[str, str]:
    metadata, _instructions = _read_skill_contract(skill)
    output_file = str(metadata.get("output_file") or "").strip()
    path = PurePosixPath(output_file)
    if (
        not output_file
        or path.is_absolute()
        or ".." in path.parts
        or path.suffix.lower() != ".json"
    ):
        raise ValueError("structured Skill output_file must be a safe JSON path")
    return {
        "artifact": f"skills/{skill['skillId']}/{path.as_posix()}",
        "mimeType": "application/json",
        "template": "",
        "contractSource": "frozen_structured_json",
        "schemaSha256": contract["output"]["sha256"],
    }


class _UniqueKeySafeLoader(SAFE_LOADER):
    """Safe YAML loader that rejects ambiguous mappings."""


def _construct_unique_mapping(
    loader: _UniqueKeySafeLoader,
    node: yaml.MappingNode,
    deep: bool = False,
) -> dict[str, Any]:
    loader.flatten_mapping(node)
    mapping: dict[str, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str) or key in mapping:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "research-config keys must be unique strings",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeySafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def _registered_durable_skill_from_registry_entry(
    entry: Mapping[str, Any],
    *,
    handoff_schema_skill_ids: frozenset[str],
) -> dict[str, Any] | None:
    """Normalize one Registry row through the canonical freezing authority."""
    canonical = str(entry.get("skill_id") or "").strip()
    skill_path = str(entry.get("skill_path") or "").strip()
    if not canonical or not skill_path:
        return None
    source_contract = {
        key: value
        for key, value in entry.items()
        if key not in {"display_name", "owner", "fixture_status", "notes"}
    }
    try:
        source_contract = freeze_registered_source_contract(
            source_contract,
            skill_path=skill_path,
            require_frozen_schemas=canonical in handoff_schema_skill_ids,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError, yaml.YAMLError):
        return None
    skill = {
        "skillId": canonical,
        "definitionVersion": 1,
        "enabled": bool(entry.get("enabled", True)),
        "claudeName": str(entry.get("claude_name") or "").strip(),
        "displayName": str(entry.get("display_name") or canonical).strip(),
        "skillPath": skill_path,
        "runtime": str(entry.get("runtime") or "").strip(),
        "status": str(entry.get("status") or "").strip(),
        "skillType": str(entry.get("skill_type") or "").strip(),
        "packs": [str(value) for value in entry.get("packs") or []],
        "substages": [str(value) for value in entry.get("substages") or []],
        "sourceContract": source_contract,
    }
    skill["runtimeContract"] = _runtime_contract(skill)
    # Contract readiness for grants/planning. Live resolve still requires
    # status == ready so draft Skills can be designed without executing.
    skill["durableExecutable"] = _has_durable_runtime_contract(skill)
    return skill


def list_registered_durable_skills() -> list[dict[str, Any]]:
    """Return normalized production Skill records without executing them."""
    path = repo_root() / "skills" / "registry.yaml"
    try:
        payload = read_yaml_source(path) or {}
    except (OSError, yaml.YAMLError):
        return []
    try:
        from app.modules.skill_binding_source import (
            SkillBindingSourceError,
            skill_ids_requiring_frozen_handoff_schemas,
        )

        handoff_schema_skill_ids = skill_ids_requiring_frozen_handoff_schemas()
    except SkillBindingSourceError:
        return []
    skills: list[dict[str, Any]] = []
    for entry in payload.get("skills") or []:
        if not isinstance(entry, Mapping):
            continue
        skill = _registered_durable_skill_from_registry_entry(
            entry,
            handoff_schema_skill_ids=handoff_schema_skill_ids,
        )
        if skill is not None:
            skills.append(skill)
    return skills


def resolve_registered_durable_skill(skill_id: str) -> dict[str, Any] | None:
    """Resolve one Skill without freezing every unrelated Registry entry."""
    wanted = _normal(skill_id)
    path = repo_root() / "skills" / "registry.yaml"
    try:
        payload = read_yaml_source(path) or {}
    except (OSError, yaml.YAMLError):
        return None

    matches = [
        entry
        for entry in payload.get("skills") or []
        if isinstance(entry, Mapping)
        and wanted
        in {
            _normal(str(entry.get("skill_id") or "")),
            _normal(str(entry.get("claude_name") or "")),
        }
    ]
    if not matches:
        return None

    try:
        from app.modules.skill_binding_source import (
            SkillBindingSourceError,
            skill_ids_requiring_frozen_handoff_schemas,
        )

        handoff_schema_skill_ids = skill_ids_requiring_frozen_handoff_schemas()
    except SkillBindingSourceError:
        return None

    for entry in matches:
        skill = _registered_durable_skill_from_registry_entry(
            entry,
            handoff_schema_skill_ids=handoff_schema_skill_ids,
        )
        if skill is not None:
            return skill
    return None



def _schema_property_names(
    schema: Mapping[str, Any],
    *,
    root: Mapping[str, Any],
    seen_refs: frozenset[str] = frozenset(),
) -> set[str]:
    """Return the public top-level fields accepted by a composed object schema."""
    names: set[str] = set()
    properties = schema.get("properties")
    if isinstance(properties, Mapping):
        if any(not isinstance(key, str) or not key for key in properties):
            raise ValueError("capability_contract_input_property_invalid")
        names.update(properties)

    reference = schema.get("$ref")
    if isinstance(reference, str) and reference.startswith("#/") and reference not in seen_refs:
        target: Any = root
        for raw_part in reference[2:].split("/"):
            part = raw_part.replace("~1", "/").replace("~0", "~")
            if not isinstance(target, Mapping) or part not in target:
                raise ValueError("capability_contract_input_ref_invalid")
            target = target[part]
        if not isinstance(target, Mapping):
            raise ValueError("capability_contract_input_ref_invalid")
        names.update(
            _schema_property_names(
                target,
                root=root,
                seen_refs=seen_refs | {reference},
            )
        )

    for keyword in ("allOf", "anyOf", "oneOf"):
        branches = schema.get(keyword)
        if branches is None:
            continue
        if not isinstance(branches, list) or any(
            not isinstance(branch, Mapping) for branch in branches
        ):
            raise ValueError("capability_contract_input_composition_invalid")
        for branch in branches:
            names.update(_schema_property_names(branch, root=root, seen_refs=seen_refs))
    return names


def registered_skill_capability_contract(
    source_contract: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Project formal Skill capability facts without semantic inference."""
    unknown = (
        {"status": "unknown", "acceptedInputRefs": []},
        {"status": "unknown", "artifactTypes": []},
        {
            "deterministic": False,
            "source": "canonical_registry_unspecified",
        },
    )
    if not isinstance(source_contract, Mapping):
        return unknown

    declaration = source_contract.get("capability_contract")
    if declaration is None:
        return unknown
    if not isinstance(declaration, Mapping):
        raise ValueError("capability_contract_not_object")
    if set(declaration) != {"artifact_types", "judgment_support"}:
        raise ValueError("capability_contract_fields_invalid")

    frozen = source_contract.get("frozenSchemas")
    if not isinstance(frozen, Mapping):
        raise ValueError("capability_contract_requires_frozen_schemas")

    input_entry = frozen.get("input")
    output_entry = frozen.get("output")
    if not isinstance(input_entry, Mapping) or not isinstance(output_entry, Mapping):
        raise ValueError("capability_contract_requires_public_schemas")

    input_schema = input_entry.get("schema")
    output_schema = output_entry.get("schema")
    if not isinstance(input_schema, Mapping) or not isinstance(output_schema, Mapping):
        raise ValueError("capability_contract_schema_invalid")

    accepted_inputs = sorted(_schema_property_names(input_schema, root=input_schema))
    if not accepted_inputs:
        raise ValueError("capability_contract_input_properties_missing")

    artifact_types = declaration.get("artifact_types")
    if not isinstance(artifact_types, list) or not artifact_types:
        raise ValueError("capability_contract_artifact_types_invalid")
    if any(not isinstance(value, str) or not value.strip() for value in artifact_types):
        raise ValueError("capability_contract_artifact_type_invalid")
    artifact_types = [value.strip() for value in artifact_types]
    if len(set(artifact_types)) != len(artifact_types):
        raise ValueError("capability_contract_artifact_type_duplicate")

    support = declaration.get("judgment_support")
    if not isinstance(support, Mapping):
        raise ValueError("capability_contract_judgment_support_invalid")
    if set(support) != {"deterministic", "domain_professional"}:
        raise ValueError("capability_contract_judgment_support_fields_invalid")

    deterministic = support.get("deterministic")
    domain_professional = support.get("domain_professional")
    if not isinstance(deterministic, bool) or not isinstance(domain_professional, bool):
        raise ValueError("capability_contract_judgment_support_type_invalid")

    return (
        {
            "status": "known",
            "acceptedInputRefs": accepted_inputs,
        },
        {
            "status": "known",
            "artifactTypes": artifact_types,
        },
        {
            "deterministic": deterministic,
            "domainProfessional": domain_professional,
            "humanAuthority": False,
            "source": "skill_capability_contract",
        },
    )


def resolve_registered_durable_skill_runtime(
    skill_id: str,
    *,
    definition_version: int = 1,
    snapshot_definition: dict[str, Any] | None = None,
    frozen_execution_policy_present: bool = False,
) -> dict[str, Any] | None:
    """Resolve current runtime against only frozen execution-critical facts."""
    skill = resolve_registered_durable_skill(skill_id)
    if (
        skill is None
        or definition_version != skill.get("definitionVersion")
        or skill.get("status") != "ready"
        or not skill.get("enabled")
        or not skill.get("durableExecutable")
    ):
        return None
    if snapshot_definition is not None and not _frozen_execution_contract_matches(
        skill, snapshot_definition
    ):
        return None
    runtime_contract = _runtime_contract(skill)
    if runtime_contract is None:
        return None
    kind = runtime_contract["kind"]
    try:
        metadata, _instructions = _read_skill_contract(skill)
        if kind in {
            _RUNTIME_KIND_REASONING_ADAPTER,
            _RUNTIME_KIND_DETERMINISTIC,
            _RUNTIME_KIND_EXTERNAL_ADAPTER,
        }:
            if not _is_python_adapter_skill(skill["skillId"]):
                return None
            expected_adapter = _python_adapter_id(skill["skillId"])
            if runtime_contract["adapter_id"] != expected_adapter:
                return None
            if (
                kind == _RUNTIME_KIND_EXTERNAL_ADAPTER
                and runtime_contract["adapter_id"] not in _EXTERNAL_ADAPTER_EVIDENCE_VALIDATORS
            ):
                return None
            output_contract = {
                "artifact": f"skills/{skill['skillId']}/result.json",
                "mimeType": "application/json",
                "template": "{}",
                "contractSource": "python_adapter",
            }
        elif kind == _RUNTIME_KIND_SKILL_SCRIPT:
            output_contract = {
                "artifact": f"skills/{skill['skillId']}/result.json",
                "mimeType": "application/json",
                "template": "{}",
                "contractSource": "skill_script",
            }
        elif kind == _RUNTIME_KIND_LLM_REASONING:
            structured_contract = _structured_json_contract(skill)
            frozen_finalization = _frozen_finalization_contract(skill)
            json_contract = (
                _frozen_schema_contract(skill)
                if frozen_finalization is not None
                else structured_contract
            )
            if frozen_finalization is not None and json_contract is None:
                return None
            output_contract = (
                _structured_output_contract(skill, json_contract)
                if json_contract is not None
                else _durable_output_contract(metadata, skill=skill)
            )
        else:
            return None
    except (OSError, yaml.YAMLError, ValueError):
        return None
    runtime = dict(skill)
    runtime.update({
        "runtimeKind": kind,
        "requiresExternalAction": runtime_contract["requires_external_action"],
        "adapterId": runtime_contract["adapter_id"],
        "evidenceContractVersion": runtime_contract["evidence_contract_version"],
        "logicalProviderId": None,
        "modelProfile": None,
        "artifactContract": f"direct_skill:{skill['skillId']}:v1",
        "artifactSchemaDigest": "sha256:" + hashlib.sha256(
            json.dumps(output_contract, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("utf-8")
        ).hexdigest(),
        "artifactMimeType": output_contract["mimeType"],
    })
    if kind == _RUNTIME_KIND_LLM_REASONING:
        snapshot_source = (
            snapshot_definition.get("sourceContract")
            if isinstance(snapshot_definition, dict)
            else None
        )
        has_frozen_execution_policy = bool(frozen_execution_policy_present) or (
            isinstance(snapshot_source, dict)
            and snapshot_source.get(DURABLE_LLM_EXECUTION_POLICY_KEY) is not None
        ) or (
            isinstance(skill.get("sourceContract"), dict)
            and skill["sourceContract"].get(DURABLE_LLM_EXECUTION_POLICY_KEY) is not None
        )
        if has_frozen_execution_policy:
            if runtime_contract["adapter_id"] != _HUIYAN_DURABLE_ADAPTER:
                return None
        else:
            model_profile = _registered_durable_model_profile(skill)
            if model_profile is None:
                return None
            try:
                registry = load_provider_registry()
                provider_id = registry.defaultProviderId
                provider = registry.require(provider_id)
                config = resolve_provider_runtime_config(
                    provider_id,
                    model_profile=model_profile,
                    registry=registry,
                )
            except (ProviderRegistryError, OSError, ValueError):
                return None
            if (
                runtime_contract["adapter_id"] != _HUIYAN_DURABLE_ADAPTER
                or provider.adapter != _HUIYAN_DURABLE_ADAPTER
                or config.adapter != _HUIYAN_DURABLE_ADAPTER
            ):
                return None
            runtime["logicalProviderId"] = provider_id
            runtime["modelProfile"] = model_profile
    return runtime


def _frozen_execution_contract_matches(
    live_skill: Mapping[str, Any],
    snapshot_definition: Mapping[str, Any],
) -> bool:
    """Compare identity, adapter/runtime kind and frozen schema identities.

    Capability semantics were accepted before routing.  Display metadata,
    source list ordering and other non-execution fields must not become a
    second qualification gate at dispatch.
    """
    if (
        snapshot_definition.get("skillId") != live_skill.get("skillId")
        or snapshot_definition.get("definitionVersion")
        != live_skill.get("definitionVersion")
        or snapshot_definition.get("status") != "ready"
        or snapshot_definition.get("enabled") is not True
    ):
        return False
    snapshot_source = snapshot_definition.get("sourceContract")
    live_source = live_skill.get("sourceContract")
    if not isinstance(snapshot_source, Mapping) or not isinstance(live_source, Mapping):
        return False
    snapshot_runtime = _runtime_contract({"sourceContract": dict(snapshot_source)})
    live_runtime = _runtime_contract(dict(live_skill))
    if snapshot_runtime is None or snapshot_runtime != live_runtime:
        return False
    snapshot_roles = snapshot_definition.get("executionMethodRoles")
    if snapshot_roles is None:
        snapshot_roles = snapshot_source.get("execution_method_roles") or []
    live_roles = live_source.get("execution_method_roles") or []
    if not _same_string_set(snapshot_roles, live_roles):
        return False
    snapshot_schemas = _frozen_schema_identities(snapshot_source)
    live_schemas = _frozen_schema_identities(live_source)
    if snapshot_schemas is False or live_schemas is False or snapshot_schemas != live_schemas:
        return False
    if _frozen_finalization_identity(snapshot_source) != _frozen_finalization_identity(live_source):
        return False
    return True


def _same_string_set(first: Any, second: Any) -> bool:
    if not isinstance(first, (list, tuple)) or not isinstance(second, (list, tuple)):
        return False
    if any(not isinstance(value, str) or not value.strip() for value in [*first, *second]):
        return False
    return {value.strip() for value in first} == {value.strip() for value in second}


def _frozen_schema_identities(
    source: Mapping[str, Any],
) -> tuple[str, str] | None | bool:
    raw = source.get("frozenSchemas")
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        return False
    identities: list[str] = []
    for name in ("input", "output"):
        entry = raw.get(name)
        digest = entry.get("sha256") if isinstance(entry, Mapping) else None
        normalized = str(digest or "").strip().lower()
        if re.fullmatch(r"[0-9a-f]{64}", normalized) is None:
            return False
        identities.append(normalized)
    return identities[0], identities[1]


def _frozen_finalization_identity(source: Mapping[str, Any]) -> str | None:
    raw = source.get("frozenFinalization")
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        return "invalid"
    try:
        normalized = json.dumps(raw, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    except (TypeError, ValueError):
        return "invalid"
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def execute_production_durable_skill(
    request: dict[str, Any], *, prepared_runtime: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Production entrypoint enforcing concrete Binding-v2 input authority."""
    skill_id = str(request.get("skillId") or "")
    if isinstance(prepared_runtime, dict):
        skill = dict(prepared_runtime)
        if _normal(str(skill.get("skillId") or "")) != _normal(skill_id):
            return _failed(
                "The prepared capability runtime does not match the requested Skill.",
                "skill_runtime_unavailable",
            )
    else:
        skill = resolve_registered_durable_skill(skill_id)
        if skill is None:
            return _failed("The requested research capability is not registered.", "skill_definition_unknown")

    from app.modules.skill_binding_source import (
        SkillBindingSourceError,
        binding_handoff_evidence_matches,
        skill_ids_with_binding_inputs,
    )

    try:
        requires_binding_evidence = skill_id in skill_ids_with_binding_inputs()
    except SkillBindingSourceError:
        return _failed(
            "The canonical Skill Binding source is invalid.",
            "skill_binding_contract_invalid",
            failed_stage="binding_v2_precondition",
        )
    if requires_binding_evidence and not binding_handoff_evidence_matches(
        skill_id, request.get("bindingInputEvidence")
    ):
        return _failed(
            "The requested capability input was not materialized through its frozen Binding v2 contract.",
            "skill_binding_precondition_missing",
            failed_stage="binding_v2_precondition",
        )
    if isinstance(prepared_runtime, dict):
        return execute_durable_skill(
            request,
            prepared_runtime=skill,
        )
    return execute_durable_skill(request)


_SOURCE_PROGRESS_LABELS = {
    "openalex": "OpenAlex",
    "semantic_scholar": "Semantic Scholar",
    "arxiv": "arXiv",
    "deepxiv": "DeepXiv",
}


def _source_acquisition_progress_callback(request: dict[str, Any]):
    task_id = str(request.get("taskId") or "").strip()
    owner_id = str(request.get("ownerId") or "").strip()
    workspace_id = str(request.get("workspaceId") or "").strip()
    decision_id = str(request.get("executionDecisionId") or "").strip()
    work_unit_id = str(request.get("workUnitId") or "").strip()
    capability_call_id = str(request.get("capabilityCallId") or "").strip()
    if not all((task_id, owner_id, workspace_id, decision_id, work_unit_id, capability_call_id)):
        return None

    def emit(raw: dict[str, Any]) -> None:
        if str(raw.get("phase") or "") != "source_batch_completed":
            return
        source = str(raw.get("source_name") or "").strip()
        label = _SOURCE_PROGRESS_LABELS.get(source, "文献来源")
        count = raw.get("source_candidate_count")
        if not isinstance(count, int) or count < 0:
            count = 0
        query_index = raw.get("query_index")
        if not isinstance(query_index, int) or query_index < 0:
            query_index = 0
        failed = bool(raw.get("failed"))
        samples = raw.get("sample_candidates")
        titles = []
        if isinstance(samples, list):
            for item in samples[:3]:
                if not isinstance(item, dict):
                    continue
                title = " ".join(str(item.get("title") or "").split()).strip()
                if title and title not in titles:
                    titles.append(title[:120])

        if failed and count == 0:
            headline = f"{label} 本轮没有返回候选文献"
            summary = "其他文献来源继续检索，已找到的结果会继续保留。"
        else:
            headline = f"{label} 已找到 {count} 篇候选文献"
            summary = ("；".join(titles) if titles else "候选文献已保留，其他来源继续检索。")

        event = {
            "sourceEventId": f"{capability_call_id}:source:{source or 'unknown'}:q{query_index}",
            "eventType": "meaningful_progress",
            "presentationLevel": "primary",
            "workUnitId": work_unit_id,
            "headline": headline,
            "summary": summary,
            "invalidateScopes": ["collaboration"],
            "payload": {
                "aggregateCount": count,
                "activityKey": f"source-acquisition:{work_unit_id}:{source or 'unknown'}",
                "phase": "running",
            },
            "occurredAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        try:
            from app.modules.task_events import TaskResearchEventRepository

            TaskResearchEventRepository().append_many(
                owner_id=owner_id,
                workspace_id=workspace_id,
                task_id=task_id,
                source_identity=decision_id,
                events=[event],
            )
        except Exception:
            logger.warning(
                "source acquisition progress event could not be persisted task=%s work_unit=%s",
                task_id,
                work_unit_id,
                exc_info=True,
            )

    return emit


def execute_new_durable_llm_child(
    *,
    skill_id: str,
    parameters: dict[str, Any],
    parent_capability_call_id: str,
    adapter_name: str,
) -> dict[str, Any]:
    """Prepare and execute one NEW reasoning sub-call under its parent's authority.

    Reuse the WorkUnit policy resolver against the child's own registered
    source contract.  Freeze the request before execution; the executor never
    resolves a missing policy.  Persisted WorkUnit replay/recovery continues to
    use execute_durable_skill with its original frozen request, not this
    new-call entrypoint.  This function adds no retry or recovery authority.
    """
    if (
        not isinstance(parent_capability_call_id, str)
        or not parent_capability_call_id.strip()
        or not isinstance(adapter_name, str)
        or not adapter_name.strip()
    ):
        return _failed(
            "The child capability call cannot be attributed to its parent.",
            "skill_input_invalid",
            failed_stage="child_call_preparation",
            first_mismatch="child_call_evidence_identity_missing",
        )
    if not isinstance(parameters, dict):
        return _failed(
            "The child capability input is invalid.",
            "skill_input_invalid",
            failed_stage="child_call_preparation",
            first_mismatch="child_parameters_not_object",
        )
    try:
        runtime = resolve_registered_durable_skill_runtime(skill_id)
        if runtime is None or runtime.get("runtimeKind") != _RUNTIME_KIND_LLM_REASONING:
            return _failed(
                "The child capability has no registered reasoning runtime.",
                "skill_runtime_unavailable",
                failed_stage="child_call_preparation",
                first_mismatch="child_reasoning_runtime_unavailable",
            )
        execution_policy = resolve_durable_llm_execution_policy(runtime)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        code = str(exc).strip()
        # Only machine codes may cross this boundary; no environment values.
        if re.fullmatch(r"[A-Za-z0-9._:/-]{1,160}", code) is None:
            code = "child_execution_configuration_unavailable"
        return _failed(
            "The child's registered execution configuration cannot be prepared.",
            "provider_configuration",
            failed_stage="child_call_preparation",
            first_mismatch=code,
            preparation={"exceptionType": type(exc).__name__},
        )
    request: dict[str, Any] = {
        "skillId": runtime["skillId"],
        "parameters": deepcopy(parameters),
        "logicalProviderId": (
            execution_policy["providerId"]
            if execution_policy is not None
            else runtime.get("logicalProviderId")
        ),
        "providerEvidence": {
            "capabilityCallId": parent_capability_call_id.strip(),
            "evidenceKind": EVIDENCE_KIND_SUB_CALL,
            "adapterName": adapter_name.strip(),
        },
    }
    # Metering identity lives in the environment inside the adapter process
    # (see _child_metering_env); adopt it so sub-call usage is attributed to
    # the owning task/user instead of being silently unmetered.
    for request_key, env_key in (
        ("taskId", "OPENAIR_TASK_ID"),
        ("ownerId", "OPENAIR_OWNER_ID"),
        ("workspaceId", "OPENAIR_WORKSPACE_ID"),
    ):
        forwarded = str(os.environ.get(env_key) or "").strip()
        if forwarded:
            request[request_key] = forwarded
    if execution_policy is not None:
        request["resolvedExecutionPolicy"] = deepcopy(execution_policy)
    # Undeclared/legacy contracts deliberately retain their existing path,
    # including the specialized Evidence batch executor.
    return execute_durable_skill(request, prepared_runtime=deepcopy(runtime))


def _frozen_request_authority(
    request: Mapping[str, Any],
) -> tuple[int, dict[str, Any]] | None:
    """Validate one persisted Definition/Binding route against live authority."""
    fields = (
        "definitionVersion", "snapshotDefinition", "sourceBindingRefs",
        "selectedSkillBindings",
    )
    present = [request.get(field) is not None for field in fields]
    if not any(present):
        return None
    if not all(present):
        raise ValueError("frozen_skill_authority_incomplete")

    skill_id = str(request.get("skillId") or "").strip()
    definition_version = request.get("definitionVersion")
    snapshot = request.get("snapshotDefinition")
    refs = request.get("sourceBindingRefs")
    bindings = request.get("selectedSkillBindings")
    if (
        not skill_id
        or not isinstance(definition_version, int)
        or isinstance(definition_version, bool)
        or definition_version < 1
        or not isinstance(snapshot, Mapping)
        or snapshot.get("skillId") != skill_id
        or snapshot.get("definitionVersion") != definition_version
        or not isinstance(refs, list)
        or len(refs) != 1
        or not isinstance(refs[0], Mapping)
        or not isinstance(bindings, list)
        or len(bindings) != 1
        or not isinstance(bindings[0], Mapping)
    ):
        raise ValueError("frozen_skill_authority_invalid")
    binding = bindings[0]
    definition_id = str(binding.get("definitionId") or "").strip()
    binding_version = binding.get("bindingVersion")
    if (
        not definition_id
        or binding.get("bindingId") != f"bind:{definition_id}:skill:{skill_id}"
        or not isinstance(binding_version, int)
        or isinstance(binding_version, bool)
        or binding_version < 1
        or binding.get("skillId") != skill_id
        or binding.get("skillDefinitionVersion") != definition_version
        or not isinstance(binding.get("definitionVersion"), int)
        or isinstance(binding.get("definitionVersion"), bool)
        or binding.get("definitionVersion") < 1
        or binding.get("enabled") is not True
        or binding.get("sourceAuthority") != "skill_binding_source"
        or dict(refs[0]) != {
            "bindingId": binding["bindingId"],
            "bindingVersion": binding_version,
        }
    ):
        raise ValueError("frozen_skill_binding_identity_invalid")

    from app.modules.skill_binding_source import load_skill_binding_source_rows

    live_matches = [
        row for row in load_skill_binding_source_rows()
        if row.get("bindingId") == binding["bindingId"]
        and row.get("bindingVersion") == binding_version
    ]
    if len(live_matches) != 1:
        raise ValueError("frozen_skill_binding_unavailable")
    live = live_matches[0]
    for field in (
        "bindingId", "bindingVersion", "definitionId", "definitionVersion",
        "skillId", "skillDefinitionVersion", "inputSources", "runtimeInputs",
        "scientificStateProjection",
    ):
        if binding.get(field) != live.get(field):
            raise ValueError("frozen_skill_binding_drift")
    return definition_version, deepcopy(dict(snapshot))


def execute_durable_skill(
    request: dict[str, Any], *, prepared_runtime: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Execute exactly one explicitly classified durable Skill runtime."""
    if isinstance(prepared_runtime, dict):
        runtime = dict(prepared_runtime)
        skill = runtime
        if _normal(str(skill.get("skillId") or "")) != _normal(str(request.get("skillId") or "")):
            return _failed(
                "The prepared capability runtime does not match the request.",
                "skill_runtime_unavailable",
            )
    else:
        skill_id = str(request.get("skillId") or "")
        try:
            frozen_authority = _frozen_request_authority(request)
        except ValueError:
            return _failed(
                "The frozen capability authority is incomplete or no longer matches its Binding.",
                "skill_runtime_unavailable",
            )
        if frozen_authority is not None:
            definition_version, snapshot_definition = frozen_authority
            runtime = resolve_registered_durable_skill_runtime(
                skill_id,
                definition_version=definition_version,
                snapshot_definition=snapshot_definition,
                frozen_execution_policy_present=request.get("resolvedExecutionPolicy") is not None,
            )
            skill = runtime
        else:
            skill = resolve_registered_durable_skill(skill_id)
            if skill is None:
                return _failed("The requested research capability is not registered.", "skill_definition_unknown")
            runtime = resolve_registered_durable_skill_runtime(
                skill["skillId"],
                frozen_execution_policy_present=request.get("resolvedExecutionPolicy") is not None,
            )
        if runtime is None:
            return _failed("This capability has no truthful durable runtime.", "skill_runtime_unavailable")
    canonical = skill["skillId"]
    kind = runtime["runtimeKind"]
    if kind in {
        _RUNTIME_KIND_REASONING_ADAPTER,
        _RUNTIME_KIND_DETERMINISTIC,
        _RUNTIME_KIND_EXTERNAL_ADAPTER,
    }:
        parameters = request.get("parameters")
        if not isinstance(parameters, dict):
            return _failed("The requested capability input is invalid.", "skill_input_invalid")
        try:
            progress_callback = (
                _source_acquisition_progress_callback(request)
                if runtime["adapterId"] in {
                    "python_adapter:opentrajectory_source_acquisition",
                    "python_adapter:pvd_literature_evidence_acquisition_closure",
                }
                else None
            )
            execution_cwd = None
            environment_overrides = None
            if kind == _RUNTIME_KIND_REASONING_ADAPTER:
                parent_call_id = str(request.get("capabilityCallId") or "").strip()
                if not parent_call_id:
                    return _failed(
                        "The reasoning adapter parent capability call is unavailable.",
                        "skill_input_invalid",
                    )
                environment_overrides = {"OPENAIR_PARENT_CAPABILITY_CALL_ID": parent_call_id}
            if runtime["adapterId"] == "python_adapter:pvd_literature_evidence_acquisition_closure":
                parent_call_id = str(request.get("capabilityCallId") or "").strip()
                if not parent_call_id:
                    return _failed(
                        "The PVD literature closure parent capability call is unavailable.",
                        "skill_input_invalid",
                    )
                environment_overrides = {
                    "OPENAIR_PARENT_CAPABILITY_CALL_ID": parent_call_id,
                }
            if runtime["adapterId"] in {
                "python_adapter:environment_setup",
                "python_adapter:exploratory_run",
                "python_adapter:repository_revision_acquisition",
                "python_adapter:official_reference_source_acquisition",
                "python_adapter:resource_identity_acquisition",
                "python_adapter:actual_metric_extraction",
                "python_adapter:reproduction_record_assembly",
            }:
                task_id = str(request.get("taskId") or "").strip()
                if not task_id:
                    return _failed(
                        "The environment setup task workspace is unavailable.",
                        "skill_input_invalid",
                    )
                execution_cwd = prepare_task_workspace(task_id).resolve()
                environment_overrides = {"OPENAIR_ADAPTER_EXECUTION_CWD": str(execution_cwd)}
            private_owner = request.get("_internalSkillExecutionOwner")
            if kind == _RUNTIME_KIND_REASONING_ADAPTER and isinstance(private_owner, Mapping):
                environment_overrides = {
                    **(environment_overrides or {}),
                    "OPENAIR_INTERNAL_SKILL_EXECUTION_OWNER": json.dumps(dict(private_owner)),
                }
            # Every skill-owned adapter subprocess may run its own LLM calls
            # (e.g. formula-crossover RunPlan/crossover); metering identity is
            # therefore available to all of them, not just child-call adapters.
            environment_overrides = {
                **(environment_overrides or {}),
                **_child_metering_env(request),
            }
            output = run_python_skill_adapter(
                repo_root=repo_root(),
                skill_id=canonical,
                skill_path=skill["skillPath"],
                adapter_id=runtime["adapterId"],
                input_payload=parameters,
                progress_callback=progress_callback,
                execution_cwd=execution_cwd,
                environment_overrides=environment_overrides,
                execution_checkpoint_id=(
                    str(request.get("executionCheckpointId") or "").strip() or None
                    if kind == _RUNTIME_KIND_EXTERNAL_ADAPTER
                    else None
                ),
                **(
                    {"authorize_submission": request["_internalAdapterIoAuthorization"]}
                    if request.get("_internalAdapterIoAuthorization") is not None
                    else {}
                ),
            )
            from app.execution.research_internal_loop_pending import pending_runtime_output

            pending_output = pending_runtime_output(request, runtime, output)
            if pending_output is not None:
                return pending_output
            if (
                runtime["adapterId"] == "python_adapter:repository_revision_acquisition"
                and isinstance(output, dict)
                and output.get("state") == "failed"
            ):
                failure = _repository_revision_failure_details(output)
                if failure is not None:
                    return _failed(
                        "Repository revision acquisition failed.",
                        "repository_revision_acquisition_failed",
                        failed_stage=failure["failed_stage"],
                        adapter_failure_code=failure["failure_code"],
                        **({"git_exit_code": failure["git_exit_code"]} if "git_exit_code" in failure else {}),
                    )
            if (
                kind == _RUNTIME_KIND_EXTERNAL_ADAPTER
                and runtime["adapterId"] in _FORMAL_SCHEMA_GATED_EXTERNAL_ADAPTERS
                and not _matches_external_adapter_formal_schema(runtime["adapterId"], output)
            ):
                return _failed(
                    "The Skill-owned execution returned an invalid result.",
                    "skill_result_contract_invalid",
                )
            if (
                runtime["adapterId"] == "python_adapter:cs_paper_source_resolution"
                and isinstance(output, dict)
                and output.get("state") == "failed"
            ):
                failure = _cs_paper_source_resolution_failure_details(output)
                if failure is not None:
                    return _failed(
                        "CS paper source resolution failed.",
                        "cs_paper_source_resolution_failed",
                        failed_stage=failure["failed_stage"],
                        adapter_failure_code=failure["failure_code"],
                    )
            if (
                runtime["adapterId"] == "python_adapter:resource_identity_acquisition"
                and isinstance(output, dict)
                and output.get("state") == "failed"
            ):
                failure = _resource_identity_failure_details(output)
                if failure is not None:
                    return _failed(
                        "Resource identity acquisition failed.",
                        "resource_identity_acquisition_failed",
                        failed_stage=failure["failed_stage"],
                        adapter_failure_code=failure["failure_code"],
                    )
        except Exception:
            if kind == _RUNTIME_KIND_EXTERNAL_ADAPTER:
                return _unknown("The capability result cannot be established after one execution attempt.")
            if kind == _RUNTIME_KIND_REASONING_ADAPTER:
                return _failed("The reasoning capability did not complete.", "reasoning_adapter_failed")
            return _failed("The deterministic capability did not complete.", "deterministic_adapter_failed")
        if kind == _RUNTIME_KIND_EXTERNAL_ADAPTER and not _has_external_adapter_output_evidence(
            runtime["adapterId"], output
        ):
            failure_kwargs: dict[str, str] = {}
            if runtime["adapterId"] in {
                "python_adapter:academic_source_retrieval_skill",
                "python_adapter:opentrajectory_source_acquisition",
            }:
                failure_kwargs["failed_stage"] = "retrieval_report_evidence_gate"
                failure_kwargs["first_mismatch"] = _retrieval_failure_first_mismatch(output)
            return _failed(
                "The external capability returned no verifiable action evidence.",
                "external_action_evidence_missing",
                **failure_kwargs,
            )
        content = json.dumps(output, ensure_ascii=False, indent=2).encode("utf-8")
        digest = hashlib.sha256(content).hexdigest()
        evidence: list[dict[str, str]] = []
        evidence_outcome = "completed"
        summary = f"{canonical} produced material for the requesting Agent to review."
        if kind == _RUNTIME_KIND_EXTERNAL_ADAPTER:
            evidence_outcome = _external_adapter_evidence_outcome(runtime["adapterId"], output)
            if (
                runtime["adapterId"] == "python_adapter:exploratory_run"
                and output.get("state") == "failed"
            ):
                summary = (
                    f"{canonical} completed a real process attempt with "
                    f"execution_status={output.get('execution_status')}; review the execution record."
                )
            elif evidence_outcome == "partial":
                summary = (
                    f"{canonical} produced material with partial external-action failures; "
                    "review the artifact before treating every claim as complete."
                )
            evidence = [{
                "adapter": runtime["adapterId"],
                "outcome": evidence_outcome,
                "evidenceRef": f"skill-result:{canonical}:sha256:{digest}",
                "contentSha256": digest,
            }]
        return {
            "state": "succeeded",
            "summary": summary,
            "relatedSkillId": canonical,
            "externalActionEvidence": evidence,
            "artifactPayload": {
                "contentBase64": base64.b64encode(content).decode("ascii"),
                "contentType": "application/json",
                "presentation": {
                    "title": f"{canonical} result",
                    "mimeType": "application/json",
                    "metadata": {"runtime": kind, "skillId": canonical},
                    "contentSha256": digest,
                    "contentSize": len(content),
                },
            },
        }
    if kind == _RUNTIME_KIND_SKILL_SCRIPT:
        parameters = request.get("parameters")
        if not isinstance(parameters, dict):
            return _failed("The requested capability input is invalid.", "skill_input_invalid")
        try:
            from app.execution.materials_skill_script_runtime import (
                execute_checkpointed_materials_skill_script,
            )

            output = execute_checkpointed_materials_skill_script(
                skill_id=canonical,
                task_id=str(request.get("taskId") or ""),
                work_unit_id=str(request.get("workUnitId") or ""),
                parameters=parameters,
                checkpoint_id=(
                    str(request.get("executionCheckpointId") or "").strip() or None
                ),
                owner_id=str(request.get("ownerId") or "").strip(),
                workspace_id=str(request.get("workspaceId") or "").strip(),
            )
        except Exception:
            # A locally-raised exception is a known local failure, never an
            # external unknown (a permanent "result pending" with no writer to
            # reconcile it).  Log the traceback so the root cause stays
            # diagnosable — task_feb85da died silently here for a 1 ms raise.
            logger.exception(
                "skill_script execution raised locally (skill_id=%s taskId=%s workUnitId=%s)",
                canonical,
                request.get("taskId"),
                request.get("workUnitId"),
            )
            return _failed(
                "The Skill-owned execution raised locally before returning a result.",
                "skill_script_local_execution_failed",
                failed_stage="skill_script_execution",
            )
        if not isinstance(output, dict) or str(output.get("state") or "") not in {"succeeded", "partial_failure"}:
            return _failed("The Skill-owned execution returned an invalid result.", "skill_result_contract_invalid")
        content = json.dumps(output, ensure_ascii=False, indent=2).encode("utf-8")
        digest = hashlib.sha256(content).hexdigest()
        evidence_outcome = "partial" if output.get("state") == "partial_failure" else "completed"
        return {
            "state": "succeeded",
            "summary": f"{canonical} completed through its registered Skill scripts.",
            "relatedSkillId": canonical,
            "externalActionEvidence": [{
                "adapter": runtime["adapterId"],
                "outcome": evidence_outcome,
                "evidenceRef": f"skill-result:{canonical}:sha256:{digest}",
                "contentSha256": digest,
            }],
            "artifactPayload": {
                "contentBase64": base64.b64encode(content).decode("ascii"),
                "contentType": "application/json",
                "presentation": {
                    "title": f"{canonical} result",
                    "mimeType": "application/json",
                    "metadata": {"runtime": kind, "skillId": canonical},
                    "contentSha256": digest,
                    "contentSize": len(content),
                },
            },
        }
    if kind == _RUNTIME_KIND_LLM_REASONING:
        return _execute_durable_huiyan_skill(request, skill, runtime=runtime)
    return _failed("This capability runtime is unsupported.", "skill_runtime_unavailable")



def _combine_usage_wires(values: list[dict[str, Any] | None]) -> dict[str, Any] | None:
    """Sum real provider usage from bounded sub-calls without hiding retries."""
    from llm_core.token_usage import from_wire_usage, to_wire_usage

    parts = [from_wire_usage(value) for value in values if value]
    parts = [part for part in parts if part]
    if not parts:
        return None
    input_tokens = sum(int(part.get("input_tokens") or 0) for part in parts)
    output_tokens = sum(int(part.get("output_tokens") or 0) for part in parts)
    return to_wire_usage({
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "estimated": any(part.get("estimated") for part in parts),
        "raw": None,
    })


def _evidence_batch_field(skill_id: str, parameters: Any, frozen_finalization: Any) -> str | None:
    """Return the model-draft array field for the two shared literature Evidence Skills."""
    field = _EVIDENCE_BATCH_DRAFT_FIELDS.get(str(skill_id or "").strip())
    if not field or not isinstance(parameters, dict) or not isinstance(frozen_finalization, dict):
        return None
    packets = parameters.get("paper_packets")
    if not isinstance(packets, list) or not packets:
        return None
    draft = frozen_finalization.get("draftSchema")
    schema = draft.get("schema") if isinstance(draft, dict) else None
    properties = schema.get("properties") if isinstance(schema, dict) else None
    array_schema = properties.get(field) if isinstance(properties, dict) else None
    if not isinstance(array_schema, dict) or array_schema.get("type") != "array":
        return None
    return field


def _narrow_evidence_batch_schema(
    schema: dict[str, Any], *, field: str, paper_count: int,
) -> dict[str, Any]:
    """Give each paper a deterministic share of the existing global draft budget."""
    narrowed = deepcopy(schema)
    target = narrowed.get("properties", {}).get(field)
    if not isinstance(target, dict):
        raise ValueError("evidence batch draft field is unavailable")
    global_max = target.get("maxItems")
    if isinstance(global_max, int) and not isinstance(global_max, bool) and global_max > 0:
        target["maxItems"] = max(1, global_max // max(1, paper_count))
    return narrowed


def _payload_for_evidence_paper(
    base_payload: dict[str, Any],
    *,
    parameters: dict[str, Any],
    packet: dict[str, Any],
    draft_schema: dict[str, Any],
    retry: bool,
) -> dict[str, Any]:
    payload = deepcopy(base_payload)
    messages = payload.get("messages")
    if not isinstance(messages, list) or len(messages) != 2:
        raise ValueError("evidence batch base payload is invalid")
    user_content = json.loads(str(messages[1].get("content") or "{}"))
    scientific_input = user_content.get("scientificInput")
    if isinstance(scientific_input, dict):
        per_paper_input = dict(scientific_input)
        per_paper_input["paper_packets"] = [deepcopy(packet)]
        user_content["scientificInput"] = per_paper_input
    else:
        per_paper_parameters = dict(parameters)
        per_paper_parameters["paper_packets"] = [deepcopy(packet)]
        user_content["selectedParameters"] = per_paper_parameters
    if retry:
        messages[0]["content"] = str(messages[0].get("content") or "") + (
            " This is the one allowed recovery attempt for the same paper after a transient "
            "transport or structured-output failure. Return only the same scientific judgment "
            "in the required JSON schema. Do not add facts, citations, measurements, or causal "
            "claims merely to satisfy the format."
        )
    messages[1]["content"] = json.dumps(
        user_content, ensure_ascii=False, separators=(",", ":")
    )
    return payload


def _admit_batch_provider_payload(
    payload: dict[str, Any],
    *,
    adapter_id: str,
    provider_schema: dict[str, Any],
) -> dict[str, Any]:
    """Admit the batch-specific Schema into the exact payload sent to transport."""
    response_format = structured_response_format(
        adapter_id,
        schema_name="openair_structured_output",
    )
    return apply_provider_schema_transport(
        adapter_id,
        payload=payload,
        response_format=response_format,
        provider_schema=provider_schema,
    )


def _merge_structured_batch_draft(
    merged: dict[str, Any], draft: dict[str, Any], field: str,
) -> None:
    """Merge returned arrays and retain every other returned field as raw material."""
    values = draft.get(field)
    if isinstance(values, list):
        merged[field].extend(values)
    if not isinstance(values, list) or any(key != field for key in draft):
        merged.setdefault("unmerged_batch_outputs", []).append(deepcopy(draft))


def _execute_evidence_batch_requests(
    *,
    request: dict[str, Any],
    skill_id: str,
    parameters: dict[str, Any],
    provider_parameters: dict[str, Any],
    field: str,
    base_payload: dict[str, Any],
    draft_schema: dict[str, Any],
    runtime_config: Any,
    endpoint: str,
    base_headers: dict[str, str],
    provider_id: str,
    max_output_chars: int,
) -> dict[str, Any]:
    """Run one bounded LLM subtask per paper; one bad paper never kills valid siblings."""
    packets = parameters.get("paper_packets") or []
    provider_packets = provider_parameters.get("paper_packets") or []
    if not isinstance(packets, list) or not packets:
        raise ValueError("evidence batch requires paper_packets")
    if not isinstance(provider_packets, list) or len(provider_packets) != len(packets):
        raise ValueError("evidence batch provider projection is invalid")
    if any(not isinstance(packet, dict) for packet in provider_packets):
        raise ValueError("evidence batch provider projection is invalid")
    narrowed_schema = _narrow_evidence_batch_schema(
        draft_schema, field=field, paper_count=len(packets)
    )
    provider_evidence = request.get("providerEvidence")
    if not isinstance(provider_evidence, dict):
        raise ProviderAttemptEvidenceError("provider_attempt_evidence_invalid")
    capability_call_id = str(provider_evidence.get("capabilityCallId") or "").strip()
    if not capability_call_id:
        raise ProviderAttemptEvidenceError("provider_attempt_evidence_invalid")
    evidence_kind = str(provider_evidence.get("evidenceKind") or "provider_submission").strip()

    def request_bytes(index: int, attempt_index: int) -> tuple[dict[str, Any], bytes]:
        payload = _payload_for_evidence_paper(
            base_payload,
            parameters=parameters,
            packet=dict(provider_packets[index]),
            draft_schema=narrowed_schema,
            retry=attempt_index > 0,
        )
        payload = _admit_batch_provider_payload(
            payload,
            adapter_id=runtime_config.adapter,
            provider_schema=narrowed_schema,
        )
        body = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
        return payload, body

    reserved_main: tuple[Any, Any, bytes] | None = None
    if evidence_kind == "provider_submission":
        _payload, body = request_bytes(0, 0)
        repository = ProviderHttpAttemptRepository()
        attempt = repository.begin(
            capability_call_id=capability_call_id,
            attempt_number=provider_evidence.get("attemptNumber") or 0,
            request_body=body,
            endpoint=endpoint,
            adapter=runtime_config.adapter,
            operation_identity=evidence_paper_operation_identity(skill_id, parameters, packets[0], provider_evidence),
            provider=provider_id,
            model=runtime_config.model,
        )
        reserved_main = (repository, attempt, body)
    elif evidence_kind != "sub_call":
        raise ProviderAttemptEvidenceError("provider_attempt_evidence_kind_invalid")

    def run_one(index: int) -> dict[str, Any]:
        usages: list[dict[str, Any] | None] = []
        last_reason = "unknown"
        last_provider_execution_id: str | None = None
        attempts_made = 0
        for attempt_index in range(_EVIDENCE_BATCH_MAX_ATTEMPTS):
            attempts_made = attempt_index + 1
            _payload, body = request_bytes(index, attempt_index)
            if index == 0 and attempt_index == 0 and reserved_main is not None:
                repository, attempt, reserved_body = reserved_main
                if reserved_body != body:
                    raise ProviderAttemptEvidenceError("provider_attempt_request_mismatch")
            else:
                repository = ProviderHttpAttemptRepository()
                try:
                    attempt = repository.begin_sub_attempt(
                        capability_call_id=capability_call_id,
                        evidence_kind=EVIDENCE_KIND_SUB_CALL,
                        adapter_name=f"evidence_paper_{index + 1:03d}_attempt_{attempt_index + 1}",
                        request_body=body,
                        endpoint=endpoint,
                        adapter=runtime_config.adapter,
                        operation_identity=evidence_paper_operation_identity(skill_id, parameters, packets[index], provider_evidence),
                        provider=provider_id,
                        model=runtime_config.model,
                        logical_attempt_ordinal=attempt_index + 1,
                    )
                except ProviderAttemptEvidenceError:
                    last_reason = "provider_attempt_evidence_unavailable"
                    break
            headers = dict(base_headers)
            headers.update(attempt.headers)
            try:
                mark_skill_provider_io_started(request, repository, attempt)
                with httpx.Client(timeout=_llm_http_timeout(
                    runtime_config=runtime_config,
                    execution_policy=None,
                    deadline=None,
                )) as client:
                    response = client.post(endpoint, headers=headers, content=body)
            except (httpx.HTTPError, OSError) as exc:
                submission_known_not_sent = isinstance(
                    exc, (HttpxPoolTimeout, HttpxConnectTimeout, HttpxConnectError)
                )
                repository.finish(
                    attempt,
                    request_body=body,
                    outcome=(
                        "transport_not_submitted"
                        if submission_known_not_sent
                        else "transport_uncertain"
                    ),
                    http_status=None,
                    response_body=None,
                    response_headers=None,
                )
                last_provider_execution_id = attempt.attempt_id
                last_reason = (
                    "transport_not_submitted"
                    if submission_known_not_sent
                    else "transport_uncertain"
                )
                # A definitely-unsent attempt may use the existing bounded recovery
                # attempt.  An uncertain submission is absorbing: a second physical
                # Provider request would be an unproven replay.
                if (
                    submission_known_not_sent
                    and attempt_index + 1 < _EVIDENCE_BATCH_MAX_ATTEMPTS
                ):
                    continue
                break

            response_body = response.content
            status_code = int(response.status_code)
            repository.finish(
                attempt,
                request_body=body,
                outcome="response_received" if status_code < 400 else "provider_http_error",
                http_status=status_code,
                response_body=response_body,
                response_headers=response.headers,
            )
            parsed_body: dict[str, Any] | None = None
            try:
                candidate = json.loads(response_body.decode("utf-8"))
                if isinstance(candidate, dict):
                    parsed_body = candidate
                    usages.append(_billable_usage_wire_from_body(
                        candidate,
                        request_text=body.decode("utf-8", errors="replace"),
                        provider_id=provider_id,
                        model=runtime_config.model,
                    ))
            except (UnicodeDecodeError, json.JSONDecodeError):
                parsed_body = None

            if status_code >= 400:
                last_reason = f"http_{status_code}"
                retryable = status_code == 429 or status_code >= 500
                if retryable and attempt_index + 1 < _EVIDENCE_BATCH_MAX_ATTEMPTS:
                    continue
                break
            if parsed_body is None:
                last_reason = "provider_json_invalid"
                break
            try:
                raw = parsed_body["choices"][0]["message"]["content"]
                draft = _parse_structured_output(raw)
                if not isinstance(draft, dict):
                    raise ValueError("evidence batch draft is not an object")
                draft = _validate_structured_json(
                    draft, narrowed_schema, f"{skill_id} per-paper draft"
                )
            except (KeyError, IndexError, TypeError, ValueError, RuntimeError):
                last_reason = "structured_output_invalid"
                break
            return {
                "ok": True,
                "index": index,
                "draft": draft,
                "usages": usages,
                "attempts": attempt_index + 1,
            }
        packet = packets[index] if isinstance(packets[index], dict) else {}
        return {
            "ok": False,
            "index": index,
            "candidateId": str(packet.get("candidate_id") or ""),
            "reason": last_reason,
            "usages": usages,
            "attempts": attempts_made,
            **(
                {"providerExecutionId": last_provider_execution_id}
                if last_provider_execution_id
                else {}
            ),
        }

    workers = min(_EVIDENCE_BATCH_MAX_PARALLEL, len(packets))
    results: list[dict[str, Any]] = []
    if workers == 1:
        results.append(run_one(0))
    else:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="openair-evidence") as executor:
            futures = [executor.submit(run_one, index) for index in range(len(packets))]
            for future in as_completed(futures):
                results.append(future.result())
    results.sort(key=lambda row: int(row["index"]))

    merged = {field: []}
    failures: list[dict[str, Any]] = []
    usages: list[dict[str, Any] | None] = []
    for row in results:
        usages.extend(row.get("usages") or [])
        if row.get("ok") is True:
            _merge_structured_batch_draft(merged, row["draft"], field)
        else:
            failures.append({
                "paperIndex": int(row["index"]) + 1,
                "candidateId": row.get("candidateId") or "",
                "reason": row.get("reason") or "unknown",
                **(
                    {"providerExecutionId": row.get("providerExecutionId")}
                    if row.get("providerExecutionId")
                    else {}
                ),
            })
    # Validate the combined draft against the unchanged global contract before finalization.
    if merged[field]:
        merged = _validate_structured_json(merged, draft_schema, f"{skill_id} merged draft")
    return {
        "draft": merged,
        "successfulPaperCount": len(results) - len(failures),
        "failedPapers": failures,
        "usageWire": _combine_usage_wires(usages),
        "maxParallel": workers,
    }
def _frozen_llm_batch_policy(skill: Mapping[str, Any]) -> dict[str, Any] | None:
    source = skill.get("sourceContract")
    if not isinstance(source, Mapping):
        return None
    raw = source.get(DURABLE_LLM_BATCH_POLICY_KEY)
    if raw is None:
        return None
    return normalize_durable_llm_batch_policy_declaration(raw)


def _narrow_top_level_array_schema(
    schema: dict[str, Any], *, field: str, batch_size: int,
) -> dict[str, Any]:
    narrowed = deepcopy(schema)
    target = narrowed.get("properties", {}).get(field)
    if not isinstance(target, dict) or target.get("type") != "array":
        raise ValueError("durable_llm_batch_output_field_invalid")
    existing_max = target.get("maxItems")
    if isinstance(existing_max, int) and not isinstance(existing_max, bool):
        target["maxItems"] = min(existing_max, batch_size)
    else:
        target["maxItems"] = batch_size
    return narrowed


def _payload_for_structured_batch(
    base_payload: dict[str, Any],
    *,
    provider_message_contract: str | None,
    input_field: str,
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    payload = deepcopy(base_payload)
    messages = payload.get("messages")
    if not isinstance(messages, list) or len(messages) != 2:
        raise ValueError("durable_llm_batch_base_payload_invalid")
    user_content = json.loads(str(messages[1].get("content") or "{}"))
    parameter_key = (
        "scientificInput"
        if provider_message_contract == SCIENTIFIC_MINIMAL_V1
        else "selectedParameters"
    )
    visible = user_content.get(parameter_key)
    if not isinstance(visible, dict):
        raise ValueError("durable_llm_batch_input_payload_invalid")
    visible = deepcopy(visible)
    visible[input_field] = deepcopy(items)
    user_content[parameter_key] = visible
    messages[1]["content"] = json.dumps(
        user_content, ensure_ascii=False, separators=(",", ":")
    )
    return payload


def _structured_batch_draft_failure(exc: BaseException, *, stage: str, instance: Any) -> dict[str, Any]:
    """A completed response preserves decode, shape, parse and schema boundaries."""
    detail = (
        _schema_failure_diagnostics(exc, instance)
        if stage == "json_schema_failed"
        else {"failed_stage": stage, "first_mismatch": type(exc).__name__}
    )
    return _failed(
        "The completed provider response did not satisfy the frozen structured draft contract.",
        "provider_response_contract_invalid",
        **detail,
    )


def _structured_batch_failure_result(failures: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate typed failed attempts without converting them to schema errors.

    Stable input order selects the diagnostic within a state. An uncertain
    attempt stays uncertain even when another batch has a known failure.
    Successful batches are handled separately and are never replayed here.
    """
    records = [row for row in failures if isinstance(row, dict)]
    typed = [row["failure"] for row in records if isinstance(row.get("failure"), dict)]
    uncertain = [item for item in typed if item.get("state") == "unknown"]
    if typed:
        result = deepcopy((uncertain or typed)[0])
    else:
        result = _failed(
            "The structured batch runtime did not preserve its failure evidence.",
            "provider_configuration",
            failed_stage="structured_batch_failure_evidence",
            first_mismatch="typed_batch_failure_missing",
        )
    result.setdefault("diagnostics", {})["structuredBatch"] = {"failedBatches": deepcopy(records)}
    return result


def _structured_batch_http_failure(status_code: int, *, response_headers: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Preserve HTTP facts; a rejected HTTP exchange is not a malformed draft.

    As in the non-batch executor, 5xx does not establish a scientific result.
    This classification does not authorize another submission or choose a model.
    """
    transient = status_code in {408, 429} or 500 <= status_code <= 599
    result = _failed(
        "The provider returned an HTTP error; no structured model result was received.",
        "provider_http_error" if transient else "provider_rejected_request",
        failed_stage="provider_http_response",
        first_mismatch=f"http_{status_code}",
        providerHttp={"httpStatus": status_code},
    )
    from app.modules.provider_transport_evidence import provider_request_identity
    request_id = provider_request_identity(response_headers)
    if request_id is not None:
        result["diagnostics"]["providerHttp"]["providerRequestId"] = request_id
    return result


def _structured_batch_transport_failure(exc: BaseException) -> dict[str, Any]:
    """Keep definitely-unsent failures separate from possibly-submitted requests."""
    if isinstance(exc, HttpxPoolTimeout):
        phase, submitted = "pool", False
    elif isinstance(exc, (HttpxConnectTimeout, HttpxConnectError)):
        phase, submitted = "connect", False
    elif isinstance(exc, (HttpxReadTimeout, HttpxReadError)):
        phase, submitted = "read", None
    elif isinstance(exc, (HttpxWriteTimeout, HttpxWriteError)):
        phase, submitted = "write", None
    elif isinstance(exc, (HttpxLocalProtocolError, HttpxRemoteProtocolError)):
        phase, submitted = "protocol", None
    else:
        phase, submitted = "other", None
    result = _failed(
        "The provider exchange did not establish a structured model result.",
        "provider_connection_unavailable" if submitted is False else "provider_transport_uncertain",
        failed_stage=f"provider_transport_{phase}",
        first_mismatch=type(exc).__name__,
        transport={"phase": phase, "exceptionType": type(exc).__name__, "submissionKnownNotSent": submitted is False},
    )
    if submitted is not False:
        result.update(state="unknown", externalResultUnknown=True)
    return result


def _execute_structured_batch_requests(
    *,
    request: dict[str, Any],
    skill_id: str,
    provider_parameters: dict[str, Any],
    batch_policy: dict[str, Any],
    provider_message_contract: str | None,
    base_payload: dict[str, Any],
    draft_schema: dict[str, Any],
    runtime_config: Any,
    endpoint: str,
    base_headers: dict[str, str],
    provider_id: str,
    execution_policy: dict[str, Any],
    task_deadline: float | None,
    max_output_chars: int,
) -> dict[str, Any]:
    """Run one generic bounded structured map over an input array.

    The runtime only partitions input, executes the same frozen scientific Skill,
    and merges the declared output array. Scientific decisions stay model-owned.
    """
    input_field = batch_policy["input_array_field"]
    output_field = batch_policy["output_array_field"]
    identity_field = batch_policy["identity_field"]
    raw_items = provider_parameters.get(input_field)
    if not isinstance(raw_items, list) or not raw_items:
        raise ValueError("durable_llm_batch_input_array_invalid")
    if any(not isinstance(item, dict) for item in raw_items):
        raise ValueError("durable_llm_batch_input_item_invalid")

    max_items = int(batch_policy["max_items_per_batch"])
    batches = [
        [deepcopy(item) for item in raw_items[index : index + max_items]]
        for index in range(0, len(raw_items), max_items)
    ]
    provider_evidence = request.get("providerEvidence")
    if not isinstance(provider_evidence, dict):
        raise ProviderAttemptEvidenceError("provider_attempt_evidence_invalid")
    capability_call_id = str(provider_evidence.get("capabilityCallId") or "").strip()
    if not capability_call_id:
        raise ProviderAttemptEvidenceError("provider_attempt_evidence_invalid")
    evidence_kind = str(
        provider_evidence.get("evidenceKind") or "provider_submission"
    ).strip()

    def request_bytes(index: int) -> tuple[dict[str, Any], bytes, dict[str, Any]]:
        narrowed = _narrow_top_level_array_schema(
            draft_schema, field=output_field, batch_size=len(batches[index])
        )
        payload = _payload_for_structured_batch(
            base_payload,
            provider_message_contract=provider_message_contract,
            input_field=input_field,
            items=batches[index],
        )
        payload = _admit_batch_provider_payload(
            payload,
            adapter_id=runtime_config.adapter,
            provider_schema=narrowed,
        )
        body = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode(
            "utf-8"
        )
        return payload, body, narrowed

    reserved_main: tuple[Any, Any, bytes] | None = None
    if evidence_kind == "provider_submission":
        _payload, body, _schema = request_bytes(0)
        repository = ProviderHttpAttemptRepository()
        attempt = repository.begin(
            capability_call_id=capability_call_id,
            attempt_number=provider_evidence.get("attemptNumber") or 0,
            request_body=body,
            endpoint=endpoint,
            adapter=runtime_config.adapter,
            operation_identity=f"structured_batch:{skill_id}:batch:1",
            provider=provider_id,
            model=runtime_config.model,
            resolved_execution_policy=execution_policy,
        )
        reserved_main = (repository, attempt, body)
    elif evidence_kind != "sub_call":
        raise ProviderAttemptEvidenceError("provider_attempt_evidence_kind_invalid")

    def run_one(index: int) -> dict[str, Any]:
        try:
            _payload, body, narrowed = request_bytes(index)
            if index == 0 and reserved_main is not None:
                repository, attempt, reserved_body = reserved_main
                if reserved_body != body:
                    raise ProviderAttemptEvidenceError("provider_attempt_request_mismatch")
            else:
                repository = ProviderHttpAttemptRepository()
                try:
                    attempt = repository.begin_sub_attempt(
                        capability_call_id=capability_call_id,
                        evidence_kind=EVIDENCE_KIND_SUB_CALL,
                        adapter_name=f"structured_batch_{index + 1:03d}",
                        request_body=body,
                        endpoint=endpoint,
                        adapter=runtime_config.adapter,
                        operation_identity=f"structured_batch:{skill_id}:batch:{index + 1}",
                        provider=provider_id,
                        model=runtime_config.model,
                        resolved_execution_policy=execution_policy,
                    )
                except ProviderAttemptEvidenceError:
                    return {
                        "ok": False,
                        "index": index,
                        "reason": "provider_attempt_evidence_unavailable",
                        "identities": [str(row.get(identity_field) or "") for row in batches[index]],
                        "usage": None,
                        "failure": _provider_attempt_evidence_failed(),
                    }
            headers = dict(base_headers)
            headers.update(attempt.headers)
            timeout = _llm_http_timeout(
                runtime_config=runtime_config,
                execution_policy=execution_policy,
                deadline=task_deadline,
            )
            mark_skill_provider_io_started(request, repository, attempt)
            with httpx.Client(timeout=timeout) as client:
                response = client.post(endpoint, headers=headers, content=body)
            response_body = response.content
            status_code = int(response.status_code)
            repository.finish(
                attempt,
                request_body=body,
                outcome="response_received" if status_code < 400 else "provider_http_error",
                http_status=status_code,
                response_body=response_body,
                response_headers=response.headers,
            )
            if status_code >= 400:
                return {
                    "ok": False,
                    "index": index,
                    "reason": f"http_{status_code}",
                    "identities": [str(row.get(identity_field) or "") for row in batches[index]],
                    "usage": None,
                    "failure": _structured_batch_http_failure(status_code, response_headers=response.headers),
                }
            try:
                parse_stage = 'provider_body_decode'
                provider_body = json.loads(response_body.decode("utf-8"))
                if not isinstance(provider_body, dict):
                    raise ValueError("provider body is not an object")
                parse_stage = 'provider_response_shape'
                raw = provider_body["choices"][0]["message"]["content"]
                parse_stage = 'structured_output_parse'
                draft = _parse_structured_output(raw)
                if not isinstance(draft, dict):
                    raise ValueError("structured batch draft is not an object")
                parse_stage = 'json_schema_failed'
                draft = _validate_structured_json(
                    draft, narrowed, f"{skill_id} structured batch"
                )
            except (KeyError, IndexError, TypeError, UnicodeDecodeError, json.JSONDecodeError, ValueError, RuntimeError) as exc:
                return {
                    "ok": False,
                    "index": index,
                    "reason": (
                        "output_limit_exceeded"
                        if parse_stage == "output_limit_exceeded"
                        else "structured_output_invalid"
                    ),
                    "identities": [str(row.get(identity_field) or "") for row in batches[index]],
                    "usage": _billable_usage_wire_from_body(
                        provider_body if "provider_body" in locals() else {},
                        request_text=body.decode("utf-8", errors="replace"),
                        response_text=response_body.decode("utf-8", errors="replace"),
                        provider_id=provider_id,
                        model=runtime_config.model,
                    ),
                    "failure": _structured_batch_draft_failure(exc, stage=parse_stage, instance=locals().get("draft")),
                }
            usage = _billable_usage_wire_from_body(
                provider_body,
                request_text=body.decode("utf-8", errors="replace"),
                response_text=str(raw),
                provider_id=provider_id,
                model=runtime_config.model,
            )
            return {"ok": True, "index": index, "draft": draft, "usage": usage}
        except (httpx.HTTPError, OSError) as exc:
            if "repository" in locals() and "attempt" in locals() and "body" in locals():
                repository.finish(
                    attempt,
                    request_body=body,
                    outcome=(
                        "transport_not_submitted"
                        if isinstance(exc, (HttpxPoolTimeout, HttpxConnectTimeout, HttpxConnectError))
                        else "transport_uncertain"
                    ),
                    http_status=None,
                    response_body=None,
                    response_headers=None,
                )
            submission_known_not_sent = isinstance(
                exc, (HttpxPoolTimeout, HttpxConnectTimeout, HttpxConnectError)
            )
            return {
                "ok": False,
                "index": index,
                "reason": "transport_not_submitted" if submission_known_not_sent else "transport_uncertain",
                "identities": [str(row.get(identity_field) or "") for row in batches[index]],
                "usage": None,
                "failure": _structured_batch_transport_failure(exc),
            }

    workers = min(int(batch_policy["max_parallel"]), len(batches))
    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="openair-llm-batch") as executor:
        futures = [executor.submit(run_one, index) for index in range(len(batches))]
        for future in as_completed(futures):
            results.append(future.result())
    results.sort(key=lambda row: int(row["index"]))

    merged = {output_field: []}
    failures: list[dict[str, Any]] = []
    usages: list[dict[str, Any] | None] = []
    for row in results:
        usages.append(row.get("usage"))
        if row.get("ok") is True:
            _merge_structured_batch_draft(merged, row["draft"], output_field)
        else:
            failures.append({
                "batchIndex": int(row["index"]) + 1,
                "reason": str(row.get("reason") or "unknown"),
                "inputIdentities": list(row.get("identities") or []),
                "failure": deepcopy(row.get("failure")),
            })
    if merged[output_field]:
        merged = _validate_structured_json(
            merged, draft_schema, f"{skill_id} merged structured batch"
        )
    return {
        "draft": merged,
        "successfulBatchCount": len(results) - len(failures),
        "failedBatches": failures,
        "usageWire": _combine_usage_wires(usages),
        "maxParallel": workers,
        "batchCount": len(batches),
    }


def _provider_visible_parameters(
    metadata: dict[str, Any], parameters: dict[str, Any]
) -> dict[str, Any]:
    """Compatibility wrapper for deterministic model-visible projection."""
    return project_provider_parameters(metadata, parameters)


def _execute_durable_huiyan_skill(
    request: dict[str, Any], skill: dict[str, Any], *, runtime: dict[str, Any]
) -> dict[str, Any]:
    """Make one primary provider request for an explicitly durable Skill.

    A direct structured reasoning Skill may issue at most one side-effect-free
    structured-contract correction sub-call after a known completed response. The orchestrator
    persists and fences the parent call before entering this adapter; any transport
    outcome that cannot be known remains absorbing and is returned as unknown.
    """
    parameters = request.get("parameters")
    if not isinstance(parameters, dict):
        return _failed("The requested capability input is invalid.", "provider_response_contract_invalid")
    raw_execution_policy = request.get("resolvedExecutionPolicy")
    source_contract = skill.get("sourceContract")
    if (
        raw_execution_policy is None
        and isinstance(source_contract, Mapping)
        and source_contract.get(DURABLE_LLM_EXECUTION_POLICY_KEY) is not None
    ):
        return _failed(
            "The declared execution policy was not frozen before this call.",
            "provider_configuration",
            failed_stage="execution_policy_precondition",
            first_mismatch="resolved_execution_policy_missing",
        )
    try:
        execution_policy = (
            normalize_resolved_durable_llm_execution_policy(raw_execution_policy)
            if raw_execution_policy is not None
            else None
        )
    except ValueError:
        return _failed(
            "The frozen execution policy is invalid.", "provider_configuration",
            failed_stage="execution_policy_precondition",
            first_mismatch="resolved_execution_policy_invalid",
        )

    task_deadline = _llm_task_deadline(execution_policy)
    input_warnings: list[str] = []

    provider_id = str(
        execution_policy["providerId"]
        if execution_policy is not None
        else request.get("logicalProviderId") or ""
    ).strip()
    model_profile = str(runtime.get("modelProfile") or "").strip()
    if not provider_id:
        return _failed("The frozen provider configuration is invalid.", "provider_configuration")
    if execution_policy is None and model_profile not in _DURABLE_LLM_MODEL_PROFILES:
        return _failed("The frozen provider configuration is invalid.", "provider_configuration")
    try:
        registry = load_provider_registry()
        provider_definition = registry.require(provider_id)
        runtime_config = (
            resolve_provider_runtime_config(
                provider_id,
                model=str(execution_policy["model"]),
                registry=registry,
            )
            if execution_policy is not None
            else resolve_provider_runtime_config(
                provider_id,
                model_profile=model_profile,
                registry=registry,
            )
        )
        if execution_policy is not None:
            runtime_config = replace(
                runtime_config,
                timeoutSeconds=float(execution_policy["readTimeoutSeconds"]),
            )
            if execution_policy["adapterId"] != runtime_config.adapter:
                return _failed("The frozen provider adapter is not supported.", "provider_configuration")
            if execution_policy["loadReferences"] is not False:
                return _failed("The frozen reference-loading policy is unsupported.", "provider_configuration")
        if runtime_config.adapter != "huiyan_chat_completions":
            return _failed("The frozen provider adapter is not supported.", "provider_configuration")
        if runtime.get("runtimeKind") != _RUNTIME_KIND_LLM_REASONING:
            return _failed("The frozen Skill runtime is invalid.", "provider_configuration")
        metadata, instructions = _read_skill_contract(skill)
        provider_message_contract = resolve_provider_message_contract(metadata)
        structured_contract = _structured_json_contract(skill)
        frozen_finalization = _frozen_finalization_contract(skill)
        json_contract = (
            _frozen_schema_contract(skill)
            if frozen_finalization is not None
            else structured_contract
        )
        if frozen_finalization is not None and json_contract is None:
            raise ValueError("frozen finalization schemas unavailable")
        if (
            execution_policy is not None
            and execution_policy["structuredOutputRecoveryAttempts"] > 0
            and json_contract is None
        ):
            raise ValueError("structured output recovery requires a frozen structured contract")
        if provider_message_contract == SCIENTIFIC_MINIMAL_V1 and json_contract is None:
            raise ValueError("scientific_minimal_requires_structured_output")
        if json_contract is not None:
            try:
                provider_input = _validate_structured_json(
                    parameters, json_contract["input"]["schema"], "Skill input", role="input",
                    input_warnings=input_warnings,
                )
            except ValueError:
                return _failed(
                    "The frozen Skill parameters do not meet the structured input contract.",
                    "skill_input_invalid",
                )
            output_contract = _structured_output_contract(skill, json_contract)
        else:
            output_contract = _durable_output_contract(metadata, skill=skill)
            provider_input = parameters
        # Consumers may read the unique representation-normalized copy, while
        # the durable request remains the unchanged source of original inputs.
        parameters = provider_input
        provider_parameters = _provider_visible_parameters(metadata, provider_input)
        if normalize_external_action_evidence(request.get("externalActionEvidence", [])):
            return _failed(
                "A reasoning-only Skill cannot receive external-action evidence.",
                "skill_input_invalid",
            )
        max_output_chars = provider_definition.maxOutputChars
    except (ProviderRegistryError, OSError, yaml.YAMLError, ValueError):
        return _failed("The frozen provider or Skill contract is unavailable.", "provider_configuration")

    if json_contract is not None:
        system_content = (
            "Execute exactly one registered OpenAIR structured Skill. Return ONLY the raw JSON object "
            "required by the selected Skill; do not wrap it in title/content or markdown. Use only the frozen "
            "request inputs and the selected Skill instructions. This is a reasoning-only Skill: do not browse, "
            "retrieve, query databases, read or write files, execute code, run experiments, or claim any external "
            "observation. Do not invent DOI, URL, database records, execution logs, code-run results, or experiment "
            "observations. Preserve uncertainty and evidence limits exactly."
        )
        provider_output_schema = (
            frozen_finalization["draftSchema"]["schema"]
            if frozen_finalization is not None
            else json_contract["output"]["schema"]
        )
        output_payload: dict[str, Any] = {
            **output_contract,
            "maxOutputChars": max_output_chars,
        }
        from app.execution.scientific_output_fields import scientific_output_fields
        output_payload["scientificOutputFields"] = scientific_output_fields(provider_output_schema)
        if structured_contract is not None:
            output_payload["guardVersion"] = structured_contract["guardVersion"]
        llm_cfg = metadata.get("llm") if isinstance(metadata.get("llm"), dict) else {}
        temperature = llm_cfg.get("temperature", 0)
        if execution_policy is not None:
            max_tokens = int(execution_policy["maxOutputTokens"])
        else:
            declared_tokens = llm_cfg.get("max_completion_tokens")
            max_tokens = _token_budget_for_character_limit(max_output_chars)
            if isinstance(declared_tokens, int) and not isinstance(declared_tokens, bool) and declared_tokens > 0:
                max_tokens = min(max_tokens, declared_tokens)
    else:
        system_content = (
            "Execute exactly one registered OpenAIR research Skill. Return one JSON object with "
            "title and content. content must be the complete user-visible artifact required by outputContract. "
            f"The content value must contain at most {max_output_chars} characters. "
            "Use only the frozen request inputs. This is a reasoning-only Skill: do not browse, retrieve, "
            "query databases, read or write files, execute code, run experiments, or claim any external "
            "observation. Do not invent DOI, URL, database records, file paths, execution logs, code-run "
            "results, or experiment observations. A source identifier may appear only when that exact "
            "identifier is already present in the frozen inputs. Preserve evidence limits and do not include "
            "provider, runtime, secret, trace, workspace, or internal execution metadata. Do not copy "
            "template comments."
        )
        output_payload = {**output_contract, "maxOutputChars": max_output_chars}
        temperature = 0.2
        max_tokens = (
            int(execution_policy["maxOutputTokens"])
            if execution_policy is not None
            else _token_budget_for_character_limit(max_output_chars)
        )

    from app.execution.scientific_input_fidelity import with_input_limits

    system_content = with_input_limits(system_content, input_warnings)

    try:
        provider_user_payload = (
            build_scientific_minimal_user_payload(
                instructions=instructions,
                scientific_input=provider_parameters,
                output_schema=provider_output_schema if json_contract is not None else None,
            )
            if provider_message_contract == SCIENTIFIC_MINIMAL_V1
            else {
                "selectedSkill": {
                    "skillId": skill["skillId"],
                    "skillMetadata": metadata,
                    "instructions": instructions,
                },
                "frozenGoal": request.get("frozenGoal"),
                "frozenConstraints": request.get("frozenConstraints") or [],
                "controllerInputs": request.get("controllerInputContext") or [],
                "selectedParameters": provider_parameters,
                "runtimeContract": {
                    "kind": _RUNTIME_KIND_LLM_REASONING,
                    "requiresExternalAction": False,
                    "adapterId": runtime["adapterId"],
                    "evidenceContractVersion": _NO_EXTERNAL_EVIDENCE_CONTRACT,
                },
                "responseRequirement": "Return one valid json object only.",
                "outputContract": output_payload,
            }
        )
    except ValueError:
        return _failed(
            "The frozen provider or Skill contract is unavailable.",
            "provider_configuration",
        )

    payload = {
        "model": runtime_config.model,
        "messages": [
            {
                "role": "system",
                "content": (
                    f"{system_content} Return only valid JSON. "
                    "Do not include prose outside the JSON object."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    provider_user_payload, ensure_ascii=False, separators=(",", ":")
                ),
            },
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "response_format": {
            "type": (
                str(execution_policy["responseMode"])
                if execution_policy is not None
                else "json_object"
            )
        },
        **(
            {"reasoning_effort": str(execution_policy["reasoningEffort"])}
            if execution_policy is not None
            else {}
        ),
    }
    if json_contract is not None:
        try:
            payload = apply_provider_schema_transport(
                runtime_config.adapter,
                payload=payload,
                response_format=payload.get("response_format"),
                provider_schema=provider_output_schema,
            )
        except ProviderSchemaCapabilityError:
            return _failed(
                "The selected Provider cannot natively enforce the frozen output Schema.",
                "provider_configuration",
                failed_stage="provider_schema_compatibility",
                first_mismatch=PROVIDER_SCHEMA_UNSUPPORTED,
            )
        except ValueError:
            return _failed(
                "The frozen provider or Skill contract is unavailable.",
                "provider_configuration",
            )
    endpoint = f"{runtime_config.baseUrl.rstrip('/')}/v1/chat/completions"
    request_body = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    headers = {"Authorization": f"Bearer {runtime_config.apiKey}", "Content-Type": "application/json"}
    provider_body_warnings: list[str] = []
    evidence_batch_failures: list[dict[str, Any]] = []
    structured_batch_failures: list[dict[str, Any]] = []
    batch_usage_wire: dict[str, Any] | None = None
    # Synthetic aggregates are deterministic merges of already-validated drafts.
    # They must never be judged as a single Provider response against maxOutputChars.
    provider_content_is_synthetic_aggregate = False
    batch_field = _evidence_batch_field(skill["skillId"], parameters, frozen_finalization)
    structured_batch_policy = _frozen_llm_batch_policy(skill)
    if batch_field is not None and structured_batch_policy is not None:
        return _failed(
            "The frozen Skill declares more than one batch runtime.",
            "provider_configuration",
        )
    if structured_batch_policy is not None:
        if execution_policy is None or frozen_finalization is None:
            return _failed(
                "The frozen structured batch runtime is incomplete.",
                "provider_configuration",
            )
        if execution_policy["structuredOutputRecoveryAttempts"] != 0:
            return _failed(
                "Structured batch execution requires batch-local failure isolation instead of whole-call recovery.",
                "provider_configuration",
            )
        try:
            batch = _execute_structured_batch_requests(
                request=request,
                skill_id=skill["skillId"],
                provider_parameters=provider_parameters,
                batch_policy=structured_batch_policy,
                provider_message_contract=provider_message_contract,
                base_payload=payload,
                draft_schema=frozen_finalization["draftSchema"]["schema"],
                runtime_config=runtime_config,
                endpoint=endpoint,
                base_headers={
                    "Authorization": f"Bearer {runtime_config.apiKey}",
                    "Content-Type": "application/json",
                },
                provider_id=provider_id,
                execution_policy=execution_policy,
                task_deadline=task_deadline,
                max_output_chars=max_output_chars,
            )
        except ProviderAttemptEvidenceError as exc:
            return _provider_attempt_evidence_failed(exc)
        except ProviderSchemaCapabilityError:
            return _failed(
                "The selected Provider cannot natively enforce the batch output Schema.",
                "provider_configuration",
                failed_stage="provider_schema_compatibility",
                first_mismatch=PROVIDER_SCHEMA_UNSUPPORTED,
            )
        except (ValueError, RuntimeError):
            return _failed(
                "The bounded structured batch execution could not be prepared.",
                "provider_configuration",
            )
        batch_usage_wire = batch.get("usageWire")
        structured_batch_failures = list(batch.get("failedBatches") or [])
        if int(batch.get("successfulBatchCount") or 0) < 1:
            _record_durable_skill_usage(
                request=request, provider_id=provider_id, model=runtime_config.model,
                usage_wire=batch_usage_wire, status="failed",
            )
            return _structured_batch_failure_result(structured_batch_failures)
        body = {
            "choices": [{"message": {"content": json.dumps(
                batch["draft"], ensure_ascii=False, separators=(",", ":")
            )}}],
            "model": runtime_config.model,
        }
        provider_content_is_synthetic_aggregate = True
        provider_body_warnings.append(
            "structured_batch:"
            f"count={int(batch.get('batchCount') or 0)}:"
            f"max_parallel={int(batch.get('maxParallel') or 1)}"
        )
        if structured_batch_failures:
            provider_body_warnings.append(
                f"structured_batch_partial_failure:{len(structured_batch_failures)}"
            )
        request_body = json.dumps(
            payload, ensure_ascii=True, separators=(",", ":")
        ).encode("utf-8")
    elif batch_field is not None:
        if execution_policy is not None:
            return _failed(
                "The frozen execution policy cannot be combined with the specialized evidence-batch runtime.",
                "provider_configuration",
            )
        try:
            batch = _execute_evidence_batch_requests(
                request=request,
                skill_id=skill["skillId"],
                parameters=parameters,
                provider_parameters=provider_parameters,
                field=batch_field,
                base_payload=payload,
                draft_schema=frozen_finalization["draftSchema"]["schema"],
                runtime_config=runtime_config,
                endpoint=endpoint,
                base_headers={
                    "Authorization": f"Bearer {runtime_config.apiKey}",
                    "Content-Type": "application/json",
                },
                provider_id=provider_id,
                max_output_chars=max_output_chars,
            )
        except ProviderAttemptEvidenceError as exc:
            return _provider_attempt_evidence_failed(exc)
        except ProviderSchemaCapabilityError:
            return _failed(
                "The selected Provider cannot natively enforce the batch output Schema.",
                "provider_configuration",
                failed_stage="provider_schema_compatibility",
                first_mismatch=PROVIDER_SCHEMA_UNSUPPORTED,
            )
        except (ValueError, RuntimeError):
            return _failed(
                "The bounded per-paper Evidence execution could not be prepared.",
                "provider_configuration",
            )
        batch_usage_wire = batch.get("usageWire")
        evidence_batch_failures = list(batch.get("failedPapers") or [])
        if int(batch.get("successfulPaperCount") or 0) < 1:
            _record_durable_skill_usage(
                request=request, provider_id=provider_id, model=runtime_config.model,
                usage_wire=batch_usage_wire, status="failed",
            )
            uncertain = [
                item for item in evidence_batch_failures
                if str(item.get("reason") or "") == "transport_uncertain"
            ]
            if uncertain:
                execution_ids = [
                    str(item.get("providerExecutionId") or "").strip()
                    for item in uncertain
                    if str(item.get("providerExecutionId") or "").strip()
                ]
                result = _unknown(
                    "The per-paper Provider execution result cannot be established.",
                    provider_execution_id=(execution_ids[0] if len(execution_ids) == 1 else None),
                )
                if execution_ids:
                    result["providerExecutionIds"] = execution_ids
                result["diagnostics"] = {
                    "failedPapers": deepcopy(evidence_batch_failures),
                }
                return result
            return _failed(
                "No usable Evidence could be extracted from the available papers.",
                "provider_response_contract_invalid",
                failed_stage="per_paper_evidence_extraction_exhausted",
                first_mismatch="no_usable_paper_result",
            )
        body = {
            "choices": [{"message": {"content": json.dumps(
                batch["draft"], ensure_ascii=False, separators=(",", ":")
            )}}],
            "model": runtime_config.model,
        }
        provider_content_is_synthetic_aggregate = True
        provider_body_warnings.append(
            f"per_paper_evidence_batch:max_parallel={int(batch.get('maxParallel') or 1)}"
        )
        request_body = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    else:
        if json_contract is not None:
            request_body = json.dumps(
                payload, ensure_ascii=True, separators=(",", ":")
            ).encode("utf-8")
        provider_evidence = request.get("providerEvidence")
        if not isinstance(provider_evidence, dict):
            return _provider_attempt_evidence_failed(
                ProviderAttemptEvidenceError("provider_attempt_evidence_invalid")
            )
        attempt_repository = ProviderHttpAttemptRepository()
        try:
            evidence_kind = str(provider_evidence.get("evidenceKind") or "provider_submission").strip()
            if evidence_kind == "sub_call":
                adapter_name = str(provider_evidence.get("adapterName") or "").strip()
                if not adapter_name:
                    raise ProviderAttemptEvidenceError("provider_sub_attempt_identity_invalid")
                # The operation identity must distinguish concurrent siblings that
                # share one adapter name (e.g. parallel per-record deep verification
                # under one parent call): without the request hash the dedup query in
                # _reserve_attempt treats them as replays of one running attempt and
                # they fail each other as transport_not_submitted.
                attempt = attempt_repository.begin_sub_attempt(
                    capability_call_id=str(provider_evidence.get("capabilityCallId") or ""),
                    evidence_kind=EVIDENCE_KIND_SUB_CALL,
                    adapter_name=adapter_name,
                    request_body=request_body,
                    endpoint=endpoint,
                    adapter=runtime_config.adapter,
                    operation_identity=(
                        f"skill:{skill['skillId']}:sub:{adapter_name}"
                        f":{request_sha256(request_body)[:16]}"
                    ),
                    provider=provider_id,
                    model=runtime_config.model,
                    resolved_execution_policy=execution_policy,
                )
            elif evidence_kind == "provider_submission":
                attempt = attempt_repository.begin(
                    capability_call_id=str(provider_evidence.get("capabilityCallId") or ""),
                    attempt_number=provider_evidence.get("attemptNumber") or 0,
                    request_body=request_body,
                    endpoint=endpoint,
                    adapter=runtime_config.adapter,
                    operation_identity=f"skill:{skill['skillId']}:provider_submission",
                    provider=provider_id,
                    model=runtime_config.model,
                    resolved_execution_policy=execution_policy,
                )
            else:
                raise ProviderAttemptEvidenceError("provider_attempt_evidence_kind_invalid")
        except ProviderAttemptEvidenceError as exc:
            return _provider_attempt_evidence_failed(exc)
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
                mark_skill_provider_io_started(request, attempt_repository, attempt)
                with httpx.Client(timeout=_llm_http_timeout(
                    runtime_config=runtime_config,
                    execution_policy=execution_policy,
                    deadline=task_deadline,
                )) as client:
                    response = client.post(endpoint, headers=headers, content=request_body)
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
            try:
                body = json.loads(response_body.decode("utf-8")) if response_body is not None else response.json()
            except ValueError:
                raw_text = (
                    response_body.decode("utf-8", errors="replace") if response_body is not None else ""
                )
                if not str(raw_text or "").strip():
                    _record_durable_skill_usage(
                        request=request,
                        provider_id=provider_id,
                        model=runtime_config.model,
                        usage_wire=_billable_usage_wire_from_body(
                            {},
                            request_text=request_body.decode("utf-8", errors="replace"),
                            response_text=raw_text,
                            provider_id=provider_id,
                            model=runtime_config.model,
                        ),
                        status="failed",
                    )
                    return _failed(
                        "The provider returned no usable semantic content.",
                        "provider_response_contract_invalid",
                        failed_stage="empty_semantic_content",
                        first_mismatch="empty_semantic_content",
                    )
                body = {"choices": [{"message": {"content": raw_text}}]}
                provider_body_warnings.append("provider_body_text_fallback")
        except httpx.HTTPStatusError as exc:
            status_code = int(exc.response.status_code)
            classification = (
                "provider_rejected_request"
                if 400 <= status_code < 500
                else "provider_http_error"
            )
            return _failed(
                "The provider returned an HTTP error for this frozen research request.",
                classification,
                failed_stage="provider_http_response",
                first_mismatch=f"http_{status_code}",
                providerHttp={"httpStatus": status_code},
            )
        except ValueError:
            return _failed(
                "The provider returned a completed response that does not meet the Skill artifact contract.",
                "provider_response_contract_invalid",
            )
        except (httpx.HTTPError, OSError) as exc:
            submission_known_not_sent = isinstance(
                exc, (HttpxPoolTimeout, HttpxConnectTimeout, HttpxConnectError)
            )
            attempt_repository.finish(
                attempt,
                request_body=request_body,
                outcome=(
                    "transport_not_submitted"
                    if submission_known_not_sent
                    else "transport_uncertain"
                ),
                http_status=None,
                response_body=None,
                response_headers=None,
            )
            if submission_known_not_sent:
                return _failed(
                    "The Provider submission did not reach the Provider transport.",
                    "provider_connection_unavailable",
                    failed_stage="provider_transport_pre_submission",
                    first_mismatch=type(exc).__name__,
                    providerExecutionId=attempt.attempt_id,
                )
            return _unknown(
                "The provider result cannot be established after one request.",
                provider_execution_id=attempt.attempt_id,
            )

    # Meter as soon as a completed HTTP body is in hand — before artifact
    # validation — so schema/truthfulness failures still settle burned tokens.
    usage_wire = batch_usage_wire or _billable_usage_wire_from_body(
        body,
        request_text=request_body.decode("utf-8", errors="replace"),
        provider_id=provider_id,
        model=runtime_config.model,
    )

    def _meter(*, status: str) -> None:
        _record_durable_skill_usage(
            request=request,
            provider_id=provider_id,
            model=runtime_config.model,
            usage_wire=usage_wire,
            status=status,
        )

    from app.execution.skill_contract_policy import (
        ResearchOutputContractGap,
        finalizer_failure_preserves_draft,
        materialize_semantic_output,
        provider_content_for_preservation,
    )

    artifact_warnings: list[str] = [*input_warnings, *provider_body_warnings]
    result_protocol_metadata: dict[str, Any] | None = None
    try:
        if json_contract is not None:
            raw = ""
            parsed: Any = None
            structured_recovery_used = False
            try:
                raw = body["choices"][0]["message"]["content"]
                if not provider_content_is_synthetic_aggregate and len(str(raw or "")) > max_output_chars:
                    artifact_warnings.append("output_contract_gap:$:max_output_chars")
                parsed = _parse_structured_output(raw)
                if not isinstance(parsed, dict):
                    raise ValueError("structured Skill output must be a JSON object")
            except (KeyError, IndexError, TypeError, ValueError):
                can_recover = bool(
                    execution_policy is not None
                    and execution_policy["structuredOutputRecoveryAttempts"] == 1
                    and batch_field is None
                    and str(raw or "").strip()
                )
                if not can_recover:
                    if isinstance(raw, str) and raw.strip():
                        raise ResearchOutputContractGap(
                            "structured_output_json_object_required"
                        ) from None
                    _meter(status="failed")
                    return _failed(
                        "The provider did not return the required structured JSON object.",
                        "provider_response_contract_invalid",
                        failed_stage="json_parse_failed",
                        first_mismatch="structured_output_json_object_required",
                    )
                try:
                    correction_body, correction_usage = _request_structured_correction(
                        request=request,
                        base_payload=payload,
                        previous_output=str(raw),
                        runtime_config=runtime_config,
                        endpoint=endpoint,
                        headers=headers,
                        attempt_repository=attempt_repository,
                        provider_id=provider_id,
                        resolved_execution_policy=execution_policy,
                        task_deadline=task_deadline,
                        correction_hint="json_parse_failed",
                    )
                    structured_recovery_used = True
                    usage_wire = _combine_usage_wires([usage_wire, correction_usage])
                    raw = correction_body["choices"][0]["message"]["content"]
                    if len(str(raw or "")) > max_output_chars:
                        artifact_warnings.append("output_contract_gap:$:max_output_chars")
                    parsed = _parse_structured_output(raw)
                    if not isinstance(parsed, dict):
                        raise ValueError("structured Skill output must be a JSON object")
                except httpx.HTTPStatusError as exc:
                    _meter(status="failed")
                    status_code = int(exc.response.status_code)
                    return _failed(
                        "The provider returned an HTTP error for the bounded structured-output recovery request.",
                        (
                            "provider_rejected_request"
                            if 400 <= status_code < 500
                            else "provider_http_error"
                        ),
                        failed_stage="provider_http_response",
                        first_mismatch=f"http_{status_code}",
                        providerHttp={"httpStatus": status_code},
                    )
                except (httpx.HTTPError, OSError) as exc:
                    _meter(status="failed")
                    execution_id = str(
                        getattr(exc, "openair_provider_execution_id", "") or ""
                    ).strip() or None
                    if bool(getattr(exc, "openair_submission_known_not_sent", False)):
                        return _failed(
                            "The bounded recovery request was proven not submitted to the Provider.",
                            "provider_connection_unavailable",
                            failed_stage="provider_transport_pre_submission",
                            first_mismatch=type(exc).__name__,
                            providerExecutionId=execution_id,
                        )
                    return _unknown(
                        "The provider result cannot be established after the bounded recovery request.",
                        provider_execution_id=execution_id,
                    )
                except (KeyError, IndexError, TypeError, ValueError) as exc:
                    raise ResearchOutputContractGap(STRUCTURED_OUTPUT_CORRECTION_EXHAUSTED) from exc
            if frozen_finalization is not None:
                from app.execution.skill_finalization_runtime import (
                    finalize_and_validate_registered_output,
                )

                from app.execution.skill_finalization_runtime import SkillFinalizationError
                try:
                    finalized = finalize_and_validate_registered_output(
                        repo_root=repo_root(), skill_path=str(skill["skillPath"]),
                        frozen_contract=frozen_finalization, input_payload=parameters,
                        draft_payload=parsed, final_schema=json_contract["output"]["schema"],
                    )
                except SkillFinalizationError as exc:
                    if finalizer_failure_preserves_draft(exc.code):
                        raise ResearchOutputContractGap(
                            exc.code, stage="skill_finalization_failed"
                        ) from exc
                    _meter(status="failed")
                    return _failed(
                        "The registered deterministic finalizer could not execute.",
                        _SKILL_FINALIZATION_RUNTIME_FAILED,
                        failed_stage="skill_finalization_failed",
                        first_mismatch=exc.code,
                        finalization=exc.diagnostics["finalization"],
                    )
                guarded = finalized.output
                if finalized.unfinalized:
                    artifact_warnings.append("unfinalized_scientific_content")
                    result_protocol_metadata = merge_result_protocols(
                        result_protocol_metadata,
                        {"status": "degraded", "quarantined_items": [{"path": "$", "reason": "finalization_contract_gap"}],
                         "warnings": ["finalization_contract_gap:" + str(row) for row in finalized.errors]},
                    )
                elif finalized.status == "degraded":
                    result_protocol_metadata = merge_result_protocols(
                        result_protocol_metadata,
                        {"status": "degraded", "warnings": [str(row) for row in finalized.errors]},
                    )
            else:
                try:
                    guarded, guard_diagnostics = apply_opentrajectory_runtime_guard(
                        skill_id=skill["skillId"],
                        input_payload=parameters,
                        output_payload=parsed,
                    )
                    recovery_warnings = _runtime_guard_recovery_warnings(guard_diagnostics)
                    artifact_warnings.extend(recovery_warnings)
                    guard_protocol = guard_diagnostics.get("result_protocol")
                    result_protocol_metadata = merge_result_protocols(
                        result_protocol_metadata,
                        guard_protocol if isinstance(guard_protocol, Mapping) else None,
                        warnings=recovery_warnings,
                    )
                except RuntimeGuardRejected as exc:
                    failures = exc.diagnostics.get("hard_gate_failures") or []
                    _meter(status="failed")
                    return _failed(
                        "The structured Skill output failed its runtime guard.",
                        "provider_response_contract_invalid",
                        failed_stage="runtime_guard_failed",
                        first_mismatch=str(failures[0]) if failures else "runtime_guard_rejected",
                    )
                if skill["skillId"] == "execution_readiness_assessment" and isinstance(guarded, dict):
                    from app.execution.plan_resource_relation import enforce_readiness_relation

                    guarded = enforce_readiness_relation(
                        guarded,
                        parameters.get("execution_plan_facts"), parameters.get("code_identity"),
                    )
                guarded = _validate_structured_json(
                    guarded, json_contract["output"]["schema"], "Skill output",
                    input_warnings=artifact_warnings,
                )
            if evidence_batch_failures and isinstance(guarded, dict):
                summary = guarded.get("summary")
                if isinstance(summary, dict):
                    summary["status"] = "degraded"
                    summary["degraded"] = True
                warnings = guarded.get("extraction_warnings")
                if not isinstance(warnings, list):
                    warnings = []
                    guarded["extraction_warnings"] = warnings
                for failure in evidence_batch_failures:
                    warnings.append(
                        "subtask_unavailable:"
                        f"paper_index={failure['paperIndex']}:"
                        f"candidate_id={failure.get('candidateId') or ''}"
                    )
                guarded["extraction_warnings"] = list(dict.fromkeys(
                    str(item) for item in warnings if str(item).strip()
                ))[:80]
                guarded = _validate_structured_json(
                    guarded, json_contract["output"]["schema"], "Skill output",
                    input_warnings=artifact_warnings,
                )

            if not isinstance(guarded, dict):
                _meter(status="failed")
                return _failed(
                    "The structured Skill output is not a JSON object.",
                    "provider_response_contract_invalid",
                    failed_stage="json_schema_failed",
                    first_mismatch="structured_output_json_object_required",
                )
            candidate_protocol = guarded.get("result_protocol")
            if isinstance(candidate_protocol, Mapping):
                result_protocol_metadata = merge_result_protocols(
                    result_protocol_metadata, candidate_protocol
                )
                artifact_warnings.extend(result_protocol_metadata.get("warnings") or [])
                if result_protocol_metadata.get("status") == "degraded":
                    artifact_warnings.append("partial_result_available")
            serialized = json.dumps(guarded, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            if frozen_finalization is None and len(serialized) > max_output_chars:
                artifact_warnings.append("output_contract_gap:$:max_output_chars")
            artifact = {
                "title": str(skill.get("displayName") or skill["skillId"]).strip(),
                "content": serialized,
                "warnings": artifact_warnings,
            }
        else:
            artifact = _parse_huiyan_artifact(
                body,
                skill_id=skill["skillId"],
                max_output_chars=max_output_chars,
                output_contract=output_contract,
                forbidden_values=[
                    runtime_config.apiKey,
                    *(value for value in (
                        provider_id, runtime_config.model, runtime_config.baseUrl,
                    ) if isinstance(value, str) and len(value) >= 16),
                ],
            )
            artifact_warnings.extend(list(artifact.get("warnings") or []))
    except (KeyError, IndexError, TypeError, ValueError, RuntimeError) as exc:
        if frozen_finalization is not None and not isinstance(exc, ResearchOutputContractGap):
            _meter(status="failed")
            return _failed(
                "The finalized Skill output could not be materialized as a formal Artifact.",
                _SKILL_FINALIZATION_RUNTIME_FAILED,
                failed_stage="skill_finalization_failed",
                first_mismatch="finalized_output_materialization_failed",
            )
        if isinstance(exc, ResearchOutputContractGap):
            result_protocol_metadata = exc.result_protocol
        fallback_raw = provider_content_for_preservation(body)
        artifact = materialize_semantic_output(
            fallback_raw,
            fallback_title=str(skill.get("displayName") or skill["skillId"]).strip(),
            mime_type=output_contract["mimeType"],
            max_output_chars=max_output_chars,
            secret_values=[runtime_config.apiKey],
        )
        artifact_warnings.extend(list(artifact.get("warnings") or []))
    content = artifact["content"].encode("utf-8")
    mime_type = output_contract["mimeType"]
    digest = hashlib.sha256(content).hexdigest()
    _meter(status="success")
    metadata = {"runtime": _RUNTIME_KIND_LLM_REASONING, "skillId": skill["skillId"]}
    result_protocol_metadata = merge_result_protocols(
        result_protocol_metadata, warnings=artifact_warnings
    )
    metadata["resultProtocol"] = result_protocol_metadata
    protocol_warnings = list(result_protocol_metadata.get("warnings") or [])
    if protocol_warnings:
        metadata["warnings"] = protocol_warnings[:24]
    metadata["artifactQuality"] = artifact_quality_for_protocol(
        result_protocol_metadata
    )
    return {
        "state": "succeeded",
        "summary": f"{skill['skillId']} produced material for the requesting Agent to review.",
        "relatedSkillId": skill["skillId"],
        "providerModel": runtime_config.model,
        "externalActionEvidence": [],
        "artifactPayload": {
            "contentBase64": base64.b64encode(content).decode("ascii"),
            "contentType": mime_type,
            "presentation": {
                "title": artifact["title"],
                "mimeType": mime_type,
                "metadata": metadata,
                "contentSha256": digest,
                "contentSize": len(content),
            },
        },
    }


def _read_skill_contract(skill: dict[str, Any]) -> tuple[dict[str, Any], str]:
    path = repo_root() / str(skill["skillPath"])
    metadata = read_yaml_source(path / "skill.yaml") or {}
    instructions = (path / "SKILL.md").read_text(encoding="utf-8").strip()
    if not isinstance(metadata, dict) or not instructions or _normal(str(metadata.get("skill_id") or "")) != _normal(skill["skillId"]):
        raise ValueError("invalid Skill contract")
    return metadata, instructions


def _durable_output_contract(
    metadata: dict[str, Any],
    *,
    skill: dict[str, Any] | None = None,
) -> dict[str, str]:
    durable = metadata.get("durable_execution")
    output_file = metadata.get("output_file")
    spec = metadata.get("spec")
    explicit_contract = (
        metadata.get("runtime") != "claude_code"
        or metadata.get("status") != "ready"
        or metadata.get("auto_allowed") is not True
        or not isinstance(durable, dict)
        or set(durable) != {"adapter"}
        or durable.get("adapter") != _HUIYAN_DURABLE_ADAPTER
        or not isinstance(output_file, str)
        or not output_file.strip()
        or not isinstance(spec, str)
        or spec.strip() != output_file.strip()
    ) is False
    if not explicit_contract:
        runtime_contract = _runtime_contract(skill) if skill is not None else None
        if runtime_contract is None or runtime_contract["kind"] != _RUNTIME_KIND_LLM_REASONING:
            raise ValueError("Skill is not declared for durable reasoning execution")
        return {
            "artifact": f"skills/{skill['skillId']}/result.md",
            "mimeType": "text/markdown",
            "template": _GENERATED_DURABLE_TEMPLATE,
            "contractSource": "generated_registered_skill",
        }
    path = PurePosixPath(output_file.strip())
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError("Skill output path is unsafe")
    suffix = path.suffix.lower()
    mime_type = {".md": "text/markdown", ".yaml": "text/yaml", ".yml": "text/yaml"}.get(suffix)
    if mime_type is None:
        raise ValueError("Skill output format is unsupported")
    template_path = repo_root() / "schemas" / "sdr" / "specs" / Path(*path.parts)
    template = template_path.read_text(encoding="utf-8").strip()
    if not template:
        raise ValueError("Skill output template is unavailable")
    return {
        "artifact": path.as_posix(),
        "mimeType": mime_type,
        "template": template,
        "contractSource": "declared_skill",
    }


def _parse_huiyan_artifact(
    body: Any,
    *,
    skill_id: str,
    max_output_chars: int,
    output_contract: dict[str, str] | None = None,
    forbidden_values: list[str],
) -> dict[str, Any]:
    from app.execution.skill_contract_policy import materialize_semantic_output

    if not isinstance(body, dict):
        raise ValueError("provider body is not an object")
    try:
        raw = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("provider content is invalid") from exc
    contract = output_contract or {
        "artifact": "research-config.yaml",
        "mimeType": "text/yaml",
        "template": "",
    }
    fallback_title = str(skill_id or contract.get("artifact") or "result").replace("_", " ").strip() or "result"
    if contract.get("mimeType") == "text/yaml":
        fallback_title = "Research configuration"
    return materialize_semantic_output(
        raw,
        fallback_title=fallback_title,
        mime_type=str(contract.get("mimeType") or "text/plain"),
        max_output_chars=max_output_chars,
        secret_values=forbidden_values,
    )


def _validate_markdown_artifact(content: str, template: str) -> str:
    """Sanitize Markdown presentation. Heading sequence is not an acceptance gate."""
    del template
    from app.execution.skill_contract_policy import strip_semantic_wrappers

    return strip_semantic_wrappers(content)


def _markdown_heading_key(value: str) -> str:
    heading = re.sub(r"^#{1,6}\s+", "", value.strip()).casefold()
    return " ".join(re.findall(r"[\w]+", heading, re.UNICODE))


def _valid_source_identifier(value: str) -> bool:
    normalized = value.strip()
    if _is_placeholder_source_identifier(normalized):
        return False
    return bool(re.search(
        r"(?:https?://|\bdoi\s*:|10\.\d{4,9}/|\barxiv\s*:|\bpm(?:id|cid)\s*:|\bopenalex\s*:|semantic scholar)",
        normalized,
        re.I,
    ))


def _contains_internal_output(
    content: str, title: str, body: dict[str, Any], skill_id: str, forbidden_values: list[str],
) -> bool:
    text = f"{title}\n{content}".lower()
    forbidden_labels = re.compile(r"\b(?:api[_ -]?key|authorization|bearer|access[_ -]?token|session[_ -]?id|run[_ -]?id|task[_ -]?id|trace[_ -]?id|workspace(?:[_ -]?path)?)\b", re.I)
    internal_field_assignment = re.compile(
        r"\b(?:provider(?:[_ -]?id)?|model(?:[_ -]?id)?|runtime|secret|"
        r"worker(?:[_ -]?id)?|call(?:[_ -]?id)?|owner(?:[_ -]?id)?)\s*[:=]",
        re.I,
    )
    if forbidden_labels.search(text) or internal_field_assignment.search(text):
        return True
    # Provider response identifiers and the selected runtime identifiers are
    # not user-visible research evidence.
    values = [str(body.get("id") or ""), skill_id, *forbidden_values]
    return any(value and value.lower() in text for value in values)


def _valid_research_config(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    if set(value) != _RESEARCH_CONFIG_KEYS or value.get("schema") != "sdr-driven":
        return False
    if value.get("research_type") not in {"M", "D", "C", "H"} or value.get("ai_mode") not in {"copilot", "autopilot"}:
        return False
    target_venue = value.get("target_venue")
    resources = value.get("resources")
    criteria = value.get("minimum_success_criteria")
    modification_log = value.get("modification_log")
    return (
        isinstance(value.get("project"), str) and bool(value["project"].strip())
        and isinstance(value.get("domain"), str) and bool(value["domain"].strip())
        and isinstance(value.get("subdomain"), str)
        and isinstance(target_venue, dict) and set(target_venue) == _TARGET_VENUE_KEYS
        and all(isinstance(target_venue[key], str) for key in _TARGET_VENUE_KEYS)
        and isinstance(resources, dict) and set(resources) == _RESOURCE_KEYS
        and all(isinstance(resources[key], str) for key in _RESOURCE_KEYS)
        and isinstance(value.get("checkpoint_stages"), list)
        and all(isinstance(item, str) for item in value["checkpoint_stages"])
        and isinstance(criteria, dict)
        and set(criteria) == _SUCCESS_CRITERIA_KEYS
        and _is_nonnegative_int(criteria["min_baselines"])
        and _is_nonnegative_int(criteria["min_ablation_runs"])
        and isinstance(criteria["require_proposed"], bool)
        and isinstance(criteria["required_metrics"], list)
        and all(isinstance(item, str) for item in criteria["required_metrics"])
        and isinstance(modification_log, list)
        and all(
            isinstance(entry, dict)
            and set(entry) == _MODIFICATION_LOG_KEYS
            and all(isinstance(entry[key], str) for key in _MODIFICATION_LOG_KEYS)
            for entry in modification_log
        )
    )


def _contains_internal_execution_key(value: Any) -> bool:
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized = re.sub(r"[- ]+", "_", re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", str(key).strip())).lower()
            if normalized in _INTERNAL_EXECUTION_KEYS or _contains_internal_execution_key(nested):
                return True
    elif isinstance(value, list):
        return any(_contains_internal_execution_key(item) for item in value)
    return False


def _token_budget_for_character_limit(max_output_chars: int) -> int:
    if isinstance(max_output_chars, bool) or not isinstance(max_output_chars, int) or max_output_chars < 1:
        raise ValueError("invalid provider output character limit")
    return max(1, min(8192, max_output_chars // 4))


def _is_nonnegative_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _provider_attempt_evidence_failed(exc: BaseException | None = None) -> dict[str, Any]:
    code = str(exc or "provider_attempt_evidence_unavailable").strip()
    if re.fullmatch(r"[A-Za-z0-9._:/;-]{1,200}", code) is None:
        code = "provider_attempt_evidence_unavailable"
    return _failed(
        "The Provider physical-attempt evidence could not be prepared.",
        "provider_attempt_evidence",
        failed_stage="provider_attempt_evidence",
        first_mismatch=code,
    )


def _failed(
    summary: str,
    classification: str,
    *,
    failed_stage: str | None = None,
    first_mismatch: str | None = None,
    **diagnostics: Any,
) -> dict[str, Any]:
    result: dict[str, Any] = {"state": "failed", "summary": summary, "failureClassification": classification}
    if failed_stage is not None:
        result["failedStage"] = failed_stage
    if first_mismatch is not None:
        result["firstMismatch"] = first_mismatch
    if diagnostics:
        result["diagnostics"] = diagnostics
    return result


def _unknown(
    summary: str,
    *,
    provider_execution_id: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "state": "unknown",
        "summary": summary,
        "externalResultUnknown": True,
    }
    execution_id = str(provider_execution_id or "").strip()
    if execution_id:
        result["providerExecutionId"] = execution_id
    return result


def _is_python_adapter_skill(skill_id: str) -> bool:
    # Dispatch remains registry-driven: no caller can reach the adapter without
    # a registry record and the frozen work-unit allowlist. Adapter support
    # itself is shared with Skill Lab and Workbench execute_skill.
    return resolve_python_adapter_skill(skill_id) is not None


def _has_durable_runtime_contract(skill: dict[str, Any]) -> bool:
    runtime_contract = _runtime_contract(skill)
    if runtime_contract is None:
        return False
    try:
        metadata, _instructions = _read_skill_contract(skill)
        if runtime_contract["kind"] == _RUNTIME_KIND_LLM_REASONING:
            if _registered_durable_model_profile(skill) is None:
                return False
            structured_contract = _structured_json_contract(skill)
            frozen_finalization = _frozen_finalization_contract(skill)
            json_contract = (
                _frozen_schema_contract(skill)
                if frozen_finalization is not None
                else structured_contract
            )
            if frozen_finalization is not None and json_contract is None:
                return False
            if json_contract is not None:
                _validate_structured_json({}, {"type": "object"}, "structured runtime dependency")
                _structured_output_contract(skill, json_contract)
                return True
            _durable_output_contract(metadata, skill=skill)
            return True
        if runtime_contract["kind"] in {
            _RUNTIME_KIND_REASONING_ADAPTER,
            _RUNTIME_KIND_DETERMINISTIC,
            _RUNTIME_KIND_EXTERNAL_ADAPTER,
        }:
            return (
                _is_python_adapter_skill(skill["skillId"])
                and runtime_contract["adapter_id"] == _python_adapter_id(skill["skillId"])
                and (
                    runtime_contract["kind"] != _RUNTIME_KIND_EXTERNAL_ADAPTER
                    or runtime_contract["adapter_id"] in _EXTERNAL_ADAPTER_EVIDENCE_VALIDATORS
                )
            )
        if runtime_contract["kind"] == _RUNTIME_KIND_SKILL_SCRIPT:
            return skill["skillId"] in {
                "materials_mp_constraint_search",
                "crystal_design_structure_crossover_agent",
                "vasp_stability_durable",
                "analysis_report_durable",
                "flux_experiment",
            }
    except (OSError, yaml.YAMLError, ValueError):
        return False
    return False


def _has_pvd_literature_closure_evidence(output: dict[str, Any]) -> bool:
    report = output.get("retrieval_report")
    if not isinstance(report, dict):
        return False
    if not _has_retrieval_report_evidence({"retrieval_report": report, "candidates": []}):
        return False
    return isinstance(output.get("coverage"), dict) and isinstance(output.get("reading_evidence"), dict)


def _matches_external_adapter_formal_schema(adapter_id: str, output: Any) -> bool:
    schema_parts = _FORMAL_SCHEMA_GATED_EXTERNAL_ADAPTERS.get(adapter_id)
    if schema_parts is None or not isinstance(output, dict):
        return False
    try:
        from jsonschema.validators import validator_for
        from jsonschema.exceptions import SchemaError

        schema_path = repo_root() / "skills"
        schema = json.loads(
            schema_path.joinpath(*schema_parts).read_text(encoding="utf-8")
        )
        if any(error.validator == "required" for error in validator_for(schema)(schema).iter_errors(output)):
            return False
    except (
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        SchemaError,
    ):
        return False
    return True


def _has_external_adapter_output_evidence(adapter_id: str, output: Any) -> bool:
    if adapter_id not in _EXTERNAL_ADAPTER_EVIDENCE_VALIDATORS or not isinstance(output, dict):
        return False
    if adapter_id == "python_adapter:crystal_design_structure_crossover_py":
        if _has_materials_structure_no_seed_preflight_evidence(output):
            return True
        return _has_mcp_transport_evidence(output)

    if adapter_id in {
        "python_adapter:materials_magnet_design_mvp",
        "python_adapter:crystal_design_formula_crossover",
        "python_adapter:crystal_design_formula_crossover_py",
    }:
        return _has_mcp_transport_evidence(output)
    if adapter_id == "python_adapter:materials_mp_constraint_search":
        return _has_materials_project_query_evidence(output)
    if adapter_id in {
        "python_adapter:academic_source_retrieval_skill",
        "python_adapter:opentrajectory_source_acquisition",
    }:
        return _has_retrieval_report_evidence(output)
    if adapter_id == "python_adapter:source_access_readiness_resolver":
        return _has_source_access_probe_evidence(output)
    if adapter_id == "python_adapter:deepxiv_progressive_reading":
        return _has_progressive_reading_evidence(output)
    if adapter_id == "python_adapter:pvd_literature_evidence_acquisition_closure":
        return _has_pvd_literature_closure_evidence(output)
    if adapter_id == "python_adapter:environment_setup":
        return _has_environment_setup_evidence(output)
    if adapter_id == "python_adapter:exploratory_run":
        return _has_exploratory_run_evidence(output)
    if adapter_id == "python_adapter:repository_revision_acquisition":
        return _has_repository_revision_acquisition_evidence(output)
    if adapter_id == "python_adapter:cs_paper_source_resolution":
        return _has_cs_paper_source_resolution_evidence(output)
    if adapter_id == "python_adapter:resource_identity_acquisition":
        return _has_resource_identity_acquisition_evidence(output)
    return False


def _resource_identity_failure_details(output: dict[str, Any]) -> dict[str, Any] | None:
    allowed_stages = {
        "input_validation", "workspace", "provider_preflight", "resolve_revision",
        "verify_identity", "resolve_manifest", "staging", "acquire", "materialization",
        "publish", "cleanup", "batch_acquire",
    }
    code = output.get("failure_code")
    stage = output.get("failed_stage")
    if (
        output.get("state") != "failed"
        or not isinstance(code, str) or not code.strip()
        or stage not in allowed_stages
    ):
        return None
    return {"failure_code": code, "failed_stage": stage}


def _has_single_resource_identity_evidence(output: dict[str, Any]) -> bool:
    resolved = str(output.get("resolved_commit_sha") or "").strip().lower()
    actual = output.get("actual_identity")
    execution = output.get("execution_evidence")
    relative = str(output.get("workspace_relative_path") or "")
    if (
        output.get("state") != "succeeded"
        or not re.fullmatch(r"[0-9a-f]{40}", resolved)
        or not relative.startswith("resource/")
        or not isinstance(actual, dict)
        or str(actual.get("commit_sha") or "").strip().lower() != resolved
        or not isinstance(execution, dict)
        or not str(execution.get("provider_client") or "").strip()
        or execution.get("materialized") is not True
    ):
        return False
    expected_operation = (
        "snapshot_download"
        if output.get("resource_kind") == "huggingface_snapshot"
        else "hf_hub_download"
    )
    return execution.get("operation") == expected_operation


def _has_resource_identity_acquisition_evidence(output: dict[str, Any]) -> bool:
    resources = output.get("resources")
    if resources is None:
        return _has_single_resource_identity_evidence(output)
    if not isinstance(resources, list) or not resources:
        return False

    succeeded = 0
    failed = 0
    for row in resources:
        result = row.get("result") if isinstance(row, dict) else None
        if not isinstance(result, dict):
            return False
        if result.get("state") == "succeeded":
            if not _has_single_resource_identity_evidence(result):
                return False
            succeeded += 1
        elif result.get("state") == "failed":
            if _resource_identity_failure_details(result) is None:
                return False
            failed += 1
        else:
            return False

    summary = output.get("summary")
    if not isinstance(summary, dict) or summary != {
        "total": len(resources),
        "succeeded": succeeded,
        "failed": failed,
    }:
        return False
    if succeeded == 0:
        return False
    expected_state = "succeeded" if failed == 0 else "partial_failure"
    return output.get("state") == expected_state


def _repository_revision_failure_details(output: dict[str, Any]) -> dict[str, Any] | None:
    allowed = {
        ("skill_input_invalid", "input_validation"),
        ("workspace_boundary_invalid", "workspace"),
        ("repository_target_conflict", "workspace"),
        ("git_unavailable", "git_preflight"),
        ("repository_unavailable", "clone"),
        ("workspace_boundary_invalid", "post_clone_workspace_check"),
        ("revision_unresolvable", "resolve_revision"),
        ("checkout_failed", "checkout"),
        ("head_read_failed", "read_head"),
        ("head_verification_mismatch", "verify_head"),
        ("repository_cleanup_failed", "cleanup"),
    }
    code = output.get("failure_code")
    stage = output.get("failed_stage")
    if output.get("state") != "failed" or (code, stage) not in allowed:
        return None
    result: dict[str, Any] = {"failure_code": code, "failed_stage": stage}
    exit_code = output.get("git_exit_code")
    if exit_code is not None:
        if not isinstance(exit_code, int) or isinstance(exit_code, bool):
            return None
        result["git_exit_code"] = exit_code
    return result


def _cs_paper_source_resolution_failure_details(output: dict[str, Any]) -> dict[str, str] | None:
    """Preserve a source adapter's evidenced failure instead of relabeling it.

    A failed external read is not missing evidence when the adapter records the
    attempted operation.  The adapter schema is the narrow authority for which
    source-resolution failures can cross this runtime boundary.
    """
    allowed = {
        ("skill_input_invalid", "input_validation"),
        ("source_provider_unavailable", "source_lookup"),
        ("source_provider_unavailable", "source_read"),
        ("git_unavailable", "repository_probe"),
        ("repository_unavailable", "repository_probe"),
    }
    code = output.get("failure_code")
    stage = output.get("failed_stage")
    if output.get("state") != "failed" or (code, stage) not in allowed:
        return None
    return {"failure_code": code, "failed_stage": stage}


def _has_repository_revision_acquisition_evidence(output: dict[str, Any]) -> bool:
    resolved = str(output.get("resolved_commit_sha") or "").strip().lower()
    verified = str(output.get("verified_head") or "").strip().lower()
    execution = output.get("execution_evidence")
    if (
        output.get("state") != "succeeded"
        or output.get("workspace_relative_path") != "repository"
        or not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", resolved)
        or verified != resolved
        or not isinstance(execution, dict)
        or not str(execution.get("git_version") or "").strip()
        or execution.get("head_matches_resolved_commit") is not True
    ):
        return False
    operations = execution.get("operations")
    if not isinstance(operations, list) or len(operations) != 4:
        return False
    expected = (["clone", "resolve_revision", "checkout", "read_head"],
                ["verify_origin", "resolve_revision", "verify_clean", "read_head"])
    return [row.get("operation") for row in operations if isinstance(row, dict)] in expected and all(
        isinstance(row, dict) and row.get("exit_code") == 0 for row in operations
    )


def _has_cs_paper_source_resolution_evidence(output: dict[str, Any]) -> bool:
    evidence = output.get("execution_evidence")
    if not isinstance(evidence, dict) or evidence.get("network_action_performed") is not True:
        return False
    operations = evidence.get("operations")
    return isinstance(operations, list) and bool(operations)


def _has_environment_setup_evidence(output: dict[str, Any]) -> bool:
    if (
        output.get("schema_version") != "openair-environment-record/v1"
        or output.get("state") != "succeeded"
        or output.get("workspace_relative_path") != "environment"
        or output.get("environment_record_relative_path")
        != "environment/environment-record.json"
    ):
        return False
    identity = output.get("environment_identity")
    verification = output.get("verification")
    execution = output.get("execution_evidence")
    if not isinstance(identity, dict) or not isinstance(verification, dict) or not isinstance(execution, dict):
        return False
    if not re.fullmatch(
        r"[0-9a-f]{64}",
        str(identity.get("fingerprint_sha256") or "").strip().lower(),
    ):
        return False
    imports = verification.get("imports")
    if (
        verification.get("python_version_match") is not True
        or verification.get("pip_check") != "passed"
        or not isinstance(imports, list)
        or not imports
        or any(
            not isinstance(row, dict)
            or not str(row.get("module") or "").strip()
            or row.get("status") != "passed"
            for row in imports
        )
    ):
        return False
    readiness = verification.get("runtime_readiness")
    if not isinstance(readiness, dict):
        return False
    if readiness.get("configured") is True:
        if readiness.get("status") != "passed" or readiness.get("exit_code") != 0:
            return False
        readiness_required = True
    elif readiness == {"configured": False, "status": "not_configured"}:
        readiness_required = False
    else:
        return False
    operations = execution.get("operations")
    if not isinstance(operations, list):
        return False
    by_name = {
        row.get("operation"): row
        for row in operations
        if isinstance(row, dict) and isinstance(row.get("operation"), str)
    }
    required = {
        "create_environment",
        "install_requirements",
        "pip_check",
        "verify_imports",
        "read_environment_identity",
    }
    if readiness_required:
        required.add("runtime_readiness_probe")
    if set(by_name) != required or any(row.get("exit_code") != 0 for row in by_name.values()):
        return False
    install_timeout = by_name["install_requirements"].get("timeout_seconds")
    if not isinstance(install_timeout, int) or isinstance(install_timeout, bool) or install_timeout < 1:
        return False
    builder = execution.get("builder")
    return bool(
        isinstance(builder, dict)
        and builder.get("kind") in {"uv", "venv"}
        and str(builder.get("version") or "").strip()
        and str(execution.get("pip_version") or "").strip()
    )


def _has_exploratory_run_evidence(output: dict[str, Any]) -> bool:
    """Accept only records proving a real controlled process attempt."""
    state = output.get("state")
    status = output.get("execution_status")
    timed_out = output.get("timed_out")
    exit_code = output.get("exit_code")
    process_id = output.get("process_id")
    failure_code = output.get("failure_code")

    if state == "succeeded":
        if not (
            status == "succeeded"
            and timed_out is False
            and exit_code == 0
            and isinstance(process_id, int)
            and not isinstance(process_id, bool)
            and process_id > 0
            and failure_code is None
        ):
            return False
    elif state == "failed":
        if status == "process_start_failed":
            if not (
                timed_out is False
                and exit_code is None
                and process_id is None
                and failure_code == "process_start_failed"
                and isinstance(output.get("process_start_error"), dict)
            ):
                return False
        elif status == "non_zero_exit":
            if not (
                timed_out is False
                and isinstance(exit_code, int)
                and not isinstance(exit_code, bool)
                and exit_code != 0
                and isinstance(process_id, int)
                and not isinstance(process_id, bool)
                and process_id > 0
                and failure_code == "non_zero_exit"
            ):
                return False
        elif status == "timeout":
            if not (
                timed_out is True
                and isinstance(process_id, int)
                and not isinstance(process_id, bool)
                and process_id > 0
                and failure_code == "execution_timeout"
            ):
                return False
        elif status == "termination_failed":
            if not (
                timed_out is True
                and isinstance(process_id, int)
                and not isinstance(process_id, bool)
                and process_id > 0
                and failure_code == "process_termination_failed"
                and isinstance(output.get("termination_evidence"), dict)
            ):
                return False
        else:
            return False
    else:
        return False

    for stream_name in ("stdout", "stderr"):
        capture = output.get(stream_name)
        if not isinstance(capture, dict):
            return False
        total = capture.get("total_bytes")
        captured = capture.get("captured_bytes")
        if not (
            isinstance(total, int)
            and not isinstance(total, bool)
            and isinstance(captured, int)
            and not isinstance(captured, bool)
            and 0 <= captured <= min(total, 65536)
            and capture.get("truncated") is (total > captured)
        ):
            return False

    for row in output.get("resource_usage") or []:
        if not isinstance(row, dict):
            return False
        verification = row.get("verification")
        planned = row.get("planned_resource")
        actual = row.get("actual_usage")
        if verification == "verified":
            if not isinstance(planned, dict) or not isinstance(actual, dict):
                return False
            if actual.get("workspace_relative_path") != planned.get("workspace_relative_path"):
                return False
            if actual.get("immutable_identity") != planned.get("actual_identity"):
                return False
        elif verification == "unverified":
            if actual is not None or row.get("consumption_evidence") is not None:
                return False
        elif verification != "mismatch":
            return False

    return True


def _has_source_access_probe_evidence(output: dict[str, Any]) -> bool:
    summary = output.get("summary")
    candidates = output.get("candidates")
    if not isinstance(summary, dict) or not isinstance(candidates, list):
        return False
    attempts = summary.get("probe_attempts")
    if summary.get("network_probe_enabled") is not True or not isinstance(attempts, int) or attempts < 1:
        return False
    observed = 0
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        for route in candidate.get("access_routes") or []:
            if not isinstance(route, dict):
                continue
            if route.get("probe_status") in {"verified", "unavailable", "probe_failed"}:
                observed += 1
    return observed >= 1


def _has_materials_structure_no_seed_preflight_evidence(
    output: dict[str, Any],
) -> bool:
    """Accept only the exact auditable no-seed/non-execution result."""
    if str(output.get("state") or "").strip() != "partial_failure":
        return False

    summary = output.get("run_summary")
    if not isinstance(summary, dict):
        return False

    return (
        summary.get("execution") == "skipped"
        and summary.get("reason") == "no_usable_seed_cifs"
        and summary.get("seed_count") == 0
        and summary.get("scientific_execution_started") is False
        and output.get("iterations") == []
        and output.get("pareto_cif_paths") == []
    )


def _has_mcp_transport_evidence(output: dict[str, Any]) -> bool:
    state = str(output.get("state") or "").strip()
    if state not in {"succeeded", "partial_failure"}:
        return False

    def walk(value: Any):
        if isinstance(value, dict):
            yield value
            for child in value.values():
                yield from walk(child)
        elif isinstance(value, list):
            for child in value:
                yield from walk(child)

    for row in walk(output):
        request_hash = str(row.get("request_sha256") or "").strip().lower()
        transport_outcome = str(row.get("transport_outcome") or "").strip()
        if (
            str(row.get("mcp_name") or "").strip()
            and str(row.get("tool") or "").strip()
            and re.fullmatch(r"[0-9a-f]{64}", request_hash)
            and transport_outcome in {"ok", "timeout", "http_error", "mcp_error"}
        ):
            return True
    return False


def _has_materials_project_query_evidence(output: dict[str, Any]) -> bool:
    state = str(output.get("state") or "").strip()
    evidence = output.get("external_action_evidence")
    if state not in {"succeeded", "partial_failure"} or not isinstance(evidence, list):
        return False
    return any(
        isinstance(row, dict)
        and row.get("service") == "materials_project"
        and row.get("operation") == "materials.summary.search"
        and re.fullmatch(r"[0-9a-f]{64}", str(row.get("request_sha256") or "").strip().lower())
        and row.get("transport_outcome") in {"ok", "error"}
        for row in evidence
    )


def _retrieval_execution_outcome(output: Any) -> str:
    """Classify observable retrieval execution without inventing transport facts.

    ``query_counts`` is populated only after at least one source request returned
    successfully, even when it returned zero candidates. ``failed_sources`` proves
    attempted source I/O that failed. Empty query_counts + no failed_sources means
    the adapter never established that an external query was attempted.
    """
    if not isinstance(output, dict):
        return "invalid"
    report = output.get("retrieval_report")
    candidates = output.get("metadata_candidates")
    if not isinstance(candidates, list):
        candidates = output.get("candidates")
    if not isinstance(report, dict) or not isinstance(candidates, list):
        return "invalid"
    source_counts = report.get("source_counts")
    query_counts = report.get("query_counts")
    failed_sources = report.get("failed_sources")
    if not isinstance(source_counts, dict) or not source_counts:
        return "invalid"
    attempted_success = isinstance(query_counts, dict) and bool(query_counts)
    attempted_failure = isinstance(failed_sources, list) and bool(failed_sources)
    if not attempted_success and not attempted_failure:
        return "not_attempted"
    if attempted_success and attempted_failure:
        return "partial_success"
    if attempted_failure:
        return "source_failure"
    if candidates:
        return "success"
    return "attempted_zero_result"


def _has_retrieval_report_evidence(output: dict[str, Any]) -> bool:
    return _retrieval_execution_outcome(output) in {
        "attempted_zero_result",
        "source_failure",
        "partial_success",
        "success",
    }


def _retrieval_failure_first_mismatch(output: Any) -> str:
    """Return bounded, body-free retrieval facts for an evidence-gate rejection.

    The retrieval adapter already emits its report and candidate provenance.  This
    deliberately serializes only those existing aggregate facts so the dispatch
    failure record can identify the first observable boundary on the next run.
    """
    if not isinstance(output, dict):
        return "retrieval-report-output-invalid"
    report = output.get("retrieval_report")
    candidates = output.get("metadata_candidates")
    if not isinstance(candidates, list):
        candidates = output.get("candidates")
    if not isinstance(report, dict):
        return "retrieval-report-missing"
    source_counts = report.get("source_counts")
    failed_sources = report.get("failed_sources")
    failed_names = {
        str(row.get("source_name") or "").strip()
        for row in failed_sources if isinstance(failed_sources, list) and isinstance(row, dict)
    }
    source_facts: list[str] = []
    if isinstance(source_counts, dict):
        for source, count in source_counts.items():
            source_id = str(source or "").strip().lower()
            if not re.fullmatch(r"[a-z0-9_]{1,32}", source_id):
                continue
            normalized = count if _is_nonnegative_int(count) else "unknown"
            status = "failed" if source_id in failed_names else "ok"
            source_facts.append(f"{source_id}-{status}-{normalized}")
    provenance_count = sum(
        1 for candidate in candidates if isinstance(candidate, dict)
        and (candidate.get("provenance") or candidate.get("source_name"))
    ) if isinstance(candidates, list) else 0
    final_count = report.get("total_candidates")
    final = final_count if _is_nonnegative_int(final_count) else "unknown"
    failed_count = len(failed_sources) if isinstance(failed_sources, list) else 0
    query_counts = report.get("query_counts")
    query_count = len(query_counts) if isinstance(query_counts, dict) else 0
    outcome = _retrieval_execution_outcome(output).replace("_", "-")
    facts = (
        f"retrieval-report-dict.sources-{'_'.join(source_facts) or 'none'}"
        f".queries-{query_count}.final-{final}.provenance-{provenance_count}"
        f".failed-{failed_count}.{outcome}"
    )
    # ``firstMismatch`` is persisted by the existing dispatch contract and is
    # deliberately restricted to 160 ASCII safe identifier characters.
    return facts[:160]


def _has_progressive_reading_evidence(output: dict[str, Any]) -> bool:
    return (
        isinstance(output.get("paper_packets"), list)
        and isinstance(output.get("summary"), dict)
        and isinstance(output.get("audit"), dict)
    )


def _external_adapter_evidence_outcome(adapter_id: str, output: Any) -> str:
    if not isinstance(output, dict):
        return "completed"
    state = str(output.get("state") or "").strip()
    if state == "partial_failure":
        return "partial"
    if adapter_id in {
        "python_adapter:academic_source_retrieval_skill",
        "python_adapter:opentrajectory_source_acquisition",
    }:
        report = output.get("retrieval_report")
        if isinstance(report, dict) and report.get("partial_success"):
            return "partial"
    if adapter_id == "python_adapter:deepxiv_progressive_reading":
        summary = output.get("summary")
        if isinstance(summary, dict) and (summary.get("partial_success") or summary.get("degraded")):
            return "partial"
    if adapter_id == "python_adapter:exploratory_run" and state == "failed":
        return "partial"
    if adapter_id == "python_adapter:cs_paper_source_resolution" and state == "incomplete":
        return "partial"
    return "completed"


def durable_skill_runtime_statistics() -> dict[str, int]:
    counts = {
        "TOTAL_SKILLS": 0,
        "LLM_REASONING_SKILLS": 0,
        "DETERMINISTIC_SKILLS": 0,
        "EXTERNAL_ADAPTER_SKILLS": 0,
        "SKILL_SCRIPT_SKILLS": 0,
        "FAIL_CLOSED_SKILLS": 0,
        "UNKNOWN_SKILLS": 0,
    }
    for skill in list_registered_durable_skills():
        counts["TOTAL_SKILLS"] += 1
        raw = skill.get("sourceContract", {}).get("runtime_contract")
        contract = _runtime_contract(skill)
        if contract is None:
            key = "FAIL_CLOSED_SKILLS" if raw is None else "UNKNOWN_SKILLS"
            counts[key] += 1
        elif contract["kind"] in {
            _RUNTIME_KIND_LLM_REASONING,
            _RUNTIME_KIND_REASONING_ADAPTER,
        }:
            counts["LLM_REASONING_SKILLS"] += 1
        elif contract["kind"] == _RUNTIME_KIND_DETERMINISTIC:
            counts["DETERMINISTIC_SKILLS"] += 1
        elif contract["kind"] == _RUNTIME_KIND_EXTERNAL_ADAPTER:
            counts["EXTERNAL_ADAPTER_SKILLS"] += 1
        elif contract["kind"] == _RUNTIME_KIND_SKILL_SCRIPT:
            counts["SKILL_SCRIPT_SKILLS"] += 1
    return counts


def normalize_external_action_evidence(value: Any) -> tuple[dict[str, str], ...]:
    """Validate structured evidence emitted by a real external-action adapter."""
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)) or len(value) > 100:
        raise ValueError("external action evidence is invalid")
    normalized: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict) or set(item) != {"adapter", "outcome", "evidenceRef", "contentSha256"}:
            raise ValueError("external action evidence is invalid")
        adapter = str(item["adapter"] or "").strip()
        outcome = str(item["outcome"] or "").strip()
        evidence_ref = str(item["evidenceRef"] or "").strip()
        content_hash = str(item["contentSha256"] or "").strip().lower()
        if (
            not re.fullmatch(r"[a-z0-9][a-z0-9_.:-]{0,239}", adapter)
            or outcome not in _EXTERNAL_EVIDENCE_OUTCOMES
            or not re.fullmatch(r"skill-result:[a-z0-9_:-]+:sha256:[0-9a-f]{64}", evidence_ref)
            or not re.fullmatch(r"[0-9a-f]{64}", content_hash)
        ):
            raise ValueError("external action evidence is invalid")
        normalized.append({
            "adapter": adapter,
            "outcome": outcome,
            "evidenceRef": evidence_ref,
            "contentSha256": content_hash,
        })
    return tuple(normalized)


def _runtime_contract(skill: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(skill, dict):
        return None
    source = skill.get("sourceContract")
    raw = source.get("runtime_contract") if isinstance(source, dict) else None
    contract_keys = set(raw) if isinstance(raw, dict) else None
    if (
        not contract_keys
        or not _RUNTIME_CONTRACT_KEYS <= contract_keys
        or contract_keys - _RUNTIME_CONTRACT_KEYS - _RUNTIME_CONTRACT_OPTIONAL_KEYS
    ):
        return None
    if "internal_skill_calls" in raw:
        if raw.get("kind") != _RUNTIME_KIND_REASONING_ADAPTER:
            return None
        from app.execution.research_internal_skill_call import InternalSkillCallError, _declaration

        try:
            _declaration({"sourceContract": source})
        except InternalSkillCallError:
            return None
    kind = str(raw.get("kind") or "").strip()
    requires_external = raw.get("requires_external_action")
    adapter_id = str(raw.get("adapter_id") or "").strip()
    evidence_version = str(raw.get("evidence_contract_version") or "").strip()
    if not isinstance(requires_external, bool) or not adapter_id or not evidence_version:
        return None
    if kind == _RUNTIME_KIND_LLM_REASONING:
        if (
            requires_external
            or adapter_id != _HUIYAN_DURABLE_ADAPTER
            or evidence_version != _NO_EXTERNAL_EVIDENCE_CONTRACT
        ):
            return None
    elif kind == _RUNTIME_KIND_REASONING_ADAPTER:
        if (
            requires_external
            or not adapter_id.startswith("python_adapter:")
            or evidence_version != _NO_EXTERNAL_EVIDENCE_CONTRACT
        ):
            return None
    elif kind == _RUNTIME_KIND_DETERMINISTIC:
        if (
            requires_external
            or not adapter_id.startswith("python_adapter:")
            or evidence_version != _DETERMINISTIC_EVIDENCE_CONTRACT_V1
        ):
            return None
    elif kind == _RUNTIME_KIND_EXTERNAL_ADAPTER:
        if (
            not requires_external
            or not adapter_id.startswith("python_adapter:")
            or evidence_version != _EXTERNAL_EVIDENCE_CONTRACT_V1
        ):
            return None
    elif kind == _RUNTIME_KIND_SKILL_SCRIPT:
        if (
            not requires_external
            or not adapter_id.startswith("skill_runtime:")
            or evidence_version != _SKILL_EXECUTION_EVIDENCE_CONTRACT_V1
        ):
            return None
    else:
        return None
    return {
        "kind": kind,
        "requires_external_action": requires_external,
        "adapter_id": adapter_id,
        "evidence_contract_version": evidence_version,
    }


def _python_adapter_id(skill_id: str) -> str:
    return f"python_adapter:{_normal(skill_id)}"








_PLACEHOLDER_SOURCE_IDENTIFIER_RE = re.compile(
    r"(?<![\w])(?:"
    r"待补\s*(?:doi|url|链接|来源|id)|"
    r"未知\s*(?:doi|url|链接|来源|id)|"
    r"示例\s*(?:doi|url|链接|来源|id)|"
    r"占位\s*(?:doi|url|链接|来源|id)|"
    r"(?:placeholder|example|unknown|tbd|n/?a|todo)\s*(?:doi|url|link|source|id)"
    r")(?![\w])",
    re.I,
)


def _is_placeholder_source_identifier(value: str) -> bool:
    normalized = re.sub(r"\s+", " ", value.strip())
    return bool(_PLACEHOLDER_SOURCE_IDENTIFIER_RE.search(normalized))


def _normal(value: str) -> str:
    return value.strip().replace("-", "_").lower()
