"""Source mapping helpers for research-lead packages."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

EXPECTED_RECORDED_RUN_FILES = (
    "task.json",
    "stages.json",
    "capability_calls.json",
    "artifacts.json",
    "proposal-v3.md",
    "experiment_design-v2.md",
    "literature.md",
    "task_plan.md",
    "summary.md",
)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_text_if_present(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def source_paths_for_recorded_run(run_dir: Path) -> list[str]:
    return [str(run_dir / name) for name in EXPECTED_RECORDED_RUN_FILES if (run_dir / name).exists()]


def artifact_refs_from_recorded_run(run_dir: Path) -> list[dict[str, str]]:
    artifacts_path = run_dir / "artifacts.json"
    if not artifacts_path.exists():
        return []

    refs: list[dict[str, str]] = []
    for artifact in read_json(artifacts_path):
        refs.append(
            {
                "artifact_id": str(artifact.get("artifact_id", "")),
                "artifact_type": str(artifact.get("type", "")),
                "title": str(artifact.get("title", "")),
                "path": str(run_dir / str(artifact.get("path", ""))),
            }
        )
    return refs


def recorded_run_source_mapping(run_dir: Path) -> dict[str, Any]:
    source_paths = source_paths_for_recorded_run(run_dir)
    present_names = {Path(path).name for path in source_paths}
    missing_names = [name for name in EXPECTED_RECORDED_RUN_FILES if name not in present_names]
    notes = [
        "Mapped markdown proposal and experiment design into generic proposal fields.",
        "Mapped artifact ids into artifact_refs only; no evidence refs were inferred from artifacts.",
    ]
    if missing_names:
        notes.append("Some expected recorded-run files were not present.")

    return {
        "source_type": "recorded_run",
        "source_paths": source_paths,
        "field_mapping_notes": notes,
        "unmapped_source_fields": missing_names,
        "mapping_confidence": "medium" if not missing_names else "low",
    }
