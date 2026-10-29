from __future__ import annotations

from copy import deepcopy
from importlib import import_module, util
import json
from pathlib import Path

import pytest


RECIPE_PATH = Path(__file__).parents[3] / "schemas" / "agents" / "multi-agent-recipes.yaml"


def _module():
    name = "app.modules.multi_agent_recipe_registry"
    assert util.find_spec(name) is not None, "validated Recipe Registry boundary is absent"
    return import_module(name)


def _payload() -> dict:
    return {
        "schemaVersion": 1,
        "recipes": [
            {
                "recipeId": "producer-support",
                "recipeVersion": 1,
                "substageDefinitionId": "sdr:evidence_extraction",
                "substageDefinitionVersion": 1,
                "applicabilityRuleRefs": ["rule:substage-match"],
                "assignmentTemplates": [
                    {
                        "templateKey": "producer",
                        "role": "primary",
                        "requiredSupportedRole": "primary",
                        "objectiveTemplate": "Produce bounded evidence.",
                        "dependencyTemplateKeys": [],
                    },
                    {
                        "templateKey": "support",
                        "role": "support",
                        "requiredSupportedRole": "support",
                        "objectiveTemplate": "Support bounded evidence.",
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
                            "artifactType": "evidence_trace",
                            "minimumCount": 1,
                            "sourceTemplateKeys": ["producer", "support"],
                        }
                    ],
                },
                "enabled": True,
            }
        ],
    }


def test_recipe_registry_loads_only_explicit_validated_recipes() -> None:
    module = _module()
    registry = module.build_multi_agent_recipe_registry(_payload())
    assert [(row.recipe_id, row.recipe_version) for row in registry.list_recipes()] == [("producer-support", 1)]
    assert registry.get("unregistered-combination", 1) is None


def test_recipe_registry_rejects_duplicate_identity_and_invalid_topology() -> None:
    module = _module()
    duplicate = _payload()
    duplicate["recipes"].append(deepcopy(duplicate["recipes"][0]))
    with pytest.raises(module.MultiAgentRecipeRegistryError, match="duplicate"):
        module.build_multi_agent_recipe_registry(duplicate)

    cyclic = _payload()
    cyclic["recipes"][0]["assignmentTemplates"][0]["dependencyTemplateKeys"] = ["support"]
    with pytest.raises(module.MultiAgentRecipeRegistryError, match="dependency"):
        module.build_multi_agent_recipe_registry(cyclic)


def test_recipe_schema_requires_explicit_topology_and_assembly() -> None:
    schema = Path(__file__).parents[3] / "schemas" / "agents" / "multi-agent-recipes.schema.json"
    assert schema.exists(), "Recipe Registry schema is absent"
    payload = json.loads(schema.read_text(encoding="utf-8"))
    required = set(payload["$defs"]["recipe"]["required"])
    assert {"assignmentTemplates", "completionPolicyTemplate", "resultAssemblyPolicyTemplate", "enabled"} <= required


def test_production_recipe_registry_is_validated_and_nonempty() -> None:
    module = _module()
    assert RECIPE_PATH.resolve().exists()
    registry = module.load_multi_agent_recipe_registry(RECIPE_PATH.resolve())
    assert registry.list_recipes()
    recipe = registry.get("question_refinement_with_independent_review", 1)
    assert recipe is not None
    assert [row.template_key for row in recipe.assignment_templates] == ["producer", "reviewer"]
    assert recipe.assignment_templates[1].role.value == "reviewer"
    assert recipe.assignment_templates[1].dependency_template_keys == ["producer"]
    assert recipe.completion_policy_template.reviewer_template_keys == ["reviewer"]
