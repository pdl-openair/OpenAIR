from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
TEST_DIR = Path(__file__).resolve().parent
if str(TEST_DIR) not in sys.path:
    sys.path.insert(0, str(TEST_DIR))

from gap_to_action_planner import (  # noqa: E402
    BFE_FIXED_FIELDS,
    REQUIRED_ACTION_FIELDS,
    REQUIRED_GAP_TYPES,
    load_suite,
    run_planner,
    validate_planner_output,
)

FIXTURE = REPO_ROOT / "skills/opentrajectory/tests/fixtures/p08b_3n_locked_suite.json"
EXPECTED = (
    REPO_ROOT
    / "artifacts/opentrajectory-p08b-3n-gap-to-action-harness-05b8bc1/before_after_output.json"
)


@pytest.fixture(scope="module")
def planner_output() -> dict:
    return run_planner(load_suite(FIXTURE))


def _case(output: dict, section: str, name: str) -> dict:
    return next(row for row in output[section]["cases"] if row["case"] == name)


def test_all_gap_types_emit_required_action_schema(planner_output: dict) -> None:
    seen = {row["gap_type"] for row in planner_output["action_rows"]}
    assert set(REQUIRED_GAP_TYPES) <= seen
    for row in planner_output["action_rows"]:
        assert set(REQUIRED_ACTION_FIELDS) <= set(row)
        assert row["post_action_status"] == row["current_status"]


def test_paperbench_action_remains_reproduction_workflow(planner_output: dict) -> None:
    paperbench = _case(planner_output, "treatment", "PaperBench bounded subcase")
    rendered = json.dumps(paperbench).lower()
    assert "reproduction" in rendered
    assert "scientific " + "validation" not in rendered
    assert "scientific " + "discovery" not in rendered


def test_assb_action_remains_reproducibility_grounded(planner_output: dict) -> None:
    assb = _case(planner_output, "treatment", "ASSB interlaboratory reproducibility")
    rendered = json.dumps(assb).lower()
    assert "reproducibility" in rendered
    for forbidden in ("dis" + "covery", "break" + "through", "universal " + "rule"):
        assert forbidden not in rendered


def test_bfe_fixed_status_preserved_before_and_after(planner_output: dict) -> None:
    for section in ("baseline", "treatment"):
        bfe = _case(planner_output, section, "BFE/F2DEM / Li||NCM811")
        assert bfe["role"] == "NEGATIVE_ONLY"
        for key, expected in BFE_FIXED_FIELDS.items():
            assert bfe["status_fields"][key] == expected
