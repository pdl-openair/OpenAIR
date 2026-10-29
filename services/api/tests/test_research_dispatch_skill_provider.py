from __future__ import annotations

import base64
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tests.support.authorized_dispatch import authorized_dispatch

from app.execution import research_dispatch_skill_provider as skill_provider_module
from app.execution.research_dispatch_provider import (
    DispatchProviderKnownRejected,
    DispatchProviderResultUnknown,
    DispatchSubmissionRequest,
)
from app.execution.research_dispatch_skill_provider import DirectSkillResearchDispatchProvider
from app.execution.research_dispatch_worker import process_authorized_recovery
from app.models.research_orchestration import ExecutionPurpose, ExecutionStrategy, RegistrySnapshotRef, ResearchTaskV12
from app.modules.provider_registry import ProviderSubmissionSafety
from app.modules.skill_binding_source import (
    binding_handoff_evidence_matches,
    load_skill_binding_source_rows,
)
from app.modules.research_orchestration_repository import AuthorizedDispatch


_FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "packages"
    / "shared"
    / "test-fixtures"
    / "research-orchestration-v1.2-valid.json"
)


def _literature_search_task() -> tuple[ResearchTaskV12, Any]:
    payload = deepcopy(json.loads(_FIXTURE.read_text(encoding="utf-8"))["researchTask"])
    payload["executionDecisions"] = []
    payload["workUnits"] = []
    payload["capabilityCalls"] = []
    payload["artifacts"] = []
    payload["agentInteractionRecords"] = []
    instance = payload["researchDesignVersions"][0]["instances"][0]
    instance.update(
        {
            "substageDefinitionId": "sdr:literature_search",
            "substageDefinitionVersion": 1,
        }
    )
    payload["substageRuntimeStates"][0].update(
        {
            "status": "ready",
            "activeExecutionDecisionId": None,
            "workUnitIds": [],
            "artifactRefs": [],
        }
    )
    task = ResearchTaskV12.model_validate(payload)
    return task, task.research_design_versions[0].instances[0]


@dataclass(frozen=True)
class _Ref:
    binding_id: str
    binding_version: int


@dataclass(frozen=True)
class _Executor:
    executor_type: str
    executor_id: str
    definition_version: int

    def model_dump(self, **_kwargs):
        return {
            "executorType": self.executor_type,
            "executorId": self.executor_id,
            "definitionVersion": self.definition_version,
        }


@dataclass(frozen=True)
class _Actor:
    actor_type: str
    actor_id: str
    definition_version: int | None

    def model_dump(self, **_kwargs):
        return {
            "actorType": self.actor_type,
            "actorId": self.actor_id,
            "definitionVersion": self.definition_version,
        }


class _WorkUnit:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.work_unit_id = payload["workUnitId"]
        self.assignment_id = payload["assignmentId"]
        self.execution_decision_id = "decision-1"
        self.executor_ref = _Executor("skill", "literature_search", 1)
        self.responsible_actor_ref = _Actor("orchestrator", "openair_orchestrator", None)
        self.allowed_skill_refs = []
        self.registry_snapshot_ref = RegistrySnapshotRef(schemaVersion=1, contentHash="sha256:" + "1" * 64)
        self.substage_definition_id = "sdr:literature_search"
        self.substage_definition_version = 1
        self.instruction = payload["instruction"]
        self.input_artifact_refs = []
        self.input_artifact_snapshots = []
        self.input_memory_refs = []
        self.stop_conditions = ["acceptance_policy_terminal"]
        self.research_design_version = 1
        self.substage_instance_id = "sub-1"
        self.invocation_context = {"canonical_user_query": "Find bounded literature evidence."}
        self.resolved_execution_policy = payload.get("resolvedExecutionPolicy")
        self._payload = {**payload, "invocationContext": dict(self.invocation_context)}

    def model_dump(self, **_kwargs):
        return self._payload


class _Repository:
    def __init__(self, payload: dict[str, Any], snapshot: dict[str, Any]) -> None:
        work_unit = _WorkUnit(payload)
        primary = SimpleNamespace(
            assignment_id="assignment-primary",
            role=SimpleNamespace(value="primary"),
            executor_ref=work_unit.executor_ref,
            responsible_actor_ref=work_unit.responsible_actor_ref,
            allowed_skill_refs=[],
            source_binding_refs=[_Ref("bind:sdr:literature_search:skill:literature_search", 1)],
            input_refs=[],
            dependency_assignment_ids=[],
        )
        reviewer = SimpleNamespace(
            assignment_id="assignment-reviewer",
            role=SimpleNamespace(value="reviewer"),
            executor_ref=_Executor("agent", "result_reliability_audit", 1),
            responsible_actor_ref=_Actor("agent", "result_reliability_audit", 1),
            allowed_skill_refs=[],
            dependency_assignment_ids=[primary.assignment_id],
        )
        decision = SimpleNamespace(
            execution_decision_id="decision-1",
            status=SimpleNamespace(value="dispatched"),
            strategy=ExecutionStrategy.DIRECT_SKILL,
            purpose=ExecutionPurpose.COMPLETION,
            registry_snapshot_ref=work_unit.registry_snapshot_ref,
            assignments=[primary, reviewer],
            research_design_version=1,
            completion_policy=SimpleNamespace(
                review_requirement=SimpleNamespace(value="all_reviewers_approve"),
                reviewer_assignment_ids=[reviewer.assignment_id],
            ),
        )
        self.task = SimpleNamespace(
            task_id="task-1",
            work_units=[work_unit],
            execution_decisions=[decision],
            registry_snapshot_ref=work_unit.registry_snapshot_ref,
            research_design_versions=[
                SimpleNamespace(
                    version=1,
                    status=SimpleNamespace(value="confirmed"),
                    goal="Find bounded literature evidence.",
                )
            ],
        )
        self.snapshot = snapshot

    def load(self, **_kwargs):
        return self.task

    def load_registry_snapshot(self, _content_hash: str):
        return self.snapshot

    def read_direct_skill_input_context_for_dispatch(self, **_kwargs):
        return {
            "canonicalUserQuery": "Find bounded literature evidence.",
            "invocationContext": {"canonical_user_query": "Find bounded literature evidence."},
            "inputArtifactRefs": [],
            "artifacts": [],
        }


def _snapshot_definition() -> dict[str, Any]:
    return {
        "skillId": "literature_search",
        "definitionVersion": 1,
        "status": "ready",
        "enabled": True,
        "runtime": "claude_code",
        "skillPath": "skills/research/literature-search",
        "skillType": "atomic",
        "riskPolicy": {"riskClass": None, "source": "canonical_registry_unspecified"},
        "sourceContract": {
            "skill_id": "literature_search",
            "frozenSchemas": {
                "input": {
                    "sha256": "0" * 64,
                    "schema": {
                        "type": "object",
                        "required": ["goal"],
                        "properties": {"goal": {"type": "string", "minLength": 1}},
                        "additionalProperties": False,
                    },
                }
            },
        },
        "inputContract": {"status": "unknown", "acceptedInputRefs": []},
        "outputContract": {"status": "unknown", "artifactTypes": []},
        "judgmentSupport": {"deterministic": False, "source": "canonical_registry_unspecified"},
    }


def _snapshot(definition: dict[str, Any]) -> dict[str, Any]:
    return {
        "definitions": {"skills": [definition]},
        "bindings": {
            "skills": [
                {
                    "bindingId": "bind:sdr:literature_search:skill:literature_search",
                    "bindingVersion": 1,
                    "definitionId": "sdr:literature_search",
                    "definitionVersion": 1,
                    "skillId": "literature_search",
                    "skillDefinitionVersion": 1,
                    "enabled": True,
                    "runtimeInputs": {
                        "contractVersion": 2,
                        "fields": [
                            {
                                "targetField": "goal",
                                "source": {
                                    "kind": "invocationContext",
                                    "field": "canonical_user_query",
                                },
                            }
                        ],
                    },
                }
            ]
        },
    }


def _payload() -> dict[str, Any]:
    return {
        "workUnitId": "work-1",
        "assignmentId": "assignment-primary",
        "instruction": "形成有来源边界的文献综述。",
        "invocationContext": {"canonical_user_query": "Find bounded literature evidence."},
        "executorRef": {
            "executorType": "skill",
            "executorId": "literature_search",
            "definitionVersion": 1,
        },
    }


def _request(payload: dict[str, Any]) -> DispatchSubmissionRequest:
    return DispatchSubmissionRequest(
        dispatch_id="dispatch-1",
        task_id="task-1",
        execution_decision_id="decision-1",
        work_unit_id="work-1",
        idempotency_key="workunit:decision-1:assignment-primary",
        owner_id="owner-1",
        workspace_id="workspace-1",
        payload=payload,
        capability_call_id="call:dispatch:test",
        attempt_number=1,
    )


def _runtime() -> dict[str, Any]:
    return {
        "artifactContract": "direct_skill:literature_search:v1",
        "artifactSchemaDigest": "sha256:" + "2" * 64,
        "artifactMimeType": "text/markdown",
        "logicalProviderId": "openai_primary",
        "modelProfile": "terra",
        "runtimeKind": "llm_reasoning",
        "requiresExternalAction": False,
        "adapterId": "huiyan_chat_completions",
        "evidenceContractVersion": "none",
    }


def _success(
    content: str = "# 结果\n\n真实受控输出。",
    *,
    provider_model: str = "test-terra-concrete-model",
) -> dict[str, Any]:
    raw = content.encode("utf-8")
    return {
        "state": "succeeded",
        "relatedSkillId": "literature_search",
        "providerModel": provider_model,
        "artifactPayload": {
            "contentBase64": base64.b64encode(raw).decode("ascii"),
            "contentType": "text/markdown",
            "presentation": {
                "title": "文献综述",
                "mimeType": "text/markdown",
                "contentSha256": hashlib.sha256(raw).hexdigest(),
                "contentSize": len(raw),
            },
        },
    }


def _provider(monkeypatch: pytest.MonkeyPatch, execute):
    definition = _snapshot_definition()
    repository = _Repository(_payload(), _snapshot(definition))
    monkeypatch.setattr(
        skill_provider_module,
        "resolve_registered_durable_skill_runtime",
        lambda *_args, **_kwargs: _runtime(),
    )
    return DirectSkillResearchDispatchProvider(
        skill_id="literature_search",
        definition_version=1,
        snapshot_definition=definition,
        source_binding_refs=(("bind:sdr:literature_search:skill:literature_search", 1),),
        repository=repository,  # type: ignore[arg-type]
        runtime_resolver=lambda *_args, **_kwargs: _runtime(),
        execute=execute,
    )


def test_direct_skill_preflight_has_no_physical_execution_and_submit_calls_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    provider = _provider(monkeypatch, lambda request: calls.append(request) or _success())

    prepared = provider.preflight()
    assert calls == []
    completed = prepared.submit(_request(_payload()))

    assert len(calls) == 1
    assert calls[0]["capabilityCallId"] == "call:dispatch:test"
    assert calls[0]["attemptNumber"] == 1
    assert completed.content.startswith("# 结果")
    assert completed.model == "test-terra-concrete-model"
    assert prepared.capabilities.submissionMode == "single_attempt_no_replay"






def test_direct_skill_preserves_code_owned_result_protocol(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _success()
    result["artifactPayload"]["presentation"]["metadata"] = {
        "resultProtocol": {
            "status": "degraded",
            "accepted_items": [{"path": "queries", "count": 7}],
            "quarantined_items": [{"path": "queries[7]", "reason": "invalid_query"}],
            "repaired_items": [],
            "warnings": ["one query was skipped"],
            "coverage": {"accepted_query_count": 7},
        },
        "warnings": ["one query was skipped"],
        "artifactQuality": "degraded",
    }
    provider = _provider(monkeypatch, lambda _request: result)

    completed = provider.preflight().submit(_request(_payload()))

    assert completed.result_protocol["status"] == "degraded"
    assert completed.result_protocol["coverage"]["accepted_query_count"] == 7
    assert completed.result_protocol["quarantined_items"] == [
        {"path": "queries[7]", "reason": "invalid_query"}
    ]
    assert completed.warnings == ("one query was skipped",)




def test_direct_skill_unknown_is_absorbing_and_not_mapped_to_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _provider(
        monkeypatch,
        lambda _request: {
            "state": "unknown",
            "externalResultUnknown": True,
            "summary": "结果无法证明。",
        },
    )
    with pytest.raises(DispatchProviderResultUnknown):
        provider.preflight().submit(_request(_payload()))


def test_direct_skill_local_completed_result_wrapping_failure_is_known_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _provider(monkeypatch, lambda _request: _success())
    monkeypatch.setattr(
        skill_provider_module,
        "_completed_submission",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            TypeError("local DTO wrapping failed")
        ),
    )

    with pytest.raises(
        DispatchProviderKnownRejected,
        match="skill_result_local_processing_failed",
    ) as exc_info:
        provider.preflight().submit(_request(_payload()))

    assert exc_info.value.failure_detail == {
        "failedStage": "dispatch_result_wrapping"
    }


def test_direct_skill_known_failure_and_invalid_success_are_distinct(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failed = _provider(
        monkeypatch,
        lambda _request: {
            "state": "failed",
            "failureClassification": "skill_input_invalid",
            "summary": "输入不完整。",
        },
    )
    with pytest.raises(DispatchProviderKnownRejected, match="skill_input_invalid"):
        failed.preflight().submit(_request(_payload()))

    invalid = _success()
    invalid["artifactPayload"]["presentation"]["contentSize"] += 1
    provider = _provider(monkeypatch, lambda _request: invalid)
    with pytest.raises(DispatchProviderKnownRejected, match="skill_result_contract_invalid"):
        provider.preflight().submit(_request(_payload()))


def test_llm_success_without_actual_provider_model_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _success()
    result.pop("providerModel")
    provider = _provider(monkeypatch, lambda _request: result)

    with pytest.raises(
        DispatchProviderKnownRejected,
        match="skill_result_provider_model_missing",
    ):
        provider.preflight().submit(_request(_payload()))


def test_invalid_registered_input_is_rejected_before_skill_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    provider = _provider(monkeypatch, lambda request: calls.append(request) or _success())
    prepared = provider.preflight()
    prepared.repository.read_direct_skill_input_context_for_dispatch = lambda **_kwargs: {
        "canonicalUserQuery": "",
        "inputArtifactRefs": [],
        "artifacts": [],
    }

    with pytest.raises(
        DispatchProviderKnownRejected,
        match="input_composition_invocation_context_missing",
    ):
        prepared.submit(_request(_payload()))

    assert calls == []


def test_frozen_snapshot_mismatch_blocks_before_skill_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    provider = _provider(monkeypatch, lambda request: calls.append(request) or _success())
    prepared = provider.preflight()
    prepared.repository.snapshot["definitions"]["skills"][0]["skillPath"] = "changed"

    with pytest.raises(DispatchProviderKnownRejected, match="dispatch_skill_definition_mismatch"):
        prepared.submit(_request(_payload()))
    assert calls == []


class _RecoveryPrepared:
    provider_id = "direct_skill_runtime"
    adapter = "durable_skill_runtime"

    capabilities = ProviderSubmissionSafety(False, False, False, False, "single_attempt_no_replay")

    def __init__(self) -> None:
        self.submit_calls = 0
        self.query_calls = 0

    def submit(self, _request):
        self.submit_calls += 1
        return SimpleNamespace(remote_run_id="")

    def query_by_idempotency_key(self, _key):
        self.query_calls += 1
        raise AssertionError("single-attempt recovery must not query")


class _RecoveryProvider:
    def __init__(self, prepared: _RecoveryPrepared) -> None:
        self.prepared = prepared

    def preflight(self):
        return self.prepared


class _RecoveryService:
    def __init__(self) -> None:
        self.outcomes = []
        self.provider_identity = None

    def revalidate_recovery_submission(self, *_args, **_kwargs):
        return "ready"

    def mark_external_action_started(self, fence, *, provider_id, adapter, lease_seconds=30):
        self.provider_identity = (provider_id, adapter)
        return fence

    def record_outcome(self, fence, **kwargs):
        self.outcomes.append((fence, kwargs))
        return True


def test_unstarted_single_attempt_recovery_submits_once_without_query() -> None:
    authorized = authorized_dispatch(
        dispatch_id="dispatch-1",
        task_id="task-1",
        execution_decision_id="decision-1",
        work_unit_id="work-1",
        idempotency_key="stable-key",
        lease_owner="worker-1",
        attempt_count=1,
        authorization_task_revision=4,
        work_unit_payload={"executorRef": {"executorType": "skill"}},
        submission_mode="single_attempt_no_replay",
        external_action_started_at=None,
    )
    prepared = _RecoveryPrepared()
    service = _RecoveryService()

    result = process_authorized_recovery(
        authorized,
        owner_id="owner-1",
        workspace_id="workspace-1",
        provider=_RecoveryProvider(prepared),
        service=service,  # type: ignore[arg-type]
    )

    assert result.status == "submitted"
    assert prepared.submit_calls == 1
    assert prepared.query_calls == 0
    assert service.provider_identity == (
        "direct_skill_runtime",
        "durable_skill_runtime",
    )

@dataclass(frozen=True)
class _SnapshotRef:
    content_hash: str = "sha256:" + "1" * 64
    schema_version: int = 1

    def model_dump(self, **_kwargs):
        return {"schemaVersion": self.schema_version, "contentHash": self.content_hash}


class _ResolverRepository:
    def __init__(self, task, snapshot) -> None:
        self.task = task
        self.snapshot = snapshot
        self.loads = []

    def load(self, **kwargs):
        self.loads.append(kwargs)
        if kwargs["owner_id"] != "owner-1" or kwargs["workspace_id"] != "workspace-1":
            raise ValueError("scope mismatch")
        return self.task

    def load_registry_snapshot(self, _content_hash):
        return self.snapshot


def _resolver_task(*, executor_type: str = "skill"):
    snapshot_ref = _SnapshotRef()
    skill = executor_type == "skill"
    executor = _Executor(executor_type, "literature_search" if skill else "domain-agent", 1)
    actor = (
        _Actor("orchestrator", "openair_orchestrator", None)
        if skill
        else _Actor("agent", "domain-agent", 1)
    )
    binding_id = (
        "bind:sdr:literature_search:skill:literature_search"
        if skill
        else "bind:sdr:literature_search:agent:domain-agent:primary"
    )
    assignment = SimpleNamespace(
        assignment_id="assignment-primary",
        role=SimpleNamespace(value="primary"),
        executor_ref=executor,
        responsible_actor_ref=actor,
        allowed_skill_refs=[],
        source_binding_refs=[_Ref(binding_id, 1)],
        dependency_assignment_ids=[],
    )
    reviewer = SimpleNamespace(
        assignment_id="assignment-reviewer",
        role=SimpleNamespace(value="reviewer"),
        executor_ref=_Executor("agent", "result_reliability_audit", 1),
        responsible_actor_ref=_Actor("agent", "result_reliability_audit", 1),
        allowed_skill_refs=[],
        dependency_assignment_ids=[assignment.assignment_id],
    )
    readiness_ref = SimpleNamespace(
        skill_id=executor.executor_id if skill else None,
        agent_id=None if skill else executor.executor_id,
        definition_version=1,
    )
    decision = SimpleNamespace(
        execution_decision_id="decision-1",
        status=SimpleNamespace(value="dispatched"),
        strategy=SimpleNamespace(value="direct_skill" if skill else "specialist_agent"),
        purpose=SimpleNamespace(value="completion"),
        registry_snapshot_ref=snapshot_ref,
        assignments=[assignment, reviewer] if skill else [assignment],
        completion_policy=SimpleNamespace(
            review_requirement=SimpleNamespace(
                value="all_reviewers_approve" if skill else "none"
            ),
            reviewer_assignment_ids=[reviewer.assignment_id] if skill else []
        ),
        runtime_readiness=[
            SimpleNamespace(
                capability_ref=readiness_ref,
                ready=True,
                runtime_binding_id=f"registry:{binding_id}:1",
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
            ),
            *(
                [
                    SimpleNamespace(
                        capability_ref=SimpleNamespace(
                            agent_id="result_reliability_audit",
                            skill_id=None,
                            definition_version=1,
                        ),
                        ready=True,
                        runtime_binding_id="registry:review-contract:result_reliability_audit:1",
                        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
                    )
                ]
                if skill
                else []
            ),
        ],
    )
    work_unit = SimpleNamespace(
        work_unit_id="work-1",
        assignment_id="assignment-primary",
        execution_decision_id="decision-1",
        idempotency_key="stable-key",
        status=SimpleNamespace(value="queued"),
        registry_snapshot_ref=snapshot_ref,
        executor_ref=executor,
        responsible_actor_ref=actor,
        allowed_skill_refs=[],
        substage_definition_id="sdr:literature_search",
        substage_definition_version=1,
    )
    design_ref = SimpleNamespace(research_design_id="design-1", version=1)
    task = SimpleNamespace(
        task_id="task-1",
        revision=5,
        active_research_design_ref=design_ref,
        research_design_versions=[
            SimpleNamespace(
                research_design_id="design-1",
                version=1,
                status=SimpleNamespace(value="confirmed"),
            )
        ],
        execution_decisions=[decision],
        work_units=[work_unit],
        registry_snapshot_ref=snapshot_ref,
    )
    if skill:
        definition = _snapshot_definition()
        snapshot = _snapshot(definition)
        snapshot["definitions"]["agents"] = [
            {
                "agentId": "result_reliability_audit",
                "definitionVersion": 1,
                "enabled": True,
                "allowedProviders": ["openai_primary"],
                "defaultProvider": "openai_primary",
                "reviewContract": {
                    "frozenArtifactContract": {
                        "artifactContract": "independent_result_review_v1"
                    }
                },
            }
        ]
    else:
        snapshot = {
            "definitions": {
                "agents": [
                    {
                        "agentId": "domain-agent",
                        "definitionVersion": 1,
                        "enabled": True,
                        "allowedProviders": ["openai_primary"],
                        "defaultProvider": "openai_primary",
                    }
                ]
            },
            "bindings": {
                "agents": [
                    {
                        "bindingId": binding_id,
                        "bindingVersion": 1,
                        "agentId": "domain-agent",
                        "agentDefinitionVersion": 1,
                        "enabled": True,
                    }
                ]
            },
        }
    return task, snapshot


def _candidate_row(**overrides):
    row = {
        "dispatch_id": "dispatch-1",
        "task_id": "task-1",
        "execution_decision_id": "decision-1",
        "work_unit_id": "work-1",
        "idempotency_key": "stable-key",
        "status": "pending",
        "submission_mode": None,
        "external_action_started_at": None,
        "owner_id": "owner-1",
        "workspace_id": "workspace-1",
        "revision": 5,
    }
    row.update(overrides)
    return row


def test_production_binding_resolver_accepts_exact_ready_direct_skill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from worker import entrypoint

    task, snapshot = _resolver_task(executor_type="skill")
    repository = _ResolverRepository(task, snapshot)
    resolver = entrypoint.PostgresDispatchProviderBindingResolver(repository=repository)
    monkeypatch.setattr(entrypoint.settings, "research_orchestration_v1", True)
    monkeypatch.setattr(entrypoint.settings, "persistence_backend", "postgres")
    monkeypatch.setattr(resolver, "_candidate_rows", lambda: [_candidate_row()])
    monkeypatch.setattr(
        entrypoint,
        "resolve_registered_durable_skill_runtime",
        lambda *_args, **_kwargs: _runtime(),
    )

    binding = resolver.resolve()

    assert binding is not None
    assert binding.executor_type == "skill"
    assert binding.provider_id is None
    assert binding.executor_ref["executorType"] == "skill"
    assert binding.source_binding_refs == (
        ("bind:sdr:literature_search:skill:literature_search", 1),
    )


def test_production_binding_resolver_accepts_no_reviewer_skill_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from worker import entrypoint

    task, snapshot = _resolver_task(executor_type="skill")
    decision = task.execution_decisions[0]
    primary = decision.assignments[0]
    decision.assignments = [primary]
    decision.completion_policy = SimpleNamespace(
        review_requirement=SimpleNamespace(value="none"),
        reviewer_assignment_ids=[],
    )
    decision.runtime_readiness = [decision.runtime_readiness[0]]
    snapshot["definitions"].pop("agents")
    repository = _ResolverRepository(task, snapshot)
    resolver = entrypoint.PostgresDispatchProviderBindingResolver(repository=repository)
    monkeypatch.setattr(entrypoint.settings, "research_orchestration_v1", True)
    monkeypatch.setattr(entrypoint.settings, "persistence_backend", "postgres")
    monkeypatch.setattr(resolver, "_candidate_rows", lambda: [_candidate_row()])
    monkeypatch.setattr(
        entrypoint,
        "resolve_registered_durable_skill_runtime",
        lambda *_args, **_kwargs: _runtime(),
    )

    binding = resolver.resolve()

    assert binding is not None
    assert binding.executor_type == "skill"
    assert binding.executor_ref["executorId"] == "literature_search"


def test_production_binding_resolver_rejects_malformed_reviewer_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from worker import entrypoint

    task, snapshot = _resolver_task(executor_type="skill")
    decision = task.execution_decisions[0]
    decision.completion_policy.reviewer_assignment_ids = ["assignment-other"]
    repository = _ResolverRepository(task, snapshot)
    resolver = entrypoint.PostgresDispatchProviderBindingResolver(repository=repository)
    monkeypatch.setattr(entrypoint.settings, "research_orchestration_v1", True)
    monkeypatch.setattr(entrypoint.settings, "persistence_backend", "postgres")
    monkeypatch.setattr(resolver, "_candidate_rows", lambda: [_candidate_row()])
    monkeypatch.setattr(
        entrypoint,
        "resolve_registered_durable_skill_runtime",
        lambda *_args, **_kwargs: pytest.fail("malformed review contract reached runtime"),
    )

    assert resolver.resolve() is None


def test_dispatch_review_contract_none_without_reviewers_is_valid() -> None:
    from worker.entrypoint import _dispatch_review_contract_is_valid

    decision = SimpleNamespace(
        completion_policy=SimpleNamespace(
            review_requirement=SimpleNamespace(value="none"),
            reviewer_assignment_ids=[],
        ),
        assignments=[],
        strategy=ExecutionStrategy.DIRECT_SKILL,
    )
    assignment = SimpleNamespace(assignment_id="assignment-primary")

    assert _dispatch_review_contract_is_valid(
        decision, assignment=assignment, reviewers=[]
    ) is True


def test_dispatch_review_contract_none_with_reviewer_is_invalid() -> None:
    from worker.entrypoint import _dispatch_review_contract_is_valid

    reviewer = SimpleNamespace(
        assignment_id="assignment-reviewer",
        dependency_assignment_ids=["assignment-primary"],
    )
    decision = SimpleNamespace(
        completion_policy=SimpleNamespace(
            review_requirement=SimpleNamespace(value="none"),
            reviewer_assignment_ids=[reviewer.assignment_id],
        ),
        assignments=[reviewer],
        strategy=ExecutionStrategy.DIRECT_SKILL,
    )
    assignment = SimpleNamespace(assignment_id="assignment-primary")

    assert _dispatch_review_contract_is_valid(
        decision, assignment=assignment, reviewers=[reviewer]
    ) is False


def test_dispatch_review_contract_required_without_reviewer_is_invalid() -> None:
    from worker.entrypoint import _dispatch_review_contract_is_valid

    decision = SimpleNamespace(
        completion_policy=SimpleNamespace(
            review_requirement=SimpleNamespace(value="all_reviewers_approve"),
            reviewer_assignment_ids=[],
        ),
        assignments=[],
        strategy=ExecutionStrategy.DIRECT_SKILL,
    )
    assignment = SimpleNamespace(assignment_id="assignment-primary")

    assert _dispatch_review_contract_is_valid(
        decision, assignment=assignment, reviewers=[]
    ) is False


@pytest.mark.parametrize(
    "row, runtime",
    [
        (_candidate_row(revision=4), _runtime()),
        (_candidate_row(workspace_id="workspace-other"), _runtime()),
        (_candidate_row(), None),
    ],
)
def test_direct_skill_binding_fails_closed_for_stale_scope_or_runtime(
    monkeypatch: pytest.MonkeyPatch,
    row: dict[str, Any],
    runtime: dict[str, Any] | None,
) -> None:
    from worker import entrypoint

    task, snapshot = _resolver_task(executor_type="skill")
    repository = _ResolverRepository(task, snapshot)
    resolver = entrypoint.PostgresDispatchProviderBindingResolver(repository=repository)
    monkeypatch.setattr(entrypoint.settings, "research_orchestration_v1", True)
    monkeypatch.setattr(entrypoint.settings, "persistence_backend", "postgres")
    monkeypatch.setattr(resolver, "_candidate_rows", lambda: [row])
    monkeypatch.setattr(
        entrypoint,
        "resolve_registered_durable_skill_runtime",
        lambda *_args, **_kwargs: runtime,
    )

    assert resolver.resolve() is None


def test_agent_binding_route_remains_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    from worker import entrypoint

    task, snapshot = _resolver_task(executor_type="agent")
    resolver = entrypoint.PostgresDispatchProviderBindingResolver(
        repository=_ResolverRepository(task, snapshot)
    )
    monkeypatch.setattr(entrypoint.settings, "research_orchestration_v1", True)
    monkeypatch.setattr(entrypoint.settings, "persistence_backend", "postgres")
    monkeypatch.setattr(resolver, "_candidate_rows", lambda: [_candidate_row()])

    binding = resolver.resolve()

    assert binding is not None
    assert binding.executor_type == "agent"
    assert binding.provider_id == "openai_primary"

@pytest.mark.parametrize(
    "live_runtime, schema_executable, expected",
    [(_runtime(), True, True), (_runtime(), False, False), (None, True, False)],
)
def test_production_readiness_requires_live_skill_runtime(
    monkeypatch: pytest.MonkeyPatch,
    live_runtime: dict[str, Any] | None,
    schema_executable: bool,
    expected: bool,
) -> None:
    from app.modules import provider_registry as provider_registry_module
    from app.modules import research_execution_decision_service as decision_module

    definition = _snapshot_definition()
    reviewer = {
        "agentId": "result_reliability_audit",
        "definitionVersion": 1,
        "enabled": True,
        "allowedProviders": ["openai_primary"],
        "defaultProvider": "openai_primary",
        "reviewContract": {
            "frozenArtifactContract": {"artifactContract": "independent_result_review_v1"}
        },
    }
    snapshot = _snapshot(definition)
    snapshot["definitions"]["agents"] = [reviewer]
    snapshot["bindings"]["agents"] = []
    snapshot["staticPolicies"] = {"policyVersion": "registry-policy.v1"}
    task, instance = _literature_search_task()
    safety = ProviderSubmissionSafety(False, False, False, False, "single_attempt_no_replay")
    registry = SimpleNamespace(
        require=lambda _provider_id: SimpleNamespace(submissionSafety=safety)
    )
    monkeypatch.setattr(provider_registry_module, "load_provider_registry", lambda: registry)
    monkeypatch.setattr(
        provider_registry_module,
        "resolve_provider_runtime_config",
        lambda *_args, **_kwargs: object(),
    )
    monkeypatch.setattr(
        decision_module,
        "_satisfied_human_gate_refs",
        lambda *_args, **_kwargs: set(),
    )
    monkeypatch.setattr(
        skill_provider_module,
        "resolve_registered_durable_skill_runtime",
        lambda *_args, **_kwargs: live_runtime,
    )
    # The function imports this symbol directly at call time.
    monkeypatch.setattr(
        __import__(
            "app.execution.durable_skill_runtime", fromlist=["resolve_registered_durable_skill_runtime"]
        ),
        "resolve_registered_durable_skill_runtime",
        lambda *_args, **_kwargs: live_runtime,
    )
    monkeypatch.setattr(
        __import__(
            "app.execution.durable_skill_runtime",
            fromlist=["registered_durable_skill_provider_schema_executable"],
        ),
        "registered_durable_skill_provider_schema_executable",
        lambda *_args, **_kwargs: schema_executable,
    )

    facts, readiness = decision_module.build_production_resolution_facts(
        task, instance, snapshot
    )

    assert facts.runtime_readiness["skill:literature_search:1"] is expected
    assert readiness["skill:literature_search:1"].ready is expected
    assert readiness["agent:result_reliability_audit:1"].ready is True


def test_direct_skill_binding_requires_live_independent_reviewer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from worker import entrypoint

    task, snapshot = _resolver_task(executor_type="skill")
    task.execution_decisions[0].runtime_readiness = [
        row
        for row in task.execution_decisions[0].runtime_readiness
        if getattr(row.capability_ref, "skill_id", None) == "literature_search"
    ]
    resolver = entrypoint.PostgresDispatchProviderBindingResolver(
        repository=_ResolverRepository(task, snapshot)
    )
    monkeypatch.setattr(entrypoint.settings, "research_orchestration_v1", True)
    monkeypatch.setattr(entrypoint.settings, "persistence_backend", "postgres")
    monkeypatch.setattr(resolver, "_candidate_rows", lambda: [_candidate_row()])
    monkeypatch.setattr(
        entrypoint,
        "resolve_registered_durable_skill_runtime",
        lambda *_args, **_kwargs: _runtime(),
    )

    assert resolver.resolve() is None


def test_python_adapter_runtime_declares_actual_json_artifact_contract() -> None:
    from app.execution.durable_skill_runtime import resolve_registered_durable_skill_runtime

    runtime = resolve_registered_durable_skill_runtime("materials_magnet_design_mvp")

    assert runtime is not None
    assert runtime["artifactMimeType"] == "application/json"
    assert runtime["artifactContract"] == "direct_skill:materials_magnet_design_mvp:v1"


def test_direct_skill_has_no_default_reviewer_factory() -> None:
    from app.modules import research_execution_decision_service as decision_module

    assert not hasattr(decision_module, "_direct_skill_reviewer_assignment")


def test_direct_skill_completed_result_uses_canonical_dispatch_service_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.execution.research_dispatch_provider import DispatchSubmissionCompleted
    from app.modules import research_dispatch_service as dispatch_service_module
    from app.modules.research_dispatch_service import ResearchDispatchService
    from app.modules.research_orchestration_repository import ExecutionContinuationDirective

    events: list[str] = []

    class Repository:
        def record_dispatch_completed_result(self, _fence, **kwargs):
            events.append("record")
            self.recorded = kwargs
            return True

        def load(self, **_kwargs):
            return SimpleNamespace(execution_decisions=[])

        def prepare_execution_continuation(self, **_kwargs):
            events.append("continuation")
            return ExecutionContinuationDirective(
                action="none",
                expected_task_revision=6,
                next_instance_id=None,
                selection_request_id=None,
                reason_code="review_pending",
            )

        def finalize_dispatch_completed_result(self, _fence, **_kwargs):
            events.append("finalize")
            return True

    repository = Repository()
    fence = authorized_dispatch(
        dispatch_id="dispatch-skill-1",
        task_id="task-1",
        execution_decision_id="decision-1",
        work_unit_id="work-1",
        idempotency_key="stable-key",
        lease_owner="worker-1",
        attempt_count=1,
        authorization_task_revision=5,
        work_unit_payload={
            "executorRef": {
                "executorType": "skill",
                "executorId": "literature_search",
                "definitionVersion": 1,
            }
        },
        submission_mode="single_attempt_no_replay",
    )
    monkeypatch.setattr(
        skill_provider_module,
        "validate_completed_skill_dispatch_result",
        lambda *_args, **_kwargs: SimpleNamespace(
            skill_id="literature_search",
            definition_version=1,
            artifactContract="direct_skill:literature_search:v1",
            artifactSchemaDigest="sha256:" + "2" * 64,
            orchestrationPlan={
                "assignmentRole": "primary",
                "externalSourceRecords": [{
                    "record_path": "/paper_packets/*",
                    "identity_fields": [{
                        "field_path": "/arxiv_id",
                        "identity_type": "arxiv_identifier",
                    }],
                }],
            },
        ),
    )
    blobs: dict[str, bytes] = {}
    monkeypatch.setattr(
        dispatch_service_module.artifact_write_service,
        "store_durable_bytes",
        lambda _task_id, storage_ref, data, _content_type: (
            blobs.__setitem__(storage_ref, data)
            or SimpleNamespace(storage_type="local", storage_ref=storage_ref)
        ),
    )
    monkeypatch.setattr(
        dispatch_service_module.artifact_write_service,
        "read_durable_bytes",
        lambda _task_id, _storage_type, storage_ref: blobs.get(storage_ref),
    )
    real_source_validation = dispatch_service_module.validate_result_external_sources

    def validate_sources_after_result_save(content, declaration, *, result_protocol=None):
        assert "durable/research-dispatch/dispatch-skill-1/result.json" in blobs
        return real_source_validation(content, declaration, result_protocol=result_protocol)

    monkeypatch.setattr(
        dispatch_service_module,
        "validate_result_external_sources",
        validate_sources_after_result_save,
    )

    recorded = ResearchDispatchService(repository).record_completed_result(  # type: ignore[arg-type]
        fence,
        owner_id="owner-1",
        workspace_id="workspace-1",
        result=DispatchSubmissionCompleted(
            title="文献综述",
            mime_type="application/json",
            content=json.dumps({"paper_packets": [
                {"arxiv_id": "2409.00001", "body": "Unknown source in text"},
                {"arxiv_id": "Unknown source", "body": "invalid identity"},
            ]}),
            provider_id="direct_skill_runtime",
            adapter="durable_skill_runtime",
            result_protocol={
                "status": "degraded",
                "accepted_items": [{"path": "paper_packets", "count": 1}],
                "quarantined_items": [{"path": "paper_packets[1]", "reason": "invalid_source_identity"}],
                "repaired_items": [],
                "warnings": ["one source record was quarantined"],
                "coverage": {"accepted_count": 1, "input_count": 2},
            },
            warnings=("one source record was quarantined",),
            external_action_evidence=({
                "adapter": "python_adapter:materials_magnet_design_mvp",
                "outcome": "completed",
                "evidenceRef": "skill-result:materials_magnet_design_mvp:sha256:" + "a" * 64,
                "contentSha256": "a" * 64,
            },),
        ),
    )

    assert recorded is True
    assert events == ["record"]
    assert repository.recorded["artifact_contract"] == "direct_skill:literature_search:v1"
    assert repository.recorded["artifact_storage_ref"].endswith("/artifact.json")
    assert repository.recorded["source_identity_acceptance"]["acceptedCount"] == 1
    assert repository.recorded["source_identity_acceptance"]["rejectedCount"] == 1
    assert repository.recorded["source_identity_acceptance"]["skillId"] == "literature_search"
    assert repository.recorded["source_identity_acceptance"]["definitionVersion"] == 1
    result_envelope = json.loads(blobs["durable/research-dispatch/dispatch-skill-1/result.json"])
    assert len(json.loads(result_envelope["content"])["paper_packets"]) == 2
    artifact = json.loads(
        blobs["durable/research-dispatch/dispatch-skill-1/artifact.json"]
    )
    assert [row["arxiv_id"] for row in artifact["paper_packets"]] == ["2409.00001"]
    assert result_envelope["externalActionEvidence"][0]["adapter"] == (
        "python_adapter:materials_magnet_design_mvp"
    )
    assert result_envelope["schemaVersion"] == 2
    assert "formalGateSatisfied" not in result_envelope
    assert "manualReviewReasons" not in result_envelope
    assert result_envelope["resultProtocol"]["status"] == "degraded"
    assert result_envelope["warnings"] == ["one source record was quarantined"]
    assert repository.recorded["result"].result_protocol["status"] == "degraded"

    recovery_fence = replace(
        fence,
        result_storage_type="local",
        result_storage_scope=fence.task_id,
        result_storage_ref="durable/research-dispatch/dispatch-skill-1/result.json",
    )
    events.clear()
    assert ResearchDispatchService(repository).recover_completed_result(
        recovery_fence,
        owner_id="owner-1",
        workspace_id="workspace-1",
    ) is True
    assert repository.recorded["result"].result_protocol["status"] == "degraded"
    assert repository.recorded["result"].warnings == (
        "one source record was quarantined",
    )


def test_python_adapter_exception_is_absorbing_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.execution import durable_skill_runtime

    monkeypatch.setattr(
        durable_skill_runtime,
        "run_python_skill_adapter",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("adapter interrupted")),
    )

    result = durable_skill_runtime.execute_durable_skill(
        {"skillId": "materials_magnet_design_mvp", "parameters": {}}
    )

    assert result["state"] == "unknown"
    assert result["externalResultUnknown"] is True


def _external_frozen_dispatch() -> skill_provider_module.FrozenDirectSkillDispatch:
    return skill_provider_module.FrozenDirectSkillDispatch(
        skill_id="materials_magnet_design_mvp",
        definition_version=1,
        artifactContract="direct_skill:materials_magnet_design_mvp:v1",
        artifactSchemaDigest="sha256:" + "3" * 64,
        artifactMimeType="application/json",
        logicalProviderId=None,
        modelProfile=None,
        resolvedExecutionPolicy=None,
        runtimeKind="external_adapter",
        requiresExternalAction=True,
        adapterId="python_adapter:materials_magnet_design_mvp",
        evidenceContractVersion="external-action-evidence/v1",
        orchestrationPlan={"assignmentRole": "primary"},
    )


def _external_runtime_result(*, content: str, include_evidence: bool = True) -> dict[str, Any]:
    raw = content.encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    result: dict[str, Any] = {
        "state": "succeeded",
        "relatedSkillId": "materials_magnet_design_mvp",
        "artifactPayload": {
            "contentBase64": base64.b64encode(raw).decode("ascii"),
            "contentType": "application/json",
            "presentation": {
                "title": "Materials result",
                "mimeType": "application/json",
                "contentSha256": digest,
                "contentSize": len(raw),
            },
        },
    }
    if include_evidence:
        result["externalActionEvidence"] = [{
            "adapter": "python_adapter:materials_magnet_design_mvp",
            "outcome": "completed",
            "evidenceRef": f"skill-result:materials_magnet_design_mvp:sha256:{digest}",
            "contentSha256": digest,
        }]
    return result


def test_external_skill_provider_preserves_structured_action_evidence() -> None:
    content = '{"state":"succeeded","iterations":[{"mcp_calls":[{"request_sha256":"' + "a" * 64 + '"}]}]}'

    completed = skill_provider_module._completed_submission(
        _external_runtime_result(content=content),
        frozen=_external_frozen_dispatch(),
    )

    assert completed.external_action_evidence == ({
        "adapter": "python_adapter:materials_magnet_design_mvp",
        "outcome": "completed",
        "evidenceRef": (
            "skill-result:materials_magnet_design_mvp:sha256:"
            + hashlib.sha256(content.encode("utf-8")).hexdigest()
        ),
        "contentSha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
    },)




def test_external_skill_provider_preserves_provider_execution_identity_on_unknown() -> None:
    with pytest.raises(DispatchProviderResultUnknown) as captured:
        skill_provider_module._completed_submission(
            {
                "state": "unknown",
                "externalResultUnknown": True,
                "providerExecutionId": "pat-openair-1",
            },
            frozen=_external_frozen_dispatch(),
        )

    assert captured.value.code == "single_attempt_result_unknown"
    assert captured.value.remote_run_id == "pat-openair-1"


def test_external_skill_provider_rejects_missing_action_evidence() -> None:
    with pytest.raises(DispatchProviderKnownRejected, match="skill_result_external_evidence_missing"):
        skill_provider_module._completed_submission(
            _external_runtime_result(content='{"state":"succeeded"}', include_evidence=False),
            frozen=_external_frozen_dispatch(),
        )


def test_external_skill_provider_preserves_only_safe_runtime_failure_diagnostics() -> None:
    with pytest.raises(DispatchProviderKnownRejected) as captured:
        skill_provider_module._completed_submission(
            {
                "state": "failed",
                "failureClassification": "provider_response_contract_invalid",
                "failedStage": "runtime_guard_failed",
                "firstMismatch": "P07C-R03",
                "diagnostics": {"providerBody": "must-not-persist"},
            },
            frozen=_external_frozen_dispatch(),
        )

    assert captured.value.code == "provider_response_contract_invalid"
    assert captured.value.failure_detail == {
        "failedStage": "runtime_guard_failed",
        "firstMismatch": "P07C-R03",
    }


def test_skill_provider_does_not_scan_free_text_for_source_identity() -> None:
    frozen = _external_frozen_dispatch()
    content = '{"state":"succeeded","source":"待补 DOI"}'
    completed = skill_provider_module._completed_submission(
        _external_runtime_result(content=content),
        frozen=frozen,
    )

    assert completed.content == content


def _mutate_resolver_task_to_skill_composition(task, snapshot) -> None:
    decision = task.execution_decisions[0]
    first = decision.assignments[0]
    reviewer = decision.assignments[-1]
    second = SimpleNamespace(
        assignment_id="assignment-second",
        role=SimpleNamespace(value="primary"),
        executor_ref=_Executor("skill", "candidate_dedup_and_provenance", 1),
        responsible_actor_ref=_Actor("orchestrator", "openair_orchestrator", None),
        allowed_skill_refs=[],
        source_binding_refs=[
            _Ref(
                "bind:sdr:literature_search:skill:candidate_dedup_and_provenance",
                1,
            )
        ],
        dependency_assignment_ids=[first.assignment_id],
    )
    reviewer.dependency_assignment_ids = [second.assignment_id]
    decision.strategy = SimpleNamespace(value="skill_composition")
    decision.assignments = [first, second, reviewer]
    decision.runtime_readiness.append(
        SimpleNamespace(
            capability_ref=SimpleNamespace(
                skill_id="candidate_dedup_and_provenance",
                agent_id=None,
                definition_version=1,
            ),
            ready=True,
            runtime_binding_id="python_adapter:candidate_dedup_and_provenance",
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        )
    )
    snapshot["definitions"]["substages"] = [
        {
            "definitionId": "sdr:literature_search",
            "definitionVersion": 1,
            "executionComposition": {
                "semantics": "ordered_required",
                "requiredSkillRefs": [
                    {"skillId": "literature_search", "definitionVersion": 1},
                    {"skillId": "candidate_dedup_and_provenance", "definitionVersion": 1},
                ],
            },
        }
    ]


def test_production_binding_resolver_accepts_first_skill_of_frozen_composition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from worker import entrypoint

    task, snapshot = _resolver_task(executor_type="skill")
    _mutate_resolver_task_to_skill_composition(task, snapshot)
    repository = _ResolverRepository(task, snapshot)
    resolver = entrypoint.PostgresDispatchProviderBindingResolver(repository=repository)
    monkeypatch.setattr(entrypoint.settings, "research_orchestration_v1", True)
    monkeypatch.setattr(entrypoint.settings, "persistence_backend", "postgres")
    monkeypatch.setattr(resolver, "_candidate_rows", lambda: [_candidate_row()])
    monkeypatch.setattr(
        entrypoint,
        "resolve_registered_durable_skill_runtime",
        lambda *_args, **_kwargs: _runtime(),
    )

    binding = resolver.resolve()

    assert binding is not None
    assert binding.executor_type == "skill"
    assert binding.executor_ref["executorId"] == "literature_search"


def test_frozen_skill_request_recovers_unique_artifact_type_from_same_snapshot_capability_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        skill_provider_module,
        "resolve_registered_durable_skill_runtime",
        lambda *_args, **_kwargs: _runtime(),
    )
    definition = _snapshot_definition()
    # Model the historical shape that caused W2-3: the public outputContract
    # projection is empty, while the immutable frozen Skill sourceContract
    # still carries the formal capability declaration and public schemas.
    definition["sourceContract"]["frozenSchemas"]["output"] = {
        "sha256": "3" * 64,
        "schema": {
            "type": "object",
            "properties": {"items": {"type": "array"}},
            "additionalProperties": True,
        },
    }
    definition["sourceContract"]["capability_contract"] = {
        "artifact_types": ["cs_reproduction_execution_inputs"],
        "judgment_support": {
            "deterministic": False,
            "domain_professional": False,
        },
    }
    definition["outputContract"] = {"status": "unknown", "artifactTypes": []}
    repository = _Repository(_payload(), _snapshot(definition))

    frozen = skill_provider_module.frozen_skill_request_for_dispatch(
        _request(_payload()),
        skill_id="literature_search",
        definition_version=1,
        snapshot_definition=definition,
        source_binding_refs=(("bind:sdr:literature_search:skill:literature_search", 1),),
        repository=repository,  # type: ignore[arg-type]
    )

    assert frozen.orchestrationPlan["artifactKind"] == "cs_reproduction_execution_inputs"


def test_frozen_skill_request_does_not_guess_artifact_type_without_snapshot_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        skill_provider_module,
        "resolve_registered_durable_skill_runtime",
        lambda *_args, **_kwargs: _runtime(),
    )
    definition = _snapshot_definition()
    repository = _Repository(_payload(), _snapshot(definition))

    frozen = skill_provider_module.frozen_skill_request_for_dispatch(
        _request(_payload()),
        skill_id="literature_search",
        definition_version=1,
        snapshot_definition=definition,
        source_binding_refs=(("bind:sdr:literature_search:skill:literature_search", 1),),
        repository=repository,  # type: ignore[arg-type]
    )

    assert "artifactKind" not in frozen.orchestrationPlan


def test_frozen_skill_request_reuses_only_a_successfully_closed_decision_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        skill_provider_module,
        "resolve_registered_durable_skill_runtime",
        lambda *_args, **_kwargs: _runtime(),
    )
    definition = _snapshot_definition()
    repository = _Repository(_payload(), _snapshot(definition))
    decision = repository.task.execution_decisions[0]
    decision.status = SimpleNamespace(value="closed")
    decision.outcome = SimpleNamespace(value="succeeded")

    with pytest.raises(DispatchProviderKnownRejected, match="dispatch_assignment_snapshot_mismatch"):
        skill_provider_module.frozen_skill_request_for_dispatch(
            _request(_payload()),
            skill_id="literature_search",
            definition_version=1,
            snapshot_definition=definition,
            source_binding_refs=(("bind:sdr:literature_search:skill:literature_search", 1),),
            repository=repository,  # type: ignore[arg-type]
        )

    frozen = skill_provider_module.frozen_skill_request_for_dispatch(
        _request(_payload()),
        skill_id="literature_search",
        definition_version=1,
        snapshot_definition=definition,
        source_binding_refs=(("bind:sdr:literature_search:skill:literature_search", 1),),
        repository=repository,  # type: ignore[arg-type]
        allow_closed_decision_for_result_recovery=True,
    )

    assert frozen.skill_id == "literature_search"


def test_direct_skill_provider_revalidates_frozen_composition_sequence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        skill_provider_module,
        "resolve_registered_durable_skill_runtime",
        lambda *_args, **_kwargs: _runtime(),
    )
    definition = _snapshot_definition()
    payload = _payload()
    repository = _Repository(payload, _snapshot(definition))
    decision = repository.task.execution_decisions[0]
    first = decision.assignments[0]
    reviewer = decision.assignments[-1]
    second = SimpleNamespace(
        assignment_id="assignment-second",
        role=SimpleNamespace(value="primary"),
        executor_ref=_Executor("skill", "candidate_dedup_and_provenance", 1),
        responsible_actor_ref=_Actor("orchestrator", "openair_orchestrator", None),
        allowed_skill_refs=[],
        source_binding_refs=[_Ref("bind:sdr:literature_search:skill:candidate_dedup_and_provenance", 1)],
        dependency_assignment_ids=[first.assignment_id],
    )
    reviewer.dependency_assignment_ids = [second.assignment_id]
    decision.strategy = ExecutionStrategy.SKILL_COMPOSITION
    decision.assignments = [first, second, reviewer]
    repository.snapshot["definitions"]["substages"] = [
        {
            "definitionId": "sdr:literature_search",
            "definitionVersion": 1,
            "executionComposition": {
                "semantics": "ordered_required",
                "requiredSkillRefs": [
                    {"skillId": "literature_search", "definitionVersion": 1},
                    {"skillId": "candidate_dedup_and_provenance", "definitionVersion": 1},
                ],
            },
        }
    ]

    frozen = skill_provider_module.frozen_skill_request_for_dispatch(
        _request(payload),
        skill_id="literature_search",
        definition_version=1,
        snapshot_definition=definition,
        source_binding_refs=(("bind:sdr:literature_search:skill:literature_search", 1),),
        repository=repository,  # type: ignore[arg-type]
    )

    assert frozen.skill_id == "literature_search"
    repository.snapshot["definitions"]["substages"][0]["executionComposition"][
        "requiredSkillRefs"
    ].reverse()
    with pytest.raises(DispatchProviderKnownRejected, match="dispatch_skill_composition_mismatch"):
        skill_provider_module.frozen_skill_request_for_dispatch(
            _request(payload),
            skill_id="literature_search",
            definition_version=1,
            snapshot_definition=definition,
            source_binding_refs=(("bind:sdr:literature_search:skill:literature_search", 1),),
            repository=repository,  # type: ignore[arg-type]
        )


def _direct_skill_handoff_fixture(
    *,
    binding_id: str,
    skill_id: str,
    resolved_parameters: dict[str, str],
    scientific_state_version: int,
) -> tuple[_Repository, dict[str, Any], dict[str, Any]]:
    binding = next(
        row for row in load_skill_binding_source_rows()
        if row["bindingId"] == binding_id
    )
    required_fields = list(resolved_parameters)
    definition = {
        "skillId": skill_id,
        "definitionVersion": 1,
        "status": "ready",
        "enabled": True,
        "sourceContract": {
            "skill_id": skill_id,
            "frozenSchemas": {
                "input": {
                    "sha256": "0" * 64,
                    "schema": {
                        "type": "object",
                        "required": required_fields,
                        "properties": {
                            field: {"type": "string", "minLength": 1}
                            for field in required_fields
                        },
                        "additionalProperties": False,
                    }
                },
                "output": {
                    "sha256": "c" * 64,
                    "schema": {
                        "type": "object",
                        "required": ["structured_context"],
                        "properties": {
                            "structured_context": {"type": "object"},
                        },
                        "additionalProperties": True,
                    },
                },
            },
        },
        "outputContract": {
            "status": "ready",
            "artifactTypes": [f"{skill_id}_artifact"],
        },
    }
    payload = _payload()
    repository = _Repository(payload, {
        "definitions": {"skills": [definition]},
        "bindings": {"skills": [{**binding, "enabled": True}]},
    })
    work_unit = repository.task.work_units[0]
    work_unit.executor_ref = _Executor("skill", skill_id, 1)
    work_unit.substage_definition_id = binding["definitionId"]
    work_unit.substage_definition_version = binding["definitionVersion"]
    work_unit.resolved_parameters = dict(resolved_parameters)
    work_unit.input_scientific_state_version = scientific_state_version
    work_unit.expected_artifact_types = [f"{skill_id}_artifact"]
    primary = repository.task.execution_decisions[0].assignments[0]
    primary.executor_ref = work_unit.executor_ref
    primary.source_binding_refs = [_Ref(binding["bindingId"], binding["bindingVersion"])]
    return repository, definition, binding


@pytest.mark.parametrize(
    ("binding_id", "skill_id", "resolved_parameters", "path_name"),
    [
        (
            "bind:pvd:research_goal_equipment_constraints:skill:pvd_research_request_understanding",
            "pvd_research_request_understanding",
            {"query": "Canonical user research question."},
            "pvd_problem_modeling",
        ),
        (
            "bind:opentrajectory:research_record_establishment:skill:problem_framing_generation",
            "problem_framing_generation",
            {"user_query": "Canonical user research question."},
            "ot_research_record_establishment",
        ),
    ],
)
def test_direct_skill_handoff_uses_canonical_binding_identity_before_runtime(
    monkeypatch: pytest.MonkeyPatch,
    binding_id: str,
    skill_id: str,
    resolved_parameters: dict[str, str],
    path_name: str,
) -> None:
    from app.execution import durable_skill_runtime

    repository, definition, binding = _direct_skill_handoff_fixture(
        binding_id=binding_id,
        skill_id=skill_id,
        resolved_parameters=resolved_parameters,
        scientific_state_version=7,
    )
    monkeypatch.setattr(
        skill_provider_module,
        "resolve_registered_durable_skill_runtime",
        lambda *_args, **_kwargs: _runtime(),
    )

    frozen = skill_provider_module.frozen_skill_request_for_dispatch(
        _request(_payload()),
        skill_id=skill_id,
        definition_version=1,
        snapshot_definition=definition,
        source_binding_refs=((binding["bindingId"], binding["bindingVersion"]),),
        repository=repository,  # type: ignore[arg-type]
    )

    evidence = frozen.orchestrationPlan["bindingInputEvidence"]
    assert path_name in {"pvd_problem_modeling", "ot_research_record_establishment"}
    assert "scientificStateVersion" not in evidence
    assert repository.task.work_units[0].input_scientific_state_version == 7
    assert binding_handoff_evidence_matches(skill_id, evidence) is True
    assert frozen.orchestrationPlan["artifactKind"] == f"{skill_id}_artifact"

    executed: list[dict[str, Any]] = []
    monkeypatch.setattr(
        durable_skill_runtime,
        "execute_durable_skill",
        lambda request: executed.append(request) or {"state": "succeeded"},
    )
    result = durable_skill_runtime.execute_production_durable_skill({
        "skillId": skill_id,
        "parameters": frozen.orchestrationPlan["resolvedParameters"],
        "bindingInputEvidence": evidence,
    })

    assert result["state"] == "succeeded"
    assert len(executed) == 1


def test_scientific_projection_without_work_unit_result_identity_rejects_before_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, definition, binding = _direct_skill_handoff_fixture(
        binding_id=(
            "bind:pvd:research_goal_equipment_constraints:"
            "skill:pvd_research_request_understanding"
        ),
        skill_id="pvd_research_request_understanding",
        resolved_parameters={"query": "Canonical user research question."},
        scientific_state_version=7,
    )
    binding["scientificStateProjection"] = {
        "contract_version": 2,
        "materializer_ref": "pvd:research-request-understanding:v1",
        "facts": [
            {
                "fact_key": "pvd_research_request_context",
                "source_field": "structured_context",
            }
        ],
    }
    repository.task.work_units[0].expected_artifact_types = []
    repository.task.execution_decisions[0].assignments[
        0
    ].expected_artifact_types = []
    monkeypatch.setattr(
        skill_provider_module,
        "resolve_registered_durable_skill_runtime",
        lambda *_args, **_kwargs: _runtime(),
    )

    with pytest.raises(
        DispatchProviderKnownRejected,
        match="scientific_state_result_identity_missing",
    ):
        skill_provider_module.frozen_skill_request_for_dispatch(
            _request(_payload()),
            skill_id="pvd_research_request_understanding",
            definition_version=1,
            snapshot_definition=definition,
            source_binding_refs=((binding["bindingId"], binding["bindingVersion"]),),
            repository=repository,  # type: ignore[arg-type]
        )


def test_direct_skill_handoff_true_binding_identity_mismatch_stays_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.execution import durable_skill_runtime

    binding = next(
        row for row in load_skill_binding_source_rows()
        if row["bindingId"]
        == "bind:opentrajectory:research_record_establishment:skill:problem_framing_generation"
    )
    evidence = {
        "bindingId": binding["bindingId"],
        "bindingVersion": binding["bindingVersion"] + 1,
        "bindingContractVersion": 2,
        "inputSources": binding.get("inputSources"),
        "runtimeInputs": binding.get("runtimeInputs"),
    }
    executed: list[dict[str, Any]] = []
    monkeypatch.setattr(
        durable_skill_runtime,
        "execute_durable_skill",
        lambda request: executed.append(request) or {"state": "succeeded"},
    )

    result = durable_skill_runtime.execute_production_durable_skill({
        "skillId": "problem_framing_generation",
        "parameters": {"user_query": "x"},
        "bindingInputEvidence": evidence,
    })

    assert result["state"] == "failed"
    assert result["failureClassification"] == "skill_binding_precondition_missing"
    assert executed == []


def test_direct_skill_handoff_missing_required_identity_stays_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.execution import durable_skill_runtime

    binding = next(
        row for row in load_skill_binding_source_rows()
        if row["bindingId"]
        == "bind:pvd:research_goal_equipment_constraints:skill:pvd_research_request_understanding"
    )
    evidence = {
        "bindingVersion": binding["bindingVersion"],
        "bindingContractVersion": 2,
        "inputSources": binding.get("inputSources"),
        "runtimeInputs": binding.get("runtimeInputs"),
    }
    executed: list[dict[str, Any]] = []
    monkeypatch.setattr(
        durable_skill_runtime,
        "execute_durable_skill",
        lambda request: executed.append(request) or {"state": "succeeded"},
    )

    result = durable_skill_runtime.execute_production_durable_skill({
        "skillId": "pvd_research_request_understanding",
        "parameters": {"query": "x"},
        "bindingInputEvidence": evidence,
    })

    assert result["state"] == "failed"
    assert result["failureClassification"] == "skill_binding_precondition_missing"
    assert executed == []


def test_resume_reuses_workunit_frozen_execution_policy_after_live_runtime_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    definition = _snapshot_definition()
    definition["sourceContract"]["durable_llm_execution_policy"] = {
        "contract_version": 1,
        "model_profile": "terra",
        "reasoning_effort": "none",
        "max_output_tokens": 6000,
        "read_timeout_seconds": 60,
        "response_mode": "json_object",
        "load_references": False,
        "structured_output_recovery_attempts": 1,
    }
    frozen_policy = {
        "contractVersion": 1,
        "providerId": "openai_primary",
        "adapterId": "huiyan_chat_completions",
        "model": "frozen-terra-model",
        "modelProfile": "terra",
        "reasoningEffort": "none",
        "maxOutputTokens": 6000,
        "readTimeoutSeconds": 60.0,
        "responseMode": "json_object",
        "loadReferences": False,
        "structuredOutputRecoveryAttempts": 1,
    }
    payload = {**_payload(), "resolvedExecutionPolicy": frozen_policy}
    repository = _Repository(payload, _snapshot(definition))
    calls: list[dict[str, Any]] = []
    changed_runtime = {**_runtime(), "logicalProviderId": "new_default_provider", "modelProfile": "sol"}
    monkeypatch.setattr(
        skill_provider_module,
        "resolve_registered_durable_skill_runtime",
        lambda *_args, **_kwargs: changed_runtime,
    )
    provider = DirectSkillResearchDispatchProvider(
        skill_id="literature_search",
        definition_version=1,
        snapshot_definition=definition,
        source_binding_refs=(("bind:sdr:literature_search:skill:literature_search", 1),),
        repository=repository,  # type: ignore[arg-type]
        runtime_resolver=lambda *_args, **_kwargs: changed_runtime,
        execute=lambda request: calls.append(request) or _success(
            provider_model="frozen-terra-model"
        ),
    )

    completed = provider.preflight().submit(_request(payload))

    assert len(calls) == 1
    assert calls[0]["resolvedExecutionPolicy"] == frozen_policy
    assert calls[0]["logicalProviderId"] == "openai_primary"
    assert calls[0]["modelProfile"] == "terra"
    assert completed.model == "frozen-terra-model"
