"""Build task execution graphs from selected capabilities.

This module treats pack catalogs as capability libraries. It does not expand a
pack into a full task workflow unless the caller explicitly selects every node.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

import yaml

from app.modules import pack_config
from app.modules.repo_paths import repo_root
from app.modules.task_constants import STAGE_ORDER


class ExecutionGraphError(ValueError):
    """Raised when a real execution graph cannot be derived safely."""


def _norm(value: str) -> str:
    return value.strip().replace("-", "_")


@lru_cache(maxsize=1)
def _skill_registry_entries() -> list[dict[str, Any]]:
    path = repo_root() / "skills" / "registry.yaml"
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    entries = data.get("skills") or []
    return [entry for entry in entries if isinstance(entry, dict)]


def _find_skill_entry(skill_id: str) -> dict[str, Any] | None:
    wanted = _norm(skill_id)
    for entry in _skill_registry_entries():
        ids = {
            _norm(str(entry.get("skill_id") or "")),
            _norm(str(entry.get("claude_name") or "")),
        }
        if wanted in ids:
            return entry
    return None


def _load_skill_yaml(entry: dict[str, Any]) -> dict[str, Any]:
    rel = entry.get("skill_path")
    if not isinstance(rel, str) or not rel.strip():
        return {}
    path = repo_root() / rel / "skill.yaml"
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data if isinstance(data, dict) else {}


def _skill_substages(skill_id: str) -> tuple[str, list[str]]:
    entry = _find_skill_entry(skill_id)
    if not entry:
        raise ExecutionGraphError(f"unknown skill: {skill_id}")
    skill_yaml = _load_skill_yaml(entry)
    raw = skill_yaml.get("substages")
    if not isinstance(raw, list) or not raw:
        raw = entry.get("substages")
    if not isinstance(raw, list) or not raw:
        raise ExecutionGraphError(f"skill has no substage metadata: {skill_id}")
    substages = [item.strip() for item in raw if isinstance(item, str) and item.strip()]
    if not substages:
        raise ExecutionGraphError(f"skill has no valid substage metadata: {skill_id}")
    canonical = str(entry.get("skill_id") or skill_id).strip() or skill_id
    return canonical, substages


def resolve_skill_substage_items(skill_ids: list[str]) -> list[dict[str, Any]]:
    """Resolve skill ids into selected substage rows using skill metadata."""

    selected: list[dict[str, Any]] = []
    for skill_id in skill_ids:
        if not isinstance(skill_id, str) or not skill_id.strip():
            continue
        canonical, substages = _skill_substages(skill_id)
        for substage in substages:
            selected.append({"substage": substage, "skills": [canonical]})
    return selected


def _enabled_pack_substage_keys(pack: str) -> set[str]:
    keys: set[str] = set()
    for entries in pack_config.load_stage_substage_map(pack=pack).values():
        for entry in entries:
            if entry.get("enabled", True):
                keys.add(entry["key"])
    return keys


def _validate_substage(pack: str, substage_key: str) -> str:
    enabled_keys = _enabled_pack_substage_keys(pack)
    if substage_key not in enabled_keys:
        if pack_config.get_substage_stage(substage_key, pack=pack):
            raise ExecutionGraphError(f"substage is disabled in pack {pack}: {substage_key}")
        raise ExecutionGraphError(f"substage is not available in pack {pack}: {substage_key}")
    stage_key = pack_config.get_substage_stage(substage_key, pack=pack)
    if not stage_key:
        raise ExecutionGraphError(f"substage has no stage in pack {pack}: {substage_key}")
    return stage_key


def build_creation_plan_stages_from_substages(
    substages: list[dict[str, Any]],
    *,
    pack: str = "sdr",
    stage_descriptions: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Return creationPlan.stages for selected substages only."""

    selected_by_stage: dict[str, dict[str, dict[str, Any]]] = {}
    for item in substages:
        substage_key = item.get("substage")
        if not isinstance(substage_key, str) or not substage_key.strip():
            continue
        substage_key = substage_key.strip()
        stage_key = _validate_substage(pack, substage_key)
        by_substage = selected_by_stage.setdefault(stage_key, {})
        row = by_substage.setdefault(substage_key, {"substage": substage_key, "skills": []})
        skills = item.get("skills")
        if isinstance(skills, list):
            for skill in skills:
                if isinstance(skill, str) and skill.strip() and skill not in row["skills"]:
                    row["skills"].append(skill.strip())
        rationale = item.get("rationale")
        if isinstance(rationale, str) and rationale.strip() and not row.get("rationale"):
            row["rationale"] = rationale.strip()
        expected_artifact_types = item.get("expectedArtifactTypes")
        if isinstance(expected_artifact_types, list):
            row.setdefault("expectedArtifactTypes", [])
            for artifact_type in expected_artifact_types:
                if (
                    isinstance(artifact_type, str)
                    and artifact_type.strip()
                    and artifact_type.strip() not in row["expectedArtifactTypes"]
                ):
                    row["expectedArtifactTypes"].append(artifact_type.strip())
        if isinstance(item.get("confirmationRequired"), bool):
            row["confirmationRequired"] = bool(item["confirmationRequired"])

    if not selected_by_stage:
        raise ExecutionGraphError("execution graph must include at least one substage")

    stage_descriptions = stage_descriptions or {}
    raw_stage_meta = pack_config.load_raw_stage_substage_map(pack=pack)
    stages: list[dict[str, Any]] = []
    for stage_key in STAGE_ORDER:
        rows_by_key = selected_by_stage.get(stage_key)
        if not rows_by_key:
            continue
        rows = list(rows_by_key.values())
        for row in rows:
            if not row["skills"]:
                row.pop("skills", None)
        fallback_description = (raw_stage_meta.get(stage_key) or {}).get("label") or stage_key
        stages.append(
            {
                "stageKey": stage_key,
                "description": stage_descriptions.get(stage_key) or fallback_description,
                "subStages": [row["substage"] for row in rows],
                "substages": rows,
            }
        )
    return stages


def build_creation_plan_stages_from_skill_ids(
    skill_ids: list[str],
    *,
    pack: str = "sdr",
    stage_descriptions: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    return build_creation_plan_stages_from_substages(
        resolve_skill_substage_items(skill_ids),
        pack=pack,
        stage_descriptions=stage_descriptions,
    )
