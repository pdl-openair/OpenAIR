#!/usr/bin/env python3
"""P0.5d offline regression for replay-backed deterministic live bridge Skills."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
API_ROOT = REPO_ROOT / "services/api"

SKILLS = {
    "candidate_dedup_and_provenance": "candidate-dedup-and-provenance",
    "source_access_readiness_resolver": "source-access-readiness-resolver-skill",
    "title_abstract_screening": "title-abstract-screening-skill",
    "candidate_source_scoring": "candidate-source-scoring",
    "candidate_tiering": "candidate-tiering",
    "topk_source_selection": "topk-source-selection",
}


def _parse_payload(stdout: str) -> dict:
    for line in reversed([x.strip() for x in stdout.splitlines() if x.strip()]):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    raise RuntimeError(f"bridge returned no JSON: {stdout[-800:]}")


def main() -> int:
    clean_env = dict(os.environ)
    clean_env.pop("LLM_PROVIDER_API_KEY", None)
    clean_env.pop("LLM_PROVIDER_BASE_URL", None)
    with tempfile.TemporaryDirectory(prefix="p05d-openair-bridge-") as tmpdir:
        for skill_id, skill_dir_name in SKILLS.items():
            request_path = REPO_ROOT / "skills/opentrajectory" / skill_dir_name / "examples/request.json"
            request = json.loads(request_path.read_text(encoding="utf-8"))
            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "app.execution.run_single_structured_skill",
                    "--skill-id",
                    skill_id,
                    "--workspace-dir",
                    tmpdir,
                    "--task-id",
                    f"p05d_{skill_id}",
                ],
                cwd=str(API_ROOT),
                input=json.dumps(request, ensure_ascii=False),
                capture_output=True,
                text=True,
                env=clean_env,
            )
            payload = _parse_payload(proc.stdout)
            if proc.returncode != 0 or payload.get("ok") is not True:
                raise RuntimeError(f"{skill_id} failed: stdout={proc.stdout[-800:]} stderr={proc.stderr[-800:]}")
            diagnostics = payload.get("diagnostics") or {}
            if diagnostics.get("provider") != "python_adapter":
                raise RuntimeError(f"{skill_id} did not use python_adapter: {diagnostics}")
            if diagnostics.get("input_schema_passed") is not True or diagnostics.get("output_schema_passed") is not True:
                raise RuntimeError(f"{skill_id} schema checks missing: {diagnostics}")
            print(f"[PASS] {skill_id}: python_adapter + schema checks")
    print("[p05d-openair-bridge] PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
