from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "adapter" / "reference_metric_fact_assembly_adapter.py"
spec = importlib.util.spec_from_file_location("reference_metric_fact_assembly_adapter", MODULE)
adapter = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(adapter)


def _source_package(content: str) -> dict:
    raw = content.encode("utf-8")
    sha = hashlib.sha256(raw).hexdigest()
    return {
        "schema_version": "openair-reference-source-package/v1",
        "state": "succeeded",
        "source_kind": "official_repository",
        "repository": "https://github.com/example/project.git",
        "requested_revision": "main",
        "resolved_commit_sha": "a" * 40,
        "documents": [
            {
                "document_id": "refdoc:" + "b" * 64,
                "path": "README.md",
                "sha256": sha,
                "line_count": len(content.splitlines()),
                "content": content,
            }
        ],
    }


def _candidate(value: float = 81.58) -> dict:
    return {
        "metric_name": "Avg.",
        "reference_value": value,
        "value_type": "number",
        "unit": None,
        "experiment_conditions": [
            "model=princeton-nlp/sup-simcse-bert-base-uncased",
            "task_set=sts",
            "mode=test",
            "pooler=cls",
        ],
        "source_document_id": "refdoc:" + "b" * 64,
        "source_excerpt": "| 75.30 | 84.67 | 80.19 | 85.40 | 80.82 | 84.26 | 80.39 | 81.58 |",
        "comparison_rule": {"kind": "exact"},
        "comparison_rule_basis_excerpt": "which is expected to output the results in a tabular format:",
    }


def _official_simcse_excerpt() -> str:
    return "\n".join(
        [
            "For STS tasks, our evaluation takes the all setting, and report Spearman's correlation.",
            "python evaluation.py --model_name_or_path princeton-nlp/sup-simcse-bert-base-uncased --pooler cls --task_set sts --mode test",
            "which is expected to output the results in a tabular format:",
            "| STS12 | STS13 | STS14 | STS15 | STS16 | STSBenchmark | SICKRelatedness | Avg. |",
            "| 75.30 | 84.67 | 80.19 | 85.40 | 80.82 | 84.26 | 80.39 | 81.58 |",
        ]
    )


def test_real_simcse_reference_shape_becomes_direct_comparison_request() -> None:
    payload = {
        "source_package": _source_package(_official_simcse_excerpt()),
        "interpretation": {"reference_candidates": [_candidate()], "unresolved": []},
    }
    result = adapter.run_reference_metric_fact_assembly(payload)

    assert result["assembly_status"] == "complete"
    assert result["reference_metric_source_conflicts"] == []
    assert result["reference_metric_unresolved"] == []
    assert result["reference_metric_comparison_requests"] == [
        {
            "metric_name": "Avg.",
            "reference_value": 81.58,
            "value_type": "number",
            "unit": None,
            "comparison_rule": {"kind": "exact"},
        }
    ]
    record = result["reference_metric_records"][0]
    assert record["source"]["path"] == "README.md"
    assert record["source"]["locator"]["line_start"] == 5
    assert record["comparison_rule"]["basis"]["line_start"] == 3


def test_missing_rule_basis_never_invents_tolerance_or_comparison_request() -> None:
    candidate = _candidate()
    candidate["comparison_rule"] = None
    candidate["comparison_rule_basis_excerpt"] = None
    result = adapter.run_reference_metric_fact_assembly(
        {
            "source_package": _source_package(_official_simcse_excerpt()),
            "interpretation": {"reference_candidates": [candidate], "unresolved": []},
        }
    )

    assert result["assembly_status"] == "incomplete"
    assert result["reference_metric_comparison_requests"] == []
    assert result["reference_metric_records"][0]["comparison_rule_status"] == "missing"
    assert {row["reason"] for row in result["reference_metric_unresolved"]} == {"comparison_rule_basis_missing"}


def test_production_adapter_contains_no_demo_specific_branching() -> None:
    production = MODULE.read_text(encoding="utf-8").lower()
    for forbidden in ["simcse", "senteval", "princeton-nlp", "stsbenchmark"]:
        assert forbidden not in production


def test_unmatched_quote_and_numeric_text_are_retained_without_invented_locator():
    candidate = _candidate()
    candidate["reference_value"] = "81.58"
    candidate["source_excerpt"] = "研究摘要中的指标描述"
    candidate["comparison_rule_basis_excerpt"] = "摘要性对比依据"
    result = adapter.run_reference_metric_fact_assembly({
        "source_package": _source_package(_official_simcse_excerpt()),
        "interpretation": {"reference_candidates": [candidate], "unresolved": []},
        "extra": "保留输入扩展",
    })
    record = result["reference_metric_records"][0]
    assert record["reference_value"] == "81.58"
    assert record["source"]["locator"] == {"line_start": None, "line_end": None, "excerpt": candidate["source_excerpt"]}
    assert record["comparison_rule"]["basis"]["excerpt"] == "摘要性对比依据"
    assert result["reference_metric_comparison_requests"][0]["reference_value"] == "81.58"


def test_multiple_reference_values_all_reach_comparison():
    candidates = [_candidate(81.58), _candidate(81.57)]
    result = adapter.run_reference_metric_fact_assembly({"source_package": _source_package(_official_simcse_excerpt()), "interpretation": {"reference_candidates": candidates, "unresolved": []}})
    assert [row["reference_value"] for row in result["reference_metric_comparison_requests"]] == [81.58, 81.57]
