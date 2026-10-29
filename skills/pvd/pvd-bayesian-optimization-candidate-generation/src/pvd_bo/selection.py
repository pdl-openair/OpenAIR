from __future__ import annotations

import numpy as np


def greedy_diverse_selection(
    acquisition: np.ndarray,
    normalized_candidates: np.ndarray,
    n_select: int,
    penalty_weight: float,
    group_keys: np.ndarray | None,
    max_per_group: int | None,
) -> np.ndarray:

    scores = acquisition.astype(float).copy()
    available = np.ones(len(scores), dtype=bool)
    selected: list[int] = []

    for _ in range(min(n_select, len(scores))):
        available_indices = np.flatnonzero(available)
        if not len(available_indices):
            break
        best_index = int(available_indices[np.argmax(scores[available_indices])])
        selected.append(best_index)
        available[best_index] = False

        if group_keys is not None:
            selected_keys = group_keys[np.asarray(selected)]
            current_key = group_keys[best_index]
            if int(np.all(selected_keys == current_key, axis=1).sum()) >= int(max_per_group):
                available[np.all(group_keys == current_key, axis=1)] = False

        remaining = np.flatnonzero(available)
        if not len(remaining):
            continue
        distances = np.linalg.norm(
            normalized_candidates[remaining] - normalized_candidates[best_index],
            axis=1,
        )
        normalized_distances = (distances - distances.min()) / (
            distances.max() - distances.min() + 1e-10
        )
        near_penalty = 1.0 - normalized_distances
        scores[remaining] *= 1.0 - penalty_weight * near_penalty

    return np.asarray(selected, dtype=int)
