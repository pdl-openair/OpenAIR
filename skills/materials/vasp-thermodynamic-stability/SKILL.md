---
name: vasp-thermodynamic-stability
description: >
  VASP thermodynamic stability analysis workflow for crystalline materials.
  Automates the full pipeline: query Materials Project for competing phases,
  generate unified VASP inputs (POSCAR/INCAR/KPOINTS/POTCAR), submit HPC
  calculations (relaxation + static), extract energies, and build convex hull
  phase diagram to calculate energy above hull (ehull) and formation energy.
  Use this skill when the user mentions thermodynamic stability, convex hull,
  ehull, formation energy, competing phases, phase diagram, stability
  verification, or wants to assess whether a compound is thermodynamically
  stable using DFT/VASP calculations. Also triggers for queries about
  Materials Project phase analysis, pymatgen PhaseDiagram usage in stability
  context, or batch VASP calculation setup for multiple competing phases.
---

# VASP Thermodynamic Stability Analysis

Complete workflow for verifying whether a compound is thermodynamically stable by constructing a convex hull phase diagram from DFT calculations.

## Overview

Given a target compound (e.g., AlInSe3), this workflow:
1. Queries Materials Project for all competing phases in the chemical system
2. Generates VASP input files with unified calculation parameters
3. Runs relaxation and static calculations on HPC
4. Extracts total energies and builds a phase diagram
5. Reports ehull (energy above hull) and formation energy

**Acceptance criteria**: ehull <= 0.05 eV/atom AND formation energy <= 0 eV/atom

## When to Use

- Assessing thermodynamic stability of a new or hypothetical compound
- Verifying DFT-predicted stability against the convex hull
- Setting up batch VASP calculations for competing phases
- Any task involving pymatgen PhaseDiagram for stability analysis

## Quick Start

```bash
# Step 0: Copy helper scripts from skill into workdir (only needed once per project)
SKILL_SCRIPTS=<path-to-skill>/scripts
cp "$SKILL_SCRIPTS/"{generate_all_potcar.sh,submit_all.sh,run_hpc.sh,hpc_results.py} ./

# Step 1: Query MP and generate all VASP inputs
python <path-to-skill>/scripts/setup_competing_phases.py \
  --formula AlInSe3 \
  --target-poscar /path/to/POSCAR \
  --workdir competing_phases

cd competing_phases
cp ../*.sh ../hpc_results.py ./   # bring helper scripts into the workdir

# Step 2: Generate POTCAR files (requires VASP_PSP_DIR env var)
export VASP_PSP_DIR=/path/to/vasp/pseudopotentials
./generate_all_potcar.sh

# Step 3: Submit relaxations (requires jcc CLI + JCC_USERNAME/JCC_PASSWORD env vars)
#   submit_all.sh auto-passes --task-name <Formula>_<Nat>at_relax
./submit_all.sh relax
# To manually submit one phase with multi-core parallelism:
#   Small/medium cells (≤30 atoms): --nodes 2 --ntasks 8 (matches KPAR=2 NCORE=4)
#   Large cells (≥60 atoms):        --nodes 1 --ntasks 4 (avoid GLEX OOM)
#   ./run_hpc.sh --local-path Se_64at/relax --output-dir results/Se_64at_relax \
#                --task-name Se_64at_relax --nodes 1 --ntasks 4

# Step 4: After relaxations complete
./submit_all.sh collect    # CONTCAR -> static/POSCAR
./submit_all.sh static     # Submit static calculations
./submit_all.sh energies   # Extract energies to energies.json

# Step 5: Analyze stability
python <path-to-skill>/scripts/analyze_stability.py --workdir .
```

## Workflow Details

### Phase 1: Query Competing Phases

The script `setup_competing_phases.py` queries Materials Project API for:
- All **stable phases** on the convex hull (ehull = 0)
- **Near-stable phases** with ehull <= cutoff (default 0.05 eV/atom)
- Automatically removes duplicate formulas (keeps lowest ehull)

Output: `submission_info.json` manifest with all phase metadata.

**Dependencies**: `mp-api`, `pymatgen`, MP API key (`MP_API_KEY` env var)

### Phase 2: VASP Input Generation

Each competing phase gets a directory structure:
```
<Formula>_<Nat>at/
├── relax/      # Structure relaxation (ISIF=3, NSW=100)
│   ├── POSCAR  # From MP or user-provided
│   ├── INCAR   # Unified parameters
│   └── KPOINTS # Automatic density (KPPA=3000)
└── static/     # Static calculation (NSW=0, ISMEAR=-5)
    ├── POSCAR  # Placeholder (replaced by CONTCAR after relax)
    ├── INCAR   # Unified parameters
    └── KPOINTS # Higher density (KPPA=5000)
```

**Unified VASP parameters** (reasoning below):

| Parameter | Relax | Static | Reason |
|-----------|-------|--------|--------|
| ENCUT | 450 | 450 | High enough for all elements; ensures energy consistency |
| PREC | Accurate | Accurate | Required for reliable forces and stresses |
| EDIFF | 1E-6 | 1E-7 | Tighter for static to ensure converged energy |
| ISMEAR | 0 (Gaussian) | -5 (tetra) | Tetrahedron for accurate static energy |
| SIGMA | 0.05 | 0.05 | Small width for insulators/semiconductors |
| LREAL | A | A | Projector-augmented for memory efficiency |
| IVDW | 12 | 12 | DFT-D3-BJ dispersion correction |
| NCORE | 4 | 4 | Bands-per-group; pick so `NCORE × KPAR` ≈ `--ntasks` |
| KPAR | 2 (1 for >=28at) | 2 (1 for >=28at) | K-point parallelism; reduce for large systems or OOM |

**KPOINTS**: Generated via pymatgen `Kpoints.automatic_density()`:
- Relaxation: KPPA = 3000 (k-points per reciprocal atom)
- Static: KPPA = 5000 (denser for accurate energy)

### Phase 3: HPC Submission

Uses the `jcc` CLI tool to submit to a remote HPC Slurm cluster. The `jcc` binary
is **not** bundled with this skill — users must install it separately and ensure
it is on `PATH`. Credentials and cluster settings are read from environment
variables (no hardcoded values):

| Env var | Purpose | Required |
|---------|---------|----------|
| `JCC_BASE_URL` | jcc service URL | yes |
| `JCC_USERNAME` | account username | yes |
| `JCC_PASSWORD` | account password | yes |
| `JCC_CLUSTER_NAME` | target cluster (default `HPCSlurmArm`) | no |
| `JCC_APP_TYPE` | app type (default `vasp`) | no |
| `JCC_NODES` | default node count (default `1`; CLI `--nodes` overrides) | no |
| `JCC_NTASKS` | default total MPI process count (default `4`; CLI `--ntasks` overrides). Note: jcc's own `--help` text claims this is "per-node" — **that is wrong**. Verified by VASP log: `nodes=2 ntasks=8` → `running on 8 total cores` (not 16). Larger values give real parallel speedup when paired with `KPAR`/`NCORE` in INCAR (e.g. `ntasks=8 + KPAR=2 NCORE=4`). See "GLEX memory threshold" below for the upper bound. | no |
| `VASP_PSP_DIR` | pseudopotential root for POTCAR generation | yes (Step 2) |
| `MP_API_KEY` | Materials Project API key | yes (Step 1) |

The bundled `scripts/run_hpc.sh` is a generic wrapper that uploads a local
VASP input directory to HPC, polls until completion, and downloads the result
zip, then safely extracts it into a new `download/` directory (normally `download/workdir/`).
Existing output archives/extraction directories are not overwritten. Keep `hpc_results.py`
beside `run_hpc.sh` and `submit_all.sh`; Python is selected by MATERIALS_PY / EVOLVE_PY / python. The bundled `scripts/submit_all.sh` orchestrates the full pipeline:

- `relax` — Submit all relaxation calculations
- `collect` — Download results, copy CONTCAR -> static/POSCAR
- `static` — Submit static calculations with relaxed structures
- `energies` — Extract `energy(sigma->0)` from OUTCAR to `energies.json`
- `status` — Check completion status
- `all` — Full automated pipeline

**Task naming convention**: Always pass `--task-name <Formula>_<Nat>at_<stage>`
(e.g. `AlInSe3_10at_relax`). Task names show up on the HPC console and must
identify the material — do not use random suffixes like `vasp-task-P9qKNfOU`.
`submit_all.sh` does this automatically; for manual `run_hpc.sh` calls it is
the caller's responsibility.

**jcc CLI conventions** (see `references/README.md` for full command tree):
- `jcc hpc status`, `jcc hpc log`, `jcc hpc result`, `jcc hpc result download`
  all take **`--task-name`** (not `--task-id`) in the current jcc version.
- `jcc hpc create` accepts `--nodes` and `--ntasks` for resource sizing.
- `jcc login` emits a `POST https://...` log line before the JSON body;
  JSON extraction must skip that prefix (the bundled script handles this).

**Key consideration — MPI parallelism on HPCSlurmArm**: The platform-side
job launcher is hardcoded as `yhrun --mpi=pmix` (visible in `jcc hpc log`).
Multi-MPI works fine; verified 2026-06-25:

| Config | Outcome | Evidence |
|--------|---------|----------|
| `nodes=1 ntasks=4` | ✅ Stable | Cu5TeI3_9at, Se_64at — clean VASP main loop |
| `nodes=2 ntasks=8` | ✅ Stable for ≤30-atom cells | Te2I_24at: `running on 8 total cores` |
| `nodes=2 ntasks=8` | ❌ GLEX OOM on ≥64-atom cells | Se_64at: `Cannot allocate memory` → pmix -25 |
| `nodes=2 ntasks=16` | ❌ GLEX OOM regardless of cell | All 3 test tasks failed |

**Important: `--ntasks` is TOTAL MPI processes, not per-node.** jcc's own
`--help` text says "每个节点任务数" — **that is wrong**. Confirmed by VASP
log: `--nodes 2 --ntasks 8` produces `running on 8 total cores` (not 16).

**GLEX memory threshold** (root cause of large-cell failures):
- Each MPI rank pins network memory via the GLEX interconnect
- Total pinned memory scales with `ranks × per-rank VASP memory`
- At 8+ ranks on a 64-atom cell, GLEX hits `REG_UMEM cmd: Cannot allocate
  memory`, which cascades to `pmix_v3 status = -25` and aborts the step
- **Rule of thumb**:
  - ≤30-atom cells: `--ntasks 8` is safe
  - 30–60-atom cells: try `--ntasks 8`, fall back to 4 if GLEX OOM appears
  - ≥60-atom cells: use `--ntasks 4` directly
- Match INCAR: pick `KPAR × NCORE = ntasks` (e.g. `ntasks=8 + KPAR=2 NCORE=4`)

If `jcc hpc log` ever shows `Cannot allocate memory` / `GLEX create region
failed` / `pmix_v3 status = -25`, halve `--ntasks` and resubmit.

### Phase 4: Stability Analysis

`analyze_stability.py` builds the phase diagram using pymatgen:

```python
from pymatgen.analysis.phase_diagram import PhaseDiagram
pd = PhaseDiagram(entries)
ehull = pd.get_e_above_hull(target_entry)
eform = pd.get_form_energy(target_entry) / natoms
```

**Output interpretation**:
- ehull = 0: Thermodynamically stable (on the convex hull)
- ehull <= 0.05: Metastable (likely synthesizable)
- ehull <= 0.1: Moderately metastable
- ehull > 0.1: Unstable

### Quick Validation with MP

Before running expensive VASP calculations, validate with MP energies:
```bash
python <path-to-skill>/scripts/analyze_stability.py --use-mp --target-formula AlInSe3
```

This gives a rough estimate of where the compound sits on the hull.

## Bundled Scripts

All helper scripts ship inside `scripts/` and are self-contained templates.
None of them contain hardcoded absolute paths, credentials, or element lists.
Copy them into each project's workdir before running.

### `scripts/generate_all_potcar.sh`

Generates POTCAR for all phases by concatenating element pseudopotentials.
Reads the element list dynamically from `submission_info.json` — works for
any chemical system without editing.

```bash
export VASP_PSP_DIR=/path/to/vasp/pseudopotentials   # $VASP_PSP_DIR/<Element>/POTCAR
./generate_all_potcar.sh
./generate_all_potcar.sh --psp-dir /custom/path/potpaw_PBE
```

### `scripts/submit_all.sh`

Batch HPC submission orchestrator. Looks for `run_hpc.sh` in the same
directory by default; override with `RUN_HPC=/your/path ./submit_all.sh ...`
if you keep it elsewhere.

### `scripts/run_hpc.sh`

Generic HPC task wrapper built on the `jcc` CLI. Reads all credentials and
cluster settings from environment variables — install `jcc` separately and
export `JCC_BASE_URL`, `JCC_USERNAME`, `JCC_PASSWORD` before running.

## Energy Contract (UTF-8 JSON)

Legacy `energies.json` values are **eV per manifest `formula` unit**, normally the
pymatgen reduced formula (including its O2 convention), NOT the full VASP cell.
For an O2 cell with 8 atoms: `E_formula = E_cell / (8 / 2)`.
`--energy-basis cell` explicitly interprets legacy scalar values as cell totals
using verified `submission_info.json.natoms`; never infer the basis from magnitude.

New `submit_all.sh energies` writes unambiguous records:
```json
{"O2_8at": {"energy_eV": -40.0, "basis": "cell", "natoms": 8,
             "source": "results/O2_8at_static/download/workdir/OUTCAR"}}
```
`natoms` comes from OUTCAR NIONS. The analyzer converts cell/atom/formula_unit
records, rejects missing phases and non-finite values, and warns for untagged
legacy data or |E/atom| > 30 eV. The magnitude warning is a heuristic, NOT a
physical validity test and cannot reliably detect unit mistakes. All JSON is UTF-8.

### No local PAW library

If authorized to use the platform's VASPKIT service, stage a copy of each POSCAR
in a **new empty directory**, then run `jcc hpc vaspkit --path <staged-POSCAR>`.
Copy only the returned POTCAR into the calculation directories; do not overwrite
the unified INCAR/KPOINTS with service defaults. Keep POTCAR element order and
pseudopotential family consistent across target and competitors. This avoids a
local PAW library, not the requirement for a valid VASP/PAW license. The band-gap
atomate2 input writer remains a separate optional dependency.

### Log parser failure

If `jcc hpc log` raises a Go JSON unmarshal error, the wrapper saves `hpc.log`,
`hpc-log-diagnostic.log`, `status.log`, and (on task failure) `result-list.log`.
Do not infer SCF/MPI root causes without actual logs. Retrieve result archives when
available and inspect OUTCAR/stdout/stderr as data, never execute archive scripts.
The CLI binary's parser itself requires an upstream fix; no CLI source is bundled.

## Energy Extraction

Energy is extracted as `energy(sigma->0)` from the OUTCAR's last ionic step:
```bash
grep "energy(sigma->0)" OUTCAR | tail -1 | awk '{print $NF}'
```

**Important**: Use static calculation energies for the final phase diagram, not relaxation energies. Relaxation energies include unconverged forces/stresses and are less accurate for thermodynamic comparison.

## Common Issues

| Problem | Cause | Solution |
|---------|-------|----------|
| OOM (exit 137) | LREAL=.FALSE. too memory-heavy | Use LREAL=A |
| GLEX/PMIx errors | MPI communication failure (rare; `--ntasks > 1` confirmed working 2026-06-25) | If seen, fall back to `--ntasks 1`; otherwise use multi-core normally |
| Download failures | HPC cleanup before download | Resubmit the task |
| Dense k-grid | pymatgen KPPA too high for large cells | Reduce KPPA or use automatic density |
| MP API errors | Pydantic validation on new fields | Use `get_entries_in_chemsys()` + `PhaseDiagram` |

## Dependencies

- Python: `pymatgen`, `mp-api`, `numpy`
- HPC: `jcc` CLI (user-installed, must be on `PATH`) + `jq`
- VASP pseudopotentials: PAW_PBE set, exposed via `VASP_PSP_DIR`
- Environment variables:
  - `MP_API_KEY` — Materials Project access (Step 1)
  - `JCC_BASE_URL`, `JCC_USERNAME`, `JCC_PASSWORD` — HPC submission (Step 3)
  - `JCC_CLUSTER_NAME`, `JCC_APP_TYPE` — optional HPC settings

## Scripts

| Script | Purpose |
|--------|---------|
| `scripts/setup_competing_phases.py` | Query MP, download structures, generate VASP inputs |
| `scripts/analyze_stability.py` | Build phase diagram, calculate ehull and formation energy |
| `scripts/generate_all_potcar.sh` | Concatenate element POTCARs for every phase (any chemsys) |
| `scripts/submit_all.sh` | Orchestrate relax/collect/static/energies pipeline |
| `scripts/run_hpc.sh` | Generic jcc-based HPC task wrapper (template) |
