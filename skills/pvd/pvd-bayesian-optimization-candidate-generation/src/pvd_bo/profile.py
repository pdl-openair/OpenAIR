from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

SUPPORTED_ALGORITHM_ID = "gpr_ucb_matern25_v1"
SUPPORTED_OBJECTIVE_TRANSFORMS = {"identity", "absolute_value"}
SUPPORTED_OBJECTIVE_DIRECTIONS = {"maximize", "minimize"}
SUPPORTED_CONSTRAINT_KINDS = {"sum_bounds", "fraction_bounds"}


def _positive_int(value: Any, field: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an integer") from exc
    return result












def load_model_profile(skill_root: Path, profile_id: str) -> tuple[dict[str, Any], str]:
    if not profile_id or any(
        ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
        for ch in profile_id
    ):
        raise ValueError("model_profile_id contains unsupported characters")

    profiles_root = (skill_root / "model_profiles").resolve()
    path = (profiles_root / f"{profile_id}.json").resolve()
    if path.parent != profiles_root or not path.exists():
        raise ValueError(f"unknown model_profile_id: {profile_id}")

    raw = path.read_bytes()
    profile_sha256 = hashlib.sha256(raw).hexdigest()
    profile = json.loads(raw.decode("utf-8"))



    acquisition = profile.get("acquisition")

    top_n = _positive_int(profile.get("top_n"), "top_n")
    _positive_int(profile.get("prediction_chunk_size"), "prediction_chunk_size")

    base_dataset = profile.get("base_dataset")
    _positive_int(base_dataset.get("observation_count"), "base_dataset.observation_count")

    return profile, profile_sha256
