from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.execution.research_dispatch_provider import DispatchSubmissionCompleted
from app.modules import research_dispatch_service as dispatch_service_module
from app.modules.research_dispatch_service import ResearchDispatchService
from app.modules.research_orchestration_repository import (
    AuthorizedDispatch,
    ExecutionContinuationDirective,
)


def test_skill_completion_projects_validated_fact_before_repository_persistence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    projection = {
        "contract_version": 2,
        "materializer_ref": "pvd:experimental-system-context:v1",
        "facts": [
            {
                "fact_key": "experimental_system_context",
                "source_field": "experimental_system_context",
            }
        ],
    }

    class Repository:
        def __init__(self) -> None:
            self.completed_kwargs = None

        def record_dispatch_completed_result(self, _fence, **kwargs):
            self.completed_kwargs = kwargs
            return True

        def prepare_execution_continuation(self, **_kwargs):
            return ExecutionContinuationDirective(
                action="none",
                expected_task_revision=5,
                next_instance_id=None,
                selection_request_id=None,
                reason_code=None,
            )

        def finalize_dispatch_completed_result(self, _fence, **_kwargs):
            return True

    from app.execution import research_dispatch_skill_provider

    monkeypatch.setattr(
        research_dispatch_skill_provider,
        "validate_completed_skill_dispatch_result",
        lambda *_args, **_kwargs: SimpleNamespace(
            artifactContract="pvd_context_v1",
            artifactSchemaDigest="a" * 64,
            orchestrationPlan={
                "assignmentRole": "producer",
                "executionPurpose": "completion",
                "scientificStateProjection": projection,
            },
        ),
    )

    blobs: dict[str, bytes] = {}

    def _store(_task_id, storage_ref, data, _content_type, **_kwargs):
        blobs[storage_ref] = data
        return SimpleNamespace(
            storage_type="local",
            storage_ref=storage_ref,
            storage_scope="task-pvd",
        )

    monkeypatch.setattr(
        dispatch_service_module.artifact_write_service,
        "store_durable_bytes",
        _store,
    )
    monkeypatch.setattr(
        dispatch_service_module.artifact_write_service,
        "read_durable_bytes",
        lambda _task_id, _storage_type, storage_ref, *_args, **_kwargs: blobs[
            storage_ref
        ],
    )
    monkeypatch.setattr(
        ResearchDispatchService,
        "_recover_confirmed_semantic_material_gap",
        lambda *_args, **_kwargs: False,
    )

    repository = Repository()
    authorized = AuthorizedDispatch(
        dispatch_id="dispatch-pvd-node1",
        task_id="task-pvd",
        execution_decision_id="decision-pvd-node1",
        work_unit_id="work-pvd-node1",
        idempotency_key="dispatch-pvd-node1:key",
        lease_owner="worker",
        attempt_count=1,
        authorization_task_revision=4,
        work_unit_payload={
            "executorRef": {
                "executorType": "skill",
                "executorId": "pvd_experimental_context_structuring",
            },
            "instruction": "structure context",
        },
        submission_mode="durable_idempotent",
    )
    content = '{"experimental_system_context":{"contract":"ctx"}}'

    assert ResearchDispatchService(repository).record_completed_result(
        authorized,
        owner_id="owner",
        workspace_id="workspace",
        result=DispatchSubmissionCompleted(
            title="PVD context",
            mime_type="application/json",
            content=content,
            provider_id="provider",
            adapter="huiyan_chat_completions",
        ),
    ) is True

    assert repository.completed_kwargs is not None
    assert repository.completed_kwargs["scientific_state_fact_projections"] == {
        "experimental_system_context": {"contract": "ctx"}
    }
    assert (
        repository.completed_kwargs["scientific_state_projection_declaration"]
        == projection
    )
