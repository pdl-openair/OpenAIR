from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

import numpy as np
from sklearn.neighbors import NearestNeighbors


def _axis_values(axis: Mapping[str, Any]) -> np.ndarray:
    low = float(axis["min"])
    high = float(axis["max"])
    step = float(axis["step"])
    if not np.isfinite([low, high, step]).all() or step <= 0 or high < low:
        raise ValueError(f"invalid grid axis: {dict(axis)}")

    count = int(math.floor((high - low) / step + 1e-10)) + 1
    values = low + np.arange(count, dtype=float) * step
    if values[-1] < high - 1e-8:
        values = np.append(values, high)
    return np.round(values, 10)


def _column_indices(feature_order: Sequence[str]) -> dict[str, int]:
    return {feature: index for index, feature in enumerate(feature_order)}


def _constraint_mask(
    matrix: np.ndarray,
    feature_order: Sequence[str],
    constraint: Mapping[str, Any],
) -> np.ndarray:
    indices = _column_indices(feature_order)
    kind = str(constraint["kind"])
    low = float(constraint["min"])
    high = float(constraint["max"])

    if kind == "sum_bounds":
        columns = [indices[str(feature)] for feature in constraint["features"]]
        value = matrix[:, columns].sum(axis=1)
    elif kind == "fraction_bounds":
        numerator = matrix[:, indices[str(constraint["numerator"])]]
        columns = [indices[str(feature)] for feature in constraint["denominator_features"]]
        denominator = matrix[:, columns].sum(axis=1)
        value = np.divide(
            numerator,
            denominator,
            out=np.full_like(numerator, np.nan),
            where=denominator != 0,
        )
    else:
        raise ValueError(f"unsupported constraint kind: {kind}")

    return (value >= low) & (value <= high)


def generate_constrained_grid(profile: Mapping[str, Any]) -> tuple[np.ndarray, int, int]:
    feature_order = tuple(str(item) for item in profile["feature_order"])
    grid = profile["grid"]
    axes = [_axis_values(grid[feature]) for feature in feature_order]
    mesh = np.meshgrid(*axes, indexing="ij")
    matrix = np.column_stack([item.ravel() for item in mesh])
    raw_count = len(matrix)

    valid = np.ones(raw_count, dtype=bool)
    for constraint in profile.get("constraints", []):
        valid &= _constraint_mask(matrix, feature_order, constraint)
    constrained = matrix[valid]
    return constrained, raw_count, raw_count - len(constrained)


def exclude_observed(grid: np.ndarray, x_train: np.ndarray) -> tuple[np.ndarray, int]:
    observed = {tuple(row.tolist()) for row in np.round(x_train.astype(float), decimals=8)}
    rounded_grid = np.round(grid.astype(float), decimals=8)
    keep = np.fromiter(
        (tuple(row.tolist()) not in observed for row in rounded_grid),
        dtype=bool,
        count=len(grid),
    )
    return grid[keep], int((~keep).sum())


def normalize_by_bounds(values: np.ndarray, profile: Mapping[str, Any]) -> np.ndarray:
    feature_order = tuple(str(item) for item in profile["feature_order"])
    grid = profile["grid"]
    lows = np.asarray([grid[feature]["min"] for feature in feature_order], dtype=float)
    highs = np.asarray([grid[feature]["max"] for feature in feature_order], dtype=float)
    spans = highs - lows
    if np.any(spans <= 0):
        raise ValueError("grid spans must be positive")
    return (values.astype(float) - lows) / spans


def observed_distance_mask(
    candidates: np.ndarray,
    x_train: np.ndarray,
    profile: Mapping[str, Any],
    minimum_distance: float,
    chunk_size: int,
) -> np.ndarray:
    normalized_candidates = normalize_by_bounds(candidates, profile)
    normalized_observed = normalize_by_bounds(x_train, profile)
    neighbors = NearestNeighbors(n_neighbors=1).fit(normalized_observed)
    distances = np.empty(len(candidates), dtype=float)

    for start in range(0, len(candidates), chunk_size):
        stop = min(start + chunk_size, len(candidates))
        distances[start:stop] = neighbors.kneighbors(
            normalized_candidates[start:stop], return_distance=True
        )[0].ravel()
    return distances >= minimum_distance


def diversity_group_keys(candidates: np.ndarray, profile: Mapping[str, Any]) -> np.ndarray | None:
    group_by = profile["diversity"].get("group_by", [])
    if not group_by:
        return None
    feature_order = tuple(str(item) for item in profile["feature_order"])
    indices = _column_indices(feature_order)
    return candidates[:, [indices[str(feature)] for feature in group_by]]
