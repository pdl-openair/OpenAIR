from __future__ import annotations

import pytest

from app.execution.durable_orchestrator_bridge import (
    FrozenPlanError,
    _selected_skill_routes,
    build_durable_orchestration_plan,
    compile_durable_work_units,
)
from app.modules.agent_registry import load_agent_registry


def _skill_capability(skill_id: str, *, available: bool = True) -> dict:
    definition_id = "test:route"
    return {
        "kind": "skill",
        "id": skill_id,
        "resolutionType": "skill_route",
        "requestedCapability": skill_id,
        "resolvedAgentId": "general_research_agent",
        "resolvedSkillIds": [skill_id],
        "routeAvailability": "available" if available else "unavailable",
        "resolvedSkillDefinitions": [{
            "skillId": skill_id,
            "definitionVersion": 1,
            "status": "ready",
            "enabled": True,
            "inputContract": {"status": "known", "acceptedInputRefs": ["goal"]},
            "outputContract": {"status": "known", "artifactTypes": ["result"]},
            "judgmentSupport": {"deterministic": True},
        }],
        "resolvedSkillBindings": ([{
            "bindingId": f"bind:{definition_id}:skill:{skill_id}",
            "bindingVersion": 1,
            "definitionId": definition_id,
            "definitionVersion": 1,
            "skillId": skill_id,
            "skillDefinitionVersion": 1,
            "enabled": True,
            "sourceAuthority": "skill_binding_source",
        }] if available else []),
        **({} if available else {"routeUnavailableReason": "skill_binding_unavailable"}),
    }


def test_compiler_freezes_every_planned_agent_artifact_contract_before_provider_dispatch() -> None:
    units = compile_durable_work_units({
        "input_snapshot": {
            "goalSummary": "extract evidence, audit it, then design validation",
            "inputMemoryRefs": ["memory_goal", "memory_goal"],
            "inputArtifactRefs": ["artifact_brief"],
            "creationPlan": {
                "orchestratorDecision": {
                    "primaryAgent": "paper_evidence_extraction",
                    "supportingAgents": ["result_reliability_audit", "validation_design"],
                },
                "selectedCapabilities": [
                    {"kind": "agent", "id": "paper_evidence_extraction", "substages": ["evidence_extraction"], "skillIds": ["literature-search"]},
                    {"kind": "agent", "id": "result_reliability_audit", "substages": ["result_acceptance"], "skillIds": ["audit"]},
                    {"kind": "agent", "id": "validation_design", "substages": ["variable_definition"], "skillIds": ["variable-identification"]},
                ],
                "stages": [
                    {"stageKey": "problem_modeling", "substages": [{"substage": "evidence_extraction", "skills": ["evidence-table"]}]},
                    {"stageKey": "solution_design", "substages": [{"substage": "result_acceptance"}]},
                    {"stageKey": "solution_design", "substages": [{"substage": "variable_definition"}]},
                ],
            },
        },
    })

    assert [(unit["agentId"], unit["artifactContract"]) for unit in units] == [
        ("paper_evidence_extraction", "paper_evidence_extraction_artifact_v1"),
        ("result_reliability_audit", "result_reliability_audit_artifact_v1"),
        ("validation_design", "validation_design_artifact_v1"),
    ]
    assert all(unit["artifactSchema"] and unit["artifactSchemaDigest"] for unit in units)


def test_compiler_freezes_a_schema_backed_specialist() -> None:
    units = compile_durable_work_units({
        "input_snapshot": {"goalSummary": "extract paper evidence", "creationPlan": {
            "selectedCapabilities": [{"kind": "agent", "id": "research_question_refinement", "skillIds": []}],
        }},
    })

    assert [(unit["kind"], unit["agentId"], unit["selectedSkillIds"]) for unit in units] == [
        ("specialist", "research_question_refinement", []),
    ]
    assert units[0]["modelMode"] == "terra"


def test_compiler_freezes_generalist_artifact_contract() -> None:
    units = compile_durable_work_units({
        "input_snapshot": {"goalSummary": "perform a safety review", "creationPlan": {
            "selectedCapabilities": [{"kind": "agent", "id": "general_research_agent", "skillIds": []}],
        }},
    })

    assert [(unit["agentId"], unit["artifactContract"]) for unit in units] == [
        ("general_research_agent", "general_research_reasoning_artifact_v1"),
    ]


def test_compiler_freezes_registered_question_artifact_contract() -> None:
    unit = compile_durable_work_units({
        "input_snapshot": {"goalSummary": "bound this question", "creationPlan": {
            "selectedCapabilities": [{"kind": "agent", "id": "research_question_refinement", "skillIds": []}],
        }},
    })[0]

    assert unit["artifactContract"] == "research_question_refinement_artifact_v1"
    assert unit["artifactContractSchemaVersion"] == 1
    assert unit["artifactRequiredFields"] == [
        "question_candidates", "scope_boundary", "assumptions",
        "verification_readiness", "next_evidence_needs", "handoff",
    ]
    review = unit["mandatoryReviewContract"]
    assert review["artifactContract"] == "independent_result_review_v1"
    assert review["evaluationCriteria"]
    assert any("planning-only" in criterion for criterion in review["evaluationCriteria"])


def test_bridge_blocks_unregistered_frozen_agent_before_execution() -> None:
    plan = build_durable_orchestration_plan({
        "run_id": "run_plan", "task_id": "task_plan", "owner_id": "owner_plan", "workspace_id": "workspace_plan",
        "run_number": 1, "lease_epoch": 4, "source_mode": "live", "input_snapshot": {"creationPlan": {
            "orchestratorDecision": {"primaryAgent": "not_registered", "supportingAgents": []},
        }},
    })

    assert plan["workUnits"] == []
    assert plan["planBlock"] == "unregistered_agent:primaryAgent:not_registered"
    assert plan["selectedAgentId"] is None


def test_bridge_uses_only_the_frozen_plan_not_task_messages_or_a_new_router_choice() -> None:
    plan = build_durable_orchestration_plan({
        "run_id": "run_plan", "task_id": "task_plan", "owner_id": "owner_plan", "workspace_id": "workspace_plan",
        "run_number": 1, "lease_epoch": 4, "source_mode": "live", "target_substage": "variable_definition",
        "input_snapshot": {
            "currentStage": "solution_design",
            "creationPlan": {"selectedCapabilities": [{"kind": "agent", "id": "research_question_refinement", "skillIds": []}]},
            "agentMessages": [{"text": "must not alter frozen execution"}],
        },
    })

    assert [unit["agentId"] for unit in plan["workUnits"]] == ["research_question_refinement"]
    assert plan["selectedAgentId"] == "research_question_refinement"
    assert "inputSnapshot" not in plan
    assert "agentMessages" not in plan


def test_selected_skill_route_uses_frozen_identity_and_binding() -> None:
    capability = _skill_capability("formal_skill")

    routes = _selected_skill_routes(
        {"selectedCapabilities": [capability]}, load_agent_registry()
    )

    assert routes[0]["skillIds"] == ["formal_skill"]
    assert routes[0]["skillBindings"][0]["bindingId"] == (
        "bind:test:route:skill:formal_skill"
    )


def test_unavailable_route_does_not_revoke_an_independent_frozen_route() -> None:
    routes = _selected_skill_routes(
        {"selectedCapabilities": [
            _skill_capability("missing_skill", available=False),
            _skill_capability("formal_skill"),
        ]},
        load_agent_registry(),
    )

    assert [route["skillIds"] for route in routes] == [["formal_skill"]]


def test_only_unavailable_route_is_locally_blocked() -> None:
    with pytest.raises(FrozenPlanError, match="required_skill_route_unavailable"):
        _selected_skill_routes(
            {"selectedCapabilities": [_skill_capability("missing_skill", available=False)]},
            load_agent_registry(),
        )


def test_agent_only_plan_never_checks_skill_routes() -> None:
    assert _selected_skill_routes(
        {"selectedCapabilities": [{
            "kind": "agent",
            "id": "research_question_refinement",
            "resolutionType": "specialist",
        }]},
        load_agent_registry(),
    ) == []
