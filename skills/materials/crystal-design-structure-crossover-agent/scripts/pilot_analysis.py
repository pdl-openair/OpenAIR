#!/usr/bin/env python3
"""Aggregate metrics across an ablation's run directories.

Reads the snapshot keys written by evolve_driver.write_snapshot:
sun_count / ehull_median / stable_count / pareto_front_size.
(Legacy snapshots without these keys score 0 — the driver has written
them since the memory-ablation fixes; old run dirs predate them.)

Also audits the final population for structural duplicates: batch-of-1
evaluation makes the driver's per-individual `unique` flag near-constant,
so duplicates inside the final population silently inflate sun_count.
`_distinct_metrics` re-derives uniqueness at the population level (same
StructureMatcher protocol as parity_metrics: default tolerances,
reduced-formula buckets, first-come-first-served) and yields
sun_count_distinct / unique_frac / n_distinct as auxiliary audit metrics.
Runs without population_gen*.json simply omit those keys.

Adds pairwise mode comparison (Mann-Whitney U with normal-approximation
p-value + Cliff's delta effect size) — small-replicate, distribution-free.
"""
from __future__ import annotations

import json
import math
import statistics
from itertools import combinations
from pathlib import Path

_METRIC_KEYS = (
    "sun_count",
    "ehull_median",
    "pareto_front_size",
    "stable_count",
    "sun_count_distinct",  # audit-only: absent for runs w/o population_gen*.json
    "unique_frac",         # audit-only: distinct / n_pop of the final population
)


def _snapshot_metrics(run_dir: Path) -> dict[str, float] | None:
    snap_path = run_dir / "snapshot_latest.json"
    cfg_path = run_dir / "config.json"
    if not (snap_path.exists() and cfg_path.exists()):
        return None
    snap = json.loads(snap_path.read_text(encoding="utf-8"))
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    # config['mode'] is canonical; fall back to dir-name convention <mode>_rep<N>
    mode = cfg.get("mode", run_dir.name.rsplit("_rep", 1)[0])
    # pareto_front_size is the driver's key; accept legacy pareto_size too
    pareto = snap.get("pareto_front_size", snap.get("pareto_size", 0))
    out = {
        "mode": mode,
        "sun_count": float(snap.get("sun_count", 0)),
        "ehull_median": float(snap.get("ehull_median", 0.0)),
        "pareto_front_size": float(pareto),
        "stable_count": float(snap.get("stable_count", 0)),
    }
    distinct = _distinct_metrics(run_dir)
    if distinct is not None:
        out.update(distinct)
    return out


def _distinct_metrics(run_dir: Path) -> dict[str, float] | None:
    """Population-level duplicate audit of the last archived generation.

    Returns None when no population_gen*.json exists (pre-fix runs). SUN
    membership mirrors evolve_driver.is_sun (novel ∧ stable ∧ unique flags);
    the unique flag itself is near-constant under batch-of-1 evaluation, so
    the dedup here — not that flag — is what makes sun_count_distinct honest.
    """
    gen_files = sorted(run_dir.glob("population_gen*.json"))
    if not gen_files:
        return None
    # lazy import: keeps the stats core pure-python; the audit needs pymatgen
    from parity_metrics import uniqueness_flags

    pop = [x for x in json.loads(gen_files[-1].read_text(encoding="utf-8"))
           if isinstance(x, dict) and x.get("cif")]
    flags = uniqueness_flags(pop)
    is_sun = [bool(x.get("novel")) and bool(x.get("stable")) and bool(x.get("unique"))
              for x in pop]
    return {
        "n_pop": float(len(pop)),
        "n_distinct": float(flags.sum()),
        "unique_frac": float(flags.mean()) if len(flags) else 0.0,
        "sun_count_distinct": float(sum(f for f, s in zip(flags, is_sun) if s)),
    }


def aggregate_metrics(base_dir: Path) -> dict[str, dict[str, float]]:
    base_dir = Path(base_dir)
    by_mode: dict[str, list[dict[str, float]]] = {}
    if not base_dir.exists():
        return {}
    for run_dir in sorted(base_dir.iterdir()):
        if not run_dir.is_dir():
            continue
        m = _snapshot_metrics(run_dir)
        if m is None:
            continue
        by_mode.setdefault(m.pop("mode"), []).append(m)

    out: dict[str, dict[str, float]] = {}
    for mode, rows in by_mode.items():
        out[mode] = {"n_runs": float(len(rows))}
        for key in _METRIC_KEYS:
            vals = [r[key] for r in rows if key in r]
            if not vals:  # audit-only keys may be absent for every run
                continue
            out[mode][f"{key}_mean"] = statistics.mean(vals)
            out[mode][f"{key}_stdev"] = (
                statistics.stdev(vals) if len(vals) > 1 else 0.0
            )
    return out


def _mann_whitney_u(a: list[float], b: list[float]) -> tuple[float, float]:
    """Mann-Whitney U (two-sided) with normal-approximation p-value.

    Pure-python (no scipy) — replicates are few, ties handled via midranks.
    Returns (U, p).
    """
    na, nb = len(a), len(b)
    ranked = sorted([(v, 0) for v in a] + [(v, 1) for v in b])
    # midranks
    ranks: list[float] = [0.0] * (na + nb)
    i = 0
    while i < len(ranked):
        j = i
        while j < len(ranked) and ranked[j][0] == ranked[i][0]:
            j += 1
        mid = (i + j + 1) / 2.0  # average of 1-based ranks i+1..j
        for k in range(i, j):
            ranks[k] = mid
        i = j
    rank_sum_a = sum(r for r, (_, g) in zip(ranks, ranked) if g == 0)
    u1 = rank_sum_a - na * (na + 1) / 2.0
    u = min(u1, na * nb - u1)
    mu = na * nb / 2.0
    # tie-corrected sigma
    all_vals = [v for v, _ in ranked]
    tie_term = 0.0
    i = 0
    while i < len(all_vals):
        j = i
        while j < len(all_vals) and all_vals[j] == all_vals[i]:
            j += 1
        t = j - i
        tie_term += t**3 - t
        i = j
    n = na + nb
    sigma_sq = (na * nb / 12.0) * ((n + 1) - tie_term / (n * (n - 1)))
    sigma = math.sqrt(max(sigma_sq, 1e-12))
    z = (u - mu + 0.5) / sigma  # continuity correction toward mu
    p = 2.0 * 0.5 * math.erfc(abs(z) / math.sqrt(2.0))
    return u, p


def _cliffs_delta(a: list[float], b: list[float]) -> float:
    """Cliff's delta: #(a>b) - #(a<b) over |a|·|b|. 0 = no effect."""
    if not a or not b:
        return 0.0
    greater = sum(1 for x in a for y in b if x > y)
    less = sum(1 for x in a for y in b if x < y)
    return (greater - less) / (len(a) * len(b))


def compare_modes(
    base_dir: Path,
    metric: str = "sun_count",
    modes: tuple[str, ...] | None = None,
) -> dict[str, dict[str, float]]:
    """Pairwise Mann-Whitney U + Cliff's delta between modes on one metric."""
    base_dir = Path(base_dir)
    by_mode: dict[str, list[float]] = {}
    for run_dir in sorted(base_dir.iterdir()):
        if not run_dir.is_dir():
            continue
        m = _snapshot_metrics(run_dir)
        if m is None or metric not in m:  # audit-only metrics may be absent
            continue
        by_mode.setdefault(m["mode"], []).append(m[metric])
    wanted = list(modes) if modes else sorted(by_mode)
    out: dict[str, dict[str, float]] = {}
    for ma, mb in combinations([x for x in wanted if x in by_mode], 2):
        a, b = by_mode[ma], by_mode[mb]
        u, p = _mann_whitney_u(a, b)
        out[f"{ma}_vs_{mb}"] = {
            "n_a": float(len(a)),
            "n_b": float(len(b)),
            "mean_a": statistics.mean(a),
            "mean_b": statistics.mean(b),
            "U": u,
            "p_value": p,
            "cliffs_delta": _cliffs_delta(a, b),
        }
    return out


def main() -> int:
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--base-dir", required=True, type=Path)
    p.add_argument("--metric", default="sun_count",
                   help="metric for pairwise mode comparison (default sun_count)")
    p.add_argument("--out", type=Path, default=None)
    args = p.parse_args()
    report = {
        "aggregate": aggregate_metrics(args.base_dir),
        "pairwise": compare_modes(args.base_dir, metric=args.metric),
    }
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
