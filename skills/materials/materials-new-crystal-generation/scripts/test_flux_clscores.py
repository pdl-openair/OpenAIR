#!/usr/bin/env python3
"""Test flux MCP cl_score on a sample of seed CIFs to confirm it works reliably."""
import base64
import json
import os
import urllib.request
from pathlib import Path

_MCP_BASE = os.environ.get("MATERIALS_MCP_BASE_URL", "").rstrip("/")
FLUX_URL = f"{_MCP_BASE}/flux/mcp" if _MCP_BASE else ""


def call(name, args, timeout=300, retries=3):
    import time as _t
    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                FLUX_URL,
                data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                 "params": {"name": name, "arguments": args}}).encode(),
                headers={"Content-Type": "application/json",
                         "Accept": "application/json, text/event-stream"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read().decode()
            if "data:" in body:
                for ln in body.splitlines():
                    if ln.startswith("data:"):
                        return json.loads(ln[5:].strip())
            return json.loads(body)
        except Exception as e:
            last_err = e
            print(f"    (retry {attempt+1}/{retries}: {type(e).__name__}: {e})")
            _t.sleep(5 * (attempt + 1))
    raise last_err


def main():
    seed_dir = Path("mp_candidates_v3_common/cifs")
    # Pick a diverse sample of 8 CIFs
    all_cifs = sorted(seed_dir.glob("*.cif"))
    sample = all_cifs[:8]
    import time as _t
    print(f"Testing flux MCP predict_flux_properties on {len(sample)} CIFs\n")
    print(f"{'name':<40} {'cl':>7} {'Eg':>6} {'Eform':>7} {'ehull':>6} {'ne':>3} {'els'}")
    print("-" * 100)

    cl_scores = []
    for cif in sample:
        b64 = base64.b64encode(cif.read_bytes()).decode()
        r = call("predict_flux_properties",
                 {"cif_base64": b64, "name": cif.name,
                  "fields": ["cl_score", "bandgap_ev", "formation_energy_ev",
                             "hull_ev", "num_elements", "element_set"]})
        text = r["result"]["content"][0]["text"]
        data = json.loads(text)
        p = data.get("properties", {})
        cl = p.get("cl_score")
        bg = p.get("bandgap_ev")
        fe = p.get("formation_energy_ev")
        eh = p.get("hull_ev")
        ne = p.get("num_elements")
        els = ",".join(p.get("element_set", []))
        cl_scores.append(cl)
        print(f"{cif.name[:38]:<40} {cl:>7.3f} {bg:>6.2f} {fe:>7.3f} {eh:>6.2f} {ne:>3} {els}", flush=True)
        _t.sleep(2)

    print()
    n_valid = sum(1 for c in cl_scores if isinstance(c, (int, float)) and 0 <= c <= 1)
    n_broken = sum(1 for c in cl_scores if c == -1.0)
    print(f"Valid cl_score (0..1): {n_valid}/{len(cl_scores)}")
    print(f"Broken (-1.0): {n_broken}/{len(cl_scores)}")
    if n_valid:
        avg = sum(c for c in cl_scores if isinstance(c, (int, float)) and c != -1.0) / n_valid
        above_05 = sum(1 for c in cl_scores if isinstance(c, (int, float)) and c >= 0.5)
        print(f"avg cl_score (valid): {avg:.3f}")
        print(f"passing cl_score>=0.5: {above_05}/{n_valid}")


if __name__ == "__main__":
    main()
