# Runtime Topology

## The 5 MCPs formula-crossover-agent depends on

| Service | Endpoint | Tool | Used in |
|---|---|---|---|
| diffcsp-generate | `${MATERIALS_MCP_BASE_URL}/diffcsp-generate/mcp` | `generate_structures` | Phase 2b: formula → CIF |
| mattersim-relax | `${MATERIALS_MCP_BASE_URL}/mattersim-relax/mcp` | `relax_structure` | Phase 2c: CIF relax |
| alignn | `${MATERIALS_MCP_BASE_URL}/alignn/mcp` | `predict_alignn_properties` | Phase 2d: property scoring |
| mattergen-evaluate | `${MATERIALS_MCP_BASE_URL}/mattergen-evaluate/mcp` | `evaluate_structures_async` / `get_evaluation_result` | Phase 2f: population health |
| mattergen-generate | `${MATERIALS_MCP_BASE_URL}/mattergen-generate/mcp` | `generate_crystals` | (optional) alternate seed pool |

Each MCP serializes GPU work (process lock). Expect ~30s per DiffCSP call, ~10s per MatterSim relax, ~1s per ALIGNN call.

## Per-service URL override

Set `<SERVICE>_URL` env var to override the base for one service:

```bash
export MCP_ALIGNN_URL=http://special-alignn:8000/mcp
```

Valid override vars: `MCP_DIFFCSP_URL`, `MCP_RELAX_URL`, `MCP_ALIGNN_URL`, `MCP_EVALUATE_URL`, `MCP_GENERATE_URL`.

## Pre-flight check

```bash
python scripts/probe_mcps.py
```

All 5 checks must PASS before `init`. GPU time is expensive — don't burn it on a broken endpoint.

## Healthcheck curls

For each MCP, verify it responds to `tools/list`:

```bash
curl -sS -X POST "${MATERIALS_MCP_BASE_URL}/diffcsp-generate/mcp" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' | jq .result.tools[].name
```

Expected: includes `generate_structures`.
