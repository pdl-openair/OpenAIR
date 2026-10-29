# MCP tool reference — SAGA Magnet MVP

Authoritative signatures for every MCP tool this skill calls. If a tool
call shape changes, update this file first, then update SKILL.md.

All four core MCPs are HTTP JSON-RPC services on
`<MATERIALS_MCP_BASE_URL>/<name>/mcp` (set in `.env`). The skill orchestrator
(Claude) calls them via the Claude harness MCP tool namespace, e.g.
`mcp__diffcsp-generate__generate_structures`.

## diffcsp-generate

### `generate_structures` (synchronous)

| Param | Type | Default | Notes |
|-------|------|---------|-------|
| `formula` | string | required | e.g. `"SrTiO3"`, `"GaTe"`, `"LiCoO2"` |
| `model_name` | enum | `mp_csp` | `mp_csp` / `mpts_csp` / `carbon_csp` / `perov_csp` |
| `num_evals` | int | 1 | Independent samples for the same formula |
| `batch_size` | int | min(500, num_evals) | Usually leave default |
| `step_lr` | float | dataset default | Usually leave default |

Response:

```jsonc
{
  "model": "mp_csp",
  "dataset": "mp_20",
  "formula": "Fe2O3",
  "step_lr": 1e-5,
  "num_generated": 1,
  "num_failed": 0,
  "structures": [{
    "index": 0,
    "formula": "Fe2O3",
    "num_atoms": 10,
    "spacegroup": 167,
    "cif": "<CIF text>"
  }]
}
```

Failure mode: `value: null, error: "..."`. Common: formula out of training
distribution; reduce `num_evals` and retry, or switch to `mpts_csp`.

### `generate_structures_async` / `get_generation_result`

Async pair for long batches. Returns `{job_id}` immediately; poll until
`status == "done"`. Use only when `num_evals > 4` per formula.

## mattersim-relax

### `relax_structure` (synchronous)

| Param | Type | Default | Notes |
|-------|------|---------|-------|
| `cif_content` | string | one of cif_* | CIF text — preferred for agent use |
| `cif_path` | string | | Server-side path |
| `cif_base64` | string | | Base64 of CIF |
| `cif` | string | | Auto-detect |
| `max_steps` | int | 50 | Increase to 200 for stubborn structures |
| `device` | enum | auto | `cuda` / `cpu` |
| `checkpoint` | string | default 1M model | Path or name |

Response:

```jsonc
{
  "name": "...",
  "input_source": "content",
  "num_atoms": 10,
  "relaxed_cif": "<CIF text>",
  "total_energy_eV": -123.4,
  "energy_per_atom_eV": -12.34,
  "f_max_eV_A": 0.03,    // force convergence; <0.1 well-converged
  "s_max_GPa": 2.5       // stress max component
}
```

Drop the candidate if `f_max_eV_A > 1.0` — MatterSim did not converge.

### `relax_structure_async` / `get_relax_result`

Async pair. Use for batches > 5 structures.

## alignn_prediction

### `predict_alignn_properties`

| Param | Type | Default | Notes |
|-------|------|---------|-------|
| `cif_content` | string | one of cif_* | Preferred |
| `cif_path` | string | | Server-side path |
| `cif_base64` | string | | Base64 |
| `cif` | string | | Auto-detect |
| `models` | list[string] | 4 core | Subset of: `formation_energy, bandgap, dielectric, ehull, bulk_modulus, shear_modulus, exfoliation_energy, slme, supercon_tc, magmom` |
| `name` | string | optional | Tag for logs |

For this MVP, always pass `models=["magmom", "ehull"]`. Returns:

```jsonc
{
  "name": "...",
  "input_source": "content",
  "properties": {
    "magmom": {"value": 24.0, "note": "μB total"},
    "ehull":  {"value": 0.03}
  }
}
```

Failure semantics: a property with `value: null` AND an `error` field
means the model rejected the structure. Drop the candidate; do not retry
deterministically.

## mattergen-evaluate (population health — informational, not in reward)

Evaluates a batch of CIFs for structural novelty / uniqueness / validity
and (optionally, after relax) energy stability against an 845k-structure
reference set (alex-mp). This skill calls it once per iteration on the
**MatterSim-relaxed** batch with `relax=False` and the MatterSim energies
already in hand — no second relax.

### `evaluate_structures_async` / `get_evaluation_result`

Strongly preferred over the sync `evaluate_structures` — the sync call
blocks for minutes (弛豫 + reference-set matching) and will trip the MCP
client timeout. Submit async, poll at the next iteration's step 2a (or
at Phase 3 for the last iter).

| Param | Type | Default | Notes |
|-------|------|---------|-------|
| `structures` | list[dict] | required | One entry per CIF: `{"cif_content": "..."}` or `cif_path` / `cif_base64`. Adding a `"name"` field helps logs. |
| `relax` | bool | `true` | **This skill always passes `false`** — structures are already MatterSim-relaxed. |
| `energies` | list[float] | none | Required when `relax=false`. Total energy per structure in eV = `MatterSim.energy_per_atom_eV * num_atoms`. |
| `energy_correction_scheme` | enum | `MP2020` | `MP2020` (default; matches MatterSim) or `TRI2024`. |
| `structure_matcher` | enum | `disordered` | `disordered` (default) or `ordered`. |
| `potential_load_path` | string | bind-mount 1M.pth | Override only if you brought your own MatterSim checkpoint. |

Response from `evaluate_structures_async`:

```jsonc
{ "job_id": "<id>", "status": "pending", "hint": "poll get_evaluation_result" }
```

Response from `get_evaluation_result(job_id)`:

```jsonc
{
  "job_id": "...",
  "status": "done",                    // pending / running / done / error
  "result": {
    "scheme": "MP2020",
    "relax": false,
    "num_input": 3,
    "num_evaluated": 3,
    "n_failed_jobs": 0,
    "metrics": {
      "frac_novel_structures": 0.333,
      "frac_unique_structures": 0.667,
      "frac_novel_unique_structures": 0.333,
      "precision": 1.0, "recall": 0.0,
      "avg_structure_validity": 1.0,
      "avg_comp_validity": 1.0,
      "avg_energy_above_hull_per_atom": 0.041,   // relax=false uses supplied energies
      "frac_stable_structures": 1.0,
      "frac_novel_unique_stable_structures": 0.333
    },
    "parse_errors": []
  }
}
```

Important properties for this skill:

- **Aggregate only.** The tool returns one `metrics` dict per batch —
  no per-structure novelty/stability flags. That's why it cannot enter
  the per-candidate reward. See `reward_and_filters.md` §"Why
  mattergen-eval is not in reward".
- **Small-batch noise.** With `batch_size=3`, each candidate moves the
  fractions by 0.33. Phase 3 optionally re-submits the union of all
  survivors from the whole run for a less noisy aggregate.
- **Disagreement with ALIGNN `ehull` is expected.** Different reference
  sets and correction schemes. ALIGNN `ehull` stays the hard filter;
  `avg_energy_above_hull_per_atom` is reported for cross-check only.
- **Don't retry on `status:error`.** Sidecar errors are usually OOM or
  reference-set issues. Note in the report; loop outcome is unaffected.

Failure mode: a property with `value: null` AND an `error` field means
the model rejected the structure. Drop the candidate; do not retry
deterministically.

## mattergen-generate (optional, for CSP)

Not used in the default MVP loop. Provided for completeness — useful
when the user wants to add MatterGen structures as an alternative
init pool or for seeded CSP.

### `generate_crystals` (synchronous, ~2 min/run)

| Param | Type | Default | Notes |
|-------|------|---------|-------|
| `model_name` | enum | `mattergen_base` | `mattergen_base`/`chemical_system`/`space_group`/`dft_band_gap`/etc |
| `conditions` | dict | none | e.g. `{"dft_band_gap": 2.5}` — must match the model |
| `num_samples` | int | 2 | Total structures |
| `batch_size` / `num_batches` | int | auto | V100 16GB: keep `batch_size` ≤ 8 |
| `guidance_factor` | float | 0.0 | Conditioning strength |
| `target_compositions` | list[dict] | none | CSP mode |

### `generate_crystals_async` / `get_generation_result`

Strongly preferred — sync blocks ~2 min.

## material-platform (optional, for run archival)

The 30+ tools on this MCP are for the协同开发平台: dataset/model/code
CRUD, training pipelines, app deployment. Not used in the MVP loop.
If the user wants to upload the final top-K as a dataset or kick off
DFT, this is where those tools live — see the platform MCP instructions
block in the system prompt for the workflow.

## Common pitfalls

- **Async vs sync**: each pair shares arguments. Sync blocks the agent
  turn until done; async returns `{job_id}` immediately. Use async when
  a batch will clearly exceed the MCP client timeout (default ~60s).
- **CIF input forms are mutually exclusive**: never pass both
  `cif_content` and `cif_base64`. Pick one.
- **Property key casing**: alignn_prediction returns lowercase keys
  (`magmom`, `ehull`). constraint JSON in mattergen historically uses
  `_eV` suffixes. This skill does not write constraint JSON, so the
  mismatch does not bite — but be aware.
