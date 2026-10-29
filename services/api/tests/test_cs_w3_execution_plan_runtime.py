from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml
from jsonschema import Draft7Validator

from app.execution.python_adapter_runner import run_python_skill_adapter
from app.execution.python_adapter_skills import (
    resolve_python_adapter_entrypoint,
    resolve_python_adapter_skill,
)

ROOT = Path(__file__).resolve().parents[3]
DOC_ID = "refdoc:" + "a" * 64
REF_ID = "refmetric:" + "b" * 64
CONTENT = (
    "Install torch==2.4.0. Verify import torch.\n"
    "Run python evaluate.py --output results.json from the repository root.\n"
    "Results are printed to stdout as JSON field score.\n"
)


def _registry_rows() -> dict[str, dict]:
    raw = yaml.safe_load((ROOT / "skills" / "registry.yaml").read_text(encoding="utf-8"))
    return {row["skill_id"]: row for row in raw["skills"]}


def _source_package() -> dict:
    digest = hashlib.sha256(CONTENT.encode("utf-8")).hexdigest()
    return {
        "schema_version": "openair-reference-source-package/v1",
        "state": "succeeded",
        "source_kind": "official_repository",
        "repository": "https://example.test/repo.git",
        "requested_revision": "main",
        "resolved_commit_sha": "1" * 40,
        "workspace_relative_path": "repository",
        "documents": [
            {
                "document_id": DOC_ID,
                "path": "README.md",
                "sha256": digest,
                "line_count": 3,
                "content": CONTENT,
            }
        ],
        "read_evidence": {
            "git_version": "git version test",
            "verified_head": "1" * 40,
            "head_matches_upstream_artifact": True,
            "candidate_document_count": 1,
            "included_document_count": 1,
            "total_included_bytes": len(CONTENT.encode("utf-8")),
        },
    }


def _fact(value, excerpt: str) -> dict:
    return {"value": value, "source_document_id": DOC_ID, "source_excerpt": excerpt}


def test_cs_w3_execution_plan_adapter_uses_shared_python_runtime_path() -> None:
    rows = _registry_rows()
    assert resolve_python_adapter_skill("execution_plan_fact_assembly") == (
        "execution_plan_fact_assembly",
        "skills/cs_research/execution-plan-fact-assembly",
    )
    assert rows["execution_plan_fact_assembly"]["runtime_contract"] == {
        "kind": "deterministic",
        "requires_external_action": False,
        "adapter_id": "python_adapter:execution_plan_fact_assembly",
        "evidence_contract_version": "deterministic-output/v1",
    }
    assert resolve_python_adapter_entrypoint("python_adapter:execution_plan_fact_assembly") == (
        "adapter.execution_plan_fact_assembly_adapter",
        "run_execution_plan_fact_assembly",
        "payload_v1",
    )

    interpretation = {
        "resources": [],
        "environment": {
            "python_version": None,
            "requirements": [_fact("torch==2.4.0", "Install torch==2.4.0.")],
            "verify_imports": [_fact("torch", "Verify import torch.")],
            "find_links": None,
        },
        "execution": {
            "entrypoint": _fact(
                "evaluate.py",
                "Run python evaluate.py --output results.json from the repository root.",
            ),
            "arguments": _fact(
                ["--output", "results.json"],
                "Run python evaluate.py --output results.json from the repository root.",
            ),
            "working_directory": _fact(".", "from the repository root"),
            "timeout_seconds": None,
            "environment_variables": None,
        },
        "metric_extraction_jobs": [
            {
                "reference_ids": [REF_ID],
                "result_source": {
                    "kind": "execution_stream",
                    "stream": "stdout",
                    "format": "json",
                    "table": None,
                    "source_document_id": DOC_ID,
                    "source_excerpt": "Results are printed to stdout as JSON field score.",
                },
                "selectors": [
                    {"reference_id": REF_ID, "selector": {"json_pointer": "/score"}}
                ],
            }
        ],
        "unresolved": [],
    }
    reference_metrics = [
        {
            "reference_id": REF_ID,
            "metric_name": "score",
            "reference_value": 0.9,
            "value_type": "number",
            "unit": None,
        }
    ]
    result = run_python_skill_adapter(
        repo_root=ROOT,
        skill_id="execution_plan_fact_assembly",
        skill_path="skills/cs_research/execution-plan-fact-assembly",
        adapter_id="python_adapter:execution_plan_fact_assembly",
        input_payload={
            "source_package": _source_package(),
            "interpretation": interpretation,
            "reference_metrics": reference_metrics,
        },
    )

    schema = json.loads(
        (ROOT / "skills/cs_research/execution-plan-fact-assembly/output.schema.json").read_text(
            encoding="utf-8"
        )
    )
    Draft7Validator(schema).validate(result)
    assert result["plan_status"] == "complete"
    assert result["actual_metric_result_source"] == {
        "kind": "execution_stream",
        "stream": "stdout",
        "format": "json",
    }
    assert result["actual_metric_requests"] == [
        {
            "metric_name": "score",
            "value_type": "number",
            "unit": None,
            "selector": {"json_pointer": "/score"},
        }
    ]
    assert result["execution_plan_runtime_pending"] == ["execution_record_relative_path"]
