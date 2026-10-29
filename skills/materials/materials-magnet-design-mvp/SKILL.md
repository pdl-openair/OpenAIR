---
name: materials-magnet-design-mvp
description: >
  Run the SAGA-Opt MVP loop for permanent-magnet materials discovery: an
  evolutionary search that maximizes magnetic density (μB/Å³) while
  minimizing supply-chain risk (HHI), with an optional E_hull stability
  filter. Use whenever the user wants to "search for new magnet formulas",
  "evolve magnetic materials", "run SAGA MVP", "find high-mag-density low-HHI
  candidates", "do crossover on magnet compositions", mentions NdFeB / SmCo /
  ferrite alternatives, or asks to iterate formulas → 3D structures → scores.
  Triggers also on "磁密度", "永磁", "HHI", "magnet MVP", "SAGA-Opt",
  "DiffCSP + MatterSim + ALIGNN". The skill drives six remote MCPs
  (diffcsp-generate / mattersim-relax / alignn_prediction / mattergen-generate
  / mattergen-evaluate / material-platform) — Claude is both the orchestrator
  and the LLM crossover engine. pymatgen runs locally for HHI and CIF volume.
---

# SAGA Magnet-Materials MVP Skill

This skill runs the **MVP** version of SAGA-Opt: a fixed two-objective
evolutionary search over chemical formulas, with the inner loop wired to
remote materials models. The full SAGA outer loop (dynamic objectives,
auto-written scorers, over-optimization guards) is out of scope — see
`skills/materials/magnet_materials_design_MVP.md` §0 for the line.

The pipeline:

```
init formulas (MP) ─▶ LLM crossover ─▶ DiffCSP (formula→CIF)
                  ─▶ MatterSim relax ─▶ ALIGNN (magmom, ehull)
                  ─▶ pymatgen HHI   ─▶ filter + reward + select top-K
                                                       │
                                  ┌─── MatterGen-evaluate (async, agg metrics:
                                  │     novelty / uniqueness / validity / stability,
                                  │     incl. SUN = frac_novel_unique_stable_structures)
                                  │         SUN of the PREVIOUS iter's batch shapes
                                  │         reward at batch scope (constant across pool)
                                           │
                                           ▼
                                      next iteration
```

## The architectural reality

Six remote MCPs do the heavy lifting. Claude is a thin orchestrator that
also doubles as the crossover LLM.

| Stage | Tool | Lives at |
|-------|------|----------|
| Formula → CIF | `mcp__diffcsp-generate__generate_structures` | `<MATERIALS_MCP_BASE_URL>/diffcsp-generate/mcp` |
| CIF relax | `mcp__mattersim-relax__relax_structure` | `<MATERIALS_MCP_BASE_URL>/mattersim-relax/mcp` |
| Properties | `mcp__alignn_prediction__predict_alignn_properties` | `<MATERIALS_MCP_BASE_URL>/alignn/mcp` |
| Population health (async) | `mcp__mattergen-evaluate__evaluate_structures_async` | `<MATERIALS_MCP_BASE_URL>/mattergen-evaluate/mcp` |
| (Optional) Crystal gen | `mcp__mattergen-generate__generate_crystals` | `<MATERIALS_MCP_BASE_URL>/mattergen-generate/mcp` |
| (Optional) Platform | `mcp__material-platform__*` | `<MATERIALS_MCP_BASE_URL>/material-platform/mcp` |
| HHI + volume | local pymatgen (no MCP) | the agent's own python |

Claude never imports torch, never runs DiffCSP locally, never SSHes.
Everything heavy is a tool call. HHI and CIF volume are cheap, so they run
locally via `scripts/hhi.py` and `scripts/mag_density.py`.

## Fixed objectives (do not evolve)

Two per-candidate objectives, equal weight, min-max normalized inside
each batch, plus one batch-level shaping term from mattergen-evaluate:

```
reward_i = norm(mag_density)_i + (1 - norm(hhi)_i) + w_sun * batch_SUN_prev
```

- `mag_density = total_magmom_μB / cell_volume_Å³` — bigger is better.
- `hhi = HHI_production` from `pymatgen.analysis.hhi.HHIModel` — smaller is better.
- `batch_SUN_prev` = `frac_novel_unique_stable_structures` (S∧U∧N) from
  the prior iteration's mattergen-evaluate job (drained at the start of
  the current iter). Lagged by one iteration because the current
  batch's SUN isn't measured until after selection. `0` on iteration 0.
- `w_sun` = `0.5` by default (configurable; set to `0` to disable
  shaping without dropping SUN from reports).
- Thresholds (informational, not hard gates unless `ehull_lt` is set):
  - mag_density > 0.2 Å⁻³
  - hhi < 1500
  - ehull < 0.1 eV/atom (hard filter when `filter_ehull=True`, default on)

The two per-candidate terms decide ranking within the pool. The SUN
term is constant across the pool being ranked, so it shifts reported
reward magnitude / trends but does **not** change selection within an
iteration — see `references/reward_and_filters.md` §"How mattergen-eval
SUN enters reward" for the precise semantics and the upgrade path to
per-candidate SUN via `batch_size=1` submission.

Why equal weight and min-max norm: it is the simplest non-trivial
aggregation. If one objective dominates the user's actual priority, ask
them and rebalance — but for MVP this is fine.

## Phase 0 — Bootstrap (do this before any run)

1. Run `python scripts/probe_mcps.py`. All five core MCPs (`diffcsp`,
   `mattersim`, `alignn`, `mattergen-evaluate`, and the local pymatgen
   check) must PASS. If any fail, do not start a run — GPU time is
   expensive.
2. Confirm `MP_API_KEY` is available — read from the environment, or
   from a `.env` file. `scripts/initial_formulas.py` auto-loads `.env`
   (search order: `$OPENAIR_DOTENV`, then nearest `.env` walking up
   from CWD, then nearest `.env` walking up from the script). An
   explicit env var always wins over `.env`. If neither source has it,
   ask the user before proceeding — the MP init step will fail without
   it. (Fallback: `scripts/initial_formulas.py --fallback` returns a
   curated 20-formula list of Fe/Co/Ni/Cr/Mn binaries and ternaries so
   the loop can still run end-to-end. Tell the user when you use the
   fallback.)
3. Sanity-check the config against the user's intent — see "Config" below.

## Config (the only knob the user usually touches)

```python
config = {
    "init_n":          10,    # formulas pulled from MP for gen 0
    "batch_size":       3,    # new formulas per iteration (tokens scale linearly)
    "max_iters":        3,    # hard cap on iterations
    "top_k":            5,    # parents retained between iterations
    "filter_ehull":     True, # hard-filter on ALIGNN ehull < 0.1 eV/atom
    "ehull_lt":         0.1,  # eV/atom
    "mag_density_gt":   0.2,  # Å^-3 (informational threshold for reporting)
    "hhi_lt":          1500,  # informational threshold for reporting
    "run_mattergen_eval": True, # submit each iter's relaxed batch to
                                # mattergen-evaluate (async) for aggregate
                                # novelty / uniqueness / stability metrics.
                                # Reuses the MatterSim-relaxed CIFs + energies
                                # so no second relax is performed.
    "w_sun":            0.5,  # batch-level SUN shaping weight added to
                                # every candidate's reward (constant across
                                # pool — see "Fixed objectives"). Set to 0
                                # to disable shaping while still reporting
                                # SUN in iteration / final reports.
    "seed":             0,
}
```

These defaults are deliberately tiny so a first run finishes in a few
minutes on the GPU and a few thousand tokens. Production-scale runs
(100 init / 20 batch / 10 iters, the paper's SAGA setting) cost real
GPU+token budget — only scale up after a smoke run confirms the loop is
green.

## Phase 1 — Initialize the parent pool

```python
# scripts/initial_formulas.py --n 10 --out /tmp/init.json
# returns [{"formula": "Fe3O4", "mp_id": "mp-19770"}, ...]
```

The script queries MP for chemistries likely to contain transition metals
(Fe/Co/Ni/Cr/Mn + a p-block anion), shuffles, takes `init_n`. The exact
filter is in the script. If MP is unreachable, the script writes the
fallback list — surface that to the user clearly.

## Phase 2 — The loop (one iteration)

For each iteration `i` in `1..max_iters`:

### 2a. LLM crossover (Claude does this directly)

**If a mattergen-evaluate job from the prior iteration is still pending**,
poll it now via `mcp__mattergen-evaluate__get_evaluation_result(job_id=...)`
(see step 2f). If status is `done`, capture `metrics` into the run log
and stash `metrics.frac_novel_unique_stable_structures` as
`batch_SUN_prev` for this iteration's reward (step 2g). If `error` or
still `pending`, set `batch_SUN_prev = 0` and note it in the report.
Either way, do not block the loop on it.

Look at the current parents (formula + mag_density + hhi + reward) and
propose `batch_size` new formulas. Apply real magnetic-materials domain
knowledge:

- **Substitution**: swap one element for a same-group or same-row peer
  (Co↔Fe↔Ni, Cr↔Mn, Te↔Se↔S, B↔C↔N).
- **Stoichiometry tweak**: shift ratios within a known family
  (Nd2Fe14B → Nd2Fe13CoB, SmCo5 → Sm2Co7).
- **Structure-type transfer**: take a known structure prototype (Heusler,
  perovskite, ThMn12, YCo5) and swap the magnetic site.
- **Avoid obvious garbage**: no noble gases, no actinides beyond U, no
  monatomic "formulas", respect typical valence.

Bad suggestions are fine — selection culls them — but try to make each
new formula plausibly synthesizable. **Never emit a formula you've already
scored in this run** unless the user asked for repeats.

Output: a plain list of `batch_size` formula strings, e.g.
`["Nd2Fe13CoB", "Sm2Co7", "Fe3Sn2"]`.

### 2b. Structure generation (DiffCSP, parallel fan-out)

For each formula, call `mcp__diffcsp-generate__generate_structures` with:

```
formula=<f>, model_name="mp_csp", num_evals=1
```

`mp_csp` is the general MP-20 checkpoint — use it unless the user
specifies a different model (carbon/perov/mpts). One call per formula,
all in the same assistant turn (parallel fan-out). Each returns a CIF
inside `structures[0].cif`. Collect failures — a formula failing DiffCSP
is normal (the model rejects chemistries outside its training
distribution); skip it and note the count.

### 2c. Geometry optimization (MatterSim relax, parallel fan-out)

For each CIF from 2b, call `mcp__mattersim-relax__relax_structure` with
`cif_content=<cif>, max_steps=50`. Returns `relaxed_cif` plus
`energy_per_atom_eV` and `f_max_eV_A` (force convergence indicator).
Drop the candidate if `f_max_eV_A > 1.0` after relaxation — that means
MatterSim did not converge and the structure is not physical.

### 2d. Property scoring (ALIGNN, parallel fan-out)

For each relaxed CIF, call `mcp__alignn_prediction__predict_alignn_properties`
with `cif_content=<relaxed_cif>, models=["magmom", "ehull"]`. Returns
`properties.magmom.value` (μB total) and `properties.ehull.value`
(eV/atom). Skip the candidate if either returns `value: null` (model
failure) — that's normal for off-distribution chemistries.

### 2e. mag_density + HHI (local python)

```bash
python scripts/mag_density.py --magmom <μB> --cif <path-or-string>
# → {"mag_density": 0.34, "volume": 211.5, "n_atoms": 12}

python scripts/hhi.py --formula "<formula>"
# → {"hhi": 1828.9, "hhi_reserve": 1129.5}
```

Run both per surviving candidate, in parallel where possible.

### 2f. Population health (MatterGen-evaluate, async — reuses relaxed CIFs)

The candidates are already relaxed (2c) and we already have MatterSim
energies, so this step is essentially free of extra GPU cost. Submit the
batch in the background and poll later — do NOT block the loop.

Skip entirely if `run_mattergen_eval=False`.

```
mcp__mattergen-evaluate__evaluate_structures_async(
    structures=[{"cif_content": r["cif"], "name": r["formula"]}
                for r in surviving_candidates],
    relax=False,                                # already done in 2c
    energies=[r["total_energy_eV"] for r in surviving_candidates],
    energy_correction_scheme="MP2020",          # matches MatterSim's training
    structure_matcher="disordered",
)
# returns {job_id, status: "pending", hint}
```

Notes:
- `total_energy_eV` per candidate = `energy_per_atom_eV * num_atoms` from
  the MatterSim relax response. `num_atoms` is also in that response, or
  recompute from the CIF.
- `relax=False` skips the MatterSim-via-MatterGen re-relax — we already
  have a relaxed CIF + energy, so spending another ~minute per structure
  would be pure waste.
- This is **aggregate** (one metrics dict for the whole batch). It does
  NOT produce per-candidate flags. The headline metric
  `frac_novel_unique_stable_structures` (SUN) flows into the next
  iteration's reward as a batch-level shaping term (see step 2g and
  `references/reward_and_filters.md` §"How mattergen-eval SUN enters
  reward"); the other fractions remain informational.
- On small batches (`batch_size=3`) the fractions (e.g.
  `frac_novel_structures=0.33`) are coarse — note this in the report.
  They become meaningful when aggregated across iterations; Phase 3
  optionally re-submits the union of all survivors as one batch.

Carry `job_id` forward to step 2a of the next iteration (or to Phase 3
if this is the last iteration) and poll there.

### 2g. Filter + reward + select

For each surviving candidate:

```
record = {
    "formula": f,
    "cif": relaxed_cif,
    "magmom": μB, "volume": Å³, "mag_density": μB/Å³,
    "hhi": hhi_production,
    "ehull": eV/atom,
    "reward": None,                  # filled after normalization
}
```

1. **Hard filter** (if `filter_ehull=True`): drop everything with
   `ehull >= ehull_lt`. If fewer than 2 candidates survive the filter,
   relax it for this iteration only and warn the user — the loop can
   still progress.
2. **Normalize** within the surviving batch:
   - `mag_density_norm = (mag - min_mag) / (max_mag - min_mag)`
   - `hhi_norm = (hhi - min_hhi) / (max_hhi - min_hhi)`
   - Edge case: if all mag (or hhi) equal, set that norm column to 0.5.
3. **Reward** = `mag_density_norm + (1 - hhi_norm) + w_sun * batch_SUN_prev`.
   The SUN term is the same constant for every candidate in the pool
   being ranked (see "Fixed objectives"); it shifts reported reward
   magnitude / trend but does not change within-iteration ranking.
4. **Pool** = new records ∪ current parents (parents keep their reward
   from the prior iteration; renormalize the whole pool together so
   rewards are comparable).
5. **Select top-K** = sort pool by reward desc, take `top_k`.

### 2h. Report

After each iteration, surface to the user:

- iteration number, best reward, best formula, best mag_density + hhi
- new entries that made the top-K (highlights)
- any filters tripped / DiffCSP failures / MatterSim non-convergence
- **population health from the prior iteration's mattergen-evaluate job**
  (if `run_mattergen_eval=True` and the job finished): one line with
  `frac_novel`, `frac_unique`, `frac_stable`, `SUN`
  (`frac_novel_unique_stable_structures`), `avg_energy_above_hull`. Also
  note the `w_sun * batch_SUN_prev` addend applied to every candidate's
  reward this iteration. If the job is still pending, say so and defer
  to next iteration.

A compact table is fine:

```
iter 2 | best reward=1.08 | Fe3Sn2 (mag_d=0.41 Å⁻³, hhi=820, ehull=0.03)
       | new in top-K: Fe3Sn2, Co3Sn2
       | dropped: 2 (DiffCSP fail), 1 (ehull=0.31)
       | mattergen-eval (iter 1 batch, n=3): novel=0.33 unique=0.67
       |   stable=1.00 SUN=0.33 avg_e_above_hull=0.04 — small batch, coarse
       | reward shaping: +w_sun*batch_SUN_prev = +0.5*0.33 = +0.165 (all candidates)
```

### 2i. Early stop

If the best reward in the pool has not improved for 2 consecutive
iterations, stop early and tell the user. Don't burn GPU on a converged
search.

## Phase 3 — Final report

After the loop ends (max_iters reached or early stop):

0. **Drain pending mattergen-evaluate jobs**. Poll any job_id still open
   from the last iteration. Then, if `run_mattergen_eval=True`, optionally
   re-submit the **union** of all surviving candidates from the whole run
   as one batch — this gives less-noisy aggregate fractions than the
   per-iteration batches. Wait for it (it's a single async call) and
   surface its `metrics` in item 5 below.

Produce:

1. **Top-K table**: formula, mag_density, hhi, ehull, reward — sorted.
2. **Pareto-style note**: if mag_density and hhi trade off (which they
   usually do — rare-earth high-mag ↔ high-HHI), point out the knee.
3. **Chemical trends**: e.g. "Co-based formulas dominate the high-reward
   end this run", "all top-K contain Sn as the anion".
4. **Recommended next run**: tighten / loosen / shift chemistry.
5. **Population health (MatterGen-evaluate)**: aggregate
   novelty / uniqueness / validity / stability across all candidates
   evaluated this run, with SUN (`frac_novel_unique_stable_structures`)
   surfaced as the headline. Also report the per-iteration
   `batch_SUN_prev` series and the cumulative reward shaping contribution
   (`w_sun * Σ batch_SUN_prev`). Compare `avg_energy_above_hull_per_atom`
   to the ALIGNN `ehull` filter — if they disagree materially, flag it.
6. **DoD checklist**: every box in `magnet_materials_design_MVP.md` §6
   that the run actually exercised.

Optionally save the top-K structures as CIFs to a user-specified
directory for downstream DFT.

## Tool-by-tool quick reference

See `references/mcp_tools.md` for full signatures. Quick map:

| MCP | Tool | Key inputs | Key outputs |
|-----|------|------------|-------------|
| diffcsp-generate | `generate_structures` | formula, model_name, num_evals | structures[].cif |
| mattersim-relax | `relax_structure` | cif_content, max_steps | relaxed_cif, f_max_eV_A, energy_per_atom_eV |
| alignn_prediction | `predict_alignn_properties` | cif_content, models=[magmom,ehull] | properties.{magmom,ehull}.value |
| mattergen-evaluate | `evaluate_structures_async` | structures[], relax=False, energies[] | job_id → metrics (aggregate) |
| mattergen-generate | `generate_crystals` | (optional CSP) model_name, conditions, num_samples | structures[].cif |
| material-platform | (many) | only if user wants to upload results | dataset/model/code CRUD |

## What this MVP does NOT do (by design)

- **No dynamic objective evolution**. Two fixed objectives. The full
  SAGA analyzer/planner/implementer loop is §7 of the MVP doc, not here.
- **No per-candidate SUN synthesizability filter**. SUN enters only as a
  batch-level shaping term (`w_sun * batch_SUN_prev`, constant across
  the pool — see "Fixed objectives"). A per-candidate SUN hard filter
  would require `batch_size=1` mattergen-evaluate calls (minutes per
  structure against the 845k alex-mp reference set); E_hull stays the
  only per-candidate hard filter.
- **No automatic over-optimization guards** (scaling/truncation).
- **No DFT validation in-loop**. Top-K is reported for offline VASP.

If the user asks for any of the above, escalate to the full SAGA doc
(`skills/materials/magnet_materials_design.md`), not this MVP skill.

## Failure modes the agent should handle

| Symptom | Likely cause | Agent action |
|---------|-------------|--------------|
| DiffCSP returns `value:null` for >50% of formulas | Off-distribution chemistry for `mp_csp` | Suggest switching to `mpts_csp` or restructuring crossover |
| MatterSim `f_max_eV_A > 1.0` for most candidates | Bad initial CIFs / wrong stoichiometry | Drop those candidates; if all fail, retry DiffCSP with `num_evals=3` |
| ALIGNN returns `null` for magmom | Structure too off-distribution | Drop the candidate; do not retry — ALIGNN failures are deterministic |
| MP API key missing / 401 | Not in env or `.env` | Use fallback list, tell user, suggest `export MP_API_KEY=...` or adding it to `.env` |
| All candidates filtered by ehull | Threshold too tight for the chemistry family | Loosen to 0.2 eV/atom for one iteration; warn the user |
| Loop converges in 1-2 iters | Population too small or crossover too conservative | Suggest raising batch_size or `top_k` |
| mattergen-evaluate job `status:error` | Sidecar OOM or reference-set mismatch | Note in report; do not retry inside the loop. `batch_SUN_prev = 0` for the affected iteration; ranking is unaffected because the SUN term is constant across the pool |
| `avg_energy_above_hull` (MatterGen) and ALIGNN `ehull` disagree strongly | Different correction schemes or reference sets | Surface both; keep ALIGNN ehull as the hard filter for consistency |
| mattergen-evaluate `parse_errors` non-empty | A CIF the sidecar couldn't parse | Cross-check the candidate's CIF and skip it from next batch |

## Bundled resources

- `scripts/probe_mcps.py` — smoke-test all 5 core MCPs (incl.
  mattergen-evaluate via `tools/list`) + local pymatgen. Run before every
  fresh run.
- `scripts/initial_formulas.py` — MP query via `mp_api`. Falls back to a
  bundled list if MP is unreachable or no API key.
- `scripts/hhi.py` — HHI from formula via `pymatgen.analysis.hhi.HHIModel`.
- `scripts/mag_density.py` — mag_density from magmom + CIF volume via
  pymatgen `Structure`.
- `references/mcp_tools.md` — full signatures for every MCP tool this
  skill calls. Authoritative.
- `references/reward_and_filters.md` — exact reward math, normalization
  edge cases, filter rationale.
- `evals/evals.json` — 3 test prompts covering smoke / multi-iter /
  failure-injection.
- `tests/test_helpers.py` — unit tests for `hhi.py` + `mag_density.py`.

## Quick start (when the user says "just run it")

1. `python scripts/probe_mcps.py` — confirm green.
2. Confirm `MP_API_KEY` is set (or accept fallback).
3. Run a 1-iteration smoke: `init_n=5, batch_size=3, max_iters=1,
   top_k=5`. Surface results.
4. If green, run the full MVP default: `init_n=10, batch_size=3,
   max_iters=3, top_k=5`.
5. Report the top-K table + Pareto knee + recommended next run.
