---
name: crystal-design-formula-crossover-agent
description: "Agent-guided multi-objective evolutionary search over chemical formulas. Use when the user wants to discover / evolve / search for new crystal formulas toward materials goals (找新晶体 / 进化材料 / SAGA-Opt / DiffCSP+ALIGNN / 公式交叉搜索). Claude is the crossover LLM AND the strategist: read snapshot → propose batch_size formulas → call driver step → maybe steer → loop."
---

# crystal-design-formula-crossover-agent — agent-guided evolutionary formula search

Sibling to `crystal-design-formula-crossover-py` (durable). This version runs Claude in the loop: you propose crossover formulas each iteration, decide whether to steer, and report to the user.

## When to use which

| Aspect | This skill (-agent) | Sibling (-py) |
|---|---|---|
| Runtime | `claude_code` (multi-turn) | `python_adapter` (durable worker) |
| Crossover LLM | **Claude (you)** | LLM_PROVIDER_BASE_URL (OpenAI-compat) |
| Hints mutable | Yes, via `steer` between iterations | No, locked at RunPlan |
| Mid-run user intervention | Native | None |
| Best for | Interactive exploration, "let me adjust" | Batch / headless / workbench worker |

## What runs where

- **Deterministic engine (skill-owned):** `scripts/formula_driver.py` (init/step/steer/finalize/status) + `scripts/mcp_client.py` (HTTP wrappers) + `scripts/{probe_mcps,initial_formulas,hhi,mag_density}.py`.
- **Runs on the host** via `scripts/run.sh` (auto-picks project venv with pymatgen, falls back to mattergen container).
- **Compute primitives (deployed MCPs):** diffcsp-generate, mattersim-relax, alignn, mattergen-evaluate. Endpoints resolved from `MATERIALS_MCP_BASE_URL` (`.env`) or per-service `MCP_<NAME>_URL` overrides.

## The driver invocation

```bash
bash <SKILL>/scripts/run.sh <subcommand> --run-dir <RUN_DIR> ...
```

`<RUN_DIR>` (e.g. `outputs/evo_gan`) holds `runplan.json` (locked), `constraints.json` + `crossover_hints.json` (mutable), `population.json`, per-iter snapshots, and final `leaderboard.csv` + `pareto/`.

## The flow

**Step 0 — Pre-flight.** Run `python scripts/probe_mcps.py`. All 5 checks must PASS. Confirm `MP_API_KEY` is in env or `.env`.

**Step 1 — Phase 0.5 RunPlan.** Read user goal → consult `references/property_catalog.md` → draft RunPlan. **Show user the plan** (objectives, hard_filter, crossover_hints, init_elements, fallback_formulas, time estimate). Get explicit yes before continuing.

**Step 2 — init.** Build run_dir + initial pool + baseline snapshot.
```bash
bash <SKILL>/scripts/run.sh init --run-dir <RUN_DIR> \
  --goal "<verbatim goal>" \
  --objectives '{"bandgap":"max","hhi":"min"}' \
  --hard-filter '[{"name":"ehull","op":"<","value":0.1}]' \
  --alignn-models '["bandgap","ehull","hhi"]' \
  --crossover-hints '{"swap_groups":[["Ga","In","Al"],["N","P","As"]],"prototypes":["zincblende","wurtzite"],"avoid":["noble gases","actinides > U"]}' \
  --init-elements 'Ga,In,Al,N,P,As' \
  --fallback-formulas 'GaN,GaAs,InP' \
  --init-n 10 --batch-size 3 --max-iters 3 --top-k 5 \
  --run-mattergen-eval
```
Read `snapshot_iter0.json`. Confirm baseline looks reasonable.

**Step 3 — evolve loop (you steer each iteration).** Repeat until `max_iters`:
1. Read `snapshot_latest.json`.
2. **You are the crossover LLM.** Apply `crossover_hints` + current pool → propose `batch_size` new formula strings. Never repeat an already-scored formula unless user asks.
3. `bash <SKILL>/scripts/run.sh step --run-dir <RUN_DIR> --new-formulas "f1,f2,f3"`
4. Read new `snapshot_latest.json`. Per `references/steering-playbook.md`, decide whether to `steer`:
   - `steer --ban Pb` — ban element
   - `steer --constraint ehull:0.2::10` — adjust hard filter
   - `steer --crossover-hint prototypes:perovskite` — focus hints
   - `steer --batch-size 5` — change loop dynamics
   - **doing nothing is valid** if the snapshot is healthy
5. If you steered, next `step` reads the updated constraints/hints.

**Step 4 — finalize.**
```bash
bash <SKILL>/scripts/run.sh finalize --run-dir <RUN_DIR> --top-n 10 --evaluate
```
Exports `leaderboard.csv` + `pareto/*.cif` + `summary.json`.

**Step 5 — report.** Top-K table + Pareto knee + chemical trends + recommended next run + RunPlan recap.

## Hard rules

1. **Objectives fixed at init.** Changing objectives → new run seeded from `pareto/`.
2. **You are the crossover LLM.** Apply real domain knowledge from `crossover_hints`; don't emit random formulas.
3. **Never repeat a scored formula** in the same run unless user asks.
4. **Each MCP serializes GPU.** Expect minutes per iteration. Don't spam parallel `step` calls.
5. **Driver never calls LLM.** If `step --new-formulas` is empty, driver exits 4; re-propose.

## Reference files

- `references/property_catalog.md` — NL goal → ALIGNN model / objectives / hints mapping.
- `references/objective-constraint-schema.md` — JSON shapes for objectives/constraints/hints.
- `references/reward_and_filters.md` — filter+reward math (authoritative).
- `references/steering-playbook.md` — trigger→action table for in-loop decisions.
- `references/runtime-topology.md` — MCP endpoints + healthcheck curls.
- `references/mcp_tools.md` — full MCP signatures.
