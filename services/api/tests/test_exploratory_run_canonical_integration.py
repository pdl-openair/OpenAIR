from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import venv

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
SKILL_ROOT = REPO_ROOT / "skills" / "cs_research" / "exploratory-run"
PLAN_ROOT = REPO_ROOT / "skills" / "cs_research" / "execution-plan-fact-assembly"
PLAN_BINDING_ID = "bind:cs_reproduction:execution_plan_acquisition:skill:execution_plan_fact_assembly"
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))
# Several cs_research skills expose a bare-top-level `adapter` package; in the
# shared per-group pytest process whichever skill imported first owns the name.
# Drop any earlier binding so this import always resolves to THIS skill's
# package regardless of file order (sync-2 accommodation).
sys.modules.pop("adapter", None)

from adapter import exploratory_run_adapter as exploratory_adapter  # noqa: E402
from app.execution.python_adapter_skills import (  # noqa: E402
    PYTHON_ADAPTER_ENTRYPOINTS,
    resolve_python_adapter_skill,
)
from app.execution.research_input_contract_gate import (  # noqa: E402
    InputContractGateFailure,
    SkillInputRequest,
    resolve_registered_skill_input,
    validate_registered_skill_input_snapshot,
)


def _registry_row() -> dict:
    rows = yaml.safe_load((REPO_ROOT / "skills" / "registry.yaml").read_text(encoding="utf-8"))["skills"]
    return next(row for row in rows if row["skill_id"] == "exploratory_run")


def _binding_row() -> dict:
    rows = yaml.safe_load((REPO_ROOT / "skills" / "skill-bindings.yaml").read_text(encoding="utf-8"))["bindings"]
    return next(row for row in rows if row["bindingId"] == "bind:sdr:exploratory_execution:skill:exploratory_run")


def _plan_binding_row() -> dict:
    rows = yaml.safe_load((REPO_ROOT / "skills" / "skill-bindings.yaml").read_text(encoding="utf-8"))["bindings"]
    return next(row for row in rows if row["bindingId"] == PLAN_BINDING_ID)


def _python_relative() -> str:
    return "Scripts/python.exe" if os.name == "nt" else "bin/python"


def _prepare_workspace(root: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    environment = root / "environment"
    venv.EnvBuilder(with_pip=False).create(environment)
    record = {
        "schema_version": "openair-environment-record/v1",
        "state": "succeeded",
        "environment_identity": {"fingerprint_sha256": "0" * 64},
        "verification": {},
        "execution_evidence": {},
        "workspace_relative_path": "environment",
        "environment_record_relative_path": "environment/environment-record.json",
    }
    (environment / "environment-record.json").write_text(json.dumps(record), encoding="utf-8")
    (root / "repository").mkdir()
    monkeypatch.chdir(root)
    monkeypatch.setenv("OPENAIR_ADAPTER_EXECUTION_CWD", str(root))
    return _python_relative()


def test_registry_binding_and_python_adapter_use_the_formal_external_contract() -> None:
    registry = _registry_row()
    assert registry["runtime"] == "python_adapter"
    assert registry["runtime_contract"] == {
        "kind": "external_adapter",
        "requires_external_action": True,
        "adapter_id": "python_adapter:exploratory_run",
        "evidence_contract_version": "external-action-evidence/v1",
    }
    assert resolve_python_adapter_skill("exploratory_run") == (
        "exploratory_run",
        "skills/cs_research/exploratory-run",
    )
    assert PYTHON_ADAPTER_ENTRYPOINTS["python_adapter:exploratory_run"] == (
        "adapter.exploratory_run_adapter",
        "run_exploratory_run",
        "payload_v1",
    )

    binding = _binding_row()["inputSources"]
    fields = {row["targetField"]: row for row in binding["fields"]}
    assert binding["contractVersion"] == 2
    assert {name for name, row in fields.items() if row["sourcePresence"] == "required"} == {
        "executable", "argv", "working_directory", "timeout_seconds"
    }
    assert {name for name, row in fields.items() if row["sourcePresence"] == "optional"} == {
        "environment_variables"
    }
    for name, row in fields.items():
        assert row["source"] == {
            "kind": "artifact",
            "bindingId": PLAN_BINDING_ID,
            "bindingVersion": 1,
            "field": name,
        }


def test_real_process_success_and_non_zero_remain_actual_execution_facts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    executable = _prepare_workspace(tmp_path, monkeypatch)
    success = exploratory_adapter.run_exploratory_run({
        "executable": executable,
        "argv": ["-c", "print('canonical-ok')"],
        "working_directory": "repository",
        "timeout_seconds": 10,
    })
    assert success["state"] == "succeeded"
    assert success["execution_status"] == "succeeded"
    assert success["exit_code"] == 0
    assert success["stdout"]["text"].strip() == "canonical-ok"
    assert (tmp_path / success["execution_record_relative_path"]).is_file()

    failed = exploratory_adapter.run_exploratory_run({
        "executable": executable,
        "argv": ["-c", "import sys; print('before-fail'); sys.exit(7)"],
        "working_directory": "repository",
        "timeout_seconds": 10,
    })
    assert failed["state"] == "failed"
    assert failed["execution_status"] == "non_zero_exit"
    assert failed["failure_code"] == "non_zero_exit"
    assert failed["exit_code"] == 7
    assert "before-fail" in failed["stdout"]["text"]
    assert (tmp_path / failed["execution_record_relative_path"]).is_file()


def _execution_plan_payload() -> dict:
    return {
        "schema_version": "openair-execution-plan/v1",
        "state": "succeeded",
        "plan_status": "complete",
        "execution_plan_id": "execplan:" + "0" * 64,
        "source_identity": {},
        "resource_acquisition_requests": [],
        "python_version": "3.11",
        "requirements": ["pytest==8.0.0"],
        "verify_imports": ["pytest"],
        "dependency_install_timeout_seconds": 240,
        "find_links": [],
        "executable": "bin/python",
        "argv": ["-c", "print('fixed')"],
        "working_directory": "repository",
        "timeout_seconds": 12,
        "environment_variables": {"OPENAIR_TEST_MODE": "1"},
        "execution_resource_binding_plan": [],
        "actual_metric_result_source": {"kind": "execution_stream", "stream": "stdout", "format": "pipe_table"},
        "actual_metric_requests": [{"metric_name": "accuracy", "value_type": "number", "unit": None}],
        "metric_extraction_jobs": [],
        "execution_plan_provenance": [],
        "execution_plan_unresolved": [],
        "execution_plan_runtime_pending": [],
    }


def _execution_plan_artifact(payload: dict | None = None) -> dict:
    return {
        "sourceCapabilityType": "skill",
        "sourceCapabilityId": "execution_plan_fact_assembly",
        "sourceCapabilityDefinitionVersion": 1,
        "sourceBindingRefs": [{"bindingId": PLAN_BINDING_ID, "bindingVersion": 1}],
        "mimeType": "application/json",
        "content": json.dumps(payload or _execution_plan_payload()).encode("utf-8"),
    }


def _minimal_frozen_snapshot() -> tuple[dict, dict]:
    input_schema = json.loads((SKILL_ROOT / "input.schema.json").read_text(encoding="utf-8"))
    plan_output_schema = json.loads((PLAN_ROOT / "output.schema.json").read_text(encoding="utf-8"))
    registry = _registry_row()
    binding = _binding_row()
    frozen_binding = {**binding, "enabled": True}
    plan_binding = {**_plan_binding_row(), "enabled": True}
    snapshot = {
        "definitions": {
            "skills": [
                {
                    "skillId": "exploratory_run",
                    "definitionVersion": 1,
                    "sourceContract": {
                        **registry,
                        "frozenSchemas": {
                            "input": {"sha256": "0" * 64, "schema": input_schema},
                        },
                    },
                },
                {
                    "skillId": "execution_plan_fact_assembly",
                    "definitionVersion": 1,
                    "sourceContract": {
                        "frozenSchemas": {
                            "output": {"sha256": "1" * 64, "schema": plan_output_schema},
                        },
                    },
                },
            ],
            "agents": [],
        },
        "bindings": {"skills": [plan_binding, frozen_binding]},
    }
    return snapshot, frozen_binding


def test_formal_inputs_are_resolved_exactly_and_missing_required_artifact_fails_closed() -> None:
    snapshot, binding = _minimal_frozen_snapshot()
    request = SkillInputRequest(
        task_id="task-exploratory",
        strategy="direct_skill",
        skill_id="exploratory_run",
        definition_version=1,
    )
    expected = {
        "executable": "bin/python",
        "argv": ["-c", "print('fixed')"],
        "working_directory": "repository",
        "timeout_seconds": 12,
        "environment_variables": {"OPENAIR_TEST_MODE": "1"},
    }
    resolved = resolve_registered_skill_input(
        snapshot=snapshot,
        request=request,
        input_context={"artifacts": [_execution_plan_artifact()]},
        skill_binding=binding,
    )
    assert resolved == expected

    # Dispatch-time validation consumes only the WorkUnit-frozen payload and
    # does not re-read a later execution-plan Artifact.
    assert validate_registered_skill_input_snapshot(
        snapshot=snapshot,
        request=request,
        resolved_input=resolved,
        skill_binding=binding,
    ) == expected

    with pytest.raises(InputContractGateFailure) as caught:
        resolve_registered_skill_input(
            snapshot=snapshot,
            request=request,
            input_context={"artifacts": []},
            skill_binding=binding,
        )
    assert caught.value.code == "input_composition_source_artifact_missing"
