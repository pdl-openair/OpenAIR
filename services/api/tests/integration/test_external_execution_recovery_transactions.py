"""Production-path proofs for no-replay external execution recovery."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import threading
from typing import Any
from urllib.parse import urlparse

import pytest

from app.config import settings
from app.execution.durable_orchestrator_runtime import freeze_artifact_contract_body
from app.execution.openai_responses_provider import (
    HttpOpenAIResponsesTransport,
    OpenAIResponsesTransportRequest,
)
from app.execution.research_dispatch_provider import (
    DispatchSubmissionCompleted,
)
from app.execution.research_dispatch_worker import process_authorized_recovery
from app.modules.artifact_write import ArtifactStorageError
from app.modules.external_execution_checkpoint import (
    ExternalExecutionCheckpointRepository,
)
from app.modules.provider_registry import ProviderRuntimeConfig, ProviderSubmissionSafety
from app.modules.research_dispatch_service import ResearchDispatchService
from app.modules.research_orchestration_repository import dispatch_capability_call_id

from tests.support.research_dispatch_transactions import (
    clean_tables,
    postgres_environment,
    repository,
    _claim,
    _dispatch_row,
    _namespaced_dispatch_mutation_connection,
    _task_row,
)


from tests.integration.test_research_dispatch_result_transactions import (
    _seed_with_archived_registry,
)

NO_REPLAY = ProviderSubmissionSafety(
    False, False, False, False, "single_attempt_no_replay"
)
RESULT_SCHEMA = {
    "contractId": "rca_external_result_v1",
    "semanticVersion": "1.0.0",
    "type": "object",
    "properties": {"result": {"type": "string"}},
    "required": ["result"],
    "additionalProperties": False,
}
FROZEN_RESULT_CONTRACT = freeze_artifact_contract_body(
    "rca_external_result_v1",
    RESULT_SCHEMA,
    source_ref="tests/rca_external_result_v1.json",
)


@pytest.fixture(autouse=True)
def configure_checkpoint_database(
    monkeypatch: pytest.MonkeyPatch,
    postgres_environment,
) -> None:
    parsed = urlparse(os.environ["OPENAIR_T04_POSTGRES_DSN"])
    monkeypatch.setattr(settings, "postgres_host", parsed.hostname or "localhost")
    monkeypatch.setattr(settings, "postgres_port", parsed.port or 5432)
    monkeypatch.setattr(settings, "postgres_user", parsed.username or "postgres")
    monkeypatch.setattr(settings, "postgres_password", parsed.password or "")
    monkeypatch.setattr(
        settings,
        "postgres_database",
        parsed.path.lstrip("/"),
    )
    monkeypatch.setattr(settings, "persistence_backend", "postgres")
    with postgres_environment() as connection:
        connection.execute("DELETE FROM openair_provider_http_attempts")


class ControlledProvider:
    def __init__(self, artifact: dict[str, Any]) -> None:
        self.artifact = artifact
        self.call_count = 0
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                self.rfile.read(length)
                outer.call_count += 1
                body = json.dumps(
                    {
                        "id": "rca-response-1",
                        "choices": [
                            {
                                "message": {
                                    "content": json.dumps(
                                        outer.artifact, separators=(",", ":")
                                    )
                                },
                                "finish_reason": "stop",
                            }
                        ],
                    },
                    separators=(",", ":"),
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("X-Request-Id", "rca-request-1")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, _format: str, *_args: Any) -> None:
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}"

    def __enter__(self) -> "ControlledProvider":
        self.thread.start()
        return self

    def __exit__(self, *_args: Any) -> None:
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()


class PreparedHttpProvider:
    provider_id = "openai_primary"
    adapter = "huiyan_chat_completions"
    capabilities = NO_REPLAY
    runtime = {"runtimeKind": "llm_reasoning"}

    def __init__(self, base_url: str) -> None:
        self.transport = HttpOpenAIResponsesTransport()
        self.runtime_config = ProviderRuntimeConfig(
            providerId=self.provider_id,
            adapter=self.adapter,
            model="rca-controlled-model",
            baseUrl=base_url,
            apiKey="rca-test-token",
            timeoutSeconds=5,
        )
        self.submit_count = 0

    def submit(self, request):
        self.submit_count += 1
        structured, usage = self.transport.execute(
            OpenAIResponsesTransportRequest(
                providerId=self.provider_id,
                adapter=self.adapter,
                model="rca-controlled-model",
                inputText='{"frozen":"request"}',
                artifactContract="rca_external_result_v1",
                artifactContractSchemaVersion=1,
                artifactRequiredFields=("result",),
                frozenArtifactContract=FROZEN_RESULT_CONTRACT,
                capabilityCallId=request.capability_call_id,
                attemptNumber=request.attempt_number,
                allowSkillRequests=False,
            ),
            self.runtime_config,
        )
        content = structured["content"]
        if not isinstance(content, str):
            content = json.dumps(
                content, ensure_ascii=True, separators=(",", ":"), sort_keys=True
            )
        return DispatchSubmissionCompleted(
            title=str(structured["title"]),
            mime_type=str(structured["mimeType"]),
            content=content,
            provider_id=self.provider_id,
            adapter=self.adapter,
            usage=usage,
        )


class Provider:
    def __init__(self, prepared: PreparedHttpProvider) -> None:
        self.prepared = prepared

    def preflight(self) -> PreparedHttpProvider:
        return self.prepared


class CrashableCanonicalService(ResearchDispatchService):
    def __init__(self, repository) -> None:
        super().__init__(repository=repository)
        self.crash_before_finalize = True

    def record_completed_result(
        self,
        fence,
        *,
        owner_id: str,
        workspace_id: str,
        result: DispatchSubmissionCompleted,
    ) -> bool:
        if self.crash_before_finalize:
            raise ArtifactStorageError("injected_finalization_crash")
        storage_ref = f"durable/research-dispatch/{fence.dispatch_id}/result.json"
        if not self._repository.reserve_dispatch_result_storage(
            fence,
            storage_type="local",
            storage_scope=fence.task_id,
            storage_ref=storage_ref,
        ):
            return False
        return self._repository.record_dispatch_completed_result(
            fence,
            owner_id=owner_id,
            workspace_id=workspace_id,
            result=result,
            artifact_contract="rca_external_result_v1",
            artifact_schema_digest=FROZEN_RESULT_CONTRACT.schema_digest,
            storage_type="local",
            storage_scope=fence.task_id,
            storage_ref=storage_ref,
            content_sha256=hashlib.sha256(result.content.encode("utf-8")).hexdigest(),
            artifact_storage_type="local",
            artifact_storage_scope=fence.task_id,
            artifact_storage_ref=(
                f"durable/research-dispatch/{fence.dispatch_id}/artifact.json"
            ),
            produced_artifact_type="evidence_summary",
            accepted_artifact_content=result.content,
        )


def _authorize(repository, connect, suffix: str):
    task, dispatch = _seed_with_archived_registry(repository, connect, suffix)
    fence = _claim(repository, task.task_id, f"worker-{suffix}")
    assert fence is not None
    authorized = repository.authorize_dispatch_submission(
        fence,
        owner_id="owner",
        workspace_id="workspace",
        expected_task_revision=1,
        submission_mode="single_attempt_no_replay",
    )
    assert authorized is not None
    return task, dispatch, authorized


def _checkpoint_request_body(authorized) -> bytes:
    return json.dumps(
        {
            "dispatchId": authorized.dispatch_id,
            "taskId": authorized.task_id,
            "executionDecisionId": authorized.execution_decision_id,
            "workUnitId": authorized.work_unit_id,
            "idempotencyKey": authorized.idempotency_key,
            "payload": authorized.work_unit_payload,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _expire_dispatch(connect, dispatch_id: str) -> None:
    with _namespaced_dispatch_mutation_connection(connect) as connection:
        connection.execute(
            "UPDATE openair_dispatch "
            "SET lease_expires_at = clock_timestamp() - interval '1 second' "
            "WHERE dispatch_id = %s",
            (dispatch_id,),
        )


def test_production_staged_response_recovers_once_and_finalizes_once(
    repository,
    postgres_environment,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "artifact_storage", "local")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    task, dispatch, authorized = _authorize(
        repository, postgres_environment, "rca_staged_recovery"
    )
    service = CrashableCanonicalService(repository)
    service.prepare_external_execution(
        authorized,
        owner_id="owner",
        workspace_id="workspace",
        execution_kind="provider_http",
        provider_id="openai_primary",
        adapter="huiyan_chat_completions",
        request_body=_checkpoint_request_body(authorized),
    )

    artifact = {
        "title": "RCA recovered result",
        "mimeType": "text/plain",
        "content": {"result": "real provider payload"},
    }
    with ControlledProvider(artifact) as http:
        prepared = PreparedHttpProvider(http.base_url)
        first = process_authorized_recovery(
            authorized,
            owner_id="owner",
            workspace_id="workspace",
            provider=Provider(prepared),
            service=service,
        )
        assert first.status == "retryable_error"
        assert first.error_code == "injected_finalization_crash"
        assert http.call_count == prepared.submit_count == 1

        call_id = dispatch_capability_call_id(authorized)
        staged = ExternalExecutionCheckpointRepository().get(
            capability_call_id=call_id
        )
        assert staged is not None
        assert staged.phase == "response_received"
        assert staged.response_kind == "dispatch_completed"
        assert staged.response_payload is not None
        assert _dispatch_row(
            postgres_environment, dispatch.dispatch_id
        )["external_action_started_at"] is not None

        _expire_dispatch(postgres_environment, dispatch.dispatch_id)
        recovered = repository.claim_authorized_recovery(
            lease_owner="recovery-rca",
            lease_seconds=60,
            task_id=task.task_id,
            owner_id="owner",
            workspace_id="workspace",
        )[0]
        service.crash_before_finalize = False

        def finalize_once():
            return process_authorized_recovery(
                recovered,
                owner_id="owner",
                workspace_id="workspace",
                provider=None,
                service=service,
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = [future.result() for future in (pool.submit(finalize_once), pool.submit(finalize_once))]

        assert any(result.status == "completed" and result.recorded for result in results)
        assert http.call_count == prepared.submit_count == 1

    completed = ExternalExecutionCheckpointRepository().get(
        capability_call_id=dispatch_capability_call_id(authorized)
    )
    assert completed is not None
    assert completed.phase == "canonical_completed"
    task_row = _task_row(postgres_environment, task.task_id)
    assert len(task_row["payload"]["results"]) == 1
    assert len(task_row["payload"]["artifacts"]) == 1


def test_tampered_staged_payload_fails_closed_without_second_http_call(
    repository,
    postgres_environment,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    task, dispatch, authorized = _authorize(
        repository, postgres_environment, "rca_tampered_payload"
    )
    service = CrashableCanonicalService(repository)
    service.prepare_external_execution(
        authorized,
        owner_id="owner",
        workspace_id="workspace",
        execution_kind="provider_http",
        provider_id="openai_primary",
        adapter="huiyan_chat_completions",
        request_body=_checkpoint_request_body(authorized),
    )
    artifact = {
        "title": "Valid staged result",
        "mimeType": "text/plain",
        "content": {"result": "provider payload before tampering"},
    }

    with ControlledProvider(artifact) as http:
        prepared = PreparedHttpProvider(http.base_url)
        first = process_authorized_recovery(
            authorized,
            owner_id="owner",
            workspace_id="workspace",
            provider=Provider(prepared),
            service=service,
        )
        assert first.status == "retryable_error"
        assert http.call_count == prepared.submit_count == 1

        call_id = dispatch_capability_call_id(authorized)
        with postgres_environment() as connection:
            connection.execute(
                """UPDATE openair_external_execution_checkpoints
                      SET response_payload = %s
                    WHERE capability_call_id = %s""",
                (b'{"tampered":true}', call_id),
            )

        _expire_dispatch(postgres_environment, dispatch.dispatch_id)
        recovered = repository.claim_authorized_recovery(
            lease_owner="recovery-integrity",
            lease_seconds=60,
            task_id=task.task_id,
            owner_id="owner",
            workspace_id="workspace",
        )[0]
        before_payload = _task_row(postgres_environment, task.task_id)["payload"]
        before_results = json.loads(json.dumps(before_payload["results"]))
        before_artifacts = json.loads(json.dumps(before_payload["artifacts"]))
        result = process_authorized_recovery(
            recovered,
            owner_id="owner",
            workspace_id="workspace",
            provider=None,
            service=service,
        )
        assert result.status == "failed"
        assert result.recorded is True
        assert result.error_code == "staged_external_result_invalid"
        assert http.call_count == prepared.submit_count == 1

    task_row = _task_row(postgres_environment, task.task_id)
    payload = task_row["payload"]
    decision = next(
        item
        for item in payload["executionDecisions"]
        if item["executionDecisionId"] == recovered.execution_decision_id
    )
    assert (decision["status"], decision["outcome"]) == ("closed", "blocked")
    assert task_row["status"] == payload["status"] == "running"
    assert payload["results"] == before_results
    assert payload["artifacts"] == before_artifacts
    dispatch_row = _dispatch_row(postgres_environment, dispatch.dispatch_id)
    assert dispatch_row["status"] == "failed"
    assert dispatch_row["last_error_code"] == "staged_external_result_invalid"


def test_ambiguous_submitted_commits_blocked_system_failure_and_preserves_value(
    repository,
    postgres_environment,
) -> None:
    task, dispatch, authorized = _authorize(
        repository, postgres_environment, "rca_ambiguous_submitted"
    )
    service = ResearchDispatchService(repository=repository)
    checkpoint = service.prepare_external_execution(
        authorized,
        owner_id="owner",
        workspace_id="workspace",
        execution_kind="provider_http",
        provider_id="openai_primary",
        adapter="huiyan_chat_completions",
        request_body=_checkpoint_request_body(authorized),
    )
    assert checkpoint is not None
    started = service.mark_external_action_started(
        authorized,
        provider_id="openai_primary",
        adapter="huiyan_chat_completions",
        lease_seconds=60,
    )
    assert started
    call_id = dispatch_capability_call_id(authorized)
    assert ExternalExecutionCheckpointRepository().mark_submitted(
        capability_call_id=call_id
    )
    before_payload = _task_row(postgres_environment, task.task_id)["payload"]
    before_results = json.loads(json.dumps(before_payload["results"]))
    before_artifacts = json.loads(json.dumps(before_payload["artifacts"]))

    _expire_dispatch(postgres_environment, dispatch.dispatch_id)
    recovered = repository.claim_authorized_recovery(
        lease_owner="recovery-ambiguous",
        lease_seconds=60,
        task_id=task.task_id,
        owner_id="owner",
        workspace_id="workspace",
    )[0]
    result = process_authorized_recovery(
        recovered,
        owner_id="owner",
        workspace_id="workspace",
        provider=None,
        service=service,
    )

    assert result.status == "failed"
    assert result.recorded is True
    assert result.error_code == "external_execution_result_unavailable"
    dispatch_row = _dispatch_row(postgres_environment, dispatch.dispatch_id)
    assert dispatch_row["status"] == "failed"
    assert dispatch_row["last_error_code"] == "external_execution_result_unavailable"

    task_row = _task_row(postgres_environment, task.task_id)
    payload = task_row["payload"]
    decision = next(
        item
        for item in payload["executionDecisions"]
        if item["executionDecisionId"] == recovered.execution_decision_id
    )
    assert (decision["status"], decision["outcome"]) == ("closed", "blocked")
    assert task_row["status"] == payload["status"] == "running"
    assert payload["results"] == before_results
    assert payload["artifacts"] == before_artifacts
    work_unit = next(
        item for item in payload["workUnits"]
        if item["workUnitId"] == recovered.work_unit_id
    )
    assert work_unit["status"] == "failed"
    assert work_unit["terminalReasonCode"] == "external_execution_result_unavailable"
    capability_call = next(
        item for item in payload["capabilityCalls"]
        if item["capabilityCallId"] == call_id
    )
    assert capability_call["status"] == "failed"
