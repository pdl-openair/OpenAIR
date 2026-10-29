from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from evals.opentrajectory.harness.strategy_phase1_3 import run_strategy_phase1_3 as runner

VALID_OUTPUT = json.loads(
    (
        Path(__file__).resolve().parents[4]
        / "skills/opentrajectory/retrieval-matrix-generation/examples/response.json"
    ).read_text(encoding="utf-8")
)

BASE_MANIFEST = {
    "case_id": "phase3-normal-pass",
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
    "artifacts": {"output_path": "output.json"},
}


def _write_case(case_dir: Path, manifest: dict, output: dict | None) -> None:
    case_dir.mkdir(parents=True, exist_ok=True)
    (case_dir / "case_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if output is not None:
        (case_dir / "output.json").write_text(
            json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8"
        )


def test_cli_replay_pass(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    case_dir = tmp_path / "case"
    _write_case(case_dir, copy.deepcopy(BASE_MANIFEST), VALID_OUTPUT)
    rc = runner.main(["--replay-case-dir", str(case_dir)])
    out = capsys.readouterr().out.strip()
    assert rc == 0
    assert json.loads(out)["overall_status"] == "passed"
    assert (case_dir / "deterministic_result.json").exists()
    manifest = json.loads((case_dir / "case_manifest.json").read_text(encoding="utf-8"))
    assert manifest["deterministic_overall_status"] == "passed"
    assert manifest["deterministic_baseline_eligible"] is False


def test_cli_replay_failed(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    case_dir = tmp_path / "case"
    bad_output = copy.deepcopy(VALID_OUTPUT)
    bad_output.pop("expanded_query_variants", None)
    _write_case(case_dir, copy.deepcopy(BASE_MANIFEST), bad_output)
    rc = runner.main(["--replay-case-dir", str(case_dir)])
    out = capsys.readouterr().out.strip()
    assert rc == 1
    assert json.loads(out)["overall_status"] == "failed"
    manifest = json.loads((case_dir / "case_manifest.json").read_text(encoding="utf-8"))
    assert manifest["deterministic_overall_status"] == "failed"
    assert manifest["deterministic_baseline_eligible"] is False


def test_cli_manual_review_required(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    case_dir = tmp_path / "case"
    manifest = copy.deepcopy(BASE_MANIFEST)
    manifest["handling_class"] = "manual_review_required"
    _write_case(case_dir, manifest, VALID_OUTPUT)
    rc = runner.main(["--replay-case-dir", str(case_dir)])
    out = capsys.readouterr().out.strip()
    assert rc == 2
    assert json.loads(out)["overall_status"] == "manual_review_required"
    manifest_after = json.loads((case_dir / "case_manifest.json").read_text(encoding="utf-8"))
    assert manifest_after["deterministic_baseline_eligible"] is False


def test_cli_scenario_b_skipped(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    case_dir = tmp_path / "case"
    manifest = copy.deepcopy(BASE_MANIFEST)
    manifest.update(
        {
            "execution_mode": "skipped",
            "handling_class": "scenario_b_placeholder",
            "baseline_eligible": False,
            "scenario_b_placeholder": True,
            "artifacts": {},
        }
    )
    _write_case(case_dir, manifest, None)
    rc = runner.main(["--replay-case-dir", str(case_dir)])
    out = capsys.readouterr().out.strip()
    assert rc == 0
    assert json.loads(out)["overall_status"] == "skipped"
    assert not (case_dir / "deterministic_result.json").exists()


def test_cli_common_skipped(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    case_dir = tmp_path / "case"
    manifest = copy.deepcopy(BASE_MANIFEST)
    manifest.update({"execution_mode": "skipped", "baseline_eligible": False})
    _write_case(case_dir, manifest, None)
    rc = runner.main(["--replay-case-dir", str(case_dir)])
    capsys.readouterr()
    assert rc == 0
    assert not (case_dir / "deterministic_result.json").exists()


def test_cli_validate_only_conflict(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    case_dir = tmp_path / "case"
    _write_case(case_dir, copy.deepcopy(BASE_MANIFEST), VALID_OUTPUT)
    rc = runner.main(["--replay-case-dir", str(case_dir), "--validate-only"])
    captured = capsys.readouterr()
    assert rc == 2
    assert "cannot be combined" in captured.err
    assert not (case_dir / "deterministic_result.json").exists()


def test_cli_invalid_replay_path(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    rc = runner.main(["--replay-case-dir", str(tmp_path / "missing")])
    captured = capsys.readouterr()
    assert rc == 2
    assert "Missing JSON file" in captured.err


def test_cli_default_behavior_unchanged(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    calls: list[str] = []

    class DummyCase:
        def __init__(self, case_id: str, split: str):
            self.record = {
                "case_id": case_id,
                "execution_policy": {"executable": True},
                "expected": {"phase3": {"handling_class": "normal_execute"}},
            }
            self.case_id = case_id
            self.dataset_split = split

    class DummyDataset:
        dataset_id = "strategy-phase1-3"
        dataset_version = "v-test"
        dataset_files = {"core": "core.jsonl"}
        dataset_hashes = {"core": "abc"}
        cases = [DummyCase("case-1", "core")]

    def fake_load(_):
        calls.append("load")
        return DummyDataset()

    def fake_validate(_):
        calls.append("validate")

    def fake_adapter(*_args, **_kwargs):
        calls.append("adapter")
        return {"skipped": True}

    monkeypatch.setattr(runner, "load_strategy_phase1_3_dataset", fake_load)
    monkeypatch.setattr(runner, "_validate_scenario_b", fake_validate)
    monkeypatch.setattr(runner, "evaluate_phase3_replay_case", fake_adapter)
    monkeypatch.chdir(tmp_path)
    rc = runner.main(["--output-root", str(tmp_path / "runs")])
    assert rc == 0
    assert calls == ["load", "validate"]
