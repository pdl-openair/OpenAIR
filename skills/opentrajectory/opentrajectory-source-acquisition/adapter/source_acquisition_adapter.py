"""OpenTrajectory source-acquisition execution.

One research work unit with three internal modules:
external retrieval -> deterministic deduplication -> deterministic access readiness.
"""
from __future__ import annotations

import importlib
import importlib.util
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable
import json
import os


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


@lru_cache(maxsize=None)
def _child_callable(
    skill_dir_name: str,
    module_name: str,
    callable_name: str,
) -> Callable[..., dict[str, Any]]:
    adapter_dir = _repo_root() / "skills" / "opentrajectory" / skill_dir_name / "adapter"
    init_py = adapter_dir / "__init__.py"
    if not init_py.is_file():
        raise RuntimeError(f"child adapter package missing: {adapter_dir}")

    package_name = "_ot_source_acquisition_" + skill_dir_name.replace("-", "_")
    if package_name not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            package_name,
            init_py,
            submodule_search_locations=[str(adapter_dir)],
        )
        if spec is None or spec.loader is None:
            raise RuntimeError(f"child adapter package cannot be loaded: {adapter_dir}")
        package = importlib.util.module_from_spec(spec)
        sys.modules[package_name] = package
        spec.loader.exec_module(package)

    module = importlib.import_module(f"{package_name}.{module_name}")
    fn = getattr(module, callable_name, None)
    if not callable(fn):
        raise RuntimeError(f"child adapter callable missing: {skill_dir_name}:{callable_name}")
    return fn


def _emit_progress(event: dict[str, Any]) -> None:
    """Send one user-safe progress fact to the parent adapter runner."""
    progress_file = str(os.environ.get("OPENAIR_SKILL_PROGRESS_FILE") or "").strip()
    if not progress_file:
        return
    path = Path(progress_file)
    line = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
        handle.flush()


def _empty_access_result(task_id: str) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "skill_id": "source-access-readiness-resolver-skill",
        "candidates": [],
        "summary": {
            "total_candidates": 0,
            "structured_fulltext_available_count": 0,
            "pdf_available_count": 0,
            "landing_page_only_count": 0,
            "metadata_only_count": 0,
            "access_blocked_count": 0,
            "needs_acquisition_count": 0,
        },
        "warnings": [],
        "errors": [],
    }


def run_source_acquisition(payload: dict[str, Any]) -> dict[str, Any]:
    task_id = str(payload.get("task_id") or "").strip()
    if not task_id:
        raise ValueError("task_id is required")

    config = payload.get("retrieval_config")
    if not isinstance(config, dict):
        config = {}

    retrieval = _child_callable(
        "academic-source-retrieval-skill",
        "retrieval_adapter",
        "run_retrieval",
    )
    deduplicate = _child_callable(
        "candidate-dedup-and-provenance",
        "dedup_adapter",
        "deduplicate_candidates",
    )
    resolve_access = _child_callable(
        "source-access-readiness-resolver-skill",
        "access_readiness_adapter",
        "resolve_access_readiness",
    )

    retrieval_input = {
        "user_query": payload.get("user_query"),
        "search_matrix": payload.get("search_matrix") or [],
        "expanded_query_variants": payload.get("expanded_query_variants") or [],
    }
    retrieval_result = retrieval(
        skill2_data=retrieval_input,
        target_sources=list(
            config.get("target_sources")
            or ["openalex", "semantic_scholar", "arxiv"]
        ),
        max_results_per_query=int(config.get("max_results_per_query", 10)),
        max_total_candidates=int(config.get("max_total_candidates", 120)),
        timeout_seconds=int(config.get("timeout_seconds", 30)),
        allow_partial_success=bool(config.get("allow_partial_success", True)),
        s2_api_key=payload.get("s2_api_key"),
        progress_callback=_emit_progress,
    )

    dedup_result = deduplicate(
        {"metadata_candidates": retrieval_result.get("metadata_candidates") or []}
    )
    deduped = dedup_result.get("deduped_candidates") or []

    if deduped:
        access_result = resolve_access(
            {
                "task_id": task_id,
                "skill_id": "source-access-readiness-resolver-skill",
                "candidates": deduped,
            }
        )
    else:
        access_result = _empty_access_result(task_id)

    return {
        "task_id": task_id,
        "skill_id": "opentrajectory_source_acquisition",
        "candidates": access_result.get("candidates") or [],
        "retrieval_report": retrieval_result.get("retrieval_report") or {},
        "dedup_report": dedup_result.get("dedup_report") or {},
        "summary": access_result.get("summary") or {},
        "warnings": access_result.get("warnings") or [],
        "errors": access_result.get("errors") or [],
    }
