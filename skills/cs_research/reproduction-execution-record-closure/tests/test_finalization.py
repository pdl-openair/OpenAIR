from __future__ import annotations

import importlib.util
import json
import sys
from copy import deepcopy
from pathlib import Path

import yaml
from jsonschema.validators import validator_for

ROOT = Path(__file__).resolve().parents[4]
SKILL_ROOT = ROOT / "skills/cs_research/reproduction-execution-record-closure"
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
        "cs_execution_record_finalizer", SKILL_ROOT / "scripts/finalize.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _resource() -> dict:
    return {
        "resource_kind": "huggingface_snapshot",
        "repo_id": "demo/model",
        "repo_type": "model",
        "resolved_commit_sha": "3" * 40,
        "actual_identity": {"kind": "huggingface_commit", "commit_sha": "3" * 40},
        "workspace_relative_path": "resource/model",
    }


def _frozen() -> dict:
    return {
        "execution_plan_id": "execplan:" + "1" * 64,
        "readiness_status": "ready",
        "executable": "Scripts/python.exe",
        "argv": ["train.py", "{{openair-resource:model}}"],
        "working_directory": "repository",
        "timeout_seconds": 600,
        "environment_variables": {"OPENAIR_TEST_TOKEN": "secret"},
        "resource_bindings": [{"usage": "model", "argv_index": 1, "resource": _resource()}],
        "frozen_identities": {
            "code": {},
            "environment": {"environment_identity": {"fingerprint_sha256": "4" * 64}},
            "resources": [],
        },
        "frozen_input_sha256": "5" * 64,
    }


def _raw(*, state: str = "succeeded", execution_status: str = "succeeded") -> dict:
    result = {
        "schema_version": "openair-execution-record/v1",
        "state": state,
        "execution_status": execution_status,
        "actual_executable": "environment/Scripts/python.exe",
        "argv": ["train.py", "C:/workspace/resource/model"],
        "working_directory": "repository",
        "environment": {"fingerprint_sha256": "4" * 64},
        "environment_variable_names": ["OPENAIR_TEST_TOKEN"],
        "resource_usage": [
            {
                "usage": "model",
                "planned_resource": _resource(),
                "delivery": {"kind": "argv_path", "argument_index": 1},
                "verification": "verified",
            }
        ],
        "started_at_utc": "2026-10-09T09:00:00Z",
        "ended_at_utc": "2026-10-09T09:00:01Z",
        "duration_seconds": 1.0,
        "exit_code": 0 if state == "succeeded" else 2,
        "timed_out": False,
        "stdout": {"text": '{"accuracy": 1.0}', "truncated": False},
        "stderr": {"text": "", "truncated": False},
        "execution_record_relative_path": "execution/run-1/execution-record.json",
    }
    if state != "succeeded":
        result["failure_code"] = "non_zero_exit"
    return result


def _input(raw: dict | None = None) -> dict:
    return {
        "reproduction_experiment_design": {"status": "ready"},
        "execution_readiness_record": {"status": "ready"},
        "frozen_execution_input": _frozen(),
        **({"raw_execution_record": raw} if raw is not None else {}),
    }


def _schema(name: str) -> dict:
    return json.loads((SKILL_ROOT / name).read_text(encoding="utf-8"))


def test_source_execution_wins_over_model_tampering() -> None:
    raw = _raw()
    draft = {
        "unknowns": ["硬件代际差异仍需科研解释。"],
        "status": "succeeded",
        "execution": {**raw, "exit_code": 99, "argv": ["forged"]},
    }

    result = _finalizer().finalize(_input(raw), draft)

    assert result.status == "succeeded"
    assert result.output["status"] == "succeeded"
    assert result.output["execution"] == raw
    assert result.output["execution"]["exit_code"] == 0
    assert result.output["unknowns"][0] == "硬件代际差异仍需科研解释。"
    schema = _schema("output.schema.json")
    assert not list(validator_for(schema)(schema).iter_errors(result.output))


def test_failed_execution_cannot_be_rewritten_as_success() -> None:
    raw = _raw(state="failed", execution_status="non_zero_exit")

    result = _finalizer().finalize(_input(raw), {"unknowns": [], "status": "succeeded"})

    assert result.output["status"] == "failed"
    assert result.output["execution"] == raw
    assert result.output["execution"]["exit_code"] == 2
    assert result.output["partial_failures"][0]["failure_code"] == "non_zero_exit"


def test_missing_raw_execution_does_not_invent_a_success_or_trace() -> None:
    result = _finalizer().finalize(_input(), {"unknowns": []})

    assert result.output["status"] == "failed"
    assert result.output["execution"] is None
    assert result.output["raw_output_trace"] == []
    assert result.output["actual_result_provenance"] == {}
    assert "raw_execution_record_unavailable" in result.output["unknowns"]


def test_registered_finalizer_restores_execution_after_freezing_contract() -> None:
    metadata = yaml.safe_load((SKILL_ROOT / "skill.yaml").read_text(encoding="utf-8"))
    frozen = build_frozen_finalization_contract(
        "skills/cs_research/reproduction-execution-record-closure",
        metadata["finalization"],
        repo_root=ROOT,
    )
    raw = _raw()
    draft = {"unknowns": [], "execution": {**raw, "exit_code": 99}}

    finalized = finalize_and_validate_registered_output(
        repo_root=ROOT,
        skill_path="skills/cs_research/reproduction-execution-record-closure",
        frozen_contract=frozen,
        input_payload=_input(raw),
        draft_payload=draft,
        final_schema=_schema("output.schema.json"),
    )

    assert finalized.status == "succeeded"
    assert finalized.output["execution"] == raw
    assert not finalized.errors


def test_model_view_excludes_full_raw_execution_and_frozen_commands() -> None:
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
            "code": {"revision": "must-not-reach-model"},
        },
        "frozen_execution_input": {
            "execution_plan_id": "execplan:" + "1" * 64,
            "readiness_status": "ready",
            "frozen_input_sha256": "2" * 64,
            "argv": ["must-not-reach-model"],
        },
        "raw_execution_record": _raw(),
        "raw_actual_metrics": {"metrics": [{"actual_value": 99.0}]},
    }

    visible = project_provider_parameters(metadata, deepcopy(payload))

    assert resolve_provider_message_contract(metadata) == SCIENTIFIC_MINIMAL_V1
    assert set(visible) == {
        "reproduction_experiment_design",
        "execution_readiness_record",
        "frozen_execution_input",
    }
    assert set(visible["frozen_execution_input"]) == {
        "execution_plan_id",
        "readiness_status",
        "frozen_input_sha256",
    }
    assert "must-not-reach-model" not in json.dumps(visible, ensure_ascii=False)
