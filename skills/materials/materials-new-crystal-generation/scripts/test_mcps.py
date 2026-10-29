#!/usr/bin/env python3
"""
test_mcps.py — Comprehensive test suite for the two DEMO MCP servers.

Purpose
=======
Run this after every server-side change (code deploy, model reload, restart)
to catch regressions in the mattergen + crystal-property-prediction MCP HTTP services. The suite
exercises both endpoints and checks the four bugfixes that were applied
on 2026-06-17 (formula serialization, crystal_id population, hybrid fill,
status consistency).

No third-party dependencies — stdlib only. Copy this single file to any
machine that can reach the MCP HTTP endpoints and run it.

Endpoints
---------
    mattergen  http://<host>:<port>/mcp/mattergen/mcp   evolution loop
    cppred     http://<host>:<port>/mcp/flux/mcp        CIF → 9 properties

Usage
=====
    # Full suite (both MCPs + 1-generation evolution). ~3-6 min.
    python test_mcps.py

    # Quick mode — skip the slow evolution integration test.
    python test_mcps.py --skip-evolution

    # Flux only — useful when mattergen is being restarted.
    python test_mcps.py --cppred-only

    # Custom endpoints (e.g. pointing at a staging GPU box).
    python test_mcps.py --mattergen http://192.0.2.5:22222/mcp/mattergen/mcp \
                        --cppred      http://192.0.2.5:22222/mcp/flux/mcp

    # CI-friendly JSON report (in addition to human-readable stdout).
    python test_mcps.py --json report.json

    # Enumerate test names without running.
    python test_mcps.py --list

    # Show full server payloads for failing tests (debugging).
    python test_mcps.py --verbose

Other flags:
    --timeout 180              per-call HTTP timeout in seconds
    --evolution-timeout 900    poll deadline for autonomous evolution
    --model-path PATH          mattergen checkpoint dir (overrides server default)
    --cif-dir DIR              CIF fixture dir (repeatable)

Exit codes
----------
    0  every test passed
    1  one or more tests failed
    2  setup error (e.g. CIF fixtures not found)

Test inventory
==============
[cppred]
    cppred_health                       predict_flux_properties returns 9 sane fields
    cppred_cl_score_not_penalized       JACS ensemble loaded (cl_score != -1.0)
    cppred_crsite3_reference_values     known CrSiTe3 values inside expected bands
    cppred_multiple_cifs                N different CIFs return clean properties
    cppred_input_forms_equivalent       cif_content vs cif_base64 agree
    cppred_fields_subset                fields arg returns only requested subset
    cppred_bogus_input_returns_penalty  invalid CIF triggers penalty sentinels

[mattergen]
    mattergen_health                     evolution_service_info status=running
    mattergen_list_sessions              list_evolution_sessions returns a list
    mattergen_autonomous_evolution_submit  run_autonomous_evolution returns task_id
    mattergen_autonomous_evolution_complete  the submitted run reaches "completed"
    bug2_hybrid_fills_beyond_seeds       population grows beyond seed count
    bug3_formula_not_unknown             pareto entries carry real formulas
    bug4_crystal_id_in_both_views        crystal_id populated in BOTH population views
    bug5_status_consistency              result.status == list_sessions.status
    bug1_no_duplicate_pareto_entries     informational — bug #1 left intentionally unfixed
    mattergen_interactive_session_smoke  create/start/step/continue/delete lifecycle
    mattergen_cleanup_session            delete_evolution_session removes the test session

Background — the 5 mattergen MCP bugs (observed 2026-06-15)
===========================================================
The first 4 were fixed in the 2026-06-17 deploy; #1 was left in place
because it only reproduces with very small populations / few generations.

    #1  Duplicate entries in pareto_front / population — same crystal appears
        twice with different fitness values. Not fixed; suite reports INFO.
    #2  hybrid + seed_cif_files did not trigger MatterGen fill — population
        stayed at the seed count. Fixed; covered by bug2_hybrid_fills_beyond_seeds.
    #3  formula field returned "unknown" for every candidate. Fixed; covered
        by bug3_formula_not_unknown.
    #4  crystal_id was null in the full population view but populated in the
        simplified view (two different serialization paths). Fixed; covered
        by bug4_crystal_id_in_both_views.
    #5  Status race: get_autonomous_evolution_result flipped to "completed"
        before list_evolution_sessions caught up. Fixed; covered by
        bug5_status_consistency.

Troubleshooting
===============
- All cppred properties sane except cl_score=-1.0 → JACS ensemble failed to
  load inside the crystal-property-prediction MCP. Restart the crystal-property-prediction MCP process on the GPU box.
- mattergen_autonomous_evolution_submit fails with "未找到 config.yaml" →
  the default model_path on the server points at a directory missing
  config.yaml. Pass --model-path or fix the server-side MATTERGEN_MODEL_PATH.
- HTTP 502 from either endpoint → the FastMCP backend process crashed.
  SSH to the GPU box and restart it.
- Evolution stuck on "initializing" for >2 min → first generation is slow
  (cold MatterGen + JACS load); raise --evolution-timeout.

References
==========
- Skill doc:        .claude/skills/demo-emo-orchestrator/SKILL.md
- Tool contract:    .claude/skills/demo-emo-orchestrator/references/mcp_servers.md
- Property catalog: .claude/skills/demo-emo-orchestrator/references/property_catalog.md
- Smoke probe:      .claude/skills/demo-emo-orchestrator/scripts/probe_mcp.py
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Optional

# --------------------------------------------------------------------------- #
# Defaults
# --------------------------------------------------------------------------- #

DEFAULT_MATTERGEN_URL = "http://mcp.example.internal:22222/mcp/mattergen/mcp"
DEFAULT_CPPRED_URL = "http://mcp.example.internal:22222/mcp/flux/mcp"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# scripts/ -> demo-emo-orchestrator/ -> skills/ -> .claude/ -> project root
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(SCRIPT_DIR))))
DEFAULT_CIF_DIR = os.path.join(PROJECT_ROOT, "cif_files")
DEFAULT_MP_DIR = os.path.join(PROJECT_ROOT, "mp_candidates_v3_common", "cifs")

# CrSiTe3 reference bands — sourced from references/mcp_servers.md (cl_score=0.786,
# bandgap ~0.6 eV) and widened to tolerate ALIGNN weight drift across restarts.
CRSITE3_BANDS = {
    "bandgap_ev": (0.2, 1.5),
    "cl_score": (0.5, 0.95),
    "hull_ev": (-1.0, 5.0),
    "formation_energy_ev": (-2.0, 0.5),
    "f_max_ev_a": (-0.1, 1.5),
    "exfoliation_energy_mev": (-10.0, 200.0),
}

# Penalty sentinels — see references/mcp_servers.md "Penalty values".
PENALTY_VALUES = {
    "bandgap_ev": 999.0,
    "formation_energy_ev": 999.0,
    "hull_ev": 999.0,
    "exfoliation_energy_mev": 9999.0,
    "f_max_ev_a": 999.0,
    "cl_score": -1.0,
    "sg_number": -1,
}

ALL_CPPRED_FIELDS = [
    "bandgap_ev", "formation_energy_ev", "hull_ev",
    "exfoliation_energy_mev", "f_max_ev_a", "cl_score",
    "num_elements", "element_set", "sg_number",
]


# --------------------------------------------------------------------------- #
# ANSI colors (optional — disabled if not a TTY)
# --------------------------------------------------------------------------- #

_IS_TTY = hasattr(sys.stderr, "isatty") and sys.stderr.isatty()


def _c(code: str, text: str) -> str:
    if not _IS_TTY:
        return text
    return f"\033[{code}m{text}\033[0m"


def _green(s: str) -> str: return _c("32", s)
def _red(s: str) -> str:   return _c("31", s)
def _yellow(s: str) -> str: return _c("33", s)
def _dim(s: str) -> str:   return _c("2", s)


# --------------------------------------------------------------------------- #
# HTTP / JSON-RPC helper
# --------------------------------------------------------------------------- #

class MCPError(RuntimeError):
    pass


def call_mcp(url: str, tool: str, arguments: dict, timeout: float) -> dict:
    """Call a streamable-http MCP tool and return the parsed payload."""
    payload = {
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": tool, "arguments": arguments},
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        raise MCPError(f"HTTP {e.code}: {e.read().decode('utf-8', errors='replace')[:300]}")
    except Exception as e:
        raise MCPError(f"network error: {e}")

    if "data:" in body:
        lines = [ln for ln in body.splitlines() if ln.startswith("data:")]
        if not lines:
            raise MCPError(f"SSE response with no data: {body[:200]}")
        envelope = json.loads(lines[-1][len("data:"):].strip())
    else:
        envelope = json.loads(body)

    if "error" in envelope:
        raise MCPError(f"JSON-RPC error from {tool}: {envelope['error']}")

    result = envelope.get("result", {})
    if result.get("isError"):
        raise MCPError(f"{tool} returned isError=true: {result}")
    text = ""
    for item in result.get("content", []):
        if item.get("type") == "text":
            text = item.get("text", "")
            break
    if not text:
        return result
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"raw": text}


# --------------------------------------------------------------------------- #
# CIF helpers
# --------------------------------------------------------------------------- #

def load_cif_content(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def load_cif_b64(path: str) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("ascii")


def resolve_fixture(name: str, dirs: list[str]) -> str:
    for d in dirs:
        p = os.path.join(d, name)
        if os.path.isfile(p):
            return p
    raise FileNotFoundError(f"CIF fixture '{name}' not in: {dirs}")


# --------------------------------------------------------------------------- #
# Test result tracking
# --------------------------------------------------------------------------- #

@dataclass
class TestResult:
    name: str
    passed: bool
    detail: str = ""
    elapsed: float = 0.0
    payload: Optional[Any] = None  # last response, for verbose dump
    hint: str = ""                 # likely cause / next step when failed


@dataclass
class TestSuite:
    results: list[TestResult] = field(default_factory=list)
    verbose: bool = False

    def record(self, name: str, passed: bool, detail: str = "",
               elapsed: float = 0.0, payload: Any = None,
               hint: str = "") -> None:
        self.results.append(TestResult(name, passed, detail, elapsed, payload, hint))
        marker = _green("PASS") if passed else _red("FAIL")
        print(f"  [{marker}] {name}  ({elapsed:.1f}s)")
        if detail:
            print(_dim(f"         {detail}"))
        if not passed:
            if hint:
                print(_yellow(f"         hint: {hint}"))
            if self.verbose and payload is not None:
                print(_dim("         payload:"))
                print(_dim(json.dumps(payload, indent=2, ensure_ascii=False)[:2000]))

    @property
    def passed_count(self) -> int: return sum(1 for r in self.results if r.passed)
    @property
    def failed_count(self) -> int: return sum(1 for r in self.results if not r.passed)

    def print_summary(self) -> None:
        total = len(self.results)
        print()
        print("=" * 72)
        line = f"{self.passed_count}/{total} passed"
        if self.failed_count == 0:
            print(_green(f"ALL TESTS PASSED — {line}"))
        else:
            print(_red(f"SOME TESTS FAILED — {line}"))
            for r in self.results:
                if not r.passed:
                    print(_red(f"  ✗ {r.name}"))
                    print(_dim(f"      {r.detail}"))
                    if r.hint:
                        print(_yellow(f"      hint: {r.hint}"))
        print("=" * 72)

    def to_json(self) -> dict:
        return {
            "summary": {
                "total": len(self.results),
                "passed": self.passed_count,
                "failed": self.failed_count,
            },
            "results": [
                {
                    "name": r.name,
                    "passed": r.passed,
                    "detail": r.detail,
                    "hint": r.hint,
                    "elapsed_seconds": round(r.elapsed, 2),
                }
                for r in self.results
            ],
        }


# --------------------------------------------------------------------------- #
# Crystal-property-prediction MCP tests
# --------------------------------------------------------------------------- #

def test_cppred_health(suite: TestSuite, cppred_url: str, timeout: float) -> dict:
    """Default CIF (CrSiTe3) returns all 9 properties with sane structure."""
    name = "cppred_health"
    t0 = time.time()
    try:
        out = call_mcp(cppred_url, "predict_flux_properties",
                       {"fields": ALL_CPPRED_FIELDS}, timeout)
    except Exception as e:
        suite.record(name, False, f"call failed: {e}", time.time() - t0,
                     hint="crystal-property-prediction MCP unreachable — check the FastMCP process on the GPU box "
                          "and verify the URL/port. nginx 502 = backend crashed.")
        return {}
    elapsed = time.time() - t0
    props = out.get("properties") or {}
    missing = [f for f in ALL_CPPRED_FIELDS if f not in props]
    if missing:
        suite.record(name, False, f"missing fields: {missing}", elapsed, out,
                     hint="predict_flux_properties changed its response shape — "
                          "regression in the crystal-property-prediction MCP serialization layer.")
        return out
    suite.record(name, True, f"returned {len(props)} fields for {out.get('name')}", elapsed, out)
    return out


def test_cppred_cl_score_not_penalized(suite: TestSuite, cppred_url: str, timeout: float) -> None:
    """JACS ensemble must be loaded — cl_score should NOT be the -1.0 penalty."""
    name = "cppred_cl_score_not_penalized"
    t0 = time.time()
    try:
        out = call_mcp(cppred_url, "predict_flux_properties",
                       {"fields": ["cl_score"]}, timeout)
    except Exception as e:
        suite.record(name, False, f"call failed: {e}", time.time() - t0,
                     hint="crystal-property-prediction MCP unreachable — see cppred_health hint.")
        return
    elapsed = time.time() - t0
    cl = (out.get("properties") or {}).get("cl_score")
    if cl is None:
        suite.record(name, False, "cl_score field missing", elapsed, out,
                     hint="response schema regression — cl_score should always be present.")
        return
    if cl == PENALTY_VALUES["cl_score"]:
        suite.record(name, False,
                     "cl_score=-1.0 (JACS failed to load)", elapsed, out,
                     hint="Restart the crystal-property-prediction MCP process. The 100-bag CGCNN ensemble in "
                          "DEMO/PUCGCNN/ failed to initialize on this boot.")
        return
    if not (0.0 <= cl <= 1.0):
        suite.record(name, False, f"cl_score out of [0,1]: {cl}", elapsed, out,
                     hint="cl_score must be a probability in [0,1]. "
                          "JACS returned a garbage value — investigate the ensemble.")
        return
    suite.record(name, True, f"cl_score={cl:.4f}", elapsed, out)


def test_cppred_crsite3_reference_values(suite: TestSuite, cppred_url: str, timeout: float,
                                       cif_path: str) -> None:
    """Known CrSiTe3 values inside expected bands."""
    name = "cppred_crsite3_reference_values"
    t0 = time.time()
    try:
        out = call_mcp(cppred_url, "predict_flux_properties",
                       {"cif_base64": load_cif_b64(cif_path),
                        "name": "CrSiTe3.cif"}, timeout)
    except Exception as e:
        suite.record(name, False, f"call failed: {e}", time.time() - t0,
                     hint="crystal-property-prediction MCP unreachable — see cppred_health hint.")
        return
    elapsed = time.time() - t0
    props = out.get("properties") or {}
    failures = []
    for key, (lo, hi) in CRSITE3_BANDS.items():
        v = props.get(key)
        if v is None:
            failures.append(f"{key} missing")
        elif not (lo <= v <= hi):
            failures.append(f"{key}={v} outside [{lo},{hi}]")
    elements = props.get("element_set") or []
    if set(elements) != {"Cr", "Si", "Te"}:
        failures.append(f"element_set={elements} != [Cr,Si,Te]")
    if failures:
        suite.record(name, False, "; ".join(failures), elapsed, out,
                     hint="CrSiTe3 is the calibration anchor. If only one property "
                          "drifted, the corresponding model (ALIGNN/CHGNet) reloaded "
                          "different weights. If many drift, the pipeline is broken.")
    else:
        suite.record(name, True,
                     f"Eg={props.get('bandgap_ev'):.3f} cl={props.get('cl_score'):.3f} "
                     f"sg={props.get('sg_number')}", elapsed, out)


def test_cppred_multiple_cifs(suite: TestSuite, cppred_url: str, timeout: float,
                            cif_paths: list[str]) -> None:
    """Run cppred on N CIFs — all must return real properties (no penalties)."""
    name = "cppred_multiple_cifs"
    t0 = time.time()
    fails = []
    oks = 0
    for p in cif_paths:
        try:
            out = call_mcp(cppred_url, "predict_flux_properties",
                           {"cif_base64": load_cif_b64(p),
                            "name": os.path.basename(p)}, timeout)
        except Exception as e:
            fails.append(f"{os.path.basename(p)}: {e}")
            continue
        props = out.get("properties") or {}
        penalized = [k for k, pen in PENALTY_VALUES.items() if props.get(k) == pen]
        if penalized:
            fails.append(f"{os.path.basename(p)}: penalties {penalized}")
        else:
            oks += 1
    elapsed = time.time() - t0
    if fails:
        suite.record(name, False,
                     f"{len(fails)}/{len(cif_paths)} failed — {'; '.join(fails[:3])}",
                     elapsed,
                     hint="If all CIFs hit the same penalty (e.g. cl_score), "
                          "the underlying model is broken. If only some, inspect "
                          "those CIFs for format issues.")
    else:
        suite.record(name, True, f"{oks}/{len(cif_paths)} CIFs returned clean properties", elapsed)


def test_cppred_input_forms_equivalent(suite: TestSuite, cppred_url: str, timeout: float,
                                     cif_path: str) -> None:
    """cif_content and cif_base64 should return equivalent bandgap/hull/cl_score."""
    name = "cppred_input_forms_equivalent"
    t0 = time.time()
    try:
        a = call_mcp(cppred_url, "predict_flux_properties",
                     {"cif_content": load_cif_content(cif_path),
                      "name": "via_content", "fields": ["bandgap_ev", "hull_ev", "cl_score"]},
                     timeout)
        b = call_mcp(cppred_url, "predict_flux_properties",
                     {"cif_base64": load_cif_b64(cif_path),
                      "name": "via_base64", "fields": ["bandgap_ev", "hull_ev", "cl_score"]},
                     timeout)
    except Exception as e:
        suite.record(name, False, f"call failed: {e}", time.time() - t0,
                     hint="crystal-property-prediction MCP unreachable — see cppred_health hint.")
        return
    elapsed = time.time() - t0
    pa, pb = a.get("properties", {}), b.get("properties", {})
    # Tolerance: ALIGNN inference has minor GPU non-determinism (~1e-4 typical).
    # Anything beyond 1e-2 absolute is a real divergence worth flagging.
    TOL = 1e-2
    diffs = []
    for k in ("bandgap_ev", "hull_ev", "cl_score"):
        va, vb = pa.get(k), pb.get(k)
        if va is None or vb is None:
            diffs.append(f"{k} missing")
        elif abs(va - vb) > TOL:
            diffs.append(f"{k}: content={va} vs base64={vb} (Δ={abs(va-vb):.4f})")
    if diffs:
        suite.record(name, False, "; ".join(diffs), elapsed, {"content": a, "base64": b},
                     hint="Large divergence between cif_content and cif_base64 paths. "
                          "Likely a base64 decode bug, wrong default encoding, or "
                          "different MatterSim relaxation seed.")
    else:
        suite.record(name, True,
                     f"content & base64 agree (within Δ<{TOL})", elapsed)


def test_cppred_fields_subset(suite: TestSuite, cppred_url: str, timeout: float,
                            cif_path: str) -> None:
    """fields=['bandgap_ev'] should return only that field."""
    name = "cppred_fields_subset"
    t0 = time.time()
    try:
        out = call_mcp(cppred_url, "predict_flux_properties",
                       {"cif_base64": load_cif_b64(cif_path),
                        "name": "subset_test",
                        "fields": ["bandgap_ev"]}, timeout)
    except Exception as e:
        suite.record(name, False, f"call failed: {e}", time.time() - t0,
                     hint="crystal-property-prediction MCP unreachable — see cppred_health hint.")
        return
    elapsed = time.time() - t0
    props = out.get("properties") or {}
    bg = props.get("bandgap_ev")
    if bg is None:
        suite.record(name, False, "bandgap_ev missing despite fields request", elapsed, out,
                     hint="The fields=[] filter regressed — server stopped honoring it.")
        return
    if bg == PENALTY_VALUES["bandgap_ev"]:
        suite.record(name, False, f"bandgap_ev={bg} is penalty sentinel", elapsed, out,
                     hint="ALIGNN failed for this CIF — check MatterSim relax output.")
        return
    suite.record(name, True, f"bandgap_ev={bg:.3f}", elapsed, out)


def test_cppred_bogus_input_returns_penalty(suite: TestSuite, cppred_url: str,
                                          timeout: float) -> None:
    """Garbage CIF input should trigger penalty sentinels, not crash."""
    name = "cppred_bogus_input_returns_penalty"
    t0 = time.time()
    garbage = "this is not a CIF at all\nblah blah\n"
    try:
        out = call_mcp(cppred_url, "predict_flux_properties",
                       {"cif_content": garbage, "name": "bogus",
                        "fields": ["bandgap_ev", "cl_score"]}, timeout)
    except MCPError as e:
        suite.record(name, True, f"server rejected bad CIF: {str(e)[:80]}", time.time() - t0)
        return
    except Exception as e:
        suite.record(name, False, f"unexpected exception: {e}", time.time() - t0,
                     hint="Server raised an unhandled exception on bad input. "
                          "Wrap CIF parsing in try/except and return penalties.")
        return
    elapsed = time.time() - t0
    props = out.get("properties") or {}
    bg = props.get("bandgap_ev")
    cl = props.get("cl_score")
    penalized = (bg == PENALTY_VALUES["bandgap_ev"]) or (cl == PENALTY_VALUES["cl_score"])
    if penalized:
        suite.record(name, True, f"penalty returned (bg={bg}, cl={cl})", elapsed, out)
    else:
        suite.record(name, False,
                     f"expected penalty for garbage input, got bg={bg} cl={cl}", elapsed, out,
                     hint="Garbage CIF should not produce real-looking values. "
                          "Input validation is missing or broken.")


# --------------------------------------------------------------------------- #
# Mattergen MCP tests
# --------------------------------------------------------------------------- #

def test_mattergen_health(suite: TestSuite, mg_url: str, timeout: float) -> dict:
    name = "mattergen_health"
    t0 = time.time()
    try:
        out = call_mcp(mg_url, "evolution_service_info", {}, timeout)
    except Exception as e:
        suite.record(name, False, f"call failed: {e}", time.time() - t0,
                     hint="mattergen MCP unreachable — check the FastMCP process and "
                          "confirm the URL/port. nginx 502 = backend crashed.")
        return {}
    elapsed = time.time() - t0
    if out.get("status") != "running":
        suite.record(name, False, f"status={out.get('status')} (expected 'running')", elapsed, out,
                     hint="Server is up but reports degraded — likely MatterGen checkpoint "
                          "failed to load. Check mattergen MCP logs.")
        return out
    suite.record(name, True,
                 f"status=running active_sessions={out.get('active_sessions')}", elapsed, out)
    return out


def test_mattergen_list_sessions(suite: TestSuite, mg_url: str, timeout: float) -> list:
    name = "mattergen_list_sessions"
    t0 = time.time()
    try:
        out = call_mcp(mg_url, "list_evolution_sessions", {}, timeout)
    except Exception as e:
        suite.record(name, False, f"call failed: {e}", time.time() - t0,
                     hint="mattergen MCP unreachable — see mattergen_health hint.")
        return []
    elapsed = time.time() - t0
    if isinstance(out, list):
        sessions = out
    elif isinstance(out, dict):
        sessions = out.get("sessions") or out.get("data") or []
    else:
        sessions = []
    if not isinstance(sessions, list):
        suite.record(name, False, f"unexpected payload: {out!r}", elapsed, out,
                     hint="list_evolution_sessions response shape changed — "
                          "regression in serialization layer.")
        return []
    suite.record(name, True, f"{len(sessions)} session(s)", elapsed, out)
    return sessions


def _submit_hybrid_evolution(mg_url: str, timeout: float, seed_paths: list[str],
                             model_path: str, session_name: str) -> dict:
    """Submit a tiny hybrid evolution and return the submission response."""
    seeds = [{"name": os.path.basename(p), "cif_base64": load_cif_b64(p)} for p in seed_paths]
    args = {
        "session_name": session_name,
        "n_pops": 8,
        "max_generations": 1,
        "n_offspring_per_generation": 8,
        "noise_level": 0.3,
        "emo_engine": "TriplePopCMOEA",
        # Avoid cl_score as a constraint — keep test robust to JACS state.
        # Objectives use the lowercase _ev suffix that both servers accept.
        "objectives_json": json.dumps({"hull_ev": "minimize", "bandgap_ev": "maximize"}),
        "constraints_json": json.dumps({
            "bandgap_eV": {"min": 0.3, "max": 6.0, "weight": 5.0},
            "formation_energy_eV": {"max": 1.0, "weight": 10.0},
            "num_elements": {"min": 2, "max": 4, "weight": 5.0},
        }),
        "initial_pool_mode": "hybrid",
        "seed_cif_files": json.dumps(seeds),
        "batch_size": 4,
        "max_attempts": 10,
    }
    if model_path:
        args["model_path"] = model_path
    return call_mcp(mg_url, "run_autonomous_evolution", args, timeout)


def _poll_until_done(mg_url: str, task_id: str, timeout: float, deadline: float) -> dict:
    """Poll autonomous evolution; returns the last response."""
    last = {}
    while time.time() < deadline:
        r = call_mcp(mg_url, "get_autonomous_evolution_result",
                     {"task_id": task_id, "top_pareto": 20}, timeout)
        last = r
        st = r.get("status")
        if st in ("completed", "error", "failed"):
            return r
        time.sleep(20)
    return last


def run_evolution_integration(suite: TestSuite, mg_url: str, timeout: float,
                              evolution_timeout: float, seed_paths: list[str],
                              model_path: str) -> tuple[str, str, dict]:
    """
    Submit + poll a tiny hybrid evolution. Returns (session_id, task_id, final_result).
    Records one test for submission + one for completion.
    """
    submit_name = "mattergen_autonomous_evolution_submit"
    poll_name = "mattergen_autonomous_evolution_complete"

    t0 = time.time()
    try:
        sub = _submit_hybrid_evolution(mg_url, timeout, seed_paths, model_path,
                                       "mcp_test_suite_evolution")
    except Exception as e:
        suite.record(submit_name, False, f"submit failed: {e}", time.time() - t0,
                     hint="If the error mentions config.yaml, the server's default "
                          "MATTERGEN_MODEL_PATH is wrong — pass --model-path or fix "
                          "the env var on the GPU box.")
        return "", "", {}
    elapsed = time.time() - t0
    task_id = sub.get("task_id")
    session_id = sub.get("session_id")
    if not task_id:
        suite.record(submit_name, False, f"no task_id in response: {sub}", elapsed, sub,
                     hint="run_autonomous_evolution response schema changed.")
        return "", "", {}
    suite.record(submit_name, True,
                 f"task_id={task_id} session_id={session_id or '(deferred)'}", elapsed, sub)

    # Poll
    t0 = time.time()
    deadline = time.time() + evolution_timeout
    last = {}
    while time.time() < deadline:
        try:
            r = call_mcp(mg_url, "get_autonomous_evolution_result",
                         {"task_id": task_id, "top_pareto": 20}, timeout)
        except Exception as e:
            suite.record(poll_name, False, f"poll error: {e}", time.time() - t0,
                         hint="Transient network issue or server crashed mid-run.")
            return session_id or "", task_id, {}
        last = r
        st = r.get("status")
        if st == "completed":
            suite.record(poll_name, True,
                         f"completed gen={r.get('total_generations')} "
                         f"qualified={r.get('total_qualified')}",
                         time.time() - t0, r)
            return session_id or r.get("session_id") or "", task_id, r
        if st in ("error", "failed"):
            err = r.get("error") or r.get("message") or "unknown error"
            suite.record(poll_name, False, f"status={st}: {err}", time.time() - t0, r,
                         hint="Read the error message — common causes: "
                          "bad model_path, JACS port conflict, constraint schema typo.")
            return session_id or "", task_id, r
        time.sleep(20)
    suite.record(poll_name, False,
                 f"timed out after {evolution_timeout}s (last status={last.get('status')})",
                 time.time() - t0, last,
                 hint="First generation is slow on a cold GPU (MatterGen + JACS load). "
                      "Raise --evolution-timeout, or run a warm-up evolution first.")
    return session_id or "", task_id, last


def test_bug2_hybrid_fills_beyond_seeds(suite: TestSuite, result: dict,
                                        n_seeds: int) -> None:
    """Bug #2: hybrid mode + seed_cif_files should fill population beyond seed count."""
    name = "bug2_hybrid_fills_beyond_seeds"
    if result.get("status") != "completed":
        suite.record(name, False, "skipped — evolution did not complete")
        return
    pop_size = result.get("population_size")
    pareto = result.get("pareto_front") or []
    candidates = pop_size
    if candidates is None:
        candidates = len({p.get("name") or p.get("crystal_id") for p in pareto}) or None
    if candidates is None:
        suite.record(name, False, "no population_size / pareto entries to inspect", 0.0, result,
                     hint="Response missing population_size — schema regression.")
        return
    if candidates <= n_seeds:
        suite.record(name, False,
                     f"population_size={candidates} <= n_seeds={n_seeds} (hybrid fill broken)",
                     0.0, result,
                     hint="The cif_plus_model_fill branch from Evoloop.py:294-313 is "
                          "not wired to the seed_cif_files path. Check the "
                          "initial_pool_mode dispatcher in the mattergen MCP.")
        return
    suite.record(name, True,
                 f"population_size={candidates} > n_seeds={n_seeds} (hybrid fill works)",
                 0.0, result)


def test_bug3_formula_not_unknown(suite: TestSuite, result: dict) -> None:
    """Bug #3: formula field should NOT be 'unknown'."""
    name = "bug3_formula_not_unknown"
    if result.get("status") != "completed":
        suite.record(name, False, "skipped — evolution did not complete")
        return
    pareto = result.get("pareto_front") or []
    if not pareto:
        suite.record(name, False, "empty pareto_front — cannot evaluate", 0.0, result,
                     hint="Zero qualified candidates. Loosen constraints or raise n_pops.")
        return
    unknowns = [p for p in pareto if (p.get("formula") in (None, "unknown", ""))]
    if unknowns:
        suite.record(name, False,
                     f"{len(unknowns)}/{len(pareto)} entries have formula='unknown'",
                     0.0, result,
                     hint="_simplify_crystal is not deriving formula from element_set. "
                          "Fix the serialization helper in the mattergen MCP.")
        return
    formulas = [p.get("formula") for p in pareto[:5]]
    suite.record(name, True, f"first formulas: {formulas}", 0.0, result)


def test_bug4_crystal_id_in_both_views(suite: TestSuite, mg_url: str, timeout: float,
                                       session_id: str) -> None:
    """Bug #4: crystal_id populated in BOTH simplified and full population views."""
    name = "bug4_crystal_id_in_both_views"
    if not session_id:
        suite.record(name, False, "skipped — no session_id")
        return
    t0 = time.time()
    try:
        full = call_mcp(mg_url, "get_evolution_population",
                        {"session_id": session_id, "simplified": False}, timeout)
        simp = call_mcp(mg_url, "get_evolution_population",
                        {"session_id": session_id, "simplified": True}, timeout)
    except Exception as e:
        suite.record(name, False, f"call failed: {e}", time.time() - t0,
                     hint="get_evolution_population unreachable — see mattergen_health hint.")
        return
    elapsed = time.time() - t0

    def _pop(resp):
        if isinstance(resp, list): return resp
        if isinstance(resp, dict):
            for k in ("population", "data", "crystals"):
                v = resp.get(k)
                if isinstance(v, list): return v
        return []

    full_pop = _pop(full)
    simp_pop = _pop(simp)
    if not full_pop or not simp_pop:
        suite.record(name, False,
                     f"empty population (full={len(full_pop)} simp={len(simp_pop)})",
                     elapsed, {"full": full, "simplified": simp},
                     hint="Population fetch returned empty — session may be gone or "
                          "the response shape changed.")
        return
    full_nulls = sum(1 for c in full_pop if c.get("crystal_id") in (None, ""))
    simp_nulls = sum(1 for c in simp_pop if c.get("crystal_id") in (None, ""))
    if full_nulls or simp_nulls:
        suite.record(name, False,
                     f"null crystal_ids: full={full_nulls}/{len(full_pop)} "
                     f"simplified={simp_nulls}/{len(simp_pop)}",
                     elapsed, {"full_sample": full_pop[:2], "simp_sample": simp_pop[:2]},
                     hint="Two serialization paths in get_population — one drops "
                          "crystal_id. Unify them in the mattergen MCP.")
        return
    suite.record(name, True,
                 f"crystal_id present in all {len(full_pop)} full + {len(simp_pop)} simplified",
                 elapsed)


def test_bug5_status_consistency(suite: TestSuite, mg_url: str, timeout: float,
                                 session_id: str, result_status: str) -> None:
    """Bug #5: list_evolution_sessions status should agree with result.status."""
    name = "bug5_status_consistency"
    if not session_id or not result_status:
        suite.record(name, False, "skipped — no session_id or result status")
        return
    t0 = time.time()
    try:
        out = call_mcp(mg_url, "list_evolution_sessions", {}, timeout)
    except Exception as e:
        suite.record(name, False, f"call failed: {e}", time.time() - t0,
                     hint="mattergen MCP unreachable — see mattergen_health hint.")
        return
    elapsed = time.time() - t0
    sessions = out if isinstance(out, list) else (
        out.get("sessions") or out.get("data") or [] if isinstance(out, dict) else []
    )
    match = None
    for s in sessions:
        sid = s.get("session_id") or s.get("id")
        if sid == session_id:
            match = s
            break
    if match is None:
        suite.record(name, False,
                     f"session {session_id} not in list ({len(sessions)} sessions)",
                     elapsed, out,
                     hint="Session vanished from list before result was read — "
                          "or session_id naming is inconsistent between endpoints.")
        return
    list_status = match.get("status")
    if list_status != result_status:
        suite.record(name, False,
                     f"result.status={result_status} vs list.status={list_status}",
                     elapsed, match,
                     hint="_autonomous_tasks status flip is not propagating to the "
                          "session manager. Fix the state-machine bridge in the mattergen MCP.")
        return
    suite.record(name, True, f"both report status={list_status}", elapsed, match)


def test_bug1_no_duplicate_pareto_entries(suite: TestSuite, result: dict) -> None:
    """Bug #1 (not fixed — informational): duplicates flagged but not blocking."""
    name = "bug1_no_duplicate_pareto_entries"
    if result.get("status") != "completed":
        suite.record(name, False, "skipped — evolution did not complete")
        return
    pareto = result.get("pareto_front") or []
    if not pareto:
        suite.record(name, False, "empty pareto_front", 0.0, result)
        return
    names = [p.get("name") or p.get("crystal_id") for p in pareto]
    dup = sorted({n for n in names if n is not None and names.count(n) > 1})
    if dup:
        suite.record(name, True,
                     f"INFO: {len(dup)} duplicate name(s) (bug #1, known unfixed): {dup[:3]}",
                     0.0, result)
        return
    suite.record(name, True, f"no duplicates in {len(pareto)} pareto entries", 0.0, result)


def test_mattergen_interactive_session_smoke(suite: TestSuite, mg_url: str, timeout: float,
                                             model_path: str) -> str:
    """
    Exercise the interactive lifecycle: create → start → step → continue → delete.
    Returns the session_id (or "" on failure) so the caller can clean up.
    """
    name = "mattergen_interactive_session_smoke"
    t0 = time.time()
    session_id = ""
    try:
        args = {
            "mode": "interactive",
            "session_name": "mcp_test_suite_interactive",
            "n_pops": 4,
            "max_generations": 1,
            "n_offspring_per_generation": 4,
            "noise_level": 0.3,
            "emo_engine": "TriplePopCMOEA",
            "objectives_json": json.dumps({"hull_ev": "minimize", "bandgap_ev": "maximize"}),
            "constraints_json": json.dumps({"num_elements": {"min": 2, "max": 4, "weight": 5.0}}),
            "initial_pool_mode": "random",
            "batch_size": 2,
            "max_attempts": 5,
        }
        if model_path:
            args["model_path"] = model_path
        created = call_mcp(mg_url, "create_evolution_session", args, timeout)
        session_id = created.get("session_id") or ""
        if not session_id:
            suite.record(name, False, f"no session_id: {created}", time.time() - t0, created,
                         hint="create_evolution_session response schema changed.")
            return ""

        # Best-effort lifecycle — we don't require steps to complete, only that the
        # endpoints respond. This is a smoke test, not a full evolution.
        call_mcp(mg_url, "start_evolution",
                 {"session_id": session_id, "auto_continue": False}, timeout)
        # step may return immediately if paused at a non-matching boundary
        try:
            call_mcp(mg_url, "step_evolution",
                     {"session_id": session_id, "steps": 1,
                      "pause_at": "generation_end"}, timeout)
        except MCPError as e:
            # Some servers reject step if already running — that's OK for smoke.
            pass
        try:
            call_mcp(mg_url, "continue_evolution",
                     {"session_id": session_id}, timeout)
        except MCPError:
            pass
        suite.record(name, True, f"interactive lifecycle ok (session={session_id})",
                     time.time() - t0)
        return session_id
    except Exception as e:
        suite.record(name, False, f"lifecycle failed: {e}", time.time() - t0,
                     hint="One of create/start/step/continue failed. Check the "
                          "interactive-mode dispatcher in the mattergen MCP.")
        return session_id


def test_mattergen_cleanup_session(suite: TestSuite, mg_url: str, timeout: float,
                                   session_id: str) -> None:
    name = "mattergen_cleanup_session"
    if not session_id:
        suite.record(name, False, "skipped — no session_id to delete")
        return
    t0 = time.time()
    try:
        out = call_mcp(mg_url, "delete_evolution_session",
                       {"session_id": session_id}, timeout)
    except Exception as e:
        suite.record(name, False, f"delete failed: {e}", time.time() - t0,
                     hint="delete_evolution_session endpoint broken — session leaks.")
        return
    elapsed = time.time() - t0
    suite.record(name, True, f"delete_evolution_session returned: {out}", elapsed, out)


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="DEMO MCP test suite",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--mattergen", default=DEFAULT_MATTERGEN_URL,
                   help=f"mattergen MCP URL (default: {DEFAULT_MATTERGEN_URL})")
    p.add_argument("--cppred", default=DEFAULT_CPPRED_URL,
                   help=f"crystal-property-prediction MCP URL (default: {DEFAULT_CPPRED_URL})")
    p.add_argument("--timeout", type=float, default=180.0,
                   help="per-call HTTP timeout in seconds (default: 180)")
    p.add_argument("--evolution-timeout", type=float, default=900.0,
                   help="evolution poll deadline in seconds (default: 900)")
    p.add_argument("--cppred-only", action="store_true",
                   help="skip all mattergen tests")
    p.add_argument("--skip-evolution", action="store_true",
                   help="skip the slow mattergen evolution integration test "
                        "(also skips the bug-# tests)")
    p.add_argument("--skip-interactive", action="store_true",
                   help="skip the interactive session smoke test")
    p.add_argument("--model-path", default="",
                   help="mattergen checkpoint path; empty uses server default")
    p.add_argument("--cif-dir", action="append", default=[],
                   help="CIF fixture dir (repeatable); defaults to project cif_files/ "
                        "and mp_candidates_v3_common/cifs/")
    p.add_argument("--verbose", action="store_true",
                   help="dump full payloads for failed tests")
    p.add_argument("--json", metavar="PATH", default="",
                   help="also write a JSON report to PATH (CI-friendly)")
    p.add_argument("--list", action="store_true",
                   help="list all test names and exit")
    return p.parse_args()


ALL_TEST_NAMES = [
    "[cppred] cppred_health",
    "[cppred] cppred_cl_score_not_penalized",
    "[cppred] cppred_crsite3_reference_values",
    "[cppred] cppred_multiple_cifs",
    "[cppred] cppred_input_forms_equivalent",
    "[cppred] cppred_fields_subset",
    "[cppred] cppred_bogus_input_returns_penalty",
    "[mattergen] mattergen_health",
    "[mattergen] mattergen_list_sessions",
    "[mattergen] mattergen_autonomous_evolution_submit",
    "[mattergen] mattergen_autonomous_evolution_complete",
    "[mattergen] bug2_hybrid_fills_beyond_seeds",
    "[mattergen] bug3_formula_not_unknown",
    "[mattergen] bug4_crystal_id_in_both_views",
    "[mattergen] bug5_status_consistency",
    "[mattergen] bug1_no_duplicate_pareto_entries",
    "[mattergen] mattergen_interactive_session_smoke",
    "[mattergen] mattergen_cleanup_session",
]


def main() -> int:
    args = parse_args()

    if args.list:
        print("Tests in this suite:")
        for n in ALL_TEST_NAMES:
            print(f"  {n}")
        print(f"\nTotal: {len(ALL_TEST_NAMES)}")
        return 0

    cif_dirs = args.cif_dir or [DEFAULT_CIF_DIR, DEFAULT_MP_DIR]

    # Resolve fixtures up front so we fail fast if they're missing.
    try:
        crsite3 = resolve_fixture("CrSiTe3.cif", cif_dirs)
        extra_cifs = []
        for n in ("AlInSe3.cif",):
            try:
                extra_cifs.append(resolve_fixture(n, cif_dirs))
            except FileNotFoundError:
                pass
        try:
            extra_cifs.append(resolve_fixture("mp-555903_Al(IO3)3.cif", cif_dirs))
            extra_cifs.append(resolve_fixture("mp-1374068_Al2(WO4)3.cif", cif_dirs))
        except FileNotFoundError:
            pass
        multi_cifs = [crsite3] + extra_cifs
    except FileNotFoundError as e:
        print(_red(f"FIXTURE MISSING: {e}"))
        print(_dim(f"  looked in: {cif_dirs}"))
        print(_dim("  pass --cif-dir to point at your CIFs, or run from the project root."))
        return 2

    suite = TestSuite(verbose=args.verbose)

    print(_yellow("=" * 72))
    print(_yellow("DEMO MCP test suite"))
    print(_dim(f"  mattergen: {args.mattergen}"))
    print(_dim(f"  cppred:    {args.cppred}"))
    print(_dim(f"  fixtures:  {cif_dirs}"))
    print(_yellow("=" * 72))

    # ----------------------------------------------------------------- cppred
    print(_yellow("\n[crystal-property-prediction MCP]"))
    test_cppred_health(suite, args.cppred, args.timeout)
    test_cppred_cl_score_not_penalized(suite, args.cppred, args.timeout)
    test_cppred_crsite3_reference_values(suite, args.cppred, args.timeout, crsite3)
    if len(multi_cifs) >= 2:
        test_cppred_multiple_cifs(suite, args.cppred, args.timeout, multi_cifs[:4])
    test_cppred_input_forms_equivalent(suite, args.cppred, args.timeout, crsite3)
    test_cppred_fields_subset(suite, args.cppred, args.timeout, crsite3)
    test_cppred_bogus_input_returns_penalty(suite, args.cppred, args.timeout)

    # ------------------------------------------------------------ mattergen
    if args.cppred_only:
        print(_yellow("\n[mattergen MCP] -- skipped (--cppred-only)"))
    else:
        print(_yellow("\n[mattergen MCP]"))
        health = test_mattergen_health(suite, args.mattergen, args.timeout)
        test_mattergen_list_sessions(suite, args.mattergen, args.timeout)

        interactive_session = ""
        if (health.get("status") == "running" and not args.skip_evolution
                and not args.skip_interactive):
            print(_dim("  interactive session smoke test..."))
            interactive_session = test_mattergen_interactive_session_smoke(
                suite, args.mattergen, args.timeout, args.model_path,
            )
            if interactive_session:
                test_mattergen_cleanup_session(suite, args.mattergen, args.timeout,
                                               interactive_session)

        if health.get("status") == "running" and not args.skip_evolution:
            seeds = multi_cifs[:2] if len(multi_cifs) >= 2 else [multi_cifs[0]]
            print(_dim(f"  submitting 1-generation hybrid evolution with {len(seeds)} seed CIF(s)..."))
            session_id, task_id, result = run_evolution_integration(
                suite, args.mattergen, args.timeout,
                args.evolution_timeout, seeds, args.model_path,
            )
            if result.get("status") == "completed":
                test_bug2_hybrid_fills_beyond_seeds(suite, result, len(seeds))
                test_bug3_formula_not_unknown(suite, result)
                test_bug4_crystal_id_in_both_views(suite, args.mattergen, args.timeout, session_id)
                test_bug5_status_consistency(suite, args.mattergen, args.timeout,
                                             session_id, result.get("status"))
                test_bug1_no_duplicate_pareto_entries(suite, result)
            else:
                print(_dim("  -- bug tests skipped; evolution did not complete --"))
            test_mattergen_cleanup_session(suite, args.mattergen, args.timeout, session_id)
        elif args.skip_evolution:
            print(_dim("  -- evolution tests skipped (--skip-evolution) --"))

    suite.print_summary()

    if args.json:
        try:
            with open(args.json, "w", encoding="utf-8") as f:
                json.dump(suite.to_json(), f, indent=2, ensure_ascii=False)
            print(_dim(f"\nJSON report written to: {args.json}"))
        except Exception as e:
            print(_red(f"\nfailed to write JSON report: {e}"))

    return 0 if suite.failed_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
