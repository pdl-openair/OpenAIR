"""HTTP client wrappers for the 5 materials MCPs.

All functions return None on failure (caller decides how to record).
Endpoint resolution: $MATERIALS_MCP_BASE_URL/<service>/mcp, overridable
via MCP_<NAME>_URL per service.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any


def _base_url() -> str:
    base = os.environ.get("MATERIALS_MCP_BASE_URL", "").rstrip("/")
    if not base:
        raise RuntimeError("MATERIALS_MCP_BASE_URL unset")
    return base


def _endpoint(service: str, env_override: str) -> str:
    override = os.environ.get(env_override, "").strip()
    if override:
        return override
    return f"{_base_url()}/{service}/mcp"


def _http_post_json(url: str, payload: dict, timeout: float = 120.0) -> dict | None:
    """POST JSON to MCP endpoint, return parsed response or None on failure."""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
    except (urllib.error.URLError, TimeoutError, OSError):
        return None
    # MCP returns either JSON or SSE-streamed JSON; extract the result field.
    return _parse_mcp_response(raw)


def _parse_mcp_response(raw: str) -> dict | None:
    """Extract the JSON result from an MCP HTTP response (handles SSE framing)."""
    if not raw.strip():
        return None
    # Try plain JSON first
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    # SSE framing: look for `data: {...}` lines
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            try:
                return json.loads(line[5:].strip())
            except json.JSONDecodeError:
                continue
    return None


def _unwrap(mcp_response: dict | None) -> dict | None:
    """MCP wraps results in {jsonrpc, id, result}. Drill into result.content[0].text -> JSON."""
    if not mcp_response:
        return None
    if "result" not in mcp_response:
        return mcp_response  # already unwrapped
    result = mcp_response["result"]
    if isinstance(result, dict) and "content" in result:
        for item in result["content"]:
            if isinstance(item, dict) and item.get("type") == "text":
                try:
                    return json.loads(item["text"])
                except json.JSONDecodeError:
                    continue
    return result


# ============================================================================
# DiffCSP — formula → CIF
# ============================================================================

def call_diffcsp(formula: str, model_name: str = "mp_csp", num_evals: int = 1) -> dict | None:
    """Returns {"cif": str} or None."""
    url = _endpoint("diffcsp-generate", "MCP_DIFFCSP_URL")
    payload = {
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "generate_structures",
                   "arguments": {"formula": formula, "model_name": model_name, "num_evals": num_evals}},
    }
    resp = _unwrap(_http_post_json(url, payload, timeout=180.0))
    if not resp:
        return None
    structures = resp.get("structures") or []
    if not structures:
        return None
    cif = structures[0].get("cif")
    if not cif:
        return None
    return {"cif": cif}


# ============================================================================
# MatterSim relax — CIF → relaxed_CIF + energy
# ============================================================================

def call_mattersim_relax(cif_content: str, max_steps: int = 50) -> dict | None:
    url = _endpoint("mattersim-relax", "MCP_RELAX_URL")
    payload = {
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "relax_structure",
                   "arguments": {"cif_content": cif_content, "max_steps": max_steps}},
    }
    resp = _unwrap(_http_post_json(url, payload, timeout=300.0))
    if not resp:
        return None
    if "error" in resp or resp.get("value") is None:
        return None
    try:
        return {
            "relaxed_cif": resp["relaxed_cif"],
            "energy_per_atom_eV": float(resp["energy_per_atom_eV"]),
            "f_max_eV_A": float(resp["f_max_eV_A"]),
            "num_atoms": int(resp["num_atoms"]),
            "total_energy_eV": float(resp["energy_per_atom_eV"]) * int(resp["num_atoms"]),
        }
    except (KeyError, TypeError, ValueError):
        return None


# ============================================================================
# ALIGNN — CIF → property values
# ============================================================================

def call_alignn(cif_content: str, models: list[str]) -> dict | None:
    """Returns {<model>: {value, note?, error?}} or None on transport failure."""
    url = _endpoint("alignn", "MCP_ALIGNN_URL")
    payload = {
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "predict_alignn_properties",
                   "arguments": {"cif_content": cif_content, "models": models}},
    }
    resp = _unwrap(_http_post_json(url, payload, timeout=120.0))
    if not resp:
        return None
    return resp.get("properties") or {}


# ============================================================================
# MatterGen evaluate — async batch novelty/stability
# ============================================================================

def submit_mattergen_eval(structures: list[dict], energies: list[float]) -> str | None:
    """Returns job_id or None."""
    url = _endpoint("mattergen-evaluate", "MCP_EVALUATE_URL")
    payload = {
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "evaluate_structures_async",
                   "arguments": {"structures": structures, "relax": False,
                                 "energies": energies, "energy_correction_scheme": "MP2020",
                                 "structure_matcher": "disordered"}},
    }
    resp = _unwrap(_http_post_json(url, payload, timeout=60.0))
    if not resp:
        return None
    return resp.get("job_id")


def drain_mattergen_eval(job_id: str) -> dict | None:
    """Poll once. Returns {status, metrics?} or None on transport error."""
    url = _endpoint("mattergen-evaluate", "MCP_EVALUATE_URL")
    payload = {
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "get_evaluation_result",
                   "arguments": {"job_id": job_id}},
    }
    resp = _unwrap(_http_post_json(url, payload, timeout=30.0))
    if not resp:
        return None
    return {"status": resp.get("status", "unknown"), "metrics": resp.get("metrics"),
            "error": resp.get("error")}
