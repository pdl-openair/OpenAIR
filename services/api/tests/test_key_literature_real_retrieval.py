from __future__ import annotations

import base64
import hashlib
import json
from copy import deepcopy
from functools import lru_cache, partial
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

import pytest
from app.execution import research_dispatch_huiyan_provider as provider_module
from app.execution.research_dispatch_provider import (
    DispatchProviderResultUnknown,
    DispatchSubmissionRequest,
)
from app.modules.research_orchestration_resolver import build_registry_snapshot
from jsonschema import Draft202012Validator


def _request() -> DispatchSubmissionRequest:
    return DispatchSubmissionRequest(
        dispatch_id="dispatch-key-literature-1",
        task_id="task-key-literature-1",
        execution_decision_id="decision-key-literature-1",
        work_unit_id="work-key-literature-1",
        idempotency_key="workunit:decision-key-literature-1:work-key-literature-1",
        owner_id="owner-1",
        workspace_id="workspace-1",
        payload={
            "authorizationSnapshot": {
                "confirmationRefs": ["research-design-confirmation:1"],
            },
            "registrySnapshotRef": {"contentHash": "snapshot-hash"},
        },
        capability_call_id="call-key-literature-1",
        attempt_number=1,
    )


def _frozen() -> SimpleNamespace:
    return SimpleNamespace(
        selectedAgentId="key_literature_positioning",
        selectedSkillIds=(
            "intent_extraction",
            "literature_review",
            "academic_source_retrieval_skill",
        ),
        artifactContract="key_literature_positioning_artifact_v1",
        modelProfile="terra",
        allowSkillRequests=False,
        frozenInputSnapshot={
            "instruction": "Identify the key literature.",
            "resolvedInputMemory": {
                "user_goal": "Find grounded GraphRAG literature for scientific QA",
            },
        },
    )


@lru_cache(maxsize=1)
def _snapshot_envelope():
    return build_registry_snapshot().envelope


class _SnapshotRepository:
    def __init__(self) -> None:
        self.snapshot = deepcopy(_snapshot_envelope())

    def load_registry_snapshot(self, content_hash: str) -> Mapping[str, Any]:
        assert content_hash == "snapshot-hash"
        return self.snapshot


def _retrieval_output() -> tuple[dict[str, Any], dict[str, Any]]:
    material = {
        "retrieval_report": {
            "source_counts": {"openalex": 1, "semantic_scholar": 0, "arxiv": 0},
            "query_counts": {"GraphRAG scientific literature question answering": 1},
            "failed_sources": [],
        },
        "metadata_candidates": [{
            "title": "From Local to Global: A Graph RAG Approach to Query-Focused Summarization",
            "doi": "10.48550/arXiv.2404.16130",
            "url": "https://arxiv.org/abs/2404.16130",
            "source_name": "openalex",
            "provenance": {"source_name": "openalex"},
        }],
    }
    raw = json.dumps(material, ensure_ascii=False, indent=2).encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    return material, {
        "state": "succeeded",
        "relatedSkillId": "academic_source_retrieval_skill",
        "externalActionEvidence": [{
            "adapter": "python_adapter:academic_source_retrieval_skill",
            "outcome": "completed",
            "evidenceRef": f"skill-result:academic_source_retrieval_skill:sha256:{digest}",
            "contentSha256": digest,
        }],
        "artifactPayload": {
            "contentBase64": base64.b64encode(raw).decode("ascii"),
            "contentType": "application/json",
            "presentation": {
                "title": "academic_source_retrieval_skill result",
                "mimeType": "application/json",
                "contentSha256": digest,
                "contentSize": len(raw),
            },
        },
    }


def test_key_literature_snapshot_freezes_existing_real_retrieval_skill() -> None:
    snapshot = _snapshot_envelope()
    grants = snapshot["bindings"]["agentSkills"]
    assert any(
        row.get("agentId") == "key_literature_positioning"
        and row.get("skillId") == "academic_source_retrieval_skill"
        and row.get("enabled") is True
        for row in grants
    )
    skill = next(
        row
        for row in snapshot["definitions"]["skills"]
        if row.get("skillId") == "academic_source_retrieval_skill"
    )
    runtime = skill["sourceContract"]["runtime_contract"]
    assert runtime == {
        "kind": "external_adapter",
        "requires_external_action": True,
        "adapter_id": "python_adapter:academic_source_retrieval_skill",
        "evidence_contract_version": "external-action-evidence/v1",
    }


def test_key_literature_retrieval_is_persisted_and_read_back_before_use(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected_material, runtime_result = _retrieval_output()
    events: list[str] = []
    captured: dict[str, Any] = {}
    blobs: dict[str, bytes] = {}

    def execute_chain(**kwargs) -> dict[str, Any]:
        events.append("retrieve")
        captured.update(kwargs)
        return runtime_result

    def store(_task_id: str, storage_ref: str, data: bytes, _content_type: str):
        events.append("store")
        blobs[storage_ref] = data
        return SimpleNamespace(storage_type="local", storage_ref=storage_ref)

    def read(_task_id: str, _storage_type: str, storage_ref: str, _storage_scope: str | None = None) -> bytes | None:
        events.append("read")
        return blobs.get(storage_ref)

    monkeypatch.setattr(provider_module, "execute_frozen_agent_skill", execute_chain)
    monkeypatch.setattr(provider_module.artifact_write_service, "store_durable_bytes", store)
    monkeypatch.setattr(provider_module.artifact_write_service, "read_durable_bytes", read)

    retrieval = provider_module._execute_key_literature_retrieval(
        _request(), _frozen(), repository=_SnapshotRepository()  # type: ignore[arg-type]
    )

    assert events == ["retrieve", "store", "read"]
    assert captured["target_skill_ref"] == ("academic_source_retrieval_skill", 1)
    canonical_query = captured["canonical_user_query"]
    assert canonical_query
    assert "GraphRAG" in canonical_query
    assert "scientific QA" in canonical_query
    assert captured["parameters"]["retrieval_config"]["target_sources"] == [
        "openalex", "semantic_scholar", "arxiv"
    ]
    assert retrieval.material["retrievalResult"] == expected_material
    assert retrieval.material["contentSha256"] == retrieval.external_action_evidence[0]["contentSha256"]


def test_key_literature_chinese_goal_is_not_semantically_decomposed_by_python_before_retrieval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _material, runtime_result = _retrieval_output()
    frozen = _frozen()
    chinese_goal = (
        "调研近几年固态锂电池中硫化物固态电解质的主要材料体系，"
        "比较其离子电导率、界面稳定性和工程化难点，并形成结构化研究结论。"
    )
    frozen.frozenInputSnapshot["resolvedInputMemory"]["user_goal"] = chinese_goal
    captured: dict[str, Any] = {}
    monkeypatch.delenv("LLM_PROVIDER_API_KEY", raising=False)

    def execute_chain(**kwargs) -> dict[str, Any]:
        captured.update(kwargs)
        return runtime_result

    monkeypatch.setattr(provider_module, "execute_frozen_agent_skill", execute_chain)
    monkeypatch.setattr(
        provider_module.artifact_write_service,
        "store_durable_bytes",
        lambda _task_id, storage_ref, data, _content_type: SimpleNamespace(
            storage_type="local", storage_ref=storage_ref, storage_scope=None, data=data
        ),
    )
    monkeypatch.setattr(
        provider_module.artifact_write_service,
        "read_durable_bytes",
        lambda _task_id, _storage_type, _storage_ref, _storage_scope=None: base64.b64decode(
            runtime_result["artifactPayload"]["contentBase64"]
        ),
    )

    provider_module._execute_key_literature_retrieval(
        _request(), frozen, repository=_SnapshotRepository()  # type: ignore[arg-type]
    )

    assert captured["canonical_user_query"] == chinese_goal
    overrides = captured["parameters"]
    assert overrides["expanded_query_variants"] == []
    assert overrides["search_matrix"] == []
    assert overrides["user_query"] == chinese_goal


def test_key_literature_retrieval_unknown_fails_closed_before_downstream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stored = False

    monkeypatch.setattr(
        provider_module,
        "execute_frozen_agent_skill",
        lambda **_kwargs: {"state": "unknown", "externalResultUnknown": True},
    )

    def store(*_args, **_kwargs):
        nonlocal stored
        stored = True
        raise AssertionError("unknown retrieval must not be persisted as successful evidence")

    monkeypatch.setattr(provider_module.artifact_write_service, "store_durable_bytes", store)

    with pytest.raises(DispatchProviderResultUnknown, match="key_literature_retrieval_result_unknown"):
        provider_module._execute_key_literature_retrieval(
            _request(), _frozen(), repository=_SnapshotRepository()  # type: ignore[arg-type]
        )
    assert stored is False



def test_key_literature_calls_real_strict_agent_skill_boundary(monkeypatch):
    from app.execution.skill_dependency_runtime import execute_frozen_agent_skill
    from app.execution.skill_schema_contract import build_frozen_skill_schema_contract

    _material, result = _retrieval_output()
    repository = _SnapshotRepository()
    seen = []
    skill = next(
        row for row in repository.snapshot["definitions"]["skills"]
        if row["skillId"] == "academic_source_retrieval_skill"
    )
    schema = build_frozen_skill_schema_contract(skill["skillPath"])["input"]["schema"]

    def execute(request):
        Draft202012Validator(schema).validate(request["parameters"])
        seen.append(request)
        return result

    monkeypatch.setattr(
        provider_module, "execute_frozen_agent_skill",
        partial(execute_frozen_agent_skill, execute=execute),
    )
    raw = base64.b64decode(result["artifactPayload"]["contentBase64"])
    monkeypatch.setattr(
        provider_module.artifact_write_service, "store_durable_bytes",
        lambda *args: SimpleNamespace(storage_type="local", storage_ref="offline"),
    )
    monkeypatch.setattr(
        provider_module.artifact_write_service, "read_durable_bytes", lambda *args: raw,
    )
    provider_module._execute_key_literature_retrieval(
        _request(), _frozen(), repository=repository,
    )
    assert len(seen) == 1
    assert seen[0]["parameters"]["user_query"]
    confirmed_goal = _frozen().frozenInputSnapshot["resolvedInputMemory"]["user_goal"]
    assert seen[0]["frozenGoal"] == confirmed_goal


ROOT = Path(__file__).resolve().parents[3]


def _retrieval_evidence() -> provider_module._KeyLiteratureRetrievalEvidence:
    material, runtime_result = _retrieval_output()
    return provider_module._KeyLiteratureRetrievalEvidence(
        material={"retrievalResult": material},
        external_action_evidence=tuple(runtime_result["externalActionEvidence"]),
    )


def _validate_key_literature_schema(content: str) -> dict[str, Any]:
    schema = json.loads(
        (ROOT / "schemas/agents/artifacts/key_literature_positioning_artifact_v1.schema.json").read_text(
            encoding="utf-8"
        )
    )
    body = json.loads(content)
    Draft202012Validator(schema).validate(body)
    return body


def test_key_literature_seed_artifact_is_schema_valid_and_retrieval_only() -> None:
    body = _validate_key_literature_schema(
        provider_module._key_literature_seed_content(_frozen(), _retrieval_evidence())
    )

    assert body["search_boundary"]["retrieval_outcome"] == "attempted"
    assert body["reading_priority"] == []
    assert len(body["candidate_literature"]) == 1
    candidate = body["candidate_literature"][0]
    assert candidate["title"] == (
        "From Local to Global: A Graph RAG Approach to Query-Focused Summarization"
    )
    assert candidate["source_identifier"] in {
        "doi:10.48550/arxiv.2404.16130",
        "arxiv:2404.16130",
        "https://arxiv.org/abs/2404.16130",
    }
    assert candidate["screening_status"] == "seed"
    assert candidate["role_hint"] == "other"
    assert "provenance-grounded" in body["coverage_limitations"][0]["statement"]


def test_key_literature_seed_artifact_drops_unusable_rows_without_rejecting_usable_sources() -> None:
    material, runtime_result = _retrieval_output()
    material["metadata_candidates"].append({
        "title": "Unusable row without source identity",
        "source_name": "openalex",
        "provenance": {"source_name": "openalex"},
    })
    retrieval = provider_module._KeyLiteratureRetrievalEvidence(
        material={"retrievalResult": material},
        external_action_evidence=tuple(runtime_result["externalActionEvidence"]),
    )

    body = _validate_key_literature_schema(
        provider_module._key_literature_seed_content(_frozen(), retrieval)
    )

    assert [row["title"] for row in body["candidate_literature"]] == [
        "From Local to Global: A Graph RAG Approach to Query-Focused Summarization"
    ]


def test_submit_key_literature_fastpath_never_calls_positioning_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frozen = _frozen()
    retrieval = _retrieval_evidence()
    monkeypatch.setattr(
        provider_module,
        "frozen_request_for_dispatch",
        lambda *_args, **_kwargs: frozen,
    )
    monkeypatch.setattr(
        provider_module,
        "_execute_key_literature_retrieval",
        lambda *_args, **_kwargs: retrieval,
    )

    class Executor:
        def execute(self, *_args, **_kwargs):
            raise AssertionError(
                "key literature fast path must not spend another model call rewriting retrieval citations"
            )

    provider = provider_module.PreparedHuiyanResearchDispatch(
        provider_id="openai_primary",
        capabilities=SimpleNamespace(),  # type: ignore[arg-type]
        repository=SimpleNamespace(),  # type: ignore[arg-type]
        executor=Executor(),  # type: ignore[arg-type]
    )

    completed = provider.submit(_request())
    body = _validate_key_literature_schema(completed.content)

    assert completed.adapter == "python_adapter:academic_source_retrieval_skill"
    assert completed.model is None
    assert completed.external_action_evidence == retrieval.external_action_evidence
    assert body["handoff"]["handoff_status"] == "ready_for_next_consumer"
    assert body["handoff"]["candidate_ids"] == ["retrieval_seed_01"]
