"""Regression: meter provider tokens even when business outcome fails."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from llm_core.token_usage import to_wire_usage
from app.execution import durable_skill_runtime
from app.execution.research_dispatch_provider import DispatchProviderKnownRejected
from app.execution.research_dispatch_worker import _record_rejected_dispatch_usage
from app.execution.run_single_structured_skill import _record_structured_skill_usage
from app.modules.json_user_repository import JsonUserRepository
from app.modules.provider_transport_evidence import ProviderHttpAttempt, ProviderAttemptEvidenceError
from app.modules.quota_service import QuotaService
from app.modules.token_ledger_repository import JsonTokenLedgerRepository
import hashlib


@pytest.fixture()
def ledger(tmp_path: Path) -> JsonTokenLedgerRepository:
    repo = JsonTokenLedgerRepository(ledger_dir=tmp_path / "ledger")
    repo.bootstrap()
    return repo


@pytest.fixture()
def billed_user(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ledger: JsonTokenLedgerRepository):
    users = JsonUserRepository(users_dir=tmp_path / "users", workspaces_dir=tmp_path / "workspaces")
    users.bootstrap()
    user = users.create_user(
        email="meter@x.test",
        username="meter_user",
        hashed_password="x",
        display_name="meter",
        is_active=True,
    )
    users.update_user_quota_fields(user["user_id"], quota_tier="trial", period_used=0, bonus_quota=0)
    quota = QuotaService(users)
    monkeypatch.setattr(
        "app.modules.token_ledger_repository.get_token_ledger_repository", lambda: ledger
    )
    monkeypatch.setattr("app.modules.quota_service.get_quota_service", lambda: quota)
    return user, quota, ledger


def test_known_rejected_carries_usage() -> None:
    usage = {"inputTokens": 11, "outputTokens": 7, "totalTokens": 18}
    error = DispatchProviderKnownRejected("artifact_schema_invalid", usage=usage)
    assert error.code == "artifact_schema_invalid"
    assert error.usage == usage


def test_record_rejected_dispatch_usage_settles(billed_user) -> None:
    user, quota, ledger = billed_user
    usage = to_wire_usage(
        {"input_tokens": 20, "output_tokens": 10, "total_tokens": 30, "estimated": False, "raw": None}
    )
    _record_rejected_dispatch_usage(
        owner_id=user["user_id"],
        workspace_id="ws_1",
        task_id="task_1",
        usage=usage,
        provider_id="openai_primary",
    )
    rows, _ = ledger.list_usage(user["user_id"])
    assert len(rows) == 1
    assert rows[0]["status"] == "failed"
    assert rows[0]["total_tokens"] == 30
    assert rows[0]["source"] == "durable_dispatch"
    assert quota.get_quota_state(user["user_id"])["periodUsed"] == 30


def test_structured_skill_usage_settles_quota(billed_user) -> None:
    user, quota, ledger = billed_user
    _record_structured_skill_usage(
        user["user_id"],
        "task_structured",
        {
            "prompt_tokens": 12,
            "completion_tokens": 8,
            "total_tokens": 20,
            "provider": "huiyan",
            "model": "glm",
        },
    )
    rows, _ = ledger.list_usage(user["user_id"])
    assert len(rows) == 1
    assert rows[0]["source"] == "structured_skill"
    assert rows[0]["total_tokens"] == 20
    assert quota.get_quota_state(user["user_id"])["periodUsed"] == 20


def test_durable_skill_meters_on_artifact_contract_failure(
    billed_user,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HTTP 200 + invalid artifact must still settle (even when usage is omitted)."""
    user, quota, ledger = billed_user

    class _ReservedAttemptRepository:
        def begin(self, **kwargs):
            call_id = kwargs["capability_call_id"]
            if not call_id or kwargs["attempt_number"] != 1:
                raise ProviderAttemptEvidenceError("provider_attempt_identity_invalid")
            return ProviderHttpAttempt(
                attempt_id=f"attempt-{call_id}",
                capability_call_id=call_id,
                attempt_number=1,
                request_hash=hashlib.sha256(kwargs["request_body"]).hexdigest(),
                provider_endpoint_identity="test:endpoint",
            )

        def finish(self, _attempt, **_kwargs) -> None:
            return None

    class Response:
        status_code = 200
        headers: dict[str, str] = {}

        def raise_for_status(self) -> None:
            return None

        @property
        def content(self) -> bytes:
            return json.dumps(
                {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {"title": "bad", "content": "not-a-valid-research-config"}
                                )
                            }
                        }
                    ]
                    # usage intentionally omitted → estimate path
                },
                ensure_ascii=True,
                separators=(",", ":"),
            ).encode("utf-8")

    class Client:
        def __init__(self, *args, **kwargs) -> None:
            return None

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            return None

        def post(self, *args, **kwargs) -> Response:
            return Response()

    monkeypatch.setattr(durable_skill_runtime, "ProviderHttpAttemptRepository", _ReservedAttemptRepository)
    monkeypatch.setattr(
        durable_skill_runtime,
        "httpx",
        SimpleNamespace(Client=Client, HTTPStatusError=httpx.HTTPStatusError, HTTPError=httpx.HTTPError),
        raising=False,
    )
    monkeypatch.setenv("LLM_PROVIDER_API_KEY", "test-key")
    monkeypatch.setenv("LLM_PROVIDER_BASE_URL", "https://provider.test")
    monkeypatch.setenv("LLM_PROVIDER_MODEL_TERRA", "frozen-terra-model")

    result = durable_skill_runtime.execute_durable_skill(
        {
            "skillId": "intent_understanding",
            "parameters": {},
            "frozenGoal": "Bounded research goal.",
            "logicalProviderId": "openai_primary",
            "modelProfile": "terra",
            "ownerId": user["user_id"],
            "workspaceId": "ws_meter",
            "taskId": "task_meter",
            "providerEvidence": {
                "capabilityCallId": "call-meter-artifact-fail",
                "attemptNumber": 1,
            },
        }
    )
    assert result["state"] == "failed"
    rows, _ = ledger.list_usage(user["user_id"])
    assert len(rows) == 1
    assert rows[0]["status"] == "failed"
    assert rows[0]["source"] == "durable_dispatch"
    assert rows[0]["total_tokens"] >= 1
    assert rows[0]["estimated"] is True
    assert quota.get_quota_state(user["user_id"])["periodUsed"] == rows[0]["total_tokens"]


def test_record_dispatch_usage_applies_floor_when_usage_missing(billed_user) -> None:
    from app.execution.research_dispatch_provider import DispatchSubmissionCompleted
    from app.execution.research_dispatch_worker import _record_dispatch_usage

    user, quota, ledger = billed_user
    accepted = DispatchSubmissionCompleted(
        title="t",
        mime_type="text/plain",
        content="hello",
        provider_id="openai_primary",
        adapter="huiyan_chat_completions",
        usage=None,
    )
    _record_dispatch_usage(
        owner_id=user["user_id"],
        workspace_id="ws_1",
        task_id="task_floor",
        accepted=accepted,
    )
    rows, _ = ledger.list_usage(user["user_id"])
    assert len(rows) == 1
    assert rows[0]["total_tokens"] == 1
    assert rows[0]["estimated"] is True
    assert quota.get_quota_state(user["user_id"])["periodUsed"] == 1
