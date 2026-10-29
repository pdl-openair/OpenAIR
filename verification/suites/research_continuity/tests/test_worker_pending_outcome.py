"""Existing Worker entry catches a proved pending marker without a final result."""

from app.execution import research_dispatch_worker as worker
from app.execution.research_internal_loop_pending import DispatchProviderInternalExecutionPending
from tests import test_research_dispatch_worker as support


def test_internal_pending_preserves_owner_scope_and_running_outcome(monkeypatch):
    monkeypatch.setattr(worker, "_observe_openair_provider_execution", lambda *args, **kwargs: None)

    def pending():
        raise DispatchProviderInternalExecutionPending("software-child-call")

    prepared = support.Prepared(on_submit=pending)
    result, service = support._run(prepared)
    assert result.status == "submitted"
    assert len(prepared.calls) == 1
    assert len(service.outcomes) == 1
    fence, outcome, _stopped = service.outcomes[0]
    assert outcome["outcome"] == "running"
    assert outcome["owner_id"] == prepared.calls[0].owner_id
    assert outcome["workspace_id"] == prepared.calls[0].workspace_id
    assert fence.dispatch_id == prepared.calls[0].dispatch_id
    assert not service.completed_results
