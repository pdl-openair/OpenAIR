# Reward computation and filters — exact math

This file is the source of truth for the math in Phase 2f. The SKILL.md
version is intentionally a summary; this is the precise spec.

## Inputs

After Phase 2e, every surviving candidate has:

- `formula` — string
- `cif` — relaxed CIF text
- `magmom` — float (μB, total magnetic moment, from ALIGNN)
- `volume` — float (Å³, from pymatgen.Structure on the relaxed CIF)
- `mag_density` = `magmom / volume` — float (μB/Å³)
- `hhi` — float (HHI_production, from `pymatgen.analysis.hhi.HHIModel`)
- `ehull` — float (eV/atom, from ALIGNN)

Plus one **batch-level** input carried in from the previous iteration:

- `batch_SUN_prev` — float ∈ `[0, 1]`, the `frac_novel_unique_stable_structures`
  (S∧U∧N) returned by the mattergen-evaluate job drained at the start of
  this iteration (see SKILL.md §"Drain pending mattergen-evaluate jobs").
  On iteration 0 (no prior job) `batch_SUN_prev = 0`.

## Step 1 — hard filter (ehull)

If `filter_ehull=True` (default), drop every candidate with
`ehull >= ehull_lt` (default 0.1 eV/atom). Keep the dropped ones in a
side list for the iteration report.

**Relaxation rule**: if fewer than 2 candidates survive, retry the
filter at `ehull_lt=0.2`. If still fewer than 2 survive, the iteration
proceeds with whatever survived (even 0 — selection then carries the
prior parents forward unchanged and the user is warned).

## Step 2 — normalization (min-max within the pool being ranked)

The pool being ranked = (surviving new candidates) ∪ (current parents).
Renormalize the *whole pool together* — otherwise rewards from prior
iterations are not comparable with this iteration's new candidates.

For objective `o` with values `[v_1, ..., v_n]`:

- If `max(v) == min(v)`: every `norm(v_i) = 0.5`. (Avoids div-by-zero
  and avoids giving a fake "1.0" to a constant column.)
- Else: `norm(v_i) = (v_i - min(v)) / (max(v) - min(v))`.

Apply separately to `mag_density` and `hhi`.

## Step 3 — reward

```
reward_i = mag_density_norm_i + (1 - hhi_norm_i) + w_sun * batch_SUN_prev
```

Range: `[0, 2 + w_sun]`. With the default `w_sun = 0.5` the range is
`[0, 2.5]`. A reward of 2.5 means "best mag_density AND best (lowest)
HHI in the pool, plus the prior batch scored SUN = 1.0". A reward of 0
means "worst of both, plus prior batch scored SUN = 0".

The two per-candidate terms (`mag_density_norm`, `1 - hhi_norm`) decide
ranking **within** the current pool. `batch_SUN_prev` is the same
constant for every candidate in the pool being ranked, so it shifts all
rewards equally and does **not** change selection within an iteration.
It is included because:

1. The reported reward / trend then reflects population health, not
   just per-candidate objectives — useful for the iteration + final
   reports and for cross-run comparison.
2. It is the only hook currently wired for SUN pressure. If the user
   later wants real per-candidate SUN pressure, the upgrade path is
   `batch_size=1` submission (see `mcp_tools.md` §mattergen-evaluate);
   this term is the placeholder that swap replaces.

Equal weighting of the two per-candidate terms is the MVP default. If
the user says "I care more about HHI", change to
`0.5*mag_density_norm + 1.0*(1 - hhi_norm) + w_sun * batch_SUN_prev`
and document the new weights in the report. Always re-normalize before
changing weights — the formula above assumes the first two terms in
`[0,1]`. `w_sun` is configurable independently (default `0.5`); raise
it to make SUN more visible in reported reward, lower it (to `0`) to
disable shaping without removing the metric from reports.

## Step 4 — selection

Sort pool by `reward` descending. Take the top `top_k`. These are the
parents for the next iteration. Ties break toward lower `ehull`, then
toward lower `hhi`, then toward alphabetical formula (deterministic).

## Edge cases

| Case | Handling |
|------|----------|
| All candidates dropped by ehull filter | Retry at 0.2; if still 0, parents unchanged, warn user |
| Pool size < `top_k` | Keep all; top_k effectively = pool size |
| A parent's CIF was lost (e.g. context compaction) | Drop the parent — can't re-score without its CIF |
| `magmom <= 0` (non-magnetic or anti-ferromagnetic) | Keep but `mag_density` will be ≤ 0 → low reward. No special case |
| `hhi` model returns null for an element (e.g. exotic) | Drop candidate; cannot rank |
| Iteration 0 (no prior mattergen-eval job drained) | `batch_SUN_prev = 0`; reward range collapses to `[0, 2]` for this iter only |
| Prior mattergen-eval job `status:error` or never finished | `batch_SUN_prev = 0` and note in iteration report; do not block selection |

## Why these choices

- **Min-max norm vs z-score**: min-max keeps the reward in a bounded
  range, which makes "0.5 improvement" interpretable across runs. z-score
  is unbounded and harder to communicate.
- **Pool renormalization vs per-iteration norm**: per-iteration norm
  makes rewards non-monotonic (a parent can lose reward without
  changing). Pool-wide norm keeps rewards comparable across iterations.
- **ehull as the only hard filter**: per-candidate SUN (synthesizability)
  would need `batch_size=1` mattergen-evaluate calls — minutes per
  structure against the 845k alex-mp reference set, too slow for the
  default MVP loop. Formation energy is already absorbed into ehull.
  Bandgap is irrelevant for magnetism. So ehull is the cheap,
  per-candidate, deterministic stability gate; SUN enters as a
  batch-level shaping term instead (see Step 3).
- **No early-stop on reward plateau < 2 iters**: a 1-iter plateau is
  noise; 2 iters is signal. Paper uses 3 — too conservative for the
  small default `max_iters=3` of the MVP.

## How mattergen-eval SUN enters reward (batch-level shaping)

`mattergen-evaluate` produces **one aggregate metrics dict per batch**,
not per-candidate scores. There is no way to ask "is candidate X novel?"
— only "what fraction of this batch is novel?". The headline aggregate
is `frac_novel_unique_stable_structures` (SUN = S∧U∧N).

We wire that aggregate into reward as a **batch-level shaping term**:
`reward_i += w_sun * batch_SUN_prev`, where `batch_SUN_prev` is the SUN
of the **previous** iteration's batch (the mattergen-eval job drained
at the start of the current iteration). Lag is forced by the async
design — at the time we compute reward for the current batch, that
batch's own SUN has not been measured yet.

Honest caveat: because `batch_SUN_prev` is a constant across the pool
being ranked (new candidates ∪ parents, all re-normalized together in
Step 2), this term does **not** change selection within an iteration.
It only affects reported reward magnitude, reward trends across
iterations, and cross-run comparison. If you need real per-candidate
SUN pressure, switch to `batch_size=1` submission (see `mcp_tools.md`
§mattergen-evaluate) and replace `batch_SUN_prev` with the per-candidate
flag — at the cost of N× reference-set matching time.

What mattergen-eval *also* provides, still reported as population health
but not wired into reward:

- `frac_novel_structures`, `frac_unique_structures`,
  `frac_novel_unique_structures` — search diversity signals.
- `avg_structure_validity`, `avg_comp_validity` — structural sanity.
- `avg_energy_above_hull_per_atom`, `frac_stable_structures` —
  cross-check against ALIGNN `ehull`. Material disagreements here are
  normal (different reference sets / correction schemes) and surface
  in the iteration + final reports, never as a hard filter.

Ergonomic rule: keep ALIGNN `ehull` as the hard filter (cheap,
per-candidate, deterministic); let SUN shape reward at batch scope and
surface the rest as the population dashboard.
