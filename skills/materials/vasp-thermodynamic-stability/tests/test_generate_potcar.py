"""Linux smoke for the production POTCAR generator.

The production tool is a Bash/HPC utility. Windows local pytest is not its
runtime authority; this test runs on POSIX/Linux and is intentionally skipped
on Windows.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
SCRIPT = (
    REPO_ROOT
    / "skills"
    / "materials"
    / "vasp-thermodynamic-stability"
    / "scripts"
    / "generate_all_potcar.sh"
)


def _write_fake_psp(
    root: Path,
    elements: tuple[str, ...] = ("Cr", "Si", "Te"),
) -> None:
    for element in elements:
        target = root / element
        target.mkdir(parents=True, exist_ok=True)
        (target / "POTCAR").write_text(
            f"FAKE POTCAR FOR {element}\n",
            encoding="utf-8",
        )


@pytest.mark.smoke
@pytest.mark.skipif(os.name == "nt", reason="production POTCAR generator is a POSIX Bash/HPC utility")
def test_generate_potcar_sh_smoke(tmp_path: Path) -> None:
    assert SCRIPT.is_file()

    script_under_test = tmp_path / "generate_all_potcar.sh"
    script_under_test.write_bytes(SCRIPT.read_bytes())

    missing = subprocess.run(
        ["bash", str(script_under_test)],
        cwd=tmp_path,
        env={**os.environ, "VASP_PSP_DIR": ""},
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert missing.returncode != 0
    assert "ERROR" in (missing.stdout + missing.stderr).upper()

    psp_root = tmp_path / "fake_psp"
    _write_fake_psp(psp_root)

    phase_dir = tmp_path / "CrSiTe3_5at"
    (phase_dir / "relax").mkdir(parents=True)
    (phase_dir / "static").mkdir(parents=True)

    (tmp_path / "submission_info.json").write_text(
        json.dumps(
            [
                {
                    "formula": "CrSiTe3",
                    "dirname": "CrSiTe3_5at",
                    "natoms": 5,
                    "elements": ["Cr", "Si", "Te"],
                    "kpoints_relax": [4, 4, 4],
                    "kpoints_static": [6, 6, 6],
                    "is_target": True,
                    "spacegroup": "P1",
                }
            ]
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        ["bash", str(script_under_test), "--psp-dir", "./fake_psp"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert result.returncode == 0, result.stdout + result.stderr

    relax_potcar = phase_dir / "relax" / "POTCAR"
    static_potcar = phase_dir / "static" / "POTCAR"
    assert relax_potcar.is_file()
    assert static_potcar.is_file()

    content = relax_potcar.read_text(encoding="utf-8")
    assert "FAKE POTCAR FOR Cr" in content
    assert "FAKE POTCAR FOR Si" in content
    assert "FAKE POTCAR FOR Te" in content
