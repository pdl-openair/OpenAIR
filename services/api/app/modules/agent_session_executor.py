"""Run one plaza AgentSession turn via OpenAIResponsesProvider (no ResearchDesign)."""

from __future__ import annotations

import json
import logging
from typing import Any, Mapping
from uuid import uuid4

from app.execution.durable_orchestrator_runtime import (
    FrozenExecutionRequestError,
    FrozenOrchestrationExecutionRequest,
    load_frozen_artifact_contract,
)
from app.execution.openai_responses_provider import (
    HttpOpenAIResponsesTransport,
    OpenAIResponsesProviderAdapter,
    ProviderAttemptEvidenceUnavailableError,
    ProviderConfigurationError,
    ProviderContractError,
    ProviderExecutionError,
    ProviderRejectedRequestError,
    ProviderTransportNotSubmittedError,
    ProviderTransportUncertainError,
)
from app.execution.research_dispatch_huiyan_provider import DISPATCH_MODEL_PROFILE
from app.modules.agent_registry import get_registered_agent
from app.modules.provider_registry import load_provider_registry
from app.modules.provider_transport_evidence import (
    ProviderHttpAttemptRepository,
    provider_execution_parent,
)
from app.modules.repo_paths import repo_root

logger = logging.getLogger(__name__)


class AgentSessionExecutionError(RuntimeError):
    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code


def _schema_ref_for_contract(contract_id: str, preferred_ref: str | None) -> str:
    if preferred_ref and preferred_ref.strip():
        return preferred_ref.strip()
    candidate = f"schemas/agents/artifacts/{contract_id}.schema.json"
    if (repo_root() / candidate).is_file():
        return candidate
    raise AgentSessionExecutionError("artifact_schema_missing", f"schema not found for {contract_id}")


def _resolve_contract(agent: Any, active_step: dict[str, Any] | None) -> tuple[str, str]:
    step_contract = str((active_step or {}).get("artifactContract") or "").strip()
    output = agent.outputContract if isinstance(agent.outputContract, dict) else {}
    agent_contract = str(output.get("artifactContract") or "").strip()
    schema_ref = str(output.get("artifactSchema") or "").strip() or None
    contract_id = step_contract or agent_contract
    if not contract_id:
        raise AgentSessionExecutionError("artifact_contract_missing", "agent has no artifactContract")
    # Step-specific contracts may differ from the agent's primary outputContract schema path.
    if step_contract and step_contract != agent_contract:
        schema_ref = None
    return contract_id, _schema_ref_for_contract(contract_id, schema_ref)


def _build_input_snapshot(*, user_goal: str, agent_id: str, step: dict[str, Any] | None, messages: list[dict[str, Any]]) -> dict[str, Any]:
    history = [
        {"role": str(row.get("role") or ""), "content": str(row.get("content") or "")}
        for row in messages
        if str(row.get("role") or "") in {"user", "assistant"} and str(row.get("content") or "").strip()
    ]
    return {
        "instruction": (
            f"You are the registered OpenAIR specialist agent `{agent_id}`. "
            "Produce one final artifact for the active step from the user goal and recent dialogue. "
            "Do not invent literature retrieval or experimental results that were not supplied."
        ),
        "user_goal": user_goal,
        "activeStep": {
            "stepKey": (step or {}).get("stepKey"),
            "stage": (step or {}).get("stage"),
            "substage": (step or {}).get("substage"),
            "role": (step or {}).get("role"),
            "supportedRole": (step or {}).get("supportedRole"),
        },
        "dialogue": history[-12:],
    }


def _build_skill_input_snapshot(
    *,
    user_goal: str,
    skill_id: str,
    display_name: str,
    step: dict[str, Any] | None,
    messages: list[dict[str, Any]],
) -> dict[str, Any]:
    history = [
        {"role": str(row.get("role") or ""), "content": str(row.get("content") or "")}
        for row in messages
        if str(row.get("role") or "") in {"user", "assistant"} and str(row.get("content") or "").strip()
    ]
    return {
        "instruction": (
            f"You are executing the registered OpenAIR Skill `{skill_id}` ({display_name}). "
            "Produce one final structured artifact for the active skill step from the user goal "
            "and recent dialogue. Do not invent literature retrieval or experimental results "
            "that were not supplied."
        ),
        "user_goal": user_goal,
        "skillId": skill_id,
        "activeStep": {
            "stepKey": (step or {}).get("stepKey"),
            "stage": (step or {}).get("stage"),
            "substage": (step or {}).get("substage"),
            "role": (step or {}).get("role"),
            "supportedRole": (step or {}).get("supportedRole"),
        },
        "dialogue": history[-12:],
    }


def run_skill_session_turn(
    *,
    session: dict[str, Any],
    user_goal: str,
    owner_id: str,
    workspace_id: str,
) -> dict[str, Any]:
    """Execute the pinned skill once via the same provider path as plaza agents."""
    from app.execution.durable_skill_runtime import resolve_registered_durable_skill

    skill_id = str(session.get("agentId") or "").strip()
    skill = resolve_registered_durable_skill(skill_id)
    if skill is None or not skill.get("enabled") or not skill.get("durableExecutable"):
        raise AgentSessionExecutionError("agent_unavailable", "registered skill missing or not executable")

    display_name = str(session.get("agentDisplayName") or skill.get("displayName") or skill_id)
    steps = list(session.get("steps") or [])
    active_key = session.get("activeStepKey")
    active_step = next((row for row in steps if row.get("stepKey") == active_key), steps[0] if steps else None)

    contract_id = "general_research_reasoning_artifact_v1"
    schema_ref = "schemas/agents/artifacts/general_research_reasoning_artifact_v1.schema.json"
    try:
        frozen = load_frozen_artifact_contract(contract_id, schema_ref)
    except FrozenExecutionRequestError as exc:
        raise AgentSessionExecutionError("artifact_schema_invalid", str(exc)) from exc

    raw_required = frozen.schema.get("required")
    if not isinstance(raw_required, (list, tuple)):
        raise AgentSessionExecutionError(
            "artifact_schema_invalid",
            "artifact schema required fields unavailable",
        )
    required_fields = tuple(str(item).strip() for item in raw_required if str(item).strip())
    if not required_fields or len(required_fields) != len(set(required_fields)):
        raise AgentSessionExecutionError(
            "artifact_schema_invalid",
            "artifact schema required fields must be non-empty and unique",
        )

    provider_id = "openai_primary"
    session_id = str(session["sessionId"])
    run_id = f"skt_{uuid4().hex[:12]}"
    task_id = session_id
    snapshot = _build_skill_input_snapshot(
        user_goal=user_goal,
        skill_id=skill_id,
        display_name=display_name,
        step=active_step if isinstance(active_step, dict) else None,
        messages=list(session.get("messages") or []),
    )
    plan = {
        "context": {
            "runId": run_id,
            "taskId": task_id,
            "ownerId": owner_id,
            "workspaceId": workspace_id,
        },
        "selectedAgentId": f"skill:{skill_id}",
        "selectedSkillIds": [skill_id],
        "selectedProvider": provider_id,
        "modelProfile": DISPATCH_MODEL_PROFILE,
        "providerPolicy": {
            "allowedProviders": [provider_id],
            "defaultProvider": provider_id,
        },
        "artifactContract": frozen.contract_id,
        "artifactContractSchemaVersion": 1,
        "artifactRequiredFields": list(required_fields),
        "artifactSemanticVersion": frozen.semantic_version,
        "artifactSchemaDigest": frozen.schema_digest,
        "artifactSchema": dict(frozen.schema),
        "artifactSchemaRef": frozen.source_ref,
    }

    try:
        request = FrozenOrchestrationExecutionRequest(
            runId=run_id,
            taskId=task_id,
            ownerId=owner_id,
            workspaceId=workspace_id,
            orchestrationPlan=plan,
            frozenInputSnapshot=snapshot,
            selectedAgentId=f"skill:{skill_id}",
            selectedSkillIds=(skill_id,),
            logicalProviderId=provider_id,
            modelProfile=DISPATCH_MODEL_PROFILE,
            artifactContract=frozen.contract_id,
            artifactContractSchemaVersion=1,
            artifactRequiredFields=required_fields,
            artifactSemanticVersion=frozen.semantic_version,
            artifactSchemaDigest=frozen.schema_digest,
            artifactSchema=frozen.schema,
            artifactSchemaRef=frozen.source_ref,
            allowSkillRequests=False,
        )
    except FrozenExecutionRequestError as exc:
        raise AgentSessionExecutionError("execution_request_invalid", str(exc)) from exc

    adapter = OpenAIResponsesProviderAdapter(
        transport=HttpOpenAIResponsesTransport(
            attempt_repository=ProviderHttpAttemptRepository(),
        ),
        registry=load_provider_registry(),
    )
    try:
        with provider_execution_parent(
            kind="agent_session",
            parent_id=session_id,
            originating_authority="agent_session:skill_turn",
            only_if_absent=True,
        ):
            result = adapter.execute(request, capability_call_id=run_id, attempt_number=1)
    except ProviderAttemptEvidenceUnavailableError as exc:
        raise AgentSessionExecutionError("provider_attempt_evidence", str(exc)) from exc
    except ProviderConfigurationError as exc:
        raise AgentSessionExecutionError("provider_configuration_unavailable", str(exc)) from exc
    except ProviderRejectedRequestError as exc:
        raise AgentSessionExecutionError("provider_rejected", str(exc)) from exc
    except ProviderContractError as exc:
        code = getattr(exc, "safe_error_code", None) or "provider_contract_invalid"
        raise AgentSessionExecutionError(str(code), str(exc)) from exc
    except ProviderTransportNotSubmittedError as exc:
        raise AgentSessionExecutionError("provider_transport_not_submitted", str(exc)) from exc
    except ProviderTransportUncertainError as exc:
        raise AgentSessionExecutionError("provider_transport_uncertain", str(exc)) from exc
    except ProviderExecutionError as exc:
        raise AgentSessionExecutionError("provider_execution_failed", str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("skill_session_turn_unexpected skillId=%s sessionId=%s", skill_id, session_id)
        raise AgentSessionExecutionError("provider_execution_failed", str(exc)) from exc

    raw_content = result.content
    if isinstance(raw_content, str):
        try:
            body = json.loads(raw_content)
        except json.JSONDecodeError:
            body = {"raw": raw_content}
    elif isinstance(raw_content, dict):
        body = raw_content
    else:
        body = {"raw": str(raw_content)}

    title = str(result.title or "").strip() or f"{display_name} 产物"
    summary = _summarize_body(body, fallback=user_goal)
    return {
        "runId": run_id,
        "title": title,
        "mimeType": result.mimeType,
        "artifactContract": frozen.contract_id,
        "stepKey": (active_step or {}).get("stepKey") if isinstance(active_step, dict) else None,
        "summary": summary,
        "body": body,
        "assistantMessage": _assistant_message(title, summary),
    }


def run_agent_session_turn(
    *,
    session: dict[str, Any],
    user_goal: str,
    owner_id: str,
    workspace_id: str,
) -> dict[str, Any]:
    """Execute the pinned agent/skill once and return artifact payload for session storage."""
    if str(session.get("sessionKind") or "agent") == "skill":
        return run_skill_session_turn(
            session=session,
            user_goal=user_goal,
            owner_id=owner_id,
            workspace_id=workspace_id,
        )

    agent_id = str(session.get("agentId") or "").strip()
    agent = get_registered_agent(agent_id)
    if agent is None or not agent.enabled:
        raise AgentSessionExecutionError("agent_unavailable", "registered agent missing or disabled")

    steps = list(session.get("steps") or [])
    active_key = session.get("activeStepKey")
    active_step = next((row for row in steps if row.get("stepKey") == active_key), steps[0] if steps else None)
    contract_id, schema_ref = _resolve_contract(agent, active_step if isinstance(active_step, dict) else None)

    try:
        frozen = load_frozen_artifact_contract(contract_id, schema_ref)
    except FrozenExecutionRequestError as exc:
        raise AgentSessionExecutionError("artifact_schema_invalid", str(exc)) from exc

    # Frozen schemas store `required` as a tuple (immutable mapping thaw); accept list|tuple.
    raw_required = frozen.schema.get("required")
    if not isinstance(raw_required, (list, tuple)):
        raise AgentSessionExecutionError(
            "artifact_schema_invalid",
            "artifact schema required fields unavailable",
        )
    required_fields = tuple(str(item).strip() for item in raw_required if str(item).strip())
    if not required_fields or len(required_fields) != len(set(required_fields)):
        raise AgentSessionExecutionError(
            "artifact_schema_invalid",
            "artifact schema required fields must be non-empty and unique",
        )
    provider_id = str(agent.defaultProvider or "openai_primary").strip() or "openai_primary"
    allowed = list(agent.allowedProviders) or [provider_id]
    if provider_id not in allowed:
        allowed = [provider_id, *allowed]

    session_id = str(session["sessionId"])
    run_id = f"agt_{uuid4().hex[:12]}"
    task_id = session_id  # synthetic; not a ResearchDesign task
    snapshot = _build_input_snapshot(
        user_goal=user_goal,
        agent_id=agent_id,
        step=active_step if isinstance(active_step, dict) else None,
        messages=list(session.get("messages") or []),
    )
    plan = {
        "context": {
            "runId": run_id,
            "taskId": task_id,
            "ownerId": owner_id,
            "workspaceId": workspace_id,
        },
        "selectedAgentId": agent_id,
        "selectedSkillIds": [],
        "selectedProvider": provider_id,
        "modelProfile": DISPATCH_MODEL_PROFILE,
        "providerPolicy": {
            "allowedProviders": allowed,
            "defaultProvider": provider_id,
        },
        "artifactContract": frozen.contract_id,
        "artifactContractSchemaVersion": 1,
        "artifactRequiredFields": list(required_fields),
        "artifactSemanticVersion": frozen.semantic_version,
        "artifactSchemaDigest": frozen.schema_digest,
        "artifactSchema": dict(frozen.schema),
        "artifactSchemaRef": frozen.source_ref,
    }

    try:
        request = FrozenOrchestrationExecutionRequest(
            runId=run_id,
            taskId=task_id,
            ownerId=owner_id,
            workspaceId=workspace_id,
            orchestrationPlan=plan,
            frozenInputSnapshot=snapshot,
            selectedAgentId=agent_id,
            selectedSkillIds=(),
            logicalProviderId=provider_id,
            modelProfile=DISPATCH_MODEL_PROFILE,
            artifactContract=frozen.contract_id,
            artifactContractSchemaVersion=1,
            artifactRequiredFields=required_fields,
            artifactSemanticVersion=frozen.semantic_version,
            artifactSchemaDigest=frozen.schema_digest,
            artifactSchema=frozen.schema,
            artifactSchemaRef=frozen.source_ref,
            allowSkillRequests=False,
        )
    except FrozenExecutionRequestError as exc:
        raise AgentSessionExecutionError("execution_request_invalid", str(exc)) from exc

    adapter = OpenAIResponsesProviderAdapter(
        transport=HttpOpenAIResponsesTransport(
            attempt_repository=ProviderHttpAttemptRepository(),
        ),
        registry=load_provider_registry(),
    )
    try:
        with provider_execution_parent(
            kind="agent_session",
            parent_id=session_id,
            originating_authority="agent_session:agent_turn",
            only_if_absent=True,
        ):
            result = adapter.execute(request, capability_call_id=run_id, attempt_number=1)
    except ProviderAttemptEvidenceUnavailableError as exc:
        raise AgentSessionExecutionError("provider_attempt_evidence", str(exc)) from exc
    except ProviderConfigurationError as exc:
        raise AgentSessionExecutionError("provider_configuration_unavailable", str(exc)) from exc
    except ProviderRejectedRequestError as exc:
        raise AgentSessionExecutionError("provider_rejected", str(exc)) from exc
    except ProviderContractError as exc:
        code = getattr(exc, "safe_error_code", None) or "provider_contract_invalid"
        raise AgentSessionExecutionError(str(code), str(exc)) from exc
    except ProviderTransportNotSubmittedError as exc:
        raise AgentSessionExecutionError("provider_transport_not_submitted", str(exc)) from exc
    except ProviderTransportUncertainError as exc:
        raise AgentSessionExecutionError("provider_transport_uncertain", str(exc)) from exc
    except ProviderExecutionError as exc:
        raise AgentSessionExecutionError("provider_execution_failed", str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("agent_session_turn_unexpected agentId=%s sessionId=%s", agent_id, session_id)
        raise AgentSessionExecutionError("provider_execution_failed", str(exc)) from exc

    raw_content = result.content
    if isinstance(raw_content, str):
        try:
            body = json.loads(raw_content)
        except json.JSONDecodeError:
            body = {"raw": raw_content}
    elif isinstance(raw_content, dict):
        body = raw_content
    else:
        body = {"raw": str(raw_content)}

    title = str(result.title or "").strip() or f"{agent.displayName} 产物"
    summary = _summarize_body(body, fallback=user_goal)
    return {
        "runId": run_id,
        "title": title,
        "mimeType": result.mimeType,
        "artifactContract": frozen.contract_id,
        "stepKey": (active_step or {}).get("stepKey") if isinstance(active_step, dict) else None,
        "summary": summary,
        "body": body,
        "assistantMessage": _assistant_message(title, summary),
    }


def _summarize_body(body: dict[str, Any], *, fallback: str) -> str:
    for key in ("reasoning_summary", "scope", "summary", "verdict", "handoff"):
        value = body.get(key)
        if isinstance(value, str) and value.strip():
            text = " ".join(value.split())
            return text if len(text) <= 160 else f"{text[:160]}…"
    text = " ".join(fallback.split())
    return text if len(text) <= 120 else f"{text[:120]}…"


def _assistant_message(title: str, summary: str) -> str:
    return f"已完成一轮执行，产物「{title}」已写入中间栏。\n\n{summary}"
