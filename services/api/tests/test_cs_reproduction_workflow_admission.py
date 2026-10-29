from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.modules.cs_reproduction_workflow_admission import (
    CS_REPRODUCTION_SKILL_PACK,
    CS_REPRODUCTION_WORKFLOW_ID,
    cs_reproduction_workflow_planning_admission,
)
from app.modules.production_definition_source import ProductionDefinitionSource
from app.modules.scientific_completion_integrity import expected_final_result_fields
from app.modules.registry_snapshot_repository import InMemoryRegistrySnapshotRepository
from app.modules.research_orchestration_resolver import build_registry_snapshot
from app.modules.scientific_state_fact_projection import (
    materialize_scientific_state_projection,
    project_validated_structured_result,
)
from app.modules.workflow_planning import workflow_planning_bundle
from app.execution.research_input_contract_gate import (
    SkillInputRequest,
    resolve_registered_skill_input,
)


def test_cs_reproduction_admission_uses_explicit_workflow_identity() -> None:
    selection = cs_reproduction_workflow_planning_admission()

    assert selection.workflow_id == CS_REPRODUCTION_WORKFLOW_ID
    assert CS_REPRODUCTION_SKILL_PACK == "cs_reproduction"


def test_cs_reproduction_public_submit_contract_accepts_explicit_workflow() -> None:
    from app.routes.research_start import ResearchStartSubmitIn

    request = ResearchStartSubmitIn(
        input="@计算机领域论文复现 请复现指定论文实验。",
        clientRequestId="cs-w1-public-contract",
        requestedWorkflow=CS_REPRODUCTION_WORKFLOW_ID,
    )

    assert request.requestedWorkflow == CS_REPRODUCTION_WORKFLOW_ID


def test_cs_reproduction_worker_preserves_explicit_workflow_choice() -> None:
    from app.execution.research_start_worker_runtime import _workflow_planning_admission

    selection = _workflow_planning_admission(
        {
            "intent": {
                "input": "@计算机领域论文复现 请复现指定论文实验。",
                "sourcePage": "home",
                "selectedHint": None,
                "presetQuery": None,
                "requestedWorkflow": CS_REPRODUCTION_WORKFLOW_ID,
            }
        }
    )

    assert selection is not None
    assert selection.workflow_id == CS_REPRODUCTION_WORKFLOW_ID


def test_cs_reproduction_workflow_uses_latest_complete_responsibility_contract() -> None:
    source = ProductionDefinitionSource(archive=InMemoryRegistrySnapshotRepository())
    selection = cs_reproduction_workflow_planning_admission()

    bundle = workflow_planning_bundle(
        selection=selection,
        raw_goal="复现指定论文实验",
        definition_source=source,
    )

    assert bundle is not None
    assert bundle.admission["workflowId"] == CS_REPRODUCTION_WORKFLOW_ID
    assert bundle.admission["workflowVersion"] == 7
    assert bundle.admission["skillPack"] == CS_REPRODUCTION_SKILL_PACK
    assert bundle.planning_context["schemaVersion"] == "workflow-planning-context.v2"
    assert bundle.planning_context["planningPolicy"] == {
        "responsibilityPlanning": "project_adaptive",
        "existingResultReuse": "validated_existing_results",
        "conditionalResponsibilities": "planner_selected",
    }
    assert [row["definitionId"] for row in bundle.planning_context["coreResponsibilities"]] == [
        "cs_reproduction:reproduction_baseline_criteria",
        "cs_reproduction:reproduction_experiment_design",
        "cs_reproduction:execution_readiness_validation",
        "cs_reproduction:real_execution_result_acquisition",
        "cs_reproduction:result_comparability_judgment",
        "cs_reproduction:reproduction_conclusion_recommendation",
    ]
    assert [row["definitionId"] for row in bundle.planning_context["conditionalResponsibilities"]] == [
        "cs_reproduction:deviation_diagnosis_validation"
    ]
    assert [
        row["definitionVersion"]
        for row in (
            bundle.planning_context["coreResponsibilities"]
            + bundle.planning_context["conditionalResponsibilities"]
        )
    ] == [3, 3, 3, 3, 3, 4, 4]
    assert bundle.planning_context["dependencies"][-2:] == [
        {
            "upstreamNodeKey": "result_comparability_judgment",
            "downstreamNodeKey": "deviation_diagnosis_validation",
            "upstreamDefinitionId": "cs_reproduction:result_comparability_judgment",
            "downstreamDefinitionId": "cs_reproduction:deviation_diagnosis_validation",
            "dependencyType": "scientific_dependency",
        },
        {
            "upstreamNodeKey": "deviation_diagnosis_validation",
            "downstreamNodeKey": "reproduction_conclusion_recommendation",
            "upstreamDefinitionId": "cs_reproduction:deviation_diagnosis_validation",
            "downstreamDefinitionId": "cs_reproduction:reproduction_conclusion_recommendation",
            "dependencyType": "optional_enhancement",
        },
    ]


def test_cs_reproduction_snapshot_has_current_v7_external_action_contracts() -> None:
    snapshot = json.loads(build_registry_snapshot().canonical_payload)
    substages = {
        (row["definitionId"], row["definitionVersion"]): row
        for row in snapshot["definitions"]["substages"]
        if row.get("pack") == CS_REPRODUCTION_SKILL_PACK
    }

    expected = {
        ("cs_reproduction:reproduction_baseline_criteria", 3): ["external_read"],
        ("cs_reproduction:reproduction_experiment_design", 3): [],
        ("cs_reproduction:execution_readiness_validation", 3): ["external_read"],
        ("cs_reproduction:real_execution_result_acquisition", 3): ["external_compute"],
        ("cs_reproduction:result_comparability_judgment", 3): [],
        ("cs_reproduction:reproduction_conclusion_recommendation", 4): [],
        ("cs_reproduction:deviation_diagnosis_validation", 4): [],
    }
    for identity, required_actions in expected.items():
        assert substages[identity]["externalActionContract"] == {
            "status": "known",
            "requiredActionKinds": required_actions,
        }


def test_cs_v5_baseline_resolves_source_then_preserves_repository_revision_lineage() -> None:
    snapshot = json.loads(build_registry_snapshot().canonical_payload)
    bindings = snapshot["bindings"]["skills"]

    input_binding = next(
        row
        for row in bindings
        if row["bindingId"]
        == "bind:cs_reproduction:reproduction_baseline_criteria:skill:cs_reproduction_input_structuring"
        and row["bindingVersion"] == 3
    )
    assert input_binding["enabled"] is True
    assert input_binding["definitionVersion"] == 3
    assert input_binding["runtimeInputs"]["fields"] == [
        {
            "targetField": "research_goal",
            "source": {"kind": "invocationContext", "field": "canonical_user_query"},
        }
    ]

    source_binding = next(
        row
        for row in bindings
        if row["bindingId"]
        == "bind:cs_reproduction:reproduction_baseline_criteria:skill:cs_paper_source_resolution"
        and row["bindingVersion"] == 3
    )
    assert source_binding["enabled"] is True
    assert source_binding["definitionVersion"] == 3
    assert source_binding["inputSources"]["fields"] == [
        {
            "targetField": "repository",
            "sourcePresence": "conditional",
            "source": {
                "kind": "artifact",
                "bindingId": (
                    "bind:cs_reproduction:reproduction_baseline_criteria:"
                    "skill:cs_reproduction_input_structuring"
                ),
                "bindingVersion": 3,
                "field": "repository",
            },
        },
        {
            "targetField": "revision",
            "sourcePresence": "conditional",
            "source": {
                "kind": "artifact",
                "bindingId": (
                    "bind:cs_reproduction:reproduction_baseline_criteria:"
                    "skill:cs_reproduction_input_structuring"
                ),
                "bindingVersion": 3,
                "field": "revision",
            },
        },
    ]

    code_binding = next(
        row
        for row in bindings
        if row["bindingId"]
        == "bind:cs_reproduction:reproduction_baseline_criteria:skill:repository_revision_acquisition"
        and row["bindingVersion"] == 3
    )
    assert code_binding["inputSources"]["fields"] == [
        {
            "targetField": "repository",
            "sourcePresence": "required",
            "source": {
                "kind": "artifact",
                "bindingId": (
                    "bind:cs_reproduction:reproduction_baseline_criteria:"
                    "skill:cs_paper_source_resolution"
                ),
                "bindingVersion": 3,
                "field": "repository",
            },
        },
        {
            "targetField": "revision",
            "sourcePresence": "required",
            "source": {
                "kind": "artifact",
                "bindingId": (
                    "bind:cs_reproduction:reproduction_baseline_criteria:"
                    "skill:cs_paper_source_resolution"
                ),
                "bindingVersion": 3,
                "field": "requested_revision",
            },
        },
        {
            "targetField": "source_evidence",
            "sourcePresence": "required",
            "source": {
                "kind": "artifact",
                "bindingId": (
                    "bind:cs_reproduction:reproduction_baseline_criteria:"
                    "skill:cs_paper_source_resolution"
                ),
                "bindingVersion": 3,
                "field": "source_evidence",
            },
        },
    ]


def test_cs_v5_source_resolution_accepts_a_natural_query_without_predeclared_code_identity() -> None:
    snapshot = json.loads(build_registry_snapshot().canonical_payload)
    bindings = snapshot["bindings"]["skills"]
    input_binding = next(
        row
        for row in bindings
        if row["bindingId"]
        == "bind:cs_reproduction:reproduction_baseline_criteria:skill:cs_reproduction_input_structuring"
        and row["bindingVersion"] == 3
    )
    source_binding = next(
        row
        for row in bindings
        if row["bindingId"]
        == "bind:cs_reproduction:reproduction_baseline_criteria:skill:cs_paper_source_resolution"
        and row["bindingVersion"] == 3
    )
    structured_input = {
        "repository": None,
        "revision": None,
        "unresolved_inputs": ["repository", "revision"],
    }

    resolved = resolve_registered_skill_input(
        snapshot=snapshot,
        request=SkillInputRequest(
            task_id="task-cs-natural-source",
            strategy="specialist_agent",
            skill_id="cs_paper_source_resolution",
            definition_version=1,
        ),
        input_context={
            "artifacts": [{
                "sourceCapabilityType": "skill",
                "sourceCapabilityId": "cs_reproduction_input_structuring",
                "sourceCapabilityDefinitionVersion": 1,
                "sourceBindingRefs": [{
                    "bindingId": input_binding["bindingId"],
                    "bindingVersion": input_binding["bindingVersion"],
                }],
                "mimeType": "application/json",
                "content": json.dumps(structured_input).encode("utf-8"),
            }],
            "invocationContext": {
                "canonical_user_query": (
                    "我想复现 SimCSE 官方的 sup-simcse-bert-base-uncased 在 STS 上的结果，帮我规划一下怎么做。"
                )
            },
        },
        skill_binding=source_binding,
    )

    assert resolved == {
        "repository": None,
        "revision": None,
        "research_goal": "我想复现 SimCSE 官方的 sup-simcse-bert-base-uncased 在 STS 上的结果，帮我规划一下怎么做。",
    }


def test_cs_reproduction_home_entry_does_not_inject_demo_repository() -> None:
    root = Path(__file__).resolve().parents[3]
    home = (root / "apps/web/src/pages/HomePage.tsx").read_text(encoding="utf-8")
    assert 'const CS_REPRODUCTION_MENTION = "@计算机论文实验复现";' in home
    assert "我想复现一下 SimCSE 里 supervised BERT-base 的 STS 结果" in home
    assert "princeton-nlp/SimCSE" not in home


def test_cs_reproduction_home_entry_preserves_existing_workflow_entries() -> None:
    root = Path(__file__).resolve().parents[3]
    home = (root / "apps/web/src/pages/HomePage.tsx").read_text(encoding="utf-8")
    client = (root / "apps/web/src/api/client.ts").read_text(encoding="utf-8")
    assert "薄膜沉积（PVD）工艺优化" in home
    assert "科研证据综合分析" in home
    assert "计算机论文实验复现" in home
    assert '"pvd_process_optimization_workflow"' in client
    assert '"opentrajectory_research_workflow"' in client
    assert '"cs_paper_reproduction_workflow"' in client


def test_cs_v4_baseline_reference_chain_is_artifact_lineage_and_independent_from_actual_metrics() -> None:
    snapshot = json.loads(build_registry_snapshot().canonical_payload)
    bindings = [
        row
        for row in snapshot["bindings"]["skills"]
        if row["definitionId"] == "cs_reproduction:reproduction_baseline_criteria"
        and row["definitionVersion"] == 2
        and row["enabled"] is True
    ]
    assert {row["skillId"] for row in bindings} == {
        "cs_reproduction_input_structuring",
        "repository_revision_acquisition",
        "official_reference_source_acquisition",
        "reference_metric_interpretation",
        "reference_metric_fact_assembly",
        "reproduction_baseline_and_criteria",
    }
    serialized = json.dumps(bindings, ensure_ascii=False)
    assert "actual_metrics" not in serialized

    source_binding = next(
        row for row in bindings if row["skillId"] == "official_reference_source_acquisition"
    )
    assert source_binding["inputSources"]["fields"] == [
        {
            "targetField": "code_source",
            "sourcePresence": "required",
            "source": {
                "kind": "artifact",
                "bindingId": (
                    "bind:cs_reproduction:reproduction_baseline_criteria:"
                    "skill:repository_revision_acquisition"
                ),
                "bindingVersion": 2,
                "field": "$",
            },
        }
    ]


def test_cs_v4_reference_projection_and_b05_comparison_use_current_authorities() -> None:
    snapshot = json.loads(build_registry_snapshot().canonical_payload)
    skills = {row["skillId"]: row for row in snapshot["definitions"]["skills"]}
    declaration = skills["reference_metric_fact_assembly"]["sourceContract"][
        "scientific_state_projection"
    ]
    assert declaration["contract_version"] == 2
    assert {row["fact_key"] for row in declaration["facts"]} >= {
        "reference_metric_records",
        "reference_metric_comparison_requests",
    }

    comparison_binding = next(
        row
        for row in snapshot["bindings"]["skills"]
        if row["bindingId"]
        == "bind:cs_reproduction:result_comparability_judgment:skill:reference_metric_comparison"
        and row["bindingVersion"] == 2
    )
    assert comparison_binding["enabled"] is True
    reference_row = next(
        row
        for row in comparison_binding["inputSources"]["fields"]
        if row["targetField"] == "reference_metrics"
    )
    assert reference_row == {
        "targetField": "reference_metrics",
        "sourcePresence": "required",
        "source": {
            "kind": "artifact",
            "bindingId": (
                "bind:cs_reproduction:reproduction_baseline_criteria:"
                "skill:reproduction_baseline_and_criteria"
            ),
            "bindingVersion": 2,
            "field": "reference_metric_comparison_requests",
        },
    }


def test_cs_v4_execution_plan_remains_artifact_authority_not_scientific_state() -> None:
    snapshot = json.loads(build_registry_snapshot().canonical_payload)
    skills = {row["skillId"]: row for row in snapshot["definitions"]["skills"]}
    assert "scientific_state_projection" not in skills["execution_plan_fact_assembly"]["sourceContract"]

    bindings = snapshot["bindings"]["skills"]
    design_binding_id = (
        "bind:cs_reproduction:reproduction_experiment_design:skill:reproduction_experiment_design"
    )
    environment_binding = next(
        row
        for row in bindings
        if row["bindingId"]
        == "bind:cs_reproduction:execution_readiness_validation:skill:environment_setup"
        and row["bindingVersion"] == 2
    )
    environment_rows = {
        row["targetField"]: row for row in environment_binding["inputSources"]["fields"]
    }
    for target_field in {"python_version", "requirements", "verify_imports"}:
        source = environment_rows[target_field]["source"]
        assert source["kind"] == "artifact"
        assert source["bindingId"] == design_binding_id
        assert source["bindingVersion"] == 2

    frozen_binding = next(
        row
        for row in bindings
        if row["bindingId"]
        == "bind:cs_reproduction:real_execution_result_acquisition:skill:reproduction_execution_input_closure"
        and row["bindingVersion"] == 2
    )
    frozen_rows = {row["targetField"]: row for row in frozen_binding["inputSources"]["fields"]}
    assert {key: value for key, value in frozen_rows["execution_plan_facts"]["source"].items() if key != "admission"} == {
        "kind": "artifact",
        "bindingId": (
            "bind:cs_reproduction:reproduction_experiment_design:"
            "skill:execution_plan_fact_assembly"
        ),
        "bindingVersion": 2,
        "field": "$",
    }

    run_binding = next(
        row
        for row in bindings
        if row["bindingId"]
        == "bind:cs_reproduction:real_execution_result_acquisition:skill:exploratory_run"
        and row["bindingVersion"] == 2
    )
    for row in run_binding["inputSources"]["fields"]:
        assert row["source"]["kind"] == "artifact"
        assert row["source"]["bindingId"] == (
            "bind:cs_reproduction:real_execution_result_acquisition:"
            "skill:reproduction_execution_input_closure"
        )
        assert row["source"]["bindingVersion"] == 2


def test_cs_current_authority_outputs_publish_explicit_scientific_state_acceptance_chain() -> None:
    snapshot = json.loads(build_registry_snapshot().canonical_payload)
    skills = {row["skillId"]: row for row in snapshot["definitions"]["skills"]}
    expected = {
        "actual_metric_records_closure": {
            "actual_metric_status": "status",
            "actual_metric_execution_record_ref": "execution_record_ref",
            "actual_metric_records": "metrics",
            "actual_metric_reference_values_used": "reference_values_used",
            "actual_metric_partial_failures": "partial_failures",
            "actual_metric_unknowns": "unknowns",
        },
        "reproduction_comparison_closure": {
            "reproduction_comparison_verdict": "overall_verdict",
            "reproduction_comparability": "comparability",
            "reproduction_metric_judgments": "metric_judgments",
            "reproduction_condition_mismatches": "condition_mismatches",
            "reproduction_rule_application": "rule_application",
            "reproduction_comparison_evidence_refs": "evidence_refs",
            "reproduction_comparison_unknowns": "unknowns",
        },
        "reproduction_deviation_diagnosis_v2": {
            "reproduction_evidence_supported_causes": "evidence_supported_causes",
            "reproduction_deviation_hypotheses": "hypotheses",
            "reproduction_diagnosis_remaining_unknowns": "remaining_unknowns",
        },
        "reproduction_conclusion_closure": {
            "reproduction_verdict": "verdict",
            "reproduction_comparison": "comparison",
            "reproduction_comparison_source": "comparison_source",
            "reproduction_conclusion_answer": "answer",
            "reproduction_scope": "scope",
            "reproduction_execution_status": "execution_status",
            "reproduction_evidence_sufficiency": "evidence_sufficiency",
            "reproduction_conclusion_evidence_refs": "evidence_refs",
            "reproduction_limitations": "limitations",
            "reproduction_unknowns": "unknowns",
            "reproduction_upstream_unknowns": "upstream_unknowns",
            "reproduction_next_steps": "next_steps",
            "reproduction_synthesis_gaps": "synthesis_gaps",
            "reproduction_isolated_synthesis_segments": "isolated_synthesis_segments",
        },
    }

    for skill_id, fact_map in expected.items():
        declaration = skills[skill_id]["sourceContract"]["scientific_state_projection"]
        assert declaration["contract_version"] == 2
        assert declaration["materializer_ref"] == "research:declared-scientific-facts:v1"
        assert {row["fact_key"]: row["source_field"] for row in declaration["facts"]} == fact_map

    # Plans, raw execution products and the B07 synthesis draft remain Artifacts;
    # they must not bypass the accepted-result boundary into ScientificState.
    for skill_id in (
        "execution_plan_fact_assembly",
        "exploratory_run",
        "actual_metric_extraction",
        "reproduction_conclusion_synthesis_v2",
    ):
        assert "scientific_state_projection" not in skills[skill_id]["sourceContract"]


def test_cs_accepted_result_chain_reaches_v3_final_result_without_authority_rewrite() -> None:
    snapshot = json.loads(build_registry_snapshot().canonical_payload)
    skills = {row["skillId"]: row for row in snapshot["definitions"]["skills"]}
    task = {
        "taskId": "task-cs-state-chain",
        "status": "running",
        "finishedAt": None,
        "finalResultAuthority": None,
        "scientificStates": [],
    }
    occurred_at = datetime(2026, 9, 20, 6, 0, tzinfo=timezone.utc)
    steps = [
        (
            "actual_metric_records_closure",
            "artifact-actual-metrics",
            {
                "status": "complete",
                "execution_record_ref": "artifact-execution-record",
                "metrics": [{"metric_name": "Avg.", "value": 81.58}],
                "reference_values_used": False,
                "partial_failures": [],
                "unknowns": [],
            },
        ),
        (
            "reproduction_comparison_closure",
            "artifact-comparison",
            {
                "overall_verdict": "meets_standard",
                "comparability": {"status": "comparable"},
                "metric_judgments": [{"metric_name": "Avg.", "verdict": "meets_standard"}],
                "condition_mismatches": [],
                "rule_application": {"kind": "exact"},
                "evidence_refs": ["artifact-actual-metrics", "artifact-reference-metrics"],
                "unknowns": [],
            },
        ),
        (
            "reproduction_deviation_diagnosis_v2",
            "artifact-diagnosis",
            {
                "entry_assessment": {
                    "status": "applicable",
                    "trigger_types": ["run_variation"],
                    "decision_relevance": "material",
                    "operational_failure_only": False,
                    "reason": "Residual variance may affect interpretation.",
                },
                "evidence_supported_causes": [],
                "hypotheses": [
                    {
                        "hypothesis_id": "hyp-1",
                        "statement": "A remaining implementation difference may explain residual variance.",
                        "supporting_evidence_refs": [],
                        "missing_evidence": ["Direct validation is not yet available."],
                        "affected_judgments": ["reproduction_conclusion"],
                    }
                ],
                "validation_actions": [],
                "judgment_impacts": [],
                "remaining_unknowns": [
                    {
                        "unknown_id": "unknown-1",
                        "statement": "Residual run-to-run variance is not yet resolved.",
                        "judgment_effect": "Does not alter the accepted B05 verdict.",
                    }
                ],
                "closure": {
                    "validation_needed": False,
                    "can_close_with_limitations": True,
                    "reason": "The remaining uncertainty does not change the B05 verdict.",
                },
            },
        ),
        (
            "reproduction_conclusion_closure",
            "artifact-conclusion",
            {
                "verdict": "reproduced",
                "comparison": {"overall_verdict": "meets_standard"},
                "comparison_source": "reproduction_comparison_judgment",
                "answer": {
                    "text": "The tested scope reproduces the frozen comparison target.",
                    "evidence_refs": ["artifact-comparison", "artifact-execution-record"],
                },
                "scope": {
                    "text": "Frozen code revision, model resource and STS test scope.",
                    "evidence_refs": ["artifact-execution-record"],
                },
                "execution_status": "succeeded",
                "evidence_sufficiency": "sufficient",
                "evidence_refs": ["artifact-comparison", "artifact-execution-record"],
                "limitations": [{
                    "text": "Conclusion is limited to the frozen evaluation scope.",
                    "evidence_refs": ["artifact-execution-record"],
                }],
                "unknowns": [{
                    "text": "Residual run-to-run variance is not yet resolved.",
                    "evidence_refs": ["artifact-diagnosis"],
                }],
                "upstream_unknowns": [{
                    "text": "Residual run-to-run variance is not yet resolved.",
                    "source": "reproduction_deviation_diagnosis",
                }],
                "next_steps": [],
                "synthesis_gaps": [],
                "isolated_synthesis_segments": [],
            },
        ),
    ]

    for index, (skill_id, artifact_id, document) in enumerate(steps):
        declaration = skills[skill_id]["sourceContract"]["scientific_state_projection"]
        projected = project_validated_structured_result(
            json.dumps(document, ensure_ascii=False),
            declaration,
        )
        task = materialize_scientific_state_projection(
            task_payload=task,
            declaration=declaration,
            projected_facts=projected,
            source_artifact_id=artifact_id,
            research_design_version=1,
            occurred_at=occurred_at.replace(minute=index),
        )

    state = task["scientificStates"][-1]
    facts = state["scientificFacts"]
    assert facts["actual_metric_records"] == [{"metric_name": "Avg.", "value": 81.58}]
    assert facts["reproduction_comparison_verdict"] == "meets_standard"
    assert facts["reproduction_deviation_hypotheses"][0]["hypothesis_id"] == "hyp-1"
    assert facts["reproduction_verdict"] == "reproduced"
    assert facts["reproduction_execution_status"] == "succeeded"
    assert facts["reproduction_conclusion_answer"]["text"].startswith(
        "The tested scope reproduces"
    )
    assert state["sourceRefs"] == [
        "artifact-actual-metrics",
        "artifact-comparison",
        "artifact-diagnosis",
        "artifact-conclusion",
    ]

    # B06 can add an explicitly-labelled hypothesis, but it cannot replace the
    # B05 comparison authority. B07 carries a deterministic mapped conclusion
    # under a separate key, so both authorities remain visible and auditable.
    assert facts["reproduction_comparison_verdict"] == "meets_standard"
    assert facts["reproduction_verdict"] == "reproduced"

    final_fields = expected_final_result_fields(
        state,
        decision={"uncertainty": [], "gaps": [], "limitations": []},
    )
    assert final_fields["scientificResult"] == facts
    assert final_fields["scientificResult"]["reproduction_comparison_verdict"] == "meets_standard"
    assert final_fields["scientificResult"]["reproduction_verdict"] == "reproduced"
