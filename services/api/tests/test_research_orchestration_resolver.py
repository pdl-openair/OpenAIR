from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from hashlib import sha256
from importlib import import_module, util
import json
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from app.models.research_orchestration import (
    AgentRef,
    InstanceRef,
    ResearchDesignRef,
    ResearchDesignVersion,
    RegistrySnapshotRef,
    SkillRef,
    MultiAgentRecipeRef,
)


FIXTURE = Path(__file__).parent / "fixtures" / "research_resolver_cases_v1.json"
SHARED = Path(__file__).parents[3] / "packages" / "shared" / "test-fixtures" / "research-orchestration-v1.2-valid.json"


def _module(name: str):
    qualified = f"app.modules.{name}"
    assert util.find_spec(qualified) is not None, f"required T03 module is absent: {qualified}"
    return import_module(qualified)


def _design() -> ResearchDesignVersion:
    payload = json.loads(SHARED.read_text(encoding="utf-8"))["researchTask"]["researchDesignVersions"][0]
    return ResearchDesignVersion.model_validate(payload)


def _case() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _candidate_id(evaluation) -> str:
    ref = evaluation.candidate_ref
    if isinstance(ref, AgentRef):
        return f"agent:{ref.agent_id}:{ref.definition_version}"
    if isinstance(ref, SkillRef):
        return f"skill:{ref.skill_id}:{ref.definition_version}"
    assert isinstance(ref, MultiAgentRecipeRef)
    return f"recipe:{ref.recipe_id}:{ref.recipe_version}"


def _envelope(*, reverse: bool = False, fallback: bool = True, disabled: bool = False) -> dict:
    case = _case()
    definition = {
        "definitionId": case["definitionId"],
        "definitionVersion": case["definitionVersion"],
        "pack": "sdr",
        "stage": case["stageKey"],
        "substageKey": "evidence_synthesis",
        "enabled": not disabled,
        "label": "Evidence synthesis",
        "description": "Produce the bounded evidence summary.",
        "riskPolicy": {"riskClass": "low"},
    }
    definition.update(
        _module("research_orchestration_resolver").parse_definition_spec(
            "sdr",
            "specs/example.md",
            "# Evidence synthesis\n\n## Input\n\n- material_contract\n\n"
            "## Output\n\n- evidence_summary\n\n## Acceptance\n\n- deterministic_contract\n",
        )
    )
    skills = [
        {
            "skillId": skill_id,
            "definitionVersion": 1,
            "status": "ready",
            "enabled": True,
            "runtime": "claude_code",
            "skillPath": f"skills/test/{skill_id}",
            "skillType": "atomic",
            "riskPolicy": {"riskClass": "low", "source": "test"},
            "sourceContract": {"skill_id": skill_id},
            "inputContract": {"status": "known", "acceptedInputRefs": ["material_contract"]},
            "outputContract": {"status": "known", "artifactTypes": ["evidence_summary"]},
            "judgmentSupport": {"deterministic": True, "source": "test_contract"},
        }
        for skill_id in ("skill-a", "skill-b")
    ]

    def agent(agent_id: str, kind: str, scope: str, *, professional: bool) -> dict:
        return {
            "agentId": agent_id,
            "definitionVersion": 1,
            "agentKind": kind,
            "candidateScope": scope,
            "enabled": True,
            "visibility": "internal",
            "aliases": [],
            "capability": ["evidence_synthesis"],
            "notFor": [],
            "inputContract": {"required": ["material_contract"]},
            "outputContract": {
                "artifactKind": "evidence_summary" if kind == "specialist" else "progress_note",
                "artifactContract": "test_artifact_v1",
                "requiredFields": ["summary"],
            },
            "failClosedRules": ["contract_mismatch"],
            "budgetPolicy": {"defaultMode": "draft_only", "maxWallTimeSec": 30, "maxToolCalls": 0},
            "evaluator": {
                "id": "test",
                "artifactValidator": "test",
                "requiredMetadata": ["source"],
                "qualitySignals": ["traceable"],
            },
            "allowedProviders": ["provider-a"],
            "defaultProvider": "provider-a",
            "toolRequirements": {},
            "judgmentSupport": {"domainProfessional": professional, "humanAuthority": False},
        }

    agents = [
        agent("specialist-a", "specialist", "explicit_mappings", professional=True),
        agent("specialist-b", "specialist", "explicit_mappings", professional=False),
    ]
    if fallback:
        agents.append(agent("general-research", "general_research", "all_enabled_substages", professional=False))
    skill_bindings = [
        {
            "bindingId": f"bind:sdr:evidence_synthesis:skill:{skill_id}",
            "bindingVersion": 1,
            "definitionId": case["definitionId"],
            "definitionVersion": 1,
            "skillId": skill_id,
            "skillDefinitionVersion": 1,
            "supportedRole": "tool",
            "enabled": True,
            "sourceAuthority": "skill_binding_source",
        }
        for skill_id in ("skill-a", "skill-b")
    ]
    agent_bindings = [
        {
            "bindingId": "bind:sdr:evidence_synthesis:agent:specialist-a:primary",
            "bindingVersion": 1,
            "definitionId": case["definitionId"],
            "definitionVersion": 1,
            "agentId": "specialist-a",
            "agentDefinitionVersion": 1,
            "supportedRole": "primary",
            "enabled": True,
            "sourceAuthority": "agent_registry_mapping",
            "artifactContract": "evidence_summary_v1",
        },
        {
            "bindingId": "bind:sdr:evidence_synthesis:agent:specialist-b:support",
            "bindingVersion": 1,
            "definitionId": case["definitionId"],
            "definitionVersion": 1,
            "agentId": "specialist-b",
            "agentDefinitionVersion": 1,
            "supportedRole": "support",
            "enabled": True,
            "sourceAuthority": "agent_registry_mapping",
            "artifactContract": "evidence_summary_v1",
        },
    ]
    if fallback and not disabled:
        agent_bindings.append(
            {
                "bindingId": "bind:sdr:evidence_synthesis:agent:general-research:primary",
                "bindingVersion": 1,
                "definitionId": case["definitionId"],
                "definitionVersion": 1,
                "agentId": "general-research",
                "agentDefinitionVersion": 1,
                "supportedRole": "primary",
                "enabled": True,
                "sourceAuthority": "general_fallback_policy",
            }
        )
    recipe = {
        "recipeId": "producer-support",
        "recipeVersion": 1,
        "substageDefinitionId": case["definitionId"],
        "substageDefinitionVersion": 1,
        "applicabilityRuleRefs": ["rule:substage-match"],
        "assignmentTemplates": [
            {
                "templateKey": "producer",
                "role": "primary",
                "requiredSupportedRole": "primary",
                "objectiveTemplate": "Produce the bounded result.",
                "dependencyTemplateKeys": [],
            },
            {
                "templateKey": "support",
                "role": "support",
                "requiredSupportedRole": "support",
                "objectiveTemplate": "Support the bounded result.",
                "dependencyTemplateKeys": ["producer"],
            },
        ],
        "completionPolicyTemplate": {
            "requiredTemplateKeys": ["producer", "support"],
            "successMode": "all_required",
            "reviewerTemplateKeys": [],
            "reviewRequirement": "none",
            "partialSuccessAllowed": False,
            "onReviewerReject": "new_revision_decision",
        },
        "resultAssemblyPolicyTemplate": {
            "mode": "artifact_set",
            "assemblerTemplateKey": None,
            "finalArtifactRequirements": [
                {
                    "artifactType": "evidence_summary",
                    "minimumCount": 1,
                    "sourceTemplateKeys": ["producer", "support"],
                }
            ],
        },
        "enabled": True,
    }
    envelope = {
        "schemaVersion": 1,
        "definitions": {
            "packs": [{"pack": "sdr", "definitionVersion": 1, "source": {"key": "sdr"}}],
            "substages": [definition],
            "skills": skills,
            "agents": agents,
        },
        "bindings": {
            "skills": skill_bindings,
            "agents": agent_bindings,
            "agentSkills": [
                {
                    "bindingId": "grant:specialist-a:skill-a",
                    "bindingVersion": 1,
                    "agentId": "specialist-a",
                    "agentDefinitionVersion": 1,
                    "skillId": "skill-a",
                    "skillDefinitionVersion": 1,
                    "enabled": True,
                    "sourceAuthority": "agent_registry_skill_grant",
                }
            ],
        },
        "recipes": [recipe],
        "staticPolicies": {
            "policyVersion": "registry-policy.v1",
            "unknownCanonicalRiskClass": "high",
            "unboundSkillPolicy": "not_applicable",
            "providerReadinessSource": "resolution_facts_only",
            "systemRecoveryAuthorities": [],
        },
    }
    if reverse:
        for value in envelope["definitions"].values():
            value.reverse()
        for value in envelope["bindings"].values():
            value.reverse()
        envelope["recipes"].reverse()
    return envelope


_EXECUTION_METHOD_ID = "test_evidence_pipeline"


def _execution_method_envelope(
    *,
    final_output: str = "evidence_summary",
    final_judgment: str = "domain_professional",
    final_professional_supported: bool = True,
) -> dict:
    envelope = _envelope()
    method = {
        "methodId": _EXECUTION_METHOD_ID,
        "methodVersion": 1,
        "semantics": "role_graph_v1",
        "roles": [
            {
                "roleId": "retrieval",
                "requirement": "required",
                "dependsOn": [],
                "capabilityKinds": ["skill"],
                "releasePolicy": {"kind": "always"},
                "failurePolicy": "fail_closed",
                "parallelism": "single",
                "qualificationContract": {
                    "objective": "Retrieve candidate research records.",
                    "requiredInputRefs": ["material_contract"],
                    "outputArtifactTypes": ["candidate_records"],
                    "judgmentRequirement": "none",
                },
            },
            {
                "roleId": "reading",
                "requirement": "required",
                "dependsOn": ["retrieval"],
                "capabilityKinds": ["skill"],
                "releasePolicy": {"kind": "always"},
                "failurePolicy": "fail_closed",
                "parallelism": "single",
                "qualificationContract": {
                    "objective": "Read candidate records for usable research content.",
                    "requiredInputRefs": ["material_contract"],
                    "outputArtifactTypes": ["read_records"],
                    "judgmentRequirement": "none",
                },
            },
            {
                "roleId": "evidence",
                "requirement": "required",
                "dependsOn": ["reading"],
                "capabilityKinds": ["agent"],
                "releasePolicy": {"kind": "always"},
                "failurePolicy": "fail_closed",
                "parallelism": "single",
                "qualificationContract": {
                    "objective": "Assemble traceable evidence records.",
                    "requiredInputRefs": ["material_contract"],
                    "outputArtifactTypes": ["evidence_records"],
                    "judgmentRequirement": "none",
                },
            },
            {
                "roleId": "final_judgment",
                "requirement": "required",
                "dependsOn": ["evidence"],
                "capabilityKinds": ["agent"],
                "releasePolicy": {"kind": "always"},
                "failurePolicy": "fail_closed",
                "parallelism": "single",
                "qualificationContract": {
                    "objective": "Produce the final evidence judgment.",
                    "requiredInputRefs": ["material_contract"],
                    "outputArtifactTypes": [final_output],
                    "judgmentRequirement": final_judgment,
                },
            },
        ],
        "completionRoleIds": ["final_judgment"],
        "stopConditions": [],
        "qualityConstraints": [],
    }
    spec = (
        "# Evidence synthesis\n\n"
        "## Input\n\n- material_contract\n\n"
        "## Output\n\n- evidence_summary\n\n"
        "## Acceptance\n\n- deterministic_contract\n\n"
        "## Execution Method\n\n```yaml\n"
        + yaml.safe_dump(method, sort_keys=False, allow_unicode=True)
        + "```\n"
    )
    definition = envelope["definitions"]["substages"][0]
    definition.update(
        _module("research_orchestration_resolver").parse_definition_spec(
            "sdr", "specs/example.md", spec
        )
    )
    envelope["recipes"] = []

    skill_by_id = {row["skillId"]: row for row in envelope["definitions"]["skills"]}
    skill_by_id["skill-a"]["outputContract"]["artifactTypes"] = ["candidate_records"]
    skill_by_id["skill-a"]["executionMethodRoles"] = [f"{_EXECUTION_METHOD_ID}:retrieval"]
    skill_by_id["skill-b"]["outputContract"]["artifactTypes"] = ["read_records"]
    skill_by_id["skill-b"]["executionMethodRoles"] = [f"{_EXECUTION_METHOD_ID}:reading"]

    agent_by_id = {row["agentId"]: row for row in envelope["definitions"]["agents"]}
    agent_by_id["specialist-b"]["outputContract"]["artifactKind"] = "evidence_records"
    agent_by_id["specialist-b"]["executionMethodRoles"] = [f"{_EXECUTION_METHOD_ID}:evidence"]
    agent_by_id["specialist-a"]["outputContract"]["artifactKind"] = "evidence_summary"
    agent_by_id["specialist-a"]["executionMethodRoles"] = [f"{_EXECUTION_METHOD_ID}:final_judgment"]
    agent_by_id["specialist-a"]["judgmentSupport"]["domainProfessional"] = final_professional_supported
    return envelope


def _execution_method_facts(envelope: dict):
    resolver_module = _module("research_orchestration_resolver")
    candidate_ids = {
        f"skill:{row['skillId']}:{row['skillDefinitionVersion']}"
        for row in envelope["bindings"]["skills"]
        if row["enabled"]
    } | {
        f"agent:{row['agentId']}:{row['agentDefinitionVersion']}"
        for row in envelope["bindings"]["agents"]
        if row["enabled"]
    }
    return resolver_module.ResolutionFacts(
        runtimeReadiness={candidate_id: True for candidate_id in candidate_ids},
        providerReadiness={"provider-a": True},
        authorizedCandidateRefs=candidate_ids,
        availableInputRefs={"material_contract"},
        applicableRuleRefs=set(),
        riskAllowed=True,
        budgetAvailable=True,
        policyVersion="registry-policy.v1",
    )


def _archived(envelope: dict | None = None):
    repository_module = _module("registry_snapshot_repository")
    snapshot = repository_module.snapshot_from_envelope(envelope or _envelope())
    repository = repository_module.InMemoryRegistrySnapshotRepository()
    repository.put(snapshot)
    return repository, snapshot


def _facts(
    *, runtime: bool = True, provider: bool = True, authorized: bool = True,
    satisfied_human_gate_refs: set[str] | None = None,
):
    resolver_module = _module("research_orchestration_resolver")
    candidate_keys = set(_case()["expectedCandidateIds"])
    return resolver_module.ResolutionFacts(
        runtimeReadiness={key: runtime for key in candidate_keys if not key.startswith("recipe:")},
        providerReadiness={"provider-a": provider},
        authorizedCandidateRefs=candidate_keys if authorized else set(),
        availableInputRefs={"material_contract"},
        applicableRuleRefs={"rule:substage-match"},
        riskAllowed=True,
        budgetAvailable=True,
        policyVersion="registry-policy.v1",
        satisfiedHumanGateRefs=satisfied_human_gate_refs or set(),
    )


def _resolve(envelope: dict | None = None, *, facts=None, design: ResearchDesignVersion | None = None):
    resolver_module = _module("research_orchestration_resolver")
    repository, snapshot = _archived(envelope)
    design = design or _design()
    resolver = resolver_module.ResearchOrchestrationResolver(repository)
    result = resolver.resolve(
        design=design,
        designRef=ResearchDesignRef(researchDesignId=design.research_design_id, version=design.version),
        instanceRef=InstanceRef(substageInstanceId="sub_contract_full", instanceRevision=1),
        registrySnapshotRef=RegistrySnapshotRef(schemaVersion=1, contentHash=snapshot.content_hash),
        facts=facts or _facts(),
    )
    return result, snapshot


def test_registry_source_reorder_preserves_snapshot_hash_and_resolution() -> None:
    repository_module = _module("registry_snapshot_repository")
    first = repository_module.snapshot_from_envelope(_envelope())
    second = repository_module.snapshot_from_envelope(_envelope(reverse=True))
    assert first.content_hash == second.content_hash
    assert first.canonical_payload == second.canonical_payload
    assert _resolve(_envelope())[0].model_dump(mode="json") == _resolve(_envelope(reverse=True))[0].model_dump(mode="json")


def test_production_definition_rows_freeze_external_action_contracts() -> None:
    resolver_module = _module("research_orchestration_resolver")

    _packs, definitions = resolver_module._definition_rows()

    assert definitions[("opentrajectory", "source_acquisition")][
        "externalActionContract"
    ] == {
        "status": "known",
        "requiredActionKinds": ["external_read"],
    }
    assert definitions[("materials", "database_search")][
        "externalActionContract"
    ] == {
        "status": "known",
        "requiredActionKinds": ["external_read"],
    }
    assert definitions[("materials", "stability_verification")][
        "externalActionContract"
    ] == {
        "status": "known",
        "requiredActionKinds": [],
    }
    assert definitions[("sdr", "question_refinement")][
        "externalActionContract"
    ] == {
        "status": "unknown",
        "requiredActionKinds": [],
    }


def test_external_action_contract_is_validated_and_canonicalized() -> None:
    repository_module = _module("registry_snapshot_repository")
    first = _envelope()
    first["definitions"]["substages"][0]["externalActionContract"] = {
        "status": "known",
        "requiredActionKinds": ["external_read", "external_compute"],
    }
    second = deepcopy(first)
    second["definitions"]["substages"][0]["externalActionContract"][
        "requiredActionKinds"
    ].reverse()

    first_snapshot = repository_module.snapshot_from_envelope(first)
    second_snapshot = repository_module.snapshot_from_envelope(second)

    assert first_snapshot.content_hash == second_snapshot.content_hash
    action_contract = first_snapshot.envelope["definitions"]["substages"][0][
        "externalActionContract"
    ]
    assert action_contract == {
        "status": "known",
        "requiredActionKinds": ["external_compute", "external_read"],
    }


def test_external_action_contract_rejects_unknown_kind() -> None:
    repository_module = _module("registry_snapshot_repository")
    envelope = _envelope()
    envelope["definitions"]["substages"][0]["externalActionContract"] = {
        "status": "known",
        "requiredActionKinds": ["unknown_action"],
    }

    with pytest.raises(
        repository_module.RegistrySnapshotIntegrityError,
        match="externalActionContract is invalid",
    ):
        repository_module.snapshot_from_envelope(envelope)


def test_unknown_external_action_contract_cannot_claim_actions() -> None:
    repository_module = _module("registry_snapshot_repository")
    envelope = _envelope()
    envelope["definitions"]["substages"][0]["externalActionContract"] = {
        "status": "unknown",
        "requiredActionKinds": ["external_read"],
    }

    with pytest.raises(
        repository_module.RegistrySnapshotIntegrityError,
        match="externalActionContract is invalid",
    ):
        repository_module.snapshot_from_envelope(envelope)


def test_ordered_recipe_topology_is_not_globally_sorted() -> None:
    repository_module = _module("registry_snapshot_repository")
    original = _envelope()
    reordered = deepcopy(original)
    reordered["recipes"][0]["assignmentTemplates"].reverse()
    assert repository_module.snapshot_from_envelope(original).content_hash != repository_module.snapshot_from_envelope(reordered).content_hash


def test_production_snapshot_has_unique_authority_identity_and_is_repeatable() -> None:
    resolver_module = _module("research_orchestration_resolver")
    first = resolver_module.build_registry_snapshot()
    second = resolver_module.build_registry_snapshot()
    assert first.content_hash == second.content_hash
    envelope = first.envelope

    definition_identity_fields = {
        "agents": ("agentId", "definitionVersion"),
        "packs": ("pack", "definitionVersion"),
        "skills": ("skillId", "definitionVersion"),
        "substages": ("definitionId", "definitionVersion"),
    }
    for authority, fields in definition_identity_fields.items():
        rows = envelope["definitions"][authority]
        identities = [tuple(row[field] for field in fields) for row in rows]
        assert rows
        assert len(identities) == len(set(identities))

    enabled_definition_refs = {
        (row["definitionId"], row["definitionVersion"])
        for row in envelope["definitions"]["substages"]
        if row["enabled"]
    }
    fallback_bindings = [
        row
        for row in envelope["bindings"]["agents"]
        if row["sourceAuthority"] == "general_fallback_policy"
    ]
    fallback_definition_refs = {
        (row["definitionId"], row["definitionVersion"])
        for row in fallback_bindings
    }
    assert len(fallback_bindings) == len(fallback_definition_refs)
    assert fallback_definition_refs == enabled_definition_refs


def test_production_bindings_have_unique_resolvable_identity_and_propagate_enablement() -> None:
    resolver_module = _module("research_orchestration_resolver")
    snapshot = resolver_module.build_registry_snapshot().envelope
    definitions = {
        (row["definitionId"], row["definitionVersion"]): row["enabled"]
        for row in snapshot["definitions"]["substages"]
    }
    agents = {
        (row["agentId"], row["definitionVersion"]): row["enabled"]
        for row in snapshot["definitions"]["agents"]
    }
    skills = {
        (row["skillId"], row["definitionVersion"]): row["enabled"]
        for row in snapshot["definitions"]["skills"]
    }
    bindings = [
        row
        for authority_bindings in snapshot["bindings"].values()
        for row in authority_bindings
    ]
    binding_identities = [
        (row["bindingId"], row["bindingVersion"])
        for row in bindings
    ]
    assert bindings
    assert len(binding_identities) == len(set(binding_identities))

    for binding in snapshot["bindings"]["agentSkills"]:
        agent_enabled = agents[
            (binding["agentId"], binding["agentDefinitionVersion"])
        ]
        skill_enabled = skills[
            (binding["skillId"], binding["skillDefinitionVersion"])
        ]
        assert not binding["enabled"] or (agent_enabled and skill_enabled)

    for binding in snapshot["bindings"]["agents"]:
        definition_enabled = definitions[
            (binding["definitionId"], binding["definitionVersion"])
        ]
        agent_enabled = agents[
            (binding["agentId"], binding["agentDefinitionVersion"])
        ]
        assert not binding["enabled"] or (definition_enabled and agent_enabled)

    for binding in snapshot["bindings"]["skills"]:
        definition_enabled = definitions[
            (binding["definitionId"], binding["definitionVersion"])
        ]
        skill_enabled = skills[
            (binding["skillId"], binding["skillDefinitionVersion"])
        ]
        assert not binding["enabled"] or (definition_enabled and skill_enabled)


def test_repository_recomputes_hash_on_put_get_and_keeps_history() -> None:
    repository_module = _module("registry_snapshot_repository")
    repository = repository_module.InMemoryRegistrySnapshotRepository()
    old = repository_module.snapshot_from_envelope(_envelope())
    repository.put(old)
    changed_envelope = _envelope()
    changed_envelope["definitions"]["agents"][0]["capability"].append("current_registry_capability")
    new = repository_module.snapshot_from_envelope(changed_envelope)
    repository.put(new)
    assert repository.get(old.content_hash).canonical_payload == old.canonical_payload
    assert repository.get(new.content_hash).canonical_payload == new.canonical_payload

    forged = deepcopy(old)
    object.__setattr__(forged, "content_hash", new.content_hash)
    with pytest.raises(repository_module.RegistrySnapshotIntegrityError):
        repository.put(forged)
    repository.corrupt_for_test(old.content_hash, changed_envelope)
    with pytest.raises(repository_module.RegistrySnapshotIntegrityError):
        repository.get(old.content_hash)


def test_resolver_requires_archived_snapshot_and_exact_design_instance_identity() -> None:
    resolver_module = _module("research_orchestration_resolver")
    repository_module = _module("registry_snapshot_repository")
    snapshot = repository_module.snapshot_from_envelope(_envelope())
    repository = repository_module.InMemoryRegistrySnapshotRepository()
    resolver = resolver_module.ResearchOrchestrationResolver(repository)
    design = _design()
    kwargs = {
        "design": design,
        "designRef": ResearchDesignRef(researchDesignId=design.research_design_id, version=design.version),
        "instanceRef": InstanceRef(substageInstanceId="sub_contract_full", instanceRevision=1),
        "registrySnapshotRef": RegistrySnapshotRef(schemaVersion=1, contentHash=snapshot.content_hash),
        "facts": _facts(),
    }
    with pytest.raises(repository_module.RegistrySnapshotNotFoundError):
        resolver.resolve(**kwargs)
    repository.put(snapshot)
    kwargs["designRef"] = ResearchDesignRef(researchDesignId="other-design", version=1)
    with pytest.raises(resolver_module.ResolutionRejectedError, match="design_ref_mismatch"):
        resolver.resolve(**kwargs)
    kwargs["designRef"] = ResearchDesignRef(researchDesignId=design.research_design_id, version=design.version)
    kwargs["instanceRef"] = InstanceRef(substageInstanceId="same-id-other-design", instanceRevision=1)
    with pytest.raises(resolver_module.ResolutionRejectedError, match="unknown_instance"):
        resolver.resolve(**kwargs)


def test_all_candidates_are_evaluated_before_preference_and_equivalents_stay_equal() -> None:
    result, _snapshot = _resolve()
    candidate_ids = [_candidate_id(row) for row in result.candidate_evaluations]
    assert candidate_ids == sorted(_case()["expectedCandidateIds"])
    by_id = {_candidate_id(row): row for row in result.candidate_evaluations}
    first, second = _case()["equivalentCandidates"]
    assert by_id[first].preference == by_id[second].preference
    serialized = result.model_dump(mode="json")
    assert not any(key in json.dumps(serialized) for key in ('"rank"', '"tieGroup"', '"winner"'))


def test_unauthorized_agent_skill_grants_are_not_inferred_from_name_or_substage() -> None:
    _result, snapshot = _resolve()
    grants = snapshot.envelope["bindings"]["agentSkills"]
    assert [(row["agentId"], row["skillId"]) for row in grants] == [("specialist-a", "skill-a")]
    assert all(not (row["agentId"] == "specialist-b" and row["skillId"] == "skill-b") for row in grants)


def test_ready_candidates_remain_discovered_but_are_not_executable_without_runtime_authority() -> None:
    result, _snapshot = _resolve(facts=_facts(runtime=False, provider=False, authorized=False))
    assert result.candidate_evaluations
    for row in result.candidate_evaluations:
        assert row.preference.executable_now is False
        blockers = set(row.preference.execution_blocker_codes)
        assert {"authorization_denied", "runtime_unavailable"}.issubset(blockers)
    fallback = next(row for row in result.candidate_evaluations if _candidate_id(row) == _case()["fallbackCandidate"])
    assert "provider_unavailable" in fallback.preference.execution_blocker_codes


def test_no_top_k_pretrim_hides_completion_candidate() -> None:
    result, _snapshot = _resolve()
    by_id = {_candidate_id(row): row for row in result.candidate_evaluations}
    completion = by_id[_case()["completionCandidate"]]
    assert completion.preference.completion_coverage == "completion_capable"
    assert len(result.candidate_evaluations) == len(_case()["expectedCandidateIds"])


def test_only_registered_recipe_becomes_a_multi_agent_candidate() -> None:
    result, _snapshot = _resolve()
    recipes = [row for row in result.candidate_evaluations if _candidate_id(row).startswith("recipe:")]
    assert [_candidate_id(row) for row in recipes] == ["recipe:producer-support:1"]


def test_historical_snapshot_resolution_survives_current_registry_change() -> None:
    resolver_module = _module("research_orchestration_resolver")
    repository_module = _module("registry_snapshot_repository")
    repository = repository_module.InMemoryRegistrySnapshotRepository()
    old = repository_module.snapshot_from_envelope(_envelope())
    changed = _envelope()
    current_only = deepcopy(changed["definitions"]["skills"][0])
    current_only["skillId"] = "skill-current-only"
    current_only["skillPath"] = "skills/test/current-only"
    current_only["sourceContract"] = {"skill_id": "skill-current-only"}
    changed["definitions"]["skills"].append(current_only)
    new = repository_module.snapshot_from_envelope(changed)
    repository.put(old)
    repository.put(new)
    design = _design()
    result = resolver_module.ResearchOrchestrationResolver(repository).resolve(
        design=design,
        designRef=ResearchDesignRef(researchDesignId=design.research_design_id, version=1),
        instanceRef=InstanceRef(substageInstanceId="sub_contract_full", instanceRevision=1),
        registrySnapshotRef=RegistrySnapshotRef(schemaVersion=1, contentHash=old.content_hash),
        facts=_facts(),
    )
    assert [_candidate_id(row) for row in result.candidate_evaluations] == sorted(_case()["expectedCandidateIds"])


def test_provider_readiness_changes_resolution_not_snapshot_hash() -> None:
    executable, snapshot = _resolve(facts=_facts(provider=True))
    blocked, same_snapshot = _resolve(facts=_facts(provider=False))
    assert snapshot.content_hash == same_snapshot.content_hash
    assert executable.model_dump(mode="json") != blocked.model_dump(mode="json")
    changed_policy = _envelope()
    changed_policy["definitions"]["agents"][0]["allowedProviders"] = ["provider-b"]
    changed_policy["definitions"]["agents"][0]["defaultProvider"] = "provider-b"
    repository_module = _module("registry_snapshot_repository")
    assert repository_module.snapshot_from_envelope(changed_policy).content_hash != snapshot.content_hash


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        ("unknown", "definition_unknown"),
        ("wrong_stage", "definition_stage_mismatch"),
        ("wrong_version", "definition_version_mismatch"),
        ("missing_fallback", "general_fallback_missing"),
    ],
)
def test_definition_and_fallback_invariants_fail_closed(mutation: str, code: str) -> None:
    resolver_module = _module("research_orchestration_resolver")
    repository_module = _module("registry_snapshot_repository")
    envelope = _envelope(fallback=mutation != "missing_fallback", disabled=mutation == "disabled")
    if mutation == "wrong_stage":
        envelope["definitions"]["substages"][0]["stage"] = "problem_modeling"
    if mutation == "unknown":
        envelope["definitions"]["substages"][0]["definitionId"] = "sdr:other_definition"
    if mutation == "wrong_version":
        envelope["definitions"]["substages"][0]["definitionVersion"] = 2
    if mutation in {"unknown", "wrong_version", "missing_fallback"}:
        with pytest.raises(repository_module.RegistrySnapshotIntegrityError):
            repository_module.snapshot_from_envelope(envelope)
    else:
        with pytest.raises(resolver_module.ResolutionRejectedError, match=code):
            _resolve(envelope)


def test_consistency_audit_inputs_cannot_change_hash_or_fill_authority() -> None:
    resolver_module = _module("research_orchestration_resolver")
    first = resolver_module.build_registry_snapshot(consistencyAudit={"skill-a": "matches"})
    second = resolver_module.build_registry_snapshot(consistencyAudit={"skill-a": "changed-noncanonical"})
    assert first.content_hash == second.content_hash
    with pytest.raises(resolver_module.RegistryAuthorityError, match="consistency_audit_conflict"):
        resolver_module.build_registry_snapshot(
            consistencyAudit={"skill-a": "canonical_conflict"},
        )


def test_production_definitions_archive_full_normalized_spec_contracts() -> None:
    resolver_module = _module("research_orchestration_resolver")
    snapshot = resolver_module.build_registry_snapshot()
    definitions = snapshot.envelope["definitions"]["substages"]
    definition_identities = [
        (definition["definitionId"], definition["definitionVersion"])
        for definition in definitions
    ]
    assert definitions
    assert len(definition_identities) == len(set(definition_identities))
    for definition in definitions:
        source = definition["specSource"]
        assert source["content"]
        assert "\r" not in source["content"]
        assert set(source["parsed"]) == {
            "format", "parserVersion", "headings", "sections", "tables", "checklists", "yamlDocument"
        }
        assert source["repoPath"].startswith(f"schemas/{definition['pack']}/specs/")
        assert source["tracked"] is True
        assert definition["inputContract"]["status"] in {"known", "unknown"}
        assert definition["outputContract"]["status"] in {"known", "unknown"}
        assert definition["acceptanceContract"]["status"] in {"known", "unknown"}


def test_production_snapshot_freezes_question_artifact_and_independent_review_schemas() -> None:
    snapshot = _module("research_orchestration_resolver").build_registry_snapshot()
    agents = {
        row["agentId"]: row for row in snapshot.envelope["definitions"]["agents"]
    }
    producer = agents["research_question_refinement"]["outputContract"]["frozenArtifactContract"]
    reviewer = agents["result_reliability_audit"]["reviewContract"]["frozenArtifactContract"]

    assert producer["artifactContract"] == "research_question_refinement_artifact_v1"
    assert reviewer["artifactContract"] == "independent_result_review_v1"
    assert producer["artifactSchemaDigest"].startswith("sha256:") is False
    assert reviewer["artifactSchemaDigest"].startswith("sha256:") is False
    assert producer["artifactSchema"]["contractId"] == producer["artifactContract"]
    assert reviewer["artifactSchema"]["contractId"] == reviewer["artifactContract"]


def test_spec_content_changes_snapshot_hash_but_line_endings_do_not() -> None:
    resolver_module = _module("research_orchestration_resolver")
    repository_module = _module("registry_snapshot_repository")
    parsed_lf = resolver_module.parse_definition_spec("sdr", "specs/example.md", "# Spec\n\n## Input\n\n- material_contract\n")
    parsed_crlf = resolver_module.parse_definition_spec(
        "sdr", "specs/example.md", "# Spec\r\n\r\n## Input\r\n\r\n- material_contract\r\n"
    )
    assert parsed_lf == parsed_crlf
    first = _envelope()
    first["definitions"]["substages"][0].update(parsed_lf)
    second = deepcopy(first)
    second["definitions"]["substages"][0].update(
        resolver_module.parse_definition_spec("sdr", "specs/example.md", "# Changed\n\n## Input\n\n- material_contract\n")
    )
    assert repository_module.snapshot_from_envelope(first).content_hash != repository_module.snapshot_from_envelope(second).content_hash


def test_validated_snapshot_helper_rejects_noncanonical_or_ambiguous_spec_identity() -> None:
    resolver_module = _module("research_orchestration_resolver")
    repository_module = _module("registry_snapshot_repository")
    with pytest.raises(repository_module.RegistrySnapshotIntegrityError, match="not canonical"):
        resolver_module.parse_definition_spec("sdr", r"specs\\example.md", "# Spec\n")

    envelope = _envelope()
    duplicate = deepcopy(envelope["definitions"]["substages"][0])
    duplicate.update({"definitionId": "sdr:duplicate", "substageKey": "duplicate", "enabled": False})
    envelope["definitions"]["substages"].append(duplicate)
    with pytest.raises(repository_module.RegistrySnapshotIntegrityError, match="repoPath is ambiguous"):
        repository_module.snapshot_from_envelope(envelope)


@pytest.mark.parametrize(
    "ref,code",
    [
        ("C:/escape.md", "absolute_spec_ref"),
        ("../escape.md", "invalid_spec_ref"),
        ("nested/../../escape.md", "invalid_spec_ref"),
        (r"specs\\example.md", "invalid_spec_ref"),
        ("missing.md", "missing_spec"),
    ],
)
def test_canonical_spec_path_authority_fails_closed(ref: str, code: str) -> None:
    resolver_module = _module("research_orchestration_resolver")
    with pytest.raises(resolver_module.RegistryAuthorityError, match=code):
        resolver_module.load_definition_spec("sdr", ref)


def test_canonical_spec_rejects_untracked_non_utf8_and_symlink(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    resolver_module = _module("research_orchestration_resolver")
    specs = tmp_path / "schemas" / "sdr" / "specs"
    specs.mkdir(parents=True)
    untracked = specs / "untracked.md"
    untracked.write_text("# Untracked\n", encoding="utf-8")
    with pytest.raises(resolver_module.RegistryAuthorityError, match="untracked_spec"):
        resolver_module.load_definition_spec("sdr", "untracked.md", tracked_paths=set(), root=tmp_path)

    invalid = specs / "invalid.md"
    invalid.write_bytes(b"\xff\xfe")
    with pytest.raises(resolver_module.RegistryAuthorityError, match="non_utf8_spec"):
        resolver_module.load_definition_spec(
            "sdr", "invalid.md", tracked_paths={"schemas/sdr/specs/invalid.md"}, root=tmp_path
        )

    target = specs / "target.md"
    target.write_text("# Target\n", encoding="utf-8")
    link = specs / "link.md"
    link.write_text("# Link placeholder\n", encoding="utf-8")
    real_resolve = Path.resolve

    def resolve_with_link(path: Path, strict: bool = False) -> Path:
        if path == link:
            return target.absolute()
        return real_resolve(path, strict=strict)

    monkeypatch.setattr(Path, "resolve", resolve_with_link)
    with pytest.raises(resolver_module.RegistryAuthorityError, match="symlink_spec_ref"):
        resolver_module.load_definition_spec(
            "sdr", "link.md", tracked_paths={"schemas/sdr/specs/link.md"}, root=tmp_path
        )


def test_production_snapshot_builder_rejects_source_payload_injection() -> None:
    resolver_module = _module("research_orchestration_resolver")
    with pytest.raises(TypeError):
        resolver_module.build_registry_snapshot(sourcePayload=_envelope())


def test_production_snapshot_accepts_declared_system_recovery_authorities() -> None:
    snapshot = _module("research_orchestration_resolver").build_registry_snapshot()
    authorities = snapshot.envelope["staticPolicies"]["systemRecoveryAuthorities"]
    assert authorities == [{
        "reasonCode": "semantic_material_insufficient",
        "definitionId": "opentrajectory:progressive_reading",
        "objective": "Recover source material needed by the blocked research step.",
        "expectedArtifactTypes": ["source_materials"],
    }]


def _legacy_snapshot(envelope: dict):
    repository_module = _module("registry_snapshot_repository")
    legacy = deepcopy(envelope)
    legacy["staticPolicies"].pop("systemRecoveryAuthorities")
    payload = repository_module.canonicalize_envelope(
        legacy,
        allow_legacy_static_policies=True,
    )
    return repository_module.RegistrySnapshot(
        schema_version=1,
        content_hash=f"sha256:{sha256(payload).hexdigest()}",
        canonical_payload=payload,
    )


def test_legacy_snapshot_is_hash_verified_before_exact_read_only_runtime_adapter() -> None:
    repository_module = _module("registry_snapshot_repository")
    legacy = _legacy_snapshot(_envelope())
    archive = repository_module.InMemoryRegistrySnapshotRepository()
    archive._rows[legacy.content_hash] = (legacy.schema_version, legacy.canonical_payload)
    before = dict(archive._rows)

    restored = archive.get(legacy.content_hash)

    assert restored.content_hash == legacy.content_hash
    assert restored.canonical_payload == legacy.canonical_payload
    assert restored.runtime_payload is not None
    assert restored.envelope["staticPolicies"]["systemRecoveryAuthorities"] == [{
        "reasonCode": "semantic_material_insufficient",
        "definitionId": "opentrajectory:progressive_reading",
        "objective": "Recover source material needed by the blocked research step.",
        "expectedArtifactTypes": ["source_materials"],
    }]
    assert archive._rows == before

    tampered = json.loads(legacy.canonical_payload)
    tampered["definitions"]["substages"][0]["label"] = "tampered"
    archive._rows[legacy.content_hash] = (
        1,
        repository_module.canonicalize_envelope(
            tampered,
            allow_legacy_static_policies=True,
        ),
    )
    with pytest.raises(repository_module.RegistrySnapshotIntegrityError, match="hash/schema/payload mismatch"):
        archive.get(legacy.content_hash)


def test_legacy_snapshot_postgres_read_does_not_write_archive() -> None:
    repository_module = _module("registry_snapshot_repository")
    legacy = _legacy_snapshot(_envelope())
    statements: list[str] = []

    class Result:
        def fetchone(self):
            return {
                "schema_version": legacy.schema_version,
                "content_hash": legacy.content_hash,
                "payload": json.loads(legacy.canonical_payload),
            }

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def execute(self, statement, _params):
            statements.append(statement)
            assert statement.lstrip().startswith("SELECT")
            return Result()

    restored = repository_module.RegistrySnapshotRepository(lambda: Connection()).get(
        legacy.content_hash
    )

    assert restored.content_hash == legacy.content_hash
    assert len(statements) == 1


def test_current_snapshot_shape_remains_strict_and_writer_never_emits_legacy_shape() -> None:
    repository_module = _module("registry_snapshot_repository")
    snapshot = repository_module.snapshot_from_envelope(_envelope())
    archive = repository_module.InMemoryRegistrySnapshotRepository()
    archive.put(snapshot)
    assert archive.get(snapshot.content_hash).runtime_payload is None

    legacy = _envelope()
    legacy["staticPolicies"].pop("systemRecoveryAuthorities")
    with pytest.raises(repository_module.RegistrySnapshotIntegrityError, match="staticPolicies shape"):
        repository_module.snapshot_from_envelope(legacy)

    for mutation in (
        lambda envelope: envelope["staticPolicies"].pop("providerReadinessSource"),
        lambda envelope: envelope["staticPolicies"].__setitem__("policyVersion", 1),
        lambda envelope: envelope["staticPolicies"].__setitem__("systemRecoveryAuthorities", "invalid"),
        lambda envelope: envelope["staticPolicies"].__setitem__("unexpected", True),
    ):
        malformed = _envelope()
        mutation(malformed)
        with pytest.raises(repository_module.RegistrySnapshotIntegrityError):
            repository_module.snapshot_from_envelope(malformed)


@pytest.mark.parametrize(
    "mutation",
    ["dangling_binding", "invalid_role", "string_enabled", "missing_version", "unknown_source", "invalid_policy"],
)
def test_snapshot_archive_rejects_invalid_authority(mutation: str) -> None:
    repository_module = _module("registry_snapshot_repository")
    envelope = _envelope()
    if mutation == "dangling_binding":
        envelope["bindings"]["skills"][0]["definitionId"] = "sdr:missing"
    elif mutation == "invalid_role":
        envelope["bindings"]["skills"][0]["supportedRole"] = "primary-ish"
    elif mutation == "string_enabled":
        envelope["definitions"]["skills"][0]["enabled"] = "false"
    elif mutation == "missing_version":
        envelope["definitions"]["skills"][0].pop("definitionVersion")
    elif mutation == "unknown_source":
        envelope["bindings"]["agents"][0]["sourceAuthority"] = "scanner"
    else:
        envelope["staticPolicies"]["arbitrary"] = True
    with pytest.raises(repository_module.RegistrySnapshotIntegrityError):
        repository_module.snapshot_from_envelope(envelope)




def test_resolution_facts_are_strict_and_policy_governed() -> None:
    resolver_module = _module("research_orchestration_resolver")
    base = {
        "runtimeReadiness": {},
        "providerReadiness": {},
        "authorizedCandidateRefs": set(),
        "availableInputRefs": set(),
        "applicableRuleRefs": set(),
        "riskAllowed": True,
        "budgetAvailable": True,
        "policyVersion": "registry-policy.v1",
    }
    for field_name in ("riskAllowed", "budgetAvailable"):
        with pytest.raises(ValidationError):
            resolver_module.ResolutionFacts(**(base | {field_name: "false"}))
    with pytest.raises(ValidationError):
        resolver_module.ResolutionFacts(**(base | {"activeWorkUnitCounts": {"skill:x:1": -1}}))
    with pytest.raises(ValidationError):
        resolver_module.ResolutionFacts(**(base | {"activeWorkUnitCounts": {"skill:x:1": True}}))
    with pytest.raises(ValidationError):
        resolver_module.ResolutionFacts(**(base | {"lastAssignedAt": {"skill:x:1": datetime.now()}}))
    with pytest.raises(ValidationError):
        resolver_module.ResolutionFacts(**(base | {"unknownFact": True}))
    facts = resolver_module.ResolutionFacts(**base)
    with pytest.raises(ValidationError):
        facts.policy_version = "other-policy.v1"


def test_resolver_rejects_unknown_fact_keys_and_policy_mismatch() -> None:
    resolver_module = _module("research_orchestration_resolver")
    for facts, code in (
        (_facts().model_copy(update={"runtime_readiness": {"skill:unknown:1": True}}), "unknown_candidate_fact"),
        (_facts().model_copy(update={"provider_readiness": {"provider-unknown": True}}), "unknown_provider_fact"),
        (_facts().model_copy(update={"policy_version": "other-policy.v1"}), "policy_version_mismatch"),
    ):
        with pytest.raises(resolver_module.ResolutionRejectedError, match=code):
            _resolve(facts=facts)


def _design_with_judgment(requirement: str) -> ResearchDesignVersion:
    design = _design().model_copy(deep=True)
    instance = next(row for row in design.instances if row.substage_instance_id == "sub_contract_full")
    criterion_type = type(instance.acceptance_criteria[0])
    payload = instance.acceptance_criteria[0].model_dump(mode="json", by_alias=True)
    payload["judgmentRequirement"] = requirement
    if requirement == "deterministic":
        payload["evaluatorType"] = "deterministic"
        payload["evaluatorRef"] = None
    elif requirement == "domain_professional":
        payload["criterionKind"] = "professional_judgment"
        payload["evaluatorType"] = "agent"
        payload["evaluatorRef"] = "test"
    elif requirement == "research_synthesis":
        payload["criterionKind"] = "professional_judgment"
        payload["evaluatorType"] = "deterministic"
        payload["evaluatorRef"] = None
    else:
        payload["criterionKind"] = "professional_judgment"
        payload["evaluatorType"] = "human_gate"
        payload["evaluatorRef"] = "human-gate:test"
    instance.acceptance_criteria = [criterion_type.model_validate(payload)]
    return design


def test_execution_method_intermediate_roles_use_role_output_and_not_final_acceptance() -> None:
    envelope = _execution_method_envelope()
    design = _design_with_judgment("domain_professional")
    result, _ = _resolve(
        envelope, facts=_execution_method_facts(envelope), design=design
    )
    by_id = {_candidate_id(row): row for row in result.candidate_evaluations}

    expected = {
        "skill:skill-a:1": ("retrieval", "not_required"),
        "skill:skill-b:1": ("reading", "not_required"),
        "agent:specialist-b:1": ("evidence", "not_required"),
        "agent:specialist-a:1": ("final_judgment", "required_supported"),
    }
    for candidate_id, (role_id, judgment_fit) in expected.items():
        evaluation = by_id[candidate_id]
        assert evaluation.eligible
        assert evaluation.preference.completion_coverage == "progress_capable"
        role_rows = {
            row.role_ref: row for row in evaluation.execution_method_role_evaluations
        }
        role = role_rows[f"{_EXECUTION_METHOD_ID}:{role_id}"]
        assert role.eligible
        assert "output_incompatible" not in role.rejection_codes
        assert role.semantic_fit_evidence.professional_judgment_fit == judgment_fit

    assert by_id["agent:specialist-b:1"].preference.supports_professional_judgment is False
    assert by_id["agent:specialist-a:1"].preference.supports_professional_judgment is True


def test_execution_method_professional_role_rejects_candidate_without_support() -> None:
    envelope = _execution_method_envelope(final_professional_supported=False)
    design = _design_with_judgment("domain_professional")
    result, _ = _resolve(
        envelope, facts=_execution_method_facts(envelope), design=design
    )
    by_id = {_candidate_id(row): row for row in result.candidate_evaluations}

    final = by_id["agent:specialist-a:1"]
    role = final.execution_method_role_evaluations[0]
    assert role.role_ref == f"{_EXECUTION_METHOD_ID}:final_judgment"
    assert not role.eligible
    assert "semantic_fit_insufficient" in role.rejection_codes
    assert role.semantic_fit_evidence.professional_judgment_fit == "unsupported"
    assert not final.eligible


@pytest.mark.parametrize(
    ("final_output", "final_judgment", "reason"),
    [
        ("intermediate_only", "domain_professional", "execution_method_completion_output_incompatible"),
        ("evidence_summary", "none", "execution_method_completion_acceptance_incompatible"),
    ],
)
def test_execution_method_overall_must_cover_definition_completion_contract(
    final_output: str, final_judgment: str, reason: str
) -> None:
    resolver_module = _module("research_orchestration_resolver")
    envelope = _execution_method_envelope(
        final_output=final_output, final_judgment=final_judgment
    )
    design = _design_with_judgment("domain_professional")

    with pytest.raises(resolver_module.ResolutionRejectedError, match=reason):
        _resolve(envelope, facts=_execution_method_facts(envelope), design=design)


def test_no_execution_method_keeps_single_capability_definition_semantics() -> None:
    envelope = _envelope()
    envelope["definitions"]["skills"][0]["outputContract"]["artifactTypes"] = [
        "candidate_records"
    ]
    result, _ = _resolve(envelope, design=_design_with_judgment("domain_professional"))
    by_id = {_candidate_id(row): row for row in result.candidate_evaluations}

    retrieval_like_skill = by_id["skill:skill-a:1"]
    assert not retrieval_like_skill.eligible
    assert "output_incompatible" in retrieval_like_skill.rejection_codes
    assert "semantic_fit_insufficient" in retrieval_like_skill.rejection_codes
    assert retrieval_like_skill.execution_method_role_evaluations == []


def test_execution_method_real_resolver_to_execution_decision_selects_role_candidates() -> None:
    from app.models.research_orchestration import ResearchTaskV12, RuntimeReadinessSnapshot
    from tests.support.research_execution_decision import freeze_prepared_selection_for_test
    from tests.support.research_lifecycle import build_confirmed_research_task

    envelope = _execution_method_envelope()
    design = _design_with_judgment("domain_professional")
    facts = _execution_method_facts(envelope)
    resolution, snapshot = _resolve(envelope, facts=facts, design=design)

    task_payload = build_confirmed_research_task().model_dump(mode="json", by_alias=True)
    task_payload["researchDesignVersions"] = [
        design.model_dump(mode="json", by_alias=True)
    ]
    task_payload["registrySnapshotRef"] = {
        "schemaVersion": 1,
        "contentHash": snapshot.content_hash,
    }
    task = ResearchTaskV12.model_validate(task_payload)

    runtime_readiness = {}
    for evaluation in resolution.candidate_evaluations:
        if isinstance(evaluation.candidate_ref, MultiAgentRecipeRef):
            continue
        candidate_id = _candidate_id(evaluation)
        runtime_readiness[candidate_id] = RuntimeReadinessSnapshot.model_validate(
            {
                "capabilityRef": evaluation.candidate_ref.model_dump(
                    mode="json", by_alias=True
                ),
                "ready": True,
                "runtimeBindingId": f"runtime:{candidate_id}",
                "checkedAt": "2026-07-28T00:00:03Z",
                "expiresAt": "2026-07-28T01:00:03Z",
                "reason": None,
            }
        )

    decision_service = _module("research_execution_decision_service")
    prepared = decision_service.prepare_selection(
        task,
        instance_id="sub_contract_full",
        resolution=resolution,
        snapshot_envelope=snapshot.envelope,
        facts=facts,
        runtime_readiness=runtime_readiness,
        selection_request_id="selection-role-resolver-integration",
        selection_command_hash="command-role-resolver-integration",
        expected_task_revision=task.revision,
        owner_id="owner_contract",
        workspace_id="workspace_contract",
        decided_at=datetime.fromisoformat("2026-07-28T00:00:04+00:00"),
    )

    assert dict(prepared.execution_method_role_candidates) == {
        "retrieval": "skill:skill-a:1",
        "reading": "skill:skill-b:1",
        "evidence": "agent:specialist-b:1",
        "final_judgment": "agent:specialist-a:1",
    }

    materialized = freeze_prepared_selection_for_test(
        prepared, task, prepared.final_tie_candidate_ids[0]
    )
    assert materialized.decision.strategy.value == "execution_method"
    assignments = {
        row.execution_method_role_id: row for row in materialized.decision.assignments
    }
    assert assignments["retrieval"].executor_ref.executor_id == "skill-a"
    assert assignments["reading"].executor_ref.executor_id == "skill-b"
    assert assignments["evidence"].executor_ref.executor_id == "specialist-b"
    assert assignments["final_judgment"].executor_ref.executor_id == "specialist-a"

    for role_id in ("retrieval", "reading", "evidence"):
        assert assignments[role_id].completion_coverage == "progress_capable"
        role = next(
            row
            for row in envelope["definitions"]["substages"][0]["executionMethod"]["roles"]
            if row["roleId"] == role_id
        )
        assert assignments[role_id].expected_artifact_types == role[
            "qualificationContract"
        ]["outputArtifactTypes"]
        assert assignments[role_id].acceptance_criteria == []
    final_assignment = assignments["final_judgment"]
    assert final_assignment.completion_coverage == "completion_capable"
    assert final_assignment.expected_artifact_types == ["evidence_summary"]
    assert final_assignment.acceptance_criteria
    assert final_assignment.acceptance_criteria[0].judgment_requirement == "domain_professional"


def test_skill_binding_role_is_tool_and_deterministic_contract_can_complete() -> None:
    result, snapshot = _resolve(design=_design_with_judgment("deterministic"))
    assert {row["supportedRole"] for row in snapshot.envelope["bindings"]["skills"]} == {"tool"}
    skills = [row for row in result.candidate_evaluations if isinstance(row.candidate_ref, SkillRef)]
    assert skills
    assert all(row.eligible for row in skills)
    assert all(row.preference.completion_coverage == "completion_capable" for row in skills)


def test_definition_required_input_and_output_contracts_fail_closed() -> None:
    design = _design().model_copy(deep=True)
    instance = next(row for row in design.instances if row.substage_instance_id == "sub_contract_full")
    instance.input_refs = []
    result, _ = _resolve(design=design)
    non_fallback = [
        row for row in result.candidate_evaluations
        if _candidate_id(row) != _case()["fallbackCandidate"] and not isinstance(row.candidate_ref, MultiAgentRecipeRef)
    ]
    assert non_fallback
    assert all(not row.eligible and "input_incompatible" in row.rejection_codes for row in non_fallback)

    design = _design().model_copy(deep=True)
    instance = next(row for row in design.instances if row.substage_instance_id == "sub_contract_full")
    instance.expected_artifact_types = ["other_artifact"]
    result, _ = _resolve(design=design)
    non_fallback = [
        row for row in result.candidate_evaluations
        if _candidate_id(row) != _case()["fallbackCandidate"] and not isinstance(row.candidate_ref, MultiAgentRecipeRef)
    ]
    assert all(not row.eligible and "output_incompatible" in row.rejection_codes for row in non_fallback)


def test_unknown_contract_and_exact_normalized_not_for_are_rejected() -> None:
    envelope = _envelope()
    envelope["definitions"]["substages"][0].update(
        _module("research_orchestration_resolver").parse_definition_spec(
            "sdr", "specs/example.md", "# Evidence synthesis\n\nNo structured contracts.\n"
        )
    )
    result, _ = _resolve(envelope)
    non_fallback = [row for row in result.candidate_evaluations if _candidate_id(row) != _case()["fallbackCandidate"]]
    assert all(not row.eligible and "semantic_fit_insufficient" in row.rejection_codes for row in non_fallback)
    assert all(row.preference.semantic_fit != "exact" for row in non_fallback)

    envelope = _envelope()
    envelope["definitions"]["agents"][0]["notFor"] = ["  CREATE   THE BOUNDED EVIDENCE SUMMARY.  "]
    result, _ = _resolve(envelope)
    by_id = {_candidate_id(row): row for row in result.candidate_evaluations}
    assert not by_id["agent:specialist-a:1"].eligible
    assert "explicitly_not_for" in by_id["agent:specialist-a:1"].rejection_codes
    assert by_id["agent:specialist-b:1"].eligible


def test_semantic_only_reasoning_definition_is_capability_gated_for_completion() -> None:
    envelope = _envelope()
    for skill in envelope["definitions"]["skills"]:
        skill["sourceContract"]["runtime_contract"] = {
            "kind": "llm_reasoning",
            "requires_external_action": False,
            "adapter_id": "huiyan_chat_completions",
            "evidence_contract_version": "none",
        }
    envelope["definitions"]["substages"][0].update(
        _module("research_orchestration_resolver").parse_definition_spec(
            "sdr", "specs/example.md", "# Evidence synthesis\n\nNo structured contracts.\n"
        )
    )
    design = _design_with_judgment("deterministic")
    instance = next(row for row in design.instances if row.substage_instance_id == "sub_contract_full")
    instance.expected_artifact_types = ["sdr.evidence_synthesis.planned_output"]
    criterion_type = type(instance.acceptance_criteria[0])
    criterion = instance.acceptance_criteria[0].model_dump(mode="json", by_alias=True)
    criterion["requiredArtifactType"] = "sdr.evidence_synthesis.planned_output"
    instance.acceptance_criteria = [criterion_type.model_validate(criterion)]

    result, _ = _resolve(envelope, design=design)
    by_id = {_candidate_id(row): row for row in result.candidate_evaluations}
    fallback = by_id[_case()["fallbackCandidate"]]

    assert fallback.eligible
    assert fallback.preference.completion_coverage == "completion_capable"
    assert "registry-policy.v1:executor-capability" in (
        fallback.preference.semantic_fit_evidence.positive_rule_refs
    )
    skill_rows = [row for row in result.candidate_evaluations if isinstance(row.candidate_ref, SkillRef)]
    assert skill_rows
    assert all(row.eligible for row in skill_rows)
    assert all(row.preference.completion_coverage == "completion_capable" for row in skill_rows)
    specialist_rows = [
        row for row in result.candidate_evaluations
        if isinstance(row.candidate_ref, AgentRef)
        and _candidate_id(row) != _case()["fallbackCandidate"]
    ]
    assert all(not row.eligible for row in specialist_rows)


def test_semantic_only_definition_without_output_coverage_remains_progress_only() -> None:
    envelope = _envelope()
    for skill in envelope["definitions"]["skills"]:
        skill["sourceContract"]["runtime_contract"] = {
            "kind": "llm_reasoning",
            "requires_external_action": False,
            "adapter_id": "huiyan_chat_completions",
            "evidence_contract_version": "none",
        }
    envelope["definitions"]["substages"][0].update(
        _module("research_orchestration_resolver").parse_definition_spec(
            "sdr", "specs/example.md", "# Evidence synthesis\n\nNo structured contracts.\n"
        )
    )
    design = _design_with_judgment("deterministic")
    instance = next(row for row in design.instances if row.substage_instance_id == "sub_contract_full")
    instance.expected_artifact_types = ["sdr.evidence_synthesis.planned_output"]
    criterion_type = type(instance.acceptance_criteria[0])
    criterion = instance.acceptance_criteria[0].model_dump(mode="json", by_alias=True)
    criterion["requiredArtifactType"] = None
    instance.acceptance_criteria = [criterion_type.model_validate(criterion)]

    result, _ = _resolve(envelope, design=design)
    by_id = {_candidate_id(row): row for row in result.candidate_evaluations}
    fallback = by_id[_case()["fallbackCandidate"]]

    assert fallback.eligible
    assert fallback.preference.completion_coverage == "progress_capable"


def test_source_acquisition_external_contract_blocks_general_fallback_completion_but_allows_skill() -> None:
    envelope = _envelope()
    definition = envelope["definitions"]["substages"][0]
    definition.update(
        {
            "definitionId": "opentrajectory:source_acquisition",
            "pack": "opentrajectory",
            "stage": "execution_validation",
            "substageKey": "source_acquisition",
            "label": "Source acquisition",
            "description": "Acquire and normalize real literature sources.",
        }
    )
    definition.update(
        _module("research_orchestration_resolver").parse_definition_spec(
            "opentrajectory",
            "specs/03-validation/source-acquisition.md",
            "# Source acquisition\n\nNo structured contracts.\n",
        )
    )
    envelope["definitions"]["packs"] = [
        {"pack": "opentrajectory", "definitionVersion": 1, "source": {"key": "opentrajectory"}}
    ]
    for skill in envelope["definitions"]["skills"]:
        skill["sourceContract"]["runtime_contract"] = {
            "kind": "external_adapter",
            "requires_external_action": True,
            "adapter_id": f"python_adapter:{skill['skillId']}",
            "evidence_contract_version": "external-action-evidence/v1",
        }
    for binding in envelope["bindings"]["skills"]:
        binding["definitionId"] = definition["definitionId"]
        binding["bindingId"] = (
            f"bind:{definition['definitionId']}:skill:{binding['skillId']}"
        )
    for binding in envelope["bindings"]["agents"]:
        binding["definitionId"] = definition["definitionId"]
        binding["bindingId"] = (
            f"bind:{definition['definitionId']}:agent:{binding['agentId']}:{binding['supportedRole']}"
        )
    envelope["recipes"] = []

    design = _design_with_judgment("deterministic")
    instance = next(row for row in design.instances if row.substage_instance_id == "sub_contract_full")
    instance.substage_definition_id = definition["definitionId"]
    instance.stage_key = type(instance.stage_key)("execution_validation")
    instance.objective = "Acquire real literature sources for downstream evidence work."
    planned_output = "opentrajectory.source_acquisition.planned_output"
    instance.expected_artifact_types = [planned_output]
    criterion_type = type(instance.acceptance_criteria[0])
    criterion = instance.acceptance_criteria[0].model_dump(mode="json", by_alias=True)
    criterion["requiredArtifactType"] = planned_output
    criterion["description"] = "Produce source-acquisition evidence from an authorized executor."
    instance.acceptance_criteria = [criterion_type.model_validate(criterion)]

    facts = _module("research_orchestration_resolver").ResolutionFacts(
        runtimeReadiness={
            "skill:skill-a:1": True,
            "skill:skill-b:1": True,
            "agent:specialist-a:1": True,
            "agent:specialist-b:1": True,
            "agent:general-research:1": True,
        },
        providerReadiness={"provider-a": True},
        authorizedCandidateRefs={
            "skill:skill-a:1",
            "skill:skill-b:1",
            "agent:specialist-a:1",
            "agent:specialist-b:1",
            "agent:general-research:1",
        },
        availableInputRefs={"material_contract"},
        applicableRuleRefs=set(),
        riskAllowed=True,
        budgetAvailable=True,
        policyVersion="registry-policy.v1",
    )
    result, _ = _resolve(envelope, facts=facts, design=design)
    by_id = {_candidate_id(row): row for row in result.candidate_evaluations}
    fallback = by_id["agent:general-research:1"]
    assert fallback.eligible
    assert fallback.preference.completion_coverage == "progress_capable"
    assert "registry-policy.v1:executor-capability" not in (
        fallback.preference.semantic_fit_evidence.positive_rule_refs
    )
    skill_rows = [row for row in result.candidate_evaluations if isinstance(row.candidate_ref, SkillRef)]
    assert skill_rows
    assert all(row.preference.completion_coverage == "completion_capable" for row in skill_rows)




def test_research_synthesis_allows_authorized_general_agent_without_domain_professional() -> None:
    envelope = _envelope()
    for skill in envelope["definitions"]["skills"]:
        skill["sourceContract"]["runtime_contract"] = {
            "kind": "llm_reasoning",
            "requires_external_action": False,
            "adapter_id": "test_reasoning",
            "evidence_contract_version": "none",
        }
    envelope["definitions"]["substages"][0].update(
        _module("research_orchestration_resolver").parse_definition_spec(
            "sdr", "specs/example.md", "# Evidence synthesis\n\nNo structured contracts.\n"
        )
    )
    design = _design_with_judgment("research_synthesis")
    instance = next(row for row in design.instances if row.substage_instance_id == "sub_contract_full")
    instance.expected_artifact_types = ["evidence_summary"]
    result, _ = _resolve(envelope, design=design)
    by_id = {_candidate_id(row): row for row in result.candidate_evaluations}
    fallback = by_id["agent:general-research:1"]
    assert fallback.eligible
    assert fallback.preference.supports_professional_judgment
    assert fallback.preference.completion_coverage == "completion_capable"
    assert envelope["definitions"]["agents"][-1]["judgmentSupport"]["domainProfessional"] is False


def test_recipe_excludes_fallback_and_reuses_constituent_semantics() -> None:
    envelope = _envelope()
    envelope["bindings"]["agents"] = [
        row for row in envelope["bindings"]["agents"] if row["agentId"] != "specialist-a"
    ]
    facts = _facts().model_copy(
        update={
            "authorized_candidate_refs": {
                ref for ref in _facts().authorized_candidate_refs if ref != "agent:specialist-a:1"
            },
            "runtime_readiness": {
                ref: ready for ref, ready in _facts().runtime_readiness.items() if ref != "agent:specialist-a:1"
            },
        }
    )
    result, _ = _resolve(envelope, facts=facts)
    recipe = next(row for row in result.candidate_evaluations if isinstance(row.candidate_ref, MultiAgentRecipeRef))
    assert not recipe.eligible
    assert "role_mismatch" in recipe.rejection_codes
    assert recipe.preference.completion_coverage == "progress_capable"

    envelope = _envelope()
    envelope["definitions"]["agents"][0]["notFor"] = ["Create the bounded evidence summary."]
    result, _ = _resolve(envelope)
    recipe = next(row for row in result.candidate_evaluations if isinstance(row.candidate_ref, MultiAgentRecipeRef))
    assert not recipe.eligible
    assert recipe.preference.completion_coverage == "progress_capable"


def test_recipe_executable_aggregates_constituent_authority_and_readiness() -> None:
    facts = _facts(provider=False)
    facts = facts.model_copy(
        update={
            "authorized_candidate_refs": {
                ref for ref in facts.authorized_candidate_refs if not ref.startswith("agent:")
            },
            "runtime_readiness": {
                ref: (False if ref.startswith("agent:") else ready)
                for ref, ready in facts.runtime_readiness.items()
            },
        }
    )
    result, _ = _resolve(facts=facts)
    recipe = next(row for row in result.candidate_evaluations if isinstance(row.candidate_ref, MultiAgentRecipeRef))
    assert recipe.eligible
    assert recipe.preference.executable_now is False
    assert {"authorization_denied", "runtime_unavailable", "provider_unavailable"} <= set(
        recipe.preference.execution_blocker_codes
    )


@pytest.mark.parametrize(
    ("case_name", "role_states", "expected_executable", "expected_recipe_blockers"),
    [
        (
            "support_split",
            {
                "primary": [("producer-full", True, True)],
                "support": [("support-authorized", True, False), ("support-runtime", False, True)],
            },
            False,
            {"authorization_denied", "runtime_unavailable"},
        ),
        (
            "producer_split",
            {
                "primary": [("producer-authorized", True, False), ("producer-runtime", False, True)],
                "support": [("support-full", True, True)],
            },
            False,
            {"authorization_denied", "runtime_unavailable"},
        ),
        (
            "complete_with_blocked_decoys",
            {
                "primary": [("producer-full", True, True)],
                "support": [
                    ("support-authorized", True, False),
                    ("support-runtime", False, True),
                    ("support-full", True, True),
                ],
            },
            True,
            set(),
        ),
        (
            "different_complete_agents_per_template",
            {
                "primary": [("producer-full", True, True)],
                "support": [("support-full", True, True)],
            },
            True,
            set(),
        ),
    ],
    ids=lambda value: value if isinstance(value, str) else None,
)
def test_recipe_requires_one_complete_agent_row_per_template(
    case_name: str,
    role_states: dict[str, list[tuple[str, bool, bool]]],
    expected_executable: bool,
    expected_recipe_blockers: set[str],
) -> None:
    envelope = _envelope()
    definition = envelope["definitions"]["substages"][0]
    definition.update(
        {
            "definitionId": "sdr:evidence_extraction",
            "stage": "execution_validation",
            "substageKey": "evidence_extraction",
        }
    )
    definition.update(
        _module("research_orchestration_resolver").parse_definition_spec(
            "sdr",
            "specs/example.md",
            "# Evidence extraction\n\n## Input\n\n- material_contract\n\n"
            "## Output\n\n- evidence_trace\n\n## Acceptance\n\n- deterministic_contract\n",
        )
    )
    for skill in envelope["definitions"]["skills"]:
        skill["outputContract"]["artifactTypes"] = ["evidence_trace"]
    for binding in envelope["bindings"]["skills"]:
        binding["definitionId"] = definition["definitionId"]
        binding["bindingId"] = f"bind:{definition['definitionId']}:skill:{binding['skillId']}"
    registered_recipe = _module("multi_agent_recipe_registry").load_multi_agent_recipe_registry().get(
        "parallel_evidence_synthesis", 1
    )
    assert registered_recipe is not None
    envelope["recipes"] = [registered_recipe.model_dump(mode="json", by_alias=True)]
    fallback_definition = next(
        row for row in envelope["definitions"]["agents"] if row["agentId"] == "general-research"
    )
    fallback_binding = next(
        row
        for row in envelope["bindings"]["agents"]
        if row["sourceAuthority"] == "general_fallback_policy"
    )
    template_agent = next(
        row for row in envelope["definitions"]["agents"] if row["agentId"] == "specialist-a"
    )
    template_agent["outputContract"]["artifactKind"] = "evidence_trace"
    generated_agents: list[dict] = []
    generated_bindings: list[dict] = []
    runtime_readiness: dict[str, bool] = {}
    authorized_refs = {"recipe:parallel_evidence_synthesis:1", "skill:skill-a:1", "skill:skill-b:1"}
    expected_direct: dict[str, tuple[bool, bool]] = {}
    for role, states in role_states.items():
        for agent_id, authorized, runtime_ready in states:
            agent = deepcopy(template_agent)
            agent["agentId"] = agent_id
            generated_agents.append(agent)
            generated_bindings.append(
                {
                    "bindingId": f"bind:sdr:evidence_extraction:agent:{agent_id}:{role}",
                    "bindingVersion": 1,
                    "definitionId": "sdr:evidence_extraction",
                    "definitionVersion": 1,
                    "agentId": agent_id,
                    "agentDefinitionVersion": 1,
                    "supportedRole": role,
                    "enabled": True,
                    "sourceAuthority": "agent_registry_mapping",
                    "artifactContract": "evidence_summary_v1",
                }
            )
            candidate_id = f"agent:{agent_id}:1"
            runtime_readiness[candidate_id] = runtime_ready
            if authorized:
                authorized_refs.add(candidate_id)
            expected_direct[candidate_id] = (authorized, runtime_ready)

    envelope["definitions"]["agents"] = generated_agents + [fallback_definition]
    fallback_binding["definitionId"] = "sdr:evidence_extraction"
    fallback_binding["bindingId"] = "bind:sdr:evidence_extraction:agent:general-research:primary"
    envelope["bindings"]["agents"] = generated_bindings + [fallback_binding]
    envelope["bindings"]["agentSkills"] = []
    runtime_readiness["agent:general-research:1"] = True
    authorized_refs.add("agent:general-research:1")
    facts = _module("research_orchestration_resolver").ResolutionFacts(
        runtimeReadiness=runtime_readiness | {"skill:skill-a:1": True, "skill:skill-b:1": True},
        providerReadiness={"provider-a": True},
        authorizedCandidateRefs=authorized_refs,
        availableInputRefs={"material_contract"},
        applicableRuleRefs={"registry-policy.v1:substage-match"},
        riskAllowed=True,
        budgetAvailable=True,
        policyVersion="registry-policy.v1",
    )
    design = _design().model_copy(deep=True)
    instance = next(row for row in design.instances if row.substage_instance_id == "sub_contract_full")
    instance.substage_definition_id = "sdr:evidence_extraction"
    instance.stage_key = type(instance.stage_key)("execution_validation")
    instance.expected_artifact_types = ["evidence_trace"]
    criterion_type = type(instance.acceptance_criteria[0])
    criterion_payload = instance.acceptance_criteria[0].model_dump(mode="json", by_alias=True)
    criterion_payload["requiredArtifactType"] = "evidence_trace"
    instance.acceptance_criteria = [criterion_type.model_validate(criterion_payload)]
    result, _ = _resolve(envelope, facts=facts, design=design)
    by_id = {_candidate_id(row): row for row in result.candidate_evaluations}
    for candidate_id, (authorized, runtime_ready) in expected_direct.items():
        direct = by_id[candidate_id]
        assert direct.eligible is True, case_name
        assert direct.preference.executable_now is (authorized and runtime_ready), case_name
        expected_blockers = set()
        if not authorized:
            expected_blockers.add("authorization_denied")
        if not runtime_ready:
            expected_blockers.add("runtime_unavailable")
        assert set(direct.preference.execution_blocker_codes) == expected_blockers, case_name

    recipe = by_id["recipe:parallel_evidence_synthesis:1"]
    assert recipe.eligible is True, case_name
    assert recipe.preference.executable_now is expected_executable, case_name
    assert set(recipe.preference.execution_blocker_codes) == expected_recipe_blockers, case_name


def test_recipe_completion_requires_assembly_and_reviewer_closure() -> None:
    envelope = _envelope()
    envelope["recipes"][0]["resultAssemblyPolicyTemplate"]["finalArtifactRequirements"][0]["artifactType"] = "other"
    result, _ = _resolve(envelope)
    recipe = next(row for row in result.candidate_evaluations if isinstance(row.candidate_ref, MultiAgentRecipeRef))
    assert recipe.preference.completion_coverage == "progress_capable"

    envelope = _envelope()
    envelope["recipes"][0]["assignmentTemplates"].append(
        {
            "templateKey": "reviewer",
            "role": "reviewer",
            "requiredSupportedRole": "reviewer",
            "objectiveTemplate": "Review the result.",
            "dependencyTemplateKeys": ["producer"],
        }
    )
    envelope["recipes"][0]["completionPolicyTemplate"]["requiredTemplateKeys"].append("reviewer")
    envelope["recipes"][0]["completionPolicyTemplate"]["reviewerTemplateKeys"] = ["reviewer"]
    envelope["recipes"][0]["completionPolicyTemplate"]["reviewRequirement"] = "all_reviewers_approve"
    result, _ = _resolve(envelope)
    recipe = next(row for row in result.candidate_evaluations if isinstance(row.candidate_ref, MultiAgentRecipeRef))
    assert not recipe.eligible
    assert recipe.preference.completion_coverage == "progress_capable"


def test_opentrajectory_snapshot_freezes_ordered_required_l3_composition() -> None:
    snapshot = _module("research_orchestration_resolver").build_registry_snapshot().envelope
    definitions = {
        row["definitionId"]: row
        for row in snapshot["definitions"]["substages"]
    }

    source_acquisition = definitions["opentrajectory:source_acquisition"]["executionComposition"]
    assert source_acquisition["semantics"] == "ordered_required"
    assert source_acquisition["workflowId"] == "opentrajectory_research_workflow"
    assert [row["skillId"] for row in source_acquisition["requiredSkillRefs"]] == [
        "opentrajectory_source_acquisition",
    ]

    screening = definitions["opentrajectory:source_screening_selection"]["executionComposition"]
    assert [row["skillId"] for row in screening["requiredSkillRefs"]] == [
        "opentrajectory_source_screening_selection",
    ]


def test_pvd_unmigrated_m3_definition_is_disabled_and_has_no_composition() -> None:
    snapshot = _module("research_orchestration_resolver").build_registry_snapshot().envelope
    definitions = {
        row["definitionId"]: row
        for row in snapshot["definitions"]["substages"]
    }

    m3 = definitions["pvd:literature_experiment_record_verification"]
    assert m3["enabled"] is False
    assert "executionComposition" not in m3
    assert not [
        row
        for row in snapshot["bindings"]["skills"]
        if row["definitionId"] == "pvd:literature_experiment_record_verification"
    ]


def test_opentrajectory_composition_order_does_not_depend_on_registry_sequence_or_array_order(
    monkeypatch,
) -> None:
    resolver = _module("research_orchestration_resolver")
    original_load_yaml = resolver._load_yaml

    def without_registry_sequence(path):
        payload = deepcopy(original_load_yaml(path))
        if path == resolver.SKILLS_PATH:
            for row in payload.get("skills") or []:
                row.pop("sequence", None)
            payload["skills"].reverse()
        return payload

    monkeypatch.setattr(resolver, "_load_yaml", without_registry_sequence)

    snapshot = resolver.build_registry_snapshot().envelope
    definitions = {
        row["definitionId"]: row
        for row in snapshot["definitions"]["substages"]
    }

    source_acquisition = definitions[
        "opentrajectory:source_acquisition"
    ]["executionComposition"]
    assert source_acquisition["sourceAuthority"] == (
        "schemas/opentrajectory/substage-skill-map.yaml"
    )
    assert [
        row["skillId"]
        for row in source_acquisition["requiredSkillRefs"]
    ] == [
        "opentrajectory_source_acquisition",
    ]

    screening = definitions[
        "opentrajectory:source_screening_selection"
    ]["executionComposition"]
    assert [
        row["skillId"]
        for row in screening["requiredSkillRefs"]
    ] == [
        "opentrajectory_source_screening_selection",
    ]


def _materials_database_search_design(*, expected_artifact: str = "materials.database_search.planned_output"):
    design = _design_with_judgment("deterministic")
    instance = next(row for row in design.instances if row.substage_instance_id == "sub_contract_full")
    instance.substage_definition_id = "materials:database_search"
    instance.substage_definition_version = 1
    instance.stage_key = type(instance.stage_key)("problem_modeling")
    instance.expected_artifact_types = [expected_artifact]
    criterion_type = type(instance.acceptance_criteria[0])
    criterion = instance.acceptance_criteria[0].model_dump(mode="json", by_alias=True)
    criterion["requiredArtifactType"] = expected_artifact
    instance.acceptance_criteria = [criterion_type.model_validate(criterion)]
    return design


def _resolve_production_snapshot(*, facts, design: ResearchDesignVersion | None = None):
    resolver_module = _module("research_orchestration_resolver")
    repository_module = _module("registry_snapshot_repository")
    snapshot = resolver_module.build_registry_snapshot()
    repository = repository_module.InMemoryRegistrySnapshotRepository()
    repository.put(snapshot)
    design = design or _materials_database_search_design()
    resolver = resolver_module.ResearchOrchestrationResolver(repository)
    result = resolver.resolve(
        design=design,
        designRef=ResearchDesignRef(researchDesignId=design.research_design_id, version=design.version),
        instanceRef=InstanceRef(substageInstanceId="sub_contract_full", instanceRevision=1),
        registrySnapshotRef=RegistrySnapshotRef(schemaVersion=1, contentHash=snapshot.content_hash),
        facts=facts,
    )
    return result, snapshot


def _materials_mp_facts(*, runtime_ready: bool = True, authorized: bool = True):
    resolver_module = _module("research_orchestration_resolver")
    skill_id = "skill:materials_mp_constraint_search:1"
    return resolver_module.ResolutionFacts(
        runtimeReadiness={
            skill_id: runtime_ready,
            "agent:general_research_agent:1": True,
        },
        providerReadiness={},
        authorizedCandidateRefs={skill_id, "agent:general_research_agent:1"} if authorized else set(),
        availableInputRefs={"material_contract"},
        applicableRuleRefs=set(),
        satisfiedHumanGateRefs=set(),
        riskAllowed=True,
        budgetAvailable=True,
        policyVersion="registry-policy.v1",
    )


def _materials_mp_evaluation(result):
    return next(
        row
        for row in result.candidate_evaluations
        if isinstance(row.candidate_ref, SkillRef)
        and row.candidate_ref.skill_id == "materials_mp_constraint_search"
    )


def test_frozen_materials_external_adapter_with_registered_runtime_is_completion_capable() -> None:
    result, snapshot = _resolve_production_snapshot(facts=_materials_mp_facts())
    evaluation = _materials_mp_evaluation(result)
    skill = next(
        row
        for row in snapshot.envelope["definitions"]["skills"]
        if row["skillId"] == "materials_mp_constraint_search"
    )
    frozen = skill["sourceContract"]["runtime_contract"]
    contract = _module("research_orchestration_resolver").ResearchOrchestrationResolver._skill_runtime_contract(
        skill
    )

    assert contract == {
        "kind": frozen["kind"],
        "adapter_id": frozen["adapter_id"],
        "requires_external_action": frozen["requires_external_action"],
        "evidence_contract_version": frozen["evidence_contract_version"],
    }
    assert contract == {
        "kind": "external_adapter",
        "adapter_id": "python_adapter:materials_mp_constraint_search",
        "requires_external_action": True,
        "evidence_contract_version": "external-action-evidence/v1",
    }
    assert evaluation.eligible is True
    assert evaluation.preference.executable_now is True
    assert evaluation.preference.completion_coverage == "completion_capable"
    assert evaluation.rejection_codes == []


def test_skill_script_without_registered_runtime_remains_semantically_rejected() -> None:
    resolver_module = _module("research_orchestration_resolver")
    repository_module = _module("registry_snapshot_repository")
    snapshot = resolver_module.build_registry_snapshot()
    envelope = deepcopy(snapshot.envelope)
    fake_skill_id = "unregistered_skill_script"
    envelope["definitions"]["skills"].append(
        {
            "skillId": fake_skill_id,
            "definitionVersion": 1,
            "status": "ready",
            "enabled": True,
            "runtime": "claude_code",
            "skillPath": "skills/test/unregistered-skill-script",
            "skillType": "atomic",
            "riskPolicy": {"riskClass": "low", "source": "test"},
            "sourceContract": {
                "runtime_contract": {
                    "kind": "skill_script",
                    "requires_external_action": True,
                    "adapter_id": f"skill_runtime:{fake_skill_id}",
                    "evidence_contract_version": "skill-execution-evidence/v1",
                }
            },
            "inputContract": {"status": "unknown", "acceptedInputRefs": []},
            "outputContract": {"status": "unknown", "artifactTypes": []},
            "judgmentSupport": {"deterministic": False, "source": "test"},
        }
    )
    envelope["bindings"]["skills"].append(
        {
            "bindingId": f"bind:materials:database_search:skill:{fake_skill_id}",
            "bindingVersion": 1,
            "definitionId": "materials:database_search",
            "definitionVersion": 1,
            "skillId": fake_skill_id,
            "skillDefinitionVersion": 1,
            "supportedRole": "tool",
            "enabled": True,
            "sourceAuthority": "skill_binding_source",
        }
    )
    archived = repository_module.snapshot_from_envelope(envelope)
    repository = repository_module.InMemoryRegistrySnapshotRepository()
    repository.put(archived)
    design = _materials_database_search_design()
    resolver = resolver_module.ResearchOrchestrationResolver(repository)
    result = resolver.resolve(
        design=design,
        designRef=ResearchDesignRef(researchDesignId=design.research_design_id, version=design.version),
        instanceRef=InstanceRef(substageInstanceId="sub_contract_full", instanceRevision=1),
        registrySnapshotRef=RegistrySnapshotRef(schemaVersion=1, contentHash=archived.content_hash),
        facts=resolver_module.ResolutionFacts(
            runtimeReadiness={
                f"skill:{fake_skill_id}:1": True,
                "agent:general_research_agent:1": True,
            },
            providerReadiness={},
            authorizedCandidateRefs={
                f"skill:{fake_skill_id}:1",
                "agent:general_research_agent:1",
            },
            availableInputRefs={"material_contract"},
            applicableRuleRefs=set(),
            satisfiedHumanGateRefs=set(),
            riskAllowed=True,
            budgetAvailable=True,
            policyVersion="registry-policy.v1",
        ),
    )
    evaluation = next(
        row
        for row in result.candidate_evaluations
        if isinstance(row.candidate_ref, SkillRef) and row.candidate_ref.skill_id == fake_skill_id
    )

    assert evaluation.eligible is False
    assert "semantic_fit_insufficient" in evaluation.rejection_codes
    assert evaluation.preference.completion_coverage == "progress_capable"


def test_skill_script_runtime_not_ready_remains_non_executable() -> None:
    result, _ = _resolve_production_snapshot(facts=_materials_mp_facts(runtime_ready=False))
    evaluation = _materials_mp_evaluation(result)

    assert evaluation.eligible is True
    assert evaluation.preference.executable_now is False
    assert "runtime_unavailable" in evaluation.preference.execution_blocker_codes


def test_skill_script_semantic_mismatch_still_rejects_when_instance_contract_unknown() -> None:
    design = _materials_database_search_design()
    instance = next(row for row in design.instances if row.substage_instance_id == "sub_contract_full")
    instance.expected_artifact_types = []
    instance.acceptance_criteria = []
    result, _ = _resolve_production_snapshot(facts=_materials_mp_facts(), design=design)
    evaluation = _materials_mp_evaluation(result)

    assert evaluation.eligible is False
    assert "semantic_fit_insufficient" in evaluation.rejection_codes
    assert evaluation.preference.completion_coverage == "progress_capable"

def test_pvd_workflow_v2_identity_and_composition_are_frozen_across_production_authorities() -> None:
    repo_root = Path(__file__).parents[3]
    workflow = yaml.safe_load(
        (repo_root / "schemas" / "workflows" / "pvd_process_optimization_workflow_v2.yaml").read_text(encoding="utf-8")
    )
    v2_keys = [row["nodeKey"] for row in workflow["nodes"]]
    v2_definition_ids = {f"pvd:{value}" for value in v2_keys}

    assert v2_keys == [
        "evidence_applicability_comparability_analysis",
        "experimental_feedback_round_analysis",
        "process_factor_candidate_experiment_analysis",
        "parameter_experiment_plan_feasibility_validation",
        "round_experiment_plan_parameter_recommendation",
    ]

    composition_map = yaml.safe_load(
        (repo_root / "schemas" / "pvd" / "substage-skill-map.yaml").read_text(encoding="utf-8")
    )
    expected_compositions = {
        "evidence_applicability_comparability_analysis": [
            "pvd_evidence_applicability_comparability_analysis",
        ],
        "experimental_feedback_round_analysis": [
            "pvd_research_request_understanding",
            "pvd_experimental_feedback_round_analysis",
        ],
        "process_factor_candidate_experiment_analysis": [
            "pvd_research_request_understanding",
            "pvd_process_factor_candidate_experiment_analysis",
        ],
        "parameter_experiment_plan_feasibility_validation": [
            "pvd_parameter_experiment_plan_feasibility_validation",
        ],
        "round_experiment_plan_parameter_recommendation": [
            "pvd_round_experiment_plan_parameter_recommendation",
        ],
    }
    assert {key: composition_map[key] for key in v2_keys} == expected_compositions

    snapshot = _module("research_orchestration_resolver").build_registry_snapshot().envelope
    pvd_definitions = {
        row["definitionId"]: row
        for row in snapshot["definitions"]["substages"]
        if row["pack"] == "pvd"
    }
    assert v2_definition_ids <= set(pvd_definitions)

    for key in v2_keys:
        definition = pvd_definitions[f"pvd:{key}"]
        assert definition["enabled"] is True
        assert definition["executionComposition"]["workflowId"] == "pvd_process_optimization_workflow"
        assert definition["executionComposition"]["skillPack"] == "pvd"
        assert definition["executionComposition"]["sourceAuthority"] == "schemas/pvd/substage-skill-map.yaml"
        assert [row["skillId"] for row in definition["executionComposition"]["requiredSkillRefs"]] == composition_map[key]

    snapshot_binding_definition_ids = {
        row["definitionId"]
        for row in snapshot["bindings"]["skills"]
        if row["definitionId"] in v2_definition_ids
    }
    assert snapshot_binding_definition_ids == v2_definition_ids

def test_professional_skill_eligibility_comes_from_formal_capability_contract() -> None:
    envelope = _envelope()

    skill_a = next(
        row
        for row in envelope["definitions"]["skills"]
        if row["skillId"] == "skill-a"
    )
    skill_b = next(
        row
        for row in envelope["definitions"]["skills"]
        if row["skillId"] == "skill-b"
    )

    skill_a["judgmentSupport"] = {
        "deterministic": True,
        "domainProfessional": True,
        "humanAuthority": False,
        "source": "test_contract",
    }
    skill_b["judgmentSupport"] = {
        "deterministic": True,
        "domainProfessional": False,
        "humanAuthority": False,
        "source": "test_contract",
    }

    result, _ = _resolve(
        envelope,
        design=_design_with_judgment("domain_professional"),
    )
    by_id = {
        _candidate_id(row): row
        for row in result.candidate_evaluations
    }

    professional_skill = by_id["skill:skill-a:1"]
    ordinary_skill = by_id["skill:skill-b:1"]

    assert professional_skill.eligible
    assert (
        professional_skill.preference.completion_coverage
        == "completion_capable"
    )
    assert professional_skill.preference.supports_professional_judgment

    assert not ordinary_skill.eligible
    assert (
        ordinary_skill.preference.completion_coverage
        == "progress_capable"
    )


def test_human_authority_remains_explicit_external_gate() -> None:
    envelope = _envelope()

    for skill in envelope["definitions"]["skills"]:
        skill["judgmentSupport"] = {
            "deterministic": True,
            "domainProfessional": True,
            "humanAuthority": False,
            "source": "test_contract",
        }

    design = _design_with_judgment("human_authority")

    blocked, _ = _resolve(envelope, design=design)

    assert all(
        row.preference.completion_coverage == "progress_capable"
        for row in blocked.candidate_evaluations
    )

    satisfied, _ = _resolve(
        envelope,
        design=design,
        facts=_facts(
            satisfied_human_gate_refs={"human-gate:test"}
        ),
    )

    assert any(
        row.preference.completion_coverage == "completion_capable"
        for row in satisfied.candidate_evaluations
    )


def _fallback_evidence_inputs():
    """Keep real capability contracts; remove the optional composition recipe."""
    envelope = _envelope()
    envelope["recipes"] = []
    facts = _facts()
    facts.authorized_candidate_refs.discard("recipe:producer-support:1")
    facts.applicable_rule_refs.clear()
    return envelope, facts


def _prepare_fallback_evidence(
    envelope: dict,
    facts,
    *,
    design: ResearchDesignVersion | None = None,
):
    from app.models.research_orchestration import ResearchTaskV12, RuntimeReadinessSnapshot
    from tests.support.research_lifecycle import build_confirmed_research_task

    design = design or _design_with_judgment("domain_professional")
    resolution, snapshot = _resolve(envelope, facts=facts, design=design)
    payload = build_confirmed_research_task().model_dump(mode="json", by_alias=True)
    payload["researchDesignVersions"] = [design.model_dump(mode="json", by_alias=True)]
    payload["registrySnapshotRef"] = {"schemaVersion": 1, "contentHash": snapshot.content_hash}
    task = ResearchTaskV12.model_validate(payload)
    task_before = task.model_dump(mode="json", by_alias=True)
    readiness = {}
    for evaluation in resolution.candidate_evaluations:
        if isinstance(evaluation.candidate_ref, MultiAgentRecipeRef):
            continue
        identity = _candidate_id(evaluation)
        ready = facts.runtime_readiness.get(identity, False)
        readiness[identity] = RuntimeReadinessSnapshot.model_validate({
            "capabilityRef": evaluation.candidate_ref.model_dump(mode="json", by_alias=True),
            "ready": ready,
            "runtimeBindingId": f"runtime:{identity}" if ready else None,
            "checkedAt": "2026-07-28T00:00:03Z",
            "expiresAt": "2026-07-28T01:00:03Z",
            "reason": None if ready else "runtime_unavailable",
        })
    try:
        prepared = _module("research_execution_decision_service").prepare_selection(
            task, instance_id="sub_contract_full", resolution=resolution,
            snapshot_envelope=snapshot.envelope, facts=facts, runtime_readiness=readiness,
            selection_request_id="fallback-evidence", selection_command_hash="fallback-evidence",
            expected_task_revision=task.revision, owner_id="owner_contract", workspace_id="workspace_contract",
            decided_at=datetime.fromisoformat("2026-07-28T00:00:04+00:00"),
        )
    finally:
        assert task.model_dump(mode="json", by_alias=True) == task_before
    return task, resolution, snapshot, prepared


def _general_reasoning_fallback_selection(
    substage_definition_id: str,
    *,
    workflow_id: str = "generic_reasoning_workflow",
    workflow_version: int = 1,
    judgment_requirement: str = "domain_professional",
    external_contract: dict | None = None,
):
    envelope, facts = _fallback_evidence_inputs()
    general = next(
        row
        for row in envelope["definitions"]["agents"]
        if row["agentKind"] == "general_research"
    )
    general["agentId"] = "general_research_agent"
    general_binding = next(
        row
        for row in envelope["bindings"]["agents"]
        if row["sourceAuthority"] == "general_fallback_policy"
    )
    general_binding.update(
        {
            "bindingId": (
                f"bind:{substage_definition_id}:agent:general_research_agent:primary"
            ),
            "definitionId": substage_definition_id,
            "agentId": "general_research_agent",
        }
    )
    envelope["definitions"]["agents"] = [general]
    envelope["definitions"]["skills"] = []
    envelope["bindings"]["agents"] = [general_binding]
    envelope["bindings"]["skills"] = []
    envelope["bindings"]["agentSkills"] = []
    envelope["recipes"] = []
    definition = envelope["definitions"]["substages"][0]
    definition.update(
        {
            "definitionId": substage_definition_id,
            "pack": "opentrajectory",
            "substageKey": substage_definition_id.split(":", 1)[-1],
        }
    )
    definition.update(
        _module("research_orchestration_resolver").parse_definition_spec(
            "opentrajectory",
            "specs/example.md",
            "# Bounded reasoning\n\nNo dedicated Skill or structured contract.\n",
        )
    )
    definition["externalActionContract"] = external_contract or {
        "status": "known", "requiredActionKinds": [],
    }
    envelope["definitions"]["packs"] = [
        {
            "pack": "opentrajectory",
            "definitionVersion": 1,
            "source": {"key": "opentrajectory"},
        }
    ]
    facts = facts.model_copy(
        update={
            "runtime_readiness": {"agent:general_research_agent:1": True},
            "authorized_candidate_refs": {"agent:general_research_agent:1"},
            "applicable_rule_refs": set(),
        }
    )

    design_payload = _design_with_judgment(judgment_requirement).model_dump(
        mode="json", by_alias=True
    )
    design_payload["workflowProvenanceRef"] = {
        "workflowId": workflow_id,
        "workflowVersion": workflow_version,
        "contentHash": f"sha256:{'d' * 64}",
    }
    design_payload["instances"][0]["substageDefinitionId"] = (
        substage_definition_id
    )
    design = ResearchDesignVersion.model_validate(design_payload)
    return _prepare_fallback_evidence(envelope, facts, design=design)


@pytest.mark.parametrize(
    "judgment_requirement,workflow_id,workflow_version",
    (
        ("domain_professional", "generic_reasoning_workflow", 1),
        ("deterministic", "other_workflow", 2),
        ("research_synthesis", "freeform_reasoning", 4),
    ),
)
def test_general_reasoning_without_dedicated_skill_gets_real_completion_authority(
    judgment_requirement: str,
    workflow_id: str,
    workflow_version: int,
) -> None:
    task, resolution, _snapshot, prepared = _general_reasoning_fallback_selection(
        "opentrajectory:bounded_reasoning",
        workflow_id=workflow_id,
        workflow_version=workflow_version,
        judgment_requirement=judgment_requirement,
    )
    service = _module("research_execution_decision_service")
    identity = "agent:general_research_agent:1"
    preparation = service.prepare_selection_execution(prepared, task, identity)
    materialized = service.freeze_selection_execution(prepared, task, preparation)
    decision = materialized.decision
    work = materialized.work_units[0]

    assert prepared.final_tie_candidate_ids == (identity,)
    assert decision.strategy == "general_research_agent"
    assert decision.purpose == "completion"
    assert decision.completion_policy is not None
    assert decision.progress_policy is None
    assert decision.assignments[0].completion_coverage == "completion_capable"
    assert decision.assignments[0].acceptance_criteria[0].judgment_requirement == (
        judgment_requirement
    )
    assert work.expected_artifact_types == ["evidence_summary"]
    assert work.substage_definition_id == "opentrajectory:bounded_reasoning"
    assert decision.candidate_evaluations == resolution.candidate_evaluations


@pytest.mark.parametrize(
    "external_contract,judgment_requirement",
    (
        ({"status": "known", "requiredActionKinds": ["external_read"]}, "deterministic"),
        ({"status": "known", "requiredActionKinds": ["external_compute"]}, "deterministic"),
        ({"status": "unknown", "requiredActionKinds": []}, "deterministic"),
        ({"status": "known", "requiredActionKinds": []}, "human_authority"),
    ),
)
def test_general_reasoning_does_not_gain_external_or_human_authority(
    external_contract: dict,
    judgment_requirement: str,
) -> None:
    task, _resolution, _snapshot, prepared = _general_reasoning_fallback_selection(
        "opentrajectory:bounded_reasoning",
        judgment_requirement=judgment_requirement,
        external_contract=external_contract,
    )
    decision = _module("research_execution_decision_service").prepare_selection_execution(
        prepared, task, "agent:general_research_agent:1"
    ).decision

    assert decision.purpose == "progress"
    assert decision.completion_policy is None
    assert decision.progress_policy is not None
    assert decision.assignments[0].completion_coverage == "progress_capable"


def _freeze_and_assert_fallback_evidence(
    selection, *, reason: str | None, identity: str = "agent:general-research:1",
):
    """Check actual producer output and WorkUnit materialization, not a hand-built Decision."""
    task, resolution, snapshot, prepared = selection
    service = _module("research_execution_decision_service")
    before = task.model_dump(mode="json", by_alias=True)
    evaluations = [row.model_dump(mode="json", by_alias=True) for row in resolution.candidate_evaluations]
    assert prepared.final_tie_candidate_ids == (identity,)
    preparation = service.prepare_selection_execution(prepared, task, identity)
    materialized = service.freeze_selection_execution(prepared, task, preparation)
    decision = materialized.decision
    general = identity == "agent:general-research:1"
    selected = next(row for row in resolution.candidate_evaluations if _candidate_id(row) == identity)
    expected_refs = {
        row.candidate_evaluation_id for row in resolution.candidate_evaluations
        if _candidate_id(row) != "agent:general-research:1"
    } if general else {selected.candidate_evaluation_id}

    assert [row.model_dump(mode="json", by_alias=True) for row in decision.candidate_evaluations] == evaluations
    assert [row.model_dump(mode="json", by_alias=True) for row in resolution.candidate_evaluations] == evaluations
    assert set(decision.preferred_candidate_evaluation_refs) == expected_refs
    assert len(decision.preferred_candidate_evaluation_refs) == len(expected_refs)
    assert decision.fallback_reason_code == reason
    assert decision.fallback_invoked is general
    assert decision.strategy == ("general_research_agent" if general else "specialist_agent")
    assert decision.purpose == ("progress" if general else "completion")
    assert decision.assignments[0].source_binding_refs == selected.source_binding_refs
    assert decision.registry_snapshot_ref == resolution.registry_snapshot_ref
    frozen_bindings = {
        (row["bindingId"], row["bindingVersion"])
        for kind in ("skills", "agents") for row in snapshot.envelope["bindings"][kind]
    }
    assert all(
        (ref.binding_id, ref.binding_version) in frozen_bindings
        for row in decision.candidate_evaluations for ref in row.source_binding_refs
    )
    assert len(materialized.work_units) == 1
    work = materialized.work_units[0]
    assert work.execution_decision_id == decision.execution_decision_id
    assert work.assignment_id == decision.assignments[0].assignment_id
    assert work.executor_ref == decision.assignments[0].executor_ref
    assert work.registry_snapshot_ref == decision.registry_snapshot_ref
    assert work.substage_instance_id == "sub_contract_full"
    assert work.status == "queued"
    if general:
        assert decision.completion_policy is None
        assert decision.progress_policy is not None
        assert work.expected_artifact_types == []
        assert work.acceptance_criteria == []
    assert task.model_dump(mode="json", by_alias=True) == before
    assert materialized.task.research_design_versions == task.research_design_versions
    assert materialized.task.artifacts == task.artifacts
    assert materialized.task.completion_decisions == task.completion_decisions == []
    assert materialized.task.final_results == task.final_results == []
    assert materialized.task.work_units == [*task.work_units, work]
    return materialized


@pytest.mark.parametrize(
    "contract_change,rejection",
    [
        ("output", "output_incompatible"),
        ("input", "input_incompatible"),
        ("professional_judgment", "semantic_fit_insufficient"),
    ],
)
def test_fallback_evidence_preserves_rejected_contract_candidates(contract_change: str, rejection: str) -> None:
    """Known-bad f0d2aeac loses all refs and raises fallback_preferred_evidence_missing."""
    envelope, facts = _fallback_evidence_inputs()
    for skill in envelope["definitions"]["skills"]:
        if contract_change == "output":
            skill["outputContract"]["artifactTypes"] = ["other_output"]
        elif contract_change == "input":
            skill["inputContract"]["acceptedInputRefs"].append("additional_material")
    for agent in envelope["definitions"]["agents"]:
        if agent["agentKind"] == "general_research":
            continue
        if contract_change == "output":
            agent["outputContract"]["artifactKind"] = "other_output"
        elif contract_change == "input":
            agent["inputContract"]["required"].append("additional_material")
        else:
            agent["judgmentSupport"]["domainProfessional"] = False
    selection = _prepare_fallback_evidence(envelope, facts)
    preferred = [row for row in selection[1].candidate_evaluations if _candidate_id(row) != "agent:general-research:1"]
    assert preferred and all(not row.eligible and rejection in row.rejection_codes for row in preferred)
    _freeze_and_assert_fallback_evidence(selection, reason="no_completion_coverage_preferred_candidate")


@pytest.mark.parametrize("blocker", ["runtime_unavailable", "provider_unavailable", "authorization_denied"])
def test_fallback_evidence_reason_uses_completion_candidate_blockers(blocker: str) -> None:
    envelope, facts = _fallback_evidence_inputs()
    preferred_id = "agent:specialist-a:1"
    if blocker == "runtime_unavailable":
        facts.runtime_readiness[preferred_id] = False
    elif blocker == "authorization_denied":
        facts.authorized_candidate_refs.remove(preferred_id)
    else:
        specialist = next(row for row in envelope["definitions"]["agents"] if row["agentId"] == "specialist-a")
        specialist["allowedProviders"] = ["provider-specialist"]
        specialist["defaultProvider"] = "provider-specialist"
        facts.provider_readiness["provider-specialist"] = False
    selection = _prepare_fallback_evidence(envelope, facts)
    preferred = next(row for row in selection[1].candidate_evaluations if _candidate_id(row) == preferred_id)
    assert preferred.eligible and preferred.preference.completion_coverage == "completion_capable"
    assert preferred.preference.execution_blocker_codes == [blocker]
    reason = {
        "runtime_unavailable": "preferred_not_executable_runtime",
        "provider_unavailable": "preferred_not_executable_provider",
        "authorization_denied": "preferred_not_executable_authorization",
    }[blocker]
    _freeze_and_assert_fallback_evidence(selection, reason=reason)


def test_fallback_evidence_keeps_mixed_rejected_progress_and_completion_candidates() -> None:
    envelope, facts = _fallback_evidence_inputs()
    support = next(row for row in envelope["definitions"]["agents"] if row["agentId"] == "specialist-b")
    support["judgmentSupport"]["domainProfessional"] = True
    facts.authorized_candidate_refs.remove("agent:specialist-b:1")
    facts.runtime_readiness["agent:specialist-a:1"] = False
    selection = _prepare_fallback_evidence(envelope, facts)
    rows = {_candidate_id(row): row for row in selection[1].candidate_evaluations}
    assert not rows["skill:skill-a:1"].eligible
    assert rows["agent:specialist-b:1"].eligible
    assert rows["agent:specialist-b:1"].preference.completion_coverage == "progress_capable"
    assert rows["agent:specialist-b:1"].preference.execution_blocker_codes == ["authorization_denied"]
    assert rows["agent:specialist-a:1"].eligible
    assert rows["agent:specialist-a:1"].preference.completion_coverage == "completion_capable"
    assert rows["agent:specialist-a:1"].preference.execution_blocker_codes == ["runtime_unavailable"]
    _freeze_and_assert_fallback_evidence(selection, reason="preferred_not_executable_runtime")


def test_fallback_evidence_keeps_recipe_and_constituent_provenance() -> None:
    envelope, facts = _envelope(), _facts()
    for agent in envelope["definitions"]["agents"]:
        if agent["agentKind"] != "general_research":
            agent["judgmentSupport"]["domainProfessional"] = True
            facts.runtime_readiness[f"agent:{agent['agentId']}:1"] = False
    selection = _prepare_fallback_evidence(envelope, facts)
    recipe = next(row for row in selection[1].candidate_evaluations if isinstance(row.candidate_ref, MultiAgentRecipeRef))
    assert recipe.eligible and recipe.preference.completion_coverage == "completion_capable"
    assert recipe.preference.execution_blocker_codes == ["runtime_unavailable"]
    assert len(recipe.source_binding_refs) == 2
    _freeze_and_assert_fallback_evidence(selection, reason="preferred_not_executable_runtime")


def test_fallback_evidence_does_not_displace_an_available_completion_candidate() -> None:
    envelope, facts = _fallback_evidence_inputs()
    selection = _prepare_fallback_evidence(envelope, facts)
    _freeze_and_assert_fallback_evidence(selection, reason=None, identity="agent:specialist-a:1")
    task, _resolution, _snapshot, prepared = selection
    service = _module("research_execution_decision_service")
    with pytest.raises(service.DecisionPolicyViolation, match="fairness_selected_candidate_invalid"):
        service.prepare_selection_execution(prepared, task, "agent:general-research:1")


def test_fallback_evidence_without_preferred_does_not_invent_references() -> None:
    envelope, facts = _fallback_evidence_inputs()
    envelope["definitions"]["skills"] = []
    envelope["definitions"]["agents"] = [row for row in envelope["definitions"]["agents"] if row["agentKind"] == "general_research"]
    envelope["bindings"]["skills"] = []
    envelope["bindings"]["agentSkills"] = []
    envelope["bindings"]["agents"] = [row for row in envelope["bindings"]["agents"] if row["sourceAuthority"] == "general_fallback_policy"]
    facts.authorized_candidate_refs.intersection_update({"agent:general-research:1"})
    facts.runtime_readiness.clear()
    facts.runtime_readiness["agent:general-research:1"] = True
    selection = _prepare_fallback_evidence(envelope, facts)
    assert len(selection[1].candidate_evaluations) == 1
    _freeze_and_assert_fallback_evidence(selection, reason="no_completion_coverage_preferred_candidate")


def test_fallback_resolution_preserves_missing_research_input_facts() -> None:
    envelope, facts = _fallback_evidence_inputs()
    facts.available_input_refs.clear()
    result, _ = _resolve(
        envelope, facts=facts, design=_design_with_judgment("deterministic")
    )
    assert facts.available_input_refs == set()
    assert result.candidate_evaluations
    for candidate in result.candidate_evaluations:
        assert candidate.eligible
        assert "required_input_missing" in candidate.preference.execution_blocker_codes
        assert "authorization_denied" not in candidate.preference.execution_blocker_codes
        assert "runtime_unavailable" not in candidate.preference.execution_blocker_codes
        assert not candidate.preference.executable_now
