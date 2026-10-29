"""Read-only display derivation for historically unfinalized OT record drafts."""
from __future__ import annotations

import json
from typing import Any, Callable

import yaml

from app.execution.skill_finalization_runtime import (
    build_frozen_finalization_contract,
    execute_registered_finalizer,
)
from app.modules.repo_paths import repo_root


def historical_record_display_projection(
    task: dict[str, Any],
    artifact: dict[str, Any],
    content: str,
    *,
    load_source: Callable[[str], str],
) -> dict[str, Any] | None:
    """Use actual sourceRefs and the same finalizer, without changing stored truth."""
    artifact_id = artifact.get("artifactId") or artifact.get("id")
    original = next(
        (row for row in task.get("artifacts", []) if (row.get("artifactId") or row.get("id")) == artifact_id),
        None,
    )
    if not original or original.get("artifactType") != "research_records":
        return None
    validator = original.get("validatorResult") or {}
    if "unfinalized_scientific_content" not in validator.get("warnings", []):
        return None
    request: dict[str, Any] = {}
    used_sources: list[str] = []
    for reference in original.get("sourceRefs", []):
        if not any((row.get("artifactId") or row.get("id")) == reference for row in task.get("artifacts", [])):
            continue
        source = json.loads(load_source(reference))
        for key in ("paper_packets", "evidence_schema"):
            if key in source:
                request[key] = source[key]
                used_sources.append(reference)
    if "paper_packets" not in request or "evidence_schema" not in request:
        return None
    root = repo_root()
    skill_path = "skills/opentrajectory/evidence-node-extraction-skill"
    metadata = yaml.safe_load((root / skill_path / "skill.yaml").read_text(encoding="utf-8"))
    contract = build_frozen_finalization_contract(skill_path, metadata["finalization"], repo_root=root)
    result = execute_registered_finalizer(
        repo_root=root,
        skill_path=skill_path,
        frozen_contract=contract,
        input_payload=request,
        draft_payload=json.loads(content),
    )
    return {
        "kind": "historical_draft_display",
        "label": "历史草稿整理展示",
        "sourceArtifactId": artifact_id,
        "sourceArtifactIds": list(dict.fromkeys(used_sources)),
        "originalStatus": "unfinalized",
        "content": json.dumps(result.output, ensure_ascii=False),
    }
