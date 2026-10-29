import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse

from app.auth import RequestIdentity, ensure_task_in_scope, resolve_request_identity
from app.config import settings
from app.execution.agent_service import agent_chat_stream, execute_skill
from app.execution.explicit_chat_runtime import ExplicitChatRuntimeError, run_explicit_chat_runtime
from app.execution.background_execution import track_background_execution
from app.models.agent_schemas import (
    AgentChatIn,
    AgentChatOut,
    AgentMessageListOut,
    AgentMessageOut,
    CapabilityCallListOut,
    ConfirmationResolveIn,
    ConfirmationResolveOut,
    ExecuteSkillIn,
    ExecuteSkillOut,
    SystemAgentRunIn,
    SystemAgentRunOut,
)
logger = logging.getLogger(__name__)

from app.models.schemas import CapabilityCallOut
from app.modules.system_agent_executor import SystemAgentExecutionError, run_system_agent
from app.modules import sdr_config
from app.modules.specialist_runtime_gateway import (
    RuntimeGatewayDecision,
    execution_subject,
    gateway_metadata,
    prepare_specialist_agent_call,
    specialist_runtime_message,
)
from app.modules.task_store import store
from app.modules.task_repository import TaskRepository
from app.modules.task_message_repository import TaskMessageRepository

router = APIRouter(prefix="/tasks", tags=["agent"])


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _task_message_runtime(task: dict, messages: list[dict]) -> str:
    for message in reversed(messages):
        runtime = message.get("runtime")
        if runtime:
            return str(runtime)
    for call in reversed(task.get("capabilityCalls") or []):
        runtime = call.get("runtime")
        if runtime:
            return str(runtime)
    return "none"


def _recent_task_messages(task_id: str, identity: RequestIdentity) -> list[dict]:
    """Latest page of chat messages from the openair_task_messages table.

    Used for the postgres streaming ``done`` event's ``messages`` field (the
    payload no longer carries agentMessages in postgres mode).
    """
    messages, _has_more, _latest = TaskMessageRepository().list_messages(
        task_id=task_id,
        owner_id=identity["ownerId"],
        workspace_id=identity["workspaceId"],
    )
    return messages


def _gateway_reply(decision: RuntimeGatewayDecision) -> str:
    if decision.action == "block":
        return f"Specialist Agent call blocked by Runtime Gateway: {decision.reason}"
    if decision.action in {"queue", "merge", "wait"}:
        return f"Current substage is already running. User input policy={decision.userInputPolicy}; the new input was {decision.action}."
    return decision.reason


def _postgres_gateway_reply(decision: RuntimeGatewayDecision) -> tuple[str, str | None]:
    """Keep PostgreSQL stream errors user-safe; technical reasons stay in logs."""
    if decision.action == "block":
        logger.warning("explicit chat gateway blocked task_id=%s reason=%s", decision.taskId, decision.reason)
        return "This chat request cannot proceed safely. Please review the task inputs and try again.", "chat_gateway_blocked"
    return _gateway_reply(decision), None


def _record_gateway_block(task_id: str, decision: RuntimeGatewayDecision) -> str | None:
    status = "insufficient_evidence" if decision.materialReadinessReport else "failed"
    call = store.add_capability_call(
        task_id,
        skill_id=decision.targetAgent or "specialist_agent_runtime_gateway",
        status=status,
        runtime="specialist_agent_runtime_gateway",
        result_summary=decision.reason,
        started_at=_utc_now(),
        ended_at=_utc_now(),
    )
    if not call:
        return None
    store.patch_capability_call(
        task_id,
        call["id"],
        status=status,
        metadata=gateway_metadata(decision),
    )
    return call["id"]


def _append_gateway_assistant_message(task_id: str, decision: RuntimeGatewayDecision) -> None:
    store.append_agent_message(
        task_id,
        role="assistant",
        text=_gateway_reply(decision),
        runtime="specialist_agent_runtime_gateway",
        message_type=f"runtime_gateway_{decision.action}",
        capability_call_id=decision.callId,
    )


async def _collect_agent_chat_stream(
    task_id: str,
    message: str,
    skill_id: str | None,
    substage_key: str | None,
    *,
    runtime_message: str | None = None,
) -> dict:
    """Return the terminal result of the same runtime path used by streaming clients."""
    terminal_event: dict | None = None
    async for event in agent_chat_stream(
        task_id,
        message,
        skill_id,
        substage_key,
        runtime_message=runtime_message,
    ):
        if event.get("type") == "done":
            terminal_event = event

    if terminal_event is None:
        raise RuntimeError("agent runtime ended without a terminal event")
    return {
        "reply": terminal_event.get("reply") or "",
        "runtime": terminal_event.get("runtime") or "error",
        "messages": terminal_event.get("messages") or [],
        "capabilityCallId": terminal_event.get("capabilityCallId"),
        "createdArtifactIds": terminal_event.get("createdArtifactIds") or [],
        "error": terminal_event.get("error"),
    }


async def _postgres_collaboration_stream(
    *, task: dict, task_id: str, body: AgentChatIn, identity: RequestIdentity,
    decision: RuntimeGatewayDecision,
):
    """Repository/CAS collaboration chat: messages only, never research capabilityCalls.

    Entered only by the user's composer request. Creates no execution-run row and
    does not mutate research CapabilityCall facts. Persist user message, run the
    collaboration agent, optionally apply Workbench-authoritative actions, then
    persist the assistant reply.
    """
    repo = TaskRepository()
    if not decision.should_call_runtime:
        reply, gateway_error = _postgres_gateway_reply(decision)
        call_name = None
        call_status = "failed"
        user_message = None
        if decision.action == "block":
            call_name = decision.targetAgent or "specialist_agent_runtime_gateway"
            call_status = "insufficient_evidence" if decision.materialReadinessReport else "failed"
        elif decision.action in {"queue", "merge", "wait"}:
            call_name = f"{execution_subject(decision)}_user_input_{decision.action}"
            call_status = "not_started" if decision.action in {"queue", "merge"} else "needs_input"
            user_message = body.message
        payload, call_id = repo.record_collaboration_gateway(
            task_id=task_id, owner_id=identity["ownerId"], workspace_id=identity["workspaceId"],
            reply=reply, action=decision.action, call_name=call_name, call_status=call_status, user_message=user_message,
        )
        yield {
            "type": "done", "reply": reply, "runtime": "specialist_agent_runtime_gateway",
            "messages": _recent_task_messages(task_id, identity), "capabilityCallId": call_id,
            "createdArtifactIds": [], "error": gateway_error,
            "executionMode": decision.executionMode, "targetAgent": execution_subject(decision),
            "contextPackId": decision.contextPackId, "userInputPolicy": decision.userInputPolicy,
        }
        return

    skill_id = body.skillId or "workbench_chat"
    source_mode = "replay" if settings.skill_exec_mode == "replay" else "live"
    runtime = "replay" if source_mode == "replay" else "huiyan_chat_completions"
    from app.modules.workbench_user_actions import project_workbench_user_actions
    from app.modules.workbench_collaboration_actions import apply_collaboration_proposed_action

    actions_snapshot = project_workbench_user_actions(
        task_payload=task,
        owner_id=identity["ownerId"],
        workspace_id=identity["workspaceId"],
        actor_id=identity["createdBy"],
    )
    try:
        if decision.action == "cancel" and decision.callId:
            repo.cancel_collaboration_call(
                task_id=task_id, owner_id=identity["ownerId"], workspace_id=identity["workspaceId"], call_id=decision.callId,
            )
        call, _payload = repo.begin_collaboration_chat(
            task_id=task_id, owner_id=identity["ownerId"], workspace_id=identity["workspaceId"],
            message=body.message, skill_id=skill_id, runtime=runtime, source_mode=source_mode,
            stage_key=body.substageKey or task.get("currentStage"),
        )
    except Exception:
        logger.exception("postgres collaboration start failed task_id=%s", task_id)
        yield {"type": "done", "reply": "暂时无法记录这条消息，请稍后重试。", "runtime": "error", "messages": [], "capabilityCallId": None, "createdArtifactIds": [], "error": "chat_persistence_failed"}
        return
    call_id = call["id"]
    yield {
        "type": "started",
        "runtime": runtime,
        "capabilityCallId": call_id,
        "skillId": skill_id,
        "requestedAt": call.get("requestedAt") or _utc_now(),
    }
    try:
        result = await run_explicit_chat_runtime(
            task=task,
            message=specialist_runtime_message(decision, original_message=body.message) or body.message,
            skill_id=skill_id,
            source_mode=source_mode,
            actions_snapshot=actions_snapshot,
        )
        reply = result.reply
        action_receipt = None
        proposed = result.proposed_action
        if isinstance(proposed, dict) and proposed.get("type") == "regenerate_research_plan":
            from app.execution.workbench_collaboration_agent import reply_for_plan_regeneration

            latest = repo.get_task(task_id) or task
            action_receipt = apply_collaboration_proposed_action(
                task_payload=latest,
                identity=identity,
                proposed=proposed,
            )
            reply = reply_for_plan_regeneration(action_receipt)
            proposed = None
        yield {"type": "assistant_delta", "text": reply, "capabilityCallId": call_id}
        if proposed:
            # Re-load the latest payload so action application sees the chat revision.
            latest = repo.get_task(task_id) or task
            action_receipt = apply_collaboration_proposed_action(
                task_payload=latest,
                identity=identity,
                proposed=proposed,
            )
        metadata = dict(result.source_metadata or {})
        if action_receipt is not None:
            metadata["appliedAction"] = action_receipt
        payload = repo.finish_collaboration_chat(
            task_id=task_id, owner_id=identity["ownerId"], workspace_id=identity["workspaceId"],
            call_id=call_id, reply=reply, runtime=result.runtime, session_id=result.session_id,
            source_metadata=metadata,
        )
        yield {
            "type": "done",
            "reply": reply,
            "runtime": result.runtime,
            "messages": _recent_task_messages(task_id, identity),
            "capabilityCallId": call_id, "createdArtifactIds": [], "error": None,
            "executionMode": decision.executionMode, "targetAgent": execution_subject(decision),
            "contextPackId": decision.contextPackId, "userInputPolicy": decision.userInputPolicy,
            "appliedAction": action_receipt,
        }
    except ExplicitChatRuntimeError as exc:
        logger.warning("explicit collaboration runtime failed task_id=%s code=%s", task_id, exc.code)
        user_reply = "研究助手暂时无法完成回复，请稍后重试。"
        payload = repo.finish_collaboration_chat(
            task_id=task_id, owner_id=identity["ownerId"], workspace_id=identity["workspaceId"],
            call_id=call_id, reply=user_reply, runtime=runtime,
            error_code=exc.code, source_metadata={"runtimeFailure": exc.code},
        )
        yield {"type": "done", "reply": user_reply, "runtime": "error", "messages": _recent_task_messages(task_id, identity), "capabilityCallId": call_id, "createdArtifactIds": [], "error": "chat_runtime_failed"}
    except Exception:
        logger.exception("explicit collaboration terminal persistence failed task_id=%s", task_id)
        yield {"type": "done", "reply": "研究助手回复未能保存，请稍后重试。", "runtime": "error", "messages": [], "capabilityCallId": call_id, "createdArtifactIds": [], "error": "chat_persistence_failed"}


@router.get("/{task_id}/agent/messages", response_model=AgentMessageListOut)
async def list_agent_messages(
    task_id: str,
    before_seq: Annotated[int | None, Query(alias="before", ge=1)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    identity: RequestIdentity = Depends(resolve_request_identity),
) -> AgentMessageListOut:
    if settings.persistence_backend == "postgres":
        task = ensure_task_in_scope(TaskRepository().get_task(task_id), identity)
        session_id = task.get("agentSessionId")
        raw_messages, has_more, _latest_seq = TaskMessageRepository().list_messages(
            task_id=task_id,
            owner_id=identity["ownerId"],
            workspace_id=identity["workspaceId"],
            before_seq=before_seq,
            limit=limit,
        )
        next_before_seq = raw_messages[-1]["seq"] if raw_messages else None
        messages = [AgentMessageOut.model_validate(m) for m in raw_messages]
        return AgentMessageListOut(
            messages=messages,
            runtime=_task_message_runtime(task, raw_messages),
            sessionId=session_id,
            ownerId=task.get("ownerId"),
            workspaceId=task.get("workspaceId"),
            createdBy=task.get("createdBy"),
            hasMore=has_more,
            nextBeforeSeq=next_before_seq,
        )
    task = ensure_task_in_scope(store.get_task(task_id), identity)
    raw_messages = store.get_agent_messages(task_id)
    session_id = store.get_agent_session_id(task_id)
    messages = [AgentMessageOut.model_validate(m) for m in raw_messages]
    return AgentMessageListOut(
        messages=messages,
        runtime=_task_message_runtime(task, raw_messages),
        sessionId=session_id,
        ownerId=task.get("ownerId"),
        workspaceId=task.get("workspaceId"),
        createdBy=task.get("createdBy"),
    )


@router.post("/{task_id}/agent/chat", response_model=AgentChatOut)
async def post_agent_chat(
    task_id: str,
    body: AgentChatIn,
    identity: RequestIdentity = Depends(resolve_request_identity),
) -> AgentChatOut:
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail={
            "code": "synchronous_model_route_retired",
            "use": f"/api/tasks/{task_id}/agent/chat/stream",
        },
    )


async def _post_agent_chat_impl(
    task_id: str,
    body: AgentChatIn,
    identity: RequestIdentity,
) -> AgentChatOut:
    task = ensure_task_in_scope(store.get_task(task_id), identity)
    gateway_decision = prepare_specialist_agent_call(
        task=task,
        message=body.message,
        skill_id=body.skillId,
        substage_key=body.substageKey,
        requested_execution_mode=body.executionMode,
        user_input_policy=body.inputPolicy,
    )
    if not gateway_decision.should_call_runtime:
        if gateway_decision.action == "block":
            call_id = _record_gateway_block(task_id, gateway_decision)
            gateway_decision = RuntimeGatewayDecision(
                **{**gateway_decision.to_dict(), "callId": call_id}
            )
        _append_gateway_assistant_message(task_id, gateway_decision)
        return AgentChatOut(
            reply=_gateway_reply(gateway_decision),
            runtime="specialist_agent_runtime_gateway",
            messages=[AgentMessageOut.model_validate(m) for m in store.get_agent_messages(task_id)],
            capabilityCallId=gateway_decision.callId,
            createdArtifactIds=[],
            error=None if gateway_decision.action in {"queue", "merge", "wait"} else gateway_decision.reason,
            executionMode=gateway_decision.executionMode,
            targetAgent=execution_subject(gateway_decision),
            contextPackId=gateway_decision.contextPackId,
            userInputPolicy=gateway_decision.userInputPolicy,
        )
    try:
        result = await _collect_agent_chat_stream(
            task_id,
            body.message,
            body.skillId,
            body.substageKey,
            runtime_message=specialist_runtime_message(gateway_decision, original_message=body.message),
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="task not found") from None
    if result.get("capabilityCallId"):
        current = store.get_task(task_id) or task
        call = next(
            (row for row in current.get("capabilityCalls") or [] if row.get("id") == result.get("capabilityCallId")),
            None,
        )
        store.patch_capability_call(
            task_id,
            result["capabilityCallId"],
            status=call.get("status") if call else "completed",
            metadata=gateway_metadata(gateway_decision),
        )
    return AgentChatOut(
        reply=result["reply"],
        runtime=result["runtime"],
        messages=[AgentMessageOut.model_validate(m) for m in result["messages"]],
        capabilityCallId=result.get("capabilityCallId"),
        createdArtifactIds=result.get("createdArtifactIds") or [],
        error=result.get("error"),
        executionMode=gateway_decision.executionMode,
        targetAgent=execution_subject(gateway_decision),
        contextPackId=gateway_decision.contextPackId,
        userInputPolicy=gateway_decision.userInputPolicy,
    )


@router.post("/{task_id}/agent/confirmations/{confirmation_id}/resolve", response_model=ConfirmationResolveOut)
def resolve_confirmation(
    task_id: str,
    confirmation_id: str,
    body: ConfirmationResolveIn,
    identity: RequestIdentity = Depends(resolve_request_identity),
) -> ConfirmationResolveOut:
    ensure_task_in_scope(store.get_task(task_id), identity)
    resolved = store.resolve_confirmation_request(
        task_id,
        confirmation_id,
        action=body.action,
        note=body.note,
        selected_option=body.selectedOption,
    )
    if not resolved:
        raise HTTPException(status_code=404, detail="confirmation not found")
    return ConfirmationResolveOut.model_validate(resolved)


@router.post("/{task_id}/agent/chat/stream")
async def post_agent_chat_stream(
    task_id: str,
    body: AgentChatIn,
    identity: RequestIdentity = Depends(resolve_request_identity),
) -> StreamingResponse:
    from app.modules.heavy_task_queue import HeavyQueueFull, get_heavy_task_queue

    queue = get_heavy_task_queue()
    try:
        heavy_ticket = await asyncio.to_thread(queue.acquire, identity["ownerId"], kind="agent_chat_stream")
    except HeavyQueueFull as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "code": "heavy_queue_full",
                "message": str(exc),
                "retryAfter": exc.retry_after,
                "position": exc.position,
            },
            headers={"Retry-After": str(exc.retry_after)},
        ) from exc

    def _release_heavy() -> None:
        queue.release(heavy_ticket)

    task = ensure_task_in_scope(store.get_task(task_id), identity)
    # Collaboration chat is not a research execution dispatch: no skill gate.
    gateway_decision = prepare_specialist_agent_call(
        task=task,
        message=body.message,
        skill_id=body.skillId,
        substage_key=body.substageKey,
        requested_execution_mode=body.executionMode,
        user_input_policy=body.inputPolicy,
        allow_task_store_mutations=settings.persistence_backend != "postgres",
    )

    if settings.persistence_backend == "postgres":
        async def postgres_event_iter():
            try:
                async for event in _postgres_collaboration_stream(
                    task=task, task_id=task_id, body=body, identity=identity, decision=gateway_decision,
                ):
                    yield json.dumps(event, ensure_ascii=False) + "\n"
            finally:
                _release_heavy()
        return StreamingResponse(
            postgres_event_iter(),
            media_type="application/x-ndjson",
        )

    if not gateway_decision.should_call_runtime:
        async def immediate_event_iter():
            try:
                decision = gateway_decision
                if gateway_decision.action == "block":
                    call_id = _record_gateway_block(task_id, gateway_decision)
                    decision = RuntimeGatewayDecision(
                        **{**gateway_decision.to_dict(), "callId": call_id}
                    )
                _append_gateway_assistant_message(task_id, decision)
                yield json.dumps(
                    {
                        "type": "done",
                        "reply": _gateway_reply(decision),
                        "runtime": "specialist_agent_runtime_gateway",
                        "messages": store.get_agent_messages(task_id),
                        "capabilityCallId": decision.callId,
                        "createdArtifactIds": [],
                        "error": None if decision.action in {"queue", "merge", "wait"} else decision.reason,
                        "executionMode": decision.executionMode,
                        "targetAgent": execution_subject(decision),
                        "contextPackId": decision.contextPackId,
                        "userInputPolicy": decision.userInputPolicy,
                    },
                    ensure_ascii=False,
                ) + "\n"
            finally:
                _release_heavy()
        return StreamingResponse(immediate_event_iter(), media_type="application/x-ndjson")

    events: asyncio.Queue[dict | None] = asyncio.Queue()
    subscriber_active = True

    async def publish(event: dict | None) -> None:
        # The execution owns its durable task updates; this queue is only for
        # the current HTTP subscriber. Do not accumulate an unbounded event
        # backlog after the browser has navigated away.
        if subscriber_active:
            await events.put(event)

    async def execute_in_background() -> None:
        started_call_id: str | None = None
        try:
            async for event in agent_chat_stream(
                task_id,
                body.message,
                body.skillId,
                body.substageKey,
                runtime_message=specialist_runtime_message(gateway_decision, original_message=body.message),
            ):
                if event.get("type") == "started" and event.get("capabilityCallId"):
                    started_call_id = event["capabilityCallId"]
                if event.get("capabilityCallId"):
                    event.setdefault("targetAgent", execution_subject(gateway_decision))
                    event.setdefault("executionMode", gateway_decision.executionMode)
                    event.setdefault("contextPackId", gateway_decision.contextPackId)
                    event.setdefault("userInputPolicy", gateway_decision.userInputPolicy)
                if event.get("type") == "done" and event.get("capabilityCallId"):
                    current = store.get_task(task_id) or task
                    call = next(
                        (row for row in current.get("capabilityCalls") or [] if row.get("id") == event.get("capabilityCallId")),
                        None,
                    )
                    store.patch_capability_call(
                        task_id,
                        event["capabilityCallId"],
                        status=call.get("status") if call else "completed",
                        metadata=gateway_metadata(gateway_decision),
                    )
                await publish(event)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("event_iter failed task_id=%s", task_id)
            await publish(
                {
                    "type": "done",
                    "reply": "",
                    "runtime": "error",
                    "messages": [],
                    "capabilityCallId": None,
                    "createdArtifactIds": [],
                    "error": "chat_runtime_failed",
                }
            )
        finally:
            if started_call_id:
                current = store.get_task(task_id) or {}
                call = next((row for row in current.get("capabilityCalls") or [] if row.get("id") == started_call_id), None)
                if call and call.get("status") == "running":
                    store.patch_capability_call(
                        task_id,
                        started_call_id,
                        status="failed",
                        result_summary="Live Skill stream ended before a terminal result was recorded. Retry the current Skill.",
                        ended_at=_utc_now(),
                    )
            _release_heavy()
            await publish(None)

    background_execution = asyncio.create_task(execute_in_background())
    track_background_execution(task_id, background_execution)

    async def event_iter():
        nonlocal subscriber_active
        try:
            while True:
                event = await events.get()
                if event is None:
                    return
                yield json.dumps(event, ensure_ascii=False) + "\n"
        finally:
            subscriber_active = False

    return StreamingResponse(event_iter(), media_type="application/x-ndjson")


@router.post("/{task_id}/execute", response_model=ExecuteSkillOut)
async def post_execute_skill(
    task_id: str,
    body: ExecuteSkillIn,
    identity: RequestIdentity = Depends(resolve_request_identity),
) -> ExecuteSkillOut:
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail={"code": "synchronous_execution_route_retired", "use": f"/api/tasks/{task_id}/start"},
    )


@router.post("/{task_id}/system-agents/run", response_model=SystemAgentRunOut)
def post_system_agent_run(
    task_id: str,
    body: SystemAgentRunIn,
    identity: RequestIdentity = Depends(resolve_request_identity),
) -> SystemAgentRunOut:
    ensure_task_in_scope(store.get_task(task_id), identity)
    try:
        result = run_system_agent(
            task_id,
            agent_id=body.agentId,
            source_text=body.sourceText,
            artifact_id=body.artifactId,
            criteria=body.criteria,
            audience=body.audience,
            output_format=body.outputFormat,
            review_decision=body.reviewDecision,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="task not found") from None
    except SystemAgentExecutionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return SystemAgentRunOut.model_validate(result)


@router.get("/{task_id}/calls", response_model=CapabilityCallListOut)
def list_capability_calls(task_id: str, identity: RequestIdentity = Depends(resolve_request_identity)) -> CapabilityCallListOut:
    task = ensure_task_in_scope(store.get_task(task_id), identity)
    return CapabilityCallListOut(calls=task.get("capabilityCalls") or [])


@router.get("/{task_id}/calls/{call_id}", response_model=CapabilityCallOut)
def get_capability_call(task_id: str, call_id: str, identity: RequestIdentity = Depends(resolve_request_identity)) -> CapabilityCallOut:
    task = ensure_task_in_scope(store.get_task(task_id), identity)
    for call in task.get("capabilityCalls") or []:
        if call.get("id") == call_id:
            return CapabilityCallOut.model_validate(call)
    raise HTTPException(status_code=404, detail="call not found")

