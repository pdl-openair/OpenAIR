from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from evals.opentrajectory.harness.strategy_phase1_3 import run_strategy_phase1_3 as runner


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_case(case_dir: Path, manifest: dict[str, Any], output: dict[str, Any]) -> None:
    case_dir.mkdir(parents=True, exist_ok=True)
    _write_json(case_dir / "case_manifest.json", manifest)
    _write_json(case_dir / "output.json", output)


def _base_manifest(case_id: str) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "skill_name": "retrieval-matrix-generation",
        "execution_mode": "executed",
        "handling_class": "normal_execute",
        "baseline_eligible": True,
        "scenario_b_placeholder": False,
        "eval_context": {
            "core_nonempty_paths": ["expanded_query_variants"],
            "comparison_objects": [],
            "object_aliases": {},
            "pairwise_required": False,
            "required_source_constraints": [],
            "unconfirmed_items": [],
            "strict_terms": [],
            "negative_terms": [],
            "fallback_used": False,
        },
        "artifacts": {
            "output_path": "output.json",
        },
    }


def _schema_path() -> Path:
    return (
        _repo_root()
        / "skills"
        / "opentrajectory"
        / "retrieval-matrix-generation"
        / "output.schema.json"
    )


def _example_path() -> Path:
    return (
        _repo_root()
        / "skills"
        / "opentrajectory"
        / "retrieval-matrix-generation"
        / "examples"
        / "response.json"
    )


def test_replay_cli_accepts_repository_example_under_production_schema(tmp_path: Path) -> None:
    schema = _read_json(_schema_path())
    example = _read_json(_example_path())
    original_output_text = json.dumps(example, ensure_ascii=False, indent=2)

    case_dir = tmp_path / "pass-case"
    manifest = _base_manifest("phase3-production-schema-pass")
    _write_case(case_dir, manifest, copy.deepcopy(example))

    before_output = (case_dir / "output.json").read_text(encoding="utf-8")
    rc = runner.main(["--replay-case-dir", str(case_dir)])
    after_output = (case_dir / "output.json").read_text(encoding="utf-8")

    assert (
        len(example["expanded_query_variants"])
        >= schema["properties"]["expanded_query_variants"]["minItems"]
    )
    assert rc == 0
    assert before_output == after_output == original_output_text

    result_path = case_dir / "deterministic_result.json"
    assert result_path.exists()

    result = _read_json(result_path)
    assert result["overall_status"] == "passed"
    assert result["baseline_eligible"] is False
    assert result["scientific_quality_status"] == "not_evaluated"
    assert any(
        check["check_id"] == "COM-002" and check["status"] == "passed" for check in result["checks"]
    )

    manifest_after = _read_json(case_dir / "case_manifest.json")
    assert manifest_after["baseline_eligible"] is True
    assert manifest_after["deterministic_overall_status"] == "passed"
    assert manifest_after["deterministic_baseline_eligible"] is False
    assert manifest_after["deterministic_result_path"] == "deterministic_result.json"


def test_replay_cli_rejects_repository_example_when_query_variants_below_production_minimum(
    tmp_path: Path,
) -> None:
    example = _read_json(_example_path())
    truncated = copy.deepcopy(example)
    minimum = _read_json(_schema_path())["properties"]["expanded_query_variants"]["minItems"]
    assert minimum > 0
    truncated["expanded_query_variants"] = truncated["expanded_query_variants"][: minimum - 1]

    case_dir = tmp_path / "fail-case"
    manifest = _base_manifest("phase3-production-schema-mismatch")
    _write_case(case_dir, manifest, truncated)

    before_output = (case_dir / "output.json").read_text(encoding="utf-8")
    rc = runner.main(["--replay-case-dir", str(case_dir)])
    after_output = (case_dir / "output.json").read_text(encoding="utf-8")

    assert rc == 1
    assert before_output == after_output

    result_path = case_dir / "deterministic_result.json"
    assert result_path.exists()

    result = _read_json(result_path)
    assert result["overall_status"] == "failed"
    assert result["baseline_eligible"] is False

    com_002 = next(check for check in result["checks"] if check["check_id"] == "COM-002")
    assert com_002["status"] == "failed"
    assert com_002["failure_level"] == "hard_gate"
    evidence = json.dumps(com_002.get("evidence") or {}, ensure_ascii=False)
    message = str(com_002.get("message") or "")
    assert "expanded_query_variants" in evidence or "expanded_query_variants" in message

    manifest_after = _read_json(case_dir / "case_manifest.json")
    assert manifest_after["baseline_eligible"] is True
    assert manifest_after["deterministic_overall_status"] == "failed"
    assert manifest_after["deterministic_baseline_eligible"] is False
    assert manifest_after["deterministic_result_path"] == "deterministic_result.json"
