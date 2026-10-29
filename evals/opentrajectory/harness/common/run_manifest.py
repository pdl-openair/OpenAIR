from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass
class CaseManifest:
    case_id: str
    dataset_split: str
    executable: bool
    routing_status: str
    handling_class: str | None
    skill_execution_status: str
    artifact_presence: str
    skip_reason: str | None = None
    eligible_for_regression_run: bool | None = None
    eligible_for_baseline: bool | None = None
    eligible_for_scoring: bool | None = None
    eligible_for_fixture_generation: bool | None = None
    misuse_is_hard_failure: bool | None = None


@dataclass
class RunManifest:
    run_id: str
    timestamp: str
    execution_mode: str
    eval_suite_version: str
    dataset_id: str
    dataset_version: str
    dataset_files: dict[str, str]
    dataset_hashes: dict[str, str]
    runner_version: str
    runtime_commit_or_na: str
    total_case_count: int
    executable_case_count: int
    skipped_case_count: int
    invalid_case_count: int
    elapsed_ms: int


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def case_manifest_dict(manifest: CaseManifest) -> dict[str, Any]:
    return asdict(manifest)


def run_manifest_dict(manifest: RunManifest) -> dict[str, Any]:
    return asdict(manifest)
