from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from evals.opentrajectory.harness.common.deterministic_checks import run_common_checks
from evals.opentrajectory.harness.common.run_manifest import write_json
from evals.opentrajectory.harness.strategy_phase1_3.phase3_deterministic_checks import (
    run_phase3_deterministic_checks,
)

_REQUIRED_MANIFEST_FIELDS = {
    "case_id",
    "skill_name",
    "execution_mode",
    "handling_class",
    "baseline_eligible",
    "scenario_b_placeholder",
    "artifacts",
}


class Phase3ReplayAdapterError(RuntimeError):
    pass


def _read_json(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise Phase3ReplayAdapterError(f"Missing JSON file: {path}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise Phase3ReplayAdapterError(
            f"Invalid JSON in {path.name}: {exc.msg} (line {exc.lineno}, column {exc.colno})"
        ) from exc
    if not isinstance(data, dict):
        raise Phase3ReplayAdapterError(f"Expected JSON object in {path}")
    return data


def _safe_output_path(case_dir: Path, output_path: str) -> Path:
    candidate = Path(output_path)
    if candidate.is_absolute():
        raise Phase3ReplayAdapterError(f"output_path must be relative to case_dir: {output_path}")
    resolved = (case_dir / candidate).resolve()
    case_root = case_dir.resolve()
    if resolved != case_root and case_root not in resolved.parents:
        raise Phase3ReplayAdapterError(f"output_path escapes case_dir: {output_path}")
    return resolved


def _normalize_eval_context(raw: Any) -> dict[str, Any]:
    ctx = raw if isinstance(raw, dict) else {}
    return {
        "core_nonempty_paths": list(ctx.get("core_nonempty_paths") or []),
        "comparison_objects": list(ctx.get("comparison_objects") or []),
        "object_aliases": deepcopy(ctx.get("object_aliases") or {}),
        "pairwise_required": bool(ctx.get("pairwise_required", False)),
        "required_source_constraints": list(ctx.get("required_source_constraints") or []),
        "unconfirmed_items": list(ctx.get("unconfirmed_items") or []),
        "strict_terms": list(ctx.get("strict_terms") or []),
        "negative_terms": list(ctx.get("negative_terms") or []),
        "fallback_used": bool(ctx.get("fallback_used", False)),
    }


def load_phase3_replay_case(case_dir: Path) -> dict[str, Any]:
    case_dir = Path(case_dir)
    manifest_path = case_dir / "case_manifest.json"
    manifest = _read_json(manifest_path)
    missing = sorted(_REQUIRED_MANIFEST_FIELDS - set(manifest))
    if missing:
        raise Phase3ReplayAdapterError(f"Missing required manifest fields: {', '.join(missing)}")
    if manifest.get("skill_name") != "retrieval-matrix-generation":
        raise Phase3ReplayAdapterError(f"Unsupported skill_name: {manifest.get('skill_name')}")
    output_path = None
    artifacts = manifest.get("artifacts") or {}
    if not isinstance(artifacts, dict):
        raise Phase3ReplayAdapterError("artifacts must be a JSON object")
    if "output_path" in artifacts:
        output_path = artifacts["output_path"]
    elif manifest.get("execution_mode") == "executed":
        output_path = "output.json"
    if manifest.get("execution_mode") == "executed":
        if not isinstance(output_path, str) or not output_path.strip():
            raise Phase3ReplayAdapterError("executed case requires a valid output_path")
        output_file = _safe_output_path(case_dir, output_path)
        output_payload = _read_json(output_file)
    else:
        output_file = None
        output_payload = None
    return {
        "case_dir": case_dir,
        "manifest": deepcopy(manifest),
        "output_path": output_path,
        "output_file": output_file,
        "output_payload": output_payload,
    }


def build_phase3_eval_context(
    replay_case: dict[str, Any], output_schema: dict[str, Any]
) -> dict[str, Any]:
    manifest = deepcopy(replay_case.get("manifest") or {})
    eval_context = _normalize_eval_context(manifest.get("eval_context") or {})
    context = {
        "execution_mode": manifest.get("execution_mode"),
        "handling_class": manifest.get("handling_class"),
        "baseline_eligible": manifest.get("baseline_eligible"),
        "scenario_b_placeholder": bool(manifest.get("scenario_b_placeholder", False)),
        "artifacts": deepcopy(manifest.get("artifacts") or {}),
        "output_payload": deepcopy(replay_case.get("output_payload") or {}),
        "output_text": json.dumps(replay_case.get("output_payload") or {}, ensure_ascii=False),
        "output_schema": deepcopy(output_schema),
    }
    context.update(eval_context)
    return context


def evaluate_phase3_replay_case(case_dir: Path, output_schema_path: Path) -> dict[str, Any]:
    case_dir = Path(case_dir)
    replay_case = load_phase3_replay_case(case_dir)
    manifest = deepcopy(replay_case["manifest"])
    execution_mode = manifest.get("execution_mode")
    if execution_mode != "executed" or manifest.get("scenario_b_placeholder") is True:
        manifest["deterministic_overall_status"] = None
        manifest["deterministic_baseline_eligible"] = False
        write_json(case_dir / "case_manifest.json", manifest)
        return {
            "case_id": manifest.get("case_id"),
            "execution_mode": execution_mode,
            "skipped": True,
            "deterministic_result_path": None,
        }

    output_schema = _read_json(Path(output_schema_path))
    context = build_phase3_eval_context(replay_case, output_schema)
    common_result = run_common_checks(context)
    phase3_result = run_phase3_deterministic_checks(context)
    combined_checks = list(common_result.get("checks") or []) + list(
        phase3_result.get("checks") or []
    )
    result = {
        "schema_version": phase3_result.get("schema_version")
        or common_result.get("schema_version"),
        "overall_status": "failed"
        if common_result.get("overall_status") == "failed"
        or phase3_result.get("overall_status") == "failed"
        else (
            "manual_review_required"
            if common_result.get("overall_status") == "manual_review_required"
            or phase3_result.get("overall_status") == "manual_review_required"
            else "passed"
        ),
        "assessment_scope": "structural_contract",
        "scientific_quality_status": "not_evaluated",
        "baseline_eligible": False,
        "checks": combined_checks,
        "summary": {
            "hard_gate_failed": [
                c["check_id"]
                for c in combined_checks
                if c["status"] == "failed" and c["failure_level"] == "hard_gate"
            ],
            "score_penalties": [
                c["check_id"]
                for c in combined_checks
                if c["status"] == "failed" and c["failure_level"] == "score_penalty"
            ],
            "manual_review_triggers": [
                c["check_id"] for c in combined_checks if c["status"] == "manual_review_trigger"
            ],
        },
    }
    result_path = case_dir / "deterministic_result.json"
    write_json(result_path, result)
    manifest["deterministic_result_path"] = "deterministic_result.json"
    manifest["deterministic_overall_status"] = result["overall_status"]
    manifest["deterministic_baseline_eligible"] = result["baseline_eligible"]
    write_json(case_dir / "case_manifest.json", manifest)
    return {
        "case_id": manifest.get("case_id"),
        "execution_mode": execution_mode,
        "skipped": False,
        "deterministic_result_path": "deterministic_result.json",
        "deterministic_overall_status": result["overall_status"],
        "deterministic_baseline_eligible": result["baseline_eligible"],
        "result": result,
    }
