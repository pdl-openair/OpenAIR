"""Declarative workspace-file registration shared by legacy and v1 paths.

Covers the pure collector (glob/dedupe/order/caps), the markdown asset-ref
rewrite, the orchestration-v1 helper (deterministic ids, dedupe against
already-materialized artifacts, best-effort failure isolation) and the
work-unit live progress read-through route.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import pytest

from app.execution.declared_skill_files import (
    collect_declared_files,
    declared_file_segment,
    read_declared_file_bytes,
    rewrite_markdown_asset_refs,
)


# ---------------------------------------------------------------------------
# Pure collector
# ---------------------------------------------------------------------------


def _make_unit(tmp_path: Path) -> tuple[Path, str, str]:
    base = tmp_path / "task_t1" / "workunit_w1"
    (base / "skill-dir" / "figures").mkdir(parents=True)
    (base / "skill-dir" / "pareto").mkdir(parents=True)
    return base, "task_t1", "workunit_w1"


def test_collect_matches_patterns_and_orders_images_first(tmp_path: Path) -> None:
    base, task_id, wu = _make_unit(tmp_path)
    (base / "skill-dir" / "report.md").write_text("# r", encoding="utf-8")
    (base / "skill-dir" / "figures" / "fig.png").write_bytes(b"\x89PNG")
    (base / "skill-dir" / "pareto" / "01_MgO.cif").write_text("data_MgO", encoding="utf-8")
    files = collect_declared_files(
        str(tmp_path), task_id, wu,
        ["skill-dir/report.md", "skill-dir/figures/*.png", "skill-dir/pareto/*.cif"],
    )
    rels = [f.rel for f in files]
    assert rels == ["skill-dir/figures/fig.png", "skill-dir/report.md", "skill-dir/pareto/01_MgO.cif"]
    assert files[0].format == "png" and files[1].format == "markdown" and files[2].format == "cif"


def test_collect_skips_unsupported_and_oversized(tmp_path: Path) -> None:
    base, task_id, wu = _make_unit(tmp_path)
    (base / "skill-dir" / "note.txt").write_text("x", encoding="utf-8")
    (base / "skill-dir" / "big.png").write_bytes(b"0" * (5 * 1024 * 1024 + 1))
    (base / "skill-dir" / "ok.csv").write_text("a,b", encoding="utf-8")
    files = collect_declared_files(str(tmp_path), task_id, wu, ["skill-dir/*"])
    assert [f.rel for f in files] == ["skill-dir/ok.csv"]


def test_collect_missing_dir_or_patterns_yields_nothing(tmp_path: Path) -> None:
    assert collect_declared_files(str(tmp_path), "task_t1", "workunit_w1", ["a/*.md"]) == []
    base, task_id, wu = _make_unit(tmp_path)
    (base / "skill-dir" / "report.md").write_text("# r", encoding="utf-8")
    assert collect_declared_files(str(tmp_path), task_id, wu, None) == []
    assert collect_declared_files(str(tmp_path), task_id, wu, [123, "  "]) == []


def test_markdown_asset_refs_rewrite_to_platform_raw_urls(tmp_path: Path) -> None:
    base = tmp_path
    md_dir = base / "skill-dir"
    raw = '# T\n\n<img src="figures/fig.png" width="600">\n\n![alt](figures/fig.png)\n\n<img src="https://x/y.png">\n'
    registered = {"skill-dir/figures/fig.png": "artifact_123"}
    rewritten = rewrite_markdown_asset_refs(raw, base, md_dir, "task_t1", registered)
    assert '/api/tasks/task_t1/artifacts/artifact_123/raw' in rewritten
    assert rewritten.count("artifact_123") == 2
    assert "https://x/y.png" in rewritten  # absolute refs stay intact


def test_read_declared_file_bytes_rewrites_markdown_only(tmp_path: Path) -> None:
    base, task_id, wu = _make_unit(tmp_path)
    (base / "skill-dir" / "figures" / "fig.png").write_bytes(b"\x89PNG")
    (base / "skill-dir" / "report.md").write_text('<img src="figures/fig.png">', encoding="utf-8")
    files = collect_declared_files(str(tmp_path), task_id, wu, ["skill-dir/figures/*.png", "skill-dir/report.md"])
    png, md = files
    registered: dict[str, str] = {}
    png_bytes = read_declared_file_bytes(png, base=base, task_id=task_id, registered=registered)
    assert png_bytes == b"\x89PNG"
    registered[png.rel] = "artifact_png"
    md_bytes = read_declared_file_bytes(md, base=base, task_id=task_id, registered=registered)
    assert b"/api/tasks/task_t1/artifacts/artifact_png/raw" in md_bytes


# ---------------------------------------------------------------------------
# Orchestration v1 helper
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Attempt:
    capability_call_id: str = "call_1"
    attempt_number: int = 1


def _fence(repository_module):
    return repository_module.AuthorizedDispatch(
        dispatch_id="dispatch:abc",
        task_id="task_t1",
        execution_decision_id="ed_1",
        work_unit_id="workunit_w1",
        idempotency_key="idem-1",
        lease_owner="worker-1",
        attempt_count=1,
        authorization_task_revision=2,
        work_unit_payload={},
        external_attempt=_Attempt(),
    )


@dataclass
class _StoredBlob:
    storage_type: str = "external"
    storage_ref: str = ""
    storage_scope: str | None = "minio+http://minio:9000/openair-artifacts"


class _StubWriteService:
    def __init__(self) -> None:
        self.stored: list[tuple[str, bytes, str]] = []

    def store_durable_bytes(self, task_id: str, ref: str, data: bytes, mime: str, **_: object):
        self.stored.append((ref, data, mime))
        return _StoredBlob(storage_ref=ref)


@pytest.fixture()
def v1_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    import app.modules.research_orchestration_repository as repo_module

    monkeypatch.setenv("OPENAIR_WORKSPACE_ROOT", str(tmp_path))
    stub = _StubWriteService()
    monkeypatch.setattr(repo_module, "artifact_write_service", stub)
    base = tmp_path / "task_t1" / "workunit_w1"
    (base / "skill-dir" / "pareto").mkdir(parents=True)
    (base / "skill-dir" / "pareto" / "01_MgO.cif").write_text("data_MgO", encoding="utf-8")
    (base / "skill-dir" / "leaderboard.csv").write_text("rank\n1\n", encoding="utf-8")
    return repo_module, base, stub


def _register_skill(monkeypatch: pytest.MonkeyPatch, patterns) -> None:
    import app.execution.durable_skill_runtime as runtime_module

    monkeypatch.setattr(
        runtime_module,
        "resolve_registered_durable_skill",
        lambda skill_id: {"sourceContract": {"runtime_contract": {"artifact_files": patterns}}},
    )


_WORK = {"workUnitId": "workunit_w1", "substageInstanceId": "sub_1"}
_ASSIGNMENT = {"executorRef": {"executorType": "skill", "executorId": "crossover_skill"}}


def test_v1_helper_registers_files_with_deterministic_ids(v1_env, monkeypatch: pytest.MonkeyPatch) -> None:
    repo_module, base, stub = v1_env
    _register_skill(monkeypatch, ["skill-dir/leaderboard.csv", "skill-dir/pareto/*.cif"])
    repo = repo_module.ResearchOrchestrationRepository()
    rows, entries, refs = repo._declared_skill_file_artifacts(
        fence=_fence(repo_module),
        work=_WORK,
        assignment=_ASSIGNMENT,
        call_id="call_1",
        result_id="res_1",
        existing_artifacts_by_id={},
        requires_any_review=False,
        artifact_contract="direct_skill:crossover:v1",
        produced_artifact_type="pareto_candidates",
        expected_types=["pareto_candidates"],
    )
    assert len(rows) == 2 and len(entries) == 2 and len(refs) == 2
    # images-first ordering does not apply here (no images); csv before cif via sorted rel
    id_csv = "artifact:dispatch:abc:file:skill-dir_leaderboard.csv"
    id_cif = "artifact:dispatch:abc:file:skill-dir_pareto_01_MgO.cif"
    assert refs == [id_csv, id_cif]
    row = rows[0]
    assert row["artifactType"] == "pareto_candidates.declared_file"
    assert row["artifactState"] == "usable"
    assert row["sourceCapabilityCallId"] == "call_1" and row["sourceResultId"] == "res_1"
    assert row["workUnitId"] == "workunit_w1" and row["substageInstanceId"] == "sub_1"
    expected_bytes = (base / "skill-dir" / "leaderboard.csv").read_bytes()
    # Downstream input validation (_read_verified_execution_artifacts) rejects
    # any artifact whose validator lacks a sha256 artifactSchemaDigest — the
    # declared-file validator must therefore carry the content digest.
    expected_sha = hashlib.sha256(expected_bytes).hexdigest()
    assert row["validatorResult"]["artifactSchemaDigest"] == expected_sha
    assert row["validatorResult"]["contentSha256"] == expected_sha
    entry = entries[0]
    assert entry["workspaceFile"]["content_sha256"] == expected_sha
    assert entry["metadata"]["relatedSkillId"] == "crossover_skill"
    assert entry["metadata"]["declaredFile"] == "skill-dir/leaderboard.csv"
    assert stub.stored[0][0] == "durable/research-dispatch/dispatch:abc/files/skill-dir/leaderboard.csv"


def test_v1_helper_skips_already_materialized_ids(v1_env, monkeypatch: pytest.MonkeyPatch) -> None:
    repo_module, base, stub = v1_env
    _register_skill(monkeypatch, ["skill-dir/leaderboard.csv"])
    repo = repo_module.ResearchOrchestrationRepository()
    fence = _fence(repo_module)
    existing = {f"artifact:dispatch:abc:file:skill-dir_leaderboard.csv": {}}
    rows, entries, refs = repo._declared_skill_file_artifacts(
        fence=fence, work=_WORK, assignment=_ASSIGNMENT, call_id="call_1", result_id="res_1",
        existing_artifacts_by_id=existing, requires_any_review=False,
        artifact_contract="c", produced_artifact_type="t", expected_types=["t"],
    )
    assert rows == [] and entries == []
    assert refs == ["artifact:dispatch:abc:file:skill-dir_leaderboard.csv"]  # ref still counted
    assert stub.stored == []  # nothing re-stored


def test_v1_helper_isolates_storage_failures(v1_env, monkeypatch: pytest.MonkeyPatch) -> None:
    repo_module, base, stub = v1_env
    _register_skill(monkeypatch, ["skill-dir/leaderboard.csv", "skill-dir/pareto/*.cif"])

    class _Boom(_StubWriteService):
        def store_durable_bytes(self, *args: object, **kwargs: object):
            raise RuntimeError("minio down")

    monkeypatch.setattr(repo_module, "artifact_write_service", _Boom())
    repo = repo_module.ResearchOrchestrationRepository()
    rows, entries, refs = repo._declared_skill_file_artifacts(
        fence=_fence(repo_module), work=_WORK, assignment=_ASSIGNMENT, call_id="call_1",
        result_id="res_1", existing_artifacts_by_id={}, requires_any_review=False,
        artifact_contract="c", produced_artifact_type="t", expected_types=["t"],
    )
    assert rows == [] and entries == []
    assert len(refs) == 2  # ids still deterministic; artifacts simply absent


def test_v1_helper_ignores_non_skill_executors_and_missing_contract(v1_env, monkeypatch: pytest.MonkeyPatch) -> None:
    repo_module, base, _stub = v1_env
    repo = repo_module.ResearchOrchestrationRepository()
    result = repo._declared_skill_file_artifacts(
        fence=_fence(repo_module), work=_WORK,
        assignment={"executorRef": {"executorType": "model", "executorId": "m1"}},
        call_id="c", result_id="r", existing_artifacts_by_id={},
        requires_any_review=False, artifact_contract="c",
        produced_artifact_type=None, expected_types=[],
    )
    assert result == ([], [], [])
    _register_skill(monkeypatch, [])
    result = repo._declared_skill_file_artifacts(
        fence=_fence(repo_module), work=_WORK, assignment=_ASSIGNMENT, call_id="c",
        result_id="r", existing_artifacts_by_id={}, requires_any_review=False,
        artifact_contract="c", produced_artifact_type=None, expected_types=[],
    )
    assert result == ([], [], [])


# ---------------------------------------------------------------------------
# Live progress read-through route
# ---------------------------------------------------------------------------


def test_work_unit_progress_route_reads_workspace_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import app.routes.tasks as tasks_module

    monkeypatch.setattr(tasks_module.settings, "persistence_backend", "postgres")
    monkeypatch.setattr(tasks_module, "read_task_scope", lambda task_id: {"taskId": task_id})
    monkeypatch.setattr(tasks_module, "ensure_task_in_scope", lambda scope, identity: scope)
    monkeypatch.setenv("OPENAIR_WORKSPACE_ROOT", str(tmp_path))
    unit_dir = tmp_path / "task_t1" / "workunit_w1"
    unit_dir.mkdir(parents=True)

    class _Identity:
        pass

    # No file yet → exists=false (panel hides itself, no error path on the client)
    body = tasks_module.get_work_unit_progress("task_t1", "workunit_w1", _Identity())
    assert body == {"exists": False, "content": None, "updatedAt": None}

    (unit_dir / "progress.md").write_text(
        "- **[10:00:00**] 已提交 HPC 任务 MgO_2at_relax\n", encoding="utf-8"
    )
    body = tasks_module.get_work_unit_progress("task_t1", "workunit_w1", _Identity())
    assert body["exists"] is True
    assert "MgO_2at_relax" in body["content"]
    assert body["updatedAt"]

    # Path traversal / odd ids are sanitized to the same segment rules as writers
    body = tasks_module.get_work_unit_progress("task/../t1", "workunit/w1", _Identity())
    assert declared_file_segment("task/../t1") == "task_.._t1"
