#!/usr/bin/env python3
"""Compute magnetic density (μB / Å³) from a total magnetic moment and a CIF.

mag_density = total_magmom_μB / cell_volume_Å³

Cell volume comes from pymatgen.Structure. CIF can be passed inline
(--cif-content) or as a file path (--cif-path).

Output:
    {"mag_density": 0.341, "volume": 211.5, "n_atoms": 12, "ok": true}

Usage:
    python scripts/mag_density.py --magmom 24.0 --cif-path relaxed.cif
    python scripts/mag_density.py --magmom 24.0 --cif-content "$(cat r.cif)"
"""
from __future__ import annotations

import argparse
import json
import sys


def compute_mag_density(magmom: float, cif: str) -> dict:
    try:
        from pymatgen.core import Structure
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"import: {exc!r}"}

    try:
        struct = Structure.from_str(cif, fmt="cif")
        volume = float(struct.volume)
        n_atoms = len(struct)
        if volume <= 0:
            return {"ok": False, "error": f"non-positive volume {volume}"}
        return {
            "ok": True,
            "mag_density": float(magmom) / volume,
            "volume": volume,
            "n_atoms": n_atoms,
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": repr(exc)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--magmom", type=float, required=True, help="Total magnetic moment in μB")
    parser.add_argument("--cif-path", help="Path to a CIF file")
    parser.add_argument("--cif-content", help="Inline CIF text")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()

    if bool(args.cif_path) == bool(args.cif_content):
        print(json.dumps({"ok": False, "error": "pass exactly one of --cif-path / --cif-content"}))
        return 2

    cif = open(args.cif_path).read() if args.cif_path else args.cif_content
    result = compute_mag_density(args.magmom, cif)
    print(json.dumps(result, indent=2 if args.pretty else None))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
