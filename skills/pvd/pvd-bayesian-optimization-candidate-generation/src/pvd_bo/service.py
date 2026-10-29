from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .config import configured_model_profile_id
from .dataset import (
    canonical_json_sha256,
    combine_training_observations,
    load_verified_base_dataset,
)
from .domain import evaluate_process_context
from .optimizer import generate_candidates
from .profile import load_model_profile
from .state_observations import extract_real_training_observations

PUBLIC_CANDIDATE_PARAMETER_MAP = {
    "power_w": ("power", "W"),
    "n2_sccm": ("n2_flow", "sccm"),
    "ar_sccm": ("ar_flow", "sccm"),
    "time_s": ("deposition_time", "s"),
}


def _skill_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _public_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    expected_internal_features = set(PUBLIC_CANDIDATE_PARAMETER_MAP)
    for candidate in candidates:
        internal_parameters = candidate["parameters"]
        public_parameters: dict[str, float] = {}
        public_units: dict[str, str] = {}
        for internal_name, (public_name, unit) in PUBLIC_CANDIDATE_PARAMETER_MAP.items():
            public_parameters[public_name] = float(internal_parameters[internal_name])
            public_units[public_name] = unit
        result.append(
            {
                **{k: v for k, v in candidate.items() if k != "parameters"},
                "parameters": public_parameters,
                "units": public_units,
            }
        )
    return result


def _objective_output(profile: Mapping[str, Any]) -> dict[str, Any]:
    objective = profile["objective"]
    return {
        "target_metric": str(objective["target_metric"]),
        "transform": str(objective["transform"]),
        "direction": str(objective["direction"]),
    }


def _not_applicable(
    applicability: dict[str, Any],
    profile: Mapping[str, Any],
    profile_sha256: str,
    input_sha256: str,
) -> dict[str, Any]:
    return {
        "state": "succeeded",
        "candidate_generation_status": "not_applicable",
        "applicability": applicability,
        "objective": _objective_output(profile),
        "candidates": [],
        "training_summary": None,
        "run_diagnostics": None,
        "provenance": {
            "algorithm_id": str(profile["algorithm_id"]),
            "profile_id": str(profile["profile_id"]),
            "profile_version": int(profile["profile_version"]),
            "model_profile_sha256": profile_sha256,
            "base_dataset_id": str(profile["base_dataset"]["dataset_id"]),
            "base_dataset_sha256": str(profile["base_dataset"]["sha256"]),
            "input_sha256": input_sha256,
            "random_seed": int(profile["random_seed"]),
        },
    }


def run_candidate_generation(payload: Mapping[str, Any]) -> dict[str, Any]:

    scientific_state_facts = payload.get("scientific_state_facts")

    input_sha256 = canonical_json_sha256(dict(payload))
    root = _skill_root()
    profile_id = configured_model_profile_id(root)
    profile, profile_sha256 = load_model_profile(root, profile_id)

    process_context, real_training_observations = extract_real_training_observations(
        scientific_state_facts, profile
    )
    applicability = evaluate_process_context(process_context, profile)


    base_dataset = load_verified_base_dataset(root, profile)
    base_applicability = evaluate_process_context(base_dataset.get("process_context"), profile)

    x_train, y_target, training_summary = combine_training_observations(
        base_dataset["observations"],
        real_training_observations,
        profile,
    )
    candidates, diagnostics = generate_candidates(x_train, y_target, profile)
    public_candidates = _public_candidates(candidates)

    return {
        "state": "succeeded",
        "candidate_generation_status": "generated",
        "applicability": applicability.to_dict(),
        "objective": _objective_output(profile),
        "candidates": public_candidates,
        "training_summary": training_summary,
        "run_diagnostics": diagnostics.to_dict(),
        "provenance": {
            "algorithm_id": str(profile["algorithm_id"]),
            "profile_id": str(profile["profile_id"]),
            "profile_version": int(profile["profile_version"]),
            "model_profile_sha256": profile_sha256,
            "base_dataset_id": str(profile["base_dataset"]["dataset_id"]),
            "base_dataset_sha256": str(profile["base_dataset"]["sha256"]),
            "input_sha256": input_sha256,
            "random_seed": int(profile["random_seed"]),
        },
    }
