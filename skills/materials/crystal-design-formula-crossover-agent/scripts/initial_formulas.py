#!/usr/bin/env python3
"""Pull an initial pool of formulas from Materials Project, scoped to a
configurable element set.

Uses mp_api. By default returns formulas containing at least one of the
magnetic transition metals (Fe/Co/Ni/Cr/Mn) — the canonical seed set for
the original SAGA magnet MVP. Pass `--elements` to scope to any other
goal-appropriate element family (e.g. Ga,In,As,P,Sb,Se,Te for
semiconductors; B,C,N,Ti,W,Mo for hard materials).

`MP_API_KEY` is read from the environment, or from a `.env` file
auto-discovered by `_load_dotenv()` (nearest `.env` walking up from CWD,
then from the script dir; an explicit env var always wins over `.env`).

If MP_API_KEY is missing or the API is unreachable, prints the fallback
list so the loop can still run end-to-end. The default fallback is the
magnet list; pass `--fallback-list` to override with a goal-appropriate
seed set. Always surface the fallback to the user — it is intentionally
diverse but not exhaustive.

Usage:
    # default (magnet seed)
    python scripts/initial_formulas.py --n 10 --out /tmp/init.json

    # semiconductor seed
    python scripts/initial_formulas.py --n 10 \\
        --elements Ga,In,As,P,Sb,Se,Te \\
        --fallback-list GaN,GaAs,InP,ZnSe,ZnS,BN,AlN,SiC

    # skip MP, use fallback only
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


def query_mp(
    n: int,
    seed: int,
    with_structures: bool = False,
    elements: set[str] | None = None,
) -> list[dict]:
    """Query Materials Project for formulas containing any of `elements`.

    `elements` defaults to `MAGNETIC_ELEMENTS` for backward compatibility
    with the original SAGA magnet MVP. Pass a goal-appropriate set
    (e.g. {"Ga","In","As","P","Sb","Se","Te"} for semiconductors).
    """
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

    if elements is None:
        elements = MAGNETIC_ELEMENTS

    formulas: list[dict] = []
    # Iterate over each seed element to get diversity. Use summary_search
    # with chemsys filters; cap results per element so a common one (Fe)
    # doesn't drown out the rest.
    per_element = max(n * 3, 20)
    fields = ["formula_pretty", "material_id", "energy_above_hull"]
    if with_structures:
        # 'structure' returns the final DFT-relaxed pymatgen Structure
        fields.append("structure")
    for el in elements:
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


def fallback(n: int, seed: int, formulas: list[str] | None = None) -> list[dict]:
    """Return n formulas from a bundled fallback list.

    `formulas` defaults to the magnet `FALLBACK` list for backward
    compatibility. Pass a goal-appropriate seed list (e.g. semiconductor
    or hard-material formulas) when calling from a non-magnet RunPlan.
    """
    pool_list = list(formulas) if formulas is not None else list(FALLBACK)
    rng = random.Random(seed)
    rng.shuffle(pool_list)
    return [{"formula": f, "mp_id": None, "fallback": True} for f in pool_list[:n]]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=10)
    parser.add_argument("--out", help="Write JSON to file; default stdout")
    parser.add_argument("--fallback", action="store_true", help="Skip MP, use fallback list")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument(
        "--elements",
        type=str,
        default=None,
        help="Comma-separated element symbols to scope the MP query. "
             "Defaults to the magnet seed set (Fe,Co,Ni,Cr,Mn). "
             "Example: --elements Ga,In,As,P,Sb,Se,Te",
    )
    parser.add_argument(
        "--fallback-list",
        type=str,
        default=None,
        help="Comma-separated formulas to use as the fallback pool. "
             "Defaults to the bundled magnet list. "
             "Example: --fallback-list GaN,GaAs,InP,ZnSe",
    )
    parser.add_argument(
        "--with-structures",
        action="store_true",
        help="Also fetch the DFT-relaxed CIF for each MP entry. "
             "Adds 'cif' and 'energy_above_hull' to each result. "
             "Use this when feeding parents directly to MatterSim/ALIGNN "
             "instead of going through DiffCSP.",
    )
    args = parser.parse_args()

    elements = None
    if args.elements:
        elements = {e.strip() for e in args.elements.split(",") if e.strip()}
    fallback_formulas = None
    if args.fallback_list:
        fallback_formulas = [f.strip() for f in args.fallback_list.split(",") if f.strip()]

    if args.fallback:
        result = fallback(args.n, args.seed, formulas=fallback_formulas)
    else:
        result = query_mp(args.n, args.seed, with_structures=args.with_structures, elements=elements)
        if not result:
            print("[warn] MP query returned nothing — falling back to bundled list", file=sys.stderr)
            result = fallback(args.n, args.seed, formulas=fallback_formulas)

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
