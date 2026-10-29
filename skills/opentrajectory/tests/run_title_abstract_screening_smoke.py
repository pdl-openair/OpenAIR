"""Minimal smoke for title-abstract-screening runner dispatch.

This smoke validates only:
1) runner wrapper/dispatch is callable,
2) adapter output is non-empty,
3) output includes summary.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.api.app.execution.python_adapter_runner import run_python_skill_adapter


def _sample_input() -> dict:
    return {
        "task_id": "smoke-task-8",
        "run_id": "smoke-run-8",
        "candidates": [
            {
                "candidate_id": "cand-1",
                "title": "A trajectory planning approach",
                "abstract": "We study trajectory planning with metadata-only constraints.",
                "access_status": "structured_fulltext_ready",
                "needs_acquisition": False,
                "provenance": [],
            }
        ],
    }


def run_smoke(skill_id: str) -> dict:
    repo_root = Path(__file__).resolve().parents[3]
    return run_python_skill_adapter(
        repo_root=repo_root,
        skill_id=skill_id,
        skill_path="skills/opentrajectory/title-abstract-screening-skill",
        input_payload=_sample_input(),
    )


def assert_smoke_result(result: dict) -> None:
    assert isinstance(result, dict) and result, "dispatch returned empty result"
    assert "summary" in result and isinstance(result["summary"], dict), "summary missing"


def main() -> None:
    for sid in ("title_abstract_screening", "title-abstract-screening-skill"):
        result = run_smoke(sid)
        assert_smoke_result(result)
    print("title_abstract_screening smoke passed")


if __name__ == "__main__":
    main()
