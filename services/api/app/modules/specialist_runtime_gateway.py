"""Fail-closed gateway for production Specialist Agent calls."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import logging
import re
from typing import Any, Literal

from app.modules.agent_orchestrator import (
    ExecutionMode,
    UserInputPolicy,
    resolve_specialist_agent_for_call,
    select_execution_mode,
)
from app.modules.agent_registry import AgentRegistryError, load_agent_registry
from app.modules.research_agent_context_pack import build_research_agent_context_pack
from app.modules.task_store import store

logger = logging.getLogger(__name__)


GatewayAction = Literal["proceed", "block", "queue", "merge", "cancel", "wait"]
DEFAULT_EXECUTION_SUBJECT = "openair_orchestrator"


@dataclass(frozen=True)
class RuntimeGatewayDecision:
    action: GatewayAction
    taskId: str
    targetAgent: str | None
    executionMode: ExecutionMode | None
    userInputPolicy: UserInputPolicy
    reason: str
    contextPackId: str | None = None
    callId: str | None = None
    runKey: str | None = None
    budget: dict[str, Any] | None = None
    contextPack: dict[str, Any] | None = None
    materialReadinessReport: dict[str, Any] | None = None

    @property
    def should_call_runtime(self) -> bool:
        return self.action in {"proceed", "cancel"}

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def prepare_specialist_agent_call(
    *,
    task: dict[str, Any],
    message: str,
    skill_id: str | None = None,
    substage_key: str | None = None,
    requested_execution_mode: str | None = None,
    user_input_policy: str | None = None,
    allow_task_store_mutations: bool = True,
) -> RuntimeGatewayDecision:
    """Validate one specialist call before the runtime may execute it."""

    task_id = str(task.get("taskId") or "")
    target_agent = resolve_specialist_agent_for_call(
        skill_id=skill_id,
        substage_key=substage_key,
        task=task,
    )
    policy = _normalize_user_input_policy(user_input_policy, message)
    active_call = _active_call_for_substage(task, substage_key)
    if target_agent is None:
        execution_mode = _generic_execution_mode(str(task.get("status") or ""))
        if active_call is not None:
            if policy == "cancel":
                if not allow_task_store_mutations:
                    return RuntimeGatewayDecision(
                        action="cancel", taskId=task_id, targetAgent=DEFAULT_EXECUTION_SUBJECT,
                        executionMode=execution_mode, userInputPolicy=policy,
                        reason=f"Cancel active call {active_call.get('id')} before the explicit replacement chat.",
                        callId=str(active_call.get("id")), runKey=_run_key(task_id, substage_key),
                    )
                store.patch_capability_call(
                    task_id,
                    str(active_call.get("id")),
                    status="failed",
                    result_summary="Cancelled by a newer user input before generic runtime handoff.",
                    ended_at=_utc_now_from_task_store(),
                    metadata={
                        "runtimeGateway": "specialist_agent_runtime_gateway.v1",
                        "userInputPolicy": "cancel",
                    },
                )
            else:
                return _handle_active_call(
                    task=task,
                    message=message,
                    target_agent=DEFAULT_EXECUTION_SUBJECT,
                    execution_mode=execution_mode,
                    substage_key=substage_key,
                    policy=policy,
                    active_call=active_call,
                    allow_task_store_mutations=allow_task_store_mutations,
                )
        return RuntimeGatewayDecision(
            action="proceed",
            taskId=task_id,
            targetAgent=None,
            executionMode=execution_mode,
            userInputPolicy=policy,
            reason="No registered specialist target was inferred; generic workbench chat may proceed.",
            runKey=_run_key(task_id, substage_key),
        )

    registry = load_agent_registry()
    try:
        agent = registry.require(target_agent)
        execution_mode = select_execution_mode(
            agent,
            task_status=str(task.get("status") or ""),
            requested_mode=requested_execution_mode,
        )
    except (AgentRegistryError, ValueError) as exc:
        logger.warning("specialist gateway decision failed", exc_info=True)
        return RuntimeGatewayDecision(
            action="block",
            taskId=task_id,
            targetAgent=target_agent,
            executionMode=None,
            userInputPolicy=policy,
            reason="chat_gateway_unavailable",
            runKey=_run_key(task_id, substage_key),
        )

    if active_call is not None:
        if policy == "cancel":
            if not allow_task_store_mutations:
                return RuntimeGatewayDecision(
                    action="cancel", taskId=task_id, targetAgent=agent.agentId,
                    executionMode=execution_mode, userInputPolicy=policy,
                    reason=f"Cancel active call {active_call.get('id')} before the explicit replacement chat.",
                    callId=str(active_call.get("id")), runKey=_run_key(task_id, substage_key),
                )
            store.patch_capability_call(
                task_id,
                str(active_call.get("id")),
                status="failed",
                result_summary="Cancelled by a newer user input before specialist runtime handoff.",
                ended_at=_utc_now_from_task_store(),
                metadata={
                    "runtimeGateway": "specialist_agent_runtime_gateway.v1",
                    "userInputPolicy": "cancel",
                },
            )
        else:
            return _handle_active_call(
                task=task,
                message=message,
                target_agent=agent.agentId,
                execution_mode=execution_mode,
                substage_key=substage_key,
                policy=policy,
                active_call=active_call,
                allow_task_store_mutations=allow_task_store_mutations,
            )

    pack_result = build_research_agent_context_pack(
        target_agent=agent.agentId,
        user_input=_gateway_user_input(task, message),
        materials=_materials_from_task_and_message(task, message, agent.agentId),
        task_id=task_id,
        top_k=int(agent.budgetPolicy.get("maxContextMaterials") or 4),
    )
    if pack_result.status != "ready" or pack_result.contextPack is None:
        report = pack_result.materialReadinessReport.model_dump(mode="json")
        if agent.agentId == "validation_design" and execution_mode == "draft_only":
            context_pack = _validation_design_fail_closed_context(
                task=task,
                agent_id=agent.agentId,
                substage_key=substage_key,
                report=report,
                registry_budget=agent.budgetPolicy,
            )
            return RuntimeGatewayDecision(
                action="proceed",
                taskId=task_id,
                targetAgent=agent.agentId,
                executionMode=execution_mode,
                userInputPolicy=policy,
                reason="validation design fail-closed to a draft-only context envelope; execution remains forbidden.",
                contextPackId=context_pack["contextPackId"],
                runKey=_run_key(task_id, substage_key),
                budget=context_pack["budget"],
                contextPack=context_pack,
                materialReadinessReport=report,
            )
        return RuntimeGatewayDecision(
            action="block",
            taskId=task_id,
            targetAgent=agent.agentId,
            executionMode=execution_mode,
            userInputPolicy=policy,
            reason=report.get("reason") or "specialist context pack failed closed",
            runKey=_run_key(task_id, substage_key),
            materialReadinessReport=report,
        )

    pack = pack_result.contextPack
    budget = pack.budget.model_dump(mode="json")
    budget_violation = _budget_violation(agent.budgetPolicy, budget, execution_mode)
    if budget_violation:
        return RuntimeGatewayDecision(
            action="block",
            taskId=task_id,
            targetAgent=agent.agentId,
            executionMode=execution_mode,
            userInputPolicy=policy,
            reason=budget_violation,
            contextPackId=pack.contextPackId,
            runKey=_run_key(task_id, substage_key),
            budget=budget,
            contextPack=pack.model_dump(mode="json"),
            materialReadinessReport=pack.materialReadinessReport.model_dump(mode="json"),
        )

    return RuntimeGatewayDecision(
        action="proceed",
        taskId=task_id,
        targetAgent=agent.agentId,
        executionMode=execution_mode,
        userInputPolicy=policy,
        reason="registered specialist call has registry metadata, context pack, budget, and contract.",
        contextPackId=pack.contextPackId,
        runKey=_run_key(task_id, substage_key),
        budget=budget,
        contextPack=pack.model_dump(mode="json"),
        materialReadinessReport=pack.materialReadinessReport.model_dump(mode="json"),
    )


def _validation_design_fail_closed_context(
    *,
    task: dict[str, Any],
    agent_id: str,
    substage_key: str | None,
    report: dict[str, Any],
    registry_budget: dict[str, Any],
) -> dict[str, Any]:
    task_id = str(task.get("taskId") or "")
    return {
        "schemaVersion": "research_agent_context_pack.v1.gateway_fail_closed",
        "contextPackId": f"ctx_{agent_id}_draft_only_{task_id[-8:] or 'unknown'}",
        "taskId": task_id,
        "targetAgent": agent_id,
        "sdrStage": "solution_design",
        "substage": substage_key or "validation_design",
        "subgoal": "Draft only the minimum validation design skeleton; do not execute or claim results.",
        "allowedMaterialIds": [],
        "selectedExcerpts": [],
        "knownGaps": report.get("missingMaterials") or ["execution request cannot be run from draft context"],
        "forbiddenOutputs": [
            "experiment execution",
            "result claim",
            "effectiveness conclusion",
            "resource launch",
        ],
        "outputContract": {
            "contractId": "validation_design_artifact_v1",
            "artifactKind": "validation_plan",
            "requiredFields": [
                "status",
                "execution_status",
                "minimum_viable_validation_inputs",
                "clarification_questions",
                "cannot_execute_until",
            ],
            "failClosedStatus": "design_only",
        },
        "budget": {
            "mode": "draft_only",
            "maxWallTimeSec": min(90, int(registry_budget.get("maxWallTimeSec") or 90)),
            "maxToolCalls": min(2, int(registry_budget.get("maxToolCalls") or 2)),
        },
        "requiredValidationChecks": [
            "execution_status remains not_started",
            "no experiment result or effectiveness conclusion is emitted",
            "missing inputs and confirmation gates are explicit",
        ],
        "materialReadinessReport": report,
    }


def gateway_metadata(decision: RuntimeGatewayDecision) -> dict[str, Any]:
    return {
        "runtimeGateway": "specialist_agent_runtime_gateway.v1",
        "targetAgent": decision.targetAgent or DEFAULT_EXECUTION_SUBJECT,
        "executionMode": decision.executionMode,
        "userInputPolicy": decision.userInputPolicy,
        "contextPackId": decision.contextPackId,
        "runKey": decision.runKey,
        "budget": decision.budget,
        "materialReadinessReport": decision.materialReadinessReport,
        "gatewayReason": decision.reason,
    }


def execution_subject(decision: RuntimeGatewayDecision) -> str:
    """Return the durable, user-visible owner for one runtime call.

    A missing specialist is not an anonymous assistant: the OpenAIR controller
    still owns routing, boundaries, and the decision to invoke the runtime.
    """
    return decision.targetAgent or DEFAULT_EXECUTION_SUBJECT


def specialist_runtime_message(decision: RuntimeGatewayDecision, *, original_message: str) -> str | None:
    """Return the bounded prompt that may be handed to the specialist runtime."""

    if decision.action != "proceed" or not decision.targetAgent:
        return None
    if not decision.contextPack:
        return None
    envelope = {
        "runtimeGateway": "specialist_agent_runtime_gateway.v1",
        "targetAgent": decision.targetAgent,
        "executionMode": decision.executionMode,
        "contextPackId": decision.contextPackId,
        "contextPack": decision.contextPack,
    }
    return (
        "You are executing a registered OpenAIR Specialist Agent call.\n"
        "Use only the Runtime Gateway envelope below. Do not use raw task materials, unselected sources, "
        "or unstated assumptions. If the envelope is fail-closed or draft-only, return only the bounded "
        "artifact allowed by the outputContract and forbiddenOutputs.\n"
        "The user's visible request is included only as intent context; the Context Pack is the source boundary.\n\n"
        f"Visible user request:\n{original_message.strip()}\n\n"
        "Runtime Gateway envelope:\n"
        f"{json.dumps(envelope, ensure_ascii=False, indent=2)}"
    )


def _handle_active_call(
    *,
    task: dict[str, Any],
    message: str,
    target_agent: str,
    execution_mode: ExecutionMode,
    substage_key: str | None,
    policy: UserInputPolicy,
    active_call: dict[str, Any],
    allow_task_store_mutations: bool = True,
) -> RuntimeGatewayDecision:
    task_id = str(task.get("taskId") or "")
    run_key = _run_key(task_id, substage_key)
    call = None
    if allow_task_store_mutations:
        call = store.add_capability_call(
            task_id,
            skill_id=f"{target_agent}_user_input_{policy}",
            status="not_started" if policy in {"queue", "merge"} else "needs_input",
            runtime="specialist_agent_runtime_gateway",
            result_summary=f"User input {policy} while call {active_call.get('id')} is running.",
            stage_key=active_call.get("stageKey"),
            substage_instance_id=active_call.get("subStageInstanceId"),
        )
    if allow_task_store_mutations and call:
        store.patch_capability_call(
            task_id,
            call["id"],
            status=call["status"],
            metadata={
                "runtimeGateway": "specialist_agent_runtime_gateway.v1",
                "targetAgent": target_agent,
                "executionMode": execution_mode,
                "userInputPolicy": policy,
                "runKey": run_key,
                "blockedByCallId": active_call.get("id"),
            },
        )
    if allow_task_store_mutations:
        store.append_agent_message(
        task_id,
        role="user",
        text=message,
        runtime="specialist_agent_runtime_gateway",
        message_type=f"{policy}_while_running",
        capability_call_id=call.get("id") if call else None,
        )
    return RuntimeGatewayDecision(
        action=policy,
        taskId=task_id,
        targetAgent=target_agent,
        executionMode=execution_mode,
        userInputPolicy=policy,
        reason=f"Same task/substage already has running call {active_call.get('id')}; input policy={policy}.",
        callId=call.get("id") if call else None,
        runKey=run_key,
    )


def _active_call_for_substage(task: dict[str, Any], substage_key: str | None) -> dict[str, Any] | None:
    if not substage_key:
        return None
    substage_instance_ids = {
        sub.get("subStageInstanceId")
        for detail in task.get("stageDetails") or []
        for sub in detail.get("subStages") or []
        if sub.get("subStage") == substage_key and sub.get("subStageInstanceId")
    }
    for call in task.get("capabilityCalls") or []:
        if call.get("status") != "running":
            continue
        if call.get("subStageInstanceId") in substage_instance_ids:
            return call
    return None


def _normalize_user_input_policy(value: str | None, message: str) -> UserInputPolicy:
    raw = (value or "").strip().lower()
    if raw in {"queue", "merge", "cancel", "wait"}:
        return raw  # type: ignore[return-value]
    lower = message.lower()
    if any(marker in lower for marker in ("cancel previous", "cancel the current", "/cancel")):
        return "cancel"
    if any(marker in lower for marker in ("merge this", "add this to current", "/merge")):
        return "merge"
    if any(marker in lower for marker in ("wait", "after current", "/wait")):
        return "wait"
    return "queue"


def _generic_execution_mode(task_status: str) -> ExecutionMode:
    if task_status == "draft":
        return "draft_only"
    if task_status == "awaiting_confirmation":
        return "critique_only"
    if task_status == "running":
        return "full_execution"
    return "route_only"


def _budget_violation(registry_budget: dict[str, Any], pack_budget: dict[str, Any], execution_mode: ExecutionMode) -> str | None:
    allowed_modes = {str(mode) for mode in registry_budget.get("allowedModes") or []}
    if execution_mode not in allowed_modes:
        return f"execution mode {execution_mode} is not allowed by registry budget policy"
    if int(pack_budget.get("maxWallTimeSec") or 0) > int(registry_budget.get("maxWallTimeSec") or 0):
        return "context pack wall-time budget exceeds registry budget"
    if int(pack_budget.get("maxToolCalls") or 0) > int(registry_budget.get("maxToolCalls") or 0):
        return "context pack tool-call budget exceeds registry budget"
    return None


def _materials_from_task_and_message(task: dict[str, Any], message: str, agent_id: str) -> list[dict[str, Any]]:
    materials: list[dict[str, Any]] = []
    for index, material in enumerate(task.get("materials") or [], start=1):
        if isinstance(material, dict):
            materials.append(material)
        elif isinstance(material, str) and material.strip():
            materials.append(_material_from_text(material, index=index, agent_id=agent_id, source="task_material"))
    for artifact in task.get("artifacts") or []:
        if not isinstance(artifact, dict):
            continue
        inline = str(artifact.get("inlineContent") or "").strip()
        kind = str(artifact.get("kind") or "")
        if not inline:
            continue
        parsed = _materials_from_structured_text(inline, start_index=len(materials) + 1)
        if parsed:
            materials.extend(parsed)
        elif kind not in {"research_start_draft", "stage_summary"}:
            materials.append(_material_from_text(inline, index=len(materials) + 1, agent_id=agent_id, source=artifact.get("id") or kind))
    goal = str(task.get("goalSummary") or "")
    plan = task.get("creationPlan") if isinstance(task.get("creationPlan"), dict) else {}
    plan_goal = str(plan.get("goalSummary") or "")
    for text, source in ((goal, "goal"), (plan_goal, "creation_plan_goal"), (message, "message")):
        if _looks_like_supplied_material(text):
            parsed = _materials_from_structured_text(text, start_index=len(materials) + 1)
            if parsed:
                materials.extend(parsed)
            else:
                materials.append(_material_from_text(text, index=len(materials) + 1, agent_id=agent_id, source=source))
    return materials


_PROVIDED_MATERIAL_RE = re.compile(
    r"^\s*\[(?P<id>[A-Za-z0-9_.:-]+)\]\s+"
    r"(?P<type>[A-Za-z0-9_.:-]+)\s*:\s*"
    r"(?:locator=(?P<locator>.*?);\s*)?"
    r"content=(?P<content>.+?)\s*$"
)
_INLINE_MATERIAL_RE = re.compile(
    r"\[(?P<id>[A-Za-z0-9_.:-]+)\]\s+"
    r"(?P<type>[A-Za-z0-9_.:-]+)\s*:\s*"
    r"(?:locator=(?P<locator>.*?);\s*)?"
    r"content=(?P<content>.*?)(?=\s+\[[A-Za-z0-9_.:-]+\]\s+[A-Za-z0-9_.:-]+\s*:|$)",
    re.DOTALL,
)


def _materials_from_structured_text(text: str, *, start_index: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for offset, line in enumerate(text.splitlines()):
        match = _PROVIDED_MATERIAL_RE.match(line)
        if not match:
            continue
        material_id = match.group("id") or f"M{start_index + offset}"
        locator = (match.group("locator") or material_id).strip()
        content = (match.group("content") or "").strip()
        material_type = (match.group("type") or "material").strip()
        if content:
            rows.append(
                {
                    "materialId": material_id,
                    "locator": locator or material_id,
                    "type": material_type,
                    "text": content[:6000],
                }
            )
    if rows:
        return rows
    compact = " ".join(text.split())
    for offset, match in enumerate(_INLINE_MATERIAL_RE.finditer(compact)):
        material_id = match.group("id") or f"M{start_index + offset}"
        locator = (match.group("locator") or material_id).strip()
        content = (match.group("content") or "").strip()
        material_type = (match.group("type") or "material").strip()
        if content:
            rows.append(
                {
                    "materialId": material_id,
                    "locator": locator or material_id,
                    "type": material_type,
                    "text": content[:6000],
                }
            )
    return rows


def _material_from_text(text: str, *, index: int, agent_id: str, source: str) -> dict[str, Any]:
    return {
        "materialId": f"M{index}",
        "locator": source,
        "type": _material_type_for_agent(agent_id),
        "text": text.strip()[:6000],
    }


def _material_type_for_agent(agent_id: str) -> str:
    if agent_id == "result_reliability_audit":
        return "raw_data"
    if agent_id == "research_gap_identification":
        return "review_note"
    if agent_id == "validation_design":
        return "hypothesis"
    return "results_excerpt"


def _looks_like_supplied_material(text: str) -> bool:
    lower = text.lower()
    markers = [
        "materials:",
        "provided materials",
        "paper excerpt",
        "figure caption",
        "result table",
        "run log",
        "raw csv",
        "sample_id",
        "experiment note",
    ]
    return len(text.strip()) > 60 and any(marker in lower for marker in markers)


def _gateway_user_input(task: dict[str, Any], message: str) -> str:
    goal = str(task.get("goalSummary") or "").strip()
    if goal and message.strip() and message.strip() not in goal:
        return f"{goal}\n\nLatest user input: {message.strip()}"
    return message.strip() or goal


def _run_key(task_id: str, substage_key: str | None) -> str:
    return f"{task_id}:{substage_key or 'task'}"


def _utc_now_from_task_store() -> str:
    # Avoid exporting a new task_store API only for gateway bookkeeping.
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
