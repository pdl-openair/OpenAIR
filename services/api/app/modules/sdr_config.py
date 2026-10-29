"""Backward-compatible wrapper: delegates to pack_config with pack='sdr'."""

from __future__ import annotations

from app.modules.pack_config import (  # noqa: F401
    get_all_substage_keys,
    get_default_substage_for_stage,
    get_optional_substages,
    get_required_substages,
    get_substage_label,
    get_substage_skills,
    get_substage_spec,
    get_substage_stage,
    load_skill_id_to_substage,
    load_stage_substage_map,
)
