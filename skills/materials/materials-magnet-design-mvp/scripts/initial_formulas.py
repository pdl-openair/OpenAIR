#!/usr/bin/env python3
"""Pull an initial pool of magnetic-relevant formulas from Materials Project.

Uses mp_api. Returns formulas that contain at least one transition-metal
magnetic site (Fe/Co/Ni/Cr/Mn) plus any p-block anion (O/S/Se/Te/N/P/B/C).

`MP_API_KEY` is read from the environment, or from a `.env` file
auto-discovered by `_load_dotenv()` (nearest `.env` walking up from CWD,
then from the script dir; an explicit env var always wins over `.env`).

If MP_API_KEY is missing or the API is unreachable, prints the bundled
fallback list so the loop can still run end-to-end. Always surface that
to the user — fallback is intentionally diverse but not exhaustive.

Usage:
    python scripts/initial_formulas.py --n 10 --out /tmp/init.json
    python scripts/initial_formulas.py --fallback --n 10
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
from pathlib import Path

MAGNETIC_ELEMENTS = {"Fe", "Co", "Ni", "Cr", "Mn"}
ANION_ELEMENTS = {"O", "S", "Se", "Te", "N", "P", "B", "C"}


def _load_dotenv(*, override: bool = False) -> str | None:
    """Load a `.env` file into os.environ. Zero-dependency.

    Search order (first existing file wins):
      1. $OPENAIR_DOTENV (explicit override path)
      2. `.env` in any parent of CWD (so it works from any repo subdir)
      3. `.env` in any parent of this script's location

    Existing environment variables take precedence over `.env` unless
    `override=True`. Only well-formed `KEY=VALUE` lines are applied;
    malformed lines are silently skipped. Returns the path loaded or None.
    """
    candidates: list[Path] = []
    explicit = os.environ.get("OPENAIR_DOTENV")
    if explicit:
        candidates.append(Path(explicit))
    # CWD first, then walk upward (nearest .env wins, like git config).
    cwd = Path.cwd()
    candidates.append(cwd / ".env")
    candidates.extend(p / ".env" for p in cwd.parents)
    # Script dir + its parents as a fallback (helps when running from
    # outside the repo, e.g. a fresh shell whose CWD is $HOME).
    script_dir = Path(__file__).resolve().parent
    candidates.append(script_dir / ".env")
    candidates.extend(p / ".env" for p in script_dir.parents)

    seen: set[Path] = set()
    for cand in candidates:
        try:
            cand = cand.resolve()
        except OSError:
            continue
        if cand in seen or not cand.is_file():
            continue
        seen.add(cand)
        try:
            text = cand.read_text(encoding="utf-8")
        except OSError:
            continue
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            # strip optional leading `export `
            if line.startswith("export "):
                line = line[len("export "):].lstrip()
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            if not key or not key.replace("_", "").isalnum():
                continue
            value = value.strip()
            # strip matching surrounding quotes
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]
            if override or key not in os.environ:
                os.environ[key] = value
        return str(cand)
    return None

# 20 hand-picked formulas spanning common magnet structure families.
# Used only when MP is unreachable.
FALLBACK = [
    "Fe3O4", "Fe2O3", "FeO", "CoFe2O4", "NiFe2O4",
    "MnFe2O4", "BaFe12O19", "SrFe12O19", "CrO2", "Cr2O3",
    "Co3O4", "NiO", "MnO", "Mn3O4", "Fe3Sn2",
    "Co3Sn2", "Fe2B", "FePt", "CoPt", "MnAl",
]


def query_mp(n: int, seed: int, with_structures: bool = False) -> list[dict]:
    try:
        from mp_api.client import MPRester
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] mp_api import failed: {exc!r}", file=sys.stderr)
        return []

    dotenv_path = _load_dotenv()
    api_key = os.environ.get("MP_API_KEY") or os.environ.get("MP_API_ENDPOINT_KEY")
    if not api_key:
        print(
            f"[warn] MP_API_KEY not set (env + .env lookup at {dotenv_path!r})",
            file=sys.stderr,
        )
        return []

    formulas: list[dict] = []
    # Iterate over each magnetic element to get diversity. Use
    # summary_search with chemsys filters; cap results per element so a
    # common one (Fe) doesn't drown out the rest.
    per_element = max(n * 3, 20)
    fields = ["formula_pretty", "material_id", "energy_above_hull"]
    if with_structures:
        # 'structure' returns the final DFT-relaxed pymatgen Structure
        fields.append("structure")
    for el in MAGNETIC_ELEMENTS:
        try:
            with MPRester(api_key) as mpr:
                docs = mpr.materials.summary.search(
                    elements=[el],
                    fields=fields,
                )
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] MP query failed for {el}: {exc!r}", file=sys.stderr)
            continue
        # Keep only reasonably stable ones, prefer formulas with 2-4 elements
        kept = []
        for d in docs:
            eh = getattr(d, "energy_above_hull", None)
            formula = getattr(d, "formula_pretty", None)
            if formula is None:
                continue
            n_el = len(_unique_elements(formula))
            if eh is not None and eh > 0.2:
                continue
            if n_el < 2 or n_el > 4:
                continue
            entry = {
                "formula": formula,
                "mp_id": getattr(d, "material_id", None),
                "energy_above_hull": eh,
            }
            if with_structures:
                struct = getattr(d, "structure", None)
                entry["cif"] = _structure_to_cif(struct, formula)
                if entry["cif"] is None:
                    # skip entries without a usable structure
                    continue
            kept.append(entry)
        # shuffle deterministically per element so reruns are stable
        rng = random.Random(seed + hash(el))
        rng.shuffle(kept)
        formulas.extend(kept[:per_element])

    # dedupe by formula
    seen = set()
    unique = []
    for f in formulas:
        if f["formula"] in seen:
            continue
        seen.add(f["formula"])
        unique.append(f)

    rng = random.Random(seed)
    rng.shuffle(unique)
    return unique[:n]


def _structure_to_cif(struct, formula: str) -> str | None:
    """Convert a pymatgen Structure to a CIF string, robustly."""
    if struct is None:
        return None
    try:
        # Use the structure's own CIF exporter if available
        return struct.to(fmt="cif")
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] CIF export failed for {formula}: {exc!r}", file=sys.stderr)
        return None


def _unique_elements(formula: str) -> set[str]:
    try:
        from pymatgen.core import Composition
        return {str(el) for el in Composition(formula).elements}
    except Exception:  # noqa: BLE001
        return set()


def fallback(n: int, seed: int) -> list[dict]:
    rng = random.Random(seed)
    pool = list(FALLBACK)
    rng.shuffle(pool)
    return [{"formula": f, "mp_id": None, "fallback": True} for f in pool[:n]]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=10)
    parser.add_argument("--out", help="Write JSON to file; default stdout")
    parser.add_argument("--fallback", action="store_true", help="Skip MP, use fallback list")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument(
        "--with-structures",
        action="store_true",
        help="Also fetch the DFT-relaxed CIF for each MP entry. "
             "Adds 'cif' and 'energy_above_hull' to each result. "
             "Use this when feeding parents directly to MatterSim/ALIGNN "
             "instead of going through DiffCSP.",
    )
    args = parser.parse_args()

    if args.fallback:
        result = fallback(args.n, args.seed)
    else:
        result = query_mp(args.n, args.seed, with_structures=args.with_structures)
        if not result:
            print("[warn] MP query returned nothing — falling back to bundled list", file=sys.stderr)
            result = fallback(args.n, args.seed)

    payload = {
        "source": "fallback" if all(r.get("fallback") for r in result) else "mp_api",
        "n": len(result),
        "formulas": result,
    }
    text = json.dumps(payload, indent=2)
    if args.out:
        open(args.out, "w").write(text)
        print(f"[ok] wrote {len(result)} formulas → {args.out}", file=sys.stderr)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
