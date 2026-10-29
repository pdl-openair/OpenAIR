from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import httpx
import pytest
from jsonschema import Draft7Validator

from app.execution import durable_skill_runtime, skill_finalization_runtime
from app.execution.research_input_contract_gate import (
    InputContractGateFailure,
    SkillInputRequest,
    resolve_registered_skill_input,
)
from app.modules.artifact_content import load_artifact_content
from app.modules.provider_transport_evidence import ProviderAttemptEvidenceError, ProviderHttpAttempt
from app.modules.skill_binding_source import BINDING_CONTRACT_VERSION, load_skill_binding_source_rows


REPO_ROOT = Path(__file__).resolve().parents[3]
SKILL_ROOT = REPO_ROOT / "skills" / "opentrajectory" / "evidence-schema-design-skill"


class _ReservedAttemptRepository:
    def begin(self, **kwargs) -> ProviderHttpAttempt:
        call_id = str(kwargs["capability_call_id"] or "").strip()
        if not call_id or kwargs["attempt_number"] != 1:
            raise ProviderAttemptEvidenceError("provider_attempt_identity_invalid")
        return ProviderHttpAttempt(
            attempt_id=f"attempt-{call_id}",
            capability_call_id=call_id,
            attempt_number=1,
            request_hash=hashlib.sha256(kwargs["request_body"]).hexdigest(),
            provider_endpoint_identity="test:endpoint",
        )

    def begin_sub_attempt(self, **kwargs) -> ProviderHttpAttempt:
        call_id = str(kwargs["capability_call_id"] or "").strip()
        if not call_id:
            raise ProviderAttemptEvidenceError("provider_sub_attempt_identity_invalid")
        return ProviderHttpAttempt(
            attempt_id=f"sub-attempt-{call_id}",
            capability_call_id=call_id,
            attempt_number=2,
            request_hash=hashlib.sha256(kwargs["request_body"]).hexdigest(),
            provider_endpoint_identity="test:endpoint",
            evidence_kind=str(kwargs["evidence_kind"]),
            adapter_name=str(kwargs["adapter_name"]),
        )

    def mark_io_started(self, _attempt: ProviderHttpAttempt) -> None:
        return None

    def finish(self, _attempt: ProviderHttpAttempt, **_kwargs) -> None:
        return None

    def mark_io_started(self, _attempt: ProviderHttpAttempt) -> None:
        return None


class _ResponseEvidence:
    status_code = 200
    headers: dict[str, str] = {}

    @property
    def content(self) -> bytes:
        return json.dumps(self.json(), ensure_ascii=True, separators=(",", ":")).encode("utf-8")

    def raise_for_status(self) -> None:
        return None


@pytest.fixture(autouse=True)
def reserved_provider_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(durable_skill_runtime, "ProviderHttpAttemptRepository", _ReservedAttemptRepository)


def _parameters() -> dict:
    constraints_criteria = {
        "interpreted_constraints": [{
            "constraint_name": "test_condition",
            "original_phrase": "under the stated test condition",
            "constraint_type": "experimental_condition",
            "interpreted_value": "stated test condition",
            "candidate_values": [],
            "semantic_state": "confirmed",
            "domain_rationale": "The condition is explicit in the research request.",
            "downstream_search_terms": ["test condition"],
            "evidence_basis": "original user query",
        }],
        "missing_information": [],
        "research_risks": [],
    }
    retrieval_strategy_design = json.loads(
        (
            REPO_ROOT
            / "skills/opentrajectory/retrieval-matrix-generation/examples/response.json"
        ).read_text(encoding="utf-8")
    )
    retrieval_strategy_design["result_protocol"] = {
        "status": "ok",
        "accepted_items": [
            {"path": "search_matrix", "count": 7},
            {"path": "expanded_query_variants", "count": 8},
        ],
        "quarantined_items": [],
        "repaired_items": [],
        "warnings": [],
        "coverage": {
            "search_intent_count": 7,
            "search_intent_total": 7,
            "accepted_query_count": 8,
            "quarantined_item_count": 0,
        },
    }
    return {
        "task_id": "task-t0b-evidence-schema",
        "intent_understanding": json.loads(
            (REPO_ROOT / "skills/opentrajectory/structured-requirement-generation/examples/response.json").read_text(encoding="utf-8")
        ),
        "constraints_criteria": constraints_criteria,
        "retrieval_strategy_design": retrieval_strategy_design,
    }


def _semantic_draft() -> dict:
    return {
        "ready": [
            {
                "task_scope": "Preserve formulation, test conditions, result metrics, and comparison context.",
                "domain_hints": ["photocatalysis", "materials science"],
                "design_assumptions": ["Unreported experimental values remain unknown."],
                "evidence_targets": [
                    {
                        "target_type": "performance_metric",
                        "scientific_role": "metric",
                        "description": "Record degradation performance together with the experimental context.",
                        "requiredness": "required",
                        "completion_criteria": "A reproducible source reports the metric and its test conditions.",
                        "allowed_evidence_forms": ["results text", "table", "figure"],
                        "condition_dimensions": ["illumination", "pollutant", "catalyst loading"],
                        "comparison_requirements": ["same metric definition and comparable test conditions"],
                        "provenance_requirements": ["source anchor"],
                    }
                ],
                "gap_taxonomy": ["missing_condition", "missing_metric", "not_comparable"],
                "uncertainty_rules": [
                    {
                        "trigger": "A comparison condition is not reported.",
                        "handling": "Keep the comparison unresolved.",
                    }
                ],
                "design_warnings": [],
            }
        ],
        "blocked": [],
    }


def _binding_evidence() -> dict:
    binding = next(row for row in load_skill_binding_source_rows() if row["skillId"] == "evidence_schema_design")
    return {
        "bindingId": binding["bindingId"],
        "bindingVersion": binding["bindingVersion"],
        "bindingContractVersion": BINDING_CONTRACT_VERSION,
        "inputSources": binding.get("inputSources"),
        "runtimeInputs": binding.get("runtimeInputs"),
    }


def _resolved_execution_policy() -> dict:
    return {
        "contractVersion": 1,
        "providerId": "openai_primary",
        "adapterId": "huiyan_chat_completions",
        "model": "frozen-terra-model",
        "modelProfile": "terra",
        "reasoningEffort": "minimal",
        "maxOutputTokens": 5000,
        "readTimeoutSeconds": 90.0,
        "taskBudgetSeconds": 135.0,
        "responseMode": "json_object",
        "loadReferences": False,
        "structuredOutputRecoveryAttempts": 1,
    }


def _run(
    monkeypatch: pytest.MonkeyPatch,
    provider_output: dict,
    *,
    recovery_attempts: int = 1,
) -> tuple[dict, list[dict], list[dict]]:
    requests: list[dict] = []
    finalizer_calls: list[dict] = []

    class Response(_ResponseEvidence):
        def json(self) -> dict:
            return {"choices": [{"message": {"content": json.dumps(provider_output)}}]}

    class Client:
        def __init__(self, *args, **kwargs) -> None:
            return None

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            return None

        def post(self, _url: str, *, content: bytes, **_kwargs) -> Response:
            requests.append(json.loads(content))
            return Response()

    original = skill_finalization_runtime.execute_registered_finalizer

    def counted_execute_finalizer(**kwargs):
        finalizer_calls.append(kwargs)
        return original(**kwargs)

    monkeypatch.setattr(skill_finalization_runtime, "execute_registered_finalizer", counted_execute_finalizer)
    monkeypatch.setattr(
        durable_skill_runtime,
        "httpx",
        SimpleNamespace(Client=Client, HTTPStatusError=httpx.HTTPStatusError, HTTPError=httpx.HTTPError),
    )
    monkeypatch.setattr(
        durable_skill_runtime,
        "apply_provider_schema_transport",
        lambda _provider, *, payload, **_kwargs: dict(payload),
    )
    monkeypatch.setattr(
        durable_skill_runtime,
        "_billable_usage_wire_from_body",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setenv("LLM_PROVIDER_API_KEY", "test-key")
    monkeypatch.setenv("LLM_PROVIDER_BASE_URL", "https://provider.test")
    monkeypatch.setenv("LLM_PROVIDER_MODEL_TERRA", "frozen-terra-model")

    execution_policy = _resolved_execution_policy()
    execution_policy["structuredOutputRecoveryAttempts"] = recovery_attempts
    result = durable_skill_runtime.execute_production_durable_skill(
        {
            "skillId": "evidence_schema_design",
            "parameters": _parameters(),
            "frozenGoal": "Design the evidence schema for the confirmed research task.",
            "logicalProviderId": "openai_primary",
            "modelProfile": "terra",
            "resolvedExecutionPolicy": execution_policy,
            "bindingInputEvidence": _binding_evidence(),
            "providerEvidence": {
                "capabilityCallId": "call-t0b-evidence-schema",
                "attemptNumber": 1,
            },
        }
    )
    return result, requests, finalizer_calls


def test_real_registry_freezes_evidence_schema_finalization_contract() -> None:
    skill = durable_skill_runtime.resolve_registered_durable_skill("evidence_schema_design")
    assert skill is not None
    source = skill["sourceContract"]
    assert source["durable_model_profile"] == "terra"
    assert source["durable_llm_execution_policy"]["structured_output_recovery_attempts"] == 1
    assert source["durable_llm_execution_policy"]["task_budget_seconds"] == 135.0
    assert source["durable_llm_execution_policy"]["model_profile"] is None
    assert "frozenFinalization" in source
    assert source["frozenFinalization"]["entrypoint"] == {
        "module": "scripts.finalize",
        "callable": "finalize",
    }
    assert source["frozenFinalization"]["draftSchema"]["path"] == "model_output.schema.json"
    assert source["frozenFinalization"]["draftSchema"]["schema"]["title"] == "evidence-schema-design model semantic draft"
    assert source["frozenSchemas"]["output"]["schema"] == json.loads(
        (SKILL_ROOT / "output.schema.json").read_text(encoding="utf-8")
    )


def test_production_path_uses_semantic_draft_then_finalizer_then_formal_artifact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, requests, finalizer_calls = _run(monkeypatch, _semantic_draft())

    assert result["state"] == "succeeded"
    assert len(requests) == 1
    assert len(finalizer_calls) == 1
    assert finalizer_calls[0]["draft_payload"] == _semantic_draft()

    provider_contract = json.loads(requests[0]["messages"][1]["content"])

    canonical_input = _parameters()
    assert finalizer_calls[0]["input_payload"] == canonical_input
    assert canonical_input["task_id"] == "task-t0b-evidence-schema"
    assert canonical_input["retrieval_strategy_design"]["expanded_query_variants"]
    scientific_input = provider_contract["scientificInput"]
    assert "task_id" not in scientific_input
    assert scientific_input["intent_understanding"] == canonical_input["intent_understanding"]
    assert scientific_input["constraints_criteria"] == canonical_input["constraints_criteria"]
    assert set(scientific_input["retrieval_strategy_design"]) == {
        "search_matrix",
        "quality_report",
        "assembled_facets",
    }

    artifact = json.loads(base64.b64decode(result["artifactPayload"]["contentBase64"]))
    assert artifact["status"] == "ready"
    assert artifact["evidence_schema"]["schema_id"] == "task-t0b-evidence-schema:evidence"
    assert artifact["evidence_schema"]["schema_version"] == "0.1.0"
    assert artifact["evidence_schema"]["evidence_targets"][0]["target_id"] == "target_001"
    assert artifact["evidence_schema"]["uncertainty_rules"][0]["rule_id"] == "u01"
    assert "optional_extensions" not in artifact["evidence_schema"]
    assert artifact["evidence_schema"]["evidence_targets"][0]["description"] == (
        _semantic_draft()["ready"][0]["evidence_targets"][0]["description"]
    )


def test_provider_draft_defers_uniqueness_to_fail_closed_formal_validation() -> None:
    skill = durable_skill_runtime.resolve_registered_durable_skill("evidence_schema_design")
    assert skill is not None
    source = skill["sourceContract"]
    draft_schema = source["frozenFinalization"]["draftSchema"]["schema"]
    formal_schema = source["frozenSchemas"]["output"]["schema"]

    assert "uniqueItems" not in json.dumps(draft_schema, sort_keys=True)
    assert '"uniqueItems": true' in json.dumps(formal_schema, sort_keys=True)

    duplicate_draft = _semantic_draft()
    duplicate_draft["ready"][0]["domain_hints"] = ["photocatalysis", "photocatalysis"]
    assert Draft7Validator(draft_schema).is_valid(duplicate_draft)

    with pytest.raises(skill_finalization_runtime.SkillFinalizationError) as exc_info:
        skill_finalization_runtime.finalize_and_validate_registered_output(
            repo_root=REPO_ROOT,
            skill_path="skills/opentrajectory/evidence-schema-design-skill",
            frozen_contract=source["frozenFinalization"],
            input_payload=_parameters(),
            draft_payload=duplicate_draft,
            final_schema=formal_schema,
        )

    assert exc_info.value.code == "final_schema_validation_failed"
    assert exc_info.value.errors


def test_provider_cannot_supply_formal_machine_fields_through_semantic_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invalid = _semantic_draft()
    invalid["ready"][0]["schema_id"] = "model-owned-id"
    result, _requests, finalizer_calls = _run(
        monkeypatch,
        invalid,
        recovery_attempts=0,
    )

    assert result["state"] == "failed"
    assert result["failedStage"] == "skill_finalization_failed"
    assert result["firstMismatch"] == "semantic_draft_schema_invalid"
    assert finalizer_calls == []
    assert "artifactPayload" not in result


def _assembled_reading_input(evidence_artifact: dict) -> dict:
    rows = load_skill_binding_source_rows()
    reading_binding = next(row for row in rows if row["skillId"] == "deepxiv_progressive_reading")
    schema_binding = next(row for row in rows if row["skillId"] == "evidence_schema_design")
    topk_ref = next(
        field["source"] for field in reading_binding["inputSources"]["fields"]
        if field["targetField"] == "topk_selection"
    )
    topk_binding = next(row for row in rows if row["bindingId"] == topk_ref["bindingId"])
    topk_document = {
        "topk_selection": {"selected_for_p03": [{"candidate_id": "candidate-1", "title": "Paper"}]}
    }
    def frozen(schema: dict) -> dict:
        return {"schema": schema, "sha256": "0" * 64}
    snapshot = {
        "definitions": {"agents": [], "skills": [
            {
                "skillId": schema_binding["skillId"],
                "definitionVersion": schema_binding["skillDefinitionVersion"],
                "sourceContract": {"frozenSchemas": {"output": frozen(json.loads(
                    (SKILL_ROOT / "output.schema.json").read_text(encoding="utf-8")
                ))}},
            },
            {
                "skillId": topk_binding["skillId"],
                "definitionVersion": topk_binding["skillDefinitionVersion"],
                "sourceContract": {"frozenSchemas": {"output": frozen({
                    "type": "object", "required": ["topk_selection"],
                    "properties": {"topk_selection": {"type": "object"}},
                })}},
            },
            {
                "skillId": reading_binding["skillId"],
                "definitionVersion": reading_binding["skillDefinitionVersion"],
                "sourceContract": {"frozenSchemas": {"input": frozen(json.loads(
                    (REPO_ROOT / "skills/opentrajectory/deepxiv-progressive-reading-skill/input.schema.json").read_text(encoding="utf-8")
                ))}},
            },
        ]},
        "bindings": {"skills": [
            {**schema_binding, "enabled": True},
            {**topk_binding, "enabled": True},
            {**reading_binding, "enabled": True},
        ]},
    }

    def source_artifact(binding: dict, document: dict) -> dict:
        return {
            "sourceCapabilityType": "skill",
            "sourceCapabilityId": binding["skillId"],
            "sourceCapabilityDefinitionVersion": binding["skillDefinitionVersion"],
            "sourceBindingRefs": [{
                "bindingId": binding["bindingId"], "bindingVersion": binding["bindingVersion"],
            }],
            "mimeType": "application/json",
            "content": json.dumps(document).encode("utf-8"),
        }

    request = SkillInputRequest(
        task_id="task-t0b-evidence-schema", strategy="direct_skill",
        skill_id=reading_binding["skillId"],
        definition_version=reading_binding["skillDefinitionVersion"],
    )
    return resolve_registered_skill_input(
        snapshot=snapshot,
        request=request,
        input_context={"artifacts": [
            source_artifact(schema_binding, evidence_artifact),
            source_artifact(topk_binding, topk_document),
        ]},
        skill_binding={**reading_binding, "enabled": True},
    )


def _finalized_artifact() -> dict:
    skill = durable_skill_runtime.resolve_registered_durable_skill("evidence_schema_design")
    assert skill is not None
    source = skill["sourceContract"]
    result = skill_finalization_runtime.finalize_and_validate_registered_output(
        repo_root=REPO_ROOT,
        skill_path="skills/opentrajectory/evidence-schema-design-skill",
        frozen_contract=source["frozenFinalization"],
        input_payload=_parameters(),
        draft_payload=_semantic_draft(),
        final_schema=source["frozenSchemas"]["output"]["schema"],
    )
    assert result.status == "succeeded"
    return result.output


def test_current_handoff_rejects_retired_and_dynamic_fields_without_harming_current_fields() -> None:
    artifact = _finalized_artifact()
    formal = json.loads((SKILL_ROOT / "output.schema.json").read_text(encoding="utf-8"))
    reading_schema = json.loads((
        REPO_ROOT / "skills/opentrajectory/deepxiv-progressive-reading-skill/input.schema.json"
    ).read_text(encoding="utf-8"))
    current_fields = formal["definitions"]["evidence_schema"]
    consumer_fields = reading_schema["properties"]["evidence_schema"]
    assert set(current_fields["properties"]) == set(consumer_fields["properties"])
    assert set(current_fields["required"]) == set(consumer_fields["required"])
    assert not list(Draft7Validator(formal).iter_errors(artifact))

    assembled = _assembled_reading_input(artifact)
    assert assembled["evidence_schema"] == artifact["evidence_schema"]
    assert assembled["evidence_schema"]["task_context"]["design_assumptions"] == (
        _semantic_draft()["ready"][0]["design_assumptions"]
    )
    adapter_path = REPO_ROOT / "skills/opentrajectory/deepxiv-progressive-reading-skill/adapter/progressive_reading_adapter.py"
    spec = importlib.util.spec_from_file_location("current_progressive_reader", adapter_path)
    assert spec and spec.loader
    adapter = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = adapter
    spec.loader.exec_module(adapter)
    assert adapter._evidence_schema(assembled) == artifact["evidence_schema"]

    dynamic_field = "_".join(("optional", "extensions"))
    for field_name in ("optional_extensions", dynamic_field, "extensions"):
        bad = deepcopy(artifact)
        bad["evidence_schema"][field_name] = []
        assert list(Draft7Validator(formal).iter_errors(bad))
        with pytest.raises(InputContractGateFailure, match="input_composition_source_artifact_invalid"):
            _assembled_reading_input(bad)


def test_legacy_artifact_is_readable_but_cannot_reenter_current_handoff() -> None:
    old = _finalized_artifact()
    old["evidence_schema"]["optional_extensions"] = []
    text, mode = load_artifact_content(
        "task-t0b-evidence-schema",
        {"storageType": "inline", "inlineContent": json.dumps(old)},
    )
    assert mode == "inline"
    assert json.loads(text) == old
    with pytest.raises(InputContractGateFailure, match="input_composition_source_artifact_invalid"):
        _assembled_reading_input(json.loads(text))
