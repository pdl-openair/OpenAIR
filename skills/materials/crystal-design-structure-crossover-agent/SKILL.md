---
name: crystal-design-structure-crossover-agent
description: Agent-guided multi-objective evolutionary search for inorganic crystals. Use when the user wants to discover / optimize / evolve crystal structures toward materials goals (e.g. "find a stable lead-free wide-bandgap oxide", "evolve hard materials maximizing bulk modulus", "search for low-ehull high-dielectric compositions", 进化晶体 / 多目标搜索 / 帕累托前沿 / 变异搜索 / 遗传算法找材料 / Pareto front / evolve structures / crystal discovery). Drives a diffusion-based evolutionary loop (mutation = noise+denoise via the diffusion-mutate MCP), evaluates fitness via a pluggable backend (alignn-predict by default; mattersim-relax energy as alternative), selects via NSGA-II/CDP, and lets Claude steer the search between generations. Also use when the user mentions DEMO / EGD / evolutionary crystal generation (this skill IS the engine — no separate service needed).
---

# crystal-design-structure-crossover-agent — agent-guided evolutionary crystal search

Orchestrate a multi-objective evolutionary search over the deployed material MCPs. You (Claude) are the **evolutionary strategist**: translate the user's goal into objectives/constraints, drive the loop one generation at a time, read each generation's snapshot, and adaptively steer (ban elements, tighten constraints, raise mutation strength, push diversity). The deterministic loop + selection code lives in `scripts/` and runs **in-container**; you call it per step.

## What runs where (read this first)

- **Deterministic code (skill-owned):** `scripts/evolve_driver.py` (step-CLI: init/step/steer/status/finalize) + `scripts/saes_selection.py` (NSGA-II/CDP + StructureMatcher pruning, vendored from flux) + `scripts/mcp_client.py` (HTTP client) + `scripts/evaluators.py` (pluggable fitness-backend registry).
- **Runs on the host, not in a container.** The driver needs only `pymatgen` + `numpy` (no GPU) and reaches the MCPs over the reverse proxy. Use any venv that has those installed. A wrapper `scripts/run.sh` auto-picks: `$EVOLVE_PY` if set and importable, else repo-root `.venv/bin/python` if it can `import pymatgen`, else falls back to a Docker image (override via `$EVOLVE_IMAGE`). `run.sh` also auto-loads `.env` from the repo root so `MATERIALS_MCP_BASE_URL` is in scope.
- **Fitness is pluggable via `--fitness-backend` at `init`.** Three backends ship today:
  - `alignn` (default) — fast/rough per-individual properties via alignn-predict (bandgap, ehull, dielectric, bulk/shear modulus, magmom, slme, supercon_tc, exfoliation_energy, formation_energy). Use for multi-property objectives.
  - `mattersim` — per-individual energy/forces/stress from mattersim-relax only (keys: `energy_per_atom`, `total_energy`, `f_max`, `s_max`). Faster (skips the second MCP), accurate energy. Best for stability-proxy goals under fixed composition.
  - `mattergen_eval` — SUN-aligned per-individual metrics via chunked batch `evaluate_structures` calls reading the server's `per_structure` detail (keys: `ehull_eval`, `ehull_sat`, `novel`, `stable`, `unique` (≡1 convention — batch uniqueness is chunk-size-dependent; population-level dedup is pilot_analysis's distinct audit), `comp_valid_eval`, `structure_valid`, `rmsd_relax` — 弛豫位移 RMSD(Å),skill 侧自算 raw vs relaxed(evaluate 的同名 batch 指标在已弛豫结构上恒 ~0,不可用)). ≈2–3× faster than the pre-2026-09 batch-of-1 protocol (values equivalent: flags identical, ehull diff ≤1e-6 eV/atom) and the only backend that exposes SUN novelty/stability. Old servers without `per_structure` auto-fall back to batch-of-1.
- **Compute primitives (deployed MCPs, must be up):** `diffusion-mutate` (mutation+crossover, required), `mattersim-relax` (required — relaxation for all backends, fitness source for `mattersim` backend), `alignn-predict` (required for `alignn` backend, otherwise optional), `mattergen-evaluate` (final novelty report, required only for `finalize --evaluate`), `mattergen-generate` (optional initial pool). Endpoints are derived as `${MATERIALS_MCP_BASE_URL}/<service>/mcp` from `.env` (host never committed); per-service override via `MCP_<NAME>_URL`. See `references/runtime-topology.md`.
- **No persistent engine service.** The skill itself IS the engine — state persists in the run dir (JSON), so each subcommand is independent.

## The driver invocation (every subcommand uses this wrapper)

```bash
bash <SKILL>/scripts/run.sh <subcommand> --run-dir <RUN_DIR> ...
```
`<SKILL>` is the absolute path to this skill directory in whatever repo it's synced into (e.g. `.claude/skills/crystal-design-structure-crossover-agent` after `sync_claude_skills.py`, or `skills/materials/crystal-design-structure-crossover-agent` in source). `run.sh` runs `evolve_driver.py` with a host Python that has `pymatgen` (no docker, no mounts by default). `<RUN_DIR>` (e.g. `outputs/evo_widegap`) holds `config.json`, `constraints.json`, `population.json`, per-gen snapshots, and final `leaderboard.csv` + `pareto/`. Because it's host-side, **seed CIF paths and `--run-dir` can be any host path** — no mount gymnastics. If no venv has pymatgen, `run.sh` falls back to `$EVOLVE_IMAGE` Docker image (then `--run-dir` must be under your cwd so it can be mounted).

## The flow

**Step 0 — Pre-flight.** Confirm the required MCPs are up (`curl` each `/mcp` handshake, see `references/runtime-topology.md`). Translate the user's NL goal → `objectives` + `constraints` using `references/property-keys.md` and `references/objective-constraint-schema.md`. Pick `--fitness-backend` (`alignn` default for multi-property; `mattersim` for energy-only stability-proxy), then `RUN_DIR`, `pop-size`, `max-generations`, `noise-level`, and `--crossover-rate` (0=pure mutation, 0.5=half/half — recommended when the seed pool is narrow).

**Step 1 — init.** Build + characterize the initial pool (seed CIFs via `--seed-cifs` — any host path now works, and/or `--generate N` for mattergen-generate). Show the user the generation-0 Pareto front and confirm the config before evolving further.
```
# Default alignn backend (multi-property objectives)
bash <SKILL>/scripts/run.sh init --run-dir <RUN_DIR> \
    --fitness-backend alignn \
    --objectives '{"ehull":"minimize","bandgap":"maximize"}' \
    --constraints '{"bandgap":{"min":0.5,"max":3.0,"weight":10},"element_set":{"banned":["Pb"]}}' \
    --pop-size 20 --max-generations 30 --noise-level 0.3 --crossover-rate 0.5 \
    --seed-cifs /path/to/AlInSe3.cif,/path/to/b.cif --generate 15

# mattersim backend (energy-only stability proxy under fixed composition)
bash <SKILL>/scripts/run.sh init --run-dir <RUN_DIR> \
    --fitness-backend mattersim \
    --objectives '{"energy_per_atom":"minimize"}' \
    --constraints '{"element_set":{"allowed_only":["Si","O"],"weight":10},"sg_number":{"allowed_only":[148,227]}}' \
    --pop-size 20 --max-generations 30 --noise-level 0.3 \
    --seed-cifs /path/to/seed_SiO2.cif
```
(`<SKILL>` = the absolute path to this skill directory, see "The driver invocation" above.)

**Step 2 — evolve loop (you steer each generation).** Repeat until `max-generations`:
1. `bash <SKILL>/scripts/run.sh step --run-dir <RUN_DIR>` → produces offspring (diffusion-mutate: mutation and/or crossover), characterizes (relax+alignn), runs SAES environmental selection, writes `snapshot_gen<N>.json`.
2. Read `snapshot_latest.json`. Decide steering per `references/steering-playbook.md`:
   - ban a dominant unwanted element → `steer --ban Pb`
   - tighten / add a constraint → `steer --constraint bandgap:1.0::10`
   - adjust a constraint's hardness → `steer --weight bandgap:5`
   - raise/lower mutation strength → `steer --noise-level 0.5`
   - flip crossover on/off mid-run → `steer --crossover-rate 0.5`
   - **doing nothing is valid** if the snapshot is healthy (good diversity, front improving, constraints OK).
3. If you steered, the next `step` picks it up from `constraints.json`. If not, just `step` again.

**Step 3 — finalize.**
```
bash <SKILL>/scripts/run.sh finalize --run-dir <RUN_DIR> --top-n 20 --evaluate
```
Exports `pareto/*.cif` + `leaderboard.csv`; `--evaluate` runs `mattergen-evaluate` for an independent novelty/stability report (`evaluate_report.json`).

**Step 3b — render Pareto CIF.** PNG rendering is now built into the `finalize` subcommand — `evolve_driver.py` calls `render_batch.py` automatically after writing all CIF files. Images land next to the CIF files (`pareto/01_MgO.png`, etc.). No manual step needed. If rendering fails for any reason it prints a warning but does not block the CIF artifacts.

**Step 4 — report.** Summarize the Pareto front to the user: top formulas, their objective trade-offs, novelty/stability metrics, the CIF paths, and the rendered PNG paths. Flag any infeasible candidates still on the front.

## Hard rules (enforce these)

1. **Objectives are fixed at `init`.** To shift objectives mid-search, start a new run seeded with the current Pareto front (`--seed-cifs` from `pareto/`). Changing objectives invalidates Pareto ranking — only `steer` (constraints/weights/noise) is allowed mid-run.
2. **Mutate outputs are characterized before selection.** The driver already does relax→alignn per offspring; don't inject uncharacterized structures.
3. **Crossover is available via `crossover_rate`.** The `diffusion-mutate` MCP exposes both `mutate_structure` (single-parent) and `crossover_structures` (two-parent blend). Set `--crossover-rate` at `init` (0 = pure mutation, 0.5 = half/half, 1 = pure crossover), or switch mid-run via `steer --crossover-rate`. Default 0 (mutation-only) unless the goal benefits from combining parents.
4. **Each MCP serializes GPU work** (process lock). A generation over pop 20 ≈ 20 mutations + ~40 characterizations; expect **minutes per generation** (faster for `mattersim` backend since it skips the second MCP). Tell the user it's slow and don't spam parallel calls.
5. **Fitness backend is fixed at `init`** (`--fitness-backend`), like objectives. Switching backend invalidates comparability of property values across the run — start a new run (you can reuse seed CIFs from the current Pareto front). Mid-run you may only `steer` constraints/weights/noise/crossover, not the backend.

## Reference files

- `references/property-keys.md` — NL term ↔ alignn/engine property key table (bandgap, ehull, bulk_modulus, …). Marks `cl_score` unavailable (JACS dropped).
- `references/objective-constraint-schema.md` — objectives/constraints JSON shapes, `weight ≥ 10 ≡ hard`, `element_set`/`sg_number` special keys, worked examples.
- `references/steering-playbook.md` — trigger→action table, soft phase vocabulary, when NOT to steer.
- `references/runtime-topology.md` — the 5 MCP endpoints, health-check curls, the docker-run wrapper, graceful degradation.

## Memory module (cross-run memory-augmented steering)

When `--memory-dir <path>` is set at `init`, every `step` writes two extra
artifacts next to `snapshot_latest.json`:

- `memory_query_result.json` — top-K natural-language memories retrieved from
  the shared store, ranked by Jaccard similarity × confidence. Read this file
  alongside the snapshot before deciding whether to steer.
- After the next step, a new `MemoryEntry` is appended to
  `<memory-dir>/store.json` capturing the (prev_context, last_steer_action,
  Δsun/Δehull) outcome. Confidence is outcome-driven: starts at 0.5,
  immediately ±0.05 depending on whether the action improved SUN/ehull
  (ExpeL-style upvote/downvote — retrieval frequency never boosts
  confidence), and decays ×0.98 per step.

To enable agent-with-memory mode in a run:

```bash
bash <SKILL>/scripts/run.sh init --run-dir <RUN_DIR> \
    --fitness-backend mattergen_eval \
    --objectives '{...}' --constraints '{...}' \
    --memory-dir /path/to/shared_memory \
    --family rare_earth_intermetallics \
    ...
```

See `references/memory-module.md` for schema and retrieval details.

## Goal-evolving demo (mid-search pivot)

For the Nature-paper Fig 7a demo. Mid-search objective change requires that
the new objectives' property keys are already characterized in the current
population (no re-evaluation):

```bash
bash <SKILL>/scripts/run.sh pivot --run-dir <RUN_DIR> \
    --objectives '{"ehull": "minimize", "novel": "maximize"}'
```

Re-ranks the existing population in place using NSGA-II fast non-dominated
sorting with the new objectives. Appends a `pivot_log.jsonl` entry. See
`references/goal-evolving-demo.md` for the demo runbook.

## 4-way ablation harness

For the Nature-paper Fig 4. Scaffolds N×M run directories for the four
steering modes:

```bash
python <SKILL>/scripts/ablation_runner.py scaffold \
    --base-run-dir <BASE> --family <TAG> \
    --fitness-backend mattergen_eval \
    --objectives '{...}' --constraints '{...}' \
    --seeds <cif1,cif2,...> \
    --modes static_rules,agent_no_mem,agent_with_mem \
    --replicates 5 --pop-size 16 --max-generations 4
```

STATIC_RULES runs are fully automated (`run_static.sh` per run dir).
AGENT_NO_MEM and AGENT_WITH_MEM runs require external Claude orchestration
(`MANUAL_ORCHESTRATION.md` per run dir). Aggregate metrics via
`pilot_analysis.py`.
