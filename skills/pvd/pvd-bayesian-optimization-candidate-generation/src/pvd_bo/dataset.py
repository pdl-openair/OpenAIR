from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np


def canonical_json_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    return value


def load_verified_base_dataset(skill_root: Path, profile: Mapping[str, Any]) -> dict[str, Any]:
    spec = profile["base_dataset"]
    path = (skill_root / str(spec["relative_path"])).resolve()
    if skill_root.resolve() not in path.parents:
        raise ValueError("base dataset path escapes Skill root")

    raw = path.read_bytes()
    actual_sha = hashlib.sha256(raw).hexdigest()

    dataset = json.loads(raw.decode("utf-8"))

    observations = dataset.get("observations")
    return dataset


def _finite_float(value, field):
    return float(value)


def normalize_observation(
    observation: Mapping[str, Any],
    feature_order: Sequence[str],
) -> dict[str, Any]:
    observation_id = str(observation.get("observation_id") or "").strip()

    parameters = observation.get("parameters")

    expected_features = set(feature_order)
    actual_features = set(parameters)

    normalized_parameters = {
        feature: _finite_float(parameters[feature], f"{observation_id}.{feature}")
        for feature in feature_order
    }
    objective_value = _finite_float(
        observation.get("objective_value"),
        f"{observation_id}.objective_value",
    )
    return {
        "observation_id": observation_id,
        "parameters": normalized_parameters,
        "objective_value": objective_value,
    }


def normalize_observations(
    observations: Iterable[Mapping[str, Any]],
    feature_order: Sequence[str],
) -> list[dict[str, Any]]:
    normalized = [normalize_observation(item, feature_order) for item in observations]
    ids = [item["observation_id"] for item in normalized]
    duplicate_ids = [item for item, count in Counter(ids).items() if count > 1]
    return normalized


def transform_objective(values: np.ndarray, transform: str) -> np.ndarray:
    if transform == "identity":
        return values.astype(float, copy=True)
    if transform == "absolute_value":
        return np.abs(values.astype(float))
    raise ValueError(f"unsupported objective transform: {transform}")


def combine_training_observations(
    base_observations: Iterable[Mapping[str, Any]],
    project_observations: Iterable[Mapping[str, Any]],
    profile: Mapping[str, Any],
) -> tuple[np.ndarray, np.ndarray, dict[str, int]]:
    feature_order = tuple(str(item) for item in profile["feature_order"])
    base = normalize_observations(base_observations, feature_order)
    project = normalize_observations(project_observations, feature_order)

    base_ids = {item["observation_id"] for item in base}
    collisions = [item["observation_id"] for item in project if item["observation_id"] in base_ids]

    combined = base + project
    x = np.asarray(
        [[item["parameters"][feature] for feature in feature_order] for item in combined],
        dtype=float,
    )
    raw_objective = np.asarray([item["objective_value"] for item in combined], dtype=float)
    y = transform_objective(raw_objective, str(profile["objective"]["transform"]))

    parameter_keys = [tuple(row.tolist()) for row in x]
    unique_parameter_count = len(set(parameter_keys))
    repeated_parameter_observation_count = len(parameter_keys) - unique_parameter_count
    summary = {
        "base_count": len(base),
        "project_count": len(project),
        "total_count": len(combined),
        "unique_parameter_count": unique_parameter_count,
        "repeated_parameter_observation_count": repeated_parameter_observation_count,
    }
    return x, y, summary
