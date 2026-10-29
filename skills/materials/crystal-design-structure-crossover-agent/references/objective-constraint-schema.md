# Objectives & constraints schema

## Objectives (`--objectives`)

JSON object mapping a **property key** → `"minimize"` | `"maximize"`. Defines the
Pareto ranking. Fixed at `init` (see SKILL.md hard rule 1).

```json
{"ehull": "minimize", "bandgap": "maximize"}
```
- 2–3 objectives is the sweet spot. >4 makes the Pareto front crowded and selection weak.
- Keys must be in `BACKEND_KEYS[<fitness-backend>]` (see `property-keys.md`).
  `element_set` / `sg_number` cannot be objectives (they're constraint-only).
  `charge_neutral` / `comp_valid` are bool constraints, not objectives.

## Constraints (`--constraints`)

JSON object: key → rule dict. Three rule shapes:

**Continuous (min/max):**
```json
{"bandgap": {"min": 0.5, "max": 3.0, "weight": 10},
 "formation_energy": {"max": 0, "weight": 10},
 "ehull": {"max": 0.05, "weight": 10}}
```
- Missing `min`/`max` = unbounded on that side.
- `weight` scales the violation penalty. **`weight ≥ 10` acts as a hard constraint** (any violation is dominated out). `weight` 1–5 = soft preference.

**Element whitelist/blacklist:**
```json
{"element_set": {"banned": ["Pb", "Cd"], "weight": 10}}
{"element_set": {"allowed_only": ["Cu", "O"], "weight": 10}}
```
- `banned`: each forbidden element present adds `weight` to the violation.
- `allowed_only`: each off-list element adds `weight`.

**Discrete scalar (space group):**
```json
{"sg_number": {"allowed_only": [225, 221]}}
{"sg_number": {"banned": [1]}}
```

## Worked examples

**Stable lead-free wide-bandgap oxide for transparent electronics:**
```json
objectives:   {"bandgap": "maximize", "ehull": "minimize"}
constraints:  {"bandgap": {"min": 1.5, "weight": 10},
               "ehull": {"max": 0.1, "weight": 10},
               "element_set": {"allowed_only": ["O"], "banned": ["Pb"], "weight": 10}}
```
Wait — `allowed_only:["O"]` allows *only* O, which forbids the cation. Use it only when
fixing the anion set loosely; otherwise omit and rely on `banned`. Better:
```json
constraints:  {"bandgap": {"min": 1.5, "weight": 10},
               "ehull": {"max": 0.1, "weight": 10},
               "element_set": {"banned": ["Pb","Cd","Hg"], "weight": 10}}
```

**Hard magnetic material (alignn backend):**
```json
objectives:   {"bulk_modulus": "maximize", "magmom": "maximize"}
constraints:  {"magmom": {"min": 1.0, "weight": 5}, "ehull": {"max": 0.1, "weight": 10}}
```

**Energy-minimization under fixed composition (mattersim backend):**
```json
objectives:   {"energy_per_atom": "minimize"}
constraints:  {"element_set": {"allowed_only": ["Si","O"], "weight": 10},
               "sg_number":   {"allowed_only": [148, 227]}}
```
⚠️ `energy_per_atom` is composition-dependent — pairing it with `element_set` /
`sg_number` constraints is mandatory, otherwise the search collapses toward
small compositions. See `property-keys.md` footgun note.

## How `steer` edits constraints mid-run

`steer` flags (see `evolve_driver.py steer --help`):
- `--ban Pb,Cd` → adds to `element_set.banned` (weight ≥ 10).
- `--allow-only Cu,O` → sets `element_set.allowed_only`.
- `--clear-ban` → removes the `element_set` rule.
- `--constraint KEY:MIN:MAX:WEIGHT` (appendable) → e.g. `--constraint bandgap:1.0::10` (min 1.0, no max, weight 10). Empty MIN/MAX fields are skipped.
- `--weight KEY:W` (appendable) → adjust an existing constraint's weight (soften/tighten).
- `--noise-level 0.5` → changes mutation strength (written to config.json).

**Key validation:** every key referenced in `--constraint` / `--weight` / `--ban`
/ `--allow-only` is validated against the run's `--fitness-backend`. Unknown
keys hard-error before any constraint file change — preventing the silent
SAES-100.0-violation failure mode where a `mattersim`-backend run adds
`bandgap` as a constraint and selection quietly breaks.
