from __future__ import annotations

import warnings
from dataclasses import asdict, dataclass
from typing import Any, Mapping

import numpy as np
from sklearn.exceptions import ConvergenceWarning
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import ConstantKernel, Matern, WhiteKernel
from sklearn.preprocessing import StandardScaler

from .search_space import (
    diversity_group_keys,
    exclude_observed,
    generate_constrained_grid,
    normalize_by_bounds,
    observed_distance_mask,
)
from .selection import greedy_diverse_selection


@dataclass(frozen=True)
class OptimizerDiagnostics:
    training_count: int
    raw_grid_count: int
    constrained_grid_count: int
    excluded_by_constraints: int
    excluded_observed_count: int
    candidate_count_before_distance_filter: int
    distance_eligible_count: int
    distance_filter_applied: bool
    fitted_kernel: str
    fit_warning_count: int
    fit_warning_messages: tuple[str, ...]
    target_mean: float
    target_std: float
    kappa: float
    top_n: int
    diversity_min_distance: float
    diversity_penalty_weight: float
    diversity_group_by: tuple[str, ...]
    max_per_group: int | None
    high_ucb_pool_size: int

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["fit_warning_messages"] = list(self.fit_warning_messages)
        data["diversity_group_by"] = list(self.diversity_group_by)
        return data


def _build_model(profile: Mapping[str, Any]) -> GaussianProcessRegressor:
    gpr = profile["gpr"]
    kernel = (
        ConstantKernel(1.0, tuple(gpr["constant_bounds"]))
        * Matern(
            length_scale=np.asarray(gpr["length_scale_initial"], dtype=float),
            length_scale_bounds=tuple(gpr["length_scale_bounds"]),
            nu=2.5,
        )
        + WhiteKernel(
            noise_level=float(gpr["white_noise_initial"]),
            noise_level_bounds=tuple(gpr["white_noise_bounds"]),
        )
    )
    return GaussianProcessRegressor(
        kernel=kernel,
        alpha=float(gpr["alpha"]),
        normalize_y=bool(gpr["normalize_y"]),
        n_restarts_optimizer=int(gpr["optimizer_restarts"]),
        random_state=int(profile["random_seed"]),
    )


def _predict_unscaled(
    model: GaussianProcessRegressor,
    x_scaler: StandardScaler,
    y_scaler: StandardScaler,
    candidates: np.ndarray,
    chunk_size: int,
) -> tuple[np.ndarray, np.ndarray]:
    means: list[np.ndarray] = []
    stds: list[np.ndarray] = []
    for start in range(0, len(candidates), chunk_size):
        transformed = x_scaler.transform(candidates[start : start + chunk_size])
        mean_scaled, std_scaled = model.predict(transformed, return_std=True)
        means.append(y_scaler.inverse_transform(mean_scaled.reshape(-1, 1)).ravel())
        stds.append(std_scaled * y_scaler.scale_[0])
    return np.concatenate(means), np.concatenate(stds)




def _utility_sign(profile: Mapping[str, Any]) -> float:
    return 1.0 if profile["objective"]["direction"] == "maximize" else -1.0


def generate_candidates(
    x_train: np.ndarray,
    y_target: np.ndarray,
    profile: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], OptimizerDiagnostics]:
    feature_order = tuple(str(item) for item in profile["feature_order"])

    sign = _utility_sign(profile)
    y_utility = y_target * sign
    x_scaler = StandardScaler()
    y_scaler = StandardScaler()
    x_scaled = x_scaler.fit_transform(x_train)
    y_scaled = y_scaler.fit_transform(y_utility.reshape(-1, 1)).ravel()

    model = _build_model(profile)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", category=ConvergenceWarning)
        model.fit(x_scaled, y_scaled)

    grid, raw_count, excluded_constraints = generate_constrained_grid(profile)
    candidates, excluded_observed = exclude_observed(grid, x_train)
    if not len(candidates):
        raise ValueError("all configured grid points have already been observed")

    diversity = profile["diversity"]
    minimum_distance = float(diversity["min_distance_from_observed"])
    penalty_weight = float(diversity["greedy_penalty_weight"])
    pool_limit = int(diversity["high_ucb_pool_size"])
    group_by = tuple(str(item) for item in diversity.get("group_by", []))
    max_per_group = int(diversity["max_per_group"]) if group_by else None
    top_n = int(profile["top_n"])
    chunk_size = int(profile["prediction_chunk_size"])
    if top_n <= 0 or chunk_size <= 0 or pool_limit <= 0:
        raise ValueError("invalid optimizer sizes")

    candidate_count_before_distance = len(candidates)
    distance_mask = observed_distance_mask(
        candidates,
        x_train,
        profile,
        minimum_distance,
        chunk_size,
    )
    distance_eligible_count = int(distance_mask.sum())
    distance_filter_applied = distance_eligible_count >= top_n
    if distance_filter_applied:
        candidates = candidates[distance_mask]

    utility_mean, prediction_std = _predict_unscaled(
        model,
        x_scaler,
        y_scaler,
        candidates,
        chunk_size,
    )
    kappa = float(profile["acquisition"]["kappa"])
    acquisition = utility_mean + kappa * prediction_std

    pool_count = min(pool_limit, len(candidates))
    pool_indices = np.argsort(-acquisition, kind="stable")[:pool_count]
    pool_candidates = candidates[pool_indices]
    pool_acquisition = acquisition[pool_indices]
    pool_normalized = normalize_by_bounds(pool_candidates, profile)
    group_keys = diversity_group_keys(pool_candidates, profile)
    selected = greedy_diverse_selection(
        pool_acquisition,
        pool_normalized,
        min(top_n, pool_count),
        penalty_weight,
        group_keys,
        max_per_group,
    )

    selected_candidates = pool_candidates[selected]
    selected_utility_mean = utility_mean[pool_indices][selected]
    selected_std = prediction_std[pool_indices][selected]
    selected_acquisition = acquisition[pool_indices][selected]

    output_candidates: list[dict[str, Any]] = []
    for rank, (row, mean, std, score) in enumerate(
        zip(
            selected_candidates,
            selected_utility_mean,
            selected_std,
            selected_acquisition,
        ),
        start=1,
    ):
        output_candidates.append(
            {
                "candidate_rank": rank,
                "parameters": {
                    feature: float(row[index])
                    for index, feature in enumerate(feature_order)
                },
                "predicted_target": float(mean * sign),
                "predictive_std": float(std),
                "acquisition_score": float(score),
                "selection_purpose": "exploration_exploitation_tradeoff_with_diversity",
            }
        )

    diagnostics = OptimizerDiagnostics(
        training_count=len(x_train),
        raw_grid_count=raw_count,
        constrained_grid_count=len(grid),
        excluded_by_constraints=excluded_constraints,
        excluded_observed_count=excluded_observed,
        candidate_count_before_distance_filter=candidate_count_before_distance,
        distance_eligible_count=distance_eligible_count,
        distance_filter_applied=distance_filter_applied,
        fitted_kernel=str(model.kernel_),
        fit_warning_count=len(caught),
        fit_warning_messages=tuple(str(item.message) for item in caught),
        target_mean=float(np.mean(y_target)),
        target_std=float(np.std(y_target, ddof=1)) if len(y_target) > 1 else 0.0,
        kappa=kappa,
        top_n=len(selected),
        diversity_min_distance=minimum_distance,
        diversity_penalty_weight=penalty_weight,
        diversity_group_by=group_by,
        max_per_group=max_per_group,
        high_ucb_pool_size=pool_count,
    )
    return output_candidates, diagnostics
