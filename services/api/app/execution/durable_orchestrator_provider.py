"""Durable provider boundary with a production Responses transport."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

from app.execution.durable_orchestrator_runtime import FrozenOrchestrationExecutionRequest
from app.execution.openai_responses_provider import (
    HttpOpenAIResponsesTransport,
    OpenAIResponsesProviderAdapter,
    OpenAIResponsesTransportRequest,
    PreparedOpenAIResponsesExecution,
    ProviderContractError,
    ProviderExecutionError,
    ProviderExecutionResult,
)
from app.modules.provider_registry import ProviderRuntimeConfig


_TEST_FAKE_SWITCH = "OPENAIR_DURABLE_TEST_FAKE_TRANSPORT"


class _TestMemoryTransport:
    """A process-local fixture transport, reachable only in exact test mode."""

    is_fake_transport = True

    def execute(
        self, request: OpenAIResponsesTransportRequest, runtime_config: ProviderRuntimeConfig,
    ) -> tuple[Mapping[str, Any], Mapping[str, Any] | None]:
        log_path = os.environ.get("OPENAIR_DURABLE_TEST_FAKE_TRANSPORT_LOG", "")
        if log_path:
            with Path(log_path).open("a", encoding="ascii") as stream:
                stream.write("called\n")
        delay = float(os.environ.get("OPENAIR_DURABLE_TEST_FAKE_TRANSPORT_DELAY_SECONDS", "0"))
        if delay > 0:
            time.sleep(delay)
        if os.environ.get("OPENAIR_DURABLE_TEST_FAKE_TRANSPORT_RESPONSE_LOSS") == "1":
            from app.execution.openai_responses_provider import ProviderTransportUncertainError

            raise ProviderTransportUncertainError("Provider transport result is unknown")
        status = os.environ.get("OPENAIR_DURABLE_TEST_FAKE_TRANSPORT_HTTP_STATUS")
        if status == "400":
            from app.execution.openai_responses_provider import ProviderResponseError

            raise ProviderResponseError("Provider returned an unsuccessful response")
        if status == "500":
            from app.execution.openai_responses_provider import ProviderTransportUncertainError

            raise ProviderTransportUncertainError("Provider transport result is unknown")
        try:
            payload = json.loads(request.inputText)
        except json.JSONDecodeError:
            payload = {}
        snapshot = payload.get("frozenInputSnapshot") if isinstance(payload, dict) else None
        unit = snapshot.get("activeWorkUnit") if isinstance(snapshot, dict) else None
        if isinstance(unit, dict) and unit.get("phase") == "review_artifact":
            materials = unit.get("reviewMaterials")
            usable = (
                isinstance(materials, list)
                and bool(materials)
                and all(
                    isinstance(material, dict)
                    and isinstance(material.get("content"), str)
                    and bool(material["content"].strip())
                    and material.get("truncated") is False
                    for material in materials
                )
            )
            review = {
                "verdict": "usable" if usable else "needs_revision",
                "basis": (
                    "Deterministic test review accepted the isolated artifact."
                    if usable
                    else "Deterministic test review could not verify a complete artifact body."
                ),
                "issues": [] if usable else [{
                    "code": "incomplete_test_material",
                    "severity": "blocking",
                    "message": "The deterministic reviewer requires a complete non-empty artifact body.",
                }],
                "missingInputs": [] if usable else ["complete_artifact_body"],
            }
            return {
                "title": "Deterministic independent review",
                "mimeType": "text/plain",
                "content": json.dumps(review, ensure_ascii=True, separators=(",", ":")),
            }, None
        expected = payload.get("expectedArtifactContract") if isinstance(payload, dict) else None
        fields = expected.get("requiredFields") if isinstance(expected, dict) else None
        if not isinstance(fields, list) or not fields or not all(isinstance(field, str) and field for field in fields):
            raise ProviderExecutionError("Frozen artifact contract is unavailable")
        artifact = {field: {"status": "isolated"} for field in fields}
        return {
            "title": "Isolated durable test result",
            "mimeType": "text/plain",
            "content": json.dumps(artifact, ensure_ascii=True, separators=(",", ":"), sort_keys=True),
        }, None


def _adapter() -> OpenAIResponsesProviderAdapter:
    if os.environ.get("OPENAIR_ENV") == "test" and os.environ.get(_TEST_FAKE_SWITCH) == "1":
        return OpenAIResponsesProviderAdapter(transport=_TestMemoryTransport())
    return OpenAIResponsesProviderAdapter(transport=HttpOpenAIResponsesTransport())


def prepare_durable_orchestration(request: FrozenOrchestrationExecutionRequest) -> PreparedOpenAIResponsesExecution:
    """Validate registry and environment before recording execution_started."""
    return _adapter().prepare(request)


def execute_prepared_durable_orchestration(
    prepared: PreparedOpenAIResponsesExecution,
    *,
    provider_evidence: Mapping[str, Any] | None = None,
    owner_id: str | None = None,
    workspace_id: str | None = None,
    task_id: str | None = None,
) -> dict[str, Any]:
    if provider_evidence is not None:
        call_id = str(provider_evidence.get("capabilityCallId") or "").strip()
        attempt_number = provider_evidence.get("attemptNumber")
        if not call_id or isinstance(attempt_number, bool) or not isinstance(attempt_number, int):
            raise ProviderExecutionError("Provider attempt evidence is invalid")
        prepared = replace(
            prepared,
            request=replace(
                prepared.request,
                capabilityCallId=call_id,
                attemptNumber=attempt_number,
            ),
        )
    try:
        result = _adapter().execute_prepared(prepared)
    except ProviderContractError as exc:
        # Schema/contract rejection happens after the provider call; still meter.
        usage = getattr(exc, "usage", None)
        if owner_id and isinstance(usage, Mapping) and usage:
            from app.modules.token_ledger_repository import meter_call

            meter_call(
                user_id=owner_id,
                source="durable_orchestration",
                usage_wire=dict(usage),
                workspace_id=workspace_id,
                task_id=task_id,
                provider=prepared.provider_id,
                model=prepared.request.model,
                status="failed",
            )
        raise
    # The openai_responses adapter makes a real provider call for every agent
    # unit (both the skillRequest routing turn and the final-artifact turn).
    # ``_artifact_result`` drops ``usage`` from the returned dict, so account
    # for the tokens here before that boundary (U-04, E path).
    _record_orchestration_usage(
        result=result,
        owner_id=owner_id,
        workspace_id=workspace_id,
        task_id=task_id,
        provider_id=prepared.provider_id,
        model=prepared.request.model,
    )
    return _artifact_result(result)


def _record_orchestration_usage(
    *,
    result: ProviderExecutionResult,
    owner_id: str | None,
    workspace_id: str | None,
    task_id: str | None,
    provider_id: str,
    model: str,
) -> None:
    """Best-effort metering for one orchestration provider turn (U-04, E path).

    Mirrors ``durable_skill_runtime._record_durable_skill_usage``: the orchestration
    request already carries owner/task context, so account here rather than threading
    usage through the orchestrator. Fail-open when there is no owner (the test-compat
    entry point ``execute_durable_orchestration`` passes none).
    """
    if not owner_id:
        return
    usage_wire = dict(result.usage) if result.usage else None
    if usage_wire is None:
        usage_wire = {
            "inputTokens": 0,
            "outputTokens": 1,
            "totalTokens": 1,
            "estimated": True,
            "raw": {"reason": "orchestration_usage_missing_floor"},
        }
    from app.modules.token_ledger_repository import meter_call

    meter_call(
        user_id=owner_id,
        source="durable_orchestration",
        usage_wire=usage_wire,
        workspace_id=workspace_id,
        task_id=task_id,
        provider=provider_id,
        model=model,
        status="success",
    )


def execute_durable_orchestration(request: FrozenOrchestrationExecutionRequest) -> dict[str, Any]:
    """Compatibility entry point for focused adapter tests."""
    return execute_prepared_durable_orchestration(prepare_durable_orchestration(request))


def _artifact_result(result: ProviderExecutionResult) -> dict[str, Any]:
    if result.skillRequest is not None:
        return {"skillRequest": dict(result.skillRequest)}
    if result.content is None or result.title is None or result.mimeType is None:
        raise ProviderExecutionError("Provider did not return a final artifact")
    content = result.content.encode("utf-8")
    return {
        "artifacts": [{
            "contentBase64": base64.b64encode(content).decode("ascii"),
            "contentType": result.mimeType,
            "presentation": {
                "title": result.title,
                "mimeType": result.mimeType,
                "metadata": dict(result.metadata),
                "contentSha256": hashlib.sha256(content).hexdigest(),
                "contentSize": len(content),
            },
        }],
    }
