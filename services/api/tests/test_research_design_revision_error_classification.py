from __future__ import annotations

import pytest

from app.execution.research_start_provider_adapter import (
    ProviderContractError,
    ProviderKnownFailureError,
    ProviderTransportUncertainError,
)
from app.modules.research_design_service import (
    RESEARCH_DESIGN_REVISION_RETRY_MESSAGE,
    RESEARCH_DESIGN_REVISION_USER_MESSAGE,
    classify_research_design_revision_failure,
)


def test_semantic_revision_mismatch_is_the_only_user_adjustment_message() -> None:
    error = ProviderContractError(
        safe_error_code="research_design_revision_semantic_mismatch",
        safe_field_paths=("/workItems/0/objective",),
    )

    classified = classify_research_design_revision_failure(error)

    assert str(classified) == RESEARCH_DESIGN_REVISION_USER_MESSAGE
    assert "调整研究步骤" in str(classified)


@pytest.mark.parametrize(
    "error",
    [
        RuntimeError("HUIYAN planner provider configuration is incomplete"),
        ProviderKnownFailureError(status_code=503),
        ProviderTransportUncertainError(),
        TimeoutError("planner timeout"),
        RuntimeError("internal provider failure"),
        ProviderContractError(safe_error_code="planner_semantic_proposal_invalid"),
    ],
)
def test_infrastructure_or_non_revision_contract_failure_uses_neutral_retry_message(error: Exception) -> None:
    classified = classify_research_design_revision_failure(error)
    message = str(classified)

    assert message == RESEARCH_DESIGN_REVISION_RETRY_MESSAGE
    assert "调整研究步骤" not in message
    assert "provider" not in message.lower()
    assert "transport" not in message.lower()
    assert "configuration" not in message.lower()
    assert "internal" not in message.lower()
