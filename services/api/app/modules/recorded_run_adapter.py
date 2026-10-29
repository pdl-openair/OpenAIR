"""
Map fixture disk layout (snake_case JSON) → API task dict (camelCase).

Used only by dev seed (`task_dev_seed`); not imported by runtime task store.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.modules.task_constants import STAGE_ORDER, STAGE_SUBSTAGE_CATALOG

TASK_STATUSES = {"running", "awaiting_confirmation", "completed", "failed"}
STAGE_STATUSES = {"not_started", "running", "completed", "failed"}
CAPABILITY_STATUSES = {"not_started", "running", "completed", "failed"}


def _read_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _default_stage_catalog() -> list[dict[str, Any]]:
    return [
        {
            "stageKey": stage_key,
            "allowedSubStages": [dict(entry) for entry in STAGE_SUBSTAGE_CATALOG.get(stage_key, [])],
        }
        for stage_key in STAGE_ORDER
    ]


def _stage_catalog_from_details(stage_details: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build replay catalog from recorded substages; fall back only when none were recorded."""
    catalog: list[dict[str, Any]] = []
    for detail in stage_details:
        stage_key = detail["stageKey"]
        allowed_substages = [
            {
                "key": sub_stage["subStage"],
                "stageKey": stage_key,
                "label": sub_stage.get("label") or sub_stage["subStage"],
                "description": "",
                "enabled": True,
            }
            for sub_stage in detail.get("subStages") or []
        ]
        catalog.append({"stageKey": stage_key, "allowedSubStages": allowed_substages})
    return catalog or _default_stage_catalog()


def _map_task_status(raw: str) -> str:
    s = (raw or "").lower()
    if s in ("completed",):
        return "completed"
    if s in ("failed", "blocked"):
        return "failed"
    if s in ("awaiting_confirmation", "waiting_user", "pending"):
        return "awaiting_confirmation"
    if s in TASK_STATUSES:
        return s
    return "running"


def _map_stage_status(raw: str) -> str:
    s = (raw or "not_started").lower()
    return s if s in STAGE_STATUSES else "not_started"


def _map_capability_status(raw: str) -> str:
    s = (raw or "not_started").lower()
    if s == "pending":
        return "not_started"
    return s if s in CAPABILITY_STATUSES else "not_started"


def _map_sub_stage(row: dict[str, Any], stage_key: str, sequence: int) -> dict[str, Any]:
    sub_stage = row["sub_stage"]
    return {
        "subStage": sub_stage,
        "status": _map_stage_status(row.get("status", "not_started")),
        "artifactRefs": list(row.get("artifact_refs") or []),
        "subStageInstanceId": row.get("sub_stage_instance_id") or f"sub_{stage_key}_{sequence}_{sub_stage}",
        "parentStageKey": stage_key,
        "label": row.get("label") or sub_stage,
        "capabilityCallRefs": list(row.get("capability_call_refs") or []),
        "summary": row.get("summary"),
        "sequence": row.get("sequence", sequence),
        "startedAt": row.get("started_at"),
        "finishedAt": row.get("finished_at"),
    }


def _map_stage_detail(row: dict[str, Any]) -> dict[str, Any]:
    stage = row["stage"]
    if stage not in STAGE_ORDER:
        raise ValueError(f"unknown stage key: {stage}")
    sub_stages = [_map_sub_stage(s, stage, idx) for idx, s in enumerate(row.get("sub_stages") or [])]
    return {
        "stageKey": stage,
        "status": _map_stage_status(row.get("status", "not_started")),
        "summary": row.get("summary"),
        "risks": row.get("risks"),
        "nextSteps": row.get("next_steps"),
        "artifactRefs": list(row.get("artifact_refs") or []),
        "subStages": sub_stages,
    }


def _map_evidence_ref(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "claim": row["claim"],
        "sourceTitle": row["source_title"],
        "sourceUrl": row["source_url"],
        "excerpt": row["excerpt"],
        "sourceIdentifier": row.get("source_identifier"),
        "locator": row.get("locator"),
    }


def _map_artifact(row: dict[str, Any], updated_at: str) -> dict[str, Any]:
    path = row.get("path")
    return {
        "id": row["artifact_id"],
        "kind": row.get("type", "research_note"),
        "title": row["title"],
        "summary": path,
        "createdAt": updated_at,
        "path": path,
        "format": row.get("format"),
        "relatedStage": row.get("related_stage"),
        "relatedSkillId": row.get("related_skill_id"),
        "subStageInstanceId": row.get("sub_stage_instance_id"),
        "sourceCapabilityCallId": row.get("source_capability_call_id"),
        "evidenceRefs": [_map_evidence_ref(item) for item in row.get("evidence_refs") or []],
    }


def _map_capability_call(row: dict[str, Any]) -> dict[str, Any]:
    err = row.get("error")
    if err is not None and not isinstance(err, str):
        import json

        err = json.dumps(err, ensure_ascii=False)
    original_runtime = row.get("runtime")
    source_metadata = dict(row.get("source_metadata") or {})
    if original_runtime:
        source_metadata.setdefault("recordedOriginalRuntime", original_runtime)
    return {
        "id": row["call_id"],
        "kind": "in_app_agent",
        "name": row["skill_id"],
        "status": _map_capability_status(row.get("status", "not_started")),
        "requestedAt": row.get("started_at") or "",
        "finishedAt": row.get("ended_at"),
        "resultSummary": err if err else None,
        # Keep the recorded executor for audit only.  The imported call itself
        # is replay provenance and must never be normalized back to live.
        "runtime": original_runtime,
        "runMode": "replay",
        "sourceMode": "replay",
        "sourceMetadata": source_metadata,
        "artifactRefs": list(row.get("artifact_refs") or []),
        "stageKey": row.get("stage_key") or row.get("related_stage"),
        "subStageInstanceId": row.get("sub_stage_instance_id"),
        "inputArtifactRefs": list(row.get("input_artifact_refs") or ([] if not row.get("input_ref") else [row.get("input_ref")])),
        "outputArtifactRefs": list(row.get("output_artifact_refs") or ([] if not row.get("output_ref") else [row.get("output_ref")])),
    }


def load_recorded_run(run_dir: Path) -> dict[str, Any]:
    task_path = run_dir / "task.json"
    if not task_path.is_file():
        raise FileNotFoundError(f"missing task.json in {run_dir}")

    raw_task = _read_json(task_path)
    raw_stages = _read_json(run_dir / "stages.json") if (run_dir / "stages.json").is_file() else []
    raw_artifacts = _read_json(run_dir / "artifacts.json") if (run_dir / "artifacts.json").is_file() else []
    raw_calls = (
        _read_json(run_dir / "capability_calls.json")
        if (run_dir / "capability_calls.json").is_file()
        else []
    )

    updated_at = raw_task.get("updated_at") or raw_task.get("created_at") or ""
    stage_details = [_map_stage_detail(s) for s in raw_stages]

    current_stage = raw_task.get("current_stage", "problem_modeling")
    if current_stage not in STAGE_ORDER:
        current_stage = "problem_modeling"

    return {
        "taskId": raw_task["task_id"],
        "title": raw_task["title"],
        "goalSummary": raw_task.get("user_goal"),
        "domain": raw_task.get("domain"),
        "currentStage": current_stage,
        "status": _map_task_status(raw_task.get("status", "running")),
        "resultSource": "replay",
        "createdAt": raw_task.get("created_at"),
        "updatedAt": raw_task.get("updated_at"),
        "stages": list(STAGE_ORDER),
        "stageCatalog": _stage_catalog_from_details(stage_details),
        "stageDetails": stage_details,
        "artifacts": [_map_artifact(a, updated_at) for a in raw_artifacts],
        "confirmations": [],
        "capabilityCalls": [_map_capability_call(c) for c in raw_calls],
    }


def load_all_recorded_run_tasks(
    *,
    runs_root: Path,
    mocks_dir: Path | None = None,
) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}

    if runs_root.is_dir():
        for run_dir in sorted(runs_root.iterdir()):
            if not run_dir.is_dir():
                continue
            if not (run_dir / "task.json").is_file():
                continue
            try:
                task = load_recorded_run(run_dir)
                out[task["taskId"]] = task
            except (OSError, ValueError, KeyError, json.JSONDecodeError) as e:
                raise RuntimeError(f"failed to load recorded run {run_dir.name}: {e}") from e

    if mocks_dir and mocks_dir.is_dir():
        for path in sorted(mocks_dir.glob("*.json")):
            raw = _read_json(path)
            if not isinstance(raw, dict) or "taskId" not in raw:
                continue
            tid = raw["taskId"]
            if tid not in out:
                raw.setdefault("resultSource", "demo")
                out[tid] = raw

    return out
