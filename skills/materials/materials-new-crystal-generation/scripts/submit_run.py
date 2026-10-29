#!/usr/bin/env python3
"""
Submit an autonomous DEMO evolution run seeded with user-supplied CIFs.

Usage:
    python submit_run.py --seed-dir mp_candidates_v3_common/cifs \
        --session-name ternary_2d_semiconductor_v1 \
        --n-pops 64 --max-generations 25 \
        --out task_id.txt
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

_MCP_BASE = os.environ.get("MATERIALS_MCP_BASE_URL", "").rstrip("/")
MATTERGEN_URL = f"{_MCP_BASE}/mattergen/mcp" if _MCP_BASE else ""


def http_post_json(url: str, payload: dict, timeout: float) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        raise RuntimeError(
            f"HTTP {e.code}: {e.read().decode('utf-8', errors='replace')[:500]}"
        )
    if body.lstrip().startswith("event:") or "\ndata:" in body:
        lines = [ln for ln in body.splitlines() if ln.startswith("data:")]
        if not lines:
            raise RuntimeError(f"SSE with no data: {body[:200]}")
        return json.loads(lines[-1][len("data:"):].strip())
    return json.loads(body)


def call_tool(tool_name: str, args: dict, timeout: float = 120.0) -> dict:
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": args},
    }
    resp = http_post_json(MATTERGEN_URL, payload, timeout)
    if "error" in resp:
        raise RuntimeError(f"MCP error: {resp['error']}")
    result = resp.get("result", {})
    if result.get("isError"):
        raise RuntimeError(f"isError=true: {result}")
    text_blob = ""
    for item in result.get("content", []):
        if item.get("type") == "text":
            text_blob = item.get("text", "")
            break
    if not text_blob:
        return result
    try:
        return json.loads(text_blob)
    except json.JSONDecodeError:
        return {"raw": text_blob}


def load_seeds(seed_dir: Path) -> list[dict]:
    seeds = []
    for cif in sorted(seed_dir.glob("*.cif")):
        content = cif.read_bytes()
        seeds.append(
            {"name": cif.name, "cif_base64": base64.b64encode(content).decode("ascii")}
        )
    return seeds


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed-dir", required=True, type=Path)
    ap.add_argument("--session-name", required=True)
    ap.add_argument("--n-pops", type=int, default=64)
    ap.add_argument("--max-generations", type=int, default=25)
    ap.add_argument("--n-offspring", type=int, default=64)
    ap.add_argument("--noise-level", type=float, default=0.3)
    ap.add_argument("--emo-engine", default="TriplePopCMOEA")
    ap.add_argument("--out", default="task_id.txt")
    args = ap.parse_args()

    seeds = load_seeds(args.seed_dir)
    print(f"Loaded {len(seeds)} seed CIFs from {args.seed_dir}")

    objectives = {
        "ehull_eV": "minimize",
        "dielectric_epsx": "maximize",
    }
    constraints = {
        "bandgap_eV": {"min": 0.2, "max": 3.5, "weight": 5.0},
        "exfoliation_energy_meV": {"min": -5.0, "max": 350.0, "weight": 5.0},
        "formation_energy_eV": {"max": 0, "weight": 10.0},
        "cl_score": {"min": 0.50, "weight": 5.0},
        "f_max_eV_A": {"max": 2.0, "weight": 5.0},
        "ehull_eV": {"max": 1.0, "weight": 5.0},
        "num_elements": {"min": 3, "max": 3, "weight": 10.0},
        "element_set": {
            "banned": ["Hg", "Tl", "Na", "K", "F"],
            "weight": 5.0,
        },
    }

    submit_args = {
        "session_name": args.session_name,
        "n_pops": args.n_pops,
        "max_generations": args.max_generations,
        "n_offspring_per_generation": args.n_offspring,
        "noise_level": args.noise_level,
        "emo_engine": args.emo_engine,
        "objectives_json": json.dumps(objectives),
        "constraints_json": json.dumps(constraints),
        "initial_pool_mode": "random",  # hybrid bug workaround per skill notes
        "seed_cif_files": json.dumps(seeds),
        "batch_size": 8,
        "max_attempts": 10,
    }

    print(f"Submitting run: {args.session_name}")
    print(f"  n_pops={args.n_pops}, max_generations={args.max_generations}")
    print(f"  noise={args.noise_level}, engine={args.emo_engine}")
    print(f"  objectives={objectives}")
    print(f"  constraints keys={list(constraints.keys())}")

    out = call_tool("run_autonomous_evolution", submit_args, timeout=180.0)
    print("\n=== Submission response ===")
    print(json.dumps(out, indent=2, ensure_ascii=False)[:2000])

    task_id = out.get("task_id") or out.get("session_id")
    if not task_id:
        print("ERROR: no task_id in response", file=sys.stderr)
        return 1
    Path(args.out).write_text(str(task_id))
    print(f"\nTask ID: {task_id}  (saved to {args.out})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
