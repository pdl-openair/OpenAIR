from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path

from jsonschema import Draft202012Validator
import yaml

from app.execution.research_dispatch_provider import DispatchSubmissionCompleted
from app.models.research_orchestration import ResearchTaskV12
from app.modules.research_orchestration_repository import ResearchOrchestrationRepository
from app.modules.research_orchestration_resolver import build_registry_snapshot
from app.modules.scientific_state_fact_projection import (
    materialize_scientific_state_projection,
    project_validated_structured_result,
    validate_scientific_state_projection_declaration,
)
from tests.test_line_a1_progress_lifecycle import NOW, _fixture, _running_progress_task_and_fence


ROOT = Path(__file__).resolve().parents[3]
REGISTRY = ROOT / "skills" / "registry.yaml"


def _declared_projection() -> dict:
    return {
        "contract_version": 2,
        "materializer_ref": "research:declared-scientific-facts:v1",
        "facts": [
            {
                "fact_key": "evidence_supported_conclusions",
                "source_field": "evidence_supported_conclusions",
            },
            {"fact_key": "evidence_gaps", "source_field": "evidence_gaps"},
        ],
    }


def _repository() -> ResearchOrchestrationRepository:
    repository = ResearchOrchestrationRepository.__new__(
        ResearchOrchestrationRepository
    )
    repository._reconcile_task_lifecycle_with_frozen_snapshot = (
        lambda task, occurred_at=None: task
    )
    return repository


def _pvd_recommendation_projection() -> dict:
    snapshot = json.loads(build_registry_snapshot().canonical_payload)
    skill_definition = next(
        row
        for row in snapshot["definitions"]["skills"]
        if row["skillId"]
        == "pvd_round_experiment_plan_parameter_recommendation"
    )
    return skill_definition["sourceContract"]["scientific_state_projection"]


def test_declared_research_facts_materialize_without_reinterpretation() -> None:
    output_schema = {
        "type": "object",
        "required": ["evidence_supported_conclusions", "evidence_gaps"],
        "properties": {
            "evidence_supported_conclusions": {"type": "array"},
            "evidence_gaps": {"type": "array"},
        },
    }
    declaration = validate_scientific_state_projection_declaration(
        _declared_projection(), output_schema=output_schema
    )
    conclusions = [{"statement": "Accepted conclusion", "confidence": 0.9}]
    gaps = [{"gap": "Unresolved boundary", "impact": "changes next task"}]
    projected = project_validated_structured_result(
        json.dumps(
            {
                "evidence_supported_conclusions": conclusions,
                "evidence_gaps": gaps,
            }
        ),
        declaration,
    )
    payload = {
        "taskId": "task-1",
        "status": "completed",
        "finishedAt": "2026-08-30T00:00:00+00:00",
        "scientificStates": [
            {
                "scientificStateVersion": 3,
                "researchDesignVersion": 1,
                "scientificFacts": {"existing_fact": {"value": "keep"}},
                "executionState": {"validatedArtifactRefs": ["artifact-old"]},
                "sourceRefs": ["artifact-old"],
                "createdAt": "2026-08-30T00:00:00+00:00",
            }
        ],
        "finalResultAuthority": {"mode": "artifact_set"},
    }

    result = materialize_scientific_state_projection(
        task_payload=payload,
        declaration=declaration,
        projected_facts=projected,
        source_artifact_id="artifact-new",
        research_design_version=1,
        occurred_at=datetime(2026, 8, 31, tzinfo=timezone.utc),
    )

    state = result["scientificStates"][-1]
    assert state["scientificStateVersion"] == 4
    assert state["scientificFacts"] == {
        "existing_fact": {"value": "keep"},
        "evidence_supported_conclusions": conclusions,
        "evidence_gaps": gaps,
    }
    assert state["executionState"] == {"validatedArtifactRefs": ["artifact-old"]}
    assert state["sourceRefs"] == ["artifact-old", "artifact-new"]
    assert result["finalResultAuthority"] is None
    assert result["status"] == "running"
    assert result["finishedAt"] is None

    replayed = materialize_scientific_state_projection(
        task_payload=result,
        declaration=declaration,
        projected_facts=projected,
        source_artifact_id="artifact-confirming-replay",
        research_design_version=1,
        occurred_at=datetime(2026, 8, 31, 0, 1, tzinfo=timezone.utc),
    )
    assert replayed == result


def test_passed_execution_result_materializes_declared_research_facts() -> None:
    task, fence = _running_progress_task_and_fence()
    payload = task.model_dump(mode="json", by_alias=True)
    decision = payload["executionDecisions"][0]
    completion = _fixture()["executionDecisions"][0]
    decision.update(
        {
            "purpose": "completion",
            "completionPolicy": completion["completionPolicy"],
            "progressPolicy": None,
            "candidateEvaluations": completion["candidateEvaluations"],
            "assignments": deepcopy(completion["assignments"]),
        }
    )
    expected_types = list(decision["assignments"][0]["expectedArtifactTypes"])
    payload["workUnits"][0].update(
        {"expectedArtifactTypes": expected_types, "status": "running", "finishedAt": None}
    )
    payload["capabilityCalls"][0].update(
        {"status": "running", "outputArtifactRefs": [], "finishedAt": None}
    )
    payload["scientificStates"] = []
    task = ResearchTaskV12.model_validate(payload)
    declaration = _declared_projection()
    conclusions = [{"statement": "Result A changes the accepted knowledge."}]
    gaps = [{"gap": "Resolve mechanism uncertainty next."}]

    updated, _catalog_artifact, _dispatches, _message = (
        _repository()._task_with_completed_result(
            task,
            fence=fence,
            result=DispatchSubmissionCompleted(
                title="Accepted scientific synthesis",
                mime_type="application/json",
                content=json.dumps(
                    {
                        "evidence_supported_conclusions": conclusions,
                        "evidence_gaps": gaps,
                    }
                ),
                provider_id="openai_primary",
                adapter="huiyan_chat_completions",
            ),
            artifact_contract="research_synthesis_v1",
            artifact_schema_digest="a" * 64,
            content_sha256="b" * 64,
            storage_type="local",
            storage_scope=task.task_id,
            storage_ref="durable/research-dispatch/dispatch-progress-a1/result.json",
            scientific_state_fact_projections={
                "evidence_supported_conclusions": conclusions,
                "evidence_gaps": gaps,
            },
            scientific_state_projection_declaration=declaration,
            now=NOW,
        )
    )

    produced_result = updated.results[0]
    produced_artifact = next(
        row for row in updated.artifacts if row.source_result_id == produced_result.result_id
    )
    assert produced_result.check_status.value == "passed"
    assert produced_result.scientific_state_versions == [1]
    accepted_state = updated.scientific_states[0]
    assert accepted_state.source_refs == [produced_artifact.artifact_id]
    assert accepted_state.scientific_facts == {
        "evidence_supported_conclusions": conclusions,
        "evidence_gaps": gaps,
    }


def test_registry_explicitly_enables_two_flow_validation_skills() -> None:
    registry = yaml.safe_load(REGISTRY.read_text(encoding="utf-8")) or {}
    assert isinstance(registry, dict)
    rows = registry.get("skills") or []
    assert isinstance(rows, list)
    by_id = {row["skill_id"]: row for row in rows if isinstance(row, dict)}

    assert by_id["mechanism_chain_synthesis"]["scientific_state_projection"] == {
        "contract_version": 2,
        "materializer_ref": "research:declared-scientific-facts:v1",
        "facts": [
            {"fact_key": "mechanism_edges", "source_field": "mechanism_edges"},
            {"fact_key": "mechanism_chains", "source_field": "mechanism_chains"},
            {"fact_key": "mechanism_conflicts", "source_field": "conflicts"},
            {"fact_key": "mechanism_gaps", "source_field": "gaps"},
        ],
    }
    assert by_id["research_lead_hypothesis_generation"]["scientific_state_projection"] == {
        "contract_version": 2,
        "materializer_ref": "research:declared-scientific-facts:v1",
        "facts": [
            {"fact_key": "direct_answer", "source_field": "direct_answer"},
            {
                "fact_key": "evidence_supported_conclusions",
                "source_field": "evidence_supported_conclusions",
            },
            {"fact_key": "evidence_conflicts", "source_field": "conflicts"},
            {"fact_key": "uncertainties", "source_field": "uncertainties"},
            {"fact_key": "evidence_gaps", "source_field": "evidence_gaps"},
            {"fact_key": "research_leads", "source_field": "research_leads"},
            {"fact_key": "hypotheses", "source_field": "hypotheses"},
        ],
    }


def _accepted_result_task() -> tuple[ResearchTaskV12, object]:
    task, fence = _running_progress_task_and_fence()
    payload = task.model_dump(mode="json", by_alias=True)
    decision = payload["executionDecisions"][0]
    completion = _fixture()["executionDecisions"][0]
    decision.update(
        {
            "purpose": "completion",
            "completionPolicy": completion["completionPolicy"],
            "progressPolicy": None,
            "candidateEvaluations": completion["candidateEvaluations"],
            "assignments": deepcopy(completion["assignments"]),
        }
    )
    expected_types = list(decision["assignments"][0]["expectedArtifactTypes"])
    payload["workUnits"][0].update(
        {"expectedArtifactTypes": expected_types, "status": "running", "finishedAt": None}
    )
    payload["capabilityCalls"][0].update(
        {"status": "running", "outputArtifactRefs": [], "finishedAt": None}
    )
    payload["scientificStates"] = []
    return ResearchTaskV12.model_validate(payload), fence


def test_accepted_cs_readiness_result_materializes_only_decision_relevant_state() -> None:
    task, fence = _accepted_result_task()
    registry = yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))
    declaration = next(
        row["scientific_state_projection"]
        for row in registry["skills"]
        if row["skill_id"] == "execution_readiness_assessment"
    )
    result_content = {
        "status": "not_ready",
        "executable_scope": ["evaluation-only"],
        "code": {"revision": "abc123"},
        "resources": {"checkpoint": "missing"},
        "environment": {"cuda": "unknown"},
        "entry_verification": {"command": "python eval.py"},
        "blocking_gaps": [{"kind": "checkpoint_missing"}],
        "unknowns": ["CUDA compatibility is unverified"],
    }
    projected = project_validated_structured_result(
        json.dumps(result_content),
        declaration,
    )

    updated, _catalog_artifact, _dispatches, _message = (
        _repository()._task_with_completed_result(
            task,
            fence=fence,
            result=DispatchSubmissionCompleted(
                title="CS execution readiness",
                mime_type="application/json",
                content=json.dumps(result_content),
                provider_id="openai_primary",
                adapter="huiyan_chat_completions",
            ),
            artifact_contract="cs_execution_readiness_v1",
            artifact_schema_digest="a" * 64,
            content_sha256="b" * 64,
            storage_type="local",
            storage_scope=task.task_id,
            storage_ref="durable/research-dispatch/cs-readiness/result.json",
            scientific_state_fact_projections=projected,
            scientific_state_projection_declaration=declaration,
            now=NOW,
        )
    )

    produced_result = updated.results[0]
    assert produced_result.check_status.value == "passed"
    assert produced_result.scientific_state_versions == [1]
    assert updated.scientific_states[-1].scientific_facts == {
        "execution_readiness_status": "not_ready",
        "execution_readiness_executable_scope": ["evaluation-only"],
        "execution_readiness_blocking_gaps": [{"kind": "checkpoint_missing"}],
        "execution_readiness_unknowns": ["CUDA compatibility is unverified"],
    }


def test_production_registry_pvd_projection_preserves_ranked_and_blocked_experiments() -> None:
    task, fence = _accepted_result_task()
    payload = task.model_dump(mode="json", by_alias=True)
    payload["scientificStates"] = [
        {
            "scientificStateVersion": 1,
            "researchDesignVersion": 1,
            "scientificFacts": {
                "experimental_system_contexts": [
                    {
                        "experimental_system_context_ref": "pvd-context:1",
                        "experimental_system_context": {"contract": "pvd-context/v1"},
                    }
                ],
                "current_experimental_system_context_ref": "pvd-context:1",
                "experiment_history": [],
                "execution_readiness_status": "ready",
                "execution_readiness_executable_scope": ["experiment-substrate-bias"],
                "execution_readiness_blocking_gaps": [],
            },
            "executionState": {},
            "sourceRefs": ["artifact-context"],
            "createdAt": "2026-09-25T00:00:00Z",
        }
    ]
    task = ResearchTaskV12.model_validate(payload)
    declaration = _pvd_recommendation_projection()
    result_content = {
        "decision_status": "recommendation",
        "first_run_ranking_status": "ranked",
        "experiment_count": 1,
        "experiments": [
            {
                "experiment_id": "experiment-substrate-bias",
                "priority": 1,
                "parameters": [
                    {
                        "parameter_key": "substrate_bias",
                        "display_name": "Substrate bias",
                        "recommended_value": -50,
                        "unit": "V",
                    }
                ],
                "validation_goal": "Test lower stress without losing texture.",
                "failure_signal": "Texture falls below the accepted boundary.",
            }
        ],
        "blocked_experiments": [
            {
                "experiment_id": "experiment-out-of-range",
                "reason_codes": ["hard_boundary_violation:substrate_bias:minimum"],
            }
        ],
        "next_step": "Run the experiment and measure stress and texture.",
        "important_unknowns": ["Stress response remains unmeasured."],
    }
    projected = project_validated_structured_result(
        json.dumps(result_content),
        declaration,
    )

    updated, _catalog_artifact, _dispatches, _message = (
        _repository()._task_with_completed_result(
            task,
            fence=fence,
            result=DispatchSubmissionCompleted(
                title="PVD recommendation",
                mime_type="application/json",
                content=json.dumps(result_content),
                provider_id="openai_primary",
                adapter="huiyan_chat_completions",
            ),
            artifact_contract="pvd_recommendation_v1",
            artifact_schema_digest="c" * 64,
            content_sha256="d" * 64,
            storage_type="local",
            storage_scope=task.task_id,
            storage_ref="durable/research-dispatch/pvd-recommendation/result.json",
            scientific_state_fact_projections=projected,
            scientific_state_projection_declaration=declaration,
            now=NOW,
        )
    )

    produced_result = updated.results[0]
    assert produced_result.check_status.value == "passed"
    assert produced_result.scientific_state_versions == [2]
    recommendation = updated.scientific_states[-1].scientific_facts[
        "experiment_history"
    ][0]["system_recommendation"]
    assert recommendation["parameters"] == [
        {
            "parameter_key": "substrate_bias",
            "display_name": "Substrate bias",
            "recommended_value": -50,
            "unit": "V",
        }
    ]
    assert recommendation["validation_goal"] == "Test lower stress without losing texture."
    assert recommendation["failure_signal"] == "Texture falls below the accepted boundary."
    assert recommendation["next_step"] == "Run the experiment and measure stress and texture."
    decision = updated.scientific_states[-1].scientific_facts[
        "current_recommendation_decision"
    ]
    assert decision["decision_status"] == "recommendation"
    assert decision["first_run_ranking_status"] == "ranked"
    assert decision["blocked_experiments"] == [
        {
            "experiment_id": "experiment-out-of-range",
            "reason_codes": ["hard_boundary_violation:substrate_bias:minimum"],
        }
    ]
    facts = updated.scientific_states[-1].scientific_facts
    assert facts["execution_readiness_status"] == "ready"
    assert facts["execution_readiness_executable_scope"] == [
        "experiment-substrate-bias"
    ]
    assert facts["execution_readiness_blocking_gaps"] == []
    assert decision["important_unknowns"] == ["Stress response remains unmeasured."]


def test_accepted_pvd_no_recommendation_reaches_next_decision_without_experiment() -> None:
    task, fence = _accepted_result_task()
    payload = task.model_dump(mode="json", by_alias=True)
    payload["scientificStates"] = [
        {
            "scientificStateVersion": 1,
            "researchDesignVersion": 1,
            "scientificFacts": {
                "experimental_system_contexts": [
                    {
                        "experimental_system_context_ref": "pvd-context:1",
                        "experimental_system_context": {"contract": "pvd-context/v1"},
                    }
                ],
                "current_experimental_system_context_ref": "pvd-context:1",
                "experiment_history": [],
            },
            "executionState": {},
            "sourceRefs": ["artifact-context"],
            "createdAt": "2026-09-25T00:00:00Z",
        }
    ]
    task = ResearchTaskV12.model_validate(payload)
    declaration = _pvd_recommendation_projection()
    result_content = {
        "decision_status": "no_recommendation",
        "first_run_ranking_status": "not_applicable",
        "experiment_count": 0,
        "experiments": [],
        "blocked_experiments": [],
        "limited_resource_first_to_run_experiment_ids": [],
        "direction_change_signals": [],
        "next_step": "Measure chamber pressure stability.",
        "shared_conditions": [],
        "shared_risks": [],
        "important_unknowns": [
            "Chamber pressure stability has not been measured."
        ],
        "portfolio_note": "Evidence is insufficient for a reliable recommendation.",
        "global_optimum_claimed": False,
        "audit": {},
    }
    output_schema = json.loads(
        (
            ROOT
            / "skills"
            / "pvd"
            / "pvd-round-experiment-plan-parameter-recommendation"
            / "output.schema.json"
        ).read_text(encoding="utf-8")
    )
    Draft202012Validator(output_schema).validate(result_content)
    projected = project_validated_structured_result(
        json.dumps(result_content),
        declaration,
    )

    updated, _catalog_artifact, _dispatches, _message = (
        _repository()._task_with_completed_result(
            task,
            fence=fence,
            result=DispatchSubmissionCompleted(
                title="PVD no recommendation",
                mime_type="application/json",
                content=json.dumps(result_content),
                provider_id="openai_primary",
                adapter="huiyan_chat_completions",
            ),
            artifact_contract="pvd_recommendation_v1",
            artifact_schema_digest="e" * 64,
            content_sha256="f" * 64,
            storage_type="local",
            storage_scope=task.task_id,
            storage_ref="durable/research-dispatch/pvd-no-recommendation/result.json",
            scientific_state_fact_projections=projected,
            scientific_state_projection_declaration=declaration,
            now=NOW,
        )
    )

    produced_result = updated.results[0]
    assert produced_result.check_status.value == "passed"
    assert produced_result.scientific_state_versions == [2]
    facts = updated.scientific_states[-1].scientific_facts
    assert facts["experiment_history"] == []
    assert facts["current_recommendation_decision"] == {
        "decision_status": "no_recommendation",
        "first_run_ranking_status": "not_applicable",
        "blocked_experiments": [],
        "important_unknowns": [
            "Chamber pressure stability has not been measured."
        ],
        "next_step": "Measure chamber pressure stability.",
        "provenance": {
            "origin": "system",
            "source_ref": updated.scientific_states[-1].source_refs[-1],
        },
    }


def test_accepted_ot_result_preserves_declared_scientific_roles() -> None:
    task, fence = _accepted_result_task()
    registry = yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))
    declaration = next(
        row["scientific_state_projection"]
        for row in registry["skills"]
        if row["skill_id"] == "research_lead_hypothesis_generation"
    )
    result_content = {
        "direct_answer": "The strongest supported interpretation is mechanism A.",
        "evidence_supported_conclusions": [
            {
                "statement": "Mechanism A is supported under the studied boundary.",
                "support_refs": ["record:1"],
            }
        ],
        "conflicts": [
            {
                "statement": "Record 2 reports the opposite trend.",
                "source_refs": ["record:2"],
            }
        ],
        "uncertainties": [
            {
                "statement": "The high-temperature boundary remains unknown.",
                "importance": "It may reverse the current interpretation.",
            }
        ],
        "evidence_gaps": [
            {
                "gap": "No direct measurement above 500 K.",
                "importance": "This limits extrapolation.",
            }
        ],
        "research_leads": [
            {
                "statement": "Measure the response above 500 K.",
                "role": "next_validation_direction",
            }
        ],
        "hypotheses": [
            {
                "statement": "Mechanism B may dominate above 500 K.",
                "status": "unvalidated",
            }
        ],
    }
    projected = project_validated_structured_result(
        json.dumps(result_content),
        declaration,
    )

    updated, _catalog_artifact, _dispatches, _message = (
        _repository()._task_with_completed_result(
            task,
            fence=fence,
            result=DispatchSubmissionCompleted(
                title="OT final synthesis",
                mime_type="application/json",
                content=json.dumps(result_content),
                provider_id="openai_primary",
                adapter="huiyan_chat_completions",
            ),
            artifact_contract="ot_final_synthesis_v1",
            artifact_schema_digest="e" * 64,
            content_sha256="f" * 64,
            storage_type="local",
            storage_scope=task.task_id,
            storage_ref="durable/research-dispatch/ot-final-synthesis/result.json",
            scientific_state_fact_projections=projected,
            scientific_state_projection_declaration=declaration,
            now=NOW,
        )
    )

    produced_result = updated.results[0]
    assert produced_result.check_status.value == "passed"
    assert produced_result.scientific_state_versions == [1]
    state = updated.scientific_states[-1].scientific_facts
    assert state == projected
    assert state["research_leads"][0]["role"] == "next_validation_direction"
    assert state["hypotheses"][0]["status"] == "unvalidated"
