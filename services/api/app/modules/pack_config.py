"""Load skill-pack configuration from YAML files under schemas/{pack}/."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from app.modules.repo_paths import repo_root

logger = logging.getLogger(__name__)

_ROOT = repo_root()

# Dict-based cache: _cache[pack][filename] = parsed yaml dict
_cache: dict[str, dict[str, dict[str, Any]]] = {}


def _get_yaml(pack: str, name: str) -> dict[str, Any]:
    if pack not in _cache:
        _cache[pack] = {}
    if name not in _cache[pack]:
        # Try pack-specific path first, fall back to top-level for backward compat
        pack_path = _ROOT / "schemas" / pack / name
        if pack_path.exists():
            with open(pack_path, encoding="utf-8") as f:
                _cache[pack][name] = yaml.safe_load(f) or {}
        else:
            top_path = _ROOT / "schemas" / name
            if top_path.exists():
                with open(top_path, encoding="utf-8") as f:
                    _cache[pack][name] = yaml.safe_load(f) or {}
            else:
                logger.warning("Pack config file not found: %s/%s", pack, name)
                _cache[pack][name] = {}
    return _cache[pack][name]


_PACKS_CACHE: list[dict[str, Any]] | None = None


def list_available_packs() -> list[dict[str, Any]]:
    """Return descriptors from schemas/packs.yaml. Empty list if missing."""
    global _PACKS_CACHE
    if _PACKS_CACHE is None:
        path = _ROOT / "schemas" / "packs.yaml"
        if not path.exists():
            _PACKS_CACHE = []
        else:
            with open(path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            _PACKS_CACHE = list(data.get("packs") or [])
    return _PACKS_CACHE


def _stage_substage_map(pack: str = "sdr") -> dict[str, list[dict[str, Any]]]:
    return _get_yaml(pack, "stage-substage-map.yaml")


def _substage_skill_map(pack: str = "sdr") -> dict[str, list[str]]:
    return _get_yaml(pack, "substage-skill-map.yaml")


def _substage_spec_map(pack: str = "sdr") -> dict[str, str]:
    return _get_yaml(pack, "substage-spec-map.yaml")


def _research_type_overrides(pack: str = "sdr") -> dict[str, Any]:
    return _get_yaml(pack, "research_type_overrides.yaml")


def load_stage_substage_map(pack: str = "sdr") -> dict[str, list[dict[str, Any]]]:
    """Return catalog compatible with STAGE_SUBSTAGE_CATALOG format.

    Handles both old format (entries as list) and new format (entries under
    ``substages`` key with stage-level label/description).
    Adds ``stageKey`` field to each entry for backward compatibility.
    """
    raw = _stage_substage_map(pack)
    result: dict[str, list[dict[str, Any]]] = {}
    for stage_key, stage_data in raw.items():
        if isinstance(stage_data, dict):
            entries = stage_data.get("substages", [])
        elif isinstance(stage_data, list):
            entries = stage_data
        else:
            continue
        result[stage_key] = []
        for entry in entries:
            result[stage_key].append({
                "key": entry["key"],
                "stageKey": stage_key,
                "label": entry.get("label", entry["key"]),
                "description": entry.get("description", ""),
                "enabled": entry.get("enabled", True),
            })
    return result


def load_raw_stage_substage_map(pack: str = "sdr") -> dict[str, Any]:
    """Return the raw stage map, including stage-level label/description."""
    return _stage_substage_map(pack)


def get_substage_skills(substage_key: str, pack: str = "sdr") -> list[str]:
    """Return L3 skill_ids for a substage."""
    raw = _substage_skill_map(pack).get(substage_key, [])
    return [
        s["skill_id"] if isinstance(s, dict) else s
        for s in raw
    ]


def get_substage_skills_with_meta(substage_key: str, pack: str = "sdr") -> list[dict[str, str]]:
    """Return L3 skills with full metadata (skill_id, display_name, description) for orchestration prompt."""
    raw = _substage_skill_map(pack).get(substage_key, [])
    result: list[dict[str, str]] = []
    for s in raw:
        if isinstance(s, dict):
            result.append({
                "skill_id": s.get("skill_id", ""),
                "display_name": s.get("display_name", s.get("skill_id", "")),
                "description": s.get("description", ""),
            })
        else:
            result.append({"skill_id": str(s), "display_name": str(s), "description": ""})
    return result


def get_substage_spec(substage_key: str, pack: str = "sdr") -> str | None:
    return _substage_spec_map(pack).get(substage_key)


def _get_substage_entries(pack: str = "sdr") -> list[dict[str, Any]]:
    """Flatten all substage entries from the stage-substage map."""
    entries_out: list[dict[str, Any]] = []
    for stage_data in _stage_substage_map(pack).values():
        if isinstance(stage_data, dict):
            entries_out.extend(stage_data.get("substages", []))
        elif isinstance(stage_data, list):
            entries_out.extend(stage_data)
    return entries_out


def get_substage_label(substage_key: str, pack: str = "sdr") -> str:
    for entry in _get_substage_entries(pack):
        if entry["key"] == substage_key:
            return entry.get("label", substage_key)
    return substage_key


def get_all_substage_keys(pack: str = "sdr") -> list[str]:
    return [e["key"] for e in _get_substage_entries(pack)]


def get_default_substage_for_stage(stage_key: str, pack: str = "sdr") -> str | None:
    stage_data = _stage_substage_map(pack).get(stage_key)
    if isinstance(stage_data, dict):
        entries = stage_data.get("substages", [])
    elif isinstance(stage_data, list):
        entries = stage_data
    else:
        return None
    return entries[0]["key"] if entries else None


def get_required_substages(research_type: str, pack: str = "sdr") -> list[str]:
    overrides = _research_type_overrides(pack)
    entry = overrides.get(research_type, {})
    return entry.get("required_substages", [])


def get_optional_substages(research_type: str, pack: str = "sdr") -> list[str]:
    overrides = _research_type_overrides(pack)
    entry = overrides.get(research_type, {})
    return entry.get("optional_substages", [])


def load_skill_id_to_substage(pack: str = "sdr") -> dict[str, str]:
    """Load skill_id/claude_name -> substage_key mapping from skill-id-to-substage.yaml."""
    return _get_yaml(pack, "skill-id-to-substage.yaml")


def get_substage_stage(substage_key: str, pack: str = "sdr") -> str | None:
    for stage_key, stage_data in _stage_substage_map(pack).items():
        if isinstance(stage_data, dict):
            entries = stage_data.get("substages", [])
        elif isinstance(stage_data, list):
            entries = stage_data
        else:
            continue
        for entry in entries:
            if entry["key"] == substage_key:
                return stage_key
    return None
