---
name: materials-new-crystal-generation
description: >
  Orchestrate the DEMO (Diffusion-based Evolutionary Multi-objective Optimization) pipeline on MatterGen for inorganic crystal discovery. Use whenever the user wants to RUN/CONFIGURE/DEBUG/EXTEND the evolutionary loop, modify a running search (tighten constraints, down-rank structures, inject priors), inspect progress (Pareto front, population), evaluate a CIF, debug a stalled run, or explain a result. Triggers: "跑一轮进化", "evolve 2D dielectric crystals", "find a ternary semiconductor with bandgap 1-2 eV", "kick off a run", "intervene mid-run", "inject domain knowledge", or mentions of Evoloop / TriplePopCMOEA / StandardCDP / Pareto / MatterGen / MatterSim / JACS cl_score / cppred. Two remote HTTP MCP servers (FastMCP on mcp.example.internal:22222): mattergen MCP runs the entire loop; crystal-property-prediction MCP computes 9 properties per CIF. Claude runs no models locally and never SSHes.
---

# DEMO Evolutionary Orchestration Skill

This skill turns Claude into a research collaborator for the DEMO
evolutionary pipeline. The pipeline discovers inorganic crystals that
satisfy property constraints while pushing the Pareto frontier of two (or
more) objectives, using MatterGen's diffusion model as a structure-aware
genetic operator.

## The architectural reality

Everything heavy lives behind **two remote HTTP MCP servers** on the same
GPU box (`mcp.example.internal:22222`). Claude is a thin orchestrator.

```
              ┌────────────────────────────────────────────────────┐
              │              Claude (this skill)                    │
              │  - translates user intent → objectives/constraints  │
              │  - submits runs, monitors, intervenes, reports      │
              └─────────┬──────────────────────────┬────────────────┘
                        │ HTTP MCP                 │ HTTP MCP
                        ▼                          ▼
   ┌────────────────────────────────────┐  ┌──────────────────────────────┐
   │ mattergen MCP                      │  │ crystal-property-prediction MCP                     │
   │ http://mcp.example.internal:22222/         │  │ http://mcp.example.internal:22222/   │
   │        mcp/mattergen/mcp           │  │        mcp/flux/mcp          │
   │                                    │  │                              │
   │  The entire evolution loop:        │  │  predict_flux_properties     │
   │  - run_autonomous_evolution        │  │  (CIF → 9 scalar properties) │
   │  - get_autonomous_evolution_result │  │  MatterSim + ALIGNN + CHGNet │
   │  - create_evolution_session        │  │  + JACS in one call          │
   │  - start/step/continue_evolution   │  │                              │
   │  - get_evolution_status/population │  │                              │
   │  - get_evolution_pareto_front      │  │                              │
   │  - modify_crystal_fitness          │  │                              │
   │  - add_evolution_constraint        │  │                              │
   │  - inject_evolution_knowledge      │  │                              │
   │                                    │  │                              │
   │  Holds: MatterGen ckpt, MatterSim,│  │                              │
   │  JACS ensemble, EMO engine state   │  │                              │
   └────────────────────────────────────┘  └──────────────────────────────┘
```

**The agent never imports torch / pymatgen. The agent never runs
`Evoloop.py` directly. The agent never SSHes into the GPU box.**
Everything is a tool call.

## Two execution modes

### Autonomous mode (default, recommended)

One-shot asynchronous submit + poll. Use this for ~95% of requests.

```
1. run_autonomous_evolution({...})      → returns task_id immediately
2. get_autonomous_evolution_result(task_id, top_pareto=10)
   → status: initializing | running | completed
   → poll every ~30-60s until status=completed
3. read pareto_front from final response
```

The agent's job here is mostly to translate the user's intent into the
right `objectives_json` / `constraints_json` and to interpret the
resulting Pareto front in plain language.

### Interactive mode (when the user wants to steer mid-run)

Step-by-step with pause points for intervention. Use this when the user
explicitly wants to inject domain knowledge, fix fitness mid-run, or
watch the population evolve one generation at a time.

```
1. create_evolution_session(mode="interactive", {...})  → session_id
2. start_evolution(session_id, auto_continue=false)
3. step_evolution(session_id, steps=1, pause_at="fitness_evaluated")
4. inspect: get_evolution_status, get_evolution_population
5. optionally intervene:
   - modify_crystal_fitness    (e.g. penalize candidates with Pb)
   - add_evolution_constraint  (e.g. tighten bandgap to [1.0, 2.5])
   - inject_evolution_knowledge(e.g. "prior literature favors Cr-based")
6. continue_evolution(session_id)
7. repeat 3-6 until max_generations
8. get_evolution_pareto_front(session_id)
```

`pause_at` accepts `generation_start` / `fitness_evaluated` /
`generation_end`. The natural intervention point is
`fitness_evaluated` — properties are computed but selection hasn't run
yet, so modifications take effect immediately for the current generation.

## Configuring a run

`run_autonomous_evolution` and `create_evolution_session` take the same
arguments. All have sensible defaults; only override what the user asks
for.

| Argument | Type | Range | Default | Meaning |
|----------|------|-------|---------|---------|
| `session_name` | string | — | auto | Human-readable label; appears in `list_evolution_sessions` |
| `n_pops` | int | 1–256 | 8 | Population size per group |
| `max_generations` | int | 1–100 | 2 | Hard cap on generations |
| `n_offspring_per_generation` | int | 1–256 | 8 | Offspring count per generation |
| `noise_level` | float | 0.0–1.0 | 0.3 | MatterGen mutation strength |
| `model_path` | string | — | env `MATTERGEN_MODEL_PATH` | Checkpoint path; leave empty for default |
| `emo_engine` | string | — | `TriplePopCMOEA` | Or `StandardCDP` |
| `objectives_json` | string (JSON) | — | ehull min + dielectric max | Pareto axes |
| `constraints_json` | string (JSON) | — | 3 example constraints | CDP constraints |
| `initial_pool_mode` | enum | random / cif_dir / hybrid | random | How to build gen 0 (see "Initial population modes" below) |
| `seed_cif_files` | string (base64 or JSON) | — | `""` | Seed CIFs to anchor gen 0; **preferred way to customize the initial pool**. Single CIF: raw base64. Multiple: JSON array `["b64_1","b64_2"]` or named `[{"name":"a.cif","cif_base64":"..."}]`. Server tags each as `meta={"type":"initial_cif"}` and `name="init_NNN.cif"`. |
| `batch_size` | int | ≥1 | 4 | MatterGen per-batch generation count |
| `max_attempts` | int | ≥1 | 10 | Reject-and-retry on invalid samples |

### Initial population modes

What each `initial_pool_mode` value actually does server-side (verified against `Evoloop.py:284-313` and the mattergen MCP source at `DEMO/evolution_mcp_server/`):

| Mode | Source of gen 0 | When `seed_cif_files` is also set |
|------|-----------------|-----------------------------------|
| `random` | MatterGen diffusion samples N structures from scratch — no external anchors | seeds are loaded first, then `random` fill runs for any remaining slots |
| `cif_dir` | Reads CIFs from a server-side directory | seeds still load; `cif_dir` path is server-configured, not exposed via this MCP |
| `hybrid` | Load CIFs first, then MatterGen fills remaining slots up to `n_pops` (`Evoloop.py:294-313` parity) | **Known bug (2026-06-15):** when only `seed_cif_files` is set (no `cif_dir`), `hybrid` does NOT trigger the model fill — population stays at the seed count. Workaround until server fix: pass `initial_pool_mode="random"` so model generation kicks in alongside the seeds, or wait for the server-side fix. |

For "I want my own CIFs to seed the search" — the answer is `seed_cif_files` with `random` mode. The seeds land in the pool tagged `meta={"type":"initial_cif"}`, and MatterGen generates the rest.

### objectives_json

JSON string. Exactly two keys for the 2D Pareto plot. Values are
`"minimize"` or `"maximize"`. Keys must match the property names returned
by `predict_flux_properties` (see `references/property_catalog.md`).

```json
{"ehull_eV": "minimize", "dielectric_epsx": "maximize"}
```

Common pairings:

- Stability vs property: `ehull_eV` ↓ vs `bandgap_eV` ↑
- Stability vs function: `ehull_eV` ↓ vs `dielectric_epsx` ↑
- Synthesizability vs function: `cl_score` ↑ vs `bandgap_eV` ↑

### constraints_json

JSON string. Each constraint is keyed by property name and supports:

```jsonc
{
  "bandgap_eV":          {"min": 0.2, "max": 3.5, "weight": 5.0},
  "formation_energy_eV": {"max": 0, "weight": 10.0},      // hard
  "num_elements":        {"min": 3, "max": 3, "weight": 10.0},  // exact ternary
  "element_set":         {"banned": ["Hg","Tl","Na","K","F"], "weight": 5.0},
  "sg_number":           {"banned": [1], "weight": 1.0}
}
```

Rules:

- `weight` 10 ≈ hard (any violation sinks the candidate below all feasible
  ones); weight 1 ≈ soft. Default 5 if omitted.
- `element_set` accepts `banned` (blacklist) or `allowed_only` (whitelist), not both.
- `num_elements` with `min == max` enforces an exact k-ary search.
- Empty constraints: `"{}"` — pure Pareto search, no feasibility filter.
- Constraint keys MUST match property names. A typo (`bandgap` vs
  `bandgap_eV`) silently turns the constraint into a no-op because every
  candidate reads the 999.0 penalty default.

## Property evaluation (the crystal-property-prediction MCP)

`predict_flux_properties` takes one CIF and returns 9 scalar properties in
one call. Internally it runs the full pipeline: MatterSim relax → CHGNet
(forces/stress/exfoliation) → ALIGNN (formation/bandgap/dielectric/ehull)
→ JACS (cl_score). All server-side, all in one round-trip (~10s for
CrSiTe3, longer for big structures).

CIF input — pick ONE of four mutually exclusive forms:

| Form | When to use |
|------|-------------|
| `cif_content` | CIF text in a string — **preferred for agent use** |
| `cif_path` | Path on the server's filesystem (server-side runs only) |
| `cif_base64` | Same content, base64-encoded |
| `cif` | Auto-detect: looks like content → content; looks like path → path |

Optional `name` tags the structure for logs. Optional `fields` (string
array) returns only a subset — useful for cheap re-checks.

If no CIF is provided at all, the server uses its built-in default
(CrSiTe3) — handy for smoke tests.

Returns:

```jsonc
{
  "name": "CrSiTe3.cif",
  "path": null,                  // server path if cif_path was used
  "input_source": "content",
  "properties": {
    "bandgap_ev": 0.596,         // eV (ALIGNN jv_optb88vdw_bandgap)
    "formation_energy_ev": -0.128,  // eV/atom (ALIGNN jv_formation_energy_peratom)
    "hull_ev": 2.28,             // eV/atom (ALIGNN jv_ehull)
    "exfoliation_energy_mev": 2.9,  // meV/atom (CHGNet 2D-vs-bulk)
    "f_max_ev_a": 0.464,         // eV/Å (MatterSim-relaxed + CHGNet)
    "cl_score": 0.786,           // 0..1 (JACS ensemble)
    "num_elements": 3,
    "element_set": ["Cr","Si","Te"],
    "sg_number": 148
  }
}
```

**Property key naming note**: the crystal-property-prediction MCP returns lowercase `_ev` /
`_mev` / `_ev_a` suffixes (`bandgap_ev`, `f_max_ev_a`). The constraint
JSON in mattergen MCP historically uses `_eV` / `_meV` / `_eV_A` suffixes
(`bandgap_eV`, `f_max_eV_A`). The two servers share the same backing
code, so both suffixes work in `objectives_json` / `constraints_json` —
but **when writing a constraint, use the `_eV` form** for consistency
with `Evoloop.py`. See `references/property_catalog.md` for the full
table and aliases.

## Phase 0 — Bootstrap (the agent's first move)

Before submitting any run:

1. Run `scripts/probe_mcp.py`. Both servers must PASS. Do NOT submit a
   run if either is unreachable — the GPU time is the most expensive
   resource in this project.
2. If `evolution_service_info` shows `active_sessions > 0`, decide
   whether to wait, list sessions (`list_evolution_sessions`), or kill
   one (`delete_evolution_session`). One run per GPU is usually right.
3. Sanity-check the constraint schema against
   `references/property_catalog.md` — every key must exist in the
   cppred response.

## Phase 1 — Submit a run (autonomous)

```python
args = {
    "session_name": "ternary_dielectric_v1",
    "n_pops": 32,
    "max_generations": 20,
    "n_offspring_per_generation": 32,
    "noise_level": 0.3,
    "emo_engine": "TriplePopCMOEA",
    "objectives_json": json.dumps({"ehull_eV": "minimize",
                                   "dielectric_epsx": "maximize"}),
    "constraints_json": json.dumps({
        "bandgap_eV": {"min": 0.5, "max": 3.0, "weight": 5.0},
        "formation_energy_eV": {"max": 0, "weight": 10.0},
        "cl_score": {"min": 0.5, "weight": 5.0},
        "num_elements": {"min": 3, "max": 3, "weight": 10.0},
        "element_set": {"banned": ["Hg","Tl","Pb","Cd"], "weight": 5.0}
    }),
    "initial_pool_mode": "random"
}
task_id = run_autonomous_evolution(args)["task_id"]
```

## Phase 2 — Monitor

```python
while True:
    r = get_autonomous_evolution_result(task_id, top_pareto=10)
    if r["status"] == "completed":
        pareto = r["pareto_front"]
        break
    # r["status"] in {"initializing", "running"}
    # r typically has current_generation, n_qualified_so_far, etc.
    sleep(45)
```

Report progress to the user at meaningful checkpoints — generation 1
done, halfway, final. Surface interesting candidates as they appear
rather than dumping the whole Pareto front at the end.

## Phase 3 — Interpret the Pareto front

The Pareto front is a list of candidates that trade off the two
objectives. The agent's job is to read it like a materials scientist:

- Identify the **knees** — points where giving up a little of one
  objective buys a lot of the other. These are usually the most
  interesting candidates.
- Flag candidates with **red-flag properties** even if they made the
  front: very low cl_score (synthesizability risk), very high f_max
  (unrelaxed), high hull_ev (thermodynamically unstable).
- Note **chemical trends** — if the front is dominated by Cr/S/Se
  combinations, that's a result worth surfacing.
- Recommend **next runs** based on gaps: "the front is sparse on the
  high-dielectric end; consider raising n_pops or adding a fourth
  element".

## Phase 4 — Iterate

Based on the user's reaction to the Pareto front:

- **Tighten constraints** for a focused second run.
- **Add `inject_evolution_knowledge`** in a new interactive run if the
  user has literature-backed priors (e.g. "this family is known to favor
  space group 148").
- **Switch engines** — if TriplePopCMOEA's feasible population collapses
  to a single cluster, try StandardCDP.
- **Raise `noise_level`** slightly (0.3 → 0.4) if the front stops moving.

## Adding a new property

This is now a server-side change, not an agent change:

1. Add the property to the crystal-property-prediction MCP's `predict_flux_properties` response
   (or to the underlying `extract_all_properties` in
   `DEMO/characterize_functions.py` that both MCPs share).
2. Add the property key to `references/property_catalog.md`.
3. From the agent's perspective: nothing changes. The new property is
   immediately usable as a constraint or objective.

## Debugging a stalled run

| Symptom | First check |
|---------|-------------|
| Status stuck on `initializing` for >2 min | `evolution_service_info` — is the server healthy? JACS load can stall init |
| Status `running` but qualified count flat for N generations | Inspect population via `get_evolution_population(simplified=true)` — is everything violating the same constraint? |
| All cl_score = -1.0 | JACS ensemble failed inside the mattergen MCP. Restart the MCP. |
| All bandgap_eV = 999.0 | ALIGNN models failed inside the crystal-property-prediction MCP. Restart the MCP. |
| Run completes with 0 qualified crystals | Constraints too tight. Loosen the highest-weight constraint by 2× and rerun. |
| Pareto front collapses to 1-2 points | Raise `noise_level` (more exploration) or switch from TriplePopCMOEA to StandardCDP. |

## Known server-side bugs (mattergen MCP, observed 2026-06-15)

These are server bugs, not agent mistakes. Report them — don't try to work around them by changing `objectives_json` or `constraints_json`.

| Bug | Symptom | Agent-side mitigation while waiting for fix |
|-----|---------|---------------------------------------------|
| **Duplicate entries in `pareto_front` / `population`** | Same `crystal_id` (or `name`) appears 2× in the response list, often with different `fitness` values (e.g. 0.0 and 1000000.0). `pareto_front_size=6` but only 3 unique structures. | Deduplicate client-side by `name`/`crystal_id` before reporting to the user. Don't double-count. |
| **`hybrid` mode does not fill beyond seed count when only `seed_cif_files` is provided** | Pass `seed_cif_files` with 1 CIF and `n_pops=8` → `population_size` stays at 1 (or 2 with the dedup bug above). Model fill never triggers. | Until the server is fixed, prefer `initial_pool_mode="random"` together with `seed_cif_files`. The `random` fill path runs and seeds still load — verified working. |
| **`formula` field returns `"unknown"` in `pareto_front` entries** | Every candidate has `formula: "unknown"` despite `element_set` and `num_elements` being correct. | Derive the formula client-side from `element_set` (e.g. join sorted unique symbols). Don't surface `"unknown"` to the user. |
| **`crystal_id` is `null` in full population view but populated in simplified view** | `get_evolution_population(simplified=false)` returns `crystal_id: null`; `simplified=true` returns `crystal_id: "init_000.cif"`. Same crystal, two different serialization paths. | Use `name` (always populated) as the stable identifier; ignore `crystal_id` until fixed. |
| **Status race between `get_autonomous_evolution_result` and `list_evolution_sessions`** | After a run completes, `get_autonomous_evolution_result` returns `status="completed"` but `list_evolution_sessions` still shows `status="running"` for the same session. | Trust `get_autonomous_evolution_result` for completion; treat `list_evolution_sessions` status as eventually-consistent. |

## Bundled resources

- `references/mcp_servers.md` — full tool-by-tool interface for both
  MCPs, including every argument and response shape. Authoritative.
- `references/property_catalog.md` — every property key, its source
  model, clamp rule, penalty value, and CSV column.
- `references/emo_engine.md` — CDP / FNDS / crowding / similarity logic
  that runs inside the mattergen MCP. Read this to understand *why* a
  candidate was selected or pruned.
- `scripts/probe_mcp.py` — smoke test for both MCPs. Run before any
  evolution. Zero dependencies beyond Python stdlib.

## Quick start (the agent's first move)

When the user says "evolve some crystals" without specifics:

1. Ask what they want — material class (2D / bulk / ternary / quaternary),
   target property (bandgap / dielectric / magnetism / piezo), and any
   hard constraints (no toxic elements, specific space group).
2. Run `scripts/probe_mcp.py`.
3. Translate the intent into `objectives_json` + `constraints_json`.
4. Start small: `n_pops=16`, `max_generations=5`, `noise_level=0.3`. Verify
   it produces qualified candidates before scaling up.
5. Submit via `run_autonomous_evolution`. Poll every 45s.
6. When done, summarize the Pareto front: top-3 candidates, the knee
   point, chemical trends, and recommended next run.

Never submit a run without confirming both MCPs are alive. Never submit
two simultaneous runs on the same GPU without checking
`evolution_service_info.active_sessions` first.
