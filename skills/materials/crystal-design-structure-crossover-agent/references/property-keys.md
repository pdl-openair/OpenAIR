# Property keys — NL term ↔ objective/constraint key, per fitness backend

These keys are what `evolve_driver.py` writes into individual dicts and what
`objectives`/`constraints` JSON must reference. The available keys depend on
which `--fitness-backend` was chosen at `init`. **Mixing keys across backends in
the same run is not supported** — see SKILL.md hard rule 5.

`needed_keys()` (in `evolve_driver.py`) filters the user's `objectives` ∪
`constraints` against `BACKEND_KEYS[backend]` and asks the backend to compute
only those. `validate_objective_constraint_keys()` runs at `init` and `steer`
and hard-errors on unknown keys (avoids SAES silently treating missing values
as 100.0 constraint violation).

## Backend-independent keys — always available

These come from `structure_meta()` on the relaxed structure (pymatgen), not
from any backend MCP. They work as objectives or constraints regardless of
backend.

| key | type | use |
|---|---|---|
| `element_set` | list of symbols | `{"element_set":{"banned":["Pb","Cd"]}}` (lead-free) or `{"element_set":{"allowed_only":["Cu","O"]}}`. `weight ≥ 10` = hard ban. |
| `sg_number` | int 1–230 | `{"sg_number":{"allowed_only":[225]}}` or `{"sg_number":{"banned":[1]}}` (ban P1 disorder) |
| `charge_neutral` | bool | `{"charge_neutral":{"expected":true,"weight":10}}` — smact validity check (matches mattergen-evaluate's `comp_validity`) |
| `comp_valid` | bool | alias of `charge_neutral` |
| `num_elements` | int | `{"num_elements":{"min":2,"weight":10}}` — hard-ban single-element compositions (the ehull-driven composition-collapse footgun). Continuous scalar; `weight ≥ 10` ≡ hard. |
| `num_atoms` | int | `{"num_atoms":{"min":4,"weight":10}}` — hard-ban tiny atomic clusters (e.g. diatomic trivially-stable structures). Use when ehull-objective search drifts to trivially small unit cells. |

## `alignn` backend keys (default, fast/rough)

Per-individual, via alignn-predict MCP after mattersim-relax. Seconds per
structure. Good for multi-property objectives; has ~0.1–0.3 eV error on
bandgap/ehull (per `steering-playbook.md` anti-patterns — steer on trends, not
single values).

| alignn key | unit | NL phrase → use as |
|---|---|---|
| `formation_energy` | eV/atom | "low formation energy" → constraint `{"formation_energy":{"max":0,"weight":10}}` |
| `bandgap` | eV | "wide-bandgap" → `{"bandgap":"maximize"}` or `{"bandgap":{"min":1.5,"weight":10}}`; "semiconductor" → `{"bandgap":{"min":0.5,"max":3.0}}`; "metallic" → `{"bandgap":{"max":0.01}}` |
| `dielectric` | εx | "high-k" → `{"dielectric":"maximize"}` |
| `ehull` | eV/atom | "stable" → `{"ehull":{"max":0.05,"weight":10}}`; also a common objective `{"ehull":"minimize"}` |
| `bulk_modulus` | GPa | "hard material" → `{"bulk_modulus":"maximize"}` |
| `shear_modulus` | GPa | "stiff" → `{"shear_modulus":"maximize"}` |
| `magmom` | μB | "magnetic" → `{"magmom":{"min":0.1,"weight":5}}` |
| `exfoliation_energy` | meV/atom | "2D / exfoliable" → context-dependent (minimize for easy exfoliation) |
| `slme` | % | "photovoltaic / absorber" → `{"slme":"maximize"}` |
| `supercon_tc` | K | "superconductor" → `{"supercon_tc":"maximize"}` |

## `mattersim` backend keys (per-individual, accurate energy only)

Per-individual, via mattersim-relax MCP only (no second MCP call after relax —
fastest backend). Use when the goal reduces to energy/stability and you don't
need bandgap/dielectric/etc.

| key | type | use |
|---|---|---|
| `energy_per_atom` | eV/atom | stability proxy |
| `total_energy` | eV | absolute energy |
| `f_max` | eV/Å | residual max force after relax — useful as a "well-relaxed" constraint |
| `s_max` | GPa | residual max stress after relax |

### ⚠️ `energy_per_atom`-as-objective footgun

`energy_per_atom` is **composition-dependent** — a structure with fewer or
smaller atoms trivially scores lower without being "more stable". As a pure
objective (`{"energy_per_atom":"minimize"}`) the search will collapse toward
small/single-atom compositions. **Always pair it with composition constraints:**

```json
{"energy_per_atom": "minimize"}
// + constraints:
{"element_set": {"allowed_only": ["Si","O"], "weight": 10},
 "sg_number":   {"allowed_only": [148, 227]}}
```

Or use it as a **constraint** rather than objective (e.g. cut structures above
some per-atom energy threshold) when you have a different primary signal.

## Unavailable

- `cl_score` (synthesizability CL-score) — **requires JACS, which is dropped.**
  If a goal mentions "synthesizable / CL-score", reject and ask to rephrase
  around `ehull`/`formation_energy`.
- ~~`mattergen_eval` backend keys — not yet wired~~ **wired since 2026-09**:
  `evaluate_structures` returns a `per_structure` list (per-structure
  `energy_above_hull_per_atom`/`novel`/`stable`/...), and the backend reads it
  via chunked batch calls (old servers auto-fall back to batch-of-1, same
  values). See `scripts/evaluators.py`.

## Notes

- alignn predicts on the **relaxed** structure (driver relaxes via
  mattersim-relax first); there may be small drift vs engine-internal values —
  fine for steering.
- If a backend returns `null` (failure) for an individual, SAES treats it as
  the extreme (worst) value for that objective — it won't crash the run.
- Adding a new backend: implement `<name>_characterize_batch` in
  `scripts/evaluators.py`, register in `FITNESS_BACKENDS`, declare its keys in
  `BACKEND_KEYS` + `DEFAULT_KEYS`. Driver, SAES, and all reference docs key off
  these registries.
