# Reward computation and filters — exact math

This file is the source of truth for the math in Phase 2g. The SKILL.md
version is intentionally a summary; this is the precise spec, generalized
to any RunPlan with N objectives (any direction, any weight).

## Inputs

After Phase 2d–2e, every surviving candidate has:

- `formula` — string
- `cif` — relaxed CIF text
- One float per **objective** in the RunPlan, named by `objective.name`
  (e.g. `mag_density`, `bandgap`, `bulk_modulus`, `hhi`, `density`, …).
- One float per **hard filter** in the RunPlan (typically just `ehull`).

The objectives come from three places:
1. **Direct ALIGNN values** — e.g. `bandgap`, `bulk_modulus`,
   `exfoliation_energy`. Just copy `properties.<name>.value`.
2. **Derived from ALIGNN + CIF** — e.g. `mag_density = magmom / volume`.
   Computed by a local script (`scripts/mag_density.py`) or inline python
   following the recipe in `property_catalog.md` §"Writing your own row".
3. **Pure-formula signals** — e.g. `hhi` from `scripts/hhi.py`.

If any objective's value is `null` (ALIGNN failure, parse error), drop
the candidate — cannot rank.

## Step 1 — hard filters

For each filter declared in `RunPlan.hard_filter`, drop every candidate
where `value <op> threshold` is FALSE. The default filter is:

```
{"name": "ehull", "op": "<", "value": 0.1}   # eV/atom
```

Additional filters that commonly appear:

```
{"name": "bandgap", "op": ">", "value": 1.0}    # semiconductors
{"name": "bandgap", "op": ">", "value": 3.0}    # wide-gap / TCO
{"name": "exfoliation_energy", "op": "<", "value": 200}  # meV/atom, 2D
{"name": "density", "op": "<", "value": 5.0}    # lightweight
```

`op` is one of `<`, `<=`, `>`, `>=`, `==`, `!=` (rare). Keep dropped
candidates in a side list for the iteration report.

**Relaxation rule**: if fewer than 2 candidates survive the filter set,
relax the **most restrictive** filter (largest drop count) by 2× its
threshold (e.g. ehull 0.1 → 0.2 eV/atom; bandgap 1.0 → 0.5 eV). If still
fewer than 2 survive, the iteration proceeds with whatever survived —
even 0 — and selection carries the prior parents forward unchanged with
a user warning.

## Step 2 — normalization (min-max, pool-wide)

The pool being ranked = (surviving new candidates) ∪ (current parents).
Renormalize the *whole pool together* — otherwise rewards from prior
iterations are not comparable with this iteration's new candidates.

For objective `o` with values `[v_1, ..., v_n]`:

- If `max(v) == min(v)`: every `norm(v_i) = 0.5`. (Avoids div-by-zero
  and avoids giving a fake "1.0" to a constant column.)
- Else: `norm(v_i) = (v_i - min(v)) / (max(v) - min(v))`.

Apply separately to each objective.

## Step 3 — reward (N objectives, weighted, direction-aware)

```
                 ┌  norm(v_o,i)        if direction_o == "max"
contrib_o,i  =   ┤
                 └  1 - norm(v_o,i)    if direction_o == "min"

reward_i = Σ_o  weight_o · contrib_o,i
```

Range: `[0, Σ_o weight_o]`. With all weights = 1 and 2 objectives,
range is `[0, 2]` — matching the original magnet MVP.

**Default weights**: all 1.0. If the user says "I care more about X than
Y", bump X's weight to 2.0 (or have the user pick). Document any non-
uniform weights in the final report.

**Why direction-aware instead of negating min-objectives**: keeps the
norm column interpretable (`norm(hhi)` is still 0 at the cheapest, 1 at
the rarest) while the reward formula remains additive. The reader of the
report can sanity-check `norm(hhi)` directly.

## Step 4 — selection

Sort pool by `reward` descending. Take the top `top_k`. These are the
parents for the next iteration.

Tie-break order (deterministic):
1. Lower `ehull` (more stable wins ties).
2. Lower `hhi` if `hhi` is in the RunPlan (cheaper wins ties).
3. Lower count of ALIGNN failures during scoring.
4. Alphabetical formula (last resort).

## Edge cases

| Case | Handling |
|------|----------|
| All candidates dropped by filters | Apply relaxation rule (above). If still 0, parents unchanged, warn user. |
| Pool size < `top_k` | Keep all; top_k effectively = pool size. |
| A parent's CIF was lost (e.g. context compaction) | Drop the parent — cannot re-score without its CIF. |
| An objective value is `null` for some candidates | Drop only those candidates; keep the rest. |
| All candidates tie on every objective (constant columns) | Every norm column = 0.5, every reward = `Σ weight · 0.5`. Selection falls back to tie-breakers. |
| An objective is `min` but the user actually said "near a target value" (e.g. bandgap ≈ 1.5 eV) | Convert to a `min` objective on `|value - target|` and document. Don't try to fit a windowed reward — it complicates normalization. |
| A `hhi` lookup returns null for an exotic element | Drop candidate; cannot rank. |
| Hard filter references a value not present in the record (e.g. `bandgap` filter but ALIGNN didn't return it) | Drop candidate; record the misconfiguration in the run log. The RunPlan's `alignn_models` should always include every property referenced by filters — Phase 0.5 should enforce this. |

## Why these choices

- **Min-max vs z-score**: min-max keeps the reward in a bounded range,
  which makes "0.5 improvement" interpretable across runs. z-score is
  unbounded and harder to communicate.
- **Pool renormalization vs per-iteration norm**: per-iteration norm
  makes rewards non-monotonic (a parent can lose reward without
  changing). Pool-wide norm keeps rewards comparable across iterations.
- **ehull as the default hard filter**: SUN (synthesizability) is
  expensive and noisy at MVP scope. Formation energy is already absorbed
  into ehull. Bandgap is only meaningful for semiconductors. So ehull
  is the cheap, meaningful stability gate that applies to every RunPlan.
- **2× relaxation rather than dropping the filter entirely**: keeps a
  stability signal even when the chemistry family is hostile to the
  threshold, while still letting the loop progress.
- **No early-stop on reward plateau < 2 iters**: a 1-iter plateau is
  noise; 2 iters is signal. Paper uses 3 — too conservative for the
  small default `max_iters=3` of the MVP.

## Why mattergen-eval is not in reward

`mattergen-evaluate` produces **one aggregate metrics dict per batch**,
not per-candidate scores. There is no way to ask "is candidate X novel?"
— only "what fraction of this batch is novel?". That shape cannot drive
per-candidate selection, so by construction it stays out of the reward.

What it *does* provide, useful as population health:

- `frac_novel_structures`, `frac_unique_structures`,
  `frac_novel_unique_structures` — search diversity signals.
- `avg_structure_validity`, `avg_comp_validity` — structural sanity.
- `avg_energy_above_hull_per_atom`, `frac_stable_structures` —
  cross-check against the RunPlan's ALIGNN `ehull`. Material
  disagreements here are normal (different reference sets / correction
  schemes) and surface in the iteration + final reports, never as a
  hard filter.

Ergonomic rule: keep ALIGNN `ehull` as the hard filter (cheap,
per-candidate, deterministic); treat mattergen-eval as the population
dashboard. If the user later wants per-candidate synthesizability
filtering, the next step is SUN, not mattergen-eval.
