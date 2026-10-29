"""Shared pytest fixtures for vasp-thermodynamic-stability skill tests.

Fixtures load data from tests/fixtures/ (recorded samples copied from
tmp/materials-audit/) so all tests run fully offline — no MP_API_KEY,
no VASP, no HPC, no real POTCAR files.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


# ---- Common key params shared by all INCAR stubs (audit §4.3 E3) ------------
# ENCUT=450; PREC=Accurate; EDIFF=1E-6; ISMEAR=0; SIGMA=0.05;
# LREAL=A; IVDW=12; NCORE=4
_COMMON_INCAR_BODY = """\
ENCUT   = 450
PREC    = Accurate
EDIFF   = 1E-6
ISMEAR  = 0
SIGMA   = 0.05
LREAL   = A
IVDW    = 12
NCORE   = 4
"""


def _make_incar(system: str, kpar: int) -> str:
    """Build a synthetic INCAR stub matching audit §4.3 E3 documented params.

    The line `SYSTEM = <phase_name>` is the only line that legitimately differs
    across phases; everything else (ENCUT/PREC/EDIFF/ISMEAR/SIGMA/LREAL/IVDW/
    NCORE) is identical. KPAR is the documented per-phase knob: 2 for normal
    phases (<28 atoms), 1 for large phases (>=28 atoms) — see SKILL.md L117.
    """
    return f"SYSTEM = {system}\n{_COMMON_INCAR_BODY}KPAR    = {kpar}\n"


@pytest.fixture
def submission_info_14phases() -> list[dict]:
    """Load 14-phase submission_info.json (target = CrSiTe3).

    Verifies audit §4.2 D1 / §4.3 E1: setup_competing_phases.py produced a
    complete manifest of 14 entries (3 unary + binaries + 1 ternary target).
    """
    with open(FIXTURES_DIR / "submission_info.json", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def mp_analyze_snapshot() -> dict:
    """Load recorded stability_result.json from mp_quick run.

    Snapshot of analyze_stability.py output when run against MP-fetched
    energies for CrSiTe3 chemsys (audit §4.2 D2).
    """
    with open(FIXTURES_DIR / "mp_stability_result.json", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def incar_stubs() -> dict[str, str]:
    """Synthetic INCAR texts covering both KPAR branches (audit §4.3 E3).

    Returns mapping of dirname → INCAR text. We synthesize 4 stubs:
      * 2 normal phases (<28 atoms) → KPAR = 2
      * 2 large phases (>=28 atoms) → KPAR = 1 (SKILL.md L117)
    Sufficient to assert KPAR branch divergence while keeping the test set
    minimal.
    """
    return {
        # normal phases — KPAR = 2
        "Cr_2at": _make_incar("Cr_2at", kpar=2),
        "Si_2at": _make_incar("Si_2at", kpar=2),
        # large phases — KPAR = 1
        "CrTe3_32at": _make_incar("CrTe3_32at", kpar=1),
        "Si19Te8_54at": _make_incar("Si19Te8_54at", kpar=1),
    }


@pytest.fixture
def fake_psp_dir(tmp_path):
    """Factory: creates a synthetic VASP_PSP_DIR with Cr/Si/Te POTCAR stubs.

    Returns a function so each test can build its own isolated psp dir under
    tmp_path. Resulting layout::

        <tmp_path>/fake_psp/
            Cr/POTCAR     # "FAKE POTCAR FOR Cr\n"
            Si/POTCAR     # "FAKE POTCAR FOR Si\n"
            Te/POTCAR     # "FAKE POTCAR FOR Te\n"

    No real POTCAR content; just enough for generate_all_potcar.sh to
    concatenate.
    """
    def _build(elements: tuple[str, ...] = ("Cr", "Si", "Te")) -> Path:
        psp_root = tmp_path / "fake_psp"
        psp_root.mkdir(parents=True, exist_ok=True)
        for el in elements:
            el_dir = psp_root / el
            el_dir.mkdir(parents=True, exist_ok=True)
            (el_dir / "POTCAR").write_text(f"FAKE POTCAR FOR {el}\n")
        return psp_root

    return _build
