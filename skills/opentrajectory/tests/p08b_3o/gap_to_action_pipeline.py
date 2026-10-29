"""Compatibility shim for the historical P08B-3O import path."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from skills.opentrajectory.gap_to_action.planner import REQUIRED_GAP_TYPES  # noqa: E402
from skills.opentrajectory.gap_to_action.pipeline import (  # noqa: E402
    ACTION_DETAIL_BY_GAP_TYPE,
    GAP_TYPE_BY_VISIBLE_SLOT,
    REQUIRED_PIPELINE_FORBIDDEN_TERMS,
    build_planner_suite_from_research_lead_package as _build_planner_suite,
    run_gap_to_action_pipeline as _run_pipeline,
    validate_pipeline_output as _validate_pipeline_output,
)

P08B_AUTHENTIC_GAP_NORMALIZER_PATH = (
    REPO_ROOT / "scripts" / "opentrajectory" / "p08b_authentic_gap_normalizer.py"
)


def _load_authentic_gap_normalizer():
    spec = importlib.util.spec_from_file_location(
        "p08b_authentic_gap_normalizer",
        P08B_AUTHENTIC_GAP_NORMALIZER_PATH,
    )
    if spec is None or spec.loader is None:
        raise ImportError(
            f"Cannot load authentic gap normalizer from {P08B_AUTHENTIC_GAP_NORMALIZER_PATH}"
        )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _legacy_normalizer_for(package: dict[str, Any]):
    if package.get("contract_type") != "authentic_research_lead_package":
        return None
    return _load_authentic_gap_normalizer()


def build_planner_suite_from_research_lead_package(package: dict[str, Any]) -> dict[str, Any]:
    return _build_planner_suite(package, normalizer=_legacy_normalizer_for(package))


def run_gap_to_action_pipeline(package: dict[str, Any]) -> dict[str, Any]:
    return _run_pipeline(
        package,
        normalizer=_legacy_normalizer_for(package),
        required_gap_types=REQUIRED_GAP_TYPES,
    )


def validate_pipeline_output(output: dict[str, Any]) -> None:
    _validate_pipeline_output(output, required_gap_types=REQUIRED_GAP_TYPES)


def write_pipeline_output(package: dict[str, Any], output_path: Path) -> dict[str, Any]:
    output = run_gap_to_action_pipeline(package)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return output
