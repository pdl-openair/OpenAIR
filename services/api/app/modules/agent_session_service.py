"""Create and advance plaza AgentSession without ResearchDesign / planning."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

from app.config import settings
from app.modules.agent_registry import get_registered_agent
from app.modules.agent_session_executor import AgentSessionExecutionError, run_agent_session_turn
from app.modules.agent_session_store import (
    agent_session_store,
    new_agent_session_id,
    new_artifact_id,
    new_message_id,
)
from app.modules.agent_session_artifact_markdown import project_agent_session_artifact_markdown
from app.modules.resource_catalog import (
    ResourceCatalogUnavailableError,
    get_resource_for_research_start,
)

logger = logging.getLogger(__name__)


class AgentSessionError(RuntimeError):
    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _use_postgres() -> bool:
    return str(getattr(settings, "persistence_backend", "") or "").strip().lower() == "postgres"


def _create_session(session: dict[str, Any]) -> dict[str, Any]:
    if _use_postgres():
        from app.modules.agent_session_repository import AgentSessionRepository

        return AgentSessionRepository().create(session)
    return agent_session_store.create(session)


def _get_scoped_session(session_id: str, *, owner_id: str, workspace_id: str) -> dict[str, Any] | None:
    if _use_postgres():
        from app.modules.agent_session_repository import AgentSessionRepository

        return AgentSessionRepository().get_scoped(
            session_id, owner_id=owner_id, workspace_id=workspace_id
        )
    return agent_session_store.get_scoped(session_id, owner_id=owner_id, workspace_id=workspace_id)


def _replace_session(session: dict[str, Any]) -> dict[str, Any]:
    if _use_postgres():
        from app.modules.agent_session_repository import AgentSessionRepository

        return AgentSessionRepository().replace(session)
    return agent_session_store.replace(session)


def _list_session_summaries(
    *, owner_id: str, workspace_id: str, limit: int = 50
) -> list[dict[str, Any]]:
    if _use_postgres():
        from app.modules.agent_session_repository import AgentSessionRepository

        return AgentSessionRepository().list_summaries(
            owner_id=owner_id, workspace_id=workspace_id, limit=limit
        )
    return agent_session_store.list_summaries(
        owner_id=owner_id, workspace_id=workspace_id, limit=limit
    )

def _safe_filename(title: str, artifact_id: str) -> str:
    stem = re.sub(r"[^\w\u4e00-\u9fff\-]+", "_", (title or "").strip(), flags=re.UNICODE)
    stem = stem.strip("._")[:80] or "artifact"
    return f"{stem}_{artifact_id[-8:]}.md"


def attachment_content_disposition(filename: str, *, artifact_id: str = "") -> str:
    """ASCII filename + RFC 5987 filename* so non-latin titles do not break headers."""
    from urllib.parse import quote

    suffix = (artifact_id or "file")[-8:]
    ascii_name = f"artifact_{suffix}.md"
    display_name = filename.strip() or ascii_name
    if not display_name.lower().endswith(".md"):
        display_name = f"{display_name}.md"
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(display_name)}"


def _markdown_bytes(body: dict[str, Any], title: str) -> bytes:
    return project_agent_session_artifact_markdown(body, title).encode("utf-8")


def _store_session_artifact_bytes(
    *,
    session_id: str,
    artifact_id: str,
    body: dict[str, Any],
    title: str,
) -> dict[str, Any]:
    """Persist artifact Markdown to MinIO (preferred) or local durable storage."""
    payload = _markdown_bytes(body, title)
    digest = hashlib.sha256(payload).hexdigest()
    filename = f"{artifact_id}.md"
    content_type = "text/markdown; charset=utf-8"

    if settings.artifact_storage == "minio":
        try:
            from app.modules.minio_client import current_minio_storage_scope, upload_bytes

            object_key = upload_bytes(
                session_id,
                filename,
                payload,
                content_type=content_type,
            )
            return {
                "storageType": "external",
                "externalKey": object_key,
                "storageRef": object_key,
                "storageScope": current_minio_storage_scope(),
                "contentSha256": digest,
                "contentSize": len(payload),
                "downloadFormat": "markdown",
                "downloadAvailable": True,
            }
        except Exception:  # noqa: BLE001
            logger.exception(
                "agent_session_minio_upload_failed sessionId=%s artifactId=%s",
                session_id,
                artifact_id,
            )

    try:
        from app.execution.execution_context import get_task_workspace_dir

        ws_dir = get_task_workspace_dir(session_id)
        target_dir = ws_dir / "agent-session"
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / filename).write_bytes(payload)
        storage_ref = f"agent-session/{filename}"
        return {
            "storageType": "local",
            "path": storage_ref,
            "storageRef": storage_ref,
            "storageScope": session_id,
            "contentSha256": digest,
            "contentSize": len(payload),
            "downloadFormat": "markdown",
            "downloadAvailable": True,
        }
    except Exception:  # noqa: BLE001
        logger.exception(
            "agent_session_local_store_failed sessionId=%s artifactId=%s",
            session_id,
            artifact_id,
        )
        return {
            "storageType": "inline",
            "downloadFormat": "markdown",
            "downloadAvailable": True,
            "contentSha256": digest,
            "contentSize": len(payload),
        }


def _download_payload_from_blob(
    blob: bytes,
    *,
    body: dict[str, Any] | None,
    title: str,
    artifact_id: str,
) -> tuple[bytes, str, str]:
    filename = _safe_filename(title, artifact_id)
    media_type = "text/markdown; charset=utf-8"
    text = blob.decode("utf-8", errors="replace").lstrip()
    if text.startswith("{") or text.startswith("["):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            return _markdown_bytes(parsed, title), media_type, filename
        if isinstance(body, dict):
            return _markdown_bytes(body, title), media_type, filename
    return blob, media_type, filename


def _artifact_download_tuple(session_id: str, artifact: dict[str, Any]) -> tuple[bytes, str, str]:
    title = str(artifact.get("title") or "会话产物")
    artifact_id = str(artifact.get("artifactId") or "artifact")
    body = artifact.get("body") if isinstance(artifact.get("body"), dict) else None
    storage_type = str(artifact.get("storageType") or "").strip().lower()
    storage_ref = str(artifact.get("storageRef") or artifact.get("externalKey") or artifact.get("path") or "").strip()
    storage_scope = str(artifact.get("storageScope") or "").strip() or None

    if storage_type in {"external", "local"} and storage_ref:
        try:
            from app.modules.artifact_write import ArtifactStorageError, artifact_write_service

            blob = artifact_write_service.read_durable_bytes(
                session_id,
                storage_type,
                storage_ref,
                storage_scope=storage_scope,
            )
            if blob is not None:
                return _download_payload_from_blob(
                    blob,
                    body=body,
                    title=title,
                    artifact_id=artifact_id,
                )
        except ArtifactStorageError:
            logger.exception(
                "agent_session_artifact_read_failed sessionId=%s artifactId=%s",
                session_id,
                artifact_id,
            )

    if isinstance(body, dict):
        return (
            _markdown_bytes(body, title),
            "text/markdown; charset=utf-8",
            _safe_filename(title, artifact_id),
        )
    raise AgentSessionError("artifact_content_unavailable", "artifact content unavailable")


def read_session_artifact_bytes(
    *,
    session_id: str,
    owner_id: str,
    workspace_id: str,
    artifact_id: str,
) -> tuple[bytes, str, str]:
    """Return (bytes, media_type, filename) for one session artifact download."""
    row = _get_scoped_session(session_id, owner_id=owner_id, workspace_id=workspace_id)
    if row is None:
        raise AgentSessionError("not_found", "session not found")
    artifact = next(
        (
            item
            for item in (row.get("artifacts") or [])
            if str(item.get("artifactId") or "") == artifact_id
        ),
        None,
    )
    if artifact is None:
        raise AgentSessionError("artifact_not_found", "artifact not found")
    return _artifact_download_tuple(session_id, artifact)


def build_session_artifacts_zip(
    *,
    session_id: str,
    owner_id: str,
    workspace_id: str,
) -> tuple[bytes, str]:
    """Pack all session artifacts into a zip of Markdown files."""
    import io
    import zipfile

    row = _get_scoped_session(session_id, owner_id=owner_id, workspace_id=workspace_id)
    if row is None:
        raise AgentSessionError("not_found", "session not found")
    artifacts = [item for item in (row.get("artifacts") or []) if isinstance(item, dict)]
    if not artifacts:
        raise AgentSessionError("artifact_not_found", "no artifacts to pack")

    used_names: set[str] = set()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        for index, artifact in enumerate(artifacts, start=1):
            try:
                blob, _media_type, filename = _artifact_download_tuple(session_id, artifact)
            except AgentSessionError:
                logger.warning(
                    "agent_session_zip_skip_artifact sessionId=%s artifactId=%s",
                    session_id,
                    artifact.get("artifactId"),
                )
                continue
            name = filename or f"artifact_{index}.md"
            if name in used_names:
                stem = name[:-3] if name.lower().endswith(".md") else name
                name = f"{stem}_{index}.md"
            used_names.add(name)
            archive.writestr(name, blob)
        if not used_names:
            raise AgentSessionError("artifact_content_unavailable", "artifact content unavailable")

    zip_name = f"agent_session_{session_id}_artifacts.zip"
    return buffer.getvalue(), zip_name


def _turn_failure_message(exc: AgentSessionExecutionError) -> str:
    detail = " ".join(str(exc).split())
    if detail and detail != exc.code:
        if len(detail) > 180:
            detail = f"{detail[:180]}…"
        return f"本轮执行未完成（{exc.code}）：{detail}。可换个表述重试，或换一个可试用智能体。"
    hints = {
        "provider_configuration_unavailable": "服务端 Provider 配置不可用，请检查 LLM_PROVIDER_* 环境变量后重启 API。",
        "execution_request_invalid": "执行请求构造失败，请换个可试用智能体或联系开发排查。",
        "provider_rejected": "上游模型拒绝了本次请求，请精简目标后重试。",
        "provider_transport_uncertain": "上游调用结果不确定，请稍后重试。",
        "provider_execution_failed": "上游执行失败，请稍后重试。",
    }
    hint = hints.get(exc.code, "请换个表述重试，或换一个可试用智能体。")
    return f"本轮执行未完成（{exc.code}）。{hint}"


def _step_key(mapping: dict[str, Any]) -> str:
    pack = str(mapping.get("pack") or "sdr")
    stage = str(mapping.get("stage") or "")
    substage = str(mapping.get("substage") or "")
    return f"{pack}:{stage}:{substage}"


def _freeze_steps(agent: Any) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    for mapping in agent.sdr_mappings:
        if mapping.get("enabled") is False:
            continue
        stage = str(mapping.get("stage") or "").strip()
        substage = str(mapping.get("substage") or "").strip()
        if not stage or not substage:
            continue
        steps.append(
            {
                "stepKey": _step_key(mapping),
                "pack": str(mapping.get("pack") or "sdr"),
                "stage": stage,
                "substage": substage,
                "role": str(mapping.get("role") or "") or None,
                "supportedRole": str(mapping.get("supportedRole") or "") or None,
                "artifactContract": str(mapping.get("artifactContract") or "") or None,
                "enabled": True,
            }
        )
    return steps


def _pick_active_step_key(steps: list[dict[str, Any]]) -> str | None:
    for step in steps:
        if step.get("supportedRole") == "primary":
            return str(step["stepKey"])
    if steps:
        return str(steps[0]["stepKey"])
    return None


def _public_artifact(artifact: dict[str, Any]) -> dict[str, Any]:
    return {
        "artifactId": artifact.get("artifactId"),
        "title": artifact.get("title"),
        "stepKey": artifact.get("stepKey"),
        "artifactContract": artifact.get("artifactContract"),
        "runId": artifact.get("runId"),
        "summary": artifact.get("summary"),
        "body": artifact.get("body") if isinstance(artifact.get("body"), dict) else {},
        "downloadAvailable": bool(
            artifact.get("downloadAvailable")
            or artifact.get("storageType") in {"external", "local", "inline"}
            or isinstance(artifact.get("body"), dict)
        ),
        "createdAt": artifact.get("createdAt"),
    }


def _freeze_skill_steps(skill: dict[str, Any]) -> list[dict[str, Any]]:
    source = skill.get("sourceContract") if isinstance(skill.get("sourceContract"), dict) else {}
    packs = [str(p).strip() for p in (skill.get("packs") or source.get("packs") or []) if str(p).strip()]
    if not packs:
        pack_one = str(source.get("pack") or "").strip()
        packs = [pack_one] if pack_one else ["sdr"]
    substages = [str(s).strip() for s in (skill.get("substages") or source.get("substages") or []) if str(s).strip()]
    if not substages:
        substages = ["skill_run"]
    stages = [str(s).strip() for s in (source.get("stages") or []) if str(s).strip()]
    stage = stages[0] if stages else "execution_validation"
    skill_id = str(skill.get("skillId") or "skill")
    steps: list[dict[str, Any]] = []
    for pack in packs:
        for index, substage in enumerate(substages):
            steps.append(
                {
                    "stepKey": f"{pack}:{substage}:skill:{skill_id}",
                    "pack": pack,
                    "stage": stage,
                    "substage": substage,
                    "role": "skill",
                    "supportedRole": "primary" if index == 0 else "supporting",
                    "artifactContract": "general_research_reasoning_artifact_v1",
                    "enabled": True,
                }
            )
    return steps


def _public_view(session: dict[str, Any]) -> dict[str, Any]:
    return {
        "sessionId": session["sessionId"],
        "title": session["title"],
        "status": session["status"],
        "resourceId": session["resourceId"],
        "resourceVersion": session["resourceVersion"],
        "agentId": session["agentId"],
        "agentDisplayName": session["agentDisplayName"],
        "sessionKind": str(session.get("sessionKind") or "agent"),
        "activeStepKey": session.get("activeStepKey"),
        "steps": list(session.get("steps") or []),
        "messages": list(session.get("messages") or []),
        "artifacts": [_public_artifact(item) for item in (session.get("artifacts") or [])],
        "createdAt": session["createdAt"],
        "updatedAt": session["updatedAt"],
    }


def create_agent_session(
    *,
    resource_id: str,
    owner_id: str,
    workspace_id: str,
) -> dict[str, Any]:
    rid = resource_id.strip()
    if not rid:
        raise AgentSessionError("invalid_resource", "resourceId is required")

    try:
        resource = get_resource_for_research_start(rid)
    except ResourceCatalogUnavailableError as exc:
        raise AgentSessionError("catalog_unavailable", str(exc)) from exc

    if resource is None:
        raise AgentSessionError("not_tryable", "resource is not tryable or not found")

    resource_type = str(resource.get("resourceType") or "").strip().lower()
    if resource_type == "agent":
        return _create_agent_resource_session(
            resource=resource, owner_id=owner_id, workspace_id=workspace_id
        )
    if resource_type == "skill":
        return _create_skill_resource_session(
            resource=resource, owner_id=owner_id, workspace_id=workspace_id
        )
    raise AgentSessionError(
        "unsupported_resource_type",
        "only agent and skill resources are supported",
    )


def _create_agent_resource_session(
    *,
    resource: dict[str, Any],
    owner_id: str,
    workspace_id: str,
) -> dict[str, Any]:
    agent_id = str(resource.get("capabilityId") or "").strip()
    if not agent_id:
        raise AgentSessionError("missing_capability", "catalog row lacks capabilityId")

    agent = get_registered_agent(agent_id)
    if agent is None or not agent.enabled:
        raise AgentSessionError("agent_unavailable", "registered agent is missing or disabled")

    steps = _freeze_steps(agent)
    if not steps:
        raise AgentSessionError("no_steps", "agent has no enabled sdr.mappings")

    title = str(resource.get("name") or agent.displayName).strip() or agent.displayName
    now = _utc_now()
    active = _pick_active_step_key(steps)
    session = {
        "sessionId": new_agent_session_id(),
        "ownerId": owner_id,
        "workspaceId": workspace_id,
        "title": title,
        "status": "ready",
        "sessionKind": "agent",
        "resourceId": str(resource["id"]),
        "resourceVersion": str(resource.get("releaseVersion") or ""),
        "agentId": agent_id,
        "agentDisplayName": title,
        "activeStepKey": active,
        "steps": steps,
        "messages": [
            {
                "messageId": new_message_id(),
                "role": "system",
                "content": f"你正在使用「{title}」。直接描述你的研究目标即可，无需确认研究方案。",
                "createdAt": now,
            }
        ],
        "artifacts": [],
        "createdAt": now,
        "updatedAt": now,
    }
    stored = _create_session(session)
    return _public_view(stored)


def _create_skill_resource_session(
    *,
    resource: dict[str, Any],
    owner_id: str,
    workspace_id: str,
) -> dict[str, Any]:
    from app.execution.durable_skill_runtime import resolve_registered_durable_skill

    skill_id = str(resource.get("capabilityId") or "").strip()
    if not skill_id:
        raise AgentSessionError("missing_capability", "catalog row lacks capabilityId")

    skill = resolve_registered_durable_skill(skill_id)
    if skill is None or not skill.get("enabled"):
        raise AgentSessionError("agent_unavailable", "registered skill is missing or disabled")
    if not skill.get("durableExecutable"):
        raise AgentSessionError("agent_unavailable", "skill has no durable executable runtime")

    steps = _freeze_skill_steps(skill)
    if not steps:
        raise AgentSessionError("no_steps", "skill has no pack/substage binding")

    title = str(resource.get("name") or skill.get("displayName") or skill_id).strip() or skill_id
    now = _utc_now()
    active = _pick_active_step_key(steps)
    session = {
        "sessionId": new_agent_session_id(),
        "ownerId": owner_id,
        "workspaceId": workspace_id,
        "title": title,
        "status": "ready",
        "sessionKind": "skill",
        "resourceId": str(resource["id"]),
        "resourceVersion": str(resource.get("releaseVersion") or ""),
        "agentId": skill_id,
        "agentDisplayName": title,
        "activeStepKey": active,
        "steps": steps,
        "messages": [
            {
                "messageId": new_message_id(),
                "role": "system",
                "content": f"你正在试用 Skill「{title}」。直接描述你的研究目标或输入即可，无需确认研究方案。",
                "createdAt": now,
            }
        ],
        "artifacts": [],
        "createdAt": now,
        "updatedAt": now,
    }
    stored = _create_session(session)
    return _public_view(stored)


def get_agent_session(*, session_id: str, owner_id: str, workspace_id: str) -> dict[str, Any] | None:
    row = _get_scoped_session(session_id, owner_id=owner_id, workspace_id=workspace_id)
    return _public_view(row) if row else None


def list_agent_sessions(*, owner_id: str, workspace_id: str, limit: int = 50) -> list[dict[str, Any]]:
    return _list_session_summaries(owner_id=owner_id, workspace_id=workspace_id, limit=limit)

def _demo_title_from_message(current_title: str, agent_display_name: str, content: str) -> str:
    """Title starts as agent display name; first user message becomes a short summary."""
    text = " ".join(content.strip().split())
    if not text:
        return current_title
    if current_title.strip() == agent_display_name.strip() or not current_title.strip():
        return text if len(text) <= 36 else f"{text[:36]}…"
    return current_title


def append_user_message(
    *,
    session_id: str,
    owner_id: str,
    workspace_id: str,
    content: str,
    request_id: str | None = None,
) -> dict[str, Any]:
    text = content.strip()
    if not text:
        raise AgentSessionError("empty_message", "content is required")

    row = _get_scoped_session(session_id, owner_id=owner_id, workspace_id=workspace_id)
    if row is None:
        raise AgentSessionError("not_found", "session not found")

    turn_requests = dict(row.get("turnRequests") or {})
    if request_id and turn_requests.get(request_id) == "completed":
        return _public_view(row)

    now = _utc_now()
    messages = list(row.get("messages") or [])
    if not request_id or request_id not in turn_requests:
        messages.append(
            {
                "messageId": new_message_id(),
                "role": "user",
                "content": text,
                "createdAt": now,
            }
        )
        if request_id:
            turn_requests[request_id] = "running"
    row["messages"] = messages
    row["turnRequests"] = turn_requests
    row["status"] = "running"
    row["title"] = _demo_title_from_message(
        str(row.get("title") or ""),
        str(row.get("agentDisplayName") or ""),
        text,
    )
    row["updatedAt"] = now
    _replace_session(row)

    try:
        turn = run_agent_session_turn(
            session=row,
            user_goal=text,
            owner_id=owner_id,
            workspace_id=workspace_id,
        )
    except AgentSessionExecutionError as exc:
        fail_now = _utc_now()
        messages.append(
            {
                "messageId": new_message_id(),
                "role": "assistant",
                "content": _turn_failure_message(exc),
                "createdAt": fail_now,
            }
        )
        row["messages"] = messages
        row["status"] = "failed" if exc.code in {
            "agent_unavailable",
            "artifact_schema_missing",
            "artifact_contract_missing",
            "provider_configuration_unavailable",
        } else "waiting_input"
        row["updatedAt"] = fail_now
        if request_id:
            turn_requests[request_id] = "completed"
            row["turnRequests"] = turn_requests
        stored = _replace_session(row)
        logger.warning(
            "agent_session_turn_failed sessionId=%s code=%s",
            session_id,
            exc.code,
        )
        return _public_view(stored)

    done_at = _utc_now()
    messages.append(
        {
            "messageId": new_message_id(),
            "role": "assistant",
            "content": str(turn.get("assistantMessage") or "已完成执行。"),
            "createdAt": done_at,
        }
    )
    artifact_id = new_artifact_id()
    body = turn.get("body") if isinstance(turn.get("body"), dict) else {"raw": turn.get("body")}
    title = str(turn.get("title") or "会话产物")
    storage_meta = _store_session_artifact_bytes(
        session_id=str(row["sessionId"]),
        artifact_id=artifact_id,
        body=body if isinstance(body, dict) else {"raw": body},
        title=title,
    )
    artifact_row = {
        "artifactId": artifact_id,
        "title": title,
        "stepKey": turn.get("stepKey"),
        "artifactContract": turn.get("artifactContract"),
        "summary": str(turn.get("summary") or text),
        "body": body if isinstance(body, dict) else {"raw": body},
        "runId": turn.get("runId"),
        "createdAt": done_at,
        **storage_meta,
    }
    artifacts = list(row.get("artifacts") or [])
    artifacts.append(artifact_row)
    row["messages"] = messages
    row["artifacts"] = artifacts
    row["status"] = "ready"
    if request_id:
        turn_requests[request_id] = "completed"
        row["turnRequests"] = turn_requests
    row["updatedAt"] = done_at
    stored = _replace_session(row)
    return _public_view(stored)
