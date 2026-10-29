---
name: crystal-design-structure-crossover-py
description: Python-adapter (durable, single-call) version of crystal-design-structure-crossover-agent — runs the same DEMO/EGD evolutionary crystal search end-to-end in one subprocess call instead of Claude steering each generation. Use when the user wants to "evolve / search / discover crystals" in batch mode (background worker, no mid-run human intervention), or when invoking through the workbench durable worker / python_adapter runtime. Accepts a natural-language goal, runs Phase 0 LLM runplan + init + step x N + finalize, returns Pareto front + leaderboard. For interactive per-generation steering, use the sibling claude_code skill crystal-design-structure-crossover-agent instead.
---

# crystal-design-structure-crossover-py — durable batch evolutionary crystal search

Sibling to `crystal-design-structure-crossover-agent` (claude_code). This version runs the **full** evolutionary loop in a single Python adapter call, without Claude driving each generation.

## When to use which

| Aspect | This skill (`-py`) | Sibling (claude_code) |
|---|---|---|
| Runtime | `python_adapter` (durable worker) | `claude_code` (Claude Agent SDK multi-turn) |
| Mid-run steering | Optional: in-adapter LLM call between generations (`enable_llm_steer=true`) | Native: Claude reads each snapshot and decides |
| Latency | One call, minutes | Many turns, similar wall-clock but more tokens |
| Best for | Workbench background tasks, batch eval, headless runs | Interactive sessions where user wants to intervene |
| Reuses | `skills/materials/crystal-design-structure-crossover-agent/scripts/run.sh` (same engine) | Same engine, Claude orchestrates |

## Pipeline (see `adapter/structure_crossover_adapter.py`)

```
Phase 0  LLM RunPlan (goal -> objectives, constraints, pop/gens/noise/crossover/seeds)
         Skipped when task_instruction.skip_runplan_llm=true.
Phase 1  init   : evolve_driver init --objectives … --constraints … --seed-cifs …
Phase 2  step x N: evolve_driver step (optionally in-adapter LLM steer between gens)
Phase 3  finalize: evolve_driver finalize --top-n … [--evaluate]
Phase 4  assemble: read leaderboard.csv + pareto/*.cif + snapshots -> output dict
```

Failure semantics: never raise. MCP timeouts / parse errors / LLM 5xx are captured into `partial_failure`. The return dict always carries `state`, `run_dir`, `objectives`, `top_k_table`, `iterations`, `run_summary`.

## Inputs

- `goal` (required): NL goal, e.g. "find stable narrow-gap layered semiconductors without Pb".
- `candidates` (required): each candidate supplies `material_id`, `formula_pretty`, and CIF content in `cif_text`. `cif_text` is never a Windows or POSIX host path.
- `task_instruction` (optional dict or JSON string):
  - `skip_runplan_llm: bool` — skip Phase 0, use provided config
  - `objectives`, `constraints` — overrides
  - `pop_size` (≤12 for eval), `max_generations` (≤5 for eval), `noise_level`, `crossover_rate`
  - `top_n`, `run_evaluate: bool`, `generate_n: int`
  - `enable_llm_steer: bool` — opt in to in-adapter LLM steering (default false; conservative)
  - `run_dir: str` — explicit run directory (default: `skills/materials/evaluation/high_mobility/runs/<eval_id>/`)
  - `eval_id: str` — slug used to name the run directory

## Output

See `skills/registry.yaml` `output_schema` for `crystal_design_structure_crossover_py`. Key fields:
- `state`: `succeeded` | `partial_failure`
- `run_dir`, `objectives`, `constraints`
- `top_k_table`: CSV string from `leaderboard.csv`
- `iterations`: per-generation snapshot digest
- `pareto_cif_paths[]`: exported CIF paths under `<run_dir>/pareto/`
- `evaluate_report_path`: optional novelty/stability JSON
- `partial_failure[]`: soft failures

## Hard rules

1. **Never raise.** Any exception inside the adapter is a bug — capture into `partial_failure` and continue.
2. **Objectives fixed at init.** Mid-run `enable_llm_steer` may only ban an element, tighten a constraint, or adjust a weight — same policy as the claude_code sibling's `steer` subcommand.
3. **Reuse, don't duplicate.** This adapter calls the sibling skill's `scripts/run.sh`. If that script moves or breaks, this skill breaks — by design.
4. **Eval guardrails.** For benchmark runs, cap `pop_size ≤ 12` and `max_generations ≤ 5` to bound GPU time.

## Local invocation

```bash
python skills/materials/crystal-design-structure-crossover-py/adapter/structure_crossover_adapter.py input.json

# input.json
{
  "goal": "find stable narrow-gap layered semiconductors without Pb",
  "candidates": [{
    "material_id": "demo-si",
    "formula_pretty": "Si",
    "cif_text": "data_demo_si\\n_cell_length_a 5.43\\n"
  }]
}
```

## References

- `../crystal-design-structure-crossover-agent/SKILL.md` — the agent-guided version
- `../crystal-design-structure-crossover-agent/scripts/evolve_driver.py` — the deterministic engine
- `../crystal-design-structure-crossover-agent/references/property-keys.md` — valid objective keys
- `../crystal-design-structure-crossover-agent/references/objective-constraint-schema.md` — JSON shapes
