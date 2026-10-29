# Debugging the non-scf VASP crash on HPCSlurmArm

## Symptom

Steps 03 (non-scf uniform) and 04 (non-scf line) abort on HPCSlurmArm.
The slurm log shows:

```
yhrun: error: cn10241: tasks 1-3: Aborted
slurmstepd: error: mpi/pmix_v3: _errhandler: cn10241 [...]: pmixp_client_v2.c:211: Error handler invoked: status = -25
slurmstepd: error: *** STEP 5924922.0 ON cn10241 CANCELLED ***
yhrun: Job step aborted
```

VASP itself dies with a stack trace before any "entering main loop" line.

## Likely Cause

The paper-faithful INCAR (from `compute_band_gap.py::build_band_structure_maker`)
includes flags that some VASP builds reject at input-parse time:

```python
"APACO": 16,                    # pair-correlation length, unusual for band calc
"KPOINT_BSE": [-1, 0, 0, 0],   # BSE-related; may be ignored or rejected
"GGA": "PE",                    # forcing PBE functional explicitly
"LASPH": False,                 # explicit disable of aspherical corrections
"NATURALO": 0,                  # natural-orbital output control (obscure)
"NELMDL": 0,                    # non-default non-SCF electron steps
```

The static step on the same cluster runs fine — the difference is exactly
these non-scf-specific flags. Most VASP versions silently ignore unknown
flags or flag-incompatible values, but some builds (especially with `--strict`
parses or vendor-modified VASP) abort.

## Proposed INCAR Cleanup

If the user explicitly needs non-scf band structure (not just `gap_eV`),
try removing the suspect flags one at a time, in this order (cheapest
change first):

1. **Remove `KPOINT_BSE`** — it's only relevant for BSE post-processing,
   not for a plain band-structure non-scf. This is the most likely
   culprit on a non-BSE-compiled VASP.
2. **Remove `APACO`** — defaults to a sensible value; only matters for
   pair-correlation analysis which we don't use.
3. **Remove `NATURALO`** — obscure output control, defaults are fine.
4. **Set `LASPH = True`** — modern VASP with hybrid/GW support prefers
   this for accuracy and some builds require it for non-scf with PAW.
5. **Set `GGA = --` (omit)** — let VASP pick from POTCAR metadata.

To patch the INCAR for a single run, edit
`<output-dir>/inputs/<NAME>/03_non-scf_uniform/INCAR` and
`04_non-scf_line/INCAR` after Phase 0 of `run_bandgap_hpc.sh`. Then
delete `runs/3-03_non-scf_uniform/` and `runs/4-04_non-scf_line/` (if
they exist from a failed attempt) and re-invoke the orchestrator — the
resume logic will skip steps 00–02 and retry only 03–04.

To patch upstream, edit `compute_band_gap.py::build_band_structure_maker`
and drop the offending keys from the `user_incar_settings` dict.

## Workaround

For most applications (semiconductor classification, gap value reporting,
indirect-vs-direct determination), the **static SCF band gap** is good
enough. Read it directly from the static vasprun.xml:

```python
from pymatgen.io.vasp import Vasprun
vr = Vasprun("<output-dir>/runs/2-02_static/download/vasprun.xml",
             parse_potcar_file=False)
gap, cbm, vbm, direct = vr.eigenvalue_band_properties
```

For MoS2 this gives 1.2138 eV indirect vs. literature PBE 1.23 eV indirect.
For most bulk semiconductors the static-mesh gap agrees with the
high-symmetry-path gap to within ~0.05 eV (sometimes exactly equal if the
VBM/CBM happens to lie at a k-point that's in the static mesh).

When the static-mesh value is **not** good enough:

- **Effective mass calculations** need the band curvature along a specific
  direction → non-scf line required.
- **Optical absorption** with k-selection rules → non-scf uniform + BSE
  required.
- **Topological materials** with band inversions at off-Gamma k-points →
  non-scf line required to resolve them.

## What I Haven't Tried Yet

- Submitting the same INCAR with `KPOINT_BSE` removed to see if step 03 survives.
- Checking the cluster's VASP version (`jcc hpc log` of a crashed run often
  prints the VASP banner early on, before the abort — check `vasp_run_tmp.log`).
- Asking the cluster admins which VASP flags their build accepts.
- Trying `LASPH = True` (it's `False` in the paper spec; some cluster VASP
  builds require it for stability of non-scf convergence).

These are good next steps when the band path visualization becomes a
priority.
