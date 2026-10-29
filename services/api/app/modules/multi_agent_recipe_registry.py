"""Validated, versioned multi-Agent recipe registry."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError
import yaml

from app.models.research_orchestration import MultiAgentRecipeDefinition
from app.modules.repo_paths import repo_root


REGISTRY_PATH = repo_root() / "schemas" / "agents" / "multi-agent-recipes.yaml"


class MultiAgentRecipeRegistryError(ValueError):
    """Raised when Recipe authority is missing, ambiguous, or invalid."""


@dataclass(frozen=True)
class MultiAgentRecipeRegistry:
    schema_version: int
    recipes: dict[tuple[str, int], MultiAgentRecipeDefinition]

    def get(self, recipe_id: str, recipe_version: int) -> MultiAgentRecipeDefinition | None:
        return self.recipes.get((recipe_id, recipe_version))

    def list_recipes(self) -> list[MultiAgentRecipeDefinition]:
        return [self.recipes[key] for key in sorted(self.recipes)]


def build_multi_agent_recipe_registry(payload: dict[str, Any]) -> MultiAgentRecipeRegistry:
    if not isinstance(payload, dict):
        raise MultiAgentRecipeRegistryError("Recipe Registry payload must be a mapping")
    schema_version = payload.get("schemaVersion")
    if not isinstance(schema_version, int) or isinstance(schema_version, bool) or schema_version < 1:
        raise MultiAgentRecipeRegistryError("Recipe Registry schemaVersion must be a positive integer")
    rows = payload.get("recipes")
    if not isinstance(rows, list):
        raise MultiAgentRecipeRegistryError("Recipe Registry recipes must be a list")

    recipes: dict[tuple[str, int], MultiAgentRecipeDefinition] = {}
    for index, row in enumerate(rows):
        try:
            recipe = MultiAgentRecipeDefinition.model_validate(row)
        except ValidationError as exc:
            raise MultiAgentRecipeRegistryError(f"invalid recipe topology at index {index}: {exc}") from exc
        identity = (recipe.recipe_id, recipe.recipe_version)
        if identity in recipes:
            raise MultiAgentRecipeRegistryError(f"duplicate Recipe identity: {identity[0]}@{identity[1]}")
        recipes[identity] = recipe
    return MultiAgentRecipeRegistry(schema_version=schema_version, recipes=recipes)


def load_multi_agent_recipe_registry(path: Path = REGISTRY_PATH) -> MultiAgentRecipeRegistry:
    if not path.exists():
        raise MultiAgentRecipeRegistryError(f"Recipe Registry file does not exist: {path}")
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return build_multi_agent_recipe_registry(payload)
