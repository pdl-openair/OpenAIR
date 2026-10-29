from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.execution.research_dispatch_huiyan_provider import (
    PreparedHuiyanResearchDispatch,
    _frozen_request,
    _resolved_input_memory,
)
from app.execution import research_dispatch_huiyan_provider as huiyan_provider
from app.execution.openai_responses_provider import (
    ProviderContractError,
    ProviderTransportNotSubmittedError,
    ProviderTransportUncertainError,
)
from app.execution.research_dispatch_provider import (
    DispatchProviderKnownRejected,
    DispatchProviderResultUnknown,
    DispatchSubmissionRequest,
)
from app.models.research_external_result import RejectedResponseDiagnosis, SafeValidatorDiagnostic
from app.models.research_orchestration import ExecutionAssignment, ResearchTaskV12, WorkUnit
from app.modules.provider_registry import ProviderSubmissionSafety
from app.modules.research_orchestration_repository import ResearchOrchestrationPersistenceError


FIXTURE = Path(__file__).resolve().parents[3] / "packages/shared/test-fixtures/research-orchestration-v1.2-valid.json"


class Repository:
    def __init__(self, task, snapshot):
        self.task = task
        self.snapshot = snapshot
        self.agent_input_materials = []
        self.agent_input_material_reads = []
        self.review_materials = []
        self.review_material_reads = []

    def load(self, **_scope):
        return self.task

    def load_registry_snapshot(self, _content_hash):
        return self.snapshot

    def read_agent_input_materials_for_dispatch(self, **scope):
        self.agent_input_material_reads.append(scope)
        return self.agent_input_materials

    def read_review_materials_for_dispatch(self, **scope):
        self.review_material_reads.append(scope)
        return self.review_materials


def _request_and_repository():
    from app.modules.research_orchestration_resolver import build_registry_snapshot

    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))["researchTask"]
    decision = payload["executionDecisions"][0]
    assignment = decision["assignments"][0]
    agent_ref = {
        "executorType": "agent",
        "executorId": "research_question_refinement",
        "definitionVersion": 1,
    }
    actor_ref = {
        "actorType": "agent",
        "actorId": "research_question_refinement",
        "definitionVersion": 1,
    }
    decision["strategy"] = "specialist_agent"
    decision["candidateEvaluations"][0]["candidateRef"] = {
        "agentId": "research_question_refinement",
        "definitionVersion": 1,
    }
    decision["candidateEvaluations"][0]["supportedRole"] = "primary"
    decision["runtimeReadiness"][0]["capabilityRef"] = {
        "agentId": "research_question_refinement",
        "definitionVersion": 1,
    }
    assignment["executorRef"] = agent_ref
    assignment["responsibleActorRef"] = actor_ref
    payload["workUnits"][0]["executorRef"] = agent_ref
    payload["workUnits"][0]["responsibleActorRef"] = actor_ref
    task = ResearchTaskV12.model_validate(payload)
    work = task.work_units[0]
    request = DispatchSubmissionRequest(
        dispatch_id="dispatch-test",
        task_id=task.task_id,
        execution_decision_id=work.execution_decision_id,
        work_unit_id=work.work_unit_id,
        idempotency_key=work.idempotency_key,
        owner_id="owner",
        workspace_id="workspace",
        payload=work.model_dump(mode="json", by_alias=True),
    )
    return request, Repository(task, build_registry_snapshot().envelope)


def test_semantic_memory_refs_are_materialized_before_provider_transport() -> None:
    task = SimpleNamespace(
        model_dump=lambda **_kwargs: {
            "goalSummary": "调研 2024 年以来 GraphRAG，并设计可复现实验。",
        }
    )
    work = SimpleNamespace(
        input_memory_refs=["user_goal", "search_target", "validation_target", "opaque_memory_ref"],
        instruction="比较 GraphRAG 与混合检索 RAG，并形成验证设计。",
    )

    assert _resolved_input_memory(task, work) == {
        "user_goal": "调研 2024 年以来 GraphRAG，并设计可复现实验。",
        "search_target": "比较 GraphRAG 与混合检索 RAG，并形成验证设计。",
        "validation_target": "比较 GraphRAG 与混合检索 RAG，并形成验证设计。",
    }


def test_producer_dispatch_uses_frozen_output_contract() -> None:
    request, repository = _request_and_repository()

    frozen = _frozen_request(request, provider_id="openai_primary", repository=repository)

    assert frozen.modelProfile == "terra"
    assert frozen.allowSkillRequests is False
    assert frozen.artifactContract == "research_question_refinement_artifact_v1"
    assert frozen.artifactSchema["contractId"] == frozen.artifactContract
    assert frozen.orchestrationPlan["artifactKind"] == "research_question_refinement"
    assert repository.agent_input_material_reads == []
    assert repository.review_material_reads == []


def test_primary_agent_dispatch_materializes_current_canonical_artifact_revision() -> None:
    request, repository = _request_and_repository()
    payload = repository.task.model_dump(mode="json", by_alias=True)
    decision = payload["executionDecisions"][0]
    assignment = decision["assignments"][0]
    work = payload["workUnits"][0]
    artifact_id = payload["artifacts"][0]["artifactId"]
    assignment["inputRefs"] = [artifact_id]
    work["inputArtifactRefs"] = [artifact_id]
    work["inputMemoryRefs"] = []
    repository.task = ResearchTaskV12.model_validate(payload)
    work_model = repository.task.work_units[0]
    request = DispatchSubmissionRequest(
        **{**request.__dict__, "payload": work_model.model_dump(mode="json", by_alias=True)}
    )
    repository.agent_input_materials = [{
        "artifactId": artifact_id,
        "artifactType": "evidence_summary",
        "mimeType": "text/markdown",
        "content": "# V2\nuser canonical revision",
        "contentSha256": "b" * 64,
        "currentVersionId": "v_user_2",
    }]

    frozen = _frozen_request(request, provider_id="openai_primary", repository=repository)

    assert [dict(row) for row in frozen.frozenInputSnapshot["inputArtifactMaterials"]] == repository.agent_input_materials
    assert repository.agent_input_material_reads == [{
        "task_id": request.task_id,
        "owner_id": request.owner_id,
        "workspace_id": request.workspace_id,
        "execution_decision_id": request.execution_decision_id,
        "work_unit_id": request.work_unit_id,
    }]


def _review_request_and_repository():
    request, repository = _request_and_repository()
    payload = repository.task.model_dump(mode="json", by_alias=True)
    decision = payload["executionDecisions"][0]
    assignment = decision["assignments"][0]
    assignment["role"] = "reviewer"
    assignment["executorRef"] = {"executorType": "agent", "executorId": "result_reliability_audit", "definitionVersion": 1}
    assignment["responsibleActorRef"] = {"actorType": "agent", "actorId": "result_reliability_audit", "definitionVersion": 1}
    work = payload["workUnits"][0]
    work["executorRef"] = assignment["executorRef"]
    work["responsibleActorRef"] = assignment["responsibleActorRef"]
    work["allowedSkillRefs"] = []
    work["inputArtifactRefs"] = ["artifact-verified"]
    assignment["inputRefs"] = ["artifact-verified"]
    assignment["expectedArtifactTypes"] = []
    assignment["acceptanceCriteria"] = []
    work["expectedArtifactTypes"] = []
    work["acceptanceCriteria"] = []
    decision["completionPolicy"]["reviewerAssignmentIds"] = [assignment["assignmentId"]]
    decision["completionPolicy"]["reviewRequirement"] = "all_reviewers_approve"
    reviewer_assignment = ExecutionAssignment.model_validate(assignment)
    reviewer_work = WorkUnit.model_validate(work)
    repository.task = SimpleNamespace(
        work_units=[reviewer_work],
        execution_decisions=[
            SimpleNamespace(
                execution_decision_id=decision["executionDecisionId"],
                assignments=[reviewer_assignment],
            )
        ],
    )
    request = DispatchSubmissionRequest(
        **{**request.__dict__, "payload": reviewer_work.model_dump(mode="json", by_alias=True)}
    )
    repository.review_materials = [
        {
            "artifactId": "artifact-verified",
            "title": "Verified research result",
            "mimeType": "text/plain",
            "content": '{"result":"validated"}',
            "truncated": False,
            "contentSha256": "a" * 64,
        }
    ]
    return request, repository


def test_reviewer_dispatch_uses_review_contract_and_verified_artifact_content() -> None:
    request, repository = _review_request_and_repository()

    frozen = _frozen_request(request, provider_id="openai_primary", repository=repository)

    assert frozen.artifactContract == "independent_result_review_v1"
    assert frozen.artifactContract != "result_reliability_audit_artifact_v1"
    assert "artifactKind" not in frozen.orchestrationPlan
    assert tuple(frozen.artifactSchema["required"]) == ("verdict", "basis", "issues", "missingInputs")
    materials = [dict(row) for row in frozen.frozenInputSnapshot["reviewMaterials"]]
    assert materials == repository.review_materials
    assert repository.review_material_reads == [
        {
            "task_id": request.task_id,
            "owner_id": request.owner_id,
            "workspace_id": request.workspace_id,
            "execution_decision_id": request.execution_decision_id,
            "reviewer_work_unit_id": request.work_unit_id,
        }
    ]
    assert "providerId" not in materials[0]
    assert "adapter" not in materials[0]


def test_missing_verified_review_material_fails_before_provider_transport() -> None:
    request, repository = _review_request_and_repository()

    def reject_materials(**_scope):
        raise ResearchOrchestrationPersistenceError("review_artifact_content_verification_failed")

    repository.read_review_materials_for_dispatch = reject_materials

    class Executor:
        calls = 0

        def execute(self, _request, **_evidence):
            self.calls += 1
            raise AssertionError("provider transport must not run")

    executor = Executor()
    prepared = PreparedHuiyanResearchDispatch(
        provider_id="openai_primary",
        capabilities=ProviderSubmissionSafety(
            submissionMode="single_attempt_no_replay",
            acceptsIdempotencyKey=True,
            returnsReceiptOrRemoteRunId=False,
            queryByIdempotencyKey=False,
            queryByReceiptOrRemoteRunId=False,
        ),
        repository=repository,
        executor=executor,
    )

    with pytest.raises(DispatchProviderKnownRejected, match="review_materials_unavailable"):
        prepared.submit(request)

    assert executor.calls == 0


def test_dispatch_capability_attempt_identity_reaches_openai_executor() -> None:
    request, repository = _request_and_repository()
    request = DispatchSubmissionRequest(
        **{
            **request.__dict__,
            "capability_call_id": "call:dispatch:deterministic",
            "attempt_number": 1,
        }
    )

    class Executor:
        evidence = None

        def execute(self, _request, **evidence):
            self.evidence = evidence
            return SimpleNamespace(
                skillRequest=None,
                title="Result",
                mimeType="text/plain",
                content='{"result":"ok"}',
                metadata={"adapter": "huiyan_chat_completions"},
                usage=None,
            )

    executor = Executor()
    prepared = PreparedHuiyanResearchDispatch(
        provider_id="openai_primary",
        capabilities=ProviderSubmissionSafety(True, False, False, False),
        repository=repository,
        executor=executor,
    )

    prepared.submit(request)

    assert executor.evidence == {
        "capability_call_id": "call:dispatch:deterministic",
        "attempt_number": 1,
    }


def _mechanical_contract_error(raw: bytes = b'{"title": "Result", "mimeType": "text/plain", "content": {}} trailing') -> ProviderContractError:
    error = ProviderContractError("provider_response_contract_invalid")
    error.diagnosis = RejectedResponseDiagnosis(
        category="invalid_json",
        normalized_finish_reason="stop",
        diagnostics=(
            SafeValidatorDiagnostic(
                error_code="provider_response_invalid_json",
                category="invalid_json",
                json_pointer="",
                field_path="",
                validator_keyword="json_parse",
            ),
        ),
    )
    error.evidence_body = raw
    error.total_byte_length = len(raw)
    error.hash_incomplete = False
    error.usage = {"inputTokens": 11, "outputTokens": 7, "totalTokens": 18}
    return error


def _valid_executor_result() -> SimpleNamespace:
    return SimpleNamespace(
        skillRequest=None,
        title="Result",
        mimeType="text/plain",
        content='{"result":"ok"}',
        metadata={"adapter": "huiyan_chat_completions"},
        usage=None,
    )


def test_agent_schema_failure_does_not_submit_an_unauthorized_correction() -> None:
    request, repository = _request_and_repository()

    class Executor:
        def __init__(self) -> None:
            self.calls = []

        def execute(self, _request, **kwargs):
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                raise _mechanical_contract_error()
            return _valid_executor_result()

    executor = Executor()
    prepared = PreparedHuiyanResearchDispatch(
        provider_id="openai_primary",
        capabilities=ProviderSubmissionSafety(True, False, False, False),
        repository=repository,
        executor=executor,
    )

    with pytest.raises(DispatchProviderKnownRejected) as captured:
        prepared.submit(request)

    assert captured.value.code == "provider_response_contract_invalid"
    assert captured.value.usage == {"inputTokens": 11, "outputTokens": 7, "totalTokens": 18}
    assert executor.calls == [{
        "capability_call_id": request.capability_call_id,
        "attempt_number": request.attempt_number,
    }]


def test_agent_repeated_schema_failure_has_no_adapter_retry_budget() -> None:
    request, repository = _request_and_repository()

    class Executor:
        def __init__(self) -> None:
            self.calls = 0

        def execute(self, _request, **_kwargs):
            self.calls += 1
            raise _mechanical_contract_error()

    executor = Executor()
    prepared = PreparedHuiyanResearchDispatch(
        provider_id="openai_primary",
        capabilities=ProviderSubmissionSafety(True, False, False, False),
        repository=repository,
        executor=executor,
    )

    with pytest.raises(DispatchProviderKnownRejected) as captured:
        prepared.submit(request)

    assert captured.value.code == "provider_response_contract_invalid"
    assert executor.calls == 1


def test_semantic_contract_failure_is_not_auto_repaired_or_fabricated() -> None:
    request, repository = _request_and_repository()

    class Executor:
        def __init__(self) -> None:
            self.calls = 0

        def execute(self, _request, **_kwargs):
            self.calls += 1
            error = ProviderContractError("provider_response_contract_invalid")
            error.diagnosis = None
            error.safe_error_code = "artifact_scientific_evidence_required"
            error.safe_field_paths = ("/evidence",)
            raise error

    executor = Executor()
    prepared = PreparedHuiyanResearchDispatch(
        provider_id="openai_primary",
        capabilities=ProviderSubmissionSafety(True, False, False, False),
        repository=repository,
        executor=executor,
    )

    with pytest.raises(DispatchProviderKnownRejected) as captured:
        prepared.submit(request)

    assert captured.value.code == "artifact_scientific_evidence_required"
    assert executor.calls == 1



def test_transport_unknown_preserves_provider_execution_identity() -> None:
    request, repository = _request_and_repository()

    class Executor:
        def execute(self, _request, **_kwargs):
            raise ProviderTransportUncertainError(
                provider_execution_id="pat-provider-unknown",
            )

    prepared = PreparedHuiyanResearchDispatch(
        provider_id="openai_primary",
        capabilities=ProviderSubmissionSafety(True, False, False, False),
        repository=repository,
        executor=Executor(),
    )

    with pytest.raises(DispatchProviderResultUnknown) as captured:
        prepared.submit(request)

    assert captured.value.remote_run_id == "pat-provider-unknown"


def test_transport_proven_not_submitted_is_known_failure_not_external_unknown() -> None:
    request, repository = _request_and_repository()

    class Executor:
        def execute(self, _request, **_kwargs):
            raise ProviderTransportNotSubmittedError(
                provider_execution_id="pat-provider-not-submitted",
            )

    prepared = PreparedHuiyanResearchDispatch(
        provider_id="openai_primary",
        capabilities=ProviderSubmissionSafety(True, False, False, False),
        repository=repository,
        executor=Executor(),
    )

    with pytest.raises(DispatchProviderKnownRejected) as captured:
        prepared.submit(request)

    assert captured.value.code == "provider_transport_not_submitted"


def test_untyped_executor_exception_is_internal_known_rejection_not_external_unknown() -> None:
    request, repository = _request_and_repository()

    class Executor:
        def execute(self, _request, **_kwargs):
            raise RuntimeError("local normalization bug")

    prepared = PreparedHuiyanResearchDispatch(
        provider_id="openai_primary",
        capabilities=ProviderSubmissionSafety(True, False, False, False),
        repository=repository,
        executor=Executor(),
    )

    with pytest.raises(DispatchProviderKnownRejected) as captured:
        prepared.submit(request)

    assert captured.value.code == "provider_execution_invalid"


def _retrieval_material() -> dict:
    return {
        "metadata_candidates": [{
            "title": "GraphRAG for scientific question answering",
            "source_identifier": "https://doi.org/10.1000/graphrag",
        }]
    }


def test_key_literature_retrieval_failure_keeps_safe_runtime_detail() -> None:
    result = {
        "state": "failed",
        "failureClassification": "external_action_evidence_missing",
        "failedStage": "retrieval_report_evidence_gate",
        "firstMismatch": "retrieval-report-dict.sources-openalex-ok-10_arxiv-ok-10.queries-2.final-0.provenance-0.failed-0.attempted-zero-result",
    }

    with pytest.raises(DispatchProviderKnownRejected) as raised:
        huiyan_provider._decode_key_literature_retrieval_result(result)

    assert raised.value.code == "external_action_evidence_missing"
    assert raised.value.failure_detail == {
        "failedStage": "retrieval_report_evidence_gate",
        "firstMismatch": result["firstMismatch"],
    }


def test_key_literature_retrieval_rehydrates_durable_material_with_storage_scope(monkeypatch) -> None:
    request, _repository = _request_and_repository()
    raw = json.dumps(_retrieval_material()).encode("utf-8")
    digest = __import__("hashlib").sha256(raw).hexdigest()
    calls = []

    class Stored:
        storage_type = "external"
        storage_ref = "durable/external-action-evidence/retrieval.json"
        storage_scope = "minio+http://example/openair"

    monkeypatch.setattr(
        huiyan_provider.artifact_write_service,
        "store_durable_bytes",
        lambda *_args: Stored(),
    )

    def read(*args):
        calls.append(args)
        return raw

    monkeypatch.setattr(huiyan_provider.artifact_write_service, "read_durable_bytes", read)
    material = huiyan_provider._persist_and_rehydrate_key_literature_retrieval(
        request,
        raw=raw,
        evidence=({
            "adapter": "python_adapter:academic_source_retrieval_skill",
            "outcome": "partial",
            "evidenceRef": "evidence:retrieval",
            "contentSha256": digest,
        },),
    )

    assert material == _retrieval_material()
    assert calls == [(request.task_id, Stored.storage_type, Stored.storage_ref, Stored.storage_scope)]


def test_key_literature_retrieval_durability_failure_fails_closed_before_synthesis(monkeypatch) -> None:
    request, _repository = _request_and_repository()
    raw = json.dumps(_retrieval_material()).encode("utf-8")

    monkeypatch.setattr(
        huiyan_provider.artifact_write_service,
        "store_durable_bytes",
        lambda *_args: (_ for _ in ()).throw(OSError("write failed")),
    )

    with pytest.raises(huiyan_provider.DispatchProviderResultUnknown, match="key_literature_retrieval_evidence_unavailable"):
        huiyan_provider._persist_and_rehydrate_key_literature_retrieval(
            request,
            raw=raw,
            evidence=({
                "adapter": "python_adapter:academic_source_retrieval_skill",
                "outcome": "partial",
                "evidenceRef": "evidence:retrieval",
                "contentSha256": __import__("hashlib").sha256(raw).hexdigest(),
            },),
        )
