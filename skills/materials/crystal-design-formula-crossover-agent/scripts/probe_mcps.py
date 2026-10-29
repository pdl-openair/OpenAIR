#!/usr/bin/env python3
"""Smoke-test the four core MCPs used by the SAGA-MVP skill.

Uses the standard MCP protocol (initialize → initialized → tools/list →
tools/call). Exits 0 if all four probes pass; non-zero otherwise.

Run BEFORE every fresh MVP run — GPU time is the most expensive
resource in this project and a dead MCP wastes it fast.

Usage:
    python scripts/probe_mcps.py
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any

BASE = os.environ.get("MATERIALS_MCP_BASE_URL", "").rstrip("/")
if not BASE:
    print("ERROR: MATERIALS_MCP_BASE_URL is not set (see .env.example).", file=sys.stderr)
    sys.exit(2)
TIMEOUT_S = 60


class McpSession:
    def __init__(self, url: str):
        self.url = url
        self._id = 0

    def _post(self, payload: dict[str, Any], expect_result: bool = True) -> dict[str, Any] | None:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            raw = resp.read().decode("utf-8", errors="replace").strip()

        if not expect_result:
            return None

        # FastMCP may stream as SSE; the result is the last "data:" line
        text = raw
        if "data:" in text:
            last = None
            for line in text.splitlines():
                if line.startswith("data:"):
                    last = line[len("data:"):].strip()
            if last is None:
                raise RuntimeError("SSE stream without data: line")
            text = last
        return json.loads(text)

    def initialize(self) -> dict[str, Any]:
        self._id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": self._id,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "saga-probe", "version": "0.1"},
            },
        }
        result = self._post(payload)
        if "error" in result:
            raise RuntimeError(f"initialize error: {result['error']}")
        # Notify initialized (no response expected)
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"}, expect_result=False)
        return result.get("result", {})

    def call(self, tool: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
        self._id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": self._id,
            "method": "tools/call",
            "params": {"name": tool, "arguments": args or {}},
        }
        result = self._post(payload)
        if "error" in result:
            raise RuntimeError(f"rpc error: {result['error']}")
        return result.get("result", {})


def check_pymatgen() -> tuple[bool, str]:
    try:
        from pymatgen.analysis.hhi import HHIModel  # noqa: F401
        from pymatgen.core import Structure  # noqa: F401
        return True, "ok"
    except Exception as exc:  # noqa: BLE001
        return False, f"err: {exc!r}"


def probe_diffcsp() -> tuple[bool, str]:
    s = McpSession(BASE + "/diffcsp-generate/mcp")
    s.initialize()
    r = s.call("generate_structures", {"formula": "Fe2O3", "num_evals": 1, "model_name": "mp_csp"})
    n_gen = _safe_path(r, "num_generated")
    if n_gen is None:
        # DiffCSP returns plain result; try to parse
        text = json.dumps(r)[:200]
        return True, f"ok (response: {text})"
    return True, f"ok (num_generated={n_gen})"


def probe_mattersim() -> tuple[bool, str]:
    s = McpSession(BASE + "/mattersim-relax/mcp")
    s.initialize()
    cif = (
        "data_Fe\n_cell_length_a 2.87\n_cell_length_b 2.87\n"
        "_cell_length_c 2.87\n_cell_angle_alpha 90\n"
        "_cell_angle_beta 90\n_cell_angle_gamma 90\n"
        "_symmetry_space_group_name_H-M 'P 1'\n"
        "loop_ _atom_site_label _atom_site_type_symbol "
        "_atom_site_fract_x _atom_site_fract_y _atom_site_fract_z\n"
        " Fe1 Fe 0 0 0\n"
    )
    r = s.call("relax_structure", {"cif_content": cif, "max_steps": 5})
    text = json.dumps(r)[:200]
    return True, f"ok (response: {text})"


def probe_alignn() -> tuple[bool, str]:
    s = McpSession(BASE + "/alignn/mcp")
    s.initialize()
    # Default CIF (CrSiTe3) is used when no CIF supplied
    r = s.call("predict_alignn_properties", {"models": ["magmom"]})
    text = json.dumps(r)[:200]
    return True, f"ok (response: {text})"


def probe_mattergen_eval() -> tuple[bool, str]:
    """Probe mattergen-evaluate via tools/list (cheap, no GPU).

    Calling evaluate_structures for real is minutes-level — wrong for a
    probe. tools/list confirms the server is alive and the tool is
    registered.
    """
    s = McpSession(BASE + "/mattergen-evaluate/mcp")
    s.initialize()
    s._id += 1
    payload = {
        "jsonrpc": "2.0",
        "id": s._id,
        "method": "tools/list",
        "params": {},
    }
    result = s._post(payload)
    tools = result.get("result", {}).get("tools", [])
    names = [t.get("name", "") for t in tools]
    needed = {"evaluate_structures_async", "get_evaluation_result"}
    if not needed.issubset(set(names)):
        missing = needed - set(names)
        return False, f"missing tools: {sorted(missing)} (have {names})"
    return True, f"ok (tools present: {sorted(needed)})"


def _safe_path(obj: Any, *keys: str) -> Any:
    cur = obj
    for k in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


def main() -> int:
    print("Probing core MCPs for SAGA-MVP skill...")
    failures = 0

    pm_ok, pm_msg = check_pymatgen()
    print(f"  [local] pymatgen:     {'PASS' if pm_ok else 'FAIL'} ({pm_msg})")
    failures += 0 if pm_ok else 1

    for label, fn in [
        ("diffcsp", probe_diffcsp),
        ("mattersim", probe_mattersim),
        ("alignn", probe_alignn),
        ("mgen-eval", probe_mattergen_eval),
    ]:
        try:
            ok, msg = fn()
        except Exception as exc:  # noqa: BLE001
            ok, msg = False, f"exception: {exc!r}"
        print(f"  [mcp]   {label:10s}: {'PASS' if ok else 'FAIL'} ({msg})")
        failures += 0 if ok else 1

    print()
    if failures:
        print(f"RESULT: {failures} probe(s) FAILED. Do NOT start a run.")
        return 1
    print("RESULT: all probes PASS.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
