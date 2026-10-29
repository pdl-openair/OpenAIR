from __future__ import annotations

from pathlib import Path
import subprocess
import sys

import pytest


PRODUCT_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = Path(__file__).resolve().parents[5]


def test_product_module_is_not_scanner_visible_before_track_c() -> None:
    """Track B is a Python product module; formal Skill metadata belongs to Track C wiring."""
    assert PRODUCT_DIR.is_dir()
    assert (PRODUCT_DIR / "README.md").is_file()
    assert not (PRODUCT_DIR / "SKILL.md").exists()
    assert not (PRODUCT_DIR / "skill.yaml").exists()


def test_real_registry_check_reports_no_gap_to_action_entry_or_error() -> None:
    """Exercise the production registry CLI when the complete repository is available."""
    register = REPO_ROOT / "scripts" / "register_skills.py"
    if not register.is_file():
        pytest.skip("full-repository registry CLI is absent from the supplied authoring slice")

    proc = subprocess.run(
        [sys.executable, str(register), "check", "--verbose"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    combined = f"{proc.stdout}\n{proc.stderr}".lower()
    # Baseline has pre-existing errors/drift, so exit code is intentionally not asserted.
    assert "gap_to_action" not in combined
    assert "gap-to-action" not in combined


def test_product_import_survives_registry_metadata_removal() -> None:
    import skills.opentrajectory.gap_to_action as product

    assert callable(product.build_research_lead_package)
    assert callable(product.plan_gap_to_actions)
    assert callable(product.run_gap_to_action_pipeline)
    assert callable(product.run_gap_to_action_adapter)
