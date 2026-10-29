"""Minimal Python adapter runner for selected OpenTrajectory skills.

**Runtime internal implementation — do not import directly from API layer.**
API orchestration code should use ``app.execution.runtime_boundary`` instead.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from app.execution.execution_context import get_execution_cwd
from app.execution.python_adapter_skills import resolve_python_adapter_entrypoint


def _load_module_from_path(module_name: str, file_path: Path):
    spec = importlib.util.spec_from_file_location(module_name, str(file_path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load module spec: {file_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _api_package_roots(repo_root: Path) -> list[Path]:
    """Candidate import roots that expose the backend ``app`` package.

    The checkout keeps the package at repo/services/api; the flattened service
    image places it directly under the image root. External adapters import
    ``app.execution`` for durable child-skill calls, so the runner must make
    the package importable in both layouts.
    """
    return [repo_root / "services" / "api", repo_root]


def _run_registered_adapter_entrypoint(
    *,
    repo_root: Path,
    skill_dir: Path,
    entrypoint: tuple[str, str, str],
    input_payload: dict[str, Any],
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
    execution_cwd: Path | None = None,
    environment_overrides: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Execute one explicitly registered adapter callable in an isolated process."""
    module_name, callable_name, invocation = entrypoint
    worker = '\nimport importlib\nimport json\nimport sys\n\nfor _stream in (sys.stdin, sys.stdout, sys.stderr):\n    _reconfigure = getattr(_stream, "reconfigure", None)\n    if callable(_reconfigure):\n        _reconfigure(encoding="utf-8")\n\nskill_dir, module_name, callable_name, invocation = sys.argv[1:5]\nsys.path.insert(0, skill_dir)\npayload = json.load(sys.stdin)\nmodule = importlib.import_module(module_name)\nfn = getattr(module, callable_name)\nif invocation == "payload_v1":\n    result = fn(payload)\nelif invocation == "academic_retrieval_v1":\n    config = payload.get("retrieval_config") or {}\n    skill2_data = {\n        "user_query": payload.get("user_query"),\n        "search_matrix": payload.get("search_matrix") or [],\n        "expanded_query_variants": payload.get("expanded_query_variants") or [],\n    }\n    for key in ("research_evidence_contract", "evidence_contract", "contract"):\n        if key in payload:\n            skill2_data[key] = payload.get(key)\n    result = fn(\n        skill2_data=skill2_data,\n        target_sources=config.get("target_sources") or ["openalex", "semantic_scholar", "arxiv"],\n        max_results_per_query=int(config.get("max_results_per_query", 10)),\n        max_total_candidates=int(config.get("max_total_candidates", 120)),\n        timeout_seconds=int(config.get("timeout_seconds", 45)),\n        allow_partial_success=bool(config.get("allow_partial_success", True)),\n        s2_api_key=payload.get("s2_api_key"),\n    )\nelse:\n    raise RuntimeError("unknown registered adapter invocation")\njson.dump(result, sys.stdout, ensure_ascii=False, separators=(",", ":"))\n'
    progress_offset = 0
    with tempfile.TemporaryDirectory(prefix="openair_adapter_") as tmpdir:
        progress_path = Path(tmpdir) / "progress.jsonl"
        stdout_path = Path(tmpdir) / "stdout.txt"
        stderr_path = Path(tmpdir) / "stderr.txt"
        env = os.environ.copy()
        if environment_overrides is not None:
            env.update({str(key): str(value) for key, value in environment_overrides.items()})
        # The isolated worker only puts the skill directory on sys.path; make the
        # backend ``app`` package importable too, matching both deployment layouts.
        python_path_parts = [
            str(root)
            for root in _api_package_roots(repo_root)
            if (root / "app" / "__init__.py").is_file()
        ]
        if python_path_parts:
            existing = env.get("PYTHONPATH")
            if existing:
                python_path_parts.append(existing)
            env["PYTHONPATH"] = os.pathsep.join(python_path_parts)
        if progress_callback is not None:
            env["OPENAIR_SKILL_PROGRESS_FILE"] = str(progress_path)

        def drain_progress() -> None:
            nonlocal progress_offset
            if progress_callback is None or not progress_path.exists():
                return
            with progress_path.open("r", encoding="utf-8") as handle:
                handle.seek(progress_offset)
                for line in handle:
                    raw = line.strip()
                    if not raw:
                        continue
                    try:
                        event = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(event, dict):
                        try:
                            progress_callback(event)
                        except Exception:
                            # Progress display must never change the Skill result.
                            continue
                progress_offset = handle.tell()

        with stdout_path.open("w+", encoding="utf-8") as stdout_handle, stderr_path.open(
            "w+", encoding="utf-8"
        ) as stderr_handle:
            proc = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    worker,
                    str(skill_dir),
                    module_name,
                    callable_name,
                    invocation,
                ],
                stdin=subprocess.PIPE,
                stdout=stdout_handle,
                stderr=stderr_handle,
                text=True,
                encoding="utf-8",
                errors="strict",
                cwd=str(execution_cwd or get_execution_cwd()),
                env=env,
            )
            assert proc.stdin is not None
            proc.stdin.write(json.dumps(input_payload, ensure_ascii=False, separators=(",", ":")))
            proc.stdin.close()
            while proc.poll() is None:
                drain_progress()
                time.sleep(0.05)
            drain_progress()
            stdout_handle.seek(0)
            stderr_handle.seek(0)
            stdout = stdout_handle.read()
            stderr = stderr_handle.read()

    if proc.returncode != 0:
        raise RuntimeError(
            "registered Python adapter failed\n"
            f"stdout:\n{stdout}\n"
            f"stderr:\n{stderr}"
        )
    try:
        output = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("registered Python adapter returned invalid JSON") from exc
    if not isinstance(output, dict):
        raise RuntimeError("registered Python adapter must return a JSON object")
    return output


def _run_python_skill_adapter_once(
    *,
    repo_root: Path,
    skill_id: str,
    skill_path: str,
    adapter_id: str | None = None,
    input_payload: dict[str, Any],
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
    execution_cwd: Path | None = None,
    environment_overrides: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Execute Python adapter for supported skills.

    Args:
        repo_root: repository root path.
        skill_id: skill id from skill.yaml/registry.
        skill_path: relative skill directory from registry.
        adapter_id: formal runtime_contract.adapter_id when Registry-driven.
        input_payload: validated input-like payload.
    """
    skill_dir = (repo_root / skill_path).resolve()

    registered_entrypoint = resolve_python_adapter_entrypoint(adapter_id or "")
    if registered_entrypoint is not None:
        return _run_registered_adapter_entrypoint(
            repo_root=repo_root,
            skill_dir=skill_dir,
            entrypoint=registered_entrypoint,
            input_payload=input_payload,
            progress_callback=progress_callback,
            execution_cwd=execution_cwd,
            environment_overrides=environment_overrides,
        )

    if skill_id == "academic_source_retrieval_skill":
        adapter_pkg_dir = (skill_dir / "adapter").resolve()
        adapter_init = adapter_pkg_dir / "__init__.py"
        retrieval_py = adapter_pkg_dir / "retrieval_adapter.py"
        if not adapter_init.exists() or not retrieval_py.exists():
            raise RuntimeError(f"retrieval adapter package not found: {adapter_pkg_dir}")

        retrieval_config = input_payload.get("retrieval_config") or {}
        skill2_data = {
            "user_query": input_payload.get("user_query"),
            "search_matrix": input_payload.get("search_matrix") or [],
            "expanded_query_variants": input_payload.get("expanded_query_variants") or [],
        }
        for contract_key in ("research_evidence_contract", "evidence_contract", "contract"):
            if contract_key in input_payload:
                skill2_data[contract_key] = input_payload.get(contract_key)
        run_payload = {
            "skill2_data": skill2_data,
            "target_sources": retrieval_config.get("target_sources") or ["openalex", "semantic_scholar", "arxiv"],
            "max_results_per_query": int(retrieval_config.get("max_results_per_query", 10)),
            "max_total_candidates": int(retrieval_config.get("max_total_candidates", 120)),
            "timeout_seconds": int(retrieval_config.get("timeout_seconds", 45)),
            "allow_partial_success": bool(retrieval_config.get("allow_partial_success", True)),
            "s2_api_key": input_payload.get("s2_api_key"),
            "skill_dir": str(skill_dir),
        }

        with tempfile.TemporaryDirectory(prefix="phase4_retrieval_") as tmpdir:
            tmp = Path(tmpdir)
            in_json = tmp / "input.json"
            out_json = tmp / "output.json"
            worker_py = tmp / "worker.py"

            in_json.write_text(json.dumps(run_payload, ensure_ascii=False), encoding="utf-8")
            worker_py.write_text(
                """
import json
import sys
from pathlib import Path

inp = Path(sys.argv[1])
out = Path(sys.argv[2])
payload = json.loads(inp.read_text(encoding=\"utf-8\"))
skill_dir = payload[\"skill_dir\"]

if skill_dir not in sys.path:
    sys.path.insert(0, skill_dir)

from adapter.retrieval_adapter import run_retrieval

result = run_retrieval(
    skill2_data=payload[\"skill2_data\"],
    target_sources=payload[\"target_sources\"],
    max_results_per_query=payload[\"max_results_per_query\"],
    max_total_candidates=payload[\"max_total_candidates\"],
    timeout_seconds=payload[\"timeout_seconds\"],
    allow_partial_success=payload[\"allow_partial_success\"],
    s2_api_key=payload.get(\"s2_api_key\"),
)
out.write_text(json.dumps(result, ensure_ascii=False), encoding=\"utf-8\")
""".strip(),
                encoding="utf-8",
            )

            proc = subprocess.run(
                [sys.executable, str(worker_py), str(in_json), str(out_json)],
                cwd=str(get_execution_cwd()),
                capture_output=True,
                text=True,
            )
            if proc.returncode != 0:
                raise RuntimeError(
                    "phase4 subprocess failed\n"
                    f"stdout:\n{proc.stdout}\n"
                    f"stderr:\n{proc.stderr}"
                )
            if not out_json.exists():
                raise RuntimeError("phase4 subprocess did not produce output.json")
            return json.loads(out_json.read_text(encoding="utf-8"))

    if skill_id == "candidate_dedup_and_provenance":
        adapter_py = skill_dir / "adapter" / "dedup_adapter.py"
        if not adapter_py.exists():
            raise RuntimeError(f"dedup adapter not found: {adapter_py}")
        module = _load_module_from_path("candidate_dedup_and_provenance_adapter", adapter_py)
        fn = getattr(module, "deduplicate_candidates", None)
        if fn is None:
            raise RuntimeError("deduplicate_candidates not found in dedup_adapter.py")
        return fn(input_payload)

    if skill_id == "candidate_source_scoring":
        adapter_py = skill_dir / "adapter" / "scoring_adapter.py"
        if not adapter_py.exists():
            raise RuntimeError(f"scoring adapter not found: {adapter_py}")
        module = _load_module_from_path("candidate_source_scoring_adapter", adapter_py)
        fn = getattr(module, "score_candidates", None)
        if fn is None:
            raise RuntimeError("score_candidates not found in scoring_adapter.py")

        return fn(input_payload)

    if skill_id == "candidate_tiering":
        adapter_py = skill_dir / "adapter" / "tiering_adapter.py"
        if not adapter_py.exists():
            raise RuntimeError(f"tiering adapter not found: {adapter_py}")
        module = _load_module_from_path("candidate_tiering_adapter", adapter_py)
        fn = getattr(module, "tier_candidates", None)
        if fn is None:
            raise RuntimeError("tier_candidates not found in tiering_adapter.py")
        return fn(input_payload)

    if skill_id == "topk_source_selection":
        adapter_py = skill_dir / "adapter" / "topk_adapter.py"
        if not adapter_py.exists():
            raise RuntimeError(f"topk adapter not found: {adapter_py}")
        module = _load_module_from_path("topk_source_selection_adapter", adapter_py)
        fn = getattr(module, "build_topk_selection", None)
        if fn is None:
            raise RuntimeError("build_topk_selection not found in topk_adapter.py")
        return fn(input_payload)

    if skill_id in {"source_access_readiness_resolver", "source-access-readiness-resolver-skill"}:
        adapter_py = skill_dir / "adapter" / "access_readiness_adapter.py"
        if not adapter_py.exists():
            raise RuntimeError(f"access readiness adapter not found: {adapter_py}")
        module = _load_module_from_path("source_access_readiness_adapter", adapter_py)
        fn = getattr(module, "resolve_access_readiness", None)
        if fn is None:
            raise RuntimeError("resolve_access_readiness not found in access_readiness_adapter.py")
        return fn(input_payload)

    if skill_id in {"title_abstract_screening", "title-abstract-screening-skill"}:
        adapter_py = skill_dir / "adapter" / "title_abstract_screening_adapter.py"
        if not adapter_py.exists():
            raise RuntimeError(f"title-abstract screening adapter not found: {adapter_py}")
        module = _load_module_from_path("title_abstract_screening_adapter", adapter_py)
        fn = getattr(module, "screen_title_abstract_candidates", None)
        if fn is None:
            raise RuntimeError("screen_title_abstract_candidates not found in title_abstract_screening_adapter.py")
        return fn(input_payload)

    if skill_id in {"deepxiv_progressive_reading", "deepxiv-progressive-reading-skill"}:
        adapter_py = skill_dir / "adapter" / "progressive_reading_adapter.py"
        if not adapter_py.exists():
            raise RuntimeError(f"DeepXiv progressive reading adapter not found: {adapter_py}")
        module = _load_module_from_path("deepxiv_progressive_reading_adapter", adapter_py)
        fn = getattr(module, "progressive_read_candidates", None)
        if fn is None:
            raise RuntimeError("progressive_read_candidates not found in progressive_reading_adapter.py")
        return fn(input_payload)

    if skill_id in {"materials_magnet_design_mvp", "materials-magnet-design-mvp"}:
        # Subprocess pattern (mirror academic_source_retrieval_skill): pymatgen
        # crash isolation + structured stderr capture. Adapter must never
        # raise; it always returns a populated dict (state=succeeded|partial_failure).
        adapter_pkg_dir = (skill_dir / "adapter").resolve()
        adapter_init = adapter_pkg_dir / "__init__.py"
        magnet_py = adapter_pkg_dir / "magnet_adapter.py"
        if not adapter_init.exists() or not magnet_py.exists():
            raise RuntimeError(f"materials-magnet-design-mvp adapter package not found: {adapter_pkg_dir}")

        with tempfile.TemporaryDirectory(prefix="magnet_mvp_") as tmpdir:
            tmp = Path(tmpdir)
            in_json = tmp / "input.json"
            out_json = tmp / "output.json"
            worker_py = tmp / "worker.py"

            in_json.write_text(
                json.dumps(input_payload, ensure_ascii=False), encoding="utf-8"
            )
            worker_py.write_text(
                """
import json
import sys
from pathlib import Path

inp = Path(sys.argv[1])
out = Path(sys.argv[2])
payload = json.loads(inp.read_text(encoding=\"utf-8\"))
skill_dir = Path(sys.argv[3])

# Make the skill's adapter package importable.
sys.path.insert(0, str(skill_dir))
# Make the shared MCP client importable as `import mcp_client`.
# Follow the backend package actually loaded by this process; this is the
# same authority in a repository checkout and in a flattened service image.
from app.modules.repo_paths import backend_app_root

modules_dir = backend_app_root() / \"modules\"
if modules_dir.is_dir() and str(modules_dir) not in sys.path:
    sys.path.insert(0, str(modules_dir))

from adapter.magnet_adapter import run_magnet_mvp

result = run_magnet_mvp(payload)
out.write_text(json.dumps(result, ensure_ascii=False), encoding=\"utf-8\")
""".strip(),
                encoding="utf-8",
            )

            proc = subprocess.run(
                [sys.executable, str(worker_py), str(in_json), str(out_json), str(skill_dir)],
                cwd=str(get_execution_cwd()),
                capture_output=True,
                text=True,
            )
            if proc.returncode != 0:
                # Adapter is supposed to never raise, so a non-zero exit is a
                # hard failure. Bubble up so durable_skill_runtime marks failed.
                raise RuntimeError(
                    "materials-magnet-design-mvp subprocess failed\n"
                    f"stdout:\n{proc.stdout}\n"
                    f"stderr:\n{proc.stderr}"
                )
            if not out_json.exists():
                raise RuntimeError("materials-magnet-design-mvp subprocess did not produce output.json")
            return json.loads(out_json.read_text(encoding="utf-8"))

    if skill_id in {"crystal_design_structure_crossover_py", "crystal-design-structure-crossover-py", "crystal_design_structure_crossover", "materials-crystal-design-structure-crossover"}:
        adapter_pkg_dir = (skill_dir / "adapter").resolve()
        adapter_init = adapter_pkg_dir / "__init__.py"
        structure_py = adapter_pkg_dir / "structure_crossover_adapter.py"
        if not adapter_init.exists() or not structure_py.exists():
            raise RuntimeError(
                f"crystal-design-structure-crossover adapter package not found: {adapter_pkg_dir}"
            )

        with tempfile.TemporaryDirectory(prefix="structure_crossover_mvp_") as tmpdir:
            tmp = Path(tmpdir)
            in_json = tmp / "input.json"
            out_json = tmp / "output.json"
            worker_py = tmp / "worker.py"

            in_json.write_text(json.dumps(input_payload, ensure_ascii=False), encoding="utf-8")
            worker_py.write_text(
                """
import json
import sys
from pathlib import Path

inp = Path(sys.argv[1])
out = Path(sys.argv[2])
skill_dir = Path(sys.argv[3])
payload = json.loads(inp.read_text(encoding="utf-8"))

sys.path.insert(0, str(skill_dir))
from adapter.structure_crossover_adapter import run_structure_crossover_mvp

result = run_structure_crossover_mvp(payload)
out.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
""".strip(),
                encoding="utf-8",
            )

            proc = subprocess.run(
                [sys.executable, str(worker_py), str(in_json), str(out_json), str(skill_dir)],
                cwd=str(get_execution_cwd()),
                capture_output=True,
                text=True,
            )
            if proc.returncode != 0:
                raise RuntimeError(
                    "crystal-design-structure-crossover subprocess failed\n"
                    f"stdout:\n{proc.stdout}\n"
                    f"stderr:\n{proc.stderr}"
                )
            if not out_json.exists():
                raise RuntimeError(
                    "crystal-design-structure-crossover subprocess did not produce output.json"
                )
            return json.loads(out_json.read_text(encoding="utf-8"))

    if skill_id in {"crystal_design_formula_crossover_py", "crystal-design-formula-crossover-py", "crystal_design_formula_crossover", "materials-crystal-design-formula-crossover"}:
        # Subprocess pattern (mirror materials_magnet_design_mvp): pymatgen
        # crash isolation + structured stderr capture. Adapter must never
        # raise; it always returns a populated dict (state=succeeded|partial_failure).
        # Note: the crossover adapter bootstraps its own mcp_client import path
        # internally (see crossover_adapter.py header), so the worker only needs
        # to put the skill dir on sys.path.
        adapter_pkg_dir = (skill_dir / "adapter").resolve()
        adapter_init = adapter_pkg_dir / "__init__.py"
        crossover_py = adapter_pkg_dir / "crossover_adapter.py"
        if not adapter_init.exists() or not crossover_py.exists():
            raise RuntimeError(f"crystal-design-formula-crossover adapter package not found: {adapter_pkg_dir}")

        with tempfile.TemporaryDirectory(prefix="crossover_mvp_") as tmpdir:
            tmp = Path(tmpdir)
            in_json = tmp / "input.json"
            out_json = tmp / "output.json"
            worker_py = tmp / "worker.py"

            in_json.write_text(
                json.dumps(input_payload, ensure_ascii=False), encoding="utf-8"
            )
            worker_py.write_text(
                """
import json
import sys
from pathlib import Path

inp = Path(sys.argv[1])
out = Path(sys.argv[2])
payload = json.loads(inp.read_text(encoding=\"utf-8\"))
skill_dir = Path(sys.argv[3])

sys.path.insert(0, str(skill_dir))
from adapter.crossover_adapter import run_crossover_mvp

result = run_crossover_mvp(payload)
out.write_text(json.dumps(result, ensure_ascii=False), encoding=\"utf-8\")
""".strip(),
                encoding="utf-8",
            )

            proc = subprocess.run(
                [sys.executable, str(worker_py), str(in_json), str(out_json), str(skill_dir)],
                cwd=str(get_execution_cwd()),
                capture_output=True,
                text=True,
            )
            if proc.returncode != 0:
                # Adapter is supposed to never raise, so a non-zero exit is a
                # hard failure. Bubble up so durable_skill_runtime marks failed.
                raise RuntimeError(
                    "crystal-design-formula-crossover subprocess failed\n"
                    f"stdout:\n{proc.stdout}\n"
                    f"stderr:\n{proc.stderr}"
                )
            if not out_json.exists():
                raise RuntimeError("crystal-design-formula-crossover subprocess did not produce output.json")
            return json.loads(out_json.read_text(encoding="utf-8"))

    if skill_id in {"materials_mp_constraint_search", "materials-mp-constraint-search"}:
        # Subprocess pattern (mirror materials_magnet_design_mvp): mp_api +
        # pymatgen crash isolation + structured stderr capture. Adapter must
        # never raise; it always returns a populated dict
        # (state=succeeded|partial_failure).
        adapter_pkg_dir = (skill_dir / "adapter").resolve()
        adapter_init = adapter_pkg_dir / "__init__.py"
        search_py = adapter_pkg_dir / "search_adapter.py"
        if not adapter_init.exists() or not search_py.exists():
            raise RuntimeError(f"materials-mp-constraint-search adapter package not found: {adapter_pkg_dir}")

        with tempfile.TemporaryDirectory(prefix="mp_search_") as tmpdir:
            tmp = Path(tmpdir)
            in_json = tmp / "input.json"
            out_json = tmp / "output.json"
            worker_py = tmp / "worker.py"

            in_json.write_text(
                json.dumps(input_payload, ensure_ascii=False), encoding="utf-8"
            )
            worker_py.write_text(
                """
import json
import sys
from pathlib import Path

inp = Path(sys.argv[1])
out = Path(sys.argv[2])
payload = json.loads(inp.read_text(encoding=\"utf-8\"))
skill_dir = Path(sys.argv[3])

sys.path.insert(0, str(skill_dir))

from adapter.search_adapter import run_mp_constraint_search

result = run_mp_constraint_search(payload)
out.write_text(json.dumps(result, ensure_ascii=False), encoding=\"utf-8\")
""".strip(),
                encoding="utf-8",
            )

            proc = subprocess.run(
                [sys.executable, str(worker_py), str(in_json), str(out_json), str(skill_dir)],
                cwd=str(get_execution_cwd()),
                capture_output=True,
                text=True,
            )
            if proc.returncode != 0:
                raise RuntimeError(
                    "materials-mp-constraint-search subprocess failed\n"
                    f"stdout:\n{proc.stdout}\n"
                    f"stderr:\n{proc.stderr}"
                )
            if not out_json.exists():
                raise RuntimeError("materials-mp-constraint-search subprocess did not produce output.json")
            return json.loads(out_json.read_text(encoding="utf-8"))

    raise RuntimeError(f"python adapter runner does not support skill_id={skill_id}")


def run_python_skill_adapter(
    *,
    repo_root: Path,
    skill_id: str,
    skill_path: str,
    adapter_id: str | None = None,
    input_payload: dict[str, Any],
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
    execution_cwd: Path | None = None,
    environment_overrides: Mapping[str, str] | None = None,
    execution_checkpoint_id: str | None = None,
    authorize_submission: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Execute once, or resume the exact staged adapter result without replay."""
    if not execution_checkpoint_id:
        return _run_python_skill_adapter_once(
            repo_root=repo_root,
            skill_id=skill_id,
            skill_path=skill_path,
            adapter_id=adapter_id,
            input_payload=input_payload,
            progress_callback=progress_callback,
            execution_cwd=execution_cwd,
            environment_overrides=environment_overrides,
        )

    from app.modules.external_execution_checkpoint import (
        ExternalExecutionCheckpointError,
        ExternalExecutionCheckpointRepository,
    )

    checkpoints = ExternalExecutionCheckpointRepository()
    checkpoint = checkpoints.get(capability_call_id=execution_checkpoint_id)
    if checkpoint is None or checkpoint.execution_kind != "external_adapter":
        raise ExternalExecutionCheckpointError("external_adapter_checkpoint_invalid")
    if (
        checkpoint.phase == "response_received"
        and checkpoint.response_kind == "adapter_result"
        and checkpoint.response_payload is not None
    ):
        recovered = json.loads(checkpoint.response_payload.decode("utf-8"))
        if not isinstance(recovered, dict):
            raise ExternalExecutionCheckpointError(
                "external_adapter_checkpoint_payload_invalid"
            )
        return recovered
    if checkpoint.phase != "prepared" or not (
        authorize_submission()
        if authorize_submission is not None
        else checkpoints.mark_submitted(capability_call_id=execution_checkpoint_id)
    ):
        raise ExternalExecutionCheckpointError(
            "external_adapter_result_unavailable"
        )
    result = _run_python_skill_adapter_once(
        repo_root=repo_root,
        skill_id=skill_id,
        skill_path=skill_path,
        adapter_id=adapter_id,
        input_payload=input_payload,
        progress_callback=progress_callback,
        execution_cwd=execution_cwd,
        environment_overrides=environment_overrides,
    )
    encoded = json.dumps(
        result, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    checkpoints.stage_response(
        capability_call_id=execution_checkpoint_id,
        response_kind="adapter_result",
        payload=encoded,
    )
    return result
