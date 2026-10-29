# Runtime topology & the docker-run wrapper

## The 5 MCPs (must be up before init)

| service | host port | role in the loop |
|---|---|---|
| `diffusion-mutate` | 9131 | mutation (noise+denoise) — **required** |
| `mattersim-relax` | 9129 | relax offspring (all backends) + fitness source for `mattersim` backend — **required** |
| `alignn-predict` | 9125 | fitness source for `alignn` backend only — **required if `--fitness-backend alignn`** (default) |
| `mattergen-evaluate` | 9130 | final novelty/stability report — required only for `finalize --evaluate` |
| `mattergen-generate` | 9126 | initial pool (if `--generate N`) — required only then |

All expose `/mcp` (FastMCP streamable-http). The nginx reverse proxy at `${MATERIALS_MCP_BASE_URL}` (configured in `.env`, **never committed**) strips the `/mcp/<service>` prefix and forwards to the in-container `/mcp`. The driver builds each endpoint as `${MATERIALS_MCP_BASE_URL}/<service>/mcp`.

Required env (repo-root `.env`, auto-loaded by `scripts/run.sh`):

```bash
# .env — keep out of git (already in .gitignore)
MATERIALS_MCP_BASE_URL=http://<host>:<port>/mcp
```

| service | endpoint |
|---|---|
| `diffusion-mutate` | `${MATERIALS_MCP_BASE_URL}/diffusion-mutate/mcp` |
| `mattersim-relax` | `${MATERIALS_MCP_BASE_URL}/mattersim-relax/mcp` |
| `alignn-predict` | `${MATERIALS_MCP_BASE_URL}/alignn/mcp` |
| `mattergen-evaluate` | `${MATERIALS_MCP_BASE_URL}/mattergen-evaluate/mcp` |
| `mattergen-generate` | `${MATERIALS_MCP_BASE_URL}/mattergen-generate/mcp` |

Per-service override: set `MCP_<NAME>_URL` (e.g. `MCP_MUTATE_URL=http://localhost:9131/mcp` for direct `--network host` access on the box) to bypass the proxy for one service.

## Health-check (run before init)

```bash
BASE="${MATERIALS_MCP_BASE_URL:?set MATERIALS_MCP_BASE_URL in .env}"
for u in \
  "$BASE/diffusion-mutate/mcp" \
  "$BASE/mattersim-relax/mcp" \
  "$BASE/alignn/mcp" \
  "$BASE/mattergen-evaluate/mcp" \
  "$BASE/mattergen-generate/mcp"; do
  code=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$u" \
    -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
    -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"p","version":"1"}}}')
  echo "$u -> HTTP $code"
done
```
Expect 200 for each required service. If a non-required one is down, skip the feature that needs it (`--generate`, `--evaluate`) rather than aborting.

## The driver wrapper (host-side, preferred)

The driver needs only `pymatgen` + `numpy` (no GPU) and talks to the MCPs over HTTP at `${MATERIALS_MCP_BASE_URL}/<service>/mcp` (from `.env`, overridable per service via `MCP_<NAME>_URL`), so it **runs on the host in any venv that has those packages** — no docker, no mounts. One-time setup:

```bash
# create or reuse any Python>=3.10 venv, then:
pip install pymatgen numpy
# (uv equivalent) uv pip install --python <venv>/bin/python pymatgen numpy
```

Then every subcommand goes through `run.sh`, which auto-picks the interpreter:

```bash
SKILL=<absolute path to this skill directory>
RUN_DIR=outputs/evo_<goal>          # any host path
mkdir -p "$RUN_DIR"
bash "$SKILL/scripts/run.sh" <subcommand> --run-dir "$RUN_DIR" "$@"
```

`run.sh` resolution order:
1. `$EVOLVE_PY` if it can `import pymatgen`
2. repo-root `.venv/bin/python` if it can `import pymatgen`
3. fallback: `docker run --rm --network host` the `$EVOLVE_IMAGE` image (only if no venv has pymatgen; then `--run-dir` must be under your cwd so it can be mounted)

Because it's host-side, **`--run-dir` and `--seed-cifs` accept any host path** — no "seeds must be inside the mounted run-dir" gotcha. Output files are owned by your user (not root).

## Timing expectations (set user expectations)

- Each MCP serializes GPU work (process lock).
- Per generation (pop 20): ~20 mutations + ~40 characterizations. Backend-dependent:
  - `alignn` backend: ~40 (relax + alignn) → **several minutes**.
  - `mattersim` backend: ~40 (relax only, no second MCP) → roughly **half** the alignn time.
- `finalize --evaluate` adds minutes more (845k reference matching).
- You (Claude) are in the loop between steps, so the cadence is: `step` (wait) → read snapshot → decide → maybe `steer` → `step`. Don't fire steps in parallel.

## Graceful degradation

- `mattersim-relax` failure on an individual → driver falls back to the raw (unrelaxed) structure and continues (alignn on raw).
- `alignn-predict` failure on an individual → that property is `null` → SAES scores it worst-case; individual likely gets selected out. Run continues.
- `diffusion-mutate` failure on an offspring → that offspring is skipped; fewer offspring that generation. Run continues.
- Whole-MCP outage (health-check fails) → abort before init with a clear message; don't attempt a partial run.
