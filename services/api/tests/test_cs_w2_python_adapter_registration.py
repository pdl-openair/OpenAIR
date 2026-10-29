from __future__ import annotations

import json

from jsonschema import Draft7Validator
from pathlib import Path
import subprocess

import yaml

from app.execution.python_adapter_runner import run_python_skill_adapter
from app.execution.python_adapter_skills import (
    resolve_python_adapter_entrypoint,
    resolve_python_adapter_skill,
)


ROOT = Path(__file__).resolve().parents[3]

_EXPECTED = {
    "official_reference_source_acquisition": (
        "skills/cs_research/official-reference-source-acquisition",
        "python_adapter:official_reference_source_acquisition",
        (
            "adapter.official_reference_source_acquisition_adapter",
            "run_official_reference_source_acquisition",
            "payload_v1",
        ),
    ),
    "reference_metric_fact_assembly": (
        "skills/cs_research/reference-metric-fact-assembly",
        "python_adapter:reference_metric_fact_assembly",
        (
            "adapter.reference_metric_fact_assembly_adapter",
            "run_reference_metric_fact_assembly",
            "payload_v1",
        ),
    ),
    "reproduction_conclusion_closure": (
        "skills/cs_research/reproduction-conclusion-closure",
        "python_adapter:reproduction_conclusion_closure",
        (
            "adapter.reproduction_conclusion_closure_adapter",
            "run_reproduction_conclusion_closure",
            "payload_v1",
        ),
    ),
}


def _registry_rows() -> dict[str, dict]:
    raw = yaml.safe_load((ROOT / "skills" / "registry.yaml").read_text(encoding="utf-8"))
    rows = raw.get("skills") if isinstance(raw, dict) else raw
    assert isinstance(rows, list)
    return {row["skill_id"]: row for row in rows}


def _git(*args: str, cwd: Path) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)
    return result.stdout.strip()


def test_cs_w2_python_adapters_use_existing_canonical_registration_path() -> None:
    rows = _registry_rows()
    for skill_id, (path, adapter_id, entrypoint) in _EXPECTED.items():
        assert resolve_python_adapter_skill(skill_id) == (skill_id, path)
        assert rows[skill_id]["runtime_contract"]["adapter_id"] == adapter_id
        assert rows[skill_id]["runtime_contract"]["kind"] == "deterministic"
        assert resolve_python_adapter_entrypoint(adapter_id) == entrypoint


def test_official_source_reader_runs_through_shared_python_runner(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    repository = workspace / "repository"
    repository.mkdir()
    _git("init", cwd=repository)
    _git("config", "user.email", "test@example.com", cwd=repository)
    _git("config", "user.name", "OpenAIR Test", cwd=repository)
    (repository / "README.md").write_text("# Official\n\nAvg. = 81.58\n", encoding="utf-8")
    _git("add", "README.md", cwd=repository)
    _git("commit", "-m", "fixture", cwd=repository)
    commit = _git("rev-parse", "HEAD", cwd=repository)

    result = run_python_skill_adapter(
        repo_root=ROOT,
        skill_id="official_reference_source_acquisition",
        skill_path="skills/cs_research/official-reference-source-acquisition",
        adapter_id="python_adapter:official_reference_source_acquisition",
        input_payload={
            "code_source": {
                "state": "succeeded",
                "repository": "https://example.test/official.git",
                "requested_revision": "main",
                "resolved_commit_sha": commit,
                "verified_head": commit,
                "workspace_relative_path": "repository",
                "execution_evidence": {},
            }
        },
        execution_cwd=workspace,
        environment_overrides={"OPENAIR_ADAPTER_EXECUTION_CWD": str(workspace.resolve())},
    )

    assert result["state"] == "succeeded"
    assert result["resolved_commit_sha"] == commit
    assert result["read_evidence"]["head_matches_upstream_artifact"] is True
    assert [row["path"] for row in result["documents"]] == ["README.md"]


def test_reference_fact_assembly_runs_through_shared_python_runner() -> None:
    content = "Avg. = 81.58\nexpected output\n"
    import hashlib

    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    document_id = hashlib.sha256(
        f"https://example.test/official.git\n{'a' * 40}\nREADME.md\n{digest}".encode("utf-8")
    ).hexdigest()
    source_package = {
        "schema_version": "openair-reference-source-package/v1",
        "state": "succeeded",
        "source_kind": "official_repository",
        "repository": "https://example.test/official.git",
        "requested_revision": "main",
        "resolved_commit_sha": "a" * 40,
        "workspace_relative_path": "repository",
        "documents": [
            {
                "document_id": f"refdoc:{document_id}",
                "path": "README.md",
                "sha256": digest,
                "line_count": 2,
                "content": content,
            }
        ],
        "read_evidence": {
            "git_version": "git version test",
            "verified_head": "a" * 40,
            "head_matches_upstream_artifact": True,
            "candidate_document_count": 1,
            "included_document_count": 1,
            "total_included_bytes": len(content.encode("utf-8")),
        },
    }
    interpretation = {
        "reference_candidates": [
            {
                "metric_name": "Avg.",
                "reference_value": 81.58,
                "value_type": "number",
                "unit": None,
                "experiment_conditions": ["same evaluation setting"],
                "source_document_id": f"refdoc:{document_id}",
                "source_excerpt": "Avg. = 81.58",
                "comparison_rule": {"kind": "exact"},
                "comparison_rule_basis_excerpt": "expected output",
            }
        ],
        "unresolved": [],
    }

    result = run_python_skill_adapter(
        repo_root=ROOT,
        skill_id="reference_metric_fact_assembly",
        skill_path="skills/cs_research/reference-metric-fact-assembly",
        adapter_id="python_adapter:reference_metric_fact_assembly",
        input_payload={"source_package": source_package, "interpretation": interpretation},
    )

    assert result["assembly_status"] == "complete"
    assert result["reference_metric_comparison_requests"] == [
        {
            "metric_name": "Avg.",
            "reference_value": 81.58,
            "value_type": "number",
            "unit": None,
            "comparison_rule": {"kind": "exact"},
        }
    ]


def test_official_source_reader_is_workspace_bound_in_durable_runtime() -> None:
    runtime_source = (ROOT / "services" / "api" / "app" / "execution" / "durable_skill_runtime.py").read_text(encoding="utf-8")
    assert '"python_adapter:official_reference_source_acquisition"' in runtime_source
    marker = 'environment_overrides = {\n                    "OPENAIR_ADAPTER_EXECUTION_CWD": str(execution_cwd)\n                }'
    assert marker in runtime_source


def test_cs_w2_skill_assets_match_registry_and_schema_contracts() -> None:
    rows = _registry_rows()
    expected_paths = {
        "reference_metric_acquisition": "skills/cs_reproduction/reference-metric-acquisition",
        "official_reference_source_acquisition": "skills/cs_research/official-reference-source-acquisition",
        "reference_metric_interpretation": "skills/cs_research/reference-metric-interpretation",
        "reference_metric_fact_assembly": "skills/cs_research/reference-metric-fact-assembly",
    }
    for skill_id, relative_path in expected_paths.items():
        skill_dir = ROOT / relative_path
        assert skill_dir.is_dir()
        assert (skill_dir / "SKILL.md").is_file()
        skill_yaml = yaml.safe_load((skill_dir / "skill.yaml").read_text(encoding="utf-8"))
        registry = rows[skill_id]
        assert skill_yaml["skill_id"] == registry["skill_id"]
        assert skill_yaml["claude_name"] == registry["claude_name"]
        assert skill_yaml["runtime"] == registry["runtime"]
        assert skill_yaml["skill_type"] == registry["skill_type"]
        assert skill_yaml["stages"] == registry["stages"]
        assert skill_yaml["substages"] == registry["substages"]
        assert skill_yaml["runtime_contract"] == registry["runtime_contract"]
        assert skill_yaml["notes"] == registry["notes"]
        for schema_name in ("input.schema.json", "output.schema.json"):
            schema_path = skill_dir / schema_name
            if not schema_path.exists():
                continue
            schema = json.loads(schema_path.read_text(encoding="utf-8"))
            Draft7Validator.check_schema(schema)
