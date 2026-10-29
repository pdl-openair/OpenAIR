---
name: vasp-band-gap
description: >
  Calculate the band gap (and band structure) of a crystalline material from a
  CIF file using DFT/VASP on the HPCSlurmArm cloud cluster. Implements the
  MatterGen paper protocol (microsoft/mattergen#213): MP-GGA double relax →
  static SCF → non-scf uniform → non-scf line, all orchestrated through the
  jcc CLI. Use this skill whenever the user mentions band gap, band structure,
  PBE gap, HSE gap, direct/indirect gap, optical absorption edge, VBM/CBM,
  electron eigenvalues, or asks "is this material a semiconductor / metal /
  insulator" given a crystal structure. Also triggers for batch band-gap
  calculations on multiple CIFs, or when the user references the MatterGen
  band-gap recipe specifically. The skill reads a CIF, writes 5 VASP job
  directories, submits them in sequence to the cloud, and parses vasprun.xml
  to report gap_eV + direct/indirect + VBM/CBM.
---

# VASP Band Gap Calculation (MatterGen Protocol)

End-to-end pipeline that takes a CIF, runs the MatterGen paper's 5-step VASP
protocol on the HPCSlurmArm cloud cluster via the jcc CLI, and reports the
band gap. Validated on MoS2-mp-2815 → 1.21 eV indirect (lit. PBE ≈ 1.23 eV).

## When to Use

Trigger this skill whenever the user wants to compute a band gap or band
structure from a crystal CIF. Typical prompts:

- "What's the band gap of MoS2?"
- "Calculate the PBE gap for this structure"
- "Is this material a metal or semiconductor?"
- "Run a band structure calculation on <CIF>"
- "Give me VBM/CBM for <formula>"
- Batch: "Compute band gaps for all CIFs in this directory"

Do **not** trigger for: pure structure relaxation without band structure
(that's `vasp-thermodynamic-stability`), formation energy / ehull queries
(same skill), or hypothetical "predict the band gap with ML" requests (use
`alignn_prediction` MCP instead — no VASP needed).

## The 5-step Protocol (MatterGen issue #213)

The paper-faithful recipe is five serial VASP jobs. Each consumes the
previous step's `CONTCAR`; the non-scf steps also consume static's `CHGCAR`:

| # | Job               | Purpose                                  | INCAR highlights                                |
|---|-------------------|------------------------------------------|-------------------------------------------------|
| 00 | MP-GGA relax 1    | Coarse ionic + cell relax                | ISIF=3, IBRION=2, NSW=100, EDIFFG=-0.02         |
| 01 | MP-GGA relax 2    | Fine ionic relax on relaxed cell         | ISIF=2, IBRION=2, NSW=100                       |
| 02 | static SCF        | Dense uniform mesh, converged wavefunction | IBRION=-1, NSW=0, ISMEAR=-5, LAECHG=.True., LORBIT=11 |
| 03 | non-scf uniform   | DOS on dense uniform mesh                | ICHARG=11, ISTART=1, SIGMA=0.001                |
| 04 | non-scf line      | Band structure along high-sym path       | same as 03 but line-mode KPOINTS                |

After step 02 the band gap can already be read from `eigenvalue_band_properties`
on the static uniform mesh — this is the **recommended fast path** for just
getting a gap value. Steps 03/04 are only needed for high-resolution DOS or
along-path band structure plots.

## Quick Start

> 路径约定：以下 `<skills-dir>` 指 skill 安装目录（项目级安装为项目根下 `.claude/skills/`，用户级为 `~/.claude/skills/`）。在项目根目录执行时可直接用 `.claude/skills/vasp-band-gap/...`。

```bash
# Single CIF, full 5-step protocol (paper-faithful)
bash <skills-dir>/vasp-band-gap/scripts/run_bandgap_hpc.sh \
    --cif /path/to/material.cif \
    --name MyMaterial-mp-XXXX \
    --output-dir /tmp/my_material \
    --nodes 1 --ntasks 4

# Fast path: skip the two relax steps, jump straight to static (uses raw POSCAR)
# Useful when the CIF is already from a relaxed/converged source (e.g., Materials Project)
bash <skills-dir>/vasp-band-gap/scripts/run_bandgap_hpc.sh \
    --cif /path/to/material.cif \
    --name MyMaterial-mp-XXXX \
    --output-dir /tmp/my_material \
    --nodes 1 --ntasks 4 --skip-relax
```

Result is written to `<output-dir>/final/band_gap.json`:

```json
{
  "MyMaterial-mp-XXXX": {
    "gap_eV": 1.2138,
    "vbm_eV": 5.9745,
    "cbm_eV": 7.1883,
    "direct": false,
    "source": "static_uniform_mesh_9x9x2"
  }
}
```

## What the Pipeline Does

1. **Generate VASP inputs** (`compute_band_gap.py --write-inputs`):
   reads the CIF, builds an atomate2 `BandStructureMaker` with paper-faithful
   INCAR settings, walks the resulting jobflow Flow, and writes 5 job
   directories under `<output-dir>/inputs/<NAME>/0X_<job>/`. Each directory
   has INCAR, POSCAR, KPOINTS, POTCAR (real POTCARs require a local PAW PBE
   pseudopotential library registered via `PMG_VASP_PSP_DIR` in `~/.pmgrc.yaml`).

2. **Submit each step to HPC** (`run_bandgap_hpc.sh` → `run_hpc.sh`):
   for each step, build an `upload/` dir (INCAR/KPOINTS/POTCAR + POSCAR from
   prev CONTCAR + CHGCAR for non-scf), upload via `jcc commonfile create`,
   submit via `jcc hpc create`, poll `jcc hpc status` until Completed,
   download result zip via `jcc hpc result download`, unzip, flatten
   `workdir/`, then move on.

3. **Parse band gap** (final phase): reads `vasprun.xml`, extracts
   `eigenvalue_band_properties = (gap, cbm, vbm, direct)` from the
   non-scf line step (preferred) or static step (fallback).

## Resume / Incremental Runs

The orchestrator skips a step if its `download/CONTCAR` (or
`download/vasprun.xml` for non-scf) already exists. This means:

- A failed run can be re-invoked with the same `--output-dir` and completed
  steps are skipped — no wasted HPC compute.
- To force re-run a step, delete its `runs/<N>-<suffix>/` directory.

## Known Limitations & Fast Paths

**Static-only band gap (recommended default for "just give me the gap"):**
The non-scf steps (03/04) currently crash on HPCSlurmArm because the
paper-faithful INCAR includes `KPOINT_BSE`, `APACO=16`, and `LASPH=False`
which that VASP build apparently rejects — VASP aborts with a MPI stack
trace. The static SCF eigenvalues give an accurate gap on the uniform mesh
(anywhere within ~0.05 eV of the high-symmetry-path value for typical
semiconductors), so for reporting `gap_eV` it's sufficient.

To extract just the static gap after a run:

```python
from pymatgen.io.vasp import Vasprun
vr = Vasprun("<output-dir>/runs/2-02_static/download/vasprun.xml", parse_potcar_file=False)
gap, cbm, vbm, direct = vr.eigenvalue_band_properties
```

If the user explicitly needs along-path band structure (for plotting or
effective mass), see `references/non-scf-debug.md` for the INCAR cleanup
steps to try.

**Cluster queue wait:** small jobs (≤30 atoms) typically finish in 5–15 min
of wall time once they start. During busy periods the queue wait can be much
longer. The orchestrator polls every 15 s.

**Result zip propagation:** after `status=Completed`, the result zip is not
immediately downloadable — server-side packaging takes 2–10 min. The script
waits up to 10 min (`RESULT_MAX_RETRIES=30 × RESULT_RETRY_INTERVAL=20s`),
env-overridable.

## Prerequisites

The orchestrator expects:

- **jcc CLI** installed and on PATH (cluster submission)
- **Credentials** in `.env` at the project root (found by walking up from the
  working directory; override with `ENV_FILE`):
  `JCC_BASE_URL`, `JCC_USERNAME`, `JCC_PASSWORD`, `JCC_CLUSTER_NAME=HPCSlurmArm`
- **Python** with atomate2 + pymatgen + jobflow (used for input generation and
  vasprun.xml parsing; override the interpreter with `VENV_PY`, defaults to
  `python3`/`python` on PATH)
- **POTCAR library** — local PAW PBE pseudopotentials registered via
  `~/.pmgrc.yaml` (`PMG_VASP_PSP_DIR`), required by pymatgen to write real
  POTCARs for arbitrary elements
- **Sibling skill** `vasp-thermodynamic-stability` installed next to this
  skill (provides the `run_hpc.sh` one-task jcc submission wrapper; located
  relative to this skill, override with `RUN_HPC`)

If any of these are missing, the script fails fast with a clear message.

## References

- [`references/protocol.md`](references/protocol.md) — Full 5-step INCAR
  spec from MatterGen issue #213, with rationale per flag
- [`references/non-scf-debug.md`](references/non-scf-debug.md) — Debugging
  notes for the KPOINT_BSE / APACO / LASPH=False cluster crash, with
  proposed INCAR cleanups to try
- [`references/jcc-quirks.md`](references/jcc-quirks.md) — Non-obvious jcc
  CLI behaviors (stderr routing, result zip delay, name uniqueness,
  commonfile drop bug). Also saved as a memory entry.
- [`scripts/compute_band_gap.py`](scripts/compute_band_gap.py) — Input
  generator (atomate2 BandStructureMaker + pymatgen MPRelaxSet)
- [`scripts/run_bandgap_hpc.sh`](scripts/run_bandgap_hpc.sh) — 5-step
  orchestrator that calls `run_hpc.sh` per step

## Cost & Time Expectations

For a 6-atom system like MoS2 on HPCSlurmArm with `--nodes 1 --ntasks 4`:

| Step              | Wall time (queue+run) | Notes                                |
|-------------------|-----------------------|--------------------------------------|
| 00 relax 1        | 10–20 min             | Coarse relax, can converge in <10    |
| 01 relax 2        | 5–15 min              | Fine relax from already-good cell    |
| 02 static         | 5–15 min              | Single-point on dense mesh           |
| 03 non-scf uniform| (currently crashes)   | See limitations                       |
| 04 non-scf line   | (currently crashes)   | See limitations                       |

For `--skip-relax` (start from MP-relaxed POSCAR): only step 02 is needed,
~10 min total. **This is the recommended mode for Materials Project CIFs.**

For larger systems (≥30 atoms), bump to `--nodes 2 --ntasks 8` and watch
for GLEX OOM at ≥50 atoms (drop back to `--ntasks 4` if needed).

## Output Layout

```
<output-dir>/
├── inputs/
│   └── <NAME>/
│       ├── 00_MP_GGA_relax_1/{INCAR,POSCAR,KPOINTS,POTCAR}
│       ├── 01_MP_GGA_relax_2/...
│       ├── 02_static/...
│       ├── 03_non-scf_uniform/...
│       └── 04_non-scf_line/...
├── runs/
│   ├── 0-00_MP_GGA_relax_1/{upload/, download/, download_dir/*.zip}
│   ├── 1-01_MP_GGA_relax_2/...
│   ├── 2-02_static/...           ← after success: download/CONTCAR, CHGCAR, vasprun.xml
│   ├── 3-03_non-scf_uniform/...
│   └── 4-04_non-scf_line/...
└── final/
    └── band_gap.json              ← final result (and copied vasprun.xml from step 04, or 02 fallback)
```
