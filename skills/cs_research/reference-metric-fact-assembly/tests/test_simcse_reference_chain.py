from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import jsonschema


SKILLS_ROOT = Path(__file__).resolve().parents[3]
SOURCE_ROOT = SKILLS_ROOT / "cs_research" / "official-reference-source-acquisition"
ASSEMBLY_ROOT = SKILLS_ROOT / "cs_research" / "reference-metric-fact-assembly"
COMPARISON_ROOT = SKILLS_ROOT / "cs_research" / "reference-metric-comparison"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


source_adapter = _load_module(
    "cs_w2_source_adapter",
    SOURCE_ROOT / "adapter" / "official_reference_source_acquisition_adapter.py",
)
assembly_adapter = _load_module(
    "cs_w2_assembly_adapter",
    ASSEMBLY_ROOT / "adapter" / "reference_metric_fact_assembly_adapter.py",
)
sys.path.insert(0, str(COMPARISON_ROOT))
from adapter import reference_metric_comparison_adapter as comparison_adapter  # noqa: E402


_SIMCSE_OFFICIAL_README_EXCERPT = """# SimCSE: Simple Contrastive Learning of Sentence Embeddings

For STS tasks, our evaluation takes the \"all\" setting, and report Spearman's correlation.

python evaluation.py \\
    --model_name_or_path princeton-nlp/sup-simcse-bert-base-uncased \\
    --pooler cls \\
    --task_set sts \\
    --mode test

which is expected to output the results in a tabular format:

------ test ------
| STS12 | STS13 | STS14 | STS15 | STS16 | STSBenchmark | SICKRelatedness |  Avg. |
| 75.30 | 84.67 | 80.19 | 85.40 | 80.82 |    84.26     |      80.39      | 81.58 |
"""


def _git(*args: str, cwd: Path) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)
    return result.stdout.strip()


def test_real_simcse_official_reference_is_consumed_by_existing_comparator(tmp_path: Path, monkeypatch) -> None:
    # Snapshot content is copied from the official princeton-nlp/SimCSE README.
    # The fixture is intentionally SimCSE-specific; production adapters remain generic.
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    repository = workspace / "repository"
    repository.mkdir()
    _git("init", cwd=repository)
    _git("config", "user.email", "test@example.com", cwd=repository)
    _git("config", "user.name", "OpenAIR Test", cwd=repository)
    (repository / "README.md").write_text(_SIMCSE_OFFICIAL_README_EXCERPT, encoding="utf-8")
    _git("add", "README.md", cwd=repository)
    _git("commit", "-m", "official SimCSE README reference fixture", cwd=repository)
    commit = _git("rev-parse", "HEAD", cwd=repository)

    code_source = {
        "state": "succeeded",
        "repository": "https://github.com/princeton-nlp/SimCSE.git",
        "requested_revision": "main",
        "resolved_commit_sha": commit,
        "verified_head": commit,
        "workspace_relative_path": "repository",
        "execution_evidence": {},
    }
    monkeypatch.chdir(workspace)
    monkeypatch.setenv("OPENAIR_ADAPTER_EXECUTION_CWD", str(workspace.resolve()))
    source_package = source_adapter.run_official_reference_source_acquisition({"code_source": code_source})
    assert source_package["state"] == "succeeded"
    assert source_package["resolved_commit_sha"] == commit
    readme = next(row for row in source_package["documents"] if row["path"] == "README.md")

    interpretation = {
        "reference_candidates": [
            {
                "metric_name": "Avg.",
                "reference_value": 81.58,
                "value_type": "number",
                "unit": None,
                "experiment_conditions": [
                    "model_name_or_path=princeton-nlp/sup-simcse-bert-base-uncased",
                    "pooler=cls",
                    "task_set=sts",
                    "mode=test",
                    "STS evaluation uses the all setting and Spearman correlation",
                ],
                "source_document_id": readme["document_id"],
                "source_excerpt": "| 75.30 | 84.67 | 80.19 | 85.40 | 80.82 |    84.26     |      80.39      | 81.58 |",
                "comparison_rule": {"kind": "exact"},
                "comparison_rule_basis_excerpt": "which is expected to output the results in a tabular format:",
            }
        ],
        "unresolved": [],
    }

    interpretation_schema = json.loads(
        (SKILLS_ROOT / "cs_research" / "reference-metric-interpretation" / "output.schema.json").read_text(encoding="utf-8")
    )
    jsonschema.validate(interpretation, interpretation_schema)

    assembled = assembly_adapter.run_reference_metric_fact_assembly(
        {"source_package": source_package, "interpretation": interpretation}
    )
    output_schema = json.loads((ASSEMBLY_ROOT / "output.schema.json").read_text(encoding="utf-8"))
    jsonschema.validate(assembled, output_schema)
    assert assembled["assembly_status"] == "complete"
    assert assembled["reference_metric_source_conflicts"] == []
    assert assembled["reference_metric_unresolved"] == []
    assert assembled["reference_metric_comparison_requests"] == [
        {
            "metric_name": "Avg.",
            "reference_value": 81.58,
            "value_type": "number",
            "unit": None,
            "comparison_rule": {"kind": "exact"},
        }
    ]

    actual_metrics = {
        "schema_version": "openair-actual-metrics/v1",
        "state": "succeeded",
        "extraction_status": "complete",
        "metrics": [
            {
                "metric_name": "Avg.",
                "actual_value": 81.58,
                "value_type": "number",
                "unit": None,
                "extraction_status": "succeeded",
                "source_trace": {"source_sha256": "a" * 64, "column": "Avg."},
            }
        ],
    }
    comparison = comparison_adapter.run_reference_metric_comparison(
        {
            "actual_metrics": actual_metrics,
            "reference_metrics": assembled["reference_metric_comparison_requests"],
        }
    )
    assert comparison["comparison_status"] == "complete"
    assert comparison["overall_result"] == "meets_standard"
    assert comparison["metrics"][0]["meets_rule"] is True


def test_reference_interpretation_schema_allows_a_bounded_full_table_excerpt() -> None:
    """A formal table may need more than a short preview to stay independently readable."""
    schema = json.loads(
        (SKILLS_ROOT / "cs_research" / "reference-metric-interpretation" / "output.schema.json").read_text(encoding="utf-8")
    )
    excerpt = "x" * 1024
    candidate = {
        "reference_candidates": [{
            "metric_name": "score",
            "reference_value": 1.0,
            "value_type": "number",
            "unit": None,
            "experiment_conditions": ["official evaluation"],
            "source_document_id": "refdoc:" + "a" * 64,
            "source_excerpt": excerpt,
            "comparison_rule": None,
            "comparison_rule_basis_excerpt": None,
        }],
        "unresolved": [],
    }
    jsonschema.validate(candidate, schema)
