from __future__ import annotations

import importlib
import importlib.util
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def _load_adapter_package(skill_dir_name: str, module_name: str):
    adapter_dir = ROOT / "skills" / "opentrajectory" / skill_dir_name / "adapter"
    package_name = "_test_ot_" + skill_dir_name.replace("-", "_")
    spec = importlib.util.spec_from_file_location(
        package_name,
        adapter_dir / "__init__.py",
        submodule_search_locations=[str(adapter_dir)],
    )
    assert spec is not None and spec.loader is not None
    package = importlib.util.module_from_spec(spec)
    sys.modules[package_name] = package
    spec.loader.exec_module(package)
    return importlib.import_module(f"{package_name}.{module_name}")


def test_source_acquisition_composes_three_internal_modules(monkeypatch) -> None:
    module = _load_adapter_package(
        "opentrajectory-source-acquisition",
        "source_acquisition_adapter",
    )
    calls: list[str] = []

    def retrieval(**_kwargs):
        calls.append("retrieval")
        return {
            "metadata_candidates": [
                {
                    "candidate_id": "openalex-1",
                    "title": "Candidate A",
                    "source_name": "openalex",
                    "provenance": [{"source_name": "openalex", "matched_query": "q"}],
                }
            ],
            "retrieval_report": {
                "source_counts": {"openalex": 1},
                "query_counts": {"q": 1},
                "failed_sources": [],
                "total_candidates": 1,
                "partial_success": False,
                "fallback_used": False,
                "risk_flags": [],
            },
        }

    def deduplicate(payload):
        calls.append("dedup")
        assert len(payload["metadata_candidates"]) == 1
        return {
            "deduped_candidates": payload["metadata_candidates"],
            "dedup_report": {"input_count": 1, "output_count": 1},
        }

    def resolve_access(payload):
        calls.append("access")
        assert payload["skill_id"] == "source-access-readiness-resolver-skill"
        return {
            "candidates": [
                {
                    **payload["candidates"][0],
                    "access_status": "landing_page_only",
                }
            ],
            "summary": {"total_candidates": 1},
            "warnings": [],
            "errors": [],
        }

    child = {
        "academic-source-retrieval-skill": retrieval,
        "candidate-dedup-and-provenance": deduplicate,
        "source-access-readiness-resolver-skill": resolve_access,
    }
    monkeypatch.setattr(
        module,
        "_child_callable",
        lambda skill_dir_name, *_args: child[skill_dir_name],
    )

    output = module.run_source_acquisition(
        {
            "task_id": "task-1",
            "user_query": "battery electrolyte",
            "search_matrix": [],
            "expanded_query_variants": [],
            "quality_report": {},
        }
    )

    assert calls == ["retrieval", "dedup", "access"]
    assert output["skill_id"] == "opentrajectory_source_acquisition"
    assert output["candidates"][0]["candidate_id"] == "openalex-1"
    assert output["retrieval_report"]["total_candidates"] == 1


def test_retrieval_parallelism_preserves_full_candidate_pool(monkeypatch) -> None:
    module = _load_adapter_package(
        "academic-source-retrieval-skill",
        "retrieval_adapter",
    )
    lock = threading.Lock()
    active = 0
    max_active = 0

    def fake_fetch(*, variant, q_idx, source, **_kwargs):
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.03)
        with lock:
            active -= 1
        return (
            [
                {
                    "candidate_id": f"{source}-{q_idx}",
                    "title": "Keep this candidate",
                    "source_name": source,
                    "provenance": [
                        {
                            "source_name": source,
                            "matched_query": variant["query"],
                        }
                    ],
                }
            ],
            None,
            None,
        )

    monkeypatch.setattr(module, "_fetch_for_variant_source", fake_fetch)

    result = module.run_retrieval(
        skill2_data={
            "expanded_query_variants": [
                {"query": "query one", "variant_type": "broad"},
                {"query": "query two", "variant_type": "broad"},
            ],
            "research_evidence_contract": {
                "offtopic_patterns": ["keep this candidate"],
                "domain_anchor_terms": ["something else"],
            },
        },
        target_sources=["openalex", "semantic_scholar", "arxiv"],
        max_results_per_query=10,
        max_total_candidates=20,
        timeout_seconds=30,
        allow_partial_success=True,
    )

    assert 2 <= max_active <= 4
    assert len(result["metadata_candidates"]) == 6
    assert result["retrieval_report"]["total_candidates"] == 6
    assert "candidate_relevance_diagnostics" not in result["retrieval_report"]


def test_source_acquisition_emits_incremental_candidate_progress(monkeypatch, tmp_path) -> None:
    module = _load_adapter_package(
        "opentrajectory-source-acquisition",
        "source_acquisition_adapter",
    )
    progress_file = tmp_path / "progress.jsonl"
    monkeypatch.setenv("OPENAIR_SKILL_PROGRESS_FILE", str(progress_file))

    def retrieval(*, progress_callback=None, **_kwargs):
        assert progress_callback is not None
        progress_callback(
            {
                "phase": "source_batch_completed",
                "source_name": "openalex",
                "query_index": 0,
                "batch_candidate_count": 1,
                "source_candidate_count": 1,
                "failed": False,
                "sample_candidates": [
                    {"title": "Candidate A", "doi": "10.1/a", "url": "https://example.test/a"}
                ],
            }
        )
        return {
            "metadata_candidates": [],
            "retrieval_report": {
                "source_counts": {"openalex": 1},
                "query_counts": {"q": 1},
                "failed_sources": [],
                "total_candidates": 0,
                "partial_success": False,
                "fallback_used": False,
                "risk_flags": [],
            },
        }

    child = {
        "academic-source-retrieval-skill": retrieval,
        "candidate-dedup-and-provenance": lambda _payload: {
            "deduped_candidates": [],
            "dedup_report": {"input_count": 0, "output_count": 0},
        },
        "source-access-readiness-resolver-skill": lambda _payload: {},
    }
    monkeypatch.setattr(
        module,
        "_child_callable",
        lambda skill_dir_name, *_args: child[skill_dir_name],
    )

    module.run_source_acquisition(
        {
            "task_id": "task-progress",
            "user_query": "battery electrolyte",
            "search_matrix": [],
            "expanded_query_variants": [],
        }
    )

    import json

    rows = [json.loads(line) for line in progress_file.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert rows[0]["source_name"] == "openalex"
    assert rows[0]["source_candidate_count"] == 1
    assert rows[0]["sample_candidates"][0]["title"] == "Candidate A"


def test_retrieval_executes_all_expanded_query_variants(monkeypatch) -> None:
    module = _load_adapter_package(
        "academic-source-retrieval-skill",
        "retrieval_adapter",
    )
    seen: list[tuple[int, str]] = []

    def fake_fetch(*, variant, q_idx, source, **_kwargs):
        seen.append((q_idx, variant["query"]))
        return (
            [
                {
                    "candidate_id": f"{source}-{q_idx}",
                    "title": f"Candidate {q_idx}",
                    "source_name": source,
                    "provenance": [
                        {
                            "source_name": source,
                            "matched_query": variant["query"],
                        }
                    ],
                }
            ],
            None,
            None,
        )

    monkeypatch.setattr(module, "_fetch_for_variant_source", fake_fetch)
    variants = [
        {"query": f"query {index}", "variant_type": "coverage"}
        for index in range(5)
    ]
    result = module.run_retrieval(
        skill2_data={"expanded_query_variants": variants},
        target_sources=["openalex"],
        max_results_per_query=10,
        max_total_candidates=20,
        timeout_seconds=30,
        allow_partial_success=True,
    )

    assert seen == [(index, f"query {index}") for index in range(5)]
    assert len(result["metadata_candidates"]) == 5


def test_retrieval_marks_query_level_failure_as_partial_success(monkeypatch) -> None:
    module = _load_adapter_package(
        "academic-source-retrieval-skill",
        "retrieval_adapter",
    )

    def fake_fetch(*, variant, q_idx, source, **_kwargs):
        if q_idx == 1:
            return [], "timeout: simulated", None
        return (
            [
                {
                    "candidate_id": f"{source}-{q_idx}",
                    "title": f"Candidate {q_idx}",
                    "source_name": source,
                    "provenance": [
                        {
                            "source_name": source,
                            "matched_query": variant["query"],
                        }
                    ],
                }
            ],
            None,
            None,
        )

    monkeypatch.setattr(module, "_fetch_for_variant_source", fake_fetch)
    result = module.run_retrieval(
        skill2_data={
            "expanded_query_variants": [
                {"query": "query zero", "variant_type": "coverage"},
                {"query": "query one", "variant_type": "coverage"},
            ]
        },
        target_sources=["openalex"],
        max_results_per_query=10,
        max_total_candidates=20,
        timeout_seconds=30,
        allow_partial_success=True,
    )

    assert len(result["metadata_candidates"]) == 1
    assert result["retrieval_report"]["failed_sources"] == []
    assert result["retrieval_report"]["partial_success"] is True
    assert any("q1 error" in item for item in result["retrieval_report"]["risk_flags"])


def test_retrieval_reports_candidate_capacity_truncation(monkeypatch) -> None:
    module = _load_adapter_package(
        "academic-source-retrieval-skill",
        "retrieval_adapter",
    )

    def fake_fetch(*, variant, q_idx, source, **_kwargs):
        return (
            [
                {
                    "candidate_id": f"{source}-{q_idx}-{index}",
                    "title": f"Candidate {q_idx}-{index}",
                    "source_name": source,
                    "provenance": [
                        {
                            "source_name": source,
                            "matched_query": variant["query"],
                        }
                    ],
                }
                for index in range(2)
            ],
            None,
            None,
        )

    monkeypatch.setattr(module, "_fetch_for_variant_source", fake_fetch)
    result = module.run_retrieval(
        skill2_data={
            "expanded_query_variants": [
                {"query": "query zero", "variant_type": "coverage"},
                {"query": "query one", "variant_type": "coverage"},
            ]
        },
        target_sources=["openalex"],
        max_results_per_query=10,
        max_total_candidates=3,
        timeout_seconds=30,
        allow_partial_success=True,
    )

    assert len(result["metadata_candidates"]) == 3
    assert "candidate_limit_reached" in result["retrieval_report"]["risk_flags"]
