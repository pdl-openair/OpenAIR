#!/usr/bin/env python3
"""
probe_mcp.py — Verify both DEMO MCP servers are alive before starting a run.

Usage:
    python probe_mcp.py [--mattergen URL] [--cppred URL] [--timeout 30]

Exits 0 if both servers respond with a sensible payload, non-zero otherwise.
The agent should run this BEFORE every evolutionary search.

This script does NOT need pymatgen, torch, requests, or any model package —
only the Python stdlib. It calls the MCP streamable-http endpoints directly
with the minimal JSON-RPC envelope.

Real tool surface (as of 2026-06-15):
    mattergen MCP — `evolution_service_info` (15 session-management tools total)
    crystal-property-prediction MCP — `predict_flux_properties` (1 tool, 9 properties per call)
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import time
from typing import Any

DEFAULT_MATTERGEN = "http://mcp.example.internal:22222/mcp/mattergen/mcp"
DEFAULT_CPPRED = "http://mcp.example.internal:22222/mcp/flux/mcp"

# CrSiTe3 — the crystal-property-prediction MCP's built-in default CIF. Known 2D magnetic semiconductor,
# so bandgap ≈ 0.5–0.7 eV and exfoliation energy should be low (<50 meV/atom).
DEFAULT_NAME = "CrSiTe3"


def _http_post_json(url: str, payload: dict, timeout: float) -> dict[str, Any]:
    import urllib.request
    import urllib.error

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
            f"HTTP {e.code} from {url}: {e.read().decode('utf-8', errors='replace')[:300]}"
        )
    except Exception as e:
        raise RuntimeError(f"network error hitting {url}: {e}")

    # FastMCP streamable-http returns either application/json or text/event-stream.
    if body.lstrip().startswith("event:") or "\ndata:" in body:
        lines = [ln for ln in body.splitlines() if ln.startswith("data:")]
        if not lines:
            raise RuntimeError(f"SSE response with no data lines: {body[:200]}")
        return json.loads(lines[-1][len("data:"):].strip())
    return json.loads(body)


def _call_tool(url: str, tool_name: str, args: dict, timeout: float) -> dict[str, Any]:
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": args},
    }
    resp = _http_post_json(url, payload, timeout)
    if "error" in resp:
        raise RuntimeError(f"MCP error from {tool_name}: {resp['error']}")
    result = resp.get("result", {})
    # Tool result content is a list of {type:text, text:"..."} items.
    # The cppred server also returns structuredContent as a stringified JSON.
    if result.get("isError"):
        raise RuntimeError(f"{tool_name} returned isError=true: {result}")
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


def _probe_mattergen(url: str, timeout: float) -> tuple[bool, str]:
    start = time.time()
    try:
        out = _call_tool(url, "evolution_service_info", {}, timeout)
    except Exception as e:
        return False, f"FAIL: {e}"
    elapsed = time.time() - start

    status = out.get("status")
    active = out.get("active_sessions")
    if status == "running":
        return True, (
            f"PASS  evolution_service_info → status={status} "
            f"active_sessions={active}  ({elapsed:.1f}s)"
        )
    return False, f"UNEXPECTED: evolution_service_info returned {out!r}  ({elapsed:.1f}s)"


def _probe_cppred(url: str, timeout: float) -> tuple[bool, str]:
    # Use the server's built-in default CIF (CrSiTe3) by passing no CIF at all.
    # The server substitutes CPPRED_MCP_DEFAULT_CIF_BASE64.
    start = time.time()
    try:
        out = _call_tool(
            url,
            "predict_flux_properties",
            {"fields": ["bandgap_ev", "hull_ev", "cl_score", "num_elements",
                        "sg_number", "formation_energy_ev", "f_max_ev_a",
                        "exfoliation_energy_mev"]},
            timeout,
        )
    except Exception as e:
        return False, f"FAIL: {e}"
    elapsed = time.time() - start

    props = out.get("properties") or {}
    name = out.get("name", "?")
    # Sanity band: CrSiTe3 is a known 2D magnetic semiconductor.
    bg = props.get("bandgap_ev")
    exf = props.get("exfoliation_energy_mev")
    cl = props.get("cl_score")
    if (
        isinstance(bg, (int, float)) and 0 < bg < 5
        and isinstance(exf, (int, float)) and exf < 1000
        and isinstance(cl, (int, float)) and 0 <= cl <= 1
    ):
        return True, (
            f"PASS  predict_flux_properties({name}) → "
            f"Eg={bg:.3f} eV, E_exf={exf:.1f} meV, CL={cl:.3f}, "
            f"hull={props.get('hull_ev'):.2f}, "
            f"E_form={props.get('formation_energy_ev'):.3f}, "
            f"f_max={props.get('f_max_ev_a'):.3f}  ({elapsed:.1f}s)"
        )
    return False, f"UNEXPECTED: predict_flux_properties returned {out!r}  ({elapsed:.1f}s)"


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe DEMO MCP servers")
    parser.add_argument("--mattergen", default=DEFAULT_MATTERGEN)
    parser.add_argument("--cppred", default=DEFAULT_CPPRED)
    parser.add_argument("--timeout", type=float, default=60.0)
    args = parser.parse_args()

    print(f"Probing mattergen {args.mattergen}")
    mg_ok, mg_msg = _probe_mattergen(args.mattergen, args.timeout)
    print(f"  → {mg_msg}")

    print(f"Probing cppred    {args.cppred}")
    cppred_ok, cppred_msg = _probe_cppred(args.cppred, args.timeout)
    print(f"  → {cppred_msg}")

    print()
    if mg_ok and cppred_ok:
        print("ALL MCP PROBES PASSED — safe to start a run.")
        return 0
    print("ONE OR MORE PROBES FAILED — do NOT start a run until resolved.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
