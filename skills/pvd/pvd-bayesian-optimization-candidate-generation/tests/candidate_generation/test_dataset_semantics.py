from __future__ import annotations

from pathlib import Path

from pvd_bo.dataset import combine_training_observations, load_json, load_verified_base_dataset

ROOT = Path(__file__).resolve().parents[2]


def _profile_and_base():
    profile = load_json(ROOT / "model_profiles/al40sc_mo_110mm_v1.json")
    base = load_verified_base_dataset(ROOT, profile)["observations"]
    return profile, base


def test_repeated_parameter_observations_are_all_retained() -> None:
    profile, base = _profile_and_base()
    params = {"power_w": 130, "n2_sccm": 12, "ar_sccm": 27, "time_s": 1500}
    project = [
        {"observation_id": "r1", "parameters": params, "objective_value": 15.8},
        {"observation_id": "r2", "parameters": params, "objective_value": 17.2},
        {"observation_id": "r3", "parameters": params, "objective_value": 16.4},
    ]
    x, y, summary = combine_training_observations(base, project, profile)
    assert len(x) == 66
    assert y[-3:].tolist() == [15.8, 17.2, 16.4]
    assert summary == {
        "base_count": 63,
        "project_count": 3,
        "total_count": 66,
        "unique_parameter_count": 64,
        "repeated_parameter_observation_count": 2,
    }


def test_profile_defined_absolute_transform_preserves_signed_observations() -> None:
    profile, base = _profile_and_base()
    params = {"power_w": 130, "n2_sccm": 12, "ar_sccm": 27, "time_s": 1500}
    _, y, summary = combine_training_observations(
        base,
        [
            {"observation_id": "negative", "parameters": params, "objective_value": -15.2},
            {"observation_id": "positive", "parameters": params, "objective_value": 14.1},
        ],
        profile,
    )
    assert y[-2:].tolist() == [15.2, 14.1]
    assert summary["project_count"] == 2
