"""Protect assembly's receipt of real acquisition output at its registered entry."""

import importlib
import importlib.util
import json
import shutil
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import jsonschema
import pytest
from adapter.reproduction_record_assembly_adapter import run_reproduction_record_assembly
from app.execution.durable_skill_runtime import freeze_registered_source_contract
from app.execution.python_adapter_runner import run_python_skill_adapter
from app.execution.research_input_contract_gate import (
    SkillInputRequest,
    validate_registered_skill_input_snapshot,
)
from app.execution.skill_schema_contract import build_frozen_skill_schema_contract

ROOT = Path(__file__).resolve().parents[4]
ASSEMBLY_PATH = "skills/cs_research/reproduction-record-assembly"
ACQUISITION_PATH = "skills/cs_research/repository-revision-acquisition"


def _complete_payload(tmp_path, monkeypatch):
    material_path = Path(__file__).with_name("test_reproduction_record_assembly_adapter.py")
    spec = importlib.util.spec_from_file_location("assembly_record_materials", material_path)
    materials = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(materials)
    return materials._payload(tmp_path, monkeypatch)


@pytest.fixture(scope="module")
def actual_acquisition(tmp_path_factory):
    """Only redirect the external clone transport; execute all Git work for real."""
    skill_root = ROOT / ACQUISITION_PATH
    package_name = "assembly_test_acquisition"
    spec = importlib.util.spec_from_file_location(
        package_name,
        skill_root / "adapter/__init__.py",
        submodule_search_locations=[str(skill_root / "adapter")],
    )
    package = importlib.util.module_from_spec(spec)
    sys.modules[package_name] = package
    spec.loader.exec_module(package)
    acquisition = importlib.import_module(f"{package_name}.repository_revision_adapter")
    base = tmp_path_factory.mktemp("code-handoff")
    source = base / "source"
    source.mkdir()
    git = shutil.which("git")
    assert git, "real Git is required for this evidence"
    subprocess.run([git, "init", "-b", "main"], cwd=source, check=True, capture_output=True)
    (source / "README.md").write_text("Local repository transport material.\n", encoding="utf-8")
    subprocess.run([git, "add", "README.md"], cwd=source, check=True, capture_output=True)
    subprocess.run(
        [
            git,
            "-c",
            "user.name=OpenAIR Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-m",
            "local material",
        ],
        cwd=source,
        check=True,
        capture_output=True,
    )
    workspace = base / "workspace"
    workspace.mkdir()
    original = acquisition._run_git

    def local_transport(executable, args, *, cwd):
        if args[:2] == ["clone", "--no-checkout"]:
            env = acquisition._git_environment()
            env["GIT_ALLOW_PROTOCOL"] = "file"
            return subprocess.run(
                [executable, "clone", "--no-checkout", "--", str(source), "repository"],
                cwd=cwd,
                env=env,
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
        return original(executable, args, cwd=cwd)

    evidence = {
        "kind": "primary_paper_author_declared_code_link",
        "source_url": "https://arxiv.org/abs/2104.08821v4",
        "repository_url": "https://github.com/princeton-nlp/simcse.git",
        "selection_basis": "unique_author_declared_code_link_in_primary_paper",
        "content_form": "metadata_only",
        "paper_identity": {"arxiv_id": "2104.08821v4", "title": "SimCSE"},
    }
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(acquisition, "_run_git", local_transport)
        patch.chdir(workspace)
        patch.setenv("OPENAIR_ADAPTER_EXECUTION_CWD", str(workspace))
        result = acquisition.run_repository_revision_acquisition(
            {
                "repository": evidence["repository_url"],
                "revision": "HEAD",
                "source_evidence": evidence,
            }
        )
    contract = build_frozen_skill_schema_contract(ACQUISITION_PATH)
    jsonschema.validate(result, contract["output"]["schema"])
    actual_head = subprocess.run(
        [git, "rev-parse", "HEAD"],
        cwd=workspace / "repository",
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert result["state"] == "succeeded", result
    assert result["resolved_commit_sha"] == result["verified_head"] == actual_head
    return result, workspace


def _assemble(code, workspace):
    payload = {
        "code_identity": code,
        "resource_identities": [],
        "environment_record": None,
        "execution_record": None,
        "actual_metrics": None,
        "reference_comparison": None,
    }
    return _assemble_payload(payload, workspace)


def _assemble_payload(payload, workspace):
    before = deepcopy(payload)
    direct_result = run_reproduction_record_assembly(payload)
    assert payload == before
    result = run_python_skill_adapter(
        repo_root=ROOT,
        skill_id="reproduction_record_assembly",
        skill_path=ASSEMBLY_PATH,
        adapter_id="python_adapter:reproduction_record_assembly",
        input_payload=payload,
        execution_cwd=workspace,
        environment_overrides={"OPENAIR_ADAPTER_EXECUTION_CWD": str(workspace)},
    )
    assert result == direct_result
    output = build_frozen_skill_schema_contract(ASSEMBLY_PATH)["output"]["schema"]
    jsonschema.validate(result, output)
    return result


@pytest.mark.parametrize(
    "content_form", ["metadata_only", "landing_page", "html", "pdf", "xml", "tei"]
)
def test_registered_assembly_preserves_formal_evidence_variants(actual_acquisition, content_form):
    code, workspace = actual_acquisition
    code = deepcopy(code)
    code["source_evidence"]["content_form"] = content_form
    code["source_evidence"]["paper_identity"] = {"doi": "10.1/example", "openalex_id": "W123"}
    result = _assemble(code, workspace)
    assert result["state"] == "succeeded"
    assert result["code_identity"]["source_evidence"] == code["source_evidence"]


def test_registered_assembly_allows_same_repository_normalized_identity(actual_acquisition):
    code, workspace = actual_acquisition
    code = deepcopy(code)
    code["source_evidence"]["repository_url"] = "https://GITHUB.COM/princeton-nlp/SIMCSE/"
    result = _assemble(code, workspace)
    assert result["state"] == "succeeded"
    assert result["code_identity"]["source_evidence"] == code["source_evidence"]


@pytest.mark.parametrize("shape", ["complete", "legacy", "absent"])
def test_registered_assembly_accepts_actual_git_output(actual_acquisition, shape):
    code, workspace = actual_acquisition
    code = deepcopy(code)
    if shape == "legacy":
        code["source_evidence"].pop("content_form")
        code["source_evidence"].pop("paper_identity")
    elif shape == "absent":
        code.pop("source_evidence")
    result = _assemble(code, workspace)
    assert result["state"] == "succeeded", result
    assert result["record_status"] == "incomplete"
    assert result["reproduction_result"] == "indeterminate"
    assert result["missing_components"] == [
        "environment_record",
        "execution_record",
        "actual_metrics",
        "reference_comparison",
    ]
    assert result["code_identity"]["resolved_commit_sha"] == code["verified_head"]
    if "source_evidence" in code:
        assert result["code_identity"]["source_evidence"] == code["source_evidence"]
    else:
        assert "source_evidence" not in result["code_identity"]
