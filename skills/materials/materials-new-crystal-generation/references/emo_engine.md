# EMO Engines — CDP / FNDS / Crowding / Similarity

> **Architecture note (2026-06-15):** Selection logic now runs entirely
> inside the mattergen MCP server, not in the agent. The agent only
> chooses which engine to use via the `emo_engine` argument
> (`TriplePopCMOEA` or `StandardCDP`). This document explains what the
> server is doing so the agent can interpret selection outcomes
> intelligently — especially when a run stalls or a candidate the user
> expected to survive was pruned.

Both `StandardCDPEngine` and `TriplePopCMOEAEngine` live in
`DEMO/EMO_frameworks.py` and implement `BaseEMOEngine` with three methods:
`initialize(pool)`, `ask(n_offspring, ...)`, `tell(offspring)`. The
mattergen MCP wraps them; the agent's `step_evolution` / `continue_evolution`
calls translate into engine `ask` + `tell` cycles server-side.

## BaseEMOEngine contract

```python
class BaseEMOEngine:
    def __init__(self, constraints, objectives, pop_size,
                 similar_score_threshold, similar_rms_threshold,
                 very_similar_score_threshold, very_similar_rms_threshold): ...

    def initialize(self, initial_pool: list[dict]) -> None:
        """Seed the population. Pool must be fully characterized."""

    def ask(self, n_offspring: int, parent_selection_mode: str,
            tournament_k: int, noise_level: float,
            model_path: str, limit_density) -> tuple[list, list]:
        """Returns (pending_denoise, offspring_meta).
        pending_denoise: noisy ChemGraphs ready for mattergen_denoise.
        offspring_meta: parallel list of dicts with mutation type, parent idx, etc.
        """

    def tell(self, offspring_population: list[dict]) -> None:
        """Merge offspring into population, run selection, update state."""

    def get_current_population(self) -> list[dict]:
        """Snapshot of the current population."""
```

## Selection internals

`tell()` runs these in order:

### 1. Fast Non-Dominated Sorting (FNDS)

`fast_non_dominated_sorting(combined, objectives)` partitions the combined
parent+offspring pool into Pareto fronts F1, F2, …. Front 1 = the Pareto
frontier (no candidate dominates any other in F1).

A candidate A dominates B iff A is at least as good as B on every objective
AND strictly better on at least one. "Good" depends on
`objectives[key] == "minimize" | "maximize"`.

### 2. Constraint Domination Principle (CDP)

For each candidate, compute the total constraint violation:

```
violation = Σ over each constraint c:
    weight_c * (does candidate violate c ? magnitude : 0)
```

Where "magnitude" is the distance past the bound, scaled by the property's
typical range. A candidate with `violation == 0` is feasible.

CDP reorders the FNDS result: any feasible candidate dominates any
infeasible one. Within feasible candidates, normal Pareto domination
applies. Within infeasible candidates, lower violation wins.

Penalty defaults (`999.0`, `-1.0`) are sized so that any candidate with a
failed property reads as maximally infeasible — guaranteeing pruning.

### 3. Crowding Distance

Within each front, compute crowding distance for each candidate: the sum of
normalized distances to its neighbors along each objective axis. Higher
crowding distance = more isolated = preferred.

When the front overflows the remaining population budget, candidates with
lower crowding distance are pruned first. This is what keeps the Pareto
frontier diverse rather than clustering at one corner.

### 4. Structural Similarity Pruning

After objective-based selection, prune near-duplicates using
`pymatgen.analysis.structure_matcher.StructureMatcher`:

```python
from pymatgen.analysis.structure_matcher import StructureMatcher
sm = StructureMatcher(primitive_cell=False, scale=False,
                      attempt_snap=False, comparator=ElementComparator())
sm.fit(struct_a, struct_b)  # → True if considered the same
```

Two thresholds (from `Evoloop.py`):

| Threshold | Purpose | Default |
|-----------|---------|---------|
| `similar_score_threshold` | Within-generation similarity | 0.80 |
| `similar_rms_threshold` | Within-generation RMS distance | 0.20 Å |
| `very_similar_score_threshold` | Cross-generation dedup (in `qualified_crystals/`) | 0.80 |
| `very_similar_rms_threshold` | Cross-generation RMS | 0.08 Å |

The cross-generation (stricter) threshold prevents the same crystal from
appearing in `qualified_crystals/` twice across different generations —
important because the final Pareto re-sort operates on the full archive.

## StandardCDPEngine

Single population. Every generation: parents + offspring compete under CDP.
Simple, deterministic, fast. Use when constraints are well-understood and
the feasible region is reachable from the initial pool.

## TriplePopCMOEAEngine

Three co-evolving populations:

1. **Feasible population** — only candidates with `violation == 0`
2. **Infeasible population** — candidates exploring the boundary of the
   constraint region; preserves diversity that would otherwise be lost when
   all feasible candidates cluster
3. **Boundary population** — candidates near the feasibility boundary;
   bridges the two and prevents premature convergence

Default in `Evoloop.py`. Better when constraints are tight or conflicting
(e.g. formation_energy < 0 AND bandgap > 1.5 are hard to satisfy jointly
with raw MatterGen output). ~3× memory cost.

The engine returns candidates tagged with `pop_type ∈ {"feasible",
"infeasible", "boundary"}` in `data_dict["pop_type"]`. This tag appears in
the per-generation CSV's `Pop_Type` column — useful for diagnosing where
the search is spending effort.

## Tuning advice

| Symptom | Action |
|---------|--------|
| All candidates land in `infeasible` for >5 generations | Relax the tightest constraint by 2× or add `cif_plus_model_fill` to inject diversity |
| `feasible` population collapses to a single cluster | Lower `similar_score_threshold` (more aggressive dedup prevents cluster collapse from accumulating) |
| Run converges to one Pareto point | Switch from StandardCDP to TriplePopCMOEA; or raise `noise_level` |
| Run produces many invalid offspring | Lower `noise_level` (current setting is mutating too aggressively) |
| Qualified archive grows but final Pareto is single-front | Cross-generation dedup too strict; raise `very_similar_rms_threshold` slightly |

## Why engines stay local

The selection step is pure integer/float arithmetic on small dicts (≤300
candidates × ~20 properties). Moving it to an MCP server would add a
network round-trip per generation for no model benefit. MCP is for **model
inference**; selection logic stays where it has zero latency.
