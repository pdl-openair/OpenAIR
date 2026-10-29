# Property Catalog

Every property the DEMO pipeline can produce. The crystal-property-prediction MCP returns all 9
in a single `predict_flux_properties` call; the mattergen MCP uses these
same keys inside `objectives_json` and `constraints_json`.

## The 9 crystal-property-prediction MCP properties

| cppred key | Alias (also accepted) | Source model | Clamp | Penalty | Unit | Physical meaning |
|----------|----------------------|--------------|-------|---------|------|------------------|
| `bandgap_ev` | `bandgap_eV`, `bandgap` | ALIGNN `jv_optb88vdw_bandgap_alignn` | `max(0, x)` | 999 | eV | Electronic bandgap (OptB88vdW functional) |
| `formation_energy_ev` | `formation_energy_eV`, `e_form` | ALIGNN `jv_formation_energy_peratom_alignn` | — | 999 | eV/atom | Formation energy from elements |
| `hull_ev` | `ehull_eV`, `ehull`, `hull` | ALIGNN `jv_ehull_alignn` | `max(0, x)` | 999 | eV/atom | Energy above convex hull — thermodynamic stability |
| `exfoliation_energy_mev` | `exfoliation_energy_meV`, `exf` | CHGNet (2D-with-vacuum vs bulk-with-vdW-gap ΔE) | — | 9999 | meV/atom | 2D exfoliation energy — ease of peeling layers |
| `f_max_ev_a` | `f_max_eV_A`, `f_max` | MatterSim relaxation + CHGNet forces | — | 999 | eV/Å | Max atomic force L2 norm post-relax — convergence quality |
| `cl_score` | `clscore`, `cl` | JACS 100-bag CGCNN ensemble | — | -1 | 0..1 | Crystal-likeness probability — synthesizability proxy |
| `num_elements` | `n_elements` | pymatgen composition | — | — | int | Independent element count |
| `element_set` | `elements` | pymatgen composition | — | `[]` | string[] | Element symbols, e.g. `["Cr","Si","Te"]` |
| `sg_number` | `spacegroup_number` | pymatgen symmetry (symprec=0.1) | — | -1 | 1..230 | International space group number |

Plus two top-level response keys (not properties):
- `name` — structure label, used by JACS and logs
- `path` — server-side path if `cif_path` was the input form
- `input_source` — `"path"` / `"content"` / `"base64"`

## Constraint shapes

Constraint JSON keys MUST match the canonical names above (or the
aliases). The mattergen MCP normalizes them internally.

```python
# Numeric range constraint
"bandgap_eV": {"min": 0.2, "max": 3.5, "weight": 5.0}

# One-sided
"formation_energy_eV": {"max": 0, "weight": 10.0}        # hard

# Exact value (min == max)
"num_elements": {"min": 3, "max": 3, "weight": 10.0}      # ternary

# Set constraint — exactly one of banned / allowed_only
"element_set":  {"banned": ["Hg","Tl","Pb","Cd"], "weight": 5.0}
"sg_number":    {"banned": [1], "weight": 1.0}            # never P1
```

`weight` rules:

| Weight | Effect |
|--------|--------|
| 10 | Effectively hard — any violation sinks the candidate below all feasible ones |
| 5 | Soft — typical "preferred but not required" |
| 1 | Tie-breaker — only matters between candidates with equal major violations |

Penalty values (`999`, `9999`, `-1`) are sized so that any failed property
reads as maximally infeasible — guaranteeing pruning under CDP.

## Objective shapes

```python
my_objectives = {
    "ehull_eV":        "minimize",   # stability
    "dielectric_epsx": "maximize",   # dielectric response
}
```

Exactly two keys. Pareto plot is 2D. Note that `dielectric_epsx` is NOT
in the standard crystal-property-prediction MCP 9 — it requires ALIGNN `jv_epsx_alignn`. The
constraint/objective schema still accepts it because the underlying
`extract_all_properties` supports it; the mattergen MCP internally calls
a slightly extended property pipeline when needed.

## Common objective pairings

| Goal | objective_x (min) | objective_y (max) | Why |
|------|-------|-------|-----|
| Stable + functional semiconductor | `ehull_eV` | `bandgap_eV` | Hull ≈ 0 + useful bandgap |
| Stable + high-κ dielectric | `ehull_eV` | `dielectric_epsx` | Stability vs polarizability |
| Synthesizable + functional | `ehull_eV` | `cl_score` | Both > 0.5 is the sweet spot |
| Magnetic + stable | `ehull_eV` | `total_magnetization` | For spintronic materials |
| Easy exfoliation + stable | `ehull_eV` | `-exfoliation_energy_meV` (sign trick) | For 2D material isolation |

Note: minimize-objective vs maximize-objective is set per-key. To treat
"low exfoliation = good" as a maximize, either negate in your head or
pass `{"exfoliation_energy_meV": "minimize"}` instead.

## Property not in the standard 9

These properties are supported by the underlying
`characterize_functions.py` but not in the default crystal-property-prediction MCP response. To use
them as objectives/constraints, the crystal-property-prediction MCP must be extended to return
them:

| Property | Source | Notes |
|----------|--------|-------|
| `dielectric_epsx` | ALIGNN `jv_epsx_alignn` | x-axis dielectric; clamp `>= 1.0` |
| `s_max_GPa` | CHGNet stress | Max stress × 160.21766 |
| `total_magnetization` | CHGNet magmoms | Σ \|m_i\|, μB |
| `has_magnetism` | derived | `total_mag > 0.5` |
| `has_piezo_potential` | pymatgen symmetry | Non-centrosymmetric |
| `in_plane_anisotropy_ratio` | lattice abc | In-plane ratio |
| `mattersim_energy_eV` | MatterSim relax | Per-atom post-relax energy |

If you need one of these, talk to the user about extending the crystal-property-prediction MCP
or using a custom run.

## Diagnostic flags

When debugging a stalled run, dump one candidate's full property dict
via `get_evolution_population(simplified=false)` and check for these
tells:

| Pattern | Diagnosis |
|---------|-----------|
| Many `*_ev = 999` / `hull_ev = 999` | ALIGNN failure inside crystal-property-prediction MCP; restart |
| All `cl_score = -1` | JACS not loaded inside mattergen MCP; restart |
| `f_max_ev_a = 999` everywhere | MatterSim relax diverged; structures are isolated atoms |
| `exfoliation_energy_mev = 9999` everywhere | Not 2D candidates, or vacuum detection broken |
| `sg_number = -1` | Structure too distorted for symmetry detection |
| All properties fine, but candidate still marked invalid | Constraint key typo — check against this catalog |
