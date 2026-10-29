"""Task event delivery and persistent user-safe research activity projection.

The authoritative Task/Run/collaboration aggregates remain the scientific fact
source.  ``openair_task_stream_events`` stores only a sanitized presentation
projection that can be replayed safely. PostgreSQL LISTEN/NOTIFY is a wake-up
mechanism; event bodies are always read from the table.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Iterator, Mapping
from uuid import uuid4

try:  # Optional at import time so JSON-backend unit tests stay pure.
    import psycopg
    from psycopg.rows import dict_row
except ModuleNotFoundError:  # pragma: no cover - production installs psycopg[binary]
    psycopg = None  # type: ignore[assignment]
    dict_row = None  # type: ignore[assignment]

from app.config import settings
from app.modules.research_blocker_semantics import blocker_kind

_TASK_STREAM_CHANNEL = "openair_task_stream_events"
_MAX_HEADLINE = 160
_MAX_SUMMARY = 500
_MAX_REFS = 20
_SAFE_INVALIDATION_SCOPES = {
    "task",
    "collaboration",
    "artifact",
    "review",
    "recovery",
    "controller_command",
}
_UNSAFE_KEY_PATTERN = re.compile(
    r"(?:chain.?of.?thought|reasoning|hidden.?token|prompt|system.?prompt|developer.?prompt|"
    r"authorization|auth.?header|api.?key|secret|password|access.?token|refresh.?token|"
    r"tool.?arguments?|shell.?output|stack.?trace|raw.?request|raw.?response)",
    re.IGNORECASE,
)
_UNSAFE_VALUE_PATTERN = re.compile(
    r"(?:bearer\s+[a-z0-9._~+/=-]{8,}|sk-[a-z0-9_-]{12,}|-----BEGIN [A-Z ]+PRIVATE KEY-----)",
    re.IGNORECASE,
)

_LEGACY_CAPABILITY_ACTIVITY_LABELS = {
    "intent_understanding": "研究意图梳理",
    "question_refinement": "研究问题梳理",
    "literature_search": "学术检索",
    "source_screening": "资料筛选",
    "evidence_extraction": "证据提取",
    "gap_identification": "研究空缺识别",
    "variable_definition": "变量定义",
    "baseline_selection": "基线设计",
    "validation_design": "验证方案设计",
    "ablation_design": "消融实验设计",
    "analysis_planning": "分析方案设计",
    "result_audit": "结果可靠性核查",
}
_LEGACY_AGENT_ACTIVITY_LABELS = {
    "agent_question_refinement_v0_3": "研究问题凝练 Agent",
    "agent_literature_research_v0_3": "关键文献定位 Agent",
    "agent_evidence_extraction_v0_3": "论文证据抽取 Agent",
    "agent_gap_identification_v0_3": "研究空缺识别 Agent",
    "agent_validation_design_v0_3": "验证方案设计 Agent",
    "agent_result_audit_v0_3": "结果可靠性审计 Agent",
    "agent_work_plan_v0_3": "研究推进计划 Agent",
    "agent_summary_v0_3": "总结 Agent",
    "agent_review_v0_3": "复核 Agent",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _postgres_conninfo() -> str:
    return (
        f"host={settings.postgres_host} port={settings.postgres_port} "
        f"dbname={settings.postgres_database} user={settings.postgres_user}"
    )


@dataclass
class TaskEventSubscription:
    id: str
    task_id: str
    loop: asyncio.AbstractEventLoop
    queue: asyncio.Queue[dict[str, Any]]


_lock = threading.Lock()
_subscribers: dict[str, dict[str, TaskEventSubscription]] = {}
_sequence = 0


def subscribe_task_events(task_id: str, *, max_queue_size: int = 100) -> TaskEventSubscription:
    """Legacy/in-memory wake-up used by the JSON backend and compatibility SSE."""
    subscription = TaskEventSubscription(
        id=uuid4().hex,
        task_id=task_id,
        loop=asyncio.get_running_loop(),
        queue=asyncio.Queue(maxsize=max_queue_size),
    )
    with _lock:
        _subscribers.setdefault(task_id, {})[subscription.id] = subscription
    return subscription


def unsubscribe_task_events(subscription: TaskEventSubscription) -> None:
    with _lock:
        subscribers = _subscribers.get(subscription.task_id)
        if not subscribers:
            return
        subscribers.pop(subscription.id, None)
        if not subscribers:
            _subscribers.pop(subscription.task_id, None)


def publish_task_event(
    task_id: str,
    event_type: str,
    *,
    updated_at: str | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    """Publish a compatibility wake-up event.

    This function intentionally does not persist arbitrary caller payloads. The
    durable research stream is materialized from authoritative repositories and
    stored through :class:`TaskResearchEventRepository` after allow-listing.
    """
    global _sequence
    with _lock:
        subscribers = list(_subscribers.get(task_id, {}).values())
        if not subscribers:
            return
        _sequence += 1
        event = {
            "id": f"evt_{_sequence}",
            "type": event_type,
            "taskId": task_id,
            "updatedAt": updated_at,
            "emittedAt": _utc_now(),
            "payload": payload or {},
        }

    for subscription in subscribers:
        def deliver(sub: TaskEventSubscription = subscription, item: dict[str, Any] = event) -> None:
            if sub.queue.full():
                try:
                    sub.queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            sub.queue.put_nowait(item)

        subscription.loop.call_soon_threadsafe(deliver)


@dataclass(frozen=True)
class ResearchEventCursor:
    sequence: int
    expired: bool = False


class PostgresTaskEventListener:
    """Dedicated, scope-filtered LISTEN connection established before replay."""

    def __init__(
        self,
        *,
        conninfo: str,
        password: str,
        owner_id: str,
        workspace_id: str,
        task_id: str,
    ) -> None:
        if psycopg is None:
            raise RuntimeError("psycopg is required for the PostgreSQL research stream")
        self._connection = psycopg.connect(conninfo, password=password, autocommit=True)
        self._connection.execute(f"LISTEN {_TASK_STREAM_CHANNEL}")
        self._scope = (owner_id, workspace_id, task_id)
        self._closed = False

    def wait(self, timeout: float) -> bool:
        if self._closed:
            return False
        deadline = time.monotonic() + max(0.0, timeout)
        while not self._closed:
            remaining = max(0.0, deadline - time.monotonic())
            notification = next(
                self._connection.notifies(timeout=remaining, stop_after=1),
                None,
            )
            if notification is None:
                return False
            try:
                payload = json.loads(notification.payload)
            except (TypeError, ValueError):
                continue
            scope = (
                str(payload.get("ownerId") or ""),
                str(payload.get("workspaceId") or ""),
                str(payload.get("taskId") or ""),
            )
            if scope == self._scope:
                return True
            if time.monotonic() >= deadline:
                return False
        return False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._connection.close()


class TaskResearchEventRepository:
    """Persistent, scoped, immutable user-safe research activity projection."""

    def __init__(self, *, conninfo: str | None = None, password: str | None = None) -> None:
        self._conninfo = conninfo or _postgres_conninfo()
        self._password = settings.postgres_password if password is None else password

    @contextmanager
    def _connect(self) -> Iterator[psycopg.Connection[Any]]:
        if psycopg is None:
            raise RuntimeError("psycopg is required for the PostgreSQL research stream")
        conn = psycopg.connect(
            self._conninfo,
            password=self._password,
            row_factory=dict_row,
        )
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def subscribe(
        self, *, owner_id: str, workspace_id: str, task_id: str,
    ) -> PostgresTaskEventListener:
        return PostgresTaskEventListener(
            conninfo=self._conninfo,
            password=self._password,
            owner_id=owner_id,
            workspace_id=workspace_id,
            task_id=task_id,
        )

    def append_many(
        self,
        *,
        owner_id: str,
        workspace_id: str,
        task_id: str,
        source_identity: str,
        events: Iterable[Mapping[str, Any]],
    ) -> int:
        inserted = 0
        with self._connect() as conn:
            with conn.cursor() as cur:
                # Logical retention is enforced by expires_at; bounded opportunistic
                # pruning prevents dead rows from accumulating without a cron job.
                cur.execute(
                    """
                    DELETE FROM openair_task_stream_events
                    WHERE event_sequence IN (
                        SELECT event_sequence
                        FROM openair_task_stream_events
                        WHERE expires_at <= NOW()
                        ORDER BY event_sequence
                        LIMIT 1000
                    )
                    """
                )
                for raw in events:
                    event = validate_research_activity_event(raw)
                    source_event_id = str(event["sourceEventId"])
                    event_source_identity = _research_event_source_identity(
                        task_id=task_id,
                        source_identity=source_identity,
                        event=event,
                    )
                    event_id = _stable_event_id(
                        owner_id=owner_id,
                        workspace_id=workspace_id,
                        task_id=task_id,
                        source_identity=event_source_identity,
                        source_event_id=source_event_id,
                    )
                    cur.execute(
                        """
                        INSERT INTO openair_task_stream_events (
                            event_id, source_event_id, owner_id, workspace_id,
                            task_id, source_identity, event_type,
                            presentation_level, work_unit_id, headline, summary,
                            invalidate_scopes, payload, occurred_at
                        ) VALUES (
                            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                            %s::jsonb, %s::jsonb, %s::timestamptz
                        )
                        ON CONFLICT (owner_id, workspace_id, task_id, source_identity, source_event_id)
                        DO NOTHING
                        """,
                        (
                            event_id,
                            source_event_id,
                            owner_id,
                            workspace_id,
                            task_id,
                            event_source_identity,
                            event["eventType"],
                            event["presentationLevel"],
                            event.get("workUnitId"),
                            event["headline"],
                            event.get("summary"),
                            json.dumps(event["invalidateScopes"], ensure_ascii=False),
                            json.dumps(event.get("payload") or {}, ensure_ascii=False),
                            event["occurredAt"],
                        ),
                    )
                    inserted += int(cur.rowcount == 1)
        return inserted

    def cursor(
        self,
        *,
        owner_id: str,
        workspace_id: str,
        task_id: str,
        source_identity: str,
        event_id: str | None,
    ) -> ResearchEventCursor:
        if not event_id:
            return ResearchEventCursor(sequence=0)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT event_sequence
                    FROM openair_task_stream_events
                    WHERE owner_id=%s AND workspace_id=%s AND task_id=%s
                      AND source_identity IN (%s, %s)
                      AND event_id=%s AND expires_at > NOW()
                    """,
                    (
                        owner_id,
                        workspace_id,
                        task_id,
                        source_identity,
                        _planning_source_identity(task_id),
                        event_id,
                    ),
                )
                row = cur.fetchone()
                if row:
                    return ResearchEventCursor(sequence=int(row["event_sequence"]))
        # Any supplied cursor that is absent from the retained, scoped window
        # requires a reset, including the case where every older row expired.
        return ResearchEventCursor(sequence=0, expired=True)

    def list_after(
        self,
        *,
        owner_id: str,
        workspace_id: str,
        task_id: str,
        source_identity: str,
        sequence: int,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        bounded_limit = max(1, min(limit, 500))
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT event_sequence, event_id, event_type,
                           presentation_level, work_unit_id, headline, summary,
                           invalidate_scopes, payload, occurred_at, source_identity
                    FROM openair_task_stream_events
                    WHERE owner_id=%s AND workspace_id=%s AND task_id=%s
                      AND source_identity IN (%s, %s)
                      AND event_sequence > %s AND expires_at > NOW()
                    ORDER BY event_sequence ASC
                    LIMIT %s
                    """,
                    (
                        owner_id,
                        workspace_id,
                        task_id,
                        source_identity,
                        _planning_source_identity(task_id),
                        sequence,
                        bounded_limit,
                    ),
                )
                rows = cur.fetchall()
        return [
            {
                "id": str(row["event_id"]),
                "type": str(row["event_type"]),
                "taskId": task_id,
                "sequence": int(row["event_sequence"]),
                "sourceIdentity": str(row["source_identity"]),
                "occurredAt": _iso_value(row["occurred_at"]),
                "payload": {
                    "presentationLevel": str(row["presentation_level"]),
                    "workUnitId": row["work_unit_id"],
                    **_readable_activity_copy(str(row["headline"]), row["summary"]),
                    "invalidateScopes": list(row["invalidate_scopes"] or []),
                    **dict(row["payload"] or {}),
                },
            }
            for row in rows
        ]


def _planning_source_identity(task_id: str) -> str:
    return f"task:{task_id}:planning"


def _research_event_source_identity(
    *, task_id: str, source_identity: str, event: Mapping[str, Any],
) -> str:
    payload = event.get("payload") if isinstance(event.get("payload"), Mapping) else {}
    activity_key = str(payload.get("activityKey") or "")
    # Planning/design facts belong to the Task, not to an execution decision.
    # Keep their storage identity stable across pre-run -> run transitions while
    # leaving all execution events fenced by the existing run source identity.
    if activity_key.startswith(("planning-attempt:", "planning-thought:", "research-design:")):
        return _planning_source_identity(task_id)
    return source_identity


def _stable_event_id(
    *, owner_id: str, workspace_id: str, task_id: str, source_identity: str, source_event_id: str,
) -> str:
    digest = hashlib.sha256(
        "\x1f".join((owner_id, workspace_id, task_id, source_identity, source_event_id)).encode("utf-8")
    ).hexdigest()
    return f"tre_{digest[:32]}"


def _iso_value(value: Any) -> str:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return str(value or "")


def _safe_text(value: Any, *, limit: int) -> str | None:
    text = " ".join(str(value or "").split()).strip()
    if not text:
        return None
    if _UNSAFE_VALUE_PATTERN.search(text):
        raise ValueError("research activity contains a credential-like value")
    return text[:limit]


def _safe_refs(value: Any) -> list[str]:
    refs: list[str] = []
    for item in value or []:
        text = _safe_text(item, limit=160)
        if text and text not in refs:
            refs.append(text)
        if len(refs) >= _MAX_REFS:
            break
    return refs


def _explicit_activity_label(payload: Mapping[str, Any]) -> str | None:
    """Prefer trusted presentation metadata, including user-provided names."""
    for key in ("userFacingLabel", "userProvidedName"):
        label = _safe_text(payload.get(key), limit=120)
        if label:
            return label
    return None


def _registry_activity_label(identifier: str, *, agent: bool) -> str | None:
    """Resolve built-ins from registries without exposing registry identifiers."""
    lookup_id = identifier.rsplit(":", 1)[-1]
    try:
        if agent:
            from app.modules.agent_registry import load_agent_registry

            registry = load_agent_registry()
            registered = registry.get(identifier) or registry.get(lookup_id)
            label = _safe_text(getattr(registered, "displayName", None), limit=120) if registered else None
        else:
            from app.modules.task_execution_graph import _find_skill_entry

            entry = _find_skill_entry(lookup_id)
            label = _safe_text(entry.get("display_name"), limit=120) if entry else None
    except Exception:
        # Presentation must fail closed: registry read failures never expose raw ids.
        return None
    if not label or label in {identifier, lookup_id}:
        return None
    return label


def _activity_label(payload: Mapping[str, Any], *, agent: bool = False) -> str | None:
    """Return a research-facing name; never fall back to a raw capability identifier."""
    label = _explicit_activity_label(payload)
    if label:
        return label
    identifier = _safe_text(
        payload.get("capabilityId")
        or payload.get("skillId")
        or payload.get("agentId")
        or payload.get("executorId")
        or payload.get("targetAgent"),
        limit=160,
    )
    if not identifier:
        return None
    label = _registry_activity_label(identifier, agent=agent)
    if label:
        return label
    mapping = _LEGACY_AGENT_ACTIVITY_LABELS if agent else _LEGACY_CAPABILITY_ACTIVITY_LABELS
    if identifier in mapping:
        return mapping[identifier]
    # Keep only curated compatibility aliases for old durable facts. Unknown
    # registry/capability keys stay hidden or use the neutral activity headline.
    return mapping.get(identifier.rsplit(":", 1)[-1])


def _activity_key(payload: Mapping[str, Any], source_event_id: str) -> str:
    raw = str(payload.get("callId") or payload.get("capabilityCallId") or source_event_id)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _projection_source_id(source_event_id: str) -> str:
    # Version the presentation projection without changing transport identity.
    # Existing v1 rows remain replayable; v2 rows can be appended once because
    # durable persistence is intentionally insert-only.
    return f"research-activity-v2:{source_event_id}"


def validate_research_activity_event(value: Mapping[str, Any]) -> dict[str, Any]:
    """Allow-list one event before it enters the persistent user stream."""
    for key in value:
        if _UNSAFE_KEY_PATTERN.search(str(key)):
            raise ValueError(f"unsafe research activity field: {key}")
    source_event_id = _safe_text(value.get("sourceEventId"), limit=300)
    event_type = _safe_text(value.get("eventType"), limit=80)
    headline = _safe_text(value.get("headline"), limit=_MAX_HEADLINE)
    occurred_at = _safe_text(value.get("occurredAt"), limit=80)
    if not source_event_id or not event_type or not headline or not occurred_at:
        raise ValueError("research activity is missing a required safe field")
    level = "detail" if value.get("presentationLevel") == "detail" else "primary"
    scopes = [
        str(scope)
        for scope in value.get("invalidateScopes") or []
        if str(scope) in _SAFE_INVALIDATION_SCOPES
    ]
    payload = value.get("payload") if isinstance(value.get("payload"), Mapping) else {}
    safe_payload: dict[str, Any] = {}
    if "artifactRefs" in payload:
        safe_payload["artifactRefs"] = _safe_refs(payload.get("artifactRefs"))
    if "aggregateCount" in payload:
        count = payload.get("aggregateCount")
        if isinstance(count, int) and 0 <= count <= 100000:
            safe_payload["aggregateCount"] = count
    if "activityKey" in payload:
        activity_key = _safe_text(payload.get("activityKey"), limit=80)
        if activity_key:
            safe_payload["activityKey"] = activity_key
    if "phase" in payload:
        phase = _safe_text(payload.get("phase"), limit=40)
        if phase:
            safe_payload["phase"] = phase
    return {
        "sourceEventId": source_event_id,
        "eventType": event_type,
        "presentationLevel": level,
        "workUnitId": _safe_text(value.get("workUnitId"), limit=200),
        "headline": headline,
        "summary": _safe_text(
            value.get("summary"),
            limit=4000 if str(payload.get("activityKey") or "").startswith("planning-thought:")
            else _MAX_SUMMARY,
        ),
        "invalidateScopes": sorted(set(scopes)),
        "payload": safe_payload,
        "occurredAt": occurred_at,
    }


def _interrupted_activity_copy(payload: Mapping[str, Any]) -> tuple[str, str]:
    """Activity is descriptive; coarse status never assigns input ownership."""
    status = str(payload.get("status") or "")
    reason = payload.get("reasonCode") or payload.get("terminalReasonCode") or payload.get("errorCode")
    kind = blocker_kind(reason, status=status)
    if kind == "external_result_unknown":
        return "正在核对外部执行结果", "执行可能已经开始；系统会先核对原执行，避免重复提交。你现在无需操作。"
    if kind == "user_input":
        return "当前步骤需要明确输入或确认", "请查看任务面板中的具体要求和可用操作；已有成果保留。"
    if kind == "external_dependency":
        return "正在等待前置结果或外部条件", "后续工作暂未继续，由系统协调；你现在无需操作。"
    if not reason:
        headline = "当前步骤等待必要信息确认" if status == "needs_input" else "当前研究步骤暂未继续"
        return headline, "系统正在确认中断原因；已有成果保留，是否需要你操作以任务面板中的具体要求为准。"
    return "当前研究步骤未能继续", "系统负责核对和处理当前执行条件；已有成果保留。你可重试，或在下方告诉研究助手你的需求。"


def _readable_activity_copy(headline: str, summary: str | None) -> dict[str, Any]:
    """Correct prior coarse presentation on read while retaining event identity.

    Stored events remain immutable. These exact historical display strings did
    not contain evidence of responsibility and cannot override today's Task.
    """
    if headline in {"正在等待用户处理", "正在等待依赖"}:
        return {"headline": "当前研究步骤暂未继续",
                "summary": "请查看当前任务说明；已有成果保留，是否需要你操作以已确认的具体要求为准。"}
    if summary == "系统正在检查是否可以自动重试或继续。":
        return {"headline": headline, "summary": "系统正在核对中断原因；已有成果保留，可用操作以当前任务说明为准。"}
    return {"headline": headline, "summary": summary}


def project_research_activity_events(lifecycle_events: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Project durable lifecycle facts to append-friendly, user-safe stream events.

    The projector keeps distinct WorkUnit facts distinct. Repeated lifecycle
    updates for one capability call carry a stable ``activityKey`` so the
    client can replace only that low-value status transition without erasing
    other research process history.
    """
    lifecycle = [event for event in lifecycle_events if isinstance(event, Mapping)]
    projected: list[dict[str, Any]] = []

    for event in lifecycle:
        event_type = str(event.get("type") or "")
        payload = event.get("payload") if isinstance(event.get("payload"), Mapping) else {}
        source_event_id = str(event.get("id") or "")
        occurred_at = str(event.get("occurredAt") or event.get("emittedAt") or "")
        work_unit_id = str(payload.get("workUnitId") or "") or None
        phase = str(payload.get("phase") or "")
        status = str(payload.get("status") or "")
        if not source_event_id or not occurred_at:
            continue

        if event_type == "skill_lifecycle":
            label = _activity_label(payload)
            if not label:
                # Unknown/internal capability identifiers are intentionally not
                # surfaced as raw engineering names.
                continue
            if phase in {"scheduled", "queued", "started", "running"}:
                headline = f"正在使用{label}"
            elif phase in {"completed", "succeeded"}:
                headline = f"{label}已完成"
            elif phase in {"failed", "blocked", "stopped"}:
                headline = f"{label}需要核对"
            else:
                continue
            projected.append({
                "sourceEventId": _projection_source_id(source_event_id),
                "eventType": "research_tool_summary",
                "presentationLevel": "detail",
                "workUnitId": work_unit_id,
                "headline": headline,
                "summary": None,
                "invalidateScopes": ["collaboration"],
                "payload": {
                    "activityKey": _activity_key(payload, source_event_id),
                    "phase": phase,
                    "artifactRefs": payload.get("outputArtifactRefs") or payload.get("artifactRefs") or [],
                },
                "occurredAt": occurred_at,
            })
            continue

        headline: str | None = None
        summary: str | None = None
        activity_key: str | None = None
        scopes = ["collaboration"]
        safe_type = "research_progress"

        if event_type == "planning_thought":
            from app.modules.research_start_thinking import public_thinking_text
            summary = public_thinking_text(str(payload.get("text") or ""))
            if not summary:
                continue
            safe_type = "meaningful_progress"
            headline = "方案思考"
            activity_key = str(payload.get("activityKey") or "")
        elif event_type == "planning_lifecycle":
            safe_type = "meaningful_progress"
            planning_attempt_id = str(payload.get("planningAttemptId") or "").strip()
            if not planning_attempt_id:
                continue
            activity_key = f"planning-attempt:{planning_attempt_id}"
            if phase == "started":
                headline = "正在组织研究方案"
            elif phase == "failed":
                headline = "本次研究方案生成未完成"
            else:
                continue
        elif event_type == "research_design_lifecycle":
            safe_type = "meaningful_progress"
            research_design_id = str(payload.get("researchDesignId") or "").strip()
            design_version = payload.get("designVersion")
            if not research_design_id or not isinstance(design_version, int) or design_version < 1:
                continue
            activity_key = f"research-design:{research_design_id}:v{design_version}"
            if phase == "formed":
                headline = "研究方案已形成" if design_version == 1 else f"研究方案已更新至第 {design_version} 版"
                scopes.append("task")
            elif phase == "confirmed":
                headline = (
                    "研究方案已确认，准备进入执行"
                    if design_version == 1
                    else f"研究方案第 {design_version} 版已确认，准备进入执行"
                )
                scopes.append("task")
            else:
                continue
        elif event_type == "orchestrator_lifecycle" and phase in {"scheduled", "started"}:
            headline = "研究目标已经开始" if phase == "started" else "研究目标已进入执行准备"
        elif event_type == "agent_lifecycle" and phase == "started":
            agent_label = _activity_label(payload, agent=True)
            headline = f"{agent_label} 开始工作" if agent_label else "研究步骤开始"
        elif event_type == "agent_lifecycle" and phase in {"completed", "failed", "blocked", "stopped"}:
            agent_label = _activity_label(payload, agent=True)
            safe_type = "meaningful_progress"
            if status in {"needs_input", "blocked", "waiting", "blocked_recoverable"}:
                headline, summary = _interrupted_activity_copy(payload)
                scopes.append("task")
            elif status in {"failed", "external_result_unknown", "stopped", "stopped_by_user"}:
                if status == "failed":
                    headline, summary = _interrupted_activity_copy(payload)
                elif status == "external_result_unknown":
                    headline = "正在核对外部执行结果"
                    summary = "结果暂时无法确认；系统会先核对，避免重复执行。"
                else:
                    headline = f"{agent_label} 已停止" if agent_label else "当前研究步骤已停止"
                    summary = "系统正在更新当前任务状态。"
                scopes.extend(["task", "recovery"])
            elif status in {"completed", "succeeded"} and phase == "completed":
                headline = f"{agent_label} 完成当前工作" if agent_label else "当前研究步骤已完成"
            elif phase == "stopped" or status == "cancelled":
                headline = "当前研究步骤已停止"
                summary = "已有成果保留；停止原因和后续操作以当前任务说明为准。"
            else:
                headline = "当前研究步骤暂未继续"
                summary = "系统正在核对当前执行事实；已有成果保留，是否需要你操作以任务面板的具体要求为准。"
        elif event_type == "handoff_completed":
            safe_type = "meaningful_progress"
            headline = "工作已经交接"
        elif event_type == "mandatory_review_lifecycle":
            if phase in {"assigned", "started"}:
                headline = "复核已经开始"
            else:
                safe_type = "meaningful_progress"
                headline = "复核已经结束，正在核对结论"
            summary = "复核结论将以最新任务状态为准。"
            scopes.extend(["review", "task"])
        elif event_type == "artifact_persisted":
            safe_type = "meaningful_progress"
            headline = "产物已经形成，正在核对可用性"
            scopes.extend(["artifact", "task"])
        elif event_type == "execution_terminal":
            safe_type = "meaningful_progress"
            headline = "本轮执行已结束"
            summary = "系统已更新任务状态；已经完成的研究内容和产物会保留。"
            scopes.extend(["task", "artifact", "review", "recovery"])
        elif status in {"needs_input", "blocked", "waiting", "blocked_recoverable"}:
            headline, summary = _interrupted_activity_copy(payload)
            scopes.append("task")
        if not headline:
            continue

        projected.append({
            "sourceEventId": _projection_source_id(source_event_id),
            "eventType": safe_type,
            "presentationLevel": "primary",
            "workUnitId": work_unit_id,
            "headline": headline,
            "summary": summary,
            "invalidateScopes": scopes,
            "payload": {
                "phase": phase,
                "artifactRefs": payload.get("outputArtifactRefs") or payload.get("artifactRefs") or [],
                **({"activityKey": activity_key} if activity_key else {}),
            },
            "occurredAt": occurred_at,
        })

    return [validate_research_activity_event(event) for event in projected]
