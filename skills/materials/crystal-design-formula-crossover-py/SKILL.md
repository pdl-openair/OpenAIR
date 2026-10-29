---
name: crystal-design-formula-crossover-py
description: Python-adapter (durable, single-call) version of crystal-design-formula-crossover-agent — runs the SAGA-Opt MVP formula search end-to-end in one subprocess call instead of Claude steering each iteration. Use when the user wants to "evolve / search / discover crystal formulas" in batch mode (background worker, no mid-run human intervention). Accepts a natural-language goal, runs Phase 0.5 LLM RunPlan + init + step × N + finalize, returns top-K + leaderboard.
---

# crystal-design-formula-crossover-py — durable batch evolutionary formula search

Sibling to `crystal-design-formula-crossover-agent` (claude_code). This version runs the **full** evolutionary loop in a single Python adapter call. Crossover LLM is `LLM_PROVIDER_BASE_URL` (OpenAI-compatible endpoint), not Claude.

## When to use which

| Aspect | This skill (-py) | Sibling (-agent) |
|---|---|---|
| Runtime | `python_adapter` (durable worker) | `claude_code` (multi-turn) |
| Mid-run steering | Optional: in-adapter LLM call between iterations (default off) | Native: Claude reads snapshot, decides |
| Crossover LLM | `LLM_PROVIDER_BASE_URL` | Claude (you) |
| Best for | Workbench background tasks, batch eval, headless runs | Interactive sessions where user wants to intervene |
| Reuses | `skills/materials/crystal-design-formula-crossover-agent/scripts/run.sh` (same engine) | Same engine |

## Pipeline (see `adapter/crossover_adapter.py`)

```
Phase 0   LLM RunPlan (goal → objectives, hard_filter, alignn_models,
            crossover_hints, init_elements, fallback_formulas, config)
          May be skipped only when task_instruction already supplies an
          explicit scientific RunPlan and a confirmed initial search scope.
Phase 1   init   : formula_driver.py init (writes runplan, snapshot_iter0)
Phase 2   step x N: adapter calls _llm_crossover → formula_driver.py step
            (optionally in-adapter LLM steer between gens)
Phase 3   finalize: formula_driver.py finalize --top-n ... [--evaluate]
Phase 4   assemble: read leaderboard.csv + summary.json → output dict
```

Failure semantics: never raise. If the Phase 0 RunPlan cannot be formed from the LLM or explicit structured input, the adapter returns `partial_failure` **before** `init` and does not invent a keyword/default scientific plan. Later MCP / crossover failures remain localized in `partial_failure`. The return dict always carries `state`, `run_dir`, `top_k_table`, `iterations`, `run_summary`.

## Inputs

- `goal` (required): NL goal, e.g. "find stable wide-bandgap oxide semiconductors without Pb".
- `task_instruction` (optional dict or JSON string):
  - `skip_runplan_llm: bool` — bypass the planner LLM **only** when the task instruction already contains the explicit scientific RunPlan; this flag never selects a default/keyword plan
  - `objectives`, `hard_filter`, `alignn_models`, `crossover_hints` — explicit RunPlan fields / overrides
  - `init_n`, `batch_size`, `max_generations` (alias `max_iters`), `top_k`
  - `init_elements`, `fallback_formulas`
  - `run_mattergen_eval: bool`
  - `seed_cifs: list[str]` — host paths to seed CIF files (mapped to fallback_formulas)
  - `enable_llm_steer: bool` — opt in to in-adapter LLM steering (default false)
  - `run_dir: str` — explicit run directory

## Output

See `skills/registry.yaml` `output_schema` for `crystal_design_formula_crossover_py`. Key fields:
- `state`: `succeeded` | `partial_failure`
- `run_dir`, `top_k_table` (CSV string), `iterations`, `run_summary`
- `pareto_cif_paths[]`
- `evaluate_report_path`: optional
- `partial_failure[]`: soft failures

## Hard rules

1. **Never raise.** Any exception inside the adapter is a bug — capture it into `partial_failure`. A missing/invalid RunPlan is a local fail-closed result: stop before `init`; do not continue with invented scientific semantics.
2. **No semantic defaults in code.** Objectives, hard filters, chemistry scope, crossover hints, and seed families come from the planner LLM or explicit structured input. Code may default only execution/orchestration knobs and may deterministically derive redundant fields such as `alignn_models`.
3. **Objectives fixed at init.** Mid-run `enable_llm_steer` may only ban an element, soften a constraint, or adjust hints — same policy as the claude_code sibling's `steer` subcommand.
4. **Reuse, don't duplicate.** This adapter calls the sibling skill's `scripts/run.sh`. If that script moves or breaks, this skill breaks — by design.
5. **Eval guardrails.** For benchmark runs, cap `batch_size <= 12` and `max_iters <= 5` to bound GPU time.

## Local invocation

```bash
/home/george/code/openair/.venv/bin/python3 -m \
  skills.materials.crystal-design-formula-crossover-py.adapter.crossover_adapter \
  "find stable lead-free wide-bandgap oxide semiconductors" \
  '{"batch_size":3,"max_iters":3}'
```

## References

- `../crystal-design-formula-crossover-agent/SKILL.md` — the agent-guided version
- `../crystal-design-formula-crossover-agent/scripts/formula_driver.py` — the deterministic engine
- `../crystal-design-formula-crossover-agent/references/property_catalog.md` — NL goal → ALIGNN mapping
