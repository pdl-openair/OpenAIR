# Steering playbook (adaptive)

You drive the loop by reading `snapshot_latest.json` after each `step` and deciding
whether/how to `steer` before the next `step`. This is the skill's differentiating
value — but **over-steering harms diversity**. Doing nothing is often the right call.

## snapshot_latest.json fields you read

```
generation, max_generations, pop_size, n_valid, pareto_front_size,
objectives, constraints, top_front: [{name, formula, sg_number, is_valid,
                                      total_violation, <objective keys...>}]
```

**Key validation when steering:** every key passed to `steer --constraint` /
`--weight` / `--ban` / `--allow-only` is validated against the run's
`--fitness-backend` (stored in `config.json`). Unknown keys hard-error before
any change — if you see `key "X" not available in backend "Y"`, pick a key
from that backend's `BACKEND_KEYS` (see `property-keys.md`).

## Trigger → action

| You observe in the snapshot | Action | steer command |
|---|---|---|
| A banned/unwanted element keeps dominating (e.g. Pb recurring despite the goal) | Ban it hard | `steer --ban Pb` |
| An element you *want* is missing; population drifting to off-target chemistry | Restrict to a wanted set | `steer --allow-only Cu,O,Ti` (careful: too strict starves the pool) |
| Duplicates dominating, `pareto_front_size` shrinking, formulas repeating | Raise mutation strength to escape | `steer --noise-level 0.5` (was 0.3) |
| Front stagnates for ≥4 generations, all valid, no improvement | Tighten exploit / lower noise | `steer --noise-level 0.15` |
| Too many infeasible (`n_valid` low), constraint too strict | Soften the offending constraint | `steer --weight bandgap:5` (from 10) |
| A promising candidate near a threshold (bandgap 1.4 vs 1.5 goal) | Tighten that constraint to push the front | `steer --constraint bandgap:1.3::10` |
| Everything healthy: front growing, good diversity, mostly valid | **Do nothing** — just `step` again | (none) |

## Soft phase vocabulary (guidance, not a state machine)

- **Early** (first ~30% of generations): higher `noise-level` (0.4–0.5), loose constraints, favor diversity. Don't over-ban; let the model explore.
- **Mid**: as dominant-but-unwanted compositions appear, start banning/tightening.
- **Late** (last ~30%): lower `noise-level` (0.15–0.25), tighten constraints to exploit the best elites, aim to push the front rather than diversify.

## When NOT to steer

- `n_valid` is high AND `pareto_front_size` is growing AND top_front values are improving across the last 2–3 snapshots → leave it alone.
- Don't steer every generation — that's noise, not strategy. If unsure, `step` once more and re-read.

## Anti-patterns

- Banning elements reactively in generation 1 before seeing what the model produces (starves diversity early).
- Cranking `noise-level` to 0.8+ repeatedly — that's near-random generation, not mutation; you lose the parent's information.
- Steering on a single noisy alignn value (alignn has ~0.1–0.3 eV error on bandgap/ehull). Look at the *front's trend*, not one crystal.
