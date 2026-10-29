#!/usr/bin/env python3
"""Compute HHI (Herfindahl-Hirschman Index) for a chemical formula.

Wraps pymatgen.analysis.hhi.HHIModel. Outputs JSON:

    {"formula": "Fe2O3", "hhi": 1828.9, "hhi_reserve": 1129.5, "ok": true}

On error (unknown element, parse fail):

    {"formula": "Fe2O3", "hhi": null, "ok": false, "error": "..."}

Usage:
    python scripts/hhi.py --formula Fe2O3
    python scripts/hhi.py --formula Nd2Fe14B --pretty
"""
from __future__ import annotations

import argparse
import json
import sys


def compute_hhi(formula: str) -> dict:
    try:
        from pymatgen.analysis.hhi import HHIModel
        from pymatgen.core import Composition
    except Exception as exc:  # noqa: BLE001
        return {"formula": formula, "hhi": None, "ok": False, "error": f"import: {exc!r}"}

    try:
        comp = Composition(formula)
        # HHIModel.get_hhi returns (production, reserve)
        hhi_prod, hhi_reserve = HHIModel().get_hhi(comp)
        return {
            "formula": formula,
            "hhi": float(hhi_prod),
            "hhi_reserve": float(hhi_reserve),
            "ok": True,
        }
    except Exception as exc:  # noqa: BLE001
        return {"formula": formula, "hhi": None, "ok": False, "error": repr(exc)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--formula", required=True, help="Chemical formula, e.g. Fe2O3")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()

    result = compute_hhi(args.formula)
    print(json.dumps(result, indent=2 if args.pretty else None))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
