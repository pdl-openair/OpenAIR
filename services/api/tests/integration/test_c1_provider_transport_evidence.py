"""C1 proof: one real loopback HTTP submission and one durable DB attempt."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import psycopg
import pytest
from psycopg.rows import dict_row

from app.config import settings
from app.execution.durable_orchestrator_runtime import freeze_artifact_contract_body
from app.execution import huiyan_skill_runner
from app.execution.openai_responses_provider import (
    HttpOpenAIResponsesTransport,
    OpenAIResponsesTransportRequest,
    ProviderRejectedRequestError,
)
from app.modules.artifact_repository import ArtifactRepository
from app.modules.collaboration_repository import CollaborationRepository
from app.modules.durable_execution import DurableExecutionRepository
from app.modules.provider_registry import ProviderRuntimeConfig
from app.modules.provider_transport_evidence import DuplicateProviderAttemptError
from app.modules.task_repository import TaskRepository
from tests.integration._production_postgres_bootstrap import bootstrap_production_postgres_schema


OWNER = "c1-owner"
WORKSPACE = "c1-workspace"
WORKER = "c1-worker"


class ControlledProviderProxy:
    """Real loopback HTTP boundary with an independent duplicate-call gate."""

    def __init__(
        self,
        *,
        reject_structured_output: bool = False,
        response_content: dict[str, Any] | None = None,
    ) -> None:
        self.reject_structured_output = reject_structured_output
        self.response_content = response_content
        self.observations: list[dict[str, Any]] = []
        self.duplicate_rejections: list[dict[str, Any]] = []
        self._seen_calls: set[str] = set()
        self._lock = threading.Lock()
        proxy = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length)
                call_id = str(self.headers.get("X-OpenAIR-Capability-Call-Id") or "")
                evidence = {
                    "capabilityCallId": call_id,
                    "attemptId": str(self.headers.get("X-OpenAIR-Attempt-Id") or ""),
                    "attemptNumber": int(self.headers.get("X-OpenAIR-Attempt-Number") or 0),
                    "requestHash": hashlib.sha256(body).hexdigest(),
                    "declaredRequestHash": str(self.headers.get("X-OpenAIR-Request-Hash") or ""),
                }
                with proxy._lock:
                    if call_id in proxy._seen_calls:
                        proxy.duplicate_rejections.append(evidence)
                        self._respond(409, {"error": "duplicate_capability_call"})
                        return
                    proxy._seen_calls.add(call_id)
                    proxy.observations.append(evidence)
                if proxy.reject_structured_output:
                    self._respond(400, {"error": "unsupported response_format json_schema"})
                    return
                artifact = proxy.response_content or {
                    "title": "C1 controlled Provider result",
                    "mimeType": "text/plain",
                    "content": json.dumps({"result": {}}, separators=(",", ":")),
                }
                self._respond(200, {
                    "id": "provider-response-c1",
                    "choices": [{"message": {"content": json.dumps(artifact)}, "finish_reason": "stop"}],
                })

            def _respond(self, status: int, payload: dict[str, Any]) -> None:
                body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("X-Request-Id", "provider-request-c1")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: Any) -> None:
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}"

    def __enter__(self) -> ControlledProviderProxy:
        self.thread.start()
        return self

    def __exit__(self, *_args: Any) -> None:
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()

    def submitted_count(self, call_id: str) -> int:
        return sum(row["capabilityCallId"] == call_id for row in self.observations)


def _configure_isolated_database() -> str:
    dsn = os.environ.get("C1_TRANSPORT_TEST_DATABASE_URL", "").strip()
    if not dsn:
        pytest.fail("C1_TRANSPORT_TEST_DATABASE_URL is required; C1 refuses an implicit database")
    parsed = urlparse(dsn)
    database = parsed.path.lstrip("/").lower()
    if (
        parsed.scheme not in {"postgres", "postgresql"}
        or "c1" not in database
        or not any(marker in database for marker in ("test", "qa"))
        or any(marker in database for marker in ("prod", "production", "development", "dev"))
    ):
        pytest.fail("C1 transport evidence requires a disposable c1 test/qa PostgreSQL database")
    settings.postgres_host = parsed.hostname or "localhost"
    settings.postgres_port = parsed.port or 5432
    settings.postgres_user = parsed.username or "postgres"
    settings.postgres_password = parsed.password or ""
    settings.postgres_database = database
    settings.persistence_backend = "postgres"
    return dsn


@pytest.fixture(autouse=True)
def isolated_database(monkeypatch: pytest.MonkeyPatch) -> None:
    dsn = _configure_isolated_database()
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")
        with conn.cursor() as cur:
            bootstrap_production_postgres_schema(cur)
    ArtifactRepository().bootstrap()
    DurableExecutionRepository().bootstrap(auto_apply_migrations=False)


def _query(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with psycopg.connect(os.environ["C1_TRANSPORT_TEST_DATABASE_URL"], row_factory=dict_row) as conn:
        return list(conn.execute(sql, params).fetchall())


def _started_call(label: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    task_id = f"task-{label}"
    TaskRepository().insert_task({
        "taskId": task_id,
        "ownerId": OWNER,
        "workspaceId": WORKSPACE,
        "createdBy": OWNER,
        "title": "C1 transport evidence",
        "domain": "research_task",
        "currentStage": "evidence",
        "status": "not_started",
        "creationStatus": "confirmed",
        "resultSource": "none",
        "executionMode": "stage_confirmation",
        "stages": ["evidence"],
        "stageCatalog": [],
        "stageDetails": [{"stageKey": "evidence", "status": "not_started", "subStages": []}],
        "artifacts": [],
        "materials": [],
        "memoryRecords": [],
        "confirmations": [],
        "capabilityCalls": [],
    })
    durable = DurableExecutionRepository()
    run, _ = durable.start_initial(
        task_id=task_id,
        owner_id=OWNER,
        workspace_id=WORKSPACE,
        idempotency_key=f"start-{label}",
        source_mode="live",
    )
    claim = durable.claim(WORKER, 60)
    assert claim and claim["run_id"] == run["run_id"]
    collaboration = CollaborationRepository()
    unit = collaboration.create_work_unit(
        run_id=run["run_id"],
        owner_id=OWNER,
        workspace_id=WORKSPACE,
        worker_id=WORKER,
        fence_epoch=claim["lease_epoch"],
        agent_id="paper_evidence_extraction",
        kind="research",
        title=label,
        unit_key=f"unit-{label}",
        objective=label,
    )
    collaboration.transition_work_unit(
        work_unit_id=unit["work_unit_id"],
        owner_id=OWNER,
        workspace_id=WORKSPACE,
        worker_id=WORKER,
        fence_epoch=claim["lease_epoch"],
        state="running",
    )
    call = collaboration.create_capability_invocation(
        run_id=run["run_id"],
        owner_id=OWNER,
        workspace_id=WORKSPACE,
        worker_id=WORKER,
        fence_epoch=claim["lease_epoch"],
        work_unit_id=unit["work_unit_id"],
        requesting_agent_id="paper_evidence_extraction",
        invocation_kind="agent_brain",
        idempotency_key=f"call-{label}",
        request_fingerprint=f"fingerprint-{label}",
    )
    begun, replay = collaboration.begin_capability_invocation(
        call_id=call["call_id"],
        owner_id=OWNER,
        workspace_id=WORKSPACE,
        worker_id=WORKER,
        fence_epoch=claim["lease_epoch"],
        idempotency_key=f"call-{label}",
        request_fingerprint=f"fingerprint-{label}",
    )
    assert replay is False and begun["attempt_number"] == 1
    return run, claim, {**call, "work_unit_id": unit["work_unit_id"]}


def _transport_request(call_id: str) -> OpenAIResponsesTransportRequest:
    schema = {
        "contractId": "c1_transport_contract",
        "semanticVersion": "1.0.0",
        "type": "object",
        "properties": {"result": {"type": "object"}},
        "required": ["result"],
        "additionalProperties": False,
    }
    frozen = freeze_artifact_contract_body(
        "c1_transport_contract",
        schema,
        source_ref="schemas/c1_transport_contract.json",
    )
    return OpenAIResponsesTransportRequest(
        providerId="openai_primary",
        adapter="huiyan_chat_completions",
        model="controlled-c1-model",
        inputText='{"frozen":"request"}',
        artifactContract="c1_transport_contract",
        artifactContractSchemaVersion=1,
        artifactRequiredFields=("result",),
        frozenArtifactContract=frozen,
        capabilityCallId=call_id,
        attemptNumber=1,
    )


def _runtime(proxy: ControlledProviderProxy) -> ProviderRuntimeConfig:
    return ProviderRuntimeConfig(
        providerId="openai_primary",
        adapter="huiyan_chat_completions",
        model="controlled-c1-model",
        baseUrl=proxy.base_url,
        apiKey="isolated-c1-token",
        timeoutSeconds=5,
    )


def test_database_and_controlled_proxy_each_observe_exactly_one_submission() -> None:
    _run, _claim, call = _started_call("single-submission")
    call_id = call["call_id"]
    with ControlledProviderProxy() as proxy:
        transport = HttpOpenAIResponsesTransport()
        result, usage = transport.execute(_transport_request(call_id), _runtime(proxy))
        assert result["content"] == '{"result":{}}'
        assert usage is not None
        assert usage["estimated"] is True
        with pytest.raises(DuplicateProviderAttemptError, match="already_exists"):
            transport.execute(_transport_request(call_id), _runtime(proxy))

        rows = _query(
            """SELECT attempt_id, capability_call_id, attempt_number, request_hash,
                      provider_endpoint_identity, started_at, finished_at, http_status,
                      provider_request_id, provider_response_id, response_hash, transport_outcome
                 FROM openair_provider_http_attempts WHERE capability_call_id = %s""",
            (call_id,),
        )
        assert len(rows) == 1
        assert proxy.submitted_count(call_id) == 1
        assert not proxy.duplicate_rejections
        observed = next(row for row in proxy.observations if row["capabilityCallId"] == call_id)
        assert observed["attemptId"] == rows[0]["attempt_id"]
        assert observed["attemptNumber"] == rows[0]["attempt_number"] == 1
        assert observed["requestHash"] == observed["declaredRequestHash"] == rows[0]["request_hash"]
        assert rows[0]["finished_at"] is not None
        assert rows[0]["http_status"] == 200
        assert rows[0]["provider_request_id"] == "provider-request-c1"
        assert rows[0]["provider_response_id"] == "provider-response-c1"
        assert rows[0]["response_hash"]
        assert rows[0]["transport_outcome"] == "response_received"

        # The proxy is also independently fail-closed if its DB-side peer is bypassed.
        raw = b'{"proxy":"duplicate-proof"}'
        headers = {
            "Content-Type": "application/json",
            "X-OpenAIR-Capability-Call-Id": "proxy-only-call",
            "X-OpenAIR-Attempt-Id": "proxy-attempt-1",
            "X-OpenAIR-Attempt-Number": "1",
            "X-OpenAIR-Request-Hash": hashlib.sha256(raw).hexdigest(),
        }
        url = f"{proxy.base_url}/v1/chat/completions"
        with urlopen(Request(url, data=raw, headers=headers, method="POST"), timeout=5):  # nosec B310: loopback proxy
            pass
        with pytest.raises(HTTPError) as duplicate:
            urlopen(Request(url, data=raw, headers=headers, method="POST"), timeout=5)  # nosec B310: loopback proxy
        assert duplicate.value.code == 409
        assert len(proxy.duplicate_rejections) == 1
        assert proxy.duplicate_rejections[0]["requestHash"] == hashlib.sha256(raw).hexdigest()

    print(json.dumps({
        "DATABASE_EVIDENCE": {
            "capabilityCallId": call_id,
            "attemptId": rows[0]["attempt_id"],
            "attemptNumber": 1,
            "requestHash": rows[0]["request_hash"],
            "providerEndpointIdentity": rows[0]["provider_endpoint_identity"],
            "startedAt": rows[0]["started_at"],
            "finishedAt": rows[0]["finished_at"],
            "httpStatus": rows[0]["http_status"],
            "providerRequestId": rows[0]["provider_request_id"],
            "providerResponseId": rows[0]["provider_response_id"],
            "responseHash": rows[0]["response_hash"],
            "transportOutcome": rows[0]["transport_outcome"],
        },
        "PROXY_EVIDENCE": observed,
        "DATABASE_ATTEMPT_COUNT": len(rows),
        "PROXY_SUBMISSION_COUNT": 1,
        "PROXY_DUPLICATE_HARD_FAIL": 409,
    }, default=str, sort_keys=True))


def test_provider_incompatibility_is_one_real_failure_without_fallback_success() -> None:
    run, claim, call = _started_call("incompatible-provider")
    call_id = call["call_id"]
    with ControlledProviderProxy(reject_structured_output=True) as proxy:
        with pytest.raises(ProviderRejectedRequestError):
            HttpOpenAIResponsesTransport().execute(_transport_request(call_id), _runtime(proxy))
        assert proxy.submitted_count(call_id) == 1

    evidence = _query(
        "SELECT http_status, transport_outcome FROM openair_provider_http_attempts WHERE capability_call_id = %s",
        (call_id,),
    )
    assert evidence == [{"http_status": 400, "transport_outcome": "provider_http_error"}]

    collaboration = CollaborationRepository()
    collaboration.finish_capability_invocation(
        call_id=call_id,
        owner_id=OWNER,
        workspace_id=WORKSPACE,
        worker_id=WORKER,
        fence_epoch=claim["lease_epoch"],
        state="failed",
        summary="Provider rejected the frozen structured request.",
        output_artifact_refs=[],
        failure_classification="provider_rejected_request",
    )
    collaboration.transition_work_unit(
        work_unit_id=call["work_unit_id"],
        owner_id=OWNER,
        workspace_id=WORKSPACE,
        worker_id=WORKER,
        fence_epoch=claim["lease_epoch"],
        state="failed",
        summary="Provider rejected the frozen structured request.",
    )
    terminal = DurableExecutionRepository().callback(
        run["run_id"],
        WORKER,
        claim["lease_epoch"],
        "failed",
        {"failureClassification": "multiagent_execution_failed"},
    )
    assert terminal["state"] == "failed"
    assert terminal["failure_classification"] == "multiagent_execution_failed"
    persisted_call = _query(
        "SELECT state, failure_classification FROM openair_execution_capability_calls WHERE call_id = %s",
        (call_id,),
    )
    assert persisted_call == [{"state": "failed", "failure_classification": "provider_rejected_request"}]


def test_legacy_trial_capability_call_also_has_one_db_and_proxy_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    call_id = "legacy-call-c1"
    task = {
        "taskId": "task-legacy-c1",
        "ownerId": OWNER,
        "workspaceId": WORKSPACE,
        "createdBy": OWNER,
        "title": "Legacy C1 transport evidence",
        "domain": "research_task",
        "currentStage": "evidence",
        "status": "running",
        "creationStatus": "confirmed",
        "stages": ["evidence"],
        "stageCatalog": [],
        "stageDetails": [],
        "artifacts": [],
        "materials": [],
        "memoryRecords": [],
        "confirmations": [],
        "capabilityCalls": [{
            "id": call_id,
            "skillId": "controlled-legacy-skill",
            "status": "running",
            "runtime": "huiyan_chat_completions",
        }],
    }
    TaskRepository().insert_task(task)
    monkeypatch.setattr(
        huiyan_skill_runner,
        "_skill_contract",
        lambda _skill_id: ({"skill_id": "controlled-legacy-skill", "output_file": None}, "Execute once."),
    )
    monkeypatch.setenv("LLM_PROVIDER_API_KEY", "isolated-c1-token")
    monkeypatch.setenv("LLM_PROVIDER_MODEL", "controlled-c1-model")
    with ControlledProviderProxy(response_content={"summary": "Controlled legacy result."}) as proxy:
        monkeypatch.setenv("LLM_PROVIDER_BASE_URL", proxy.base_url)
        result = asyncio.run(huiyan_skill_runner.run_huiyan_selected_skill(SimpleNamespace(
            task_id=task["taskId"],
            requested_skill_id="controlled-legacy-skill",
            skill_id="controlled-legacy-skill",
            message="Execute the frozen legacy Skill once.",
            task=task,
            capability_call_id=call_id,
        )))
        assert result["reply"] == "Controlled legacy result."
        assert proxy.submitted_count(call_id) == 1

    rows = _query(
        "SELECT attempt_number, http_status, transport_outcome FROM openair_provider_http_attempts WHERE capability_call_id = %s",
        (call_id,),
    )
    assert rows == [{"attempt_number": 1, "http_status": 200, "transport_outcome": "response_received"}]
