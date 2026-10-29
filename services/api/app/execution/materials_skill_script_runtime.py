"""Narrow durable bridge for the workflow-owned materials discovery Skills.

The two scripts remain the execution authority.  This module only creates an
isolated run directory, invokes them after the dispatch fence is authorized,
and turns their real files into the frozen JSON artifact consumed downstream.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Mapping

from app.modules.repo_paths import repo_root


_MP_SKILL = "materials_mp_constraint_search"
_STRUCTURE_SKILL = "crystal_design_structure_crossover_agent"
_VASP_STABILITY_DURABLE_SKILL = "vasp_stability_durable"
_ANALYSIS_REPORT_DURABLE_SKILL = "analysis_report_durable"
_FLUX_EXPERIMENT_SKILL = "flux_experiment"
_SAFE_SEGMENT = re.compile(r"[^A-Za-z0-9_.-]+")
_WINDOWS_GIT_BASH = Path(r"C:\Program Files\Git\bin\bash.exe")


def _segment(value: str) -> str:
    return _SAFE_SEGMENT.sub("_", value).strip("._") or "run"


def _materials_output_root() -> Path:
    """Materials skill 输出根：容器内用任务工作区卷（与 API 共享、可注册产物），
    宿主机直跑（无 env）回落到仓库 outputs 目录。"""
    ws_root = os.environ.get("OPENAIR_WORKSPACE_ROOT", "").strip()
    return Path(ws_root) if ws_root else repo_root() / "outputs" / "canonical-materials"


def _run_dir(task_id: str, work_unit_id: str, name: str) -> Path:
    path = _materials_output_root() / _segment(task_id) / _segment(work_unit_id) / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def _append_progress(task_id: str, work_unit_id: str, message: str) -> None:
    """向工作单元的 progress.md 追加一条带时间戳的里程碑（前端实时轮询渲染）。

    文件约定位于 <OPENAIR_WORKSPACE_ROOT>/<task_id>/<work_unit_id>/progress.md。
    写失败静默忽略——进度展示绝不能影响执行。
    """
    ws_root = os.environ.get("OPENAIR_WORKSPACE_ROOT", "").strip()
    if not ws_root:
        return
    try:
        path = Path(ws_root) / _segment(task_id) / _segment(work_unit_id) / "progress.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(f"- **[{time.strftime('%H:%M:%S')}]** {message}\n")
    except OSError:
        pass


def _command_text(value: str | bytes | None) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value if isinstance(value, str) else ""


def _command_evidence(command: list[str], completed: subprocess.CompletedProcess[Any]) -> dict[str, Any]:
    encoded = json.dumps(command, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    return {
        "command_sha256": hashlib.sha256(encoded).hexdigest(),
        "returncode": completed.returncode,
        "stdout_tail": _command_text(completed.stdout)[-2000:],
        "stderr_tail": _command_text(completed.stderr)[-2000:],
    }


def _bash_command(*args: str) -> list[str]:
    """Resolve a real Bash executable instead of Windows' WSL launcher."""
    bash = _resolve_bash() if os.name == "nt" else Path("bash")
    if os.name != "nt":
        return [str(bash), *args]
    # Only the script path is interpreted by Bash itself; convert it to its
    # MSYS form (S:\\a\\b -> /s/a/b). All other arguments (run-dir, seed-cifs,
    # EVOLVE_PY) are consumed by the Windows Python driver, so they must stay
    # in native Windows form — Bash passes them through as opaque strings.
    normalized = [(_bash_path_arg(args[0]) if args else args), *args[1:]]
    return [str(bash), *normalized]


def _resolve_bash() -> Path:
    """Find a real MSYS/Git Bash, never Windows' WSL launcher."""
    candidates = [
        _WINDOWS_GIT_BASH,
        # Git for Windows is not always installed under C:\Program Files.
        Path(os.environ.get("GIT_INSTALL_ROOT", "")) / "bin" / "bash.exe",
        Path(shutil.which("bash") or ""),
    ]
    for candidate in candidates:
        try:
            if candidate.is_file() and "system32" not in str(candidate).lower():
                return candidate
        except OSError:
            continue
    return Path("bash")


def _bash_path_arg(value: str) -> str:
    drive = re.match(r"^([A-Za-z]):[\\/](.*)$", value)
    if not drive:
        return value
    return f"/{drive.group(1).lower()}/{drive.group(2).replace(chr(92), '/')}"


def _skill_script_environment() -> dict[str, str]:
    """Run the Skill with this API environment's scientific Python available.

    On Windows the driver runs under the native venv Python, so EVOLVE_PY must
    stay in native Windows form (MSYS Bash converts it automatically when
    exec-ing a Windows binary)."""
    return {**os.environ, "EVOLVE_PY": sys.executable}


def _goal_constraints(goal: str) -> tuple[float, float]:
    match = re.search(r"(?:band\s*gap|bandgap|带隙|禁带).*?([0-9]+(?:\.[0-9]+)?)\s*(?:-|–|—|to|~|至)\s*([0-9]+(?:\.[0-9]+)?)", goal, re.I)
    if match:
        lower, upper = float(match.group(1)), float(match.group(2))
        if lower <= upper:
            return lower, upper
    return 0.2, 3.5


_ELEMENT_TOKEN = re.compile(r"[A-Z][a-z]?")


def _explicit_chemical_system(goal: str) -> tuple[str, tuple[str, ...]] | None:
    """Return an explicitly written hyphenated chemical system, if present."""
    for match in re.finditer(r"(?<![A-Za-z])([A-Z][a-z]?(?:-[A-Z][a-z]?)+)(?![A-Za-z])", goal):
        elements = tuple(match.group(1).split("-"))
        if len(elements) >= 2 and all(_ELEMENT_TOKEN.fullmatch(element) for element in elements):
            normalized = tuple(sorted(set(elements)))
            return "-".join(normalized), normalized
    return None


def _clear_mp_search_outputs(run_dir: Path) -> None:
    for name in ("candidates.json", "summary.json", "report.md", "passing_candidates.json"):
        path = run_dir / name
        if path.is_file():
            path.unlink()
    cif_dir = run_dir / "cifs"
    if cif_dir.is_dir():
        for path in cif_dir.glob("*.cif"):
            path.unlink()


def _goal_candidate_count(goal: str) -> int | None:
    """导出候选数：只认检索/导出短语里明示的数字（'导出 2 个候选'）。

    裸「候选 N 个」不算——那通常是演化种群规模（'演化少量候选（约 4 个）'
    曾被误读成导出 4 个种子），种子数与种群数已解耦；匹配不到回落默认 2。
    """
    match = re.search(r"(?:导出|检索|搜索|seeds?|candidates?)\D{0,6}(\d{1,2})\s*(?:个)?", goal)
    if match:
        return max(1, min(8, int(match.group(1))))
    return None


def _execute_mp_search(*, task_id: str, work_unit_id: str, parameters: Mapping[str, Any]) -> dict[str, Any]:
    goal = str(parameters.get("goal") or "").strip()
    lower, upper = _goal_constraints(goal)
    chemical_system = _explicit_chemical_system(goal)
    element_count = len(chemical_system[1]) if chemical_system else 3
    # 默认导出 2 个种子（查询可指定，上限 8）：演示链路的默认口径是
    # 「检索导出 2 → 演化 4 → 首名进 DFT」。
    max_results = _goal_candidate_count(goal) or 2
    run_dir = _run_dir(task_id, work_unit_id, "mp-seeds")
    script = repo_root() / "skills" / "materials" / "materials-mp-constraint-search" / "scripts" / "search_mp_candidates.py"
    command = [
        sys.executable, str(script), "--output-dir", str(run_dir), "--max-results", str(max_results),
        "--bandgap", str(lower), str(upper), "--num-elements", str(element_count), str(element_count),
        "--banned-elements", "Hg", "Tl", "Na", "K", "F", "Pb", "Cd",
    ]
    if chemical_system:
        command += ["--chemsys", chemical_system[0]]
        # 查询已钉死单一化学体系时，跨体系多样性上限没有意义——不放开的话
        # diversity_filter 会把同体系的全部多形体削到 1 条，导不出 N 个候选。
        command += ["--max-per-chemsys", str(max_results)]
    _clear_mp_search_outputs(run_dir)
    _append_progress(task_id, work_unit_id, f"Materials Project 检索启动（带隙 {lower}-{upper} eV，体系 {chemical_system[0] if chemical_system else '自动'}）")
    completed = subprocess.run(command, cwd=repo_root(), capture_output=True, timeout=300)
    evidence = _command_evidence(command, completed)
    candidates_doc: dict[str, Any] = {}
    candidates_path = run_dir / "candidates.json"
    if candidates_path.exists():
        candidates_doc = json.loads(candidates_path.read_text(encoding="utf-8"))
    candidates = candidates_doc.get("candidates") if isinstance(candidates_doc.get("candidates"), list) else []
    _append_progress(task_id, work_unit_id, f"MP 检索结束（rc={completed.returncode}，候选 {len(candidates)} 个）")
    seed_paths = sorted(str(path) for path in (run_dir / "cifs").glob("*.cif"))
    report_path = run_dir / "report.md"
    report = report_path.read_text(encoding="utf-8") if report_path.exists() else _command_text(completed.stdout)[-4000:]
    partial: list[str] = []
    if completed.returncode != 0:
        partial.append(f"MP search exited with code {completed.returncode}.")
    if not candidates:
        partial.append("MP search produced no seed candidates.")
    return {
        "state": "partial_failure" if partial else "succeeded",
        "candidates": candidates,
        "report_markdown": report or "# MP seed search\n\nNo report was generated.",
        "run_summary": {
            "run_dir": str(run_dir),
            "candidate_count": len(candidates),
            "bandgap": [lower, upper],
            # The subprocess result is already available here. Preserve its bounded
            # diagnostic tail in the material artifact so a failed MP query is not
            # reduced to an opaque no-candidates result.
            "command_evidence": evidence,
        },
        "partial_failure": partial or None,
        "external_action_evidence": [{"service": "materials_project", "operation": "materials.summary.search", "request_sha256": evidence["command_sha256"], "transport_outcome": "ok" if completed.returncode == 0 else "error"}],
    }


def _markdown_table(rows: list[dict[str, str]]) -> str:
    if not rows:
        return "No Pareto candidates were exported."
    columns = list(rows[0])
    lines = ["| " + " | ".join(columns) + " |", "|" + "|".join("---" for _ in columns) + "|"]
    lines.extend("| " + " | ".join(str(row.get(column, "")) for column in columns) + " |" for row in rows)
    return "\n".join(lines)


def _execute_structure_crossover(*, task_id: str, work_unit_id: str, parameters: Mapping[str, Any]) -> dict[str, Any]:
    candidates = parameters.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise ValueError("structure crossover requires non-empty candidates")
    seed_dir = _run_dir(task_id, work_unit_id, "structure-crossover-seeds")
    seed_paths: list[str] = []
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, Mapping):
            raise ValueError("structure crossover candidate must be an object")
        material_id = str(candidate.get("material_id") or "").strip()
        formula = str(candidate.get("formula_pretty") or "").strip()
        cif_text = candidate.get("cif_text")
        if not material_id or not formula or not isinstance(cif_text, str) or not cif_text.strip():
            raise ValueError("structure crossover candidate requires material_id, formula_pretty and cif_text")
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", f"{index:03d}_{material_id}_{formula}")
        path = seed_dir / f"{safe}.cif"
        path.write_text(cif_text, encoding="utf-8")
        seed_paths.append(str(path))
    goal = str(parameters.get("goal") or "").strip()
    overrides = parameters.get("task_instruction") if isinstance(parameters.get("task_instruction"), Mapping) else {}
    run_dir = _run_dir(task_id, work_unit_id, "structure-crossover")
    skill_dir = repo_root() / "skills" / "materials" / "crystal-design-structure-crossover-agent"
    runner = skill_dir / "scripts" / "run.sh"
    # 演化规模默认恒 4（与种子数解耦：演示口径「检索导出 2 → 演化 4 →
    # 首名进 DFT」），planner 的 task_instruction 仍可覆盖。
    pop_size = int(overrides.get("pop_size") or 4)
    generations = int(overrides.get("max_generations", 1))
    crossover_rate = float(overrides.get("crossover_rate", 0.5))
    objectives = {"ehull": "minimize", "bandgap": "maximize"}
    bandgap = _goal_constraints(goal)
    constraints = {"bandgap": {"min": bandgap[0], "max": bandgap[1], "weight": 10}}
    init = _bash_command(str(runner), "init", "--run-dir", str(run_dir), "--fitness-backend", "alignn", "--objectives", json.dumps(objectives), "--constraints", json.dumps(constraints), "--pop-size", str(pop_size), "--max-generations", str(generations), "--crossover-rate", str(crossover_rate), "--seed-cifs", ",".join(seed_paths))
    executions: list[dict[str, Any]] = []
    environment = _skill_script_environment()
    _append_progress(task_id, work_unit_id, f"结构演化启动：{len(seed_paths)} 个种子，种群 {pop_size}，共 {generations} 代（init 含性质模型表征，分钟级）")
    completed = subprocess.run(init, cwd=repo_root(), capture_output=True, timeout=900, env=environment)
    executions.append({"phase": "init", **_command_evidence(init, completed)})
    _append_progress(task_id, work_unit_id, f"演化 init 完成（rc={completed.returncode}）")
    if completed.returncode == 0:
        for generation in range(generations):
            command = _bash_command(str(runner), "step", "--run-dir", str(run_dir))
            _append_progress(task_id, work_unit_id, f"第 {generation + 1} 代演化中（变异/交叉 + 性质评估，约 2-4 分钟）")
            step = subprocess.run(command, cwd=repo_root(), capture_output=True, timeout=900, env=environment)
            executions.append({"phase": f"generation_{generation + 1}", **_command_evidence(command, step)})
            _append_progress(task_id, work_unit_id, f"第 {generation + 1} 代结束（rc={step.returncode}）")
            if step.returncode != 0:
                break
        finalize = _bash_command(str(runner), "finalize", "--run-dir", str(run_dir), "--top-n", "8")
        final = subprocess.run(finalize, cwd=repo_root(), capture_output=True, timeout=300, env=environment)
        executions.append({"phase": "finalize", **_command_evidence(finalize, final)})
        _append_progress(task_id, work_unit_id, f"finalize 完成（rc={final.returncode}）：导出 Pareto 结构、排行榜与结构渲染图")
    snapshot_path = run_dir / "snapshot_latest.json"
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8")) if snapshot_path.exists() else {}
    rows: list[dict[str, str]] = []
    leaderboard = run_dir / "leaderboard.csv"
    if leaderboard.exists():
        with leaderboard.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
    pareto = sorted(str(path) for path in (run_dir / "pareto").glob("*.cif"))
    failed = [row["phase"] for row in executions if row["returncode"] != 0]
    return {
        "state": "partial_failure" if failed else "succeeded",
        "run_dir": str(run_dir), "objectives": objectives, "constraints": constraints,
        "top_k_table": _markdown_table(rows), "iterations": [snapshot] if snapshot else [],
        "run_summary": {"goal": goal, "seed_count": len(seed_paths), "pareto_count": len(pareto)},
        "pareto_cif_paths": pareto,
        "partial_failure": [f"Skill script failed during {phase}." for phase in failed] or None,
        "execution_evidence": executions,
    }


def _execute_vasp_stability_durable(*, task_id: str, work_unit_id: str, parameters: Mapping[str, Any]) -> dict[str, Any]:
    from app.modules.repo_paths import repo_root as _repo_root
    import sys as _sys
    skill_scripts = str(_repo_root() / "skills" / "materials" / "vasp-stability-durable" / "scripts")
    if skill_scripts not in _sys.path:
        _sys.path.insert(0, skill_scripts)
    from run_stability import execute_vasp_stability_durable
    return execute_vasp_stability_durable(
        task_id=task_id,
        work_unit_id=work_unit_id,
        parameters=dict(parameters),
    )


def _execute_analysis_report_durable(
    *,
    task_id: str,
    work_unit_id: str,
    parameters: Mapping[str, Any],
    owner_id: str = "",
    workspace_id: str = "",
) -> dict[str, Any]:
    from app.modules.repo_paths import repo_root as _repo_root
    import sys as _sys
    skill_scripts = str(_repo_root() / "skills" / "materials" / "analysis-report-durable" / "scripts")
    if skill_scripts not in _sys.path:
        _sys.path.insert(0, skill_scripts)
    from run_report import execute_analysis_report_durable
    return execute_analysis_report_durable(
        task_id=task_id,
        work_unit_id=work_unit_id,
        parameters=dict(parameters),
        owner_id=owner_id,
        workspace_id=workspace_id,
    )


def _execute_flux_experiment(
    *,
    task_id: str,
    work_unit_id: str,
    parameters: Mapping[str, Any],
    owner_id: str = "",
    workspace_id: str = "",
) -> dict[str, Any]:
    from app.modules.repo_paths import repo_root as _repo_root
    import sys as _sys
    skill_scripts = str(_repo_root() / "skills" / "materials" / "flux-experiment" / "scripts")
    if skill_scripts not in _sys.path:
        _sys.path.insert(0, skill_scripts)
    from run_dispatch import execute_flux_experiment_durable
    return execute_flux_experiment_durable(
        task_id=task_id,
        work_unit_id=work_unit_id,
        parameters=dict(parameters),
        owner_id=owner_id,
        workspace_id=workspace_id,
    )


def execute_materials_skill_script(
    *,
    skill_id: str,
    task_id: str,
    work_unit_id: str,
    parameters: Mapping[str, Any],
    owner_id: str = "",
    workspace_id: str = "",
) -> dict[str, Any]:
    if skill_id == _MP_SKILL:
        return _execute_mp_search(task_id=task_id, work_unit_id=work_unit_id, parameters=parameters)
    if skill_id == _STRUCTURE_SKILL:
        return _execute_structure_crossover(task_id=task_id, work_unit_id=work_unit_id, parameters=parameters)
    if skill_id == _VASP_STABILITY_DURABLE_SKILL:
        return _execute_vasp_stability_durable(task_id=task_id, work_unit_id=work_unit_id, parameters=parameters)
    if skill_id == _ANALYSIS_REPORT_DURABLE_SKILL:
        return _execute_analysis_report_durable(
            task_id=task_id,
            work_unit_id=work_unit_id,
            parameters=parameters,
            owner_id=owner_id,
            workspace_id=workspace_id,
        )
    if skill_id == _FLUX_EXPERIMENT_SKILL:
        return _execute_flux_experiment(
            task_id=task_id,
            work_unit_id=work_unit_id,
            parameters=parameters,
            owner_id=owner_id,
            workspace_id=workspace_id,
        )
    raise ValueError("unsupported_materials_skill_script")


def execute_checkpointed_materials_skill_script(
    *,
    skill_id: str,
    task_id: str,
    work_unit_id: str,
    parameters: Mapping[str, Any],
    checkpoint_id: str | None,
    owner_id: str = "",
    workspace_id: str = "",
) -> dict[str, Any]:
    if not checkpoint_id:
        return execute_materials_skill_script(
            skill_id=skill_id,
            task_id=task_id,
            work_unit_id=work_unit_id,
            parameters=parameters,
            owner_id=owner_id,
            workspace_id=workspace_id,
        )
    from app.modules.external_execution_checkpoint import (
        ExternalExecutionCheckpointError,
        ExternalExecutionCheckpointRepository,
    )

    checkpoints = ExternalExecutionCheckpointRepository()
    checkpoint = checkpoints.get(capability_call_id=checkpoint_id)
    if checkpoint is None or checkpoint.execution_kind != "skill_script":
        raise ExternalExecutionCheckpointError("skill_script_checkpoint_invalid")
    if (
        checkpoint.phase == "response_received"
        and checkpoint.response_kind == "skill_script_result"
        and checkpoint.response_payload is not None
    ):
        recovered = json.loads(checkpoint.response_payload.decode("utf-8"))
        if not isinstance(recovered, dict):
            raise ExternalExecutionCheckpointError(
                "skill_script_checkpoint_payload_invalid"
            )
        return recovered
    if checkpoint.phase != "prepared" or not checkpoints.mark_submitted(
        capability_call_id=checkpoint_id
    ):
        raise ExternalExecutionCheckpointError("skill_script_result_unavailable")
    result = execute_materials_skill_script(
        skill_id=skill_id,
        task_id=task_id,
        work_unit_id=work_unit_id,
        parameters=parameters,
        owner_id=owner_id,
        workspace_id=workspace_id,
    )
    checkpoints.stage_response(
        capability_call_id=checkpoint_id,
        response_kind="skill_script_result",
        payload=json.dumps(
            result, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8"),
    )
    return result
