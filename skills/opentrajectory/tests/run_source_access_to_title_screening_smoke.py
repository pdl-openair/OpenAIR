"""Minimal handoff smoke for #7 source access -> #8 title/abstract screening.

This smoke validates only:
1) #7 dispatch is callable and returns candidates,
2) #7 -> #8 candidate handoff shape works,
3) #8 dispatch returns non-empty screened_candidates and summary.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.api.app.execution.python_adapter_runner import run_python_skill_adapter


def _sample_source_access_input() -> dict[str, Any]:
    return {
        "task_id": "smoke-task-7-8",
        "run_id": "smoke-run-7-8",
        "skill_id": "source_access_readiness_resolver",
        "candidates": [
            {
                "candidate_id": "cand-7-8-1",
                "title": "Metadata-only trajectory planning candidate",
                "abstract": "A concise abstract to exercise deterministic screening.",
                "doi": "10.0000/smoke.1",
                "url": "https://example.org/paper-1",
                "oa_url": "https://example.org/oa-paper-1.pdf",
                "is_open_access": True,
                "provenance": [],
            }
        ],
    }


def _map_7_to_8_input(access_output: dict[str, Any]) -> dict[str, Any]:
    task_id = access_output.get("task_id", "smoke-task-7-8")
    run_id = access_output.get("run_id", "smoke-run-7-8")
    candidates = access_output.get("candidates", [])

    mapped_candidates: list[dict[str, Any]] = []
    for c in candidates:
        mapped_candidates.append(
            {
                "candidate_id": c.get("candidate_id"),
                "title": c.get("title"),
                "abstract": c.get("abstract"),
                "access_status": c.get("access_status"),
                "needs_acquisition": bool(c.get("needs_acquisition", False)),
                "provenance": c.get("provenance", []),
            }
        )

    return {
        "task_id": task_id,
        "run_id": run_id,
        "candidates": mapped_candidates,
    }


def _run_source_access_dispatch() -> dict[str, Any]:
    return run_python_skill_adapter(
        repo_root=REPO_ROOT,
        skill_id="source_access_readiness_resolver",
        skill_path="skills/opentrajectory/source-access-readiness-resolver-skill",
        input_payload=_sample_source_access_input(),
    )


def _run_title_screening_dispatch(input_payload: dict[str, Any]) -> dict[str, Any]:
    return run_python_skill_adapter(
        repo_root=REPO_ROOT,
        skill_id="title_abstract_screening",
        skill_path="skills/opentrajectory/title-abstract-screening-skill",
        input_payload=input_payload,
    )


def _assert_result(access_output: dict[str, Any], screening_output: dict[str, Any]) -> None:
    assert isinstance(access_output, dict) and access_output, "#7 returned empty result"
    assert isinstance(access_output.get("candidates"), list), "#7 candidates missing or invalid"

    assert isinstance(screening_output, dict) and screening_output, "#8 returned empty result"
    screened = screening_output.get("screened_candidates")
    assert isinstance(screened, list) and len(screened) > 0, "screened_candidates is empty"
    assert "summary" in screening_output and isinstance(screening_output["summary"], dict), "summary missing"

    errors = screening_output.get("errors")
    assert not errors, f"#8 returned errors: {errors}"


def main() -> None:
    access_output = _run_source_access_dispatch()
    screening_input = _map_7_to_8_input(access_output)
    screening_output = _run_title_screening_dispatch(screening_input)
    _assert_result(access_output, screening_output)
    print("source_access_readiness -> title_abstract_screening smoke passed")


if __name__ == "__main__":
    main()
