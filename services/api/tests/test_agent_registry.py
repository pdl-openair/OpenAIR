from __future__ import annotations

import copy
import json

import pytest

from app.modules.agent_registry import (
    AgentRegistryError,
    REGISTRY_PATH,
    build_agent_registry,
    is_registered_agent_callable,
    load_agent_registry,
    registered_agent_callable_ids,
    require_registered_agent,
    validate_agent_registry_payload,
)


EXPECTED_AGENT_IDS = {
    "general_research_agent",
    "key_literature_positioning",
    "mechanism_dispute_mapping",
    "paper_evidence_extraction",
    "research_question_refinement",
    "result_reliability_audit",
    "validation_design",
    "research_gap_identification",
}


def test_registry_loads_production_specialist_agents_with_complete_contracts() -> None:
    registry = load_agent_registry()

    assert registry.ids() == EXPECTED_AGENT_IDS
    for agent in registry.list_agents():
        assert agent.capability
        assert agent.notFor
        assert agent.inputContract["required"]
        assert agent.outputContract.get("requiredFields") or agent.outputContract.get("artifactSchema")
        assert agent.failClosedRules
        assert agent.budgetPolicy["defaultMode"] in agent.budgetPolicy["allowedModes"]
        assert agent.evaluator["id"].endswith("_validator")
        assert agent.sdr_mappings
        assert agent.skillIds


def test_registry_query_boundary_resolves_current_agent_aliases_and_rejects_unknown() -> None:
    assert require_registered_agent("agent_evidence_extraction_v0_3").agentId == "paper_evidence_extraction"
    assert require_registered_agent("validation_design_planning").agentId == "validation_design"
    assert is_registered_agent_callable("agent_gap_identification_v0_3") is True
    assert is_registered_agent_callable("ad_hoc_research_agent") is False
    assert {
        "paper_evidence_extraction",
        "agent_evidence_extraction_v0_3",
        "validation_design_planning",
    }.issubset(registered_agent_callable_ids())

    registry = load_agent_registry()
    evidence_agents = registry.by_sdr_substage("execution_validation", "evidence_extraction")
    assert {agent.agentId for agent in evidence_agents} >= {
        "paper_evidence_extraction",
        "research_gap_identification",
        "result_reliability_audit",
    }

    with pytest.raises(AgentRegistryError, match="not registered"):
        require_registered_agent("ad_hoc_research_agent")


def test_registry_records_map_to_sdr_substages_and_artifact_contracts() -> None:
    registry = load_agent_registry()

    evidence = registry.require("paper_evidence_extraction")
    assert evidence.artifact_kind == "paper_evidence_extraction"
    assert evidence.artifact_contract == "paper_evidence_extraction_artifact_v1"
    assert any(
        row["stage"] == "execution_validation"
        and row["substage"] == "evidence_extraction"
        and row["artifactContract"] == evidence.artifact_contract
        for row in evidence.sdr_mappings
    )

    validation = registry.require("validation_design")
    mapped_substages = {(row["stage"], row["substage"]) for row in validation.sdr_mappings}
    assert ("solution_design", "variable_definition") in mapped_substages
    assert ("solution_design", "analysis_planning") in mapped_substages


def test_enabled_agents_have_durable_skill_grants_covering_their_sdr_mappings() -> None:
    from app.execution.durable_skill_runtime import list_registered_durable_skills

    durable_skills = {
        str(skill["skillId"]): {str(substage) for substage in skill.get("substages") or []}
        for skill in list_registered_durable_skills()
        if skill.get("durableExecutable")
    }
    for agent in load_agent_registry().list_agents():
        if not agent.enabled:
            continue
        assert set(agent.skillIds).issubset(durable_skills)
        granted_substages = set().union(*(durable_skills[skill_id] for skill_id in agent.skillIds))
        required_substages = {
            str(mapping["substage"])
            for mapping in agent.sdr_mappings
            if mapping["enabled"]
        }
        assert required_substages.issubset(granted_substages)


def test_registry_validator_fails_closed_for_empty_unknown_or_uncovered_skill_grants() -> None:
    payload = _minimal_valid_payload()
    payload["agents"][0]["skillIds"] = []
    report = validate_agent_registry_payload(payload)
    assert any(issue.code == "missing_skill_closure" for issue in report.issues)

    payload["agents"][0]["skillIds"] = ["not_a_real_skill"]
    report = validate_agent_registry_payload(payload)
    assert any(issue.code == "unknown_durable_skill_grant" for issue in report.issues)
    assert any(issue.code == "missing_sdr_skill_coverage" for issue in report.issues)


def test_registry_validator_finds_missing_required_contract_sections() -> None:
    payload = _minimal_valid_payload()
    broken = copy.deepcopy(payload)
    agent = broken["agents"][0]
    agent.pop("inputContract")
    agent.pop("outputContract")
    agent.pop("failClosedRules")
    agent.pop("budgetPolicy")
    agent.pop("evaluator")

    report = validate_agent_registry_payload(broken)

    assert not report.valid
    codes = {issue.code for issue in report.issues}
    assert "missing_input_contract" in codes
    assert "missing_output_contract" in codes
    assert "missing_fail_closed_rules" in codes
    assert "missing_budget_policy" in codes
    assert "missing_evaluator" in codes


def test_registry_validator_accepts_artifact_schema_without_required_fields() -> None:
    payload = _minimal_valid_payload()
    payload["agents"][0]["outputContract"] = {
        "artifactKind": "research_question_refinement",
        "artifactContract": "research_question_refinement_artifact_v1",
        "artifactSchema": "schemas/agents/artifacts/research_question_refinement_artifact_v1.schema.json",
    }

    report = validate_agent_registry_payload(payload)

    assert report.valid, [issue.to_dict() for issue in report.issues]


def test_registry_validator_rejects_output_contract_without_fields_or_schema() -> None:
    payload = _minimal_valid_payload()
    payload["agents"][0]["outputContract"] = {
        "artifactKind": "test_artifact",
        "artifactContract": "test_artifact_v1",
    }

    report = validate_agent_registry_payload(payload)

    assert not report.valid
    assert any(
        issue.code == "missing_output_contract" and issue.field == "outputContract"
        for issue in report.issues
    )


def test_registry_validator_rejects_unbounded_generic_research_agent_config() -> None:
    payload = _minimal_valid_payload()
    payload["agents"][0]["capability"] = ["Agent can do research"]

    report = validate_agent_registry_payload(payload)

    assert not report.valid
    assert any(issue.code == "unbounded_capability" for issue in report.issues)


def test_registry_validator_rejects_alias_agent_id_collision() -> None:
    payload = _minimal_valid_payload()
    first = payload["agents"][0]
    first["aliases"] = ["second_agent"]
    second = copy.deepcopy(first)
    second["agentId"] = "second_agent"
    second["aliases"] = []
    payload["agents"].append(second)

    report = validate_agent_registry_payload(payload)

    assert not report.valid
    assert any(issue.code == "duplicate_agent_id" for issue in report.issues)


def test_registry_validator_rejects_zero_wall_time_budget() -> None:
    payload = _minimal_valid_payload()
    payload["agents"][0]["budgetPolicy"]["maxWallTimeSec"] = 0

    report = validate_agent_registry_payload(payload)

    assert not report.valid
    assert any(issue.field == "budgetPolicy.maxWallTimeSec" for issue in report.issues)


def test_orchestration_identity_and_provider_policy_are_explicit_and_required() -> None:
    payload = _minimal_valid_payload()
    for field in (
        "agentKind",
        "candidateScope",
        "enabled",
        "skillIds",
        "allowedProviders",
        "defaultProvider",
        "judgmentSupport",
    ):
        broken = copy.deepcopy(payload)
        broken["agents"][0].pop(field)
        report = validate_agent_registry_payload(broken)
        assert any(issue.field == field for issue in report.issues), field

    for field in ("pack", "supportedRole", "enabled"):
        broken = copy.deepcopy(payload)
        broken["agents"][0]["sdr"]["mappings"][0].pop(field)
        report = validate_agent_registry_payload(broken)
        assert any(issue.field.endswith(field) for issue in report.issues), field


def test_provider_policy_is_preserved_and_validated() -> None:
    payload = _minimal_valid_payload()
    agent = payload["agents"][0]
    agent["toolRequirements"] = {"requiresFilesystem": False}

    registered = build_agent_registry(payload).require("test_agent")
    assert registered.to_dict()["allowedProviders"] == ["openai_primary"]
    assert registered.toolRequirements == {"requiresFilesystem": False}

    agent["defaultProvider"] = "not_allowed"
    report = validate_agent_registry_payload(payload)
    assert any(issue.field == "defaultProvider" for issue in report.issues)

    agent["allowedProviders"] = ["model-name"]
    report = validate_agent_registry_payload(payload)
    assert any(issue.field == "allowedProviders" for issue in report.issues)

    agent["allowedProviders"] = ["provider_declared_by_static_agent_policy"]
    agent["defaultProvider"] = "provider_declared_by_static_agent_policy"
    report = validate_agent_registry_payload(payload)
    assert report.valid, report.to_dict()


def test_registry_schema_file_tracks_core_contract_requirements() -> None:
    schema_path = REGISTRY_PATH.with_suffix(".schema.json")
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    required = set(schema["$defs"]["agent"]["required"])

    assert {
        "agentId",
        "capability",
        "notFor",
        "inputContract",
        "outputContract",
        "failClosedRules",
        "budgetPolicy",
        "evaluator",
        "sdr",
        "agentKind",
        "candidateScope",
        "enabled",
        "skillIds",
        "allowedProviders",
        "defaultProvider",
        "judgmentSupport",
    }.issubset(required)


def test_build_registry_fails_closed_when_sdr_mapping_is_unknown() -> None:
    payload = _minimal_valid_payload()
    payload["agents"][0]["sdr"]["mappings"][0]["substage"] = "made_up_substage"

    with pytest.raises(AgentRegistryError, match="unknown_sdr_mapping"):
        build_agent_registry(payload)


def _minimal_valid_payload() -> dict:
    return {
        "schemaVersion": 1,
        "agents": [
            {
                "agentId": "test_agent",
                "displayName": "Test Agent",
                "visibility": "internal",
                "agentKind": "specialist",
                "candidateScope": "explicit_mappings",
                "enabled": True,
                "skillIds": ["evidence_mining"],
                "allowedProviders": ["openai_primary"],
                "defaultProvider": "openai_primary",
                "judgmentSupport": {"domainProfessional": True, "humanAuthority": False},
                "capability": ["Extract bounded evidence from supplied records."],
                "notFor": ["General research or unsupported claims."],
                "inputContract": {"required": ["user_goal", "material_manifest"]},
                "outputContract": {
                    "artifactKind": "test_artifact",
                    "artifactContract": "test_artifact_v1",
                    "requiredFields": ["status"],
                },
                "failClosedRules": ["If required materials are missing, return insufficient_materials."],
                "budgetPolicy": {
                    "defaultMode": "evidence_bound",
                    "allowedModes": ["evidence_bound"],
                    "maxWallTimeSec": 30,
                    "maxToolCalls": 1,
                },
                "evaluator": {
                    "id": "test_agent_validator",
                    "artifactValidator": "validate_research_agent_artifact",
                    "requiredMetadata": ["status"],
                    "qualitySignals": ["source_boundaries"],
                },
                "sdr": {
                    "primaryStages": ["execution_validation"],
                    "mappings": [
                        {
                            "pack": "sdr",
                            "stage": "execution_validation",
                            "substage": "evidence_extraction",
                            "role": "primary_execution",
                            "supportedRole": "primary",
                            "enabled": True,
                            "artifactContract": "test_artifact_v1",
                        }
                    ],
                },
            }
        ],
    }
