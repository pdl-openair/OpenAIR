from __future__ import annotations

import importlib.util
import json
import sys
from copy import deepcopy
from pathlib import Path

import yaml
from jsonschema.validators import validator_for

ROOT = Path(__file__).resolve().parents[4]
SKILL_ROOT = ROOT / "skills/cs_research/actual-metric-records-closure"
sys.path.insert(0, str(ROOT / "services/api"))

from app.execution.provider_visible_skill_input import (  # noqa: E402
    SCIENTIFIC_MINIMAL_V1,
    project_provider_parameters,
    resolve_provider_message_contract,
)
from app.execution.skill_finalization_runtime import (  # noqa: E402
    build_frozen_finalization_contract,
    finalize_and_validate_registered_output,
)


def _finalizer():
    spec = importlib.util.spec_from_file_location(
        "cs_actual_metric_finalizer", SKILL_ROOT / "scripts/finalize.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _source(*, state: str = "succeeded", extraction_status: str = "complete") -> dict:
    metrics = [
        {
            "metric_name": "accuracy",
            "actual_value": 1.0,
            "value_type": "number",
            "unit": "ratio",
            "extraction_status": "succeeded",
            "source_trace": {"json_pointer": "/accuracy"},
        }
    ]
    return {
        "schema_version": "openair-actual-metrics/v1",
        "state": state,
        "extraction_status": extraction_status,
        "execution_record_relative_path": "execution/run-1/execution-record.json",
        "execution_record_sha256": "a" * 64,
        "result_source": {"kind": "execution_stream", "format": "json", "sha256": "b" * 64},
        "requested_metric_count": len(metrics),
        "extracted_metric_count": len(metrics),
        "metrics": metrics,
    }


def _input(source: dict | None = None) -> dict:
    value = source if source is not None else _source()
    return {
        "reproduction_experiment_design": {"status": "ready"},
        "execution_readiness_record": {"status": "ready"},
        "frozen_execution_input": {"execution_plan_id": "execplan:" + "1" * 64},
        "raw_execution_record": {
            "execution_record_relative_path": "execution/run-1/execution-record.json",
        },
        **({"raw_actual_metrics": value} if source is not None else {}),
    }


def _schema(name: str) -> dict:
    return json.loads((SKILL_ROOT / name).read_text(encoding="utf-8"))


def test_source_metrics_win_over_model_tampering_and_unknowns_survive() -> None:
    source = _source()
    draft = {
        "unknowns": ["评测协议仍有科学限制。"],
        "metrics": [{**source["metrics"][0], "actual_value": 99.0}],
        "status": "complete",
    }

    result = _finalizer().finalize(_input(source), draft)

    assert result.status == "succeeded"
    assert result.output["metrics"] == source["metrics"]
    assert result.output["metrics"][0]["actual_value"] == 1.0
    assert result.output["unknowns"][0] == "评测协议仍有科学限制。"
    assert not list(
        validator_for(_schema("output.schema.json"))(_schema("output.schema.json")).iter_errors(
            result.output
        )
    )


def test_partial_source_keeps_valid_and_failed_metrics_without_synthesizing_values() -> None:
    source = _source(state="failed", extraction_status="incomplete")
    source.update(
        {
            "failure_code": "metric_extraction_incomplete",
            "failed_stage": "metrics",
            "requested_metric_count": 2,
            "extracted_metric_count": 1,
            "metrics": [
                source["metrics"][0],
                {
                    "metric_name": "f1",
                    "actual_value": None,
                    "value_type": "number",
                    "unit": "ratio",
                    "extraction_status": "failed",
                    "failure_code": "result_source_unavailable",
                },
            ],
        }
    )

    result = _finalizer().finalize(_input(source), {"unknowns": []})

    assert result.output["status"] == "incomplete"
    assert result.output["metrics"] == source["metrics"]
    assert any(
        item["scope"] == "actual_metric_extraction" for item in result.output["partial_failures"]
    )
    assert any(
        item["scope"] == "metric" and item["metric_index"] == 1
        for item in result.output["partial_failures"]
    )


def test_registered_finalizer_restores_source_after_freezing_contract() -> None:
    metadata = yaml.safe_load((SKILL_ROOT / "skill.yaml").read_text(encoding="utf-8"))
    frozen = build_frozen_finalization_contract(
        "skills/cs_research/actual-metric-records-closure",
        metadata["finalization"],
        repo_root=ROOT,
    )
    source = _source()
    draft = {"unknowns": [], "metrics": [{**source["metrics"][0], "actual_value": 99.0}]}

    finalized = finalize_and_validate_registered_output(
        repo_root=ROOT,
        skill_path="skills/cs_research/actual-metric-records-closure",
        frozen_contract=frozen,
        input_payload=_input(source),
        draft_payload=draft,
        final_schema=_schema("output.schema.json"),
    )

    assert finalized.status == "succeeded"
    assert finalized.output["metrics"] == source["metrics"]
    assert not finalized.errors


def test_model_view_excludes_the_full_actual_metric_and_execution_records() -> None:
    metadata = yaml.safe_load((SKILL_ROOT / "skill.yaml").read_text(encoding="utf-8"))
    payload = {
        "reproduction_experiment_design": {
            "status": "ready",
            "target_experiments": [{"name": "demo"}],
            "protocol": {"name": "held-out"},
            "intentional_deviations": [],
            "unknowns": [],
            "argv": ["must-not-reach-model"],
        },
        "execution_readiness_record": {
            "status": "ready",
            "plan_resource_relation_status": "verified",
            "blocking_gaps": [],
            "unknowns": [],
            "environment": {"secret": "must-not-reach-model"},
        },
        "frozen_execution_input": {
            "execution_plan_id": "execplan:" + "1" * 64,
            "readiness_status": "ready",
            "frozen_input_sha256": "2" * 64,
            "argv": ["must-not-reach-model"],
        },
        "raw_execution_record": {"stdout": {"text": "must-not-reach-model"}},
        "raw_actual_metrics": {"metrics": [{"actual_value": 99.0}]},
    }

    visible = project_provider_parameters(metadata, deepcopy(payload))

    assert resolve_provider_message_contract(metadata) == SCIENTIFIC_MINIMAL_V1
    assert set(visible) == {
        "reproduction_experiment_design",
        "execution_readiness_record",
        "frozen_execution_input",
    }
    assert "argv" not in visible["reproduction_experiment_design"]
    assert "environment" not in visible["execution_readiness_record"]
    assert set(visible["frozen_execution_input"]) == {
        "execution_plan_id",
        "readiness_status",
        "frozen_input_sha256",
    }
    assert "must-not-reach-model" not in json.dumps(visible, ensure_ascii=False)
