from __future__ import annotations

from dataclasses import replace

import pytest

from app.execution.research_dispatch_provider import (
    DispatchProviderCapabilityError,
    DispatchSubmissionAccepted,
    DispatchSubmissionQueryResult,
    DispatchSubmissionRequest,
    RegistryDispatchProviderFactory,
    require_safe_submission,
)
from app.execution.research_dispatch_huiyan_provider import HuiyanResearchDispatchProvider
from app.modules.provider_registry import ProviderSubmissionSafety, load_provider_registry


SAFE = ProviderSubmissionSafety(
    acceptsIdempotencyKey=True,
    returnsReceiptOrRemoteRunId=True,
    queryByIdempotencyKey=True,
    queryByReceiptOrRemoteRunId=False,
)


def test_safe_fake_capability_matrix_passes_preflight() -> None:
    require_safe_submission(SAFE)


@pytest.mark.parametrize(
    ("capabilities", "code"),
    (
        (replace(SAFE, acceptsIdempotencyKey=False), "provider_idempotency_unsupported"),
        (replace(SAFE, returnsReceiptOrRemoteRunId=False), "provider_durable_receipt_unsupported"),
        (replace(SAFE, queryByIdempotencyKey=False), "provider_submission_query_unsupported"),
    ),
)
def test_missing_submission_capability_fails_closed_before_transport(
    capabilities: ProviderSubmissionSafety,
    code: str,
) -> None:
    with pytest.raises(DispatchProviderCapabilityError, match=code):
        require_safe_submission(capabilities)


def test_huiyan_single_attempt_mode_passes_without_claiming_replay_safety() -> None:
    capabilities = load_provider_registry().require("openai_primary").submissionSafety

    require_safe_submission(capabilities)
    assert capabilities.submissionMode == "single_attempt_no_replay"
    assert capabilities.supports_safe_submission is True


def test_registry_factory_requires_explicit_safe_provider_and_never_uses_default() -> None:
    factory = RegistryDispatchProviderFactory()

    with pytest.raises(Exception, match="not registered"):
        factory.create("unknown_provider")
    assert isinstance(factory.create("openai_primary"), HuiyanResearchDispatchProvider)
    with pytest.raises(DispatchProviderCapabilityError, match="dispatch_provider_binding_missing"):
        factory.create("")


def test_safe_fake_replay_uses_one_key_and_returns_one_durable_receipt() -> None:
    receipts: dict[str, str] = {}
    seen_keys: list[str] = []

    def submit(request: DispatchSubmissionRequest) -> DispatchSubmissionAccepted:
        seen_keys.append(request.idempotency_key)
        receipt = receipts.setdefault(request.idempotency_key, "fake-durable-receipt-1")
        return DispatchSubmissionAccepted(receipt)

    request = DispatchSubmissionRequest(
        dispatch_id="dispatch-1",
        task_id="task-1",
        execution_decision_id="decision-1",
        work_unit_id="work-unit-1",
        idempotency_key="stable-key-1",
        owner_id="owner-1",
        workspace_id="workspace-1",
        payload={"frozen": True},
    )

    assert submit(request) == submit(request)
    assert seen_keys == ["stable-key-1", "stable-key-1"]
    assert receipts == {"stable-key-1": "fake-durable-receipt-1"}


def test_receipt_only_query_cannot_authorize_automatic_recovery() -> None:
    receipt_only = replace(
        SAFE,
        queryByIdempotencyKey=False,
        queryByReceiptOrRemoteRunId=True,
    )

    with pytest.raises(DispatchProviderCapabilityError, match="provider_idempotency_query_unsupported"):
        require_safe_submission(receipt_only)


@pytest.mark.parametrize(
    "kwargs",
    (
        {"status": "accepted"},
        {"status": "not_submitted", "remote_run_id": "fabricated"},
        {"status": "known_rejected"},
        {"status": "unknown", "remote_run_id": "contradiction"},
        {"status": "invalid"},
    ),
)
def test_query_result_rejects_missing_or_contradictory_external_facts(kwargs: dict) -> None:
    with pytest.raises(ValueError):
        DispatchSubmissionQueryResult(**kwargs)


def test_provider_error_code_cannot_carry_free_text_or_response_values() -> None:
    with pytest.raises(ValueError, match="bounded stable identifier"):
        DispatchSubmissionQueryResult(status="unknown", error_code="secret provider body: value=7")
