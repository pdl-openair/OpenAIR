# Objectives & Constraints Schema

## objectives (locked at init)

Objectives are set once at `init` via `--objectives` and persisted to `runplan.json`. They cannot change mid-run — change objectives → start a new run.

JSON shape:

```json
{
  "bandgap": "max",
  "hhi": "min"
}
```

Inline shorthand (key = objective name, value = direction). Direction is `"max"` or `"min"`. The driver expands this internally to:

```json
[{"name": "bandgap", "direction": "max", "weight": 1.0, "alignn_deps": ["bandgap"]}, ...]
```

## hard_filter (locked at init)

```json
[{"name": "ehull", "op": "<", "value": 0.1}]
```

Valid ops: `<`, `<=`, `>`, `>=`. Value is numeric. Always include `ehull` unless the user explicitly opts out of stability filtering.

## constraints.json (mutable via steer)

Three shapes:

**Element set:**

```json
{
  "element_set": {
    "banned": ["Pb", "Cd"],
    "allowed_only": ["Cu", "O", "Ti"],
    "weight": 10.0
  }
}
```

`weight >= 10.0` ≡ hard constraint. Lower weight = soft preference.

**Property range:**

```json
{
  "bandgap": {"min": 1.0, "max": 3.0, "weight": 10.0}
}
```

**Boolean:** (not currently used in formula-crossover; reserved)

## crossover_hints.json (mutable via steer)

```json
{
  "swap_groups": [["Fe", "Co", "Ni"], ["Te", "Se", "S"]],
  "prototypes": ["Heusler", "ThMn12", "YCo5"],
  "avoid": ["noble gases", "actinides > U", "monatomic formulas"]
}
```

`swap_groups` is a list of element lists (substitution groups). `prototypes` is a flat list of structure-type names. `avoid` is a flat list of free-text avoidance rules.
