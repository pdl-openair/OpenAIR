# scripts/goal_evolving.py
"""Goal-evolving demo: pivot objectives mid-search.

This is a DEMO capability for the Nature paper's Fig 7a — NOT a
production-grade mid-search pivot. It re-uses existing
characterization data, so it only works if the new objectives'
property keys are already present in population.json.

Pivot invalidates the old Pareto ranking. We re-rank in place using
saes_selection.fast_non_dominated_sorting.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Allow sibling imports when run as a script
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from saes_selection import fast_non_dominated_sorting  # noqa: E402


_VALID_DIRECTIONS = {"minimize", "maximize"}


def _load_json(p: Path) -> dict | list:
    return json.loads(p.read_text(encoding="utf-8"))


def _save_json(p: Path, obj) -> None:
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def _re_rank(population: list, objectives: dict) -> list:
    """Re-apply fast non-dominated sorting with new objectives.

    `saes_selection.fast_non_dominated_sorting` reads objective values via
    `_objective_value(ind, key, direction)` which looks up `ind[key]` at the
    TOP LEVEL of each individual dict. Our seed population stores them
    nested under `properties`, so project them up before sorting. After
    sorting, each individual has `pareto_rank` and `fitness` set by FNDS;
    we also mirror `pareto_rank` to `rank` for compatibility with code
    paths that read `ind["rank"]`.
    """
    keys = list(objectives.keys())
    for ind in population:
        props = ind.get("properties", {})
        for k in keys:
            if k in props:
                ind[k] = props[k]
    fast_non_dominated_sorting(population, objectives)
    for ind in population:
        ind["rank"] = ind.get("pareto_rank", 1)
    return population


def pivot_objectives(run_dir: Path, new_objectives: dict[str, str]) -> dict:
    run_dir = Path(run_dir)
    # Validate
    for k, d in new_objectives.items():
        if d not in _VALID_DIRECTIONS:
            raise ValueError(f"invalid direction '{d}' for key '{k}'")

    cfg = _load_json(run_dir / "config.json")
    population = _load_json(run_dir / "population.json")
    old_objectives = dict(cfg.get("objectives", {}))

    # Verify keys exist in current characterization
    available_keys: set[str] = set()
    for ind in population:
        available_keys.update(ind.get("properties", {}).keys())
    missing = set(new_objectives) - available_keys
    if missing:
        raise KeyError(
            f"unknown property key(s) {sorted(missing)}: not present in current population characterization; "
            "pivot requires keys already characterized"
        )

    # Update config + re-rank
    cfg["objectives"] = dict(new_objectives)
    _save_json(run_dir / "config.json", cfg)
    population = _re_rank(population, new_objectives)
    _save_json(run_dir / "population.json", population)

    # Append to pivot log
    log_path = run_dir / "pivot_log.jsonl"
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "old": old_objectives,
        "new": dict(new_objectives),
        "individuals_re_ranked": len(population),
    }
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    return entry
