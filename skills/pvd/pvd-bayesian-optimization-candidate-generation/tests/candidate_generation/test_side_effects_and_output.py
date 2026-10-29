from __future__ import annotations

import hashlib
import json
from pathlib import Path

import jsonschema

from helpers import validate_public_input

from pvd_bo.service import run_candidate_generation

ROOT = Path(__file__).resolve().parents[2]


def _hash_tree() -> dict[str, str]:
    result = {}
    for directory in (ROOT / "assets", ROOT / "model_profiles"):
        for path in sorted(item for item in directory.rglob("*") if item.is_file()):
            result[str(path.relative_to(ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def test_real_eligible_run_is_schema_valid_and_side_effect_free() -> None:
    before_files = sorted(str(p.relative_to(ROOT)) for p in ROOT.rglob("*") if p.is_file())
    before_hashes = _hash_tree()
    payload = json.loads((ROOT / "examples/current_system_request.json").read_text(encoding="utf-8"))
    validate_public_input(payload)
    result = run_candidate_generation(payload)
    assert result["state"] == "succeeded"
    assert result["candidate_generation_status"] == "generated"
    assert result["applicability"]["status"] == "eligible"
    assert len(result["candidates"]) == 10
    assert set(result["candidates"][0]) == {
        "candidate_rank",
        "parameters",
        "units",
        "predicted_target",
        "predictive_std",
        "acquisition_score",
        "selection_purpose",
    }
    assert set(result["candidates"][0]["parameters"]) == {
        "power", "n2_flow", "ar_flow", "deposition_time"
    }
    assert result["candidates"][0]["units"] == {
        "power": "W", "n2_flow": "sccm", "ar_flow": "sccm", "deposition_time": "s"
    }
    assert result["candidates"][0]["selection_purpose"] == (
        "exploration_exploitation_tradeoff_with_diversity"
    )
    assert result["training_summary"]["base_count"] == 63
    assert result["training_summary"]["project_count"] == 1
    assert result["training_summary"]["total_count"] == 64
    jsonschema.validate(
        result,
        json.loads((ROOT / "output.schema.json").read_text(encoding="utf-8")),
    )
    after_files = sorted(str(p.relative_to(ROOT)) for p in ROOT.rglob("*") if p.is_file())
    after_hashes = _hash_tree()
    # Python may create __pycache__; no research/model asset may be written or altered.
    assert before_hashes == after_hashes
    assert not any(path.startswith("outputs/") or path.startswith("data/backups/") for path in after_files)
    assert set(before_files).issubset(set(after_files))
