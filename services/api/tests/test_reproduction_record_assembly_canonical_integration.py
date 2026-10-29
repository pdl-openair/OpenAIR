from __future__ import annotations

from pathlib import Path

import yaml
from app.execution.python_adapter_runner import run_python_skill_adapter
from app.execution.python_adapter_skills import (
    PYTHON_ADAPTER_ENTRYPOINTS,
    resolve_python_adapter_skill,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


def _registry_row() -> dict:
    rows = yaml.safe_load((REPO_ROOT / "skills" / "registry.yaml").read_text(encoding="utf-8"))[
        "skills"
    ]
    return next(row for row in rows if row["skill_id"] == "reproduction_record_assembly")


def test_registered_deterministic_adapter_has_one_execution_entrypoint() -> None:
    registry = _registry_row()
    assert registry["stages"] == ["execution_validation"]
    assert registry["substages"] == ["result_acceptance"]
    assert registry["runtime_contract"] == {
        "kind": "deterministic",
        "requires_external_action": False,
        "adapter_id": "python_adapter:reproduction_record_assembly",
        "evidence_contract_version": "deterministic-output/v1",
    }
    assert resolve_python_adapter_skill("reproduction_record_assembly") == (
        "reproduction_record_assembly",
        "skills/cs_research/reproduction-record-assembly",
    )
    assert PYTHON_ADAPTER_ENTRYPOINTS["python_adapter:reproduction_record_assembly"] == (
        "adapter.reproduction_record_assembly_adapter",
        "run_reproduction_record_assembly",
        "payload_v1",
    )
    ids = [
        row["skill_id"]
        for row in yaml.safe_load(
            (REPO_ROOT / "schemas" / "sdr" / "substage-skill-map.yaml").read_text(encoding="utf-8")
        )["result_acceptance"]
    ]
    assert "reference_metric_comparison" in ids
    assert "reproduction_record_assembly" in ids
    assert "quality_audit" in ids


def test_registered_adapter_preserves_missing_components_as_incomplete() -> None:
    incomplete_input = {
        "code_identity": None,
        "resource_identities": [],
        "environment_record": None,
        "execution_record": None,
        "actual_metrics": None,
        "reference_comparison": None,
    }
    output = run_python_skill_adapter(
        repo_root=REPO_ROOT,
        skill_id="reproduction_record_assembly",
        skill_path="skills/cs_research/reproduction-record-assembly",
        adapter_id="python_adapter:reproduction_record_assembly",
        input_payload=incomplete_input,
    )
    assert output["state"] == "succeeded"
    assert output["record_status"] == "incomplete"
    assert output["reproduction_result"] == "indeterminate"
    assert output["missing_components"] == [
        "code_identity",
        "environment_record",
        "execution_record",
        "actual_metrics",
        "reference_comparison",
    ]
