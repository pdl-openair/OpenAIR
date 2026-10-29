---
name: cif-to-vasp
description: Generate VASP input files (POSCAR, INCAR, KPOINTS, POTCAR) from CIF crystal structure files. Use this skill whenever the user needs to prepare VASP calculations from crystal structure data, convert CIF to POSCAR, set up relaxation or static calculations, or generate VASP input sets. Also trigger when the user mentions VASP input preparation, POSCAR generation, or asks why their VASP calculation fails with ZHEGV/memory errors after generating input files — the root cause is often incorrect POSCAR coordinates or overly aggressive INCAR settings. This skill encodes hard-won lessons about cell-coordinate mismatches and memory-safe parameter choices.
---

# CIF to VASP Input Generator

Generate correct, validated VASP input files from CIF crystal structures.

The most common VASP failures (`ZHEGV failed`, `Cannot allocate memory`) trace back to two root causes this skill prevents:
1. **Wrong POSCAR coordinates** — placing conventional-cell atomic positions into a reduced/primitive cell (atoms overlap, overlap matrix becomes singular)
2. **Aggressive INCAR settings** — PREC=Accurate + dense KPOINTS + high ENCUT overwhelms HPC memory

## Workflow

### Step 1: Read and validate the CIF

Read the CIF file. Check:
- Space group and symmetry operations
- Number of atoms in the asymmetric unit
- Expected total atom count (asymmetric unit × multiplicity)
- Lattice parameters (a, b, c, alpha, beta, gamma)

If the CIF looks malformed or has missing symmetry info, ask the user before proceeding.

### Step 2: Generate POSCAR from CIF

Always use pymatgen to convert CIF → POSCAR. This ensures symmetry operations are applied correctly and coordinates match the lattice vectors.

```python
from pymatgen.core import Structure

struct = Structure.from_file("input.cif")
struct.to(fmt="poscar", filename="POSCAR")
```

After writing, clean the POSCAR by removing species charge labels from coordinate lines (e.g., `Al3+` → just the three fractional coordinates). VASP does not expect text after coordinate values.

**Critical: validate the POSCAR before proceeding.** Run this check every time:

```python
from pymatgen.core import Structure

s = Structure.from_file("POSCAR")
min_dist = float('inf')
for i in range(len(s)):
    for j in range(i+1, len(s)):
        d = s.get_distance(i, j)
        if d < min_dist:
            min_dist = d

print(f"Atoms: {len(s)}, Min distance: {min_dist:.4f} A")
```

**Validation rules:**
- Minimum interatomic distance must be > 1.5 A. If it's below this threshold, the POSCAR is wrong (atoms overlap, ZHEGV will fail). Go back to the CIF and regenerate.
- Typical bond lengths for reference:
  - Metal-chalcogen (Al-Se, In-Se, etc.): 2.3-2.8 A
  - Metal-metal: > 2.5 A
  - Chalcogen-chalcogen: > 2.3 A
- If the minimum distance looks wrong, the CIF-to-POSCAR conversion had a cell mismatch. Never try to fix this manually — regenerate from the CIF.

### Step 3: Determine the POTCAR element order

Read the POSCAR species order (the line like `Al In Se` in POSCAR). The POTCAR must concatenate pseudopotentials in exactly this order.

POTCAR generation options:
1. **VASPKit** (if installed): run `vaspkit` → task 103 (generate POTCAR automatically from POSCAR)
2. **pymatgen** (if `PMG_VASP_PSP_DIR` is set): use `MPRelaxSet(struct).potcar`
3. **Manual**: concatenate PAW_PBE pseudopotential files in the POSCAR species order

If no POTCAR source is available, write a `potcar_generator.sh` helper script that documents the required order.

### Step 4: Generate KPOINTS

The k-point mesh density must match the cell size. Larger cells need fewer k-points because the Brillouin zone is smaller.

**Rule of thumb for Gamma-centered meshes:**

| Cell dimension (A) | K-points along that axis |
|--------------------|--------------------------|
| < 5                | 6-8                      |
| 5-8                | 4-6                      |
| 8-12               | 3-4                      |
| 12-20              | 2-3                      |
| > 20               | 1-2                      |

For the c-axis specifically: if c > 15 A with layered/van der Waals structure, use 1-2 k-points along c.

**For initial relaxation, always use Gamma-centered (not Monkhorst-Pack) to avoid Gamma-point duplication issues.**

Example KPOINTS for a hexagonal cell with a=7.07, c=18.84:
```
Kpoints for relaxation
0
Gamma
3 3 1
0 0 0
```

### Step 5: Generate INCAR

Use conservative, memory-safe defaults for initial relaxation. The user can tighten parameters for production runs later.

**Default INCAR template for relaxation:**

```
System = [FORMULA]

# Electronic Optimization
ENCUT   = [ENCUT]
PREC    = Normal
ALGO    = Normal
EDIFF   = 1E-5
NELM    = 200
ISMEAR  = 0
SIGMA   = 0.05
LREAL   = A

# Ionic Optimization
IBRION  = 2
ISIF    = 3
NSW     = 100
EDIFFG  = -0.01

# Output
LCHARG  = .TRUE.
LWAVE   = .FALSE.
```

**ENCUT determination:**
- Read ENMAX values from each pseudopotential in the POTCAR
- Set ENCUT = max(ENMAX) × 1.3, rounded up to nearest 10 eV
- Example: max ENMAX = 240.3 → ENCUT = 320

**Why these defaults:**
- `PREC = Normal` (not Accurate): Accurate doubles FFT grid dimensions, multiplying memory ~8x. Normal is sufficient for relaxation. Use Accurate only for final static calculations.
- `LREAL = A`: Real-space projection operators. Reduces memory significantly for systems with >20 atoms.
- `ISMEAR = 0; SIGMA = 0.05`: Gaussian smearing for semiconductors/insulators. For metals, switch to ISMEAR=1, SIGMA=0.2.
- `EDIFF = 1E-5` (not 1E-6): Consistent with Normal precision. Tighter convergence wastes SCF cycles at Normal grid resolution.
- No `NCORE`/`KPAR` by default: Let VASP auto-determine parallelization. If the user specifies node/core counts, set NCORE = sqrt(number-of-cores-per-node) approximately.

**When to adjust:**
- **Metallic systems**: ISMEAR=1, SIGMA=0.2
- **Hybrid functionals (HSE06)**: Add `LHFCALC = .TRUE.; HFSCREEN = 0.2; ALGO = Normal; PRECFOCK = Normal`
- **Static calculation** (after relaxation): NSW=0, ISIF=2, PREC=Accurate, EDIFF=1E-6, remove LREAL
- **DOS/band structure**: Requires a converged CHGCAR first, then non-self-consistent run

### Step 6: Final validation

Before delivering the four files, verify:
1. **POSCAR**: minimum interatomic distance > 1.5 A, atom count matches CIF expectation
2. **INCAR**: ENCUT ≥ 1.3 × max(ENMAX from POTCAR)
3. **KPOINTS**: mesh density appropriate for cell dimensions
4. **POTCAR**: element order matches POSCAR species line exactly
5. **Cross-check**: number of ions in POSCAR (line 6 species counts) matches total atoms

Print a summary for the user showing:
- Cell parameters and volume
- Number of atoms and species
- Minimum interatomic distance
- ENCUT and KPOINTS mesh
- Any warnings or notes

## Common pitfalls

### Cell mismatch (the #1 cause of ZHEGV errors)
Never mix lattice vectors from one cell setting with coordinates from another. For example, if the CIF uses the conventional hexagonal cell (a=7.07 A) but you use a reduced primitive cell (a=4.08 A), the coordinates will place atoms on top of each other. Always let pymatgen handle the full conversion from CIF — it correctly pairs lattice vectors with symmetry-generated positions.

### Memory errors on HPC
The combination of PREC=Accurate + dense KPOINTS + high ENCUT can easily exceed node memory. For initial relaxation:
- Start with PREC=Normal and a sparse KPOINTS mesh
- After successful relaxation, tighten for the static run

### POTCAR element order mismatch
VASP silently accepts a POTCAR whose species order differs from POSCAR — but the results will be wrong. Always verify the order matches.

## File structure

```
output_directory/
├── POSCAR    ← from CIF via pymatgen
├── INCAR     ← template with system-specific parameters
├── KPOINTS   ← Gamma mesh scaled to cell dimensions
└── POTCAR    ← concatenated pseudopotentials matching POSCAR order
```
