from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from evals.opentrajectory.harness.strategy_phase1_3 import phase3_llm_judge as judge
from evals.opentrajectory.harness.strategy_phase1_3 import run_strategy_phase1_3 as runner

BASE_MANIFEST = {
    "case_id": "judge-pass-balanced-strategy",
    "skill_name": "retrieval-matrix-generation",
    "execution_mode": "executed",
    "handling_class": "normal_execute",
    "baseline_eligible": True,
    "scenario_b_placeholder": False,
    "eval_context": {},
    "artifacts": {"output_path": "output.json"},
    "deterministic_result_path": "deterministic_result.json",
}

BASE_OUTPUT = {
    "structured_requirement": {"query": "balanced strategy"},
    "constraint_interpretation": {"constraints": ["recency"]},
    "retrieval_matrix": {"expanded_query_variants": [{"query": "balanced strategy"}]},
}


def _write_case(case_dir: Path, manifest: dict, output: dict) -> None:
    case_dir.mkdir(parents=True, exist_ok=True)
    (case_dir / "case_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (case_dir / "output.json").write_text(
        json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (case_dir / "deterministic_result.json").write_text(
        json.dumps(
            {"overall_status": "passed", "checks": [{"check_id": "COM-002", "status": "passed"}]}
        ),
        encoding="utf-8",
    )


def test_load_judge_input_and_validate(tmp_path: Path):
    case_dir = tmp_path / "case"
    _write_case(case_dir, copy.deepcopy(BASE_MANIFEST), copy.deepcopy(BASE_OUTPUT))
    payload = judge.load_judge_input(case_dir)
    assert payload["skill_name"] == "retrieval-matrix-generation"
    assert payload["deterministic_checks"]["passed"] is True
    assert payload["retrieval_matrix"]["expanded_query_variants"][0]["query"] == "balanced strategy"


def test_llm_judge_flag_requires_replay_case_dir(capsys: pytest.CaptureFixture[str]):
    rc = runner.main(["--llm-judge"])
    captured = capsys.readouterr()
    assert rc == 2
    assert "requires --replay-case-dir" in captured.err


@pytest.mark.parametrize(("verdict", "expected_exit"), [("pass", 0), ("review", 2), ("fail", 1)])
def test_llm_judge_on_replay_case_calls_judge(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    verdict,
    expected_exit,
):
    case_dir = tmp_path / "case"
    _write_case(case_dir, copy.deepcopy(BASE_MANIFEST), copy.deepcopy(BASE_OUTPUT))
    (case_dir / "case_manifest.json").write_text(
        json.dumps(
            {
                **BASE_MANIFEST,
                "artifacts": {"output_path": "output.json"},
                "deterministic_result": {"passed": True, "checks": []},
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    calls: list[str] = []

    def fake_eval(_case_dir: Path, _schema_path: Path):
        calls.append("deterministic")
        return {
            "case_id": "judge-pass-balanced-strategy",
            "skipped": False,
            "deterministic_result_path": "deterministic_result.json",
            "result": {"overall_status": "passed", "baseline_eligible": True},
        }

    def fake_run_judge(_case_dir: Path):
        calls.append("judge")
        return (
            {"case_id": "judge-pass-balanced-strategy"},
            {"verdict": verdict, "score_total": {"pass": 10, "review": 6, "fail": 3}[verdict]},
        )

    monkeypatch.setattr(runner, "evaluate_phase3_replay_case", fake_eval)
    monkeypatch.setattr(runner, "run_judge", fake_run_judge)
    rc = runner.main(["--replay-case-dir", str(case_dir), "--llm-judge"])
    out = capsys.readouterr().out
    assert rc == expected_exit
    assert calls == ["deterministic", "judge"]
    assert (case_dir / "judge-input.json").exists()
    assert (case_dir / "judge-result.json").exists()
    assert '"mode": "phase3_llm_judge"' in out


def test_llm_judge_skips_when_deterministic_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    case_dir = tmp_path / "case"
    _write_case(case_dir, copy.deepcopy(BASE_MANIFEST), copy.deepcopy(BASE_OUTPUT))

    def fake_eval(_case_dir: Path, _schema_path: Path):
        return {
            "case_id": "judge-pass-balanced-strategy",
            "skipped": False,
            "deterministic_result_path": "deterministic_result.json",
            "result": {"overall_status": "failed", "baseline_eligible": False},
        }

    called = {"judge": False}

    def fake_run_judge(_case_dir: Path):
        called["judge"] = True
        return ({}, {})

    monkeypatch.setattr(runner, "evaluate_phase3_replay_case", fake_eval)
    monkeypatch.setattr(runner, "run_judge", fake_run_judge)
    rc = runner.main(["--replay-case-dir", str(case_dir), "--llm-judge"])
    captured = capsys.readouterr()
    assert rc == 1
    assert called["judge"] is False
    assert '"overall_status": "failed"' in captured.out


def test_validate_only_and_dry_run_unchanged(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    calls: list[str] = []

    class DummyCase:
        def __init__(self):
            self.record = {
                "case_id": "case-1",
                "execution_policy": {"executable": True},
                "expected": {"phase3": {"handling_class": "normal_execute"}},
            }
            self.case_id = "case-1"
            self.dataset_split = "core"

    class DummyDataset:
        dataset_id = "strategy-phase1-3"
        dataset_version = "v-test"
        dataset_files = {"core": "core.jsonl"}
        dataset_hashes = {"core": "abc"}
        cases = [DummyCase()]

    monkeypatch.setattr(
        runner, "load_strategy_phase1_3_dataset", lambda _p: calls.append("load") or DummyDataset()
    )
    monkeypatch.setattr(runner, "_validate_scenario_b", lambda _d: calls.append("validate"))
    monkeypatch.chdir(tmp_path)
    rc = runner.main(["--output-root", str(tmp_path / "runs"), "--validate-only"])
    captured = capsys.readouterr()
    assert rc == 0
    assert calls == ["load", "validate"]
    assert '"status": "validated"' in captured.out


@pytest.mark.parametrize(
    "evidence",
    [
        None,
        {"overall_status": "failed", "checks": [{"check_id": "COM-002", "status": "failed"}]},
        {"overall_status": "passed", "checks": []},
    ],
)
def test_missing_or_failed_structural_evidence_never_reaches_judge(monkeypatch, tmp_path, evidence):
    case_dir = tmp_path / "case"
    _write_case(case_dir, copy.deepcopy(BASE_MANIFEST), copy.deepcopy(BASE_OUTPUT))
    result_file = case_dir / "deterministic_result.json"
    if evidence is None:
        result_file.unlink()
    else:
        result_file.write_text(json.dumps(evidence), encoding="utf-8")

    def never_call(_input):
        pytest.fail("missing or failed structural evidence must block the judge")

    monkeypatch.setattr(judge, "evaluate_judge_with_llm", never_call)
    with pytest.raises(judge.Phase3JudgeError):
        judge.run_judge(case_dir)
    assert not (case_dir / "judge-result.json").exists()


@pytest.mark.parametrize("field", ["deterministic_result_path", "output_path"])
def test_judge_cannot_read_artifacts_outside_case(tmp_path, field):
    case_dir = tmp_path / "case"
    manifest = copy.deepcopy(BASE_MANIFEST)
    if field == "output_path":
        manifest["artifacts"][field] = "../outside.json"
    else:
        manifest[field] = "../outside.json"
    _write_case(case_dir, manifest, copy.deepcopy(BASE_OUTPUT))
    with pytest.raises(judge.Phase3JudgeError):
        judge.load_judge_input(case_dir)
