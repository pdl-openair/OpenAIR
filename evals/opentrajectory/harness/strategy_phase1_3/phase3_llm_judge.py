from __future__ import annotations

import json
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any

from evals.opentrajectory.harness.common.run_manifest import write_json
from evals.opentrajectory.harness.strategy_phase1_3.phase3_deterministic_adapter import (
    Phase3ReplayAdapterError,
    _safe_output_path,
)
from services.api.app.execution.llm_structured_runner import run_skill_with_llm

# LLM judge prompt/schema assets are deferred; see the judge version's README.
JUDGE_VERSION = "phase3-retrieval-matrix-llm-judge-v0"


class Phase3JudgeError(RuntimeError):
    pass


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise Phase3JudgeError(f"Missing JSON file: {path}") from exc
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise Phase3JudgeError(
            f"Invalid JSON in {path.name}: {exc.msg} (line {exc.lineno}, column {exc.colno})"
        ) from exc
    if not isinstance(data, dict):
        raise Phase3JudgeError(f"Expected JSON object in {path}")
    return data


def _validate_judge_input(judge_input: dict[str, Any]) -> None:
    required = {
        "case_id",
        "skill_name",
        "structured_requirement",
        "constraint_interpretation",
        "retrieval_matrix",
        "deterministic_checks",
    }
    missing = sorted(required - set(judge_input))
    if missing:
        raise Phase3JudgeError(f"Judge input missing required fields: {', '.join(missing)}")
    if judge_input.get("skill_name") != "retrieval-matrix-generation":
        raise Phase3JudgeError(f"Unsupported skill_name: {judge_input.get('skill_name')}")
    deterministic = judge_input.get("deterministic_checks") or {}
    if not isinstance(deterministic, dict) or deterministic.get("passed") is not True:
        raise Phase3JudgeError("Judge input requires deterministic_checks.passed to be true")


def _judge_root() -> Path:
    root = (
        Path(__file__).resolve().parents[4]
        / "evals"
        / "opentrajectory"
        / "phase3-retrieval-matrix-llm-judge-v0"
    )
    required = ("judge-prompt-v0.md", "judge-output.schema.json")
    missing = [name for name in required if not (root / name).is_file()]
    if missing:
        raise Phase3JudgeError(
            "Phase3 LLM judge assets are deferred; missing: "
            + ", ".join(missing)
            + f". Expected under {root}. See README.md for the follow-up task."
        )
    return root


def _build_judge_input(case_dir: Path) -> dict[str, Any]:
    case_dir = Path(case_dir)
    manifest = _read_json(case_dir / "case_manifest.json")
    output_path = (manifest.get("artifacts", {}) or {}).get("output_path", "output.json")
    result_path = manifest.get("deterministic_result_path")
    if not isinstance(result_path, str) or not result_path.strip():
        raise Phase3JudgeError("Judge requires persisted deterministic evidence")
    try:
        deterministic = _read_json(_safe_output_path(case_dir, result_path))
        output_payload = _read_json(_safe_output_path(case_dir, output_path))
    except Phase3ReplayAdapterError as exc:
        raise Phase3JudgeError(str(exc)) from exc
    if deterministic.get("overall_status") != "passed" or not deterministic.get("checks"):
        raise Phase3JudgeError("Judge requires passed structural checks with evidence")
    judge_input = {
        "case_id": manifest.get("case_id"),
        "skill_name": "retrieval-matrix-generation",
        "structured_requirement": deepcopy(
            output_payload.get("structured_requirement") or output_payload.get("input") or {}
        ),
        "constraint_interpretation": deepcopy(
            output_payload.get("constraint_interpretation") or {}
        ),
        "retrieval_matrix": deepcopy(
            output_payload.get("retrieval_matrix") or output_payload.get("search_matrix") or {}
        ),
        "deterministic_checks": {"passed": True, "checks": deepcopy(deterministic["checks"])},
    }
    _validate_judge_input(judge_input)
    return judge_input


def load_judge_input(case_dir: Path) -> dict[str, Any]:
    return _build_judge_input(case_dir)


def _dimension(score: int, rationale: str, evidence_refs: list[str]) -> dict[str, Any]:
    return {"score": score, "rationale": rationale, "evidence_refs": evidence_refs}


def _issues(issue_rows: list[tuple[str, str, str, list[str], str]]) -> list[dict[str, Any]]:
    return [
        {
            "severity": severity,
            "code": code,
            "description": description,
            "evidence_refs": refs,
            "recommended_fix": fix,
        }
        for severity, code, description, refs, fix in issue_rows
    ]


def _validate_judge_result(result: dict[str, Any]) -> None:
    if result.get("judge_version") != JUDGE_VERSION:
        raise Phase3JudgeError(f"Unexpected judge_version: {result.get('judge_version')}")
    if not isinstance(result.get("score_total"), int):
        raise Phase3JudgeError("Judge result missing integer score_total")
    dimension_scores = result.get("dimension_scores")
    if not isinstance(dimension_scores, dict):
        raise Phase3JudgeError("Judge result missing dimension_scores")
    expected_keys = [
        "intent_alignment",
        "semantic_coverage_and_balance",
        "constraint_operationalization",
        "query_portfolio_quality",
        "recall_precision_tradeoff",
    ]
    if sorted(dimension_scores) != sorted(expected_keys):
        raise Phase3JudgeError("Judge result dimension_scores shape mismatch")
    score_sum = 0
    for key in expected_keys:
        dimension = dimension_scores.get(key)
        if not isinstance(dimension, dict):
            raise Phase3JudgeError(f"Judge result dimension missing object: {key}")
        if set(dimension) != {"score", "rationale", "evidence_refs"}:
            raise Phase3JudgeError(f"Judge result dimension shape mismatch: {key}")
        if dimension.get("score") not in {0, 1, 2}:
            raise Phase3JudgeError(f"Judge result dimension score out of range: {key}")
        score_sum += dimension.get("score", -1)
    if score_sum != result.get("score_total"):
        raise Phase3JudgeError("Judge result score_total does not match dimension_scores sum")
    verdict = result.get("verdict")
    if verdict not in {"pass", "review", "fail"}:
        raise Phase3JudgeError(f"Unexpected verdict: {verdict}")
    issues = result.get("issues")
    if not isinstance(issues, list):
        raise Phase3JudgeError("Judge result issues must be an array")
    if (
        verdict == "fail"
        and result.get("score_total") > 5
        and not any(
            isinstance(issue, dict) and issue.get("severity") == "critical" for issue in issues
        )
    ):
        raise Phase3JudgeError("Judge verdict fail is inconsistent with rubric")
    if verdict == "pass" and result.get("score_total") < 8:
        raise Phase3JudgeError("Judge verdict pass is inconsistent with rubric")


def evaluate_judge_with_llm(judge_input: dict[str, Any]) -> dict[str, Any]:
    judge_root = _judge_root()
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        skill_dir = tmpdir_path / "judge-skill"
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(
            (judge_root / "judge-prompt-v0.md")
            .read_text(encoding="utf-8")
            .replace("{{JUDGE_INPUT_JSON}}", json.dumps(judge_input, ensure_ascii=False, indent=2)),
            encoding="utf-8",
        )
        (skill_dir / "output.schema.json").write_text(
            (judge_root / "judge-output.schema.json").read_text(encoding="utf-8"), encoding="utf-8"
        )
        (skill_dir / "skill.yaml").write_text(
            "output_file: judge-result.json\nllm:\n  model: gpt-5-mini\n"
            "  temperature: 0\n  max_completion_tokens: 3000\n"
            "  reasoning_effort: minimal\n",
            encoding="utf-8",
        )
        workspace_dir = tmpdir_path / "workspace"
        workspace_dir.mkdir(parents=True, exist_ok=True)
        output_dict, _elapsed, _diagnostics = run_skill_with_llm(
            skill_dir=skill_dir,
            input_payload={"judge_input": judge_input},
            workspace_dir=workspace_dir,
            task_id="phase3-retrieval-matrix-judge",
        )
    if not isinstance(output_dict, dict):
        raise Phase3JudgeError("LLM judge did not return a JSON object")
    _validate_judge_result(output_dict)
    return output_dict


def run_judge(case_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    case_dir = Path(case_dir)
    judge_input = load_judge_input(case_dir)
    judge_result = evaluate_judge_with_llm(judge_input)
    write_json(case_dir / "judge-input.json", judge_input)
    write_json(case_dir / "judge-result.json", judge_result)
    return judge_input, judge_result
