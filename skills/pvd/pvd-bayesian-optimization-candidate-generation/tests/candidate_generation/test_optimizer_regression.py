from __future__ import annotations

from pathlib import Path

import pytest

from pvd_bo.dataset import combine_training_observations, load_json, load_verified_base_dataset
from pvd_bo.optimizer import generate_candidates

ROOT = Path(__file__).resolve().parents[2]

EXPECTED_INITIAL_63 = [
    {"power_w": 235.0, "n2_sccm": 12.0, "ar_sccm": 27.0, "time_s": 3200.0},
    {"power_w": 185.0, "n2_sccm": 12.0, "ar_sccm": 27.0, "time_s": 1500.0},
    {"power_w": 255.0, "n2_sccm": 19.0, "ar_sccm": 48.0, "time_s": 4300.0},
    {"power_w": 170.0, "n2_sccm": 12.0, "ar_sccm": 28.0, "time_s": 4600.0},
    {"power_w": 225.0, "n2_sccm": 12.0, "ar_sccm": 28.0, "time_s": 1500.0},
    {"power_w": 215.0, "n2_sccm": 18.0, "ar_sccm": 46.0, "time_s": 4600.0},
    {"power_w": 255.0, "n2_sccm": 19.0, "ar_sccm": 48.0, "time_s": 3000.0},
    {"power_w": 155.0, "n2_sccm": 13.0, "ar_sccm": 27.0, "time_s": 3900.0},
    {"power_w": 180.0, "n2_sccm": 17.0, "ar_sccm": 27.0, "time_s": 1500.0},
    {"power_w": 255.0, "n2_sccm": 13.0, "ar_sccm": 27.0, "time_s": 4300.0},
]


def test_rewrite_preserves_original_63_algorithm_output() -> None:
    profile = load_json(ROOT / "model_profiles/al40sc_mo_110mm_v1.json")
    base = load_verified_base_dataset(ROOT, profile)
    x, y, summary = combine_training_observations(base["observations"], [], profile)
    candidates, diagnostics = generate_candidates(x, y, profile)
    assert summary["total_count"] == 63
    parameter_projection = [candidate["parameters"] for candidate in candidates]
    assert parameter_projection == EXPECTED_INITIAL_63
    assert [candidate["candidate_rank"] for candidate in candidates] == list(range(1, 11))
    for candidate in candidates:
        assert candidate["predictive_std"] >= 0
        assert candidate["acquisition_score"] == pytest.approx(
            candidate["predicted_target"] + 3.0 * candidate["predictive_std"]
        )
        assert (
            candidate["selection_purpose"]
            == "exploration_exploitation_tradeoff_with_diversity"
        )
    assert diagnostics.raw_grid_count == 1_103_232
    assert diagnostics.constrained_grid_count == 602_368
    assert diagnostics.excluded_by_constraints == 500_864
    assert diagnostics.excluded_observed_count == 40
    assert diagnostics.high_ucb_pool_size == 5000


def test_algorithm_is_profile_driven_not_case_field_driven() -> None:
    synthetic_profile = {
        "algorithm_id": "gpr_ucb_matern25_v1",
        "profile_id": "synthetic_v1",
        "profile_version": 1,
        "feature_order": ["temperature_c", "pressure_pa"],
        "objective": {
            "target_metric": "roughness",
            "transform": "identity",
            "direction": "minimize",
        },
        "grid": {
            "temperature_c": {"min": 100.0, "max": 120.0, "step": 10.0},
            "pressure_pa": {"min": 1.0, "max": 3.0, "step": 1.0},
        },
        "constraints": [],
        "gpr": {
            "constant_bounds": [0.001, 1000.0],
            "length_scale_initial": [1.0, 1.0],
            "length_scale_bounds": [0.01, 1000.0],
            "white_noise_initial": 0.1,
            "white_noise_bounds": [0.00001, 10.0],
            "alpha": 1e-8,
            "normalize_y": False,
            "optimizer_restarts": 0,
        },
        "acquisition": {"kind": "ucb", "kappa": 2.0},
        "diversity": {
            "min_distance_from_observed": 0.0,
            "high_ucb_pool_size": 20,
            "greedy_penalty_weight": 0.2,
            "group_by": [],
        },
        "prediction_chunk_size": 100,
        "random_seed": 7,
        "top_n": 2,
    }
    base = [
        {
            "observation_id": "s1",
            "parameters": {"temperature_c": 100.0, "pressure_pa": 1.0},
            "objective_value": 5.0,
        },
        {
            "observation_id": "s2",
            "parameters": {"temperature_c": 110.0, "pressure_pa": 2.0},
            "objective_value": 3.0,
        },
        {
            "observation_id": "s3",
            "parameters": {"temperature_c": 120.0, "pressure_pa": 3.0},
            "objective_value": 4.0,
        },
    ]
    x, y, _ = combine_training_observations(base, [], synthetic_profile)
    candidates, diagnostics = generate_candidates(x, y, synthetic_profile)
    assert len(candidates) == 2
    assert set(candidates[0]["parameters"]) == {"temperature_c", "pressure_pa"}
    assert diagnostics.training_count == 3
