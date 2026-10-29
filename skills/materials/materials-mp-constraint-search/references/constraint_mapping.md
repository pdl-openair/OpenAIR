# Constraint → Field Mapping (Deep Reference)

This document explains exactly how each DEMO constraint is filtered, which API
field is used, and known pitfalls.

## Constraint Coverage Matrix

| DEMO constraint | Threshold | MP server filter | MP client filter | crystal-property-prediction MCP |
|-----------------|-----------|:----------------:|:----------------:|:--------:|
| `bandgap_eV` ∈ [0.2, 3.5] | range | ✅ `band_gap=(0.2, 3.5)` | — | `bandgap_ev` |
| `formation_energy_eV` ≤ 0 | upper bound | ✅ `formation_energy_per_atom=(None, 0)` | — | `formation_energy_ev` |
| `ehull_eV` ≤ 1.0 | upper bound | ✅ `energy_above_hull=(None, 1.0)` | — | `hull_ev` |
| `num_elements` == 3 | exact | ✅ `num_elements=(3,3)` | — | `num_elements` |
| `element_set` banned | exclude | ✅ `exclude_elements=[...]` | redundancy check | `element_set` |
| `sg_number` ≠ 1 | blacklist | ❌ | ✅ client-side | `sg_number` |
| `cl_score` ≥ 0.50 | lower bound | ❌ | ❌ | ✅ `cl_score` |
| `f_max_eV_A` ≤ 2.0 | upper bound | ❌ | ❌ | ✅ `f_max_ev_a` |
| `exfoliation_energy_meV` ∈ [-5, 350] | range | ❌ | ❌ | ✅ `exfoliation_energy_mev` |

## mp_api.search() Field Names (modern API, ≥0.40)

```python
mpr.materials.summary.search(
    # Numeric ranges as tuples
    band_gap=(0.2, 3.5),                       # eV
    formation_energy_per_atom=(None, 0.0),     # eV/atom (None = no lower bound)
    energy_above_hull=(None, 1.0),             # eV/atom
    num_elements=(3, 3),                       # inclusive range

    # Element membership / exclusion
    elements=[["Mo", "S"]],                    # must contain (list-of-lists)
    exclude_elements=["Hg", "Tl", "Na", "K", "F"],  # must NOT contain

    # Chemical system (alternative to elements)
    chemsys="Mo-S",                            # exact chemsys

    # Stable-only shortcut
    is_stable=True,                            # equivalent to energy_above_hull=0

    # Pagination / chunking
    num_chunks=10,                             # 100 * num_chunks results max
    chunk_size=100,

    # Field projection (efficiency)
    fields=["material_id", "formula_pretty", "band_gap", "formation_energy_per_atom",
            "energy_above_hull", "nelements", "elements", "symmetry", "structure"],
)
```

## Backward Compatibility

Old mp_api versions (< 0.40) used different kwarg names. The script handles this:

| Modern | Legacy | Notes |
|--------|--------|-------|
| `num_elements=(3,3)` | `nelements=(3,3)` | Old API used `nelements` |
| `band_gap=(...)` | `band_gap=(...)` | Same |
| `formation_energy_per_atom=(...)` | `formation_energy_per_atom=(...)` | Same |
| `energy_above_hull=(...)` | `energy_above_hull=(...)` | Same |
| `exclude_elements=[...]` | `exclude_elements=[...]` | Same |

On `TypeError: unexpected keyword argument`, the script falls back to legacy
`nelements`. If that also fails, see the migration guide:
<https://docs.materialsproject.org/api/api_v2.html>

## Why Some Constraints Can't Be Filtered Server-Side

### cl_score (JACS synthesizability)

JACS CL-score is computed by **100 bagged CGCNN models** trained on experimental
ICSD entries (see `DEMO/PUCGCNN/jacs_server.py`). MP does not store this metric
— it's a ML ensemble prediction, not a DFT property. Must be computed on-demand
via the JACS XML-RPC microservice (or via crystal-property-prediction MCP, which wraps it).

### f_max_eV_A (max atomic force after relaxation)

This is the **residual force on a MatterSim-relaxed structure**, not a
thermodynamic quantity. It measures how well MatterSim agrees the structure is
at a local minimum. Different force fields give different `f_max` values; MP's
DFT relaxation would give a different number entirely. The DEMO evolution
pipeline uses this as a **proxy for structure plausibility**, not as a
fundamental property.

### exfoliation_energy_meV

CHGNet computes this as `E(2D + vacuum) - E(bulk)` per atom in meV. MP has a
separate **2D materials database** (m2d) with experimental/DFT exfoliation
energies for some 2D compounds, but the coverage is sparse and the methodology
differs. For 3D candidates there is no MP value at all.

## Server-Side Filter Gotchas

### `exclude_elements` is not always 100% reliable

In rare cases, MP may return a material whose `elements` list intersects
`exclude_elements` (usually due to a doping entry or a stale cache). The script
does a **redundancy client-side check** to catch this. Don't disable it.

### Range bounds are inclusive

`band_gap=(0.2, 3.5)` matches `0.2 ≤ x ≤ 3.5`. If you want strict bounds,
post-filter client-side.

### `None` means "no bound"

`formation_energy_per_atom=(None, 0.0)` means "any lower value, max 0". Using
`(-9999, 0.0)` instead would miss extremely stable compounds if MP ever stored
extremely negative values.

### Pagination: `num_chunks` is in units of 100

`num_chunks=10` → up to 1000 results. The script defaults to
`num_chunks=10` internally and caps via `--max-results` (default 200).

## Cross-Validation: MP vs ALIGNN Bandgap

| Material | MP `band_gap` (DFT) | ALIGNN `bandgap_ev` | Source of discrepancy |
|----------|---------------------|---------------------|----------------------|
| MoS2 (mp-1434) | 1.80 eV (HSE) | ~1.5 eV (OPTB88vdw-trained) | Different XC functional |
| CrSiTe3 (mp-4747) | 0.36 eV | 0.60 eV | ALIGNN on OPTB88vdw dataset |
| AlInSe3 (mp-...) | varies | 1.22 eV | ALIGNN trained on different relaxations |

When you enable `--verify-cppred`, the JSON output stores both numbers
(`bandgap_eV` from MP, `bandgap_ev_cppred` from crystal-property-prediction MCP / ALIGNN) so you can spot
discrepancies. A difference of >0.5 eV usually indicates ALIGNN was trained
on a different functional and should not be considered an error.

## SG Number Filter Rationale

`sg_number ≠ 1` (P1, no symmetry) excludes artificially-disordered structures
that are unlikely to be synthesizable. This is a soft filter — some real
materials have sg=1 due to magnetic ordering. The default ban list is `[1]`
but you can customize via `--banned-sg`.

## Performance Characteristics

| Mode | Per-candidate cost | 200 candidates |
|------|-------------------|----------------|
| Default (MP-only) | ~1ms | ~5s total |
| `--verify-cppred` | 10–30s | 30–100 min |

Crystal-property-prediction MCP cost is dominated by:
1. MatterSim relaxation (~3–8s)
2. ALIGNN bandgap + formation + ehull (~2–5s each)
3. JACS CL-score via 100-model ensemble (~2–5s)
4. CHGNet exfoliation energy (2× structure prediction, ~5s)

## When to Enable `--verify-cppred`

Enable when:
- You intend to use the candidates as **evolution seeds** and want only
  seeds that pass the **full** constraint set including cl_score
- The MP-only result count is <50 (verifying is affordable)
- You need cl_score for downstream filtering / ranking

Skip when:
- You just want a **broad survey** of what's in MP
- The MP-only result count is >200 (cppred verification would take hours)
- Crystal-property-prediction MCP is down or unreachable

## Useful One-Liners

```bash
# Quick count of how many MP candidates exist for a relaxed bandgap
python3 .claude/skills/mp-constraint-search/scripts/search_mp_candidates.py \
    --bandgap 1.0 1.5 --output-dir /tmp/survey --max-results 500

# Generate 8 high-CL-score seeds for evolution
python3 .claude/skills/mp-constraint-search/scripts/search_mp_candidates.py \
    --verify-cppred --max-results 50 --output-dir ./seeds_v1
# Then manually filter seeds_v1/candidates.json by cl_score > 0.50

# Restrict to specific chemical systems after MP query
python3 -c "
import json
allowed_chemsys = {'Mo-S', 'W-Se', 'V-O'}
with open('mp_candidates_X/candidates.json') as f:
    data = json.load(f)
for row in data['candidates']:
    if '-'.join(sorted(row['element_set'])) in allowed_chemsys:
        print(row['material_id'], row['formula_pretty'])
"
```
