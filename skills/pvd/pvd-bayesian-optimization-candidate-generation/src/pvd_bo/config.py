from __future__ import annotations

import json
from pathlib import Path
from typing import Any


EXPECTED_CONFIG_KEYS = {
    "evaluatorRef",
    "requiredRuntimeFactRefs",
    "scientificStateFactPath",
    "scientificStateFactSchema",
    "modelProfileId",
}


def load_capability_configuration(skill_root: Path) -> dict[str, Any]:
    config = json.loads(
        (skill_root / "applicability_evaluator.json").read_text(encoding="utf-8")
    )
    if not isinstance(config, dict) or set(config) != EXPECTED_CONFIG_KEYS:
        raise ValueError("applicability evaluator config is invalid")
    profile_id = str(config.get("modelProfileId") or "").strip()
    if not profile_id:
        raise ValueError("applicability evaluator profile is missing")
    return config


def configured_model_profile_id(skill_root: Path) -> str:
    return str(load_capability_configuration(skill_root)["modelProfileId"])
