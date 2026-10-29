from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path

import httpx
import pytest
from app.execution import durable_skill_runtime, skill_contract_policy
from app.execution.research_dispatch_provider import DispatchSubmissionCompleted
from app.execution.research_dispatch_skill_provider import (
    FrozenDirectSkillDispatch,
    _completed_submission,
)

ROOT = Path(__file__).resolve().parents[4]


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def production_harness(monkeypatch):
    """Reuse the established HTTP/evidence harness, never its old verdicts."""
    runtime = durable_skill_runtime
    folder = os.environ.get("T2_PARSE_CANDIDATE_DIR")
    if folder:
        candidate_policy = _load_module(
            "t2_parse_candidate_policy", Path(folder) / "skill_contract_policy_candidate.py"
        )
        monkeypatch.setitem(sys.modules, "app.execution.skill_contract_policy", candidate_policy)
        runtime = _load_module(
            "t2_parse_candidate_runtime", Path(folder) / "durable_skill_runtime_candidate.py"
        )
    else:
        assert sys.modules["app.execution.skill_contract_policy"] is skill_contract_policy
    harness = _load_module(
        "t2_parse_http_harness", ROOT / "services/api/tests/test_durable_skill_runtime.py"
    )
    monkeypatch.setattr(harness, "durable_skill_runtime", runtime)
    return harness


def _assert_readable_delivery(result, harness, expected, *, stage="json_parse_failed"):
    assert result["state"] == "succeeded"
    assert result["externalActionEvidence"] == []
    artifact = result["artifactPayload"]
    content = base64.b64decode(artifact["contentBase64"])
    assert content.decode("utf-8") == expected
    presentation = artifact["presentation"]
    assert presentation["contentSha256"] == hashlib.sha256(content).hexdigest()
    assert presentation["contentSize"] == len(content)
    metadata = presentation["metadata"]
    assert metadata["artifactQuality"] == "degraded"
    protocol = metadata["resultProtocol"]
    assert protocol["status"] == "degraded"
    assert protocol["accepted_items"] == []
    assert protocol["quarantined_items"] == [{"path": "$", "reason": stage}]
    skill = harness._generic_frozen_finalizer_skill()
    frozen = FrozenDirectSkillDispatch(
        skill_id=skill["skillId"],
        definition_version=1,
        artifactContract="research-result.v1",
        artifactSchemaDigest="0" * 64,
        artifactMimeType="application/json",
        logicalProviderId="openai_primary",
        modelProfile="terra",
        resolvedExecutionPolicy=harness._frozen_execution_policy(),
        runtimeKind="llm_reasoning",
        requiresExternalAction=False,
        adapterId="huiyan_chat_completions",
        evidenceContractVersion="none",
        orchestrationPlan={},
    )
    wrapped = _completed_submission(result, frozen=frozen)
    assert wrapped.content == expected
    assert wrapped.result_protocol == protocol
    assert wrapped.external_action_evidence == ()
    assert result["relatedSkillId"] == frozen.skill_id
    return protocol


@pytest.mark.parametrize(
    "raw", ["Readable research with unknown pressure.", "[1,null]", '"raw text"']
)
def test_known_non_object_response_reaches_dispatch_with_limits(
    monkeypatch, production_harness, raw
):
    result, requests, evidence, _ = production_harness._execute_generic_policy_skill(
        monkeypatch, response_contents=[raw], recovery_attempts=0
    )
    protocol = _assert_readable_delivery(result, production_harness, raw)
    assert len(requests) == 1
    assert [row["outcome"] for row in evidence] == ["response_received"]
    assert any("structured_output_json_object_required" in row for row in protocol["warnings"])


@pytest.mark.parametrize("correction", ["still invalid", "[]", ""])
def test_exhausted_correction_preserves_primary_and_failure_fact(
    monkeypatch, production_harness, correction
):
    primary = "Original usable observations; no experiment was run."
    result, requests, evidence, _ = production_harness._execute_generic_policy_skill(
        monkeypatch, response_contents=[primary, correction]
    )
    protocol = _assert_readable_delivery(result, production_harness, primary)
    assert len(requests) == 2
    assert [row["kind"] for row in evidence] == ["primary", "recovery"]
    assert [row["outcome"] for row in evidence] == ["response_received", "response_received"]
    assert any(
        "provider_structured_output_correction_exhausted" in row for row in protocol["warnings"]
    )


def test_valid_object_keeps_formal_finalization(monkeypatch, production_harness):
    result, requests, _, _ = production_harness._execute_generic_policy_skill(
        monkeypatch, response_contents=[json.dumps({"draft": "valid"})]
    )
    assert len(requests) == 1
    assert result["state"] == "succeeded"
    assert json.loads(base64.b64decode(result["artifactPayload"]["contentBase64"])) == {
        "canonical": "final output"
    }
    assert result["artifactPayload"]["presentation"]["metadata"]["artifactQuality"] == "standard"


@pytest.mark.parametrize("raw", ["", "   "])
def test_empty_material_does_not_become_delivery(monkeypatch, production_harness, raw):
    result, requests, _, _ = production_harness._execute_generic_policy_skill(
        monkeypatch, response_contents=[raw]
    )
    assert result["state"] == "failed"
    assert "artifactPayload" not in result
    assert len(requests) == 1


@pytest.mark.parametrize("status", [400, 500])
def test_provider_http_failure_is_not_reclassified(monkeypatch, production_harness, status):
    result, requests, _, _ = production_harness._execute_generic_policy_skill(
        monkeypatch, response_contents=["unavailable"], status_codes=[status]
    )
    assert result["state"] == "failed"
    assert "artifactPayload" not in result
    assert len(requests) == 1


def test_correction_unknown_keeps_identity_and_does_not_retry(monkeypatch, production_harness):
    result, requests, evidence, _ = production_harness._execute_generic_policy_skill(
        monkeypatch, response_contents=["primary", httpx.ReadTimeout("after submission")]
    )
    assert result["state"] == "unknown"
    assert result["externalResultUnknown"] is True
    assert result["providerExecutionId"] == "attempt-recovery"
    assert "artifactPayload" not in result
    assert len(requests) == 2
    assert [row["kind"] for row in evidence] == ["primary", "recovery"]


@pytest.mark.parametrize("quarantined", [True, False])
@pytest.mark.parametrize("sources_declared", [True, False])
def test_materialization_preserves_blob_and_respects_structure_isolation(
    monkeypatch, production_harness, quarantined, sources_declared
):
    harness = _load_module(
        "t2_parse_dispatch_harness", ROOT / "services/api/tests/test_research_dispatch_service.py"
    )
    declaration = {
        "contract_version": 1,
        "facts": [{"fact_key": "research.findings", "source_field": "findings"}],
    }
    frozen = type(
        "FrozenMaterialization",
        (),
        {
            "skill_id": "generic_research_skill",
            "definition_version": 1,
            "artifactContract": "research-result.v1",
            "artifactSchemaDigest": "0" * 64,
            "orchestrationPlan": {
                "assignmentRole": "primary",
                "artifactKind": "research_result",
                "scientificStateProjection": declaration,
            },
        },
    )()
    if sources_declared:
        frozen.orchestrationPlan["externalSourceRecords"] = [
            {
                "record_path": "/paper_packets/*",
                "identity_fields": [
                    {"field_path": "/arxiv_id", "identity_type": "arxiv_identifier"}
                ],
            }
        ]
    monkeypatch.setattr(
        harness.dispatch_service_module,
        "validate_completed_dispatch_result",
        lambda *_args, **_kwargs: frozen,
    )
    monkeypatch.setattr(harness.dispatch_service_module.settings, "artifact_storage", "local")
    blobs = {}
    harness._bind_durable_blobs(monkeypatch, blobs)
    recorded = {}

    class Repository(harness.Repository):
        def record_dispatch_completed_result(self, fence, **kwargs):
            recorded.update(kwargs)
            recorded["fence"] = fence
            return True

    protocol = {
        "status": "degraded",
        "quarantined_items": [{"path": "$", "reason": "json_parse_failed"}] if quarantined else [],
        "warnings": ["research_content_incomplete"],
    }
    content = (
        "Original content with unknowns"
        if quarantined
        else '{"findings":["valid"],"paper_packets":[{"arxiv_id":"2409.00001"}]}'
    )
    result = DispatchSubmissionCompleted(
        title="Research material",
        mime_type="application/json",
        content=content,
        provider_id="direct_skill_runtime",
        adapter="durable_skill_runtime",
        result_protocol=protocol,
    )
    service = harness.ResearchDispatchService(Repository())
    fence = harness._recovery_dispatch()
    assert service.record_completed_result(
        fence, owner_id="owner-1", workspace_id="workspace-1", result=result
    )
    assert recorded["content_sha256"] == hashlib.sha256(content.encode()).hexdigest()
    assert recorded["scientific_state_fact_projections"] == (
        {} if quarantined else {"research.findings": ["valid"]}
    )
    assert recorded["scientific_state_projection_declaration"] == declaration
    assert recorded["fence"].task_id == "task-1"
    assert recorded["result"].result_protocol["status"] == "degraded"
    acceptance = recorded["source_identity_acceptance"]
    assert acceptance["status"] == (
        "not_validated"
        if quarantined and sources_declared
        else "validated"
        if sources_declared
        else "not_declared"
    )
    assert acceptance["acceptedCount"] == (1 if sources_declared and not quarantined else 0)
    envelope = json.loads(blobs[recorded["storage_ref"]])
    assert envelope["content"] == content
    assert envelope["dispatchId"] == fence.dispatch_id
    assert envelope["resultProtocol"]["quarantined_items"] == protocol["quarantined_items"]
