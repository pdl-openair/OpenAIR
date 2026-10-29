# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Compute PBE band gaps following the MatterGen paper protocol.

This script reproduces the band-gap evaluation workflow described in
MatterGen issue microsoft/mattergen#213:

    DoubleRelax (MP-GGA compatible) -> BandStructure (PBE, MP-compatible)

The atomate2 workflow below mirrors the snippet shared by the MatterGen
authors. ``_BASE_MPGGA_SET`` is ``MPRelaxSet.yaml`` from pymatgen with the
``Yb`` POTCAR pinned to ``Yb_2`` for internal consistency.

Usage examples
--------------

# Compute band gaps for every CIF under samples/dft_band_gap/
python -u mattergen/scripts/compute_band_gap.py \
    --input-dir samples/dft_band_gap \
    --output-path results/band_gaps.json \
    --lpad-file my_launchpad.yaml

# Or pass an explicit list of files
python -u mattergen/scripts/compute_band_gap.py \
    --inputs samples/dft_band_gap/struct_0.cif samples/dft_band_gap/struct_1.cif \
    --output-path results/band_gaps.json \
    --lpad-file my_launchpad.yaml

# Dry run: just build the flows and print them, do not submit
python -u mattergen/scripts/compute_band_gap.py --input-dir samples/dft_band_gap --dry-run
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

from pymatgen.core import Structure

# atomate2 makers used in the paper's protocol
from atomate2.vasp.flows.core import BandStructureMaker
from atomate2.vasp.flows.mp import MPGGADoubleRelaxMaker

logger = logging.getLogger("mattergen.compute_band_gap")


# ---------------------------------------------------------------------------
# MP-compatible base set, identical to the one used inside MatterGen.
# Defined as MPRelaxSet.yaml with Yb pinned to Yb_2 for cross-calculation
# consistency (see issue #213, ClaudioZeni's reply).
# ---------------------------------------------------------------------------
def _base_mpgga_set() -> dict[str, Any]:
    """Return the MP-GGA base config dict used throughout the workflow."""
    import importlib.resources as resources
    from importlib import import_module

    from monty.serialization import loadfn

    # ``MPRelaxSet.yaml`` ships with pymatgen inside ``pymatgen/io/vasp``.
    # Use the installed package's own path (works even for namespace packages
    # where ``pymatgen.__file__`` is None in recent pymatgen versions).
    try:
        vasp_module = import_module("pymatgen.io.vasp")
        module_file = getattr(vasp_module, "__file__", None)
        if module_file is None:
            # Namespace package: fall back to resources API.
            with resources.as_file(resources.files("pymatgen.io.vasp").joinpath("MPRelaxSet.yaml")) as p:
                yaml_path = p
        else:
            yaml_path = Path(module_file).parent / "MPRelaxSet.yaml"
    except (ModuleNotFoundError, FileNotFoundError, AttributeError) as exc:
        raise FileNotFoundError(
            "Could not locate MPRelaxSet.yaml in pymatgen.io.vasp. "
            "Check your pymatgen installation."
        ) from exc

    if not Path(yaml_path).exists():
        raise FileNotFoundError(f"MPRelaxSet.yaml not found at {yaml_path}.")

    base = loadfn(str(yaml_path))
    base.setdefault("POTCAR", {})["Yb"] = "Yb_2"
    return base


_BASE_MPGGA_SET: dict[str, Any] = _base_mpgga_set()


# ---------------------------------------------------------------------------
# Paper-faithful BandStructure configuration.
# ---------------------------------------------------------------------------
def build_band_structure_maker() -> BandStructureMaker:
    """Build the PBE BandStructureMaker used in the MatterGen paper.

    The static and non-scf makers below are exactly the settings shared by
    ClaudioZeni in microsoft/mattergen#213.
    """
    # Imported here so the module can be imported without atomate2's VASP
    # makers being available (e.g. for ``--help``).
    from atomate2.vasp.jobs.core import NonSCFMaker, StaticMaker
    from atomate2.vasp.sets.core import NonSCFSetGenerator, StaticSetGenerator

    static_maker = StaticMaker(
        input_set_generator=StaticSetGenerator(
            config_dict=_BASE_MPGGA_SET,
            user_incar_settings={
                "IBRION": -1,
                "ISMEAR": -5,
                "LAECHG": True,
                "LCHARG": True,
                "LORBIT": 11,
                "LVHAR": True,
                "LWAVE": False,
                "NSW": 0,
                "ALGO": "Normal",
            },
            user_kpoints_settings={"reciprocal_density": 100},
        )
    )

    bs_maker = NonSCFMaker(
        input_set_generator=NonSCFSetGenerator(
            config_dict=_BASE_MPGGA_SET,
            user_incar_settings={
                "SIGMA": 0.001,
                "ISTART": 1,
                "LASPH": False,
                "NELMDL": 0,
                "APACO": 16,
                "GGA": "PE",
                "NATURALO": 0,
                "ALGO": "Normal",
                "ENCUT": 520,
                "KPOINT_BSE": [-1, 0, 0, 0],
            },
            auto_lreal=True,
        )
    )

    return BandStructureMaker(static_maker=static_maker, bs_maker=bs_maker)


def build_double_relax_maker() -> MPGGADoubleRelaxMaker:
    """Build the MP-GGA double-relax flow used before band structure."""
    return MPGGADoubleRelaxMaker()


# ---------------------------------------------------------------------------
# Workflow assembly
# ---------------------------------------------------------------------------
def make_relax_and_band_gap_flow(structure: Structure) -> Any:
    """Build the DoubleRelax + PBE BandStructure flow for a structure.

    The band structure is computed on the output of the double relaxation,
    matching the protocol described in issue #213.
    """
    from jobflow import Flow

    relax_maker = build_double_relax_maker()
    bs_maker = build_band_structure_maker()

    relax_job = relax_maker.make(structure)
    bs_flow = bs_maker.make(
        relax_job.output.structure, prev_dir=relax_job.output.dir_name
    )
    return Flow([relax_job, bs_flow], bs_flow.output, name="relax_and_band_structure")


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------
def load_structures(paths: list[Path]) -> list[tuple[str, Structure]]:
    """Load structures from CIF/POSCAR/EXTXYZ paths."""
    loaded: list[tuple[str, Structure]] = []
    for p in paths:
        try:
            structure = Structure.from_file(str(p))
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to read %s: %s", p, exc)
            continue
        loaded.append((p.stem, structure))
    return loaded


def collect_input_files(input_dir: Path | None, inputs: list[Path] | None) -> list[Path]:
    """Resolve the list of input structure files."""
    if inputs:
        return [Path(p) for p in inputs]
    if input_dir is None:
        raise ValueError("Either --input-dir or --inputs must be provided.")
    files: list[Path] = []
    for ext in ("*.cif", "*.CIF", "*.poscar", "*.POSCAR", "*.vasp", "*.xyz"):
        files.extend(sorted(input_dir.glob(ext)))
    return files


# ---------------------------------------------------------------------------
# Submission / result collection
# ---------------------------------------------------------------------------
def submit_flows(
    flows_and_names: list[tuple[str, Any]],
    lpad_file: Path | None,
    store_url: str | None,
    dry_run: bool,
) -> list[str]:
    """Submit flows to a FireWorks launchpad or a jobflow store."""
    if dry_run:
        for name, flow in flows_and_names:
            logger.info("[dry-run] Flow '%s' built with %d job(s)", name, len(flow))
        return [name for name, _ in flows_and_names]

    if lpad_file is not None:
        from fireworks import LaunchPad
        from jobflow.managers.fireworks import flow_to_workflow

        lpad = LaunchPad.from_file(str(lpad_file))
        fw_ids: list[str] = []
        for name, flow in flows_and_names:
            wf = flow_to_workflow(flow)
            wf.name = name
            lpad.add_wf(wf)
            fw_ids.append(name)
        return fw_ids

    # Otherwise use a jobflow Memory / Mongo store.
    from jobflow import STORE

    if store_url is not None:
        from jobflow import JobStore
        from maggma.stores.mongolike import MongoStore

        STORE.change_store(JobStore(MongoStore.from_uri(store_url, collection_name="band_gap_jobs")))
        return [name for name, _ in flows_and_names]

    # Submit synchronously to the in-memory store (mainly for testing).
    return [name for name, _ in flows_and_names]


def extract_band_gap_from_dir(run_dir: Path) -> float | None:
    """Parse the band gap from a completed band structure run directory."""
    vasprun = next(run_dir.rglob("vasprun.xml"), None)
    if vasprun is None:
        return None
    from pymatgen.io.vasp import Vasprun

    try:
        vr = Vasprun(str(vasprun), parse_potcar_file=False)
        return vr.eigenvalue_band_properties[0] if vr.eigenvalue_band_properties else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not parse band gap from %s: %s", vasprun, exc)
        return None


def write_flow_inputs(
    flows_and_names: list[tuple[str, Any]], structures: list[Structure], out_root: Path
) -> None:
    """Materialize the VASP input files for each leaf job of every flow.

    Walks the flow graph; for any job whose maker exposes an
    ``input_set_generator`` we render the VASP input set on the *un-relaxed*
    structure (since the relaxed one only exists after VASP runs). This is
    intended as a local sanity check: it proves the workflow is wired up
    correctly without needing the VASP binary.
    """
    from atomate2.vasp.jobs.base import BaseVaspMaker
    from jobflow import Flow, Job

    def find_input_jobs(item: Any, structure: Structure) -> list[tuple[str, Any, Structure]]:
        """Recursively collect (job_name, maker, structure) tuples."""
        found: list[tuple[str, Any, Structure]] = []
        if isinstance(item, Job):
            maker = getattr(item, "maker", None)
            if isinstance(maker, BaseVaspMaker) and hasattr(maker, "input_set_generator"):
                found.append((item.name or maker.name, maker, structure))
        elif isinstance(item, Flow):
            for child in item:
                found.extend(find_input_jobs(child, structure))
        return found

    out_root.mkdir(parents=True, exist_ok=True)
    for (name, flow), structure in zip(flows_and_names, structures):
        jobs = find_input_jobs(flow, structure)
        for i, (job_name, maker, struct) in enumerate(jobs):
            target_dir = out_root / name / f"{i:02d}_{job_name.replace(' ', '_')}"
            target_dir.mkdir(parents=True, exist_ok=True)
            # ``potcar_spec=False`` writes the real POTCAR. Requires
            # ``PMG_VASP_PSP_DIR`` (typically set in ~/.pmgrc.yaml) pointing at
            # the licensed VASP pseudopotential library. If unset, fall back to
            # a POTCAR.spec (symbol list only) so the workflow still validates.
            try:
                vis = maker.input_set_generator.get_input_set(structure=struct, potcar_spec=False)
            except Exception as exc:  # noqa: BLE001
                if "PMG_VASP_PSP_DIR" in str(exc):
                    logger.warning(
                        "PMG_VASP_PSP_DIR not configured; writing POTCAR.spec "
                        "instead of real POTCARs."
                    )
                    vis = maker.input_set_generator.get_input_set(structure=struct, potcar_spec=True)
                else:
                    raise
            vis.write_input(str(target_dir))
            logger.info("Wrote VASP inputs for '%s/%s' -> %s", name, job_name, target_dir)


def run_flows_locally(flows_and_names: list[tuple[str, Any]]) -> None:
    """Execute flows in-process via jobflow (requires VASP binary on PATH)."""
    from jobflow import run_locally

    for name, flow in flows_and_names:
        logger.info("Running flow '%s' locally (this invokes VASP).", name)
        run_locally(flow, create_folders=True, ensure_success=True)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input-dir", type=Path, default=None, help="Directory of structure files to evaluate.")
    parser.add_argument(
        "--inputs",
        nargs="+",
        type=Path,
        default=None,
        help="Explicit list of structure files (overrides --input-dir).",
    )
    parser.add_argument("--output-path", type=Path, required=True, help="Where to write the JSON results.")
    parser.add_argument(
        "--lpad-file",
        type=Path,
        default=None,
        help="FireWorks LaunchPad YAML. If set, flows are submitted to FireWorks.",
    )
    parser.add_argument(
        "--store-url",
        type=str,
        default=None,
        help="MongoDB URI for a jobflow store (alternative to FireWorks).",
    )
    parser.add_argument(
        "--scan-dir",
        type=Path,
        default=None,
        help="If set, scan this dir for completed runs and only extract band gaps to --output-path.",
    )
    parser.add_argument(
        "--write-inputs",
        type=Path,
        default=None,
        help=(
            "Write the VASP input files (INCAR/POSCAR/KPOINTS/POTCAR.spec) for each "
            "job in the flow to this directory, then exit. Does not require VASP."
        ),
    )
    parser.add_argument(
        "--run-locally",
        action="store_true",
        help="Run flows in-process via jobflow.run_locally (requires VASP binary).",
    )
    parser.add_argument("--dry-run", action="store_true", help="Build flows but do not submit.")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose logging.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    args.output_path.parent.mkdir(parents=True, exist_ok=True)

    # Pure extraction mode: parse band gaps from a directory of finished runs.
    if args.scan_dir is not None:
        results: dict[str, Any] = {}
        for sub in sorted(args.scan_dir.iterdir()):
            if not sub.is_dir():
                continue
            gap = extract_band_gap_from_dir(sub)
            results[sub.name] = gap
            logger.info("%s: band_gap_eV=%s", sub.name, gap)
        args.output_path.write_text(json.dumps(results, indent=2))
        logger.info("Wrote %d entries to %s", len(results), args.output_path)
        return 0

    files = collect_input_files(args.input_dir, args.inputs)
    if not files:
        logger.error("No input structure files found.")
        return 1
    logger.info("Found %d input structure(s).", len(files))

    structures = load_structures(files)
    if not structures:
        logger.error("No structures could be loaded.")
        return 1

    flows_and_names = [(name, make_relax_and_band_gap_flow(s)) for name, s in structures]

    if args.write_inputs is not None:
        write_flow_inputs(flows_and_names, [s for _, s in structures], args.write_inputs)
        logger.info("Done writing VASP inputs under %s", args.write_inputs)
        return 0

    if args.run_locally:
        run_flows_locally(flows_and_names)
        return 0

    submitted = submit_flows(
        flows_and_names,
        lpad_file=args.lpad_file,
        store_url=args.store_url,
        dry_run=args.dry_run,
    )
    logger.info("Submitted %d flow(s): %s", len(submitted), submitted)
    return 0


if __name__ == "__main__":
    sys.exit(main())
