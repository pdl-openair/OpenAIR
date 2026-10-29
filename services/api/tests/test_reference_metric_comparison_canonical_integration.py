from __future__ import annotations

from pathlib import Path

from app.execution.python_adapter_runner import run_python_skill_adapter

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_actual_metric_artifact_and_explicit_reference_run_deterministically() -> None:
    actual_metrics = {
        "schema_version": "openair-actual-metrics/v1",
        "state": "succeeded",
        "extraction_status": "complete",
        "metrics": [{
            "metric_name": "accuracy",
            "actual_value": 0.91,
            "value_type": "number",
            "unit": None,
            "extraction_status": "succeeded",
            "source_trace": {"execution_record_ref": "artifact:execution-record"},
        }],
    }
    reference_metrics = [{
        "metric_name": "accuracy",
        "reference_value": 0.90,
        "value_type": "number",
        "unit": None,
        "comparison_rule": {"kind": "actual_gte_reference"},
    }]
    comparability = {
        "status": "comparable",
        "conditions": {
            key: {"status": "compatible", "evidence_refs": ["artifact:condition"]}
            for key in (
                "task_scope", "implementation", "data_split", "preprocessing",
                "evaluation_protocol", "metric_definition", "randomness",
                "hardware_environment",
            )
        },
        "condition_mismatches": [],
        "evidence_refs": ["artifact:comparability"],
        "unknowns": [],
    }
    output = run_python_skill_adapter(
        repo_root=REPO_ROOT,
        skill_id="reference_metric_comparison",
        skill_path="skills/cs_research/reference-metric-comparison",
        adapter_id="python_adapter:reference_metric_comparison",
        input_payload={
            "actual_metrics": actual_metrics,
            "reference_metrics": reference_metrics,
            "reproduction_comparability_assessment": comparability,
        },
    )
    assert output["state"] == "succeeded"
    assert output["comparison_status"] == "complete"
    assert output["overall_result"] == "meets_standard"
    assert output["compared_metric_count"] == 1
