from __future__ import annotations

import pytest
import json


def _catalog() -> list[dict]:
    return [
        {
            "definitionId": "wf:intent",
            "definitionVersion": 1,
            "pack": "workflow_test",
            "planningApplicability": {
                "scopeKind": "specialized",
                "scopeLabel": "Workflow test",
                "scopeDescription": "Test workflow research work",
                "whenToUse": "Use only inside the test workflow.",
            },
            "stage": "problem_modeling",
            "label": "Intent",
            "description": "Clarify the research goal.",
            "instancePolicy": {"mode": "repeatable"},
            "requiredInputRefs": ["user:request"],
            "artifactTypes": ["intent"],
            "criterionRefs": ["crit:intent"],
            "contractComplete": True,
        },
        {
            "definitionId": "wf:optional",
            "definitionVersion": 1,
            "pack": "workflow_test",
            "planningApplicability": {
                "scopeKind": "specialized",
                "scopeLabel": "Workflow test",
                "scopeDescription": "Test workflow research work",
                "whenToUse": "Use only inside the test workflow.",
            },
            "stage": "solution_design",
            "label": "Optional",
            "description": "Optional enhancement.",
            "instancePolicy": {"mode": "repeatable"},
            "requiredInputRefs": ["intent"],
            "artifactTypes": ["optional"],
            "criterionRefs": ["crit:optional"],
            "contractComplete": True,
        },
        {
            "definitionId": "wf:findings_synthesis",
            "definitionVersion": 1,
            "pack": "workflow_test",
            "planningApplicability": {
                "scopeKind": "specialized",
                "scopeLabel": "Workflow test",
                "scopeDescription": "Test workflow research work",
                "whenToUse": "Use only inside the test workflow.",
            },
            "stage": "result_synthesis",
            "label": "Findings",
            "description": "Synthesize the research findings.",
            "instancePolicy": {"mode": "repeatable"},
            "requiredInputRefs": ["intent"],
            "artifactTypes": ["findings"],
            "criterionRefs": ["crit:findings"],
            "contractComplete": True,
        },
        {
            "definitionId": "sdr:report_generation",
            "definitionVersion": 1,
            "pack": "sdr",
            "planningApplicability": {
                "scopeKind": "general",
                "scopeLabel": "SDR",
                "scopeDescription": "General research work",
                "whenToUse": "Use across domains for final report generation.",
            },
            "stage": "result_delivery",
            "label": "Report",
            "description": "Produce the final user-facing report.",
            "instancePolicy": {"mode": "repeatable"},
            "requiredInputRefs": ["findings"],
            "artifactTypes": ["research_report"],
            "criterionRefs": ["crit:report"],
            "contractComplete": True,
        },
    ]


def _workflow_context() -> dict:
    return {
        "schemaVersion": "workflow-planning-context.v1",
        "workflowId": "wf",
        "workflowVersion": 1,
        "workflowContentHash": "sha256:" + "a" * 64,
        "requiredNodes": [
            {"nodeKey": "intent", "definitionId": "wf:intent", "definitionVersion": 1, "displayOrder": 10},
            {"nodeKey": "findings", "definitionId": "wf:findings_synthesis", "definitionVersion": 1, "displayOrder": 30},
        ],
        "optionalNodes": [
            {"nodeKey": "optional", "definitionId": "wf:optional", "definitionVersion": 1, "displayOrder": 20},
        ],
        "dependencies": [
            {
                "upstreamNodeKey": "intent",
                "downstreamNodeKey": "optional",
                "upstreamDefinitionId": "wf:intent",
                "downstreamDefinitionId": "wf:optional",
                "dependencyType": "scientific_dependency",
            },
            {
                "upstreamNodeKey": "intent",
                "downstreamNodeKey": "findings",
                "upstreamDefinitionId": "wf:intent",
                "downstreamDefinitionId": "wf:findings_synthesis",
                "dependencyType": "scientific_dependency",
            },
        ],
        "boundaryPolicy": {
            "allowedExternalActions": ["external_read", "external_compute"],
            "confirmationRequiredActions": [],
            "prohibitedActions": ["external_write", "physical_action"],
        },
    }


def test_selected_workflow_builds_minimum_required_semantics_without_optional_expansion() -> None:
    from app.modules.research_design_planner_assembly import build_deterministic_workflow_semantics

    semantic = build_deterministic_workflow_semantics(
        raw_input="Research the battery interface and form a verifiable result.",
        definition_catalog=_catalog(),
        workflow_planning_context=_workflow_context(),
    )

    assert [row.definition_id for row in semantic.work_items] == [
        "wf:intent",
        "wf:findings_synthesis",
    ]
    assert semantic.work_items[1].depends_on_local_keys == ["intent"]
    assert semantic.requested_external_actions == ["external_read", "external_compute"]


def test_selected_workflow_planner_prepares_bounded_semantic_enrichment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.modules import research_start_planner as planner

    monkeypatch.setattr(planner.settings, "research_orchestration_v1", True)
    monkeypatch.setenv("OPENAIR_RESEARCH_START_PLANNER_PROVIDER", "huiyan_chat_completions")
    monkeypatch.setenv("OPENAIR_RESEARCH_START_PLANNER_LLM_MODEL", "gpt-5.6-terra")
    monkeypatch.setenv("OPENAIR_RESEARCH_START_PLANNER_LLM_TIMEOUT_SECONDS", "77")
    monkeypatch.setenv("LLM_PROVIDER_API_KEY", "test-key")
    monkeypatch.setenv("LLM_PROVIDER_BASE_URL", "https://provider.test")

    prepared = planner.prepare_planner_attempt(
        {"input": "Research the battery interface and form a verifiable result.", "sourcePage": "home"},
        {"reason": "workflow"},
        1,
        definition_catalog=_catalog(),
        workflow_planning_context=_workflow_context(),
    )

    assert prepared.provider == "huiyan_chat_completions"
    assert prepared.model == "gpt-5.6-terra"
    assert prepared.timeout_seconds == 77.0
    assert prepared.payload["max_tokens"] > 0
    assert prepared.payload["thinking"] == {"type": "disabled"}
    assert prepared.payload["reasoning_effort"] == "none"
    serialized = json.dumps(prepared.payload, ensure_ascii=False, sort_keys=True)
    for forbidden in ("nodeKey", "definitionId", "dependsOnLocalKeys", "evaluatorRef"):
        assert forbidden not in serialized


def test_generic_v12_planning_keeps_llm_path_when_no_workflow_is_selected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.execution.research_start_provider_adapter import PreparedPlannerAttempt
    from app.modules import research_start_planner as planner

    monkeypatch.setattr(planner.settings, "research_orchestration_v1", True)
    monkeypatch.setenv("OPENAIR_RESEARCH_START_PLANNER_PROVIDER", "huiyan_chat_completions")
    monkeypatch.setenv("OPENAIR_RESEARCH_START_PLANNER_LLM_MODEL", "planner-test-model")
    monkeypatch.setenv("LLM_PROVIDER_API_KEY", "test-key")
    monkeypatch.setenv("LLM_PROVIDER_BASE_URL", "https://provider.test")

    prepared = planner.prepare_planner_attempt(
        {"input": "Create a bounded custom research plan.", "sourcePage": "home"},
        {"reason": "research_planning_start_draft"},
        1,
        definition_catalog=_catalog(),
    )

    assert isinstance(prepared, PreparedPlannerAttempt)
    assert prepared.timeout_seconds > 0


def test_deterministic_workflow_planner_fails_closed_on_unresolvable_required_node(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.modules import research_start_planner as planner

    monkeypatch.setattr(planner.settings, "research_orchestration_v1", True)
    monkeypatch.setenv("OPENAIR_RESEARCH_START_PLANNER_PROVIDER", "huiyan_chat_completions")
    monkeypatch.setenv("OPENAIR_RESEARCH_START_PLANNER_LLM_MODEL", "gpt-5.6-terra")
    monkeypatch.setenv("LLM_PROVIDER_API_KEY", "test-key")
    monkeypatch.setenv("LLM_PROVIDER_BASE_URL", "https://provider.test")
    context = _workflow_context()
    context["requiredNodes"][0]["definitionId"] = "wf:missing"

    with pytest.raises(ValueError, match="cannot be resolved uniquely"):
        planner.prepare_planner_attempt(
            {"input": "Research the battery interface.", "sourcePage": "home"},
            {"reason": "workflow"},
            1,
            definition_catalog=_catalog(),
            workflow_planning_context=context,
        )
