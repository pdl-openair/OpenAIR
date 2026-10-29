"""Contract tests for the CS reproduction v2 execution infrastructure."""

from __future__ import annotations

import json
from pathlib import Path

from app.models.research_orchestration import (
    AcceptanceCriterion,
    AgentRef,
    InstanceRef,
    ResearchDesignRef,
    ResearchDesignVersion,
    RegistrySnapshotRef,
    SkillRef,
    SubstageInstance,
)
from app.modules.registry_snapshot_repository import InMemoryRegistrySnapshotRepository
from app.modules.research_orchestration_resolver import (
    ResearchOrchestrationResolver,
    ResolutionFacts,
    build_registry_snapshot,
)


ROOT = Path(__file__).resolve().parents[3]
SHARED_TASK = (
    ROOT
    / "packages"
    / "shared"
    / "test-fixtures"
    / "research-orchestration-v1.2-valid.json"
)

CS_DEFINITION_IDS = {
    "cs_reproduction:reproduction_baseline_criteria",
    "cs_reproduction:reproduction_experiment_design",
    "cs_reproduction:execution_readiness_validation",
    "cs_reproduction:real_execution_result_acquisition",
    "cs_reproduction:result_comparability_judgment",
    "cs_reproduction:reproduction_conclusion_recommendation",
    "cs_reproduction:deviation_diagnosis_validation",
}


def _required_values(definition: dict, contract: str, field: str) -> list[str]:
    return list(definition[contract][field]["values"])


def _latest_definition(envelope: dict, definition_id: str) -> dict:
    return max(
        (
            row
            for row in envelope["definitions"]["substages"]
            if row["definitionId"] == definition_id
        ),
        key=lambda row: row["definitionVersion"],
    )


def _schema_declares_field(schema: dict, field: str) -> bool:
    path = field.split(".")

    def declares(candidate: dict) -> bool:
        for segment in path:
            candidate = candidate.get("properties", {}).get(segment)
            if not isinstance(candidate, dict):
                return False
        return True

    if declares(schema):
        return True
    definitions = schema.get("definitions", {})
    for branch in schema.get("oneOf", []):
        ref = branch.get("$ref") if isinstance(branch, dict) else None
        prefix = "#/definitions/"
        if isinstance(ref, str) and ref.startswith(prefix):
            candidate = definitions.get(ref.removeprefix(prefix))
            if isinstance(candidate, dict) and declares(candidate):
                return True
    return False


def _nested_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {
            key for child in value.values() for key in _nested_keys(child)
        }
    if isinstance(value, list):
        return {key for child in value for key in _nested_keys(child)}
    return set()


def _base_design() -> ResearchDesignVersion:
    payload = json.loads(SHARED_TASK.read_text(encoding="utf-8"))
    return ResearchDesignVersion.model_validate(
        payload["researchTask"]["researchDesignVersions"][0]
    )


def _design_for_definition(
    base: ResearchDesignVersion,
    definition: dict,
) -> ResearchDesignVersion:
    inputs = _required_values(definition, "inputContract", "requiredInputRefs")
    outputs = _required_values(definition, "outputContract", "artifactTypes")
    criteria = [
        AcceptanceCriterion.model_validate(
            {
                "criterionId": f"criterion_cs_{index}",
                "description": "The result satisfies the frozen professional contract.",
                "criterionKind": "artifact_quality",
                "coverageTargetIds": ["target_contract_full"],
                "requiredArtifactType": artifact_type,
                "judgmentRequirement": "domain_professional",
                "evaluatorType": "agent",
                "evaluatorRef": "cs-professional-contract",
            }
        )
        for index, artifact_type in enumerate(outputs)
    ]
    instance = SubstageInstance.model_validate(
        {
            "substageInstanceId": "sub_contract_full",
            "instanceRevision": 1,
            "substageDefinitionId": definition["definitionId"],
            "substageDefinitionVersion": definition["definitionVersion"],
            "stageKey": definition["stage"],
            "objective": "Produce the bounded CS reproduction result.",
            "inputRefs": inputs,
            "expectedArtifactTypes": outputs,
            "acceptanceCriteria": [
                row.model_dump(mode="json", by_alias=True) for row in criteria
            ],
            "dependencies": [],
            "sequenceHint": 0,
            "repeatableReentryAuthority": None,
        }
    )
    return base.model_copy(update={"instances": [instance]}, deep=True)


def _resolution_facts(
    envelope: dict, definition_id: str, definition_version: int
) -> ResolutionFacts:
    skills = {row["skillId"]: row for row in envelope["definitions"]["skills"]}
    agents = {row["agentId"]: row for row in envelope["definitions"]["agents"]}
    skill_bindings = [
        row
        for row in envelope["bindings"]["skills"]
        if row["definitionId"] == definition_id
        and row["definitionVersion"] == definition_version
        and row["enabled"]
        and skills[row["skillId"]]["enabled"]
        and skills[row["skillId"]]["status"] == "ready"
    ]
    agent_bindings = [
        row
        for row in envelope["bindings"]["agents"]
        if row["definitionId"] == definition_id
        and row["definitionVersion"] == definition_version
        and row["enabled"]
        and agents[row["agentId"]]["enabled"]
    ]
    recipes = [
        row
        for row in envelope["recipes"]
        if row["substageDefinitionId"] == definition_id
        and row["substageDefinitionVersion"] == definition_version
        and row["enabled"]
    ]
    skill_ids = {
        f"skill:{row['skillId']}:{row['skillDefinitionVersion']}"
        for row in skill_bindings
    }
    agent_ids = {
        f"agent:{row['agentId']}:{row['agentDefinitionVersion']}"
        for row in agent_bindings
    }
    recipe_ids = {
        f"recipe:{row['recipeId']}:{row['recipeVersion']}" for row in recipes
    }
    definition = next(
        row
        for row in envelope["definitions"]["substages"]
        if row["definitionId"] == definition_id
        and row["definitionVersion"] == definition_version
    )
    return ResolutionFacts(
        runtimeReadiness={
            candidate_id: True for candidate_id in skill_ids | agent_ids
        },
        providerReadiness={
            provider_id: True
            for row in agent_bindings
            for provider_id in agents[row["agentId"]]["allowedProviders"]
        },
        authorizedCandidateRefs=skill_ids | agent_ids | recipe_ids,
        availableInputRefs=set(
            _required_values(definition, "inputContract", "requiredInputRefs")
        ),
        applicableRuleRefs={
            rule_ref for row in recipes for rule_ref in row["applicabilityRuleRefs"]
        },
        riskAllowed=True,
        budgetAvailable=True,
        policyVersion=envelope["staticPolicies"]["policyVersion"],
    )


def test_cs_v2_definitions_have_formal_completion_paths_without_static_composition() -> None:
    envelope = build_registry_snapshot().envelope
    definitions = {}
    for row in envelope["definitions"]["substages"]:
        if row["definitionId"] not in CS_DEFINITION_IDS:
            continue
        current = definitions.get(row["definitionId"])
        if current is None or row["definitionVersion"] > current["definitionVersion"]:
            definitions[row["definitionId"]] = row
    assert set(definitions) == CS_DEFINITION_IDS
    skills = {row["skillId"]: row for row in envelope["definitions"]["skills"]}

    for definition_id, definition in definitions.items():
        assert "executionComposition" not in definition
        bindings = [
            row
            for row in envelope["bindings"]["skills"]
            if (
                row["definitionId"] == definition_id
                and row["definitionVersion"] == definition["definitionVersion"]
                and row["enabled"]
            )
        ]
        assert bindings
        bound_skills = [skills[row["skillId"]] for row in bindings]
        method = definition.get("executionMethod")
        if method is None:
            expected_outputs = set(
                _required_values(definition, "outputContract", "artifactTypes")
            )
            assert any(
                expected_outputs
                <= set(skill["outputContract"]["artifactTypes"])
                and skill["judgmentSupport"]["domainProfessional"] is True
                for skill in bound_skills
            )
            continue

        assert not {"skillId", "agentId", "model"} & _nested_keys(method)
        candidates_by_role: dict[str, list[dict]] = {}
        for skill in bound_skills:
            for role_ref in skill.get("executionMethodRoles") or []:
                candidates_by_role.setdefault(role_ref, []).append(skill)

        completion_outputs: set[str] = set()
        for role in method["roles"]:
            role_ref = f"{method['methodId']}:{role['roleId']}"
            if role["requirement"] == "required":
                assert candidates_by_role.get(role_ref), role_ref
            contract = role["qualificationContract"]
            for skill in candidates_by_role.get(role_ref, []):
                assert set(contract["requiredInputRefs"]) <= set(
                    skill["inputContract"]["acceptedInputRefs"]
                )
                assert set(contract["outputArtifactTypes"]) <= set(
                    skill["outputContract"]["artifactTypes"]
                )
            if role["roleId"] in method["completionRoleIds"]:
                completion_outputs.update(contract["outputArtifactTypes"])
        assert completion_outputs == set(
            _required_values(definition, "outputContract", "artifactTypes")
        )


def test_cs_v2_registered_capabilities_resolve_for_every_definition_role() -> None:
    snapshot = build_registry_snapshot()
    envelope = snapshot.envelope
    repository = InMemoryRegistrySnapshotRepository()
    repository.put(snapshot)
    resolver = ResearchOrchestrationResolver(repository)
    base_design = _base_design()

    definitions_by_id: dict[str, dict] = {}
    for row in envelope["definitions"]["substages"]:
        if row["definitionId"] not in CS_DEFINITION_IDS:
            continue
        current = definitions_by_id.get(row["definitionId"])
        if current is None or row["definitionVersion"] > current["definitionVersion"]:
            definitions_by_id[row["definitionId"]] = row
    definitions = list(definitions_by_id.values())
    for definition in definitions:
        design = _design_for_definition(base_design, definition)
        result = resolver.resolve(
            design=design,
            designRef=ResearchDesignRef(
                researchDesignId=design.research_design_id,
                version=design.version,
            ),
            instanceRef=InstanceRef(
                substageInstanceId="sub_contract_full", instanceRevision=1
            ),
            registrySnapshotRef=RegistrySnapshotRef(
                schemaVersion=snapshot.schema_version,
                contentHash=snapshot.content_hash,
            ),
                facts=_resolution_facts(
                    envelope,
                    definition["definitionId"],
                    definition["definitionVersion"],
                ),
        )
        method = definition.get("executionMethod")
        if method is None:
            assert any(
                isinstance(row.candidate_ref, SkillRef)
                and row.eligible
                and row.preference.executable_now
                and row.preference.completion_coverage == "completion_capable"
                for row in result.candidate_evaluations
            )
            continue

        for role in method["roles"]:
            if role["requirement"] != "required":
                continue
            role_ref = f"{method['methodId']}:{role['roleId']}"
            role_evaluations = [
                role_evaluation
                for candidate in result.candidate_evaluations
                if isinstance(candidate.candidate_ref, (SkillRef, AgentRef))
                for role_evaluation in candidate.execution_method_role_evaluations
                if role_evaluation.role_ref == role_ref
            ]
            assert any(
                row.eligible and row.executable_now for row in role_evaluations
            ), role_ref


def test_cs_v2_bindings_target_registered_inputs_and_real_source_fields() -> None:
    envelope = build_registry_snapshot().envelope
    skills = {row["skillId"]: row for row in envelope["definitions"]["skills"]}
    bindings = {
        (row["bindingId"], row["bindingVersion"]): row
        for row in envelope["bindings"]["skills"]
    }
    cs_bindings = [
        row
        for row in bindings.values()
        if row["definitionId"] in CS_DEFINITION_IDS
    ]

    for binding in cs_bindings:
        target_skill = skills[binding["skillId"]]
        accepted_inputs = set(target_skill["inputContract"]["acceptedInputRefs"])
        declared_fields = [
            field
            for contract_name in ("inputSources", "runtimeInputs")
            for field in (binding.get(contract_name) or {}).get("fields") or []
        ]
        assert {field["targetField"] for field in declared_fields} <= accepted_inputs

        for field in (binding.get("inputSources") or {}).get("fields") or []:
            source = field["source"]
            if source["kind"] != "artifact":
                continue
            source_binding = bindings[(source["bindingId"], source["bindingVersion"])]
            output_schema = skills[source_binding["skillId"]]["sourceContract"][
                "frozenSchemas"
            ]["output"]["schema"]
            assert source["field"] == "$" or _schema_declares_field(
                output_schema, source["field"]
            )


def test_cs_baseline_grounding_reuses_formal_reference_chain_with_exact_lineage() -> None:
    envelope = build_registry_snapshot().envelope
    definition_id = "cs_reproduction:reproduction_baseline_criteria"
    definition = _latest_definition(envelope, definition_id)
    method = definition["executionMethod"]
    assert method["methodId"] == "cs_reproduction_baseline_grounding"
    assert [row["roleId"] for row in method["roles"]] == [
        "input_structuring",
        "source_identity_resolution",
        "code_identity_acquisition",
        "official_source_acquisition",
        "reference_interpretation",
        "reference_fact_assembly",
        "baseline_closure",
    ]
    assert method["completionRoleIds"] == ["baseline_closure"]

    skills = {row["skillId"]: row for row in envelope["definitions"]["skills"]}
    bindings = {
        row["skillId"]: row
        for row in envelope["bindings"]["skills"]
        if row["definitionId"] == definition_id
    }
    role_skills = {
        "input_structuring": "cs_reproduction_input_structuring",
        "source_identity_resolution": "cs_paper_source_resolution",
        "code_identity_acquisition": "repository_revision_acquisition",
        "official_source_acquisition": "official_reference_source_acquisition",
        "reference_interpretation": "reference_metric_interpretation",
        "reference_fact_assembly": "reference_metric_fact_assembly",
        "baseline_closure": "reproduction_baseline_and_criteria",
    }
    assert set(bindings) == set(role_skills.values())
    for role_id, skill_id in role_skills.items():
        assert (
            f"{method['methodId']}:{role_id}"
            in skills[skill_id]["executionMethodRoles"]
        )

    expected_artifact_sources = {
        ("cs_paper_source_resolution", "repository"): (
            "cs_reproduction_input_structuring",
            "repository",
            "conditional",
        ),
        ("cs_paper_source_resolution", "revision"): (
            "cs_reproduction_input_structuring",
            "revision",
            "conditional",
        ),
        ("repository_revision_acquisition", "repository"): (
            "cs_paper_source_resolution",
            "repository",
            "required",
        ),
        ("repository_revision_acquisition", "revision"): (
            "cs_paper_source_resolution",
            "requested_revision",
            "required",
        ),
        ("repository_revision_acquisition", "source_evidence"): (
            "cs_paper_source_resolution",
            "source_evidence",
            "required",
        ),
        ("official_reference_source_acquisition", "code_source"): (
            "repository_revision_acquisition",
            "$",
            "required",
        ),
        ("reference_metric_interpretation", "source_package"): (
            "official_reference_source_acquisition",
            "$",
            "required",
        ),
        ("execution_plan_interpretation", "reproduction_baseline_and_criteria"): (
            "reproduction_baseline_and_criteria",
            "$",
            "required",
        ),
        ("reference_metric_fact_assembly", "source_package"): (
            "official_reference_source_acquisition",
            "$",
            "required",
        ),
        ("reference_metric_fact_assembly", "interpretation"): (
            "reference_metric_interpretation",
            "$",
            "required",
        ),
        ("reproduction_baseline_and_criteria", "reference_facts"): (
            "reference_metric_fact_assembly",
            "$",
            "optional",
        ),
    }
    bindings_by_id = {
        (row["bindingId"], row["bindingVersion"]): row
        for row in envelope["bindings"]["skills"]
    }
    experiment_definition = _latest_definition(
        envelope, "cs_reproduction:reproduction_experiment_design"
    )
    for (skill_id, target), expected in expected_artifact_sources.items():
        target_binding = bindings.get(skill_id)
        if target_binding is None:
            target_binding = next(
                row
                for row in envelope["bindings"]["skills"]
                if row["skillId"] == skill_id
                and row["definitionId"] == experiment_definition["definitionId"]
                and row["definitionVersion"]
                == experiment_definition["definitionVersion"]
            )
        field = next(
            row
            for row in target_binding["inputSources"]["fields"]
            if row["targetField"] == target
        )
        source_binding = bindings_by_id[
            (field["source"]["bindingId"], field["source"]["bindingVersion"])
        ]
        assert (
            source_binding["skillId"],
            field["source"]["field"],
            field["sourcePresence"],
        ) == expected


def test_cs_baseline_grounding_degrades_without_inventing_reference_facts() -> None:
    envelope = build_registry_snapshot().envelope
    definition_id = "cs_reproduction:reproduction_baseline_criteria"
    definition = _latest_definition(envelope, definition_id)
    roles = {
        row["roleId"]: row for row in definition["executionMethod"]["roles"]
    }
    assert {
        roles[role_id]["failurePolicy"]
        for role_id in (
            "input_structuring",
            "code_identity_acquisition",
            "official_source_acquisition",
            "reference_interpretation",
            "reference_fact_assembly",
        )
    } == {"continue_degraded"}
    assert roles["baseline_closure"]["failurePolicy"] == "fail_closed"

    skills = {row["skillId"]: row for row in envelope["definitions"]["skills"]}
    baseline = skills["reproduction_baseline_and_criteria"]
    input_schema = baseline["sourceContract"]["frozenSchemas"]["input"]["schema"]
    assert "reference_facts" in input_schema["properties"]
    assert "reference_facts" not in input_schema["required"]

    actual_isolation_contract = {
        "bindings": [
            row
            for row in envelope["bindings"]["skills"]
            if row["definitionId"] == definition_id
        ],
        "input_schemas": {
            skill_id: skills[skill_id]["sourceContract"]["frozenSchemas"]["input"]
            for skill_id in (
                "cs_reproduction_input_structuring",
                "repository_revision_acquisition",
                "official_reference_source_acquisition",
                "reference_metric_interpretation",
                "reference_metric_fact_assembly",
                "reproduction_baseline_and_criteria",
            )
        },
    }
    assert "actual_metric" not in json.dumps(
        actual_isolation_contract, ensure_ascii=False
    )


def test_real_execution_is_released_only_from_frozen_verified_inputs() -> None:
    envelope = build_registry_snapshot().envelope
    definition_id = "cs_reproduction:real_execution_result_acquisition"
    definition = next(
        row
        for row in envelope["definitions"]["substages"]
        if row["definitionId"] == definition_id
        and row["definitionVersion"] == 2
    )
    roles = {
        row["roleId"]: row for row in definition["executionMethod"]["roles"]
    }
    assert roles["execution_input_closure"]["failurePolicy"] == "fail_closed"
    assert roles["experiment_execution"]["dependsOn"] == ["execution_input_closure"]

    bindings = {
        row["skillId"]: row
        for row in envelope["bindings"]["skills"]
        if row["definitionId"] == definition_id
        and row["definitionVersion"] == 2
    }
    gate = bindings["reproduction_execution_input_closure"]
    gate_sources = {
        row["targetField"]: row for row in gate["inputSources"]["fields"]
    }
    assert set(gate_sources) == {
        "reproduction_experiment_design",
        "execution_plan_facts",
        "execution_readiness_record",
        "code_identity",
        "resource_identities",
        "environment_record",
    }

    run_sources = bindings["exploratory_run"]["inputSources"]["fields"]
    assert {
        row["source"]["bindingId"] for row in run_sources
    } == {
        "bind:cs_reproduction:real_execution_result_acquisition:skill:reproduction_execution_input_closure"
    }
    metric_sources = {
        row["targetField"]: row
        for row in bindings["actual_metric_extraction"]["inputSources"]["fields"]
    }
    for target in ("result_source", "metrics"):
        assert metric_sources[target]["source"]["bindingId"] == (
            "bind:cs_reproduction:real_execution_result_acquisition:skill:reproduction_execution_input_closure"
        )
    for skill_id in (
        "reproduction_execution_record_closure",
        "actual_metric_records_closure",
    ):
        fields = {
            row["targetField"]: row
            for row in bindings[skill_id]["inputSources"]["fields"]
        }
        assert fields["frozen_execution_input"]["source"]["bindingId"] == (
            "bind:cs_reproduction:real_execution_result_acquisition:skill:reproduction_execution_input_closure"
        )
