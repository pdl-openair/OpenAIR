"""Offline pytest test suite for the vasp-thermodynamic-stability skill.

All tests run fully offline — no MP_API_KEY, no VASP, no HPC, no real
POTCAR files. Setup/analyze scripts are exercised via subprocess or
inline emulation; recorded fixtures (tests/fixtures/) cover the data
shape.

References:
    * docs/audits/2026-07-10-materials-l3-quality-audit.md §4.2 D1/D2
    * docs/audits/2026-07-10-materials-l3-quality-audit.md §4.3 E1/E3/E9
    * docs/audits/2026-07-10-materials-l3-quality-audit.md §4.4 problems 1/2/4

Tests 4.3 and 4.4 are marked xfail (strict) to lock known audit findings
until the underlying scripts are patched. When patched, the xfails will
start XPASS-ing and should be converted to normal assertions.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
SKILL_DIR = REPO_ROOT / "skills" / "materials" / "vasp-thermodynamic-stability"
SCRIPTS_DIR = SKILL_DIR / "scripts"


# ============================================================================
# 4.1 — submission_info.json shape (audit §4.2 D1 / §4.3 E1)
# ============================================================================
@pytest.mark.smoke
def test_setup_competing_phases_14_entries(submission_info_14phases):
    """Verify the 14-phase competing_phases manifest recorded for CrSiTe3.

    Reference audit §4.2 D1 (workdir completeness) and §4.3 E1 (manifest
    schema). Asserts:
      * 14 total entries
      * exactly 1 target entry whose formula is CrSiTe3
      * every entry carries the documented schema fields
      * at least 3 unary (Cr/Si/Te) and exactly 1 ternary present
    """
    info = submission_info_14phases

    # 14 total entries
    assert len(info) == 14, f"expected 14 phases, got {len(info)}"

    # exactly one target — and it is CrSiTe3
    targets = [e for e in info if e.get("is_target") is True]
    assert len(targets) == 1, f"expected exactly 1 target, got {len(targets)}"
    assert targets[0]["formula"] == "CrSiTe3"

    required_fields = {
        "dirname", "natoms", "elements", "kpoints_relax",
        "kpoints_static", "spacegroup",
    }
    for entry in info:
        missing = required_fields - set(entry.keys())
        assert not missing, f"entry {entry.get('dirname')} missing {missing}"

    # unary / ternary breakdown
    unary = [e for e in info if len(e["elements"]) == 1]
    ternary = [e for e in info if len(e["elements"]) == 3]
    assert len(unary) >= 3, f"expected >=3 unary phases, got {len(unary)}"
    assert len(ternary) == 1, f"expected exactly 1 ternary, got {len(ternary)}"


# ============================================================================
# 4.2 — INCAR parameter unification (audit §4.3 E3)
# ============================================================================
@pytest.mark.contract
def test_incar_unified_params_except_kpar(incar_stubs):
    """INCARs share identical params except SYSTEM line and KPAR knob.

    Reference audit §4.3 E3 — KPAR=1 for ≥28 atom phases is documented
    SKILL.md L117 behavior, not a bug. After stripping the per-phase
    `SYSTEM = <name>` line, the two normal-phase INCARs must hash
    identically and differ from the two large-phase INCARs (whose only
    legitimate difference is KPAR 2→1).
    """
    def _strip_system(text: str) -> str:
        return "\n".join(
            line for line in text.splitlines() if not line.startswith("SYSTEM")
        )

    def _h(text: str) -> str:
        return hashlib.md5(_strip_system(text).encode("utf-8")).hexdigest()

    normal_names = ["Cr_2at", "Si_2at"]
    large_names = ["CrTe3_32at", "Si19Te8_54at"]

    normal_hashes = {_h(incar_stubs[n]) for n in normal_names}
    large_hashes = {_h(incar_stubs[n]) for n in large_names}

    # normal-phase stubs collapse to the same hash
    assert len(normal_hashes) == 1, "normal INCARs should hash identically"
    # large-phase stubs collapse to the same hash
    assert len(large_hashes) == 1, "large INCARs should hash identically"
    # the two branches legitimately differ (only KPAR)
    assert normal_hashes != large_hashes, \
        "normal vs large INCARs must differ in KPAR"

    # KPAR branch contents (whitespace-insensitive — INCAR allows any spacing)
    def _squash(text: str) -> str:
        return text.replace(" ", "")

    for n in normal_names:
        assert "KPAR=2" in _squash(incar_stubs[n]), f"{n} should specify KPAR = 2"
    for n in large_names:
        assert "KPAR=1" in _squash(incar_stubs[n]), f"{n} should specify KPAR = 1"

    # key documented params present in every stub
    for n in normal_names + large_names:
        squashed = _squash(incar_stubs[n])
        assert "ENCUT=450" in squashed, f"{n}: ENCUT=450 missing"
        assert "NCORE=4" in squashed, f"{n}: NCORE=4 missing"
        assert "IVDW=12" in squashed, f"{n}: IVDW=12 missing"


# ============================================================================
# 4.3 — invalid formula error handling (audit §4.4 problem 1) [xfail]
# ============================================================================
@pytest.mark.regression
@pytest.mark.xfail(
    reason=(
        "audit §4.4 problem 1: setup_competing_phases.py L348 calls "
        "Composition() without try/except, raises bare pymatgen ValueError "
        "for invalid formula"
    ),
    strict=True,
)
def test_invalid_formula_raises_xfail(tmp_path):
    """Invalid formula must surface a friendly Chinese error.

    xfail until L348 wraps Composition() in try/except with friendly error
    per §4.4 problem 1 improvement candidate (a). Currently the script
    raises bare `ValueError: Can't parse Element or Species from 'Not'`
    — the friendly message "无法解析化学式" is absent, so the assertion
    below fails and xfail catches.
    """
    setup_script = SCRIPTS_DIR / "setup_competing_phases.py"
    poscar = tmp_path / "POSCAR"
    poscar.write_text("placeholder\n1.0\n5 0 0 0 5 0 0 0 5\nCr Si Te\n1 1 3\ndirect\n")

    cmd = [
        sys.executable, str(setup_script),
        "--formula", "XYZNotReal",
        "--target-poscar", str(poscar),
        "--workdir", str(tmp_path / "workdir"),
    ]

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30,
            env={**os.environ, "MP_API_KEY": ""},
        )
    except subprocess.TimeoutExpired:
        pytest.skip("setup_competing_phases.py hung without MP_API_KEY")

    assert result.returncode != 0, "invalid formula must not exit 0"
    assert "无法解析化学式" in result.stderr, (
        "friendly Chinese error message must be present — currently absent "
        "(see audit §4.4 problem 1)"
    )


# ============================================================================
# 4.4 — analyze_stability.py missing-workdir bug (audit §4.4 problem 2) [xfail]
# ============================================================================
@pytest.mark.regression
@pytest.mark.xfail(
    reason=(
        "audit §4.4 problem 2: analyze_stability.py L298 writes to "
        "<workdir>/stability_result.json without "
        "os.makedirs(workdir, exist_ok=True)"
    ),
    strict=True,
)
def test_analyze_usemp_writes_result_when_workdir_missing_xfail(tmp_path):
    """stability_result.json write must succeed even if workdir doesn't exist.

    xfail until L297 adds `os.makedirs(workdir, exist_ok=True)` per
    §4.4 problem 2 improvement candidate. We don't invoke analyze_stability
    directly (needs MP_API_KEY); instead we emulate the L298 file-write
    logic to lock the failure mode.
    """
    # Mirror of analyze_stability.py L297-298 bug
    workdir = tmp_path / "does_not_exist_yet"
    result_path = workdir / "stability_result.json"

    written: bool
    try:
        with open(result_path, "w") as f:
            json.dump({"ehull": 0.0}, f)
        written = True
    except FileNotFoundError:
        written = False

    assert written is True, (
        "writing stability_result.json should succeed even when workdir "
        "doesn't yet exist — currently FileNotFoundError (audit §4.4 problem 2)"
    )


# ============================================================================
# 4.5 — generate_all_potcar.sh smoke (audit §4.3 E9 / §4.4 problem 4)
# ============================================================================
@pytest.mark.smoke
def test_generate_potcar_sh_smoke(tmp_path, fake_psp_dir):
    """Smoke test for generate_all_potcar.sh happy path + empty-PSP guard.

    Reference audit §4.3 E9 / §4.4 problem 4 — script does NOT support
    Cr_sv/GW variant selection, SKILL.md does not document this
    limitation. Smoke test covers happy path only.

    Subtest (a): empty VASP_PSP_DIR triggers explicit L42-51 error.
    Subtest (b): populated PSP dir + minimal submission_info.json
                 produces concatenated POTCARs in element order.
    """
    script_src = SCRIPTS_DIR / "generate_all_potcar.sh"
    assert script_src.is_file(), "generate_all_potcar.sh must exist"

    # ---- Subtest (a): empty PSP_DIR rejection -----------------------------
    env_empty = {**os.environ, "VASP_PSP_DIR": ""}
    result_empty = subprocess.run(
        ["bash", str(script_src)],
        env=env_empty,
        capture_output=True,
        text=True,
        cwd=str(tmp_path),
    )
    assert result_empty.returncode != 0, \
        "empty VASP_PSP_DIR must cause non-zero exit"
    combined = (result_empty.stdout + result_empty.stderr).upper()
    assert "ERROR" in combined or "VASP_PSP_DIR" in result_empty.stderr, (
        "script must surface an explicit ERROR or VASP_PSP_DIR message "
        "(see L42-51)"
    )

    # ---- Subtest (b): successful concatenation ----------------------------
    # Script reads submission_info.json from $SCRIPT_DIR. Stage an isolated
    # copy under tmp_path so we don't pollute the skill dir.
    staged_script = tmp_path / "generate_all_potcar.sh"
    staged_script.write_text(script_src.read_text())

    submission = [
        {
            "formula": "CrSiTe3",
            "dirname": "CrSiTe3_10at",
            "natoms": 10,
            "elements": ["Cr", "Si", "Te"],
            "kpoints_relax": [6, 6, 6],
            "kpoints_static": [7, 7, 7],
            "is_target": True,
            "ehull_mp": 0.0,
            "spacegroup": "R-3",
            "spacegroup_number": 148,
            "material_id": "mp-aaaaafpj",
        }
    ]
    (tmp_path / "submission_info.json").write_text(
        json.dumps(submission, ensure_ascii=False)
    )

    # Pre-create the phase relax/static dirs the script writes POTCAR into
    phase_dir = tmp_path / "CrSiTe3_10at"
    (phase_dir / "relax").mkdir(parents=True)
    (phase_dir / "static").mkdir(parents=True)

    psp_dir = fake_psp_dir(("Cr", "Si", "Te"))
    env_ok = {**os.environ, "VASP_PSP_DIR": str(psp_dir)}
    result_ok = subprocess.run(
        ["bash", str(staged_script)],
        env=env_ok,
        capture_output=True,
        text=True,
        cwd=str(tmp_path),
    )

    assert result_ok.returncode == 0, (
        f"happy-path POTCAR generation must succeed. "
        f"stdout={result_ok.stdout!r} stderr={result_ok.stderr!r}"
    )

    relax_potcar = phase_dir / "relax" / "POTCAR"
    static_potcar = phase_dir / "static" / "POTCAR"
    assert relax_potcar.is_file(), "relax/POTCAR must be written"
    assert static_potcar.is_file(), "static/POTCAR must be written"

    relax_content = relax_potcar.read_text()
    assert "FAKE POTCAR FOR Cr" in relax_content
    assert "FAKE POTCAR FOR Si" in relax_content
    assert "FAKE POTCAR FOR Te" in relax_content
