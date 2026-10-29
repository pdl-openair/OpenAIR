"""Durable, body-free physical execution truth for production Provider I/O.

Every real Provider submission receives its own durable physical-attempt
identity. Logical retries, parent workflow identity, operation identity,
transport certainty, and local processing failures remain separate facts.
"""

from __future__ import annotations

import hashlib
import json
import re
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Iterator, Literal, Mapping

import httpx
from urllib.parse import urlsplit
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row

from app.config import settings


class ProviderAttemptEvidenceError(RuntimeError):
    """The physical Provider attempt cannot be durably attributed."""


class DuplicateProviderAttemptError(ProviderAttemptEvidenceError):
    """A physical submission was blocked by replay/permanence rules."""




@dataclass(frozen=True)
class ProviderExecutionParent:
    kind: Literal["capability_call", "research_start", "model_job", "agent_session", "system_probe"]
    parent_id: str
    originating_authority: str


_CURRENT_PROVIDER_EXECUTION_PARENT: ContextVar[ProviderExecutionParent | None] = ContextVar(
    "openair_provider_execution_parent", default=None
)


@contextmanager
def provider_execution_parent(
    *,
    kind: Literal["capability_call", "research_start", "model_job", "agent_session", "system_probe"],
    parent_id: str,
    originating_authority: str,
    only_if_absent: bool = False,
) -> Iterator[ProviderExecutionParent]:
    """Bind the durable owner of physical Provider I/O for nested production code."""
    normalized_id = str(parent_id or "").strip()
    authority = str(originating_authority or "").strip()
    if not normalized_id or not authority:
        raise ProviderAttemptEvidenceError("provider_execution_parent_invalid")
    current = _CURRENT_PROVIDER_EXECUTION_PARENT.get()
    if only_if_absent and current is not None:
        yield current
        return
    parent = ProviderExecutionParent(kind=kind, parent_id=normalized_id, originating_authority=authority)
    token = _CURRENT_PROVIDER_EXECUTION_PARENT.set(parent)
    try:
        yield parent
    finally:
        _CURRENT_PROVIDER_EXECUTION_PARENT.reset(token)


def current_provider_execution_parent() -> ProviderExecutionParent | None:
    return _CURRENT_PROVIDER_EXECUTION_PARENT.get()

# Evidence kinds distinguish a primary Provider submission from child physical
# calls. Uniqueness is owned by physical attempt identity, not logical-call count.
EVIDENCE_KIND_PROVIDER_SUBMISSION = "provider_submission"
EVIDENCE_KIND_MCP_CALL = "mcp_call"
EVIDENCE_KIND_SUB_CALL = "sub_call"
_VALID_EVIDENCE_KINDS = frozenset(
    {EVIDENCE_KIND_PROVIDER_SUBMISSION, EVIDENCE_KIND_MCP_CALL, EVIDENCE_KIND_SUB_CALL}
)
_DEFAULT_CHECKPOINT_REPOSITORY = object()


_SECRET_VALUE_PATTERNS = (
    re.compile(r"(?i)\b(bearer\s+)[^\s,;]+"),
    re.compile(r"(?i)\b(api[_ -]?key|token|secret|password)\s*([=:])\s*[^\s,;]+"),
)


def _safe_rejection_diagnostics(request_body: bytes, response_body: bytes | None) -> dict[str, Any]:
    """Extract only bounded provider-error and request-shape facts; never bodies."""
    try:
        request = json.loads(request_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        request = {}
    if not isinstance(request, Mapping):
        request = {}
    response: Mapping[str, Any] = {}
    if response_body:
        try:
            parsed = json.loads(response_body.decode("utf-8"))
            response = parsed if isinstance(parsed, Mapping) else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            pass
    error = response.get("error")
    error = error if isinstance(error, Mapping) else response
    def clean(value: Any, limit: int = 300) -> str | None:
        if not isinstance(value, str):
            return None
        text = " ".join(value.split())
        for pattern in _SECRET_VALUE_PATTERNS:
            if pattern.groups == 1:
                text = pattern.sub(r"\1[REDACTED]", text)
            else:
                text = pattern.sub(r"\1\2[REDACTED]", text)
        text = text[:limit]
        return text or None
    response_format = request.get("response_format")
    mode = response_format.get("type") if isinstance(response_format, Mapping) else None
    messages = request.get("messages")
    return {
        "providerErrorType": clean(error.get("type")),
        "providerErrorCode": clean(error.get("code"), 120),
        "providerErrorMessageSummary": clean(error.get("message")),
        "requestShape": {
            "modelIdentity": clean(request.get("model"), 120),
            "parameterKeys": sorted(
                key for key in request.keys()
                if isinstance(key, str) and len(key) <= 120 and key.isprintable()
            )[:32],
            "responseFormatMode": clean(mode, 80),
            "hasMaxTokens": "max_tokens" in request,
            "hasMaxCompletionTokens": "max_completion_tokens" in request,
            "hasTools": bool(request.get("tools")),
            "hasToolChoice": "tool_choice" in request,
            "stream": request.get("stream") if isinstance(request.get("stream"), bool) else None,
            "messageCount": len(messages) if isinstance(messages, list) else None,
        },
    }


@dataclass(frozen=True)
class ProviderExecutionObservation:
    """Durable observation of one physical Provider submission.

    ``attempt_id`` is the unique physical-attempt identity. ``attempt_number``
    is the physical ordinal under ``parent_kind``/``parent_id``. Logical retry
    identity and failure provenance remain separate facts.
    """

    attempt_id: str
    capability_call_id: str | None
    attempt_number: int
    state: Literal["running", "completed", "failed", "cancelled", "unknown"]
    provider_request_id: str | None
    provider_response_id: str | None
    http_status: int | None
    transport_outcome: str
    parent_kind: str = "capability_call"
    parent_id: str | None = None
    operation_identity: str = "provider_submission"
    logical_attempt_ordinal: int = 1
    reason: str | None = None
    stage: str | None = None
    originating_authority: str | None = None
    io_started: bool | None = None
    submission_proven: bool | None = None


@dataclass(frozen=True)
class ProviderHttpAttempt:
    attempt_id: str
    capability_call_id: str
    attempt_number: int
    request_hash: str
    provider_endpoint_identity: str
    evidence_kind: str = EVIDENCE_KIND_PROVIDER_SUBMISSION
    adapter_name: str | None = None
    parent_kind: str = "capability_call"
    parent_id: str | None = None
    operation_identity: str = "provider_submission"
    logical_attempt_ordinal: int = 1
    recovered_response_body: bytes | None = None
    recovered_http_status: int | None = None
    recovered_response_headers: Mapping[str, Any] | None = None

    @property
    def headers(self) -> dict[str, str]:
        headers = {
            "X-OpenAIR-Attempt-Id": self.attempt_id,
            "X-OpenAIR-Attempt-Number": str(self.attempt_number),
            "X-OpenAIR-Request-Hash": self.request_hash,
            "X-OpenAIR-Execution-Parent": f"{self.parent_kind}:{self.parent_id or self.capability_call_id}",
        }
        if self.capability_call_id:
            headers["X-OpenAIR-Capability-Call-Id"] = self.capability_call_id
        return headers


def safe_provider_endpoint_identity(endpoint: str, adapter: str) -> str:
    """Return a stable endpoint identity without persisting credentials or query data."""
    parts = urlsplit(endpoint)
    normalized = f"{parts.scheme.lower()}://{(parts.hostname or '').lower()}:{parts.port or _default_port(parts.scheme)}{parts.path}"
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return f"{adapter}:sha256:{digest}"


def request_sha256(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _execution_policy_json(value: Mapping[str, Any] | None) -> str | None:
    if value is None:
        return None
    payload = json.dumps(dict(value), ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    if len(payload.encode("utf-8")) > 4096:
        raise ProviderAttemptEvidenceError("provider_execution_policy_evidence_too_large")
    return payload


def response_identity(body: bytes) -> str | None:
    try:
        parsed = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(parsed, Mapping):
        return None
    return _safe_id(parsed.get("id"))


def provider_request_identity(headers: Mapping[str, Any] | None) -> str | None:
    if headers is None:
        return None
    lowered = {str(key).lower(): value for key, value in headers.items()}
    for key in ("x-request-id", "x-provider-request-id", "request-id"):
        value = _safe_id(lowered.get(key))
        if value:
            return value
    return None


def execute_observed_openai_compatible_json(
    *,
    protocol: Literal["chat_completions", "responses"],
    base_url: str,
    api_key: str,
    timeout_seconds: float,
    payload: Mapping[str, Any],
    adapter: str,
    provider: str | None,
    model: str | None,
    operation_identity: str,
    logical_attempt_ordinal: int = 1,
    capability_call_id: str = "",
    evidence_kind: str = EVIDENCE_KIND_PROVIDER_SUBMISSION,
    adapter_name: str | None = None,
) -> dict[str, Any]:
    """Execute one OpenAI-compatible HTTP call through the physical-attempt ledger.

    The raw HTTP response is settled before status/JSON interpretation. Therefore
    a later local decode/schema/staging failure cannot rewrite Provider completion.
    """
    from llm_core.openai_compatible_client import (
        ProviderEndpoint,
        chat_completions_url,
        post_chat_completions,
        post_responses,
    )

    body = json.dumps(dict(payload), ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("utf-8")
    base = str(base_url or "").strip().rstrip("/")
    if protocol == "chat_completions":
        endpoint = chat_completions_url(base)
        submit = post_chat_completions
    elif protocol == "responses":
        endpoint = f"{base}/responses" if base.endswith("/v1") else f"{base}/v1/responses"
        submit = post_responses
    else:
        raise ProviderAttemptEvidenceError("provider_protocol_invalid")

    repository = ProviderHttpAttemptRepository()
    if evidence_kind == EVIDENCE_KIND_PROVIDER_SUBMISSION:
        attempt = repository.begin(
            capability_call_id=capability_call_id,
            attempt_number=max(int(logical_attempt_ordinal), 1),
            request_body=body,
            endpoint=endpoint,
            adapter=adapter,
            operation_identity=operation_identity,
            provider=provider,
            model=model,
        )
    else:
        if evidence_kind not in {EVIDENCE_KIND_MCP_CALL, EVIDENCE_KIND_SUB_CALL}:
            raise ProviderAttemptEvidenceError("provider_attempt_evidence_kind_invalid")
        attempt = repository.begin_sub_attempt(
            capability_call_id=capability_call_id,
            evidence_kind=evidence_kind,
            adapter_name=str(adapter_name or operation_identity),
            request_body=body,
            endpoint=endpoint,
            adapter=adapter,
            operation_identity=operation_identity,
            provider=provider,
            model=model,
            logical_attempt_ordinal=max(int(logical_attempt_ordinal), 1),
        )

    response_observed = False

    def before_send() -> None:
        repository.mark_io_started(attempt)

    def observe_response(response: httpx.Response) -> None:
        nonlocal response_observed
        repository.finish(
            attempt,
            request_body=body,
            outcome=("response_received" if response.status_code < 400 else "provider_http_error"),
            http_status=response.status_code,
            response_body=response.content,
            response_headers=response.headers,
        )
        response_observed = True

    endpoint_config = ProviderEndpoint(
        base_url=base_url,
        api_key=api_key,
        timeout_seconds=timeout_seconds,
    )
    try:
        return submit(
            endpoint_config,
            dict(payload),
            extra_headers=attempt.headers,
            before_send=before_send,
            response_observer=observe_response,
        )
    except (httpx.ConnectTimeout, httpx.ConnectError, httpx.PoolTimeout):
        if not response_observed:
            repository.finish(
                attempt,
                request_body=body,
                outcome="transport_not_submitted",
                http_status=None,
                response_body=None,
                response_headers=None,
            )
        raise
    except httpx.TransportError:
        if not response_observed:
            repository.finish(
                attempt,
                request_body=body,
                outcome="transport_uncertain",
                http_status=None,
                response_body=None,
                response_headers=None,
            )
        raise


def _lock_sub_attempt_sequence(cur: Any, capability_call_id: str) -> None:
    """Serialize per-call sub-attempt ordinal allocation inside one transaction."""
    cur.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
        (f"openair_provider_http_attempts:{capability_call_id}",),
    )


class ProviderHttpAttemptRepository:
    def __init__(self, *, checkpoint_repository: Any = _DEFAULT_CHECKPOINT_REPOSITORY) -> None:
        if checkpoint_repository is _DEFAULT_CHECKPOINT_REPOSITORY:
            from app.modules.external_execution_checkpoint import (
                ExternalExecutionCheckpointRepository,
            )

            checkpoint_repository = ExternalExecutionCheckpointRepository()
        self._checkpoint_repository = checkpoint_repository

    @contextmanager
    def _connect(self) -> Iterator[psycopg.Connection[Any]]:
        conninfo = (
            f"host={settings.postgres_host} port={settings.postgres_port} "
            f"dbname={settings.postgres_database} user={settings.postgres_user}"
        )
        conn = psycopg.connect(conninfo, password=settings.postgres_password, row_factory=dict_row)  # type: ignore[arg-type]
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @contextmanager
    def _connect_for_sub_attempt(self) -> Iterator[psycopg.Connection[Any]]:
        try:
            with self._connect() as conn:
                yield conn
        except psycopg.Error as exc:
            raise ProviderAttemptEvidenceError(
                "provider_sub_attempt_evidence_persistence_failed"
            ) from exc

    @staticmethod
    def _parent_for_attempt(capability_call_id: str) -> ProviderExecutionParent:
        current = current_provider_execution_parent()
        if current is not None:
            return current
        call_id = str(capability_call_id or "").strip()
        if not call_id:
            raise ProviderAttemptEvidenceError("provider_execution_parent_required")
        return ProviderExecutionParent(
            kind="capability_call",
            parent_id=call_id,
            originating_authority="capability_call_runtime",
        )

    @staticmethod
    def _operation_identity(
        *, evidence_kind: str, adapter_name: str | None, request_hash: str, operation_identity: str | None
    ) -> str:
        explicit = str(operation_identity or "").strip()
        if explicit:
            return explicit[:240]
        if evidence_kind == EVIDENCE_KIND_PROVIDER_SUBMISSION:
            return "provider_submission"
        return f"sub:{evidence_kind}:{str(adapter_name or 'external').strip() or 'external'}:{request_hash[:16]}"[:240]

    @staticmethod
    def _parent_exists_clause(parent: ProviderExecutionParent, *, require_started_call: bool) -> tuple[str, tuple[Any, ...]]:
        if parent.kind == "capability_call":
            if require_started_call:
                return (
                    """EXISTS (
                           SELECT 1 FROM openair_execution_capability_calls c
                            WHERE c.call_id = %s AND c.external_action_started = TRUE
                       ) OR EXISTS (
                           SELECT 1 FROM openair_tasks t
                           CROSS JOIN LATERAL jsonb_array_elements(
                               COALESCE(t.payload->'capabilityCalls', '[]'::jsonb)
                           ) legacy_call
                            WHERE COALESCE(legacy_call->>'capabilityCallId', legacy_call->>'id') = %s
                              AND legacy_call->>'status' = 'running'
                              AND COALESCE(legacy_call->>'runtime', '') NOT IN ('replay', 'replay_fallback')
                       )""",
                    (parent.parent_id, parent.parent_id),
                )
            return (
                """EXISTS (SELECT 1 FROM openair_execution_capability_calls c WHERE c.call_id = %s)
                    OR EXISTS (
                        SELECT 1 FROM openair_tasks t
                        CROSS JOIN LATERAL jsonb_array_elements(
                            COALESCE(t.payload->'capabilityCalls', '[]'::jsonb)
                        ) legacy_call
                         WHERE COALESCE(legacy_call->>'capabilityCallId', legacy_call->>'id') = %s
                    )""",
                (parent.parent_id, parent.parent_id),
            )
        if parent.kind == "research_start":
            return (
                """EXISTS (SELECT 1 FROM research_start_requests r WHERE r.request_id = %s)
                    OR EXISTS (SELECT 1 FROM research_start_sessions s WHERE s.session_id = %s)""",
                (parent.parent_id, parent.parent_id),
            )
        if parent.kind == "model_job":
            return "EXISTS (SELECT 1 FROM openair_model_jobs j WHERE j.job_id = %s)", (parent.parent_id,)
        if parent.kind == "agent_session":
            return "EXISTS (SELECT 1 FROM openair_agent_sessions s WHERE s.session_id = %s)", (parent.parent_id,)
        if parent.kind == "system_probe":
            return "TRUE", ()
        raise ProviderAttemptEvidenceError("provider_execution_parent_kind_invalid")

    def _reserve_attempt(
        self,
        *,
        capability_call_id: str,
        logical_attempt_ordinal: int,
        evidence_kind: str,
        adapter_name: str | None,
        request_body: bytes,
        endpoint: str,
        adapter: str,
        operation_identity: str | None,
        provider: str | None,
        model: str | None,
        resolved_execution_policy: Mapping[str, Any] | None,
        require_started_call: bool,
        allow_checkpoint_recovery: bool,
    ) -> ProviderHttpAttempt:
        if (
            isinstance(logical_attempt_ordinal, bool)
            or not isinstance(logical_attempt_ordinal, int)
            or logical_attempt_ordinal < 1
            or not request_body
            or evidence_kind not in _VALID_EVIDENCE_KINDS
        ):
            raise ProviderAttemptEvidenceError("provider_attempt_identity_invalid")
        parent = self._parent_for_attempt(capability_call_id)
        request_hash = request_sha256(request_body)
        endpoint_identity = safe_provider_endpoint_identity(endpoint, adapter)
        operation = self._operation_identity(
            evidence_kind=evidence_kind,
            adapter_name=adapter_name,
            request_hash=request_hash,
            operation_identity=operation_identity,
        )
        legacy_call_id = (
            str(capability_call_id or "").strip()
            if parent.kind == "capability_call"
            else ""
        )
        checkpoint = None
        checkpoint_repository = self._checkpoint_repository
        if allow_checkpoint_recovery and parent.kind == "capability_call" and legacy_call_id:
            try:
                if checkpoint_repository is not None:
                    checkpoint = checkpoint_repository.get(capability_call_id=legacy_call_id)
            except psycopg.errors.UndefinedTable:
                checkpoint_repository = None
                checkpoint = None

        exists_sql, exists_params = self._parent_exists_clause(
            parent, require_started_call=require_started_call
        )
        attempt_id = f"pat_{uuid4().hex}"
        with self._connect_for_sub_attempt() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (f"openair_provider_http_attempts:{parent.kind}:{parent.parent_id}",),
                )
                cur.execute(
                    """SELECT attempt_id, attempt_number, request_hash,
                              provider_endpoint_identity, execution_state,
                              execution_reason, logical_attempt_ordinal,
                              capability_call_id, evidence_kind, adapter_name,
                              response_hash, transport_outcome, io_started,
                              submission_proven
                         FROM openair_provider_http_attempts
                        WHERE parent_kind = %s AND parent_id = %s
                          AND operation_identity = %s
                        ORDER BY attempt_number DESC
                        LIMIT 1""",
                    (parent.kind, parent.parent_id, operation),
                )
                latest = cur.fetchone()

                if (
                    checkpoint is not None
                    and checkpoint.phase == "response_received"
                    and checkpoint.response_kind == "provider_http_response"
                    and checkpoint.response_payload is not None
                    and latest is not None
                    and str(latest["execution_state"]) == "completed"
                    and str(latest["transport_outcome"]) == "response_received"
                    and str(latest["request_hash"]) == request_hash
                    and str(latest["provider_endpoint_identity"]) == endpoint_identity
                    and str(latest.get("response_hash") or "")
                    == hashlib.sha256(checkpoint.response_payload).hexdigest()
                ):
                    return ProviderHttpAttempt(
                        attempt_id=str(latest["attempt_id"]),
                        capability_call_id=legacy_call_id,
                        attempt_number=int(latest["attempt_number"]),
                        request_hash=request_hash,
                        provider_endpoint_identity=endpoint_identity,
                        evidence_kind=str(latest["evidence_kind"]),
                        adapter_name=(str(latest["adapter_name"]) if latest["adapter_name"] else None),
                        parent_kind=parent.kind,
                        parent_id=parent.parent_id,
                        operation_identity=operation,
                        logical_attempt_ordinal=int(latest["logical_attempt_ordinal"]),
                        recovered_response_body=checkpoint.response_payload,
                        recovered_http_status=checkpoint.response_http_status,
                        recovered_response_headers={},
                    )

                if latest is not None:
                    state = str(latest["execution_state"])
                    reason = str(latest["execution_reason"] or "")
                    latest_logical = int(latest["logical_attempt_ordinal"])
                    if state == "unknown":
                        raise DuplicateProviderAttemptError(
                            "provider_execution_unknown_replay_forbidden"
                        )
                    if state in {"completed", "cancelled"}:
                        raise DuplicateProviderAttemptError(
                            "provider_execution_terminal_replay_forbidden"
                        )
                    if state == "running":
                        if latest["io_started"] is not False or latest["submission_proven"] is True:
                            raise DuplicateProviderAttemptError(
                                "provider_execution_already_running"
                            )
                        cur.execute(
                            """UPDATE openair_provider_http_attempts
                                  SET finished_at = clock_timestamp(),
                                      execution_state = 'failed',
                                      execution_reason = 'transport_not_submitted',
                                      execution_stage = 'pre_io_recovery',
                                      submission_proven = FALSE,
                                      transport_outcome = 'transport_not_submitted'
                                WHERE attempt_id = %s
                                  AND execution_state = 'running'
                                  AND io_started = FALSE
                                  AND submission_proven IS NOT TRUE
                               RETURNING attempt_id""",
                            (str(latest["attempt_id"]),),
                        )
                        if cur.fetchone() is None:
                            raise DuplicateProviderAttemptError(
                                "provider_execution_already_running"
                            )
                    if state == "failed" and reason != "transport_not_submitted" and logical_attempt_ordinal <= latest_logical:
                        raise DuplicateProviderAttemptError(
                            "provider_logical_retry_identity_required"
                        )

                cur.execute(
                    f"""INSERT INTO openair_provider_http_attempts (
                           attempt_id, capability_call_id, attempt_number, request_hash,
                           provider_endpoint_identity, evidence_kind, adapter_name,
                           resolved_execution_policy, parent_kind, parent_id,
                           operation_identity, logical_attempt_ordinal, provider_id, model_id,
                           execution_state, execution_stage, originating_authority,
                           io_started, submission_proven, transport_outcome
                       )
                       SELECT %s, NULLIF(%s, ''),
                              COALESCE((SELECT MAX(attempt_number) + 1
                                          FROM openair_provider_http_attempts
                                         WHERE parent_kind = %s AND parent_id = %s), 1),
                              %s, %s, %s, %s, %s::jsonb,
                              %s, %s, %s, %s, NULLIF(%s, ''), NULLIF(%s, ''),
                              'running', 'provider_submission', %s,
                              FALSE, NULL, 'in_flight'
                        WHERE {exists_sql}
                       RETURNING attempt_number""",
                    (
                        attempt_id,
                        legacy_call_id,
                        parent.kind,
                        parent.parent_id,
                        request_hash,
                        endpoint_identity,
                        evidence_kind,
                        adapter_name,
                        _execution_policy_json(resolved_execution_policy),
                        parent.kind,
                        parent.parent_id,
                        operation,
                        logical_attempt_ordinal,
                        str(provider or "").strip(),
                        str(model or "").strip(),
                        parent.originating_authority,
                        *exists_params,
                    ),
                )
                row = cur.fetchone()
                if row is None:
                    raise ProviderAttemptEvidenceError("provider_execution_parent_not_ready")
                physical_ordinal = int(row["attempt_number"])
                if (
                    checkpoint is not None
                    and checkpoint_repository is not None
                    and checkpoint.phase == "prepared"
                    and not checkpoint_repository.mark_submitted_in_transaction(
                        cur, capability_call_id=legacy_call_id
                    )
                ):
                    raise ProviderAttemptEvidenceError("provider_checkpoint_submit_conflict")
        return ProviderHttpAttempt(
            attempt_id=attempt_id,
            capability_call_id=legacy_call_id,
            attempt_number=physical_ordinal,
            request_hash=request_hash,
            provider_endpoint_identity=endpoint_identity,
            evidence_kind=evidence_kind,
            adapter_name=adapter_name,
            parent_kind=parent.kind,
            parent_id=parent.parent_id,
            operation_identity=operation,
            logical_attempt_ordinal=logical_attempt_ordinal,
        )

    def begin(
        self,
        *,
        capability_call_id: str,
        attempt_number: int,
        request_body: bytes,
        endpoint: str,
        adapter: str,
        resolved_execution_policy: Mapping[str, Any] | None = None,
        operation_identity: str | None = None,
        provider: str | None = None,
        model: str | None = None,
    ) -> ProviderHttpAttempt:
        return self._reserve_attempt(
            capability_call_id=capability_call_id,
            logical_attempt_ordinal=attempt_number,
            evidence_kind=EVIDENCE_KIND_PROVIDER_SUBMISSION,
            adapter_name=None,
            request_body=request_body,
            endpoint=endpoint,
            adapter=adapter,
            operation_identity=operation_identity,
            provider=provider,
            model=model,
            resolved_execution_policy=resolved_execution_policy,
            require_started_call=True,
            allow_checkpoint_recovery=True,
        )

    def begin_sub_attempt(
        self,
        *,
        capability_call_id: str,
        evidence_kind: str,
        adapter_name: str,
        request_body: bytes,
        endpoint: str,
        adapter: str,
        resolved_execution_policy: Mapping[str, Any] | None = None,
        operation_identity: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        logical_attempt_ordinal: int = 1,
    ) -> ProviderHttpAttempt:
        label = str(adapter_name or "").strip()
        if not label:
            raise ProviderAttemptEvidenceError("provider_sub_attempt_identity_invalid")
        if evidence_kind == EVIDENCE_KIND_PROVIDER_SUBMISSION:
            raise ProviderAttemptEvidenceError(
                "provider_sub_attempt_kind_reserved; use begin() for provider_submissions"
            )
        return self._reserve_attempt(
            capability_call_id=capability_call_id,
            logical_attempt_ordinal=logical_attempt_ordinal,
            evidence_kind=evidence_kind,
            adapter_name=label,
            request_body=request_body,
            endpoint=endpoint,
            adapter=adapter,
            operation_identity=operation_identity,
            provider=provider,
            model=model,
            resolved_execution_policy=resolved_execution_policy,
            require_started_call=False,
            allow_checkpoint_recovery=False,
        )

    def mark_io_started(self, attempt: ProviderHttpAttempt) -> None:
        """Durably record that the transport crossed the local pre-I/O boundary."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE openair_provider_http_attempts
                          SET io_started = TRUE, execution_stage = 'transport'
                        WHERE attempt_id = %s AND execution_state = 'running'
                       RETURNING attempt_id""",
                    (attempt.attempt_id,),
                )
                if cur.fetchone() is None:
                    raise ProviderAttemptEvidenceError("provider_attempt_io_start_conflict")

    def finish(
        self,
        attempt: ProviderHttpAttempt,
        *,
        request_body: bytes,
        outcome: str,
        http_status: int | None,
        response_body: bytes | None,
        response_headers: Mapping[str, Any] | None,
        reason: str | None = None,
        stage: str | None = None,
    ) -> None:
        """Finalize physical execution truth before local result processing."""
        if attempt.recovered_response_body is not None:
            return
        outcome_truth = {
            "response_received": ("completed", "provider_response_received", "response_observation", True),
            "provider_http_error": ("failed", "known_provider_failure", "response_observation", True),
            "transport_not_submitted": ("failed", "transport_not_submitted", "transport", False),
            "transport_uncertain": ("unknown", "transport_uncertain", "response_observation", None),
            "provider_cancelled": ("cancelled", "provider_cancelled", "response_observation", True),
        }
        if outcome not in outcome_truth:
            raise ProviderAttemptEvidenceError("provider_transport_outcome_invalid")
        state, default_reason, default_stage, submission_proven = outcome_truth[outcome]
        response_hash = hashlib.sha256(response_body).hexdigest() if response_body is not None else None
        diagnostics = (
            _safe_rejection_diagnostics(request_body, response_body)
            if outcome == "provider_http_error"
            else None
        )
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE openair_provider_http_attempts
                          SET finished_at = clock_timestamp(), http_status = %s,
                              provider_request_id = %s, provider_response_id = %s,
                              response_hash = %s, transport_outcome = %s,
                              rejection_diagnostics = %s::jsonb,
                              execution_state = %s, execution_reason = %s,
                              execution_stage = %s, io_started = TRUE,
                              submission_proven = %s
                        WHERE attempt_id = %s
                          AND execution_state = 'running'
                          AND finished_at IS NULL
                       RETURNING attempt_id""",
                    (
                        http_status,
                        provider_request_identity(response_headers),
                        response_identity(response_body) if response_body is not None else None,
                        response_hash,
                        outcome,
                        json.dumps(diagnostics) if diagnostics is not None else None,
                        state,
                        str(reason or default_reason)[:240],
                        str(stage or default_stage)[:160],
                        submission_proven,
                        attempt.attempt_id,
                    ),
                )
                if cur.fetchone() is None:
                    raise ProviderAttemptEvidenceError("provider_http_attempt_finish_conflict")
        # Capability-call checkpoints remain the recovery handoff for that owner.
        if attempt.parent_kind != "capability_call" or not attempt.capability_call_id:
            return
        if attempt.evidence_kind != EVIDENCE_KIND_PROVIDER_SUBMISSION:
            return
        try:
            checkpoints = self._checkpoint_repository
            if checkpoints is None:
                return
            checkpoint = checkpoints.get(capability_call_id=attempt.capability_call_id)
            if checkpoint is not None:
                if outcome == "transport_not_submitted":
                    checkpoints.mark_not_submitted(capability_call_id=attempt.capability_call_id)
                elif response_body is not None:
                    checkpoints.stage_response(
                        capability_call_id=attempt.capability_call_id,
                        response_kind="provider_http_response",
                        payload=response_body,
                        http_status=http_status,
                    )
        except psycopg.errors.UndefinedTable:
            pass

    @staticmethod
    def _observation_from_row(row: Mapping[str, Any]) -> ProviderExecutionObservation:
        state = str(row["execution_state"])
        if state not in {"running", "completed", "failed", "cancelled", "unknown"}:
            raise ProviderAttemptEvidenceError("provider_execution_observation_state_invalid")
        return ProviderExecutionObservation(
            attempt_id=str(row["attempt_id"]),
            capability_call_id=(
                str(row["capability_call_id"]) if row.get("capability_call_id") is not None else None
            ),
            attempt_number=int(row["attempt_number"]),
            state=state,  # type: ignore[arg-type]
            provider_request_id=(
                str(row["provider_request_id"]) if row.get("provider_request_id") is not None else None
            ),
            provider_response_id=(
                str(row["provider_response_id"]) if row.get("provider_response_id") is not None else None
            ),
            http_status=(int(row["http_status"]) if row.get("http_status") is not None else None),
            transport_outcome=str(row.get("transport_outcome") or "in_flight"),
            parent_kind=str(row.get("parent_kind") or "capability_call"),
            parent_id=(str(row["parent_id"]) if row.get("parent_id") is not None else None),
            operation_identity=str(row.get("operation_identity") or "provider_submission"),
            logical_attempt_ordinal=int(row.get("logical_attempt_ordinal") or 1),
            reason=(str(row["execution_reason"]) if row.get("execution_reason") is not None else None),
            stage=(str(row["execution_stage"]) if row.get("execution_stage") is not None else None),
            originating_authority=(
                str(row["originating_authority"]) if row.get("originating_authority") is not None else None
            ),
            io_started=(
                bool(row["io_started"]) if row.get("io_started") is not None else None
            ),
            submission_proven=(
                bool(row["submission_proven"]) if row.get("submission_proven") is not None else None
            ),
        )

    @staticmethod
    def _observation_columns() -> str:
        return """attempt_id, capability_call_id, attempt_number,
                  http_status, provider_request_id, provider_response_id,
                  transport_outcome, execution_state, parent_kind, parent_id,
                  operation_identity, logical_attempt_ordinal, execution_reason,
                  execution_stage, originating_authority, io_started, submission_proven"""

    def observe(
        self,
        *,
        capability_call_id: str,
        attempt_number: int = 1,
        evidence_kind: str = EVIDENCE_KIND_PROVIDER_SUBMISSION,
    ) -> ProviderExecutionObservation | None:
        """Read one exact physical attempt owned by a capability call."""
        call_id = str(capability_call_id or "").strip()
        if (
            not call_id
            or isinstance(attempt_number, bool)
            or not isinstance(attempt_number, int)
            or attempt_number < 1
            or evidence_kind not in _VALID_EVIDENCE_KINDS
        ):
            raise ProviderAttemptEvidenceError("provider_execution_observation_identity_invalid")
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""SELECT {self._observation_columns()}
                          FROM openair_provider_http_attempts
                         WHERE parent_kind = 'capability_call' AND parent_id = %s
                           AND attempt_number = %s AND evidence_kind = %s""",
                    (call_id, attempt_number, evidence_kind),
                )
                row = cur.fetchone()
        return None if row is None else self._observation_from_row(row)

    def observe_latest(
        self,
        *,
        parent_kind: str,
        parent_id: str,
        operation_identity: str | None = None,
        evidence_kind: str = EVIDENCE_KIND_PROVIDER_SUBMISSION,
    ) -> ProviderExecutionObservation | None:
        """Read the latest physical attempt for one durable parent/operation."""
        kind = str(parent_kind or "").strip()
        owner = str(parent_id or "").strip()
        operation = str(operation_identity or "").strip()
        if (
            kind not in {"capability_call", "research_start", "model_job", "agent_session", "system_probe"}
            or not owner
            or evidence_kind not in _VALID_EVIDENCE_KINDS
        ):
            raise ProviderAttemptEvidenceError("provider_execution_observation_identity_invalid")
        where_operation = " AND operation_identity = %s" if operation else ""
        params: tuple[Any, ...] = (kind, owner, evidence_kind, operation) if operation else (kind, owner, evidence_kind)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""SELECT {self._observation_columns()}
                          FROM openair_provider_http_attempts
                         WHERE parent_kind = %s AND parent_id = %s
                           AND evidence_kind = %s{where_operation}
                         ORDER BY attempt_number DESC
                         LIMIT 1""",
                    params,
                )
                row = cur.fetchone()
        return None if row is None else self._observation_from_row(row)

    def observe_attempt(self, *, attempt_id: str) -> ProviderExecutionObservation | None:
        """Read one physical attempt by its globally unique durable identity."""
        identity = str(attempt_id or "").strip()
        if not identity:
            raise ProviderAttemptEvidenceError("provider_execution_observation_identity_invalid")
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""SELECT {self._observation_columns()}
                          FROM openair_provider_http_attempts
                         WHERE attempt_id = %s""",
                    (identity,),
                )
                row = cur.fetchone()
        return None if row is None else self._observation_from_row(row)

    def record_sub_attempt(
        self,
        *,
        capability_call_id: str,
        evidence_kind: str,
        adapter_name: str | None,
        request_body: bytes,
        endpoint: str,
        adapter: str,
        outcome: str,
        resolved_execution_policy: Mapping[str, Any] | None = None,
        http_status: int | None = None,
        response_body: bytes | None = None,
        response_headers: Mapping[str, Any] | None = None,
    ) -> ProviderHttpAttempt | None:
        """Reserve and finish one child physical call through the same ledger."""
        label = str(adapter_name or "").strip()
        if evidence_kind == EVIDENCE_KIND_PROVIDER_SUBMISSION:
            raise ProviderAttemptEvidenceError(
                "provider_sub_attempt_kind_reserved; use begin() for provider_submissions"
            )
        if not label:
            raise ProviderAttemptEvidenceError("provider_sub_attempt_identity_invalid")
        try:
            attempt = self.begin_sub_attempt(
                capability_call_id=capability_call_id,
                evidence_kind=evidence_kind,
                adapter_name=label,
                request_body=request_body,
                endpoint=endpoint,
                adapter=adapter,
                resolved_execution_policy=resolved_execution_policy,
            )
        except ProviderAttemptEvidenceError as exc:
            if str(exc) == "provider_execution_parent_not_ready":
                return None
            raise
        self.mark_io_started(attempt)
        self.finish(
            attempt,
            request_body=request_body,
            outcome=outcome,
            http_status=http_status,
            response_body=response_body,
            response_headers=response_headers,
        )
        return attempt

    def list_attempts(self, *, capability_call_id: str) -> list[dict[str, Any]]:
        """Return all HTTP attempt rows for a capability_call, oldest first."""
        call_id = str(capability_call_id or "").strip()
        if not call_id:
            return []
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT attempt_id, capability_call_id, attempt_number, request_hash,
                              provider_endpoint_identity, evidence_kind, adapter_name,
                              started_at, finished_at, http_status,
                              provider_request_id, provider_response_id,
                              response_hash, transport_outcome, rejection_diagnostics,
                              resolved_execution_policy, parent_kind, parent_id,
                              operation_identity, logical_attempt_ordinal, provider_id, model_id,
                              execution_state, execution_reason, execution_stage,
                              originating_authority, io_started, submission_proven
                         FROM openair_provider_http_attempts
                        WHERE parent_kind = 'capability_call' AND parent_id = %s
                        ORDER BY attempt_number, started_at""",
                    (call_id,),
                )
                return [dict(row) for row in cur.fetchall()]


def _safe_id(value: Any) -> str | None:
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        return None
    normalized = str(value).strip()
    if not normalized or len(normalized) > 512 or any(ord(char) < 32 for char in normalized):
        return None
    return normalized


def _default_port(scheme: str) -> int:
    return 443 if scheme.lower() == "https" else 80
