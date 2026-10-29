from __future__ import annotations

import pytest

from app.modules import capability_resolver as resolver


def _skill(
    skill_id: str,
    *,
    durable: bool = True,
    packs: list[str] | None = None,
    substages: list[str] | None = None,
) -> dict:
    return {
        "skillId": skill_id,
        "definitionVersion": 1,
        "enabled": True,
        "status": "ready",
        "claudeName": skill_id.replace("_", "-"),
        "packs": packs or ["sdr"],
        "substages": substages or [],
        "durableExecutable": durable,
        "sourceContract": {
            "capability_contract": {
                "artifact_types": [f"{skill_id}.artifact"],
                "judgment_support": {
                    "deterministic": True,
                    "domain_professional": False,
                },
            },
            "execution_method_roles": ["role:b", "role:a"],
            "frozenSchemas": {
                "input": {
                    "sha256": "0" * 64,
                    "schema": {"type": "object", "properties": {"goal": {"type": "string"}}},
                },
                "output": {
                    "sha256": "1" * 64,
                    "schema": {"type": "object", "properties": {"result": {"type": "string"}}},
                },
            },
        },
    }


def _binding_snapshot(*skill_ids: str, substage: str = "route") -> dict:
    definition_id = f"test:{substage}"
    return {
        "definitions": {
            "substages": [{
                "definitionId": definition_id,
                "definitionVersion": 1,
                "substageKey": substage,
                "enabled": True,
            }],
        },
        "bindings": {
            "skills": [{
                "bindingId": f"bind:{definition_id}:skill:{skill_id}",
                "bindingVersion": 1,
                "definitionId": definition_id,
                "definitionVersion": 1,
                "skillId": skill_id,
                "skillDefinitionVersion": 1,
                "enabled": True,
                "sourceAuthority": "skill_binding_source",
            } for skill_id in skill_ids],
        },
    }


def test_registered_specialist_wins_before_a_matching_skill(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        resolver,
        "list_registered_durable_skills",
        lambda: [_skill("research_question_refinement")],
    )

    resolved = resolver.resolve_creation_plan_capabilities({
        "selectedCapabilities": [{
            "kind": "agent",
            "id": "research_question_refinement",
            "skillIds": ["research_question_refinement"],
            "rationale": "收敛可研究问题",
        }],
    })

    capability = resolved["selectedCapabilities"][0]
    assert capability["resolutionType"] == "specialist"
    assert capability["resolvedAgentId"] == "research_question_refinement"
    assert capability["resolvedSkillIds"] == ["research_question_refinement"]


def test_two_registered_durable_skills_become_one_frozen_skill_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        resolver,
        "list_registered_durable_skills",
        lambda: [_skill("source_lookup"), _skill("source_screening")],
    )
    monkeypatch.setattr(
        resolver,
        "_load_registry_snapshot",
        lambda: _binding_snapshot("source_lookup", "source_screening"),
    )

    resolved = resolver.resolve_creation_plan_capabilities({
        "inputMemoryRefs": ["memory_goal"],
        "selectedCapabilities": [{
            "kind": "skill",
            "id": "source_lookup",
            "skillIds": ["source_lookup", "source_screening"],
            "rationale": "检索并筛选真实来源",
            "requiresExternalAction": True,
        }],
    })

    capability = resolved["selectedCapabilities"][0]
    assert capability["resolutionType"] == "skill_route"
    assert capability["resolvedAgentId"] == "general_research_agent"
    assert capability["resolvedSkillIds"] == ["source_lookup", "source_screening"]
    assert capability["memoryRefs"] == ["memory_goal"]
    assert capability["routeAvailability"] == "available"
    assert [row["bindingId"] for row in capability["resolvedSkillBindings"]] == [
        "bind:test:route:skill:source_lookup",
        "bind:test:route:skill:source_screening",
    ]


def test_generalist_requires_a_durable_skill_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(resolver, "list_registered_durable_skills", lambda: [])

    with pytest.raises(
        resolver.CapabilityResolutionError,
        match="required_capability_unavailable:general_research_agent",
    ):
        resolver.resolve_creation_plan_capabilities({
            "selectedCapabilities": [{
                "kind": "workflow",
                "id": "compare_confirmed_arguments",
                "skillIds": [],
                "rationale": "比较已确认论点的逻辑一致性",
                "requiresExternalAction": False,
            }],
        })


def test_provider_unavailable_does_not_erase_formally_qualified_specialist_skill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        resolver,
        "list_registered_durable_skills",
        lambda: [_skill("research_question_refinement", durable=False)],
    )

    resolved = resolver.resolve_creation_plan_capabilities({
        "selectedCapabilities": [{
            "kind": "agent",
            "id": "research_question_refinement",
            "skillIds": ["research_question_refinement"],
            "rationale": "收敛可研究问题",
        }],
    })

    assert resolved["selectedCapabilities"][0]["resolvedSkillIds"] == [
        "research_question_refinement"
    ]


def test_missing_external_capability_blocks_before_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(resolver, "list_registered_durable_skills", lambda: [])

    with pytest.raises(
        resolver.CapabilityResolutionError,
        match="required_capability_unavailable:database_search",
    ):
        resolver.resolve_creation_plan_capabilities({
            "selectedCapabilities": [{
                "kind": "skill",
                "id": "database_search",
                "skillIds": ["database_search"],
                "rationale": "检索外部数据库",
                "requiresExternalAction": True,
            }],
        })


def test_plain_research_word_does_not_imply_external_search() -> None:
    assert resolver._requires_external_action(
        {}, "Refine an ordinary research question from supplied context.", []
    ) is False
    assert resolver._requires_external_action(
        {}, "Search external literature for relevant evidence.", []
    ) is True


def test_pack_is_not_a_cross_workflow_skill_eligibility_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        resolver,
        "list_registered_durable_skills",
        lambda: [
            _skill(
                "academic_source_retrieval_skill",
                packs=["opentrajectory"],
                substages=["source_acquisition"],
            )
        ],
    )
    monkeypatch.setattr(
        resolver,
        "_load_registry_snapshot",
        lambda: _binding_snapshot("academic_source_retrieval_skill", substage="literature_search"),
    )

    resolved = resolver.resolve_creation_plan_capabilities(
        {
            "selectedCapabilities": [
                {
                    "kind": "skill",
                    "id": "academic_source_retrieval_skill",
                    "skillIds": ["academic_source_retrieval_skill"],
                    "substages": ["literature_search"],
                    "requiresExternalAction": True,
                }
            ]
        },
        skill_pack="sdr",
    )

    assert resolved["selectedCapabilities"][0]["resolvedSkillIds"] == [
        "academic_source_retrieval_skill"
    ]


def test_formal_artifact_judgment_and_role_conflicts_exclude_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        resolver,
        "list_registered_durable_skills",
        lambda: [_skill("formal_skill")],
    )
    for requirement in (
        {"requiredArtifactTypes": ["other.artifact"]},
        {"requiredJudgmentSupport": {"domainProfessional": True}},
        {"requiredExecutionMethodRoles": ["role:missing"]},
    ):
        with pytest.raises(
            resolver.CapabilityResolutionError,
            match="required_capability_unavailable:formal_skill",
        ):
            resolver.resolve_creation_plan_capabilities({
                "selectedCapabilities": [{
                    "kind": "skill",
                    "id": "formal_skill",
                    "skillIds": ["formal_skill"],
                    "requiresExternalAction": True,
                    **requirement,
                }],
            })


def test_set_semantics_are_order_independent_and_input_plan_is_not_mutated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skill = _skill("formal_skill")
    skill["sourceContract"]["capability_contract"]["artifact_types"] = ["b", "a"]
    monkeypatch.setattr(resolver, "list_registered_durable_skills", lambda: [skill])
    monkeypatch.setattr(
        resolver,
        "_load_registry_snapshot",
        lambda: _binding_snapshot("formal_skill"),
    )
    plan = {"selectedCapabilities": [{
        "kind": "skill",
        "id": "formal_skill",
        "skillIds": ["formal_skill"],
        "requiredArtifactTypes": ["a", "b"],
        "requiredExecutionMethodRoles": ["role:a", "role:b"],
    }]}
    original = {"selectedCapabilities": [dict(plan["selectedCapabilities"][0])]}

    resolved = resolver.resolve_creation_plan_capabilities(plan)

    assert plan == original
    definition = resolved["selectedCapabilities"][0]["resolvedSkillDefinitions"][0]
    assert definition["outputContract"]["artifactTypes"] == ["a", "b"]
    assert definition["executionMethodRoles"] == ["role:a", "role:b"]


def test_missing_binding_preserves_capability_and_marks_only_route_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        resolver, "list_registered_durable_skills", lambda: [_skill("formal_skill")]
    )
    monkeypatch.setattr(
        resolver, "_load_registry_snapshot", lambda: _binding_snapshot()
    )

    resolved = resolver.resolve_creation_plan_capabilities({
        "selectedCapabilities": [{
            "kind": "skill", "id": "formal_skill", "skillIds": ["formal_skill"],
        }],
    })

    capability = resolved["selectedCapabilities"][0]
    assert capability["resolvedSkillDefinitions"][0]["skillId"] == "formal_skill"
    assert capability["resolvedSkillBindings"] == []
    assert capability["routeAvailability"] == "unavailable"
    assert capability["routeUnavailableReason"] == "skill_binding_unavailable"


def test_empty_plan_does_not_load_skill_or_binding_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        resolver, "list_registered_durable_skills", lambda: pytest.fail("skill authority loaded")
    )
    monkeypatch.setattr(
        resolver, "_load_registry_snapshot", lambda: pytest.fail("binding authority loaded")
    )

    assert resolver.resolve_creation_plan_capabilities({"selectedCapabilities": []}) == {
        "selectedCapabilities": []
    }


@pytest.mark.filterwarnings("ignore::app.modules.registry_snapshot_repository.RegistrySnapshotAdmissionWarning")
def test_real_materials_mp_constraint_search_qualifies_and_freezes_binding() -> None:
    resolved = resolver.resolve_creation_plan_capabilities({
        "selectedCapabilities": [{
            "kind": "skill",
            "id": "materials_mp_constraint_search",
            "skillIds": ["materials_mp_constraint_search"],
            "substages": ["database_search"],
            "requiresExternalAction": True,
        }],
    })

    capability = resolved["selectedCapabilities"][0]
    assert capability["routeAvailability"] == "available"
    assert capability["resolvedSkillIds"] == ["materials_mp_constraint_search"]
    assert [row["bindingId"] for row in capability["resolvedSkillBindings"]] == [
        "bind:materials:database_search:skill:materials_mp_constraint_search"
    ]


def test_canonical_substage_remains_the_skill_eligibility_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        resolver,
        "list_registered_durable_skills",
        lambda: [
            _skill(
                "academic_source_retrieval_skill",
                packs=["opentrajectory"],
                substages=["source_acquisition"],
            )
        ],
    )

    with pytest.raises(
        resolver.CapabilityResolutionError,
        match="required_capability_unavailable:academic_source_retrieval_skill",
    ):
        resolver.resolve_creation_plan_capabilities(
            {
                "selectedCapabilities": [
                    {
                        "kind": "skill",
                        "id": "academic_source_retrieval_skill",
                        "skillIds": ["academic_source_retrieval_skill"],
                        "substages": ["question_refinement"],
                        "requiresExternalAction": True,
                    }
                ]
            },
            skill_pack="sdr",
        )
