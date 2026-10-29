# MCP Test Suite

Regression tests for the two DEMO HTTP MCP servers (`mattergen` + `cppred`).
Run this after every server-side change — code deploy, model reload, restart,
config tweak — to catch breakage before it wastes a research run.

- **Single file, no dependencies.** Pure Python 3 stdlib. Copy `test_mcps.py`
  onto any machine that can reach the MCP HTTP endpoints and run it.
- **Failing tests explain themselves.** Each failure prints a `hint:` line
  with the likely cause and the next step.
- **CI-friendly.** Optional `--json report.json` emits structured results;
  exit code is `0` (all pass) / `1` (any fail) / `2` (setup error).

## Quick start

```bash
# Full suite — both MCPs, including a 1-generation evolution (~3-6 min).
python test_mcps.py

# Crystal-property-prediction MCP only — fast, useful when mattergen is being restarted.
python test_mcps.py --cppred-only

# Skip the slow evolution integration (still tests health + properties).
python test_mcps.py --skip-evolution

# Custom endpoints (e.g. pointing at a staging GPU box).
python test_mcps.py \
  --mattergen http://192.0.2.5:22222/mcp/mattergen/mcp \
  --cppred    http://192.0.2.5:22222/mcp/flux/mcp

# CI mode — write JSON alongside the human-readable stdout.
python test_mcps.py --json report.json

# Enumerate test names without running anything.
python test_mcps.py --list
```

## Endpoints

| Server | Default URL | Role |
|--------|-------------|------|
| mattergen | `http://mcp.example.internal:22222/mcp/mattergen/mcp` | evolution loop (submit, monitor, intervene, fetch results) |
| cppred | `http://mcp.example.internal:22222/mcp/flux/mcp` | CIF → 9 scalar properties in one call |

Override either with `--mattergen URL` / `--cppred URL`.

## Test inventory

18 tests total. The first 4 letters tell you what's being checked; the
right column says what fails when the test fails.

### cppred (7 tests, ~3-4 min)

| Test | What it asserts | When it fails |
|------|-----------------|---------------|
| `cppred_health` | Default CIF returns all 9 fields | Server down, schema regression |
| `cppred_cl_score_not_penalized` | `cl_score != -1.0` | JACS 100-bag ensemble failed to load |
| `cppred_crsite3_reference_values` | CrSiTe3 in known bands (Eg 0.2-1.5, cl 0.5-0.95, …) | ALIGNN / CHGNet weights drifted or broken |
| `cppred_multiple_cifs` | 4 different CIFs all return clean properties | Pipeline-wide failure |
| `cppred_input_forms_equivalent` | `cif_content` vs `cif_base64` agree (Δ<1e-2) | Base64 decode bug, encoding mismatch |
| `cppred_fields_subset` | `fields=['bandgap_ev']` returns bandgap_ev | `fields` filter regressed |
| `cppred_bogus_input_returns_penalty` | Garbage CIF returns penalty sentinel OR clean error | Missing input validation |

### mattergen (11 tests, ~3-5 min)

| Test | What it asserts | When it fails |
|------|-----------------|---------------|
| `mattergen_health` | `evolution_service_info` returns `status=running` | Server down or checkpoint failed to load |
| `mattergen_list_sessions` | `list_evolution_sessions` returns a list | Serialization regression |
| `mattergen_autonomous_evolution_submit` | `run_autonomous_evolution` returns a `task_id` | Submission path broken, bad `model_path` |
| `mattergen_autonomous_evolution_complete` | The submitted run reaches `status=completed` within `--evolution-timeout` | Model load, JACS port conflict, constraint schema typo |
| `bug2_hybrid_fills_beyond_seeds` | `population_size > n_seeds` when `initial_pool_mode=hybrid` + `seed_cif_files` | Bug #2 regression: hybrid fill branch not wired to seeds |
| `bug3_formula_not_unknown` | Pareto entries carry real `formula` strings | Bug #3 regression: `_simplify_crystal` not deriving formula |
| `bug4_crystal_id_in_both_views` | `crystal_id` populated in both `simplified=true` and `simplified=false` | Bug #4 regression: two serialization paths diverge |
| `bug5_status_consistency` | `result.status == list_evolution_sessions.status` | Bug #5 regression: status race |
| `bug1_no_duplicate_pareto_entries` | No duplicate `crystal_id` / `name` in pareto (INFO level) | Bug #1 — intentionally unfixed; test passes either way |
| `mattergen_interactive_session_smoke` | `create → start → step → continue` lifecycle works | Interactive-mode dispatcher broken |
| `mattergen_cleanup_session` | `delete_evolution_session` removes the test session | Session leak |

## CLI flags

| Flag | Default | Purpose |
|------|---------|---------|
| `--mattergen URL` | `http://mcp.example.internal:22222/mcp/mattergen/mcp` | Override mattergen endpoint |
| `--cppred URL` | `http://mcp.example.internal:22222/mcp/flux/mcp` | Override cppred endpoint |
| `--timeout SEC` | `180` | Per-call HTTP timeout |
| `--evolution-timeout SEC` | `900` | Deadline for autonomous evolution to complete |
| `--cppred-only` | off | Skip all mattergen tests |
| `--skip-evolution` | off | Skip the slow evolution integration (also skips bug-# tests) |
| `--skip-interactive` | off | Skip the interactive session smoke test |
| `--model-path PATH` | server default | mattergen checkpoint dir; pass when default is wrong |
| `--cif-dir DIR` | project `cif_files/` + `mp_candidates_v3_common/cifs/` | CIF fixture directory (repeatable) |
| `--verbose` | off | Dump full server payloads for failing tests |
| `--json PATH` | off | Also write structured JSON report to PATH |
| `--list` | off | List all test names and exit |

## CIF fixtures

The suite looks for these CIFs to anchor property tests:

- `CrSiTe3.cif` — calibration anchor; expected bandgap ≈ 0.6-0.8 eV, cl_score ≈ 0.786.
- `AlInSe3.cif`, `mp-555903_Al(IO3)3.cif`, `mp-1374068_Al2(WO4)3.cif` — extra CIFs for `cppred_multiple_cifs`.

Search order defaults to the project's `cif_files/` then `mp_candidates_v3_common/cifs/`.
Point at a custom directory with `--cif-dir` (repeatable):

```bash
python test_mcps.py --cif-dir /path/to/cifs --cif-dir /path/to/more
```

If `CrSiTe3.cif` is missing, the suite exits with code `2` and tells you which dirs it checked.

## JSON report shape

`--json report.json` writes:

```jsonc
{
  "summary": { "total": 18, "passed": 17, "failed": 1 },
  "results": [
    {
      "name": "cppred_cl_score_not_penalized",
      "passed": false,
      "detail": "cl_score=-1.0 (JACS failed to load)",
      "hint": "Restart the crystal-property-prediction MCP process. The 100-bag CGCNN ensemble in "
              "DEMO/PUCGCNN/ failed to initialize on this boot.",
      "elapsed_seconds": 8.4
    },
    ...
  ]
}
```

Easy to consume from CI, Slack bots, etc.

## Interpreting output

A passing run looks like:

```
[cppred MCP]
  [PASS] cppred_health  (17.4s)
         returned 9 fields for CrSiTe3.cif
  [PASS] cppred_cl_score_not_penalized  (22.3s)
         cl_score=0.7862
  ...

========================================================================
ALL TESTS PASSED — 18/18 passed
========================================================================
```

A failing run prints each failure with a `hint:` line suggesting the next step:

```
  [FAIL] cppred_cl_score_not_penalized  (8.4s)
         cl_score=-1.0 (JACS failed to load)
         hint: Restart the crystal-property-prediction MCP process. The 100-bag CGCNN ensemble in
               DEMO/PUCGCNN/ failed to initialize on this boot.
```

The summary at the bottom collects every failure so you can scan quickly:

```
========================================================================
SOME TESTS FAILED — 16/18 passed
  ✗ cppred_cl_score_not_penalized
      cl_score=-1.0 (JACS failed to load)
      hint: Restart the crystal-property-prediction MCP process. ...
  ✗ bug3_formula_not_unknown
      5/5 entries have formula='unknown'
      hint: _simplify_crystal is not deriving formula from element_set. ...
========================================================================
```

## Background — the 5 mattergen MCP bugs (observed 2026-06-15)

Four were fixed in the 2026-06-17 deploy. Bug #1 was left in place because it
only reproduces with very small populations / few generations and is not
load-bearing for real searches.

| # | Bug | Status | Covered by |
|---|-----|--------|------------|
| 1 | Duplicate entries in `pareto_front` / `population` (same crystal twice, different fitness) | Unfixed (INFO only) | `bug1_no_duplicate_pareto_entries` |
| 2 | `hybrid` mode + `seed_cif_files` did not trigger MatterGen fill — population stayed at seed count | **Fixed** | `bug2_hybrid_fills_beyond_seeds` |
| 3 | `formula` returned `"unknown"` for every candidate despite correct `element_set` | **Fixed** | `bug3_formula_not_unknown` |
| 4 | `crystal_id` null in full population view but populated in simplified view (two serialization paths) | **Fixed** | `bug4_crystal_id_in_both_views` |
| 5 | Status race: `get_autonomous_evolution_result` flipped to `completed` before `list_evolution_sessions` caught up | **Fixed** | `bug5_status_consistency` |

If any of #2-#5 starts failing, the corresponding bug regressed.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| `cppred_health` errors with HTTP 502 | FastMCP backend crashed | Restart the crystal-property-prediction MCP process on the GPU box |
| `cppred_cl_score_not_penalized` returns -1.0 | JACS 100-bag CGCNN ensemble failed to load | Restart crystal-property-prediction MCP (reloads JACS) |
| `mattergen_autonomous_evolution_submit` fails with `未找到 config.yaml` | Default `MATTERGEN_MODEL_PATH` is wrong on the server | Pass `--model-path /real/path`, or fix the env var |
| `mattergen_autonomous_evolution_complete` times out | Cold MatterGen + JACS load on first generation | Raise `--evolution-timeout` to 1800, or warm up with a tiny run first |
| Evolution completes with 0 qualified candidates | Constraints too tight | Not a server bug — relax the constraints in `test_mcps.py:_submit_hybrid_evolution` |
| All `cppred_multiple_cifs` hit the same penalty | Pipeline broken at the model layer | Check MatterSim relax output; check ALIGNN/CHGNet model loads |
| `bug4` fails with "session vanished" | Session was deleted before population fetch | Check for stale `delete_evolution_session` calls or session TTL |

## Relationship to other tooling

| Tool | Role |
|------|------|
| `probe_mcp.py` (this directory) | Minimal smoke test — both MCPs alive, basic sanity. Run before any research evolution. |
| `test_mcps.py` (this file) | Comprehensive regression suite — every endpoint + every bugfix. Run after every server change. |
| `references/mcp_servers.md` (skill) | Authoritative tool-by-tool interface contract. If real server diverges, edit the doc. |
| `references/property_catalog.md` (skill) | Property key aliases, source models, clamp rules, penalty values. |

## Adding a new test

1. Add a `test_<area>_<thing>(suite, ...)` function following the existing
   pattern — call `suite.record(name, passed, detail, elapsed, payload, hint=...)`.
2. Always pass `hint=` on the failure path so the next person knows what to do.
3. Append the test name to `ALL_TEST_NAMES` so `--list` reports it.
4. Wire it into `main()` in the appropriate section.
5. If it needs a CIF fixture, declare it in `main()`'s fixture resolution block.
6. Run `python test_mcps.py --list` to confirm it appears, then run the suite.

Keep tests independent — each should clean up after itself (e.g. delete any
session it creates) so the suite is re-runnable.
