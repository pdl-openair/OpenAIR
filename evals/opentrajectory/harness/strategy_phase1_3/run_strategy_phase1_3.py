from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from evals.opentrajectory.harness.common.dataset_loader import DatasetLoaderError, load_strategy_phase1_3_dataset
from evals.opentrajectory.harness.common.run_manifest import (
    CaseManifest,
    RunManifest,
    case_manifest_dict,
    now_iso,
    run_manifest_dict,
    write_json,
)
from evals.opentrajectory.harness.strategy_phase1_3.phase3_deterministic_adapter import (
    Phase3ReplayAdapterError,
    evaluate_phase3_replay_case,
)
from evals.opentrajectory.harness.strategy_phase1_3.phase3_llm_judge import Phase3JudgeError, run_judge

RUNNER_VERSION = "strategy-phase1-3-dry-run-v0.1"
EVAL_SUITE_VERSION = "strategy-phase1-3-v0.2.1"
DEFAULT_DATASET_DIR = Path("evals/opentrajectory/datasets/strategy-phase1-3")
DEFAULT_OUTPUT_ROOT = Path("evals/opentrajectory/runs/strategy-phase1-3")
SCENARIO_B_CASE_ID = "RMX-REG-2026-06-01-SCENARIO-B"
SCENARIO_B_SKIP_REASON = "scenario_b_strong_placeholder_guard"


def _validate_scenario_b(dataset) -> None:
    target = next((c.record for c in dataset.cases if c.case_id == SCENARIO_B_CASE_ID), None)
    if target is None:
        raise DatasetLoaderError(f"Missing Scenario B placeholder case: {SCENARIO_B_CASE_ID}")
    policy = target.get("execution_policy")
    if not isinstance(policy, dict):
        raise DatasetLoaderError("Scenario B execution_policy must be an object")
    if target.get("record_type") != "non_executable_regression_placeholder":
        raise DatasetLoaderError("Scenario B record_type mismatch")
    expected_flags = {
        "executable": False,
        "eligible_for_regression_run": False,
        "eligible_for_baseline": False,
        "eligible_for_scoring": False,
        "eligible_for_fixture_generation": False,
    }
    for key, expected in expected_flags.items():
        if policy.get(key) is not expected:
            raise DatasetLoaderError(f"Scenario B policy mismatch for {key}: expected {expected}, got {policy.get(key)}")
    if target.get("misuse_is_hard_failure") is not True:
        raise DatasetLoaderError("Scenario B misuse_is_hard_failure must be true")


def _case_manifest_for_record(record: dict, split: str) -> dict:
    policy = record.get("execution_policy") if isinstance(record.get("execution_policy"), dict) else {}
    executable = bool(policy.get("executable", True))
    if executable:
        manifest = CaseManifest(
            case_id=record["case_id"],
            dataset_split=split,
            executable=True,
            routing_status="executable_not_run",
            handling_class=(record.get("expected", {}) or {}).get("phase3", {}).get("handling_class"),
            skill_execution_status="not_run",
            artifact_presence="not_applicable",
        )
        return case_manifest_dict(manifest)

    manifest = CaseManifest(
        case_id=record["case_id"],
        dataset_split=split,
        executable=False,
        routing_status="skipped_non_executable_placeholder",
        handling_class=(record.get("expected", {}) or {}).get("phase3", {}).get("handling_class"),
        skill_execution_status="not_run",
        artifact_presence="not_applicable",
        skip_reason=SCENARIO_B_SKIP_REASON if record["case_id"] == SCENARIO_B_CASE_ID else "non_executable_placeholder",
        eligible_for_regression_run=False,
        eligible_for_baseline=False,
        eligible_for_scoring=False,
        eligible_for_fixture_generation=False,
        misuse_is_hard_failure=bool(record.get("misuse_is_hard_failure", False)) if record["case_id"] == SCENARIO_B_CASE_ID else None,
    )
    return case_manifest_dict(manifest)


def build_summary(run_id: str, execution_mode: str, case_manifests: list[dict], elapsed_ms: int) -> dict:
    routing_counts = Counter(m["routing_status"] for m in case_manifests)
    split_counts = Counter(m["dataset_split"] for m in case_manifests)
    skipped = sum(1 for m in case_manifests if not m["executable"])
    invalid = routing_counts.get("invalid", 0)
    return {
        "run_id": run_id,
        "execution_mode": execution_mode,
        "total_case_count": len(case_manifests),
        "executable_case_count": sum(1 for m in case_manifests if m["executable"]),
        "skipped_case_count": skipped,
        "invalid_case_count": invalid,
        "split_counts": dict(split_counts),
        "routing_status_counts": dict(routing_counts),
        "scenario_b_guard_status": "passed" if routing_counts.get("skipped_non_executable_placeholder", 0) == 1 else "failed",
        "elapsed_ms": elapsed_ms,
    }


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Dry-run routing harness for OpenTrajectory strategy phase1-3")
    parser.add_argument("--dataset-dir", type=Path, default=DEFAULT_DATASET_DIR)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--replay-case-dir", type=Path, default=None)
    parser.add_argument("--llm-judge", action="store_true")
    return parser.parse_args(argv)


def _phase3_schema_path() -> Path:
    return REPO_ROOT / "skills" / "opentrajectory" / "retrieval-matrix-generation" / "output.schema.json"


def _write_judge_artifacts(case_dir: Path, judge_input: dict, judge_result: dict) -> None:
    write_json(case_dir / "judge-input.json", judge_input)
    write_json(case_dir / "judge-result.json", judge_result)


def _run_replay_case(case_dir: Path, use_llm_judge: bool) -> int:
    try:
        summary = evaluate_phase3_replay_case(case_dir, _phase3_schema_path())
    except (Phase3ReplayAdapterError, FileNotFoundError, json.JSONDecodeError, OSError) as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 2
    if summary.get("skipped"):
        print(
            json.dumps(
                {
                    "mode": "phase3_replay_deterministic",
                    "case_id": summary.get("case_id"),
                    "overall_status": "skipped",
                    "baseline_eligible": False,
                    "deterministic_result_path": None,
                },
                ensure_ascii=False,
            )
        )
        return 0
    result = summary.get("result") or {}
    if use_llm_judge and result.get("overall_status") == "passed":
        try:
            judge_input, judge_result = run_judge(case_dir)
            _write_judge_artifacts(case_dir, judge_input, judge_result)
            print(
                json.dumps(
                    {
                        "mode": "phase3_llm_judge",
                        "case_id": judge_input.get("case_id"),
                        "verdict": judge_result.get("verdict"),
                        "score_total": judge_result.get("score_total"),
                    },
                    ensure_ascii=False,
                )
            )
            if judge_result.get("verdict") != "pass":
                return 1 if judge_result.get("verdict") == "fail" else 2
        except Phase3JudgeError as exc:
            print(f"[error] {exc}", file=sys.stderr)
            return 2
    elif use_llm_judge:
        print(
            json.dumps(
                {
                    "mode": "phase3_llm_judge",
                    "case_id": summary.get("case_id"),
                    "verdict": "skipped",
                    "score_total": None,
                },
                ensure_ascii=False,
            )
        )
    print(
        json.dumps(
            {
                "mode": "phase3_replay_deterministic",
                "case_id": summary.get("case_id"),
                "overall_status": result.get("overall_status"),
                "assessment_scope": result.get("assessment_scope"),
                "scientific_quality_status": result.get("scientific_quality_status"),
                "baseline_eligible": result.get("baseline_eligible"),
                "deterministic_result_path": summary.get("deterministic_result_path"),
            },
            ensure_ascii=False,
        )
    )
    if result.get("overall_status") == "passed":
        return 0
    if result.get("overall_status") == "manual_review_required":
        return 2
    return 1


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.replay_case_dir is not None and args.validate_only:
        print("[error] --replay-case-dir cannot be combined with --validate-only", file=sys.stderr)
        return 2
    if args.llm_judge and args.replay_case_dir is None:
        print("[error] --llm-judge requires --replay-case-dir", file=sys.stderr)
        return 2
    if args.replay_case_dir is not None:
        return _run_replay_case(args.replay_case_dir, args.llm_judge)
    start = time.time()
    try:
        dataset = load_strategy_phase1_3_dataset(args.dataset_dir)
        _validate_scenario_b(dataset)
    except Exception as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1

    if args.validate_only:
        print(json.dumps({"status": "validated", "dataset_id": dataset.dataset_id, "eval_suite_version": EVAL_SUITE_VERSION, "dataset_version": dataset.dataset_version}, ensure_ascii=False))
        return 0

    run_id = time.strftime("strategy-phase1-3-%Y%m%d-%H%M%S", time.gmtime())
    run_root = args.output_root / run_id
    cases_root = run_root / "cases"
    cases_root.mkdir(parents=True, exist_ok=True)

    case_manifests: list[dict] = []
    for case in dataset.cases:
        case_root = cases_root / case.case_id
        case_root.mkdir(parents=True, exist_ok=True)
        manifest = _case_manifest_for_record(case.record, case.dataset_split)
        write_json(case_root / "case_manifest.json", manifest)
        case_manifests.append(manifest)

    elapsed_ms = int((time.time() - start) * 1000)
    run_manifest = RunManifest(
        run_id=run_id,
        timestamp=now_iso(),
        execution_mode="dry_run_routing_only",
        eval_suite_version=EVAL_SUITE_VERSION,
        dataset_id=dataset.dataset_id,
        dataset_version=dataset.dataset_version,
        dataset_files=dataset.dataset_files,
        dataset_hashes=dataset.dataset_hashes,
        runner_version=RUNNER_VERSION,
        runtime_commit_or_na="na",
        total_case_count=len(dataset.cases),
        executable_case_count=sum(1 for m in case_manifests if m["executable"]),
        skipped_case_count=sum(1 for m in case_manifests if not m["executable"]),
        invalid_case_count=sum(1 for m in case_manifests if m["routing_status"] == "invalid"),
        elapsed_ms=elapsed_ms,
    )
    write_json(run_root / "manifest.json", run_manifest_dict(run_manifest))
    summary = build_summary(run_id, "dry_run_routing_only", case_manifests, elapsed_ms)
    write_json(run_root / "summary.json", summary)
    print(json.dumps({"run_id": run_id, "output_root": str(run_root), "summary": summary}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
