# mattergen + crystal-property-prediction MCP — Tool Interface Contract

The single source of truth for every tool the agent can call on the two
remote HTTP MCP servers. If the real server implementation diverges from
what is documented here, edit this file — do not paper over mismatches
in the skill body.

## Endpoints

| Server | URL | Role |
|--------|-----|------|
| mattergen | `http://mcp.example.internal:22222/mcp/mattergen/mcp` | Entire evolution loop — submit, monitor, intervene, fetch results |
| cppred (crystal-property-prediction MCP) | `http://mcp.example.internal:22222/mcp/flux/mcp` | CIF → 9 scalar properties in one call |

Both use FastMCP streamable-http. Wire into the project's `.mcp.json`:

```json
{
  "mcpServers": {
    "mattergen": { "type": "http", "url": "http://mcp.example.internal:22222/mcp/mattergen/mcp" },
    "cppred":    { "type": "http", "url": "http://mcp.example.internal:22222/mcp/flux/mcp" }
  }
}
```

## JSON-RPC envelope

All calls are JSON-RPC 2.0 over HTTP POST:

```jsonc
// Request
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/call",
  "params": { "name": "<tool_name>", "arguments": { ... } }
}

// Headers
Content-Type: application/json
Accept: application/json, text/event-stream
```

Response is either `application/json` directly or `text/event-stream`
(FastMCP picks based on payload size). For SSE, the JSON is in the last
`data:` line.

Tool results are wrapped: `result.content[0].text` contains a
JSON-stringified payload. Parse it again to get the actual values.

---

# mattergen MCP — 15 tools

## Health & discovery

### `evolution_service_info`

Health check. No arguments.

```jsonc
// Response (parsed from content[0].text)
{ "service": "DEMO-Evolution", "version": "1.0.0",
  "status": "running", "active_sessions": 0 }
```

If `status != "running"` or HTTP fails, the server is down. Restart on
the GPU box.

## Autonomous mode (one-shot async)

### `run_autonomous_evolution`

Submit a complete run. Returns immediately with a `task_id`; the run
executes in the background on the server.

Arguments (all optional, defaults shown):

```jsonc
{
  "session_name": null,
  "n_pops": 8,                       // 1..256
  "max_generations": 2,              // 1..100
  "n_offspring_per_generation": 8,
  "noise_level": 0.3,                // 0.0..1.0
  "model_path": "",                  // empty → MATTERGEN_MODEL_PATH env
  "emo_engine": "TriplePopCMOEA",
  "objectives_json": "{\"ehull_eV\":\"minimize\",\"dielectric_epsx\":\"maximize\"}",
  "constraints_json": "{\"bandgap_eV\":{...},\"formation_energy_eV\":{...},\"num_elements\":{...}}",
  "initial_pool_mode": "random",     // random | cif_dir | hybrid
  "batch_size": 4,
  "max_attempts": 10,
  "seed_cif_files": ""               // base64 CIF(s) to anchor gen 0
}
```

`seed_cif_files` accepts three forms (mutually compatible with any `initial_pool_mode`):

| Form | Example |
|------|---------|
| Single CIF, raw base64 string | `"eyJkYXRhI..."` |
| Multiple CIFs, JSON array of base64 strings | `["b64_1","b64_2"]` |
| Multiple CIFs, named objects | `[{"name":"a.cif","cif_base64":"..."}]` |

Loaded seeds are tagged in the population with `meta={"type":"initial_cif"}` and `name="init_NNN.cif"`.

**Interaction with `initial_pool_mode` (verified 2026-06-15):**

- `random` + seeds → seeds load first, MatterGen fills remaining `n_pops - n_seeds` slots. **Works.**
- `cif_dir` + seeds → seeds load; `cif_dir` is server-configured and not exposed via this MCP tool.
- `hybrid` + seeds (no `cif_dir`) → **buggy**: seeds load but MatterGen fill never triggers; `population_size` stays at `n_seeds`. Use `random` until the server is fixed.

Response:

```jsonc
{ "task_id": "task_20260615_032837_abc12345",
  "session_id": "sess_...", "status": "submitted" }
```

### `get_autonomous_evolution_result`

Poll the submitted task. **Required**: `task_id`.

```jsonc
{
  "task_id": "task_20260615_032837_abc12345",
  "top_pareto": 10,                  // 0..100, 0 = all
  "delete_on_complete": false        // cleanup on completion
}
```

Response (status varies):

```jsonc
// while running
{ "status": "running", "current_generation": 7, "n_qualified_so_far": 12 }

// when complete
{
  "status": "completed",
  "session_id": "sess_...",
  "total_generations": 20,
  "total_qualified": 47,
  "pareto_front": [
    { "crystal_id": "gen12_ind_003", "formula": "Cr2S3",
      "properties": { "ehull_eV": 0.04, "dielectric_epsx": 18.3, ... },
      "pareto_rank": 1 },
    ...
  ]
}
```

If `delete_on_complete=true`, the session is removed after the result is
returned. Use this for one-off runs; leave `false` for runs you may want
to inspect later.

## Interactive mode (step-by-step with intervention)

### `create_evolution_session`

Same arguments as `run_autonomous_evolution`, plus an explicit `mode`:

```jsonc
{
  "mode": "interactive",   // "autonomous" | "interactive"
  ... (same args as run_autonomous_evolution)
}
```

Returns `{ "session_id": "sess_..." }`.

### `start_evolution`

**Required**: `session_id`.

```jsonc
{ "session_id": "sess_...", "auto_continue": false }
```

`auto_continue=true` makes it behave like autonomous mode (background to
completion). `false` waits for explicit `step_evolution` calls.

### `step_evolution`

Advance the session by N steps. **Required**: `session_id`.

```jsonc
{
  "session_id": "sess_...",
  "steps": 1,
  "pause_at": "fitness_evaluated"   // optional
  // pause_at ∈ {"generation_start", "fitness_evaluated", "generation_end"}
}
```

The agent usually wants `pause_at="fitness_evaluated"` — properties are
computed but selection hasn't run, so interventions take effect for the
current generation.

### `continue_evolution`

Resume after an intervention. **Required**: `session_id`.

```jsonc
{ "session_id": "sess_..." }
```

## Inspection

### `get_evolution_status`

**Required**: `session_id`.

Returns: `current_generation`, `is_completed`, `intervention_point`
(one of the `pause_at` values, or null).

### `get_evolution_population`

**Required**: `session_id`. Optional: `simplified=true` (returns only
formula + key scalars, much smaller payload).

### `get_evolution_pareto_front`

**Required**: `session_id`. Returns the Pareto front of the qualified
archive.

## Intervention

### `modify_crystal_fitness`

Penalize or boost specific candidates mid-run. **Required**: `session_id`,
`modifications_json`.

```jsonc
{
  "session_id": "sess_...",
  "modifications_json": "[{\"crystal_id\":\"gen0_ind_001\",
                            \"fitness_adjustment\":{\"type\":\"multiply\",\"value\":1.2},
                            \"reason\":\"CLscore rising trend\"}]"
}
```

`fitness_adjustment.type` ∈ `{"multiply", "add", "set"}`. Use cases:
down-rank candidates with toxic elements that slipped past the
constraint, or boost a promising trend the engine underweighted.

### `add_evolution_constraint`

Add a constraint to a live session. **Required**: `session_id`,
`constraint`.

```jsonc
{
  "session_id": "sess_...",
  "constraint": {
    "name": "bandgap_lower",
    "property": "bandgap_eV",
    "condition": {"min": 1.0},
    "weight": 5.0
  },
  "reason": "user tightened bandgap after seeing Pareto"
}
```

### `inject_evolution_knowledge`

Inject domain knowledge into the session history. **Required**:
`session_id`, `knowledge`.

```jsonc
{
  "session_id": "sess_...",
  "knowledge": "Prior literature on 2D piezoelectrics favors space group 156, 187, or 188; deprioritize centrosymmetric groups."
}
```

This is the highest-leverage intervention — it lets the agent bias
future generations using physical / literature priors without modifying
the underlying EMO algorithm.

## Session management

### `list_evolution_sessions`

No arguments. Returns all sessions (active and completed) with status.

### `delete_evolution_session`

**Required**: `session_id`. Removes the session and frees any GPU
resources held.

---

# crystal-property-prediction MCP — 1 tool

### `predict_flux_properties`

Run the full property pipeline on one CIF. All arguments optional
(defaults shown):

```jsonc
{
  "cif_path":    null,   // server-side path; mutually exclusive with the next 3
  "cif_content": null,   // CIF text in a string — preferred for agent use
  "cif_base64":  "<default CrSiTe3 base64>",  // base64-encoded CIF
  "cif":         null,   // auto-detect: looks like content → content; path → path
  "name":        "CrSiTe3",
  "fields":      null    // string[] subset; null returns all 9
}
```

**Mutual exclusion**: pass exactly one of `cif_path` / `cif_content` /
`cif_base64` / `cif`, or none (uses the default CrSiTe3 demo).

**Fields aliases** (any of these work in the `fields` array):

| Canonical | Alias |
|-----------|-------|
| `bandgap_ev` | `bandgap_eV`, `bandgap` |
| `hull_ev` | `ehull_eV`, `ehull`, `hull` |
| `f_max_ev_a` | `f_max_eV_A`, `f_max` |
| `formation_energy_ev` | `formation_energy_eV`, `e_form` |
| `exfoliation_energy_mev` | `exfoliation_energy_meV`, `exf` |
| `cl_score` | `clscore`, `cl` |
| `num_elements` | `n_elements` |
| `element_set` | `elements` |
| `sg_number` | `spacegroup_number` |

Response:

```jsonc
{
  "name": "CrSiTe3.cif",
  "path": null,                      // server path if cif_path was used
  "input_source": "content",         // "path" | "content" | "base64"
  "properties": {
    "bandgap_ev": 0.596,             // eV (ALIGNN jv_optb88vdw_bandgap)
    "formation_energy_ev": -0.128,   // eV/atom
    "hull_ev": 2.28,                 // eV/atom
    "exfoliation_energy_mev": 2.9,   // meV/atom (CHGNet 2D vs bulk)
    "f_max_ev_a": 0.464,             // eV/Å (MatterSim-relaxed + CHGNet)
    "cl_score": 0.786,               // 0..1 (JACS 100-bag ensemble)
    "num_elements": 3,
    "element_set": ["Cr","Si","Te"],
    "sg_number": 148                 // 1..230
  }
}
```

Penalty values (set on failure): `bandgap_ev=999`, `formation_energy_ev=999`,
`hull_ev=999`, `exfoliation_energy_mev=9999`, `f_max_ev_a=999`,
`cl_score=-1`, `sg_number=-1`.

---

## Probe recipes

`scripts/probe_mcp.py` does both of these and exits 0 on success.

### mattergen probe

```jsonc
// POST tools/call
{ "name": "evolution_service_info", "arguments": {} }

// expect: { "status": "running", "active_sessions": <int> }
```

### cppred probe

Pass no CIF — server uses the default CrSiTe3. Validate that bandgap,
exfoliation, cl_score are in physically sane ranges.

```jsonc
// POST tools/call
{ "name": "predict_flux_properties",
  "arguments": { "fields": ["bandgap_ev","hull_ev","cl_score",
                             "num_elements","sg_number","formation_energy_ev",
                             "f_max_ev_a","exfoliation_energy_mev"] } }

// expect: bandgap in (0, 5), exfoliation < 1000, cl_score in [0, 1]
```

---

## Failure-mode checklist

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| HTTP 502 from nginx | FastMCP backend process crashed on the GPU box | User SSHes in and restarts the MCP process |
| `evolution_service_info` returns `status: "degraded"` | MatterGen checkpoint failed to load | Check server logs; restart |
| `predict_flux_properties` returns all penalties | MatterSim/ALIGNN/CHGNet chain broken inside crystal-property-prediction MCP | Restart crystal-property-prediction MCP |
| `cl_score` always -1.0 | JACS ensemble failed to load inside mattergen MCP | Restart mattergen MCP |
| `run_autonomous_evolution` returns error | Constraint JSON has unknown key, or `n_pops` > 256 | Read error message, fix schema |
| `get_autonomous_evolution_result` stuck on `initializing` for >2 min | First generation takes long (cold MatterGen + JACS load); or JACS port conflict | Wait; or restart mattergen MCP |
| Task `task_id` not found | Task already deleted via `delete_on_complete` | Submit a new run |

---

## When to add a new tool

- **Need a new property** the crystal-property-prediction MCP pipeline doesn't expose: extend
  `predict_flux_properties` server-side. No agent change.
- **Need a new EMO engine variant**: add a new value for `emo_engine` in
  `run_autonomous_evolution` / `create_evolution_session`. Server-side.
- **Need a new kind of intervention** (e.g. "pin these candidates so
  they survive selection"): add a new tool. Document it here. Update
  SKILL.md Phase 3 if the agent should use it autonomously.
- **Need a completely new server** (e.g. MACE for better forces): stand
  it up as a third MCP, wire into `.mcp.json`, document here. The crystal-property-prediction
  MCP can stay focused on the MatterSim/ALIGNN/CHGNet/JACS chain.

---

## Known mattergen MCP bugs (observed 2026-06-15, source: `gitlink.org.cn/somunslotus/flux`)

These are server-side defects confirmed by two real evolution runs. The agent should not try to "fix" them by changing `objectives_json` / `constraints_json` — these are serialization / state-machine bugs.

| # | Bug | Repro | Likely source file |
|---|-----|-------|--------------------|
| 1 | **Duplicate entries in `pareto_front` / `population`** — same `crystal_id` or `name` appears 2× with different `fitness` (e.g. 0.0 and 1000000.0) | `run_autonomous_evolution(n_pops=8, max_generations=2)` → `pareto_front_size=6` but only 3 unique structures | `DEMO/mcp_server/evolution_engine.py` `_pareto_front_from` / `get_pareto_front`, or the underlying `emo_engine.get_current_population` is returning a list with shared references that get mutated twice |
| 2 | **`hybrid` mode does not fill when only `seed_cif_files` is provided** — `population_size` stays at the seed count; MatterGen fill never triggers | `run_autonomous_evolution(n_pops=4, max_generations=1, initial_pool_mode="hybrid", seed_cif_files="<1 base64 CIF>")` → `population_size=1` (or 2 with bug #1) | `DEMO/evolution_mcp_server/mcp_core.py` or wherever `initial_pool_mode` is dispatched — the `cif_plus_model_fill` branch from `Evoloop.py:294-313` was not wired to the new `seed_cif_files` path |
| 3 | **`formula` field returns `"unknown"` in `pareto_front` entries** — every candidate has `formula: "unknown"` while `element_set` / `num_elements` are correct | Any `run_autonomous_evolution(...)` completion | serialization layer where `_simplify_crystal` builds the response — formula string is not being derived from `element_set` |
| 4 | **`crystal_id` is `null` in full population view but populated in simplified view** | `get_evolution_population(simplified=false)` → `crystal_id: null`; `simplified=true` → `crystal_id: "init_000.cif"` | `DEMO/mcp_server/evolution_core.py` `get_population` paths — two different `_simplify_crystal` paths, one preserves `crystal_id`, the other drops it |
| 5 | **Status race** — `get_autonomous_evolution_result` returns `status="completed"` while `list_evolution_sessions` still shows `status="running"` for the same `session_id` | Poll `get_autonomous_evolution_result` to completion, then immediately call `list_evolution_sessions` | `_autonomous_tasks` status flip in `get_autonomous_evolution_result` does not propagate to the session manager's status field |

**Bug-1 root-cause hint:** the duplicated entries have fitness values 0.0 and 1000000.0. The 1000000.0 looks like a penalty sentinel — somewhere the same crystal object is being appended once as "raw" and once as "penalized" (constraint-violation marker), which would point to a shared-reference bug in the population update step rather than in the FNDS sort itself.

**Recommended fix order:** #3 and #4 first (trivial serialization fixes, no algorithmic risk), then #1 (add a `seen = set()` dedup pass on the response builder), then #2 (requires touching the population-init dispatcher), then #5 (state-machine consistency).
