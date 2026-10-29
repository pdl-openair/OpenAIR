# Property catalog — mapping user goals to ALIGNN models

This is the lookup table Claude uses in Phase 0.5 (Goal interpretation).
Given a natural-language goal, find the matching row (or compose several)
and emit a RunPlan. Each row also lists typical reporting thresholds,
crossover chemistry hints, init element filter, and a fallback seed list.

The catalog is **a starting point, not a constraint**. If the user's goal
doesn't match a row, follow the "writing your own row" recipe at the end.

## How to read a row

| Field | Meaning |
|-------|---------|
| Goal keywords | Words in the user's request that should trigger this row. EN + CN. |
| ALIGNN models | Which `predict_alignn_properties.models` to query. **Always include `ehull`** unless the user explicitly opts out of the stability filter. |
| Direction | `max` (bigger is better) or `min` (smaller is better) per objective. |
| Local scorer | A bundled script for derived quantities. `—` means use the ALIGNN value directly. |
| Co-objectives | Other objectives that should land in the same RunPlan (HHI is the most common secondary because it's a cheap, formula-only signal). |
| Crossover hints | Element-swap groups + structural-prototype families Claude should lean on during Phase 2a (LLM crossover). |
| Init elements | Element set passed to `scripts/initial_formulas.py --elements`. The MP query returns formulas containing at least one of these. |
| Fallback formulas | Comma-separated list passed to `--fallback-list`. Used when MP is unreachable or no API key. Tailor to the property family. |

## Catalog

### Magnet / magnetic density
- **Goal keywords**: magnet, magnetic, magnet density, μB/Å³, 磁体, 磁性, 磁密度, 永磁, permanent magnet, NdFeB, SmCo, ferrite
- **ALIGNN models**: `magmom`, `ehull`
- **Direction**: max the derived `mag_density = magmom / volume` (μB/Å³)
- **Local scorer**: `scripts/mag_density.py` (computes magmom/cell_volume from a CIF)
- **Co-objectives**: `hhi` (min) — rare-earth-free is usually a hard user constraint
- **Crossover hints**: substitution within Fe↔Co↔Ni↔Cr↔Mn; anion swap Te↔Se↔S↔O; structure prototypes Heusler / ThMn12 / YCo5 / spinel / perovskite (magnetic site)
- **Init elements**: Fe, Co, Ni, Cr, Mn
- **Fallback formulas**: Fe3O4, Fe2O3, FeO, CoFe2O4, NiFe2O4, MnFe2O4, BaFe12O19, SrFe12O19, CrO2, Cr2O3, Co3O4, NiO, MnO, Mn3O4, Fe3Sn2, Co3Sn2, Fe2B, FePt, CoPt, MnAl
- **Thresholds (reporting)**: mag_density > 0.2 Å⁻³ is interesting; hhi < 1500 is "supply-chain safe"

### Semiconductor / wide-bandgap
- **Goal keywords**: semiconductor, wide-bandgap, wide band gap, bandgap, Eg, 电子材料, 半导体, 宽带隙, transparent conducting
- **ALIGNN models**: `bandgap`, `ehull`
- **Direction**: max `bandgap` (eV)
- **Local scorer**: `—` (bandgap is a direct ALIGNN value)
- **Co-objectives**: `hhi` (min) is the usual secondary; for transparent conductors also keep `bandgap > 3 eV` as a hard filter
- **Crossover hints**: cation swap Ga↔In↔Al; anion swap N↔P↔As or S↔Se↔Te; group-II–VI vs III–V vs IV–IV families; zincblende / wurtzite / chalcopyrite prototypes
- **Init elements**: Si, Ge, Ga, In, Al, As, P, Sb, S, Se, Te, Zn, Cd
- **Fallback formulas**: GaN, GaAs, GaP, InP, InAs, AlN, ZnSe, ZnS, ZnTe, CdTe, SiC, BN, AlP, GaSb, InSb
- **Thresholds (reporting)**: bandgap > 1 eV (semiconducting); > 3 eV (wide-gap / TCO)

### Hard material / high modulus
- **Goal keywords**: hard, stiff, strong, modulus, bulk modulus, shear modulus, rigid, 硬质, 高强度, 刚度, 模量
- **ALIGNN models**: `bulk_modulus`, `shear_modulus`, `ehull`
- **Direction**: max both
- **Local scorer**: `—`
- **Co-objectives**: optional `density` (min) — many hard materials are dense, so the user may want a Pareto knee on modulus vs density. Compute density inline as `sum(mass_i)/volume` from the relaxed CIF.
- **Crossover hints**: B–C–N–O covalent networks; transition-metal carbides / borides / nitrides (TiC, WC, TiB2, HfN); diamond-like / zincblende / rocksalt prototypes
- **Init elements**: B, C, N, O, Ti, W, Mo, Cr, Hf, Ta
- **Fallback formulas**: TiC, WC, B4C, c-BN, HfC, TiN, TiB2, SiO2(stishovite), Al2O3, SiC, MoS2, ReB2, WB2
- **Thresholds (reporting)**: bulk_modulus > 200 GPa is "hard"; > 400 GPa is "ultra-hard"

### 2D / exfoliable layered material
- **Goal keywords**: 2D, monolayer, exfoliable, exfoliation, layered, van der Waals, 剥离, 二维, 层状, vdW
- **ALIGNN models**: `exfoliation_energy`, `bandgap`, `ehull`
- **Direction**: min `exfoliation_energy`, max `bandgap` (for 2D semiconductors)
- **Local scorer**: `—`
- **Co-objectives**: optional `bandgap` (max) — most 2D-material searches want a gap
- **Crossover hints**: Mo/W/Nb/Ta + S/Se/Te transition-metal dichalcogenides (TMDs); MXenes (M=A1/A1-element + C/N, with surface terminations O/F/OH); Janus structures (MSTe ↔ MoSSe etc.)
- **Init elements**: Mo, W, Nb, Ta, Ti, V, S, Se, Te, C, N
- **Fallback formulas**: MoS2, MoSe2, WS2, WSe2, NbS2, NbSe2, TaS2, TiS2, VS2, WTe2, MoTe2, h-BN, phosphorene, MXene Ti3C2
- **⚠ Caveat**: DiffCSP's `mp_csp` checkpoint is trained on bulk 3D crystals and frequently rejects or distorts 2D chemistries. If DiffCSP returns `value:null` for >50% of TMD candidates, switch to `mpts_csp` or restrict crossover to known layered prototypes. See project memory on cppred/MP limitations for 2D.
- **Thresholds (reporting)**: exfoliation_energy < 200 meV/atom is "easily exfoliable"; < 100 meV/atom is excellent

### Superconductor
- **Goal keywords**: superconductor, superconducting, Tc, transition temperature, 超导, 超导体
- **ALIGNN models**: `supercon_tc`, `ehull`
- **Direction**: max `supercon_tc` (K)
- **Local scorer**: `—`
- **Co-objectives**: none typical
- **Crossover hints**: Cu-based cuprates (YBCO family); Fe-based pnictides / chalcogenides (LaFeAsO, FeSe); MgB2-family; A15 structure (Nb3Sn, Nb3Ge)
- **Init elements**: Cu, Fe, Y, Ba, Sr, La, O, As, Se, Mg, B, Nb
- **Fallback formulas**: YBa2Cu3O7, MgB2, Nb3Sn, Nb3Ge, LaFeAsO, FeSe, FeTe, Bi2Sr2CaCu2O8, HgBa2CuO4
- **Thresholds (reporting)**: supercon_tc > 10 K is interesting; > 77 K is "liquid-nitrogen-temperature"
- **⚠ Note**: The ALIGNN supercon-Tc model is trained on a small dataset and tends to predict small finite Tc even for non-superconducting materials. Treat the absolute numbers as a *relative ranking signal*, not a quantitative prediction. Surface this caveat in the final report.

### Dielectric / capacitor
- **Goal keywords**: dielectric, permittivity, capacitor, insulator, 介电, 电容, 绝缘
- **ALIGNN models**: `dielectric`, `bandgap`, `ehull`
- **Direction**: max `dielectric` (epsx)
- **Local scorer**: `—`
- **Co-objectives**: keep `bandgap` in a target range as a hard filter (insulators need Eg > 3 eV)
- **Crossover hints**: perovskite ABO3 with A=Ba/Sr/Ca/Pb and B=Ti/Zr/Hf/Sn; fluorite-structure oxides
- **Init elements**: Ba, Sr, Ca, Pb, Ti, Zr, Hf, Sn, Mg, O
- **Fallback formulas**: BaTiO3, SrTiO3, PbTiO3, BaZrO3, SrZrO3, CaTiO3, Pb(Zr,Ti)O3, MgO, HfO2, ZrO2
- **Thresholds (reporting)**: dielectric > 10 is interesting; > 1000 is high-k

### Photovoltaic / solar absorber
- **Goal keywords**: photovoltaic, solar, absorber, PV, SLME, 光伏, 太阳能, 吸光
- **ALIGNN models**: `slme`, `bandgap`, `ehull`
- **Direction**: max `slme` (%)
- **Local scorer**: `—`
- **Co-objectives**: keep `bandgap` in the Shockley–Queisser sweet spot 1.1–1.7 eV as a hard filter
- **Crossover hints**: Pb/Sn halide perovskites (APbX3 with A=MA/FA/Cs and X=I/Br/Cl); Cu-In-Ga-Se (CIGS) chalcopyrites; kesterite Cu2ZnSn(S,Se)4
- **Init elements**: Pb, Sn, I, Br, Cl, Cu, In, Ga, Ag, Zn, Se, S
- **Fallback formulas**: MAPbI3, FAPbI3, CsPbI3, CsPbBr3, CuInSe2, CuInS2, CuGaSe2, Cu2ZnSnS4, Cu2ZnSnSe4, Sb2Se3, BiFeO3
- **Thresholds (reporting)**: slme > 20% is interesting; > 30% is excellent; bandgap 1.1–1.7 eV is the SQ window

### Stable / cheap crystal (catch-all)
- **Goal keywords**: stable, cheap, low-cost, supply-chain, criticality, 稳定, 廉价, 供应链
- **ALIGNN models**: `formation_energy`, `ehull`
- **Direction**: min `formation_energy` (eV/atom)
- **Local scorer**: `—`
- **Co-objectives**: `hhi` (min) — this is the dominant objective for "cheap material" searches
- **Crossover hints**: depend on the chemistry family the user mentions; if unspecified, lean on common-rock salt / spinel / perovskite prototypes with earth-abundant elements
- **Init elements**: (user-specified; if missing, ask)
- **Fallback formulas**: (user-specified; if missing, ask)
- **Thresholds (reporting)**: formation_energy < 0 eV/atom (thermodynamically stable); hhi < 1500

## Writing your own row

If the user's goal doesn't fit any row above (e.g. "find a lithium-ion battery
cathode material with high voltage"), do this:

1. **Identify the target property.** Check the full ALIGNN model registry:
   `formation_energy, bandgap, dielectric, ehull, bulk_modulus,
   shear_modulus, exfoliation_energy, slme, supercon_tc, magmom`. If the
   property is in this list, that's your model. If not, the property is a
   **derived** quantity (like `mag_density`) — see step 3.
2. **Pick the direction.** Bigger-is-better or smaller-is-better?
3. **Check if it needs a derived scorer.** Common derived quantities:
   - `density = sum(mass_i) / cell_volume` — write inline python using
     `pymatgen.Structure` + atomic masses from `pymatgen.core.composition`.
   - `specific_strength = bulk_modulus / density` — combine two values.
   - `volumetric_capacity = capacity_per_atom * n_atoms / volume` — for
     battery materials; needs external capacity data.
   The recipe: write ≤15 lines of python inline in the run, mirroring the
   shape of `scripts/mag_density.py` (read CIF, compute, return dict).
4. **Pick co-objectives.** HHI is almost always relevant. Add others if
   the user mentions them ("hard AND lightweight" → bulk_modulus max +
   density min).
5. **Pick crossover hints.** Use materials-domain knowledge of the
   family. If you don't know the family, ask the user, or default to
   same-group / same-period substitutions.
6. **Pick init elements + fallback formulas.** 8–15 elements that span
   the family. 15–25 fallback formulas covering the canonical members.
7. **Surface the row to the user before running.** Phase 0.5 always
   ends with a confirmation step — see SKILL.md.

## Composing multiple rows

If the user's goal legitimately spans multiple families (e.g. "find a 2D
magnetic semiconductor"), compose:

- **ALIGNN models** = union of both rows (`magmom, bandgap, exfoliation_energy, ehull`).
- **Objectives** = both rows' primary objectives (max `mag_density` derived from magmom/volume, max `bandgap`, min `exfoliation_energy`).
- **Crossover hints** = intersect (e.g. magnetic TMDs — CrS2, CrSe2, Fe3GeTe2 family).
- **Init elements** = union, prioritizing the intersection (Cr, V, Fe + S, Se, Te).
- **Fallback formulas** = mix from both rows + add known members of the intersection (CrI3, Fe3GeTe2, Cr2Ge2Te6, etc.).

Composed runs are usually slower (more ALIGNN queries per candidate) and
the reward space gets crowded — consider asking the user to weight the
objectives explicitly.
